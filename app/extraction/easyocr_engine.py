import asyncio
from pathlib import Path
from typing import Any

from app.schemas.extraction import (
    ExtractedBlock,
    ExtractedPage,
    ExtractionOptions,
    ExtractionResult,
)

LANGUAGE_MAP = {
    "por": "pt",
    "pt": "pt",
    "pt-br": "pt",
    "eng": "en",
    "en": "en",
    "spa": "es",
    "es": "es",
}


class EasyOCRExtractionEngine:
    name = "easyocr"

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self._readers: dict[str, Any] = {}

    async def extract(self, file_path: Path, options: ExtractionOptions) -> ExtractionResult:
        return await asyncio.to_thread(self._extract_sync, file_path, options)

    def _reader(self, language: str):
        import easyocr

        code = LANGUAGE_MAP.get(language.lower(), language.lower())
        if code not in self._readers:
            self.model_path.mkdir(parents=True, exist_ok=True)
            self._readers[code] = easyocr.Reader(
                [code],
                gpu=False,
                model_storage_directory=str(self.model_path.resolve()),
                download_enabled=True,
                verbose=False,
            )
        return self._readers[code], code

    def _extract_sync(self, file_path: Path, options: ExtractionOptions) -> ExtractionResult:
        import numpy as np
        import pymupdf

        reader, language = self._reader(options.ocr_language)
        document = pymupdf.open(file_path)
        pages: list[ExtractedPage] = []
        try:
            scale = options.ocr_dpi / 72
            for page_index in range(document.page_count):
                page = document[page_index]
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                    pixmap.height, pixmap.width, pixmap.n
                )
                recognized = reader.readtext(
                    image,
                    detail=1,
                    paragraph=False,
                    rotation_info=[90, 180, 270],
                    batch_size=1,
                    workers=0,
                )
                ordered = sorted(recognized, key=self._reading_key)
                blocks: list[ExtractedBlock] = []
                texts: list[str] = []
                confidences: list[float] = []
                for index, (polygon, text, confidence) in enumerate(ordered, 1):
                    content = str(text).strip()
                    if not content:
                        continue
                    score = max(0.0, min(1.0, float(confidence)))
                    bbox = self._bbox(polygon, scale) if options.include_coordinates else None
                    blocks.append(
                        ExtractedBlock(
                            block_id=f"p{page_index + 1}-ocr-{index}",
                            block_type="text",
                            page_number=page_index + 1,
                            text=content,
                            bbox=bbox,
                            source="ocr",
                            confidence=score,
                            reading_order=len(blocks) + 1,
                            metadata={"provider": self.name, "language": language},
                        )
                    )
                    texts.append(content)
                    confidences.append(score)
                plain_text = "\n".join(texts)
                warnings = [] if texts else ["OCR executado, mas nenhum texto foi reconhecido."]
                pages.append(
                    ExtractedPage(
                        page_number=page_index + 1,
                        plain_text=plain_text,
                        markdown=plain_text,
                        blocks=blocks,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        rotation=int(page.rotation),
                        extraction_method="ocr",
                        has_native_text=False,
                        ocr_used=True,
                        confidence=(sum(confidences) / len(confidences) if confidences else None),
                        warnings=warnings,
                    )
                )
        finally:
            document.close()
        return ExtractionResult(
            plain_text="",
            markdown="",
            pages=pages,
            metadata={"ocr_provider": self.name, "ocr_language": language},
            engine=self.name,
        )

    @staticmethod
    def _reading_key(item) -> tuple[float, float]:
        polygon = item[0]
        return (
            min(float(point[1]) for point in polygon),
            min(float(point[0]) for point in polygon),
        )

    @staticmethod
    def _bbox(polygon, scale: float) -> list[float]:
        xs = [float(point[0]) / scale for point in polygon]
        ys = [float(point[1]) / scale for point in polygon]
        return [min(xs), min(ys), max(xs), max(ys)]
