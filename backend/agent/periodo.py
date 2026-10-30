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
# "marco" sem acento também é substantivo ("o marco do projeto"): só vale como mês com contexto
# de data antes ("em/de/desde/até/para marco") ou depois ("marco de 2025"). "Março" é sempre mês.
_ANTES_DE_DATA = re.compile(r"\b(?:em|de|desde|ate|para)\s+$")
_COM_ANO = re.compile(r"^\s+de\s+(20\d\d)\b")
# Passado relativo logo depois do mês: "julho do ano passado", "julho passado".
_PASSADO_RELATIVO = re.compile(r"^\s+(do\s+ano\s+)?passado\b")
_DESDE = re.compile(r"\bdesde\s+(?:o\s+mes\s+de\s+)?$")
_SEGUIDO_DE_ANTERIORES = re.compile(r"^\s+anteriores\b")

Contexto = Literal["alvo", "com_ano", "passado", "ano_passado", "desde"]


@dataclass(frozen=True)
class _Mencao:
    mes: int
    contexto: Contexto
    ano: int | None = None  # só em "com_ano" ("julho de 2025")
    # "novembros" ou "novembro anteriores": desta menção, não do texto inteiro (o substantivo
    # "marcos" não pode recortar a prescrição de julho).
    anteriores: bool = False


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().casefold()


def _mencoes(pergunta: str) -> list[_Mencao]:
    """Cada mês escrito na pergunta, com o contexto temporal em que aparece (#28)."""
    texto = _normalizar(pergunta)
    # NFC: "março" decomposto (c + cedilha combinante) também conta como acentuado.
    marco_acentuado = "março" in unicodedata.normalize("NFC", pergunta).casefold()
    mencoes: list[_Mencao] = []
    for m in _PADRAO_MES.finditer(texto):
        antes, depois = texto[: m.start()], texto[m.end() :]
        com_ano = _COM_ANO.match(depois)
        if m.group(1) == "marco" and not marco_acentuado:
            if not (_ANTES_DE_DATA.search(antes) or com_ano):
                continue  # substantivo, não mês
        contexto: Contexto = "alvo"
        if _DESDE.search(antes):
            contexto = "desde"
        elif rel := _PASSADO_RELATIVO.match(depois):
            contexto = "ano_passado" if rel.group(1) else "passado"
        elif com_ano:
            contexto = "com_ano"
        anteriores = bool(m.group(2)) or bool(_SEGUIDO_DE_ANTERIORES.match(depois))
        ano = int(com_ano.group(1)) if contexto == "com_ano" and com_ano else None
        mencoes.append(_Mencao(_MESES[m.group(1)], contexto, ano, anteriores))
    return mencoes


def mes_nomeado(pergunta: str) -> int | None:
    """Mês-alvo escrito na pergunta; o LLM não decide isso ("próximo mês" não é mês citado).

    Só conta mês sem contexto de passado: "julho passado", "julho de 2025" e "desde janeiro"
    não são o período-alvo (#28). Mais de um mês distinto é ambíguo: sem escolha silenciosa,
    cai no default (mês atual + 1).
    """
    achados = {m.mes for m in _mencoes(pergunta) if m.contexto == "alvo"}
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


def _ano_do_passado(mencao: _Mencao, hoje: date) -> int:
    """'julho do ano passado' = ano anterior; 'julho passado' = o último julho que já passou."""
    if mencao.contexto == "ano_passado" or mencao.mes >= hoje.month:
        return hoje.year - 1
    return hoje.year


def recorte_citado(pergunta: str, hoje: date | None = None) -> Recorte | None:
    texto = _normalizar(pergunta)
    mencoes = _mencoes(pergunta)
    if m := _FIM_DO_ANO.search(texto):
        ano_txt, passado, ano_curto, ano_tri = m.group(1), m.group(2), m.group(3), m.group(4)
        ano = int(ano_txt or ano_curto or ano_tri) if (ano_txt or ano_curto or ano_tri) else None
        if passado:
            ano = (hoje or date.today()).year - 1
        return Recorte("passado" if ano else "padrao", (10, 11, 12), ano)
    if com_ano := [m for m in mencoes if m.contexto == "com_ano"]:
        return Recorte("passado", (com_ano[0].mes,), com_ano[0].ano)
    relativos = [m for m in mencoes if m.contexto in ("passado", "ano_passado")]
    if len({m.mes for m in relativos}) == 1:
        ano = _ano_do_passado(relativos[0], hoje or date.today())
        return Recorte("passado", (relativos[0].mes,), ano)
    alvos = [m for m in mencoes if m.contexto == "alvo"]
    if len({m.mes for m in alvos}) != 1:
        return None  # nenhum, ambíguo ("maio e junho") ou só "desde": sem escolha silenciosa
    # Só "novembros"/"novembro anteriores" DO mês-alvo: "ano anterior" solto não recorta.
    return Recorte("mes_alvo", (alvos[0].mes,), anteriores=any(m.anteriores for m in alvos))
