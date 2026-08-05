import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CustomerExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    cpf_cnpj: str | None = None
    rg_ie: str | None = None
    phone: str | None = None
    email: str | None = None
    notes: str | None = None


class OrderItemExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_code: str = Field(min_length=1)
    normalized_code: str | None = None
    occurrence_number: int = Field(gt=0)
    document_order: int = Field(gt=0)
    product_code: str | None = None
    description: str = Field(min_length=1)
    width_mm: int | None = Field(default=None, gt=0)
    height_mm: int | None = Field(default=None, gt=0)
    quantity: int = Field(gt=0)
    environment: str | None = None
    glass: str | None = None
    has_subframe: bool
    has_trim: bool
    information: str | None = None
    source_page: int | None = Field(default=None, gt=0)
    source_text: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class StructuredOrderExtraction(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "order_number": "1790",
                "order_date": "2024-12-02",
                "color": "PINTURA PRETO",
                "customer": {
                    "name": "CLIENTE EXEMPLO",
                    "address": "RUA EXEMPLO Nº100",
                    "city": "CIDADE TESTE",
                    "state": None,
                    "zip_code": None,
                    "cpf_cnpj": "000.000.000-00",
                    "rg_ie": "00.000.000-0",
                    "phone": "00-00000-0000",
                    "email": None,
                    "notes": "ATEND. COMERCIAL TESTE",
                },
                "items": [
                    {
                        "original_code": "J01",
                        "normalized_code": "J01",
                        "occurrence_number": 1,
                        "document_order": 1,
                        "product_code": "FIX1",
                        "description": "FIXO 1 MÓDULO - LINHA GOLD",
                        "width_mm": 1200,
                        "height_mm": 2900,
                        "quantity": 1,
                        "environment": "SALA-1",
                        "glass": "TEMPERADO INCOLOR 8MM",
                        "has_subframe": True,
                        "has_trim": True,
                        "information": None,
                        "source_page": 1,
                        "source_text": None,
                        "confidence": 1.0,
                    }
                ],
                "warnings": [],
            }
        },
    )

    order_number: str = Field(min_length=1)
    order_date: date | None = None
    color: str | None = None
    customer: CustomerExtraction
    items: list[OrderItemExtraction] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class StructureOrderRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"mode": "preview", "force_reprocess": False}},
    )

    mode: Literal["preview", "persist"] = "preview"
    force_reprocess: bool = False


class StructureJobAccepted(BaseModel):
    structure_job_id: uuid.UUID
    document_id: uuid.UUID
    structure_type: Literal["order"] = "order"
    mode: Literal["preview", "persist"]
    status: str
    status_url: str


class StructureJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    structure_type: str
    mode: str
    status: str
    progress_percent: int
    schema_version: str
    prompt_version: str
    attempt_count: int
    max_attempts: int
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    processing_duration_ms: int | None
    error_code: str | None
    error_message_safe: str | None
    created_at: datetime
    updated_at: datetime


class StructureSummary(BaseModel):
    item_records_count: int
    total_units: int
    distinct_codes_count: int


class StructureValidation(BaseModel):
    valid: bool
    needs_review: bool
    warnings: list[str]
    checks: dict[str, bool]


class StructureResultResponse(BaseModel):
    structure_job_id: uuid.UUID
    status: str
    mode: str
    result: StructuredOrderExtraction
    summary: StructureSummary
    validation: StructureValidation
    customer_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None


class PersistStructureResponse(BaseModel):
    customer_id: uuid.UUID
    order_id: uuid.UUID
    items_created: int
    total_units: int
    status: Literal["persisted"] = "persisted"


class CustomerResponse(CustomerExtraction):
    id: uuid.UUID


class OrderItemResponse(OrderItemExtraction):
    id: uuid.UUID
    order_id: uuid.UUID
    review_status: str


class OrderResponse(BaseModel):
    id: uuid.UUID
    source_document_id: uuid.UUID
    order_number: str
    order_date: date | None
    color: str | None
    schema_version: str
    structuring_version: str
    status: str
    customer: CustomerResponse
    items: list[OrderItemResponse]


class StructureVersionResponse(BaseModel):
    structure_job_id: uuid.UUID
    status: str
    mode: str
    schema_version: str
    prompt_version: str
    customer_id: uuid.UUID | None
    order_id: uuid.UUID | None
    created_at: datetime
