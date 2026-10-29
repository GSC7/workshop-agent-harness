from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from evals.golden import Caso
from evals.judge import Avaliacao, Criterio, ErroJudge, JudgeLLM, Julgamento, nota_da_avaliacao


def _av(atendidos, inventou=False):
    return Avaliacao(
        criterios=[Criterio(criterio=f"c{i}", atendido=a) for i, a in enumerate(atendidos)],
        inventou_informacao=inventou,
        justificativa="ok",
    )


def test_nota_e_a_fracao_de_criterios_atendidos():
    assert nota_da_avaliacao(_av([True, True, True, False])) == pytest.approx(0.75)


def test_invencao_zera_a_nota():
    assert nota_da_avaliacao(_av([True, True, True], inventou=True)) == 0.0


def test_sem_criterios_nota_zero():
    assert nota_da_avaliacao(_av([])) == 0.0


@dataclass
class _ModeloFake:
    """Imita BaseChatModel.with_structured_output(...).ainvoke(...)."""

    resposta: Julgamento
    mensagens: list[Any] = field(default_factory=list)

    def with_structured_output(self, schema: Any, method: str) -> _ModeloFake:
        assert schema is Julgamento and method == "json_schema"
        return self

    async def ainvoke(self, mensagens: list[Any]) -> Julgamento:
        self.mensagens = mensagens
        return self.resposta


CASO = Caso(
    id="n1",
    pergunta="Recompra no Sul?",
    dispara_enriquecimento=True,
    fontes_esperadas=["f"],
    recomendacao_esperada="Reativar frete grátis; evitar brinde.",
    criterios=["Reativa ou cobra o frete grátis", "Recomenda evitar o brinde genérico"],
)


async def test_judge_marca_os_criterios_do_golden_na_ordem_e_a_nota_sai_do_codigo():
    modelo = _ModeloFake(
        resposta=Julgamento(atendidos=[True, False], inventou_informacao=False, justificativa="j")
    )

    avaliacao = await JudgeLLM(modelo).avaliar(CASO, "## Premissas ... frete grátis ...")  # type: ignore[arg-type]

    assert [(c.criterio, c.atendido) for c in avaliacao.criterios] == [
        ("Reativa ou cobra o frete grátis", True),
        ("Recomenda evitar o brinde genérico", False),
    ]
    assert nota_da_avaliacao(avaliacao) == pytest.approx(0.5)
    sistema, usuario = (m.content for m in modelo.mensagens)
    assert "não crie critérios" in sistema.lower() and "invent" in sistema.lower()
    assert "1. Reativa ou cobra o frete grátis" in usuario and "frete grátis ..." in usuario


async def test_resposta_do_judge_com_contagem_diferente_e_rejeitada():
    modelo = _ModeloFake(
        resposta=Julgamento(atendidos=[True], inventou_informacao=False, justificativa="j")
    )

    with pytest.raises(ErroJudge, match="2 critérios"):
        await JudgeLLM(modelo).avaliar(CASO, "relatório")  # type: ignore[arg-type]


async def test_caso_sem_criterios_no_golden_nao_e_julgado():
    sem = CASO.model_copy(update={"criterios": []})

    vazio = Julgamento(atendidos=[], inventou_informacao=False, justificativa="")
    judge = JudgeLLM(_ModeloFake(resposta=vazio))  # type: ignore[arg-type]

    with pytest.raises(ErroJudge, match="sem critérios"):
        await judge.avaliar(sem, "x")
