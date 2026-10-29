from __future__ import annotations

import json

import httpx

from evals.execucao import executar_caso
from evals.golden import Caso

CASO = Caso(
    id="n1", pergunta="Recompra no Sul?", dispara_enriquecimento=True, recomendacao_esperada="x"
)


def _sse(*eventos):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in eventos)


def _cliente(corpo: str, status: int = 200):
    def responder(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/chat" and json.loads(req.content)["pergunta"] == "Recompra no Sul?"
        return httpx.Response(status, text=corpo, headers={"content-type": "text/event-stream"})

    return httpx.AsyncClient(transport=httpx.MockTransport(responder), base_url="http://teste")


async def test_resume_o_stream_real_do_chat():
    corpo = _sse(
        {"tipo": "inicio", "run_id": "r", "conversa_id": "c"},
        {"tipo": "etapa", "nome": "planejar"},
        {"tipo": "sql", "kpi": "k", "eixo": "e", "sql": "SELECT 1"},
        {"tipo": "etapa", "nome": "enriquecer"},
        {"tipo": "aviso", "mensagem": "a"},
        {"tipo": "relatorio", "texto": "## Premissas"},
        {"tipo": "relatorio", "texto": "\n..."},
        {"tipo": "fontes_citadas", "fontes": ["f1", "f2"]},
        {"tipo": "fim", "run_id": "r", "relatorio_uri": "u"},
    )

    async with _cliente(corpo) as cliente:
        run = await executar_caso(cliente, CASO)

    assert run.concluido and run.etapas == ["planejar", "enriquecer"] and run.sqls == 1
    assert (
        run.relatorio == "## Premissas\n..."
        and run.fontes_citadas == ["f1", "f2"]
        and run.avisos == ["a"]
    )


async def test_stream_sem_fim_nao_conta_como_concluido():
    async with _cliente(_sse({"tipo": "erro", "run_id": "r", "mensagem": "x"})) as cliente:
        run = await executar_caso(cliente, CASO)

    assert not run.concluido


async def test_http_de_erro_vira_run_nao_concluido():
    async with _cliente("indisponível", status=503) as cliente:
        run = await executar_caso(cliente, CASO)

    assert not run.concluido and run.avisos == ["HTTP 503"]


async def test_evento_de_erro_do_servidor_vai_para_o_run():
    async with _cliente(
        _sse({"tipo": "erro", "run_id": "r", "mensagem": "não foi possível concluir"})
    ) as cliente:
        run = await executar_caso(cliente, CASO)

    assert not run.concluido and run.erro == "não foi possível concluir"


async def test_caso_que_estoura_o_prazo_total_vira_erro():
    import asyncio

    async def lento(req):
        await asyncio.sleep(5)
        return httpx.Response(200, text="")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lento), base_url="http://t"
    ) as cliente:
        run = await executar_caso(cliente, CASO, prazo_s=0.05)

    assert not run.concluido and "prazo" in (run.erro or "")
