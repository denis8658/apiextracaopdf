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
        self.native = PyMuPDFExtractionEngine()
        self.marker = MarkerExtractionEngine()

    async def extract(self, path: Path, options: ExtractionOptions) -> RoutedResult:
        if options.engine == "native":
            result = await self.native.extract(path, options)
            return RoutedResult(result, "motor native solicitado", self._classify(result))
        if options.engine == "marker":
            result = await self.marker.extract(path, options)
            return RoutedResult(result, "motor marker solicitado", "unknown")

        inspection = await self.native.extract(path, options)
        counts = inspection.metadata.get("native_char_counts", [])
        sufficient = sum(count >= self.settings.pdf_native_min_chars_per_page for count in counts)
        ratio = sufficient / len(counts) if counts else 0.0
        detected = self._classify(inspection)
        if ratio >= self.settings.pdf_native_min_text_page_ratio:
            return RoutedResult(
                inspection,
                f"texto nativo suficiente em {ratio:.0%} das páginas",
                detected,
            )
        result = await self.marker.extract(path, options)
        return RoutedResult(
            result,
            f"texto nativo suficiente em apenas {ratio:.0%} das páginas",
            detected,
        )

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
