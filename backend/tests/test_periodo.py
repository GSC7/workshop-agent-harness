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


# ---- #28: mês em contexto de passado, "desde" e "marco" sem acento ----

HOJE = date(2026, 10, 29)


@pytest.mark.parametrize(
    ("pergunta", "meses", "ano"),
    [
        ("Como foi a conversão em julho do ano passado?", (7,), 2025),
        ("E julho do ano passado no site?", (7,), 2025),
        # "julho passado" = o último julho que passou: em outubro de 2026, julho de 2026.
        ("Comparado a julho passado, como estamos?", (7,), 2026),
        ("Como foi novembro passado?", (11,), 2025),  # novembro ainda não chegou em 2026
        ("E outubro passado?", (10,), 2025),  # o mês corrente não "passou": o do ano anterior
        ("Como foi março do ano passado?", (3,), 2025),
    ],
)
def test_mes_em_contexto_de_passado_vira_periodo_passado(pergunta, meses, ano):
    r = recorte_citado(pergunta, hoje=HOJE)

    assert r is not None and (r.tipo, r.meses, r.ano) == ("passado", meses, ano)
    assert mes_nomeado(pergunta) is None  # nunca vira mês-alvo (futuro)


@pytest.mark.parametrize(
    "pergunta",
    ["Como evoluiu o faturamento desde janeiro?", "O que mudou desde março no Sul?"],
)
def test_desde_mes_nao_vira_mes_alvo_nem_recorte(pergunta):
    assert mes_nomeado(pergunta) is None
    assert recorte_citado(pergunta, hoje=HOJE) is None  # default, declarado nas premissas


@pytest.mark.parametrize(
    ("pergunta", "esperado"),
    [
        ("Qual o marco do projeto de frete?", None),
        ("Bater a meta é um marco para o Sul", None),
        ("vendas no marco zero da campanha", None),
        ("Como aumentar as vendas em marco?", 3),
        ("vendas de marco", 3),
        ("O que fazer para marco?", 3),
        ("Como foi marco de 2025?", None),  # período passado (mes com ano), não mês-alvo
        ("E em Março?", 3),  # com acento é sempre mês
        ("o Março chegou", 3),
    ],
)
def test_marco_sem_acento_so_e_mes_com_contexto_de_data(pergunta, esperado):
    assert mes_nomeado(pergunta) == esperado


def test_marco_substantivo_nao_vira_recorte():
    assert recorte_citado("Qual o marco do projeto de frete no Sul?", hoje=HOJE) is None


def test_marco_de_ano_continua_periodo_passado():
    r = recorte_citado("Como foi marco de 2025?", hoje=HOJE)

    assert r is not None and (r.tipo, r.meses, r.ano) == ("passado", (3,), 2025)


@pytest.mark.parametrize(
    ("pergunta", "mes"),
    [
        ("Como aumentar a conversão do site em julho?", 7),  # N3: não regredir
        ("O que funcionou em novembros anteriores?", 11),
    ],
)
def test_mes_alvo_continua_como_antes(pergunta, mes):
    r = recorte_citado(pergunta, hoje=HOJE)

    assert mes_nomeado(pergunta) == mes
    assert r is not None and (r.tipo, r.meses) == ("mes_alvo", (mes,))


def test_substantivo_marcos_nao_recorta_a_prescricao_do_mes_alvo():
    # Regressão (revisão #28): "marcos" (substantivo) ligava anteriores=True para julho.
    r = recorte_citado("Quais os marcos para julho?", hoje=HOJE)

    assert r is not None and (r.meses, r.anteriores) == ((7,), False)


def test_anteriores_e_do_mes_alvo_nao_de_outra_mencao():
    r = recorte_citado("Conversão em julho, e os novembros?", hoje=HOJE)

    assert r is None  # dois meses-alvo distintos: ambíguo, sem escolha silenciosa


@pytest.mark.parametrize(
    ("pergunta", "esperado"),
    [
        ("O que fazer até marco?", 3),
        ("Vendas a partir de marco", 3),
        ("Marco foi bom?", None),  # começo de frase sem contexto de data: conservador
        ("E nos marcos?", None),  # plural sem contexto: substantivo
    ],
)
def test_marco_sem_acento_casos_de_fronteira(pergunta, esperado):
    assert mes_nomeado(pergunta) == esperado


def test_marco_acentuado_decomposto_conta_como_mes():
    import unicodedata

    assert mes_nomeado(unicodedata.normalize("NFD", "O Março chegou")) == 3


def test_desde_mes_com_ano_tambem_cai_no_default():
    assert recorte_citado("Como evoluiu desde julho de 2025?", hoje=HOJE) is None
    assert mes_nomeado("Como evoluiu desde julho de 2025?") is None
