from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal, Protocol, get_args

from langchain_core.tools import BaseTool, tool
from openai import AsyncOpenAI, OpenAIError
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.config import settings

logger = logging.getLogger(__name__)

Colecao = Literal["camada_semantica", "diagnostico", "prescricao"]
COLECOES: tuple[str, ...] = get_args(Colecao)

_PERIODO = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class BuscaRejeitada(ValueError):
    """Busca sem os filtros obrigatórios da coleção; mensagem legível para o nó/LLM."""


@dataclass(frozen=True)
class Dimensao:
    """Recorte do negócio; campos preenchidos casam em AND exato (ADR 0005)."""

    regiao: str | None = None
    produto: str | None = None
    canal: str | None = None

    def preenchidos(self) -> dict[str, str]:
        return {f.name: v for f in fields(self) if (v := _valor(getattr(self, f.name)))}


def _valor(v: str | None) -> str | None:
    """String vazia/só espaços é ausência — senão passaria na obrigatoriedade e zeraria a busca."""
    return (v.strip() or None) if v is not None else None


def montar_filtro(
    colecao: Colecao,
    kpi_alvo: str | None = None,
    dimensao: Dimensao | None = None,
    periodos: Sequence[str] | None = None,
) -> models.Filter:
    """Filtro de payload por coleção (ADR 0005); campos fixos, nunca chave livre do LLM.

    - prescricao: kpi_alvo + dimensão obrigatórios.
    - diagnostico: dimensão obrigatória; o dado não tem kpi_alvo (filtrar zeraria a busca).
    - camada_semantica: catálogo do schema; kpi_alvo opcional, sem dimensão.
    `periodos` é recorte sazonal por periodo_referencia — nunca data_ingestao.
    """
    if colecao not in COLECOES:
        raise BuscaRejeitada(f"coleção desconhecida: {colecao!r} (use uma de {COLECOES})")
    dims = (dimensao or Dimensao()).preenchidos()
    kpi_alvo = _valor(kpi_alvo)

    condicoes: dict[str, str] = {}
    if colecao == "camada_semantica":
        if kpi_alvo is not None:
            condicoes["kpi_alvo"] = kpi_alvo
    else:
        if not dims:
            raise BuscaRejeitada(f"{colecao} exige dimensão (regiao, produto e/ou canal)")
        if colecao == "prescricao":
            if kpi_alvo is None:
                raise BuscaRejeitada("prescricao exige kpi_alvo")
            condicoes["kpi_alvo"] = kpi_alvo
        condicoes |= dims

    must: list[models.Condition] = [
        models.FieldCondition(key=k, match=models.MatchValue(value=v)) for k, v in condicoes.items()
    ]
    if periodos:
        invalidos = [p for p in periodos if not _PERIODO.match(p)]
        if invalidos:
            raise BuscaRejeitada(f"periodo_referencia deve ser AAAA-MM: {invalidos}")
        must.append(
            models.FieldCondition(
                key="periodo_referencia", match=models.MatchAny(any=list(periodos))
            )
        )
    return models.Filter(must=must)


class ErroBusca(RuntimeError):
    """Falha de infraestrutura (embedding/Qdrant), traduzida sem detalhe do provedor."""


class Embedder(Protocol):
    async def embeddar(self, texto: str) -> list[float]: ...


@dataclass(frozen=True)
class Hit:
    texto: str
    score: float
    fonte: str  # URI rastreável: é o que a recomendação cita (grounding)
    # Payload restante (periodo_referencia, resultado, subtipo...): heterogêneo por coleção.
    metadados: dict[str, Any]


def _para_hit(score: float, payload: dict[str, Any]) -> Hit | None:
    fonte = payload.get("fonte")
    if not fonte:
        logger.warning("hit sem fonte descartado (não pode ser citado)")
        return None
    meta = {k: v for k, v in payload.items() if k not in ("document", "fonte")}
    return Hit(texto=str(payload.get("document", "")), score=score, fonte=fonte, metadados=meta)


async def buscar(
    cliente: AsyncQdrantClient,
    embedder: Embedder,
    colecao: Colecao,
    query: str,
    kpi_alvo: str | None = None,
    dimensao: Dimensao | None = None,
    periodos: Sequence[str] | None = None,
    limite: int = 5,
) -> list[Hit]:
    """Busca semântica sempre filtrada; o filtro é validado antes de gastar embedding."""
    filtro = montar_filtro(colecao, kpi_alvo=kpi_alvo, dimensao=dimensao, periodos=periodos)
    vetor = await embedder.embeddar(query)
    if len(vetor) != settings.embed_dim:
        raise ErroBusca(
            f"embedding com {len(vetor)} dimensões; as coleções usam {settings.embed_dim}"
        )
    try:
        resp = await cliente.query_points(
            collection_name=colecao,
            query=vetor,
            query_filter=filtro,
            limit=limite,
            with_payload=True,
        )
    except (UnexpectedResponse, ResponseHandlingException, OSError) as exc:
        logger.warning("search: Qdrant falhou (%s)", type(exc).__name__)
        raise ErroBusca("busca semântica indisponível no momento") from None
    hits = (_para_hit(p.score, p.payload or {}) for p in resp.points)
    return [h for h in hits if h is not None]


class EmbedderOpenAI:
    """Embedding da query com o mesmo modelo usado na indexação (text-embedding-3-large)."""

    def __init__(self, cliente: AsyncOpenAI, modelo: str) -> None:
        self._cliente = cliente
        self._modelo = modelo

    async def embeddar(self, texto: str) -> list[float]:
        try:
            resp = await self._cliente.embeddings.create(model=self._modelo, input=texto)
        except OpenAIError as exc:
            logger.warning("search: embedding falhou (%s)", type(exc).__name__)
            raise ErroBusca("embedding da consulta indisponível no momento") from None
        return list(resp.data[0].embedding)


def criar_tool_search(cliente: AsyncQdrantClient, embedder: Embedder) -> BaseTool:
    """Tool única parametrizada pela coleção; o nó do grafo escolhe a coleção."""

    @tool
    async def search(
        colecao: Colecao,
        query: str,
        kpi_alvo: str | None = None,
        regiao: str | None = None,
        produto: str | None = None,
        canal: str | None = None,
        periodos: list[str] | None = None,
    ) -> dict[str, Any]:  # JSON p/ o LLM: {"hits": [...]} ou {"erro": str}
        """Busca semântica filtrada no corpus qualitativo já indexado.

        - diagnostico: o *porquê* (tickets, NPS, atas). Exige ao menos uma dimensão.
        - prescricao: o que já foi tentado e o resultado. Exige kpi_alvo + dimensão.
        - camada_semantica: definições de KPI e exemplos de SQL do schema negocio.
        A dimensão casa exatamente (regiao, produto e canal em AND). Cada hit traz a `fonte`
        rastreável, que é o que uma recomendação deve citar.

        Args:
            colecao: camada_semantica, diagnostico ou prescricao.
            query: o que procurar, em linguagem natural.
            kpi_alvo: faturamento, ticket_medio, taxa_recompra ou taxa_conversao.
            regiao: ex.: Sul, Sudeste.
            produto: categoria, ex.: Eletrônicos, Moda.
            canal: ex.: site_proprio, marketplace, loja_fisica.
            periodos: recorte sazonal opcional, lista de AAAA-MM (ex.: ["2024-11", "2025-11"]).
        """
        try:
            hits = await buscar(
                cliente,
                embedder,
                colecao,
                query,
                kpi_alvo=kpi_alvo,
                dimensao=Dimensao(regiao=regiao, produto=produto, canal=canal),
                periodos=periodos,
                limite=settings.search_limite,
            )
        except (BuscaRejeitada, ErroBusca) as exc:
            return {"erro": str(exc)}
        return {"hits": [asdict(h) for h in hits]}

    return search
