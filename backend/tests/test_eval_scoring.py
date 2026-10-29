from __future__ import annotations

import pytest

from evals.golden import Caso
from evals.scoring import Limiares, ResultadoRun, pontuar

FRETE = "minio://corpus/prescricao/2024-08-sul-frete-gratis.md"
BRINDE = "minio://corpus/prescricao/2025-03-sul-brinde-generico.md"
ATRASO = "minio://corpus/diagnostico/2025-11-sul-atrasos-transportadora.md"
NPS_SUDESTE = "minio://corpus/diagnostico/2025-09-sudeste-nps-positivo.md"
L = Limiares()


def _caso(**kw):
    base = dict(
        id="n1",
        pergunta="Como melhorar a recompra no Sul?",
        dispara_enriquecimento=True,
        fontes_esperadas=[
            FRETE,
            BRINDE,
            ATRASO,
            "minio://corpus/prescricao/2023-08-sul-frete-gratis.md",
        ],
        distratores=[NPS_SUDESTE],
        recomendacao_esperada="Reativar frete grátis.",
    )
    return Caso(**{**base, **kw})


def _run(**kw):
    base = dict(
        concluido=True,
        etapas=["planejar", "perna_quantitativa", "enriquecer", "recomendar", "relatorio"],
        fontes_citadas=[FRETE, BRINDE, ATRASO],
        relatorio="## Premissas\n\n- ...\n\n## Diagnóstico",
        sqls=1,
        avisos=[],
    )
    return ResultadoRun(**{**base, **kw})


def test_caso_que_cita_3_de_4_fontes_passa_com_recall_075():
    nota = pontuar(_caso(), _run(), L)

    assert nota.recall == pytest.approx(0.75) and nota.aprovado


def test_recall_abaixo_do_limiar_reprova_e_diz_o_que_faltou():
    nota = pontuar(_caso(), _run(fontes_citadas=[FRETE]), L)

    assert not nota.aprovado
    assert any("recall" in m for m in nota.motivos)
    assert ATRASO in nota.fontes_faltando


def test_um_distrator_citado_reprova():
    nota = pontuar(_caso(), _run(fontes_citadas=[FRETE, BRINDE, ATRASO, NPS_SUDESTE]), L)

    assert not nota.aprovado and nota.distratores_citados == [NPS_SUDESTE]


def test_enriquecer_quando_nao_devia_reprova_o_controle():
    controle = _caso(dispara_enriquecimento=False, fontes_esperadas=[], distratores=[])

    nota = pontuar(controle, _run(), L)

    assert not nota.aprovado and any("enriquec" in m for m in nota.motivos)


def test_controle_saudavel_que_nao_enriquece_passa_sem_recall():
    controle = _caso(dispara_enriquecimento=False, fontes_esperadas=[], distratores=[])

    nota = pontuar(
        controle, _run(etapas=["planejar", "perna_quantitativa", "relatorio"], fontes_citadas=[]), L
    )

    assert nota.aprovado and nota.recall is None


def test_relatorio_sem_premissas_no_topo_reprova():
    nota = pontuar(_caso(), _run(relatorio="## Diagnóstico\n..."), L)

    assert not nota.aprovado and any("premissas" in m for m in nota.motivos)


def test_citacao_fora_do_permitido_reprova_o_grounding():
    run = _run(avisos=["citação sem fonte recuperada no texto: minio://x.md"])

    assert not pontuar(_caso(), run, L).aprovado


def test_run_que_nao_concluiu_reprova():
    assert not pontuar(_caso(), _run(concluido=False), L).aprovado


def test_clarificacao_esperada_exige_no_clarificar_e_zero_sql():
    caso = _caso(
        espera_clarificacao=True, dispara_enriquecimento=False, fontes_esperadas=[], distratores=[]
    )

    ok = pontuar(
        caso,
        _run(etapas=["planejar", "clarificar"], fontes_citadas=[], sqls=0, relatorio="Qual KPI?"),
        L,
    )
    ruim = pontuar(
        caso, _run(etapas=["planejar", "perna_quantitativa", "relatorio"], fontes_citadas=[]), L
    )

    assert ok.aprovado and not ruim.aprovado


def test_prefixos_de_grounding_sao_os_mesmos_que_o_grafo_emite():
    # Se o texto do aviso mudar no grafo, a métrica não pode ficar cega em silêncio.
    from agent.grafo import AVISOS_GROUNDING

    run = _run(avisos=[f"{AVISOS_GROUNDING[0]}: minio://x.md"])

    assert not pontuar(_caso(), run, L).aprovado


def test_erro_do_servidor_aparece_no_motivo():
    nota = pontuar(_caso(), _run(concluido=False, erro="HTTP 503"), L)

    assert any("HTTP 503" in m for m in nota.motivos)
