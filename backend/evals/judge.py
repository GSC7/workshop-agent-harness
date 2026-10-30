"""LLM-judge com rubrica explícita: o LLM julga critérios; a NOTA é calculada em código."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from evals.golden import Caso

logger = logging.getLogger(__name__)


class Criterio(BaseModel):
    criterio: str
    atendido: bool


class Voto(BaseModel):
    """Um julgamento individual, guardado para auditar a consolidação (#27)."""

    atendidos: list[bool]
    inventou_informacao: bool
    nota: float
    justificativa: str = ""  # a do voto divergente é a mais útil para auditar


class Avaliacao(BaseModel):
    """Resultado auditável: cada critério do golden com a marcação do judge."""

    criterios: list[Criterio]
    inventou_informacao: bool
    justificativa: str
    # Maioria de k julgamentos (#27): os votos válidos e quantos falharam; vazio = voto único.
    votos: list[Voto] = Field(default_factory=list)
    votos_falhos: int = 0


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


class JudgeMaioria:
    """Julga k vezes o mesmo relatório e consolida por maioria (critério a critério).

    Uma leitura divergente do LLM não derruba nem aprova o caso sozinha. A nota continua
    calculada em código, sobre a avaliação consolidada. Voto que falha fica de fora; empate
    entre os válidos conta como NÃO atendido (conservador); sem voto válido, o caso não é julgado.
    """

    def __init__(self, judge: Judge, votos: int = 3) -> None:
        if votos < 1 or votos % 2 == 0:
            raise ValueError(f"votos do judge deve ser ímpar e positivo, recebido {votos}")
        self._judge = judge
        self._votos = votos

    async def avaliar(self, caso: Caso, relatorio: str) -> Avaliacao:
        resultados = await asyncio.gather(
            *(self._judge.avaliar(caso, relatorio) for _ in range(self._votos)),
            return_exceptions=True,
        )
        for r in resultados:
            if isinstance(r, BaseException) and not isinstance(r, Exception):
                raise r  # cancelamento não é voto falho
            if isinstance(r, Exception):
                logger.warning("voto do judge falhou no caso %s: %s", caso.id, type(r).__name__)
        # Molde = critérios do golden: voto com outra contagem é falho, não reinterpretado.
        validos = [
            r
            for r in resultados
            if isinstance(r, Avaliacao) and len(r.criterios) == len(caso.criterios)
        ]
        if not validos:
            raise ErroJudge(f"nenhum dos {self._votos} votos do judge foi válido")
        if self._votos == 1:
            return validos[0]

        def maioria(marcas: list[bool]) -> bool:
            return sum(marcas) * 2 > len(marcas)  # empate = False

        criterios = [
            Criterio(criterio=c, atendido=maioria([v.criterios[i].atendido for v in validos]))
            for i, c in enumerate(caso.criterios)
        ]
        inventou = maioria([v.inventou_informacao for v in validos])
        consolidado = [c.atendido for c in criterios]
        # A justificativa é de um voto que concorda com o resultado (senão, a do 1º válido).
        concorda = next(
            (
                v
                for v in validos
                if [c.atendido for c in v.criterios] == consolidado
                and v.inventou_informacao == inventou
            ),
            validos[0],
        )
        return Avaliacao(
            criterios=criterios,
            inventou_informacao=inventou,
            justificativa=concorda.justificativa,
            votos=[
                Voto(
                    atendidos=[c.atendido for c in v.criterios],
                    inventou_informacao=v.inventou_informacao,
                    nota=nota_da_avaliacao(v),
                    justificativa=v.justificativa,
                )
                for v in validos
            ],
            votos_falhos=len(resultados) - len(validos),
        )
