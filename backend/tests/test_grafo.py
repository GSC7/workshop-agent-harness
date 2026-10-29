from __future__ import annotations

import json

import pytest
from fakes import (
    EmbedderFake,
    EngineFake,
    PlanejadorFake,
    QdrantFake,
    RedatorFake,
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
    assert final["relatorio"] == "## Relatório\nRecompra no Sul."
    etapas = [e["nome"] for e in eventos if e["tipo"] == "etapa"]
    assert etapas == ["planejar", "perna_quantitativa", "enriquecer", "relatorio"]
    tipos = [e["tipo"] for e in eventos]
    assert tipos.index("sql") < tipos.index("fontes") < tipos.index("relatorio")
    # o redator só recebe o que foi medido/recuperado neste run
    contexto = json.loads(redator.contextos[0])
    assert contexto["fontes_recuperadas"][0]["fonte"] == DOC_SUL["fonte"]
    assert contexto["kpis_abaixo_da_meta"][0]["dimensao"] == "Sul"


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


def _av(dim, gap, eixo="regiao"):
    return KpiAvaliado(kpi="faturamento", eixo=eixo, dimensao=dim, gap_medio=gap, serie=[])


def test_selecao_aplica_limiar_maximo_e_pior_primeiro():
    avaliados = [
        _av("A", -0.05),
        _av("B", -0.30),
        _av("C", -0.12),
        _av("D", -0.20),
        _av("E", -0.15),
    ]

    fracos = selecionar_fracos(avaliados, None, limiar=0.10, maximo=3)

    assert [f.dimensao for f in fracos] == ["B", "D", "E"]


@pytest.mark.parametrize("valor", ["sul", "SUL", " Sul "])
def test_recorte_por_valor_ignora_caixa_e_espacos(valor):
    fracos = selecionar_fracos([_av("Sul", -0.3), _av("Norte", -0.4)], valor, 0.10, 3)

    assert [f.dimensao for f in fracos] == ["Sul"]


def test_valor_desconhecido_nao_zera_a_analise():
    fracos = selecionar_fracos([_av("Sul", -0.3)], "Atlântida", 0.10, 3)

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

    kpi = json.loads(redator.contextos[0])["kpis_abaixo_da_meta"][0]
    assert kpi["desvio_vs_meta_pct"] == -11.3
    assert kpi["situacao"] == "abaixo da meta"
    assert kpi["dimensao"] == "Brinquedos"


async def test_avisos_repetidos_sao_deduplicados():
    from qdrant_client.http.exceptions import ResponseHandlingException

    qdrant = QdrantFake(erro=ResponseHandlingException(OSError("x")))
    plano = Plano(kpis=["taxa_recompra"], eixo="regiao")
    deps = _deps(plano, linhas_kpi({"Sul": -0.3, "Nordeste": -0.2}), qdrant=qdrant)

    _, final = await _rodar(deps)

    assert len(final["avisos"]) == len(set(final["avisos"]))
