"""Golden dataset (seed/evals/golden): versionado fora do backend; aqui só é lido e validado."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

GOLDEN_PADRAO = (
    Path(__file__).resolve().parents[2] / "seed" / "evals" / "golden" / "narrativas.yaml"
)


class Caso(BaseModel):
    # Campo digitado errado (ex.: "pendnte") é erro, não silêncio.
    model_config = ConfigDict(extra="forbid")

    id: str
    pergunta: str
    dispara_enriquecimento: bool
    fontes_esperadas: list[str] = Field(default_factory=list)
    distratores: list[str] = Field(default_factory=list)
    recomendacao_esperada: str
    # Régua fixa do judge, aprovada pelo usuário (ADR 0011): o judge só marca atendido/não.
    criterios: list[str] = Field(default_factory=list)
    espera_clarificacao: bool = False
    # Falha conhecida com issue dona: relatada, fora do veredito até a issue entregar (ADR 0011).
    pendente: str | None = None
    narrativa: str | None = None
    intencao: str | None = None
    sql_esperado: str | None = None  # referência do golden; o agente usa SQL templatizado


def carregar_golden(caminho: Path = GOLDEN_PADRAO) -> list[Caso]:
    brutos = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    return [Caso.model_validate(b) for b in brutos]
