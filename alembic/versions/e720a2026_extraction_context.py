"""customer and project context for PDF extraction

Revision ID: e720a2026
Revises: d610a2026
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e720a2026"
down_revision: str | None = "d610a2026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("cliente_id", sa.String(255), nullable=True))
    op.add_column("documents", sa.Column("obra_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "obra_id")
    op.drop_column("documents", "cliente_id")
