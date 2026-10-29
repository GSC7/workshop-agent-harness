from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)


def _executar(conn: Connection) -> None:
    context.configure(connection=conn, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def _online() -> None:
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL (admin) não definida no .env")
    engine = create_async_engine(settings.database_url.get_secret_value())
    # begin() e não connect(): no SQLAlchemy 2 o connect() abre transação implícita que o
    # Alembic não comita — a migration "roda" e é desfeita no fecho (aconteceu com a 0002).
    async with engine.begin() as conn:
        await conn.run_sync(_executar)
    await engine.dispose()


asyncio.run(_online())
