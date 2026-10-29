from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import anyio
from langgraph.graph.state import CompiledStateGraph

from harness.artefatos import ClienteObjetos, salvar_relatorio
from harness.modelo import RegistroRun
from harness.repo import RepositorioRuns

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Run:
    """Identidade de UM run (por requisição): nada disso vive no serviço, que é compartilhado."""

    id: UUID
    conversa_id: UUID
    pergunta: str
    inicio: float


def _sse(evento: dict[str, Any]) -> str:
    return f"data: {json.dumps(evento, ensure_ascii=False, default=str)}\n\n"


class ServicoChat:
    """Roda o grafo, repassa os eventos em SSE à medida que saem e grava o run no harness."""

    def __init__(
        self,
        grafo: CompiledStateGraph[Any, Any, Any, Any],
        repo: RepositorioRuns,
        objetos: ClienteObjetos,
        bucket: str,
        modelos: dict[str, str],
    ) -> None:
        self._grafo = grafo
        self._repo = repo
        self._objetos = objetos
        self._bucket = bucket
        self._modelos = modelos

    async def executar(self, pergunta: str, conversa_id: UUID | None = None) -> AsyncIterator[str]:
        run = _Run(uuid4(), conversa_id or uuid4(), pergunta, time.monotonic())
        run_id = run.id
        yield _sse({"tipo": "inicio", "run_id": str(run.id), "conversa_id": str(run.conversa_id)})
        final: dict[str, Any] = {}
        fontes: list[dict[str, Any]] = []
        try:
            # Memória da conversa = os runs dela no harness (só os desta conversa).
            turno = await self._repo.turno_anterior(run.conversa_id)
            async for item in self._grafo.astream(
                {"pergunta": pergunta, "turno_anterior": turno}, stream_mode=["custom", "values"]
            ):
                # Com vários stream_mode, cada item é (modo, payload); as stubs não expressam isso.
                modo, chunk = cast(tuple[str, dict[str, Any]], item)
                if modo == "values":
                    final = chunk
                    continue
                if chunk.get("tipo") == "fontes":
                    fontes = chunk["fontes"]
                yield _sse(chunk)
            relatorio = final.get("relatorio", "")
            uri = await salvar_relatorio(self._objetos, self._bucket, run_id, relatorio)
            await self._repo.gravar(self._registro(run, final, fontes, relatorio, uri))
            yield _sse({"tipo": "fim", "run_id": str(run_id), "relatorio_uri": uri})
        except (asyncio.CancelledError, GeneratorExit):
            # Cliente caiu: o Starlette cancela pelo cancel scope do anyio (nível — reentrega o
            # cancelamento a cada await). Só um escopo blindado espera a gravação do run.
            with anyio.CancelScope(shield=True):
                await self._gravar_falha(run, final, "cancelado", "cancelado")
            raise
        except Exception as exc:
            logger.exception("run %s falhou", run_id)
            await self._gravar_falha(run, final, type(exc).__name__)
            yield _sse(
                {
                    "tipo": "erro",
                    "run_id": str(run_id),
                    "mensagem": "não foi possível concluir a análise; tente novamente",
                }
            )

    def _registro(
        self,
        run: _Run,
        final: dict[str, Any],
        fontes: list[dict[str, Any]],
        relatorio: str | None,
        uri: str | None,
        erro: str | None = None,
    ) -> RegistroRun:
        plano = final.get("plano")
        premissas = final.get("premissas")
        return RegistroRun(
            id=run.id,
            pergunta=run.pergunta,
            status="erro" if erro else "ok",
            duracao_ms=int((time.monotonic() - run.inicio) * 1000),
            plano=plano.model_dump() if plano is not None else None,
            fracos=[asdict(f) for f in final.get("fracos", [])],
            sqls=list(final.get("sqls", [])),
            fontes=fontes,
            relatorio=relatorio,
            relatorio_uri=uri,
            erro=erro,
            modelos=self._modelos,
            conversa_id=run.conversa_id,
            recomendacoes=[r.model_dump() for r in final.get("recomendacoes", [])]
            + [{**r.model_dump(), "retomada": True} for r in final.get("retomadas", [])],
            sazonais=[asdict(a) for a in final.get("sazonais", [])],
            premissas=premissas,
            avisos=list(final.get("avisos", [])),
        )

    async def _gravar_falha(
        self,
        run: _Run,
        final: dict[str, Any],
        erro: str,
        status: Literal["erro", "cancelado"] = "erro",
    ) -> None:
        # O run com erro também é rastro auditável; falhar ao gravá-lo não pode mascarar a causa.
        try:
            registro = self._registro(run, final, [], None, None, erro=erro)
            registro.status = status
            await self._repo.gravar(registro)
        except Exception:
            logger.exception("não foi possível gravar o run %s com erro", run.id)
