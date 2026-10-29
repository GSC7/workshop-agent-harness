"""Grafo determinístico do agente (rules/agente.md): topologia fixa, LLM só dentro dos nós."""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, Protocol

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.kpis import KpiAvaliado, avaliar, pares, sql_kpi
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
    meses_janela: int = 6  # série mostrada/consultada
    meses_recentes: int = 3  # base do gap médio
    limiar_fraco: float = 0.10  # gap pior que -10% = fraco
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


def _norm(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.casefold().strip()


def selecionar_fracos(
    avaliados: list[KpiAvaliado], valor: str | None, limiar: float, maximo: int
) -> list[KpiAvaliado]:
    """Pior primeiro; recorte por valor citado (ex.: 'sul' casa 'Sul') aplicado sobre o dado."""
    if valor:
        alvo = _norm(valor)
        filtrados = [a for a in avaliados if _norm(a.dimensao) == alvo]
        avaliados = filtrados or avaliados  # valor desconhecido: não zera a análise
    fracos = [a for a in avaliados if a.gap_medio < -limiar]
    return sorted(fracos, key=lambda a: a.gap_medio)[:maximo]


def _contexto_relatorio(estado: EstadoAgente) -> str:
    """Só o que foi medido e recuperado neste run vai ao LLM redator."""
    return json.dumps(
        {
            # Desvio já calculado: o LLM não deduz o número da série (leu errado no smoke real).
            "kpis_abaixo_da_meta": [
                {
                    "kpi": f.kpi,
                    "eixo": f.eixo,
                    "dimensao": f.dimensao,
                    "situacao": "abaixo da meta",
                    "desvio_vs_meta_pct": round(f.gap_medio * 100, 1),
                    "serie_mensal": f.serie,
                }
                for f in estado.get("fracos", [])
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
        get_stream_writer()({"tipo": "plano", "plano": plano.model_dump()})
        return {"plano": plano}

    async def perna_quantitativa(estado: EstadoAgente) -> dict[str, Any]:
        escrever = get_stream_writer()
        escrever({"tipo": "etapa", "nome": "perna_quantitativa"})
        plano = estado["plano"]
        avaliados: list[KpiAvaliado] = []
        sqls: list[str] = []
        for kpi, eixo in pares(list(plano.kpis), plano.eixo):
            r = await executar_sql(
                deps.engine,
                sql_kpi(kpi, eixo, cfg.meses_janela),
                cfg.sql_teto_linhas,
                cfg.sql_timeout_ms,
            )
            sqls.append(r.sql)
            escrever({"tipo": "sql", "kpi": kpi, "eixo": eixo, "sql": r.sql})
            avaliados += avaliar(kpi, eixo, r.linhas, cfg.meses_recentes)
        avisos: list[str] = []
        if plano.valor and not any(_norm(a.dimensao) == _norm(plano.valor) for a in avaliados):
            avisos.append(
                f"'{plano.valor}' não existe nos eixos analisados; a análise cobre todos os valores"
            )
            escrever({"tipo": "aviso", "mensagem": avisos[-1]})
        fracos = selecionar_fracos(avaliados, plano.valor, cfg.limiar_fraco, cfg.max_fracos)
        return {"fracos": fracos, "sqls": sqls, "avisos": avisos}

    def rota_pos_quantitativa(estado: EstadoAgente) -> Literal["enriquecer", "relatorio"]:
        # Data-driven: sem KPI fraco não há o que explicar nem prescrever.
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
        partes: list[str] = []
        async for delta in deps.redator.redigir(estado["pergunta"], _contexto_relatorio(estado)):
            partes.append(delta)
            escrever({"tipo": "relatorio", "texto": delta})
        return {"relatorio": "".join(partes)}

    # As stubs do langgraph 1.2.6 rejeitam no mypy até o exemplo canônico de add_node
    # (TypedDictLike); o ignore é só nessa linha — o resto da cadeia fica tipado como Any.
    return (
        StateGraph(EstadoAgente)
        .add_node("planejar", planejar)  # type: ignore[call-overload]
        .add_node("perna_quantitativa", perna_quantitativa)
        .add_node("enriquecer", enriquecer)
        .add_node("relatorio", relatorio)
        .add_edge(START, "planejar")
        .add_edge("planejar", "perna_quantitativa")
        .add_conditional_edges(
            "perna_quantitativa", rota_pos_quantitativa, ["enriquecer", "relatorio"]
        )
        .add_edge("enriquecer", "relatorio")
        .add_edge("relatorio", END)
        .compile()
    )
