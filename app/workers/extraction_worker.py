import asyncio
import base64
import logging
import time
import traceback
from datetime import UTC, datetime

if __name__ == "__main__":
    from app.core.runtime import reexec_with_project_python

    reexec_with_project_python("app.workers.extraction_worker")

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.db.models import (
    Document,
    DocumentPage,
    DocumentResult,
    ExtractionEvent,
    ExtractionJob,
)
from app.db.session import SessionLocal
from app.extraction.item_association import associate_result
from app.extraction.normalizer import normalize_result
from app.extraction.router import ExtractionRouter
from app.integrations.base44 import (
    Base44ItensPedidoClient,
    Base44PlanoCorteClient,
    map_batch,
    payload_hash,
    plano_corte_payload_hash,
)
from app.schemas.base44 import (
    PROCESSING_CONTEXT_ADAPTER,
    Base44ProcessingContext,
    Base44WorkflowResponse,
    PersistenceResult,
    PlanoCortePayload,
    PlanoCorteProcessingContext,
    PlanoCorteWorkflowResponse,
    ProcessingContext,
)
from app.schemas.extraction import ExtractionOptions
from app.schemas.extraction_api import PublicExtractionResult
from app.schemas.pdf_structuring import StructuredPdfResponse
from app.storage import LocalStorageBackend
from app.structuring.event_router import PostExtractionEventRouter

logger = logging.getLogger(__name__)


class StructuringStageError(Exception):
    pass


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
                or_(
                    ExtractionJob.attempt_count < ExtractionJob.max_attempts,
                    ExtractionJob.current_stage == "persistence_retry",
                ),
            )
            .order_by(ExtractionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        persistence_only = job.current_stage == "persistence_retry"
        job.status = "processing"
        job.current_stage = "persistence_retry" if persistence_only else "extracting_native"
        if not persistence_only:
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
            if image_output in {"base64", "both"}:
                encoded = base64.b64encode(image.raw_bytes).decode("ascii")
                image.content_encoding = "base64"
                image.content_base64 = encoded
                if image_output == "base64":
                    image.reference = f"data:{image.mime_type};base64,{encoded}"
            if image_output in {"reference", "both"}:
                filename = f"{image.image_id}.{image.format}"
                key = f"jobs/{job_id}/images/{filename}"

                async def chunks(value=image.raw_bytes):
                    yield value

                # A estruturação acontece depois das imagens e pode disparar um retry do mesmo
                # job. Remova a referência da tentativa anterior para que o retry seja idempotente.
                await storage.delete(key)
                await storage.save(key, chunks())
                image.reference = f"/v1/extractions/{job_id}/files/{filename}"
            image.raw_bytes = None


def _bbox(value):
    if not value or len(value) != 4:
        return None
    return {"x0": value[0], "y0": value[1], "x1": value[2], "y1": value[3]}


def public_result(document, job, result, duration_ms: int) -> dict:
    requested_pages = job.selected_pages_json or list(range(1, (document.page_count or 0) + 1))
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
                        "visual_group_id": image.visual_group_id,
                        "reference": image.reference,
                        "mime_type": image.mime_type,
                        "content_encoding": image.content_encoding,
                        "content_base64": image.content_base64,
                        "nearby_text": image.nearby_text,
                        "related_item_id": image.related_item_id,
                        "related_code": image.related_code,
                        "related_description": image.related_description,
                        "association_confidence": image.association_confidence,
                        "association_method": image.association_method,
                        "requires_review": image.requires_review,
                        "association_candidates": [
                            candidate.model_dump(mode="json")
                            for candidate in image.association_candidates
                        ],
                    }
                    for image in page.images
                ],
                "items": [
                    {
                        "id": item.item_id,
                        "page_number": item.page_number,
                        "code": item.code,
                        "description": item.description,
                        "bbox": _bbox(item.bbox),
                        "code_block_id": item.code_block_id,
                        "description_block_id": item.description_block_id,
                        "text_block_ids": item.text_block_ids,
                        "image_ids": item.image_ids,
                        "table_ids": item.table_ids,
                        "association_confidence": item.association_confidence,
                        "requires_review": item.requires_review,
                    }
                    for item in page.items
                ],
                "warnings": page.warnings,
            }
        )
    methods = [page.extraction_method for page in result.pages]
    metadata = result.metadata
    return {
        "schema_version": "1.0",
        "contexto": (
            {"cliente_id": document.cliente_id, "obra_id": document.obra_id}
            if document.cliente_id is not None and document.obra_id is not None
            else None
        ),
        "document": {
            "filename": document.original_filename,
            "mime_type": document.content_type,
            "page_count": document.page_count,
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
        "page_selection": {
            "selector": job.page_selector,
            "requested_pages": requested_pages,
            "processed_pages": [page.page_number for page in result.pages],
            "skipped_pages": sorted(
                set(requested_pages) - {page.page_number for page in result.pages}
            ),
            "document_page_count": document.page_count,
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


def processing_context(job: ExtractionJob, document: Document) -> ProcessingContext:
    raw = job.base44_context_json or {
        "evento": "processar_pdf",
        "oportunidade_id": job.oportunidade_id,
        "obra_id": document.obra_id,
        "cliente_id": document.cliente_id,
        "vendedor_id": job.vendedor_id,
    }
    return PROCESSING_CONTEXT_ADAPTER.validate_python(raw)


async def persist_success(session, job_id, routed, duration_ms: int, storage, settings) -> None:
    normalized = normalize_result(routed.result)
    job = await session.get(ExtractionJob, job_id)
    if not job:
        return
    document = await session.get(Document, job.document_id)
    job.current_stage = "merging_content"
    job.progress_percent = 95
    add_event(session, job.id, "merging_content", {"progress": 95})
    await session.commit()
    result = associate_result(normalized, settings)
    await _save_images(storage, job.id, result, job.image_output)
    extraction_payload = PublicExtractionResult.model_validate(
        public_result(document, job, result, duration_ms)
    ).model_dump(mode="json")
    payload = extraction_payload
    structured: StructuredPdfResponse | None = None
    plano_corte: PlanoCortePayload | None = None
    context: ProcessingContext | None = None
    if job.structure_output:
        context = processing_context(job, document)
        stage = (
            "structuring_cut_plan"
            if isinstance(context, PlanoCorteProcessingContext)
            else "structuring_items"
        )
        job.current_stage = stage
        job.progress_percent = 96
        add_event(session, job.id, stage, {"progress": 96, "evento": context.evento})
        await session.commit()
        try:
            routed_structure = await PostExtractionEventRouter(settings).route(
                context=context,
                extracted_content=result.plain_text,
            )
            if isinstance(routed_structure.result, StructuredPdfResponse):
                structured = routed_structure.result
                payload = structured.model_dump(mode="json")
                structured_count = len(structured.itens)
            else:
                plano_corte = routed_structure.result
                payload = plano_corte.model_dump(mode="json")
                structured_count = len(plano_corte.perfis)
            logger.info(
                "post_extraction_routed",
                extra={
                    "job_id": str(job.id),
                    "evento": routed_structure.evento,
                    "item_pedido_id": getattr(context, "item_pedido_id", None),
                    "oportunidade_id": getattr(context, "oportunidade_id", None),
                    "structurer": PostExtractionEventRouter.STRUCTURERS[context.evento],
                    "structured_count": structured_count,
                    "structuring_status": "success",
                    "destination": routed_structure.destination,
                },
            )
        except AppError:
            raise
        except Exception as exc:
            raise StructuringStageError from exc
    if job.save_to_base44 and (structured or plano_corte):
        assert context is not None
        job.current_stage = "validating_structured_data"
        job.progress_percent = 97
        add_event(
            session,
            job.id,
            "validating_structured_data",
            {"progress": 97, "evento": context.evento},
        )
        await session.commit()
        mapped = []
        persistence: PersistenceResult
        try:
            if isinstance(context, Base44ProcessingContext):
                assert structured is not None
                if len(structured.itens) > settings.max_structured_items:
                    raise AppError(
                        "too_many_structured_items",
                        "O documento excede o limite de itens estruturados.",
                        422,
                    )
                mapped = map_batch(
                    structured,
                    context.oportunidade_id,
                    context.obra_id,
                    context.cliente_id,
                    context.vendedor_id,
                    context,
                )
                current_hash = payload_hash(mapped)
                sent_count = len(mapped)
                destination = "base44_itens_pedido"
            else:
                assert plano_corte is not None
                current_hash = plano_corte_payload_hash(plano_corte)
                sent_count = 1
                destination = "base44_plano_corte"
            if job.persistence_payload_hash and job.persistence_payload_hash != current_hash:
                raise AppError(
                    "idempotency_conflict",
                    "A chave de idempotência já foi usada com outro conteúdo.",
                    409,
                )
            job.persistence_payload_hash = current_hash
            job.current_stage = "mapping_base44_payload"
            add_event(session, job.id, "mapping_base44_payload", {"progress": 98})
            await session.commit()
            job.current_stage = "saving_base44"
            job.persistence_status = "saving"
            add_event(
                session,
                job.id,
                "saving_base44",
                {"progress": 99, "destination": destination},
            )
            await session.commit()
            if isinstance(context, Base44ProcessingContext):
                saved = await Base44ItensPedidoClient(settings).create_bulk(
                    mapped, job.idempotency_key
                )
                record_ids = [record.id for record in saved.records]
            else:
                assert plano_corte is not None
                saved_plan = await Base44PlanoCorteClient(settings).create(
                    plano_corte, job.idempotency_key
                )
                record_ids = [saved_plan.id]
            persistence = PersistenceResult(
                requested=True,
                status="saved",
                destination=destination,
                sent_count=sent_count,
                saved_count=len(record_ids),
                record_ids=record_ids,
            )
            job.persistence_status = "saved"
            logger.info(
                "base44_persistence_completed",
                extra={
                    "job_id": str(job.id),
                    "evento": context.evento,
                    "item_pedido_id": getattr(context, "item_pedido_id", None),
                    "oportunidade_id": getattr(context, "oportunidade_id", None),
                    "persistence": "success",
                    "saved_count": len(record_ids),
                },
            )
            add_event(
                session,
                job.id,
                "persistence_completed",
                {
                    "saved_count": persistence.saved_count,
                    "record_ids": persistence.record_ids,
                    "progress": 100,
                },
            )
        except AppError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            status = "partial_failure" if exc.code == "base44_partial_failure" else "failed"
            error_code = (
                "ERRO_PERSISTENCIA_PLANO_CORTE"
                if isinstance(context, PlanoCorteProcessingContext)
                else exc.code
            )
            persistence = PersistenceResult(
                requested=True,
                status=status,
                destination=(
                    "base44_plano_corte"
                    if isinstance(context, PlanoCorteProcessingContext)
                    else "base44_itens_pedido"
                ),
                sent_count=1 if plano_corte is not None else len(mapped),
                saved_count=int(details.get("saved_count", 0)),
                record_ids=list(details.get("record_ids", [])),
                error={
                    "code": error_code,
                    "message": exc.message,
                    "details": {
                        "cause": exc.code,
                        "base44": exc.details,
                        "item_pedido_id": getattr(context, "item_pedido_id", None),
                    },
                },
            )
            job.persistence_status = status
            job.error_code = error_code
            job.error_message_safe = exc.message
            logger.warning(
                "base44_persistence_failed",
                extra={
                    "job_id": str(job.id),
                    "evento": context.evento,
                    "item_pedido_id": getattr(context, "item_pedido_id", None),
                    "oportunidade_id": getattr(context, "oportunidade_id", None),
                    "persistence": status,
                    "error_code": error_code,
                },
            )
            add_event(
                session,
                job.id,
                "persistence_failed",
                {"status": status, "error": persistence.error, "progress": 100},
            )
        job.persistence_result_json = persistence.model_dump(mode="json")
        if isinstance(context, Base44ProcessingContext):
            assert structured is not None
            payload = Base44WorkflowResponse(
                success=persistence.status == "saved",
                job_id=str(job.id),
                document={"format": "pdf", "pages": document.page_count},
                structured={
                    "items_count": len(structured.itens),
                    "items": [item.model_dump(mode="json") for item in structured.itens],
                },
                persistence=persistence,
            ).model_dump(mode="json")
        else:
            assert plano_corte is not None
            payload = PlanoCorteWorkflowResponse(
                success=persistence.status == "saved",
                job_id=str(job.id),
                document={"format": "pdf", "pages": document.page_count},
                structured=plano_corte,
                persistence=persistence,
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
        job.status = (
            "persistence_failed"
            if job.persistence_status in {"failed", "partial_failure"}
            else "completed"
        )
        job.current_stage = job.status
        job.engine_used = result.engine
        job.engine_selection_reason = routed.reason
        job.progress_percent = 100
        job.current_page = result.pages[-1].page_number if result.pages else None
        job.total_pages = len(extraction_payload["page_selection"]["requested_pages"])
        job.completed_at = now
        job.processing_duration_ms = duration_ms
        job.engine_version = result.engine_version
        job.warnings_json = extraction_payload["processing"]["warnings"]
        document.status = "completed"
        document.detected_pdf_type = routed.detected_pdf_type
        document.extraction_engine = result.engine
        add_event(
            session,
            job.id,
            "job.completed",
            {
                "progress": 100,
                "status": job.status,
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
        deterministic_error = isinstance(exc, AppError) and 400 <= exc.status_code < 500
        retry = job.attempt_count < job.max_attempts and not deterministic_error
        job.status = "queued" if retry else "failed"
        stage = (
            "structuring"
            if isinstance(exc, StructuringStageError | AppError)
            else "extraction"
        )
        job.current_stage = "retrying" if retry else f"{stage}_failed"
        job.failed_at = None if retry else utcnow()
        if isinstance(exc, AppError):
            job.error_code = exc.code
            job.error_message_safe = exc.message
            job.persistence_result_json = {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            }
        else:
            job.error_code = (
                "STRUCTURING_FAILED" if stage == "structuring" else "EXTRACTION_FAILED"
            )
            job.error_message_safe = (
                "O PDF foi extraído, mas não foi possível estruturar os dados."
                if stage == "structuring"
                else "Não foi possível extrair o conteúdo do PDF."
            )
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
                "stage": stage,
            },
        )


async def retry_base44_persistence(job: ExtractionJob, settings) -> None:
    async with SessionLocal() as session:
        current = await session.get(ExtractionJob, job.id)
        if not current:
            return
        document = await session.get(Document, current.document_id)
        stored = await session.scalar(
            select(DocumentResult).where(DocumentResult.document_id == current.document_id)
        )
        if not document or not stored:
            await persist_failure(session, current.id, RuntimeError("stored result missing"), 0)
            return
        context = processing_context(current, document)
        mapped = []
        plano_corte: PlanoCortePayload | None = None
        if isinstance(context, Base44ProcessingContext):
            workflow: Base44WorkflowResponse | PlanoCorteWorkflowResponse = (
                Base44WorkflowResponse.model_validate(stored.structured_json)
            )
            assert isinstance(workflow, Base44WorkflowResponse)
            structured = StructuredPdfResponse(
                contexto={"cliente_id": document.cliente_id, "obra_id": document.obra_id},
                itens=workflow.structured.items,
            )
            mapped = map_batch(
                structured,
                context.oportunidade_id,
                context.obra_id,
                context.cliente_id,
                context.vendedor_id,
                context,
            )
            current_hash = payload_hash(mapped)
        else:
            workflow = PlanoCorteWorkflowResponse.model_validate(stored.structured_json)
            plano_corte = workflow.structured
            current_hash = plano_corte_payload_hash(plano_corte)
        if current_hash != current.persistence_payload_hash:
            current.status = "persistence_failed"
            current.current_stage = "persistence_failed"
            current.error_code = "idempotency_conflict"
            current.error_message_safe = "O conteúdo persistido não corresponde ao lote original."
            await session.commit()
            return
        try:
            if isinstance(context, Base44ProcessingContext):
                saved = await Base44ItensPedidoClient(settings).create_bulk(
                    mapped, current.idempotency_key
                )
                record_ids = [record.id for record in saved.records]
                destination = "base44_itens_pedido"
                sent_count = len(mapped)
            else:
                assert plano_corte is not None
                saved_plan = await Base44PlanoCorteClient(settings).create(
                    plano_corte, current.idempotency_key
                )
                record_ids = [saved_plan.id]
                destination = "base44_plano_corte"
                sent_count = 1
            persistence = PersistenceResult(
                requested=True,
                status="saved",
                destination=destination,
                sent_count=sent_count,
                saved_count=len(record_ids),
                record_ids=record_ids,
                idempotency_replayed=True,
            )
            workflow.success = True
            workflow.persistence = persistence
            stored.structured_json = workflow.model_dump(mode="json")
            current.persistence_status = "saved"
            current.persistence_result_json = persistence.model_dump(mode="json")
            current.status = "completed"
            current.current_stage = "completed"
            current.progress_percent = 100
            current.completed_at = utcnow()
            add_event(
                session,
                current.id,
                "persistence_completed",
                {
                    "saved_count": persistence.saved_count,
                    "record_ids": persistence.record_ids,
                    "idempotency_replayed": True,
                    "progress": 100,
                },
            )
        except AppError as exc:
            error_code = (
                "ERRO_PERSISTENCIA_PLANO_CORTE"
                if isinstance(context, PlanoCorteProcessingContext)
                else exc.code
            )
            current.status = "persistence_failed"
            current.current_stage = "persistence_failed"
            current.persistence_status = "failed"
            current.error_code = error_code
            current.error_message_safe = exc.message
            add_event(
                session,
                current.id,
                "persistence_failed",
                {
                    "error": {
                        "code": error_code,
                        "message": exc.message,
                        "details": {"cause": exc.code},
                    },
                    "progress": 100,
                },
            )
        await session.commit()


async def process_job(job: ExtractionJob) -> None:
    settings = get_settings()
    storage = LocalStorageBackend(settings.storage_path)
    router = ExtractionRouter(settings)
    started = time.perf_counter()

    if job.current_stage == "persistence_retry":
        await retry_base44_persistence(job, settings)
        return

    async def progress(event_type: str, data: dict) -> None:
        async with SessionLocal() as progress_session, progress_session.begin():
            current = await progress_session.get(ExtractionJob, job.id)
            if not current or current.status == "cancelled":
                raise asyncio.CancelledError
            page = data.get("page")
            completed_pages = data.get("completed_pages")
            selected_total = data.get("selected_total") or current.total_pages or 1
            if page is not None:
                current.current_page = int(page)
            if completed_pages is not None:
                current.progress_percent = min(
                    95, int(int(completed_pages) / max(1, int(selected_total)) * 95)
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
        selected_pages = job.selected_pages_json or list(
            range(1, (document.page_count or 0) + 1)
        )
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
            pages=job.page_selector,
            selected_pages=selected_pages,
        )
        routed = await asyncio.wait_for(
            router.extract(path, options, progress), timeout=settings.extraction_timeout_seconds
        )
        duration = int((time.perf_counter() - started) * 1000)
        async with SessionLocal() as session:
            await persist_success(session, job.id, routed, duration, storage, settings)
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
