import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class UploadResponse(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID
    status: str
    filename: str
    requested_engine: str
    requested_formats: list[str]
    status_url: str
    result_url: str


class Progress(BaseModel):
    percent: int
    current_page: int | None
    total_pages: int | None


class DocumentStatusResponse(BaseModel):
    id: uuid.UUID
    filename: str
    status: str
    detected_pdf_type: str
    engine_requested: str | None
    engine_used: str | None
    progress: Progress
    created_at: datetime
    updated_at: datetime


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    engine_requested: str
    engine_used: str | None
    engine_selection_reason: str | None
    requested_formats: list[str]
    progress_percent: int
    current_page: int | None
    total_pages: int | None
    attempt_count: int
    max_attempts: int
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    error_code: str | None
    error_message_safe: str | None
    processing_duration_ms: int | None
    engine_version: str | None
    created_at: datetime
    updated_at: datetime


class ReprocessRequest(BaseModel):
    engine: Literal["auto", "native", "marker"] = "auto"
    output_formats: list[Literal["text", "markdown", "json"]] = ["text", "markdown", "json"]


class PageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    page_number: int
    plain_text: str
    markdown: str
    blocks_json: list[dict[str, Any]]
    char_count: int
    word_count: int
    has_native_text: bool
    ocr_used: bool
    confidence: float | None
    width: float | None
    height: float | None


class PaginatedPages(BaseModel):
    items: list[PageResponse]
    page: int
    page_size: int
    total: int


class DocumentListItem(BaseModel):
    id: uuid.UUID
    filename: str
    status: str
    page_count: int | None
    created_at: datetime


class PaginatedDocuments(BaseModel):
    items: list[DocumentListItem]
    page: int
    page_size: int
    total: int


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
