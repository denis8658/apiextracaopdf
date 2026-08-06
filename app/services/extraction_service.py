import re
import uuid
from datetime import UTC, datetime

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import Document, DocumentResult, ExtractionEvent, ExtractionJob
from app.schemas.extraction import ExtractionOptions
from app.services.document_service import CreatedUpload, DocumentService
from app.storage import StorageBackend


def utcnow() -> datetime:
    return datetime.now(UTC)


class ExtractionService:
    def __init__(self, session: AsyncSession, storage: StorageBackend, settings: Settings) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings
        self.documents = DocumentService(session, storage, settings)

    async def create(
        self, file: UploadFile, options: ExtractionOptions, idempotency_key: str | None
    ) -> CreatedUpload:
        created = await self.documents.upload(
            file,
            options.engine,
            [options.output_format],
            False,
            idempotency_key,
            options=options,
            temporary=True,
            reuse_by_hash=False,
        )
        if not created.reused:
            self.session.add(
                ExtractionEvent(
                    job_id=created.job.id,
                    event_type="job.queued",
                    data_json={"job_id": str(created.job.id), "progress": 0},
                )
            )
            await self.session.commit()
        return created

    async def get_job(self, job_id: uuid.UUID) -> tuple[ExtractionJob, Document]:
        job = await self.session.scalar(
            select(ExtractionJob)
            .options(selectinload(ExtractionJob.document))
            .where(ExtractionJob.id == job_id)
        )
        if not job:
            raise AppError("JOB_NOT_FOUND", "Trabalho de extração não encontrado.", 404)
        document = job.document
        expires_at = document.expires_at
        if document.status == "expired" or (
            expires_at and expires_at.replace(tzinfo=UTC) <= utcnow()
        ):
            raise AppError("JOB_EXPIRED", "O trabalho e seus dados temporários expiraram.", 410)
        return job, document

    async def get_result(self, job_id: uuid.UUID) -> tuple[ExtractionJob, DocumentResult]:
        job, _ = await self.get_job(job_id)
        if job.status == "cancelled":
            raise AppError("JOB_CANCELLED", "O trabalho foi cancelado.", 409)
        if job.status != "completed":
            raise AppError("RESULT_NOT_READY", "O resultado ainda não está disponível.", 409)
        result = await self.session.scalar(
            select(DocumentResult).where(DocumentResult.document_id == job.document_id)
        )
        if not result:
            raise AppError("RESULT_NOT_READY", "O resultado ainda não está disponível.", 409)
        return job, result

    async def events_after(self, job_id: uuid.UUID, after_id: int) -> list[ExtractionEvent]:
        await self.get_job(job_id)
        return list(
            (
                await self.session.scalars(
                    select(ExtractionEvent)
                    .where(ExtractionEvent.job_id == job_id, ExtractionEvent.id > after_id)
                    .order_by(ExtractionEvent.id)
                )
            ).all()
        )

    async def cancel(self, job_id: uuid.UUID) -> None:
        job, document = await self.get_job(job_id)
        if job.status not in {"completed", "failed", "cancelled"}:
            job.status = "cancelled"
            job.current_stage = "cancelled"
            document.status = "cancelled"
            self.session.add(
                ExtractionEvent(
                    job_id=job.id,
                    event_type="job.cancelled",
                    data_json={"job_id": str(job.id), "status": "cancelled"},
                )
            )
            await self.session.commit()
        await self.storage.delete(document.storage_key)
        await self.storage.delete_prefix(f"jobs/{job.id}")

    async def temporary_file(self, job_id: uuid.UUID, filename: str):
        await self.get_job(job_id)
        if not re.fullmatch(r"p\d+-i\d+\.[A-Za-z0-9]{1,8}", filename):
            raise AppError("JOB_NOT_FOUND", "Arquivo temporário não encontrado.", 404)
        try:
            return await self.storage.open(f"jobs/{job_id}/images/{filename}")
        except AppError as exc:
            raise AppError("JOB_NOT_FOUND", "Arquivo temporário não encontrado.", 404) from exc
