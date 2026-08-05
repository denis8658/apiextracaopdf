from collections.abc import AsyncIterator

import pymupdf
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.dependencies import get_document_service
from app.core.config import Settings
from app.db.base import Base
from app.main import app
from app.services.document_service import DocumentService
from app.storage import LocalStorageBackend


@pytest.fixture
def sample_pdf() -> bytes:
    document = pymupdf.open()
    for number in range(1, 4):
        page = document.new_page()
        page.insert_text(
            (72, 72),
            f"Orçamento 1790 - página {number}\n"
            "Cliente: CLIENTE EXEMPLO\n"
            "Descrição: JANELA PIVOTANTE",
        )
    content = document.tobytes()
    document.close()
    return content


@pytest_asyncio.fixture
async def api_client(tmp_path) -> AsyncIterator[TestClient]:
    database = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    sessions = async_sessionmaker(database, expire_on_commit=False)
    async with database.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        storage_path=tmp_path / "storage",
        cors_allowed_origins=["http://localhost:5173"],
    )

    async def service_override() -> AsyncIterator[DocumentService]:
        async with sessions() as session:
            yield DocumentService(session, LocalStorageBackend(settings.storage_path), settings)

    app.dependency_overrides[get_document_service] = service_override
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()
    await database.dispose()
