import pytest
from pydantic import ValidationError

from app.schemas.pdf_structuring import PdfItemsExtraction
from app.services.pdf_structurer_service import PdfStructurerService
from app.structuring.base import ProviderResult


class FakeProvider:
    async def parse(self, **kwargs):
        parsed = PdfItemsExtraction.model_validate(
            {
                "itens": [
                    {
                        "ordem": 99,
                        "codigo_item": "J01",
                        "descricao_produto": "  Janela   fixa ",
                        "quantidade": 1,
                        "largura": 1200,
                        "altura": 2900,
                        "tem_vidro": True,
                        "vidro": " TEMPERADO  INCOLOR 8MM ",
                        "tem_contramarco": True,
                        "tem_arremate": True,
                        "informacoes": None,
                    },
                    {
                        "ordem": 99,
                        "codigo_item": "J01",
                        "descricao_produto": None,
                        "quantidade": None,
                        "largura": None,
                        "altura": None,
                        "tem_vidro": False,
                        "vidro": "valor inconsistente",
                        "tem_contramarco": False,
                        "tem_arremate": False,
                        "informacoes": None,
                    },
                ]
            }
        )
        return ProviderResult(parsed=parsed)


class FailingProvider:
    async def parse(self, **kwargs):
        raise TimeoutError("provider internal detail")


@pytest.mark.asyncio
async def test_structurer_preserves_context_repeated_codes_order_and_nulls():
    result = await PdfStructurerService(FakeProvider()).structure(
        "conteúdo extraído", "  cli_Árvore  ", "obra/789?x=1"
    )

    assert result.contexto.model_dump() == {
        "cliente_id": "  cli_Árvore  ",
        "obra_id": "obra/789?x=1",
    }
    assert [item.codigo_item for item in result.itens] == ["J01", "J01"]
    assert [item.ordem for item in result.itens] == [1, 2]
    assert result.itens[0].tem_contramarco is True
    assert result.itens[0].tem_arremate is True
    assert result.itens[1].descricao_produto is None
    assert result.itens[1].quantidade is None
    assert result.itens[1].vidro is None


def test_invalid_ai_payload_is_rejected_by_schema():
    with pytest.raises(ValidationError):
        PdfItemsExtraction.model_validate(
            {
                "itens": [
                    {
                        "ordem": 1,
                        "codigo_item": "J01",
                        "tem_contramarco": "talvez",
                        "tem_arremate": False,
                        "campo_inventado": "não permitido",
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_provider_failure_is_propagated_without_partial_result():
    with pytest.raises(TimeoutError):
        await PdfStructurerService(FailingProvider()).structure("texto", "cli", "obra")
