"""papel somente-leitura agente_ro sobre o schema negocio

Revision ID: 0001_agente_ro
Revises:
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from app.config import settings

revision = "0001_agente_ro"
down_revision = None
branch_labels = None
depends_on = None


def _literal(valor: str) -> str:
    # DDL de papel não aceita bind param; escapa como literal SQL.
    return "'" + valor.replace("'", "''") + "'"


def upgrade() -> None:
    if settings.agente_ro_password is None:
        raise RuntimeError("AGENTE_RO_PASSWORD não definida no .env")
    senha = _literal(settings.agente_ro_password.get_secret_value())

    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agente_ro') THEN "
        "CREATE ROLE agente_ro LOGIN; END IF; END $$"
    )
    op.execute(f"ALTER ROLE agente_ro WITH LOGIN PASSWORD {senha}")
    # Defesa no banco: mesmo que um guardrail da app falhe, a sessão é read-only.
    op.execute("ALTER ROLE agente_ro SET default_transaction_read_only = on")
    op.execute(
        "DO $$ BEGIN "
        "EXECUTE format('GRANT CONNECT ON DATABASE %I TO agente_ro', current_database()); END $$"
    )
    op.execute("GRANT USAGE ON SCHEMA negocio TO agente_ro")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA negocio TO agente_ro")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA negocio FROM agente_ro")
    op.execute("REVOKE USAGE ON SCHEMA negocio FROM agente_ro")
    op.execute(
        "DO $$ BEGIN "
        "EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM agente_ro', current_database()); END $$"
    )
    op.execute("DROP ROLE IF EXISTS agente_ro")
