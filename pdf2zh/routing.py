"""Deterministic provider routing for Patch A logical translation units.

The router is deliberately local and explainable.  It consumes reconstruction
metadata but never reconstructs text, calls a provider, reads a cache, or scores
units with weights.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from pdf2zh.integrity import approved_spans_for_translation


class SelectedRoute(str, Enum):
    PRESERVE = "preserve"
    GOOGLE = "google"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class RoutingMetadata:
    """Read-only facts already known at the Patch A/provider boundary."""

    preserve_eligible: bool = False
    was_fragment_reconstructed: bool = False
    reconstruction_complexity: str | None = None
    is_callout: bool = False
    is_dense_table_text: bool = False
    logical_unit_id: str | None = None
    occurrence_id: str | None = None
    source_fragment_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingDecision:
    requested_engine: str
    selected_route: SelectedRoute
    routing_reason: str
    routing_flags: Mapping[str, bool | int | str]
    routing_seconds: float

    def debug_record(self, *, actual_provider: str | None = None) -> dict[str, Any]:
        return {
            "requested_engine": self.requested_engine,
            "selected_route": self.selected_route.value,
            "actual_provider": actual_provider,
            "routing_reason": self.routing_reason,
            "routing_flags": dict(self.routing_flags),
        }


_STYLE_TAG = re.compile(r"</?s[123]>", re.IGNORECASE)
_HANGUL = re.compile(r"[\uac00-\ud7a3]")
_HAN = re.compile(r"[\u3400-\u9fff]")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
_UI_LITERAL = re.compile(
    r"(?<![A-Za-z0-9_])(?:Start|Stop|Reset|Run|Open|Close|OK|Cancel|"
    r"ON|OFF|Enable|Disable|Enter|Exit)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(?:not|never|must\s+not|do\s+not|prohibit(?:ed|ion)?)\b|"
    r"(?:금지|않|말아야|不得|禁止|不要)",
    re.IGNORECASE,
)
_MODALITY = re.compile(
    r"\b(?:must|required|necessary|recommended|possible|permitted|shall)\b|"
    r"(?:필수|필요|권장|가능|해야|必须|需要|建议|允许)",
    re.IGNORECASE,
)
_CONDITION = re.compile(
    r"\b(?:if|when|in\s+case|unless)\b|(?:경우|때|하면|如果|当|若)",
    re.IGNORECASE,
)
_ORDER = re.compile(
    r"\b(?:before|after|then|first|next|finally|prior\s+to)\b|"
    r"(?:전에|후에|다음|먼저|이후|之前|之后|然后|首先)",
    re.IGNORECASE,
)
_OBJECT = re.compile(
    r"\b(?:axis\s+[A-Z0-9]+|port(?:\s+No\.)?\s*\d+|"
    r"(?:D|M|X|Y|R|ZR)\d+|channel\s*\d+|parameter\s+[A-Z0-9._-]+)\b",
    re.IGNORECASE,
)
_VALUE = re.compile(
    r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:\s*(?:mm/s|m/s|rpm|kHz|Hz|"
    r"ms|sec|s|V|A|mA|MPa|kPa|Pa|mm|cm|%|°C))?|\b(?:TCP|UDP|ON|OFF)\b",
    re.IGNORECASE,
)


def _visible(text: str) -> str:
    return " ".join(_STYLE_TAG.sub("", text).split())


def _object_value_pair_count(text: str) -> int:
    """Count conservative object/value associations without semantic parsing."""
    objects = list(_OBJECT.finditer(text))
    values = list(_VALUE.finditer(text))
    if not objects or not values:
        return 0
    count = 0
    for position, current in enumerate(objects):
        end = objects[position + 1].start() if position + 1 < len(objects) else len(text)
        if any(current.end() <= value.start() < end for value in values):
            count += 1
    return count


def routing_features(
    text: str,
    metadata: RoutingMetadata,
    terminology: Mapping[str, str] | None = None,
) -> Mapping[str, bool | int | str]:
    """Return the named local signals retained in debug/report mode."""
    visible = _visible(text)
    approved = approved_spans_for_translation(text, terminology)
    has_cjk = bool(_HANGUL.search(visible) or _HAN.search(visible))
    has_latin = bool(_LATIN_WORD.search(visible))
    protected_count = len(approved)
    result: dict[str, bool | int | str] = {
        "contains_mixed_script": has_cjk and has_latin,
        "contains_protected_technical_spans": protected_count > 0,
        "protected_span_count": protected_count,
        "was_fragment_reconstructed": metadata.was_fragment_reconstructed,
        "reconstruction_complexity": metadata.reconstruction_complexity or "unavailable",
        "is_callout": metadata.is_callout,
        "is_dense_table_text": metadata.is_dense_table_text,
        "contains_ui_literal": bool(_UI_LITERAL.search(visible)),
        "has_negation": bool(_NEGATION.search(visible)),
        "has_modality": bool(_MODALITY.search(visible)),
        "has_condition": bool(_CONDITION.search(visible)),
        "has_order_marker": bool(_ORDER.search(visible)),
        "object_value_pair_count": _object_value_pair_count(visible),
        "source_length": len(visible),
    }
    return MappingProxyType(result)


def route_logical_unit(
    text: str,
    metadata: RoutingMetadata | None = None,
    *,
    terminology: Mapping[str, str] | None = None,
    requested_engine: str = "auto",
) -> RoutingDecision:
    """Choose one route in the required preserve/high-risk/Google order."""
    started = time.perf_counter()
    facts = metadata or RoutingMetadata()
    features = routing_features(text, facts, terminology)
    flags = MappingProxyType(dict(features))

    if requested_engine != "auto":
        route = SelectedRoute(requested_engine)
        return RoutingDecision(
            requested_engine,
            route,
            f"explicit_{requested_engine}",
            flags,
            time.perf_counter() - started,
        )

    if facts.preserve_eligible:
        route, reason = SelectedRoute.PRESERVE, "fully_preserve_eligible"
    else:
        mixed = bool(features["contains_mixed_script"])
        protected_count = int(features["protected_span_count"])
        semantic_dependency = any(
            bool(features[name])
            for name in ("has_negation", "has_modality", "has_condition", "has_order_marker")
        )
        association_count = int(features["object_value_pair_count"])
        structural = features["reconstruction_complexity"] == "structural_merge"
        callout_risk = bool(features["is_callout"]) and (
            mixed or protected_count >= 2 or semantic_dependency or association_count >= 2
        )
        if mixed and (
            protected_count > 0
            or bool(features["contains_ui_literal"])
            or structural
        ):
            route, reason = SelectedRoute.HANDOFF, "mixed_language_context"
        elif structural:
            route, reason = SelectedRoute.HANDOFF, "structural_merge"
        elif association_count >= 2:
            route, reason = SelectedRoute.HANDOFF, "dense_object_value_associations"
        elif callout_risk:
            route, reason = SelectedRoute.HANDOFF, "context_sensitive_callout"
        elif semantic_dependency and (
            mixed
            or facts.was_fragment_reconstructed
            or protected_count >= 2
            or association_count >= 2
        ):
            route, reason = SelectedRoute.HANDOFF, "semantic_dependency_plus_complexity"
        elif len(_visible(text)) >= 600 and (mixed or protected_count >= 2):
            route, reason = SelectedRoute.HANDOFF, "long_mixed_technical_unit"
        else:
            route, reason = SelectedRoute.GOOGLE, "simple_low_risk"

    return RoutingDecision(
        requested_engine,
        route,
        reason,
        flags,
        time.perf_counter() - started,
    )
