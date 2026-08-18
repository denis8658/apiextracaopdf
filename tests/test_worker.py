import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Document, ExtractionJob
from app.schemas.extraction import ExtractedImage, ExtractedPage, ExtractionResult
from app.storage import LocalStorageBackend
from app.workers.extraction_worker import (
    StructuringStageError,
    _save_images,
    claim_job,
    persist_failure,
    public_result,
)


@pytest.mark.asyncio
async def test_image_saving_is_idempotent_across_job_retries(tmp_path):
    storage = LocalStorageBackend(tmp_path)
    job_id = uuid.uuid4()
    image = ExtractedImage(
        image_id="p1-i1",
        page_number=1,
        index=1,
        format="png",
        width=1,
        height=1,
        sha256="a" * 64,
        raw_bytes=b"first-attempt",
    )
    result = SimpleNamespace(pages=[SimpleNamespace(images=[image])])

    await _save_images(storage, job_id, result, "reference")
    image.raw_bytes = b"retry-attempt"
    await _save_images(storage, job_id, result, "reference")

    saved = await storage.open(f"jobs/{job_id}/images/p1-i1.png")
    assert saved.read_bytes() == b"retry-attempt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "has_reference", "has_base64"),
    [("reference", True, False), ("base64", True, True), ("both", True, True)],
)
async def test_image_output_modes(tmp_path, mode, has_reference, has_base64):
    storage = LocalStorageBackend(tmp_path)
    job_id = uuid.uuid4()
    image = ExtractedImage(
        image_id="p12-i3",
        page_number=12,
        index=3,
        format="png",
        mime_type="image/png",
        width=1,
        height=1,
        sha256="a" * 64,
        raw_bytes=b"image-content",
    )
    result = SimpleNamespace(pages=[SimpleNamespace(images=[image])])

    await _save_images(storage, job_id, result, mode)

    assert bool(image.reference) is has_reference
    assert bool(image.content_base64) is has_base64
    if mode == "both":
        assert image.reference == f"/v1/extractions/{job_id}/files/p12-i3.png"
        assert image.content_encoding == "base64"


def test_public_result_reports_requested_processed_and_skipped_pages():
    document = SimpleNamespace(
        original_filename="documento.pdf",
        content_type="application/pdf",
        page_count=5,
        file_size_bytes=100,
        sha256="a" * 64,
        cliente_id=" cli_456 ",
        obra_id="obr_789",
    )
    job = SimpleNamespace(
        id=uuid.uuid4(),
        ocr_language="por",
        started_at=datetime.now(UTC),
        page_selector="1,3,5",
        selected_pages_json=[1, 3, 5],
    )
    result = ExtractionResult(
        pages=[
            ExtractedPage(
                page_number=1,
                plain_text="um",
                markdown="um",
                blocks=[],
                has_native_text=True,
                ocr_used=False,
            ),
            ExtractedPage(
                page_number=5,
                plain_text="cinco",
                markdown="cinco",
                blocks=[],
                has_native_text=True,
                ocr_used=False,
            ),
        ],
        plain_text="um\ncinco",
        markdown="um\ncinco",
        metadata={},
        engine="native",
    )
    payload = public_result(document, job, result, 10)
    assert payload["contexto"] == {"cliente_id": " cli_456 ", "obra_id": "obr_789"}
    selection = payload["page_selection"]
    assert selection == {
        "selector": "1,3,5",
        "requested_pages": [1, 3, 5],
        "processed_pages": [1, 5],
        "skipped_pages": [3],
        "document_page_count": 5,
    }


@pytest.mark.asyncio
async def test_claim_job_changes_state_and_attempt(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        document = Document(
            original_filename="test.pdf",
            safe_filename="test.pdf",
            content_type="application/pdf",
            file_size_bytes=10,
            sha256="a" * 64,
            storage_provider="local",
            storage_key=f"x/{uuid.uuid4()}.pdf",
            status="queued",
            detected_pdf_type="unknown",
            retain_original=True,
        )
        session.add(document)
        await session.flush()
        session.add(
            ExtractionJob(
                document_id=document.id,
                status="queued",
                engine_requested="native",
                requested_formats=["text"],
                max_attempts=3,
            )
        )
        await session.commit()
    async with sessions() as first:
        claimed = await claim_job(first)
        assert claimed is not None
        assert claimed.status == "processing"
        assert claimed.attempt_count == 1
    async with sessions() as second:
        assert await claim_job(second) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_persistence_retry_is_claimed_without_rerunning_extraction_attempt(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        document = Document(
            original_filename="test.pdf",
            safe_filename="test.pdf",
            content_type="application/pdf",
            file_size_bytes=10,
            sha256="a" * 64,
            storage_provider="local",
            storage_key=f"x/{uuid.uuid4()}.pdf",
            status="queued",
            detected_pdf_type="native",
            retain_original=True,
        )
        session.add(document)
        await session.flush()
        session.add(
            ExtractionJob(
                document_id=document.id,
                status="queued",
                current_stage="persistence_retry",
                engine_requested="native",
                requested_formats=["json"],
                max_attempts=3,
                attempt_count=3,
                save_to_base44=True,
                persistence_status="failed",
            )
        )
        await session.commit()

    async with sessions() as session:
        claimed = await claim_job(session)
        assert claimed is not None
        assert claimed.current_stage == "persistence_retry"
        assert claimed.attempt_count == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_structuring_failure_has_safe_stage_and_does_not_create_business_records(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        document = Document(
            original_filename="test.pdf",
            safe_filename="test.pdf",
            content_type="application/pdf",
            file_size_bytes=10,
            sha256="a" * 64,
            storage_provider="local",
            storage_key=f"x/{uuid.uuid4()}.pdf",
            status="processing",
            detected_pdf_type="native",
            retain_original=True,
            cliente_id="cli",
            obra_id="obra",
        )
        job = ExtractionJob(
            document=document,
            status="processing",
            engine_requested="native",
            requested_formats=["json"],
            max_attempts=1,
            attempt_count=1,
            structure_output=True,
        )
        session.add_all([document, job])
        await session.commit()
        job_id = job.id

    async with sessions() as session:
        await persist_failure(session, job_id, StructuringStageError(), 20)

    async with sessions() as session:
        failed = await session.get(ExtractionJob, job_id)
        assert failed.status == "failed"
        assert failed.current_stage == "structuring_failed"
        assert failed.error_code == "STRUCTURING_FAILED"
        assert "internal detail" not in failed.error_message_safe
        from sqlalchemy import func, select

        from app.db.models import Order, OrderItem, StructureJob

        assert await session.scalar(select(func.count()).select_from(StructureJob)) == 0
        assert await session.scalar(select(func.count()).select_from(Order)) == 0
        assert await session.scalar(select(func.count()).select_from(OrderItem)) == 0
    await engine.dispose()
