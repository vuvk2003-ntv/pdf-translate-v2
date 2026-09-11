"""Deterministic reconstruction of provider-facing logical text units.

The PDF extractor may expose one sentence as several physical runs or lines.
This module joins only fragments with positive structural evidence and keeps
the source ownership needed by the native renderer and debug tooling.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass, field
from statistics import mean, median

logger = logging.getLogger(__name__)

Bounds = tuple[float, float, float, float]
Identity = Hashable | None

_SENTENCE_END = re.compile(r"[.!?。！？](?:[\]\)}'\"”’]*)\s*$")
_ITEM_START = re.compile(
    r"^\s*(?:=>|⇒|→|[-–—•■□▪▸▹►▶●○◆◇]|"
    r"\d+(?:\.\d+)*(?:[.)])?|[가-힣]\))\s*"
)
_STYLE_TAG = {1: "s1", 2: "s2", 3: "s3"}


@dataclass(frozen=True)
class LogicalFragment:
    """One filtered source fragment presented to reconstruction."""

    fragment_id: str
    text: str
    page: int
    bbox: Bounds
    reading_order: int
    region_id: Identity = None
    cell_id: Identity = None
    column_id: Identity = None
    paragraph_id: Identity = None
    callout_id: Identity = None
    parent_id: Identity = None
    structural_role: str = "body"
    style: int = 0
    font_size: float | None = None

    @property
    def styled_text(self) -> str:
        tag = _STYLE_TAG.get(int(self.style))
        return self.text if tag is None else f"<{tag}>{self.text}</{tag}>"


@dataclass(frozen=True)
class MergeProvenance:
    """Inspectable ownership and reason for one reconstructed unit."""

    logical_unit_id: str
    source_fragment_ids: tuple[str, ...]
    merge_reason: str
    region_id: Identity
    cell_id: Identity
    page: int


@dataclass(frozen=True)
class LogicalUnit:
    """An atomic translation record with all source children retained."""

    logical_unit_id: str
    text: str
    fragments: tuple[LogicalFragment, ...]
    bbox: Bounds
    merge_reasons: tuple[str, ...] = ()

    @property
    def page(self) -> int:
        return self.fragments[0].page

    @property
    def provenance(self) -> MergeProvenance | None:
        if len(self.fragments) < 1:
            return None
        return MergeProvenance(
            logical_unit_id=self.logical_unit_id,
            source_fragment_ids=tuple(item.fragment_id for item in self.fragments),
            merge_reason=self.merge_reasons[0],
            region_id=self.fragments[0].region_id,
            cell_id=self.fragments[0].cell_id,
            page=self.page,
        )


@dataclass
class ReconstructionMetrics:
    """Per-run reconstruction counters with candidate-conservation checks."""

    raw_text_spans: int = 0
    candidate_fragments: int = 0
    assigned_candidate_fragments: int = 0
    unassigned_candidate_fragments: int = 0
    duplicate_fragment_assignments: int = 0
    logical_units: int = 0
    provider_bound_units: int = 0
    merged_fragment_count: int = 0
    singleton_unit_count: int = 0
    continuation_candidate_count: int = 0
    accepted_merge_count: int = 0
    rejected_merge_count: int = 0
    rejected_cross_cell: int = 0
    rejected_cross_column: int = 0
    rejected_structural_role: int = 0
    rejected_page_boundary: int = 0
    rejected_geometry: int = 0
    rejected_linguistic_boundary: int = 0
    _unit_char_counts: list[int] = field(default_factory=list, repr=False)
    provenance: list[MergeProvenance] = field(default_factory=list, repr=False)
    _candidate_id_counts: Counter[str] = field(default_factory=Counter, repr=False)
    _assignment_counts: Counter[str] = field(default_factory=Counter, repr=False)

    @property
    def average_chars_per_unit(self) -> float:
        return mean(self._unit_char_counts) if self._unit_char_counts else 0.0

    @property
    def median_chars_per_unit(self) -> float:
        return median(self._unit_char_counts) if self._unit_char_counts else 0.0

    @property
    def max_chars_per_unit(self) -> int:
        return max(self._unit_char_counts, default=0)

    def absorb(self, other: ReconstructionMetrics) -> None:
        """Add one builder invocation to this run-wide accumulator."""
        for name in (
            "logical_units",
            "provider_bound_units",
            "merged_fragment_count",
            "singleton_unit_count",
            "continuation_candidate_count",
            "accepted_merge_count",
            "rejected_merge_count",
            "rejected_cross_cell",
            "rejected_cross_column",
            "rejected_structural_role",
            "rejected_page_boundary",
            "rejected_geometry",
            "rejected_linguistic_boundary",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self._unit_char_counts.extend(other._unit_char_counts)
        self.provenance.extend(other.provenance)
        self._candidate_id_counts.update(other._candidate_id_counts)
        self._assignment_counts.update(other._assignment_counts)
        self._recount_conservation()

    def _recount_conservation(self) -> None:
        self.candidate_fragments = sum(self._candidate_id_counts.values())
        self.assigned_candidate_fragments = sum(
            1
            for identifier in self._candidate_id_counts
            if self._assignment_counts[identifier] >= 1
        )
        self.unassigned_candidate_fragments = sum(
            1
            for identifier in self._candidate_id_counts
            if self._assignment_counts[identifier] == 0
        )
        self.duplicate_fragment_assignments = sum(
            max(0, count - 1) for count in self._assignment_counts.values()
        ) + sum(max(0, count - 1) for count in self._candidate_id_counts.values())

    def assert_conservation(self) -> None:
        if (
            self.unassigned_candidate_fragments
            or self.duplicate_fragment_assignments
            or self.assigned_candidate_fragments != self.candidate_fragments
        ):
            raise RuntimeError(
                "logical reconstruction violated candidate-fragment conservation"
            )

    @property
    def candidate_merge_rate(self) -> float:
        return (
            self.continuation_candidate_count / self.raw_text_spans
            if self.raw_text_spans
            else 0.0
        )

    @property
    def merge_acceptance_rate(self) -> float:
        return (
            self.accepted_merge_count / self.continuation_candidate_count
            if self.continuation_candidate_count
            else 0.0
        )

    @property
    def singleton_rate(self) -> float:
        return (
            self.singleton_unit_count / self.logical_units
            if self.logical_units
            else 0.0
        )


@dataclass(frozen=True)
class ReconstructionResult:
    units: tuple[LogicalUnit, ...]
    metrics: ReconstructionMetrics


def reconstruction_health_review(metrics: ReconstructionMetrics) -> str:
    """Apply the Patch A benchmark review thresholds; never gate runtime PDFs."""
    substantial_candidates = metrics.continuation_candidate_count >= 100 or (
        metrics.raw_text_spans > 0
        and metrics.continuation_candidate_count / metrics.raw_text_spans >= 0.20
    )
    if substantial_candidates and (
        metrics.merge_acceptance_rate < 0.25 or metrics.singleton_rate > 0.75
    ):
        return "MANUAL_REVIEW_REQUIRED"
    return "PASS"


def _same_known(first: Identity, second: Identity) -> bool:
    return first is not None and second is not None and first == second


def _different_known(first: Identity, second: Identity) -> bool:
    return first is not None and second is not None and first != second


def _barrier_between(first: Bounds, second: Bounds, barriers: Sequence[Bounds]) -> bool:
    left = min(first[0], second[0])
    right = max(first[2], second[2])
    top = min(first[3], second[1])
    bottom = max(first[3], second[1])
    for x0, y0, x1, y1 in barriers:
        if x1 - x0 < 4:
            continue
        y = (y0 + y1) / 2
        overlap = max(0.0, min(right, x1) - max(left, x0))
        if (
            top - 0.5 <= y <= bottom + 0.5
            and overlap >= min(right - left, x1 - x0) * 0.3
        ):
            return True
    return False


def _structural_evidence(first: LogicalFragment, second: LogicalFragment) -> bool:
    """Require an established shared owner; unknown never means same."""
    return any(
        _same_known(getattr(first, name), getattr(second, name))
        for name in ("cell_id", "paragraph_id", "callout_id", "region_id", "parent_id")
    )


def _strong_owner(first: LogicalFragment, second: LogicalFragment) -> bool:
    return any(
        _same_known(getattr(first, name), getattr(second, name))
        for name in ("cell_id", "paragraph_id", "callout_id")
    )


def _geometry_kind(first: LogicalFragment, second: LogicalFragment) -> str | None:
    ax0, ay0, ax1, ay1 = first.bbox
    bx0, by0, bx1, by1 = second.bbox
    first_height = max(1.0, ay1 - ay0)
    second_height = max(1.0, by1 - by0)
    line_height = max(first_height, second_height)
    vertical_overlap = max(0.0, min(ay1, by1) - max(ay0, by0))
    same_line = vertical_overlap >= min(first_height, second_height) * 0.5
    if same_line:
        horizontal_gap = bx0 - ax1
        if -line_height * 0.35 <= horizontal_gap <= line_height * 2.0:
            return "same_line"
        return None

    vertical_gap = by0 - ay1
    if vertical_gap < -line_height * 0.35 or vertical_gap > line_height * 1.35:
        return None
    overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    smaller_width = max(1.0, min(ax1 - ax0, bx1 - bx0))
    aligned_flow = overlap / smaller_width >= 0.2 or abs(bx0 - ax0) <= line_height * 4.5
    if not aligned_flow or bx0 > ax1 + line_height * 1.5:
        return None
    return "wrapped_line"


def _merge_reason(
    first: LogicalFragment,
    second: LogicalFragment,
    geometry_kind: str,
) -> str:
    if geometry_kind == "same_line":
        if first.style != second.style:
            return "inline_emphasis"
        return "same_line_continuation"
    if _same_known(first.cell_id, second.cell_id):
        return "same_cell_continuation"
    if _same_known(first.paragraph_id, second.paragraph_id):
        return "same_paragraph"
    if _same_known(first.callout_id, second.callout_id):
        return "callout_continuation"
    return "wrapped_line"


def _decision(
    first: LogicalFragment,
    second: LogicalFragment,
    barriers: Sequence[Bounds],
) -> tuple[str | None, str | None]:
    if first.page != second.page:
        return None, "rejected_page_boundary"
    if _different_known(first.cell_id, second.cell_id) or (
        (first.cell_id is None) != (second.cell_id is None)
    ):
        return None, "rejected_cross_cell"
    if _different_known(first.column_id, second.column_id):
        return None, "rejected_cross_column"
    if first.structural_role != second.structural_role:
        return None, "rejected_structural_role"
    if first.font_size and second.font_size:
        size_ratio = max(first.font_size, second.font_size) / min(
            first.font_size, second.font_size
        )
        vertical_overlap = max(
            0.0,
            min(first.bbox[3], second.bbox[3]) - max(first.bbox[1], second.bbox[1]),
        )
        if size_ratio >= 1.30 and vertical_overlap <= 0:
            return None, "rejected_structural_role"
    if _different_known(first.callout_id, second.callout_id):
        return None, "rejected_structural_role"
    if _different_known(first.region_id, second.region_id):
        return None, "rejected_structural_role"
    if _different_known(first.paragraph_id, second.paragraph_id):
        return None, "rejected_structural_role"
    if not _structural_evidence(first, second):
        return None, "rejected_structural_role"
    geometry_kind = _geometry_kind(first, second)
    if geometry_kind is None or _barrier_between(first.bbox, second.bbox, barriers):
        return None, "rejected_geometry"
    if geometry_kind == "wrapped_line" and not _strong_owner(first, second):
        # A layout-model region can contain several nearby labels.  Without a
        # cell/paragraph/callout owner, accept only a line-shaped, substantive
        # predecessor as evidence of actual wrapping.
        first_height = max(1.0, first.bbox[3] - first.bbox[1])
        first_width = max(0.0, first.bbox[2] - first.bbox[0])
        visible = re.sub(r"<[^>]+>", "", first.text).strip()
        if len(visible) < 8 or first_width < first_height * 6:
            return None, "rejected_linguistic_boundary"
    if geometry_kind != "same_line" and (
        _SENTENCE_END.search(first.text.strip()) or _ITEM_START.match(second.text)
    ):
        return None, "rejected_linguistic_boundary"
    return _merge_reason(first, second, geometry_kind), None


def _join_text(
    first: str,
    second: str,
    reason: str,
    previous: LogicalFragment,
    current: LogicalFragment,
) -> str:
    if not first:
        return second
    if not second:
        return first
    if reason in {"same_line_continuation", "inline_emphasis"}:
        if first[-1].isspace() or second[0].isspace():
            return first + second
        height = max(1.0, previous.bbox[3] - previous.bbox[1])
        gap = current.bbox[0] - previous.bbox[2]
        return first + (" " if gap > height * 0.15 else "") + second
    if first.endswith("-"):
        return first + second.lstrip()
    return first.rstrip() + " " + second.lstrip()


def build_logical_units(
    fragments: Iterable[LogicalFragment],
    *,
    barriers: Iterable[Bounds] = (),
) -> ReconstructionResult:
    """Reconstruct atomic units using only deterministic source evidence."""
    ordered = sorted(
        fragments,
        key=lambda item: (item.page, item.reading_order, item.bbox[1], item.bbox[0]),
    )
    metrics = ReconstructionMetrics()
    metrics._candidate_id_counts.update(item.fragment_id for item in ordered)
    metrics._recount_conservation()
    if not ordered:
        return ReconstructionResult((), metrics)

    frozen_barriers = tuple(barriers)
    groups: list[list[LogicalFragment]] = [[ordered[0]]]
    group_reasons: list[list[str]] = [[]]
    for fragment in ordered[1:]:
        previous = groups[-1][-1]
        metrics.continuation_candidate_count += 1
        reason, rejection = _decision(previous, fragment, frozen_barriers)
        if reason is None:
            metrics.rejected_merge_count += 1
            setattr(metrics, rejection, getattr(metrics, rejection) + 1)
            groups.append([fragment])
            group_reasons.append([])
        else:
            metrics.accepted_merge_count += 1
            groups[-1].append(fragment)
            group_reasons[-1].append(reason)

    units: list[LogicalUnit] = []
    assignments: Counter[str] = Counter()
    for group, reasons in zip(groups, group_reasons):
        text = group[0].styled_text
        for previous, fragment, reason in zip(group, group[1:], reasons):
            text = _join_text(
                text,
                fragment.styled_text,
                reason,
                previous,
                fragment,
            )
        bounds = (
            min(item.bbox[0] for item in group),
            min(item.bbox[1] for item in group),
            max(item.bbox[2] for item in group),
            max(item.bbox[3] for item in group),
        )
        unit = LogicalUnit(
            logical_unit_id=(
                f"p{group[0].page + 1}:{group[0].fragment_id}..{group[-1].fragment_id}"
            ),
            text=text,
            fragments=tuple(group),
            bbox=bounds,
            merge_reasons=tuple(reasons),
        )
        units.append(unit)
        assignments.update(item.fragment_id for item in group)
        metrics._unit_char_counts.append(len(text))
        if len(group) == 1:
            metrics.singleton_unit_count += 1
        else:
            metrics.merged_fragment_count += len(group) - 1
            provenance = unit.provenance
            if provenance is not None:
                metrics.provenance.append(provenance)
                logger.debug(
                    "logical reconstruction %s fragments=%s reason=%s region=%r cell=%r page=%s",
                    provenance.logical_unit_id,
                    provenance.source_fragment_ids,
                    provenance.merge_reason,
                    provenance.region_id,
                    provenance.cell_id,
                    provenance.page + 1,
                )

    metrics.logical_units = len(units)
    metrics._assignment_counts = assignments
    metrics._recount_conservation()
    metrics.assert_conservation()
    return ReconstructionResult(tuple(units), metrics)
