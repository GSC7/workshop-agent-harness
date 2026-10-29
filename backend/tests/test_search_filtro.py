from __future__ import annotations

import pytest

from agent.tools.search import BuscaRejeitada, Dimensao, montar_filtro


def _condicoes(filtro) -> dict[str, object]:
    """Campo -> valor casado (MatchValue) ou lista (MatchAny), p/ asserts legíveis."""
    out: dict[str, object] = {}
    for c in filtro.must:
        m = c.match
        out[c.key] = getattr(m, "value", None) or getattr(m, "any", None)
    return out


def test_prescricao_filtra_por_kpi_e_dimensao_exata():
    filtro = montar_filtro(
        "prescricao",
        kpi_alvo="faturamento",
        dimensao=Dimensao(produto="Eletrônicos", canal="marketplace"),
    )

    assert _condicoes(filtro) == {
        "kpi_alvo": "faturamento",
        "produto": "Eletrônicos",
        "canal": "marketplace",
    }


@pytest.mark.parametrize(
    ("colecao", "kpi_alvo", "dimensao", "motivo"),
    [
        ("prescricao", None, Dimensao(regiao="Sul"), "kpi_alvo"),
        ("prescricao", "taxa_recompra", None, "dimensão"),
        ("prescricao", "taxa_recompra", Dimensao(), "dimensão"),
        ("diagnostico", None, None, "dimensão"),
    ],
)
def test_enriquecimento_sem_filtro_obrigatorio_e_rejeitado(colecao, kpi_alvo, dimensao, motivo):
    with pytest.raises(BuscaRejeitada, match=motivo):
        montar_filtro(colecao, kpi_alvo=kpi_alvo, dimensao=dimensao)


def test_diagnostico_filtra_so_pela_dimensao_porque_nao_tem_kpi_no_dado():
    filtro = montar_filtro("diagnostico", kpi_alvo="taxa_recompra", dimensao=Dimensao(regiao="Sul"))

    assert _condicoes(filtro) == {"regiao": "Sul"}


def test_camada_semantica_e_catalogo_sem_dimensao():
    filtro = montar_filtro(
        "camada_semantica", kpi_alvo="faturamento", dimensao=Dimensao(regiao="Sul")
    )

    assert _condicoes(filtro) == {"kpi_alvo": "faturamento"}
    assert montar_filtro("camada_semantica").must == []


def test_recorte_sazonal_filtra_por_periodo_referencia():
    filtro = montar_filtro(
        "prescricao",
        kpi_alvo="taxa_conversao",
        dimensao=Dimensao(canal="site_proprio"),
        periodos=["2023-11", "2024-11", "2025-11"],
    )

    assert _condicoes(filtro)["periodo_referencia"] == ["2023-11", "2024-11", "2025-11"]


@pytest.mark.parametrize("periodo", ["2024-13", "2024-1", "nov/2024", "2024-11-01", ""])
def test_periodo_fora_do_formato_aaaa_mm_e_rejeitado(periodo):
    with pytest.raises(BuscaRejeitada, match="periodo_referencia"):
        montar_filtro("diagnostico", dimensao=Dimensao(regiao="Sul"), periodos=[periodo])


def test_colecao_fora_da_allowlist_e_rejeitada():
    with pytest.raises(BuscaRejeitada, match="coleção"):
        montar_filtro("harness_runs", dimensao=Dimensao(regiao="Sul"))  # type: ignore[arg-type]


@pytest.mark.parametrize("colecao", ["camada_semantica", "diagnostico", "prescricao"])
def test_filtro_nunca_usa_data_ingestao(colecao):
    filtro = montar_filtro(
        colecao,
        kpi_alvo="faturamento",
        dimensao=Dimensao(regiao="Sul", produto="Moda", canal="loja_fisica"),
        periodos=["2025-11"],
    )

    assert "data_ingestao" not in filtro.model_dump_json()


@pytest.mark.parametrize(
    ("colecao", "kpi_alvo", "dimensao", "motivo"),
    [
        ("prescricao", "taxa_recompra", Dimensao(regiao=""), "dimensão"),
        ("prescricao", "taxa_recompra", Dimensao(regiao="   "), "dimensão"),
        ("prescricao", "", Dimensao(regiao="Sul"), "kpi_alvo"),
        ("diagnostico", None, Dimensao(canal=" "), "dimensão"),
    ],
)
def test_string_vazia_nao_conta_como_filtro(colecao, kpi_alvo, dimensao, motivo):
    # LLM mandando regiao="" não pode passar na obrigatoriedade e zerar a busca em silêncio.
    with pytest.raises(BuscaRejeitada, match=motivo):
        montar_filtro(colecao, kpi_alvo=kpi_alvo, dimensao=dimensao)
