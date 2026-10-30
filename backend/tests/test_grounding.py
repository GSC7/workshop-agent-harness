from __future__ import annotations

from agent.grounding import (
    Recomendacao,
    citacoes_de_prescricao,
    citacoes_sem_fonte,
    historico_markdown,
    recomendacoes_markdown,
    validar_recomendacoes,
)
from agent.tools.search import Hit

FRETE = "minio://corpus/prescricao/2024-08-sul-frete-gratis.md"
BRINDE = "minio://corpus/prescricao/2025-03-sul-brinde-generico.md"
ATRASOS = "minio://corpus/diagnostico/2025-11-sul-atrasos-transportadora.md"


def _hit(fonte, colecao, resultado=None, texto="# Post-mortem — Frete grátis no Sul\n\nDetalhes."):
    meta = {
        "colecao": colecao,
        "para_kpi": "taxa_recompra",
        "para_dimensao": "Sul",
        "periodo_referencia": "2024-08",
    }
    if resultado:
        meta["resultado"] = resultado
    return Hit(texto=texto, score=0.9, fonte=fonte, metadados=meta)


HITS = [
    _hit(FRETE, "prescricao", "positivo"),
    _hit(BRINDE, "prescricao", "nulo", texto="# Post-mortem — Brinde genérico (mar/2025)\n\n..."),
    _hit(ATRASOS, "diagnostico"),
]


def _rec(fonte, prioridade=1, kpi="taxa_recompra", dimensao="Sul", acao="Reativar frete grátis"):
    return Recomendacao(
        prioridade=prioridade,
        kpi=kpi,
        dimensao=dimensao,
        acao=acao,
        justificativa="Funcionou em 2024.",
        fonte=fonte,
    )


def test_recomendacao_com_fonte_de_prescricao_do_run_e_valida():
    validas, descartadas = validar_recomendacoes([_rec(FRETE)], HITS)

    assert [r.fonte for r in validas] == [FRETE] and descartadas == []


def test_recomendacao_sem_fonte_recuperada_e_descartada():
    inventada = "minio://corpus/prescricao/2099-01-inventada.md"

    validas, descartadas = validar_recomendacoes([_rec(inventada)], HITS)

    assert validas == []
    assert descartadas[0][0].fonte == inventada and "não recuperada" in descartadas[0][1]


def test_fonte_de_diagnostico_nao_sustenta_prescricao():
    validas, descartadas = validar_recomendacoes([_rec(ATRASOS)], HITS)

    assert validas == [] and "prescricao" in descartadas[0][1]


def test_fonte_de_outro_kpi_ou_dimensao_e_descartada():
    validas, descartadas = validar_recomendacoes([_rec(FRETE, dimensao="Nordeste")], HITS)

    assert validas == [] and "Nordeste" in descartadas[0][1]


def test_validas_sao_reordenadas_e_renumeradas_pela_prioridade():
    recs = [_rec(BRINDE, prioridade=5, acao="Evitar brinde"), _rec(FRETE, prioridade=2)]

    validas, _ = validar_recomendacoes(recs, HITS)

    assert [(r.prioridade, r.fonte) for r in validas] == [(1, FRETE), (2, BRINDE)]


def test_markdown_de_recomendacoes_cita_a_fonte_de_cada_uma():
    md = recomendacoes_markdown([_rec(FRETE)], tem_queda_real=True)

    assert md.startswith("## Recomendações priorizadas") and f"[{FRETE}]" in md


def test_sem_recomendacao_valida_declara_ausencia_de_fundamento():
    md = recomendacoes_markdown([], tem_queda_real=True)

    assert "Não há recomendação fundamentada" in md


def test_historico_separa_o_que_funcionou_do_que_nao_funcionou_pelo_resultado():
    md = historico_markdown(HITS)

    funcionou, nao = md.split("**Não funcionou")
    assert "Frete grátis no Sul" in funcionou and FRETE in funcionou
    assert "Brinde genérico" in nao and BRINDE in nao
    assert ATRASOS not in md  # diagnóstico não é tentativa


def test_historico_vazio_sem_prescricao():
    assert historico_markdown([HITS[2]]) == ""


def test_citacao_no_texto_livre_sem_fonte_do_run_e_apontada():
    texto = f"Atrasos [{ATRASOS}] e algo inventado [minio://corpus/diagnostico/x.md]."

    assert citacoes_sem_fonte(texto, HITS) == ["minio://corpus/diagnostico/x.md"]


def _hit_multi(fonte, alvos, resultado="positivo"):
    meta = {
        "colecao": "prescricao",
        "resultado": resultado,
        "periodo_referencia": "2025-11",
        "para_kpi": alvos[0][0],
        "para_dimensao": alvos[0][1],
        "alvos": [list(a) for a in alvos],
    }
    return Hit(texto="# Bundle", score=0.9, fonte=fonte, metadados=meta)


def test_prescricao_que_serve_a_dois_kpis_fracos_vale_para_ambos():
    bundle = "minio://corpus/prescricao/2025-11-eletronicos-bundle-kit.md"
    hits = [_hit_multi(bundle, [("faturamento", "Eletrônicos"), ("faturamento", "marketplace")])]
    recs = [
        _rec(bundle, kpi="faturamento", dimensao="Eletrônicos"),
        _rec(bundle, prioridade=2, kpi="faturamento", dimensao="marketplace"),
    ]

    validas, descartadas = validar_recomendacoes(recs, hits)

    assert len(validas) == 2 and descartadas == []


def test_kpi_e_dimensao_escritos_de_outro_jeito_casam_e_saem_canonicos():
    hits = [_hit_multi("minio://x/loja.md", [("faturamento", "loja_fisica")])]

    validas, _ = validar_recomendacoes(
        [_rec("minio://x/loja.md", kpi="Faturamento", dimensao="loja física")], hits
    )

    assert [(r.kpi, r.dimensao) for r in validas] == [("faturamento", "loja_fisica")]


def test_fonte_com_colchetes_ou_espacos_ainda_casa():
    validas, _ = validar_recomendacoes([_rec(f" [{FRETE}] ")], HITS)

    assert [r.fonte for r in validas] == [FRETE]


def test_mesma_fonte_kpi_e_dimensao_com_redacoes_diferentes_vira_uma_so():
    recs = [
        _rec(FRETE, prioridade=2, acao="Frete grátis"),
        _rec(FRETE, prioridade=1, acao="Reativar frete"),
    ]

    validas, _ = validar_recomendacoes(recs, HITS)

    assert [r.acao for r in validas] == ["Reativar frete"]


def test_ausencia_declara_o_motivo_real():
    assert "falhou" in recomendacoes_markdown([], True, motivo="falha")
    assert "não tinha fonte válida" in recomendacoes_markdown([], True, motivo="descartadas")
    assert "nenhuma prescrição" in recomendacoes_markdown([], True, motivo="sem_prescricao")


def test_citacao_de_prescricao_no_texto_livre_e_apontada():
    texto = f"Causa: atrasos [{ATRASOS}]; recomendo frete [{FRETE}]."

    assert citacoes_de_prescricao(texto, HITS) == [FRETE]


def test_relatorio_anterior_nao_sustenta_recomendacao_nova():
    anterior = "minio://corpus/prescricao/2026-05-relatorio-sul-recompra.md"
    meta = {
        "colecao": "prescricao",
        "subtipo": "relatorio_anterior",
        "para_kpi": "taxa_recompra",
        "para_dimensao": "Sul",
        "alvos": [["taxa_recompra", "Sul"]],
    }
    hits = [*HITS, Hit(texto="# Relatório anterior", score=0.9, fonte=anterior, metadados=meta)]

    validas, descartadas = validar_recomendacoes([_rec(anterior)], hits)

    assert validas == [] and "relatório anterior" in descartadas[0][1]


def test_dimensao_composta_escrita_com_outro_separador_casa():
    bundle = "minio://corpus/prescricao/2025-11-eletronicos-bundle-kit.md"
    hits = [_hit_multi(bundle, [("faturamento", "Eletrônicos × marketplace")])]
    recs = [
        _rec(bundle, kpi="faturamento", dimensao=d)
        for d in ("Eletrônicos x marketplace", "Eletronicos / marketplace")
    ]

    validas, descartadas = validar_recomendacoes(recs, hits)

    assert descartadas == []
    assert [r.dimensao for r in validas] == ["Eletrônicos × marketplace"]


def test_dimensao_composta_nao_casa_com_so_uma_das_partes():
    bundle = "minio://corpus/prescricao/2025-11-eletronicos-bundle-kit.md"
    hits = [_hit_multi(bundle, [("faturamento", "Eletrônicos × marketplace")])]

    validas, _ = validar_recomendacoes(
        [_rec(bundle, kpi="faturamento", dimensao="marketplace")], hits
    )

    assert validas == []
