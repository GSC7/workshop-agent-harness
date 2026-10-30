"""Chamadas determinísticas (run_sql, search) como tools no rastro do run (#11).

O agente não conhece o Langfuse: emite os callbacks de tool do LangChain com a config do nó
corrente, e quem estiver ouvindo (CallbackHandler do Langfuse, fake de teste) registra a
chamada aninhada no nó. Sem callbacks configurados, não faz nada.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.callbacks import AsyncCallbackManager
from langchain_core.runnables.config import ensure_config

logger = logging.getLogger(__name__)


async def como_tool[T](
    nome: str,
    entrada: dict[str, Any],
    chamada: Awaitable[T],
    resumo: Callable[[T], Any],
) -> T:
    """Executa `chamada`; o rastro recebe `entrada` e `resumo(resultado)` (ou o erro)."""
    config = ensure_config()
    gerente = AsyncCallbackManager.configure(
        inheritable_callbacks=config.get("callbacks"),
        inheritable_tags=config.get("tags"),
        inheritable_metadata=config.get("metadata"),
    )
    texto = json.dumps(entrada, ensure_ascii=False, default=str)
    run = await gerente.on_tool_start({"name": nome}, texto, inputs=entrada)
    try:
        resultado = await chamada
    except BaseException as exc:
        # Cancelado (cliente caiu): o span pode ficar aberto; a cancelação segue propagando.
        await run.on_tool_error(exc)
        raise
    try:
        await run.on_tool_end(json.dumps(resumo(resultado), ensure_ascii=False, default=str))
    except Exception:
        # Rastro não é caminho crítico: resumo que falha não pode perder um resultado válido.
        logger.warning("rastro: falha ao registrar o fim da tool %s", nome)
    return resultado
