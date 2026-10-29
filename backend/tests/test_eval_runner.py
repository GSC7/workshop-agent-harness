from __future__ import annotations

import pytest

from evals.golden import Caso
from evals.judge import Avaliacao, Criterio
from evals.runner import para_markdown, rodar
from evals.scoring import Limiares, ResultadoRun

F = "minio://corpus/prescricao/f.md"


def _caso(id_, pendente=None):
    return Caso(
        id=id_,
        pergunta=f"pergunta {id_}",
        dispara_enriquecimento=True,
        fontes_esperadas=[F],
        recomendacao_esperada="esperado",
        pendente=pendente,
    )


BOM = ResultadoRun(
    concluido=True, etapas=["enriquecer"], fontes_citadas=[F], relatorio="## Premissas\n", sqls=1
)
RUIM = ResultadoRun(
    concluido=True,
    etapas=["perna_quantitativa"],
    fontes_citadas=[],
    relatorio="## Premissas\n",
    sqls=1,
)


class JudgeFake:
    def __init__(self, atendidos: list[bool]):
        self.atendidos = atendidos
        self.chamadas = 0

    async def avaliar(self, caso, relatorio):
        self.chamadas += 1
        return Avaliacao(
            criterios=[Criterio(criterio="c", atendido=a) for a in self.atendidos],
            inventou_informacao=False,
            justificativa="j",
        )


def _executor(por_pergunta):
    async def executar(caso):
        return por_pergunta[caso.id]

    return executar


async def test_todos_os_casos_contabilizados_aprovados_aprova_o_gate():
    rel = await rodar(
        [_caso("a"), _caso("b")], _executor({"a": BOM, "b": BOM}), JudgeFake([True]), Limiares()
    )

    assert rel.aprovado and [r.aprovado for r in rel.casos] == [True, True]


async def test_falha_conhecida_aparece_mas_nao_bloqueia():
    casos = [_caso("a"), _caso("n3", pendente="#9 — recorte sazonal")]

    rel = await rodar(casos, _executor({"a": BOM, "n3": RUIM}), JudgeFake([True]), Limiares())

    assert rel.aprovado
    n3 = next(r for r in rel.casos if r.caso.id == "n3")
    assert not n3.aprovado and not n3.contabilizado


async def test_judge_abaixo_do_limiar_reprova_mesmo_com_metricas_ok():
    rel = await rodar(
        [_caso("a")], _executor({"a": BOM}), JudgeFake([True, False, False]), Limiares()
    )

    assert not rel.aprovado
    assert rel.casos[0].nota_judge == pytest.approx(1 / 3)
    assert any("judge" in m for m in rel.casos[0].motivos)


async def test_sem_judge_o_veredito_e_so_deterministico():
    rel = await rodar([_caso("a")], _executor({"a": BOM}), None, Limiares())

    assert rel.aprovado and rel.casos[0].nota_judge is None


async def test_judge_nao_e_chamado_para_run_que_falhou_no_deterministico():
    judge = JudgeFake([True])

    rel = await rodar([_caso("a")], _executor({"a": RUIM}), judge, Limiares())

    assert not rel.aprovado and judge.chamadas == 0  # economiza: já reprovou em código


async def test_falha_ao_executar_um_caso_vira_reprovacao_com_motivo_e_nao_derruba_o_runner():
    async def executar(caso):
        raise ConnectionError("api fora")

    rel = await rodar([_caso("a")], executar, None, Limiares())

    assert not rel.aprovado and "ConnectionError" in rel.casos[0].motivos[0]


async def test_markdown_tem_veredito_e_uma_linha_por_caso_com_motivo():
    rel = await rodar(
        [_caso("a"), _caso("n3", pendente="#9")],
        _executor({"a": BOM, "n3": RUIM}),
        None,
        Limiares(),
    )

    md = para_markdown(rel)
    assert "APROVADO" in md and "| a |" in md and "| n3 |" in md and "#9" in md


async def test_json_do_relatorio_preserva_caso_avaliacao_e_criterios():
    # Regressão: asdict + default=str transformava Caso/Avaliacao (pydantic) em string.
    import json

    from evals.runner import para_json

    rel = await rodar([_caso("a")], _executor({"a": BOM}), JudgeFake([True, False]), Limiares())

    dados = json.loads(json.dumps(para_json(rel)))
    caso = dados["casos"][0]
    assert dados["aprovado"] is False
    assert caso["caso"]["id"] == "a"
    assert caso["avaliacao"]["criterios"][1] == {"criterio": "c", "atendido": False}
    assert caso["run"]["fontes_citadas"] == [F] and caso["nota_judge"] == 0.5


async def test_falha_do_judge_reprova_o_caso_com_motivo_sem_derrubar_o_runner():
    from evals.judge import ErroJudge

    class JudgeQuebrado:
        async def avaliar(self, caso, relatorio):
            raise ErroJudge("judge devolveu 1 de 2 critérios")

    rel = await rodar([_caso("a")], _executor({"a": BOM}), JudgeQuebrado(), Limiares())

    assert not rel.aprovado and any("judge" in m for m in rel.casos[0].motivos)


async def test_sem_nenhum_caso_contabilizado_o_gate_nao_aprova():
    # Bloqueante da revisão: all() sobre lista vazia aprovava sozinho.
    rel = await rodar(
        [_caso("x", pendente="#9")], _executor({"x": BOM}), JudgeFake([True]), Limiares()
    )

    assert not rel.aprovado and "nenhum caso contabilizado" in para_markdown(rel)


async def test_sem_judge_nao_conta_como_aprovacao_plena():
    rel = await rodar([_caso("a")], _executor({"a": BOM}), None, Limiares())

    assert rel.aprovado and rel.so_deterministico
    assert "só determinístico" in para_markdown(rel)


async def test_erro_qualquer_do_judge_reprova_o_caso_sem_derrubar_o_runner():
    class JudgeTimeout:
        async def avaliar(self, caso, relatorio):
            raise TimeoutError("openai")

    rel = await rodar(
        [_caso("a"), _caso("b")], _executor({"a": BOM, "b": BOM}), JudgeTimeout(), Limiares()
    )

    assert len(rel.casos) == 2 and not rel.aprovado
    assert "TimeoutError" in rel.casos[0].motivos[0]


async def test_pendente_que_passou_vira_alerta_para_remover_a_marca():
    rel = await rodar(
        [_caso("a"), _caso("n3", pendente="#9")], _executor({"a": BOM, "n3": BOM}), None, Limiares()
    )

    assert "remova o `pendente`" in para_markdown(rel)
