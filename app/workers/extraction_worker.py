import asyncio
import base64
import logging
import time
import traceback
from datetime import UTC, datetime

if __name__ == "__main__":
    from app.core.runtime import reexec_with_project_python

    reexec_with_project_python("app.workers.extraction_worker")

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.models import (
    Document,
    DocumentPage,
    DocumentResult,
    ExtractionEvent,
    ExtractionJob,
)
from app.db.session import SessionLocal
from app.extraction.normalizer import normalize_result
from app.extraction.router import ExtractionRouter
from app.schemas.extraction import ExtractionOptions
from app.schemas.extraction_api import PublicExtractionResult
from app.storage import LocalStorageBackend

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def add_event(session: AsyncSession, job_id, event_type: str, data: dict) -> None:
    data.setdefault("job_id", str(job_id))
    session.add(ExtractionEvent(job_id=job_id, event_type=event_type, data_json=data))


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
        job.current_stage = "native_extraction"
        job.attempt_count += 1
        job.started_at = utcnow()
        job.failed_at = None
        job.error_code = job.error_message_safe = job.error_details_internal = None
        document = await session.get(Document, job.document_id)
        if document:
            document.status = "processing"
        add_event(session, job.id, "job.started", {"progress": 0})
    return job


async def _save_images(storage, job_id, result, image_output: str) -> None:
    for page in result.pages:
        for image in page.images:
            if not image.raw_bytes:
                continue
            if image_output == "base64":
                encoded = base64.b64encode(image.raw_bytes).decode("ascii")
                image.reference = f"data:image/{image.format};base64,{encoded}"
            elif image_output == "reference":
                filename = f"{image.image_id}.{image.format}"
                key = f"jobs/{job_id}/images/{filename}"

                async def chunks(value=image.raw_bytes):
                    yield value

                await storage.save(key, chunks())
                image.reference = f"/v1/extractions/{job_id}/files/{filename}"
            image.raw_bytes = None


def _bbox(value):
    if not value or len(value) != 4:
        return None
    return {"x0": value[0], "y0": value[1], "x1": value[2], "y1": value[3]}


def public_result(document, job, result, duration_ms: int) -> dict:
    pages = []
    for page in result.pages:
        pages.append(
            {
                "page_number": page.page_number,
                "width": page.width,
                "height": page.height,
                "rotation": page.rotation,
                "extraction_method": page.extraction_method,
                "plain_text": page.plain_text,
                "markdown": page.markdown,
                "blocks": [
                    {
                        "id": block.block_id,
                        "type": block.block_type,
                        "content": block.text,
                        "source": block.source,
                        "confidence": block.confidence,
                        "reading_order": block.reading_order,
                        "bbox": _bbox(block.bbox),
                        "metadata": block.metadata,
                    }
                    for block in page.blocks
                ],
                "tables": [
                    {
                        "id": table.table_id,
                        "page_number": table.page_number,
                        "bbox": _bbox(table.bbox),
                        "headers": table.headers,
                        "rows": table.rows,
                        "columns": table.columns,
                        "cells": table.cells,
                        "markdown": table.markdown,
                        "original_text": table.original_text,
                        "source": table.source,
                        "confidence": table.confidence,
                        "method": table.method,
                    }
                    for table in page.tables
                ],
                "images": [
                    {
                        "id": image.image_id,
                        "page_number": image.page_number,
                        "index": image.index,
                        "type": image.image_type,
                        "format": image.format,
                        "width": image.width,
                        "height": image.height,
                        "bbox": _bbox(image.bbox),
                        "hash": image.sha256,
                        "reference": image.reference,
                        "nearby_text": image.nearby_text,
                        "related_code": image.related_code,
                        "related_description": image.related_description,
                        "association_confidence": image.association_confidence,
                    }
                    for image in page.images
                ],
                "warnings": page.warnings,
            }
        )
    methods = [page.extraction_method for page in result.pages]
    metadata = result.metadata
    return {
        "schema_version": "1.0",
        "document": {
            "filename": document.original_filename,
            "mime_type": document.content_type,
            "page_count": len(result.pages),
            "file_size": document.file_size_bytes,
            "document_hash": document.sha256,
            "language": job.ocr_language,
            "title": metadata.get("title"),
            "author": metadata.get("author"),
            "created_at": metadata.get("creationDate") or metadata.get("creation_date"),
        },
        "processing": {
            "job_id": str(job.id),
            "status": "completed",
            "started_at": job.started_at,
            "finished_at": utcnow(),
            "duration_ms": duration_ms,
            "native_pages": methods.count("native"),
            "ocr_pages": methods.count("ocr"),
            "hybrid_pages": methods.count("hybrid"),
            "warnings": [warning for page in result.pages for warning in page.warnings],
        },
        "pages": pages,
        "statistics": {
            "characters": len(result.plain_text),
            "words": len(result.plain_text.split()),
            "tables": sum(len(page.tables) for page in result.pages),
            "images": sum(len(page.images) for page in result.pages),
            "ocr_blocks": sum(
                block.source == "ocr" for page in result.pages for block in page.blocks
            ),
        },
    }


async def persist_success(session, job_id, routed, duration_ms: int, storage) -> None:
    result = normalize_result(routed.result)
    job = await session.get(ExtractionJob, job_id)
    if not job:
        return
    document = await session.get(Document, job.document_id)
    await _save_images(storage, job.id, result, job.image_output)
    payload = PublicExtractionResult.model_validate(
        public_result(document, job, result, duration_ms)
    ).model_dump(mode="json")
    async with session.begin_nested():
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
                structured_json=payload,
                metadata_json=result.metadata,
                schema_version="1.0",
            )
        )
        now = utcnow()
        job.status = "completed"
        job.current_stage = "completed"
        job.engine_used = result.engine
        job.engine_selection_reason = routed.reason
        job.progress_percent = 100
        job.current_page = len(result.pages)
        job.total_pages = len(result.pages)
        job.completed_at = now
        job.processing_duration_ms = duration_ms
        job.engine_version = result.engine_version
        job.warnings_json = payload["processing"]["warnings"]
        document.status = "completed"
        document.page_count = len(result.pages)
        document.detected_pdf_type = routed.detected_pdf_type
        document.extraction_engine = result.engine
        add_event(
            session,
            job.id,
            "job.completed",
            {
                "progress": 100,
                "status": "completed",
                "result_url": f"/v1/extractions/{job.id}/result",
            },
        )
    await session.commit()


async def persist_failure(session, job_id, exc: Exception, duration_ms: int) -> None:
    async with session.begin():
        job = await session.get(ExtractionJob, job_id, with_for_update=True)
        if not job:
            return
        document = await session.get(Document, job.document_id, with_for_update=True)
        retry = job.attempt_count < job.max_attempts
        job.status = "queued" if retry else "failed"
        job.current_stage = "retrying" if retry else "failed"
        job.failed_at = None if retry else utcnow()
        job.error_code = "INTERNAL_ERROR"
        job.error_message_safe = "A extração do documento falhou."
        job.error_details_internal = "".join(traceback.format_exception(exc))[-16000:]
        job.processing_duration_ms = duration_ms
        if document:
            document.status = job.status
        add_event(
            session,
            job.id,
            "job.retrying" if retry else "job.failed",
            {
                "status": job.status,
                "error": {"code": job.error_code, "message": job.error_message_safe},
            },
        )


async def process_job(job: ExtractionJob) -> None:
    settings = get_settings()
    storage = LocalStorageBackend(settings.storage_path)
    router = ExtractionRouter(settings)
    started = time.perf_counter()

    async def progress(event_type: str, data: dict) -> None:
        async with SessionLocal() as progress_session, progress_session.begin():
            current = await progress_session.get(ExtractionJob, job.id)
            if not current or current.status == "cancelled":
                raise asyncio.CancelledError
            page = data.get("page")
            if page:
                current.current_page = page
                current.progress_percent = min(
                    95, int(page / max(1, current.total_pages or 1) * 90)
                )
            current.current_stage = event_type
            add_event(
                progress_session, job.id, event_type, {**data, "progress": current.progress_percent}
            )

    try:
        async with SessionLocal() as session:
            document = await session.get(Document, job.document_id)
            if not document:
                raise RuntimeError("document record missing")
            path = await storage.open(document.storage_key)
        options = ExtractionOptions(
            engine=job.engine_requested,
            output_formats=job.requested_formats,
            output_format=job.output_format,
            ocr_mode=job.ocr_mode,
            ocr_language=job.ocr_language,
            ocr_dpi=settings.ocr_dpi,
            extract_images=job.extract_images,
            extract_tables=job.extract_tables,
            include_coordinates=job.include_coordinates,
            image_output=job.image_output,
        )
        routed = await asyncio.wait_for(
            router.extract(path, options, progress), timeout=settings.extraction_timeout_seconds
        )
        duration = int((time.perf_counter() - started) * 1000)
        async with SessionLocal() as session:
            await persist_success(session, job.id, routed, duration, storage)
        if not document.retain_original:
            await storage.delete(document.storage_key)
        logger.info(
            "extraction_completed",
            extra={"document_id": str(job.document_id), "job_id": str(job.id)},
        )
    except asyncio.CancelledError:
        logger.info("extraction_cancelled", extra={"job_id": str(job.id)})
    except Exception as exc:
        duration = int((time.perf_counter() - started) * 1000)
        async with SessionLocal() as session:
            await persist_failure(session, job.id, exc, duration)
        logger.exception(
            "extraction_failed", extra={"document_id": str(job.document_id), "job_id": str(job.id)}
        )


async def cleanup_expired() -> int:
    storage = LocalStorageBackend(get_settings().storage_path)
    now = utcnow()
    cleaned = 0
    async with SessionLocal() as session:
        documents = list(
            (
                await session.scalars(
                    select(Document)
                    .options(selectinload(Document.jobs))
                    .where(
                        Document.expires_at.is_not(None),
                        Document.expires_at <= now,
                        Document.status != "expired",
                    )
                )
            ).all()
        )
        for document in documents:
            await storage.delete(document.storage_key)
            for job in document.jobs:
                await storage.delete_prefix(f"jobs/{job.id}")
            await session.execute(
                delete(DocumentPage).where(DocumentPage.document_id == document.id)
            )
            await session.execute(
                delete(DocumentResult).where(DocumentResult.document_id == document.id)
            )
            document.status = "expired"
            cleaned += 1
        await session.commit()
    return cleaned


async def worker_loop(once: bool = False) -> None:
    settings = get_settings()
    last_cleanup = 0.0
    while True:
        if time.monotonic() - last_cleanup >= settings.extraction_cleanup_interval_seconds:
            await cleanup_expired()
            last_cleanup = time.monotonic()
        async with SessionLocal() as session:
            job = await claim_job(session)
        if job:
            await process_job(job)
        elif once:
            return
        else:
            await asyncio.sleep(settings.extraction_worker_poll_seconds)


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
