from pydantic import BaseModel, ConfigDict, Field, field_validator


class PdfItemExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordem: int = Field(gt=0)
    codigo_item: str = Field(min_length=1, max_length=128)
    descricao_produto: str | None = None
    quantidade: int | None = Field(default=None, gt=0)
    largura: int | float | None = Field(default=None, gt=0)
    altura: int | float | None = Field(default=None, gt=0)
    tem_vidro: bool | None = None
    vidro: str | None = None
    tem_contramarco: bool
    tem_arremate: bool
    informacoes: str | None = None

    @field_validator(
        "codigo_item", "descricao_produto", "vidro", "informacoes", mode="before"
    )
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = " ".join(value.split())
        return cleaned or None


class PdfItemsExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itens: list[PdfItemExtraction]


class PdfExtractionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cliente_id: str
    obra_id: str


class StructuredPdfResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contexto: PdfExtractionContext
    itens: list[PdfItemExtraction]
