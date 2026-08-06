from datetime import date
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.dependencies import get_order_structuring_service
from app.core.config import Settings
from app.db.base import Base
from app.db.models import Customer, Document, DocumentPage, DocumentResult, Order, OrderItem
from app.schemas.order_structuring import (
    CustomerExtraction,
    OrderItemExtraction,
    StructuredOrderExtraction,
)
from app.services.order_structuring_service import OrderStructuringService
from app.structure_main import app
from app.structuring.base import ProviderResult
from app.structuring.consistency import validate_consistency
from app.structuring.normalizer import (
    normalize_code,
    normalize_order,
    parse_brazilian_date,
    remove_commercial_lines,
    x_to_bool,
)
from app.structuring.prompts import ORDER_STRUCTURING_SYSTEM_PROMPT
from app.structuring.provider import OpenAIStructuredDataProvider
from app.workers import structure_worker


def order_1790() -> StructuredOrderExtraction:
    common = {"has_subframe": True, "has_trim": True, "confidence": 1.0}
    items = [
        OrderItemExtraction(
            original_code="J01",
            normalized_code="J01",
            occurrence_number=1,
            document_order=1,
            product_code="FIX1",
            description="FIXO 1 MÓDULO - LINHA GOLD",
            width_mm=1200,
            height_mm=2900,
            quantity=1,
            environment="SALA-1",
            glass="TEMPERADO INCOLOR 8MM",
            source_page=1,
            **common,
        ),
        OrderItemExtraction(
            original_code="J01",
            normalized_code="J01",
            occurrence_number=2,
            document_order=2,
            product_code="FIX1",
            description="FIXO 1 MÓDULO - LINHA GOLD",
            width_mm=1200,
            height_mm=2900,
            quantity=1,
            environment="SALA-1",
            glass="TEMPERADO INCOLOR 8MM",
            information="OBS:JUNÇÃO COM A J2 JANELA PIVOTANTE INCLUSO, TUBO PARA JUNÇÃO DAS PEÇAS",
            source_page=1,
            **common,
        ),
        OrderItemExtraction(
            original_code="J02",
            normalized_code="J02",
            occurrence_number=1,
            document_order=3,
            product_code="JC2",
            description="JANELA PIVOTANTE 1 FOLHA LINHA GOLD",
            width_mm=1250,
            height_mm=2900,
            quantity=1,
            environment="SALA-1",
            glass="TEMPERADO INCOLOR 8MM",
            information="OBS: JUNÇÃO COM J01",
            source_page=1,
            **common,
        ),
        OrderItemExtraction(
            original_code="J03",
            normalized_code="J03",
            occurrence_number=1,
            document_order=4,
            product_code="MAX1",
            description="MAXIM-AR 1 FOLHA - 90 - LINHA SUPREMA",
            width_mm=800,
            height_mm=600,
            quantity=4,
            environment="BANHOS",
            glass="MINIBOREAL 4MM",
            source_page=1,
            **common,
        ),
        OrderItemExtraction(
            original_code="J04",
            normalized_code="J04",
            occurrence_number=1,
            document_order=5,
            product_code="JC2",
            description="JANELA DE CORRER 2 FOLHAS - LINHA SUPREMA",
            width_mm=2000,
            height_mm=800,
            quantity=1,
            environment="LAVANDERIA",
            glass="TEMPERADO INCOLOR 6MM",
            source_page=2,
            **common,
        ),
        OrderItemExtraction(
            original_code="P02",
            normalized_code="P02",
            occurrence_number=1,
            document_order=6,
            product_code="PIV",
            description="PORTA PIVOTANTE COM LAMBRI RIPADO VERTICAL - LINHA GOLD",
            width_mm=1600,
            height_mm=2500,
            quantity=1,
            environment="HALL DE ENTRADA",
            glass="SEM VIDRO",
            information="OBS: COR AMADEIRADA PUXADOR E FECHADURA DIGITAL POR CONTA DO CLIENTE",
            source_page=2,
            **common,
        ),
        OrderItemExtraction(
            original_code="P3",
            normalized_code="P03",
            occurrence_number=1,
            document_order=7,
            product_code="PC4",
            description="PORTA DE CORRER 4 FOLHAS SEQUENCIAIS - LINHA GOLD",
            width_mm=4700,
            height_mm=2100,
            quantity=1,
            environment="COZINHA",
            glass="TEMPERADO INCOLOR 8MM",
            information="OBS: ESPESSURA DA PAREDE PRECISA TER 21CM.",
            source_page=2,
            **common,
        ),
        OrderItemExtraction(
            original_code="P4",
            normalized_code="P04",
            occurrence_number=1,
            document_order=8,
            product_code="PC2-GOLD-M",
            description="PORTA DE CORRER 2 FOLHAS C/ RECOLHEDOR MANUAL - LINHA GOLD",
            width_mm=1600,
            height_mm=2100,
            quantity=3,
            environment="Dormitórios",
            glass="TEMPERADO INCOLOR 6MM",
            source_page=2,
            **common,
        ),
        OrderItemExtraction(
            original_code="P5",
            normalized_code="P05",
            occurrence_number=1,
            document_order=9,
            product_code="PP",
            description="PORTA PIVOTANTE EM LAMBRI - PERFIL 100MM - GOLD",
            width_mm=1200,
            height_mm=2100,
            quantity=1,
            environment="Despensa",
            glass="SEM VIDRO",
            information="PUXADOR E FECHADURA DIGITAL POR CONTA DO CLIENTE",
            source_page=2,
            **common,
        ),
    ]
    return StructuredOrderExtraction(
        order_number="1790",
        order_date=date(2024, 12, 2),
        color="PINTURA PRETO",
        customer=CustomerExtraction(
            name="CLIENTE EXEMPLO",
            address="RUA EXEMPLO Nº100",
            city="CIDADE TESTE",
            cpf_cnpj="000.000.000-00",
            rg_ie="00.000.000-0",
            phone="00-00000-0000",
            notes="ATEND. COMERCIAL TESTE",
        ),
        items=items,
    )


class FakeOrderProvider:
    async def parse(self, *, system_prompt, document_content, output_schema):
        assert "Não crie campos para informações comerciais ou prazos" in system_prompt
        assert "=== PÁGINA 3 ===" in document_content
        assert "CM 7 dias" in document_content
        return ProviderResult(parsed=output_schema.model_validate(order_1790().model_dump()))


def test_normalization_helpers_and_commercial_scope():
    assert parse_brazilian_date("02/12/2024") == date(2024, 12, 2)
    assert normalize_code("P3") == "P03"
    assert x_to_bool("X") is True
    assert x_to_bool("") is False
    assert remove_commercial_lines("Prazo de Entrega: CM 7 dias\nOBS: PUXADOR") == "OBS: PUXADOR"
    assert "payment_terms" not in StructuredOrderExtraction.model_fields
    assert "delivery_terms" not in StructuredOrderExtraction.model_fields
    assert not {"customer_name", "customer_id", "customer_cpf", "customer_address"} & set(
        OrderItemExtraction.model_fields
    )


def test_budget_1790_deterministic_totals():
    structured = normalize_order(order_1790())
    checked = validate_consistency(structured, page_count=3, min_confidence=0.9)
    assert checked.summary.item_records_count == 9
    assert checked.summary.total_units == 14
    assert checked.summary.distinct_codes_count == 8
    assert [item.occurrence_number for item in structured.items if item.original_code == "J01"] == [
        1,
        2,
    ]
    assert structured.items[6].original_code == "P3"
    assert structured.items[6].normalized_code == "P03"
    assert all(item.source_page in {1, 2} for item in structured.items)
    assert checked.needs_review is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_budget_1790_preview_persist_and_idempotency(tmp_path, monkeypatch):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'orders.db'}"
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        app_env="test",
        database_url=database_url,
        storage_path=tmp_path / "storage",
        structuring_model="fake-order-1790",
    )
    content = (Path(__file__).parent / "fixtures" / "orcamento_1790.txt").read_text("utf-8")
    page_texts = content.split("=== PÁGINA ")[1:]
    async with sessions() as session:
        document = Document(
            original_filename="orcamento-1790.pdf",
            safe_filename="orcamento-1790.pdf",
            content_type="application/pdf",
            file_size_bytes=1,
            sha256="a" * 64,
            storage_provider="local",
            storage_key="test/orcamento-1790.pdf",
            page_count=3,
            status="completed",
            detected_pdf_type="native",
            extraction_engine="native",
            retain_original=True,
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentResult(
                document_id=document.id,
                plain_text=content,
                markdown=content,
                structured_json={},
                metadata_json={},
                schema_version="1.0",
            )
        )
        for number, text in enumerate(page_texts, 1):
            session.add(
                DocumentPage(
                    document_id=document.id,
                    page_number=number,
                    plain_text=text,
                    markdown=text,
                    blocks_json=[],
                    char_count=len(text),
                    word_count=len(text.split()),
                    has_native_text=True,
                    ocr_used=False,
                )
            )
        await session.commit()
        document_id = document.id

    async def service_override():
        async with sessions() as session:
            yield OrderStructuringService(session, settings)

    app.dependency_overrides[get_order_structuring_service] = service_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Idempotency-Key": "structure-1790"}
        first = await client.post(
            f"/api/v1/documents/{document_id}/structure/order",
            json={"mode": "preview", "force_reprocess": False},
            headers=headers,
        )
        repeated_start = await client.post(
            f"/api/v1/documents/{document_id}/structure/order",
            json={"mode": "preview", "force_reprocess": False},
            headers=headers,
        )
        assert first.status_code == 202
        assert repeated_start.status_code == 202
        assert first.json()["structure_job_id"] == repeated_start.json()["structure_job_id"]
        job_id = first.json()["structure_job_id"]

        monkeypatch.setattr(structure_worker, "SessionLocal", sessions)
        monkeypatch.setattr(structure_worker, "get_settings", lambda: settings)
        async with sessions() as session:
            claimed = await structure_worker.claim_structure_job(session)
        assert claimed is not None
        await structure_worker.process_structure_job(claimed, FakeOrderProvider())

        job_response = await client.get(f"/api/v1/structure-jobs/{job_id}")
        preview_response = await client.get(f"/api/v1/structure-jobs/{job_id}/result")
        assert job_response.json()["status"] == "completed"
        preview = preview_response.json()
        assert preview["summary"] == {
            "item_records_count": 9,
            "total_units": 14,
            "distinct_codes_count": 8,
        }
        assert all(item["source_page"] != 3 for item in preview["result"]["items"])

        persist_headers = {"Idempotency-Key": "persist-1790"}
        persisted = await client.post(
            f"/api/v1/structure-jobs/{job_id}/persist", headers=persist_headers
        )
        repeated = await client.post(
            f"/api/v1/structure-jobs/{job_id}/persist", headers=persist_headers
        )
        assert persisted.status_code == 200
        assert repeated.json() == persisted.json()
        assert persisted.json()["items_created"] == 9
        order_id = persisted.json()["order_id"]
        order = (await client.get(f"/api/v1/orders/{order_id}")).json()
        items = (await client.get(f"/api/v1/orders/{order_id}/items")).json()
        assert order["customer"]["name"] == "CLIENTE EXEMPLO"
        assert len(items) == 9
        assert sum(item["quantity"] for item in items) == 14
        assert all("customer" not in item for item in items)

        reprocessed = await client.post(
            f"/api/v1/documents/{document_id}/structure/order/reprocess",
            json={"mode": "preview", "force_reprocess": False},
            headers={"Idempotency-Key": "reprocess-1790"},
        )
        versions = await client.get(f"/api/v1/documents/{document_id}/structure-results")
        assert reprocessed.status_code == 202
        assert len(versions.json()) == 2

    app.dependency_overrides.pop(get_order_structuring_service, None)
    async with sessions() as session:
        assert len((await session.scalars(select(Customer))).all()) == 1
        assert len((await session.scalars(select(Order))).all()) == 1
        assert len((await session.scalars(select(OrderItem))).all()) == 9
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.external
@pytest.mark.asyncio
async def test_real_provider_budget_1790_when_configured():
    settings = Settings(_env_file=None)
    if settings.openai_api_key is None:
        pytest.skip("OPENAI_API_KEY não configurada")
    content = (Path(__file__).parent / "fixtures" / "orcamento_1790.txt").read_text("utf-8")
    provided = await OpenAIStructuredDataProvider(settings).parse(
        system_prompt=ORDER_STRUCTURING_SYSTEM_PROMPT,
        document_content=content,
        output_schema=StructuredOrderExtraction,
    )
    structured = normalize_order(provided.parsed)
    checked = validate_consistency(structured, page_count=3, min_confidence=0.9)
    assert checked.summary.item_records_count == 9
    assert checked.summary.total_units == 14
    assert checked.summary.distinct_codes_count == 8
    assert all(item.source_page in {1, 2} for item in structured.items)
