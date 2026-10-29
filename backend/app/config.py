from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Config única do app; no container o compose injeta os hosts internos."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Conexão do agente ao schema `negocio`: papel somente-leitura `agente_ro`.
    database_url_ro: SecretStr | None = None
    # Conexão admin (RW) e senha do papel RO: só p/ host tools (migrations), nunca no runtime.
    database_url: SecretStr | None = None
    agente_ro_password: SecretStr | None = None

    qdrant_url: str = "http://localhost:6333"
    colecoes_qdrant: tuple[str, ...] = ("camada_semantica", "diagnostico", "prescricao")

    minio_endpoint: str = "localhost:9000"
    minio_root_user: str | None = None
    minio_root_password: SecretStr | None = None
    minio_secure: bool = False
    minio_bucket_relatorios: str = "relatorios"

    health_timeout_s: float = 3.0


settings = Settings()
