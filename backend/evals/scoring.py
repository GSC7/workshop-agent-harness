"""Métricas determinísticas por caso (sem LLM): o que dá para medir, mede-se em código."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.grafo import AVISOS_GROUNDING
from evals.golden import Caso


@dataclass(frozen=True)
class Limiares:
    """Aprovados pelo usuário (ADR 0011)."""

    recall_minimo: float = 0.75
    distratores_maximo: int = 0
    judge_minimo: float = 0.7


@dataclass(frozen=True)
class ResultadoRun:
    """O que um run real devolveu pelo /chat (SSE), já resumido para pontuar."""

    concluido: bool
    etapas: list[str]
    fontes_citadas: list[str]
    relatorio: str
    sqls: int
    avisos: list[str] = field(default_factory=list)
    erro: str | None = None  # mensagem do evento "erro", HTTP != 200 ou prazo estourado


@dataclass(frozen=True)
class NotaDeterministica:
    aprovado: bool
    recall: float | None
    fontes_faltando: list[str]
    distratores_citados: list[str]
    motivos: list[str]  # por que reprovou (vazio = aprovado)


def pontuar(caso: Caso, run: ResultadoRun, limiares: Limiares) -> NotaDeterministica:
    motivos: list[str] = []
    citadas = set(run.fontes_citadas)
    if not run.concluido:
        motivos.append(f"o run não concluiu ({run.erro or 'sem evento fim'})")

    if caso.espera_clarificacao:
        if "clarificar" not in run.etapas:
            motivos.append("esperava clarificação e o agente analisou")
        if run.sqls:
            motivos.append(f"clarificação não deveria rodar SQL (rodou {run.sqls})")
    else:
        enriqueceu = "enriquecer" in run.etapas
        if enriqueceu != caso.dispara_enriquecimento:
            motivos.append(
                f"enriquecimento: esperado {caso.dispara_enriquecimento}, obtido {enriqueceu}"
            )
        if not run.relatorio.lstrip().startswith("## Premissas"):
            motivos.append("relatório sem premissas no topo")

    esperadas = set(caso.fontes_esperadas)
    recall = len(esperadas & citadas) / len(esperadas) if esperadas else None
    if recall is not None and recall < limiares.recall_minimo:
        motivos.append(f"recall de fontes {recall:.2f} < {limiares.recall_minimo:.2f}")
    distratores = sorted(set(caso.distratores) & citadas)
    if len(distratores) > limiares.distratores_maximo:
        motivos.append(f"{len(distratores)} distrator(es) citado(s)")
    furos = [a for a in run.avisos if a.startswith(AVISOS_GROUNDING)]
    if furos:
        motivos.append(f"grounding: {len(furos)} citação(ões) fora do permitido")

    return NotaDeterministica(
        aprovado=not motivos,
        recall=recall,
        fontes_faltando=sorted(esperadas - citadas),
        distratores_citados=distratores,
        motivos=motivos,
    )
