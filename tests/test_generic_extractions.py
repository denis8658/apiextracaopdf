from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import Headers, UploadFile

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import Document
from app.schemas.extraction import ExtractionOptions
from app.services.extraction_service import ExtractionService
from app.storage import LocalStorageBackend
from app.workers import extraction_worker


def create_job(api_client, sample_pdf, **fields):
    fields = {"cliente_id": "cli_456", "obra_id": "obr_789", **fields}
    return api_client.post(
        "/v1/extractions",
        files={"file": ("documento.pdf", sample_pdf, "application/pdf")},
        data=fields,
    )


def test_create_status_cancel_and_sse_reconnect(api_client, sample_pdf):
    created = create_job(
        api_client,
        sample_pdf,
        output_format="json",
        ocr_mode="never",
        extract_images="false",
        extract_tables="true",
        include_coordinates="true",
    )
    assert created.status_code == 202
    body = created.json()
    job_id = body["job_id"]
    assert body["events_url"].endswith(f"/{job_id}/events")
    assert body["expires_at"]

    status = api_client.get(f"/v1/extractions/{job_id}")
    assert status.status_code == 200
    assert status.json()["current_stage"] == "queued"

    cancelled = api_client.delete(f"/v1/extractions/{job_id}")
    assert cancelled.status_code == 204
    events = api_client.get(f"/v1/extractions/{job_id}/events")
    assert events.status_code == 200
    assert "event: job.queued" in events.text
    assert "event: job.cancelled" in events.text

    last_id = next(
        line.removeprefix("id: ") for line in events.text.splitlines() if line.startswith("id: ")
    )
    resumed = api_client.get(f"/v1/extractions/{job_id}/events", headers={"Last-Event-ID": last_id})
    assert "event: job.queued" not in resumed.text


def test_generic_endpoint_rejects_unknown_output_format(api_client, sample_pdf):
    response = create_job(api_client, sample_pdf, output_format="xml")
    assert response.status_code == 422


def test_generic_endpoint_accepts_page_selection(api_client, sample_pdf):
    created = create_job(api_client, sample_pdf, pages="3,1,3")
    assert created.status_code == 202
    status = api_client.get(f"/v1/extractions/{created.json()['job_id']}")
    assert status.status_code == 200
    assert status.json()["total_pages"] == 2


@pytest.mark.parametrize("pages", ["0", "4", "2-1", "x", "1,,2"])
def test_generic_endpoint_rejects_invalid_page_selection(api_client, sample_pdf, pages):
    response = create_job(api_client, sample_pdf, pages=pages)
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {
        "INVALID_PAGE_SELECTOR",
        "INVALID_PAGE_RANGE",
        "PAGE_OUT_OF_RANGE",
    }


def test_generic_jobs_do_not_reuse_hash_and_idempotency_includes_options(api_client, sample_pdf):
    first = create_job(api_client, sample_pdf, output_format="json")
    second = create_job(api_client, sample_pdf, output_format="json")
    assert first.json()["job_id"] != second.json()["job_id"]

    headers = {"Idempotency-Key": "same-request"}
    original = api_client.post(
        "/v1/extractions",
        files={"file": ("documento.pdf", sample_pdf, "application/pdf")},
        data={"output_format": "json", "cliente_id": "cli", "obra_id": "obra"},
        headers=headers,
    )
    changed = api_client.post(
        "/v1/extractions",
        files={"file": ("documento.pdf", sample_pdf, "application/pdf")},
        data={"output_format": "text", "cliente_id": "cli", "obra_id": "obra"},
        headers=headers,
    )
    assert original.status_code == 202
    assert changed.status_code == 409


def test_generic_upload_requires_context_and_file(api_client, sample_pdf):
    files = {"file": ("documento.pdf", sample_pdf, "application/pdf")}

    missing_cliente = api_client.post(
        "/v1/extractions", files=files, data={"obra_id": "obr_789"}
    )
    assert missing_cliente.status_code == 400
    assert missing_cliente.json()["error"]["message"] == "cliente_id é obrigatório"

    missing_obra = api_client.post(
        "/v1/extractions", files=files, data={"cliente_id": "cli_456"}
    )
    assert missing_obra.status_code == 400
    assert missing_obra.json()["error"]["message"] == "obra_id é obrigatório"

    missing_file = api_client.post(
        "/v1/extractions", data={"cliente_id": "cli_456", "obra_id": "obr_789"}
    )
    assert missing_file.status_code == 400
    assert missing_file.json()["error"]["message"] == "arquivo é obrigatório"


def test_generic_upload_preserves_context_exactly(api_client, sample_pdf):
    response = create_job(
        api_client, sample_pdf, cliente_id="  CLI-Árvore  ", obra_id="obra/789?x=1"
    )
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_expiration_cleanup_removes_temporary_pdf(tmp_path, sample_pdf, monkeypatch):
    database = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'expiry.db'}")
    sessions = async_sessionmaker(database, expire_on_commit=False)
    async with database.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'expiry.db'}",
        storage_path=tmp_path / "storage",
        extraction_job_ttl_seconds=60,
    )
    storage = LocalStorageBackend(settings.storage_path)
    upload = UploadFile(
        BytesIO(sample_pdf),
        filename="temporary.pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )
    async with sessions() as session:
        service = ExtractionService(session, storage, settings)
        created = await service.create(upload, ExtractionOptions(ocr_mode="never"), None)
        created.document.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        key = created.document.storage_key
        with pytest.raises(AppError) as captured:
            await service.get_job(created.job.id)
        assert captured.value.code == "JOB_EXPIRED"
    monkeypatch.setattr(extraction_worker, "SessionLocal", sessions)
    monkeypatch.setattr(extraction_worker, "get_settings", lambda: settings)
    assert await extraction_worker.cleanup_expired() == 1
    assert not await storage.exists(key)
    async with sessions() as session:
        document = await session.scalar(select(Document))
        assert document.status == "expired"
    await database.dispose()
