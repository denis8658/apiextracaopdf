import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Document, ExtractionJob
from app.workers.extraction_worker import claim_job


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
