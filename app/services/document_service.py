import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import UploadFile
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.errors import AppError, pdf_error
from app.db.models import Document, DocumentPage, DocumentResult, ExtractionJob
from app.extraction.validators import inspect_pdf, validate_and_stream
from app.schemas.api import UploadResponse
from app.schemas.extraction import ExtractionOptions
from app.storage import StorageBackend


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_output_formats(value: str | Sequence[str]) -> list[str]:
    raw = value if isinstance(value, str) else ",".join(value)
    formats = list(dict.fromkeys(item.strip().lower() for item in raw.split(",") if item.strip()))
    if not formats:
        formats = ["text", "markdown", "json"]
    invalid = set(formats) - {"text", "markdown", "json"}
    if invalid:
        raise AppError(
            "invalid_output_format", f"Formatos inválidos: {', '.join(sorted(invalid))}", 422
        )
    return formats


@dataclass
class CreatedUpload:
    document: Document
    job: ExtractionJob
    reused: bool


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        storage: StorageBackend,
        settings: Settings,
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings

    async def upload(
        self,
        file: UploadFile,
        engine: str,
        output_formats: str | list[str],
        retain_original: bool,
        idempotency_key: str | None,
        options: ExtractionOptions | None = None,
        temporary: bool = False,
        reuse_by_hash: bool = True,
    ) -> CreatedUpload:
        formats = parse_output_formats(output_formats)
        if engine not in {"auto", "native", "marker"}:
            raise AppError("invalid_engine", "Motor de extração inválido.", 422)
        validated, chunks = await validate_and_stream(file, self.settings)
        try:
            path = await self.storage.save(validated.storage_key, chunks)
            page_count = await inspect_pdf(path, self.settings)
        except Exception:
            await self.storage.delete(validated.storage_key)
            raise

        request_sha256 = validated.sha256
        if options is not None:
            fingerprint = f"{validated.sha256}:{options.model_dump_json()}".encode()
            request_sha256 = hashlib.sha256(fingerprint).hexdigest()
        existing_job = None
        if idempotency_key:
            existing_job = await self.session.scalar(
                select(ExtractionJob)
                .options(selectinload(ExtractionJob.document))
                .where(ExtractionJob.idempotency_key == idempotency_key)
            )
            if existing_job:
                await self.storage.delete(validated.storage_key)
                if existing_job.request_sha256 != request_sha256:
                    raise pdf_error("idempotency_conflict", 409)
                return CreatedUpload(existing_job.document, existing_job, True)

        duplicate = None
        if reuse_by_hash:
            duplicate = await self.session.scalar(
                select(Document)
                .options(selectinload(Document.jobs))
                .where(Document.sha256 == validated.sha256, Document.deleted_at.is_(None))
                .order_by(Document.created_at.desc())
            )
        if duplicate and duplicate.jobs:
            await self.storage.delete(validated.storage_key)
            job = max(duplicate.jobs, key=lambda item: item.created_at)
            return CreatedUpload(duplicate, job, True)

        document = Document(
            original_filename=validated.original_filename,
            safe_filename=validated.safe_filename,
            content_type=validated.content_type,
            file_size_bytes=validated.size,
            sha256=validated.sha256,
            storage_provider="local",
            storage_key=validated.storage_key,
            page_count=page_count,
            status="queued",
            detected_pdf_type="unknown",
            retain_original=retain_original,
            expires_at=(
                utcnow() + timedelta(seconds=self.settings.extraction_job_ttl_seconds)
                if temporary
                else None
            ),
        )
        options = options or ExtractionOptions(engine=engine, output_formats=formats)
        job = ExtractionJob(
            document=document,
            status="queued",
            engine_requested=engine,
            requested_formats=formats,
            max_attempts=self.settings.extraction_max_attempts,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            total_pages=page_count,
            output_format=options.output_format,
            ocr_mode=options.ocr_mode,
            ocr_language=options.ocr_language,
            extract_images=options.extract_images,
            extract_tables=options.extract_tables,
            include_coordinates=options.include_coordinates,
            image_output=options.image_output,
            processing_mode="async",
            current_stage="queued",
            warnings_json=[],
        )
        self.session.add_all([document, job])
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            await self.storage.delete(validated.storage_key)
            raise
        await self.session.refresh(document)
        await self.session.refresh(job)
        return CreatedUpload(document, job, False)

    @staticmethod
    def upload_response(created: CreatedUpload) -> UploadResponse:
        document, job = created.document, created.job
        return UploadResponse(
            document_id=document.id,
            job_id=job.id,
            status=job.status,
            filename=document.original_filename,
            requested_engine=job.engine_requested,
            requested_formats=job.requested_formats,
            status_url=f"/api/v1/documents/{document.id}",
            result_url=f"/api/v1/documents/{document.id}/result",
        )

    async def get_document(self, document_id: uuid.UUID) -> Document:
        document = await self.session.scalar(
            select(Document)
            .options(selectinload(Document.jobs))
            .where(Document.id == document_id, Document.deleted_at.is_(None))
        )
        if not document:
            raise pdf_error("not_found", 404)
        return document

    async def get_job(self, job_id: uuid.UUID) -> ExtractionJob:
        job = await self.session.get(ExtractionJob, job_id)
        if not job:
            raise AppError("job_not_found", "Trabalho de extração não encontrado.", 404)
        return job

    async def get_result(self, document_id: uuid.UUID) -> DocumentResult:
        await self.get_document(document_id)
        result = await self.session.scalar(
            select(DocumentResult).where(DocumentResult.document_id == document_id)
        )
        if not result:
            raise pdf_error("result_not_ready", 409)
        return result

    async def list_pages(self, document_id: uuid.UUID, page: int, page_size: int):
        await self.get_document(document_id)
        base = select(DocumentPage).where(DocumentPage.document_id == document_id)
        total = await self.session.scalar(select(func.count()).select_from(base.subquery()))
        items = list(
            (
                await self.session.scalars(
                    base.order_by(DocumentPage.page_number)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return items, int(total or 0)

    async def get_page(self, document_id: uuid.UUID, page_number: int) -> DocumentPage:
        await self.get_document(document_id)
        item = await self.session.scalar(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id,
                DocumentPage.page_number == page_number,
            )
        )
        if not item:
            raise AppError("page_not_found", "Página não encontrada.", 404)
        return item

    async def list_documents(
        self,
        status: str | None,
        filename: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        page: int,
        page_size: int,
    ):
        statement: Select[tuple[Document]] = select(Document).where(Document.deleted_at.is_(None))
        if status:
            statement = statement.where(Document.status == status)
        if filename:
            statement = statement.where(Document.original_filename.ilike(f"%{filename}%"))
        if created_from:
            statement = statement.where(Document.created_at >= created_from)
        if created_to:
            statement = statement.where(Document.created_at <= created_to)
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        items = list(
            (
                await self.session.scalars(
                    statement.order_by(Document.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return items, int(total or 0)

    async def reprocess(
        self, document_id: uuid.UUID, engine: str, formats: Sequence[str]
    ) -> ExtractionJob:
        document = await self.get_document(document_id)
        if any(job.status in {"queued", "processing"} for job in document.jobs):
            raise AppError(
                "job_already_active", "Já existe um trabalho ativo para este documento.", 409
            )
        job = ExtractionJob(
            document_id=document.id,
            status="queued",
            engine_requested=engine,
            requested_formats=parse_output_formats(formats),
            max_attempts=self.settings.extraction_max_attempts,
            request_sha256=document.sha256,
            total_pages=document.page_count,
        )
        document.status = "queued"
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def delete(self, document_id: uuid.UUID) -> None:
        document = await self.get_document(document_id)
        document.deleted_at = utcnow()
        document.status = "cancelled"
        for job in document.jobs:
            if job.status in {"queued", "processing"}:
                job.status = "cancelled"
        await self.session.commit()
        if self.settings.delete_physical_file:
            await self.storage.delete(document.storage_key)
