import re
import unicodedata
from datetime import date, datetime

from app.schemas.order_structuring import StructuredOrderExtraction

COMMERCIAL_ONLY = re.compile(
    r"\b(prazo(?:s)?|condi(?:ç|c)(?:ão|oes|ões)\s+de\s+pagamento|vencimento|cronograma|"
    r"ap[oó]s\s+a\s+medi(?:ç|c)(?:ão|ao))\b",
    re.IGNORECASE,
)


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def join_observation_lines(value: str | None) -> str | None:
    return clean_text(value)


def parse_brazilian_date(value: str | None) -> date | None:
    if not value:
        return None
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError("Data inválida; esperado DD/MM/AAAA ou AAAA-MM-DD")


def normalize_code(value: str) -> str:
    original = clean_text(value) or ""
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", original)
    if not match:
        return original.upper()
    prefix, number = match.groups()
    return f"{prefix.upper()}{int(number):02d}"


def normalize_name(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value)
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", plain).strip().upper()


def digits_only(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or None


def x_to_bool(value: str | None) -> bool:
    return bool(value and value.strip().upper() == "X")


def remove_commercial_lines(value: str | None) -> str | None:
    if not value:
        return None
    kept = [line for line in value.splitlines() if not COMMERCIAL_ONLY.search(line)]
    return clean_text(" ".join(kept))


def normalize_order(data: StructuredOrderExtraction) -> StructuredOrderExtraction:
    payload = data.model_dump()
    payload["order_number"] = clean_text(data.order_number)
    payload["color"] = clean_text(data.color)
    payload["customer"] = {
        key: clean_text(value) if isinstance(value, str) else value
        for key, value in data.customer.model_dump().items()
    }
    occurrences: dict[str, int] = {}
    normalized_items = []
    for index, item in enumerate(data.items, 1):
        item_payload = item.model_dump()
        original_code = clean_text(item.original_code) or item.original_code
        normalized = normalize_code(original_code)
        occurrences[normalized] = occurrences.get(normalized, 0) + 1
        item_payload.update(
            original_code=original_code,
            normalized_code=normalized,
            occurrence_number=occurrences[normalized],
            document_order=index,
            product_code=clean_text(item.product_code),
            description=clean_text(item.description),
            environment=clean_text(item.environment),
            glass=clean_text(item.glass),
            information=remove_commercial_lines(item.information),
            source_text=clean_text(item.source_text),
        )
        normalized_items.append(item_payload)
    payload["items"] = normalized_items
    payload["warnings"] = [clean_text(item) for item in data.warnings if clean_text(item)]
    return StructuredOrderExtraction.model_validate(payload)
