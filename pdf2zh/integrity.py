"""Deterministic Patch B translation-integrity guards and occurrence ledger.

This module consumes the logical units emitted by the converter.  It does not
split, merge, route, render, or call a provider.  Every check is local and its
cost is linear in the amount of extracted text.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum

from pdf2zh.invariants import (
    find_document_label_literals,
    find_windows_path_literals,
    protected_korean_ui_labels,
    protected_verified_proper_names,
)


class OccurrenceStatus(str, Enum):
    TRANSLATED = "TRANSLATED"
    ALLOWED_PRESERVE = "ALLOWED_PRESERVE"
    UNRESOLVED = "UNRESOLVED"


APPROVED_PRESERVE_REASONS = frozenset(
    {
        "technical_invariant",
        "reviewed_proper_name",
        "explicit_technical_term",
        "literal_path",
        "immutable_metadata",
        "explicit_ui_rule",
        "explicit_preserve_list",
    }
)


class IntegrityFailure(str, Enum):
    UNAPPROVED_PRESERVE = "unapproved_preserve"
    HANGUL_LEAK = "hangul_leak"
    HAN_LEAK = "han_leak"
    TECHNICAL_INVARIANT = "technical_invariant"
    UNCHANGED_PROSE = "unchanged_prose"
    REPEATED_TOKEN = "repeated_token_corruption"
    REPEATED_CHAR = "repeated_char_corruption"
    LENGTH_EXPLOSION = "length_explosion"
    UNEXPECTED_SCRIPT = "unexpected_script"


_COUNTER_BY_FAILURE = {
    IntegrityFailure.UNAPPROVED_PRESERVE.value: "unapproved_preserve_failures",
    IntegrityFailure.HANGUL_LEAK.value: "hangul_leak_failures",
    IntegrityFailure.HAN_LEAK.value: "han_leak_failures",
    IntegrityFailure.TECHNICAL_INVARIANT.value: "technical_invariant_failures",
    IntegrityFailure.UNCHANGED_PROSE.value: "unchanged_prose_failures",
    IntegrityFailure.REPEATED_TOKEN.value: "repeated_token_corruption_failures",
    IntegrityFailure.REPEATED_CHAR.value: "repeated_char_corruption_failures",
    IntegrityFailure.LENGTH_EXPLOSION.value: "length_explosion_failures",
    IntegrityFailure.UNEXPECTED_SCRIPT.value: "unexpected_script_failures",
}


_STYLE_TAG = re.compile(r"</?s[123]>", re.IGNORECASE)
_FORMULA_TAG = re.compile(r"</?b\d+>|\{\s*v[\d\s]+\}", re.IGNORECASE)
_SHORT_TOKEN = re.compile(r"(?<!\w)(\w{1,3})(?:[ \t]+\1){3,}(?!\w)", re.IGNORECASE)
_REPEATED_CHAR = re.compile(r"([^\w\s])\1{3,}")


def _in_ranges(character: str, ranges: Sequence[tuple[int, int]]) -> bool:
    value = ord(character)
    return any(start <= value <= end for start, end in ranges)


_HANGUL_RANGES = (
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7A3),
    (0xD7B0, 0xD7FF),
)
_HAN_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B820, 0x2CEAF),
    (0x2CEB0, 0x2EBEF),
    (0x30000, 0x3134F),
)
_UNRELATED_SCRIPT_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "Cyrillic": ((0x0400, 0x052F),),
    "Arabic": ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF)),
    "Devanagari": ((0x0900, 0x097F),),
    "Greek": ((0x0370, 0x03FF), (0x1F00, 0x1FFF)),
    "Hebrew": ((0x0590, 0x05FF),),
    "Bengali": ((0x0980, 0x09FF),),
    "Tamil": ((0x0B80, 0x0BFF),),
    "Telugu": ((0x0C00, 0x0C7F),),
    "Kannada": ((0x0C80, 0x0CFF),),
    "Malayalam": ((0x0D00, 0x0D7F),),
    "Thai": ((0x0E00, 0x0E7F),),
    "Armenian": ((0x0530, 0x058F),),
    "Georgian": ((0x10A0, 0x10FF),),
}


def contains_hangul(text: str) -> bool:
    return any(_in_ranges(character, _HANGUL_RANGES) for character in text)


def contains_han(text: str) -> bool:
    return any(_in_ranges(character, _HAN_RANGES) for character in text)


@dataclass(frozen=True)
class AllowedPreserveSpan:
    text: str
    reason: str
    preserve_reason_is_approved: bool = True


@dataclass(frozen=True)
class EligibleSourceSpan:
    source_span_id: str
    page: int
    source_text: str
    region_id: str | None = None
    logical_unit_id: str | None = None


@dataclass(frozen=True)
class Occurrence:
    occurrence_id: str
    page: int
    source_span_ids: tuple[str, ...]
    source_text: str
    logical_unit_id: str | None
    status: OccurrenceStatus | None
    target_text: str | None = None
    preserve_reason: str | None = None
    preserve_reason_is_approved: bool = False
    unresolved_reason: str | None = None
    allowed_preserve_spans: tuple[AllowedPreserveSpan, ...] = ()
    validation_failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrityMetrics:
    eligible_source_spans: int = 0
    ledger_assigned_source_spans: int = 0
    unassigned_source_spans: int = 0
    duplicate_source_span_assignments: int = 0
    source_occurrences: int = 0
    translated_occurrences: int = 0
    allowed_preserve_occurrences: int = 0
    unresolved_occurrences: int = 0
    unknown_occurrences: int = 0
    unapproved_preserve_failures: int = 0
    hangul_leak_failures: int = 0
    han_leak_failures: int = 0
    technical_invariant_failures: int = 0
    unchanged_prose_failures: int = 0
    repeated_token_corruption_failures: int = 0
    repeated_char_corruption_failures: int = 0
    length_explosion_failures: int = 0
    unexpected_script_failures: int = 0
    cache_validation_failures: int = 0
    final_output_script_leaks: int = 0

    @property
    def accounting_coverage(self) -> float:
        return (
            (
                self.translated_occurrences
                + self.allowed_preserve_occurrences
                + self.unresolved_occurrences
            )
            / self.source_occurrences
            if self.source_occurrences
            else 1.0
        )

    @property
    def translation_completion_rate(self) -> float:
        return (
            (self.translated_occurrences + self.allowed_preserve_occurrences)
            / self.source_occurrences
            if self.source_occurrences
            else 1.0
        )

    @property
    def delivery_status(self) -> str:
        integrity_failures = (
            self.unapproved_preserve_failures
            + self.hangul_leak_failures
            + self.han_leak_failures
            + self.technical_invariant_failures
            + self.unchanged_prose_failures
            + self.repeated_token_corruption_failures
            + self.repeated_char_corruption_failures
            + self.length_explosion_failures
            + self.unexpected_script_failures
        )
        if (
            self.unassigned_source_spans
            or self.duplicate_source_span_assignments
            or self.unknown_occurrences
            or self.ledger_assigned_source_spans != self.eligible_source_spans
            or self.final_output_script_leaks
            or (integrity_failures and not self.unresolved_occurrences)
        ):
            return "FAIL"
        if self.unresolved_occurrences:
            return "PARTIAL"
        return "SUCCESS"

    def assert_reconciled(self) -> None:
        if self.delivery_status == "FAIL":
            raise RuntimeError("Patch B source-span or occurrence accounting failed")
        accounted = (
            self.translated_occurrences
            + self.allowed_preserve_occurrences
            + self.unresolved_occurrences
        )
        if accounted != self.source_occurrences:
            raise RuntimeError("Patch B occurrence statuses do not reconcile")


class TranslationIntegrityError(ValueError):
    """A provider/cache result failed one or more deterministic Patch B guards."""

    def __init__(self, failure_codes: Iterable[str]) -> None:
        self.failure_codes = tuple(dict.fromkeys(str(code) for code in failure_codes))
        super().__init__("translation integrity failed: " + ", ".join(self.failure_codes))


def stable_source_span_id(page: int, index: int, source_text: str) -> str:
    digest = hashlib.sha256(
        f"page={page}\0index={index}\0{source_text}".encode()
    ).hexdigest()[:20]
    return f"span-{digest}"


def stable_occurrence_id(page: int, index: int, source_text: str) -> str:
    return "occ-" + stable_source_span_id(page, index, source_text)[5:]


def stable_logical_unit_id(page: int, index: int, source_text: str) -> str:
    return "unit-" + stable_source_span_id(page, index, source_text)[5:]


def _select_non_overlapping(
    candidates: Iterable[tuple[int, int, str, str]],
) -> tuple[AllowedPreserveSpan, ...]:
    ordered = sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]), item[3]))
    result: list[AllowedPreserveSpan] = []
    cursor = -1
    for start, end, text, reason in ordered:
        if not text or start < cursor or reason not in APPROVED_PRESERVE_REASONS:
            continue
        result.append(AllowedPreserveSpan(text, reason, True))
        cursor = end
    return tuple(result)


def approved_spans_for_translation(
    source: str,
    terminology: Mapping[str, str] | None = None,
) -> tuple[AllowedPreserveSpan, ...]:
    """Reuse existing occurrence-specific preservation provenance."""
    candidates: list[tuple[int, int, str, str]] = []
    candidates.extend(
        (span.start, span.end, span.name, "reviewed_proper_name")
        for span in protected_verified_proper_names(source, terminology)
    )
    candidates.extend(
        (span.start, span.end, span.literal, "literal_path")
        for span in find_windows_path_literals(source)
    )
    candidates.extend(
        (span.start, span.end, span.literal, "immutable_metadata")
        for span in find_document_label_literals(source)
    )
    candidates.extend(
        (span.start, span.end, span.literal, "explicit_ui_rule")
        for span in protected_korean_ui_labels(source, terminology)
    )
    for term, target in (terminology or {}).items():
        if not term or target != term:
            continue
        for match in re.finditer(re.escape(term), source):
            candidates.append(
                (match.start(), match.end(), match.group(0), "explicit_technical_term")
            )
    return _select_non_overlapping(candidates)


def _remove_approved_spans(text: str, spans: Sequence[AllowedPreserveSpan]) -> str:
    result = text
    for span in spans:
        if not span.preserve_reason_is_approved or span.reason not in APPROVED_PRESERVE_REASONS:
            continue
        result = result.replace(span.text, "", 1)
    return result


def _visible(text: str) -> str:
    return _FORMULA_TAG.sub("", _STYLE_TAG.sub("", text))


def _normalised(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", _visible(text)).casefold().split())


def _is_natural_language_prose(text: str) -> bool:
    visible = _visible(text)
    alphabetic_runs = re.findall(r"[A-Za-zÀ-ỹ]{2,}", visible)
    source_letters = sum(1 for character in visible if character.isalpha())
    return len(alphabetic_runs) >= 2 or source_letters >= 4


def _max_short_token_runs(text: str) -> Counter[str]:
    result: Counter[str] = Counter()
    for match in _SHORT_TOKEN.finditer(text):
        token = match.group(1).casefold()
        count = len(
            re.findall(
                rf"(?<!\w){re.escape(token)}(?!\w)",
                match.group(0),
                re.IGNORECASE,
            )
        )
        result[token] = max(result[token], count)
    return result


def _max_repeated_char_runs(text: str) -> Counter[str]:
    result: Counter[str] = Counter()
    for match in _REPEATED_CHAR.finditer(text):
        result[match.group(1)] = max(result[match.group(1)], len(match.group(0)))
    return result


def _effective_source_kind(source: str, source_language: str | None) -> str | None:
    language = (source_language or "").lower()
    if language.startswith("ko"):
        return "ko"
    if language.startswith("zh"):
        return "zh"
    if language in {"", "auto"}:
        if contains_hangul(source):
            return "ko"
        if contains_han(source):
            return "zh"
    return None


def validate_translation_integrity(
    source: str,
    target: str,
    *,
    source_language: str | None,
    target_language: str | None,
    translation_required: bool,
    allowed_preserve_spans: Sequence[AllowedPreserveSpan] = (),
) -> None:
    """Reject only deterministic leakage, identity, and obvious corruption."""
    if (target_language or "").lower() != "vi":
        return
    failures: list[str] = []
    approved = tuple(
        span
        for span in allowed_preserve_spans
        if span.preserve_reason_is_approved and span.reason in APPROVED_PRESERVE_REASONS
    )
    target_without_preserves = _remove_approved_spans(target, approved)
    source_without_preserves = _remove_approved_spans(source, approved)
    source_kind = _effective_source_kind(source, source_language)
    if source_kind == "ko" and contains_hangul(target_without_preserves):
        failures.append(IntegrityFailure.HANGUL_LEAK.value)
    if source_kind == "zh" and contains_han(target_without_preserves):
        failures.append(IntegrityFailure.HAN_LEAK.value)

    if (
        translation_required
        and _is_natural_language_prose(source_without_preserves)
        and _normalised(source) == _normalised(target)
    ):
        failures.append(IntegrityFailure.UNCHANGED_PROSE.value)

    source_token_runs = _max_short_token_runs(source_without_preserves)
    target_token_runs = _max_short_token_runs(target_without_preserves)
    if any(count > source_token_runs[token] for token, count in target_token_runs.items()):
        failures.append(IntegrityFailure.REPEATED_TOKEN.value)

    source_char_runs = _max_repeated_char_runs(source_without_preserves)
    target_char_runs = _max_repeated_char_runs(target_without_preserves)
    if any(count > source_char_runs[character] for character, count in target_char_runs.items()):
        failures.append(IntegrityFailure.REPEATED_CHAR.value)

    visible_source = _visible(source)
    visible_target = _visible(target)
    if len(visible_source) > 20 and len(visible_target) > 3 * len(visible_source):
        failures.append(IntegrityFailure.LENGTH_EXPLOSION.value)

    for ranges in _UNRELATED_SCRIPT_RANGES.values():
        if any(_in_ranges(character, ranges) for character in target_without_preserves) and not any(
            _in_ranges(character, ranges) for character in source_without_preserves
        ):
            failures.append(IntegrityFailure.UNEXPECTED_SCRIPT.value)
            break
    if failures:
        raise TranslationIntegrityError(failures)


class OccurrenceLedger:
    """Run-wide exact-once inventory and final occurrence accounting."""

    def __init__(self) -> None:
        self._spans: dict[str, EligibleSourceSpan] = {}
        self._occurrences: dict[str, Occurrence] = {}
        self._assignments: Counter[str] = Counter()

    @property
    def occurrences(self) -> tuple[Occurrence, ...]:
        return tuple(self._occurrences.values())

    def add_eligible_span(self, span: EligibleSourceSpan) -> None:
        if not span.source_text.strip():
            return
        if span.source_span_id in self._spans:
            raise RuntimeError(f"duplicate eligible source_span_id {span.source_span_id}")
        self._spans[span.source_span_id] = span

    def record_occurrence(self, occurrence: Occurrence) -> Occurrence:
        if occurrence.occurrence_id in self._occurrences:
            raise RuntimeError(f"duplicate occurrence_id {occurrence.occurrence_id}")
        unknown_spans = set(occurrence.source_span_ids) - set(self._spans)
        if unknown_spans:
            raise RuntimeError("occurrence refers to a source span outside the inventory")
        if occurrence.status == OccurrenceStatus.ALLOWED_PRESERVE and (
            not occurrence.preserve_reason_is_approved
            or occurrence.preserve_reason not in APPROVED_PRESERVE_REASONS
        ):
            occurrence = replace(
                occurrence,
                status=OccurrenceStatus.UNRESOLVED,
                target_text=occurrence.source_text,
                unresolved_reason=IntegrityFailure.UNAPPROVED_PRESERVE.value,
                validation_failures=tuple(
                    dict.fromkeys(
                        occurrence.validation_failures
                        + (IntegrityFailure.UNAPPROVED_PRESERVE.value,)
                    )
                ),
                preserve_reason_is_approved=False,
            )
        self._occurrences[occurrence.occurrence_id] = occurrence
        self._assignments.update(occurrence.source_span_ids)
        return occurrence

    def mark_unresolved(
        self,
        occurrence_id: str,
        reason: str,
        failure_codes: Iterable[str] = (),
    ) -> None:
        occurrence = self._occurrences[occurrence_id]
        failures = tuple(
            dict.fromkeys(occurrence.validation_failures + tuple(failure_codes))
        )
        self._occurrences[occurrence_id] = replace(
            occurrence,
            status=OccurrenceStatus.UNRESOLVED,
            target_text=occurrence.source_text,
            unresolved_reason=reason,
            validation_failures=failures,
        )

    def reconcile(
        self,
        *,
        cache_validation_failures: int = 0,
        final_output_script_leaks: int = 0,
    ) -> IntegrityMetrics:
        statuses = Counter(occurrence.status for occurrence in self._occurrences.values())
        assigned = {identifier for identifier, count in self._assignments.items() if count > 0}
        values: dict[str, int] = {
            "eligible_source_spans": len(self._spans),
            "ledger_assigned_source_spans": len(assigned),
            "unassigned_source_spans": len(set(self._spans) - assigned),
            "duplicate_source_span_assignments": sum(
                max(0, count - 1) for count in self._assignments.values()
            ),
            "source_occurrences": len(self._occurrences),
            "translated_occurrences": statuses[OccurrenceStatus.TRANSLATED],
            "allowed_preserve_occurrences": statuses[OccurrenceStatus.ALLOWED_PRESERVE],
            "unresolved_occurrences": statuses[OccurrenceStatus.UNRESOLVED],
            "unknown_occurrences": sum(
                count
                for status, count in statuses.items()
                if status not in set(OccurrenceStatus)
            ),
            "cache_validation_failures": cache_validation_failures,
            "final_output_script_leaks": final_output_script_leaks,
        }
        failure_occurrences: dict[str, set[str]] = defaultdict(set)
        for occurrence in self._occurrences.values():
            for failure in occurrence.validation_failures:
                failure_occurrences[failure].add(occurrence.occurrence_id)
        for failure, field_name in _COUNTER_BY_FAILURE.items():
            values[field_name] = len(failure_occurrences[failure])
        return IntegrityMetrics(**values)


@dataclass(frozen=True)
class FinalTextLayerAudit:
    final_output_script_leaks: int
    pages_with_leaks: tuple[int, ...] = field(default_factory=tuple)


def _script_counter(text: str, kind: str) -> Counter[str]:
    ranges = _HANGUL_RANGES if kind == "ko" else _HAN_RANGES
    return Counter(character for character in text if _in_ranges(character, ranges))


def audit_final_text_layer(
    page_texts: Mapping[int, str],
    occurrences: Sequence[Occurrence],
    *,
    source_language: str | None,
    target_language: str | None,
) -> FinalTextLayerAudit:
    """Find source-script glyphs beyond explicit preserve/fallback provenance."""
    if (target_language or "").lower() != "vi":
        return FinalTextLayerAudit(0)
    by_page: dict[int, list[Occurrence]] = defaultdict(list)
    for occurrence in occurrences:
        by_page[occurrence.page].append(occurrence)
    total = 0
    failed_pages: list[int] = []
    for page, text in page_texts.items():
        page_leaks = 0
        page_occurrences = by_page.get(page, [])
        source = "\n".join(item.source_text for item in page_occurrences)
        language = (source_language or "").lower()
        kinds: list[str] = []
        if language.startswith("ko") or (language in {"", "auto"} and contains_hangul(source)):
            kinds.append("ko")
        if language.startswith("zh") or (language in {"", "auto"} and contains_han(source)):
            kinds.append("zh")
        for kind in kinds:
            permitted = Counter()
            for occurrence in page_occurrences:
                if occurrence.status in {
                    OccurrenceStatus.ALLOWED_PRESERVE,
                    OccurrenceStatus.UNRESOLVED,
                }:
                    permitted.update(_script_counter(occurrence.source_text, kind))
                elif occurrence.status == OccurrenceStatus.TRANSLATED:
                    for span in occurrence.allowed_preserve_spans:
                        if (
                            span.preserve_reason_is_approved
                            and span.reason in APPROVED_PRESERVE_REASONS
                        ):
                            permitted.update(_script_counter(span.text, kind))
            actual = _script_counter(text, kind)
            page_leaks += sum((actual - permitted).values())
        if page_leaks:
            total += page_leaks
            failed_pages.append(page)
    return FinalTextLayerAudit(total, tuple(failed_pages))
