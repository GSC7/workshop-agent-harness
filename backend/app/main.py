from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import urllib3
from fastapi import FastAPI
from minio import Minio
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent.grafo import ConfigAnalise, Dependencias, construir_grafo
from agent.llm import PlanejadorLLM, RedatorLLM, criar_modelo
from agent.tools.search import EmbedderOpenAI
from app.config import settings
from app.routers import chat, health
from app.services.chat import ServicoChat
from app.services.stores import (
    VerificadorMinio,
    VerificadorPostgres,
    VerificadorQdrant,
    garantir_bucket,
)
from harness.repo import RepositorioRunsPostgres

logger = logging.getLogger(__name__)


def _servico_chat(
    engine_ro: AsyncEngine,
    engine_harness: AsyncEngine | None,
    qdrant: AsyncQdrantClient,
    embedder: EmbedderOpenAI | None,
    minio: Minio,
) -> ServicoChat | None:
    """Sem LLM ou sem persistência de runs (contrato), o /chat fica indisponível (503)."""
    chave = (
        settings.anthropic_api_key
        if settings.llm_provider == "anthropic"
        else settings.openai_api_key
    )
    forte, rapido = settings.llm_model_forte, settings.llm_model_rapido
    faltando = [
        nome
        for nome, valor in (
            (f"chave do provedor {settings.llm_provider}", chave),
            ("LLM_MODEL_FORTE", forte),
            ("LLM_MODEL_RAPIDO", rapido),
            ("DATABASE_URL_HARNESS", engine_harness),
        )
        if valor is None
    ]
    if chave is None or forte is None or rapido is None or engine_harness is None:
        logger.warning("chat desabilitado; falta: %s", ", ".join(faltando))
        return None
    provedor = settings.llm_provider
    deps = Dependencias(
        engine=engine_ro,
        qdrant=qdrant,
        embedder=embedder,
        planejador=PlanejadorLLM(
            criar_modelo(provedor, rapido, chave, 1024, 30, settings.llm_esforco_rapido)
        ),
        redator=RedatorLLM(
            criar_modelo(provedor, forte, chave, 8000, 120, settings.llm_esforco_forte)
        ),
        config=ConfigAnalise(
            meses_janela=settings.kpi_meses_janela,
            meses_recentes=settings.kpi_meses_recentes,
            meses_serie=settings.kpi_meses_serie,
            limiar_fraco=settings.kpi_limiar_fraco,
            limiar_yoy=settings.kpi_limiar_yoy,
            max_fracos=settings.kpi_max_fracos,
            sql_teto_linhas=settings.sql_teto_linhas,
            sql_timeout_ms=settings.sql_timeout_kpi_ms,
            search_limite=settings.search_limite,
        ),
    )
    return ServicoChat(
        grafo=construir_grafo(deps),
        repo=RepositorioRunsPostgres(engine_harness),
        objetos=minio,
        bucket=settings.minio_bucket_relatorios,
        modelos={"provedor": provedor, "forte": forte, "rapido": rapido},
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
    engine_harness = (
        create_async_engine(settings.database_url_harness.get_secret_value(), pool_pre_ping=True)
        if settings.database_url_harness is not None
        else None
    )
    app.state.servico_chat = _servico_chat(
        engine, engine_harness, qdrant, app.state.embedder, minio
    )
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
        if engine_harness is not None:
            await engine_harness.dispose()
        await engine.dispose()


app = FastAPI(title="Agente Analítico de Vendas", lifespan=lifespan)
app.include_router(health.router)
app.include_router(chat.router)
