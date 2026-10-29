from __future__ import annotations

import pytest
from fakes import EngineFake, erro_pg
from sqlalchemy.exc import TimeoutError as PoolTimeout

from agent.tools.run_sql import (
    ErroExecucaoSQL,
    SQLRejeitado,
    criar_tool_run_sql,
    executar_sql,
)
from app.config import settings


async def test_consulta_roda_em_transacao_somente_leitura_com_timeout():
    engine = EngineFake(colunas=["regiao", "total"], linhas=[("Sul", 10.5), ("Norte", 3.0)])

    resultado = await executar_sql(engine, "SELECT regiao, total FROM vendas", 500, 5000)  # type: ignore[arg-type]

    assert engine.executados[:3] == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = 5000",
        "SET LOCAL search_path = negocio",
    ]
    assert engine.executados[3] == resultado.sql
    assert resultado.sql.endswith("LIMIT 500")
    assert resultado.colunas == ["regiao", "total"]
    assert resultado.linhas == [{"regiao": "Sul", "total": 10.5}, {"regiao": "Norte", "total": 3.0}]


async def test_sql_rejeitado_nem_abre_conexao():
    engine = EngineFake()

    with pytest.raises(SQLRejeitado):
        await executar_sql(engine, "DELETE FROM pedidos", 500, 5000)  # type: ignore[arg-type]

    assert engine.conexoes == 0


@pytest.mark.parametrize(
    ("sqlstate", "mensagem"),
    [
        ("57014", "tempo limite"),  # query_canceled (statement_timeout)
        ("25006", "somente-leitura"),  # read_only_sql_transaction
        ("42P01", "42P01"),  # undefined_table: só o código, p/ o agente corrigir
    ],
)
async def test_erro_do_banco_vira_erro_de_dominio_sem_vazar_sql(sqlstate, mensagem):
    engine = EngineFake(erro=erro_pg(sqlstate))

    with pytest.raises(ErroExecucaoSQL, match=mensagem) as exc:
        await executar_sql(engine, "SELECT * FROM pedidos", 500, 5000)  # type: ignore[arg-type]

    assert "segredo" not in str(exc.value)


async def test_tool_langchain_devolve_resultado_e_sql_executado():
    engine = EngineFake(colunas=["total"], linhas=[(42,)])
    run_sql = criar_tool_run_sql(engine)  # type: ignore[arg-type]

    saida = await run_sql.ainvoke({"sql": "SELECT count(*) AS total FROM pedidos"})

    assert run_sql.name == "run_sql"
    assert saida["linhas"] == [{"total": 42}]
    assert saida["sql"].endswith(f"LIMIT {settings.sql_teto_linhas}")


async def test_tool_langchain_devolve_erro_legivel_em_vez_de_explodir():
    run_sql = criar_tool_run_sql(EngineFake())  # type: ignore[arg-type]

    saida = await run_sql.ainvoke({"sql": "DROP TABLE pedidos"})

    assert saida == {"erro": "só consultas SELECT/WITH são permitidas"}


@pytest.mark.parametrize(
    ("sqlstate", "dica"),
    [("42703", "coluna"), ("42883", "função"), ("22012", "divisão por zero")],
)
async def test_erros_comuns_trazem_dica_para_o_agente_corrigir(sqlstate, dica):
    engine = EngineFake(erro=erro_pg(sqlstate))

    with pytest.raises(ErroExecucaoSQL, match=dica):
        await executar_sql(engine, "SELECT * FROM pedidos", 500, 5000)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "erro", [OSError("conexão recusada senha=segredo"), PoolTimeout("pool esgotado segredo")]
)
async def test_falha_de_conexao_vira_erro_de_dominio(erro):
    engine = EngineFake(erro=erro)

    with pytest.raises(ErroExecucaoSQL, match="indisponível") as exc:
        await executar_sql(engine, "SELECT * FROM pedidos", 500, 5000)  # type: ignore[arg-type]

    assert "segredo" not in str(exc.value)
