"""order structuring schema without commercial terms

Revision ID: b130a1790
Revises: 79ea5131058b
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b130a1790"
down_revision: str | None = "79ea5131058b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("cpf_cnpj", sa.String(32), nullable=True),
        sa.Column("rg_ie", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("city", sa.String(255), nullable=True),
        sa.Column("state", sa.String(32), nullable=True),
        sa.Column("zip_code", sa.String(32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("normalized_name", sa.String(512), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
    )
    for column in ("cpf_cnpj", "phone", "email", "normalized_name"):
        op.create_index(op.f(f"ix_customers_{column}"), "customers", [column])

    op.create_table(
        "orders",
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("order_number", sa.String(128), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=True),
        sa.Column("color", sa.String(255), nullable=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("structuring_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], ondelete="RESTRICT", name=op.f("fk_orders_customer_id_customers")
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["documents.id"], ondelete="RESTRICT", name=op.f("fk_orders_source_document_id_documents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("source_document_id", "structuring_version", name=op.f("uq_orders_source_document_id")),
    )
    for column in ("customer_id", "source_document_id", "order_number", "status", "deleted_at"):
        op.create_index(op.f(f"ix_orders_{column}"), "orders", [column])

    op.create_table(
        "structure_jobs",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("structure_type", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message_safe", sa.Text(), nullable=True),
        sa.Column("error_details_internal", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("processing_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("request_sha256", sa.String(64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE", name=op.f("fk_structure_jobs_document_id_documents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_structure_jobs")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_structure_jobs_idempotency_key")),
    )
    op.create_index(op.f("ix_structure_jobs_document_id"), "structure_jobs", ["document_id"])
    op.create_index(op.f("ix_structure_jobs_status"), "structure_jobs", ["status"])
    op.create_index("ix_structure_jobs_status_created_at", "structure_jobs", ["status", "created_at"])

    op.create_table(
        "order_items",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("original_code", sa.String(128), nullable=False),
        sa.Column("normalized_code", sa.String(128), nullable=True),
        sa.Column("occurrence_number", sa.Integer(), nullable=False),
        sa.Column("document_order", sa.Integer(), nullable=False),
        sa.Column("product_code", sa.String(128), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("width_mm", sa.Integer(), nullable=True),
        sa.Column("height_mm", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("environment", sa.String(255), nullable=True),
        sa.Column("glass", sa.String(255), nullable=True),
        sa.Column("has_subframe", sa.Boolean(), nullable=False),
        sa.Column("has_trim", sa.Boolean(), nullable=False),
        sa.Column("information", sa.Text(), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE", name=op.f("fk_order_items_order_id_orders")),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="RESTRICT", name=op.f("fk_order_items_source_document_id_documents")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
        sa.UniqueConstraint("order_id", "document_order", name=op.f("uq_order_items_order_id")),
        sa.UniqueConstraint("order_id", "normalized_code", "occurrence_number", name="uq_order_items_code_occurrence"),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"])
    op.create_index(op.f("ix_order_items_source_document_id"), "order_items", ["source_document_id"])
    op.create_index(op.f("ix_order_items_review_status"), "order_items", ["review_status"])

    op.create_table(
        "structure_results",
        sa.Column("structure_job_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("raw_provider_response_json", json_type, nullable=True),
        sa.Column("validated_result_json", json_type, nullable=False),
        sa.Column("validation_warnings_json", json_type, nullable=False),
        sa.Column("consistency_checks_json", json_type, nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("persist_idempotency_key", sa.String(255), nullable=True),
        sa.Column("persist_request_sha256", sa.String(64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["structure_job_id"], ["structure_jobs.id"], ondelete="CASCADE", name=op.f("fk_structure_results_structure_job_id_structure_jobs")),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE", name=op.f("fk_structure_results_document_id_documents")),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL", name=op.f("fk_structure_results_customer_id_customers")),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL", name=op.f("fk_structure_results_order_id_orders")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_structure_results")),
        sa.UniqueConstraint("structure_job_id", name=op.f("uq_structure_results_structure_job_id")),
        sa.UniqueConstraint("persist_idempotency_key", name=op.f("uq_structure_results_persist_idempotency_key")),
    )
    for column in ("structure_job_id", "document_id", "customer_id", "order_id"):
        op.create_index(op.f(f"ix_structure_results_{column}"), "structure_results", [column], unique=column == "structure_job_id")


def downgrade() -> None:
    op.drop_table("structure_results")
    op.drop_table("order_items")
    op.drop_table("structure_jobs")
    op.drop_table("orders")
    op.drop_table("customers")
