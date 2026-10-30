"""#27: maioria de k julgamentos por caso — consolidação testada com judge fake (offline)."""

from __future__ import annotations

import pytest

from evals.golden import Caso
from evals.judge import Avaliacao, Criterio, ErroJudge, JudgeMaioria, nota_da_avaliacao

CRITERIOS = ["a", "b", "c"]
CASO = Caso(
    id="n1",
    pergunta="p",
    dispara_enriquecimento=False,
    recomendacao_esperada="e",
    criterios=CRITERIOS,
)


class JudgeSequencia:
    """Um voto por chamada, na ordem dada; Exception na lista = aquele voto falha."""

    def __init__(self, votos: list[tuple[list[bool], bool] | BaseException]) -> None:
        self.votos = list(votos)
        self.chamadas = 0

    async def avaliar(self, caso: Caso, relatorio: str) -> Avaliacao:
        voto = self.votos[self.chamadas]
        self.chamadas += 1
        if isinstance(voto, BaseException):
            raise voto
        atendidos, inventou = voto
        return Avaliacao(
            criterios=[Criterio(criterio=c, atendido=a) for c, a in zip(CRITERIOS, atendidos)],
            inventou_informacao=inventou,
            justificativa=f"voto {self.chamadas}",
        )


async def test_maioria_por_criterio_e_a_nota_sai_da_avaliacao_consolidada():
    judge = JudgeSequencia(
        [
            ([True, True, False], False),
            ([True, False, True], False),
            ([True, True, True], False),
        ]
    )

    avaliacao = await JudgeMaioria(judge, votos=3).avaliar(CASO, "relatório")

    assert judge.chamadas == 3
    assert [c.atendido for c in avaliacao.criterios] == [True, True, True]  # 3/3, 2/3, 2/3
    assert [c.criterio for c in avaliacao.criterios] == CRITERIOS  # texto do golden
    assert nota_da_avaliacao(avaliacao) == 1.0
    assert [v.nota for v in avaliacao.votos] == pytest.approx([2 / 3, 2 / 3, 1.0])


async def test_um_voto_divergente_nao_derruba_o_caso():
    # O caso real da oscilação: 1 de 3 leituras marca um critério a menos.
    judge = JudgeSequencia(
        [([True, True, True], False), ([True, True, False], False), ([True, True, True], False)]
    )

    avaliacao = await JudgeMaioria(judge, votos=3).avaliar(CASO, "r")

    assert nota_da_avaliacao(avaliacao) == 1.0


async def test_invencao_tambem_e_por_maioria():
    um_so = JudgeSequencia([([True] * 3, True), ([True] * 3, False), ([True] * 3, False)])
    maioria = JudgeSequencia([([True] * 3, True), ([True] * 3, True), ([True] * 3, False)])

    assert nota_da_avaliacao(await JudgeMaioria(um_so, votos=3).avaliar(CASO, "r")) == 1.0
    assert nota_da_avaliacao(await JudgeMaioria(maioria, votos=3).avaliar(CASO, "r")) == 0.0


async def test_voto_que_falha_usa_os_validos_e_empate_e_nao_atendido():
    judge = JudgeSequencia(
        [([True, True, False], False), ErroJudge("contagem"), ([True, False, False], False)]
    )

    avaliacao = await JudgeMaioria(judge, votos=3).avaliar(CASO, "r")

    # a: 2/2 · b: 1/2 (empate → não atendido, conservador) · c: 0/2
    assert [c.atendido for c in avaliacao.criterios] == [True, False, False]
    assert len(avaliacao.votos) == 2 and avaliacao.votos_falhos == 1


async def test_sem_nenhum_voto_valido_o_caso_nao_e_julgado():
    judge = JudgeSequencia([ErroJudge("x"), TimeoutError(), ErroJudge("y")])

    with pytest.raises(ErroJudge, match="nenhum dos 3 votos"):
        await JudgeMaioria(judge, votos=3).avaliar(CASO, "r")


async def test_justificativa_e_de_um_voto_que_concorda_com_o_consolidado():
    judge = JudgeSequencia(
        [([False, False, False], False), ([True, True, True], False), ([True, True, True], False)]
    )

    avaliacao = await JudgeMaioria(judge, votos=3).avaliar(CASO, "r")

    assert avaliacao.justificativa == "voto 2"  # o 1º voto é o divergente


@pytest.mark.parametrize("votos", [0, 2, 4])
def test_numero_de_votos_e_impar_e_positivo(votos):
    with pytest.raises(ValueError):
        JudgeMaioria(JudgeSequencia([]), votos=votos)


async def test_um_voto_e_o_judge_de_sempre():
    judge = JudgeSequencia([([True, False, True], False)])

    avaliacao = await JudgeMaioria(judge, votos=1).avaliar(CASO, "r")

    assert judge.chamadas == 1 and nota_da_avaliacao(avaliacao) == pytest.approx(2 / 3)


def test_votos_do_judge_vem_do_settings(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("EVAL_JUDGE_VOTOS", raising=False)
    assert Settings(_env_file=None).eval_judge_votos == 3  # type: ignore[call-arg]


@pytest.mark.parametrize("valor", ["0", "2"])
def test_votos_par_ou_zero_e_recusado_no_settings(monkeypatch, valor):
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv("EVAL_JUDGE_VOTOS", valor)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_votos_par_e_recusado_na_cli():
    import argparse

    from evals.__main__ import _votos

    with pytest.raises(argparse.ArgumentTypeError):
        _votos("2")
    assert _votos("5") == 5


async def test_voto_com_contagem_diferente_conta_como_falho():
    class JudgeTorto:
        def __init__(self) -> None:
            self.n = 0

        async def avaliar(self, caso: Caso, relatorio: str) -> Avaliacao:
            self.n += 1
            qtd = 2 if self.n == 2 else 3  # o 2º voto devolve um critério a menos
            return Avaliacao(
                criterios=[Criterio(criterio=c, atendido=True) for c in CRITERIOS[:qtd]],
                inventou_informacao=False,
                justificativa="j",
            )

    avaliacao = await JudgeMaioria(JudgeTorto(), votos=3).avaliar(CASO, "r")

    assert avaliacao.votos_falhos == 1 and [c.criterio for c in avaliacao.criterios] == CRITERIOS


async def test_cancelamento_nao_vira_voto_falho():
    import asyncio

    judge = JudgeSequencia([([True] * 3, False), asyncio.CancelledError(), ([True] * 3, False)])

    with pytest.raises(asyncio.CancelledError):
        await JudgeMaioria(judge, votos=3).avaliar(CASO, "r")


async def test_voto_guarda_a_justificativa_para_auditar_divergencia():
    judge = JudgeSequencia([([True] * 3, False), ([False] * 3, False), ([True] * 3, False)])

    avaliacao = await JudgeMaioria(judge, votos=3).avaliar(CASO, "r")

    assert [v.justificativa for v in avaliacao.votos] == ["voto 1", "voto 2", "voto 3"]


async def test_markdown_mostra_a_nota_de_cada_voto():
    from evals.runner import para_json, para_markdown, rodar
    from evals.scoring import Limiares, ResultadoRun

    caso = CASO.model_copy(update={"dispara_enriquecimento": False})
    run = ResultadoRun(
        concluido=True, etapas=[], fontes_citadas=[], relatorio="## Premissas\n", sqls=1
    )
    judge = JudgeSequencia(
        [([True] * 3, False), ([True, True, False], False), ErroJudge("contagem")]
    )

    async def executar(_: Caso) -> ResultadoRun:
        return run

    rel = await rodar([caso], executar, JudgeMaioria(judge, votos=3), Limiares())

    # 2 válidos empatam no 3º critério → não atendido (conservador): 2/3 consolidado.
    assert "0.67 (1.00/0.67, 1 falho(s))" in para_markdown(rel)
    votos = para_json(rel)["casos"][0]["avaliacao"]["votos"]
    assert [v["nota"] for v in votos] == pytest.approx([1.0, 2 / 3])


def test_resumo_da_variancia_conta_leituras_divergentes():
    from evals.variancia import resumir

    r = resumir([1.0, 0.67, 1.0, 1.0, 0.0], limiar=0.7)

    assert (r.fora_da_moda, r.abaixo_do_limiar) == (2, 2)
    assert r.desvio_padrao == pytest.approx(0.388, abs=1e-3)
