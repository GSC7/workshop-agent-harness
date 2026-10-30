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
    # Respostas por trecho do SQL (ex.: catálogo vs KPI composto); senão, colunas/linhas.
    por_trecho: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = field(default_factory=dict)
    atraso: float = 0.0  # segundos por instrução: runs concorrentes se intercalam

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
        if self.atraso:
            await asyncio.sleep(self.atraso)
        if not sql.startswith("SET") and self.erro is not None:
            raise self.erro
        for trecho, (colunas, linhas) in self.por_trecho.items():
            if trecho in sql:
                return _ResultadoFake(colunas, linhas)
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
    por_colecao: dict[str, list[dict[str, Any]]] | None = None  # docs distintos por coleção

    async def query_points(self, **kwargs: Any) -> _RespostaFake:
        self.chamadas.append(kwargs)
        if self.erro is not None:
            raise self.erro
        payloads = (
            self.por_colecao.get(kwargs["collection_name"], [])
            if self.por_colecao is not None
            else self.payloads
        )
        pontos = [_PontoFake(0.9 - i / 10, p) for i, p in enumerate(payloads)]
        return _RespostaFake(pontos[: kwargs["limit"]])


@dataclass
class PlanejadorFake:
    plano: Any = None  # agent.state.Plano
    erro: Exception | None = None
    sequencia: list[Any] = field(default_factory=list)  # um plano por turno, em ordem

    async def planejar(self, pergunta: str) -> Any:
        if self.erro is not None:
            raise self.erro
        return self.sequencia.pop(0) if self.sequencia else self.plano


@dataclass
class RedatorFake:
    """Relatório em pedaços (simula streaming); guarda o contexto recebido."""

    partes: list[str] = field(default_factory=lambda: ["## Relatório\n", "Recompra no Sul."])
    contextos: list[str] = field(default_factory=list)

    async def redigir(self, pergunta: str, contexto: str) -> AsyncIterator[str]:
        self.contextos.append(contexto)
        for p in self.partes:
            yield p


def linhas_kpi(dimensoes: dict[str, float], meses: int = 6, meta: float = 1.0) -> list[tuple]:
    """Linhas (dimensao, mes, realizado, meta) com gap constante por dimensão."""
    return [
        (dim, f"2026-{m:02d}", meta * (1 + gap), meta)
        for dim, gap in dimensoes.items()
        for m in range(7 - meses, 7)
    ]


@dataclass
class RepoRunsFake:
    runs: list[Any] = field(default_factory=list)  # harness.modelo.RegistroRun

    async def gravar(self, run: Any) -> None:
        self.runs.append(run)

    async def turno_anterior(self, conversa_id: Any) -> Any:
        """Mesma regra do repositório real: só runs ok desta conversa."""
        from agent.state import Plano, TurnoAnterior

        da_conversa = [r for r in self.runs if r.conversa_id == conversa_id and r.status == "ok"]
        if not da_conversa:
            return None
        return TurnoAnterior(
            plano=Plano.model_validate(da_conversa[-1].plano),
            recomendacoes=[rec for r in da_conversa for rec in r.recomendacoes],
        )


@dataclass
class ObjetosFake:
    objetos: dict[str, bytes] = field(default_factory=dict)

    def put_object(
        self, bucket_name: str, object_name: str, data: Any, length: int, content_type: str
    ) -> None:
        self.objetos[f"{bucket_name}/{object_name}"] = data.read()


def linhas_historico(dimensoes: dict[str, dict[int, float]], meta_2026: float = 1.0) -> list[tuple]:
    """Jan-jun de cada ano com o realizado dado; meta = realizado nos anos anteriores."""
    return [
        (dim, f"{ano}-{m:02d}", valor, meta_2026 if ano == 2026 else valor)
        for dim, anos in dimensoes.items()
        for ano, valor in anos.items()
        for m in range(1, 7)
    ]


@dataclass
class RecomendadorFake:
    """Devolve as recomendações configuradas (inclusive inválidas, p/ testar o guardrail)."""

    recomendacoes: list[Any] = field(default_factory=list)  # agent.grounding.Recomendacao
    erro: Exception | None = None
    contextos: list[str] = field(default_factory=list)

    async def recomendar(self, contexto: str) -> list[Any]:
        self.contextos.append(contexto)
        if self.erro is not None:
            raise self.erro
        return self.recomendacoes


CATALOGO_SQL = (
    ["eixo", "id", "nome"],
    [
        ("canal", 1, "site_proprio"),
        ("canal", 2, "marketplace"),
        ("canal", 3, "loja_fisica"),
        ("categoria", 1, "Eletrônicos"),
        ("categoria", 4, "Beleza"),
        ("regiao", 2, "Nordeste"),
        ("regiao", 5, "Sul"),
    ],
)
