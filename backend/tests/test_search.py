from __future__ import annotations

import pytest
from fakes import EmbedderFake, QdrantFake

from qdrant_client.http.exceptions import ResponseHandlingException

from agent.tools.search import BuscaRejeitada, Dimensao, ErroBusca, buscar, criar_tool_search

DOC_SUL = {
    "document": "Frete grátis no Sul elevou a recompra.",
    "fonte": "minio://corpus/prescricao/2024-08-sul-frete-gratis.md",
    "periodo_referencia": "2024-08",
    "resultado": "positivo",
    "kpi_alvo": "taxa_recompra",
    "regiao": "Sul",
}


async def test_busca_embedda_a_query_e_consulta_a_colecao_filtrada():
    qdrant, embedder = QdrantFake(payloads=[DOC_SUL]), EmbedderFake()

    hits = await buscar(
        qdrant,  # type: ignore[arg-type]
        embedder,
        "prescricao",
        "como aumentar a recompra",
        kpi_alvo="taxa_recompra",
        dimensao=Dimensao(regiao="Sul"),
        limite=3,
    )

    assert embedder.textos == ["como aumentar a recompra"]
    chamada = qdrant.chamadas[0]
    assert chamada["collection_name"] == "prescricao"
    assert chamada["limit"] == 3
    assert {c.key for c in chamada["query_filter"].must} == {"kpi_alvo", "regiao"}
    assert len(chamada["query"]) == 3072
    assert hits[0].fonte == DOC_SUL["fonte"]
    assert hits[0].texto == DOC_SUL["document"]
    assert hits[0].metadados["resultado"] == "positivo"
    assert "document" not in hits[0].metadados


async def test_hit_sem_fonte_e_descartado_porque_nao_pode_ser_citado():
    sem_fonte = {k: v for k, v in DOC_SUL.items() if k != "fonte"}
    qdrant = QdrantFake(payloads=[sem_fonte, DOC_SUL])

    hits = await buscar(
        qdrant,  # type: ignore[arg-type]
        EmbedderFake(),
        "diagnostico",
        "atrasos",
        dimensao=Dimensao(regiao="Sul"),
    )

    assert [h.fonte for h in hits] == [DOC_SUL["fonte"]]


async def test_busca_rejeitada_nao_gasta_embedding_nem_consulta():
    qdrant, embedder = QdrantFake(), EmbedderFake()

    with pytest.raises(BuscaRejeitada):
        await buscar(qdrant, embedder, "prescricao", "x", dimensao=Dimensao(regiao="Sul"))  # type: ignore[arg-type]

    assert embedder.textos == [] and qdrant.chamadas == []


async def test_embedding_com_dimensao_errada_falha_cedo():
    with pytest.raises(ErroBusca, match="3072"):
        await buscar(
            QdrantFake(),  # type: ignore[arg-type]
            EmbedderFake(dimensao=1536),
            "diagnostico",
            "x",
            dimensao=Dimensao(regiao="Sul"),
        )


async def test_falha_do_qdrant_vira_erro_de_busca_sem_detalhe():
    qdrant = QdrantFake(erro=ResponseHandlingException(OSError("host=qdrant segredo")))

    with pytest.raises(ErroBusca, match="indisponível") as exc:
        await buscar(qdrant, EmbedderFake(), "diagnostico", "x", dimensao=Dimensao(regiao="Sul"))  # type: ignore[arg-type]

    assert "segredo" not in str(exc.value)


async def test_tool_langchain_devolve_hits_com_fonte():
    tool = criar_tool_search(QdrantFake(payloads=[DOC_SUL]), EmbedderFake())  # type: ignore[arg-type]

    saida = await tool.ainvoke(
        {"colecao": "prescricao", "query": "recompra", "kpi_alvo": "taxa_recompra", "regiao": "Sul"}
    )

    assert tool.name == "search"
    assert saida["hits"][0]["fonte"] == DOC_SUL["fonte"]
    assert saida["hits"][0]["metadados"]["resultado"] == "positivo"


async def test_tool_langchain_devolve_erro_legivel_quando_falta_filtro():
    tool = criar_tool_search(QdrantFake(), EmbedderFake())  # type: ignore[arg-type]

    saida = await tool.ainvoke({"colecao": "prescricao", "query": "recompra", "regiao": "Sul"})

    assert saida == {"erro": "prescricao exige kpi_alvo"}
