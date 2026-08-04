import asyncio
from importlib.metadata import version
from pathlib import Path
from typing import Any

from app.schemas.extraction import (
    ExtractedBlock,
    ExtractedPage,
    ExtractionOptions,
    ExtractionResult,
)


class PyMuPDFExtractionEngine:
    name = "native"

    async def extract(self, file_path: Path, options: ExtractionOptions) -> ExtractionResult:
        return await asyncio.to_thread(self._extract_sync, file_path)

    def _extract_sync(self, file_path: Path) -> ExtractionResult:
        import pymupdf

        document = pymupdf.open(file_path)
        try:
            pages: list[ExtractedPage] = []
            char_counts: list[int] = []
            for page_index in range(document.page_count):
                page = document[page_index]
                page_number = page_index + 1
                text = page.get_text("text", sort=True)
                blocks: list[ExtractedBlock] = []
                for block_index, block in enumerate(page.get_text("blocks", sort=True)):
                    x0, y0, x1, y1, block_text, *_rest = block
                    if not str(block_text).strip():
                        continue
                    blocks.append(
                        ExtractedBlock(
                            block_id=f"p{page_number}-b{block_index + 1}",
                            block_type="text",
                            page_number=page_number,
                            text=str(block_text),
                            bbox=[float(x0), float(y0), float(x1), float(y1)],
                        )
                    )
                count = len(text.strip())
                char_counts.append(count)
                pages.append(
                    ExtractedPage(
                        page_number=page_number,
                        plain_text=text,
                        markdown=text,
                        blocks=blocks,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        has_native_text=count > 0,
                        ocr_used=False,
                    )
                )
            metadata: dict[str, Any] = dict(document.metadata or {})
            metadata.update({"page_count": document.page_count, "native_char_counts": char_counts})
            return ExtractionResult(
                plain_text="",
                markdown="",
                pages=pages,
                metadata=metadata,
                engine=self.name,
                engine_version=version("PyMuPDF"),
            )
        finally:
            document.close()
