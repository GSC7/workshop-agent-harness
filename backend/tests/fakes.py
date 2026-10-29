"""Fakes dos stores pré-populados: testes rodam offline, sem Postgres/Qdrant/MinIO reais."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

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
