from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class ExtractionOptions(BaseModel):
    engine: Literal["auto", "native", "marker"] = "auto"
    output_formats: list[Literal["text", "markdown", "json"]] = ["text", "markdown", "json"]


class ExtractedBlock(BaseModel):
    block_id: str
    block_type: str
    page_number: int
    text: str | None = None
    html: str | None = None
    bbox: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedPage(BaseModel):
    page_number: int
    plain_text: str
    markdown: str
    blocks: list[ExtractedBlock]
    width: float | None = None
    height: float | None = None
    has_native_text: bool
    ocr_used: bool
    confidence: float | None = None


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
