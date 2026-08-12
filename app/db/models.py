import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, UUIDTimestampMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Document(UUIDTimestampMixin, Base):
    __tablename__ = "documents"
    original_filename: Mapped[str] = mapped_column(String(512))
    safe_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_provider: Mapped[str] = mapped_column(String(32), default="local")
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    detected_pdf_type: Mapped[str] = mapped_column(String(16), default="unknown")
    extraction_engine: Mapped[str | None] = mapped_column(String(32))
    retain_original: Mapped[bool] = mapped_column(default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cliente_id: Mapped[str | None] = mapped_column(String(255))
    obra_id: Mapped[str | None] = mapped_column(String(255))
    jobs: Mapped[list["ExtractionJob"]] = relationship(back_populates="document")
    pages: Mapped[list["DocumentPage"]] = relationship(back_populates="document")
    result: Mapped["DocumentResult | None"] = relationship(back_populates="document", uselist=False)
    structure_jobs: Mapped[list["StructureJob"]] = relationship(back_populates="document")
    orders: Mapped[list["Order"]] = relationship(back_populates="source_document")


class ExtractionJob(UUIDTimestampMixin, Base):
    __tablename__ = "extraction_jobs"
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    engine_requested: Mapped[str] = mapped_column(String(16))
    engine_used: Mapped[str | None] = mapped_column(String(16))
    engine_selection_reason: Mapped[str | None] = mapped_column(Text)
    requested_formats: Mapped[list[str]] = mapped_column(JSONType)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    current_page: Mapped[int | None] = mapped_column(Integer)
    total_pages: Mapped[int | None] = mapped_column(Integer)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message_safe: Mapped[str | None] = mapped_column(Text)
    error_details_internal: Mapped[str | None] = mapped_column(Text)
    processing_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    engine_version: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    request_sha256: Mapped[str | None] = mapped_column(String(64))
    output_format: Mapped[str] = mapped_column(String(16), default="json")
    ocr_mode: Mapped[str] = mapped_column(String(16), default="auto")
    ocr_language: Mapped[str] = mapped_column(String(32), default="por")
    extract_images: Mapped[bool] = mapped_column(default=True)
    extract_tables: Mapped[bool] = mapped_column(default=True)
    include_coordinates: Mapped[bool] = mapped_column(default=True)
    image_output: Mapped[str] = mapped_column(String(16), default="reference")
    processing_mode: Mapped[str] = mapped_column(String(16), default="async")
    current_stage: Mapped[str | None] = mapped_column(String(64))
    warnings_json: Mapped[list[str]] = mapped_column(JSONType, default=list)
    page_selector: Mapped[str] = mapped_column(String(255), default="all")
    selected_pages_json: Mapped[list[int]] = mapped_column(JSONType, default=list)
    document: Mapped[Document] = relationship(back_populates="jobs")
    events: Mapped[list["ExtractionEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ExtractionEvent.id"
    )


class ExtractionEvent(Base):
    __tablename__ = "extraction_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_jobs.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    job: Mapped[ExtractionJob] = relationship(back_populates="events")


class DocumentResult(UUIDTimestampMixin, Base):
    __tablename__ = "document_results"
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), unique=True, index=True
    )
    plain_text: Mapped[str] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text)
    structured_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    schema_version: Mapped[str] = mapped_column(String(32), default="1.0")
    document: Mapped[Document] = relationship(back_populates="result")


class DocumentPage(UUIDTimestampMixin, Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    plain_text: Mapped[str] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text)
    blocks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONType)
    char_count: Mapped[int] = mapped_column(Integer)
    word_count: Mapped[int] = mapped_column(Integer)
    has_native_text: Mapped[bool]
    ocr_used: Mapped[bool]
    confidence: Mapped[float | None] = mapped_column(Float)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    document: Mapped[Document] = relationship(back_populates="pages")


class Customer(UUIDTimestampMixin, Base):
    __tablename__ = "customers"
    name: Mapped[str] = mapped_column(String(512))
    cpf_cnpj: Mapped[str | None] = mapped_column(String(32), index=True)
    rg_ie: Mapped[str | None] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(64), index=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(32))
    zip_code: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    normalized_name: Mapped[str | None] = mapped_column(String(512), index=True)
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Order(UUIDTimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("source_document_id", "structuring_version"),)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), index=True
    )
    order_number: Mapped[str] = mapped_column(String(128), index=True)
    order_date: Mapped[date | None] = mapped_column(Date)
    color: Mapped[str | None] = mapped_column(String(255))
    schema_version: Mapped[str] = mapped_column(String(32))
    structuring_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    customer: Mapped[Customer] = relationship(back_populates="orders")
    source_document: Mapped[Document] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.document_order"
    )


class OrderItem(UUIDTimestampMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "document_order"),
        UniqueConstraint("order_id", "normalized_code", "occurrence_number"),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), index=True
    )
    original_code: Mapped[str] = mapped_column(String(128))
    normalized_code: Mapped[str | None] = mapped_column(String(128))
    occurrence_number: Mapped[int] = mapped_column(Integer)
    document_order: Mapped[int] = mapped_column(Integer)
    product_code: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    width_mm: Mapped[int | None] = mapped_column(Integer)
    height_mm: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    environment: Mapped[str | None] = mapped_column(String(255))
    glass: Mapped[str | None] = mapped_column(String(255))
    has_subframe: Mapped[bool]
    has_trim: Mapped[bool]
    information: Mapped[str | None] = mapped_column(Text)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(32), default="approved", index=True)
    order: Mapped[Order] = relationship(back_populates="items")


class StructureJob(UUIDTimestampMixin, Base):
    __tablename__ = "structure_jobs"
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    structure_type: Mapped[str] = mapped_column(String(32), default="order")
    mode: Mapped[str] = mapped_column(String(16), default="preview")
    schema_version: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message_safe: Mapped[str | None] = mapped_column(Text)
    error_details_internal: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    processing_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    request_sha256: Mapped[str | None] = mapped_column(String(64))
    document: Mapped[Document] = relationship(back_populates="structure_jobs")
    result: Mapped["StructureResult | None"] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )


class StructureResult(UUIDTimestampMixin, Base):
    __tablename__ = "structure_results"
    structure_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("structure_jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    schema_version: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    raw_provider_response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    validated_result_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    validation_warnings_json: Mapped[list[str]] = mapped_column(JSONType)
    consistency_checks_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    persist_idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    persist_request_sha256: Mapped[str | None] = mapped_column(String(64))
    job: Mapped[StructureJob] = relationship(back_populates="result")


Index("ix_documents_status_created_at", Document.status, Document.created_at)
Index("ix_jobs_status_created_at", ExtractionJob.status, ExtractionJob.created_at)
Index("ix_structure_jobs_status_created_at", StructureJob.status, StructureJob.created_at)
