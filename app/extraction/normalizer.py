import re

from app.schemas.extraction import ExtractionResult

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = CONTROL_CHARS.sub("", value)
    return "\n".join(line.rstrip() for line in value.split("\n")).strip()


def normalize_result(result: ExtractionResult) -> ExtractionResult:
    for page in result.pages:
        page.plain_text = normalize_text(page.plain_text)
        page.markdown = normalize_text(page.markdown)
        for block in page.blocks:
            if block.text is not None:
                block.text = normalize_text(block.text)
    result.plain_text = "\n\n\f\n\n".join(page.plain_text for page in result.pages)
    result.markdown = "\n\n<!-- page-break -->\n\n".join(page.markdown for page in result.pages)
    return result
