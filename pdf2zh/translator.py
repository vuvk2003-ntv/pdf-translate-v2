"""Translation adapters for the preservation-focused PDF core."""

from __future__ import annotations

import html
import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
from collections import Counter
from typing import Any, ClassVar

import requests

from pdf2zh.cache import TranslationCache
from pdf2zh.invariants import TechnicalInvariantError, validate_technical_invariants

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"</?b\d+>")
INTERNAL_PLACEHOLDER_PATTERN = re.compile(r"\{\s*v([\d\s]+)\}", re.IGNORECASE)
PAIRED_PLACEHOLDER_PATTERN = re.compile(r"<b(\d+)></b\1>")
STYLE_TAG_PATTERN = re.compile(r"<(/?)s([123])>", re.IGNORECASE)
SAFE_CACHE_PLACEHOLDER_PATTERN = re.compile(r"\{v\d+\}|</?[bs]\d+>", re.IGNORECASE)


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
        **_: Any,
    ) -> None:
        self.lang_in = self.lang_map.get(lang_in.lower(), lang_in)
        self.lang_out = self.lang_map.get(lang_out.lower(), lang_out)
        self.model = model
        self.ignore_cache = ignore_cache
        self.cache = TranslationCache(
            self.name,
            {
                "lang_in": self.lang_in,
                "lang_out": self.lang_out,
                "model": model,
            },
        )
        self.cache_hits = 0
        self.translation_requests = 0
        self.translation_seconds = 0.0
        self._metrics_lock = threading.Lock()

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        """Translate text, consulting the persistent cache unless bypassed."""
        text = normalise_number_abbreviation(text)
        use_cache = not (self.ignore_cache or ignore_cache) and is_safe_cache_key(text)
        if use_cache:
            cached = self.cache.get(text)
            if cached is not None:
                try:
                    validate_translation_result(text, cached)
                except (FormulaPlaceholderError, TechnicalInvariantError):
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
            translated = self.do_translate(text)
        finally:
            elapsed = time.perf_counter() - started
            with self._metrics_lock:
                self.translation_seconds += elapsed
        validate_translation_result(text, translated)
        if use_cache:
            self.cache.set(text, translated)
        return translated

    def do_translate(self, text: str) -> str:
        """Translate one engine-sized text segment."""
        raise NotImplementedError

    def has_translation_for(self, text: str) -> bool:
        """Whether this engine resolved the segment instead of passing it through."""
        return True

    def translate_with_identity(self, text: str, identity: str) -> str:
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


def validate_translation_result(source: str, translated: str) -> None:
    """Validate every deterministic guard before a result can enter the cache."""
    if placeholders(source) != placeholders(translated):
        raise FormulaPlaceholderError("formula placeholders changed during translation")
    validate_style_tags(source, translated)
    validate_technical_invariants(source, translated)


def segment_identifier(text: str) -> str:
    """Return a stable non-reversible label suitable for normal logs and JSONL."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_segment_tables(
    path: str | None,
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
                validate_technical_invariants(source, translation)
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
            **kwargs,
        )
        envs = envs or {}
        self.table, self.table_by_id = load_segment_tables(envs.get("segments_in"))
        self.misses_path = envs.get("segments_out")
        self._seen: set[str] = set()
        self._resolved: set[str] = set()
        self.table_hits = 0
        self.handoff_misses = 0
        self._lock = threading.Lock()
        if self.misses_path:
            open(self.misses_path, "w", encoding="utf-8").close()

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        return self._translate(text, None, ignore_cache)

    def translate_with_identity(self, text: str, identity: str) -> str:
        return self._translate(text, identity, False)

    def _translate(
        self, text: str, identity: str | None, ignore_cache: bool
    ) -> str:
        """Resolve table/cache hits and record misses without caching passthroughs."""
        text = normalise_number_abbreviation(text)
        use_cache = not (self.ignore_cache or ignore_cache) and is_safe_cache_key(text)
        translation = None
        if identity is not None and identity in self.table_by_id:
            expected_source, candidate = self.table_by_id[identity]
            if expected_source == text:
                translation = candidate
        if translation is None:
            translation = self.table.get(text)
        if translation is not None:
            validate_translation_result(text, translation)
            if use_cache:
                self.cache.set(text, translation)
            with self._metrics_lock:
                self.table_hits += 1
            with self._lock:
                self._resolved.add(identity or text)
            return translation
        if use_cache:
            cached = self.cache.get(text)
            if cached is not None:
                try:
                    validate_translation_result(text, cached)
                except (FormulaPlaceholderError, TechnicalInvariantError):
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
        self._record_miss(text, identity)
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

    def _record_miss(self, text: str, identity: str | None = None) -> None:
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
                stream.write(
                    json.dumps(
                        {
                            "type": "untrusted_source_content",
                            "segment_id": miss_identity,
                            "src": text,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )


ENGINES: dict[str, type[BaseTranslator]] = {
    engine.name: engine for engine in (GoogleTranslator, HandoffTranslator)
}
