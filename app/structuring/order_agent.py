from app.schemas.order_structuring import StructuredOrderExtraction
from app.structuring.base import ProviderResult, StructuredDataProvider
from app.structuring.prompts import ORDER_STRUCTURING_SYSTEM_PROMPT


class OrderStructuringAgent:
    def __init__(self, provider: StructuredDataProvider) -> None:
        self.provider = provider

    async def structure(self, document_content: str) -> ProviderResult[StructuredOrderExtraction]:
        return await self.provider.parse(
            system_prompt=ORDER_STRUCTURING_SYSTEM_PROMPT,
            document_content=document_content,
            output_schema=StructuredOrderExtraction,
        )
