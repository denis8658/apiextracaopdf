from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings
from app.core.errors import AppError
from app.schemas.base44 import (
    Base44ProcessingContext,
    PlanoCortePayload,
    PlanoCorteProcessingContext,
    ProcessingContext,
)
from app.schemas.pdf_structuring import StructuredPdfResponse
from app.services.pdf_structurer_service import PdfStructurerService
from app.services.plano_corte_service import PlanoCorteStructurerService, build_plano_corte
from app.structuring.provider import create_provider


@dataclass
class RoutedStructure:
    evento: Literal["processar_pdf", "criar_plano_corte"]
    result: StructuredPdfResponse | PlanoCortePayload
    destination: Literal["base44_itens_pedido", "base44_plano_corte"]


class PostExtractionEventRouter:
    STRUCTURERS = {
        "processar_pdf": "items",
        "criar_plano_corte": "cut_plan",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def route(
        self, *, context: ProcessingContext, extracted_content: str
    ) -> RoutedStructure:
        if context.evento not in self.STRUCTURERS:
            raise AppError(
                "EVENTO_NAO_SUPORTADO",
                "Evento de processamento não suportado.",
                422,
                {"evento_recebido": context.evento},
            )
        provider = create_provider(self.settings)
        if isinstance(context, Base44ProcessingContext):
            result = await PdfStructurerService(provider).structure(
                extracted_content, context.cliente_id, context.obra_id
            )
            return RoutedStructure("processar_pdf", result, "base44_itens_pedido")
        if isinstance(context, PlanoCorteProcessingContext):
            try:
                profiles = await PlanoCorteStructurerService(provider).structure(extracted_content)
            except AppError as exc:
                if exc.code == "PERFIS_NAO_IDENTIFICADOS":
                    exc.details = {"item_pedido_id": context.item_pedido_id}
                raise
            return RoutedStructure(
                "criar_plano_corte",
                build_plano_corte(context, profiles),
                "base44_plano_corte",
            )
        raise AppError("EVENTO_NAO_SUPORTADO", "Evento de processamento não suportado.", 422)
