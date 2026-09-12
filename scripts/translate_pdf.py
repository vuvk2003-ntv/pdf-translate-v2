#!/usr/bin/env python3
"""Translate one text-based PDF while preserving its layout and formulas."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from pdf2zh.high_level import TranslationReport

SKILL_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_CORE = (SKILL_ROOT / "pdf2zh").resolve()
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

CORE_VERSION = "1.9.11"
RULESET = "code4life-preservation-v1"
DEFAULT_TARGET_LANGUAGE = "vi"
CONFIDENTIALITY_MARKERS = ("대외비", "CONFIDENTIAL", "INTERNAL", "RESTRICTED")
logger = logging.getLogger(__name__)

# Latin-script targets the bundled GoNotoKurrent font renders correctly. Scripts
# needing CJK glyphs, right-to-left runs, or complex shaping are refused rather
# than emitted as blank boxes or reordered text.
TARGET_LANGUAGES = frozenset(
    {
        "af", "ca", "cs", "cy", "da", "de", "en", "es", "et", "eu", "fi", "fr",
        "ga", "gl", "hr", "hu", "id", "is", "it", "lt", "lv", "ms", "mt", "nl",
        "no", "pl", "pt", "ro", "sk", "sl", "sq", "sv", "sw", "tl", "tr", "vi",
    }
)

ENGINES = ("google", "handoff", "auto")
EXECUTION_BACKEND = "bundled_pdf2zh"
PAGE_ID_KEY = "PDFTranslatePageID"
PAGE_GEOMETRY_TOLERANCE = 0.01

# Measured on an eight-page sample: 2 threads 48s, 4 threads 30s, 8 threads 27s,
# 12 threads 29s. Past four, the layout pass rather than the network is the floor,
# and more concurrency only raises the odds of the service throttling a long run.
DEFAULT_THREADS = 4
MAX_THREADS = 8


class TranslationError(RuntimeError):
    """Raised when input validation or the translation engine fails."""


class Translation(NamedTuple):
    """Where the translated file landed, and how much of it stayed in the source language."""

    path: Path | None
    untranslated: int = 0
    reasons: Mapping[str, int] = MappingProxyType({})
    image_only_pages: tuple[int, ...] = ()
    total_segments: int = 0
    translated_segments: int = 0
    preserved_segments: int = 0
    unresolved_segments: int = 0
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
    accounting_coverage: float = 1.0
    translation_completion_rate: float = 1.0
    delivery_status: str = "SUCCESS"
    confidentiality_markers: tuple[str, ...] = ()
    unique_translation_units: int = 0
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
    average_chars_per_unit: float = 0.0
    median_chars_per_unit: float = 0.0
    max_chars_per_unit: int = 0
    cache_hits: int = 0
    provider_requests: int = 0
    retry_units: int = 0
    handoff_table_hits: int = 0
    handoff_misses: int = 0
    auto_units: int = 0
    preserve_routed_units: int = 0
    google_routed_units: int = 0
    handoff_direct_units: int = 0
    google_validation_failures: int = 0
    google_to_handoff_escalations: int = 0
    google_to_handoff_escalated_this_run: int = 0
    pending_handoff_queue_after_run: int = 0
    handoff_validation_failures: int = 0
    handoff_unresolved_units: int = 0
    google_cache_hits: int = 0
    handoff_cache_hits: int = 0
    google_provider_requests: int = 0
    handoff_provider_batches_or_requests: int = 0
    google_provider_unavailable_units: int = 0
    handoff_provider_unavailable_units: int = 0
    google_translation_seconds: float = 0.0
    handoff_translation_seconds: float = 0.0
    routing_seconds: float = 0.0
    google_route_ratio: float = 0.0
    handoff_direct_ratio: float = 0.0
    escalation_ratio: float = 0.0
    routing_traces: tuple[dict[str, object], ...] = ()
    translation_seconds: float = 0.0
    prepare_seconds: float = 0.0
    layout_seconds: float = 0.0
    render_seconds: float = 0.0
    total_seconds: float = 0.0
    input_bytes: int = 0
    output_bytes: int = 0
    output_font_references: int = 0
    unique_output_font_objects: int = 0
    execution_backend: str | None = None
    core_version: str | None = None
    ruleset: str | None = None
    source_file: Path | None = None
    source_sha256: str | None = None
    output_file: Path | None = None
    output_sha256: str | None = None
    source_page_count: int | None = None
    output_page_count: int | None = None
    page_geometry_match: bool | None = None
    page_mapping_match: bool | None = None
    engine: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    native_validator_status: str | None = None


class CoreIdentity(NamedTuple):
    version: str
    ruleset: str
    module_path: Path


class PageGeometry(NamedTuple):
    width: float
    height: float
    rotation: int


class PdfFacts(NamedTuple):
    sha256: str
    page_count: int
    geometry: tuple[PageGeometry, ...]
    page_ids: tuple[str | None, ...]


# record_translation_failure passes up either an exception class name or one of
# the converter's fit rules. Reporting them as one sentence sent users to check
# a network that was never the problem, so they are separated here.
_FIT_MARKERS = ("font size", "cannot fit")
_FORMULA_REASON = "FormulaPlaceholderError"
_TOO_LONG_REASON = "SegmentTooLongError"
_INVARIANT_REASON = "TechnicalInvariantError"
_UNRESOLVED_REASON = "UnresolvedSegmentError"


def _count_of_segments(count: int) -> str:
    return f"{count} segment" if count == 1 else f"{count} segments"


def _describe_failures(reasons: Mapping[str, int]) -> list[str]:
    """Turn raw skip reasons into lines that say what the user can do."""
    def is_fit(reason: str) -> bool:
        return any(marker in reason for marker in _FIT_MARKERS)

    fit = sum(count for reason, count in reasons.items() if is_fit(reason))
    formula = reasons.get(_FORMULA_REASON, 0)
    too_long = reasons.get(_TOO_LONG_REASON, 0)
    invariant = reasons.get(_INVARIANT_REASON, 0)
    unresolved = reasons.get(_UNRESOLVED_REASON, 0)
    engine = {
        reason: count
        for reason, count in reasons.items()
        if reason
        not in (
            _FORMULA_REASON,
            _TOO_LONG_REASON,
            _INVARIANT_REASON,
            _UNRESOLVED_REASON,
        )
        and not is_fit(reason)
    }

    lines: list[str] = []
    if fit:
        lines.append(
            f"{_count_of_segments(fit)} stayed in the source language because the "
            "translation did not fit the original line at the smallest allowed size"
        )
    if formula:
        lines.append(
            f"{_count_of_segments(formula)} stayed in the source language because the "
            "translation came back with damaged formula markers"
        )
    if too_long:
        lines.append(
            f"{_count_of_segments(too_long)} stayed in the source language because the "
            "paragraph was longer than the translation service accepts in one request"
        )
    if invariant:
        lines.append(
            f"{_count_of_segments(invariant)} stayed in the source language because "
            "the provider changed protected technical data"
        )
    if unresolved:
        lines.append(
            f"{_count_of_segments(unresolved)} stayed in the source language because "
            "no valid handoff translation was supplied"
        )
    if engine:
        names = ", ".join(f"{name} x{count}" for name, count in sorted(engine.items()))
        lines.append(
            f"{_count_of_segments(sum(engine.values()))} stayed in the source language "
            f"because the translation engine failed ({names})"
        )
    return lines


def _positive_threads(value: str) -> int:
    threads = int(value)
    if not 1 <= threads <= MAX_THREADS:
        raise argparse.ArgumentTypeError(f"threads must be between 1 and {MAX_THREADS}")
    return threads


def _page_selection(value: str) -> str:
    if not re.fullmatch(r"[1-9]\d*(?:-[1-9]\d*)?(?:,[1-9]\d*(?:-[1-9]\d*)?)*", value):
        raise argparse.ArgumentTypeError("pages must use one-based ranges such as 1,3-5")
    for item in value.split(","):
        if "-" in item:
            start, end = (int(part) for part in item.split("-", 1))
            if start > end:
                raise argparse.ArgumentTypeError("page range start must not exceed its end")
    return value


def _source_language(value: str) -> str:
    if value == "auto" or re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?", value):
        return value
    raise argparse.ArgumentTypeError("source language must be 'auto' or a Google language code")


def _target_language(value: str) -> str:
    language = value.lower()
    if language not in TARGET_LANGUAGES:
        supported = ", ".join(sorted(TARGET_LANGUAGES))
        raise argparse.ArgumentTypeError(
            f"unsupported target language {value!r}. The bundled font covers Latin-script "
            f"targets only, so CJK, right-to-left, and complex-shaping scripts would render "
            f"as blank boxes or reordered text. Supported: {supported}"
        )
    return language


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Translate one text-based PDF while preserving layout and formulas."
    )
    parser.add_argument("input_pdf", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--target-language", default=DEFAULT_TARGET_LANGUAGE, type=_target_language
    )
    parser.add_argument("--source-language", default="auto", type=_source_language)
    parser.add_argument("--pages", type=_page_selection)
    parser.add_argument("--threads", default=DEFAULT_THREADS, type=_positive_threads)
    parser.add_argument("--engine", default="google", choices=ENGINES)
    parser.add_argument(
        "--segments",
        type=Path,
        help='handoff/auto: JSONL of {"src","dst"} records to reuse',
    )
    parser.add_argument(
        "--emit-segments",
        type=Path,
        help="handoff/auto: write pending Handoff units here as JSONL",
    )
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--terminology", type=Path, help="confirmed terminology JSON; scopes cache validity")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _validate_arguments(args: argparse.Namespace) -> None:
    if args.output_dir is None and args.emit_segments is None:
        raise TranslationError("--output-dir is required unless --emit-segments is given")
    if args.engine == "handoff":
        if args.segments is None and args.emit_segments is None:
            raise TranslationError("--engine handoff needs --segments, --emit-segments, or both")
    elif args.engine == "google" and (
        args.segments is not None or args.emit_segments is not None
    ):
        raise TranslationError(
            "--segments and --emit-segments require --engine handoff or auto"
        )


def _require_core() -> CoreIdentity:
    try:
        import pdf2zh
        importlib.import_module("pdf2zh.doclayout")
        # high_level pulls in the native stack - pikepdf/qpdf, PyMuPDF, onnx.
        # Without it a broken install slipped past this check and surfaced as a
        # raw ImportError from the engine, once per file in the queue, instead
        # of one actionable message before any work started.
        importlib.import_module("pdf2zh.high_level")
    except ImportError as error:
        requirements = SKILL_ROOT / "requirements.txt"
        install = f'"{sys.executable}" -m pip install -r "{requirements}"'
        raise TranslationError(f"PDF core dependencies are missing. Run: {install}") from error
    if pdf2zh.__version__ != CORE_VERSION:
        raise TranslationError(
            f"Expected bundled PDF core {CORE_VERSION}, found {pdf2zh.__version__}"
        )
    if getattr(pdf2zh, "__ruleset__", None) != RULESET:
        raise TranslationError("Bundled PDF core does not expose the required preservation ruleset")
    # A packaged build has no pip environment for a PyPI wheel to shadow the core,
    # and its module paths point inside the extraction directory rather than here.
    if getattr(sys, "frozen", False):
        return CoreIdentity(
            str(pdf2zh.__version__),
            str(pdf2zh.__ruleset__),
            Path(pdf2zh.__file__).resolve(),
        )
    module_path = Path(pdf2zh.__file__).resolve()
    if not module_path.is_relative_to(BUNDLED_CORE):
        raise TranslationError(f"Refusing external PDF core: {module_path}")
    return CoreIdentity(str(pdf2zh.__version__), str(pdf2zh.__ruleset__), module_path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdf_facts(path: Path, *, role: str) -> PdfFacts:
    """Read artifact facts independently; malformed PDFs cannot reach publish."""
    try:
        import pymupdf

        sha256_before = _sha256(path)
        with pymupdf.open(path) as document:
            if not document.is_pdf or document.page_count < 1:
                raise ValueError("document has no PDF pages")
            geometry: list[PageGeometry] = []
            page_ids: list[str | None] = []
            for page in document:
                geometry.append(
                    PageGeometry(
                        float(page.rect.width),
                        float(page.rect.height),
                        int(page.rotation),
                    )
                )
                kind, value = document.xref_get_key(page.xref, PAGE_ID_KEY)
                page_ids.append(value if kind == "string" else None)
        sha256_after = _sha256(path)
        if sha256_after != sha256_before:
            raise ValueError("artifact changed while its facts were being inspected")
    except Exception as error:
        raise TranslationError(f"Cannot inspect {role} PDF {path}: {_describe(error)}") from error
    return PdfFacts(sha256_after, len(geometry), tuple(geometry), tuple(page_ids))


def _require_source_unchanged(source: Path, expected_sha256: str) -> None:
    try:
        actual_sha256 = _sha256(source)
    except Exception as error:
        raise TranslationError(
            f"Source PDF became unreadable while the native engine was running: "
            f"{source}: {_describe(error)}"
        ) from error
    if actual_sha256 != expected_sha256:
        raise TranslationError(
            f"Source PDF changed while the native engine was running: {source}"
        )


def _audit_final_output_text_layer(
    path: Path,
    report: TranslationReport,
    selected_pages: list[int] | None,
    source_language: str,
    target_language: str,
) -> TranslationReport:
    """Run the local Patch B text-layer audit without rendering or OCR."""
    try:
        import pymupdf

        from pdf2zh.integrity import audit_final_text_layer

        with pymupdf.open(path) as document:
            page_numbers = (
                range(document.page_count) if selected_pages is None else selected_pages
            )
            page_texts = {
                page_number: document[page_number].get_text("text")
                for page_number in page_numbers
            }
        audit = audit_final_text_layer(
            page_texts,
            report.occurrences,
            source_language=source_language,
            target_language=target_language,
        )
    except Exception as error:
        raise TranslationError(
            f"Patch B final text-layer audit failed: {_describe(error)}"
        ) from error
    if audit.final_output_script_leaks:
        pages = ", ".join(str(page + 1) for page in audit.pages_with_leaks)
        raise TranslationError(
            "Patch B rejected the generated PDF: "
            f"final_output_script_leaks={audit.final_output_script_leaks} "
            f"on page(s) {pages}"
        )
    return replace(
        report,
        final_output_script_leaks=0,
        delivery_status=("PARTIAL" if report.unresolved_occurrences else "SUCCESS"),
    )


def _page_identity(source_sha256: str, page_index: int) -> str:
    material = f"{EXECUTION_BACKEND}:{source_sha256}:{page_index}".encode("ascii")
    return hashlib.sha256(material).hexdigest()


def _same_geometry(
    source: tuple[PageGeometry, ...], target: tuple[PageGeometry, ...]
) -> bool:
    return len(source) == len(target) and all(
        abs(first.width - second.width) <= PAGE_GEOMETRY_TOLERANCE
        and abs(first.height - second.height) <= PAGE_GEOMETRY_TOLERANCE
        and first.rotation == second.rotation
        for first, second in zip(source, target)
    )


def _validate_candidate(
    source_facts: PdfFacts,
    candidate_facts: PdfFacts,
    expected_page_ids: tuple[str, ...],
) -> None:
    if candidate_facts.page_count != source_facts.page_count:
        raise TranslationError(
            "Generated PDF failed page-count validation: "
            f"source={source_facts.page_count}, output={candidate_facts.page_count}"
        )
    for page_number, (source_page, output_page) in enumerate(
        zip(source_facts.geometry, candidate_facts.geometry), 1
    ):
        if source_page.rotation != output_page.rotation:
            raise TranslationError(
                "Generated PDF failed rotation validation on page "
                f"{page_number}: source={source_page.rotation}, output={output_page.rotation}"
            )
        if (
            abs(source_page.width - output_page.width) > PAGE_GEOMETRY_TOLERANCE
            or abs(source_page.height - output_page.height) > PAGE_GEOMETRY_TOLERANCE
        ):
            raise TranslationError(
                "Generated PDF failed page-size validation on page "
                f"{page_number}: source={source_page.width:g}x{source_page.height:g}, "
                f"output={output_page.width:g}x{output_page.height:g}"
            )
    if candidate_facts.page_ids != expected_page_ids:
        raise TranslationError(
            "Generated PDF failed page-mapping validation: native page identities are "
            "missing, duplicated, or out of order"
        )


def _validate_input(path: Path) -> Path:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise TranslationError(f"Input PDF does not exist: {source}")
    if source.suffix.lower() != ".pdf":
        raise TranslationError(f"Input must have a .pdf extension: {source}")
    with source.open("rb") as stream:
        if b"%PDF-" not in stream.read(1024):
            raise TranslationError(f"Input does not contain a PDF header: {source}")
    return source


def _describe(error: BaseException) -> str:
    """Flatten an exception chain into one line.

    The core wraps every failure in a generic "Failed to translate <path>", so
    reporting only str(error) hides the reason the document actually failed.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip()
        parts.append(f"{type(current).__name__}: {message}" if message else type(current).__name__)
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


def _pages_to_indices(pages: str | None) -> list[int] | None:
    if pages is None:
        return None
    indices: list[int] = []
    for item in pages.split(","):
        if "-" in item:
            start, end = (int(part) for part in item.split("-", 1))
            indices.extend(range(start - 1, end))
        else:
            indices.append(int(item) - 1)
    return indices


def _segment_envs(
    segments: Path | None,
    emit_segments: Path | None,
    protected_paths: set[Path] | None = None,
) -> dict[str, str]:
    """Resolve the handoff file paths that the translator reads through `envs`."""
    envs: dict[str, str] = {}
    protected_paths = protected_paths or set()
    if segments is not None:
        source = segments.expanduser().resolve()
        if not source.is_file():
            raise TranslationError(f"Segments file does not exist: {source}")
        envs["segments_in"] = str(source)
    if emit_segments is not None:
        emitted = emit_segments.expanduser().resolve()
        aliases_protected_path = emitted in protected_paths or any(
            emitted.exists() and protected.exists() and emitted.samefile(protected)
            for protected in protected_paths
        )
        if aliases_protected_path:
            raise TranslationError(
                f"Refusing to overwrite a protected source path with handoff data: {emitted}"
            )
        if envs.get("segments_in") == str(emitted):
            raise TranslationError("--segments and --emit-segments must use different files")
        emitted.parent.mkdir(parents=True, exist_ok=True)
        envs["segments_out"] = str(emitted)
    return envs


def _detect_confidentiality_markers(
    source: Path, pages: str | None = None
) -> tuple[str, ...]:
    """Best-effort text marker warning; this is not policy classification."""
    try:
        import pymupdf

        selected = _pages_to_indices(pages)
        found: set[str] = set()
        with pymupdf.open(source) as document:
            page_numbers = selected if selected is not None else range(document.page_count)
            for page_number in page_numbers:
                if not 0 <= page_number < document.page_count:
                    continue
                text = document[page_number].get_text()
                folded = text.casefold()
                for marker in CONFIDENTIALITY_MARKERS:
                    if marker.casefold() in folded:
                        found.add(marker)
        return tuple(marker for marker in CONFIDENTIALITY_MARKERS if marker in found)
    except Exception as error:
        logger.debug("Confidentiality marker preflight was unavailable: %s", error)
        return ()


_LAYOUT_MODEL: dict[str | None, object] = {}
# The desktop app warms the model on a background thread while the user is still
# picking files, so two threads really can arrive here at once. On a first run
# onnxruntime serialises a 71 MB optimised graph next to the model, and two of
# those writing the same path would race over a file the next run has to trust.
_LAYOUT_MODEL_LOCK = threading.Lock()


def _layout_model(bundled_path: str | None) -> object:
    """Return the layout model, loading it at most once per process.

    Building the inference session takes about a second and a half, which a
    batch of files would otherwise pay for every single document.
    """
    with _LAYOUT_MODEL_LOCK:
        if bundled_path not in _LAYOUT_MODEL:
            from pdf2zh.doclayout import OnnxModel

            _LAYOUT_MODEL[bundled_path] = (
                OnnxModel(bundled_path) if bundled_path else OnnxModel.load_available()
            )
        return _LAYOUT_MODEL[bundled_path]


def load_layout_model() -> object:
    """Build the inference session, raising if the native stack is unusable.

    The packaged smoke test calls this: onnxruntime and its model are the
    heaviest thing a frozen build has to load, and a bundle that cannot do it
    is broken for every document, not just the first.
    """
    return _layout_model(os.environ.get("PDF_TRANSLATE_MODEL"))


def preload_layout_model() -> None:
    """Build the inference session ahead of the first translation.

    Safe to call from any thread and any number of times; it never raises,
    because a failed warm-up only means the first translation pays the cost
    it used to pay anyway.
    """
    try:
        load_layout_model()
    except Exception:  # noqa: BLE001 - a warm-up failure must stay invisible
        pass


def _run_engine(
    source: Path,
    temp_output: Path,
    target_language: str,
    source_language: str,
    pages: str | None,
    threads: int,
    ignore_cache: bool,
    engine: str,
    envs: dict[str, object],
    on_progress: Callable[[int, int], None] | None = None,
) -> TranslationReport:
    """Run the core and return what it could not translate, and why."""
    from pdf2zh.high_level import translate

    # A packaged build ships the layout model so the first run needs no network.
    model = _layout_model(os.environ.get("PDF_TRANSLATE_MODEL"))

    # The core reports progress by handing its tqdm bar to a callback.
    callback = None
    if on_progress is not None:
        def callback(progress: object) -> None:
            on_progress(getattr(progress, "n", 0), getattr(progress, "total", 0) or 0)

    result = translate(
        files=[str(source)],
        output=str(temp_output),
        pages=_pages_to_indices(pages),
        lang_in=source_language,
        lang_out=target_language,
        service=engine,
        thread=threads,
        model=model,
        envs=envs,
        callback=callback,
        ignore_cache=ignore_cache,
    )
    if len(result) != 1:
        raise TranslationError("PDF core did not report one translated result")
    return result[0][1]


def translate_pdf(
    input_pdf: Path,
    output_dir: Path | None,
    *,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
    source_language: str = "auto",
    pages: str | None = None,
    threads: int = DEFAULT_THREADS,
    ignore_cache: bool = False,
    overwrite: bool = False,
    engine: str = "google",
    segments: Path | None = None,
    emit_segments: Path | None = None,
    terminology: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Translation:
    """Translate one PDF, reporting any segments the engine could not translate."""
    if engine not in ENGINES:
        raise TranslationError(
            f"Unsupported translation engine {engine!r}; expected one of: {', '.join(ENGINES)}"
        )
    core_identity = _require_core()
    source = _validate_input(input_pdf)
    source_facts = _pdf_facts(source, role="source")
    selected_pages = _pages_to_indices(pages)
    if selected_pages is not None and any(
        page < 0 or page >= source_facts.page_count for page in selected_pages
    ):
        raise TranslationError(
            f"Page selection exceeds the {source_facts.page_count}-page source PDF"
        )
    protected_paths = {source}
    terms = None
    if terminology is not None:
        from pdf2zh.terminology import load_terminology

        terminology = terminology.expanduser().resolve()
        protected_paths.add(terminology)
        try:
            terms = load_terminology(terminology)
        except (OSError, ValueError) as error:
            raise TranslationError(f"Cannot load terminology: {terminology}: {error}") from error
    envs: dict[str, object] = dict(_segment_envs(segments, emit_segments, protected_paths))
    if terms is not None:
        envs["terminology"] = terms
    confidentiality_markers = _detect_confidentiality_markers(source, pages)
    if confidentiality_markers:
        logger.warning(
            "Potential confidentiality marker(s) detected before translation: %s. "
            "This warning is not a legal classification or provider-policy enforcement.",
            ", ".join(confidentiality_markers),
        )

    destination: Path | None = None
    destination_dir: Path | None = None
    if output_dir is not None:
        destination_dir = output_dir.expanduser().resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{source.stem}-{target_language}.pdf"
        if destination == source or (
            destination.exists() and destination.samefile(source)
        ):
            raise TranslationError(f"Refusing to replace the source PDF: {source}")
        if destination.exists() and not overwrite:
            raise TranslationError(
                f"Output already exists: {destination}. "
                "Pass --overwrite only with replacement authorization."
            )

    with tempfile.TemporaryDirectory(prefix="pdf-translate-", dir=destination_dir) as temp:
        temp_output = Path(temp)
        expected_page_ids: tuple[str, ...] = ()
        if destination is not None:
            expected_page_ids = tuple(
                _page_identity(source_facts.sha256, page_index)
                for page_index in range(source_facts.page_count)
            )
            envs["page_identity"] = expected_page_ids
        try:
            report = _run_engine(
                source,
                temp_output,
                target_language,
                source_language,
                pages,
                threads,
                ignore_cache,
                engine,
                envs,
                on_progress,
            )
        except TranslationError:
            _require_source_unchanged(source, source_facts.sha256)
            raise
        except Exception as error:
            _require_source_unchanged(source, source_facts.sha256)
            raise TranslationError(f"PDF translation core failed: {_describe(error)}") from error

        _require_source_unchanged(source, source_facts.sha256)
        runtime_after = _require_core()
        if runtime_after != core_identity:
            raise TranslationError("Bundled PDF core identity changed during the run")

        # Nothing was translatable, so the engine produced a copy of the source
        # with no translated text in it. Handing that over as a finished
        # translation is the one outcome the preservation rules forbid outright:
        # say what the document actually needs instead. The message carries the
        # words app/errors.py matches for E-PDF-03.
        if report.eligible_source_spans == 0 and report.translatable_segments == 0:
            raise TranslationError(
                f"No text could be extracted from {source.name}: the selected pages are "
                "image-only scans. This tool does not perform OCR, so run OCR on the "
                "PDF first and translate the result."
            )

        untranslated = report.unresolved_occurrences
        image_only = tuple(sorted(report.image_only_pages))
        if destination is None:
            return Translation(
                path=None,
                untranslated=untranslated,
                reasons=report.reasons,
                image_only_pages=image_only,
                total_segments=report.total_segments,
                translated_segments=report.translated_segments,
                preserved_segments=report.preserved_segments,
                unresolved_segments=report.unresolved_segments,
                eligible_source_spans=report.eligible_source_spans,
                ledger_assigned_source_spans=report.ledger_assigned_source_spans,
                unassigned_source_spans=report.unassigned_source_spans,
                duplicate_source_span_assignments=report.duplicate_source_span_assignments,
                source_occurrences=report.source_occurrences,
                translated_occurrences=report.translated_occurrences,
                allowed_preserve_occurrences=report.allowed_preserve_occurrences,
                unresolved_occurrences=report.unresolved_occurrences,
                unknown_occurrences=report.unknown_occurrences,
                unapproved_preserve_failures=report.unapproved_preserve_failures,
                hangul_leak_failures=report.hangul_leak_failures,
                han_leak_failures=report.han_leak_failures,
                technical_invariant_failures=report.technical_invariant_failures,
                unchanged_prose_failures=report.unchanged_prose_failures,
                repeated_token_corruption_failures=(
                    report.repeated_token_corruption_failures
                ),
                repeated_char_corruption_failures=(
                    report.repeated_char_corruption_failures
                ),
                length_explosion_failures=report.length_explosion_failures,
                unexpected_script_failures=report.unexpected_script_failures,
                cache_validation_failures=report.cache_validation_failures,
                final_output_script_leaks=report.final_output_script_leaks,
                accounting_coverage=report.accounting_coverage,
                translation_completion_rate=report.translation_completion_rate,
                delivery_status=report.delivery_status,
                confidentiality_markers=confidentiality_markers,
                unique_translation_units=report.unique_translation_units,
                raw_text_spans=report.raw_text_spans,
                candidate_fragments=report.candidate_fragments,
                assigned_candidate_fragments=report.assigned_candidate_fragments,
                unassigned_candidate_fragments=report.unassigned_candidate_fragments,
                duplicate_fragment_assignments=report.duplicate_fragment_assignments,
                logical_units=report.logical_units,
                provider_bound_units=report.provider_bound_units,
                merged_fragment_count=report.merged_fragment_count,
                singleton_unit_count=report.singleton_unit_count,
                continuation_candidate_count=report.continuation_candidate_count,
                accepted_merge_count=report.accepted_merge_count,
                rejected_merge_count=report.rejected_merge_count,
                rejected_cross_cell=report.rejected_cross_cell,
                rejected_cross_column=report.rejected_cross_column,
                rejected_structural_role=report.rejected_structural_role,
                rejected_page_boundary=report.rejected_page_boundary,
                rejected_geometry=report.rejected_geometry,
                rejected_linguistic_boundary=report.rejected_linguistic_boundary,
                average_chars_per_unit=report.average_chars_per_unit,
                median_chars_per_unit=report.median_chars_per_unit,
                max_chars_per_unit=report.max_chars_per_unit,
                cache_hits=report.cache_hits,
                provider_requests=report.provider_requests,
                retry_units=report.retry_units,
                handoff_table_hits=report.handoff_table_hits,
                handoff_misses=report.handoff_misses,
                auto_units=report.auto_units,
                preserve_routed_units=report.preserve_routed_units,
                google_routed_units=report.google_routed_units,
                handoff_direct_units=report.handoff_direct_units,
                google_validation_failures=report.google_validation_failures,
                google_to_handoff_escalations=report.google_to_handoff_escalations,
                google_to_handoff_escalated_this_run=(
                    report.google_to_handoff_escalated_this_run
                ),
                pending_handoff_queue_after_run=(
                    report.pending_handoff_queue_after_run
                ),
                handoff_validation_failures=report.handoff_validation_failures,
                handoff_unresolved_units=report.handoff_unresolved_units,
                google_cache_hits=report.google_cache_hits,
                handoff_cache_hits=report.handoff_cache_hits,
                google_provider_requests=report.google_provider_requests,
                handoff_provider_batches_or_requests=(
                    report.handoff_provider_batches_or_requests
                ),
                google_provider_unavailable_units=(
                    report.google_provider_unavailable_units
                ),
                handoff_provider_unavailable_units=(
                    report.handoff_provider_unavailable_units
                ),
                google_translation_seconds=report.google_translation_seconds,
                handoff_translation_seconds=report.handoff_translation_seconds,
                routing_seconds=report.routing_seconds,
                google_route_ratio=report.google_route_ratio,
                handoff_direct_ratio=report.handoff_direct_ratio,
                escalation_ratio=report.escalation_ratio,
                routing_traces=report.routing_traces,
                translation_seconds=report.translation_seconds,
                prepare_seconds=report.prepare_seconds,
                layout_seconds=report.layout_seconds,
                render_seconds=report.render_seconds,
                total_seconds=report.total_seconds,
                input_bytes=report.input_bytes,
                output_bytes=report.output_bytes,
                output_font_references=report.output_font_references,
                unique_output_font_objects=report.unique_output_font_objects,
                execution_backend=EXECUTION_BACKEND,
                core_version=core_identity.version,
                ruleset=core_identity.ruleset,
                source_file=source,
                source_sha256=source_facts.sha256,
                source_page_count=source_facts.page_count,
                engine=engine,
                source_language=source_language,
                target_language=target_language,
            )

        generated = temp_output / f"{source.stem}-mono.pdf"
        if not generated.is_file():
            candidates = sorted(temp_output.glob("*-mono.pdf"))
            if len(candidates) != 1:
                names = ", ".join(path.name for path in temp_output.iterdir()) or "no files"
                raise TranslationError(f"Engine did not produce one translated PDF; found: {names}")
            generated = candidates[0]

        candidate_facts = _pdf_facts(generated, role="generated candidate")
        _validate_candidate(source_facts, candidate_facts, expected_page_ids)
        report = _audit_final_output_text_layer(
            generated,
            report,
            selected_pages,
            source_language,
            target_language,
        )

        # The structural gates above are fatal. Layout warnings remain diagnostic,
        # but the validator itself must complete before an artifact can be final.
        try:
            from pdf2zh.layout_qa import run_layout_qa

            layout_qa = run_layout_qa(source, generated, selected_pages)
            expected_pages_checked = (
                source_facts.page_count if selected_pages is None else len(selected_pages)
            )
            if layout_qa.pages_checked != expected_pages_checked:
                raise RuntimeError(
                    "layout validator checked "
                    f"{layout_qa.pages_checked}/{expected_pages_checked} requested pages"
                )
            if layout_qa.warnings:
                logger.warning(
                    "Post-render layout QA found %d warning(s) across %d page(s) in %.2fs",
                    len(layout_qa.warnings),
                    layout_qa.pages_checked,
                    layout_qa.elapsed_seconds,
                )
                for warning in layout_qa.warnings[:20]:
                    logger.warning(
                        "Layout QA %s page %d bbox=%s: %s",
                        warning.kind,
                        warning.page,
                        tuple(round(value, 2) for value in warning.bbox),
                        warning.detail,
                    )
        except Exception as error:
            raise TranslationError(
                f"Native pre-publish validator failed: {_describe(error)}"
            ) from error

        staged = destination_dir / f".{destination.name}.tmp"
        try:
            shutil.copyfile(generated, staged)
            staged_sha256 = _sha256(staged)
            if staged_sha256 != candidate_facts.sha256:
                raise TranslationError(
                    "Staged PDF hash does not match the validated generated candidate"
                )
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)

        final_sha256 = _sha256(destination)
        if final_sha256 != candidate_facts.sha256:
            raise TranslationError(
                "Final PDF hash does not match the validated generated candidate"
            )

    return Translation(
        path=destination,
        untranslated=untranslated,
        reasons=report.reasons,
        image_only_pages=image_only,
        total_segments=report.total_segments,
        translated_segments=report.translated_segments,
        preserved_segments=report.preserved_segments,
        unresolved_segments=report.unresolved_segments,
        eligible_source_spans=report.eligible_source_spans,
        ledger_assigned_source_spans=report.ledger_assigned_source_spans,
        unassigned_source_spans=report.unassigned_source_spans,
        duplicate_source_span_assignments=report.duplicate_source_span_assignments,
        source_occurrences=report.source_occurrences,
        translated_occurrences=report.translated_occurrences,
        allowed_preserve_occurrences=report.allowed_preserve_occurrences,
        unresolved_occurrences=report.unresolved_occurrences,
        unknown_occurrences=report.unknown_occurrences,
        unapproved_preserve_failures=report.unapproved_preserve_failures,
        hangul_leak_failures=report.hangul_leak_failures,
        han_leak_failures=report.han_leak_failures,
        technical_invariant_failures=report.technical_invariant_failures,
        unchanged_prose_failures=report.unchanged_prose_failures,
        repeated_token_corruption_failures=report.repeated_token_corruption_failures,
        repeated_char_corruption_failures=report.repeated_char_corruption_failures,
        length_explosion_failures=report.length_explosion_failures,
        unexpected_script_failures=report.unexpected_script_failures,
        cache_validation_failures=report.cache_validation_failures,
        final_output_script_leaks=report.final_output_script_leaks,
        accounting_coverage=report.accounting_coverage,
        translation_completion_rate=report.translation_completion_rate,
        delivery_status=report.delivery_status,
        confidentiality_markers=confidentiality_markers,
        unique_translation_units=report.unique_translation_units,
        raw_text_spans=report.raw_text_spans,
        candidate_fragments=report.candidate_fragments,
        assigned_candidate_fragments=report.assigned_candidate_fragments,
        unassigned_candidate_fragments=report.unassigned_candidate_fragments,
        duplicate_fragment_assignments=report.duplicate_fragment_assignments,
        logical_units=report.logical_units,
        provider_bound_units=report.provider_bound_units,
        merged_fragment_count=report.merged_fragment_count,
        singleton_unit_count=report.singleton_unit_count,
        continuation_candidate_count=report.continuation_candidate_count,
        accepted_merge_count=report.accepted_merge_count,
        rejected_merge_count=report.rejected_merge_count,
        rejected_cross_cell=report.rejected_cross_cell,
        rejected_cross_column=report.rejected_cross_column,
        rejected_structural_role=report.rejected_structural_role,
        rejected_page_boundary=report.rejected_page_boundary,
        rejected_geometry=report.rejected_geometry,
        rejected_linguistic_boundary=report.rejected_linguistic_boundary,
        average_chars_per_unit=report.average_chars_per_unit,
        median_chars_per_unit=report.median_chars_per_unit,
        max_chars_per_unit=report.max_chars_per_unit,
        cache_hits=report.cache_hits,
        provider_requests=report.provider_requests,
        retry_units=report.retry_units,
        handoff_table_hits=report.handoff_table_hits,
        handoff_misses=report.handoff_misses,
        auto_units=report.auto_units,
        preserve_routed_units=report.preserve_routed_units,
        google_routed_units=report.google_routed_units,
        handoff_direct_units=report.handoff_direct_units,
        google_validation_failures=report.google_validation_failures,
        google_to_handoff_escalations=report.google_to_handoff_escalations,
        google_to_handoff_escalated_this_run=(
            report.google_to_handoff_escalated_this_run
        ),
        pending_handoff_queue_after_run=report.pending_handoff_queue_after_run,
        handoff_validation_failures=report.handoff_validation_failures,
        handoff_unresolved_units=report.handoff_unresolved_units,
        google_cache_hits=report.google_cache_hits,
        handoff_cache_hits=report.handoff_cache_hits,
        google_provider_requests=report.google_provider_requests,
        handoff_provider_batches_or_requests=(
            report.handoff_provider_batches_or_requests
        ),
        google_provider_unavailable_units=report.google_provider_unavailable_units,
        handoff_provider_unavailable_units=report.handoff_provider_unavailable_units,
        google_translation_seconds=report.google_translation_seconds,
        handoff_translation_seconds=report.handoff_translation_seconds,
        routing_seconds=report.routing_seconds,
        google_route_ratio=report.google_route_ratio,
        handoff_direct_ratio=report.handoff_direct_ratio,
        escalation_ratio=report.escalation_ratio,
        routing_traces=report.routing_traces,
        translation_seconds=report.translation_seconds,
        prepare_seconds=report.prepare_seconds,
        layout_seconds=report.layout_seconds,
        render_seconds=report.render_seconds,
        total_seconds=report.total_seconds,
        input_bytes=report.input_bytes,
        output_bytes=report.output_bytes,
        output_font_references=report.output_font_references,
        unique_output_font_objects=report.unique_output_font_objects,
        execution_backend=EXECUTION_BACKEND,
        core_version=core_identity.version,
        ruleset=core_identity.ruleset,
        source_file=source,
        source_sha256=source_facts.sha256,
        output_file=destination,
        output_sha256=final_sha256,
        source_page_count=source_facts.page_count,
        output_page_count=candidate_facts.page_count,
        page_geometry_match=_same_geometry(
            source_facts.geometry, candidate_facts.geometry
        ),
        page_mapping_match=candidate_facts.page_ids == expected_page_ids,
        engine=engine,
        source_language=source_language,
        target_language=target_language,
        native_validator_status="PASS",
    )


def _use_utf8_output() -> None:
    """Print Vietnamese paths on a legacy console codepage instead of crashing.

    Windows terminals still default to cp1252, which cannot encode Vietnamese, so
    a path like D:\\Tai lieu\\sach-vi.pdf would raise after the work was done.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_output()
    args = _parser().parse_args(argv)
    try:
        _validate_arguments(args)
        result = translate_pdf(
            args.input_pdf,
            args.output_dir,
            target_language=args.target_language,
            source_language=args.source_language,
            pages=args.pages,
            threads=args.threads,
            ignore_cache=args.ignore_cache,
            overwrite=args.overwrite,
            engine=args.engine,
            segments=args.segments,
            emit_segments=args.emit_segments,
            terminology=args.terminology,
        )
    except TranslationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if result.path is not None:
        label = "Translated PDF" if result.delivery_status == "SUCCESS" else "Partial PDF"
        print(f"{label}: {result.path}")
        print(
            "Artifact provenance: "
            + json.dumps(
                {
                    "execution_backend": result.execution_backend,
                    "core_version": result.core_version,
                    "ruleset": result.ruleset,
                    "source_file": str(result.source_file),
                    "source_sha256": result.source_sha256,
                    "output_file": str(result.output_file),
                    "output_sha256": result.output_sha256,
                    "source_page_count": result.source_page_count,
                    "output_page_count": result.output_page_count,
                    "page_geometry_match": result.page_geometry_match,
                    "page_mapping_match": result.page_mapping_match,
                    "engine": result.engine,
                    "source_language": result.source_language,
                    "target_language": result.target_language,
                    "native_validator_status": result.native_validator_status,
                    "delivery_status": result.delivery_status,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    if result.image_only_pages:
        numbers = ", ".join(str(page + 1) for page in result.image_only_pages)
        print(
            f"warning: page {numbers} is an image-only scan and was left untranslated; "
            "this tool has no OCR"
            if len(result.image_only_pages) == 1
            else f"warning: pages {numbers} are image-only scans and were left "
            "untranslated; this tool has no OCR",
            file=sys.stderr,
        )
    for line in _describe_failures(result.reasons):
        print(f"warning: {line}", file=sys.stderr)
    if result.total_segments:
        print(
            "Segment accounting: "
            f"total={result.total_segments}, "
            f"translated={result.translated_segments}, "
            f"preserved={result.preserved_segments}, "
            f"unresolved={result.unresolved_segments}"
        )
        print(
            "Patch B coverage: "
            f"eligible_source_spans={result.eligible_source_spans}, "
            f"ledger_assigned_source_spans={result.ledger_assigned_source_spans}, "
            f"unassigned_source_spans={result.unassigned_source_spans}, "
            "duplicate_source_span_assignments="
            f"{result.duplicate_source_span_assignments}"
        )
        print(
            "Patch B occurrences: "
            f"source_occurrences={result.source_occurrences}, "
            f"translated_occurrences={result.translated_occurrences}, "
            f"allowed_preserve_occurrences={result.allowed_preserve_occurrences}, "
            f"unresolved_occurrences={result.unresolved_occurrences}, "
            f"unknown_occurrences={result.unknown_occurrences}, "
            f"accounting_coverage={result.accounting_coverage:.4f}, "
            "translation_completion_rate="
            f"{result.translation_completion_rate:.4f}, "
            f"delivery_status={result.delivery_status}"
        )
        print(
            "Patch B integrity failures: "
            f"unapproved_preserve_failures={result.unapproved_preserve_failures}, "
            f"hangul_leak_failures={result.hangul_leak_failures}, "
            f"han_leak_failures={result.han_leak_failures}, "
            f"technical_invariant_failures={result.technical_invariant_failures}, "
            f"unchanged_prose_failures={result.unchanged_prose_failures}, "
            "repeated_token_corruption_failures="
            f"{result.repeated_token_corruption_failures}, "
            "repeated_char_corruption_failures="
            f"{result.repeated_char_corruption_failures}, "
            f"length_explosion_failures={result.length_explosion_failures}, "
            f"unexpected_script_failures={result.unexpected_script_failures}, "
            f"cache_validation_failures={result.cache_validation_failures}, "
            f"final_output_script_leaks={result.final_output_script_leaks}"
        )
        print(
            "Translation work: "
            f"unique_units={result.unique_translation_units}, "
            f"provider_requests={result.provider_requests}, "
            f"retry_units={result.retry_units}, "
            f"cache_hits={result.cache_hits}, "
            f"handoff_table_hits={result.handoff_table_hits}, "
            f"handoff_misses={result.handoff_misses}"
        )
        print(
            "AUTO routing: "
            f"auto_units={result.auto_units}, "
            f"preserve_routed_units={result.preserve_routed_units}, "
            f"google_routed_units={result.google_routed_units}, "
            f"handoff_direct_units={result.handoff_direct_units}, "
            f"google_validation_failures={result.google_validation_failures}, "
            f"google_to_handoff_escalations={result.google_to_handoff_escalations}, "
            "google_to_handoff_escalated_this_run="
            f"{result.google_to_handoff_escalated_this_run}, "
            "pending_handoff_queue_after_run="
            f"{result.pending_handoff_queue_after_run}, "
            f"handoff_validation_failures={result.handoff_validation_failures}, "
            f"handoff_unresolved_units={result.handoff_unresolved_units}"
        )
        print(
            "AUTO provider/cache: "
            f"google_cache_hits={result.google_cache_hits}, "
            f"handoff_cache_hits={result.handoff_cache_hits}, "
            f"google_provider_requests={result.google_provider_requests}, "
            "handoff_provider_batches_or_requests="
            f"{result.handoff_provider_batches_or_requests}, "
            "google_provider_unavailable_units="
            f"{result.google_provider_unavailable_units}, "
            "handoff_provider_unavailable_units="
            f"{result.handoff_provider_unavailable_units}"
        )
        print(
            "AUTO timing/ratios: "
            f"google_translation_seconds={result.google_translation_seconds:.3f}, "
            f"handoff_translation_seconds={result.handoff_translation_seconds:.3f}, "
            f"routing_seconds={result.routing_seconds:.6f}, "
            f"google_route_ratio={result.google_route_ratio:.4f}, "
            f"handoff_direct_ratio={result.handoff_direct_ratio:.4f}, "
            f"escalation_ratio={result.escalation_ratio:.4f}"
        )
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            for trace in result.routing_traces:
                print(
                    "AUTO route trace: "
                    + json.dumps(trace, ensure_ascii=False, separators=(",", ":"))
                )
        print(
            "Logical reconstruction: "
            f"raw_text_spans={result.raw_text_spans}, "
            f"candidate_fragments={result.candidate_fragments}, "
            f"assigned_candidate_fragments={result.assigned_candidate_fragments}, "
            f"unassigned_candidate_fragments={result.unassigned_candidate_fragments}, "
            f"duplicate_fragment_assignments={result.duplicate_fragment_assignments}, "
            f"logical_units={result.logical_units}, "
            f"provider_bound_units={result.provider_bound_units}, "
            f"merged_fragment_count={result.merged_fragment_count}, "
            f"singleton_unit_count={result.singleton_unit_count}"
        )
        print(
            "Continuation decisions: "
            f"continuation_candidate_count={result.continuation_candidate_count}, "
            f"accepted_merge_count={result.accepted_merge_count}, "
            f"rejected_merge_count={result.rejected_merge_count}, "
            f"rejected_cross_cell={result.rejected_cross_cell}, "
            f"rejected_cross_column={result.rejected_cross_column}, "
            f"rejected_structural_role={result.rejected_structural_role}, "
            f"rejected_page_boundary={result.rejected_page_boundary}, "
            f"rejected_geometry={result.rejected_geometry}, "
            "rejected_linguistic_boundary="
            f"{result.rejected_linguistic_boundary}"
        )
        print(
            "Logical unit characters: "
            f"average_chars_per_unit={result.average_chars_per_unit:.2f}, "
            f"median_chars_per_unit={result.median_chars_per_unit:.2f}, "
            f"max_chars_per_unit={result.max_chars_per_unit}"
        )
        candidate_merge_rate = (
            result.continuation_candidate_count / result.raw_text_spans
            if result.raw_text_spans
            else 0.0
        )
        merge_acceptance_rate = (
            result.accepted_merge_count / result.continuation_candidate_count
            if result.continuation_candidate_count
            else 0.0
        )
        singleton_rate = (
            result.singleton_unit_count / result.logical_units
            if result.logical_units
            else 0.0
        )
        print(
            "Reconstruction review rates: "
            f"candidate_merge_rate={candidate_merge_rate:.4f}, "
            f"merge_acceptance_rate={merge_acceptance_rate:.4f}, "
            f"singleton_rate={singleton_rate:.4f}"
        )
        print(
            "KNOWN CROSS-PAGE CONTINUATION LIMITATION: source fragments on "
            "different pages remain independent logical units"
        )
        print(
            "Timing: "
            f"prepare_seconds={result.prepare_seconds:.3f}, "
            f"layout_seconds={result.layout_seconds:.3f}, "
            f"translation_seconds={result.translation_seconds:.3f}, "
            f"render_seconds={result.render_seconds:.3f}, "
            f"total_seconds={result.total_seconds:.3f}"
        )
        print(
            "PDF/font resources: "
            f"input_bytes={result.input_bytes}, output_bytes={result.output_bytes}, "
            f"output_font_references={result.output_font_references}, "
            f"unique_output_font_objects={result.unique_output_font_objects}"
        )
    if args.emit_segments is not None:
        emitted = args.emit_segments.expanduser().resolve()
        pending = sum(1 for line in emitted.open(encoding="utf-8") if line.strip())
        print(f"Segments left untranslated: {pending} -> {emitted}")
    if result.path is not None and result.delivery_status == "PARTIAL":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
