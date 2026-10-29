from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID


@dataclass
class RegistroRun:
    """Um run do agente como gravado no schema harness (contrato de auditoria)."""

    id: UUID
    pergunta: str
    status: Literal["ok", "erro", "cancelado"]  # cancelado = cliente desconectou no SSE
    duracao_ms: int
    # Estruturas serializadas em JSON (plano, KPIs fracos, fontes): forma varia por run.
    plano: dict[str, Any] | None = None
    fracos: list[dict[str, Any]] = field(default_factory=list)
    sqls: list[str] = field(default_factory=list)
    fontes: list[dict[str, Any]] = field(default_factory=list)
    relatorio: str | None = None
    relatorio_uri: str | None = None
    erro: str | None = None
    modelos: dict[str, str] = field(default_factory=dict)
