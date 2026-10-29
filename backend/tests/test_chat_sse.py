from __future__ import annotations

import json


from fakes import (
    EmbedderFake,
    EngineFake,
    ObjetosFake,
    PlanejadorFake,
    QdrantFake,
    RecomendadorFake,
    RedatorFake,
    RepoRunsFake,
    linhas_kpi,
)

from agent.grafo import Dependencias, construir_grafo
from agent.state import Plano
from app.dependencies import get_servico_chat
from app.main import app
from app.services.chat import ServicoChat

DOC = {
    "document": "Frete grátis no Sul.",
    "fonte": "minio://corpus/prescricao/2024-08-sul-frete-gratis.md",
}


def _servico(planejador=None):
    deps = Dependencias(
        engine=EngineFake(
            colunas=["dimensao", "mes", "realizado", "meta"], linhas=linhas_kpi({"Sul": -0.3})
        ),  # type: ignore[arg-type]
        qdrant=QdrantFake(payloads=[DOC]),  # type: ignore[arg-type]
        embedder=EmbedderFake(),
        planejador=planejador or PlanejadorFake(plano=Plano(kpis=["taxa_recompra"], eixo="regiao")),
        redator=RedatorFake(),
        recomendador=RecomendadorFake(),
    )
    repo, objetos = RepoRunsFake(), ObjetosFake()
    servico = ServicoChat(
        grafo=construir_grafo(deps),
        repo=repo,
        objetos=objetos,
        bucket="relatorios",  # type: ignore[arg-type]
        modelos={"forte": "f", "rapido": "r"},
    )
    return servico, repo, objetos


def _eventos(corpo: str) -> list[dict]:
    return [json.loads(ln[len("data: ") :]) for ln in corpo.splitlines() if ln.startswith("data: ")]


async def test_chat_responde_em_sse_e_grava_run_e_relatorio(cliente):
    servico, repo, objetos = _servico()
    app.dependency_overrides[get_servico_chat] = lambda: servico

    resp = await cliente.post("/chat", json={"pergunta": "Como melhorar a recompra no Sul?"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    eventos = _eventos(resp.text)
    tipos = [e["tipo"] for e in eventos]
    assert tipos[0] == "inicio" and tipos[-1] == "fim"
    assert {"etapa", "sql", "fontes", "relatorio"} <= set(tipos)
    run = repo.runs[0]
    assert run.status == "ok" and str(run.id) == eventos[-1]["run_id"]
    assert run.sqls and run.fontes[0]["fonte"] == DOC["fonte"]
    assert run.fracos[0]["dimensao"] == "Sul"
    assert run.relatorio_uri == f"minio://relatorios/{run.id}.md"
    assert objetos.objetos[f"relatorios/{run.id}.md"].decode() == run.relatorio
    assert eventos[-1]["relatorio_uri"] == run.relatorio_uri


async def test_falha_no_grafo_vira_evento_de_erro_e_run_com_status_erro(cliente):
    servico, repo, _ = _servico(PlanejadorFake(erro=RuntimeError("chave=segredo")))
    app.dependency_overrides[get_servico_chat] = lambda: servico

    resp = await cliente.post("/chat", json={"pergunta": "x"})

    eventos = _eventos(resp.text)
    assert eventos[-1]["tipo"] == "erro"
    assert "segredo" not in resp.text and "Traceback" not in resp.text
    assert repo.runs[0].status == "erro" and repo.runs[0].erro == "RuntimeError"


async def test_pergunta_vazia_e_rejeitada_na_validacao(cliente):
    servico, repo, _ = _servico()
    app.dependency_overrides[get_servico_chat] = lambda: servico

    resp = await cliente.post("/chat", json={"pergunta": "   "})

    assert resp.status_code == 422 and repo.runs == []


async def test_chat_indisponivel_sem_llm_configurado_responde_503(cliente):
    app.dependency_overrides[get_servico_chat] = lambda: None

    resp = await cliente.post("/chat", json={"pergunta": "Como melhorar as vendas?"})

    assert resp.status_code == 503


async def test_cliente_desconectando_no_meio_ainda_grava_o_run_como_cancelado():
    servico, repo, _ = _servico()
    gerador = servico.executar("Como melhorar a recompra no Sul?")

    await anext(gerador)  # inicio
    await anext(gerador)  # primeiro evento do grafo
    await gerador.aclose()  # o StreamingResponse faz isso quando o cliente cai

    assert len(repo.runs) == 1 and repo.runs[0].status == "cancelado"


async def test_cancelamento_pelo_anyio_espera_a_gravacao_do_run():
    # Como o Starlette na desconexão: cancel scope do anyio (nível) com o gerador no meio de
    # um await (redator ainda escrevendo). asyncio.shield sozinho não espera a gravação.
    import anyio

    class RedatorLento(RedatorFake):
        async def redigir(self, pergunta, contexto):
            yield "## Começo"
            await anyio.sleep(10)
            yield "nunca chega"

    class RepoLento(RepoRunsFake):
        async def gravar(self, run):
            await anyio.sleep(0.05)  # gravação real no Postgres não é instantânea
            await super().gravar(run)

    servico, _, _ = _servico()
    repo = RepoLento()
    servico._repo = repo  # type: ignore[attr-defined]
    servico._grafo = construir_grafo(  # type: ignore[attr-defined]
        Dependencias(
            engine=EngineFake(
                colunas=["dimensao", "mes", "realizado", "meta"], linhas=linhas_kpi({"Sul": -0.3})
            ),  # type: ignore[arg-type]
            qdrant=QdrantFake(payloads=[DOC]),  # type: ignore[arg-type]
            embedder=EmbedderFake(),
            planejador=PlanejadorFake(plano=Plano(kpis=["taxa_recompra"], eixo="regiao")),
            redator=RedatorLento(),
            recomendador=RecomendadorFake(),
        )
    )
    escrevendo = anyio.Event()

    async def consumir():
        async for evento in servico.executar("x"):
            if '"relatorio"' in evento:
                escrevendo.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(consumir)
        await escrevendo.wait()
        tg.cancel_scope.cancel()

    assert [r.status for r in repo.runs] == ["cancelado"]
