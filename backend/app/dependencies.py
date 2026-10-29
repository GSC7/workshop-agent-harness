from __future__ import annotations

from fastapi import Request

from app.services.health import VerificadorStore


def get_verificadores(request: Request) -> list[VerificadorStore]:
    """Verificadores dos stores criados no lifespan; testes substituem por fakes."""
    verificadores: list[VerificadorStore] = request.app.state.verificadores
    return verificadores
