import asyncio
import json
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

from app.api.dependencies import get_extraction_service
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.schemas.extraction import ExtractionOptions
from app.schemas.extraction_api import (
    ExtractionAccepted,
    ExtractionStatusResponse,
    PublicExtractionResult,
)
from app.schemas.pdf_structuring import StructuredPdfResponse
from app.services.extraction_service import ExtractionService

router = APIRouter(prefix="/v1/extractions", tags=["extractions"])
TERMINAL = {"completed", "failed", "cancelled"}


@router.post("", response_model=ExtractionAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_extraction(
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
    file: Annotated[UploadFile | None, File(description="Arquivo PDF")] = None,
    cliente_id: Annotated[str | None, Form(max_length=255)] = None,
    obra_id: Annotated[str | None, Form(max_length=255)] = None,
    output_format: Annotated[Literal["text", "markdown", "json"], Form()] = "json",
    ocr_mode: Annotated[Literal["auto", "always", "never"], Form()] = "auto",
    ocr_language: Annotated[str, Form(min_length=2, max_length=32)] = "por",
    extract_images: Annotated[bool, Form()] = True,
    extract_tables: Annotated[bool, Form()] = True,
    include_coordinates: Annotated[bool, Form()] = True,
    image_output: Annotated[Literal["reference", "base64", "metadata"], Form()] = "reference",
    processing_mode: Annotated[Literal["async"], Form()] = "async",
    pages: Annotated[str, Form(min_length=1, max_length=255)] = "all",
    structure_output: Annotated[bool, Form()] = False,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)] = None,
) -> ExtractionAccepted:
    if file is None:
        raise AppError("missing_file", "arquivo é obrigatório", 400)
    if cliente_id is None or not cliente_id.strip():
        raise AppError("missing_cliente_id", "cliente_id é obrigatório", 400)
    if obra_id is None or not obra_id.strip():
        raise AppError("missing_obra_id", "obra_id é obrigatório", 400)
    if structure_output and output_format != "json":
        raise AppError(
            "invalid_structured_output_format",
            "structure_output requer output_format=json",
            400,
        )
    options = ExtractionOptions(
        output_format=output_format,
        output_formats=[output_format],
        ocr_mode=ocr_mode,
        ocr_language=ocr_language,
        extract_images=extract_images,
        extract_tables=extract_tables,
        include_coordinates=include_coordinates,
        image_output=image_output,
        pages=pages,
    )
    created = await service.create(
        file, options, idempotency_key, cliente_id, obra_id, structure_output
    )
    job, document = created.job, created.document
    return ExtractionAccepted(
        job_id=job.id,
        status=job.status,
        events_url=f"/v1/extractions/{job.id}/events",
        status_url=f"/v1/extractions/{job.id}",
        result_url=f"/v1/extractions/{job.id}/result",
        expires_at=document.expires_at,
    )


@router.get("/{job_id}", response_model=ExtractionStatusResponse)
async def extraction_status(
    job_id: uuid.UUID, service: Annotated[ExtractionService, Depends(get_extraction_service)]
) -> ExtractionStatusResponse:
    job, document = await service.get_job(job_id)
    error = {"code": job.error_code, "message": job.error_message_safe} if job.error_code else None
    return ExtractionStatusResponse(
        job_id=job.id,
        status=job.status,
        progress=job.progress_percent,
        current_page=job.current_page,
        total_pages=job.total_pages,
        current_stage=job.current_stage,
        created_at=job.created_at,
        updated_at=job.updated_at,
        expires_at=document.expires_at,
        warnings=job.warnings_json or [],
        error=error,
    )


@router.get("/{job_id}/result")
async def extraction_result(
    job_id: uuid.UUID, service: Annotated[ExtractionService, Depends(get_extraction_service)]
):
    job, result = await service.get_result(job_id)
    if job.output_format == "text":
        return PlainTextResponse(result.plain_text, media_type="text/plain; charset=utf-8")
    if job.output_format == "markdown":
        return PlainTextResponse(result.markdown, media_type="text/markdown; charset=utf-8")
    schema = StructuredPdfResponse if job.structure_output else PublicExtractionResult
    payload = schema.model_validate(result.structured_json).model_dump(mode="json")
    return JSONResponse(payload)


@router.get("/{job_id}/events")
async def extraction_events(
    job_id: uuid.UUID,
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    await service.get_job(job_id)
    try:
        cursor = max(0, int(last_event_id or 0))
    except ValueError:
        cursor = 0

    async def stream():
        nonlocal cursor
        started = time.monotonic()
        last_heartbeat = started
        while time.monotonic() - started < settings.extraction_sse_timeout_seconds:
            service.session.expire_all()
            events = await service.events_after(job_id, cursor)
            for event in events:
                cursor = event.id
                data = json.dumps(event.data_json, ensure_ascii=False)
                yield f"id: {event.id}\nevent: {event.event_type}\ndata: {data}\n\n"
            job, _ = await service.get_job(job_id)
            if job.status in TERMINAL and not events:
                return
            now = time.monotonic()
            if now - last_heartbeat >= settings.extraction_sse_heartbeat_seconds:
                yield f": heartbeat {int(now)}\n\n"
                last_heartbeat = now
            await asyncio.sleep(min(1.0, settings.extraction_worker_poll_seconds))

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/{job_id}", status_code=204)
async def cancel_extraction(
    job_id: uuid.UUID, service: Annotated[ExtractionService, Depends(get_extraction_service)]
) -> Response:
    await service.cancel(job_id)
    return Response(status_code=204)


@router.get("/{job_id}/files/{filename}", include_in_schema=False)
async def temporary_image(
    job_id: uuid.UUID,
    filename: str,
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
) -> FileResponse:
    path = await service.temporary_file(job_id, filename)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=60"})
