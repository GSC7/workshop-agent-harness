"""harness.runs: trace_id (trace do run no Langfuse)

Revision ID: 0005_runs_trace_id
Revises: 0004_runs_conversa
Create Date: 2026-10-29
"""

from __future__ import annotations

from alembic import op

revision = "0005_runs_trace_id"
down_revision = "0004_runs_conversa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Id DESTINADO do trace (derivado do run_id): se o export falhar, o trace não existe no
    # Langfuse. Nulo = run sem observabilidade (sem chaves ou falha ao iniciar o trace, #11).
    op.execute("ALTER TABLE harness.runs ADD COLUMN IF NOT EXISTS trace_id text")


def downgrade() -> None:
    op.execute("ALTER TABLE harness.runs DROP COLUMN IF EXISTS trace_id")
