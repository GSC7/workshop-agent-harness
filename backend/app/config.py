from __future__ import annotations

from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Config única do app; no container o compose injeta os hosts internos."""

    # Vazio = não definido: o compose repassa opcionais como ${VAR:-} sem apagar os defaults.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Conexão do agente ao schema `negocio`: papel somente-leitura `agente_ro`.
    database_url_ro: SecretStr | None = None
    # Conexão admin (RW) e senha do papel RO: só p/ host tools (migrations), nunca no runtime.
    database_url: SecretStr | None = None
    agente_ro_password: SecretStr | None = None
    # Persistência de runs (schema harness) por papel próprio, só com INSERT/SELECT em runs.
    database_url_harness: SecretStr | None = None
    harness_rw_password: SecretStr | None = None

    # LLMs dos nós, agnósticos de provedor. Forte redige o relatório; rápido extrai a intenção.
    # Defaults = os do workshop original (upstream); o .env sobrescreve p/ trocar de modelo.
    llm_provider: Literal["openai", "anthropic"] = "openai"
    llm_model_forte: str | None = "gpt-4o"
    llm_model_rapido: str | None = "gpt-4o-mini"
    # Esforço de raciocínio opcional (OpenAI: reasoning_effort; Anthropic: effort).
    llm_esforco_forte: Literal["low", "medium", "high"] | None = None
    llm_esforco_rapido: Literal["low", "medium", "high"] | None = None
    # Judge do gate de avaliação (ADR 0011); ausente = mesmo modelo forte do relatório.
    llm_model_judge: str | None = None
    # Maioria de k julgamentos por caso (#27, ADR 0011): ímpar; 1 = judge único.
    eval_judge_votos: int = 3
    anthropic_api_key: SecretStr | None = None

    # Perna quantitativa (ADR 0006/0007): janela pelo último mês com dados; 27 meses cobrem os
    # recentes + os mesmos meses dos 2 anos anteriores (sazonalidade).
    kpi_meses_janela: int = 27
    kpi_meses_recentes: int = 3
    kpi_meses_serie: int = 6
    kpi_limiar_fraco: float = 0.10
    kpi_limiar_yoy: float = 0.10
    kpi_max_fracos: int = 3
    sql_timeout_kpi_ms: int = 15000

    qdrant_url: str = "http://localhost:6333"
    colecoes_qdrant: tuple[str, ...] = ("camada_semantica", "diagnostico", "prescricao")

    minio_endpoint: str = "localhost:9000"
    # Usuário da api, restrito ao bucket de relatórios (#26); o container recebe só MINIO_API_*.
    minio_api_user: str | None = None
    minio_api_password: SecretStr | None = None
    # Root: só para a api rodando no host sem MINIO_API_* (o seed usa o root por conta própria).
    minio_root_user: str | None = None
    minio_root_password: SecretStr | None = None
    minio_secure: bool = False
    minio_bucket_relatorios: str = "relatorios"

    health_timeout_s: float = 3.0

    # Observabilidade (#11, ADR 0014): sem as duas chaves, os runs seguem sem trace.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "http://localhost:3000"
    langfuse_timeout_s: int = 5

    # Embedding da *query* em runtime (as coleções foram indexadas com este modelo).
    openai_api_key: SecretStr | None = None
    embed_model: str = "text-embedding-3-large"
    embed_dim: int = 3072
    search_limite: int = 5

    # Guardrails do run_sql (LIMIT garantido e statement_timeout no banco).
    sql_teto_linhas: int = 500
    sql_timeout_ms: int = 5000

    @field_validator("eval_judge_votos")
    @classmethod
    def _votos_impar(cls, v: int) -> int:
        if v < 1 or v % 2 == 0:
            raise ValueError("EVAL_JUDGE_VOTOS deve ser ímpar e positivo (maioria sem empate)")
        return v

    def credenciais_minio(self) -> tuple[str, SecretStr] | None:
        """Par coerente: o da api se completo, senão o root inteiro — nunca usuário de um com a
        senha do outro (cada variável resolvida sozinha misturava os dois no host)."""
        if self.minio_api_user and self.minio_api_password:
            return self.minio_api_user, self.minio_api_password
        if self.minio_root_user and self.minio_root_password:
            return self.minio_root_user, self.minio_root_password
        return None


settings = Settings()
