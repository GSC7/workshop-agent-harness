from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.dependencies import get_servico_chat
from app.services.chat import ServicoChat

router = APIRouter()


class PedidoChat(BaseModel):
    pergunta: str = Field(min_length=1, max_length=2000)
    # Turnos com o mesmo id compartilham estado; ausente = conversa nova (id volta no "inicio").
    conversa_id: UUID | None = None

    @field_validator("pergunta")
    @classmethod
    def _sem_espacos(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("pergunta vazia")
        return v.strip()


@router.post("/chat")
async def chat(
    pedido: PedidoChat,
    servico: Annotated[ServicoChat | None, Depends(get_servico_chat)],
) -> StreamingResponse:
    if servico is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "chat indisponível no momento")
    return StreamingResponse(
        servico.executar(pedido.pergunta, pedido.conversa_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
