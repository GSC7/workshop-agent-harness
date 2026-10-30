from __future__ import annotations

from datetime import date

import pytest

from agent.periodo import mes_nomeado, periodo_alvo, recorte_citado


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


@pytest.mark.parametrize(
    ("pergunta", "tipo", "meses", "ano", "anteriores"),
    [
        ("Como aumentar a conversão do site próprio em julho?", "mes_alvo", (7,), None, False),
        (
            "O que funcionou em novembros anteriores para a conversão?",
            "mes_alvo",
            (11,),
            None,
            True,
        ),
        (
            "O ticket médio sobe muito no fim do ano — é problema?",
            "padrao",
            (10, 11, 12),
            None,
            False,
        ),
        ("Por que o faturamento caiu no fim de 2025?", "passado", (10, 11, 12), 2025, False),
        ("E no 4º trimestre de 2024?", "passado", (10, 11, 12), 2024, False),
        ("Como foi julho de 2025?", "passado", (7,), 2025, False),
    ],
)
def test_recorte_temporal_vem_do_texto(pergunta, tipo, meses, ano, anteriores):
    r = recorte_citado(pergunta)

    assert r is not None and (r.tipo, r.meses, r.ano, r.anteriores) == (
        tipo,
        meses,
        ano,
        anteriores,
    )


@pytest.mark.parametrize(
    "pergunta", ["Como melhorar minhas vendas no próximo mês?", "e em maio e junho?"]
)
def test_sem_recorte_ou_ambiguo_nao_inventa(pergunta):
    assert recorte_citado(pergunta) is None


@pytest.mark.parametrize(
    ("pergunta", "tipo", "meses", "ano"),
    [
        ("Por que caiu no fim do ano de 2025?", "passado", (10, 11, 12), 2025),
        ("Como foi o final do ano passado?", "passado", (10, 11, 12), 2025),  # hoje = 2026
    ],
)
def test_frases_comuns_de_periodo_passado(pergunta, tipo, meses, ano):
    r = recorte_citado(pergunta, hoje=date(2026, 10, 28))

    assert r is not None and (r.tipo, r.meses, r.ano) == (tipo, meses, ano)


def test_ultimo_trimestre_nao_e_confundido_com_fim_do_ano():
    # "último trimestre" costuma ser o mais recente, não out-dez: sem chute, cai no default.
    assert recorte_citado("Como foi o último trimestre?") is None


@pytest.mark.parametrize(
    ("pergunta", "anteriores"),
    [
        ("O que funcionou em novembros anteriores?", True),
        ("E nos julhos?", True),
        ("Conversão em julho, comparando com o ano anterior?", False),  # "anterior" solto não conta
    ],
)
def test_anteriores_so_vale_para_o_mes_no_plural_ou_x_anteriores(pergunta, anteriores):
    r = recorte_citado(pergunta)

    assert r is not None and r.anteriores is anteriores
