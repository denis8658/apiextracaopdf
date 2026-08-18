import re
import unicodedata
import uuid

from app.core.errors import AppError
from app.schemas.base44 import (
    CutProfile,
    CutProfileExtraction,
    CutProfilesExtraction,
    PlanoCorteItemPayload,
    PlanoCortePayload,
    PlanoCorteProcessingContext,
)
from app.structuring.base import StructuredDataProvider
from app.structuring.prompts import PLANO_CORTE_STRUCTURING_SYSTEM_PROMPT

CUT_TABLE_HEADER = ("perfil", "qtd", "medida", "corte", "descricao", "peso liquido")
CUT_PATTERN = re.compile(r"^\d{1,3}\s*/\s*\d{1,3}$")
HORIZONTAL_CUT_ROW = re.compile(
    r"^(?P<perfil>\S+)\s+(?P<qtd>[\d.,]+)\s+(?P<medida>[\d.,]+)\s+"
    r"(?P<corte>\d{1,3}\s*/\s*\d{1,3})\s+(?P<descricao>.+?)\s+"
    r"(?P<peso>[\d.,]+)$"
)


def _plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def extract_native_cut_table(document_content: str) -> list[CutProfile]:
    """Read the stable vertical table emitted by the existing PDF extractor."""
    lines = [" ".join(line.split()) for line in document_content.splitlines() if line.strip()]
    normalized = [_plain_text(line) for line in lines]
    horizontal_header = " ".join(CUT_TABLE_HEADER)
    horizontal_start = next(
        (index + 1 for index, line in enumerate(normalized) if line == horizontal_header),
        None,
    )
    if horizontal_start is not None:
        horizontal_profiles: list[CutProfile] = []
        for line in lines[horizontal_start:]:
            if _plain_text(line) in {"vidros", "informacoes"} or line.startswith("**"):
                break
            match = HORIZONTAL_CUT_ROW.fullmatch(line)
            if not match:
                if horizontal_profiles:
                    break
                continue
            horizontal_profiles.append(
                CutProfile(
                    perfil=match["perfil"],
                    qtd=parse_localized_number(match["qtd"]),
                    medida_mm=parse_localized_number(match["medida"]),
                    corte=match["corte"].replace(" ", ""),
                    descricao=match["descricao"],
                    peso_liquido_kg=parse_localized_number(match["peso"], allow_zero=True),
                )
            )
        if horizontal_profiles:
            return horizontal_profiles
    start = next(
        (
            index + len(CUT_TABLE_HEADER)
            for index in range(len(lines) - len(CUT_TABLE_HEADER) + 1)
            if tuple(normalized[index : index + len(CUT_TABLE_HEADER)]) == CUT_TABLE_HEADER
        ),
        None,
    )
    if start is None:
        return []

    profiles: list[CutProfile] = []
    index = start
    while index < len(lines):
        if lines[index].startswith("**") or normalized[index] in {"vidros", "informacoes"}:
            break
        if index + 4 >= len(lines):
            break
        code = lines[index]
        try:
            quantity = parse_localized_number(lines[index + 1])
            measure = parse_localized_number(lines[index + 2])
        except ValueError:
            break
        cut = lines[index + 3].replace(" ", "")
        if not CUT_PATTERN.fullmatch(cut):
            break
        description_parts: list[str] = []
        cursor = index + 4
        weight: float | int | None = None
        while cursor < len(lines):
            if lines[cursor].startswith("**"):
                break
            try:
                candidate = parse_localized_number(lines[cursor], allow_zero=True)
            except ValueError:
                description_parts.append(lines[cursor])
                cursor += 1
                continue
            if description_parts:
                weight = candidate
                cursor += 1
                break
            description_parts.append(lines[cursor])
            cursor += 1
        if weight is None or not description_parts:
            break
        profiles.append(
            CutProfile(
                perfil=code,
                qtd=quantity,
                medida_mm=measure,
                corte=cut,
                descricao=" ".join(description_parts),
                peso_liquido_kg=weight,
            )
        )
        index = cursor
    return profiles


def parse_localized_number(value: object, *, allow_zero: bool = False) -> float | int:
    if isinstance(value, bool) or value is None:
        raise ValueError("valor numérico ausente")
    if isinstance(value, int | float):
        number = float(value)
    else:
        text = re.sub(r"(?i)\s*(mm|kg|peças?|unidades?)\s*$", "", str(value).strip())
        text = text.replace(" ", "")
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
            text = text.replace(".", "")
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError(f"valor numérico inválido: {value}") from exc
    if number < 0 or (number == 0 and not allow_zero):
        raise ValueError("valor numérico fora da faixa permitida")
    return int(number) if number.is_integer() else number


class PlanoCorteStructurerService:
    def __init__(self, provider: StructuredDataProvider) -> None:
        self.provider = provider

    async def structure(self, document_content: str) -> list[CutProfile]:
        native_profiles = extract_native_cut_table(document_content)
        if native_profiles:
            return native_profiles
        provided = await self.provider.parse(
            system_prompt=PLANO_CORTE_STRUCTURING_SYSTEM_PROMPT,
            document_content=document_content,
            output_schema=CutProfilesExtraction,
        )
        profiles = [self._normalize(profile) for profile in provided.parsed.perfis]
        if not profiles:
            raise AppError(
                "PERFIS_NAO_IDENTIFICADOS",
                "Nenhum perfil válido foi identificado no plano de corte.",
                422,
            )
        return profiles

    @staticmethod
    def _normalize(profile: CutProfileExtraction) -> CutProfile:
        code = str(profile.perfil).strip()
        if not code:
            raise AppError("PERFIL_INVALIDO", "Código de perfil vazio.", 422)
        try:
            weight = (
                parse_localized_number(profile.peso_liquido_kg, allow_zero=True)
                if profile.peso_liquido_kg is not None
                else None
            )
            return CutProfile(
                perfil=code,
                qtd=parse_localized_number(profile.qtd),
                medida_mm=parse_localized_number(profile.medida_mm),
                corte=" ".join(profile.corte.split()) if profile.corte else None,
                descricao=" ".join(profile.descricao.split()) if profile.descricao else None,
                peso_liquido_kg=weight,
            )
        except ValueError as exc:
            raise AppError(
                "PERFIL_INVALIDO",
                f"Perfil {code} possui valor numérico inválido.",
                422,
                {"perfil": code, "message": str(exc)},
            ) from exc


def build_plano_corte(
    context: PlanoCorteProcessingContext, profiles: list[CutProfile]
) -> PlanoCortePayload:
    description = " - ".join(
        value.strip()
        for value in (context.obra_nome, context.item_ambiente, context.item_descricao)
        if value and value.strip()
    )
    item = PlanoCorteItemPayload(
        id=context.item_pedido_id,
        modelo=context.item_descricao,
        tipo=context.item_descricao,
        largura=context.item_largura,
        altura=context.item_altura,
        quantidade=context.item_quantidade,
        ambiente=context.item_ambiente,
        vidro=context.item_vidro,
    )
    return PlanoCortePayload(
        item_pedido_id=context.item_pedido_id,
        plano_id=str(uuid.uuid4()),
        descricao_plano=description,
        total_itens=1,
        total_perfis=sum(profile.qtd for profile in profiles),
        itens=[item],
        perfis=profiles,
        peso_total_kg=sum(profile.peso_liquido_kg or 0 for profile in profiles),
    )
