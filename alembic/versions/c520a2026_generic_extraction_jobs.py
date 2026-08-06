"""generic temporary extraction jobs and resumable events

Revision ID: c520a2026
Revises: b130a1790
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c520a2026"
down_revision: str | None = "b130a1790"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("documents", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_documents_expires_at"), "documents", ["expires_at"])
    columns = [
        sa.Column("output_format", sa.String(16), nullable=False, server_default="json"),
        sa.Column("ocr_mode", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("ocr_language", sa.String(32), nullable=False, server_default="por"),
        sa.Column("extract_images", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("extract_tables", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("include_coordinates", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("image_output", sa.String(16), nullable=False, server_default="reference"),
        sa.Column("processing_mode", sa.String(16), nullable=False, server_default="async"),
        sa.Column("current_stage", sa.String(64), nullable=True),
        sa.Column("warnings_json", json_type, nullable=False, server_default="[]"),
    ]
    for column in columns:
        op.add_column("extraction_jobs", column)
    op.create_table(
        "extraction_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("data_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["extraction_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_extraction_events_job_id"), "extraction_events", ["job_id"])
    op.create_index(op.f("ix_extraction_events_event_type"), "extraction_events", ["event_type"])


def downgrade() -> None:
    op.drop_index(op.f("ix_extraction_events_event_type"), table_name="extraction_events")
    op.drop_index(op.f("ix_extraction_events_job_id"), table_name="extraction_events")
    op.drop_table("extraction_events")
    for name in (
        "warnings_json", "current_stage", "processing_mode", "image_output",
        "include_coordinates", "extract_tables", "extract_images", "ocr_language",
        "ocr_mode", "output_format",
    ):
        op.drop_column("extraction_jobs", name)
    op.drop_index(op.f("ix_documents_expires_at"), table_name="documents")
    op.drop_column("documents", "expires_at")
