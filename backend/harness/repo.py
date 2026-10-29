from __future__ import annotations

import json
from dataclasses import asdict
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.state import Plano, TurnoAnterior
from harness.modelo import RegistroRun

_JSON = (
    "plano",
    "fracos",
    "sqls",
    "fontes",
    "modelos",
    "recomendacoes",
    "sazonais",
    "premissas",
    "avisos",
)

_INSERT = text(
    "INSERT INTO harness.runs (id, pergunta, status, plano, fracos, sqls, fontes, relatorio,"
    " relatorio_uri, erro, duracao_ms, modelos, conversa_id, recomendacoes, sazonais, premissas,"
    " avisos) VALUES (:id, :pergunta, :status,"
    " CAST(:plano AS jsonb), CAST(:fracos AS jsonb), CAST(:sqls AS jsonb),"
    " CAST(:fontes AS jsonb), :relatorio, :relatorio_uri, :erro, :duracao_ms,"
    " CAST(:modelos AS jsonb), :conversa_id, CAST(:recomendacoes AS jsonb),"
    " CAST(:sazonais AS jsonb), CAST(:premissas AS jsonb), CAST(:avisos AS jsonb))"
)

# Só runs ok DESTA conversa: o filtro por conversa_id é o que impede vazamento entre chats.
_DA_CONVERSA = text(
    "SELECT plano, recomendacoes FROM harness.runs"
    " WHERE conversa_id = :conversa_id AND status = 'ok' ORDER BY criado_em"
)


class RepositorioRuns(Protocol):
    async def gravar(self, run: RegistroRun) -> None: ...

    async def turno_anterior(self, conversa_id: UUID) -> TurnoAnterior | None: ...


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

    async def turno_anterior(self, conversa_id: UUID) -> TurnoAnterior | None:
        """Último plano da conversa + tudo que ela já recomendou (para não repetir)."""
        async with self._engine.connect() as conn:
            linhas = (await conn.execute(_DA_CONVERSA, {"conversa_id": conversa_id})).all()
        com_plano = [ln for ln in linhas if ln.plano]
        if not com_plano:
            return None
        return TurnoAnterior(
            plano=Plano.model_validate(com_plano[-1].plano),
            recomendacoes=[r for ln in linhas for r in (ln.recomendacoes or [])],
        )
