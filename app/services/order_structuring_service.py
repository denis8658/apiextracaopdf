import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import (
    Customer,
    Document,
    DocumentResult,
    Order,
    OrderItem,
    StructureJob,
    StructureResult,
)
from app.schemas.order_structuring import (
    CustomerResponse,
    OrderItemResponse,
    OrderResponse,
    PersistStructureResponse,
    StructuredOrderExtraction,
    StructureJobAccepted,
    StructureOrderRequest,
    StructureResultResponse,
    StructureSummary,
    StructureValidation,
    StructureVersionResponse,
)
from app.structuring.normalizer import digits_only, normalize_name


def request_hash(document_id: uuid.UUID, payload: dict) -> str:
    canonical = json.dumps(
        {"document_id": str(document_id), **payload}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class StartedStructure:
    job: StructureJob
    reused: bool


class OrderStructuringService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def start(
        self,
        document_id: uuid.UUID,
        body: StructureOrderRequest,
        idempotency_key: str | None,
    ) -> StartedStructure:
        digest = request_hash(document_id, body.model_dump(mode="json"))
        if idempotency_key:
            existing = await self.session.scalar(
                select(StructureJob).where(StructureJob.idempotency_key == idempotency_key)
            )
            if existing:
                if existing.request_sha256 != digest:
                    raise AppError(
                        "idempotency_conflict",
                        "A chave de idempotência já foi usada com outro conteúdo.",
                        409,
                    )
                return StartedStructure(existing, True)

        document = await self.session.get(Document, document_id)
        if not document or document.deleted_at is not None:
            raise AppError("document_not_found", "Documento não encontrado.", 404)
        if document.status != "completed":
            raise AppError("document_not_ready", "A extração bruta ainda não foi concluída.", 409)
        result_exists = await self.session.scalar(
            select(DocumentResult.id).where(DocumentResult.document_id == document_id)
        )
        if not result_exists:
            raise AppError(
                "document_has_no_extracted_content",
                "O documento não possui conteúdo extraído.",
                409,
            )

        latest = await self.session.scalar(
            select(StructureJob)
            .where(StructureJob.document_id == document_id)
            .order_by(StructureJob.created_at.desc())
        )
        if latest and latest.status in {"queued", "processing"}:
            if not body.force_reprocess:
                return StartedStructure(latest, True)
            raise AppError(
                "structure_job_already_running",
                "Já existe uma estruturação ativa para este documento.",
                409,
            )
        if latest and latest.status in {"completed", "needs_review"} and not body.force_reprocess:
            return StartedStructure(latest, True)

        job = StructureJob(
            document_id=document_id,
            structure_type="order",
            mode=body.mode,
            schema_version=self.settings.order_structuring_schema_version,
            prompt_version=self.settings.order_structuring_prompt_version,
            status="queued",
            max_attempts=self.settings.structuring_max_attempts,
            provider=self.settings.structuring_provider,
            model=self.settings.structuring_model,
            idempotency_key=idempotency_key,
            request_sha256=digest,
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return StartedStructure(job, False)

    @staticmethod
    def accepted(started: StartedStructure) -> StructureJobAccepted:
        job = started.job
        return StructureJobAccepted(
            structure_job_id=job.id,
            document_id=job.document_id,
            mode=job.mode,
            status=job.status,
            status_url=f"/api/v1/structure-jobs/{job.id}",
        )

    async def get_job(self, job_id: uuid.UUID) -> StructureJob:
        job = await self.session.get(StructureJob, job_id)
        if not job:
            raise AppError(
                "structure_job_not_found", "Trabalho de estruturação não encontrado.", 404
            )
        return job

    async def get_result(self, job_id: uuid.UUID) -> StructureResultResponse:
        job = await self.get_job(job_id)
        result = await self.session.scalar(
            select(StructureResult).where(StructureResult.structure_job_id == job_id)
        )
        if not result:
            if job.status in {"queued", "processing"}:
                raise AppError("result_not_ready", "O resultado ainda não está disponível.", 409)
            raise AppError(
                job.error_code or "structure_validation_failed",
                job.error_message_safe or "A estruturação não produziu um resultado válido.",
                422,
            )
        structured = StructuredOrderExtraction.model_validate(result.validated_result_json)
        summary = StructureSummary.model_validate(result.consistency_checks_json["summary"])
        checks = result.consistency_checks_json["checks"]
        return StructureResultResponse(
            structure_job_id=job.id,
            status=job.status,
            mode=job.mode,
            result=structured,
            summary=summary,
            validation=StructureValidation(
                valid=all(checks.values()),
                needs_review=job.status == "needs_review",
                warnings=result.validation_warnings_json,
                checks=checks,
            ),
            customer_id=result.customer_id,
            order_id=result.order_id,
        )

    async def _match_customer(self, data) -> Customer | None:
        if data.cpf_cnpj:
            candidates = list(
                (
                    await self.session.scalars(
                        select(Customer).where(Customer.cpf_cnpj.is_not(None))
                    )
                ).all()
            )
            wanted = digits_only(data.cpf_cnpj)
            match = next(
                (item for item in candidates if digits_only(item.cpf_cnpj) == wanted), None
            )
            if match:
                return match
        if data.email:
            match = await self.session.scalar(select(Customer).where(Customer.email == data.email))
            if match:
                return match
        if data.phone:
            candidates = list(
                (
                    await self.session.scalars(select(Customer).where(Customer.phone.is_not(None)))
                ).all()
            )
            wanted = digits_only(data.phone)
            match = next((item for item in candidates if digits_only(item.phone) == wanted), None)
            if match:
                return match
        candidates = list(
            (
                await self.session.scalars(
                    select(Customer).where(Customer.normalized_name == normalize_name(data.name))
                )
            ).all()
        )
        wanted_address = normalize_name(data.address or "")
        return next(
            (
                item
                for item in candidates
                if wanted_address and normalize_name(item.address or "") == wanted_address
            ),
            None,
        )

    async def persist(
        self, job_id: uuid.UUID, idempotency_key: str | None
    ) -> PersistStructureResponse:
        job = await self.get_job(job_id)
        result = await self.session.scalar(
            select(StructureResult).where(StructureResult.structure_job_id == job_id)
        )
        if not result:
            raise AppError("result_not_ready", "O resultado ainda não está disponível.", 409)
        digest = request_hash(
            job.document_id, {"structure_job_id": str(job_id), "action": "persist"}
        )
        if result.order_id:
            if idempotency_key and result.persist_idempotency_key == idempotency_key:
                if result.persist_request_sha256 != digest:
                    raise AppError("idempotency_conflict", "Chave usada com outro conteúdo.", 409)
                order = await self.get_order(result.order_id)
                return PersistStructureResponse(
                    customer_id=result.customer_id,
                    order_id=result.order_id,
                    items_created=len(order.items),
                    total_units=sum(item.quantity for item in order.items),
                )
            raise AppError("structure_already_persisted", "O resultado já foi persistido.", 409)
        if job.status == "needs_review":
            raise AppError(
                "structure_needs_review",
                "O resultado precisa de revisão antes da persistência.",
                409,
                {"warnings": result.validation_warnings_json},
            )
        if job.status != "completed":
            raise AppError("result_not_ready", "A estruturação ainda não foi concluída.", 409)
        checks = result.consistency_checks_json.get("checks", {})
        if not checks or not all(checks.values()):
            raise AppError(
                "structure_validation_failed",
                "O conteúdo estruturado não passou pelas validações.",
                422,
                {"warnings": result.validation_warnings_json},
            )

        data = StructuredOrderExtraction.model_validate(result.validated_result_json)
        try:
            customer = await self._match_customer(data.customer)
            if customer is None:
                customer = Customer(
                    **data.customer.model_dump(), normalized_name=normalize_name(data.customer.name)
                )
                self.session.add(customer)
                await self.session.flush()
            order = Order(
                customer_id=customer.id,
                source_document_id=job.document_id,
                order_number=data.order_number,
                order_date=data.order_date,
                color=data.color,
                schema_version=job.schema_version,
                structuring_version=str(result.id),
                status="active",
            )
            self.session.add(order)
            await self.session.flush()
            items = []
            for item in data.items:
                review_status = (
                    "needs_review"
                    if item.confidence is not None
                    and item.confidence < self.settings.structuring_auto_approve_min_confidence
                    else "approved"
                )
                record = OrderItem(
                    order_id=order.id,
                    source_document_id=job.document_id,
                    review_status=review_status,
                    **item.model_dump(),
                )
                items.append(record)
            self.session.add_all(items)
            result.customer_id = customer.id
            result.order_id = order.id
            result.persist_idempotency_key = idempotency_key
            result.persist_request_sha256 = digest
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return PersistStructureResponse(
            customer_id=customer.id,
            order_id=order.id,
            items_created=len(items),
            total_units=sum(item.quantity for item in data.items),
        )

    async def get_order(self, order_id: uuid.UUID) -> Order:
        order = await self.session.scalar(
            select(Order)
            .options(selectinload(Order.customer), selectinload(Order.items))
            .where(Order.id == order_id, Order.deleted_at.is_(None))
        )
        if not order:
            raise AppError("order_not_found", "Pedido não encontrado.", 404)
        return order

    @staticmethod
    def order_response(order: Order, include_items: bool = True) -> OrderResponse:
        customer = CustomerResponse(
            id=order.customer.id,
            name=order.customer.name,
            address=order.customer.address,
            city=order.customer.city,
            state=order.customer.state,
            zip_code=order.customer.zip_code,
            cpf_cnpj=order.customer.cpf_cnpj,
            rg_ie=order.customer.rg_ie,
            phone=order.customer.phone,
            email=order.customer.email,
            notes=order.customer.notes,
        )
        items = [
            OrderItemResponse(
                id=item.id,
                order_id=item.order_id,
                review_status=item.review_status,
                original_code=item.original_code,
                normalized_code=item.normalized_code,
                occurrence_number=item.occurrence_number,
                document_order=item.document_order,
                product_code=item.product_code,
                description=item.description,
                width_mm=item.width_mm,
                height_mm=item.height_mm,
                quantity=item.quantity,
                environment=item.environment,
                glass=item.glass,
                has_subframe=item.has_subframe,
                has_trim=item.has_trim,
                information=item.information,
                source_page=item.source_page,
                source_text=item.source_text,
                confidence=item.confidence,
            )
            for item in order.items
        ]
        return OrderResponse(
            id=order.id,
            source_document_id=order.source_document_id,
            order_number=order.order_number,
            order_date=order.order_date,
            color=order.color,
            schema_version=order.schema_version,
            structuring_version=order.structuring_version,
            status=order.status,
            customer=customer,
            items=items if include_items else [],
        )

    async def list_versions(self, document_id: uuid.UUID) -> list[StructureVersionResponse]:
        document = await self.session.get(Document, document_id)
        if not document:
            raise AppError("document_not_found", "Documento não encontrado.", 404)
        jobs = list(
            (
                await self.session.scalars(
                    select(StructureJob)
                    .options(selectinload(StructureJob.result))
                    .where(StructureJob.document_id == document_id)
                    .order_by(StructureJob.created_at.desc())
                )
            ).all()
        )
        return [
            StructureVersionResponse(
                structure_job_id=job.id,
                status=job.status,
                mode=job.mode,
                schema_version=job.schema_version,
                prompt_version=job.prompt_version,
                customer_id=job.result.customer_id if job.result else None,
                order_id=job.result.order_id if job.result else None,
                created_at=job.created_at,
            )
            for job in jobs
        ]
