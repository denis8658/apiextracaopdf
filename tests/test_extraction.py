import asyncio
from pathlib import Path

import pytest

from app.core.config import Settings
from app.extraction.marker_engine import html_to_text
from app.extraction.normalizer import normalize_result, normalize_text
from app.extraction.pymupdf_engine import PyMuPDFExtractionEngine
from app.extraction.router import ExtractionRouter
from app.schemas.extraction import ExtractionOptions


@pytest.mark.asyncio
async def test_native_extraction_preserves_pages(tmp_path, sample_pdf):
    path = tmp_path / "sample.pdf"
    path.write_bytes(sample_pdf)
    result = normalize_result(
        await PyMuPDFExtractionEngine().extract(path, ExtractionOptions(engine="native"))
    )
    assert len(result.pages) == 3
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert "Orçamento 1790" in result.plain_text
    assert all(page.blocks for page in result.pages)


@pytest.mark.asyncio
async def test_auto_selects_native_for_text_pdf(tmp_path, sample_pdf):
    path = tmp_path / "sample.pdf"
    path.write_bytes(sample_pdf)
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        pdf_native_min_chars_per_page=20,
        pdf_native_min_text_page_ratio=0.7,
    )
    routed = await ExtractionRouter(settings).extract(path, ExtractionOptions(engine="auto"))
    assert routed.result.engine == "native"
    assert routed.detected_pdf_type == "native"


def test_normalizer_keeps_unicode_and_removes_controls():
    assert normalize_text("  ação\r\nlinha\x00") == "ação\nlinha"
    assert html_to_text("<p>Janela <strong>pivotante</strong></p>") == "Janela\npivotante"


@pytest.mark.asyncio
async def test_user_model_pdf_when_available():
    path = Path(r"C:\Users\denis\Downloads\5931701f3_Oramento-1790-SEMPREO.pdf")
    if not await asyncio.to_thread(path.exists):
        pytest.skip("PDF-modelo local não disponível")
    result = await PyMuPDFExtractionEngine().extract(path, ExtractionOptions(engine="native"))
    assert len(result.pages) == 3
    assert sum(len(page.plain_text) for page in result.pages) > 1000
    assert "1790" in result.plain_text or any("1790" in page.plain_text for page in result.pages)
    routed = await ExtractionRouter(Settings()).extract(path, ExtractionOptions(engine="auto"))
    assert routed.result.engine == "native"
    assert routed.detected_pdf_type == "native"
