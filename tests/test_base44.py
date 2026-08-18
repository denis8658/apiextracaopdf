import json

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.integrations.base44 import Base44ItensPedidoClient, map_batch, parse_number
from app.schemas.base44 import Base44ProcessingContext
from app.schemas.pdf_structuring import PdfItemExtraction, StructuredPdfResponse


def structured(*items: PdfItemExtraction) -> StructuredPdfResponse:
    return StructuredPdfResponse(
        contexto={"cliente_id": "cliente_teste", "obra_id": "obra_teste"},
        itens=list(items),
    )


def item(**overrides) -> PdfItemExtraction:
    values = {
        "ordem": 1,
        "codigo_item": "JC2",
        "titulo": "Janela de correr",
        "descricao_produto": "Janela de Correr 2 Folhas",
        "quantidade": "1,5",
        "largura": "2.000 mm",
        "altura": "800 mm",
        "tem_vidro": True,
        "vidro": "TEMPERADO 6MM",
        "tem_contramarco": True,
        "tem_arremate": False,
        "ambiente": "LAVANDERIA",
        "arremate": "X",
        "contramarco": "X",
        "informacoes": None,
    }
    values.update(overrides)
    return PdfItemExtraction(**values)


def test_numeric_conversion_and_complete_mapping() -> None:
    payload = map_batch(
        structured(item()), "opp_teste", "obra_teste", "cliente_teste", "vend_teste"
    )[0]

    assert payload.model_dump(exclude_none=True) == {
        "oportunidade_id": "opp_teste",
        "obra_id": "obra_teste",
        "cliente_id": "cliente_teste",
        "vendedor_id": "vend_teste",
        "IDsecao": "JC2",
        "titulo": "Janela de correr",
        "descricao": "Janela de Correr 2 Folhas",
        "Largura": 2000,
        "Altura": 800,
        "Qtd": 1.5,
        "Vidro": "TEMPERADO 6MM",
        "Ambiente": "LAVANDERIA",
        "informacoes": "sem informações",
        "Arremate": "X",
        "tem_arremate": True,
        "tem_meia_cana": False,
        "Contramarco": "X",
        "status_item": "pendente",
        "pendente_medicao": False,
        "progresso_item_percent": 0,
    }
    assert parse_number("2000") == 2000


def test_processing_context_is_copied_to_every_item() -> None:
    context = Base44ProcessingContext(
        oportunidade_id="opp",
        cliente_id="cli",
        vendedor_id="vend",
        aprovado_por_id="admin",
        aprovado_por_nome="Administrador",
        data_aprovacao="2026-08-14T12:30:00Z",
        titulo="Oportunidade residencial",
        pdf_url="https://storage.example/document.pdf",
        valor=12345.67,
    )

    payload = map_batch(
        structured(item(titulo=None), item(ordem=2, titulo=None)),
        "opp",
        None,
        "cli",
        "vend",
        context,
    )

    assert len(payload) == 2
    assert all(entry.aprovado_por_id == "admin" for entry in payload)
    assert all(entry.titulo == "Oportunidade residencial" for entry in payload)
    assert all("valor" not in entry.model_dump() for entry in payload)
    assert "pdf_url" not in payload[0].model_dump()


def test_title_is_used_as_deterministic_description_fallback() -> None:
    payload = map_batch(
        structured(item(descricao_produto=None, titulo="JC2 - Janela de Correr")),
        "opp",
        None,
        None,
        None,
    )[0]

    assert payload.descricao == "JC2 - Janela de Correr"


def test_repeated_section_ids_are_preserved_in_order() -> None:
    payload = map_batch(
        structured(item(ordem=1), item(ordem=2, descricao_produto="Segunda ocorrência")),
        "opp",
        None,
        None,
        None,
    )

    assert [entry.IDsecao for entry in payload] == ["JC2", "JC2"]
    assert [entry.descricao for entry in payload] == [
        "Janela de Correr 2 Folhas",
        "Segunda ocorrência",
    ]


def test_invalid_numeric_value_rejects_entire_batch() -> None:
    with pytest.raises(AppError) as captured:
        map_batch(structured(item(largura="muito largo")), "opp", None, None, None)

    assert captured.value.code == "invalid_structured_items"
    assert captured.value.details["items"] == [
        {
            "index": 0,
            "IDsecao": "JC2",
            "field": "Largura",
            "message": "Largura não pôde ser convertido para número",
        }
    ]


@pytest.mark.asyncio
async def test_bulk_client_sends_exact_array_and_api_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[{"id": "base44_1"}])

    settings = Settings(base44_api_key="secret-test")
    payload = map_batch(structured(item()), "opp", None, None, None)
    response = await Base44ItensPedidoClient(
        settings, httpx.MockTransport(handler)
    ).create_bulk(payload, "operation-123")

    assert [record.id for record in response.records] == ["base44_1"]
    assert requests[0].url.path.endswith("/api/entities/ItensPedido/bulk")
    assert requests[0].headers["api_key"] == "secret-test"
    assert requests[0].headers["Idempotency-Key"] == "operation-123"
    assert isinstance(json.loads(requests[0].content), list)
    assert "created_date" not in requests[0].content.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_bulk_client_does_not_retry_permanent_errors(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": "rejected"})

    settings = Settings(base44_api_key="secret-test", base44_max_retries=3)
    with pytest.raises(AppError):
        await Base44ItensPedidoClient(settings, httpx.MockTransport(handler)).create_bulk(
            map_batch(structured(item()), "opp", None, None, None)
        )
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_bulk_client_retries_transient_status(monkeypatch, status: int) -> None:
    calls = 0

    async def no_sleep(_: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        response_status = status if calls < 3 else 200
        return httpx.Response(response_status, json=[] if calls < 3 else [{"id": "ok"}])

    monkeypatch.setattr("app.integrations.base44.asyncio.sleep", no_sleep)
    settings = Settings(base44_api_key="secret-test", base44_max_retries=3)
    response = await Base44ItensPedidoClient(
        settings, httpx.MockTransport(handler)
    ).create_bulk(map_batch(structured(item()), "opp", None, None, None))

    assert calls == 3
    assert response.records[0].id == "ok"
