"""Período-alvo do relatório: premissa resolvida em código, não pelo LLM."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal


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
_PADRAO_MES = re.compile(r"\b(" + "|".join(_MESES) + r")(s)?\b")


def mes_nomeado(pergunta: str) -> int | None:
    """Mês do ano escrito na pergunta; o LLM não decide isso ("próximo mês" não é mês citado).

    Mais de um mês distinto é ambíguo: sem escolha silenciosa, cai no default (mês atual + 1).
    """
    sem_acento = unicodedata.normalize("NFKD", pergunta).encode("ascii", "ignore").decode()
    achados = {_MESES[m] for m, _ in _PADRAO_MES.findall(sem_acento.casefold())}
    return achados.pop() if len(achados) == 1 else None


TipoRecorte = Literal["mes_alvo", "padrao", "passado"]


@dataclass(frozen=True)
class Recorte:
    """Período citado na pergunta (#9), resolvido em código.

    - mes_alvo: um mês sem ano ("em julho") → esse mês nos anos anteriores (risco sazonal).
    - padrao: "fim do ano" sem ano → padrão recorrente vs o resto do ano (descritivo).
    - passado: período com ano ("fim de 2025") → aquele período vs o mesmo do ano anterior.
    """

    tipo: TipoRecorte
    meses: tuple[int, ...]
    ano: int | None = None
    anteriores: bool = False  # "novembros anteriores": a prescrição também é recortada


# "último trimestre" fica de fora: em pt-BR costuma ser o mais recente, não out-dez.
_FIM_DO_ANO = re.compile(
    r"\b(?:(?:fim|final)\s+d[eo]\s+(?:ano(?:\s+(?:de\s+(20\d\d)|(passado)))?|(20\d\d))"
    r"|(?:4\s*[ºo°]?|quarto)\s*tri(?:mestre)?(?:\s+de\s+(20\d\d))?)\b"
)
_ANTERIORES = re.compile(
    r"\b(?:" + "|".join(_MESES) + r")s\b|\b(?:" + "|".join(_MESES) + r")\s+anteriores\b"
)
_MES_COM_ANO = re.compile(r"\b(" + "|".join(_MESES) + r")\s+de\s+(20\d\d)\b")


def recorte_citado(pergunta: str, hoje: date | None = None) -> Recorte | None:
    texto = unicodedata.normalize("NFKD", pergunta).encode("ascii", "ignore").decode().casefold()
    if m := _FIM_DO_ANO.search(texto):
        ano_txt, passado, ano_curto, ano_tri = m.group(1), m.group(2), m.group(3), m.group(4)
        ano = int(ano_txt or ano_curto or ano_tri) if (ano_txt or ano_curto or ano_tri) else None
        if passado:
            ano = (hoje or date.today()).year - 1
        return Recorte("passado" if ano else "padrao", (10, 11, 12), ano)
    if m := _MES_COM_ANO.search(texto):
        return Recorte("passado", (_MESES[m.group(1)],), int(m.group(2)))
    achados = _PADRAO_MES.findall(texto)
    meses = {_MESES[nome] for nome, _ in achados}
    if len(meses) != 1:
        return None  # nenhum ou ambíguo ("maio e junho"): sem escolha silenciosa
    # Só "novembros"/"novembro anteriores": "ano anterior" solto não recorta a prescrição.
    anteriores = bool(_ANTERIORES.search(texto))
    return Recorte("mes_alvo", (meses.pop(),), anteriores=anteriores)
