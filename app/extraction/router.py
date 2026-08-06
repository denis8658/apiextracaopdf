import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.extraction.marker_engine import MarkerExtractionEngine
from app.extraction.pymupdf_engine import PyMuPDFExtractionEngine
from app.schemas.extraction import ExtractionOptions, ExtractionResult


@dataclass
class RoutedResult:
    result: ExtractionResult
    reason: str
    detected_pdf_type: str


class ExtractionRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.native = PyMuPDFExtractionEngine(settings.max_images_per_document)
        self.marker = MarkerExtractionEngine()

    async def extract(
        self,
        path: Path,
        options: ExtractionOptions,
        progress: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> RoutedResult:
        native = await self.native.extract(path, options)
        detected = self._classify(native)
        mode = (
            "never"
            if options.engine == "native"
            else "always"
            if options.engine == "marker"
            else options.ocr_mode
        )
        targets = list(range(len(native.pages))) if mode == "always" else []
        if mode == "auto":
            targets = [
                index for index in range(len(native.pages)) if self._needs_ocr(native, index)
            ]
        if mode == "never" or not targets:
            return RoutedResult(native, "OCR desativado ou texto nativo suficiente", detected)

        semaphore = asyncio.Semaphore(max(1, self.settings.ocr_max_concurrency))

        async def replace(index: int) -> None:
            async with semaphore:
                page_number = index + 1
                if progress:
                    await progress("ocr.started", {"page": page_number})
                try:
                    ocr_page = await self._ocr_page(path, index, options)
                    original = native.pages[index]
                    ocr_page.page_number = page_number
                    for block in ocr_page.blocks:
                        block.page_number = page_number
                        block.source = "ocr"
                    ocr_page.images = original.images
                    ocr_page.tables = original.tables
                    ocr_page.rotation = original.rotation
                    ocr_page.has_native_text = original.has_native_text
                    ocr_page.ocr_used = True
                    if original.plain_text.strip():
                        ocr_page.extraction_method = "hybrid"
                        ocr_page.blocks = [*original.blocks, *ocr_page.blocks]
                        ocr_page.plain_text = (
                            f"{original.plain_text}\n{ocr_page.plain_text}".strip()
                        )
                        ocr_page.markdown = f"{original.markdown}\n\n{ocr_page.markdown}".strip()
                    else:
                        ocr_page.extraction_method = "ocr"
                    native.pages[index] = ocr_page
                except Exception as exc:
                    native.pages[index].warnings.append(
                        f"OCR não pôde processar esta página: {type(exc).__name__}"
                    )
                if progress:
                    await progress(
                        "page.processed",
                        {"page": page_number, "method": native.pages[index].extraction_method},
                    )

        await asyncio.gather(*(replace(index) for index in targets))
        native.engine = "hybrid" if len(targets) < len(native.pages) else "marker"
        return RoutedResult(native, f"OCR seletivo aplicado em {len(targets)} página(s)", detected)

    def _needs_ocr(self, result: ExtractionResult, index: int) -> bool:
        chars = result.metadata.get("native_char_counts", [])
        words = result.metadata.get("native_word_counts", [])
        invalid = result.metadata.get("native_invalid_char_ratios", [])
        return (
            index >= len(chars)
            or chars[index] < self.settings.pdf_native_min_chars_per_page
            or index >= len(words)
            or words[index] < self.settings.pdf_native_min_words_per_page
            or (
                index < len(invalid)
                and invalid[index] > self.settings.pdf_native_max_invalid_char_ratio
            )
        )

    async def _ocr_page(self, path: Path, index: int, options: ExtractionOptions):
        import pymupdf

        with tempfile.TemporaryDirectory(prefix="pdf-ocr-") as directory:
            output = Path(directory) / "page.pdf"

            def make_page() -> None:
                source = pymupdf.open(path)
                target = pymupdf.open()
                try:
                    target.insert_pdf(source, from_page=index, to_page=index)
                    target.save(output)
                finally:
                    target.close()
                    source.close()

            await asyncio.to_thread(make_page)
            result = await asyncio.wait_for(
                self.marker.extract(output, options),
                timeout=self.settings.extraction_timeout_seconds,
            )
            if not result.pages:
                raise RuntimeError("OCR retornou uma página vazia")
            return result.pages[0]

    def _classify(self, result: ExtractionResult) -> str:
        counts = result.metadata.get("native_char_counts", [])
        if not counts:
            return "unknown"
        present = sum(count >= self.settings.pdf_native_min_chars_per_page for count in counts)
        if present == len(counts):
            return "native"
        if present == 0:
            return "scanned"
        return "mixed"
