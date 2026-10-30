"""Dimensão composta (#9b): categoria × canal ou categoria × região, detectada em código.

Os valores vêm do catálogo do próprio banco (nomes e ids); nada que o LLM escreve entra no SQL.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from agent.grounding import normalizar

EixoCatalogo = Literal["categoria", "canal", "regiao"]


@dataclass(frozen=True)
class ItemCatalogo:
    eixo: EixoCatalogo
    id: int
    nome: str


@dataclass(frozen=True)
class Composto:
    eixo_b: Literal["canal", "regiao"]
    categoria: ItemCatalogo
    outro: ItemCatalogo

    @property
    def rotulo(self) -> str:
        return f"{self.categoria.nome} × {self.outro.nome}"

    @property
    def filtro(self) -> dict[str, str]:
        """Campos do payload do Qdrant (categoria vive em `produto`, ADR 0005)."""
        return {"produto": self.categoria.nome, self.eixo_b: self.outro.nome}


def _variantes(nome: str) -> set[str]:
    # "Casa & Decoração" também casa "casa e decoração" e "casa" (palavra inteira).
    base = normalizar(nome)
    return {base, base.replace("&", "e"), base.split("&")[0].strip()} - {""}


def _citados(texto: str, itens: Sequence[ItemCatalogo]) -> list[ItemCatalogo]:
    return [
        i
        for i in itens
        if any(re.search(rf"\b{re.escape(v)}\b", texto) for v in _variantes(i.nome))
    ]


def detectar_composto(pergunta: str, catalogo: Sequence[ItemCatalogo]) -> Composto | None:
    """Uma categoria + (um canal OU uma região) citados; qualquer ambiguidade = None."""
    texto = normalizar(pergunta)
    categorias = _citados(texto, [i for i in catalogo if i.eixo == "categoria"])
    canais = _citados(texto, [i for i in catalogo if i.eixo == "canal"])
    regioes = _citados(texto, [i for i in catalogo if i.eixo == "regiao"])
    if len(categorias) != 1 or len(canais) + len(regioes) != 1:
        return None
    if canais:
        return Composto("canal", categorias[0], canais[0])
    return Composto("regiao", categorias[0], regioes[0])
