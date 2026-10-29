"""Fakes dos stores pré-populados: testes rodam offline, sem Postgres/Qdrant/MinIO reais."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.exc import DBAPIError

from app.services.health import Detalhe


@dataclass
class VerificadorFake:
    """Store fake: devolve um detalhe fixo ou levanta a exceção configurada."""

    nome: str
    ok: bool = True
    detalhe: Detalhe = field(default_factory=dict)
    erro: Exception | None = None

    async def verificar(self) -> tuple[bool, Detalhe]:
        if self.erro is not None:
            raise self.erro
        return self.ok, self.detalhe


@dataclass
class VerificadorTravado:
    """Store que nunca responde — exercita o timeout do /health."""

    nome: str = "minio"

    async def verificar(self) -> tuple[bool, Detalhe]:
        await asyncio.sleep(10)
        return True, {}


@dataclass
class MinioFake:
    """Só a superfície do SDK do MinIO usada no startup."""

    buckets: set[str] = field(default_factory=set)
    erro: Exception | None = None
    criados: list[str] = field(default_factory=list)

    def bucket_exists(self, bucket: str) -> bool:
        if self.erro is not None:
            raise self.erro
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.criados.append(bucket)
        self.buckets.add(bucket)


class _ErroPg(Exception):
    """Imita o erro do driver asyncpg adaptado pelo SQLAlchemy (expõe `sqlstate`)."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"erro pg {sqlstate} senha=segredo")
        self.sqlstate = sqlstate


def erro_pg(sqlstate: str) -> DBAPIError:
    return DBAPIError("SELECT segredo FROM pedidos", None, _ErroPg(sqlstate))


@dataclass
class _ResultadoFake:
    colunas: list[str]
    linhas: list[tuple[Any, ...]]

    def keys(self) -> list[str]:
        return self.colunas

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.linhas


@dataclass
class EngineFake:
    """Registra, em ordem, o que seria enviado ao Postgres; a consulta falha se `erro` setado."""

    colunas: list[str] = field(default_factory=list)
    linhas: list[tuple[Any, ...]] = field(default_factory=list)
    erro: Exception | None = None
    executados: list[str] = field(default_factory=list)
    conexoes: int = 0
    transacao_aberta: bool = False

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[EngineFake]:
        self.conexoes += 1
        yield self

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        self.transacao_aberta = True
        try:
            yield
        finally:
            self.transacao_aberta = False

    async def exec_driver_sql(self, sql: str) -> _ResultadoFake:
        assert self.transacao_aberta, "instrução fora de transação"
        self.executados.append(sql)
        if not sql.startswith("SET") and self.erro is not None:
            raise self.erro
        return _ResultadoFake(self.colunas, self.linhas)


@dataclass
class EmbedderFake:
    """Embedding determinístico e offline; registra o que foi embeddado."""

    dimensao: int = 3072
    textos: list[str] = field(default_factory=list)

    async def embeddar(self, texto: str) -> list[float]:
        self.textos.append(texto)
        return [0.1] * self.dimensao


@dataclass
class _PontoFake:
    score: float
    payload: dict[str, Any]


@dataclass
class _RespostaFake:
    points: list[_PontoFake]


@dataclass
class QdrantFake:
    """Só `query_points`; guarda a última chamada para inspecionar filtro/coleção."""

    payloads: list[dict[str, Any]] = field(default_factory=list)
    chamadas: list[dict[str, Any]] = field(default_factory=list)
    erro: Exception | None = None

    async def query_points(self, **kwargs: Any) -> _RespostaFake:
        self.chamadas.append(kwargs)
        if self.erro is not None:
            raise self.erro
        pontos = [_PontoFake(0.9 - i / 10, p) for i, p in enumerate(self.payloads)]
        return _RespostaFake(pontos[: kwargs["limit"]])
