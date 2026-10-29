"""agente_ro enxerga tabelas futuras do schema negocio

Revision ID: 0002_agente_ro_default_privileges
Revises: 0001_agente_ro
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

revision = "0002_agente_ro_default_privileges"
down_revision = "0001_agente_ro"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GRANT ON ALL TABLES só cobre as existentes; isto cobre as criadas depois (pelo admin).
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA negocio GRANT SELECT ON TABLES TO agente_ro")


def downgrade() -> None:
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA negocio REVOKE SELECT ON TABLES FROM agente_ro")
