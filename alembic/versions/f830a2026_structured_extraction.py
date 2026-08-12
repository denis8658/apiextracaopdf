"""structured output option for extraction jobs

Revision ID: f830a2026
Revises: e720a2026
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f830a2026"
down_revision: str | None = "e720a2026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extraction_jobs",
        sa.Column("structure_output", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("extraction_jobs", "structure_output")
