import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Header, Query, Response, UploadFile, status
from fastapi.params import Depends
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.dependencies import get_document_service
from app.core.errors import AppError
from app.schemas.api import (
    DocumentListItem,
    DocumentStatusResponse,
    JobResponse,
    PageResponse,
    PaginatedDocuments,
    PaginatedPages,
    Progress,
    ReprocessRequest,
    UploadResponse,
)
from app.services.document_service import DocumentService

router = APIRouter(prefix="/api/v1", tags=["documents"])


@router.post(
    "/documents",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Envia um PDF e agenda sua extração",
)
async def upload_document(
    service: Annotated[DocumentService, Depends(get_document_service)],
    file: Annotated[UploadFile | None, File(description="Arquivo PDF")] = None,
    cliente_id: Annotated[str | None, Form(max_length=255)] = None,
    obra_id: Annotated[str | None, Form(max_length=255)] = None,
    engine: Annotated[Literal["auto", "native", "marker"], Form()] = "auto",
    output_formats: Annotated[str, Form()] = "text,markdown,json",
    retain_original: Annotated[bool, Form()] = True,
    pages: Annotated[str, Form(min_length=1, max_length=255)] = "all",
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)] = None,
) -> UploadResponse:
    if file is None:
        raise AppError("missing_file", "arquivo é obrigatório", 400)
    if cliente_id is None or not cliente_id.strip():
        raise AppError("missing_cliente_id", "cliente_id é obrigatório", 400)
    if obra_id is None or not obra_id.strip():
        raise AppError("missing_obra_id", "obra_id é obrigatório", 400)
    created = await service.upload(
        file,
        engine,
        output_formats,
        retain_original,
        idempotency_key,
        page_selector=pages,
        cliente_id=cliente_id,
        obra_id=obra_id,
    )
    return service.upload_response(created)


@router.get("/documents/{document_id}", response_model=DocumentStatusResponse)
async def get_document(
    document_id: uuid.UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentStatusResponse:
    document = await service.get_document(document_id)
    job = max(document.jobs, key=lambda item: item.created_at) if document.jobs else None
    return DocumentStatusResponse(
        id=document.id,
        filename=document.original_filename,
        status=document.status,
        detected_pdf_type=document.detected_pdf_type,
        engine_requested=job.engine_requested if job else None,
        engine_used=job.engine_used if job else None,
        progress=Progress(
            percent=job.progress_percent if job else 0,
            current_page=job.current_page if job else None,
            total_pages=job.total_pages if job else document.page_count,
        ),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.get("/extraction-jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> JobResponse:
    return JobResponse.model_validate(await service.get_job(job_id))


@router.get("/documents/{document_id}/result")
async def get_result(
    document_id: uuid.UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
    format: Literal["text", "markdown", "json"] = Query("json"),
):
    result = await service.get_result(document_id)
    if format == "text":
        return PlainTextResponse(result.plain_text, media_type="text/plain; charset=utf-8")
    if format == "markdown":
        return PlainTextResponse(result.markdown, media_type="text/markdown; charset=utf-8")
    document_payload = dict(result.structured_json)
    contexto = document_payload.pop("contexto", None)
    document_payload["plain_text"] = result.plain_text
    document_payload["markdown"] = result.markdown
    methods = {
        page.get("extraction_method")
        for page in result.structured_json.get("pages", [])
        if page.get("extraction_method")
    }
    document_payload["engine"] = " + ".join(sorted(methods)) or "unknown"
    return JSONResponse(
        {
            "schema_version": result.schema_version,
            "contexto": contexto,
            "metadata": result.metadata_json,
            "document": document_payload,
        }
    )


@router.get("/documents/{document_id}/pages", response_model=PaginatedPages)
async def list_pages(
    document_id: uuid.UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedPages:
    items, total = await service.list_pages(document_id, page, page_size)
    return PaginatedPages(
        items=[PageResponse.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/documents/{document_id}/pages/{page_number}", response_model=PageResponse)
async def get_page(
    document_id: uuid.UUID,
    page_number: int,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> PageResponse:
    return PageResponse.model_validate(await service.get_page(document_id, page_number))


@router.get("/documents", response_model=PaginatedDocuments)
async def list_documents(
    service: Annotated[DocumentService, Depends(get_document_service)],
    status_filter: str | None = Query(None, alias="status"),
    filename: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedDocuments:
    items, total = await service.list_documents(
        status_filter, filename, created_from, created_to, page, page_size
    )
    return PaginatedDocuments(
        items=[
            DocumentListItem(
                id=item.id,
                filename=item.original_filename,
                status=item.status,
                page_count=item.page_count,
                created_at=item.created_at,
            )
            for item in items
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/documents/{document_id}/reprocess", response_model=JobResponse, status_code=202)
async def reprocess(
    document_id: uuid.UUID,
    body: ReprocessRequest,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> JobResponse:
    return JobResponse.model_validate(
        await service.reprocess(document_id, body.engine, body.output_formats)
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> Response:
    await service.delete(document_id)
    return Response(status_code=204)
