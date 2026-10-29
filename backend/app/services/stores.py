from __future__ import annotations

import asyncio
import logging

from minio import Minio
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.health import Detalhe

logger = logging.getLogger(__name__)


class VerificadorPostgres:
    """Conecta como `agente_ro` e exige transação somente-leitura — guardrail do schema negocio."""

    nome = "postgres"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def verificar(self) -> tuple[bool, Detalhe]:
        async with self._engine.connect() as conn:
            linha = (
                await conn.execute(
                    text("SELECT current_user, current_setting('transaction_read_only')")
                )
            ).one()
        usuario, somente_leitura = linha[0], linha[1] == "on"
        ok = usuario == "agente_ro" and somente_leitura
        return ok, {"usuario": usuario, "somente_leitura": somente_leitura}


class VerificadorQdrant:
    """As coleções chegam pré-indexadas; faltar uma é store degradado, não algo a recriar."""

    nome = "qdrant"

    def __init__(self, cliente: AsyncQdrantClient, esperadas: tuple[str, ...]) -> None:
        self._cliente = cliente
        self._esperadas = esperadas

    async def verificar(self) -> tuple[bool, Detalhe]:
        resp = await self._cliente.get_collections()
        presentes = {c.name for c in resp.collections}
        faltando = sorted(set(self._esperadas) - presentes)
        return not faltando, {"colecoes_faltando": faltando}


class VerificadorMinio:
    nome = "minio"

    def __init__(self, cliente: Minio, bucket: str) -> None:
        self._cliente = cliente
        self._bucket = bucket

    async def verificar(self) -> tuple[bool, Detalhe]:
        # SDK do MinIO é síncrono: tira do event loop.
        existe = await asyncio.to_thread(self._cliente.bucket_exists, self._bucket)
        return existe, {"bucket": self._bucket}


async def garantir_bucket(cliente: Minio, bucket: str) -> None:
    """Provisiona o bucket de relatórios (idempotente); MinIO fora não derruba o startup."""
    try:
        if not await asyncio.to_thread(cliente.bucket_exists, bucket):
            await asyncio.to_thread(cliente.make_bucket, bucket)
            logger.info("bucket %s criado", bucket)
    except Exception:
        logger.warning("não foi possível garantir o bucket %s", bucket, exc_info=True)
