"""Período-alvo do relatório: premissa resolvida em código, não pelo LLM."""

from __future__ import annotations

import re
import unicodedata
from datetime import date


def periodo_alvo(hoje: date, mes_citado: int | None = None) -> str:
    """AAAA-MM do próximo período: mês atual + 1, ou a próxima ocorrência do mês citado.

    O mês corrente já começou, então "em outubro" dito em outubro é o próximo outubro.
    """
    if mes_citado is None:
        ano, mes = (hoje.year + 1, 1) if hoje.month == 12 else (hoje.year, hoje.month + 1)
    else:
        ano = hoje.year if mes_citado > hoje.month else hoje.year + 1
        mes = mes_citado
    return f"{ano:04d}-{mes:02d}"


_MESES = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}
_PADRAO_MES = re.compile(r"\b(" + "|".join(_MESES) + r")\b")


def mes_nomeado(pergunta: str) -> int | None:
    """Mês do ano escrito na pergunta; o LLM não decide isso ("próximo mês" não é mês citado).

    Mais de um mês distinto é ambíguo: sem escolha silenciosa, cai no default (mês atual + 1).
    """
    sem_acento = unicodedata.normalize("NFKD", pergunta).encode("ascii", "ignore").decode()
    achados = {_MESES[m] for m in _PADRAO_MES.findall(sem_acento.casefold())}
    return achados.pop() if len(achados) == 1 else None
