from __future__ import annotations

import re
from pathlib import Path

import pytest

VERSOES = Path(__file__).resolve().parents[2] / "infra" / "db" / "migrations" / "versions"
ARQUIVOS = sorted(VERSOES.glob("*.py"))


def _campo(arquivo: Path, nome: str) -> str | None:
    m = re.search(rf'^{nome} = "(.+)"', arquivo.read_text(), re.M)
    return m.group(1) if m else None


@pytest.mark.parametrize("arquivo", ARQUIVOS, ids=lambda a: a.name)
def test_id_de_revisao_cabe_na_alembic_version(arquivo):
    # alembic_version.version_num é varchar(32): ID maior faz o upgrade falhar e reverter
    # (regressão: a 0002 "rodou" mas nunca persistiu).
    revisao = _campo(arquivo, "revision")

    assert revisao is not None and len(revisao) <= 32


def test_cadeia_de_revisoes_e_linear():
    revisoes = {_campo(a, "revision") for a in ARQUIVOS}
    anteriores = [_campo(a, "down_revision") for a in ARQUIVOS]

    assert all(r is None or r in revisoes for r in anteriores)
    assert len([r for r in anteriores if r is None]) == 1  # uma única raiz
