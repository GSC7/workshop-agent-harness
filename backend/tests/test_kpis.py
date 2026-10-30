from __future__ import annotations

import pytest

from agent.kpis import EIXOS_COM_META, avaliar, pares, sql_kpi
from agent.tools.run_sql import validar_sql


@pytest.mark.parametrize(
    ("kpi", "eixo"), [(k, e) for k, eixos in EIXOS_COM_META.items() for e in eixos]
)
def test_todo_template_passa_no_guardrail_do_run_sql(kpi, eixo):
    sql = validar_sql(sql_kpi(kpi, eixo, 6), 500)

    assert "metas" in sql and f"'{kpi}'" in sql


def test_par_sem_meta_nao_tem_template():
    with pytest.raises(ValueError, match="sem meta"):
        sql_kpi("taxa_recompra", "canal", 6)


def test_pergunta_vaga_varre_todo_o_catalogo_com_meta():
    assert len(pares(None, None)) == sum(len(e) for e in EIXOS_COM_META.values())


def test_kpi_e_eixo_citados_viram_um_unico_par():
    assert pares(["taxa_recompra"], "regiao") == [("taxa_recompra", "regiao")]


def test_eixo_sem_meta_para_o_kpi_cai_nos_eixos_disponiveis():
    assert pares(["taxa_recompra"], "canal") == [
        ("taxa_recompra", "total"),
        ("taxa_recompra", "regiao"),
    ]


def test_gap_medio_usa_so_os_meses_recentes_e_ordena_pior_primeiro():
    linhas = [
        {"dimensao": "Sul", "mes": "2026-01", "realizado": 2.0, "meta": 1.0},  # fora da janela
        {"dimensao": "Sul", "mes": "2026-05", "realizado": 0.7, "meta": 1.0},
        {"dimensao": "Sul", "mes": "2026-06", "realizado": 0.7, "meta": 1.0},
        {"dimensao": "Norte", "mes": "2026-05", "realizado": 1.1, "meta": 1.0},
        {"dimensao": "Norte", "mes": "2026-06", "realizado": 1.1, "meta": 1.0},
    ]

    av = avaliar("taxa_recompra", "regiao", linhas, 2, 0.10, 0.10)

    assert [a.dimensao for a in av] == ["Sul", "Norte"]
    assert av[0].gap_medio == pytest.approx(-0.3)
    assert av[0].abaixo_da_meta and not av[1].abaixo_da_meta
    assert len(av[0].serie) == 3  # série completa vai ao relatório


def test_meta_zero_nao_divide():
    linhas = [{"dimensao": "Sul", "mes": "2026-06", "realizado": 1.0, "meta": 0}]

    assert avaliar("faturamento", "regiao", linhas, 3, 0.10, 0.10) == []


# ---- #29: só os meses que a avaliação usa ----


@pytest.mark.parametrize(("kpi", "eixo"), [(k, e) for k, es in EIXOS_COM_META.items() for e in es])
def test_filtro_de_meses_passa_no_guardrail_e_filtra_o_pedido(kpi, eixo):
    sql = sql_kpi(kpi, eixo, 60, meses_do_ano=(10, 11, 12))
    validar_sql(sql, 500)  # o guardrail aceita (re-renderiza o SQL; o trecho é conferido antes)

    assert "extract(month FROM p.data_pedido) IN (10, 11, 12)" in sql
    assert "dados_ate" in sql  # "dados até" segue vindo do banco, não da série filtrada


def test_sem_filtro_de_meses_a_janela_e_continua():
    assert "extract(month FROM p.data_pedido) IN" not in sql_kpi("faturamento", "regiao", 27)


@pytest.mark.parametrize("meses", [(0,), (13,), ("7; DROP TABLE pedidos",), (True,)])
def test_filtro_de_meses_so_aceita_mes_valido(meses):
    with pytest.raises((ValueError, TypeError)):
        sql_kpi("faturamento", "regiao", 27, meses_do_ano=meses)  # type: ignore[arg-type]


def test_recompra_continua_olhando_todo_o_historico_do_cliente():
    # O filtro de mês é do pedido avaliado; o "já tinha pedido antes" olha tudo.
    sql = sql_kpi("taxa_recompra", "regiao", 60, meses_do_ano=(7,))

    assert "a.data_pedido < p.data_pedido))" in sql
    assert "a.data_pedido < p.data_pedido AND extract" not in sql
