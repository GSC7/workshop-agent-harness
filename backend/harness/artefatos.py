from __future__ import annotations

import asyncio
import io
from typing import Protocol
from uuid import UUID


class ClienteObjetos(Protocol):
    """Superfície do SDK do MinIO usada aqui (facilita fake em teste)."""

    def put_object(
        self, bucket_name: str, object_name: str, data: io.BytesIO, length: int, content_type: str
    ) -> object: ...


async def salvar_relatorio(cliente: ClienteObjetos, bucket: str, run_id: UUID, texto: str) -> str:
    """Relatório em Markdown no MinIO; devolve a URI gravada no run."""
    dados = texto.encode("utf-8")
    nome = f"{run_id}.md"
    await asyncio.to_thread(
        cliente.put_object, bucket, nome, io.BytesIO(dados), len(dados), "text/markdown"
    )
    return f"minio://{bucket}/{nome}"
