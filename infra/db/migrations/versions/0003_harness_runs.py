"""schema harness: tabela runs + papel harness_rw (escreve só ali)

Revision ID: 0003_harness_runs
Revises: 0002_agente_ro_default_privs
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from app.config import settings

revision = "0003_harness_runs"
down_revision = "0002_agente_ro_default_privs"
branch_labels = None
depends_on = None


def _literal(valor: str) -> str:
    # DDL de papel não aceita bind param; escapa como literal SQL.
    return "'" + valor.replace("'", "''") + "'"


def upgrade() -> None:
    if settings.harness_rw_password is None:
        raise RuntimeError("HARNESS_RW_PASSWORD não definida no .env")
    senha = _literal(settings.harness_rw_password.get_secret_value())

    op.execute("CREATE SCHEMA IF NOT EXISTS harness")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS harness.runs (
            id            uuid        PRIMARY KEY,
            criado_em     timestamptz NOT NULL DEFAULT now(),
            pergunta      text        NOT NULL,
            status        text        NOT NULL CHECK (status IN ('ok', 'erro', 'cancelado')),
            plano         jsonb,
            fracos        jsonb       NOT NULL DEFAULT '[]',
            sqls          jsonb       NOT NULL DEFAULT '[]',
            fontes        jsonb       NOT NULL DEFAULT '[]',
            relatorio     text,
            relatorio_uri text,
            erro          text,
            duracao_ms    integer     NOT NULL,
            modelos       jsonb       NOT NULL DEFAULT '{}'
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_runs_criado_em ON harness.runs (criado_em DESC)")

    # Escrita do app vai por um papel próprio: o agente_ro segue read-only no banco.
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'harness_rw') THEN "
        "CREATE ROLE harness_rw LOGIN; END IF; END $$"
    )
    op.execute(f"ALTER ROLE harness_rw WITH LOGIN PASSWORD {senha}")
    op.execute(
        "DO $$ BEGIN "
        "EXECUTE format('GRANT CONNECT ON DATABASE %I TO harness_rw', current_database()); END $$"
    )
    op.execute("GRANT USAGE ON SCHEMA harness TO harness_rw")
    # SELECT p/ inspeção de runs/histórico (analista, #9); sem UPDATE/DELETE: rastro imutável.
    op.execute("GRANT SELECT, INSERT ON harness.runs TO harness_rw")


def downgrade() -> None:
    op.execute("REVOKE ALL ON harness.runs FROM harness_rw")
    op.execute("REVOKE USAGE ON SCHEMA harness FROM harness_rw")
    op.execute(
        "DO $$ BEGIN "
        "EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM harness_rw', current_database()); END $$"
    )
    op.execute("DROP ROLE IF EXISTS harness_rw")
    op.execute("DROP TABLE IF EXISTS harness.runs")
    op.execute("DROP SCHEMA IF EXISTS harness")
