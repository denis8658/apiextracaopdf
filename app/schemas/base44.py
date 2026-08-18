from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Base44ProcessingContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evento: Literal["processar_pdf"] = "processar_pdf"
    oportunidade_id: str = Field(min_length=1, max_length=255)
    obra_id: str | None = Field(default=None, min_length=1, max_length=255)
    cliente_id: str | None = Field(default=None, min_length=1, max_length=255)
    vendedor_id: str | None = Field(default=None, min_length=1, max_length=255)
    aprovado_por_id: str | None = Field(default=None, min_length=1, max_length=255)
    aprovado_por_nome: str | None = Field(default=None, min_length=1, max_length=255)
    data_aprovacao: datetime | None = None
    titulo: str | None = Field(default=None, max_length=1000)
    pdf_url: str | None = Field(default=None, max_length=4000)
    valor: float | int | None = Field(default=None, ge=0)


class PlanoCorteProcessingContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evento: Literal["criar_plano_corte"]
    obra_id: str = Field(min_length=1, max_length=255)
    obra_nome: str = Field(min_length=1, max_length=1000)
    cliente_id: str | None = Field(default=None, min_length=1, max_length=255)
    cliente_nome: str | None = Field(default=None, max_length=1000)
    item_pedido_id: str = Field(min_length=1, max_length=255)
    item_idsecao: str | None = Field(default=None, max_length=128)
    item_descricao: str = Field(min_length=1, max_length=2000)
    item_largura: float | int = Field(gt=0)
    item_altura: float | int = Field(gt=0)
    item_quantidade: float | int = Field(gt=0)
    item_ambiente: str | None = Field(default=None, max_length=1000)
    item_vidro: str | None = Field(default=None, max_length=1000)
    item_contramarco: str | None = Field(default=None, max_length=1000)
    pdf_url: str | None = Field(default=None, max_length=4000)


ProcessingContext = Annotated[
    Base44ProcessingContext | PlanoCorteProcessingContext,
    Field(discriminator="evento"),
]
PROCESSING_CONTEXT_ADAPTER: TypeAdapter[ProcessingContext] = TypeAdapter(ProcessingContext)


def parse_processing_context_json(value: str) -> ProcessingContext:
    return PROCESSING_CONTEXT_ADAPTER.validate_json(value)


class Base44ItemPedidoPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oportunidade_id: str = Field(min_length=1, max_length=255)
    obra_id: str | None = Field(default=None, min_length=1, max_length=255)
    cliente_id: str | None = Field(default=None, min_length=1, max_length=255)
    vendedor_id: str | None = Field(default=None, min_length=1, max_length=255)
    aprovado_por_id: str | None = Field(default=None, min_length=1, max_length=255)
    aprovado_por_nome: str | None = Field(default=None, min_length=1, max_length=255)
    data_aprovacao: datetime | None = None
    IDsecao: str = Field(min_length=1, max_length=128)
    titulo: str
    descricao: str
    Largura: float | int | None = Field(default=None, gt=0)
    Altura: float | int | None = Field(default=None, gt=0)
    Qtd: float | int | None = Field(default=None, gt=0)
    Vidro: str
    Ambiente: str
    informacoes: str
    Arremate: str
    tem_arremate: bool
    tem_meia_cana: bool = False
    Contramarco: str
    status_item: Literal["pendente"] = "pendente"
    pendente_medicao: bool = False
    progresso_item_percent: float = Field(default=0, ge=0, le=100)


class Base44CreatedItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str


class Base44BulkCreateResponse(BaseModel):
    records: list[Base44CreatedItem]


class PersistenceResult(BaseModel):
    requested: bool
    status: Literal["not_requested", "saved", "failed", "partial_failure"]
    destination: Literal["base44_itens_pedido", "base44_plano_corte"] = (
        "base44_itens_pedido"
    )
    sent_count: int = 0
    saved_count: int = 0
    record_ids: list[str] = Field(default_factory=list)
    idempotency_replayed: bool = False
    error: dict[str, Any] | None = None


class StructuredItemsSummary(BaseModel):
    items_count: int
    items: list[dict[str, Any]]


class Base44WorkflowResponse(BaseModel):
    success: bool
    job_id: str
    document: dict[str, Any]
    structured: StructuredItemsSummary
    persistence: PersistenceResult


class CutProfileExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    perfil: str = Field(min_length=1, max_length=255)
    qtd: int | float | str
    medida_mm: int | float | str
    corte: str | None = Field(default=None, max_length=255)
    descricao: str | None = Field(default=None, max_length=2000)
    peso_liquido_kg: int | float | str | None = None


class CutProfilesExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    perfis: list[CutProfileExtraction]


class CutProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    perfil: str = Field(min_length=1, max_length=255)
    qtd: float | int = Field(gt=0)
    medida_mm: float | int = Field(gt=0)
    corte: str | None = None
    descricao: str | None = None
    peso_liquido_kg: float | int | None = Field(default=None, ge=0)


class PlanoCorteItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    modelo: str
    tipo: str
    largura: float | int
    altura: float | int
    quantidade: float | int
    ambiente: str | None = None
    vidro: str | None = None


class PlanoCortePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_pedido_id: str
    plano_id: str
    descricao_plano: str
    total_itens: int
    total_perfis: float | int
    itens: list[PlanoCorteItemPayload]
    perfis: list[CutProfile]
    peso_total_kg: float | int
    status: Literal["pendente"] = "pendente"


class PlanoCorteCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    plano_id: str | None = None


class PlanoCorteWorkflowResponse(BaseModel):
    success: bool
    job_id: str
    document: dict[str, Any]
    structured: PlanoCortePayload
    persistence: PersistenceResult


class ItemValidationIssue(BaseModel):
    index: int
    IDsecao: str | None = None
    field: str
    message: str
