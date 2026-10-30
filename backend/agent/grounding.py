"""Grounding (regra de ouro, rules/agente.md): recomendação só com fonte de `prescricao` do run.

O LLM propõe; o código valida e escreve as seções prescritivas do relatório.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from agent.tools.search import Hit

_CITACAO = re.compile(r"\[(minio://[^\]\s]+)\]")
_RESULTADOS_FALHA = ("negativo", "nulo")


class Recomendacao(BaseModel):
    prioridade: int = Field(ge=1, description="1 = mais importante.")
    kpi: str = Field(description="KPI em queda real que a ação ataca (como no contexto).")
    dimensao: str = Field(description="Dimensão do KPI (ex.: Sul, loja_fisica).")
    acao: str = Field(description="Ação concreta para o período-alvo.")
    justificativa: str = Field(description="Por que, com base no que a fonte relata.")
    fonte: str = Field(description="Campo `fonte` exato de uma prescrição do contexto.")
    retomada: bool = Field(
        default=False,
        description="true se a ação já foi recomendada num relatório anterior do contexto.",
    )


class ListaRecomendacoes(BaseModel):
    recomendacoes: list[Recomendacao] = Field(default_factory=list)


MotivoAusencia = Literal[
    "sem_prescricao", "nenhuma_aplicavel", "falha", "descartadas", "so_retomadas"
]

_MENSAGEM_AUSENCIA: dict[str, str] = {
    "sem_prescricao": "nenhuma prescrição com fonte rastreável foi recuperada para os KPIs em "
    "queda real",
    "nenhuma_aplicavel": "nenhuma das prescrições recuperadas se aplica aos KPIs em queda real",
    "falha": "a geração de recomendações falhou neste run",
    "so_retomadas": "não há ação nova além das já recomendadas (veja o acompanhamento abaixo)",
    "descartadas": "toda recomendação proposta foi descartada porque não tinha fonte válida "
    "de prescrição deste run",
}


def normalizar(texto: str) -> str:
    """Comparação tolerante: sem acento, caixa ou separador ('loja física' == 'loja_fisica')."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[\s_-]+", " ", sem_acento.casefold()).strip()


def _chave_dimensao(dimensao: str) -> str:
    """Dimensão composta escrita de outro jeito ('Eletrônicos x marketplace', '…/…') casa."""
    return re.sub(r"\s*(?:/|\bx\b)\s*", " ", normalizar(dimensao)).strip()


def _limpar_fonte(fonte: str) -> str:
    return fonte.strip().strip("[]`'\" ").strip()


def _prescricoes(hits: Sequence[Hit]) -> dict[str, Hit]:
    return {h.fonte: h for h in hits if h.metadados.get("colecao") == "prescricao"}


def alvos(hit: Hit) -> list[tuple[str, str]]:
    """KPIs fracos que a fonte explica; um documento pode servir a mais de um (ex.: N2)."""
    lista = hit.metadados.get("alvos")
    if lista:
        return [(str(k), str(d)) for k, d in lista]
    return [(str(hit.metadados.get("para_kpi")), str(hit.metadados.get("para_dimensao")))]


def validar_recomendacoes(
    recomendacoes: Sequence[Recomendacao], hits: Sequence[Hit]
) -> tuple[list[Recomendacao], list[tuple[Recomendacao, str]]]:
    """Mantém só o que tem fonte de prescrição recuperada NESTE run, para um KPI/dimensão dela."""
    prescricoes = _prescricoes(hits)
    fontes_do_run = {h.fonte for h in hits}
    validas: dict[tuple[str, str, str], Recomendacao] = {}
    descartadas: list[tuple[Recomendacao, str]] = []
    for rec in sorted(recomendacoes, key=lambda r: r.prioridade):
        fonte = _limpar_fonte(rec.fonte)
        hit = prescricoes.get(fonte)
        if hit is not None and hit.metadados.get("subtipo") == "relatorio_anterior":
            # Relatório anterior é o que já foi dito, não uma tentativa documentada (RF-09).
            descartadas.append((rec, "fonte é um relatório anterior, não uma prescrição"))
            continue
        if hit is None:
            motivo = (
                "fonte não é da coleção prescricao"
                if fonte in fontes_do_run
                else "fonte não recuperada neste run"
            )
            descartadas.append((rec, motivo))
            continue
        alvo = next(
            (
                (k, d)
                for k, d in alvos(hit)
                if (normalizar(k), _chave_dimensao(d))
                == (normalizar(rec.kpi), _chave_dimensao(rec.dimensao))
            ),
            None,
        )
        if alvo is None:
            sobre = ", ".join(f"{k}/{d}" for k, d in alvos(hit))
            descartadas.append((rec, f"fonte é sobre {sobre}, não {rec.kpi}/{rec.dimensao}"))
            continue
        # Canônico vem do dado, não da redação do LLM; mesma fonte+alvo = uma só (a mais prioritária).
        validas.setdefault(
            (fonte, *alvo),
            rec.model_copy(update={"fonte": fonte, "kpi": alvo[0], "dimensao": alvo[1]}),
        )
    ordenadas = sorted(validas.values(), key=lambda r: r.prioridade)
    return [r.model_copy(update={"prioridade": i}) for i, r in enumerate(ordenadas, 1)], descartadas


def recomendacoes_markdown(
    validas: Sequence[Recomendacao], tem_queda_real: bool, motivo: MotivoAusencia = "sem_prescricao"
) -> str:
    """Seção prescritiva escrita pelo código: só o que passou na validação, com a fonte."""
    if not tem_queda_real:
        return ""
    if not validas:
        return (
            "## Recomendações priorizadas\n\n"
            f"Não há recomendação fundamentada: {_MENSAGEM_AUSENCIA[motivo]}. "
            "Nenhuma ação é sugerida sem fonte.\n\n"
        )
    itens = "\n".join(
        f"{r.prioridade}. **{r.acao}** ({r.kpi} · {r.dimensao}) — {r.justificativa} [{r.fonte}]"
        for r in validas
    )
    return f"## Recomendações priorizadas\n\n{itens}\n\n"


def _titulo(hit: Hit) -> str:
    primeira = next((ln for ln in hit.texto.splitlines() if ln.strip()), hit.fonte)
    return primeira.lstrip("# ").strip()


def historico_markdown(hits: Sequence[Hit]) -> str:
    """O que já funcionou / não funcionou, direto do campo `resultado` das prescrições."""
    prescricoes = [h for h in _prescricoes(hits).values() if h.metadados.get("resultado")]
    if not prescricoes:
        return ""

    def linhas(hs: list[Hit]) -> str:
        return (
            "\n".join(
                f"- {_titulo(h)} — {h.metadados.get('periodo_referencia', '?')}, "
                f"resultado {h.metadados['resultado']} [{h.fonte}]"
                for h in hs
            )
            or "- nenhum registro"
        )

    funcionou = [h for h in prescricoes if h.metadados["resultado"] == "positivo"]
    nao = [h for h in prescricoes if h.metadados["resultado"] in _RESULTADOS_FALHA]
    return (
        "## O que já funcionou e o que não funcionou\n\n"
        f"**Funcionou:**\n{linhas(funcionou)}\n\n"
        f"**Não funcionou:**\n{linhas(nao)}\n\n"
    )


def citacoes_sem_fonte(texto: str, hits: Sequence[Hit]) -> list[str]:
    """Citações [minio://...] do texto livre que não são fontes recuperadas neste run."""
    fontes = {h.fonte for h in hits}
    return list(dict.fromkeys(c for c in _CITACAO.findall(texto) if c not in fontes))


def citacoes_validas(texto: str, hits: Sequence[Hit]) -> list[str]:
    fontes = {h.fonte for h in hits}
    return list(dict.fromkeys(c for c in _CITACAO.findall(texto) if c in fontes))


def citacoes_de_prescricao(texto: str, hits: Sequence[Hit]) -> list[str]:
    """Prescrição citada no texto livre: recomendação só pode sair na seção validada."""
    prescricoes = _prescricoes(hits)
    return list(dict.fromkeys(c for c in _CITACAO.findall(texto) if c in prescricoes))


def acompanhamento_markdown(retomadas: Sequence[Recomendacao]) -> str:
    """Já recomendado (nesta conversa ou em relatório anterior): cobrar, não repetir (RF-09)."""
    if not retomadas:
        return ""
    itens = "\n".join(f"- **{r.acao}** ({r.kpi} · {r.dimensao}) [{r.fonte}]" for r in retomadas)
    return (
        "## Acompanhamento do que já foi recomendado\n\n"
        "Estas ações já foram recomendadas antes. Em vez de repeti-las, cobre a execução e meça "
        f"o efeito no KPI:\n{itens}\n\n"
    )
