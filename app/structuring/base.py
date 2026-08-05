from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass
class ProviderResult[T: BaseModel]:
    parsed: T
    raw_metadata: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class StructuredDataProvider(Protocol):
    async def parse[T: BaseModel](
        self,
        *,
        system_prompt: str,
        document_content: str,
        output_schema: type[T],
    ) -> ProviderResult[T]: ...
