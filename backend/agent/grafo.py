"""Grafo determinístico do agente (rules/agente.md): topologia fixa, LLM só dentro dos nós."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any, Literal, Protocol

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.composto import ItemCatalogo, detectar_composto
from agent.conversa import chave, herdar, ja_recomendadas
from agent.grounding import (
    MotivoAusencia,
    acompanhamento_markdown,
    Recomendacao,
    citacoes_de_prescricao,
    citacoes_sem_fonte,
    citacoes_validas,
    historico_markdown,
    normalizar,
    recomendacoes_markdown,
    validar_recomendacoes,
)
from agent.kpis import (
    Classificacao,
    KpiAvaliado,
    avaliar,
    avaliar_composto,
    avaliar_recorte,
    pares,
    sql_kpi,
    sql_kpi_composto,
)
from agent.periodo import Recorte, mes_nomeado, periodo_alvo, recorte_citado
from agent.rastro import como_tool
from agent.state import EstadoAgente, Plano
from agent.tools.run_sql import ErroExecucaoSQL, ResultadoSQL, SQLRejeitado, executar_sql
from agent.tools.search import Dimensao, Embedder, ErroBusca, Hit, buscar

logger = logging.getLogger(__name__)

# eixo do KPI -> campo de dimensão no payload do Qdrant (categoria vive em `produto`).
_CAMPO_DIMENSAO = {"regiao": "regiao", "canal": "canal", "categoria": "produto"}


class Planejador(Protocol):
    async def planejar(self, pergunta: str) -> Plano: ...


class Recomendador(Protocol):
    async def recomendar(self, contexto: str) -> list[Recomendacao]: ...


class Redator(Protocol):
    def redigir(self, pergunta: str, contexto: str) -> AsyncIterator[str]: ...


@dataclass(frozen=True)
class ConfigAnalise:
    meses_janela: int = 27  # recentes + os mesmos meses dos 2 anos anteriores (sazonalidade)
    meses_janela_recorte: int = 39  # período citado: 3 anos de ocorrências + o ano corrente
    meses_recentes: int = 3  # base do gap médio e da comparação com anos anteriores
    meses_serie: int = 6  # série exibida no relatório/UI
    limiar_fraco: float = 0.10  # gap vs meta pior que -10% = abaixo da meta
    limiar_yoy: float = 0.10  # e caindo mais de 10% vs anos anteriores = queda real (ADR 0007)
    max_fracos: int = 3
    sql_teto_linhas: int = 500
    sql_timeout_ms: int = 15000
    search_limite: int = 5


@dataclass(frozen=True)
class Dependencias:
    engine: AsyncEngine
    qdrant: AsyncQdrantClient
    embedder: Embedder | None
    planejador: Planejador
    redator: Redator
    recomendador: Recomendador
    config: ConfigAnalise = ConfigAnalise()
    hoje: Callable[[], date] = date.today  # injetável: período-alvo testável


_norm = normalizar


def selecionar_fracos(
    avaliados: list[KpiAvaliado],
    valor: str | None,
    maximo: int,
    classificacao: Classificacao = "queda_real",
) -> list[KpiAvaliado]:
    """Pior primeiro; recorte por valor citado (ex.: 'sul' casa 'Sul') aplicado sobre o dado."""
    if valor:
        alvo = _norm(valor)
        filtrados = [a for a in avaliados if _norm(a.dimensao) == alvo]
        avaliados = filtrados or avaliados  # valor desconhecido: não zera a análise
    escolhidos = [a for a in avaliados if a.classificacao == classificacao]
    return sorted(escolhidos, key=lambda a: a.gap_medio)[:maximo]


# Vocabulário do domínio comercial: presença dele veta a clarificação (o LLM rápido marcou
# "como melhorar minhas vendas?" como fora do escopo no smoke real).
_DOMINIO = re.compile(
    r"\b(vend\w*|fatur\w*|receita|ticket|recompra\w*|convers\w*|clientes?|pedidos?|lojas?"
    r"|canal|canais|regi\w*|marketplace|site|categori\w*|produtos?|metas?|kpis?|indicador\w*"
    r"|campanha\w*|comercial)\b"
)


def parece_do_dominio(pergunta: str) -> bool:
    return bool(_DOMINIO.search(normalizar(pergunta)))


# Catálogo das dimensões vindo do próprio banco: é dele que saem os ids do recorte composto.
_SQL_CATALOGO = (
    "SELECT 'regiao' AS eixo, id, nome FROM regioes UNION ALL "
    "SELECT 'canal', id, nome FROM canais UNION ALL "
    "SELECT 'categoria', id, nome FROM categorias"
)

# Prefixos dos avisos de furo de grounding: o eval (ADR 0011) os importa — mudar aqui é seguro.
AVISOS_GROUNDING = (
    "citação sem fonte recuperada no texto",
    "prescrição citada fora da seção de recomendações",
)

_CLARIFICACAO = (
    "Não consegui relacionar a pergunta aos indicadores comerciais que analiso. "
    # Sem "propor melhorias": o judge leu isso como recomendação (pergunta fora do escopo não
    # recebe recomendação nenhuma).
    "Analiso faturamento, ticket médio, taxa de recompra e taxa de conversão — no total ou por "
    "região, canal ou categoria de produto. "
    "Qual desses indicadores (e, se quiser, qual recorte) você quer analisar?"
)


def _premissas(
    plano: Plano,
    executados: list[tuple[str, str]],
    avaliados: list[KpiAvaliado],
    hoje: date,
    cfg: ConfigAnalise,
    recorte: Recorte | None = None,
) -> dict[str, Any]:
    """Defaults resolvidos em código e declarados — o escopo é o que de fato rodou."""
    meses = sorted({p["mes"] for a in avaliados for p in a.serie})
    recentes = meses[-cfg.meses_recentes :]
    return {
        "periodo_alvo": periodo_alvo(hoje, plano.mes_citado),
        "origem_periodo": (
            "herdado do turno anterior"
            if "mes_citado" in plano.herdados
            else "mês citado na pergunta, próxima ocorrência"
            if plano.mes_citado
            else "mês atual + 1"
        ),
        "kpis": sorted({k for k, _ in executados}),
        "eixos": sorted({e for _, e in executados}),
        "valor": plano.valor,
        "herdado": list(plano.herdados),
        "dados_ate": meses[-1] if meses else None,
        "janela_recente": f"{recentes[0]} a {recentes[-1]}" if recentes and not recorte else None,
        "recorte": _descrever_recorte(recorte, avaliados) if recorte else None,
        "comparacao_sazonal": (
            "período citado vs o resto do mesmo ano"
            if recorte and recorte.tipo == "padrao"
            else "mês-alvo contra a meta nos anos anteriores"
            if recorte and recorte.tipo == "mes_alvo"
            else "comparação com os mesmos meses dos 2 anos anteriores"
        ),
        "criterio_queda_real": (
            f"desvio vs meta pior que -{cfg.limiar_fraco:.0%} e queda pior que "
            f"-{cfg.limiar_yoy:.0%} vs os mesmos meses dos anos anteriores"
        ),
    }


_NOME_MES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def _descrever_recorte(recorte: Recorte, avaliados: list[KpiAvaliado]) -> str:
    nomes = (
        "outubro a dezembro" if recorte.meses == (10, 11, 12) else _NOME_MES[recorte.meses[0] - 1]
    )
    meses = sorted({p["mes"] for a in avaliados for p in a.serie})
    analisados = f" (analisado: {', '.join(meses)})" if meses else ""
    if recorte.tipo == "passado":
        return f"{nomes} de {recorte.ano} vs o mesmo período do ano anterior{analisados}"
    if recorte.tipo == "padrao":
        return f"{nomes}: padrão recorrente vs o resto do ano{analisados}"
    return f"{nomes} nos anos anteriores, como risco para o período-alvo{analisados}"


def _premissas_markdown(p: dict[str, Any]) -> str:
    kpis = ", ".join(p["kpis"]) or "nenhum"
    origem_valor = "herdado do turno anterior" if "valor" in p.get("herdado", []) else "citado"
    recorte = ", ".join(p["eixos"]) + (
        f" (valor {origem_valor}: {p['valor']})" if p["valor"] else ""
    )
    if "kpis" in p.get("herdado", []):
        kpis += " (herdado do turno anterior)"
    return (
        "## Premissas\n\n"
        f"- **Período-alvo:** {p['periodo_alvo']} ({p['origem_periodo']})\n"
        f"- **Escopo:** {kpis} · recorte: {recorte}\n"
        f"- **Dados disponíveis até:** {p['dados_ate'] or 'sem dados'}"
        + (f" · janela recente: {p['janela_recente']}" if p.get("janela_recente") else "")
        + "\n"
        + (f"- **Recorte citado:** {p['recorte']}\n" if p.get("recorte") else "")
        + f"- **Sazonalidade:** {p['comparacao_sazonal']}\n"
        f"- **Queda real:** {p['criterio_queda_real']}\n\n"
    )


def _kpi_para_llm(a: KpiAvaliado, situacao: str) -> dict[str, Any]:
    # Números já calculados: o LLM não deduz da série (leu errado no smoke real da #4).
    return {
        "kpi": a.kpi,
        "eixo": a.eixo,
        "dimensao": a.dimensao,
        "situacao": situacao,
        # Recorte composto não tem meta própria: só o ano a ano (#9b).
        "desvio_vs_meta_pct": None if a.sem_meta else round(a.gap_medio * 100, 1),
        **({"sem_meta_composta": True} if a.sem_meta else {}),
        "variacao_vs_anos_anteriores_pct": (
            round(a.variacao_yoy * 100, 1) if a.variacao_yoy is not None else None
        ),
        "serie_mensal": a.serie,
        **({"ano_referencia": a.ano_referencia} if a.ano_referencia else {}),
        **(
            {
                "vs_resto_do_ano_pct": {
                    str(k): round(v * 100, 1) for k, v in a.vs_resto_do_ano.items()
                }
            }
            if a.vs_resto_do_ano
            else {}
        ),
    }


def _contexto_relatorio(estado: EstadoAgente) -> str:
    """Só o que foi medido e recuperado neste run vai ao LLM redator."""
    return json.dumps(
        {
            # Premissas já estão no topo (código); o redator só precisa do período-alvo.
            "periodo_alvo": estado.get("premissas", {}).get("periodo_alvo"),
            "recorte": estado.get("premissas", {}).get("recorte"),
            "kpis_queda_real": [
                _kpi_para_llm(
                    f,
                    "no período citado, historicamente abaixo da meta: risco para o período-alvo"
                    if f.classificacao == "risco_sazonal"
                    else "abaixo da meta e caindo vs o mesmo período dos anos anteriores",
                )
                for f in estado.get("fracos", [])
            ],
            # Saudáveis do recorte: o redator afirma "acima/dentro da meta" com número (#9, N4).
            "kpis_saudaveis": [
                _kpi_para_llm(
                    k,
                    "recorte composto sem meta própria: avaliado só pelo ano a ano (sem queda real)"
                    if k.sem_meta
                    else "dentro ou acima da meta",
                )
                for k in estado.get("saudaveis", [])
            ],
            "kpis_padrao_do_recorte": [
                _kpi_para_llm(k, "padrão recorrente do período citado vs o resto do ano")
                for k in estado.get("padroes", [])
            ],
            "kpis_sazonais": [
                _kpi_para_llm(s, "abaixo da meta, em linha com o mesmo período dos anos anteriores")
                for s in estado.get("sazonais", [])
            ],
            # Só diagnóstico: prescrição é assunto da seção validada, não do texto livre.
            "fontes_recuperadas": [
                asdict(h)
                for h in estado.get("hits", [])
                if h.metadados.get("colecao") == "diagnostico"
            ],
            "avisos": estado.get("avisos", []),
        },
        ensure_ascii=False,
        default=str,
    )


def _contexto_recomendacao(estado: EstadoAgente) -> str:
    """Só KPIs em queda real e as fontes do run: a origem (para_kpi/para_dimensao) vai junto."""
    return json.dumps(
        {
            "periodo_alvo": estado.get("premissas", {}).get("periodo_alvo"),
            "kpis_queda_real": [
                _kpi_para_llm(
                    f,
                    "risco para o período-alvo: historicamente fraco, preparar"
                    if f.classificacao == "risco_sazonal"
                    else "queda real",
                )
                for f in estado.get("fracos", [])
            ],
            "fontes": [
                asdict(h)
                for h in estado.get("hits", [])
                if h.metadados.get("subtipo") != "relatorio_anterior"
            ],
            # O que já foi dito: não repetir tal e qual; marcar retomada (RF-09).
            "relatorios_anteriores": [
                h.texto
                for h in estado.get("hits", [])
                if h.metadados.get("subtipo") == "relatorio_anterior"
            ],
            "ja_recomendado_nesta_conversa": (
                turno.recomendacoes if (turno := estado.get("turno_anterior")) else []
            ),
        },
        ensure_ascii=False,
        default=str,
    )


def construir_grafo(deps: Dependencias) -> CompiledStateGraph[Any, Any, Any, Any]:
    cfg = deps.config
    cache_catalogo: list[ItemCatalogo] = []

    async def sql(consulta: str, nome: str = "run_sql") -> ResultadoSQL:
        """run_sql com os guardrails de sempre, registrado como tool no rastro do run."""
        return await como_tool(
            nome,
            {"sql": consulta},
            executar_sql(deps.engine, consulta, cfg.sql_teto_linhas, cfg.sql_timeout_ms),
            lambda r: {"sql": r.sql, "linhas": len(r.linhas)},
        )

    async def catalogo() -> list[ItemCatalogo]:
        """Nomes e ids de regiões/canais/categorias (uma vez por grafo); falha = vazio."""
        if not cache_catalogo:
            try:
                # Nome próprio: só o 1º run de cada grafo consulta (cache).
                r = await sql(_SQL_CATALOGO, "run_sql:catalogo")
            except (SQLRejeitado, ErroExecucaoSQL):
                logger.warning("catálogo de dimensões indisponível; sem recorte composto")
                return []
            itens = list(
                dict.fromkeys(
                    ItemCatalogo(ln["eixo"], int(ln["id"]), str(ln["nome"]))
                    for ln in r.linhas
                    if {"eixo", "id", "nome"} <= ln.keys()
                    and ln["eixo"] in ("regiao", "canal", "categoria")
                )
            )
            # Runs concorrentes podem chegar aqui juntos: atribuição única, nunca duplicar
            # (item duplicado = "duas categorias" = composto desligado em silêncio).
            if not cache_catalogo:
                cache_catalogo[:] = itens
        return cache_catalogo

    async def planejar(estado: EstadoAgente) -> dict[str, Any]:
        get_stream_writer()({"tipo": "etapa", "nome": "planejar"})
        plano = await deps.planejador.planejar(estado["pergunta"])
        # Mês-alvo vem do texto, não do LLM (no smoke ele "citou" 11 para "próximo mês").
        plano = plano.model_copy(
            update={
                "mes_citado": mes_nomeado(estado["pergunta"]),
                # Barreira em código: pergunta com vocabulário de vendas nunca vira clarificação.
                "fora_do_escopo": plano.fora_do_escopo
                and not parece_do_dominio(estado["pergunta"]),
            }
        )
        # Acompanhamento herda do turno anterior o que a pergunta nova não cita (código, #7).
        plano = herdar(plano, estado.get("turno_anterior"))
        # Período citado vem do texto, em código (#9).
        recorte = recorte_citado(estado["pergunta"], deps.hoje())
        if recorte and recorte.tipo == "passado" and plano.mes_citado:
            # "julho de 2025" é sobre o passado: não vira mês-alvo (nem é herdado depois).
            plano = plano.model_copy(update={"mes_citado": None})
        if recorte is None and plano.mes_citado and "mes_citado" in plano.herdados:
            # Mês-alvo herdado do turno anterior: a análise usa o mesmo recorte que a premissa.
            recorte = Recorte("mes_alvo", (plano.mes_citado,))
        get_stream_writer()({"tipo": "plano", "plano": plano.model_dump()})
        return {"plano": plano, "recorte": recorte}

    async def perna_quantitativa(estado: EstadoAgente) -> dict[str, Any]:
        escrever = get_stream_writer()
        escrever({"tipo": "etapa", "nome": "perna_quantitativa"})
        plano = estado["plano"]
        recorte: Recorte | None = estado.get("recorte")
        janela = cfg.meses_janela_recorte if recorte else cfg.meses_janela
        if recorte and recorte.ano:
            # Período passado: a janela cobre o ano pedido e os 2 anteriores (base do ano a ano).
            hoje = deps.hoje()
            janela = max(janela, min(60, 12 * (hoje.year - (recorte.ano - 2)) + hoje.month))
        dados_ate: str | None = None
        avaliados: list[KpiAvaliado] = []
        sqls: list[str] = []
        avisos: list[str] = []
        executados = pares(list(plano.kpis), plano.eixo)
        # Categoria + canal/região citados: o recorte pedido é o composto (#9b, em código).
        itens_catalogo = await catalogo()
        composto = detectar_composto(estado["pergunta"], itens_catalogo)
        if composto is None and plano.valor and "valor" in plano.herdados:
            # Acompanhamento ("e em 2024?") de um recorte composto continua composto.
            composto = detectar_composto(plano.valor, itens_catalogo)
        if composto and plano.kpis and "faturamento" not in plano.kpis:
            avisos.append(
                "recorte composto (categoria × canal/região) só existe para faturamento; "
                "analisado pelo eixo simples"
            )
            composto = None
        if composto:
            if fora := [k for k in plano.kpis if k != "faturamento"]:
                avisos.append(
                    f"recorte composto só existe para faturamento; não analisado: {', '.join(fora)}"
                )
            executados = [("faturamento", f"categoria×{composto.eixo_b}")]
            plano = plano.model_copy(update={"valor": composto.rotulo, "kpis": ["faturamento"]})
        if not composto and plano.eixo and plano.eixo not in {e for _, e in executados}:
            avisos.append(
                f"sem meta por {plano.eixo} para o KPI pedido; analisado nos eixos com meta"
            )
        for kpi, eixo in executados if not composto else []:
            # Recorte de mês ou período passado só usa os seus meses; o padrão ("fim do ano")
            # compara com o resto do ano e precisa de todos (#29).
            meses_do_ano = recorte.meses if recorte and recorte.tipo != "padrao" else ()
            r = await sql(sql_kpi(kpi, eixo, janela, meses_do_ano))
            sqls.append(r.sql)
            escrever({"tipo": "sql", "kpi": kpi, "eixo": eixo, "sql": r.sql})
            # "dados até" vem do banco (coluna dados_ate), não da série exibida (recorte).
            meses_sql = [str(ln.get("dados_ate") or ln["mes"]) for ln in r.linhas]
            if meses_sql:
                dados_ate = max([dados_ate or "", *meses_sql])
            if len(r.linhas) >= cfg.sql_teto_linhas:
                avisos.append(
                    f"{kpi} por {eixo}: resultado no teto de linhas, série pode estar cortada"
                )
            avaliados += (
                avaliar_recorte(kpi, eixo, r.linhas, recorte, cfg.limiar_fraco, cfg.limiar_yoy)
                if recorte
                else avaliar(
                    kpi,
                    eixo,
                    r.linhas,
                    cfg.meses_recentes,
                    cfg.limiar_fraco,
                    cfg.limiar_yoy,
                    cfg.meses_serie,
                )
            )
        if composto:
            r = await sql(sql_kpi_composto(composto, janela))
            sqls.append(r.sql)
            escrever({"tipo": "sql", "kpi": "faturamento", "eixo": executados[0][1], "sql": r.sql})
            if meses_sql := [str(ln["mes"]) for ln in r.linhas]:
                dados_ate = max([dados_ate or "", *meses_sql])
            avaliados += avaliar_composto(
                composto.eixo_b,
                composto.filtro,
                r.linhas,
                recorte,
                cfg.meses_recentes,
                cfg.limiar_yoy,
            )
        if recorte and not avaliados and executados:
            avisos.append("recorte citado sem dados completos no período; nada a comparar")
        elif composto and not avaliados:
            avisos.append(f"'{composto.rotulo}' sem histórico comparável; nada a classificar")
        if (
            plano.valor
            and not composto
            and not any(_norm(a.dimensao) == _norm(plano.valor) for a in avaliados)
        ):
            avisos.append(
                f"'{plano.valor}' não existe nos eixos analisados; a análise cobre todos os valores"
            )
        for aviso in avisos:
            escrever({"tipo": "aviso", "mensagem": aviso})
        # Queda real e risco sazonal do mês-alvo pedem causa e prescrição; o resto é descritivo.
        fracos = sorted(
            selecionar_fracos(avaliados, plano.valor, cfg.max_fracos)
            + selecionar_fracos(avaliados, plano.valor, cfg.max_fracos, "risco_sazonal"),
            key=lambda a: a.gap_medio,
        )[: cfg.max_fracos]
        sazonais = selecionar_fracos(avaliados, plano.valor, cfg.max_fracos, "sazonal")
        saudaveis = sorted(
            selecionar_fracos(avaliados, plano.valor, len(avaliados), "saudavel"),
            key=lambda a: a.gap_medio,
            reverse=True,  # melhor desempenho primeiro
        )[: cfg.max_fracos]
        padroes = selecionar_fracos(avaliados, plano.valor, cfg.max_fracos, "padrao")
        premissas = _premissas(plano, executados, avaliados, deps.hoje(), cfg, recorte)
        premissas["dados_ate"] = dados_ate
        escrever({"tipo": "premissas", "premissas": premissas})
        return {
            "plano": plano,
            "fracos": fracos,
            "sazonais": sazonais,
            "saudaveis": saudaveis,
            "padroes": padroes,
            "premissas": premissas,
            "sqls": sqls,
            "avisos": avisos,
        }

    def rota_pos_plano(estado: EstadoAgente) -> Literal["clarificar", "perna_quantitativa"]:
        # Nada resolvível: pergunta de volta em vez de relatório inventado.
        return "clarificar" if estado["plano"].fora_do_escopo else "perna_quantitativa"

    async def clarificar(estado: EstadoAgente) -> dict[str, Any]:
        escrever = get_stream_writer()
        escrever({"tipo": "etapa", "nome": "clarificar"})
        escrever({"tipo": "relatorio", "texto": _CLARIFICACAO})
        return {"relatorio": _CLARIFICACAO}

    def rota_pos_quantitativa(estado: EstadoAgente) -> Literal["enriquecer", "relatorio"]:
        # Data-driven: só queda real tem "porquê" a explicar e prescrição a buscar.
        return "enriquecer" if estado.get("fracos") else "relatorio"

    async def enriquecer(estado: EstadoAgente) -> dict[str, Any]:
        escrever = get_stream_writer()
        escrever({"tipo": "etapa", "nome": "enriquecer"})
        if deps.embedder is None:
            aviso = "busca semântica indisponível (sem chave)"
            escrever({"tipo": "aviso", "mensagem": aviso})
            return {"hits": [], "avisos": [aviso]}
        hits: dict[str, Hit] = {}
        avisos: list[str] = []
        for fraco in estado.get("fracos", []):
            if fraco.eixo == "total":
                continue  # sem dimensão não há filtro válido (ADR 0005)
            dimensao = (
                Dimensao(**fraco.filtro)  # composto: as duas dimensões em AND (ADR 0005)
                if fraco.filtro
                else Dimensao(**{_CAMPO_DIMENSAO[fraco.eixo]: fraco.dimensao})
            )
            consulta = f"{estado['pergunta']} ({fraco.kpi} em {fraco.dimensao})"
            recorte: Recorte | None = estado.get("recorte")
            # Período citado: o "porquê" é daquele período (periodo_referencia, nunca
            # data_ingestao); o que funcionou pode ser de qualquer época, salvo "X anteriores".
            meses_recorte = sorted({p["mes"] for p in fraco.serie}) if recorte else []
            periodos_por_colecao = {
                "diagnostico": meses_recorte or None,
                "prescricao": meses_recorte if recorte and recorte.anteriores else None,
            }
            for colecao in ("diagnostico", "prescricao"):
                try:
                    achados = await como_tool(
                        "search",
                        {
                            "colecao": colecao,
                            "query": consulta,
                            "kpi_alvo": fraco.kpi,
                            "dimensao": dimensao,
                            "periodos": periodos_por_colecao[colecao],
                        },
                        buscar(
                            deps.qdrant,
                            deps.embedder,
                            colecao,
                            consulta,
                            kpi_alvo=fraco.kpi,
                            dimensao=dimensao,
                            periodos=periodos_por_colecao[colecao],
                            limite=cfg.search_limite,
                        ),
                        lambda hs: {"fontes": [h.fonte for h in hs]},
                    )
                except ErroBusca as exc:
                    # Sem o "porquê" o relatório ainda tem os números: degrada, não aborta.
                    escrever({"tipo": "aviso", "mensagem": f"{colecao}: {exc}"})
                    avisos.append(f"{colecao}: {exc}")
                    continue
                for h in achados:
                    # Origem explícita: o redator não pode atribuir a fonte ao KPI errado.
                    origem: dict[str, Any] = {
                        "colecao": colecao,
                        "para_kpi": fraco.kpi,
                        "para_dimensao": fraco.dimensao,
                    }
                    if h.fonte in hits:  # mesma fonte explica outro KPI fraco: soma o alvo
                        hits[h.fonte].metadados["alvos"].append([fraco.kpi, fraco.dimensao])
                        continue
                    origem["alvos"] = [[fraco.kpi, fraco.dimensao]]
                    hits[h.fonte] = replace(h, metadados={**h.metadados, **origem})
        escrever(
            {
                "tipo": "fontes",
                "fontes": [{"fonte": h.fonte, **h.metadados} for h in hits.values()],
            }
        )
        return {"hits": list(hits.values()), "avisos": avisos}

    async def recomendar(estado: EstadoAgente) -> dict[str, Any]:
        """LLM propõe; o código só deixa passar o que tem fonte de prescrição deste run."""
        escrever = get_stream_writer()
        escrever({"tipo": "etapa", "nome": "recomendar"})
        hits = estado.get("hits", [])
        motivo: MotivoAusencia = "sem_prescricao"
        validas: list[Recomendacao] = []
        avisos: list[str] = []
        if any(h.metadados.get("colecao") == "prescricao" for h in hits):
            try:
                propostas = await deps.recomendador.recomendar(_contexto_recomendacao(estado))
            except Exception as exc:
                logger.warning("recomendador falhou (%s)", type(exc).__name__)
                propostas, motivo = [], "falha"
                avisos.append("recomendações indisponíveis neste run (falha ao gerar)")
            validas, descartadas = validar_recomendacoes(propostas, hits)
            if not propostas and motivo != "falha":
                motivo = "nenhuma_aplicavel"  # houve prescrição, mas o LLM não viu aplicação
            if descartadas:
                if not validas and motivo != "falha":
                    motivo = "descartadas"
                # Ação descartada não vai ao estado (e daí ao redator): só a contagem.
                avisos.append(
                    f"{len(descartadas)} recomendação(ões) descartada(s) sem fonte válida"
                )
                escrever(
                    {
                        "tipo": "recomendacoes_descartadas",
                        "itens": [{"motivo": m, "fonte": r.fonte} for r, m in descartadas],
                    }
                )
                for r, m in descartadas:
                    logger.info("recomendação descartada (%s): %s", m, r.acao)
        # Não repetir: já entregue nesta conversa (código) ou já dito em relatório anterior (LLM).
        ja = ja_recomendadas(estado.get("turno_anterior"))
        retomadas = [r for r in validas if r.retomada or chave(r.fonte, r.kpi, r.dimensao) in ja]
        novas = [r for r in validas if r not in retomadas]
        novas = [r.model_copy(update={"prioridade": i}) for i, r in enumerate(novas, 1)]
        if validas and not novas:
            motivo = "so_retomadas"
        for aviso in avisos:
            escrever({"tipo": "aviso", "mensagem": aviso})
        escrever({"tipo": "recomendacoes", "recomendacoes": [r.model_dump() for r in novas]})
        escrever({"tipo": "retomadas", "retomadas": [r.model_dump() for r in retomadas]})
        return {
            "recomendacoes": novas,
            "retomadas": retomadas,
            "motivo_sem_recomendacao": motivo,
            "avisos": avisos,
        }

    async def relatorio(estado: EstadoAgente) -> dict[str, Any]:
        escrever = get_stream_writer()
        escrever({"tipo": "etapa", "nome": "relatorio"})
        hits = estado.get("hits", [])
        if "recomendacoes" not in estado:  # sem queda real o nó recomendar não roda; UI estável
            escrever({"tipo": "recomendacoes", "recomendacoes": []})

        def emitir(texto: str) -> None:
            if texto:
                partes.append(texto)
                escrever({"tipo": "relatorio", "texto": texto})

        partes: list[str] = []
        # Premissas no topo escritas pelo código: o relatório nunca sai sem elas.
        if estado.get("premissas"):
            emitir(_premissas_markdown(estado["premissas"]))
        narrativa: list[str] = []  # diagnóstico e causas: única parte livre do LLM
        async for delta in deps.redator.redigir(estado["pergunta"], _contexto_relatorio(estado)):
            narrativa.append(delta)
            emitir(delta)
        emitir("\n\n")
        # Seções prescritivas escritas pelo código, só com o que passou na validação.
        recomendacoes = estado.get("recomendacoes", [])
        emitir(
            recomendacoes_markdown(
                recomendacoes,
                tem_queda_real=bool(estado.get("fracos")),
                motivo=estado.get("motivo_sem_recomendacao", "sem_prescricao"),
            )
        )
        emitir(acompanhamento_markdown(estado.get("retomadas", [])))
        emitir(historico_markdown(hits) if estado.get("fracos") else "")
        texto_livre = "".join(narrativa)
        prescricoes_no_texto = citacoes_de_prescricao(texto_livre, hits)
        avisos = [f"{AVISOS_GROUNDING[0]}: {c}" for c in citacoes_sem_fonte(texto_livre, hits)] + [
            f"{AVISOS_GROUNDING[1]}: {c}" for c in prescricoes_no_texto
        ]
        for aviso in avisos:
            escrever({"tipo": "aviso", "mensagem": aviso})
        if avisos:
            emitir(
                "> ⚠️ O diagnóstico/causas acima cita fonte fora do permitido (não recuperada "
                "neste run, ou prescrição fora da seção validada): "
                + ", ".join(a.split(": ", 1)[1] for a in avisos)
                + ". Desconsidere essa citação.\n"
            )
        # Só o que o relatório exibe: recomendações, histórico (prescrições com resultado) e
        # citações do texto livre que são diagnóstico (prescrição no texto livre não conta).
        historico = [
            h.fonte
            for h in hits
            if estado.get("fracos")
            and h.metadados.get("colecao") == "prescricao"
            and h.metadados.get("resultado")
        ]
        citadas = list(
            dict.fromkeys(
                [r.fonte for r in recomendacoes]
                + [r.fonte for r in estado.get("retomadas", [])]
                + historico
                + [c for c in citacoes_validas(texto_livre, hits) if c not in prescricoes_no_texto]
            )
        )
        escrever({"tipo": "fontes_citadas", "fontes": citadas})
        return {"relatorio": "".join(partes), "avisos": avisos}

    # As stubs do langgraph 1.2.6 rejeitam no mypy até o exemplo canônico de add_node
    # (TypedDictLike); o ignore é só nessa linha — o resto da cadeia fica tipado como Any.
    return (
        StateGraph(EstadoAgente)
        .add_node("planejar", planejar)  # type: ignore[call-overload]
        .add_node("clarificar", clarificar)
        .add_node("perna_quantitativa", perna_quantitativa)
        .add_node("enriquecer", enriquecer)
        .add_node("recomendar", recomendar)
        .add_node("relatorio", relatorio)
        .add_edge(START, "planejar")
        .add_conditional_edges("planejar", rota_pos_plano, ["clarificar", "perna_quantitativa"])
        .add_edge("clarificar", END)
        .add_conditional_edges(
            "perna_quantitativa", rota_pos_quantitativa, ["enriquecer", "relatorio"]
        )
        .add_edge("enriquecer", "recomendar")
        .add_edge("recomendar", "relatorio")
        .add_edge("relatorio", END)
        .compile()
    )
