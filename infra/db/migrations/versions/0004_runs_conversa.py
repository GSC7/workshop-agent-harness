"""harness.runs: conversa_id (multi-turno) + recomendações/sazonais/premissas/avisos

Revision ID: 0004_runs_conversa
Revises: 0003_harness_runs
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

revision = "0004_runs_conversa"
down_revision = "0003_harness_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Memória da conversa = os runs dela (#7); o resto fecha a auditoria do porquê (#5/#6).
    op.execute(
        """
        ALTER TABLE harness.runs
            ADD COLUMN IF NOT EXISTS conversa_id   uuid,
            ADD COLUMN IF NOT EXISTS recomendacoes jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN IF NOT EXISTS sazonais      jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN IF NOT EXISTS premissas     jsonb,
            ADD COLUMN IF NOT EXISTS avisos        jsonb NOT NULL DEFAULT '[]'
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_runs_conversa ON harness.runs (conversa_id, criado_em)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS harness.ix_runs_conversa")
    op.execute(
        """
        ALTER TABLE harness.runs
            DROP COLUMN IF EXISTS avisos,
            DROP COLUMN IF EXISTS premissas,
            DROP COLUMN IF EXISTS sazonais,
            DROP COLUMN IF EXISTS recomendacoes,
            DROP COLUMN IF EXISTS conversa_id
        """
    )
