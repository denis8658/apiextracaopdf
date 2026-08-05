from dataclasses import dataclass

from app.schemas.order_structuring import StructuredOrderExtraction, StructureSummary


@dataclass
class ConsistencyResult:
    summary: StructureSummary
    checks: dict[str, bool]
    warnings: list[str]
    needs_review: bool


def validate_consistency(
    order: StructuredOrderExtraction,
    *,
    page_count: int,
    min_confidence: float,
) -> ConsistencyResult:
    item_orders = [item.document_order for item in order.items]
    occurrences: dict[str, list[int]] = {}
    for item in order.items:
        code = item.normalized_code or item.original_code
        occurrences.setdefault(code, []).append(item.occurrence_number)

    checks = {
        "items_present": bool(order.items),
        "document_order_sequential": item_orders == list(range(1, len(order.items) + 1)),
        "occurrences_sequential": all(
            numbers == list(range(1, len(numbers) + 1)) for numbers in occurrences.values()
        ),
        "pages_in_range": all(
            item.source_page is None or 1 <= item.source_page <= page_count for item in order.items
        ),
        "positive_dimensions": all(
            (item.width_mm is None or item.width_mm > 0)
            and (item.height_mm is None or item.height_mm > 0)
            and item.quantity > 0
            for item in order.items
        ),
        "required_dimensions_present": all(
            item.width_mm is not None and item.height_mm is not None for item in order.items
        ),
    }
    warnings = list(order.warnings)
    for name, passed in checks.items():
        if not passed:
            warnings.append(f"Falha na verificação: {name}")
    low_confidence = [
        item.document_order
        for item in order.items
        if item.confidence is not None and item.confidence < min_confidence
    ]
    if low_confidence:
        warnings.append(f"Itens com baixa confiança: {low_confidence}")
    summary = StructureSummary(
        item_records_count=len(order.items),
        total_units=sum(item.quantity for item in order.items),
        distinct_codes_count=len({item.original_code for item in order.items}),
    )
    return ConsistencyResult(
        summary=summary,
        checks=checks,
        warnings=list(dict.fromkeys(warnings)),
        needs_review=not all(checks.values()) or bool(low_confidence) or bool(order.warnings),
    )
