"""Variância do judge (#27): `uv run python -m evals.variancia [--repeticoes N] [--votos K]`.

Roda o agente UMA vez por caso e julga o MESMO relatório N vezes com 1 voto e N vezes com
maioria de K: isola a variância do judge da do agente (e não paga N execuções do agente).
Chama o LLM de verdade (custo) e exige a pilha no ar; fora do /validar, como o gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from evals.__main__ import _judge, _votos
from evals.execucao import executar_caso
from evals.golden import carregar_golden
from evals.judge import Judge, JudgeMaioria, nota_da_avaliacao
from evals.scoring import Limiares, pontuar


@dataclass(frozen=True)
class Resumo:
    notas: list[float]
    desvio_padrao: float
    fora_da_moda: int  # notas diferentes da mais frequente: as leituras divergentes
    abaixo_do_limiar: int


def resumir(notas: list[float], limiar: float) -> Resumo:
    moda = statistics.mode(notas)
    return Resumo(
        notas=notas,
        desvio_padrao=statistics.pstdev(notas),
        fora_da_moda=sum(n != moda for n in notas),
        abaixo_do_limiar=sum(n < limiar for n in notas),
    )


async def _notas(judge: Judge, caso_id: str, relatorio: str, n: int) -> list[float]:
    caso = next(c for c in carregar_golden() if c.id == caso_id)
    return [nota_da_avaliacao(await judge.avaliar(caso, relatorio)) for _ in range(n)]


async def _principal(args: argparse.Namespace) -> None:
    lim = Limiares()
    resultado: dict[str, dict[str, object]] = {}
    async with httpx.AsyncClient(base_url=args.base_url, timeout=300) as cliente:
        for caso in carregar_golden():
            print(f"… {caso.id}", file=sys.stderr, flush=True)
            run = await executar_caso(cliente, caso)
            if not pontuar(caso, run, lim).aprovado:
                resultado[caso.id] = {"pulado": "determinístico reprovou (o gate nem julgaria)"}
                continue
            antes = await _notas(_judge(), caso.id, run.relatorio, args.repeticoes)
            depois = await _notas(
                JudgeMaioria(_judge(), votos=args.votos), caso.id, run.relatorio, args.repeticoes
            )
            resultado[caso.id] = {
                "um_voto": asdict(resumir(antes, lim.judge_minimo)),
                f"maioria_de_{args.votos}": asdict(resumir(depois, lim.judge_minimo)),
            }
    Path(args.saida).write_text(json.dumps(resultado, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.variancia", description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--repeticoes", type=int, default=5)
    parser.add_argument("--votos", type=_votos, default=3)
    parser.add_argument("--saida", default="eval-variancia.json")
    asyncio.run(_principal(parser.parse_args()))


if __name__ == "__main__":
    main()
