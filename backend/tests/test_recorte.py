from __future__ import annotations

import pytest

from agent.kpis import avaliar_recorte
from agent.periodo import Recorte


def _linhas(dim: str, valores: dict[str, tuple[float, float]]) -> list[dict]:
    return [
        {"dimensao": dim, "mes": m, "realizado": r, "meta": mt} for m, (r, mt) in valores.items()
    ]


JULHOS = {f"{a}-07": (0.020, 0.030) for a in (2023, 2024, 2025)} | {
    f"{a}-06": (0.032, 0.030) for a in (2023, 2024, 2025)
}


def test_mes_alvo_historicamente_abaixo_da_meta_e_risco_sazonal():
    av = avaliar_recorte(
        "taxa_conversao",
        "canal",
        _linhas("site_proprio", JULHOS),
        Recorte("mes_alvo", (7,)),
        0.10,
        0.10,
    )[0]

    assert av.classificacao == "risco_sazonal"
    assert av.gap_medio == pytest.approx(-1 / 3)
    assert av.ano_referencia == 2025
    assert [p["mes"] for p in av.serie] == ["2023-07", "2024-07", "2025-07"]  # só o recorte


def test_mes_alvo_que_bate_a_meta_e_saudavel():
    dados = {f"{a}-07": (0.031, 0.030) for a in (2024, 2025)}

    av = avaliar_recorte(
        "taxa_conversao",
        "canal",
        _linhas("site_proprio", dados),
        Recorte("mes_alvo", (7,)),
        0.10,
        0.10,
    )[0]

    assert av.classificacao == "saudavel"


def test_periodo_passado_com_ano_compara_com_o_mesmo_periodo_do_ano_anterior():
    q4 = {
        "2024-10": (100, 100),
        "2024-11": (100, 100),
        "2024-12": (100, 100),
        "2025-10": (56, 100),
        "2025-11": (56, 100),
        "2025-12": (56, 100),
    }

    av = avaliar_recorte(
        "faturamento",
        "canal",
        _linhas("marketplace", q4),
        Recorte("passado", (10, 11, 12), 2025),
        0.10,
        0.10,
    )[0]

    assert av.ano_referencia == 2025 and av.variacao_yoy == pytest.approx(-0.44)
    assert av.classificacao == "queda_real"


def test_padrao_do_fim_do_ano_compara_com_o_resto_do_ano_por_ano():
    dados = {}
    for ano, alta in ((2023, 1.10), (2024, 1.12), (2025, 0.98)):
        dados |= {f"{ano}-{m:02d}": (100.0, 100.0) for m in range(1, 10)}
        dados |= {f"{ano}-{m:02d}": (100.0 * alta, 100.0) for m in (10, 11, 12)}

    av = avaliar_recorte(
        "ticket_medio",
        "total",
        _linhas("total", dados),
        Recorte("padrao", (10, 11, 12)),
        0.10,
        0.10,
    )[0]

    assert av.classificacao == "padrao"
    assert av.vs_resto_do_ano == {
        2023: pytest.approx(0.10),
        2024: pytest.approx(0.12),
        2025: pytest.approx(-0.02),
    }


def test_ano_sem_todos_os_meses_do_recorte_nao_conta():
    dados = {
        "2025-10": (50, 100),
        "2025-11": (50, 100),
        "2024-10": (100, 100),
        "2024-11": (100, 100),
        "2024-12": (100, 100),
    }

    av = avaliar_recorte(
        "faturamento", "canal", _linhas("x", dados), Recorte("mes_alvo", (10, 11, 12)), 0.10, 0.10
    )[0]

    assert av.ano_referencia == 2024  # 2025 sem dezembro: não é recorte completo


def test_serie_do_periodo_passado_nao_inclui_anos_depois_do_pedido():
    dados = {f"{a}-{m}": (100, 100) for a in (2022, 2023, 2024, 2025) for m in ("10", "11", "12")}

    av = avaliar_recorte(
        "faturamento",
        "canal",
        _linhas("x", dados),
        Recorte("passado", (10, 11, 12), 2024),
        0.1,
        0.1,
    )[0]

    assert max(p["mes"] for p in av.serie) == "2024-12"


def test_risco_sazonal_usa_o_historico_dos_anos_e_nao_um_ano_so():
    # Julho abaixo da meta em 2023 e 2024, recuperou em 2025: historicamente fraco = risco.
    dados = {"2023-07": (0.020, 0.030), "2024-07": (0.020, 0.030), "2025-07": (0.031, 0.030)}

    av = avaliar_recorte(
        "taxa_conversao", "canal", _linhas("site", dados), Recorte("mes_alvo", (7,)), 0.1, 0.1
    )[0]

    assert av.classificacao == "risco_sazonal"


def test_sem_meta_no_recorte_a_dimensao_e_pulada_e_nao_vira_saudavel():
    dados = {"2025-07": (0.02, 0), "2024-07": (0.02, 0)}

    assert (
        avaliar_recorte(
            "taxa_conversao", "canal", _linhas("site", dados), Recorte("mes_alvo", (7,)), 0.1, 0.1
        )
        == []
    )
