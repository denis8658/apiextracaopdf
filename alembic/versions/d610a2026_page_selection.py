"""page selection for extraction jobs

Revision ID: d610a2026
Revises: c520a2026
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d610a2026"
down_revision: str | None = "c520a2026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "extraction_jobs",
        sa.Column("page_selector", sa.String(255), nullable=False, server_default="all"),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("selected_pages_json", json_type, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("extraction_jobs", "selected_pages_json")
    op.drop_column("extraction_jobs", "page_selector")
