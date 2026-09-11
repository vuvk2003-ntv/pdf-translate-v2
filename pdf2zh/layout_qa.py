"""Lightweight, read-only geometry checks for a rebuilt PDF."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Iterable, Sequence

import pymupdf

from pdf2zh.invariants import find_windows_path_literals
from pdf2zh.rules import upright_line_bounds

Rect = tuple[float, float, float, float]
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7a3]")
METADATA_PATTERNS = {
    "document_code": re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+){2,}\b"),
    "revision": re.compile(r"\bRev\.\s*:?\s*\d+(?:\.\d+)+\b", re.IGNORECASE),
    "page_number": re.compile(r"\b\d{1,4}\s*/\s*\d{1,4}\b"),
    "date": re.compile(r"\b(?:19|20)\d{2}[.-]\s*\d{1,2}[.-]\s*\d{1,2}\b"),
    "time": re.compile(r"\b(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\b"),
    "author_id": re.compile(r"\b[A-Z]{2,}[A-Z0-9]*\d{2,}\b"),
    "confidential": re.compile(
        r"CONFIDENTIAL(?:\s+LG\s+Display\s+Co\.,?\s+Ltd)?(?:\s+\d{4})?",
        re.IGNORECASE,
    ),
}


@dataclass(frozen=True)
class SpanGeometry:
    text: str
    bbox: Rect
    size: float
    block: int = 0
    line: int = 0

    @property
    def normalized_text(self) -> str:
        return re.sub(r"\s+", " ", self.text).strip()


@dataclass(frozen=True)
class LayoutWarning:
    kind: str
    page: int
    bbox: Rect
    detail: str


@dataclass(frozen=True)
class LayoutQAReport:
    pages_checked: int
    warnings: tuple[LayoutWarning, ...]
    elapsed_seconds: float


@dataclass(frozen=True)
class MetadataOccurrence:
    category: str
    value: str
    bbox: Rect


def classify_cjk_residual(
    text: str,
    *,
    proper_names: Iterable[str] = (),
    official_tokens: Iterable[str] = (),
    raster: bool = False,
) -> str:
    """Classify residual CJK only from explicit review evidence."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if raster:
        return "RASTER_IMAGE_TEXT_OUT_OF_SCOPE"
    if normalized in set(proper_names):
        return "ALLOWED_PROPER_NAME"
    if normalized in set(official_tokens):
        return "ALLOWED_OFFICIAL_TOKEN"
    path_spans = find_windows_path_literals(normalized)
    cjk_offsets = [match.start() for match in CJK_PATTERN.finditer(normalized)]
    if cjk_offsets and path_spans and all(
        any(span.start <= offset < span.end for span in path_spans)
        for offset in cjk_offsets
    ):
        return "ALLOWED_TECHNICAL_LITERAL"
    return "UNTRANSLATED_PROSE"


def _metadata_zone(
    bbox: Rect, direction: Sequence[float], page_height: float
) -> bool:
    upright = (
        len(direction) == 2
        and abs(float(direction[0]) - 1.0) <= 0.01
        and abs(float(direction[1])) <= 0.01
    )
    return bbox[1] <= page_height * 0.15 or bbox[3] >= page_height * 0.85 or not upright


def _metadata_value(category: str, value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if category in {"page_number", "date", "time"}:
        return value.replace(" ", "")
    return value


def immutable_metadata_occurrences(page: pymupdf.Page) -> list[MetadataOccurrence]:
    """Extract immutable values only from header, footer, or watermark lines."""
    occurrences: list[MetadataOccurrence] = []
    seen: set[tuple[str, str, tuple[float, ...]]] = set()
    for block in page.get_text("dict").get("blocks", ()):
        for line in block.get("lines", ()):
            spans = line.get("spans", ())
            text = "".join(str(span.get("text", "")) for span in spans)
            bbox_value = line.get("bbox", ())
            if not text or len(bbox_value) != 4:
                continue
            bbox = upright_line_bounds(line) or tuple(
                float(value) for value in bbox_value
            )
            direction = line.get("dir", (1.0, 0.0))
            if not _metadata_zone(bbox, direction, float(page.rect.height)):
                continue
            for category, pattern in METADATA_PATTERNS.items():
                for match in pattern.finditer(text):
                    value = _metadata_value(category, match.group(0))
                    key = (category, value, tuple(round(part, 2) for part in bbox))
                    if key in seen:
                        continue
                    seen.add(key)
                    occurrences.append(MetadataOccurrence(category, value, bbox))
    return occurrences


def _rect_overlap(first: Rect, second: Rect) -> float:
    intersection = _area(_intersection(first, second))
    smaller = min(_area(first), _area(second))
    return intersection / smaller if smaller > 0 else 0.0


def _compact_alnum(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def analyze_metadata_page(
    source_page: pymupdf.Page,
    target_page: pymupdf.Page,
    page_number: int,
) -> list[LayoutWarning]:
    """Compare immutable metadata and translatable header/footer CJK separately."""
    warnings: list[LayoutWarning] = []
    source = immutable_metadata_occurrences(source_page)
    target = immutable_metadata_occurrences(target_page)
    for category in METADATA_PATTERNS:
        source_values = sorted(item.value for item in source if item.category == category)
        target_values = sorted(item.value for item in target if item.category == category)
        if source_values == target_values:
            continue
        kind = "page_number_mismatch" if category == "page_number" else "immutable_metadata_mismatch"
        warnings.append(
            LayoutWarning(
                kind,
                page_number,
                tuple(float(value) for value in target_page.rect),
                f"{category}: source={source_values!r}, target={target_values!r}",
            )
        )

    target_lines: list[tuple[str, Rect]] = []
    for block in target_page.get_text("dict").get("blocks", ()):
        for line in block.get("lines", ()):
            bbox_value = line.get("bbox", ())
            if len(bbox_value) != 4:
                continue
            bbox = upright_line_bounds(line) or tuple(
                float(value) for value in bbox_value
            )
            direction = line.get("dir", (1.0, 0.0))
            text = "".join(str(span.get("text", "")) for span in line.get("spans", ()))
            if text and _metadata_zone(bbox, direction, float(target_page.rect.height)):
                target_lines.append((text, bbox))

    target_by_category = {(item.category, item.value) for item in target}
    for source_item in source:
        if (source_item.category, source_item.value) in target_by_category:
            continue
        source_compact = _compact_alnum(source_item.value)
        if len(source_compact) < 6:
            continue
        nearby = " ".join(
            text for text, bbox in target_lines if _rect_overlap(source_item.bbox, bbox) >= 0.2
        )
        nearby_compact = _compact_alnum(nearby)
        if source_compact not in nearby_compact and any(
            source_compact[index : index + 4] in nearby_compact
            for index in range(len(source_compact) - 3)
        ):
            warnings.append(
                LayoutWarning(
                    "metadata_fragmentation",
                    page_number,
                    source_item.bbox,
                    f"{source_item.category}: {source_item.value!r}",
                )
            )

    for text, bbox in target_lines:
        if CJK_PATTERN.search(text):
            warnings.append(
                LayoutWarning(
                    "header_footer_untranslated_prose",
                    page_number,
                    bbox,
                    re.sub(r"\s+", " ", text).strip()[:80],
                )
            )
    return warnings


def _area(rect: Rect) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersection(first: Rect, second: Rect) -> Rect:
    return (
        max(first[0], second[0]),
        max(first[1], second[1]),
        min(first[2], second[2]),
        min(first[3], second[3]),
    )


def _union(first: Rect, second: Rect) -> Rect:
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def _iou(first: Rect, second: Rect) -> float:
    intersection = _area(_intersection(first, second))
    union = _area(first) + _area(second) - intersection
    return intersection / union if union > 0 else 0.0


def _significant_overlap(first: SpanGeometry, second: SpanGeometry) -> bool:
    if first.block == second.block and first.line == second.line:
        return False
    if len(first.normalized_text) < 2 or len(second.normalized_text) < 2:
        return False
    smaller = min(_area(first.bbox), _area(second.bbox))
    intersection = _area(_intersection(first.bbox, second.bbox))
    return smaller > 0 and intersection >= 9.0 and intersection / smaller >= 0.35


def _overlap_boxes(spans: Sequence[SpanGeometry]) -> list[Rect]:
    return [
        _union(first.bbox, second.bbox)
        for index, first in enumerate(spans)
        for second in spans[index + 1 :]
        if _significant_overlap(first, second)
    ]


def _inside_center(span: SpanGeometry, rect: Rect) -> bool:
    x = (span.bbox[0] + span.bbox[2]) / 2
    y = (span.bbox[1] + span.bbox[3]) / 2
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def _spills(span: SpanGeometry, rect: Rect, tolerance: float = 1.5) -> bool:
    return (
        span.bbox[0] < rect[0] - tolerance
        or span.bbox[1] < rect[1] - tolerance
        or span.bbox[2] > rect[2] + tolerance
        or span.bbox[3] > rect[3] + tolerance
    )


def _material_cell_spill(span: SpanGeometry, rect: Rect) -> bool:
    """Ignore border-touch noise while retaining visible cross-cell overflow."""
    if len(span.normalized_text) < 2:
        return False
    width = max(1.0, span.bbox[2] - span.bbox[0])
    height = max(1.0, span.bbox[3] - span.bbox[1])
    horizontal = max(rect[0] - span.bbox[0], span.bbox[2] - rect[2], 0.0)
    vertical = max(rect[1] - span.bbox[1], span.bbox[3] - rect[3], 0.0)
    return horizontal / width >= 0.2 or vertical / height >= 0.2


def _outside(span: SpanGeometry, rect: Rect, tolerance: float = 1.0) -> bool:
    return _spills(span, rect, tolerance)


def analyze_layout_page(
    source_spans: Sequence[SpanGeometry],
    target_spans: Sequence[SpanGeometry],
    cells: Sequence[Rect],
    page_rect: Rect,
    page_number: int,
) -> list[LayoutWarning]:
    """Find target-only collisions, spills, micro-fonts, and duplicate draws."""
    warnings: list[LayoutWarning] = []
    source_overlap_boxes = _overlap_boxes(source_spans)
    source_texts = {span.normalized_text for span in source_spans if span.normalized_text}

    source_outside = sum(_outside(span, page_rect) for span in source_spans)
    target_outside = [span for span in target_spans if _outside(span, page_rect)]
    for span in target_outside[source_outside:]:
        warnings.append(LayoutWarning(
            "page_out_of_bounds", page_number, span.bbox, span.normalized_text[:80]
        ))

    for index, first in enumerate(target_spans):
        for second in target_spans[index + 1 :]:
            if not _significant_overlap(first, second):
                continue
            combined = _union(first.bbox, second.bbox)
            if any(_iou(combined, source_box) >= 0.5 for source_box in source_overlap_boxes):
                continue
            retained = (
                first.normalized_text in source_texts,
                second.normalized_text in source_texts,
            )
            kind = "translated_retained_overlap" if retained[0] != retained[1] else "new_text_overlap"
            warnings.append(LayoutWarning(
                kind,
                page_number,
                combined,
                f"{first.normalized_text[:40]!r} overlaps {second.normalized_text[:40]!r}",
            ))

    source_duplicate_boxes: list[Rect] = []
    for index, first in enumerate(source_spans):
        for second in source_spans[index + 1 :]:
            if (
                first.normalized_text
                and first.normalized_text == second.normalized_text
                and _iou(first.bbox, second.bbox) >= 0.8
            ):
                source_duplicate_boxes.append(_union(first.bbox, second.bbox))
    for index, first in enumerate(target_spans):
        for second in target_spans[index + 1 :]:
            if (
                not first.normalized_text
                or first.normalized_text != second.normalized_text
                or _iou(first.bbox, second.bbox) < 0.8
            ):
                continue
            combined = _union(first.bbox, second.bbox)
            if any(_iou(combined, source_box) >= 0.8 for source_box in source_duplicate_boxes):
                continue
            warnings.append(LayoutWarning(
                "duplicate_local_render",
                page_number,
                combined,
                first.normalized_text[:80],
            ))

    for cell_index, cell in enumerate(cells):
        source_in_cell = [span for span in source_spans if _inside_center(span, cell)]
        target_in_cell = [span for span in target_spans if _inside_center(span, cell)]
        source_spills = sum(_material_cell_spill(span, cell) for span in source_in_cell)
        target_spills = [span for span in target_in_cell if _material_cell_spill(span, cell)]
        for span in target_spills[source_spills:]:
            warnings.append(LayoutWarning(
                "table_cell_spill",
                page_number,
                span.bbox,
                f"cell {cell_index + 1}: {span.normalized_text[:80]}",
            ))

        if not source_in_cell or not target_in_cell or _area(cell) <= 0:
            continue
        source_size = median(span.size for span in source_in_cell if span.size > 0)
        target_size = median(span.size for span in target_in_cell if span.size > 0)
        if source_size <= 0 or target_size / source_size > 0.55:
            continue
        used = target_in_cell[0].bbox
        for span in target_in_cell[1:]:
            used = _union(used, span.bbox)
        if _area(used) / _area(cell) > 0.35:
            continue
        scale = source_size / target_size
        enlarged = (
            used[0],
            used[1],
            used[0] + (used[2] - used[0]) * scale,
            used[1] + (used[3] - used[1]) * scale,
        )
        if _spills(SpanGeometry("", enlarged, source_size), cell, 0.5):
            continue
        warnings.append(LayoutWarning(
            "suspicious_micro_font",
            page_number,
            used,
            f"cell {cell_index + 1}: {target_size:.2f}pt vs source {source_size:.2f}pt",
        ))
    unique: dict[tuple[str, int, tuple[float, ...]], LayoutWarning] = {}
    for warning in warnings:
        key = (
            warning.kind,
            warning.page,
            tuple(round(value, 1) for value in warning.bbox),
        )
        unique.setdefault(key, warning)
    return list(unique.values())


def _page_spans(page: pymupdf.Page) -> list[SpanGeometry]:
    spans: list[SpanGeometry] = []
    for block_index, block in enumerate(page.get_text("dict").get("blocks", ())):
        for line_index, line in enumerate(block.get("lines", ())):
            direction = line.get("dir", (1.0, 0.0))
            if len(direction) == 2 and (
                float(direction[0]) < 0.95 or abs(float(direction[1])) > 0.05
            ):
                # Diagonal watermarks and vertical labels cross ordinary table
                # text by design; treating them as flow text creates false alarms.
                continue
            for span in line.get("spans", ()):
                text = str(span.get("text", ""))
                span_bbox = span.get("bbox", ())
                bbox = (
                    upright_line_bounds({"bbox": span_bbox, "spans": [span]})
                    or tuple(float(value) for value in span_bbox)
                )
                size = float(span.get("size", 0))
                if text.strip() and len(bbox) == 4 and math.isfinite(size) and size > 0:
                    spans.append(SpanGeometry(
                        text,
                        bbox,  # type: ignore[arg-type]
                        size,
                        block_index,
                        line_index,
                    ))
    return spans


def _page_cells(page: pymupdf.Page) -> list[Rect]:
    try:
        tables = page.find_tables().tables
    except Exception:
        return []
    cells: list[Rect] = []
    for table in tables:
        for cell in table.cells:
            if cell is None:
                continue
            rect = tuple(float(value) for value in cell)
            if len(rect) == 4 and rect not in cells:
                cells.append(rect)  # type: ignore[arg-type]
    return cells


def run_layout_qa(
    source_pdf: str | Path,
    target_pdf: str | Path,
    pages: Iterable[int] | None = None,
) -> LayoutQAReport:
    """Compare source and rebuilt geometry without modifying either PDF."""
    started = time.perf_counter()
    warnings: list[LayoutWarning] = []
    with pymupdf.open(source_pdf) as source, pymupdf.open(target_pdf) as target:
        if source.page_count != target.page_count:
            warnings.append(LayoutWarning(
                "page_geometry", 0, (0, 0, 0, 0),
                f"page count changed from {source.page_count} to {target.page_count}",
            ))
        limit = min(source.page_count, target.page_count)
        selected = list(range(limit)) if pages is None else [page for page in pages if 0 <= page < limit]
        for page_index in selected:
            source_page, target_page = source[page_index], target[page_index]
            source_rect = tuple(float(value) for value in source_page.rect)
            target_rect = tuple(float(value) for value in target_page.rect)
            if source_rect != target_rect or source_page.rotation != target_page.rotation:
                warnings.append(LayoutWarning(
                    "page_geometry", page_index + 1, target_rect, "canvas size or rotation changed"
                ))
            warnings.extend(analyze_layout_page(
                _page_spans(source_page),
                _page_spans(target_page),
                _page_cells(source_page),
                target_rect,  # type: ignore[arg-type]
                page_index + 1,
            ))
            warnings.extend(analyze_metadata_page(source_page, target_page, page_index + 1))
    return LayoutQAReport(len(selected), tuple(warnings), time.perf_counter() - started)
