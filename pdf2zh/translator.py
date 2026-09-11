"""Translation adapters for the preservation-focused PDF core."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import threading
import time
import unicodedata
from collections import Counter
from typing import Any, ClassVar, NamedTuple

import requests

from pdf2zh.cache import TranslationCache
from pdf2zh.invariants import (
    TechnicalInvariantError,
    VerifiedProperNameError,
    contains_hangul,
    find_document_label_literals,
    find_windows_path_literals,
    is_standalone_verified_proper_name,
    protected_korean_ui_labels,
    protected_verified_proper_names,
    validate_korean_english_spans,
    validate_lightweight_semantics,
    validate_technical_invariants,
    validate_verified_proper_names,
)
from pdf2zh.terminology import (
    TerminologyConsistencyError,
    terminology_fingerprint,
    validate_confirmed_terminology,
)

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"</?b\d+>")
INTERNAL_PLACEHOLDER_PATTERN = re.compile(r"\{\s*v([\d\s]+)\}", re.IGNORECASE)
PAIRED_PLACEHOLDER_PATTERN = re.compile(r"<b(\d+)></b\1>")
STYLE_TAG_PATTERN = re.compile(r"<(/?)s([123])>", re.IGNORECASE)
SAFE_CACHE_PLACEHOLDER_PATTERN = re.compile(r"\{v\d+\}|</?[bs]\d+>", re.IGNORECASE)


class ProperNameMask(NamedTuple):
    identifier: int
    source: str


class ProviderLiteralMask(NamedTuple):
    identifier: int
    source: str
    kind: str


def mask_provider_literals(
    text: str,
    terminology: dict[str, str] | None = None,
) -> tuple[str, tuple[ProviderLiteralMask, ...]]:
    """Hide exact names, paths, and syntactic UI labels from a provider."""
    candidates = [
        (span.start, span.end, span.name, "verified proper name")
        for span in protected_verified_proper_names(text, terminology)
    ]
    candidates.extend(
        (span.start, span.end, span.literal, span.kind)
        for span in find_windows_path_literals(text)
    )
    candidates.extend(
        (span.start, span.end, span.literal, span.kind)
        for span in find_document_label_literals(text)
    )
    candidates.extend(
        (span.start, span.end, span.literal, span.kind)
        for span in protected_korean_ui_labels(text, terminology)
    )
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, str, str]] = []
    cursor = -1
    for candidate in candidates:
        if candidate[0] < cursor:
            continue
        selected.append(candidate)
        cursor = candidate[1]
    if not selected:
        return text, ()

    used_ids = [int(identifier) for identifier in re.findall(r"</?b(\d+)>", text)]
    next_identifier = max(used_ids, default=-1) + 1
    pieces: list[str] = []
    masks: list[ProviderLiteralMask] = []
    cursor = 0
    for offset, (start, end, source, kind) in enumerate(selected):
        identifier = next_identifier + offset
        pieces.append(text[cursor:start])
        pieces.append(f"<b{identifier}></b{identifier}>")
        masks.append(ProviderLiteralMask(identifier, source, kind))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), tuple(masks)


def restore_provider_literals(
    translated: str,
    masks: tuple[ProviderLiteralMask, ...],
) -> str:
    """Validate provider placeholders and restore every exact source span."""
    if not masks:
        return translated
    identifiers = "|".join(str(mask.identifier) for mask in masks)
    tag_pattern = re.compile(rf"</?b(?:{identifiers})>")
    expected_tags = [
        tag
        for mask in masks
        for tag in (f"<b{mask.identifier}>", f"</b{mask.identifier}>")
    ]
    error_type = (
        VerifiedProperNameError
        if all(mask.kind == "verified proper name" for mask in masks)
        else FormulaPlaceholderError
    )
    if tag_pattern.findall(translated) != expected_tags:
        raise error_type(
            "protected provider placeholders were dropped, duplicated, or reordered"
        )
    restored = translated
    for mask in masks:
        token = f"<b{mask.identifier}></b{mask.identifier}>"
        if restored.count(token) != 1:
            raise error_type(
                f"protected {mask.kind} placeholder pair is malformed"
            )
        restored = restored.replace(token, mask.source, 1)
    if tag_pattern.search(restored):
        raise error_type(
            "protected provider placeholder leaked into translated text"
        )
    return restored


def mask_verified_proper_names(
    text: str,
    terminology: dict[str, str] | None = None,
) -> tuple[str, tuple[ProperNameMask, ...]]:
    """Hide reviewed names behind fresh formula-safe tag pairs for a provider."""
    spans = protected_verified_proper_names(text, terminology)
    if not spans:
        return text, ()
    used_ids = [int(identifier) for identifier in re.findall(r"</?b(\d+)>", text)]
    next_identifier = max(used_ids, default=-1) + 1
    pieces: list[str] = []
    masks: list[ProperNameMask] = []
    cursor = 0
    for offset, span in enumerate(spans):
        identifier = next_identifier + offset
        pieces.append(text[cursor : span.start])
        pieces.append(f"<b{identifier}></b{identifier}>")
        masks.append(ProperNameMask(identifier, text[span.start : span.end]))
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces), tuple(masks)


def restore_verified_proper_names(
    translated: str,
    masks: tuple[ProperNameMask, ...],
) -> str:
    """Validate provider-safe name tokens and restore exact reviewed spelling."""
    if not masks:
        return translated
    identifiers = "|".join(str(mask.identifier) for mask in masks)
    tag_pattern = re.compile(rf"</?b(?:{identifiers})>")
    expected_tags = [
        tag
        for mask in masks
        for tag in (f"<b{mask.identifier}>", f"</b{mask.identifier}>")
    ]
    if tag_pattern.findall(translated) != expected_tags:
        raise VerifiedProperNameError(
            "verified proper-name provider placeholders were dropped, duplicated, or reordered"
        )
    restored = translated
    for mask in masks:
        token = f"<b{mask.identifier}></b{mask.identifier}>"
        if restored.count(token) != 1:
            raise VerifiedProperNameError(
                "verified proper-name provider placeholder pair is malformed"
            )
        restored = restored.replace(token, mask.source, 1)
    if tag_pattern.search(restored):
        raise VerifiedProperNameError(
            "verified proper-name provider placeholder leaked into translated text"
        )
    return restored


class FormulaPlaceholderError(ValueError):
    """Raised when a translator damages or reorders protected formula tags."""


class SegmentTooLongError(ValueError):
    """Raised when a segment exceeds what the translation service accepts.

    Upstream truncates to the limit and returns the short answer as if it were
    the whole translation, so the tail of a long paragraph disappears with
    nothing said. A segment carrying formula or style markers is caught later
    by the marker check, but plain prose is silently cut in half. Refusing the
    segment keeps the source text and lets the caller say what happened.
    """


def remove_control_characters(value: str) -> str:
    """Remove control characters that cannot be emitted safely into PDF text."""
    return "".join(character for character in value if unicodedata.category(character)[0] != "C")


NUMBER_ABBREVIATION_PATTERN = re.compile(r"(?<![A-Za-z])no\.(?=\s*\d)")


def normalise_number_abbreviation(text: str) -> str:
    """Capitalise the ``no.`` that means "number" so it is not read as "not".

    "ref. no. 305" came back as "ref. KHONG. 305": lowercase "no." mid-sentence
    reads as the negation, and every engine we can reach makes the same choice.
    The same string capitalised is unambiguous -- "No. 305" translates to
    "So 305" -- and capitalising an abbreviation that already stands for a
    proper noun changes nothing else about the sentence.

    Only ``no.`` directly in front of a number is touched, so ordinary prose
    ("there is no. Then...") is left alone.
    """
    return NUMBER_ABBREVIATION_PATTERN.sub("No.", text)


def is_safe_cache_key(text: str) -> bool:
    """Avoid reusing ambiguous short labels as though they had no context."""
    visible = SAFE_CACHE_PLACEHOLDER_PATTERN.sub("", text).strip()
    words = re.findall(r"[A-Za-z\u3400-\u9fff\uac00-\ud7a3]+", visible)
    return len(visible) >= 24 or len(words) >= 4 or bool(
        len(visible) >= 12 and re.search(r"[.!?:。！？]\s*$", visible)
    )


class BaseTranslator:
    """Cache-aware translator interface consumed by the PDF converter."""

    name = "base"
    lang_map: ClassVar[dict[str, str]] = {}

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        *,
        ignore_cache: bool = False,
        envs: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        self.lang_in = self.lang_map.get(lang_in.lower(), lang_in)
        self.lang_out = self.lang_map.get(lang_out.lower(), lang_out)
        self.model = model
        self.ignore_cache = ignore_cache
        self.terminology = dict((envs or {}).get("terminology") or {})
        self.cache = TranslationCache(
            self.name,
            {
                "lang_in": self.lang_in,
                "lang_out": self.lang_out,
                "model": model,
                "rules_terminology": terminology_fingerprint(self.terminology),
            },
        )
        self.cache_hits = 0
        self.translation_requests = 0
        self.translation_seconds = 0.0
        self._metrics_lock = threading.Lock()

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        """Translate text, consulting the persistent cache unless bypassed."""
        text = normalise_number_abbreviation(text)
        if is_standalone_verified_proper_name(text, self.terminology):
            return text
        use_cache = not (self.ignore_cache or ignore_cache) and is_safe_cache_key(text)
        if use_cache:
            cached = self.cache.get(text)
            if cached is not None:
                try:
                    self.validate(text, cached)
                except (
                    FormulaPlaceholderError,
                    TechnicalInvariantError,
                    TerminologyConsistencyError,
                    VerifiedProperNameError,
                ):
                    logger.warning(
                        "Ignoring unsafe cached translation for segment %s",
                        segment_identifier(text),
                    )
                else:
                    with self._metrics_lock:
                        self.cache_hits += 1
                    return cached
        started = time.perf_counter()
        with self._metrics_lock:
            self.translation_requests += 1
        try:
            provider_text, literal_masks = mask_provider_literals(text, self.terminology)
            translated = self.do_translate(provider_text)
            translated = restore_provider_literals(translated, literal_masks)
        finally:
            elapsed = time.perf_counter() - started
            with self._metrics_lock:
                self.translation_seconds += elapsed
        self.validate(text, translated)
        if use_cache:
            self.cache.set(text, translated)
        return translated

    def validate(self, source: str, translated: str) -> None:
        validate_translation_result(
            source,
            translated,
            target_language=self.lang_out,
            terminology=self.terminology,
        )
        validate_confirmed_terminology(source, translated, self.terminology)

    def do_translate(self, text: str) -> str:
        """Translate one engine-sized text segment."""
        raise NotImplementedError

    def has_translation_for(self, text: str) -> bool:
        """Whether this engine resolved the segment instead of passing it through."""
        return True

    def translate_with_identity(
        self,
        text: str,
        identity: str,
        *,
        context: dict[str, str] | None = None,
    ) -> str:
        """Translate one occurrence; ordinary engines do not need its identity."""
        return self.translate(text)

    def has_translation_for_identity(self, text: str, identity: str) -> bool:
        return self.has_translation_for(text)

    def metrics(self) -> dict[str, int | float]:
        """Return request/cache timing without exposing raw document text."""
        with self._metrics_lock:
            return {
                "cache_hits": self.cache_hits,
                "translation_requests": self.translation_requests,
                "translation_seconds": self.translation_seconds,
            }

    def get_rich_text_left_placeholder(self, identifier: int) -> str:
        return f"<b{identifier}>"

    def get_rich_text_right_placeholder(self, identifier: int) -> str:
        return f"</b{identifier}>"

    def get_formular_placeholder(self, identifier: int) -> str:
        return self.get_rich_text_left_placeholder(identifier) + self.get_rich_text_right_placeholder(identifier)


class GoogleTranslator(BaseTranslator):
    """Translate through Google's mobile web endpoint without an API key."""

    name = "google"
    lang_map: ClassVar[dict[str, str]] = {"zh": "zh-CN"}

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        *,
        ignore_cache: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            lang_in,
            lang_out,
            model,
            ignore_cache=ignore_cache,
            **kwargs,
        )
        self.session = requests.Session()
        self.endpoint = "https://translate.google.com/m"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            )
        }

    # The /m endpoint carries the text in the query string and rejects more
    # than this; it is the service's limit, not a preference.
    MAXIMUM_SEGMENT_CHARACTERS = 5000

    def do_translate(self, text: str) -> str:
        if len(text) > self.MAXIMUM_SEGMENT_CHARACTERS:
            raise SegmentTooLongError(
                f"segment of {len(text)} characters exceeds the "
                f"{self.MAXIMUM_SEGMENT_CHARACTERS} the service accepts"
            )
        response = self.session.get(
            self.endpoint,
            params={"tl": self.lang_out, "sl": self.lang_in, "q": text},
            headers=self.headers,
            timeout=30,
        )
        if response.status_code == 400:
            raise RuntimeError("Google Translate rejected the text segment")
        response.raise_for_status()
        match = re.search(
            r'(?s)class="(?:t0|result-container)">(.*?)<',
            response.text,
        )
        if match is None:
            raise RuntimeError("Google Translate response did not contain a translation result")
        return remove_control_characters(html.unescape(match.group(1)))


def placeholders(text: str) -> list[str]:
    """Return the formula placeholder tags in order, e.g. ['<b0>', '</b0>']."""
    return PLACEHOLDER_PATTERN.findall(text)


def encode_formula_placeholders(text: str) -> str:
    """Turn converter-internal ``{vN}`` markers into translator-safe tag pairs."""
    return INTERNAL_PLACEHOLDER_PATTERN.sub(
        lambda match: f"<b{int(match.group(1).replace(' ', ''))}></b{int(match.group(1).replace(' ', ''))}>",
        text,
    )


def restore_formula_placeholders(source: str, translated: str) -> str:
    """Validate translator output and restore its tags to converter markers."""
    encoded_source = encode_formula_placeholders(source)
    if placeholders(encoded_source) != placeholders(translated):
        raise FormulaPlaceholderError("formula placeholders changed during translation")
    validate_style_tags(encoded_source, translated)
    restored = PAIRED_PLACEHOLDER_PATTERN.sub(
        lambda match: f"{{v{match.group(1)}}}", translated
    )
    if PLACEHOLDER_PATTERN.search(restored):
        raise FormulaPlaceholderError("formula placeholder pair is malformed")
    return restored


def _style_tag_counts(text: str) -> Counter[str]:
    """Return balanced style-pair counts, allowing complete pairs to reorder."""
    stack: list[str] = []
    pairs: Counter[str] = Counter()
    for match in STYLE_TAG_PATTERN.finditer(text):
        closing, identifier = match.groups()
        if not closing:
            stack.append(identifier)
            continue
        if not stack or stack[-1] != identifier:
            raise FormulaPlaceholderError("style tags are malformed or cross-nested")
        stack.pop()
        pairs[identifier] += 1
    if stack:
        raise FormulaPlaceholderError("style tags are not closed")
    return pairs


def validate_style_tags(source: str, translated: str) -> None:
    """Require the same balanced bold/italic runs after translation."""
    if _style_tag_counts(source) != _style_tag_counts(translated):
        raise FormulaPlaceholderError("style tags changed during translation")


def validate_translation_result(
    source: str,
    translated: str,
    *,
    target_language: str | None = None,
    terminology: dict[str, str] | None = None,
) -> None:
    """Validate every deterministic guard before a result can enter the cache."""
    if placeholders(source) != placeholders(translated):
        raise FormulaPlaceholderError("formula placeholders changed during translation")
    validate_style_tags(source, translated)
    validate_verified_proper_names(source, translated, terminology)
    validate_technical_invariants(source, translated)
    validate_lightweight_semantics(source, translated, target_language)
    validate_korean_english_spans(source, translated, terminology)


def segment_identifier(text: str) -> str:
    """Return a stable non-reversible label suitable for normal logs and JSONL."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_segment_tables(
    path: str | None,
    *,
    target_language: str | None = None,
    terminology: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Load a source-to-translation table from a JSONL file of {"src", "dst"} records.

    Entries whose translation dropped or reordered a formula placeholder are
    skipped, so the next pass re-emits them instead of silently losing a formula.
    """
    if not path:
        return {}, {}
    table: dict[str, str] = {}
    table_by_id: dict[str, tuple[str, str]] = {}
    with open(path, encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                source, translation = record["src"], record["dst"]
                identity = record.get("segment_id")
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(
                    f"{path} line {number}: expected a JSON object with 'src' and 'dst'"
                ) from error
            if not isinstance(source, str) or not isinstance(translation, str):
                raise ValueError(f"{path} line {number}: 'src' and 'dst' must be strings")
            if not translation:
                continue
            # Old converter versions emitted {vN}; normalise those records so
            # existing handoff files remain usable with the documented tags.
            source = encode_formula_placeholders(source)
            translation = encode_formula_placeholders(translation)
            if placeholders(source) != placeholders(translation):
                logger.warning(
                    "%s line %d: formula placeholders differ between src and dst; "
                    "segment left untranslated",
                    path,
                    number,
                )
                continue
            try:
                validate_style_tags(source, translation)
            except FormulaPlaceholderError:
                logger.warning(
                    "%s line %d: style tags differ between src and dst; "
                    "segment left untranslated",
                    path,
                    number,
                )
                continue
            try:
                validate_translation_result(
                    source,
                    translation,
                    target_language=target_language,
                    terminology=terminology,
                )
            except VerifiedProperNameError as error:
                logger.warning(
                    "%s line %d: verified proper-name integrity changed for segment %s (%s); "
                    "segment left untranslated",
                    path,
                    number,
                    segment_identifier(source),
                    error,
                )
                continue
            except TechnicalInvariantError as error:
                logger.warning(
                    "%s line %d: technical invariants changed for segment %s (%s); "
                    "segment left untranslated",
                    path,
                    number,
                    segment_identifier(source),
                    ", ".join(item.category for item in error.differences),
                )
                continue
            table[source] = translation
            # Legacy records can identify shared content, never an occurrence ID.
            if identity is None:
                identity = segment_identifier(source)
            if identity is not None:
                if not isinstance(identity, str):
                    raise ValueError(f"{path} line {number}: 'segment_id' must be a string")
                previous = table_by_id.get(identity)
                if previous is not None and previous != (source, translation):
                    raise ValueError(
                        f"{path} line {number}: segment_id {identity} is duplicated"
                    )
                table_by_id[identity] = (source, translation)
    return table, table_by_id


def load_segment_table(path: str | None) -> dict[str, str]:
    """Backward-compatible source-keyed view of a Handoff JSONL file."""
    return load_segment_tables(path)[0]


class HandoffTranslator(BaseTranslator):
    """Translate from a table produced outside the pipeline, such as by an agent.

    Two passes: the first runs with no table and records every segment it could
    not translate, the caller fills those in, and the second runs with the filled
    table to emit the real document.
    """

    name = "handoff"

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        *,
        ignore_cache: bool = False,
        envs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        # Handoff misses still must never enter the cache. Valid supplied pairs
        # may safely use it, however, which avoids asking the agent to translate
        # repeated boilerplate again on later documents or passes.
        super().__init__(
            lang_in,
            lang_out,
            model,
            ignore_cache=ignore_cache,
            envs=envs,
            **kwargs,
        )
        envs = envs or {}
        self.table, self.table_by_id = load_segment_tables(
            envs.get("segments_in"),
            target_language=self.lang_out,
            terminology=self.terminology,
        )
        self.misses_path = envs.get("segments_out")
        self._seen: set[str] = set()
        self._resolved: set[str] = set()
        self.table_hits = 0
        self.handoff_misses = 0
        self._lock = threading.Lock()
        if self.misses_path:
            open(self.misses_path, "w", encoding="utf-8").close()

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        return self._translate(text, None, ignore_cache, None)

    def translate_with_identity(
        self,
        text: str,
        identity: str,
        *,
        context: dict[str, str] | None = None,
    ) -> str:
        return self._translate(text, identity, False, context)

    def _translate(
        self,
        text: str,
        identity: str | None,
        ignore_cache: bool,
        context: dict[str, str] | None,
    ) -> str:
        """Resolve table/cache hits and record misses without caching passthroughs."""
        text = normalise_number_abbreviation(text)
        if is_standalone_verified_proper_name(text, self.terminology):
            with self._lock:
                self._resolved.add(identity or text)
            return text
        use_cache = not (self.ignore_cache or ignore_cache) and is_safe_cache_key(text)
        if identity is not None:
            # Cache reuse must resolve the same shared identity, never another
            # occurrence or the legacy source-only namespace.
            use_cache = use_cache and identity == segment_identifier(text)
        cache_key = json.dumps([identity, text], ensure_ascii=False) if identity is not None else text
        translation = None
        if identity is not None and identity in self.table_by_id:
            expected_source, candidate = self.table_by_id[identity]
            if expected_source == text:
                translation = candidate
        if identity is None:
            translation = self.table.get(text)
        if translation is not None:
            self.validate(text, translation)
            if use_cache:
                self.cache.set(cache_key, translation)
            with self._metrics_lock:
                self.table_hits += 1
            with self._lock:
                self._resolved.add(identity or text)
            return translation
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                try:
                    self.validate(text, cached)
                except (
                    FormulaPlaceholderError,
                    TechnicalInvariantError,
                    TerminologyConsistencyError,
                    VerifiedProperNameError,
                ):
                    logger.warning(
                        "Ignoring unsafe cached handoff translation for segment %s",
                        segment_identifier(text),
                    )
                else:
                    with self._metrics_lock:
                        self.cache_hits += 1
                    with self._lock:
                        self._resolved.add(identity or text)
                    return cached
        self._record_miss(text, identity, context)
        return text

    def do_translate(self, text: str) -> str:
        """Compatibility hook; Handoff resolution is handled by ``translate``."""
        return self.translate(text)

    def has_translation_for(self, text: str) -> bool:
        text = normalise_number_abbreviation(text)
        with self._lock:
            return text in self._resolved

    def has_translation_for_identity(self, text: str, identity: str) -> bool:
        with self._lock:
            return identity in self._resolved

    def metrics(self) -> dict[str, int | float]:
        result = super().metrics()
        with self._metrics_lock:
            result.update(
                {
                    "handoff_table_hits": self.table_hits,
                    "handoff_misses": self.handoff_misses,
                }
            )
        return result

    def _record_miss(
        self,
        text: str,
        identity: str | None = None,
        context: dict[str, str] | None = None,
    ) -> None:
        """Append one untranslated segment, deduplicated, for the caller to fill in."""
        with self._lock:
            miss_identity = identity or segment_identifier(text)
            if miss_identity in self._seen:
                return
            self._seen.add(miss_identity)
            with self._metrics_lock:
                self.handoff_misses += 1
            if not self.misses_path:
                return
            with open(self.misses_path, "a", encoding="utf-8") as stream:
                record = {
                    "type": "untrusted_source_content",
                    "segment_id": miss_identity,
                    "src": text,
                }
                if (
                    context is not None
                    and contains_hangul(text)
                    and set(context) == {"type", "kind", "text"}
                    and context.get("type") == "untrusted_context"
                    and context.get("kind") == "adjacent_segment"
                    and isinstance(context.get("text"), str)
                    and 0 < len(context["text"]) <= 300
                ):
                    record["context"] = context
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")


ENGINES: dict[str, type[BaseTranslator]] = {
    engine.name: engine for engine in (GoogleTranslator, HandoffTranslator)
}
