"""LLM-judge com rubrica explícita: o LLM julga critérios; a NOTA é calculada em código."""

from __future__ import annotations

from typing import Protocol, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from evals.golden import Caso


class Criterio(BaseModel):
    criterio: str
    atendido: bool


class Avaliacao(BaseModel):
    """Resultado auditável: cada critério do golden com a marcação do judge."""

    criterios: list[Criterio]
    inventou_informacao: bool
    justificativa: str


class Julgamento(BaseModel):
    """O que o LLM devolve: só a marcação dos critérios dados, na mesma ordem."""

    atendidos: list[bool] = Field(description="Um valor por critério, na ordem dada.")
    inventou_informacao: bool = Field(
        description="true só se o relatório afirma causa ou ação sem base na resposta esperada "
        "(números do banco, premissas, avisos e acompanhamento NÃO contam)."
    )
    justificativa: str


class ErroJudge(RuntimeError):
    """Julgamento inválido (critérios ausentes ou contagem diferente): o caso não é aprovado."""


RUBRICA = """Você avalia um relatório de um agente analítico de vendas contra a RESPOSTA ESPERADA \
e uma lista NUMERADA de critérios de um golden dataset. Responda em português do Brasil.

- Para cada critério, na mesma ordem, diga se o relatório o cumpre de fato (paráfrase vale; \
omissão ou contradição não). Não crie critérios novos nem pule algum.
- Marque `inventou_informacao` só se o relatório afirma CAUSA ou AÇÃO sem base na resposta \
esperada. Números do diagnóstico vêm do banco (você não os vê) e NÃO contam; premissas, avisos e \
seções de acompanhamento também não.
- Justifique em 2-3 frases.

Não dê nota: o sistema calcula a nota a partir dos critérios."""


def nota_da_avaliacao(avaliacao: Avaliacao) -> float:
    """Fração de critérios atendidos; invenção zera (grounding é a regra de ouro)."""
    if avaliacao.inventou_informacao or not avaliacao.criterios:
        return 0.0
    return sum(c.atendido for c in avaliacao.criterios) / len(avaliacao.criterios)


class Judge(Protocol):
    async def avaliar(self, caso: Caso, relatorio: str) -> Avaliacao: ...


class JudgeLLM:
    def __init__(self, modelo: BaseChatModel) -> None:
        self._llm = modelo.with_structured_output(Julgamento, method="json_schema")

    async def avaliar(self, caso: Caso, relatorio: str) -> Avaliacao:
        if not caso.criterios:
            raise ErroJudge(f"caso {caso.id} sem critérios no golden")
        lista = "\n".join(f"{i}. {c}" for i, c in enumerate(caso.criterios, 1))
        mensagens = [
            SystemMessage(RUBRICA),
            HumanMessage(
                f"PERGUNTA:\n{caso.pergunta}\n\nRESPOSTA ESPERADA:\n{caso.recomendacao_esperada}"
                f"\n\nCRITÉRIOS:\n{lista}\n\nRELATÓRIO DO AGENTE:\n{relatorio}"
            ),
        ]
        julgamento = cast(Julgamento, await self._llm.ainvoke(mensagens))
        if len(julgamento.atendidos) != len(caso.criterios):
            raise ErroJudge(
                f"judge devolveu {len(julgamento.atendidos)} de {len(caso.criterios)} critérios"
            )
        # Casamento por posição em código: o texto do critério é o do golden, não o do LLM.
        return Avaliacao(
            criterios=[
                Criterio(criterio=c, atendido=a)
                for c, a in zip(caso.criterios, julgamento.atendidos, strict=True)
            ],
            inventou_informacao=julgamento.inventou_informacao,
            justificativa=julgamento.justificativa,
        )
