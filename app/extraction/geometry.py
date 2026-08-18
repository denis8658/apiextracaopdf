import math
from typing import Literal

BBox = list[float]


def bbox_center(box: BBox) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def bbox_area(box: BBox) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def bbox_overlap(a: BBox, b: BBox) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    denominator = min(bbox_area(a), bbox_area(b))
    return intersection / denominator if denominator else 0.0


def bbox_contains(outer: BBox, inner: BBox) -> bool:
    x, y = bbox_center(inner)
    return outer[0] <= x <= outer[2] and outer[1] <= y <= outer[3]


def horizontal_distance(a: BBox, b: BBox) -> float:
    return max(0.0, max(a[0], b[0]) - min(a[2], b[2]))


def vertical_distance(a: BBox, b: BBox) -> float:
    return max(0.0, max(a[1], b[1]) - min(a[3], b[3]))


def bbox_distance(a: BBox, b: BBox) -> float:
    return math.hypot(horizontal_distance(a, b), vertical_distance(a, b))


def same_column(a: BBox, b: BBox, tolerance: float = 0.15) -> bool:
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    narrowest = min(a[2] - a[0], b[2] - b[0])
    if narrowest > 0 and overlap / narrowest >= tolerance:
        return True
    ax, _ = bbox_center(a)
    bx, _ = bbox_center(b)
    return abs(ax - bx) <= max(a[2] - a[0], b[2] - b[0]) * 0.6


def same_row(a: BBox, b: BBox, tolerance: float = 0.25) -> bool:
    overlap = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    shortest = min(a[3] - a[1], b[3] - b[1])
    return bool(shortest > 0 and overlap / shortest >= tolerance)


def relative_position(a: BBox, b: BBox) -> Literal["above", "below", "left", "right", "overlap"]:
    if bbox_overlap(a, b) > 0:
        return "overlap"
    ax, ay = bbox_center(a)
    bx, by = bbox_center(b)
    if abs(ax - bx) > abs(ay - by):
        return "left" if ax < bx else "right"
    return "above" if ay < by else "below"


def bbox_union(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]
