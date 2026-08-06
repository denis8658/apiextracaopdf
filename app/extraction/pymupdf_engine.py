import asyncio
import hashlib
import re
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from typing import Any

from app.schemas.extraction import (
    ExtractedBlock,
    ExtractedImage,
    ExtractedPage,
    ExtractedTable,
    ExtractionOptions,
    ExtractionResult,
)

CODE_PATTERN = re.compile(r"\b[A-Z]{1,8}(?:[- ]?\d{1,6})\b", re.IGNORECASE)


def _bbox(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        values = list(value)
    except TypeError:
        return None
    if len(values) != 4:
        return None
    return [float(item) for item in values]


def _contains(outer: list[float] | None, inner: list[float] | None) -> bool:
    if not outer or not inner:
        return False
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


class PyMuPDFExtractionEngine:
    name = "native"

    def __init__(self, max_images: int = 500) -> None:
        self.max_images = max_images

    async def extract(self, file_path: Path, options: ExtractionOptions) -> ExtractionResult:
        return await asyncio.to_thread(self._extract_sync, file_path, options)

    def _extract_sync(self, file_path: Path, options: ExtractionOptions) -> ExtractionResult:
        import pymupdf

        document = pymupdf.open(file_path)
        try:
            selected_pages = options.selected_pages or list(range(1, document.page_count + 1))
            page_indices = [page_number - 1 for page_number in selected_pages]
            image_counts = (
                self._image_hash_counts(document, page_indices)
                if options.extract_images
                else Counter()
            )
            pages: list[ExtractedPage] = []
            char_counts: list[int] = []
            word_counts: list[int] = []
            invalid_ratios: list[float] = []
            image_total = 0
            for page_index in page_indices:
                page = document[page_index]
                page_number = page_index + 1
                warnings: list[str] = []
                tables = self._extract_tables(page, page_number, options, warnings)
                blocks = self._extract_blocks(page, page_number, tables, options)
                text = page.get_text("text", sort=True)
                images: list[ExtractedImage] = []
                if options.extract_images:
                    remaining = max(0, self.max_images - image_total)
                    images = self._extract_images(
                        document,
                        page,
                        page_number,
                        blocks,
                        image_counts,
                        options,
                        remaining,
                    )
                    image_total += len(images)
                    if image_total >= self.max_images:
                        warnings.append("Limite de imagens do extrator nativo atingido.")
                count = len(text.strip())
                words = len(text.split())
                invalid = sum(char == "�" or ord(char) < 9 for char in text)
                invalid_ratio = invalid / len(text) if text else 0.0
                char_counts.append(count)
                word_counts.append(words)
                invalid_ratios.append(invalid_ratio)
                pages.append(
                    ExtractedPage(
                        page_number=page_number,
                        plain_text=text,
                        markdown=self._to_markdown(blocks, tables, text),
                        blocks=blocks,
                        tables=tables,
                        images=images,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        rotation=int(page.rotation),
                        extraction_method="native",
                        has_native_text=count > 0,
                        ocr_used=False,
                        warnings=warnings,
                    )
                )
            metadata: dict[str, Any] = dict(document.metadata or {})
            metadata.update(
                {
                    "page_count": document.page_count,
                    "selected_pages": selected_pages,
                    "native_char_counts": char_counts,
                    "native_word_counts": word_counts,
                    "native_invalid_char_ratios": invalid_ratios,
                }
            )
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

    @staticmethod
    def _image_hash_counts(document, page_indices: list[int]) -> Counter[str]:
        hashes: Counter[str] = Counter()
        for page_index in page_indices:
            page = document[page_index]
            for image in page.get_images(full=True):
                try:
                    raw = document.extract_image(image[0]).get("image", b"")
                    if raw:
                        hashes[hashlib.sha256(raw).hexdigest()] += 1
                except Exception:
                    continue
        return hashes

    @staticmethod
    def _extract_tables(
        page,
        page_number: int,
        options: ExtractionOptions,
        warnings: list[str],
    ) -> list[ExtractedTable]:
        if not options.extract_tables:
            return []
        try:
            found = page.find_tables()
            tables: list[ExtractedTable] = []
            for index, table in enumerate(found.tables, 1):
                raw_rows = table.extract() or []
                rows = [
                    [str(cell or "").strip() for cell in row]
                    for row in raw_rows
                    if any(str(cell or "").strip() for cell in row)
                ]
                if not rows:
                    continue
                headers = rows[0]
                body = rows[1:]
                markdown = PyMuPDFExtractionEngine._table_markdown(headers, body)
                tables.append(
                    ExtractedTable(
                        table_id=f"p{page_number}-t{index}",
                        page_number=page_number,
                        bbox=_bbox(table.bbox) if options.include_coordinates else None,
                        headers=headers,
                        rows=body,
                        columns=headers,
                        cells=[
                            {"row": row_index, "column": column_index, "content": cell}
                            for row_index, row in enumerate(body, 1)
                            for column_index, cell in enumerate(row)
                        ],
                        markdown=markdown,
                        original_text="\n".join(" | ".join(row) for row in rows),
                        source="native",
                        confidence=1.0,
                    )
                )
            return tables
        except Exception as exc:
            warnings.append(f"Detecção de tabela parcial: {type(exc).__name__}")
            return []

    @staticmethod
    def _table_markdown(headers: list[str], rows: list[list[str]]) -> str:
        escape = lambda value: value.replace("|", "\\|")  # noqa: E731
        header = "| " + " | ".join(escape(item) for item in headers) + " |"
        separator = "|" + "|".join("---" for _ in headers) + "|"
        body = ["| " + " | ".join(escape(item) for item in row) + " |" for row in rows]
        return "\n".join([header, separator, *body])

    @staticmethod
    def _extract_blocks(
        page,
        page_number: int,
        tables: list[ExtractedTable],
        options: ExtractionOptions,
    ) -> list[ExtractedBlock]:
        raw = page.get_text("dict", sort=True)
        blocks: list[ExtractedBlock] = []
        reading_order = 0
        for raw_block in raw.get("blocks", []):
            if raw_block.get("type") != 0:
                continue
            lines: list[str] = []
            fonts: set[str] = set()
            sizes: list[float] = []
            for line in raw_block.get("lines", []):
                spans = line.get("spans", [])
                line_text = "".join(str(span.get("text", "")) for span in spans)
                if line_text.strip():
                    lines.append(line_text)
                fonts.update(str(span.get("font")) for span in spans if span.get("font"))
                sizes.extend(float(span.get("size", 0)) for span in spans if span.get("size"))
            text = "\n".join(lines).strip()
            if not text:
                continue
            reading_order += 1
            bbox = _bbox(raw_block.get("bbox")) if options.include_coordinates else None
            source = "table" if any(_contains(table.bbox, bbox) for table in tables) else "native"
            block_type = "heading" if sizes and max(sizes) >= 14 else "text"
            blocks.append(
                ExtractedBlock(
                    block_id=f"p{page_number}-b{reading_order}",
                    block_type=block_type,
                    page_number=page_number,
                    text=text,
                    bbox=bbox,
                    source=source,
                    reading_order=reading_order,
                    metadata={"fonts": sorted(fonts), "font_sizes": sorted(set(sizes))},
                )
            )
        return blocks

    @classmethod
    def _extract_images(
        cls,
        document,
        page,
        page_number: int,
        blocks: list[ExtractedBlock],
        image_counts: Counter[str],
        options: ExtractionOptions,
        remaining: int,
    ) -> list[ExtractedImage]:
        images: list[ExtractedImage] = []
        for index, image in enumerate(page.get_images(full=True), 1):
            if len(images) >= remaining:
                break
            try:
                extracted = document.extract_image(image[0])
                raw = extracted.get("image", b"")
                if not raw:
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                width = int(extracted.get("width") or image[2] or 0)
                height = int(extracted.get("height") or image[3] or 0)
                repeated = image_counts[digest] > 1
                image_type = cls._classify_image(width, height, repeated)
                if options.ignore_decorative_images and image_type in {"logo", "icon"}:
                    continue
                rects = page.get_image_rects(image[0])
                bbox = _bbox(rects[0]) if rects and options.include_coordinates else None
                nearby = cls._nearby_text(bbox, blocks)
                code_match = CODE_PATTERN.search(nearby or "")
                images.append(
                    ExtractedImage(
                        image_id=f"p{page_number}-i{index}",
                        page_number=page_number,
                        index=index,
                        image_type=image_type,
                        format=str(extracted.get("ext") or "bin"),
                        width=width,
                        height=height,
                        bbox=bbox,
                        sha256=digest,
                        nearby_text=nearby,
                        related_code=code_match.group(0) if code_match else None,
                        related_description=nearby,
                        association_confidence=0.8 if nearby else None,
                        raw_bytes=raw,
                    )
                )
            except Exception:
                continue
        return images

    @staticmethod
    def _classify_image(width: int, height: int, repeated: bool) -> str:
        if repeated:
            return "logo"
        if width <= 64 or height <= 64:
            return "icon"
        ratio = width / height if height else 1
        if ratio > 4 or ratio < 0.25:
            return "profile"
        if width >= 300 and height >= 300:
            return "technical_drawing"
        return "diagram"

    @staticmethod
    def _nearby_text(bbox: list[float] | None, blocks: list[ExtractedBlock]) -> str | None:
        if not bbox:
            return None
        center_y = (bbox[1] + bbox[3]) / 2
        candidates = [
            (abs(((block.bbox[1] + block.bbox[3]) / 2) - center_y), block.text or "")
            for block in blocks
            if block.bbox and block.text
        ]
        candidates.sort(key=lambda item: item[0])
        text = " ".join(item[1].replace("\n", " ") for item in candidates[:2]).strip()
        return text[:1000] or None

    @staticmethod
    def _to_markdown(
        blocks: list[ExtractedBlock], tables: list[ExtractedTable], fallback: str
    ) -> str:
        parts: list[str] = []
        for block in blocks:
            if block.source == "table":
                continue
            text = block.text or ""
            parts.append(f"## {text}" if block.block_type == "heading" else text)
        parts.extend(table.markdown for table in tables)
        return "\n\n".join(part for part in parts if part.strip()) or fallback
