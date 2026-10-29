from __future__ import annotations

from fastapi import Request

from app.services.chat import ServicoChat
from app.services.health import VerificadorStore


def get_verificadores(request: Request) -> list[VerificadorStore]:
    """Verificadores dos stores criados no lifespan; testes substituem por fakes."""
    verificadores: list[VerificadorStore] = request.app.state.verificadores
    return verificadores


def get_servico_chat(request: Request) -> ServicoChat | None:
    """None quando LLM ou persistência de runs não estão configurados (/chat responde 503)."""
    servico: ServicoChat | None = getattr(request.app.state, "servico_chat", None)
    return servico
