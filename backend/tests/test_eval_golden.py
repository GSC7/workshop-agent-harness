from __future__ import annotations

from evals.golden import carregar_golden


def test_golden_versionado_carrega_e_cobre_os_tipos_de_caso():
    casos = carregar_golden()

    ids = {c.id for c in casos}
    assert {"n1-recompra-sul", "n5-loja-fisica", "n6-ticket-sazonal", "c1-fora-do-escopo"} <= ids
    assert any(
        not c.dispara_enriquecimento and not c.espera_clarificacao for c in casos
    )  # controle saudável
    assert any(c.espera_clarificacao for c in casos)
    assert all(c.fontes_esperadas for c in casos if c.dispara_enriquecimento)


def test_falhas_conhecidas_apontam_a_issue_dona():
    pendentes = [c for c in carregar_golden() if c.pendente]

    assert {c.id for c in pendentes} == {
        "n2-eletronicos-marketplace",
        "n3-conversao-site",
        "n4-beleza-nordeste",
        "n6-ticket-sazonal",
    }
    assert all(c.pendente.startswith("#9") for c in pendentes)


def test_todo_caso_tem_criterios_explicitos_para_o_judge():
    # Sem critérios fixos o judge inventava a régua a cada execução (veredito instável).
    assert all(len(c.criterios) >= 3 for c in carregar_golden())


def test_campo_digitado_errado_no_golden_e_rejeitado():
    import pytest
    from pydantic import ValidationError

    from evals.golden import Caso

    with pytest.raises(ValidationError):
        Caso.model_validate(
            {
                "id": "x",
                "pergunta": "p",
                "dispara_enriquecimento": False,
                "recomendacao_esperada": "r",
                "pendnte": "#9",
            }
        )
