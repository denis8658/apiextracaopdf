import asyncio
import sys
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.extraction.easyocr_engine import EasyOCRExtractionEngine
from app.extraction.marker_engine import html_to_text
from app.extraction.normalizer import normalize_result, normalize_text
from app.extraction.page_selection import parse_page_selector
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


@pytest.mark.parametrize(
    ("selector", "total", "expected"),
    [
        ("all", 5, [1, 2, 3, 4, 5]),
        ("5", 5, [5]),
        ("1,3,5", 5, [1, 3, 5]),
        ("2-5", 5, [2, 3, 4, 5]),
        ("1,3,3,2-4", 5, [1, 2, 3, 4]),
        ("odd", 6, [1, 3, 5]),
        ("even", 6, [2, 4, 6]),
    ],
)
def test_page_selector_parses_deduplicates_and_orders(selector, total, expected):
    assert parse_page_selector(selector, total, 100) == expected


@pytest.mark.parametrize(
    ("selector", "code"),
    [
        ("0", "INVALID_PAGE_SELECTOR"),
        ("4", "PAGE_OUT_OF_RANGE"),
        ("3-1", "INVALID_PAGE_RANGE"),
        ("one", "INVALID_PAGE_SELECTOR"),
        ("1,,2", "INVALID_PAGE_SELECTOR"),
        ("1-", "INVALID_PAGE_SELECTOR"),
    ],
)
def test_page_selector_rejects_invalid_values(selector, code):
    with pytest.raises(AppError) as captured:
        parse_page_selector(selector, 3, 100)
    assert captured.value.code == code


def test_page_selector_enforces_configured_limit():
    with pytest.raises(AppError) as captured:
        parse_page_selector("all", 4, 3)
    assert captured.value.code == "PAGE_SELECTION_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_native_extraction_processes_only_selected_pages(tmp_path, sample_pdf):
    path = tmp_path / "selected.pdf"
    path.write_bytes(sample_pdf)
    result = normalize_result(
        await PyMuPDFExtractionEngine().extract(
            path,
            ExtractionOptions(
                engine="native", pages="1,3", selected_pages=[1, 3]
            ),
        )
    )
    assert [page.page_number for page in result.pages] == [1, 3]
    assert "página 1" in result.plain_text
    assert "página 2" not in result.plain_text
    assert "página 3" in result.markdown


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
    events = []

    async def progress(event_type, data):
        events.append((event_type, data))

    result = await router.extract(tmp_path / "unused.pdf", ExtractionOptions(), progress)
    assert result.result.pages[0].extraction_method == "native"
    assert result.result.pages[1].extraction_method == "ocr"
    assert result.result.pages[1].blocks[0].source == "ocr"
    assert result.result.engine == "hybrid"
    processed = [data for event, data in events if event == "page.processed"]
    assert sorted(item["page"] for item in processed) == [1, 2]
    assert sorted(item["completed_pages"] for item in processed) == [1, 2]
    assert all(item["selected_total"] == 2 for item in processed)


@pytest.mark.asyncio
async def test_easyocr_returns_traceable_blocks(tmp_path, sample_pdf, monkeypatch):
    path = tmp_path / "scanned.pdf"
    path.write_bytes(sample_pdf)
    engine = EasyOCRExtractionEngine(tmp_path / "models")

    class ReaderFake:
        def readtext(self, image, **kwargs):
            return [([[10, 20], [110, 20], [110, 50], [10, 50]], "texto", 0.92)]

    monkeypatch.setattr(engine, "_reader", lambda language: (ReaderFake(), "pt"))
    result = await engine.extract(path, ExtractionOptions(ocr_language="por", ocr_dpi=144))
    assert len(result.pages) == 3
    block = result.pages[0].blocks[0]
    assert block.source == "ocr"
    assert block.confidence == pytest.approx(0.92)
    assert block.bbox == pytest.approx([5, 10, 55, 25])


def test_easyocr_only_searches_rotations_when_first_pass_is_insufficient(tmp_path):
    engine = EasyOCRExtractionEngine(tmp_path / "models")
    sufficient = [([], "palavra reconhecida corretamente", 0.90) for _ in range(5)]
    insufficient = [([], "x", 0.20)]
    assert engine._is_sufficient(sufficient)
    assert not engine._is_sufficient(insufficient)
    assert engine._quality_score(sufficient) > engine._quality_score(insufficient)


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
