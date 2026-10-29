from __future__ import annotations

import json
from dataclasses import asdict
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.modelo import RegistroRun

_JSON = ("plano", "fracos", "sqls", "fontes", "modelos")

_INSERT = text(
    "INSERT INTO harness.runs (id, pergunta, status, plano, fracos, sqls, fontes, relatorio,"
    " relatorio_uri, erro, duracao_ms, modelos) VALUES (:id, :pergunta, :status,"
    " CAST(:plano AS jsonb), CAST(:fracos AS jsonb), CAST(:sqls AS jsonb),"
    " CAST(:fontes AS jsonb), :relatorio, :relatorio_uri, :erro, :duracao_ms,"
    " CAST(:modelos AS jsonb))"
)


class RepositorioRuns(Protocol):
    async def gravar(self, run: RegistroRun) -> None: ...


class RepositorioRunsPostgres:
    """Grava pelo papel harness_rw (só INSERT/SELECT em harness.runs)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def gravar(self, run: RegistroRun) -> None:
        valores = asdict(run)
        for campo in _JSON:
            valores[campo] = json.dumps(valores[campo], ensure_ascii=False, default=str)
        async with self._engine.begin() as conn:
            await conn.execute(_INSERT, valores)
