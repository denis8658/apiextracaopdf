from app.core.config import Settings
from app.extraction.geometry import bbox_distance, bbox_overlap, relative_position
from app.extraction.item_association import associate_result, validate_relationships
from app.schemas.extraction import (
    ExtractedBlock,
    ExtractedImage,
    ExtractedPage,
    ExtractionResult,
)


def block(
    identifier: str, text: str, bbox: list[float], order: int, page_number: int = 1
) -> ExtractedBlock:
    return ExtractedBlock(
        block_id=identifier,
        block_type="text",
        page_number=page_number,
        text=text,
        bbox=bbox,
        source="native",
        reading_order=order,
    )


def image(
    identifier: str, bbox: list[float], digest: str = "a" * 64, page_number: int = 1
) -> ExtractedImage:
    return ExtractedImage(
        image_id=identifier,
        page_number=page_number,
        index=int(identifier.rsplit("i", 1)[1]),
        image_type="diagram",
        format="png",
        width=200,
        height=200,
        bbox=bbox,
        sha256=digest,
    )


def result(blocks: list[ExtractedBlock], images: list[ExtractedImage]) -> ExtractionResult:
    return ExtractionResult(
        plain_text="",
        markdown="",
        engine="native",
        pages=[
            ExtractedPage(
                page_number=1,
                plain_text="",
                markdown="",
                blocks=blocks,
                images=images,
                width=600,
                height=800,
                extraction_method="native",
                has_native_text=True,
                ocr_used=False,
            )
        ],
    )


def test_geometry_primitives_are_deterministic() -> None:
    assert bbox_overlap([0, 0, 10, 10], [5, 5, 15, 15]) == 0.25
    assert bbox_distance([0, 0, 10, 10], [20, 0, 30, 10]) == 10
    assert relative_position([0, 0, 10, 10], [20, 0, 30, 10]) == "left"


def test_associates_two_column_items_and_images_bidirectionally() -> None:
    extracted = result(
        [
            block("p1-b1", "ABC-100", [30, 100, 100, 120], 1),
            block("p1-b2", "Torneira cromada", [30, 125, 200, 150], 2),
            block("p1-b3", "XYZ-200", [330, 100, 400, 120], 3),
            block("p1-b4", "Válvula industrial", [330, 125, 520, 150], 4),
        ],
        [image("p1-i1", [30, 160, 220, 350]), image("p1-i2", [330, 160, 520, 350])],
    )

    associate_result(extracted, Settings())
    page = extracted.pages[0]

    assert [item.code for item in page.items] == ["ABC-100", "XYZ-200"]
    assert page.images[0].related_item_id == page.items[0].item_id
    assert page.images[1].related_item_id == page.items[1].item_id
    assert page.items[0].image_ids == ["p1-i1"]
    assert page.items[1].image_ids == ["p1-i2"]
    validate_relationships(page)


def test_ambiguous_image_keeps_ranked_candidates_for_review() -> None:
    extracted = result(
        [
            block("p1-b1", "ABC-100", [100, 100, 170, 120], 1),
            block("p1-b2", "Primeiro", [100, 125, 180, 145], 2),
            block("p1-b3", "XYZ-200", [300, 100, 370, 120], 3),
            block("p1-b4", "Segundo", [300, 125, 380, 145], 4),
        ],
        [image("p1-i1", [205, 160, 275, 250])],
    )

    associate_result(extracted, Settings(association_ambiguity_margin=0.20))
    associated = extracted.pages[0].images[0]

    assert associated.related_item_id is None
    assert associated.association_method == "unresolved"
    assert associated.requires_review is True
    assert len(associated.association_candidates) == 2
    assert associated.association_candidates[0].score >= associated.association_candidates[1].score


def test_image_without_code_creates_reviewable_item() -> None:
    extracted = result(
        [block("p1-b1", "Produto sem código", [30, 100, 220, 130], 1)],
        [image("p1-i1", [30, 150, 220, 340])],
    )

    associate_result(extracted, Settings())
    page = extracted.pages[0]

    assert len(page.items) == 1
    assert page.items[0].code is None
    assert page.items[0].requires_review is True
    assert page.images[0].related_item_id == page.items[0].item_id
    validate_relationships(page)


def test_equal_hash_preserves_occurrences_but_groups_visual_content() -> None:
    extracted = result(
        [block("p1-b1", "ABC-100", [20, 100, 100, 120], 1)],
        [image("p1-i1", [20, 140, 100, 220]), image("p1-i2", [60, 140, 140, 220])],
    )

    associate_result(extracted, Settings())
    images = extracted.pages[0].images

    assert images[0].image_id != images[1].image_id
    assert images[0].visual_group_id == images[1].visual_group_id
    assert extracted.pages[0].items[0].image_ids == ["p1-i1", "p1-i2"]


def test_code_without_image_preserves_measure_in_item_blocks() -> None:
    extracted = result(
        [
            block("p1-b1", "PR-100", [30, 100, 100, 120], 1),
            block("p1-b2", "Perfil tubular", [30, 125, 180, 145], 2),
            block("p1-b3", "25 mm", [30, 150, 100, 170], 3),
        ],
        [],
    )

    associate_result(extracted, Settings())
    item = extracted.pages[0].items[0]

    assert item.code == "PR-100"
    assert item.image_ids == []
    assert item.text_block_ids == ["p1-b1", "p1-b2", "p1-b3"]


def test_same_visual_three_pages_keeps_independent_item_relationships() -> None:
    pages = []
    digest = "f" * 64
    for page_number, (code, measure) in enumerate(
        [("PR-100", "25 mm"), ("PR-101", "32 mm"), ("PR-102", "40 mm")], 12
    ):
        pages.append(
            ExtractedPage(
                page_number=page_number,
                plain_text=f"{code}\n{measure}",
                markdown="",
                blocks=[
                    block(
                        f"p{page_number}-b1", code, [30, 100, 100, 120], 1, page_number
                    ),
                    block(
                        f"p{page_number}-b2", measure, [30, 125, 100, 145], 2, page_number
                    ),
                ],
                images=[
                    image(
                        f"p{page_number}-i1",
                        [30, 160, 220, 350],
                        digest,
                        page_number,
                    )
                ],
                width=600,
                height=800,
                has_native_text=True,
                ocr_used=False,
            )
        )
    extracted = ExtractionResult(
        plain_text="", markdown="", engine="native", pages=pages
    )

    associate_result(extracted, Settings())

    images = [page.images[0] for page in extracted.pages]
    assert len({associated.image_id for associated in images}) == 3
    assert len({associated.visual_group_id for associated in images}) == 1
    assert [associated.related_item_id for associated in images] == [
        "p12-item-1",
        "p13-item-1",
        "p14-item-1",
    ]
    assert [page.items[0].text_block_ids[-1] for page in extracted.pages] == [
        "p12-b2",
        "p13-b2",
        "p14-b2",
    ]


def test_repeated_header_visual_is_classified_as_logo_without_removal() -> None:
    pages = []
    for page_number in range(1, 4):
        pages.append(
            ExtractedPage(
                page_number=page_number,
                plain_text="",
                markdown="",
                blocks=[],
                images=[
                    image(
                        f"p{page_number}-i1",
                        [20, 20, 100, 60],
                        "e" * 64,
                        page_number,
                    )
                ],
                width=600,
                height=800,
                has_native_text=False,
                ocr_used=False,
            )
        )
    extracted = ExtractionResult(plain_text="", markdown="", engine="native", pages=pages)

    associate_result(extracted, Settings())

    assert sum(len(page.images) for page in extracted.pages) == 3
    assert all(page.images[0].image_type == "logo" for page in extracted.pages)


def test_invalid_bidirectional_reference_is_rejected() -> None:
    extracted = result([], [image("p1-i1", [0, 0, 10, 10])])
    extracted.pages[0].images[0].related_item_id = "missing"

    try:
        validate_relationships(extracted.pages[0])
    except ValueError as exc:
        assert "imagem inválida" in str(exc)
    else:
        raise AssertionError("A referência inválida deveria falhar")
