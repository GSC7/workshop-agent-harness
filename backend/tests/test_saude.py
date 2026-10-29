from __future__ import annotations

import pytest

from agent.kpis import avaliar


def _serie(dim: str, por_ano: dict[int, float], meta_recente: float) -> list[dict]:
    """Meses 04-06 de cada ano com o realizado dado; meta só cobra nos meses recentes."""
    return [
        {
            "dimensao": dim,
            "mes": f"{ano}-{m:02d}",
            "realizado": valor,
            "meta": meta_recente if ano == 2026 else valor,
        }
        for ano, valor in por_ano.items()
        for m in (4, 5, 6)
    ]


def _classificar(linhas):
    return {a.dimensao: a for a in avaliar("faturamento", "regiao", linhas, 3, 0.10, 0.10)}


def test_abaixo_da_meta_e_caindo_contra_anos_anteriores_e_queda_real():
    av = _classificar(_serie("Sul", {2024: 100, 2025: 100, 2026: 70}, meta_recente=100))["Sul"]

    assert av.classificacao == "queda_real"
    assert av.variacao_yoy == pytest.approx(-0.30)


def test_abaixo_da_meta_mas_em_linha_com_anos_anteriores_e_sazonal():
    # Igual aos mesmos meses de 2024/2025: o padrão é o de sempre, a meta é que está alta.
    av = _classificar(_serie("Norte", {2024: 80, 2025: 80, 2026: 80}, meta_recente=100))["Norte"]

    assert av.classificacao == "sazonal"
    assert av.gap_medio == pytest.approx(-0.20)


def test_dentro_da_meta_e_saudavel():
    av = _classificar(_serie("Sudeste", {2024: 100, 2025: 100, 2026: 98}, meta_recente=100))[
        "Sudeste"
    ]

    assert av.classificacao == "saudavel"


def test_sem_historico_nao_presume_sazonalidade():
    linhas = [
        {"dimensao": "Sul", "mes": f"2026-0{m}", "realizado": 70, "meta": 100} for m in (4, 5, 6)
    ]

    av = _classificar(linhas)["Sul"]

    assert av.variacao_yoy is None and av.classificacao == "queda_real"


def test_serie_entregue_ao_relatorio_e_so_a_recente():
    av = _classificar(_serie("Sul", {2024: 100, 2025: 100, 2026: 70}, meta_recente=100))["Sul"]

    assert [p["mes"] for p in av.serie] == [
        "2025-04",
        "2025-05",
        "2025-06",
        "2026-04",
        "2026-05",
        "2026-06",
    ]


def test_yoy_compara_so_os_meses_que_tem_historico():
    # Regressão: recentes abr-jun com histórico só de abril comparava abr-jun contra abril.
    linhas = [
        {"dimensao": "Sul", "mes": "2025-04", "realizado": 100, "meta": 100},
        {"dimensao": "Sul", "mes": "2026-04", "realizado": 100, "meta": 100},
        {"dimensao": "Sul", "mes": "2026-05", "realizado": 50, "meta": 100},
        {"dimensao": "Sul", "mes": "2026-06", "realizado": 50, "meta": 100},
    ]

    av = _classificar(linhas)["Sul"]

    assert av.variacao_yoy == pytest.approx(0.0)  # abril vs abril


def test_realizado_nulo_e_descartado_e_nao_vira_queda_falsa():
    # nullif(sessoes, 0) devolve NULL: não pode virar realizado 0 (gap -100%).
    linhas = _serie("Sul", {2024: 100, 2025: 100, 2026: 100}, meta_recente=100)
    linhas[-1] = {**linhas[-1], "realizado": None}

    av = _classificar(linhas)["Sul"]

    assert av.classificacao == "saudavel" and av.gap_medio == pytest.approx(0.0)
