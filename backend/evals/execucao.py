"""Executa um caso no sistema real (POST /chat via nginx): avalia a pilha inteira, não só o grafo."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from evals.golden import Caso
from evals.scoring import ResultadoRun


async def executar_caso(
    cliente: httpx.AsyncClient, caso: Caso, prazo_s: float = 300
) -> ResultadoRun:
    """Prazo TOTAL por caso: o timeout do httpx é por operação e não corta um stream vivo."""
    try:
        async with asyncio.timeout(prazo_s):
            return await _executar(cliente, caso)
    except TimeoutError:
        return ResultadoRun(False, [], [], "", 0, [], erro=f"prazo de {prazo_s:.0f}s estourado")


async def _executar(cliente: httpx.AsyncClient, caso: Caso) -> ResultadoRun:
    etapas: list[str] = []
    relatorio: list[str] = []
    fontes: list[str] = []
    avisos: list[str] = []
    sqls = 0
    concluido = False
    erro: str | None = None
    async with cliente.stream("POST", "/chat", json={"pergunta": caso.pergunta}) as resposta:
        if resposta.status_code != 200:
            codigo = f"HTTP {resposta.status_code}"
            return ResultadoRun(False, [], [], "", 0, [codigo], erro=codigo)
        async for linha in resposta.aiter_lines():
            if not linha.startswith("data:"):
                continue
            # Eventos do contrato SSE do /chat: forma varia por `tipo`.
            evento: dict[str, Any] = json.loads(linha[5:].strip())
            tipo = evento.get("tipo")
            if tipo == "etapa":
                etapas.append(evento["nome"])
            elif tipo == "sql":
                sqls += 1
            elif tipo == "relatorio":
                relatorio.append(evento["texto"])
            elif tipo == "fontes_citadas":
                fontes = list(evento["fontes"])
            elif tipo == "aviso":
                avisos.append(evento["mensagem"])
            elif tipo == "fim":
                concluido = True
            elif tipo == "erro":
                erro = evento.get("mensagem")
    return ResultadoRun(concluido, etapas, fontes, "".join(relatorio), sqls, avisos, erro=erro)
