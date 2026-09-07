"""Existing document terminology contract shared by Handoff and cache validation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

from pdf2zh.invariants import TRANSLATION_RULES_VERSION

TERMINOLOGY_MATCHER_VERSION = "ascii-token-boundaries-v1"


class TerminologyConsistencyError(ValueError):
    """Raised when a confirmed document term drifts in one translation."""


def load_terminology(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(
        isinstance(source, str) and source and isinstance(target, str) and target
        for source, target in value.items()
    ):
        raise ValueError(f"{path}: terminology must map non-empty strings to strings")
    return value


def terminology_fingerprint(terminology: Mapping[str, str]) -> str:
    scope = [TRANSLATION_RULES_VERSION, dict(terminology)]
    if terminology:
        scope.append(TERMINOLOGY_MATCHER_VERSION)
    payload = json.dumps(scope,
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _contains_term(text: str, term: str) -> bool:
    if re.fullmatch(r"[A-Za-z0-9_]+", term):
        return re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", text.casefold()) is not None
    # Keep existing matching for CJK, multiword, and punctuation-bearing terms.
    return term.casefold() in text.casefold()


def validate_confirmed_terminology(source: str, translated: str, terminology: Mapping[str, str]) -> None:
    missing = [target for term, target in terminology.items()
               if _contains_term(source, term) and not _contains_term(translated, target)]
    if missing:
        raise TerminologyConsistencyError("confirmed terminology missing: " + ", ".join(sorted(set(missing))))
