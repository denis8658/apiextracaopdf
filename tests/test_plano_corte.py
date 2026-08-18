import json

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.integrations.base44 import Base44PlanoCorteClient
from app.schemas.base44 import (
    Base44ProcessingContext,
    CutProfile,
    CutProfilesExtraction,
    PlanoCorteProcessingContext,
)
from app.schemas.pdf_structuring import PdfItemsExtraction
from app.services.plano_corte_service import (
    PlanoCorteStructurerService,
    build_plano_corte,
    extract_native_cut_table,
    parse_localized_number,
)
from app.structuring.base import ProviderResult
from app.structuring.event_router import PostExtractionEventRouter


def context() -> PlanoCorteProcessingContext:
    return PlanoCorteProcessingContext(
        evento="criar_plano_corte",
        obra_id="obra-1",
        obra_nome="Residencial Aurora",
        cliente_id="cliente-1",
        item_pedido_id="item-42",
        item_idsecao="JC2",
        item_descricao="Janela de correr",
        item_largura=2000,
        item_altura=1200,
        item_quantidade=2,
        item_ambiente="Suíte",
        item_vidro="Temperado 8 mm",
    )


def profiles() -> list[CutProfile]:
    return [
        CutProfile(perfil="AL-101", qtd=2, medida_mm=1190, corte="45/45", peso_liquido_kg=1.2),
        CutProfile(perfil="AL-101", qtd=3, medida_mm=865, corte="90/45", peso_liquido_kg=0.8),
    ]


def test_build_plano_corte_preserves_lines_and_trusted_item_id() -> None:
    plan = build_plano_corte(context(), profiles())

    assert plan.item_pedido_id == "item-42"
    assert plan.itens[0].id == "item-42"
    assert [profile.medida_mm for profile in plan.perfis] == [1190, 865]
    assert plan.total_itens == 1
    assert plan.total_perfis == 5
    assert plan.peso_total_kg == 2
    assert plan.status == "pendente"


def test_localized_plan_numbers_are_normalized() -> None:
    assert parse_localized_number("1.190 mm") == 1190
    assert parse_localized_number("1,245 kg") == 1.245


def test_native_vertical_cut_table_is_parsed_without_consolidation() -> None:
    content = """Perfil
Qtd
Medida
Corte
Descrição
Peso Liquido
30023
1
840
45/45
L BATENTE
0,52
30023
2
2080
45/90
H BATENTE
2,57
** PRODUTO
"""

    parsed = extract_native_cut_table(content)

    assert len(parsed) == 2
    assert [profile.medida_mm for profile in parsed] == [840, 2080]
    assert [profile.peso_liquido_kg for profile in parsed] == [0.52, 2.57]


def test_normalized_horizontal_cut_table_is_parsed() -> None:
    content = """PERFIS
Perfil Qtd Medida Corte Descrição Peso Liquido
30023 1 840 45/45 L BATENTE 0,52
TMR-1381 19 671 90/90 LAMBRI 100MM 11,05
VIDROS
"""

    parsed = extract_native_cut_table(content)

    assert [profile.perfil for profile in parsed] == ["30023", "TMR-1381"]
    assert parsed[1].descricao == "LAMBRI 100MM"
    assert parsed[1].peso_liquido_kg == 11.05


@pytest.mark.asyncio
async def test_empty_profiles_returns_controlled_error() -> None:
    class EmptyProvider:
        async def parse(self, **kwargs):
            return ProviderResult(parsed=CutProfilesExtraction(perfis=[]))

    with pytest.raises(AppError) as captured:
        await PlanoCorteStructurerService(EmptyProvider()).structure("PDF sem perfis")

    assert captured.value.code == "PERFIS_NAO_IDENTIFICADOS"


@pytest.mark.asyncio
async def test_router_selects_cut_plan_and_adds_item_to_empty_error(monkeypatch) -> None:
    class EmptyProvider:
        async def parse(self, **kwargs):
            return ProviderResult(parsed=CutProfilesExtraction(perfis=[]))

    monkeypatch.setattr(
        "app.structuring.event_router.create_provider", lambda settings: EmptyProvider()
    )

    with pytest.raises(AppError) as captured:
        await PostExtractionEventRouter(Settings()).route(
            context=context(), extracted_content="conteúdo extraído"
        )

    assert captured.value.code == "PERFIS_NAO_IDENTIFICADOS"
    assert captured.value.details == {"item_pedido_id": "item-42"}


@pytest.mark.asyncio
async def test_router_selects_existing_item_structurer(monkeypatch) -> None:
    class ItemProvider:
        async def parse(self, **kwargs):
            assert kwargs["output_schema"] is PdfItemsExtraction
            return ProviderResult(
                parsed=PdfItemsExtraction.model_validate(
                    {
                        "itens": [
                            {
                                "ordem": 1,
                                "codigo_item": "JC2",
                                "descricao_produto": "Janela",
                                "quantidade": 1,
                                "largura": 1200,
                                "altura": 800,
                                "tem_contramarco": False,
                                "tem_arremate": False,
                            }
                        ]
                    }
                )
            )

    monkeypatch.setattr(
        "app.structuring.event_router.create_provider", lambda settings: ItemProvider()
    )
    routed = await PostExtractionEventRouter(Settings()).route(
        context=Base44ProcessingContext(evento="processar_pdf", oportunidade_id="opp-1"),
        extracted_content="orçamento extraído",
    )

    assert routed.evento == "processar_pdf"
    assert routed.destination == "base44_itens_pedido"
    assert routed.result.itens[0].codigo_item == "JC2"


@pytest.mark.asyncio
async def test_router_builds_valid_cut_plan(monkeypatch) -> None:
    class PlanProvider:
        async def parse(self, **kwargs):
            assert kwargs["output_schema"] is CutProfilesExtraction
            return ProviderResult(
                parsed=CutProfilesExtraction(
                    perfis=[
                        {
                            "perfil": "0030023-A",
                            "qtd": "4 peças",
                            "medida_mm": "1.190 mm",
                            "corte": "45/45",
                            "peso_liquido_kg": "1,245 kg",
                        }
                    ]
                )
            )

    monkeypatch.setattr(
        "app.structuring.event_router.create_provider", lambda settings: PlanProvider()
    )
    routed = await PostExtractionEventRouter(Settings()).route(
        context=context(), extracted_content="plano extraído"
    )

    assert routed.destination == "base44_plano_corte"
    assert routed.result.item_pedido_id == "item-42"
    assert routed.result.perfis[0].perfil == "0030023-A"
    assert routed.result.perfis[0].medida_mm == 1190


@pytest.mark.asyncio
async def test_plano_corte_client_sends_exact_endpoint_and_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "record-1", "plano_id": "plan-1"})

    settings = Settings(base44_api_key="secret-test")
    plan = build_plano_corte(context(), profiles())
    response = await Base44PlanoCorteClient(
        settings, httpx.MockTransport(handler)
    ).create(plan, "operation-plan-1")

    sent = json.loads(requests[0].content)
    assert response.id == "record-1"
    assert requests[0].url.path.endswith("/api/entities/PlanoCorte")
    assert requests[0].headers["api_key"] == "secret-test"
    assert requests[0].headers["Idempotency-Key"] == "operation-plan-1"
    assert sent["item_pedido_id"] == "item-42"
    assert "obra_id" not in sent
