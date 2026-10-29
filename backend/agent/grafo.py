"""Grafo determinístico do agente (rules/agente.md): topologia fixa, LLM só dentro dos nós."""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any, Literal, Protocol

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.kpis import Classificacao, KpiAvaliado, avaliar, pares, sql_kpi
from agent.periodo import mes_nomeado, periodo_alvo
from agent.state import EstadoAgente, Plano
from agent.tools.run_sql import executar_sql
from agent.tools.search import Dimensao, Embedder, ErroBusca, Hit, buscar

logger = logging.getLogger(__name__)

# eixo do KPI -> campo de dimensão no payload do Qdrant (categoria vive em `produto`).
_CAMPO_DIMENSAO = {"regiao": "regiao", "canal": "canal", "categoria": "produto"}


class Planejador(Protocol):
    async def planejar(self, pergunta: str) -> Plano: ...


class Redator(Protocol):
    def redigir(self, pergunta: str, contexto: str) -> AsyncIterator[str]: ...


@dataclass(frozen=True)
class ConfigAnalise:
    meses_janela: int = 27  # recentes + os mesmos meses dos 2 anos anteriores (sazonalidade)
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
    config: ConfigAnalise = ConfigAnalise()
    hoje: Callable[[], date] = date.today  # injetável: período-alvo testável


def _norm(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.casefold().strip()


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


_CLARIFICACAO = (
    "Não consegui relacionar a pergunta aos indicadores comerciais que analiso. "
    "Consigo explicar e propor melhorias para faturamento, ticket médio, taxa de recompra e "
    "taxa de conversão — no total ou por região, canal ou categoria de produto. "
    "Qual desses indicadores (e, se quiser, qual recorte) você quer melhorar?"
)


def _premissas(
    plano: Plano,
    executados: list[tuple[str, str]],
    avaliados: list[KpiAvaliado],
    hoje: date,
    cfg: ConfigAnalise,
) -> dict[str, Any]:
    """Defaults resolvidos em código e declarados — o escopo é o que de fato rodou."""
    meses = sorted({p["mes"] for a in avaliados for p in a.serie})
    recentes = meses[-cfg.meses_recentes :]
    return {
        "periodo_alvo": periodo_alvo(hoje, plano.mes_citado),
        "origem_periodo": "mês citado na pergunta, próxima ocorrência"
        if plano.mes_citado
        else "mês atual + 1",
        "kpis": sorted({k for k, _ in executados}),
        "eixos": sorted({e for _, e in executados}),
        "valor": plano.valor,
        "dados_ate": meses[-1] if meses else None,
        "janela_recente": f"{recentes[0]} a {recentes[-1]}" if recentes else None,
        "comparacao_sazonal": "mesmos meses dos 2 anos anteriores",
        "criterio_queda_real": (
            f"desvio vs meta pior que -{cfg.limiar_fraco:.0%} e queda pior que "
            f"-{cfg.limiar_yoy:.0%} vs os mesmos meses dos anos anteriores"
        ),
    }


def _premissas_markdown(p: dict[str, Any]) -> str:
    kpis = ", ".join(p["kpis"]) or "nenhum"
    recorte = ", ".join(p["eixos"]) + (f" (valor citado: {p['valor']})" if p["valor"] else "")
    return (
        "## Premissas\n\n"
        f"- **Período-alvo:** {p['periodo_alvo']} ({p['origem_periodo']})\n"
        f"- **Escopo:** {kpis} · recorte: {recorte}\n"
        f"- **Dados disponíveis até:** {p['dados_ate'] or 'sem dados'}"
        f" · janela recente: {p['janela_recente'] or '-'}\n"
        f"- **Sazonalidade:** comparação com os {p['comparacao_sazonal']}\n"
        f"- **Queda real:** {p['criterio_queda_real']}\n\n"
    )


def _kpi_para_llm(a: KpiAvaliado, situacao: str) -> dict[str, Any]:
    # Números já calculados: o LLM não deduz da série (leu errado no smoke real da #4).
    return {
        "kpi": a.kpi,
        "eixo": a.eixo,
        "dimensao": a.dimensao,
        "situacao": situacao,
        "desvio_vs_meta_pct": round(a.gap_medio * 100, 1),
        "variacao_vs_anos_anteriores_pct": (
            round(a.variacao_yoy * 100, 1) if a.variacao_yoy is not None else None
        ),
        "serie_mensal": a.serie,
    }


def _contexto_relatorio(estado: EstadoAgente) -> str:
    """Só o que foi medido e recuperado neste run vai ao LLM redator."""
    return json.dumps(
        {
            # Premissas já estão no topo (código); o redator só precisa do período-alvo.
            "periodo_alvo": estado.get("premissas", {}).get("periodo_alvo"),
            "kpis_queda_real": [
                _kpi_para_llm(f, "abaixo da meta e caindo vs o mesmo período dos anos anteriores")
                for f in estado.get("fracos", [])
            ],
            "kpis_sazonais": [
                _kpi_para_llm(s, "abaixo da meta, em linha com o mesmo período dos anos anteriores")
                for s in estado.get("sazonais", [])
            ],
            "fontes_recuperadas": [asdict(h) for h in estado.get("hits", [])],
            "avisos": estado.get("avisos", []),
        },
        ensure_ascii=False,
        default=str,
    )


def construir_grafo(deps: Dependencias) -> CompiledStateGraph[Any, Any, Any, Any]:
    cfg = deps.config

    async def planejar(estado: EstadoAgente) -> dict[str, Any]:
        get_stream_writer()({"tipo": "etapa", "nome": "planejar"})
        plano = await deps.planejador.planejar(estado["pergunta"])
        # Mês-alvo vem do texto, não do LLM (no smoke ele "citou" 11 para "próximo mês").
        plano = plano.model_copy(update={"mes_citado": mes_nomeado(estado["pergunta"])})
        get_stream_writer()({"tipo": "plano", "plano": plano.model_dump()})
        return {"plano": plano}

    async def perna_quantitativa(estado: EstadoAgente) -> dict[str, Any]:
        escrever = get_stream_writer()
        escrever({"tipo": "etapa", "nome": "perna_quantitativa"})
        plano = estado["plano"]
        avaliados: list[KpiAvaliado] = []
        sqls: list[str] = []
        avisos: list[str] = []
        executados = pares(list(plano.kpis), plano.eixo)
        if plano.eixo and plano.eixo not in {e for _, e in executados}:
            avisos.append(
                f"sem meta por {plano.eixo} para o KPI pedido; analisado nos eixos com meta"
            )
        for kpi, eixo in executados:
            r = await executar_sql(
                deps.engine,
                sql_kpi(kpi, eixo, cfg.meses_janela),
                cfg.sql_teto_linhas,
                cfg.sql_timeout_ms,
            )
            sqls.append(r.sql)
            escrever({"tipo": "sql", "kpi": kpi, "eixo": eixo, "sql": r.sql})
            if len(r.linhas) >= cfg.sql_teto_linhas:
                avisos.append(
                    f"{kpi} por {eixo}: resultado no teto de linhas, série pode estar cortada"
                )
            avaliados += avaliar(
                kpi,
                eixo,
                r.linhas,
                cfg.meses_recentes,
                cfg.limiar_fraco,
                cfg.limiar_yoy,
                cfg.meses_serie,
            )
        if plano.valor and not any(_norm(a.dimensao) == _norm(plano.valor) for a in avaliados):
            avisos.append(
                f"'{plano.valor}' não existe nos eixos analisados; a análise cobre todos os valores"
            )
        for aviso in avisos:
            escrever({"tipo": "aviso", "mensagem": aviso})
        fracos = selecionar_fracos(avaliados, plano.valor, cfg.max_fracos)
        sazonais = selecionar_fracos(avaliados, plano.valor, cfg.max_fracos, "sazonal")
        premissas = _premissas(plano, executados, avaliados, deps.hoje(), cfg)
        escrever({"tipo": "premissas", "premissas": premissas})
        return {
            "fracos": fracos,
            "sazonais": sazonais,
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
            dimensao = Dimensao(**{_CAMPO_DIMENSAO[fraco.eixo]: fraco.dimensao})
            consulta = f"{estado['pergunta']} ({fraco.kpi} em {fraco.dimensao})"
            for colecao in ("diagnostico", "prescricao"):
                try:
                    achados = await buscar(
                        deps.qdrant,
                        deps.embedder,
                        colecao,
                        consulta,
                        kpi_alvo=fraco.kpi,
                        dimensao=dimensao,
                        limite=cfg.search_limite,
                    )
                except ErroBusca as exc:
                    # Sem o "porquê" o relatório ainda tem os números: degrada, não aborta.
                    escrever({"tipo": "aviso", "mensagem": f"{colecao}: {exc}"})
                    avisos.append(f"{colecao}: {exc}")
                    continue
                for h in achados:
                    # Origem explícita: o redator não pode atribuir a fonte ao KPI errado.
                    origem = {
                        "colecao": colecao,
                        "para_kpi": fraco.kpi,
                        "para_dimensao": fraco.dimensao,
                    }
                    hits.setdefault(h.fonte, replace(h, metadados={**h.metadados, **origem}))
        escrever(
            {
                "tipo": "fontes",
                "fontes": [{"fonte": h.fonte, **h.metadados} for h in hits.values()],
            }
        )
        return {"hits": list(hits.values()), "avisos": avisos}

    async def relatorio(estado: EstadoAgente) -> dict[str, Any]:
        escrever = get_stream_writer()
        escrever({"tipo": "etapa", "nome": "relatorio"})
        # Premissas no topo escritas pelo código: o relatório nunca sai sem elas.
        partes = (
            [_premissas_markdown(estado.get("premissas", {}))] if estado.get("premissas") else []
        )
        if partes:
            escrever({"tipo": "relatorio", "texto": partes[0]})
        async for delta in deps.redator.redigir(estado["pergunta"], _contexto_relatorio(estado)):
            partes.append(delta)
            escrever({"tipo": "relatorio", "texto": delta})
        return {"relatorio": "".join(partes)}

    # As stubs do langgraph 1.2.6 rejeitam no mypy até o exemplo canônico de add_node
    # (TypedDictLike); o ignore é só nessa linha — o resto da cadeia fica tipado como Any.
    return (
        StateGraph(EstadoAgente)
        .add_node("planejar", planejar)  # type: ignore[call-overload]
        .add_node("clarificar", clarificar)
        .add_node("perna_quantitativa", perna_quantitativa)
        .add_node("enriquecer", enriquecer)
        .add_node("relatorio", relatorio)
        .add_edge(START, "planejar")
        .add_conditional_edges("planejar", rota_pos_plano, ["clarificar", "perna_quantitativa"])
        .add_edge("clarificar", END)
        .add_conditional_edges(
            "perna_quantitativa", rota_pos_quantitativa, ["enriquecer", "relatorio"]
        )
        .add_edge("enriquecer", "relatorio")
        .add_edge("relatorio", END)
        .compile()
    )
