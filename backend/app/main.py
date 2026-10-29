from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import urllib3
from fastapi import FastAPI
from minio import Minio
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import create_async_engine

from agent.tools.search import EmbedderOpenAI
from app.config import settings
from app.routers import health
from app.services.stores import (
    VerificadorMinio,
    VerificadorPostgres,
    VerificadorQdrant,
    garantir_bucket,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Cria os clientes dos stores uma vez; config ausente falha cedo e com mensagem clara."""
    if settings.database_url_ro is None:
        raise RuntimeError("DATABASE_URL_RO não definida (papel agente_ro)")
    if settings.minio_root_user is None or settings.minio_root_password is None:
        raise RuntimeError("credenciais do MinIO não definidas (MINIO_ROOT_USER/PASSWORD)")

    engine = create_async_engine(settings.database_url_ro.get_secret_value(), pool_pre_ping=True)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url)
    minio = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_root_user,
        secret_key=settings.minio_root_password.get_secret_value(),
        secure=settings.minio_secure,
        # Default do SDK re-tenta por longos segundos: com MinIO fora, travaria startup e health.
        http_client=urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=2, read=5), retries=urllib3.Retry(total=1)
        ),
    )
    await garantir_bucket(minio, settings.minio_bucket_relatorios)

    app.state.engine_ro = engine  # única conexão do agente ao negocio (run_sql)
    app.state.qdrant = qdrant
    # Sem chave, a busca fica indisponível, mas o app (e o /health) sobe.
    openai = (
        AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        if settings.openai_api_key is not None
        else None
    )
    app.state.embedder = EmbedderOpenAI(openai, settings.embed_model) if openai else None
    app.state.verificadores = [
        VerificadorPostgres(engine),
        VerificadorQdrant(qdrant, settings.colecoes_qdrant),
        VerificadorMinio(minio, settings.minio_bucket_relatorios),
    ]
    try:
        yield
    finally:
        if openai is not None:
            await openai.close()
        await qdrant.close()
        await engine.dispose()


app = FastAPI(title="Agente Analítico de Vendas", lifespan=lifespan)
app.include_router(health.router)
