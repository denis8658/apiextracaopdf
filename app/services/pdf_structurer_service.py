from app.schemas.pdf_structuring import (
    PdfExtractionContext,
    PdfItemsExtraction,
    StructuredPdfResponse,
)
from app.structuring.base import StructuredDataProvider
from app.structuring.prompts import PDF_ITEMS_STRUCTURING_SYSTEM_PROMPT


class PdfStructurerService:
    def __init__(self, provider: StructuredDataProvider) -> None:
        self.provider = provider

    async def structure(
        self, document_content: str, cliente_id: str | None, obra_id: str | None
    ) -> StructuredPdfResponse:
        provided = await self.provider.parse(
            system_prompt=PDF_ITEMS_STRUCTURING_SYSTEM_PROMPT,
            document_content=document_content,
            output_schema=PdfItemsExtraction,
        )
        normalized_items = []
        for ordem, item in enumerate(provided.parsed.itens, 1):
            payload = item.model_dump()
            payload["ordem"] = ordem
            if payload["tem_vidro"] is False:
                payload["vidro"] = None
            normalized_items.append(payload)
        return StructuredPdfResponse(
            contexto=PdfExtractionContext(cliente_id=cliente_id, obra_id=obra_id),
            itens=normalized_items,
        )
