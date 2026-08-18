"""optional Base44 persistence state

Revision ID: fa40a2026
Revises: f830a2026
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "fa40a2026"
down_revision: str | None = "f830a2026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extraction_jobs",
        sa.Column("save_to_base44", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("extraction_jobs", sa.Column("oportunidade_id", sa.String(255)))
    op.add_column("extraction_jobs", sa.Column("vendedor_id", sa.String(255)))
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "persistence_status",
            sa.String(32),
            nullable=False,
            server_default="not_requested",
        ),
    )
    op.add_column("extraction_jobs", sa.Column("persistence_payload_hash", sa.String(64)))
    op.add_column("extraction_jobs", sa.Column("persistence_result_json", sa.JSON()))
    op.add_column("extraction_jobs", sa.Column("base44_context_json", sa.JSON()))


def downgrade() -> None:
    op.drop_column("extraction_jobs", "base44_context_json")
    op.drop_column("extraction_jobs", "persistence_result_json")
    op.drop_column("extraction_jobs", "persistence_payload_hash")
    op.drop_column("extraction_jobs", "persistence_status")
    op.drop_column("extraction_jobs", "vendedor_id")
    op.drop_column("extraction_jobs", "oportunidade_id")
    op.drop_column("extraction_jobs", "save_to_base44")
