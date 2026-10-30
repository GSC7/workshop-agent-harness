"""Gate de avaliação: `uv run python -m evals [--base-url URL] [--sem-judge] [--votos K]`.

Roda o golden contra o sistema real (via nginx) e sai 1 se reprovar. Fora do /validar: chama o
LLM de verdade (custo) e exige a pilha no ar.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from agent.llm import criar_modelo
from app.config import settings
from evals.execucao import executar_caso
from evals.golden import Caso, carregar_golden
from evals.judge import JudgeLLM, JudgeMaioria
from evals.runner import para_json, para_markdown, rodar
from evals.scoring import Limiares, ResultadoRun


def _judge() -> JudgeLLM:
    chave = (
        settings.anthropic_api_key
        if settings.llm_provider == "anthropic"
        else settings.openai_api_key
    )
    modelo = settings.llm_model_judge or settings.llm_model_forte
    if chave is None or modelo is None:
        raise SystemExit("judge sem chave/modelo: configure o .env ou rode com --sem-judge")
    return JudgeLLM(criar_modelo(settings.llm_provider, modelo, chave, 2000, 120))


async def _principal(args: argparse.Namespace) -> int:
    casos = carregar_golden()
    async with httpx.AsyncClient(base_url=args.base_url, timeout=300) as cliente:

        async def executar(caso: Caso) -> ResultadoRun:
            print(f"… {caso.id}", file=sys.stderr, flush=True)
            return await executar_caso(cliente, caso)

        # Maioria de K julgamentos por caso (#27): uma leitura divergente não decide o caso.
        judge = None if args.sem_judge else JudgeMaioria(_judge(), votos=args.votos)
        relatorio = await rodar(casos, executar, judge, Limiares())
    md = para_markdown(relatorio)
    saida = Path(args.saida).with_suffix(".json")
    saida.with_suffix(".md").write_text(md, encoding="utf-8")
    saida.write_text(
        json.dumps(para_json(relatorio), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(md)
    if not relatorio.aprovado:
        return 1
    return 2 if relatorio.so_deterministico else 0  # 2 = passou, mas sem judge não é o gate


def _votos(texto: str) -> int:
    v = int(texto)
    if v < 1 or v % 2 == 0:
        raise argparse.ArgumentTypeError("deve ser ímpar e positivo (maioria sem empate)")
    return v


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--sem-judge", action="store_true", help="só métricas determinísticas")
    parser.add_argument("--saida", default="eval-report.json")
    parser.add_argument(
        "--votos",
        type=_votos,
        default=settings.eval_judge_votos,
        help="julgamentos por caso, consolidados por maioria (ímpar; 1 = judge único)",
    )
    sys.exit(asyncio.run(_principal(parser.parse_args())))


if __name__ == "__main__":
    main()
