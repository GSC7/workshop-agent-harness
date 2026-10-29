from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from pydantic import BaseModel, Field

from agent.kpis import Eixo, Kpi, KpiAvaliado
from agent.tools.search import Hit


class Plano(BaseModel):
    """Intenção extraída da pergunta (LLM rápido); só escolhe dentro do catálogo fechado."""

    kpis: list[Kpi] = Field(
        default_factory=list,
        description="KPIs citados na pergunta; vazio quando a pergunta é genérica sobre vendas.",
    )
    eixo: Eixo | None = Field(
        default=None,
        description="Eixo de recorte citado (regiao, canal ou categoria); null se não houver.",
    )
    valor: str | None = Field(
        default=None,
        description="Valor do eixo citado, como aparece no negócio (ex.: Sul, loja_fisica, Moda).",
    )


def _sem_repetir(atual: list[str], novos: list[str]) -> list[str]:
    """Acumula avisos entre nós sem repetir (falha de busca se repete por KPI x coleção)."""
    return list(dict.fromkeys(atual + novos))


class EstadoAgente(TypedDict, total=False):
    pergunta: str
    plano: Plano
    fracos: list[KpiAvaliado]  # abaixo da meta além do limiar, pior primeiro
    sqls: Annotated[list[str], operator.add]  # SQL efetivamente executado (inspecionável)
    hits: list[Hit]
    avisos: Annotated[list[str], _sem_repetir]  # limitações do run, declaradas no relatório
    relatorio: str
