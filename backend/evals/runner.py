"""Roda o golden contra o agente e decide pass/fail (ADR 0011)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from evals.golden import Caso
from evals.judge import Avaliacao, ErroJudge, Judge, nota_da_avaliacao
from evals.scoring import Limiares, NotaDeterministica, ResultadoRun, pontuar

logger = logging.getLogger(__name__)

Executor = Callable[[Caso], Awaitable[ResultadoRun]]


@dataclass(frozen=True)
class ResultadoCaso:
    caso: Caso
    run: ResultadoRun | None
    deterministica: NotaDeterministica | None
    avaliacao: Avaliacao | None
    nota_judge: float | None
    aprovado: bool
    motivos: list[str]

    @property
    def contabilizado(self) -> bool:
        """Falha conhecida (com issue dona) é relatada, mas fica fora do veredito."""
        return self.caso.pendente is None


@dataclass(frozen=True)
class RelatorioEval:
    casos: list[ResultadoCaso]
    limiares: Limiares
    so_deterministico: bool = False  # rodou com --sem-judge: não é aprovação plena do gate

    @property
    def contabilizados(self) -> list[ResultadoCaso]:
        return [r for r in self.casos if r.contabilizado]

    @property
    def aprovado(self) -> bool:
        # Sem nenhum caso contabilizado nada foi provado: não aprova (all([]) seria True).
        return bool(self.contabilizados) and all(r.aprovado for r in self.contabilizados)


async def _um_caso(
    caso: Caso, executar: Executor, judge: Judge | None, lim: Limiares
) -> ResultadoCaso:
    try:
        run = await executar(caso)
    except Exception as exc:  # o runner relata a falha do caso, não morre com ela
        logger.warning("caso %s falhou ao executar", caso.id, exc_info=True)
        motivo = f"falha ao executar: {type(exc).__name__}"
        return ResultadoCaso(caso, None, None, None, None, False, [motivo])
    nota = pontuar(caso, run, lim)
    motivos = list(nota.motivos)
    avaliacao, nota_judge = None, None
    # Judge só onde o determinístico passou: o que já reprovou em código não gasta LLM.
    if judge is not None and nota.aprovado:
        try:
            avaliacao = await judge.avaliar(caso, run.relatorio)
        except ErroJudge as exc:
            motivos.append(f"judge inválido: {exc}")
        except Exception as exc:  # timeout/rate limit do judge: reprova o caso, não o runner
            logger.warning("judge falhou no caso %s", caso.id, exc_info=True)
            motivos.append(f"judge falhou: {type(exc).__name__}")
        else:
            nota_judge = nota_da_avaliacao(avaliacao)
            if nota_judge < lim.judge_minimo:
                motivos.append(f"judge {nota_judge:.2f} < {lim.judge_minimo:.2f}")
    return ResultadoCaso(caso, run, nota, avaliacao, nota_judge, not motivos, motivos)


async def rodar(
    casos: list[Caso], executar: Executor, judge: Judge | None, limiares: Limiares
) -> RelatorioEval:
    # Sequencial: o agente é o sistema sob teste; não o sobrecarregar distorce latência e custo.
    resultados = [await _um_caso(c, executar, judge, limiares) for c in casos]
    return RelatorioEval(resultados, limiares, so_deterministico=judge is None)


def para_markdown(rel: RelatorioEval) -> str:
    lim = rel.limiares
    veredito = "APROVADO" if rel.aprovado else "REPROVADO"
    if rel.aprovado and rel.so_deterministico:
        veredito += " (só determinístico — judge desligado, não vale como gate)"
    linhas = [f"# Avaliação do agente — {veredito}", ""]
    if not rel.contabilizados:
        linhas += ["> ⚠️ nenhum caso contabilizado: nada foi provado, o gate não aprova.", ""]
    resolvidos = [r.caso.id for r in rel.casos if not r.contabilizado and r.aprovado]
    if resolvidos:
        linhas += [f"> ✅ pendente que passou: {', '.join(resolvidos)} — remova o `pendente`.", ""]
    linhas += [
        f"Limiares: recall de fontes ≥ {lim.recall_minimo:.2f} · distratores ≤ "
        f"{lim.distratores_maximo} · judge ≥ {lim.judge_minimo:.2f} (ADR 0011).",
        "",
        "| caso | resultado | recall | judge | motivos |",
        "|---|---|---|---|---|",
    ]
    for r in rel.casos:
        resultado = "✅ passa" if r.aprovado else "❌ falha"
        if not r.contabilizado:
            resultado += f" (conhecida: {r.caso.pendente})"
        recall = (
            f"{r.deterministica.recall:.2f}"
            if r.deterministica and r.deterministica.recall is not None
            else "—"
        )
        judge = f"{r.nota_judge:.2f}" if r.nota_judge is not None else "—"
        if r.avaliacao and r.avaliacao.votos:  # maioria (#27): as notas de cada voto
            notas = "/".join(f"{v.nota:.2f}" for v in r.avaliacao.votos)
            falhos = f", {r.avaliacao.votos_falhos} falho(s)" if r.avaliacao.votos_falhos else ""
            judge += f" ({notas}{falhos})"
        linhas.append(
            f"| {r.caso.id} | {resultado} | {recall} | {judge} | {'; '.join(r.motivos)} |"
        )
    return "\n".join(linhas) + "\n"


def para_json(rel: RelatorioEval) -> dict[str, Any]:
    """Relatório auditável: caso, run, métricas e os critérios do judge (não só a nota)."""
    return {
        "aprovado": rel.aprovado,
        "so_deterministico": rel.so_deterministico,
        "limiares": asdict(rel.limiares),
        "casos": [
            {
                "caso": r.caso.model_dump(),
                "contabilizado": r.contabilizado,
                "aprovado": r.aprovado,
                "motivos": r.motivos,
                "run": asdict(r.run) if r.run else None,
                "deterministica": asdict(r.deterministica) if r.deterministica else None,
                "avaliacao": r.avaliacao.model_dump() if r.avaliacao else None,
                "nota_judge": r.nota_judge,
            }
            for r in rel.casos
        ],
    }
