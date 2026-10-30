"""Observabilidade (#11): trace por run com nós, tools e LLM — offline, SDK real sem rede."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator, Sequence
from itertools import count
from typing import Any
from uuid import uuid4

import pytest
from fakes import RepoRunsFake
from langfuse import Langfuse
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_chat_sse import _eventos, _servico

from app.config import Settings
from app.dependencies import get_servico_chat
from app.main import app
from harness.observabilidade import (
    RastreamentoLangfuse,
    SemRastreamento,
    criar_rastreamento,
)

_chaves = count()


Fabrica = Callable[[SpanExporter], tuple[Langfuse, str]]


@pytest.fixture
def langfuse_teste() -> Iterator[Fabrica]:
    """Cliente real sem rede; shutdown no teardown (a thread de export não vaza entre testes)."""
    clientes: list[Langfuse] = []

    def criar(exporter: SpanExporter) -> tuple[Langfuse, str]:
        # O SDK guarda um cliente por public_key: chave nova por teste isola os exporters.
        chave = f"pk-lf-teste-{next(_chaves)}"
        cliente = Langfuse(
            public_key=chave,
            secret_key="sk-lf-teste",
            base_url="http://127.0.0.1:9",  # porta morta: nada sai da máquina
            span_exporter=exporter,
            flush_at=1,
        )
        clientes.append(cliente)
        return cliente, chave

    yield criar
    for c in clientes:
        c.shutdown()


def _spans_por_nome(spans: Sequence[ReadableSpan]) -> dict[str, list[ReadableSpan]]:
    nomes: dict[str, list[ReadableSpan]] = {}
    for s in spans:
        nomes.setdefault(s.name, []).append(s)
    return nomes


def _hex(trace_id: int) -> str:
    return format(trace_id, "032x")


async def test_run_gera_trace_com_nos_e_tools_e_grava_o_id_no_harness(cliente, langfuse_teste):
    exporter = InMemorySpanExporter()
    lf, chave = langfuse_teste(exporter)
    servico, repo, _ = _servico()
    servico._rastreamento = RastreamentoLangfuse(lf, chave)
    app.dependency_overrides[get_servico_chat] = lambda: servico

    resp = await cliente.post("/chat", json={"pergunta": "Como melhorar a recompra no Sul?"})
    lf.flush()

    assert _eventos(resp.text)[-1]["tipo"] == "fim"
    run = repo.runs[0]
    assert run.trace_id is not None and len(run.trace_id) == 32
    spans = exporter.get_finished_spans()
    assert {_hex(s.context.trace_id) for s in spans} == {run.trace_id}  # um trace só
    nomes = _spans_por_nome(spans)
    assert {"planejar", "perna_quantitativa", "enriquecer", "relatorio"} <= nomes.keys()
    tipo = "langfuse.observation.type"
    assert all(s.attributes[tipo] == "tool" for s in nomes["run_sql"] + nomes["search"])
    # A tool fica aninhada no nó que a chamou (não solta na raiz do trace).
    nos = {s.context.span_id: s.name for s in spans}
    assert {nos[s.parent.span_id] for s in nomes["run_sql"]} == {"perna_quantitativa"}
    assert {nos[s.parent.span_id] for s in nomes["search"]} == {"enriquecer"}
    # Turnos da mesma conversa = mesma sessão no Langfuse.
    [raiz] = nomes["agente_analitico"]
    assert raiz.attributes["session.id"] == str(run.conversa_id)


class _ExporterTravado(SpanExporter):
    """Langfuse que não responde: o export fica pendurado até ser liberado."""

    def __init__(self) -> None:
        self.liberar = threading.Event()
        self.chamado = threading.Event()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.chamado.set()
        self.liberar.wait(timeout=10)
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self.liberar.set()


async def test_langfuse_travado_nao_atrasa_o_chat(cliente, langfuse_teste):
    exporter = _ExporterTravado()
    lf, chave = langfuse_teste(exporter)
    servico, repo, _ = _servico()
    servico._rastreamento = RastreamentoLangfuse(lf, chave)
    app.dependency_overrides[get_servico_chat] = lambda: servico

    try:
        # Export síncrono no caminho do request travaria aqui por 10 s.
        resp = await asyncio.wait_for(
            cliente.post("/chat", json={"pergunta": "Como melhorar a recompra no Sul?"}),
            timeout=3,
        )
        assert exporter.chamado.wait(timeout=3)  # o export travado de fato aconteceu
    finally:
        exporter.liberar.set()

    assert _eventos(resp.text)[-1]["tipo"] == "fim"
    assert repo.runs[0].status == "ok" and repo.runs[0].trace_id is not None


class _ExporterQuebrado(SpanExporter):
    """Langfuse fora do ar: todo export levanta."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise ConnectionError("langfuse indisponível")

    def shutdown(self) -> None:
        pass


async def test_langfuse_fora_do_ar_nao_derruba_o_chat(cliente, langfuse_teste):
    lf, chave = langfuse_teste(_ExporterQuebrado())
    servico, repo, _ = _servico()
    servico._rastreamento = RastreamentoLangfuse(lf, chave)
    app.dependency_overrides[get_servico_chat] = lambda: servico

    resp = await cliente.post("/chat", json={"pergunta": "Como melhorar a recompra no Sul?"})

    assert _eventos(resp.text)[-1]["tipo"] == "fim"
    assert repo.runs[0].status == "ok"


class _RastreamentoQuebrado:
    def iniciar(self, run_id: Any, conversa_id: Any) -> Any:
        raise RuntimeError("sdk quebrado")

    def encerrar(self) -> None:
        pass


async def test_falha_ao_iniciar_o_trace_segue_o_run_sem_trace(cliente):
    servico, repo, _ = _servico()
    servico._rastreamento = _RastreamentoQuebrado()  # type: ignore[assignment]
    app.dependency_overrides[get_servico_chat] = lambda: servico

    resp = await cliente.post("/chat", json={"pergunta": "Como melhorar a recompra no Sul?"})

    assert _eventos(resp.text)[-1]["tipo"] == "fim"
    assert repo.runs[0].status == "ok" and repo.runs[0].trace_id is None


async def test_sem_rastreamento_o_run_grava_trace_id_nulo(cliente):
    servico, repo, _ = _servico()
    app.dependency_overrides[get_servico_chat] = lambda: servico

    await cliente.post("/chat", json={"pergunta": "Como melhorar a recompra no Sul?"})

    assert repo.runs[0].trace_id is None


def test_trace_id_e_derivado_do_run_id(langfuse_teste):
    lf, chave = langfuse_teste(InMemorySpanExporter())
    rastreamento = RastreamentoLangfuse(lf, chave)
    run_id = uuid4()

    primeiro, _ = rastreamento.iniciar(run_id, uuid4())
    segundo, _ = rastreamento.iniciar(run_id, uuid4())

    assert primeiro == segundo  # o id é achável no Langfuse a partir do run do harness


def test_sem_chaves_o_rastreamento_fica_desligado():
    config = Settings(_env_file=None, langfuse_public_key=None, langfuse_secret_key=None)  # type: ignore[call-arg]

    assert isinstance(criar_rastreamento(config), SemRastreamento)


def test_com_chaves_o_rastreamento_usa_o_host_do_settings():
    config = Settings(  # type: ignore[call-arg]
        _env_file=None,
        langfuse_public_key=f"pk-lf-teste-{next(_chaves)}",
        langfuse_secret_key="sk-lf-teste",
        langfuse_host="http://127.0.0.1:9",
    )

    rastreamento = criar_rastreamento(config)

    assert isinstance(rastreamento, RastreamentoLangfuse)
    rastreamento.encerrar()


async def test_repo_fake_aceita_registro_com_trace_id():
    # Contrato: o registro do run carrega o id do trace (coluna harness.runs.trace_id).
    from harness.modelo import RegistroRun

    repo = RepoRunsFake()
    await repo.gravar(
        RegistroRun(id=uuid4(), pergunta="p", status="ok", duracao_ms=1, trace_id="a" * 32)
    )

    assert repo.runs[0].trace_id == "a" * 32


async def test_como_tool_sem_callbacks_so_executa():
    from agent.rastro import como_tool

    async def soma() -> int:
        return 2

    assert await como_tool("run_sql", {"sql": "SELECT 1"}, soma(), lambda r: r) == 2


async def test_como_tool_repassa_o_erro_da_chamada_e_registra_no_rastro():
    import pytest
    from langchain_core.callbacks import AsyncCallbackHandler
    from langchain_core.runnables.config import var_child_runnable_config

    from agent.rastro import como_tool
    from agent.tools.run_sql import SQLRejeitado

    class Ouvinte(AsyncCallbackHandler):
        def __init__(self) -> None:
            self.eventos: list[str] = []

        async def on_tool_start(self, serialized: Any, input_str: str, **kw: Any) -> None:
            self.eventos.append(f"inicio:{serialized['name']}")

        async def on_tool_error(self, error: BaseException, **kw: Any) -> None:
            self.eventos.append(f"erro:{type(error).__name__}")

    async def rejeitada() -> None:
        raise SQLRejeitado("só SELECT")

    ouvinte = Ouvinte()
    token = var_child_runnable_config.set({"callbacks": [ouvinte]})
    try:
        with pytest.raises(SQLRejeitado):
            await como_tool("run_sql", {"sql": "DELETE"}, rejeitada(), lambda r: r)
    finally:
        var_child_runnable_config.reset(token)

    assert ouvinte.eventos == ["inicio:run_sql", "erro:SQLRejeitado"]


async def test_como_tool_com_resumo_quebrado_devolve_o_resultado():
    from langchain_core.callbacks import AsyncCallbackHandler
    from langchain_core.runnables.config import var_child_runnable_config

    from agent.rastro import como_tool

    class Ouvinte(AsyncCallbackHandler):
        async def on_tool_start(self, serialized: Any, input_str: str, **kw: Any) -> None:
            pass

    async def ok() -> int:
        return 7

    def resumo_quebrado(r: int) -> Any:
        raise KeyError("campo")

    token = var_child_runnable_config.set({"callbacks": [Ouvinte()]})
    try:
        resultado = await como_tool("run_sql", {"sql": "SELECT 1"}, ok(), resumo_quebrado)
    finally:
        var_child_runnable_config.reset(token)

    assert resultado == 7
