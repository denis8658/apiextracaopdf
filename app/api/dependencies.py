from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.services.document_service import DocumentService
from app.services.order_structuring_service import OrderStructuringService
from app.storage import LocalStorageBackend


def get_document_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentService:
    return DocumentService(session, LocalStorageBackend(settings.storage_path), settings)


def get_order_structuring_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OrderStructuringService:
    return OrderStructuringService(session, settings)
