import re
import unicodedata
from collections import Counter

from app.schemas.extraction import ExtractionResult

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EXCESS_BLANK_LINES = re.compile(r"\n{3,}")
LINE_HYPHENATION = re.compile(r"(?<=\w)-\n(?=[a-zá-ú])", re.IGNORECASE)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = CONTROL_CHARS.sub("", value)
    value = LINE_HYPHENATION.sub("", value)
    value = "\n".join(re.sub(r"[ \t]{2,}", " ", line).strip() for line in value.split("\n"))
    return EXCESS_BLANK_LINES.sub("\n\n", value).strip()


def normalize_result(result: ExtractionResult) -> ExtractionResult:
    boundary_lines: Counter[str] = Counter()
    for page in result.pages:
        lines = [line.strip() for line in page.plain_text.splitlines() if line.strip()]
        boundary_lines.update(set([*lines[:1], *lines[-1:]]))
    repeated = {
        line
        for line, count in boundary_lines.items()
        if len(result.pages) >= 3 and count > len(result.pages) / 2
    }
    if repeated:
        result.metadata["normalization_removed_repeated_boundaries"] = sorted(repeated)
    for page in result.pages:
        original_page_text = page.plain_text
        page.plain_text = normalize_text(page.plain_text)
        page.markdown = normalize_text(page.markdown)
        lines = page.plain_text.splitlines()
        if lines and lines[0] in repeated:
            lines = lines[1:]
        if lines and lines[-1] in repeated:
            lines = lines[:-1]
        page.plain_text = "\n".join(lines).strip()
        seen: set[tuple[str, str]] = set()
        normalized_blocks = []
        for block in page.blocks:
            if block.text is not None:
                original = block.text
                block.text = normalize_text(block.text)
                if original != block.text:
                    block.metadata.setdefault("original_text", original)
            key = (block.source, block.text or block.html or "")
            if key not in seen:
                normalized_blocks.append(block)
                seen.add(key)
        page.blocks = normalized_blocks
        if original_page_text != page.plain_text:
            page.warnings.append("Conteúdo normalizado; o original permanece nos blocos/metadados.")
    result.plain_text = "\n\n".join(
        f"Página {page.page_number}\n{page.plain_text}" for page in result.pages
    )
    result.markdown = "\n\n<!-- page-break -->\n\n".join(
        f"# Página {page.page_number}\n\n{page.markdown}" for page in result.pages
    )
    return result
