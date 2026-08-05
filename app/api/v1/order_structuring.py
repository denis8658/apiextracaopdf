import uuid
from typing import Annotated

from fastapi import APIRouter, Header, status
from fastapi.params import Depends

from app.api.dependencies import get_order_structuring_service
from app.schemas.order_structuring import (
    OrderItemResponse,
    OrderResponse,
    PersistStructureResponse,
    StructureJobAccepted,
    StructureJobResponse,
    StructureOrderRequest,
    StructureResultResponse,
    StructureVersionResponse,
)
from app.services.order_structuring_service import OrderStructuringService

router = APIRouter(prefix="/api/v1", tags=["order-structuring"])


@router.post(
    "/documents/{document_id}/structure/order",
    response_model=StructureJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Agenda a estruturação de um pedido extraído",
)
async def structure_order(
    document_id: uuid.UUID,
    body: StructureOrderRequest,
    service: Annotated[OrderStructuringService, Depends(get_order_structuring_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StructureJobAccepted:
    return service.accepted(await service.start(document_id, body, idempotency_key))


@router.post(
    "/documents/{document_id}/structure/order/reprocess",
    response_model=StructureJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cria uma nova versão da estruturação",
)
async def reprocess_order(
    document_id: uuid.UUID,
    body: StructureOrderRequest,
    service: Annotated[OrderStructuringService, Depends(get_order_structuring_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StructureJobAccepted:
    forced = body.model_copy(update={"force_reprocess": True})
    return service.accepted(await service.start(document_id, forced, idempotency_key))


@router.get("/structure-jobs/{job_id}", response_model=StructureJobResponse)
async def get_structure_job(
    job_id: uuid.UUID,
    service: Annotated[OrderStructuringService, Depends(get_order_structuring_service)],
) -> StructureJobResponse:
    return StructureJobResponse.model_validate(await service.get_job(job_id))


@router.get("/structure-jobs/{job_id}/result", response_model=StructureResultResponse)
async def get_structure_result(
    job_id: uuid.UUID,
    service: Annotated[OrderStructuringService, Depends(get_order_structuring_service)],
) -> StructureResultResponse:
    return await service.get_result(job_id)


@router.post(
    "/structure-jobs/{job_id}/persist",
    response_model=PersistStructureResponse,
    summary="Persiste um preview validado em uma transação",
)
async def persist_structure_result(
    job_id: uuid.UUID,
    service: Annotated[OrderStructuringService, Depends(get_order_structuring_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PersistStructureResponse:
    return await service.persist(job_id, idempotency_key)


@router.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: uuid.UUID,
    service: Annotated[OrderStructuringService, Depends(get_order_structuring_service)],
) -> OrderResponse:
    return service.order_response(await service.get_order(order_id))


@router.get("/orders/{order_id}/items", response_model=list[OrderItemResponse])
async def list_order_items(
    order_id: uuid.UUID,
    service: Annotated[OrderStructuringService, Depends(get_order_structuring_service)],
) -> list[OrderItemResponse]:
    return service.order_response(await service.get_order(order_id)).items


@router.get(
    "/documents/{document_id}/structure-results", response_model=list[StructureVersionResponse]
)
async def list_structure_versions(
    document_id: uuid.UUID,
    service: Annotated[OrderStructuringService, Depends(get_order_structuring_service)],
) -> list[StructureVersionResponse]:
    return await service.list_versions(document_id)
