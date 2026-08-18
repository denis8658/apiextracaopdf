import asyncio
import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core.config import Settings
from app.core.errors import AppError
from app.schemas.base44 import (
    Base44BulkCreateResponse,
    Base44CreatedItem,
    Base44ItemPedidoPayload,
    Base44ProcessingContext,
    ItemValidationIssue,
    PlanoCorteCreatedResponse,
    PlanoCortePayload,
)
from app.schemas.pdf_structuring import PdfItemExtraction, StructuredPdfResponse

TRANSIENT_STATUS = {429, 500, 502, 503, 504}
TRUE_VALUES = {"x", "sim", "s", "true", "1", "yes"}
MISSING_TEXT = "sem informações"


def parse_number(value: object) -> float | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("valor booleano não é numérico")
    if isinstance(value, int | float):
        number = float(value)
    else:
        text = re.sub(r"(?i)\s*(mm|un|unidades?)\s*$", "", str(value).strip())
        text = text.replace(" ", "")
        if not text:
            return None
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
            text = text.replace(".", "")
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError("não pôde ser convertido para número") from exc
    if number <= 0:
        raise ValueError("deve ser maior que zero")
    return int(number) if number.is_integer() else number


def _text(value: str | None) -> str:
    cleaned = " ".join((value or "").split())
    return cleaned or MISSING_TEXT


def _confirmation(raw: str | None, fallback: bool) -> bool:
    return raw.strip().casefold() in TRUE_VALUES if raw and raw.strip() else fallback


def map_item(
    item: PdfItemExtraction,
    oportunidade_id: str,
    obra_id: str | None,
    cliente_id: str | None,
    vendedor_id: str | None,
    context: Base44ProcessingContext | None = None,
) -> Base44ItemPedidoPayload:
    description = _text(item.descricao_produto or item.titulo)
    return Base44ItemPedidoPayload(
        oportunidade_id=oportunidade_id,
        obra_id=obra_id,
        cliente_id=cliente_id,
        vendedor_id=vendedor_id,
        aprovado_por_id=context.aprovado_por_id if context else None,
        aprovado_por_nome=context.aprovado_por_nome if context else None,
        data_aprovacao=context.data_aprovacao if context else None,
        IDsecao=item.codigo_item,
        titulo=(
            _text(item.titulo)
            if item.titulo
            else _text(context.titulo)
            if context and context.titulo
            else description
        ),
        descricao=description,
        Largura=parse_number(item.largura),
        Altura=parse_number(item.altura),
        Qtd=parse_number(item.quantidade),
        Vidro=_text(item.vidro),
        Ambiente=_text(item.ambiente),
        informacoes=_text(item.informacoes),
        Arremate=_text(item.arremate) if item.arremate else ("sim" if item.tem_arremate else "não"),
        tem_arremate=_confirmation(item.arremate, item.tem_arremate),
        tem_meia_cana=item.tem_meia_cana,
        Contramarco=(
            _text(item.contramarco)
            if item.contramarco
            else ("sim" if item.tem_contramarco else "não")
        ),
    )


def map_batch(
    structured: StructuredPdfResponse,
    oportunidade_id: str,
    obra_id: str | None,
    cliente_id: str | None,
    vendedor_id: str | None,
    context: Base44ProcessingContext | None = None,
) -> list[Base44ItemPedidoPayload]:
    payload: list[Base44ItemPedidoPayload] = []
    issues: list[ItemValidationIssue] = []
    for index, item in enumerate(structured.itens):
        item_issues: list[ItemValidationIssue] = []
        for field, value in (
            ("Largura", item.largura),
            ("Altura", item.altura),
            ("Qtd", item.quantidade),
        ):
            try:
                parse_number(value)
            except (ValueError, TypeError) as exc:
                item_issues.append(
                    ItemValidationIssue(
                        index=index,
                        IDsecao=item.codigo_item,
                        field=field,
                        message=f"{field} {exc}",
                    )
                )
        if item_issues:
            issues.extend(item_issues)
            continue
        try:
            payload.append(
                map_item(
                    item,
                    oportunidade_id,
                    obra_id,
                    cliente_id,
                    vendedor_id,
                    context,
                )
            )
        except (ValueError, TypeError) as exc:
            issues.append(
                ItemValidationIssue(
                    index=index,
                    IDsecao=item.codigo_item,
                    field="item",
                    message=str(exc),
                )
            )
    if issues:
        raise AppError(
            "invalid_structured_items",
            "Um ou mais itens estruturados são inválidos.",
            422,
            {"items": [issue.model_dump() for issue in issues]},
        )
    return payload


def payload_hash(payload: list[Base44ItemPedidoPayload]) -> str:
    return json_payload_hash(
        [item.model_dump(exclude_none=True, mode="json") for item in payload]
    )


def plano_corte_payload_hash(payload: PlanoCortePayload) -> str:
    return json_payload_hash(payload.model_dump(exclude_none=True, mode="json"))


def json_payload_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class Base44ItensPedidoClient:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def create_bulk(
        self,
        payload: list[Base44ItemPedidoPayload],
        idempotency_key: str | None = None,
    ) -> Base44BulkCreateResponse:
        body = [item.model_dump(exclude_none=True, mode="json") for item in payload]
        data = await self._post_json(
            self.settings.base44_itens_pedido_path, body, idempotency_key
        )
        if not isinstance(data, list):
            raise AppError("base44_invalid_response", "Resposta inválida da Base44.", 502)
        records = [Base44CreatedItem.model_validate(record) for record in data]
        if len(records) != len(payload):
            raise AppError(
                "base44_partial_failure",
                "A Base44 confirmou uma quantidade diferente da enviada.",
                502,
                {
                    "sent_count": len(payload),
                    "saved_count": len(records),
                    "record_ids": [record.id for record in records],
                },
            )
        return Base44BulkCreateResponse(records=records)

    async def _post_json(
        self, path: str, body: Any, idempotency_key: str | None
    ) -> Any:
        if not self.settings.base44_api_key:
            raise AppError("base44_not_configured", "Integração Base44 não configurada.", 503)
        url = urljoin(
            f"{self.settings.base44_api_base_url.rstrip('/')}/",
            path.lstrip("/"),
        )
        last_error: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self.settings.base44_request_timeout_seconds,
            transport=self.transport,
        ) as client:
            for attempt in range(1, self.settings.base44_max_retries + 1):
                try:
                    response = await client.post(
                        url,
                        json=body,
                        headers={
                            "api_key": self.settings.base44_api_key.get_secret_value(),
                            "Content-Type": "application/json",
                            **({"Idempotency-Key": idempotency_key} if idempotency_key else {}),
                        },
                    )
                    if (
                        response.status_code in TRANSIENT_STATUS
                        and attempt < self.settings.base44_max_retries
                    ):
                        await asyncio.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    if response.status_code in {401, 403}:
                        raise AppError(
                            "base44_authentication_failed",
                            "A credencial da integração Base44 foi recusada.",
                            502,
                        )
                    if response.is_error:
                        raise AppError(
                            "base44_request_failed",
                            f"A Base44 recusou a operação (HTTP {response.status_code}).",
                            502,
                        )
                    return response.json()
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    last_error = exc
                    if attempt < self.settings.base44_max_retries:
                        await asyncio.sleep(min(2 ** (attempt - 1), 4))
                        continue
        raise AppError(
            "base44_unavailable",
            "A Base44 está temporariamente indisponível.",
            502,
        ) from last_error


class Base44PlanoCorteClient(Base44ItensPedidoClient):
    async def create(
        self, payload: PlanoCortePayload, idempotency_key: str | None = None
    ) -> PlanoCorteCreatedResponse:
        data = await self._post_json(
            self.settings.base44_plano_corte_path,
            payload.model_dump(exclude_none=True, mode="json"),
            idempotency_key,
        )
        if not isinstance(data, dict):
            raise AppError("base44_invalid_response", "Resposta inválida da Base44.", 502)
        return PlanoCorteCreatedResponse.model_validate(data)
