"""Multi-turno (#7): herança determinística entre turnos e o que já foi recomendado."""

from __future__ import annotations

from agent.state import Plano, TurnoAnterior

__all__ = ["TurnoAnterior", "chave", "herdar", "ja_recomendadas"]


def herdar(novo: Plano, anterior: TurnoAnterior | None) -> Plano:
    """Acompanhamento ("e no Sudeste?") troca só o que cita; o resto vem do turno anterior.

    - KPI e período: os novos se citados, senão os anteriores.
    - Eixo: o novo se citado. Valor: o novo; o anterior só se o eixo não mudou ('Sul' não é canal).
    - Com conversa em andamento, pergunta que extraiu algo não é fora do escopo.
    """
    if anterior is None:
        return novo
    velho = anterior.plano
    eixo = novo.eixo or velho.eixo
    valor = novo.valor if novo.valor else (velho.valor if eixo == velho.eixo else None)
    extraiu_algo = bool(novo.kpis or novo.eixo or novo.valor or novo.mes_citado)
    herdados = [
        campo
        for campo, veio in (
            ("kpis", not novo.kpis and bool(velho.kpis)),
            ("eixo", not novo.eixo and velho.eixo is not None),
            ("valor", not novo.valor and valor is not None),
            ("mes_citado", not novo.mes_citado and velho.mes_citado is not None),
        )
        if veio
    ]
    return novo.model_copy(
        update={
            "kpis": list(novo.kpis) or list(velho.kpis),
            "eixo": eixo,
            "valor": valor,
            "mes_citado": novo.mes_citado or velho.mes_citado,
            "fora_do_escopo": novo.fora_do_escopo and not extraiu_algo,
            "herdados": herdados,
        }
    )


def chave(fonte: str, kpi: str, dimensao: str) -> tuple[str, str, str]:
    """Identidade de uma recomendação: a mesma fonte para o mesmo alvo é a mesma ação."""
    return (fonte, kpi, dimensao)


def ja_recomendadas(anterior: TurnoAnterior | None) -> set[tuple[str, str, str]]:
    if anterior is None:
        return set()
    return {chave(r["fonte"], r["kpi"], r["dimensao"]) for r in anterior.recomendacoes}
