from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.config import settings
from app.dependencies import get_verificadores
from app.services.health import RelatorioSaude, VerificadorStore, verificar_saude

router = APIRouter()


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Só o processo, sem I/O: é o healthcheck do container (store fora não derruba o nginx)."""
    return {"status": "vivo"}


@router.get("/health")
async def health(
    response: Response,
    verificadores: Annotated[list[VerificadorStore], Depends(get_verificadores)],
) -> RelatorioSaude:
    relatorio = await verificar_saude(verificadores, timeout_s=settings.health_timeout_s)
    if relatorio.degradado:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return relatorio
