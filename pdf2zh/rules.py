"""Preservation rules that distinguish the Code4Life PDF translation core."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

from pdf2zh.logical_units import (
    LogicalFragment,
    ReconstructionMetrics,
    build_logical_units,
)

FORMULA_FONT_PATTERN = re.compile(
    r"(CM[^R]|MS.M|XY|MT|BL|RM|EU|LA|RS|LINE|LCIRCLE|TeX-|rsfs|txsy|wasy|"
    r"stmary|.*Mono|.*Code|.*Sym|.*Math|.*Typewriter|Cousine|Consolas|Menlo|"
    r"Monaco|Inconsolata|Source.?Code|Fira.?Code|DejaVu.?Sans.?Mono|"
    r"Liberation.?Mono|Courier)"
)

MATH_OPERATOR_PATTERN = re.compile(
    r"[=≤≥≈≠±×÷·∑∫√∞∝+*/^]"
)
PROSE_WORD_PATTERN = re.compile(r"[a-z]{3,}")
CJK_PROSE_PATTERN = re.compile(
    r"[\uac00-\ud7a3\u3400-\u9fff]{2,}|(?:[A-Za-z]{2,}|\d+)[\uac00-\ud7a3]+"
)
COMMON_STANDALONE_TECHNICAL_TERM_PATTERN = re.compile(
    r"(?:PLC|HMI|Servo|Servo\s+Motor|Encoder|Interlock|JOG|Servo\s+ON|"
    r"SCARA\s+Robot|Linear\s+Motor|Buffer\s+C/V|Pick\s*&\s*Place|"
    r"BCR|(?:2D\s+)?CCD|FFU|PCW|Utility|Check\s+Sheet|Spare\s+Parts|C/V|"
    r"Page\s+No\.?)",
    re.IGNORECASE,
)
IMMUTABLE_METADATA_PATTERNS = (
    re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+){2,}\b"),
    re.compile(r"\bRev\.\s*:?\s*\d+(?:\.\d+)+\b", re.IGNORECASE),
    re.compile(r"\b\d{1,4}\s*/\s*\d{1,4}\b"),
    re.compile(r"\b(?:19|20)\d{2}[.-]\s*\d{1,2}[.-]\s*\d{1,2}\b"),
    re.compile(r"\b(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\b"),
    re.compile(r"\b[A-Z]{2,}[A-Z0-9]*\d{2,}\b"),
    re.compile(r"\bCONFIDENTIAL\b(?:\s+LG\s+Display\s+Co\.,?\s+Ltd)?(?:\s+\d{4})?", re.IGNORECASE),
)
MATH_FUNCTION_PATTERN = re.compile(
    r"(?<![A-Za-z])(?:sin|cos|tan|cot|sec|csc|log|ln|exp|min|max|lim|det|mod)(?![A-Za-z])",
    re.IGNORECASE,
)
STACKED_TOKEN_PATTERN = re.compile(
    r"[A-Za-z\u0370-\u03ff][A-Za-z\u0370-\u03ff0-9%]*"
)

BULLET_CHARACTERS = frozenset(
    ("•", "■", "□", "▪", "▸", "▹", "►", "▶", "●", "○", "◆", "◇", "★", "☆", "‣", "⬤")
)

PRIVATE_USE_BULLETS = frozenset(("\uf0b7", "\uf0d8", "\uf0fc"))

LANGUAGE_LINE_HEIGHT = {
    "zh-cn": 1.4,
    "zh-tw": 1.4,
    "zh-hans": 1.4,
    "zh-hant": 1.4,
    "zh": 1.4,
    "ja": 1.1,
    "ko": 1.2,
    "en": 1.2,
    "ar": 1.0,
    "ru": 0.8,
    "uk": 0.8,
    "ta": 0.8,
    "vi": 1.2,
}

# Measured ink extents, not preferences: see min_line_height_for_language.
DEFAULT_MIN_LINE_HEIGHT = 0.95
LANGUAGE_MIN_LINE_HEIGHT = {
    "vi": 1.10,
}


@dataclass(frozen=True)
class PreservationDecision:
    """A page classification whose layout must remain untouched."""

    kind: str
    detail: str


@dataclass(frozen=True)
class TableTextCluster:
    """A visual text group inside a table cell, separated from codes/units."""

    bbox: tuple[float, float, float, float]
    text: str
    words: tuple[Sequence[Any], ...]
    # ``bbox`` is the complete logical occurrence.  ``regions`` are the
    # physical source lines that own glyphs, so assigning one layout class does
    # not paint across unrelated material in the whitespace between them.
    regions: tuple[tuple[float, float, float, float], ...] = ()
    # A translation may safely use more room than the source words occupied
    # (for example the gap before a TOC leader).  Keep that fitting region
    # separate from both source ownership and provenance.
    render_bbox: tuple[float, float, float, float] | None = None
    # Used only to distinguish a heading/body role change across physical
    # lines. Inline weight/slant remains style markup inside one unit.
    font_size: float | None = None
    style: int = 0
    parent_id: Any = None
    fragment_id: str | None = None
    # Patch C reads these values from Patch A's completed result.  They are
    # provenance only: they do not participate in grouping or merge decisions.
    logical_unit_id: str | None = None
    source_fragment_ids: tuple[str, ...] = ()
    merge_reasons: tuple[str, ...] = ()
    cell_id: Any = None
    callout_id: Any = None
    structural_role: str = "body"


def is_formula_font(font_name: str) -> bool:
    """Return whether a font name marks formula or code text."""
    return FORMULA_FONT_PATTERN.match(font_name) is not None


def is_bullet_character(text: str, font_name: str | bytes = "") -> bool:
    """Recognize Unicode bullets and common Symbol/Wingdings PUA bullets."""
    if text in BULLET_CHARACTERS:
        return True
    if isinstance(font_name, bytes):
        font_name = font_name.decode(errors="ignore")
    return (
        text in PRIVATE_USE_BULLETS
        and re.search(
            r"wingdings|webdings|symbol|dingbats", font_name, re.IGNORECASE
        )
        is not None
    )


def line_height_for_language(language: str) -> float:
    """Return the translation line-height multiplier for a target language."""
    return LANGUAGE_LINE_HEIGHT.get(language.lower(), 1.1)


def min_line_height_for_language(language: str) -> float:
    """Return the tightest leading that still keeps two lines from touching.

    A paragraph that grew in translation used to buy room by crushing its
    leading to 0.75, which is below the ink of the glyphs being drawn. Measured
    by rendering every letter in the output font and reading the real ink
    extent, in em above and below the baseline:

        English lowercase   0.695 up + 0.210 down = 0.905
        Vietnamese          0.890 up + 0.210 down = 1.100

    Stacked tone marks (e-circumflex-acute, o-horn-grave) reach far higher than
    a plain ascender, so Vietnamese needs more room than English rather than
    less. Below these values the lines overlap no matter what else is right.
    """
    return LANGUAGE_MIN_LINE_HEIGHT.get(language.lower(), DEFAULT_MIN_LINE_HEIGHT)


def _rect(value: Sequence[Any]) -> tuple[float, float, float, float] | None:
    if len(value) < 4:
        return None
    try:
        return tuple(float(item) for item in value[:4])
    except (TypeError, ValueError):
        return None


def upright_line_bounds(
    line: Mapping[str, Any],
) -> tuple[float, float, float, float] | None:
    """Return the visual ink band for an upright PyMuPDF text line.

    Some technical manuals embed Hangul as Type 3 glyphs whose declared font
    box is roughly ten times the actual 11 pt line height.  PyMuPDF then
    reports a line from the baseline down through several following lines.  If
    that raw rectangle is painted into the layout map, later anchors overwrite
    earlier ones and a word can be split into translated and replayed pieces.

    Span origins, font size, ascender and descender still describe the visual
    baseline correctly.  Use them when the reported rectangle is implausible;
    retain the native bounds for ordinary fonts.
    """
    native = _rect(line.get("bbox", ()))
    spans = tuple(line.get("spans", ()))
    if native is None or not spans:
        return native

    visual: list[tuple[float, float, float, float]] = []
    for span in spans:
        bounds = _rect(span.get("bbox", ()))
        origin = span.get("origin", ())
        try:
            size = abs(float(span.get("size", 0.0)))
            baseline = float(origin[1])
            ascender = float(span.get("ascender", 0.9))
            descender = float(span.get("descender", -0.2))
        except (IndexError, TypeError, ValueError):
            continue
        if bounds is None or size <= 0:
            continue
        # Broken font metadata occasionally reports extreme ascender values as
        # well.  These conservative defaults describe the ordinary em box and
        # still include combining marks after the one-point paint padding.
        if not 0.4 <= ascender <= 1.5:
            ascender = 0.9
        if not -0.6 <= descender <= 0.2:
            descender = -0.2
        top = baseline - size * ascender
        bottom = baseline - size * descender
        visual.append((bounds[0], min(top, bottom), bounds[2], max(top, bottom)))
    if not visual:
        return native

    candidate = (
        min(rect[0] for rect in visual),
        min(rect[1] for rect in visual),
        max(rect[2] for rect in visual),
        max(rect[3] for rect in visual),
    )
    native_height = native[3] - native[1]
    visual_height = candidate[3] - candidate[1]
    if visual_height > 0 and native_height > visual_height * 2.5:
        return candidate
    return native


def _inside_any(
    rectangle: tuple[float, float, float, float],
    regions: Iterable[Sequence[Any]],
) -> bool:
    x0, y0, x1, y1 = rectangle
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for region in regions:
        bounds = _rect(region)
        if bounds is None:
            continue
        rx0, ry0, rx1, ry1 = bounds
        if rx0 <= cx <= rx1 and ry0 <= cy <= ry1:
            return True
    return False


def formula_regions(
    blocks: Iterable[Sequence[Any]],
    words: Iterable[Sequence[Any]],
    *,
    stacked_exclusions: Iterable[Sequence[Any]] = (),
) -> list[tuple[float, float, float, float]]:
    """Return ordinary-font regions whose exact mathematical layout must survive.

    The layout model catches dedicated formula fonts well, but technical PDFs
    often typeset equations in the same font as their prose. Operator-heavy
    blocks without prose words are equations. A second geometry rule catches an
    inline stacked fraction such as F1/b0 even when it sits inside a prose block.
    """
    protected: list[tuple[float, float, float, float]] = []
    for block in blocks:
        bounds = _rect(block)
        if bounds is None or len(block) < 5:
            continue
        compact = " ".join(str(block[4]).split())
        prose_candidate = MATH_FUNCTION_PATTERN.sub("", compact)
        if (
            compact
            and MATH_OPERATOR_PATTERN.search(compact)
            and PROSE_WORD_PATTERN.search(prose_candidate) is None
            and CJK_PROSE_PATTERN.search(prose_candidate) is None
        ):
            protected.append(bounds)

    candidates = list(words)
    exclusions = tuple(stacked_exclusions)
    for index, upper in enumerate(candidates):
        upper_bounds = _rect(upper)
        if upper_bounds is None or len(upper) < 8:
            continue
        upper_text = str(upper[4])
        if (
            len(upper_text) > 4
            or STACKED_TOKEN_PATTERN.fullmatch(upper_text) is None
            or not any(character.isdigit() for character in upper_text)
            or _inside_any(upper_bounds, exclusions)
        ):
            continue
        ux0, uy0, ux1, uy1 = upper_bounds
        for lower in candidates[index + 1 :]:
            lower_bounds = _rect(lower)
            if lower_bounds is None or len(lower) < 8:
                continue
            if upper[5] != lower[5] or upper[6] == lower[6]:
                continue
            lower_text = str(lower[4])
            if (
                len(lower_text) > 4
                or STACKED_TOKEN_PATTERN.fullmatch(lower_text) is None
                or not any(character.isdigit() for character in lower_text)
                or _inside_any(lower_bounds, exclusions)
            ):
                continue
            lx0, ly0, lx1, ly1 = lower_bounds
            overlap = max(0.0, min(ux1, lx1) - max(ux0, lx0))
            smaller_width = min(ux1 - ux0, lx1 - lx0)
            centre_gap = abs((uy0 + uy1) / 2 - (ly0 + ly1) / 2)
            max_height = max(uy1 - uy0, ly1 - ly0)
            if (
                smaller_width > 0
                and overlap / smaller_width >= 0.6
                and 2 < centre_gap <= 1.5 * max_height
            ):
                protected.append(
                    (
                        min(ux0, lx0),
                        min(uy0, ly0),
                        max(ux1, lx1),
                        max(uy1, ly1),
                    )
                )
    return protected


def anchored_translatable_lines(
    blocks: Iterable[Mapping[str, Any]],
) -> list[TableTextCluster]:
    """Extract upright natural-language anchors from protected regions.

    A figure or unmatched outer table column can contain ordinary PDF text.
    Its visual line rectangle is a usable local region even when no full grid
    exists.  Physical lines remain separate here; the caller may group nearby
    lines inside the same protected parent into one logical occurrence.
    """
    result: list[TableTextCluster] = []
    for block_index, block in enumerate(blocks):
        for line_index, line in enumerate(block.get("lines", ())):
            direction = line.get("dir", (1.0, 0.0))
            if (
                abs(float(direction[0]) - 1.0) > 0.01
                or abs(float(direction[1])) > 0.01
            ):
                continue
            spans = tuple(line.get("spans", ()))
            text = "".join(str(span.get("text", "")) for span in spans)
            bounds = upright_line_bounds(line)
            if bounds is None or not should_translate_table_cell(text):
                continue
            for span_index, span in enumerate(spans):
                span_text = str(span.get("text", ""))
                if not span_text:
                    continue
                span_bounds = _rect(span.get("bbox", ()))
                if span_bounds is None:
                    span_bounds = bounds
                # Keep the normalized visual line band for pathological Type 3
                # metadata while retaining the span's horizontal run extent.
                span_bounds = (span_bounds[0], bounds[1], span_bounds[2], bounds[3])
                try:
                    font_size = abs(float(span.get("size", 0.0))) or None
                except (TypeError, ValueError):
                    font_size = None
                flags = int(span.get("flags", 0) or 0)
                style = (1 if flags & 16 else 0) | (2 if flags & 2 else 0)
                result.append(
                    TableTextCluster(
                        span_bounds,
                        span_text,
                        (),
                        (bounds,),
                        font_size=font_size,
                        style=style,
                        parent_id=("text-block", block_index),
                        fragment_id=(
                            f"block-{block_index}-line-{line_index}-span-{span_index}"
                        ),
                    )
                )
    return result


def group_translatable_line_clusters(
    clusters: Iterable[TableTextCluster],
    *,
    barriers: Iterable[Sequence[Any]] = (),
    page: int = 0,
    region_id: Any = "established-parent",
    cell_id: Any = None,
    column_id: Any = None,
    paragraph_id: Any = None,
    callout_id: Any = None,
    structural_role: str = "body",
    metrics: ReconstructionMetrics | None = None,
    fragment_id_prefix: str = "fragment",
) -> list[TableTextCluster]:
    """Join conservative source-line wraps into atomic logical occurrences.

    The input must already be limited to one protected parent (one callout,
    figure, or unmatched table region).  Geometry, punctuation and list
    boundaries all have to agree before two physical lines are joined.  This
    keeps neighboring labels independent while repairing manuals whose PDF
    producer emits every visual line as a separate text block.
    """
    source = list(clusters)
    by_id: dict[str, TableTextCluster] = {}
    fragments: list[LogicalFragment] = []
    for index, cluster in enumerate(source):
        identifier = f"{fragment_id_prefix}-{cluster.fragment_id or index}"
        by_id[identifier] = cluster
        fragments.append(
            LogicalFragment(
                fragment_id=identifier,
                text=cluster.text,
                page=page,
                bbox=cluster.bbox,
                reading_order=index,
                region_id=region_id,
                cell_id=cell_id,
                column_id=column_id,
                paragraph_id=paragraph_id,
                callout_id=callout_id,
                parent_id=(
                    cluster.parent_id
                    if cluster.parent_id is not None
                    else region_id
                ),
                structural_role=structural_role,
                style=cluster.style,
                font_size=cluster.font_size,
            )
        )
    normalized_barriers = tuple(
        bound for value in barriers if (bound := _rect(value)) is not None
    )
    reconstruction = build_logical_units(fragments, barriers=normalized_barriers)
    if metrics is not None:
        metrics.absorb(reconstruction.metrics)

    result: list[TableTextCluster] = []
    for unit in reconstruction.units:
        children = [by_id[item.fragment_id] for item in unit.fragments]
        regions = tuple(
            dict.fromkeys(
                region
                for child in children
                for region in (child.regions or (child.bbox,))
            )
        )
        result.append(
            TableTextCluster(
                unit.bbox,
                unit.text,
                tuple(word for child in children for word in child.words),
                regions,
                font_size=max(
                    (
                        child.font_size
                        for child in children
                        if child.font_size is not None
                    ),
                    default=None,
                ),
                parent_id=children[0].parent_id if children else None,
                logical_unit_id=unit.logical_unit_id,
                source_fragment_ids=tuple(
                    fragment.fragment_id for fragment in unit.fragments
                ),
                merge_reasons=unit.merge_reasons,
                cell_id=cell_id,
                callout_id=callout_id,
                structural_role=structural_role,
            )
        )
    return result


_STRUCTURAL_LEADER_PATTERN = re.compile(
    r"(?:\.{2,}|[\u2024\u2025\u2026\u2500-\u257f\ufffd\x08]{2,})"
)
_STRUCTURAL_LOCATOR_PATTERN = re.compile(
    r"(?:\d{1,4}|[ivxlcdm]+)[,;:.-]?", re.IGNORECASE
)
_STRUCTURAL_SECTION_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.)]?", re.IGNORECASE
)
_CITATION_LINE_PATTERN = re.compile(
    r"(?:^\s*(?:\[\d{1,3}\]|\d{1,3}\.\s|[A-Z][a-z]+,?\s.*\(\d{4}\))|"
    r"(?:DOI|ISBN|ISSN)\b|https?://)",
    re.IGNORECASE,
)


def _word_bounds(words: Sequence[Sequence[Any]]) -> tuple[float, float, float, float]:
    return (
        min(float(word[0]) for word in words),
        min(float(word[1]) for word in words),
        max(float(word[2]) for word in words),
        max(float(word[3]) for word in words),
    )


def structural_page_text_clusters(
    words: Iterable[Sequence[Any]],
    kind: str,
) -> list[TableTextCluster]:
    """Select prose within a structural page while leaving identity data fixed.

    Each result stays on its source line. TOC/index leaders and locators,
    nomenclature symbols, and complete citation lines never enter the result.
    Mixed prose such as ``D100 Parameters`` remains one unit so existing
    technical invariants can preserve the identifier while translating prose.
    """
    grouped: dict[tuple[int, int], list[Sequence[Any]]] = {}
    for word in words:
        if len(word) < 5:
            continue
        block_number = int(word[5]) if len(word) > 5 else 0
        line_number = int(word[6]) if len(word) > 6 else 0
        grouped.setdefault((block_number, line_number), []).append(word)

    result: list[TableTextCluster] = []
    for key in sorted(grouped):
        line_words = sorted(
            grouped[key], key=lambda word: int(word[7]) if len(word) > 7 else float(word[0])
        )
        line_text = " ".join(str(word[4]) for word in line_words)
        if kind == "REFERENCES" and _CITATION_LINE_PATTERN.search(line_text):
            continue

        start, end = 0, len(line_words)
        if kind == "TOC" and end > 1 and _STRUCTURAL_SECTION_PATTERN.fullmatch(
            str(line_words[0][4])
        ):
            start = 1
        if kind in {"TOC", "INDEX"}:
            for index in range(start, end):
                if _STRUCTURAL_LEADER_PATTERN.fullmatch(str(line_words[index][4])):
                    end = index
                    break
            else:
                while end > start and _STRUCTURAL_LOCATOR_PATTERN.fullmatch(
                    str(line_words[end - 1][4])
                ):
                    end -= 1
        elif kind == "NOMENCLATURE" and end - start > 1:
            first = str(line_words[0][4])
            if not should_translate_table_cell(first):
                start = 1

        selected = line_words[start:end]
        selected_text = " ".join(str(word[4]) for word in selected)
        if not selected or not should_translate_table_cell(selected_text):
            continue
        source_bounds = _word_bounds(selected)
        render_bounds = source_bounds
        if kind in {"TOC", "INDEX"} and end < len(line_words):
            next_x = min(float(word[0]) for word in line_words[end:])
            if next_x > source_bounds[2] + 2:
                render_bounds = (
                    source_bounds[0],
                    source_bounds[1],
                    next_x - 2,
                    source_bounds[3],
                )
        result.append(
            TableTextCluster(
                source_bounds,
                selected_text,
                tuple(selected),
                (source_bounds,),
                render_bounds,
            )
        )
    return result


def immutable_metadata_regions(
    blocks: Iterable[Mapping[str, Any]],
) -> list[TableTextCluster]:
    """Locate exact metadata values without protecting adjacent Korean labels.

    Raw PyMuPDF dictionary characters let a mixed line such as a Korean date
    label plus ``2007. 07. 10`` keep only the value immutable. The remaining
    label can still enter the normal translation path. The patterns describe
    document metadata syntax; they contain no document-specific values.
    """
    result: list[TableTextCluster] = []
    seen: set[tuple[str, tuple[float, float, float, float]]] = set()
    for block in blocks:
        for line in block.get("lines", ()):
            characters: list[tuple[str, tuple[float, float, float, float]]] = []
            for span in line.get("spans", ()):
                span_bounds = upright_line_bounds(
                    {"bbox": span.get("bbox", ()), "spans": (span,)}
                )
                for character in span.get("chars", ()):
                    bounds = _rect(character.get("bbox", ()))
                    text = str(character.get("c", ""))
                    if bounds is not None and span_bounds is not None:
                        char_height = bounds[3] - bounds[1]
                        span_height = span_bounds[3] - span_bounds[1]
                        if span_height > 0 and char_height > span_height * 2.5:
                            bounds = (
                                bounds[0],
                                span_bounds[1],
                                bounds[2],
                                span_bounds[3],
                            )
                    if bounds is not None and text:
                        characters.append((text, bounds))
            if not characters:
                continue
            text = "".join(character for character, _bounds in characters)
            for pattern in IMMUTABLE_METADATA_PATTERNS:
                for match in pattern.finditer(text):
                    matched = characters[match.start() : match.end()]
                    if not matched:
                        continue
                    bounds = (
                        min(value[0] for _character, value in matched),
                        min(value[1] for _character, value in matched),
                        max(value[2] for _character, value in matched),
                        max(value[3] for _character, value in matched),
                    )
                    key = (match.group(0), bounds)
                    if key in seen:
                        continue
                    seen.add(key)
                    result.append(TableTextCluster(bounds, match.group(0), ()))
    return result


def anchored_prose_bounds(
    source: Sequence[float], parent: Sequence[float],
    text_lines: Iterable[Sequence[float]], barriers: Iterable[Sequence[float]],
) -> tuple[float, float, float, float]:
    """Borrow only clear local space bounded by existing strokes and neighbors.

    This does not infer a grid. Each existing source line owns the space up to
    a physical rule or the midpoint of the gap to the next text line. Adjacent
    translated anchors therefore cannot borrow the same gap.
    """
    x0,y0,x1,y1 = source
    left,top,right,bottom = parent
    lines = [tuple(r) for r in text_lines if tuple(r) != tuple(source)]
    strokes = list(barriers)
    for bx0,by0,bx1,by1 in strokes:
        if by0 < (y0+y1)/2 < by1 and bx1-bx0 <= 1.5:
            if bx1 <= x0: left=max(left,bx1+1)
            if bx0 >= x1: right=min(right,bx0-1)
    for bx0,by0,bx1,by1 in lines:
        if min(y1,by1)-max(y0,by0) <= 0.5:
            continue
        if bx1 <= x0: left=max(left,(bx1+x0)/2)
        if bx0 >= x1: right=min(right,(x1+bx0)/2)
    for bx0,by0,bx1,by1 in strokes:
        if min(right,bx1)-max(left,bx0) > 1 and by1-by0 <= 1.5:
            if by1 <= y0: top=max(top,by1+0.5)
            if by0 >= y1: bottom=min(bottom,by0-0.5)
    for bx0,by0,bx1,by1 in lines:
        if min(right,bx1)-max(left,bx0) <= 1:
            continue
        if by1 <= y0: top=max(top,(by1+y0)/2)
        if by0 >= y1: bottom=min(bottom,(y1+by0)/2)
    # Never shrink the source footprint because a noisy rule touches its ink.
    return min(left,x0),min(top,y0),max(right,x1),max(bottom,y1)


def matching_table_cells(
    model_bounds: Sequence[Any],
    tables: Iterable[Any],
    *,
    minimum_overlap: float = 0.5,
) -> list[tuple[float, float, float, float]]:
    """Return cells from the table whose area covers a model table detection.

    The model is the gate: PyMuPDF cell detection only enables translation when
    it can explain at least half of that already-recognised table. Unmatched
    tables keep the old, fully protected behaviour.
    """
    model = _rect(model_bounds)
    if model is None:
        return []
    mx0, my0, mx1, my1 = model
    model_area = max(0.0, mx1 - mx0) * max(0.0, my1 - my0)
    if model_area <= 0:
        return []

    best: Any = None
    best_overlap = 0.0
    for table in tables:
        bounds = _rect(getattr(table, "bbox", ()))
        if bounds is None:
            continue
        tx0, ty0, tx1, ty1 = bounds
        intersection = max(0.0, min(mx1, tx1) - max(mx0, tx0)) * max(
            0.0, min(my1, ty1) - max(my0, ty0)
        )
        overlap = intersection / model_area
        if overlap > best_overlap:
            best, best_overlap = table, overlap
    if best is None or best_overlap < minimum_overlap:
        return []

    cells: list[tuple[float, float, float, float]] = []
    for cell in getattr(best, "cells", ()):
        bounds = _rect(cell)
        if bounds is None:
            continue
        x0, y0, x1, y1 = bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if mx0 <= cx <= mx1 and my0 <= cy <= my1 and bounds not in cells:
            cells.append(bounds)
    return cells


def should_translate_table_cell(text: str) -> bool:
    """Return whether a cell contains natural-language text rather than codes.

    Product identifiers, numeric cells, formulas, and immutable metadata remain
    source glyphs. English/Korean/Chinese prose is selected by shared token-role
    rules rather than by requiring lowercase Latin or Hangul evidence.
    """
    value = " ".join(text.split())
    if not value:
        return False
    if COMMON_STANDALONE_TECHNICAL_TERM_PATTERN.fullmatch(value):
        return False
    if any(pattern.fullmatch(value) for pattern in IMMUTABLE_METADATA_PATTERNS):
        return False

    def natural_token(token: str) -> bool:
        token = token.strip("()[]{}:;,\"'“”")
        letters = "".join(character for character in token if character.isalpha())
        if re.search(r"[\uac00-\ud7a3]", letters):
            return True
        if re.search(r"[\u3400-\u9fff]{2,}", letters):
            return True
        if token.lower() in {"dry", "wet"}:
            return True
        if token.lower() in {"max", "min"}:
            return False
        if re.search(r"[\u0370-\u03ff]", token):
            return False
        if len(letters) <= 2:
            return False
        if letters.isupper():
            return False
        if (
            any(character.isdigit() for character in token)
            or re.search(r"[a-z][A-Z]", token)
            or sum(character.isupper() for character in letters) >= 2
            or re.search(r"[%._/·]", token)
        ):
            return False
        return any(character.islower() for character in letters)

    return any(natural_token(token) for token in value.split())


def upright_table_words(
    words: Iterable[Sequence[Any]], blocks: Iterable[Mapping[str, Any]]
) -> list[Sequence[Any]]:
    """Exclude rotated text such as diagonal watermarks from table cells.

    PyMuPDF numbers word blocks across text blocks only, while its dictionary
    includes image blocks too. Rebuild that text-only index so a rotated line
    cannot paint its large axis-aligned word box across otherwise separate
    table cells.
    """
    upright_lines: dict[tuple[int, int], tuple[float, float, float, float] | None] = {}
    text_block_index = 0
    for block in blocks:
        if int(block.get("type", 0)) != 0:
            continue
        for line_index, line in enumerate(block.get("lines", ())):
            direction = line.get("dir", (1.0, 0.0))
            if (
                isinstance(direction, (tuple, list))
                and len(direction) == 2
                and abs(float(direction[0]) - 1.0) <= 0.01
                and abs(float(direction[1])) <= 0.01
            ):
                line_bounds = _rect(line.get("bbox", ()))
                upright_lines[(text_block_index, line_index)] = line_bounds
        text_block_index += 1
    result: list[Sequence[Any]] = []
    for word in words:
        if len(word) < 7:
            result.append(word)
            continue
        line_bounds = upright_lines.get((int(word[5]), int(word[6])))
        if (int(word[5]), int(word[6])) not in upright_lines:
            continue
        word_bounds = _rect(word)
        if line_bounds is not None and word_bounds is not None:
            lx0, ly0, lx1, ly1 = line_bounds
            wx0, wy0, wx1, wy1 = word_bounds
            if wx0 < lx0 - 2 or wy0 < ly0 - 2 or wx1 > lx1 + 2 or wy1 > ly1 + 2:
                continue
        result.append(word)
    return result


def cluster_table_words(
    words: Iterable[Sequence[Any]],
    cell: Sequence[Any],
) -> list[TableTextCluster]:
    """Split a visually merged table cell into prose and code-like x clusters.

    Some PDFs omit the rule between a description and its abbreviation column,
    so PyMuPDF returns both as one cell. Normal word spaces are small; the jump
    to a right-aligned code is much larger. X-overlap across wrapped lines keeps
    multi-line descriptions together.
    """
    bounds = _rect(cell)
    items = [word for word in words if _rect(word) is not None and len(word) >= 5]
    if bounds is None or not items:
        return []

    heights = [max(0.1, float(word[3]) - float(word[1])) for word in items]
    gap_limit = max(3.0, median(heights) * 0.6)
    groups: list[list[Sequence[Any]]] = []
    group_bounds: list[list[float]] = []
    for word in sorted(items, key=lambda item: (float(item[0]), float(item[1]))):
        x0, y0, x1, y1 = (float(value) for value in word[:4])
        matches = [
            index
            for index, current in enumerate(group_bounds)
            if x0 <= current[2] + gap_limit and x1 >= current[0] - gap_limit
        ]
        if not matches:
            groups.append([word])
            group_bounds.append([x0, y0, x1, y1])
            continue
        target = matches[0]
        groups[target].append(word)
        current = group_bounds[target]
        current[:] = [
            min(current[0], x0),
            min(current[1], y0),
            max(current[2], x1),
            max(current[3], y1),
        ]
        for extra in reversed(matches[1:]):
            groups[target].extend(groups.pop(extra))
            other = group_bounds.pop(extra)
            current[:] = [
                min(current[0], other[0]),
                min(current[1], other[1]),
                max(current[2], other[2]),
                max(current[3], other[3]),
            ]

    ordered = sorted(zip(groups, group_bounds), key=lambda item: item[1][0])
    cx0, cy0, cx1, cy1 = bounds
    result: list[TableTextCluster] = []
    for index, (group, group_box) in enumerate(ordered):
        left = cx0 if index == 0 else (ordered[index - 1][1][2] + group_box[0]) / 2
        right = cx1 if index + 1 == len(ordered) else (
            group_box[2] + ordered[index + 1][1][0]
        ) / 2
        text = " ".join(
            str(word[4])
            for word in sorted(
                group,
                key=lambda item: (
                    int(item[5]) if len(item) > 5 else 0,
                    int(item[6]) if len(item) > 6 else 0,
                    int(item[7]) if len(item) > 7 else 0,
                ),
            )
        )
        result.append(
            TableTextCluster(
                (max(cx0, left), cy0, min(cx1, right), cy1),
                text,
                tuple(group),
            )
        )
    return result


def page_has_image(blocks: Iterable[Mapping[str, Any]]) -> bool:
    """Return whether the page draws any raster image at all.

    is_scanned_page asks whether one image covers half the page, which is the
    right question for backing rectangles but the wrong one for deciding a page
    held nothing to translate: scanners routinely emit a page as dozens of
    tiles, none of them large on its own. A page with no text and no image is
    simply blank, and saying so would be noise.
    """
    return any(block.get("type") == 1 for block in blocks)


def is_scanned_page(blocks: Iterable[Mapping[str, Any]], page_area: float) -> bool:
    """Return whether a rendered image covers more than half of the page."""
    if page_area <= 0:
        return False
    for block in blocks:
        if block.get("type") != 1:
            continue
        bbox = block.get("bbox")
        if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = (float(value) for value in bbox)
        if max(0.0, x1 - x0) * max(0.0, y1 - y0) > page_area * 0.5:
            return True
    return False


def classify_preserved_page(page_text: str) -> PreservationDecision | None:
    """Classify pages whose number-heavy structure must not be reflowed."""
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    if not lines:
        return None

    toc_score = 0
    standalone_nums = 0
    spaced_page_nums = 0
    emspace_page_nums = 0
    for line in lines:
        if (
            re.search(r"\.{5,}", line)
            or re.search(r"(\.\s){4,}", line)
            or re.search(r"[\x08\ufffd\u2500-\u257f]{3,}", line)
        ):
            toc_score += 3
        elif re.search(r"[\x08\ufffd\u2500-\u257f]+\s*\d{1,4}\s*$", line):
            toc_score += 2
        elif re.search(r"\S\s{5,}\d{1,4}\s*$", line):
            spaced_page_nums += 1
        elif re.fullmatch(r"\d{1,4}", line):
            standalone_nums += 1

        if re.search(r"[\u2002\u2003]+\s*\d{1,4}\s*$", line) or re.search(
            r"[\u2002\u2003]+\s*[ivxlcdm]+\s*$", line, re.IGNORECASE
        ):
            emspace_page_nums += 1

    has_contents_header = any(
        re.fullmatch(r"(table\s+of\s+)?contents?", line, re.IGNORECASE)
        for line in lines[:5]
    )
    if has_contents_header:
        toc_score += 5
    if spaced_page_nums >= 5:
        toc_score += spaced_page_nums
    if emspace_page_nums >= 5:
        toc_score += emspace_page_nums
    if standalone_nums >= 8 and toc_score > 0:
        toc_score += standalone_nums
    if len(lines) >= 15 and standalone_nums >= 10 and standalone_nums / len(lines) > 0.3:
        toc_score += standalone_nums
    if len(lines) >= 15:
        lines_ending_num = sum(
            1 for line in lines if re.search(r"\S\s+\d{1,4}\s*$", line)
        )
        if lines_ending_num / len(lines) > 0.8:
            toc_score += lines_ending_num
    if toc_score >= 8:
        return PreservationDecision("TOC", f"score={toc_score}")

    index_comma_numbers = sum(
        1 for line in lines if re.search(r",\s*\d{1,4}", line)
    )
    if len(lines) >= 20 and index_comma_numbers / len(lines) > 0.4:
        return PreservationDecision(
            "INDEX", f"comma_num={index_comma_numbers}/{len(lines)}"
        )
    if re.fullmatch(r"index", lines[0], re.IGNORECASE):
        return PreservationDecision("INDEX", "header")

    has_nomenclature_header = any(
        re.fullmatch(
            r"(nomenclature|list\s+of\s+symbols|symbols?\s+and\s+abbreviations?|"
            r"glossary|notation)s?",
            line,
            re.IGNORECASE,
        )
        for line in lines[:5]
    )
    if has_nomenclature_header and len(lines) >= 10:
        symbol_definition_pairs = sum(
            1
            for index in range(len(lines) - 1)
            if len(lines[index]) <= 15
            and len(lines[index + 1]) > 5
            and not lines[index].isdigit()
        )
        if symbol_definition_pairs / len(lines) > 0.3:
            return PreservationDecision(
                "NOMENCLATURE",
                f"pairs={symbol_definition_pairs}/{len(lines)}",
            )

    has_reference_header = any(
        re.fullmatch(
            r"[\xad]?(references?|bibliography|suggested\s+reading|further\s+reading|"
            r"works?\s+cited)",
            line,
            re.IGNORECASE,
        )
        for line in lines[:10]
    )
    numbered_refs = sum(1 for line in lines if re.match(r"^\d{1,3}\.\s", line))
    author_year_refs = sum(
        1 for line in lines if re.match(r"^[A-Z][a-z]+,?\s.*\(\d{4}\)", line)
    )
    bracketed_refs = sum(1 for line in lines if re.match(r"^\[\d{1,3}\]", line))
    year_parentheses = sum(1 for line in lines if re.search(r"\(\d{4}\)", line))
    isbn_doi = sum(
        1
        for line in lines
        if re.search(r"ISBN|ISSN|doi\.org|https?://", line, re.IGNORECASE)
    )
    all_refs = numbered_refs + author_year_refs + bracketed_refs
    reference_signals = all_refs + year_parentheses + isbn_doi
    if has_reference_header and reference_signals >= 5:
        return PreservationDecision(
            "REFERENCES",
            f"header, refs={all_refs}, years={year_parentheses}, isbn_doi={isbn_doi}",
        )
    if len(lines) >= 10 and all_refs >= 5 and year_parentheses + isbn_doi >= 3:
        return PreservationDecision(
            "REFERENCES",
            f"refs={all_refs}, years={year_parentheses}, isbn_doi={isbn_doi}",
        )
    return None
