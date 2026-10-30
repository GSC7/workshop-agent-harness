"""#26: a api recebe só o que lê em runtime; o MinIO da api só enxerga o bucket de relatórios."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config import Settings

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((RAIZ / "docker-compose.yml").read_text())
POLITICA = json.loads((RAIZ / "infra" / "minio" / "politica-relatorios.json").read_text())

# Lidos só por ferramentas do host (Alembic, eval), nunca pela api no container.
SO_NO_HOST = {"DATABASE_URL", "AGENTE_RO_PASSWORD", "HARNESS_RW_PASSWORD", "LLM_MODEL_JUDGE"}
# Credenciais de admin: se aparecerem na api, o isolamento falhou.
PROIBIDAS = {
    "POSTGRES_PASSWORD",
    "AGENTE_RO_PASSWORD",
    "HARNESS_RW_PASSWORD",
    "DATABASE_URL",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
}
LANGFUSE_DA_API = {
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
    "LANGFUSE_TIMEOUT_S",
}


def _nomes_do_settings() -> set[str]:
    return {campo.upper() for campo in Settings.model_fields}


def _env_da_api() -> dict[str, Any]:
    env = COMPOSE["services"]["api"].get("environment", {})
    assert isinstance(env, dict), "environment da api em formato de mapa"
    return env


def test_api_nao_recebe_o_env_inteiro():
    assert "env_file" not in COMPOSE["services"]["api"]


def test_api_nao_recebe_credencial_de_admin():
    env = _env_da_api()

    assert PROIBIDAS.isdisjoint(env)
    assert {k for k in env if k.startswith("LANGFUSE_")} <= LANGFUSE_DA_API


def test_toda_variavel_da_api_e_lida_pelo_settings():
    orfas = set(_env_da_api()) - (_nomes_do_settings() - SO_NO_HOST - PROIBIDAS)

    assert orfas == set()


def test_valores_da_api_nao_interpolam_segredo_de_admin():
    # A URL RO pode levar a senha do papel RO (é a credencial dela), mas nada de root/admin.
    valores = " ".join(str(v) for v in _env_da_api().values())
    admin = r"\$\{?(POSTGRES_PASSWORD|MINIO_ROOT_|LANGFUSE_INIT_|LANGFUSE_SALT|LANGFUSE_ENCRYPTION)"

    assert re.search(admin, valores) is None


def test_api_espera_o_usuario_restrito_do_minio_existir():
    depende = COMPOSE["services"]["api"]["depends_on"]

    assert depende["minio-init"] == {"condition": "service_completed_successfully"}


def _recursos(acao_prefixo: str = "") -> set[str]:
    return {
        r
        for st in POLITICA["Statement"]
        for r in st["Resource"]
        if any(a.startswith(acao_prefixo) for a in st["Action"])
    }


def test_politica_do_minio_so_toca_o_bucket_de_relatorios():
    assert _recursos() == {"arn:aws:s3:::relatorios", "arn:aws:s3:::relatorios/*"}
    assert all(st["Effect"] == "Allow" for st in POLITICA["Statement"])


@pytest.mark.parametrize(
    "acao", ["s3:*", "s3:DeleteObject", "s3:DeleteBucket", "s3:CreateBucket", "admin:*"]
)
def test_politica_do_minio_nao_tem_acao_destrutiva_nem_admin(acao):
    acoes = {a for st in POLITICA["Statement"] for a in st["Action"]}

    assert acao not in acoes


def test_variavel_vazia_mantem_o_default(monkeypatch):
    # O compose repassa opcionais como ${VAR:-}: vazio não pode apagar o default do código.
    monkeypatch.setenv("LLM_MODEL_FORTE", "")
    monkeypatch.setenv("SQL_TETO_LINHAS", "")

    config = Settings(_env_file=None)  # type: ignore[call-arg]

    assert config.llm_model_forte == "gpt-4o" and config.sql_teto_linhas == 500


def _credenciais(monkeypatch, **env: str) -> tuple[str, str] | None:
    for nome in ("MINIO_API_USER", "MINIO_API_PASSWORD", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
        monkeypatch.delenv(nome, raising=False)
    for nome, valor in env.items():
        monkeypatch.setenv(nome, valor)
    par = Settings(_env_file=None).credenciais_minio()  # type: ignore[call-arg]
    return (par[0], par[1].get_secret_value()) if par else None


def test_minio_usa_o_par_da_api_quando_completo(monkeypatch):
    par = _credenciais(
        monkeypatch,
        MINIO_API_USER="agente-relatorios",
        MINIO_API_PASSWORD="senha-api",
        MINIO_ROOT_USER="root",
        MINIO_ROOT_PASSWORD="senha-root",
    )

    assert par == ("agente-relatorios", "senha-api")


def test_minio_nunca_mistura_usuario_root_com_senha_da_api(monkeypatch):
    # Regressão (revisão #26): .env do host com MINIO_API_PASSWORD (exigida pelo compose) mas
    # sem MINIO_API_USER dava minioadmin + senha da api → SignatureDoesNotMatch.
    par = _credenciais(
        monkeypatch,
        MINIO_API_PASSWORD="senha-api",
        MINIO_ROOT_USER="root",
        MINIO_ROOT_PASSWORD="senha-root",
    )

    assert par == ("root", "senha-root")


def test_minio_sem_par_completo_nao_tem_credencial(monkeypatch):
    assert _credenciais(monkeypatch, MINIO_API_USER="agente-relatorios") is None


def test_nomes_do_minio_init_casam_com_a_api_e_a_politica():
    # Usuário e bucket são literais em três lugares: api, minio-init e ARN da política.
    env = _env_da_api()
    comando = " ".join(COMPOSE["services"]["minio-init"]["command"])
    usuario, bucket = env["MINIO_API_USER"], env["MINIO_BUCKET_RELATORIOS"]

    assert f"local/{bucket}" in comando and f"user add local {usuario} " in comando
    assert f"--user {usuario}" in comando
    assert _recursos() == {f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"}
