from __future__ import annotations

import pytest

from agent.composto import Composto, ItemCatalogo, detectar_composto
from agent.kpis import Recorte, avaliar_composto, sql_kpi_composto
from agent.tools.run_sql import validar_sql

CATALOGO = [
    ItemCatalogo("canal", 1, "site_proprio"),
    ItemCatalogo("canal", 2, "marketplace"),
    ItemCatalogo("canal", 3, "loja_fisica"),
    ItemCatalogo("categoria", 1, "Eletrônicos"),
    ItemCatalogo("categoria", 3, "Casa & Decoração"),
    ItemCatalogo("categoria", 4, "Beleza"),
    ItemCatalogo("regiao", 1, "Norte"),
    ItemCatalogo("regiao", 2, "Nordeste"),
]


@pytest.mark.parametrize(
    ("pergunta", "esperado"),
    [
        (
            "Por que o faturamento de Eletrônicos no marketplace caiu?",
            ("canal", "Eletrônicos", "marketplace"),
        ),
        ("Devo investir mais em Beleza no Nordeste?", ("regiao", "Beleza", "Nordeste")),
        ("Como vai casa e decoração na loja física?", ("canal", "Casa & Decoração", "loja_fisica")),
        ("E eletronicos no site proprio?", ("canal", "Eletrônicos", "site_proprio")),
    ],
)
def test_detecta_categoria_com_canal_ou_regiao(pergunta, esperado):
    c = detectar_composto(pergunta, CATALOGO)

    assert c is not None and (c.eixo_b, c.categoria.nome, c.outro.nome) == esperado


@pytest.mark.parametrize(
    "pergunta",
    [
        "Como vai o marketplace?",  # sem categoria
        "Como vai Beleza?",  # sem segunda dimensão
        "Beleza e Eletrônicos no marketplace?",  # duas categorias: ambíguo
        "Beleza no marketplace no Nordeste?",  # canal E região: ambíguo
        "Como vai o Norte?",  # 'norte' não casa 'nordeste' e vice-versa
    ],
)
def test_sem_composto_ou_ambiguo_nao_inventa(pergunta):
    assert detectar_composto(pergunta, CATALOGO) is None


def test_sql_do_composto_passa_no_guardrail_e_filtra_por_ids_inteiros():
    composto = Composto(
        "canal",
        ItemCatalogo("categoria", 1, "Eletrônicos"),
        ItemCatalogo("canal", 2, "marketplace"),
    )

    sql = validar_sql(sql_kpi_composto(composto, 39), 500)

    assert "pr.categoria_id = 1" in sql and "p.canal_id = 2" in sql
    assert "Eletrônicos" not in sql  # nenhum texto entra no SQL: filtro por id, rótulo vem do banco


def _linhas(valores):
    return [
        {"dimensao": "Eletrônicos × marketplace", "mes": m, "realizado": r}
        for m, r in valores.items()
    ]


def test_composto_no_periodo_passado_cai_44_por_cento_e_e_queda_real():
    q4 = {f"2024-{m}": 100.0 for m in ("10", "11", "12")} | {
        f"2025-{m}": 56.0 for m in ("10", "11", "12")
    }

    av = avaliar_composto(
        "canal",
        {"produto": "Eletrônicos", "canal": "marketplace"},
        _linhas(q4),
        Recorte("passado", (10, 11, 12), 2025),
        3,
        0.10,
    )[0]

    assert av.classificacao == "queda_real" and av.variacao_yoy == pytest.approx(-0.44)
    assert av.sem_meta and av.filtro == {"produto": "Eletrônicos", "canal": "marketplace"}
    assert av.eixo == "categoria×canal"


def test_composto_sem_recorte_compara_os_meses_recentes_com_os_anos_anteriores():
    dados = {
        f"{a}-0{m}": base
        for a, base in ((2024, 100.0), (2025, 100.0), (2026, 128.0))
        for m in (4, 5, 6)
    }

    av = avaliar_composto(
        "regiao", {"produto": "Beleza", "regiao": "Nordeste"}, _linhas(dados), None, 3, 0.10
    )[0]

    assert av.classificacao == "saudavel" and av.variacao_yoy == pytest.approx(0.28)


def test_composto_estavel_e_saudavel_e_sem_historico_nao_e_classificado():
    estavel = {f"{a}-0{m}": 100.0 for a in (2025, 2026) for m in (4, 5, 6)}
    sem_hist = {f"2026-0{m}": 100.0 for m in (4, 5, 6)}

    assert (
        avaliar_composto("canal", {}, _linhas(estavel), None, 3, 0.10)[0].classificacao
        == "saudavel"
    )
    assert avaliar_composto("canal", {}, _linhas(sem_hist), None, 3, 0.10) == []
