from __future__ import annotations

from agent.conversa import TurnoAnterior, herdar
from agent.state import Plano

ANTERIOR = TurnoAnterior(
    plano=Plano(kpis=["taxa_recompra"], eixo="regiao", valor="Sul", mes_citado=7),
    recomendacoes=[],
)


def test_acompanhamento_troca_so_o_que_a_pergunta_nova_cita():
    # "e na região Sudeste?" → mesmo KPI e período, outro valor do mesmo eixo
    novo = herdar(Plano(eixo="regiao", valor="Sudeste"), ANTERIOR)

    assert (novo.kpis, novo.eixo, novo.valor, novo.mes_citado) == (
        ["taxa_recompra"],
        "regiao",
        "Sudeste",
        7,
    )


def test_novo_kpi_mantem_o_recorte_anterior():
    # "e o faturamento?" → mesmo recorte (Sul), outro KPI
    novo = herdar(Plano(kpis=["faturamento"]), ANTERIOR)

    assert (novo.kpis, novo.eixo, novo.valor) == (["faturamento"], "regiao", "Sul")


def test_eixo_novo_nao_herda_valor_de_outro_eixo():
    # "e por canal?" → 'Sul' não é um canal
    novo = herdar(Plano(eixo="canal"), ANTERIOR)

    assert (novo.eixo, novo.valor) == ("canal", None)


def test_mes_novo_na_pergunta_substitui_o_anterior():
    assert herdar(Plano(mes_citado=11), ANTERIOR).mes_citado == 11


def test_acompanhamento_curto_nao_vira_fora_do_escopo():
    # o LLM marcou "e no Sudeste?" como fora do escopo, mas há conversa em andamento
    novo = herdar(Plano(valor="Sudeste", fora_do_escopo=True), ANTERIOR)

    assert novo.fora_do_escopo is False and novo.valor == "Sudeste"


def test_sem_turno_anterior_o_plano_fica_como_esta():
    plano = Plano(kpis=["faturamento"])

    assert herdar(plano, None) == plano


def test_herdar_registra_o_que_veio_do_turno_anterior():
    novo = herdar(Plano(eixo="regiao", valor="Nordeste"), ANTERIOR)

    assert sorted(novo.herdados) == ["kpis", "mes_citado"]


def test_nada_citado_de_novo_herda_tudo_e_registra():
    novo = herdar(Plano(kpis=["faturamento"]), ANTERIOR)

    assert sorted(novo.herdados) == ["eixo", "mes_citado", "valor"]


def test_herdados_ficam_fora_do_schema_do_llm():
    assert "herdados" not in Plano.model_json_schema()["properties"]
