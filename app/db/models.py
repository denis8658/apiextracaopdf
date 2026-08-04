import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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

from app.db.base import Base, UUIDTimestampMixin

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
    jobs: Mapped[list["ExtractionJob"]] = relationship(back_populates="document")
    pages: Mapped[list["DocumentPage"]] = relationship(back_populates="document")
    result: Mapped["DocumentResult | None"] = relationship(back_populates="document", uselist=False)


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
    document: Mapped[Document] = relationship(back_populates="jobs")


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


Index("ix_documents_status_created_at", Document.status, Document.created_at)
Index("ix_jobs_status_created_at", ExtractionJob.status, ExtractionJob.created_at)
