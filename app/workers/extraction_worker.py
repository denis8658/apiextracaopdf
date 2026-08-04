import asyncio
import logging
import time
import traceback
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.models import Document, DocumentPage, DocumentResult, ExtractionJob
from app.db.session import SessionLocal
from app.extraction.normalizer import normalize_result
from app.extraction.router import ExtractionRouter
from app.schemas.extraction import ExtractionOptions
from app.storage import LocalStorageBackend

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


async def claim_job(session: AsyncSession) -> ExtractionJob | None:
    async with session.begin():
        job = await session.scalar(
            select(ExtractionJob)
            .where(
                ExtractionJob.status == "queued",
                ExtractionJob.attempt_count < ExtractionJob.max_attempts,
            )
            .order_by(ExtractionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status = "processing"
        job.attempt_count += 1
        job.started_at = utcnow()
        job.failed_at = None
        job.error_code = None
        job.error_message_safe = None
        job.error_details_internal = None
        document = await session.get(Document, job.document_id)
        if document:
            document.status = "processing"
    return job


async def persist_success(
    session: AsyncSession,
    job_id,
    routed,
    duration_ms: int,
) -> None:
    result = normalize_result(routed.result)
    async with session.begin():
        job = await session.get(ExtractionJob, job_id, with_for_update=True)
        if not job or job.status == "cancelled":
            return
        document = await session.get(Document, job.document_id, with_for_update=True)
        await session.execute(
            delete(DocumentPage).where(DocumentPage.document_id == job.document_id)
        )
        await session.execute(
            delete(DocumentResult).where(DocumentResult.document_id == job.document_id)
        )
        for page in result.pages:
            session.add(
                DocumentPage(
                    document_id=job.document_id,
                    page_number=page.page_number,
                    plain_text=page.plain_text,
                    markdown=page.markdown,
                    blocks_json=[block.model_dump(mode="json") for block in page.blocks],
                    char_count=len(page.plain_text),
                    word_count=len(page.plain_text.split()),
                    has_native_text=page.has_native_text,
                    ocr_used=page.ocr_used,
                    confidence=page.confidence,
                    width=page.width,
                    height=page.height,
                )
            )
        session.add(
            DocumentResult(
                document_id=job.document_id,
                plain_text=result.plain_text,
                markdown=result.markdown,
                structured_json=result.model_dump(mode="json"),
                metadata_json=result.metadata,
                schema_version="1.0",
            )
        )
        now = utcnow()
        job.status = "completed"
        job.engine_used = result.engine
        job.engine_selection_reason = routed.reason
        job.progress_percent = 100
        job.current_page = len(result.pages)
        job.total_pages = len(result.pages)
        job.completed_at = now
        job.processing_duration_ms = duration_ms
        job.engine_version = result.engine_version
        if document:
            document.status = "completed"
            document.page_count = len(result.pages)
            document.detected_pdf_type = routed.detected_pdf_type
            document.extraction_engine = result.engine


async def persist_failure(session: AsyncSession, job_id, exc: Exception, duration_ms: int) -> None:
    async with session.begin():
        job = await session.get(ExtractionJob, job_id, with_for_update=True)
        if not job:
            return
        document = await session.get(Document, job.document_id, with_for_update=True)
        retry = job.attempt_count < job.max_attempts
        job.status = "queued" if retry else "failed"
        job.failed_at = None if retry else utcnow()
        job.error_code = "extraction_failed"
        job.error_message_safe = "A extração do documento falhou."
        job.error_details_internal = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )[-16000:]
        job.processing_duration_ms = duration_ms
        if document:
            document.status = job.status


async def process_job(job: ExtractionJob) -> None:
    settings = get_settings()
    storage = LocalStorageBackend(settings.storage_path)
    router = ExtractionRouter(settings)
    started = time.perf_counter()
    try:
        async with SessionLocal() as session:
            document = await session.get(Document, job.document_id)
            if not document:
                raise RuntimeError("document record missing")
            path = await storage.open(document.storage_key)
        routed = await router.extract(
            path,
            ExtractionOptions(engine=job.engine_requested, output_formats=job.requested_formats),
        )
        duration = int((time.perf_counter() - started) * 1000)
        async with SessionLocal() as session:
            await persist_success(session, job.id, routed, duration)
        if document and not document.retain_original:
            await storage.delete(document.storage_key)
        logger.info(
            "extraction_completed",
            extra={"document_id": str(job.document_id), "job_id": str(job.id)},
        )
    except Exception as exc:
        duration = int((time.perf_counter() - started) * 1000)
        async with SessionLocal() as session:
            await persist_failure(session, job.id, exc, duration)
        logger.exception(
            "extraction_failed", extra={"document_id": str(job.document_id), "job_id": str(job.id)}
        )


async def worker_loop(once: bool = False) -> None:
    settings = get_settings()
    while True:
        async with SessionLocal() as session:
            job = await claim_job(session)
        if job:
            await process_job(job)
        elif once:
            return
        else:
            await asyncio.sleep(settings.extraction_worker_poll_seconds)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
