import asyncio
import logging
import time
import traceback
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.models import Document, DocumentPage, StructureJob, StructureResult
from app.db.session import SessionLocal
from app.services.order_structuring_service import OrderStructuringService
from app.structuring.base import StructuredDataProvider
from app.structuring.consistency import validate_consistency
from app.structuring.normalizer import normalize_order
from app.structuring.order_agent import OrderStructuringAgent
from app.structuring.prompts import build_document_input
from app.structuring.provider import create_provider

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


async def claim_structure_job(session: AsyncSession) -> StructureJob | None:
    async with session.begin():
        job = await session.scalar(
            select(StructureJob)
            .where(
                StructureJob.status == "queued",
                StructureJob.attempt_count < StructureJob.max_attempts,
            )
            .order_by(StructureJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status = "processing"
        job.attempt_count += 1
        job.progress_percent = 10
        job.started_at = utcnow()
        job.failed_at = None
        job.error_code = None
        job.error_message_safe = None
        job.error_details_internal = None
    return job


async def process_structure_job(
    job: StructureJob, provider: StructuredDataProvider | None = None
) -> None:
    settings = get_settings()
    started = time.perf_counter()
    try:
        async with SessionLocal() as session:
            existing = await session.scalar(
                select(StructureResult).where(StructureResult.structure_job_id == job.id)
            )
            if existing:
                checks = existing.consistency_checks_json.get("checks", {})
                final_status = (
                    "needs_review"
                    if existing.validation_warnings_json or not all(checks.values())
                    else "completed"
                )
                current = await session.get(StructureJob, job.id)
                if current:
                    current.status = final_status
                    current.progress_percent = 100
                    await session.commit()
                if job.mode == "persist" and final_status == "completed" and not existing.order_id:
                    service = OrderStructuringService(session, settings)
                    await service.persist(job.id, f"automatic-persist:{job.id}")
                return
            document = await session.get(Document, job.document_id)
            if not document or document.status != "completed":
                raise RuntimeError("document_not_ready")
            pages = list(
                (
                    await session.scalars(
                        select(DocumentPage)
                        .where(DocumentPage.document_id == job.document_id)
                        .order_by(DocumentPage.page_number)
                    )
                ).all()
            )
            if not pages:
                raise RuntimeError("document_has_no_extracted_content")
            content = build_document_input(
                [(page.page_number, page.markdown or page.plain_text) for page in pages]
            )

        active_provider = provider or create_provider(settings)
        provided = await asyncio.wait_for(
            OrderStructuringAgent(active_provider).structure(content),
            timeout=settings.structuring_timeout_seconds,
        )
        normalized = normalize_order(provided.parsed)
        consistency = validate_consistency(
            normalized,
            page_count=document.page_count or len(pages),
            min_confidence=settings.structuring_auto_approve_min_confidence,
        )
        duration = int((time.perf_counter() - started) * 1000)
        final_status = "needs_review" if consistency.needs_review else "completed"
        async with SessionLocal() as session:
            current = await session.get(StructureJob, job.id, with_for_update=True)
            if not current or current.status == "cancelled":
                return
            result = StructureResult(
                structure_job_id=current.id,
                document_id=current.document_id,
                schema_version=current.schema_version,
                prompt_version=current.prompt_version,
                raw_provider_response_json=provided.raw_metadata,
                validated_result_json=normalized.model_dump(mode="json"),
                validation_warnings_json=consistency.warnings,
                consistency_checks_json={
                    "summary": consistency.summary.model_dump(),
                    "checks": consistency.checks,
                },
            )
            session.add(result)
            current.status = final_status
            current.progress_percent = 100
            current.completed_at = utcnow()
            current.processing_duration_ms = duration
            current.input_tokens = provided.input_tokens
            current.output_tokens = provided.output_tokens
            await session.commit()

        if job.mode == "persist" and final_status == "completed":
            async with SessionLocal() as session:
                service = OrderStructuringService(session, settings)
                await service.persist(job.id, f"automatic-persist:{job.id}")
        logger.info(
            "order_structuring_completed",
            extra={"document_id": str(job.document_id), "structure_job_id": str(job.id)},
        )
    except Exception as exc:
        duration = int((time.perf_counter() - started) * 1000)
        async with SessionLocal() as session:
            current = await session.get(StructureJob, job.id, with_for_update=True)
            if current:
                retry = current.attempt_count < current.max_attempts
                current.status = "queued" if retry else "failed"
                current.failed_at = None if retry else utcnow()
                current.error_code = (
                    "structure_timeout"
                    if isinstance(exc, TimeoutError)
                    else "structure_provider_error"
                )
                current.error_message_safe = "Não foi possível estruturar o documento."
                current.error_details_internal = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )[-16000:]
                current.processing_duration_ms = duration
                await session.commit()
        logger.exception(
            "order_structuring_failed",
            extra={"document_id": str(job.document_id), "structure_job_id": str(job.id)},
        )


async def structure_worker_loop(once: bool = False) -> None:
    settings = get_settings()
    while True:
        async with SessionLocal() as session:
            job = await claim_structure_job(session)
        if job:
            await process_structure_job(job)
        elif once:
            return
        else:
            await asyncio.sleep(settings.structuring_worker_poll_seconds)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(structure_worker_loop())


if __name__ == "__main__":
    main()
