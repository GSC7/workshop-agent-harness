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


DIAG = {
    "document": "# Tickets — atrasos no Sul",
    "fonte": "minio://corpus/diagnostico/2025-11-sul-atrasos-transportadora.md",
}
PRESC = {
    "document": "# Post-mortem — Frete grátis no Sul",
    "fonte": "minio://corpus/prescricao/2024-08-sul-frete-gratis.md",
    "resultado": "positivo",
}


def _servico_conversa(planos):
    from agent.grounding import Recomendacao

    frete = Recomendacao(
        prioridade=1,
        kpi="taxa_recompra",
        dimensao="Sul",
        acao="Reativar frete grátis",
        justificativa="Funcionou.",
        fonte=PRESC["fonte"],
    )
    deps = Dependencias(
        engine=EngineFake(
            colunas=["dimensao", "mes", "realizado", "meta"],
            linhas=linhas_kpi({"Sul": -0.3, "Nordeste": -0.25}),
        ),  # type: ignore[arg-type]
        qdrant=QdrantFake(por_colecao={"diagnostico": [DIAG], "prescricao": [PRESC]}),  # type: ignore[arg-type]
        embedder=EmbedderFake(),
        planejador=PlanejadorFake(sequencia=list(planos)),
        redator=RedatorFake(),
        recomendador=RecomendadorFake(recomendacoes=[frete]),
    )
    repo, objetos = RepoRunsFake(), ObjetosFake()
    servico = ServicoChat(
        grafo=construir_grafo(deps),
        repo=repo,
        objetos=objetos,
        bucket="relatorios",  # type: ignore[arg-type]
        modelos={},
    )
    return servico, repo


async def _turno(servico, pergunta, conversa_id):
    return [json.loads(e[len("data: ") :]) async for e in servico.executar(pergunta, conversa_id)]


async def test_segundo_turno_herda_o_kpi_e_grava_a_conversa():
    from uuid import uuid4

    servico, repo = _servico_conversa(
        [
            Plano(kpis=["taxa_recompra"], eixo="regiao", valor="Sul"),
            Plano(eixo="regiao", valor="Nordeste"),
        ]
    )
    conversa = uuid4()

    await _turno(servico, "Como melhorar a recompra no Sul?", conversa)
    eventos = await _turno(servico, "e no Nordeste?", conversa)

    plano = next(e for e in eventos if e["tipo"] == "plano")["plano"]
    assert (plano["kpis"], plano["valor"]) == (["taxa_recompra"], "Nordeste")
    assert [r.conversa_id for r in repo.runs] == [conversa, conversa]
    assert eventos[0] == {
        "tipo": "inicio",
        "run_id": str(repo.runs[1].id),
        "conversa_id": str(conversa),
    }


async def test_recomendacao_do_turno_anterior_nao_se_repete_na_mesma_conversa():
    from uuid import uuid4

    plano = Plano(kpis=["taxa_recompra"], eixo="regiao", valor="Sul")
    servico, repo = _servico_conversa([plano, plano])
    conversa = uuid4()

    primeiro = await _turno(servico, "Como melhorar a recompra no Sul?", conversa)
    segundo = await _turno(servico, "E agora, o que fazer no Sul?", conversa)

    assert len(next(e for e in primeiro if e["tipo"] == "recomendacoes")["recomendacoes"]) == 1
    assert next(e for e in segundo if e["tipo"] == "recomendacoes")["recomendacoes"] == []
    assert len(next(e for e in segundo if e["tipo"] == "retomadas")["retomadas"]) == 1
    assert repo.runs[0].recomendacoes[0]["fonte"] == PRESC["fonte"]


async def test_conversas_distintas_nao_vazam_estado():
    from uuid import uuid4

    plano = Plano(kpis=["taxa_recompra"], eixo="regiao", valor="Sul")
    servico, _ = _servico_conversa([plano, Plano(eixo="regiao", valor="Nordeste")])

    await _turno(servico, "Como melhorar a recompra no Sul?", uuid4())
    eventos = await _turno(servico, "e no Nordeste?", uuid4())  # outra conversa

    plano2 = next(e for e in eventos if e["tipo"] == "plano")["plano"]
    assert plano2["kpis"] == []  # nada herdado de outra conversa
    assert next(e for e in eventos if e["tipo"] == "retomadas")["retomadas"] == []


async def test_post_chat_sem_conversa_gera_uma_e_devolve_no_inicio(cliente):
    servico, repo, _ = _servico()
    app.dependency_overrides[get_servico_chat] = lambda: servico

    resp = await cliente.post("/chat", json={"pergunta": "Como melhorar a recompra no Sul?"})

    inicio = _eventos(resp.text)[0]
    assert inicio["conversa_id"] == str(repo.runs[0].conversa_id)


async def test_post_chat_com_conversa_usa_a_informada(cliente):
    from uuid import uuid4

    servico, repo, _ = _servico()
    app.dependency_overrides[get_servico_chat] = lambda: servico
    conversa = uuid4()

    await cliente.post(
        "/chat", json={"pergunta": "Como melhorar a recompra no Sul?", "conversa_id": str(conversa)}
    )

    assert repo.runs[0].conversa_id == conversa
