from pydantic import BaseModel, ConfigDict, Field, field_validator


class PdfItemExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordem: int = Field(gt=0)
    codigo_item: str = Field(min_length=1, max_length=128)
    descricao_produto: str | None = None
    titulo: str | None = None
    quantidade: int | float | str | None = None
    largura: int | float | str | None = None
    altura: int | float | str | None = None
    tem_vidro: bool | None = None
    vidro: str | None = None
    tem_contramarco: bool
    tem_arremate: bool
    tem_meia_cana: bool = False
    ambiente: str | None = None
    arremate: str | None = None
    contramarco: str | None = None
    informacoes: str | None = None

    @field_validator(
        "codigo_item",
        "titulo",
        "descricao_produto",
        "vidro",
        "ambiente",
        "arremate",
        "contramarco",
        "informacoes",
        mode="before",
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

    cliente_id: str | None = None
    obra_id: str | None = None


class StructuredPdfResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contexto: PdfExtractionContext
    itens: list[PdfItemExtraction]
