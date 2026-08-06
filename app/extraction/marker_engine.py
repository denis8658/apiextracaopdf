import asyncio
from html.parser import HTMLParser
from importlib.metadata import version
from pathlib import Path
from typing import Any

from app.schemas.extraction import (
    ExtractedBlock,
    ExtractedPage,
    ExtractionOptions,
    ExtractionResult,
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return "\n".join(parser.parts)


class MarkerExtractionEngine:
    name = "marker"

    async def extract(self, file_path: Path, options: ExtractionOptions) -> ExtractionResult:
        return await asyncio.to_thread(self._extract_sync, file_path, options)

    def _extract_sync(self, file_path: Path, options: ExtractionOptions) -> ExtractionResult:
        from marker.config.parser import ConfigParser
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict

        parser = ConfigParser(
            {
                "output_format": "json",
                "mode": "balanced",
                "force_ocr": True,
                "ocr_full_page": True,
                "highres_image_dpi": options.ocr_dpi,
            }
        )
        converter = PdfConverter(
            config=parser.generate_config_dict(),
            artifact_dict=create_model_dict(),
            processor_list=parser.get_processors(),
            renderer=parser.get_renderer(),
            llm_service=parser.get_llm_service(),
        )
        rendered = converter(str(file_path))
        raw = rendered.model_dump(mode="json")
        raw_pages = raw.get("children", [])
        pages: list[ExtractedPage] = []
        for index, raw_page in enumerate(raw_pages):
            page_number = index + 1
            blocks: list[ExtractedBlock] = []
            texts: list[str] = []
            fragments: list[str] = []
            self._flatten(raw_page, page_number, blocks, texts, fragments)
            text = "\n".join(item for item in texts if item.strip())
            try:
                from markdownify import markdownify

                markdown = markdownify("\n".join(fragments))
            except ImportError:
                markdown = text
            bbox = raw_page.get("polygon") or raw_page.get("bbox")
            width, height = self._dimensions(bbox)
            pages.append(
                ExtractedPage(
                    page_number=page_number,
                    plain_text=text,
                    markdown=markdown,
                    blocks=blocks,
                    width=width,
                    height=height,
                    has_native_text=False,
                    ocr_used=True,
                )
            )
        return ExtractionResult(
            plain_text="",
            markdown="",
            pages=pages,
            metadata={
                "marker_metadata": raw.get("metadata", {}),
                "images": [],
                "ocr_language_requested": options.ocr_language,
                "ocr_dpi": options.ocr_dpi,
            },
            engine=self.name,
            engine_version=version("marker-pdf"),
        )

    def _flatten(
        self,
        node: dict[str, Any],
        page_number: int,
        blocks: list[ExtractedBlock],
        texts: list[str],
        fragments: list[str],
    ) -> None:
        children = node.get("children") or []
        text = node.get("html") or node.get("text") or ""
        if not children and isinstance(text, str) and text.strip():
            texts.append(html_to_text(text))
            fragments.append(text)
        block_id = str(node.get("id") or f"p{page_number}-b{len(blocks) + 1}")
        if node.get("block_type") not in {"Document", "Page"}:
            blocks.append(
                ExtractedBlock(
                    block_id=block_id,
                    block_type=str(node.get("block_type", "unknown")),
                    page_number=page_number,
                    text=node.get("text"),
                    html=node.get("html"),
                    bbox=self._bbox(node.get("polygon") or node.get("bbox")),
                    source="ocr",
                    confidence=self._confidence(node),
                    reading_order=len(blocks) + 1,
                    metadata=node.get("metadata") or {},
                )
            )
        for child in children:
            self._flatten(child, page_number, blocks, texts, fragments)

    @staticmethod
    def _confidence(node: dict[str, Any]) -> float | None:
        metadata = node.get("metadata") or {}
        value = metadata.get("confidence") or metadata.get("ocr_confidence")
        if value is None:
            return None
        try:
            number = float(value)
            return max(0.0, min(1.0, number / 100 if number > 1 else number))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bbox(value: Any) -> list[float] | None:
        if (
            isinstance(value, list)
            and len(value) == 4
            and all(isinstance(v, (int, float)) for v in value)
        ):
            return [float(v) for v in value]
        if isinstance(value, dict):
            value = value.get("bbox")
            if isinstance(value, list) and len(value) == 4:
                return [float(v) for v in value]
        return None

    @classmethod
    def _dimensions(cls, value: Any) -> tuple[float | None, float | None]:
        bbox = cls._bbox(value)
        return (bbox[2] - bbox[0], bbox[3] - bbox[1]) if bbox else (None, None)
