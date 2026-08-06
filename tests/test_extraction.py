import asyncio
import sys
from pathlib import Path

import pytest

from app.core.config import Settings
from app.extraction.marker_engine import html_to_text
from app.extraction.normalizer import normalize_result, normalize_text
from app.extraction.pymupdf_engine import PyMuPDFExtractionEngine
from app.extraction.router import ExtractionRouter
from app.extraction.validators import inspect_pdf
from app.schemas.extraction import (
    ExtractedBlock,
    ExtractedPage,
    ExtractionOptions,
    ExtractionResult,
)


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


@pytest.mark.asyncio
async def test_auto_ocr_replaces_only_deficient_page(tmp_path, monkeypatch):
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        pdf_native_min_chars_per_page=20,
        pdf_native_min_words_per_page=2,
    )
    router = ExtractionRouter(settings)
    native = ExtractionResult(
        plain_text="",
        markdown="",
        pages=[
            ExtractedPage(
                page_number=1,
                plain_text="native text with enough words",
                markdown="native",
                blocks=[],
                has_native_text=True,
                ocr_used=False,
            ),
            ExtractedPage(
                page_number=2,
                plain_text="",
                markdown="",
                blocks=[],
                has_native_text=False,
                ocr_used=False,
            ),
        ],
        metadata={
            "native_char_counts": [29, 0],
            "native_word_counts": [5, 0],
            "native_invalid_char_ratios": [0.0, 0.0],
        },
        engine="native",
    )

    class NativeFake:
        async def extract(self, path, options):
            return native

    async def fake_ocr(path, index, options):
        return ExtractedPage(
            page_number=1,
            plain_text="recognized",
            markdown="recognized",
            blocks=[
                ExtractedBlock(
                    block_id="ocr-1", block_type="text", page_number=1, text="recognized"
                )
            ],
            has_native_text=False,
            ocr_used=True,
        )

    router.native = NativeFake()
    monkeypatch.setattr(router, "_ocr_page", fake_ocr)
    result = await router.extract(tmp_path / "unused.pdf", ExtractionOptions())
    assert result.result.pages[0].extraction_method == "native"
    assert result.result.pages[1].extraction_method == "ocr"
    assert result.result.pages[1].blocks[0].source == "ocr"
    assert result.result.engine == "hybrid"


def test_normalizer_keeps_unicode_and_removes_controls():
    assert normalize_text("  ação\r\nlinha\x00") == "ação\nlinha"
    assert html_to_text("<p>Janela <strong>pivotante</strong></p>") == "Janela\npivotante"


@pytest.mark.asyncio
async def test_missing_pdf_engine_is_not_reported_as_corrupted(tmp_path, monkeypatch):
    path = tmp_path / "document.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    with pytest.raises(Exception) as captured:
        await inspect_pdf(path, Settings())
    assert captured.value.code == "pdf_engine_unavailable"
    assert captured.value.status_code == 503


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
