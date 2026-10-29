from __future__ import annotations

from datetime import date

import pytest

from agent.periodo import mes_nomeado, periodo_alvo


@pytest.mark.parametrize(
    ("hoje", "mes_citado", "esperado"),
    [
        (date(2026, 10, 28), None, "2026-11"),  # default: mês atual + 1
        (date(2026, 12, 5), None, "2027-01"),  # virada de ano
        (date(2026, 1, 31), None, "2026-02"),
        (date(2026, 10, 28), 7, "2027-07"),  # "em julho": próximo julho
        (date(2026, 10, 28), 11, "2026-11"),
        (date(2026, 10, 28), 10, "2027-10"),  # mês corrente já começou: o próximo
    ],
)
def test_periodo_alvo(hoje, mes_citado, esperado):
    assert periodo_alvo(hoje, mes_citado) == esperado


@pytest.mark.parametrize(
    ("pergunta", "esperado"),
    [
        ("Como aumentar a conversão do site em julho?", 7),
        ("E em Março, o que fazer?", 3),
        ("vendas de marco", 3),  # sem acento
        ("Como melhorar minhas vendas no próximo mês?", None),  # "próximo mês" não nomeia mês
        ("o que fazer em maio e junho?", None),  # ambíguo: não escolhe por conta própria
        ("faturamento da maionese", None),  # 'maio' só como palavra inteira
    ],
)
def test_mes_nomeado_vem_do_texto_da_pergunta(pergunta, esperado):
    assert mes_nomeado(pergunta) == esperado


def test_llm_nao_ve_mes_citado_no_schema_do_plano():
    from agent.state import Plano

    assert "mes_citado" not in Plano.model_json_schema()["properties"]
