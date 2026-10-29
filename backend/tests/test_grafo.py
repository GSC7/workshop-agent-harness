from __future__ import annotations

import json
from datetime import date

import pytest

from fakes import (
    EmbedderFake,
    EngineFake,
    PlanejadorFake,
    QdrantFake,
    RedatorFake,
    linhas_historico,
    linhas_kpi,
)

from agent.grafo import ConfigAnalise, Dependencias, construir_grafo, selecionar_fracos
from agent.kpis import KpiAvaliado
from agent.state import Plano

DOC_SUL = {
    "document": "Frete grátis no Sul elevou a recompra.",
    "fonte": "minio://corpus/prescricao/2024-08-sul-frete-gratis.md",
    "resultado": "positivo",
}


def _deps(plano, linhas, qdrant=None, embedder=None, redator=None):
    return Dependencias(
        engine=EngineFake(colunas=["dimensao", "mes", "realizado", "meta"], linhas=linhas),  # type: ignore[arg-type]
        qdrant=qdrant or QdrantFake(payloads=[DOC_SUL]),  # type: ignore[arg-type]
        embedder=embedder if embedder is not None else EmbedderFake(),
        planejador=PlanejadorFake(plano=plano),
        redator=redator or RedatorFake(),
        config=ConfigAnalise(),
        hoje=lambda: date(2026, 10, 28),
    )


async def _rodar(deps, pergunta="Como melhorar a recompra no Sul?"):
    eventos, final = [], {}
    async for modo, chunk in construir_grafo(deps).astream(
        {"pergunta": pergunta}, stream_mode=["custom", "values"]
    ):
        if modo == "custom":
            eventos.append(chunk)
        else:
            final = chunk
    return eventos, final


def test_topologia_e_fixa():
    nos = set(construir_grafo(_deps(Plano(), [])).get_graph().nodes)

    assert nos == {
        "__start__",
        "planejar",
        "clarificar",
        "perna_quantitativa",
        "enriquecer",
        "relatorio",
        "__end__",
    }


async def test_caso_central_mede_kpi_fraco_enriquece_e_redige():
    redator = RedatorFake()
    plano = Plano(kpis=["taxa_recompra"], eixo="regiao", valor="sul")
    deps = _deps(plano, linhas_kpi({"Sul": -0.28, "Norte": 0.05}), redator=redator)

    eventos, final = await _rodar(deps)

    assert [(f.kpi, f.dimensao) for f in final["fracos"]] == [("taxa_recompra", "Sul")]
    assert len(final["sqls"]) == 1 and final["sqls"][0].endswith("LIMIT 500")
    assert [h.fonte for h in final["hits"]] == [DOC_SUL["fonte"]]
    assert final["relatorio"].endswith("## Relatório\nRecompra no Sul.")
    etapas = [e["nome"] for e in eventos if e["tipo"] == "etapa"]
    assert etapas == ["planejar", "perna_quantitativa", "enriquecer", "relatorio"]
    tipos = [e["tipo"] for e in eventos]
    assert tipos.index("sql") < tipos.index("fontes") < tipos.index("relatorio")
    # o redator só recebe o que foi medido/recuperado neste run
    contexto = json.loads(redator.contextos[0])
    assert contexto["fontes_recuperadas"][0]["fonte"] == DOC_SUL["fonte"]
    assert contexto["kpis_queda_real"][0]["dimensao"] == "Sul"


async def test_enriquecimento_filtra_pela_dimensao_e_kpi_do_kpi_fraco():
    qdrant = QdrantFake(payloads=[DOC_SUL])
    deps = _deps(
        Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas_kpi({"Sul": -0.3}), qdrant=qdrant
    )

    await _rodar(deps)

    colecoes = [c["collection_name"] for c in qdrant.chamadas]
    assert colecoes == ["diagnostico", "prescricao"]
    filtros = [{c.key: c.match.value for c in ch["query_filter"].must} for ch in qdrant.chamadas]
    assert filtros == [{"regiao": "Sul"}, {"kpi_alvo": "taxa_recompra", "regiao": "Sul"}]


async def test_sem_kpi_fraco_pula_o_enriquecimento():
    qdrant = QdrantFake(payloads=[DOC_SUL])
    deps = _deps(
        Plano(kpis=["faturamento"], eixo="regiao"), linhas_kpi({"Sul": 0.02}), qdrant=qdrant
    )

    eventos, final = await _rodar(deps)

    assert final["fracos"] == [] and qdrant.chamadas == []
    assert "enriquecer" not in [e.get("nome") for e in eventos]
    assert final["relatorio"]


async def test_sem_embedder_avisa_e_segue_sem_fontes():
    deps = _deps(Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas_kpi({"Sul": -0.3}))
    deps = Dependencias(**{**deps.__dict__, "embedder": None})

    eventos, final = await _rodar(deps)

    assert final["hits"] == []
    assert any(e["tipo"] == "aviso" for e in eventos)


def _av(dim, gap, classificacao="queda_real", eixo="regiao"):
    return KpiAvaliado(
        kpi="faturamento",
        eixo=eixo,
        dimensao=dim,
        gap_medio=gap,
        variacao_yoy=None,
        classificacao=classificacao,
        serie=[],
    )


def test_selecao_pega_so_a_classificacao_pedida_pior_primeiro_ate_o_maximo():
    avaliados = [
        _av("A", -0.05, "saudavel"),
        _av("B", -0.30),
        _av("C", -0.12),
        _av("D", -0.20),
        _av("E", -0.15),
        _av("F", -0.40, "sazonal"),
    ]

    fracos = selecionar_fracos(avaliados, None, maximo=3)

    assert [f.dimensao for f in fracos] == ["B", "D", "E"]
    assert [s.dimensao for s in selecionar_fracos(avaliados, None, 3, "sazonal")] == ["F"]


@pytest.mark.parametrize("valor", ["sul", "SUL", " Sul "])
def test_recorte_por_valor_ignora_caixa_e_espacos(valor):
    fracos = selecionar_fracos([_av("Sul", -0.3), _av("Norte", -0.4)], valor, 3)

    assert [f.dimensao for f in fracos] == ["Sul"]


def test_valor_desconhecido_nao_zera_a_analise():
    fracos = selecionar_fracos([_av("Sul", -0.3)], "Atlântida", 3)

    assert [f.dimensao for f in fracos] == ["Sul"]


async def test_falha_na_busca_degrada_com_aviso_em_vez_de_abortar_o_run():
    from qdrant_client.http.exceptions import ResponseHandlingException

    qdrant = QdrantFake(erro=ResponseHandlingException(OSError("x")))
    deps = _deps(
        Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas_kpi({"Sul": -0.3}), qdrant=qdrant
    )

    eventos, final = await _rodar(deps)

    assert final["hits"] == [] and final["relatorio"]
    assert any(e["tipo"] == "aviso" for e in eventos)


async def test_valor_citado_inexistente_vira_aviso_para_o_redator():
    redator = RedatorFake()
    plano = Plano(kpis=["taxa_recompra"], eixo="regiao", valor="Atlântida")
    deps = _deps(plano, linhas_kpi({"Sul": -0.3}), redator=redator)

    eventos, final = await _rodar(deps)

    avisos = [e["mensagem"] for e in eventos if e["tipo"] == "aviso"]
    assert any("Atlântida" in a for a in avisos)
    assert "Atlântida" in json.dumps(json.loads(redator.contextos[0])["avisos"], ensure_ascii=False)


async def test_cada_hit_diz_a_colecao_e_o_kpi_fraco_que_ele_explica():
    deps = _deps(Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas_kpi({"Sul": -0.3}))

    _, final = await _rodar(deps)

    meta = final["hits"][0].metadados
    assert meta["colecao"] == "diagnostico"
    assert (meta["para_kpi"], meta["para_dimensao"]) == ("taxa_recompra", "Sul")


async def test_redator_recebe_desvio_ja_calculado_e_situacao_explicita():
    # Regressão do smoke real: o LLM leu a série crua e disse "perto da meta" p/ um KPI -11%.
    redator = RedatorFake()
    deps = _deps(
        Plano(kpis=["faturamento"], eixo="categoria"),
        linhas_kpi({"Brinquedos": -0.113}),
        redator=redator,
    )

    await _rodar(deps)

    kpi = json.loads(redator.contextos[0])["kpis_queda_real"][0]
    assert kpi["desvio_vs_meta_pct"] == -11.3
    assert kpi["situacao"].startswith("abaixo da meta")
    assert kpi["dimensao"] == "Brinquedos"


async def test_avisos_repetidos_sao_deduplicados():
    from qdrant_client.http.exceptions import ResponseHandlingException

    qdrant = QdrantFake(erro=ResponseHandlingException(OSError("x")))
    plano = Plano(kpis=["taxa_recompra"], eixo="regiao")
    deps = _deps(plano, linhas_kpi({"Sul": -0.3, "Nordeste": -0.2}), qdrant=qdrant)

    _, final = await _rodar(deps)

    assert len(final["avisos"]) == len(set(final["avisos"]))


async def test_relatorio_comeca_com_premissas_escritas_pelo_codigo():
    eventos, final = await _rodar(
        _deps(Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas_kpi({"Sul": -0.3}))
    )

    rel = final["relatorio"]
    assert rel.startswith("## Premissas")
    assert "2026-11" in rel  # período-alvo = mês atual + 1 (hoje = 2026-10-28)
    assert "2026-06" in rel  # dados disponíveis até
    assert "taxa_recompra" in rel and "regiao" in rel  # escopo
    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["periodo_alvo"] == "2026-11" and premissas["dados_ate"] == "2026-06"


async def test_mes_citado_vira_o_periodo_alvo():
    plano = Plano(kpis=["taxa_conversao"], eixo="canal", mes_citado=7)
    pergunta = "Como aumentar a conversão do site em julho?"

    eventos, _ = await _rodar(_deps(plano, linhas_kpi({"site_proprio": 0.0})), pergunta=pergunta)
    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["periodo_alvo"] == "2027-07"


async def test_sazonal_nao_dispara_enriquecimento_e_vai_ao_relatorio_como_sazonal():
    qdrant, redator = QdrantFake(payloads=[DOC_SUL]), RedatorFake()
    # Brinquedos: -20% na meta de 2026, mas igual a 2024/2025 (padrão de sempre).
    linhas = linhas_historico({"Brinquedos": {2024: 0.8, 2025: 0.8, 2026: 0.8}})
    deps = _deps(
        Plano(kpis=["faturamento"], eixo="categoria"), linhas, qdrant=qdrant, redator=redator
    )

    eventos, final = await _rodar(deps)

    assert final["fracos"] == [] and qdrant.chamadas == []
    contexto = json.loads(redator.contextos[0])
    assert contexto["kpis_queda_real"] == []
    assert contexto["kpis_sazonais"][0]["dimensao"] == "Brinquedos"
    assert contexto["kpis_sazonais"][0]["situacao"].startswith("abaixo da meta, em linha")


async def test_tudo_saudavel_gera_relatorio_com_premissas_e_sem_enriquecer():
    qdrant, redator = QdrantFake(payloads=[DOC_SUL]), RedatorFake()
    deps = _deps(
        Plano(kpis=["faturamento"], eixo="regiao"),
        linhas_kpi({"Sul": 0.03, "Norte": 0.01}),
        qdrant=qdrant,
        redator=redator,
    )

    _, final = await _rodar(deps)

    assert qdrant.chamadas == [] and final["relatorio"].startswith("## Premissas")
    contexto = json.loads(redator.contextos[0])
    assert contexto["kpis_queda_real"] == [] and contexto["kpis_sazonais"] == []


async def test_pergunta_fora_do_escopo_devolve_clarificacao_sem_rodar_sql():
    deps = _deps(Plano(fora_do_escopo=True), linhas_kpi({"Sul": -0.3}))
    redator = deps.redator

    eventos, final = await _rodar(deps, pergunta="Qual a capital da França?")

    assert deps.engine.executados == []  # type: ignore[attr-defined]
    assert redator.contextos == []  # type: ignore[attr-defined]  # LLM redator nem é chamado
    assert "?" in final["relatorio"] and "faturamento" in final["relatorio"]
    assert [e["nome"] for e in eventos if e["tipo"] == "etapa"] == ["planejar", "clarificar"]


async def test_premissas_declaram_o_escopo_realmente_analisado():
    # Recompra não tem meta por canal: a análise roda total + regiao, e é isso que se declara.
    plano = Plano(kpis=["taxa_recompra"], eixo="canal")

    eventos, final = await _rodar(_deps(plano, linhas_kpi({"Sul": -0.3})))

    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["eixos"] == ["regiao", "total"]
    assert any("canal" in a for a in final["avisos"])


async def test_sem_dados_as_premissas_saem_mesmo_assim():
    _, final = await _rodar(_deps(Plano(kpis=["faturamento"], eixo="regiao"), []))

    assert final["relatorio"].startswith("## Premissas") and "sem dados" in final["relatorio"]


async def test_resultado_no_teto_de_linhas_vira_aviso():
    deps = _deps(Plano(kpis=["faturamento"], eixo="regiao"), linhas_kpi({"Sul": 0.0}))
    deps = Dependencias(**{**deps.__dict__, "config": ConfigAnalise(sql_teto_linhas=6)})

    _, final = await _rodar(deps)

    assert any("teto" in a for a in final["avisos"])


async def test_mes_citado_inventado_pelo_llm_e_ignorado():
    # Regressão do smoke: "próximo mês" e o LLM preencheu mes_citado=11.
    plano = Plano(kpis=["faturamento"], eixo="regiao", mes_citado=11)

    eventos, _ = await _rodar(
        _deps(plano, linhas_kpi({"Sul": 0.0})),
        pergunta="Como melhorar minhas vendas no próximo mês?",
    )

    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["origem_periodo"] == "mês atual + 1" and premissas["periodo_alvo"] == "2026-11"
