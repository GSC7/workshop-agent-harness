from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Detalhe de store é heterogêneo por natureza (usuário do PG, coleções, bucket).
Detalhe = dict[str, Any]


class VerificadorStore(Protocol):
    """Porta de um store pré-populado; o adaptador real faz I/O, o de teste é fake."""

    nome: str

    async def verificar(self) -> tuple[bool, Detalhe]: ...


class RelatorioSaude(BaseModel):
    status: str
    stores: dict[str, Detalhe]

    @property
    def degradado(self) -> bool:
        return self.status != "ok"


async def _verificar_um(v: VerificadorStore, timeout_s: float) -> Detalhe:
    """Falha de store vira detalhe legível — só o tipo do erro, nunca a mensagem/stack."""
    try:
        ok, detalhe = await asyncio.wait_for(v.verificar(), timeout=timeout_s)
    except Exception as exc:
        logger.warning("store %s indisponível", v.nome, exc_info=True)
        return {"ok": False, "erro": type(exc).__name__}
    return {"ok": ok, **detalhe}


async def verificar_saude(
    verificadores: Sequence[VerificadorStore], timeout_s: float = 3.0
) -> RelatorioSaude:
    resultados = await asyncio.gather(*(_verificar_um(v, timeout_s) for v in verificadores))
    stores = {v.nome: r for v, r in zip(verificadores, resultados, strict=True)}
    status = "ok" if all(r["ok"] for r in stores.values()) else "degradado"
    return RelatorioSaude(status=status, stores=stores)
