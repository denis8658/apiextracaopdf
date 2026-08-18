from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


def default_output_formats() -> list[Literal["text", "markdown", "json"]]:
    return ["text", "markdown", "json"]


class ExtractionOptions(BaseModel):
    engine: Literal["auto", "native", "marker"] = "auto"
    output_formats: list[Literal["text", "markdown", "json"]] = Field(
        default_factory=default_output_formats
    )
    output_format: Literal["text", "markdown", "json"] = "json"
    ocr_mode: Literal["auto", "always", "never"] = "auto"
    ocr_language: str = "por"
    ocr_dpi: int = Field(144, ge=72, le=600)
    extract_images: bool = True
    extract_tables: bool = True
    include_coordinates: bool = True
    ignore_decorative_images: bool = True
    image_output: Literal["reference", "base64", "both", "metadata"] = "reference"
    pages: str = "all"
    selected_pages: list[int] = Field(default_factory=list)


class ExtractedBlock(BaseModel):
    block_id: str
    block_type: str
    page_number: int
    text: str | None = None
    html: str | None = None
    bbox: list[float] | None = None
    source: Literal["native", "ocr", "image", "table", "metadata", "hybrid"] = "native"
    confidence: float | None = Field(default=None, ge=0, le=1)
    reading_order: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedTable(BaseModel):
    table_id: str
    page_number: int
    bbox: list[float] | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    cells: list[dict[str, Any]] = Field(default_factory=list)
    markdown: str = ""
    original_text: str = ""
    source: Literal["native", "ocr"] = "native"
    confidence: float | None = Field(default=None, ge=0, le=1)
    method: str = "pymupdf"


class AssociationCandidate(BaseModel):
    item_id: str
    score: float = Field(ge=0, le=1)


class ExtractedImage(BaseModel):
    image_id: str
    page_number: int
    index: int
    image_type: Literal[
        "technical_drawing", "profile", "diagram", "table_image", "logo", "photo", "icon", "unknown"
    ] = "unknown"
    format: str
    width: int
    height: int
    bbox: list[float] | None = None
    sha256: str
    visual_group_id: str | None = None
    reference: str | None = None
    mime_type: str | None = None
    content_encoding: Literal["base64"] | None = None
    content_base64: str | None = None
    nearby_text: str | None = None
    related_item_id: str | None = None
    related_code: str | None = None
    related_description: str | None = None
    association_confidence: float | None = Field(default=None, ge=0, le=1)
    association_method: Literal[
        "layout_region",
        "spatial_proximity",
        "same_column",
        "caption_match",
        "code_proximity",
        "combined",
        "unresolved",
    ] = "unresolved"
    requires_review: bool = True
    association_candidates: list[AssociationCandidate] = Field(default_factory=list)
    source: Literal["image"] = "image"
    raw_bytes: bytes | None = Field(default=None, exclude=True, repr=False)

class ExtractedItem(BaseModel):
    item_id: str
    page_number: int
    code: str | None = None
    description: str | None = None
    bbox: list[float] | None = None
    code_block_id: str | None = None
    description_block_id: str | None = None
    text_block_ids: list[str] = Field(default_factory=list)
    image_ids: list[str] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list)
    association_confidence: float = Field(ge=0, le=1)
    requires_review: bool = False


class ExtractedPage(BaseModel):
    page_number: int
    plain_text: str
    markdown: str
    blocks: list[ExtractedBlock]
    tables: list[ExtractedTable] = Field(default_factory=list)
    images: list[ExtractedImage] = Field(default_factory=list)
    items: list[ExtractedItem] = Field(default_factory=list)
    width: float | None = None
    height: float | None = None
    rotation: int = 0
    extraction_method: Literal["native", "ocr", "hybrid", "failed"] = "native"
    has_native_text: bool
    ocr_used: bool
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    plain_text: str
    markdown: str
    pages: list[ExtractedPage]
    metadata: dict[str, Any] = Field(default_factory=dict)
    engine: str
    engine_version: str | None = None


class ExtractionEngine(Protocol):
    async def extract(self, file_path: "Path", options: ExtractionOptions) -> ExtractionResult: ...


from pathlib import Path  # noqa: E402
