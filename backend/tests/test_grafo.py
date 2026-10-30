from __future__ import annotations

import json
from datetime import date

import pytest

from fakes import (
    EmbedderFake,
    EngineFake,
    PlanejadorFake,
    QdrantFake,
    RecomendadorFake,
    RedatorFake,
    linhas_historico,
    linhas_kpi,
)

from agent.grafo import ConfigAnalise, Dependencias, construir_grafo, selecionar_fracos
from agent.kpis import KpiAvaliado
from agent.state import Plano
from agent.tools.run_sql import ErroExecucaoSQL

DOC_SUL = {
    "document": "Frete grátis no Sul elevou a recompra.",
    "fonte": "minio://corpus/prescricao/2024-08-sul-frete-gratis.md",
    "resultado": "positivo",
}


def _deps(plano, linhas, qdrant=None, embedder=None, redator=None, recomendador=None):
    return Dependencias(
        engine=EngineFake(colunas=["dimensao", "mes", "realizado", "meta"], linhas=linhas),  # type: ignore[arg-type]
        qdrant=qdrant or QdrantFake(payloads=[DOC_SUL]),  # type: ignore[arg-type]
        embedder=embedder if embedder is not None else EmbedderFake(),
        planejador=PlanejadorFake(plano=plano),
        redator=redator or RedatorFake(),
        recomendador=recomendador or RecomendadorFake(),
        config=ConfigAnalise(),
        hoje=lambda: date(2026, 10, 28),
    )


async def _rodar(deps, pergunta="Como melhorar a recompra no Sul?"):
    eventos, final = [], {}
    async for modo, chunk in construir_grafo(deps).astream(
        {"pergunta": pergunta}, stream_mode=["custom", "values"]
    ):
        if modo == "custom":
            eventos.append(chunk)
        else:
            final = chunk
    return eventos, final


def test_topologia_e_fixa():
    nos = set(construir_grafo(_deps(Plano(), [])).get_graph().nodes)

    assert nos == {
        "__start__",
        "planejar",
        "clarificar",
        "perna_quantitativa",
        "enriquecer",
        "recomendar",
        "relatorio",
        "__end__",
    }


async def test_caso_central_mede_kpi_fraco_enriquece_e_redige():
    redator = RedatorFake()
    plano = Plano(kpis=["taxa_recompra"], eixo="regiao", valor="sul")
    deps = _deps(plano, linhas_kpi({"Sul": -0.28, "Norte": 0.05}), redator=redator)

    eventos, final = await _rodar(deps)

    assert [(f.kpi, f.dimensao) for f in final["fracos"]] == [("taxa_recompra", "Sul")]
    assert len(final["sqls"]) == 1 and final["sqls"][0].endswith("LIMIT 500")
    assert [h.fonte for h in final["hits"]] == [DOC_SUL["fonte"]]
    assert "## Relatório\nRecompra no Sul." in final["relatorio"]
    etapas = [e["nome"] for e in eventos if e["tipo"] == "etapa"]
    assert etapas == ["planejar", "perna_quantitativa", "enriquecer", "recomendar", "relatorio"]
    tipos = [e["tipo"] for e in eventos]
    assert tipos.index("sql") < tipos.index("fontes") < tipos.index("relatorio")
    # o redator só recebe o que foi medido/recuperado neste run
    contexto = json.loads(redator.contextos[0])
    assert contexto["fontes_recuperadas"][0]["fonte"] == DOC_SUL["fonte"]
    assert contexto["kpis_queda_real"][0]["dimensao"] == "Sul"


async def test_enriquecimento_filtra_pela_dimensao_e_kpi_do_kpi_fraco():
    qdrant = QdrantFake(payloads=[DOC_SUL])
    deps = _deps(
        Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas_kpi({"Sul": -0.3}), qdrant=qdrant
    )

    await _rodar(deps)

    colecoes = [c["collection_name"] for c in qdrant.chamadas]
    assert colecoes == ["diagnostico", "prescricao"]
    filtros = [{c.key: c.match.value for c in ch["query_filter"].must} for ch in qdrant.chamadas]
    assert filtros == [{"regiao": "Sul"}, {"kpi_alvo": "taxa_recompra", "regiao": "Sul"}]


async def test_sem_kpi_fraco_pula_o_enriquecimento():
    qdrant = QdrantFake(payloads=[DOC_SUL])
    deps = _deps(
        Plano(kpis=["faturamento"], eixo="regiao"), linhas_kpi({"Sul": 0.02}), qdrant=qdrant
    )

    eventos, final = await _rodar(deps)

    assert final["fracos"] == [] and qdrant.chamadas == []
    assert "enriquecer" not in [e.get("nome") for e in eventos]
    assert final["relatorio"]


async def test_sem_embedder_avisa_e_segue_sem_fontes():
    deps = _deps(Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas_kpi({"Sul": -0.3}))
    deps = Dependencias(**{**deps.__dict__, "embedder": None})

    eventos, final = await _rodar(deps)

    assert final["hits"] == []
    assert any(e["tipo"] == "aviso" for e in eventos)


def _av(dim, gap, classificacao="queda_real", eixo="regiao"):
    return KpiAvaliado(
        kpi="faturamento",
        eixo=eixo,
        dimensao=dim,
        gap_medio=gap,
        variacao_yoy=None,
        classificacao=classificacao,
        serie=[],
    )


def test_selecao_pega_so_a_classificacao_pedida_pior_primeiro_ate_o_maximo():
    avaliados = [
        _av("A", -0.05, "saudavel"),
        _av("B", -0.30),
        _av("C", -0.12),
        _av("D", -0.20),
        _av("E", -0.15),
        _av("F", -0.40, "sazonal"),
    ]

    fracos = selecionar_fracos(avaliados, None, maximo=3)

    assert [f.dimensao for f in fracos] == ["B", "D", "E"]
    assert [s.dimensao for s in selecionar_fracos(avaliados, None, 3, "sazonal")] == ["F"]


@pytest.mark.parametrize("valor", ["sul", "SUL", " Sul "])
def test_recorte_por_valor_ignora_caixa_e_espacos(valor):
    fracos = selecionar_fracos([_av("Sul", -0.3), _av("Norte", -0.4)], valor, 3)

    assert [f.dimensao for f in fracos] == ["Sul"]


def test_valor_desconhecido_nao_zera_a_analise():
    fracos = selecionar_fracos([_av("Sul", -0.3)], "Atlântida", 3)

    assert [f.dimensao for f in fracos] == ["Sul"]


async def test_falha_na_busca_degrada_com_aviso_em_vez_de_abortar_o_run():
    from qdrant_client.http.exceptions import ResponseHandlingException

    qdrant = QdrantFake(erro=ResponseHandlingException(OSError("x")))
    deps = _deps(
        Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas_kpi({"Sul": -0.3}), qdrant=qdrant
    )

    eventos, final = await _rodar(deps)

    assert final["hits"] == [] and final["relatorio"]
    assert any(e["tipo"] == "aviso" for e in eventos)


async def test_valor_citado_inexistente_vira_aviso_para_o_redator():
    redator = RedatorFake()
    plano = Plano(kpis=["taxa_recompra"], eixo="regiao", valor="Atlântida")
    deps = _deps(plano, linhas_kpi({"Sul": -0.3}), redator=redator)

    eventos, final = await _rodar(deps)

    avisos = [e["mensagem"] for e in eventos if e["tipo"] == "aviso"]
    assert any("Atlântida" in a for a in avisos)
    assert "Atlântida" in json.dumps(json.loads(redator.contextos[0])["avisos"], ensure_ascii=False)


async def test_cada_hit_diz_a_colecao_e_o_kpi_fraco_que_ele_explica():
    deps = _deps(Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas_kpi({"Sul": -0.3}))

    _, final = await _rodar(deps)

    meta = final["hits"][0].metadados
    assert meta["colecao"] == "diagnostico"
    assert (meta["para_kpi"], meta["para_dimensao"]) == ("taxa_recompra", "Sul")


async def test_redator_recebe_desvio_ja_calculado_e_situacao_explicita():
    # Regressão do smoke real: o LLM leu a série crua e disse "perto da meta" p/ um KPI -11%.
    redator = RedatorFake()
    deps = _deps(
        Plano(kpis=["faturamento"], eixo="categoria"),
        linhas_kpi({"Brinquedos": -0.113}),
        redator=redator,
    )

    await _rodar(deps)

    kpi = json.loads(redator.contextos[0])["kpis_queda_real"][0]
    assert kpi["desvio_vs_meta_pct"] == -11.3
    assert kpi["situacao"].startswith("abaixo da meta")
    assert kpi["dimensao"] == "Brinquedos"


async def test_avisos_repetidos_sao_deduplicados():
    from qdrant_client.http.exceptions import ResponseHandlingException

    qdrant = QdrantFake(erro=ResponseHandlingException(OSError("x")))
    plano = Plano(kpis=["taxa_recompra"], eixo="regiao")
    deps = _deps(plano, linhas_kpi({"Sul": -0.3, "Nordeste": -0.2}), qdrant=qdrant)

    _, final = await _rodar(deps)

    assert len(final["avisos"]) == len(set(final["avisos"]))


async def test_relatorio_comeca_com_premissas_escritas_pelo_codigo():
    eventos, final = await _rodar(
        _deps(Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas_kpi({"Sul": -0.3}))
    )

    rel = final["relatorio"]
    assert rel.startswith("## Premissas")
    assert "2026-11" in rel  # período-alvo = mês atual + 1 (hoje = 2026-10-28)
    assert "2026-06" in rel  # dados disponíveis até
    assert "taxa_recompra" in rel and "regiao" in rel  # escopo
    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["periodo_alvo"] == "2026-11" and premissas["dados_ate"] == "2026-06"


async def test_mes_citado_vira_o_periodo_alvo():
    plano = Plano(kpis=["taxa_conversao"], eixo="canal", mes_citado=7)
    pergunta = "Como aumentar a conversão do site em julho?"

    eventos, _ = await _rodar(_deps(plano, linhas_kpi({"site_proprio": 0.0})), pergunta=pergunta)
    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["periodo_alvo"] == "2027-07"


async def test_sazonal_nao_dispara_enriquecimento_e_vai_ao_relatorio_como_sazonal():
    qdrant, redator = QdrantFake(payloads=[DOC_SUL]), RedatorFake()
    # Brinquedos: -20% na meta de 2026, mas igual a 2024/2025 (padrão de sempre).
    linhas = linhas_historico({"Brinquedos": {2024: 0.8, 2025: 0.8, 2026: 0.8}})
    deps = _deps(
        Plano(kpis=["faturamento"], eixo="categoria"), linhas, qdrant=qdrant, redator=redator
    )

    eventos, final = await _rodar(deps)

    assert final["fracos"] == [] and qdrant.chamadas == []
    contexto = json.loads(redator.contextos[0])
    assert contexto["kpis_queda_real"] == []
    assert contexto["kpis_sazonais"][0]["dimensao"] == "Brinquedos"
    assert contexto["kpis_sazonais"][0]["situacao"].startswith("abaixo da meta, em linha")


async def test_tudo_saudavel_gera_relatorio_com_premissas_e_sem_enriquecer():
    qdrant, redator = QdrantFake(payloads=[DOC_SUL]), RedatorFake()
    deps = _deps(
        Plano(kpis=["faturamento"], eixo="regiao"),
        linhas_kpi({"Sul": 0.03, "Norte": 0.01}),
        qdrant=qdrant,
        redator=redator,
    )

    _, final = await _rodar(deps)

    assert qdrant.chamadas == [] and final["relatorio"].startswith("## Premissas")
    contexto = json.loads(redator.contextos[0])
    assert contexto["kpis_queda_real"] == [] and contexto["kpis_sazonais"] == []


async def test_pergunta_fora_do_escopo_devolve_clarificacao_sem_rodar_sql():
    deps = _deps(Plano(fora_do_escopo=True), linhas_kpi({"Sul": -0.3}))
    redator = deps.redator

    eventos, final = await _rodar(deps, pergunta="Qual a capital da França?")

    assert deps.engine.executados == []  # type: ignore[attr-defined]
    assert redator.contextos == []  # type: ignore[attr-defined]  # LLM redator nem é chamado
    assert "?" in final["relatorio"] and "faturamento" in final["relatorio"]
    assert [e["nome"] for e in eventos if e["tipo"] == "etapa"] == ["planejar", "clarificar"]


async def test_premissas_declaram_o_escopo_realmente_analisado():
    # Recompra não tem meta por canal: a análise roda total + regiao, e é isso que se declara.
    plano = Plano(kpis=["taxa_recompra"], eixo="canal")

    eventos, final = await _rodar(_deps(plano, linhas_kpi({"Sul": -0.3})))

    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["eixos"] == ["regiao", "total"]
    assert any("canal" in a for a in final["avisos"])


async def test_sem_dados_as_premissas_saem_mesmo_assim():
    _, final = await _rodar(_deps(Plano(kpis=["faturamento"], eixo="regiao"), []))

    assert final["relatorio"].startswith("## Premissas") and "sem dados" in final["relatorio"]


async def test_resultado_no_teto_de_linhas_vira_aviso():
    deps = _deps(Plano(kpis=["faturamento"], eixo="regiao"), linhas_kpi({"Sul": 0.0}))
    deps = Dependencias(**{**deps.__dict__, "config": ConfigAnalise(sql_teto_linhas=6)})

    _, final = await _rodar(deps)

    assert any("teto" in a for a in final["avisos"])


async def test_mes_citado_inventado_pelo_llm_e_ignorado():
    # Regressão do smoke: "próximo mês" e o LLM preencheu mes_citado=11.
    plano = Plano(kpis=["faturamento"], eixo="regiao", mes_citado=11)

    eventos, _ = await _rodar(
        _deps(plano, linhas_kpi({"Sul": 0.0})),
        pergunta="Como melhorar minhas vendas no próximo mês?",
    )

    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["origem_periodo"] == "mês atual + 1" and premissas["periodo_alvo"] == "2026-11"


PRESC_SUL = {
    "document": "# Post-mortem — Frete grátis no Sul (ago/2024)\n\nRecompra +11pp.",
    "fonte": "minio://corpus/prescricao/2024-08-sul-frete-gratis.md",
    "resultado": "positivo",
    "periodo_referencia": "2024-08",
}
DIAG_SUL = {
    "document": "# Tickets — atrasos no Sul",
    "fonte": "minio://corpus/diagnostico/2025-11-sul-atrasos-transportadora.md",
}


def _rec(fonte, dimensao="Sul", acao="Reativar frete grátis"):
    from agent.grounding import Recomendacao

    return Recomendacao(
        prioridade=1,
        kpi="taxa_recompra",
        dimensao=dimensao,
        acao=acao,
        justificativa="Funcionou em 2024.",
        fonte=fonte,
    )


def _deps_sul(recomendador, redator=None, por_colecao=None):
    qdrant = QdrantFake(
        por_colecao=por_colecao or {"diagnostico": [DIAG_SUL], "prescricao": [PRESC_SUL]}
    )
    return _deps(
        Plano(kpis=["taxa_recompra"], eixo="regiao"),
        linhas_kpi({"Sul": -0.3}),
        qdrant=qdrant,
        recomendador=recomendador,
        redator=redator,
    )


async def test_so_recomendacao_com_fonte_de_prescricao_do_run_chega_ao_relatorio():
    inventada = _rec("minio://corpus/prescricao/2099-01-inventada.md", acao="Campanha inventada")
    deps = _deps_sul(RecomendadorFake(recomendacoes=[_rec(PRESC_SUL["fonte"]), inventada]))

    eventos, final = await _rodar(deps)

    rel = final["relatorio"]
    assert "## Recomendações priorizadas" in rel and f"[{PRESC_SUL['fonte']}]" in rel
    assert "Campanha inventada" not in rel and "2099-01-inventada" not in rel
    recs = next(e for e in eventos if e["tipo"] == "recomendacoes")["recomendacoes"]
    assert [r["fonte"] for r in recs] == [PRESC_SUL["fonte"]]
    assert any("descartada" in e["mensagem"] for e in eventos if e["tipo"] == "aviso")


async def test_historico_de_tentativas_vem_do_resultado_das_prescricoes():
    _, final = await _rodar(_deps_sul(RecomendadorFake(recomendacoes=[_rec(PRESC_SUL["fonte"])])))

    historico = final["relatorio"].split("## O que já funcionou e o que não funcionou")[1]
    assert "Frete grátis no Sul" in historico.split("**Não funcionou")[0]


async def test_sem_prescricao_recuperada_nao_chama_o_llm_e_declara_ausencia():
    recomendador = RecomendadorFake(recomendacoes=[_rec(PRESC_SUL["fonte"])])
    deps = _deps_sul(recomendador, por_colecao={"diagnostico": [DIAG_SUL], "prescricao": []})

    _, final = await _rodar(deps)

    assert recomendador.contextos == []
    assert "Não há recomendação fundamentada" in final["relatorio"]


async def test_falha_do_recomendador_degrada_para_ausencia_de_recomendacao():
    deps = _deps_sul(RecomendadorFake(erro=RuntimeError("timeout")))

    eventos, final = await _rodar(deps)

    assert "Não há recomendação fundamentada" in final["relatorio"]
    assert any("recomenda" in e["mensagem"] for e in eventos if e["tipo"] == "aviso")


async def test_citacao_inventada_no_texto_do_redator_e_sinalizada():
    redator = RedatorFake(partes=["Causa: atrasos [minio://corpus/diagnostico/inventado.md]."])
    deps = _deps_sul(RecomendadorFake(recomendacoes=[_rec(PRESC_SUL["fonte"])]), redator=redator)

    eventos, final = await _rodar(deps)

    assert any("inventado.md" in e["mensagem"] for e in eventos if e["tipo"] == "aviso")
    assert "fora do permitido" in final["relatorio"]


async def test_evento_de_fontes_citadas_para_a_ui():
    redator = RedatorFake(partes=[f"Causa: atrasos [{DIAG_SUL['fonte']}]."])
    deps = _deps_sul(RecomendadorFake(recomendacoes=[_rec(PRESC_SUL["fonte"])]), redator=redator)

    eventos, _ = await _rodar(deps)

    citadas = next(e for e in eventos if e["tipo"] == "fontes_citadas")["fontes"]
    assert set(citadas) == {PRESC_SUL["fonte"], DIAG_SUL["fonte"]}


async def test_sem_queda_real_nao_ha_secao_prescritiva():
    deps = _deps(Plano(kpis=["faturamento"], eixo="regiao"), linhas_kpi({"Sul": 0.02}))

    _, final = await _rodar(deps)

    assert "## Recomendações priorizadas" not in final["relatorio"]


async def test_fontes_citadas_nao_incluem_prescricao_que_o_relatorio_nao_exibe():
    anterior = {
        "document": "# Relatório anterior",
        "fonte": "minio://corpus/prescricao/2026-05-relatorio.md",
    }
    deps = _deps_sul(
        RecomendadorFake(recomendacoes=[_rec(PRESC_SUL["fonte"])]),
        por_colecao={"diagnostico": [DIAG_SUL], "prescricao": [PRESC_SUL, anterior]},
    )

    eventos, _ = await _rodar(deps)

    citadas = next(e for e in eventos if e["tipo"] == "fontes_citadas")["fontes"]
    assert anterior["fonte"] not in citadas and PRESC_SUL["fonte"] in citadas


async def test_texto_de_recomendacao_descartada_nao_chega_ao_redator():
    # Bloqueante da revisão: o aviso de descarte levava a ação inventada ao contexto do redator.
    redator = RedatorFake()
    inventada = _rec("minio://corpus/prescricao/2099-01-inventada.md", acao="Campanha inventada")
    deps = _deps_sul(RecomendadorFake(recomendacoes=[inventada]), redator=redator)

    _, final = await _rodar(deps)

    assert "Campanha inventada" not in redator.contextos[0]
    assert "Campanha inventada" not in final["relatorio"]
    assert "não tinha fonte válida" in final["relatorio"]


async def test_redator_nao_recebe_prescricoes_so_diagnostico():
    redator = RedatorFake()
    deps = _deps_sul(RecomendadorFake(recomendacoes=[_rec(PRESC_SUL["fonte"])]), redator=redator)

    await _rodar(deps)

    colecoes = {
        f["metadados"]["colecao"] for f in json.loads(redator.contextos[0])["fontes_recuperadas"]
    }
    assert colecoes == {"diagnostico"}


async def test_evento_de_recomendacoes_sai_mesmo_sem_prescricao():
    deps = _deps_sul(RecomendadorFake(), por_colecao={"diagnostico": [DIAG_SUL], "prescricao": []})

    eventos, _ = await _rodar(deps)

    assert next(e for e in eventos if e["tipo"] == "recomendacoes")["recomendacoes"] == []


async def test_prescricao_compartilhada_por_dois_fracos_guarda_os_dois_alvos():
    doc = {**PRESC_SUL, "fonte": "minio://corpus/prescricao/2025-11-bundle.md"}
    qdrant = QdrantFake(por_colecao={"diagnostico": [], "prescricao": [doc]})
    linhas = linhas_kpi({"Sul": -0.3, "Norte": -0.25})
    deps = _deps(Plano(kpis=["taxa_recompra"], eixo="regiao"), linhas, qdrant=qdrant)

    _, final = await _rodar(deps)

    alvos = final["hits"][0].metadados["alvos"]
    assert sorted(map(tuple, alvos)) == [("taxa_recompra", "Norte"), ("taxa_recompra", "Sul")]


@pytest.mark.parametrize(
    "pergunta",
    [
        "Como melhorar minhas vendas no próximo mês?",
        "O ticket médio sobe no fim do ano — é problema?",
        "Por que a recompra caiu no Sul?",
        "Como aumentar a conversão do site próprio?",
    ],
)
async def test_pergunta_do_dominio_nunca_cai_na_clarificacao_mesmo_se_o_llm_errar(pergunta):
    # Regressão do smoke real: gpt-4o-mini marcou fora_do_escopo p/ a pergunta central do PRD.
    deps = _deps(Plano(fora_do_escopo=True), linhas_kpi({"Sul": 0.0}))

    eventos, _ = await _rodar(deps, pergunta=pergunta)

    assert "clarificar" not in [e.get("nome") for e in eventos]


async def test_evento_de_recomendacoes_sai_tambem_sem_queda_real():
    eventos, _ = await _rodar(
        _deps(Plano(kpis=["faturamento"], eixo="regiao"), linhas_kpi({"Sul": 0.02}))
    )

    assert next(e for e in eventos if e["tipo"] == "recomendacoes")["recomendacoes"] == []


async def test_prescricao_citada_no_texto_livre_vira_aviso_e_nao_entra_como_fonte_citada():
    # Bloqueante da re-revisão: a função existia mas não estava ligada no nó relatorio.
    redator = RedatorFake(partes=[f"Causa: atrasos. Recomendo frete [{PRESC_SUL['fonte']}]."])
    deps = _deps_sul(RecomendadorFake(recomendacoes=[]), redator=redator)

    eventos, final = await _rodar(deps)

    assert any("fora da seção" in e["mensagem"] for e in eventos if e["tipo"] == "aviso")
    assert "fora do permitido" in final["relatorio"]
    citadas = next(e for e in eventos if e["tipo"] == "fontes_citadas")["fontes"]
    assert citadas.count(PRESC_SUL["fonte"]) <= 1  # só pelo histórico, nunca pela citação livre


async def test_llm_sem_proposta_com_prescricao_recuperada_declara_que_nenhuma_se_aplica():
    deps = _deps_sul(RecomendadorFake(recomendacoes=[]))

    _, final = await _rodar(deps)

    assert "nenhuma das prescrições recuperadas se aplica" in final["relatorio"]


def _turno_sul(recomendacoes=None):
    from agent.conversa import TurnoAnterior

    return TurnoAnterior(
        plano=Plano(kpis=["taxa_recompra"], eixo="regiao", valor="Sul"),
        recomendacoes=recomendacoes or [],
    )


async def _rodar_turno(deps, pergunta, turno):
    eventos, final = [], {}
    async for modo, chunk in construir_grafo(deps).astream(
        {"pergunta": pergunta, "turno_anterior": turno}, stream_mode=["custom", "values"]
    ):
        (eventos.append(chunk) if modo == "custom" else final.update(chunk))
    return eventos, final


async def test_acompanhamento_herda_kpi_do_turno_anterior():
    deps = _deps(
        Plano(eixo="regiao", valor="Nordeste"), linhas_kpi({"Sul": -0.3, "Nordeste": -0.25})
    )

    eventos, final = await _rodar_turno(deps, "e no Nordeste?", _turno_sul())

    plano = next(e for e in eventos if e["tipo"] == "plano")["plano"]
    assert (plano["kpis"], plano["valor"]) == (["taxa_recompra"], "Nordeste")
    assert [f.dimensao for f in final["fracos"]] == ["Nordeste"]


async def test_recomendacao_ja_entregue_na_conversa_nao_se_repete_vira_acompanhamento():
    ja = [
        {
            "fonte": PRESC_SUL["fonte"],
            "kpi": "taxa_recompra",
            "dimensao": "Sul",
            "acao": "Reativar frete grátis",
        }
    ]
    deps = _deps_sul(
        RecomendadorFake(recomendacoes=[_rec(PRESC_SUL["fonte"], acao="Frete grátis de novo")])
    )

    eventos, final = await _rodar_turno(deps, "Como melhorar a recompra no Sul?", _turno_sul(ja))

    rel = final["relatorio"]
    novas = rel.split("## Recomendações priorizadas")[1].split("##")[0]
    assert "Frete grátis de novo" not in novas
    assert "## Acompanhamento do que já foi recomendado" in rel and "Frete grátis de novo" in rel
    assert next(e for e in eventos if e["tipo"] == "recomendacoes")["recomendacoes"] == []
    assert len(next(e for e in eventos if e["tipo"] == "retomadas")["retomadas"]) == 1


async def test_acao_que_o_llm_marca_como_ja_feita_em_relatorio_anterior_vai_para_acompanhamento():
    rec = _rec(PRESC_SUL["fonte"]).model_copy(update={"retomada": True})
    deps = _deps_sul(RecomendadorFake(recomendacoes=[rec]))

    _, final = await _rodar(deps)

    assert "## Acompanhamento do que já foi recomendado" in final["relatorio"]


async def test_recomendador_recebe_os_relatorios_anteriores_e_o_ja_recomendado():
    anterior = {
        "document": "# Relatório anterior — já recomendou frete grátis",
        "subtipo": "relatorio_anterior",
        "fonte": "minio://corpus/prescricao/2026-05-relatorio-sul-recompra.md",
    }
    recomendador = RecomendadorFake(recomendacoes=[_rec(PRESC_SUL["fonte"])])
    deps = _deps_sul(
        recomendador, por_colecao={"diagnostico": [DIAG_SUL], "prescricao": [PRESC_SUL, anterior]}
    )
    ja = [
        {
            "fonte": "minio://x.md",
            "kpi": "taxa_recompra",
            "dimensao": "Sul",
            "acao": "Resolver logística",
        }
    ]

    await _rodar_turno(deps, "Como melhorar a recompra no Sul?", _turno_sul(ja))

    contexto = json.loads(recomendador.contextos[0])
    assert "já recomendou frete grátis" in contexto["relatorios_anteriores"][0]
    assert contexto["ja_recomendado_nesta_conversa"][0]["acao"] == "Resolver logística"


async def test_premissas_declaram_o_que_foi_herdado_e_nao_dizem_citado():
    # Bloqueante da revisão: "e no Nordeste?" mostrava "mês citado" e "valor citado" herdados.
    from agent.conversa import TurnoAnterior

    turno = TurnoAnterior(
        plano=Plano(kpis=["taxa_recompra"], eixo="regiao", valor="Sul", mes_citado=7)
    )
    deps = _deps(Plano(kpis=["faturamento"]), linhas_kpi({"Sul": -0.3}))

    eventos, final = await _rodar_turno(deps, "e o faturamento?", turno)

    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["origem_periodo"] == "herdado do turno anterior"
    topo = final["relatorio"].split("## Diagn")[0]
    assert "valor herdado do turno anterior: Sul" in topo and "valor citado" not in topo
    assert "citado na pergunta" not in topo


async def test_pergunta_fora_do_escopo_no_meio_da_conversa_nao_quebra_a_heranca():
    from agent.conversa import TurnoAnterior

    turno = TurnoAnterior(plano=Plano(kpis=["taxa_recompra"], eixo="regiao", valor="Sul"))
    deps = _deps(Plano(fora_do_escopo=True), linhas_kpi({"Sul": -0.3}))

    eventos, final = await _rodar_turno(deps, "Qual a capital da França?", turno)

    assert "clarificar" in [e.get("nome") for e in eventos]
    # o plano gravado continua com o recorte da conversa, p/ o turno seguinte herdar
    assert (final["plano"].kpis, final["plano"].valor) == (["taxa_recompra"], "Sul")


def _linhas_julho_fraco():
    # Conversão do site: julho sempre ~-33% da meta; junho acima. Formato (dim, mes, real, meta).
    linhas = []
    for ano in (2023, 2024, 2025):
        linhas += [
            ("site_proprio", f"{ano}-06", 0.032, 0.030),
            ("site_proprio", f"{ano}-07", 0.020, 0.030),
        ]
    return linhas


def _filtro(chamada):
    return {
        c.key: getattr(c.match, "value", None) or getattr(c.match, "any", None)
        for c in chamada["query_filter"].must
    }


async def test_mes_alvo_historicamente_fraco_vira_risco_e_diagnostico_e_recortado_pelo_mes():
    qdrant = QdrantFake(por_colecao={"diagnostico": [DIAG_SUL], "prescricao": [PRESC_SUL]})
    plano = Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio")
    deps = _deps(plano, _linhas_julho_fraco(), qdrant=qdrant)

    eventos, final = await _rodar(
        deps, pergunta="Como aumentar a conversão do site próprio em julho?"
    )

    assert [(f.dimensao, f.classificacao) for f in final["fracos"]] == [
        ("site_proprio", "risco_sazonal")
    ]
    diag, presc = (_filtro(c) for c in qdrant.chamadas)
    assert diag["periodo_referencia"] == ["2023-07", "2024-07", "2025-07"]
    assert "periodo_referencia" not in presc  # o que funcionou pode ser de qualquer época
    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert "julho" in premissas["recorte"]


async def test_mes_no_plural_com_anteriores_recorta_tambem_a_prescricao():
    qdrant = QdrantFake(por_colecao={"diagnostico": [DIAG_SUL], "prescricao": [PRESC_SUL]})
    linhas = [r for r in _linhas_julho_fraco()]
    linhas = [(d, m.replace("-07", "-11"), r, mt) for d, m, r, mt in linhas]
    deps = _deps(
        Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio"), linhas, qdrant=qdrant
    )

    await _rodar(
        deps, pergunta="O que funcionou em novembros anteriores para aumentar a conversão?"
    )

    _, presc = (_filtro(c) for c in qdrant.chamadas)
    assert presc["periodo_referencia"] == ["2023-11", "2024-11", "2025-11"]


async def test_fim_do_ano_sem_ano_e_padrao_descritivo_sem_enriquecer():
    qdrant, redator = QdrantFake(payloads=[DOC_SUL]), RedatorFake()
    linhas = []
    for ano, alta in ((2023, 1.10), (2024, 1.12), (2025, 0.98)):
        linhas += [("total", f"{ano}-{m:02d}", 100.0, 100.0) for m in range(1, 10)]
        linhas += [("total", f"{ano}-{m:02d}", 100.0 * alta, 100.0) for m in (10, 11, 12)]
    deps = _deps(Plano(kpis=["ticket_medio"]), linhas, qdrant=qdrant, redator=redator)

    _, final = await _rodar(deps, pergunta="O ticket médio sobe muito no fim do ano — é problema?")

    assert final["fracos"] == [] and qdrant.chamadas == []
    padrao = json.loads(redator.contextos[0])["kpis_padrao_do_recorte"][0]
    assert padrao["vs_resto_do_ano_pct"] == {"2023": 10.0, "2024": 12.0, "2025": -2.0}


async def test_redator_recebe_os_kpis_saudaveis_do_recorte():
    redator = RedatorFake()
    deps = _deps(
        Plano(kpis=["faturamento"], eixo="regiao", valor="Nordeste"),
        linhas_kpi({"Nordeste": 0.15}),
        redator=redator,
    )

    await _rodar(deps)

    saudaveis = json.loads(redator.contextos[0])["kpis_saudaveis"]
    assert [s["dimensao"] for s in saudaveis] == ["Nordeste"]
    assert saudaveis[0]["desvio_vs_meta_pct"] == 15.0


async def test_dados_disponiveis_ate_vem_do_banco_e_nao_da_serie_do_recorte():
    linhas = _linhas_julho_fraco() + [("site_proprio", "2026-06", 0.031, 0.030)]
    deps = _deps(Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio"), linhas)

    eventos, _ = await _rodar(deps, pergunta="Como aumentar a conversão do site próprio em julho?")

    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["dados_ate"] == "2026-06"


async def test_recorte_de_mes_herdado_no_acompanhamento_e_o_que_a_analise_usa():
    # Bloqueante da revisão: premissa dizia julho (herdado) e a análise voltava aos recentes.
    from agent.conversa import TurnoAnterior

    turno = TurnoAnterior(
        plano=Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio", mes_citado=7)
    )
    deps = _deps(Plano(eixo="canal", valor="site_proprio"), _linhas_julho_fraco())

    eventos, final = await _rodar_turno(deps, "e no site próprio?", turno)

    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert "julho" in (premissas["recorte"] or "")
    assert [f.classificacao for f in final["fracos"]] == ["risco_sazonal"]


async def test_periodo_passado_nao_vira_mes_alvo_no_plano():
    deps = _deps(
        Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio"), _linhas_julho_fraco()
    )

    eventos, _ = await _rodar(deps, pergunta="Como foi a conversão do site em julho de 2025?")

    plano = next(e for e in eventos if e["tipo"] == "plano")["plano"]
    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert plano["mes_citado"] is None and premissas["origem_periodo"] == "mês atual + 1"


async def test_recorte_sem_dados_completos_avisa():
    deps = _deps(
        Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio"), _linhas_julho_fraco()
    )

    _, final = await _rodar(deps, pergunta="Como foi a conversão no fim de 2026?")

    assert any("sem dados completos" in a for a in final["avisos"])


def _deps_composto(linhas_composto, plano, qdrant=None, redator=None):
    from fakes import CATALOGO_SQL

    deps = _deps(plano, linhas_kpi({"marketplace": -0.05}), qdrant=qdrant, redator=redator)
    deps.engine.por_trecho = {  # type: ignore[attr-defined]
        "FROM regioes UNION ALL": CATALOGO_SQL,
        "pr.categoria_id = ": (["dimensao", "mes", "realizado"], linhas_composto),
    }
    return deps


async def test_categoria_com_canal_vira_recorte_composto_e_enriquece_pelas_duas_dimensoes():
    q4 = [
        ("Eletrônicos × marketplace", f"{a}-{m}", v)
        for a, v in ((2024, 100.0), (2025, 56.0))
        for m in ("10", "11", "12")
    ]
    qdrant = QdrantFake(por_colecao={"diagnostico": [DIAG_SUL], "prescricao": [PRESC_SUL]})
    deps = _deps_composto(
        q4, Plano(kpis=["faturamento"], eixo="canal", valor="marketplace"), qdrant=qdrant
    )

    eventos, final = await _rodar(
        deps, pergunta="Por que o faturamento de Eletrônicos no marketplace caiu no fim de 2025?"
    )

    [fraco] = final["fracos"]
    assert (fraco.dimensao, fraco.classificacao, fraco.sem_meta) == (
        "Eletrônicos × marketplace",
        "queda_real",
        True,
    )
    assert fraco.variacao_yoy == pytest.approx(-0.44)
    diag, _ = (_filtro(c) for c in qdrant.chamadas)
    assert (diag["produto"], diag["canal"]) == ("Eletrônicos", "marketplace")
    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert (
        premissas["eixos"] == ["categoria×canal"]
        and premissas["valor"] == "Eletrônicos × marketplace"
    )
    sqls = [e["sql"] for e in eventos if e["tipo"] == "sql"]
    assert len(sqls) == 1 and "pr.categoria_id = 1" in sqls[0]  # só o recorte pedido


async def test_composto_saudavel_vai_ao_redator_sem_meta_e_com_o_ano_a_ano():
    redator = RedatorFake()
    s1 = [
        ("Beleza × Nordeste", f"{a}-0{m}", v)
        for a, v in ((2024, 100.0), (2025, 100.0), (2026, 128.0))
        for m in (4, 5, 6)
    ]
    deps = _deps_composto(s1, Plano(eixo="categoria", valor="Beleza"), redator=redator)

    _, final = await _rodar(
        deps, pergunta="Devo investir mais em Beleza no Nordeste no próximo mês?"
    )

    assert final["fracos"] == []
    [saudavel] = json.loads(redator.contextos[0])["kpis_saudaveis"]
    assert saudavel["dimensao"] == "Beleza × Nordeste" and saudavel["desvio_vs_meta_pct"] is None
    assert saudavel["variacao_vs_anos_anteriores_pct"] == 28.0
    # Sem meta composta: o rótulo não pode dizer "dentro ou acima da meta" (o LLM repetia isso).
    assert "meta própria" in saudavel["situacao"] and "acima da meta" not in saudavel["situacao"]


async def test_composto_com_kpi_que_nao_tem_categoria_avisa_e_usa_o_eixo_simples():
    deps = _deps_composto([], Plano(kpis=["taxa_recompra"], eixo="regiao", valor="Nordeste"))

    _, final = await _rodar(deps, pergunta="Como vai a recompra de Beleza no Nordeste?")

    assert any("composto" in a for a in final["avisos"])


async def test_catalogo_concorrente_nao_duplica_e_composto_segue_detectado():
    import asyncio

    q4 = [
        ("Eletrônicos × marketplace", f"{a}-{m}", v)
        for a, v in ((2024, 100.0), (2025, 56.0))
        for m in ("10", "11", "12")
    ]
    deps = _deps_composto(q4, Plano(kpis=["faturamento"], eixo="canal", valor="marketplace"))
    deps.engine.atraso = 0.01  # type: ignore[attr-defined]
    grafo = construir_grafo(deps)  # um grafo compartilhado entre requisições, como no app
    pergunta = "Por que o faturamento de Eletrônicos no marketplace caiu no fim de 2025?"

    async def rodar():
        return await grafo.ainvoke({"pergunta": pergunta})

    finais = await asyncio.gather(rodar(), rodar())
    finais.append(await rodar())

    assert all(
        [f.dimensao for f in final["fracos"]] == ["Eletrônicos × marketplace"] for final in finais
    )
    catalogos = [s for s in deps.engine.executados if "FROM regioes UNION ALL" in s]  # type: ignore[attr-defined]
    assert len(catalogos) == 2  # os 2 concorrentes consultaram; o 3º usou o cache


async def test_catalogo_indisponivel_segue_sem_composto_e_tenta_de_novo_no_proximo_run():
    from fakes import erro_pg

    deps = _deps_composto([], Plano(kpis=["faturamento"], eixo="canal", valor="marketplace"))
    grafo = construir_grafo(deps)
    deps.engine.erro = erro_pg("57014")  # type: ignore[attr-defined]

    with pytest.raises(ErroExecucaoSQL):  # o SQL do KPI também falha; importa o catálogo
        await grafo.ainvoke({"pergunta": "Faturamento de Eletrônicos no marketplace?"})
    deps.engine.erro = None  # type: ignore[attr-defined]
    await grafo.ainvoke({"pergunta": "Faturamento de Eletrônicos no marketplace?"})

    catalogos = [s for s in deps.engine.executados if "FROM regioes UNION ALL" in s]  # type: ignore[attr-defined]
    assert len(catalogos) == 2  # a falha não ficou em cache


async def test_acompanhamento_de_recorte_composto_continua_composto():
    from agent.conversa import TurnoAnterior

    q4 = [
        ("Eletrônicos × marketplace", f"{a}-{m}", v)
        for a, v in ((2023, 100.0), (2024, 56.0))
        for m in ("10", "11", "12")
    ]
    deps = _deps_composto(q4, Plano())
    turno = TurnoAnterior(
        plano=Plano(kpis=["faturamento"], eixo="canal", valor="Eletrônicos × marketplace"),
        recomendacoes=[],
    )

    eventos, final = await _rodar_turno(deps, "e no fim de 2024?", turno)

    assert [f.dimensao for f in final["fracos"]] == ["Eletrônicos × marketplace"]
    assert not any("não existe nos eixos" in a for a in final["avisos"])


async def test_composto_avisa_kpi_que_ficou_de_fora():
    deps = _deps_composto([], Plano(kpis=["faturamento", "ticket_medio"], eixo="canal"))

    _, final = await _rodar(deps, pergunta="Faturamento e ticket de Eletrônicos no marketplace?")

    assert any("não analisado: ticket_medio" in a for a in final["avisos"])
    assert any("sem histórico comparável" in a for a in final["avisos"])
    assert not any("não existe nos eixos" in a for a in final["avisos"])


async def test_julho_do_ano_passado_analisa_o_passado_e_nao_vira_mes_alvo():
    # #28: antes, "julho do ano passado" virava mês-alvo futuro (2027-07).
    deps = _deps(
        Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio"), _linhas_julho_fraco()
    )

    eventos, final = await _rodar(
        deps, pergunta="Como foi a conversão do site em julho do ano passado?"
    )

    plano = next(e for e in eventos if e["tipo"] == "plano")["plano"]
    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert plano["mes_citado"] is None and premissas["origem_periodo"] == "mês atual + 1"
    assert "2025" in premissas["recorte"]
    [avaliado] = final["sazonais"]  # julho de 2025 igual aos anteriores: sazonal, não queda real
    # Série = julho nos anos de base, ancorada no ano pedido (ADR 0012): nada depois de 2025-07.
    meses = {p["mes"] for p in avaliado.serie}
    assert max(meses) == "2025-07" and all(m.endswith("-07") for m in meses)


async def test_desde_janeiro_cai_no_default_declarado():
    deps = _deps(Plano(kpis=["faturamento"]), linhas_kpi({"Sul": -0.3}))

    eventos, _ = await _rodar(deps, pergunta="Como evoluiu o faturamento desde janeiro?")

    plano = next(e for e in eventos if e["tipo"] == "plano")["plano"]
    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert plano["mes_citado"] is None and premissas["recorte"] is None
    assert premissas["origem_periodo"] == "mês atual + 1"


async def test_julho_passado_sem_dados_do_periodo_avisa_recorte_incompleto():
    # hoje = out/2026: "julho passado" é jul/2026, que a série (até 2025) não tem.
    deps = _deps(
        Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio"), _linhas_julho_fraco()
    )

    eventos, final = await _rodar(deps, pergunta="Como foi a conversão do site em julho passado?")

    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert "2026" in premissas["recorte"]
    assert any("sem dados completos" in a for a in final["avisos"])


async def test_mes_alvo_junto_de_mes_passado_e_descartado():
    # "comparado a julho passado, e agosto?": o recorte é o passado (julho); agosto não vira alvo.
    deps = _deps(
        Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio"), _linhas_julho_fraco()
    )

    eventos, _ = await _rodar(
        deps, pergunta="Comparado a julho do ano passado, como fica a conversão em agosto?"
    )

    plano = next(e for e in eventos if e["tipo"] == "plano")["plano"]
    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert plano["mes_citado"] is None and "2025" in premissas["recorte"]


@pytest.mark.parametrize(
    ("pergunta", "filtro"),
    [
        ("Como foi a conversão do site no fim de 2023?", "IN (10, 11, 12)"),  # passado
        ("Como aumentar a conversão do site em julho?", "IN (7)"),  # mês-alvo
        ("O que fazer com a conversão do site?", None),  # sem recorte: janela contínua
        ("A conversão do site sobe no fim do ano?", None),  # padrão precisa do resto do ano
    ],
)
async def test_consulta_traz_so_os_meses_que_a_avaliacao_usa(pergunta, filtro):
    # #29: categoria × 60 meses chegava a 480 de 500 linhas; o recorte só usa os seus meses.
    deps = _deps(
        Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio"), _linhas_julho_fraco()
    )

    eventos, _ = await _rodar(deps, pergunta=pergunta)

    [sql] = [e["sql"].casefold() for e in eventos if e["tipo"] == "sql"]  # pós-guardrail
    if filtro:
        assert f"extract(month from p.data_pedido) {filtro.casefold()}" in sql
    else:
        assert "extract(month from p.data_pedido) in" not in sql


async def test_dados_ate_vem_do_banco_mesmo_com_a_serie_filtrada():
    linhas = [(*ln, "2026-06") for ln in _linhas_julho_fraco()]  # coluna dados_ate do SQL
    deps = _deps(Plano(kpis=["taxa_conversao"], eixo="canal", valor="site_proprio"), linhas)
    colunas = ["dimensao", "mes", "realizado", "meta", "dados_ate"]
    deps.engine.colunas = colunas  # type: ignore[attr-defined]

    eventos, _ = await _rodar(deps, pergunta="Como aumentar a conversão do site em julho?")

    premissas = next(e for e in eventos if e["tipo"] == "premissas")["premissas"]
    assert premissas["dados_ate"] == "2026-06"  # e não 2025-07, o último julho da série
