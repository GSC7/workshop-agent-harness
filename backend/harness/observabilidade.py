"""Trace por run no Langfuse (#11). Observabilidade não é caminho crítico: falhar aqui só loga."""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from app.config import Settings

logger = logging.getLogger(__name__)


class Rastreamento(Protocol):
    def iniciar(
        self, run_id: UUID, conversa_id: UUID
    ) -> tuple[str | None, list[BaseCallbackHandler]]:
        """Id do trace do run (None = sem trace) e os callbacks que o alimentam."""
        ...

    def encerrar(self) -> None: ...


class SemRastreamento:
    """Sem chaves do Langfuse: o run segue igual, só sem trace."""

    def iniciar(
        self, run_id: UUID, conversa_id: UUID
    ) -> tuple[str | None, list[BaseCallbackHandler]]:
        return None, []

    def encerrar(self) -> None:
        pass


class RastreamentoLangfuse:
    """Export assíncrono (OTel em lote, em thread): Langfuse fora do ar não trava o /chat."""

    def __init__(self, cliente: Langfuse, public_key: str) -> None:
        self._cliente = cliente
        self._public_key = public_key

    def iniciar(
        self, run_id: UUID, conversa_id: UUID
    ) -> tuple[str | None, list[BaseCallbackHandler]]:
        # Derivado do run_id: do registro no harness se chega ao trace, e vice-versa.
        trace_id = Langfuse.create_trace_id(seed=str(run_id))
        handler = CallbackHandler(public_key=self._public_key, trace_context={"trace_id": trace_id})
        return trace_id, [handler]

    def encerrar(self) -> None:
        try:
            self._cliente.shutdown()  # descarrega o que ficou no lote
        except Exception:
            logger.warning("langfuse: falha ao descarregar traces no encerramento")


def criar_rastreamento(config: Settings) -> Rastreamento:
    if config.langfuse_public_key is None or config.langfuse_secret_key is None:
        logger.info("langfuse sem chaves; runs sem trace")
        return SemRastreamento()
    cliente = Langfuse(
        public_key=config.langfuse_public_key,
        secret_key=config.langfuse_secret_key.get_secret_value(),
        base_url=config.langfuse_host,
        timeout=config.langfuse_timeout_s,
    )
    return RastreamentoLangfuse(cliente, config.langfuse_public_key)
