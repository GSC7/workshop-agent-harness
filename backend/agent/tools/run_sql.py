from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import sqlglot
from langchain_core.tools import BaseTool, tool
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.config import settings

logger = logging.getLogger(__name__)

# Só leitura chega ao banco. A raiz tem que ser consulta; nenhum nó da árvore pode escrever,
# travar linha ou ser comando opaco (o parser não entendeu => não executa).
_RAIZES_PERMITIDAS = (exp.Select, exp.SetOperation)
_NOS_PROIBIDOS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Command,
    exp.Into,  # SELECT ... INTO cria tabela
    exp.Lock,  # SELECT ... FOR UPDATE trava linhas
)


SCHEMA_NEGOCIO = "negocio"
# Funções que o sqlglot tipa (Sum, Avg, Round, Lag...) são SQL padrão e passam. Desconhecidas
# (Anonymous) só com allowlist: é onde moram as que executam SQL em texto (query_to_xml,
# ts_stat...) e escapariam da checagem de schema.
_FUNCOES_DESCONHECIDAS_PERMITIDAS = frozenset({"age", "make_date"})
# Defesa extra mesmo se o sqlglot passar a tipar alguma: administrativas/I-O do Postgres,
# large objects, dblink; set_config poderia desligar o read-only default da conexão no pool.
_PREFIXOS_FUNCAO_PROIBIDA = ("pg_", "lo_", "dblink")
_FUNCOES_PROIBIDAS = frozenset({"set_config"})


class SQLRejeitado(ValueError):
    """SQL barrado pelo guardrail antes de tocar o banco; a mensagem é legível para o agente."""


def _uma_instrucao(sql: str) -> exp.Expr:
    try:
        instrucoes = [i for i in sqlglot.parse(sql, read="postgres") if i is not None]
    except SqlglotError as exc:
        raise SQLRejeitado("SQL inválido: não foi possível interpretar a consulta") from exc
    if len(instrucoes) != 1:
        raise SQLRejeitado("envie exatamente uma instrução SQL por vez")
    return instrucoes[0]


def validar_sql(sql: str, teto_linhas: int) -> str:
    """Guardrail determinístico aplicado ANTES do banco; devolve o SQL que será executado."""
    arvore = _uma_instrucao(sql)
    if not isinstance(arvore, _RAIZES_PERMITIDAS):
        raise SQLRejeitado("só consultas SELECT/WITH são permitidas")
    proibido = next((n for n in arvore.walk() if isinstance(n, _NOS_PROIBIDOS)), None)
    if proibido is not None:
        raise SQLRejeitado(f"operação não permitida em consulta somente-leitura: {proibido.key}")
    _checar_funcoes(arvore)
    _checar_schema(arvore)

    arvore = _garantir_limit(arvore, teto_linhas)
    # Sem comentários: o que vai ao banco é só o que o guardrail validou.
    return arvore.sql(dialect="postgres", comments=False)


def _checar_funcoes(arvore: exp.Expr) -> None:
    for fn in arvore.find_all(exp.Func):
        desconhecida = isinstance(fn, exp.Anonymous)
        nome = (fn.name if desconhecida else fn.sql_name()).lower()
        if (
            (desconhecida and nome not in _FUNCOES_DESCONHECIDAS_PERMITIDAS)
            or nome in _FUNCOES_PROIBIDAS
            or nome.startswith(_PREFIXOS_FUNCAO_PROIBIDA)
        ):
            raise SQLRejeitado(f"função não permitida: {nome}")


def _checar_schema(arvore: exp.Expr) -> None:
    """Só o schema negocio; sem qualificação resolve pelo search_path=negocio da execução."""
    for tabela in arvore.find_all(exp.Table):
        schema = tabela.db
        if tabela.catalog or (schema and schema != SCHEMA_NEGOCIO):
            raise SQLRejeitado(f"só o schema {SCHEMA_NEGOCIO} é consultável ({tabela.sql()})")
        # Sem schema, pg_* cairia no pg_catalog (sempre no search_path implícito).
        if not schema and tabela.name.lower().startswith("pg_"):
            raise SQLRejeitado(f"só o schema {SCHEMA_NEGOCIO} é consultável ({tabela.name})")


def _garantir_limit(arvore: exp.Expr, teto_linhas: int) -> exp.Expr:
    limit = arvore.args.get("limit")
    if limit is None:
        return arvore.limit(teto_linhas)  # type: ignore[attr-defined]  # Select/SetOperation
    valor = limit.expression
    if not (isinstance(valor, exp.Literal) and valor.is_int):
        raise SQLRejeitado("LIMIT precisa ser um número inteiro literal")
    if int(valor.this) > teto_linhas:
        limit.set("expression", exp.Literal.number(teto_linhas))
    return arvore


class ErroExecucaoSQL(RuntimeError):
    """Falha no banco traduzida em mensagem de domínio — sem SQL cru nem detalhe do driver."""


_MENSAGENS_SQLSTATE = {
    "57014": "consulta cancelada: tempo limite excedido (simplifique ou agregue mais)",
    "25006": "escrita bloqueada: a conexão é somente-leitura",
    "42P01": "tabela inexistente no schema negocio (SQLSTATE 42P01)",
    "42703": "coluna inexistente: confira os nomes das colunas (SQLSTATE 42703)",
    "42883": "função inexistente ou com argumentos de tipo errado (SQLSTATE 42883)",
    "22012": "divisão por zero: proteja o denominador com NULLIF (SQLSTATE 22012)",
}


def _traduzir(exc: DBAPIError) -> ErroExecucaoSQL:
    sqlstate = getattr(exc.orig, "sqlstate", None) or "desconhecido"
    # Erro esperado (timeout, tabela errada) é rotina do agente: uma linha; detalhe só em DEBUG.
    logger.warning("run_sql falhou no banco (SQLSTATE %s)", sqlstate)
    logger.debug("detalhe da falha do run_sql", exc_info=exc)
    mensagem = _MENSAGENS_SQLSTATE.get(
        sqlstate, f"consulta rejeitada pelo banco (SQLSTATE {sqlstate})"
    )
    return ErroExecucaoSQL(mensagem)


@dataclass(frozen=True)
class ResultadoSQL:
    sql: str  # o SQL efetivamente executado (pós-guardrail), p/ ser inspecionável
    colunas: list[str]
    # Valores vêm do driver com tipos variados (Decimal, date, str...) conforme a coluna.
    linhas: list[dict[str, Any]]


async def executar_sql(
    engine: AsyncEngine, sql: str, teto_linhas: int, timeout_ms: int
) -> ResultadoSQL:
    """Valida, e só então executa numa transação READ ONLY com timeout e search_path=negocio."""
    sql_final = validar_sql(sql, teto_linhas)
    try:
        async with engine.connect() as conn, conn.begin():
            # Reforço NO banco, independente do guardrail acima e do papel agente_ro.
            await conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            await conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            await conn.exec_driver_sql(f"SET LOCAL search_path = {SCHEMA_NEGOCIO}")
            resultado = await conn.exec_driver_sql(sql_final)
            colunas = list(resultado.keys())
            linhas = [dict(zip(colunas, linha, strict=True)) for linha in resultado.fetchall()]
    except DBAPIError as exc:
        raise _traduzir(exc) from None
    except (SQLAlchemyError, OSError) as exc:
        # Pool esgotado, conexão recusada: o banco nem respondeu à consulta.
        logger.warning("run_sql sem conexão com o banco (%s)", type(exc).__name__)
        raise ErroExecucaoSQL("banco de dados indisponível no momento") from None
    return ResultadoSQL(sql=sql_final, colunas=colunas, linhas=linhas)


def criar_tool_run_sql(engine: AsyncEngine) -> BaseTool:
    """Tool LangChain ligada à engine do agente_ro; o nó do grafo decide quando chamá-la."""

    @tool
    async def run_sql(sql: str) -> dict[str, Any]:
        """Executa UMA consulta SELECT/WITH somente-leitura no schema negocio do Postgres.

        Use para medir KPIs (faturamento, metas, pedidos) por região, produto ou canal.
        Tabelas podem ser referenciadas sem schema. O resultado é limitado em linhas e tempo;
        prefira agregar no SQL. Em caso de erro, devolve {"erro": motivo} para corrigir a consulta.

        Args:
            sql: uma única consulta SELECT ou WITH, sem ponto e vírgula extra.
        """
        try:
            r = await executar_sql(engine, sql, settings.sql_teto_linhas, settings.sql_timeout_ms)
        except (SQLRejeitado, ErroExecucaoSQL) as exc:
            return {"erro": str(exc)}
        return {"sql": r.sql, "colunas": r.colunas, "linhas": r.linhas}

    return run_sql
