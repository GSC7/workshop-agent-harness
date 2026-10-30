from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from agent.grounding import MotivoAusencia, Recomendacao
from agent.kpis import Eixo, Kpi, KpiAvaliado
from agent.periodo import Recorte
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
    # Fora do schema do LLM: o mês-alvo vem do texto (periodo.mes_nomeado), não da inferência.
    mes_citado: SkipJsonSchema[int | None] = Field(default=None, ge=1, le=12)
    # Campos que vieram do turno anterior (#7): as premissas dizem "herdado", não "citado".
    herdados: SkipJsonSchema[list[str]] = Field(default_factory=list)
    fora_do_escopo: bool = Field(
        default=False,
        description="true só se a pergunta não for sobre vendas/KPIs comerciais.",
    )


def _sem_repetir(atual: list[str], novos: list[str]) -> list[str]:
    """Acumula avisos entre nós sem repetir (falha de busca se repete por KPI x coleção)."""
    return list(dict.fromkeys(atual + novos))


@dataclass(frozen=True)
class TurnoAnterior:
    """O que a conversa já estabeleceu, lido do harness (rastro dos runs da conversa)."""

    plano: Plano
    # Recomendações já entregues na conversa: [{fonte, kpi, dimensao, acao, ...}].
    recomendacoes: list[dict[str, Any]] = field(default_factory=list)


class EstadoAgente(TypedDict, total=False):
    pergunta: str
    plano: Plano
    fracos: list[KpiAvaliado]  # queda real (meta e anos anteriores), pior primeiro
    sazonais: list[KpiAvaliado]  # abaixo da meta, mas no padrão histórico: sem prescrição
    saudaveis: list[KpiAvaliado]  # dentro/acima da meta no recorte: o redator afirma com número
    padroes: list[KpiAvaliado]  # recorte recorrente descrito (ex.: fim do ano vs resto do ano)
    recorte: Recorte | None  # período citado na pergunta (#9)
    premissas: dict[str, Any]  # resolvidas em código e declaradas no topo do relatório
    sqls: Annotated[list[str], operator.add]  # SQL efetivamente executado (inspecionável)
    hits: list[Hit]
    recomendacoes: list[Recomendacao]  # só as validadas (fonte de prescrição do run) e novas
    retomadas: list[Recomendacao]  # validadas, mas já recomendadas antes: acompanhamento
    turno_anterior: TurnoAnterior | None  # o que a conversa já estabeleceu (#7)
    motivo_sem_recomendacao: MotivoAusencia  # por que não há recomendação (declarado)
    avisos: Annotated[list[str], _sem_repetir]  # limitações do run, declaradas no relatório
    relatorio: str
