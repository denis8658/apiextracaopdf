from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.session import get_session
from app.schemas.api import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name, version=settings.app_version)


@router.get("/health/ready", response_model=HealthResponse)
@router.get("/ready", response_model=HealthResponse)
async def ready(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise AppError(
            "database_unavailable", "O banco de dados não está disponível.", 503
        ) from exc
    return HealthResponse(status="ok", service=settings.app_name, version=settings.app_version)
