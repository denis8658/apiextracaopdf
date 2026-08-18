import re
from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.config import Settings
from app.extraction.geometry import (
    bbox_contains,
    bbox_distance,
    bbox_overlap,
    bbox_union,
    same_column,
    same_row,
)
from app.schemas.extraction import (
    AssociationCandidate,
    ExtractedBlock,
    ExtractedImage,
    ExtractedItem,
    ExtractedPage,
    ExtractionResult,
)


@dataclass(frozen=True)
class _CodeAnchor:
    code: str
    block: ExtractedBlock
    confidence: float = 0.9


AssociationMethod = Literal[
    "layout_region",
    "spatial_proximity",
    "same_column",
    "caption_match",
    "code_proximity",
    "combined",
    "unresolved",
]


def _anchor_position(anchor: _CodeAnchor) -> tuple[float, float]:
    assert anchor.block.bbox is not None
    return anchor.block.bbox[1], anchor.block.bbox[0]


def _layout_role(block: ExtractedBlock, page: ExtractedPage) -> str:
    if not block.bbox or not page.height:
        return "content"
    text = (block.text or "").strip()
    if block.bbox[3] <= page.height * 0.08:
        return "header"
    if block.bbox[1] >= page.height * 0.92:
        return "page_number" if re.fullmatch(r"(?:p[aá]gina\s*)?\d+", text, re.I) else "footer"
    return "content"


def _detect_anchors(page: ExtractedPage, patterns: list[re.Pattern[str]]) -> list[_CodeAnchor]:
    anchors: list[_CodeAnchor] = []
    for block in page.blocks:
        role = _layout_role(block, page)
        block.metadata["layout_role"] = role
        if role != "content" or not block.text or not block.bbox:
            continue
        for pattern in patterns:
            match = pattern.search(block.text)
            if match:
                anchors.append(_CodeAnchor(match.group(0), block))
                break
    return sorted(anchors, key=_anchor_position)


def _block_score(block: ExtractedBlock, anchor: _CodeAnchor, page: ExtractedPage) -> float:
    if not block.bbox or not anchor.block.bbox or block.block_id == anchor.block.block_id:
        return -1.0
    diagonal = max(1.0, ((page.width or 1) ** 2 + (page.height or 1) ** 2) ** 0.5)
    proximity = max(0.0, 1.0 - bbox_distance(block.bbox, anchor.block.bbox) / (diagonal * 0.45))
    score = 0.55 * proximity
    if same_column(block.bbox, anchor.block.bbox):
        score += 0.30
    if same_row(block.bbox, anchor.block.bbox):
        score += 0.15
    if block.bbox[1] >= anchor.block.bbox[1]:
        score += 0.05
    return min(score, 1.0)


def _build_items(page: ExtractedPage, anchors: list[_CodeAnchor]) -> list[ExtractedItem]:
    items: list[ExtractedItem] = []
    assigned: dict[str, list[ExtractedBlock]] = {a.block.block_id: [a.block] for a in anchors}
    anchor_ids = set(assigned)
    for block in page.blocks:
        if (
            not block.bbox
            or block.block_id in anchor_ids
            or block.metadata.get("layout_role") != "content"
        ):
            continue
        ranked = sorted(
            ((_block_score(block, anchor, page), anchor) for anchor in anchors),
            key=lambda value: value[0],
            reverse=True,
        )
        if ranked and ranked[0][0] >= 0.35:
            assigned[ranked[0][1].block.block_id].append(block)
    for index, anchor in enumerate(anchors, 1):
        unique_blocks = {block.block_id: block for block in assigned[anchor.block.block_id]}
        blocks = list(unique_blocks.values())
        descriptions = [b for b in blocks if b.block_id != anchor.block.block_id and b.text]
        assert anchor.block.bbox is not None
        anchor_bbox: list[float] = anchor.block.bbox

        def description_distance(
            candidate: ExtractedBlock, reference: list[float] = anchor_bbox
        ) -> float:
            assert candidate.bbox is not None
            return bbox_distance(candidate.bbox, reference)

        description = min(descriptions, key=description_distance, default=None)
        items.append(
            ExtractedItem(
                item_id=f"p{page.page_number}-item-{index}",
                page_number=page.page_number,
                code=anchor.code,
                description=description.text if description else None,
                bbox=bbox_union([b.bbox for b in blocks if b.bbox]),
                code_block_id=anchor.block.block_id,
                description_block_id=description.block_id if description else None,
                text_block_ids=[b.block_id for b in blocks],
                association_confidence=0.9 if description else 0.65,
                requires_review=description is None,
            )
        )
    return items


def _image_item_score(
    image: ExtractedImage, item: ExtractedItem, page: ExtractedPage, settings: Settings
) -> tuple[float, list[str]]:
    if not image.bbox or not item.bbox:
        return 0.0, []
    signals: list[str] = []
    score = 0.0
    overlap = bbox_overlap(image.bbox, item.bbox)
    if overlap or bbox_contains(item.bbox, image.bbox):
        score += settings.association_region_weight * (0.8 + min(0.2, overlap * 0.2))
        signals.append("layout_region")
    if same_column(image.bbox, item.bbox):
        score += settings.association_column_weight
        signals.append("same_column")
    if same_row(image.bbox, item.bbox):
        score += settings.association_row_weight
        signals.append("caption_match")
    diagonal = max(1.0, ((page.width or 1) ** 2 + (page.height or 1) ** 2) ** 0.5)
    proximity = max(0.0, 1.0 - bbox_distance(image.bbox, item.bbox) / (diagonal * 0.55))
    score += settings.association_proximity_weight * proximity
    if proximity >= 0.55:
        signals.append("spatial_proximity")
    if item.code_block_id:
        code_block = next((b for b in page.blocks if b.block_id == item.code_block_id), None)
        if (
            code_block
            and code_block.bbox
            and bbox_distance(image.bbox, code_block.bbox) < diagonal * 0.25
        ):
            score += settings.association_code_weight
            signals.append("code_proximity")
    if image.image_type in {"logo", "icon"}:
        score *= 0.55
    return min(score, 1.0), signals


def _method(signals: list[str]) -> AssociationMethod:
    unique = list(dict.fromkeys(signals))
    if len(unique) > 1:
        return "combined"
    if not unique:
        return "unresolved"
    return unique[0]  # type: ignore[return-value]


def _add_image_only_item(page: ExtractedPage, image: ExtractedImage, index: int) -> ExtractedItem:
    def image_distance(candidate: ExtractedBlock) -> float:
        if candidate.bbox is None or image.bbox is None:
            return float("inf")
        return bbox_distance(candidate.bbox, image.bbox)

    nearby = sorted(
        (b for b in page.blocks if b.bbox and b.metadata.get("layout_role") == "content"),
        key=image_distance,
    )[:2]
    return ExtractedItem(
        item_id=f"p{page.page_number}-item-{index}",
        page_number=page.page_number,
        description=nearby[0].text if nearby else None,
        bbox=bbox_union([box for box in [image.bbox, *(b.bbox for b in nearby)] if box]),
        description_block_id=nearby[0].block_id if nearby else None,
        text_block_ids=[b.block_id for b in nearby],
        association_confidence=0.45,
        requires_review=True,
    )


def _classify_repeated_visuals(result: ExtractionResult) -> None:
    occurrences: dict[str, list[tuple[ExtractedImage, ExtractedPage]]] = {}
    for page in result.pages:
        for image in page.images:
            occurrences.setdefault(image.sha256, []).append((image, page))
    for grouped in occurrences.values():
        if len(grouped) < 3:
            continue
        top_positions = [
            image.bbox[1] / page.height
            for image, page in grouped
            if image.bbox and page.height
        ]
        same_header_position = (
            len(top_positions) >= 3
            and max(top_positions) - min(top_positions) <= 0.03
            and sum(top_positions) / len(top_positions) <= 0.12
        )
        if same_header_position:
            for image, _ in grouped:
                image.image_type = "logo"


def _associate_result(result: ExtractionResult, settings: Settings) -> ExtractionResult:
    _classify_repeated_visuals(result)
    patterns = [re.compile(pattern, re.I) for pattern in settings.association_code_patterns]
    for page in result.pages:
        anchors = _detect_anchors(page, patterns)
        page.items = _build_items(page, anchors)
        for image in page.images:
            image.visual_group_id = f"vg-{image.sha256[:16]}"
            ranked = sorted(
                (
                    (_image_item_score(image, item, page, settings), item)
                    for item in page.items
                ),
                key=lambda value: value[0][0],
                reverse=True,
            )
            candidates = ranked[: settings.association_max_candidates]
            image.association_candidates = [
                AssociationCandidate(item_id=item.item_id, score=round(scored[0], 4))
                for scored, item in candidates
            ]
            top_score = candidates[0][0][0] if candidates else 0.0
            margin = top_score - (candidates[1][0][0] if len(candidates) > 1 else 0.0)
            if candidates and top_score >= settings.association_min_confidence and (
                len(candidates) == 1 or margin >= settings.association_ambiguity_margin
            ):
                (score, signals), item = candidates[0]
                image.related_item_id = item.item_id
                image.related_code = item.code
                image.related_description = item.description
                image.nearby_text = " ".join(
                    b.text or "" for b in page.blocks if b.block_id in item.text_block_ids
                )[:1000] or None
                image.association_confidence = round(score, 4)
                image.association_method = _method(signals)
                image.requires_review = score < settings.association_strong_confidence
                item.image_ids.append(image.image_id)
                item.association_confidence = round((item.association_confidence + score) / 2, 4)
                item.requires_review = item.requires_review or image.requires_review
            else:
                image.association_confidence = round(top_score, 4) if candidates else None
                image.association_method = "unresolved"
                image.requires_review = True
                if not page.items:
                    item = _add_image_only_item(page, image, len(page.items) + 1)
                    item.image_ids.append(image.image_id)
                    page.items.append(item)
                    image.association_candidates = [
                        AssociationCandidate(item_id=item.item_id, score=0.45)
                    ]
                    image.related_item_id = item.item_id
                    image.related_description = item.description
                    image.association_confidence = 0.45
                    image.association_method = "spatial_proximity"
            normalized_format = (
                "jpeg" if image.format.lower() in {"jpg", "jpeg"} else image.format.lower()
            )
            image.mime_type = f"image/{normalized_format}"
        for table in page.tables:
            if table.bbox and page.items:
                items_with_bbox = [candidate for candidate in page.items if candidate.bbox]
                table_bbox = table.bbox

                def table_distance(
                    candidate: ExtractedItem, reference: list[float] = table_bbox
                ) -> float:
                    assert candidate.bbox is not None
                    return bbox_distance(reference, candidate.bbox)

                closest_item = min(items_with_bbox, key=table_distance, default=None)
                if closest_item and closest_item.bbox:
                    closest_item.table_ids.append(table.table_id)
                    closest_item.bbox = bbox_union([closest_item.bbox, table.bbox])
        validate_relationships(page)
    return result


class AssociationResolver(Protocol):
    def resolve(self, result: ExtractionResult) -> ExtractionResult: ...


class DeterministicAssociationResolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def resolve(self, result: ExtractionResult) -> ExtractionResult:
        return _associate_result(result, self.settings)


def associate_result(result: ExtractionResult, settings: Settings) -> ExtractionResult:
    resolver: AssociationResolver = DeterministicAssociationResolver(settings)
    return resolver.resolve(result)


def validate_relationships(page: ExtractedPage) -> None:
    item_ids = [item.item_id for item in page.items]
    image_ids = [image.image_id for image in page.images]
    table_ids = {table.table_id for table in page.tables}
    if len(item_ids) != len(set(item_ids)) or len(image_ids) != len(set(image_ids)):
        raise ValueError(f"IDs duplicados na página {page.page_number}")
    items = {item.item_id: item for item in page.items}
    images = {image.image_id: image for image in page.images}
    for image in page.images:
        if image.related_item_id and (
            image.related_item_id not in items
            or image.image_id not in items[image.related_item_id].image_ids
        ):
            raise ValueError(f"Relação de imagem inválida: {image.image_id}")
    for item in page.items:
        if any(
            image_id not in images or images[image_id].related_item_id != item.item_id
            for image_id in item.image_ids
        ):
            raise ValueError(f"Relação de item inválida: {item.item_id}")
        if any(table_id not in table_ids for table_id in item.table_ids):
            raise ValueError(f"Relação de tabela inválida: {item.item_id}")
