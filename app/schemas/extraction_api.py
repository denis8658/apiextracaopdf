import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtractionAccepted(BaseModel):
    job_id: uuid.UUID
    status: str
    events_url: str
    status_url: str
    result_url: str
    expires_at: datetime


class ExtractionStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    progress: int
    current_page: int | None
    total_pages: int | None
    current_stage: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    warnings: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class PublicBBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class PublicBlock(BaseModel):
    id: str
    type: str
    content: str | None
    source: Literal["native", "ocr", "image", "table", "metadata", "hybrid"]
    confidence: float | None
    reading_order: int | None
    bbox: PublicBBox | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublicTable(BaseModel):
    id: str
    page_number: int
    bbox: PublicBBox | None
    headers: list[str]
    rows: list[list[str]]
    columns: list[str]
    cells: list[dict[str, Any]]
    markdown: str
    original_text: str
    source: Literal["native", "ocr"]
    confidence: float | None
    method: str


class PublicImage(BaseModel):
    id: str
    page_number: int
    index: int
    type: str
    format: str
    width: int
    height: int
    bbox: PublicBBox | None
    hash: str
    reference: str | None
    nearby_text: str | None
    related_code: str | None
    related_description: str | None
    association_confidence: float | None


class PublicPage(BaseModel):
    page_number: int
    width: float | None
    height: float | None
    rotation: int
    extraction_method: str
    plain_text: str
    markdown: str
    blocks: list[PublicBlock]
    tables: list[PublicTable]
    images: list[PublicImage]
    warnings: list[str]


class PublicDocumentInfo(BaseModel):
    filename: str
    mime_type: str
    page_count: int
    file_size: int
    document_hash: str
    language: str
    title: str | None
    author: str | None
    created_at: str | None


class PublicProcessingInfo(BaseModel):
    job_id: uuid.UUID
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    native_pages: int
    ocr_pages: int
    hybrid_pages: int
    warnings: list[str]


class PublicStatistics(BaseModel):
    characters: int
    words: int
    tables: int
    images: int
    ocr_blocks: int


class PublicExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    document: PublicDocumentInfo
    processing: PublicProcessingInfo
    pages: list[PublicPage]
    statistics: PublicStatistics
