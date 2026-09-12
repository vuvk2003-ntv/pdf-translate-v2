import concurrent.futures
import logging
import math
import re
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import Enum, IntEnum
from string import Template
from typing import Dict

import numpy as np
from pdfminer.converter import PDFConverter
from pdfminer.layout import LTChar, LTFigure, LTLine, LTPage
from pdfminer.pdffont import PDFCIDFont, PDFUnicodeNotDefined
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.utils import apply_matrix_pt, mult_matrix
from pymupdf import Font
from tenacity import retry, stop_after_attempt, wait_exponential

from pdf2zh.integrity import (
    APPROVED_PRESERVE_REASONS,
    AllowedPreserveSpan,
    EligibleSourceSpan,
    IntegrityFailure,
    Occurrence,
    OccurrenceLedger,
    OccurrenceStatus,
    TranslationIntegrityError,
    approved_spans_for_translation,
    stable_logical_unit_id,
    stable_occurrence_id,
    stable_source_span_id,
)
from pdf2zh.invariants import (
    TechnicalInvariantError,
    VerifiedProperNameError,
    is_context_sensitive_korean,
    is_executable_code_line,
    is_standalone_verified_proper_name,
)
from pdf2zh.performance import profile_from_env
from pdf2zh.routing import RoutingMetadata, route_logical_unit
from pdf2zh.rules import (
    COMMON_STANDALONE_TECHNICAL_TERM_PATTERN,
    is_bullet_character,
    is_formula_font,
    line_height_for_language,
    min_line_height_for_language,
)
from pdf2zh.terminology import TerminologyConsistencyError
from pdf2zh.translator import (
    ENGINES,
    BaseTranslator,
    FormulaPlaceholderError,
    encode_formula_placeholders,
    restore_formula_placeholders,
    segment_identifier,
)

log = logging.getLogger(__name__)
STYLE_TAG_PATTERN = re.compile(r"<(/?)s([123])>", re.IGNORECASE)
PLACEHOLDER_ONLY_PATTERN = re.compile(r"\{v\d+\}")
IDENTITY_ORIENTATION = (1.0, 0.0, 0.0, 1.0)
BASE14_STYLE_FONTS = {0: "tiro", 1: "tibo", 2: "tiit", 3: "tibi"}
TECHNICAL_ONLY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"0x[0-9A-Fa-f]+|(?:ZR|D|M|X|Y|R|P|Z|B|W|L|F|V)\d+|"
    r"[A-Z]{1,3}\[\d+\]|(?:AL|ERR|ER|E)[.-]\d+|"
    r"(?:TCP|UDP)/\d{1,5}|"
    r"(?-i:(?=[A-Z0-9][A-Z0-9._/-]{3,})(?=[A-Z0-9._/-]*[A-Z])"
    r"(?=[A-Z0-9._/-]*\d)[A-Z0-9]+(?:[._/-][A-Z0-9]+)*[a-z]?)|"
    r"ISO\s+\d+(?:-\d+)*(?::\d{4})?|"
    r"[-+]?\d+(?:\.\d+)?\s*(?:W|kW|V|A|N|m|mA|MPa|kPa|Pa|mm|cm|"
    r"mm/s|m/s|rpm|kHz|Hz|ms|sec|s|µs|μs|°C|N·m|N\.m|Ω|%)|"
    r"(?:VAC|VDC|N|m|mA|MPa|kPa|Pa|mm|cm|mm/s|m/s|rpm|kHz|Hz|ms|"
    r"sec|s|µs|μs|°C|N·m|N\.m|Ω|%)|"
    r"PLC|I/O|JOG|Servo\s+ON|FOB|CAD|ON|OFF|OK|NG|O|X"
    r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
class TextStyle(IntEnum):
    REGULAR = 0
    BOLD = 1
    ITALIC = 2
    BOLD_ITALIC = 3


def text_style_from_font(font_name: str | bytes) -> TextStyle:
    """Infer PDF text emphasis from common PostScript font face names.

    The Adobe Pro families abbreviate the slanted face, so `MinionPro-It` and
    `MyriadPro-BoldIt` have to be read as italic even though they never spell
    the word out. Only a trailing abbreviation counts, or ordinary words that
    happen to end in those letters would be matched.
    """
    if isinstance(font_name, bytes):
        font_name = font_name.decode("utf-8", errors="ignore")
    face = font_name.split("+")[-1]
    bold = re.search(r"bold|semibold|demi|black|heavy", face, re.IGNORECASE)
    italic = re.search(r"italic|oblique|slanted", face, re.IGNORECASE) or re.search(
        r"(?:^|[^A-Za-z])(?:It|Ital)$|[a-z](?:It|Ital)$", face
    )
    if bold and italic:
        return TextStyle.BOLD_ITALIC
    if bold:
        return TextStyle.BOLD
    if italic:
        return TextStyle.ITALIC
    return TextStyle.REGULAR


# PDF 32000-1 table 123: the font descriptor's own account of its face.
FLAG_ITALIC = 1 << 6
FLAG_FORCE_BOLD = 1 << 18


def text_style_from_descriptor(descriptor: Dict | None) -> TextStyle | None:
    """Read emphasis from the font descriptor, or None if it does not say.

    The embedded font declares what it is, so this beats guessing from a name
    that a producer is free to abbreviate or subset-prefix. BabelDOC reads the
    same properties out of the embedded font program; the descriptor carries
    them without unpacking the font, and is what pdfminer already parsed.
    """
    if not descriptor:
        return None
    try:
        flags = int(descriptor.get("Flags") or 0)
    except (TypeError, ValueError):
        flags = 0
    try:
        angle = float(descriptor.get("ItalicAngle") or 0)
    except (TypeError, ValueError):
        angle = 0.0
    try:
        weight = float(descriptor.get("FontWeight") or 0)
    except (TypeError, ValueError):
        weight = 0.0
    if not flags and not angle and not weight:
        return None
    italic = bool(flags & FLAG_ITALIC) or angle != 0.0
    bold = bool(flags & FLAG_FORCE_BOLD) or weight >= 600
    if bold and italic:
        return TextStyle.BOLD_ITALIC
    if bold:
        return TextStyle.BOLD
    if italic:
        return TextStyle.ITALIC
    return TextStyle.REGULAR


def text_style_of(character: LTChar) -> TextStyle:
    """Emphasis for one glyph: what the font declares, else what it is called."""
    descriptor = getattr(getattr(character, "font", None), "descriptor", None)
    style = text_style_from_descriptor(descriptor)
    if style is not None:
        return style
    return text_style_from_font(character.fontname)


def text_orientation(matrix) -> tuple[float, float, float, float] | None:
    """Return the nearest quarter-turn from the glyph baseline direction.

    Some PDF producers use a negative font size together with a reflected text
    matrix (for example ``1 0 0 -1``) to draw ordinary upright text.  Looking at
    all four matrix components mistakes that implementation detail for an
    unsupported orientation and causes the glyphs to be replayed upside down.
    The first matrix column is the logical baseline, so it is sufficient for
    classifying the four supported reading directions.
    """
    a, b, c, d = (float(value) for value in matrix[:4])
    x_scale = math.hypot(a, b)
    if x_scale <= 1e-6 or math.hypot(c, d) <= 1e-6:
        return None
    baseline = (a / x_scale, b / x_scale)
    candidates = (
        IDENTITY_ORIENTATION,
        (0.0, 1.0, -1.0, 0.0),
        (-1.0, 0.0, 0.0, -1.0),
        (0.0, -1.0, 1.0, 0.0),
    )
    distances = [
        (baseline[0] - candidate[0]) ** 2
        + (baseline[1] - candidate[1]) ** 2
        for candidate in candidates
    ]
    best = min(range(len(candidates)), key=distances.__getitem__)
    return candidates[best] if distances[best] <= 0.04 else None


def normalised_text_matrix(matrix) -> tuple[float, float, float, float]:
    a, b, c, d = (float(value) for value in matrix[:4])
    x_scale = max(math.hypot(a, b), 1e-6)
    y_scale = max(math.hypot(c, d), 1e-6)
    if a * d - b * c < 0:
        # A reflected text matrix is normally paired with a negative font size.
        # Preserve its baseline rotation but remove the technical reflection.
        ux, uy = a / x_scale, b / x_scale
        return (ux, uy, -uy, ux)
    return (a / x_scale, b / x_scale, c / y_scale, d / y_scale)


def is_outside_page(
    character: LTChar, page: tuple[float, float, float, float] | None
) -> bool:
    """Whether a glyph falls entirely off the paper, and so is never seen.

    Some producers park stray glyphs above the page between the styled runs of
    a paragraph - a 23rd-edition physiology textbook emits a space at y=823 on a
    792-point page between every bold term and the prose around it. Those
    glyphs land in no layout region, so they take the catch-all class, and the
    class change breaks the real paragraph in two at each of them. Every bold
    term then became its own paragraph, reflowed inside its own width, and the
    fragments printed over each other once the translation stopped being the
    length of the English.

    A glyph that only overlaps the edge is kept: it is clipped, not invisible.
    """
    if page is None:
        return False
    x0, y0, x1, y1 = page
    return (
        character.x1 <= x0
        or character.x0 >= x1
        or character.y1 <= y0
        or character.y0 >= y1
    )


def output_font_lacks_glyph(text: str, font: Font | None) -> bool:
    """Whether the prose font has no outline at all for this character.

    `raw_string` writes `font.has_glyph(ord(c))` straight into an Identity-H
    font, and a missing glyph is glyph 0. The character then draws as .notdef -
    a blank or a hollow box - and extracts as U+0000, so it is lost twice over.
    A physical chemistry textbook lost the arrowhead and ballot-box markers that
    open its list items this way, and a physiology textbook lost the apostrophe
    in its own title.

    Keeping the source glyph instead is what the bullet rule already does. It
    is also the only thing that can be right in general: the character exists in
    the source document, drawn by a font that does have it.
    """
    if font is None or not text:
        return False
    character = text[0]
    if character.isspace():
        return False
    try:
        return font.has_glyph(ord(character)) == 0
    except Exception:
        return False


def is_translatable_source_script_character(text: str) -> bool:
    """Keep Korean/Chinese source letters eligible if the target font lacks them.

    The output font only needs to draw a successful target translation. Treating
    source Hangul/Han as unrenderable before translation hid the text from both
    Google and Handoff. An unresolved segment is replayed with its embedded
    source font later in the converter.
    """
    return any(
        "\u3400" <= character <= "\u9fff"
        or "\uac00" <= character <= "\ud7a3"
        for character in text
    )


# A line that stops well short of its column is the last line of a paragraph.
# Justified prose reaches the right edge on every line but the last, and even
# ragged-right prose rarely gives up a quarter of the measure mid-paragraph.
PARAGRAPH_END_RATIO = 0.75


def line_ends_paragraph(
    line_end: float,
    bounds: tuple[float, float, float, float] | None,
    ratio: float = PARAGRAPH_END_RATIO,
) -> bool:
    """Whether a line finishing here is the last line of its paragraph.

    Without this a whole column of prose is one paragraph, because the layout
    model returns the column as a single region and nothing else in the source
    says where one paragraph stops. The translation then reflows as one
    continuous block: first-line indents disappear, the blank line between
    paragraphs goes, and short lines such as the four answers to a
    multiple-choice question run together on one line.

    BabelDOC splits on the same signal, comparing against the median line
    width of the page. The column the layout model already found is a steadier
    reference: it is the measure those lines were set to.
    """
    if bounds is None:
        return False
    x0, _y0, x1, _y1 = bounds
    width = x1 - x0
    if width <= 0:
        return False
    return line_end < x0 + width * ratio


def layout_class_for_bounds(
    layout: np.ndarray,
    bounds: Sequence[float],
) -> int:
    """Read a glyph's class from its visual interior instead of its origin.

    Recovered regions are painted from source-line rectangles.  A glyph origin
    may sit exactly on an integer edge, and a later metadata carve can touch
    one corner without owning the glyph.  The center is the stable ownership
    point; nearby interior samples recover from a one-pixel hole while never
    borrowing a class that merely touches the outside of the glyph.
    """
    x0, y0, x1, y1 = (float(value) for value in bounds[:4])
    height, width = layout.shape

    def value_at(x: float, y: float) -> int:
        column = int(np.clip(math.floor(x), 0, width - 1))
        row = int(np.clip(math.floor(y), 0, height - 1))
        return int(layout[row, column])

    centre = value_at((x0 + x1) / 2, (y0 + y1) / 2)
    if centre:
        return centre
    samples = (
        value_at(x0 * 0.75 + x1 * 0.25, (y0 + y1) / 2),
        value_at(x0 * 0.25 + x1 * 0.75, (y0 + y1) / 2),
        value_at((x0 + x1) / 2, y0 * 0.75 + y1 * 0.25),
        value_at((x0 + x1) / 2, y0 * 0.25 + y1 * 0.75),
    )
    nonzero = [value for value in samples if value]
    if nonzero:
        return Counter(nonzero).most_common(1)[0][0]
    return 0


# A subscript or a superscript is a character or two. Anything this long that
# reads as words is body text that merely happens to be set smaller than what
# opened its paragraph.
MINIMUM_PROSE_RUN = 12


def run_is_prose(text: str) -> bool:
    """Whether a run held back for being small is really body text.

    A caption whose bold label is set larger than its body makes the whole body
    look like a subscript against it, so an entire figure caption was preserved
    as source glyphs and never translated. Size alone cannot tell the two
    apart, but length and shape can: a run this long, made of words, is prose.

    Runs preserved for any other reason never reach this test. A formula font,
    a protected layout region, a character the output font cannot draw and a
    rotated baseline all still keep their source glyphs however long they run.
    """
    visible = text.strip()
    if len(visible) < MINIMUM_PROSE_RUN:
        return False
    return re.search(r"[a-z]{3,}", visible) is not None


def paragraph_width_budget(x: float, x0: float, x1: float, lines: int) -> float:
    """Return usable width while accounting for a first-line indentation."""
    if lines <= 0 or x1 <= x0:
        return 0.0
    first_line = max(0.0, x1 - max(x, x0))
    return first_line + max(0, lines - 1) * (x1 - x0)


# Setting a colour leaves it in force for everything drawn after it, so a run
# that states no colour of its own takes the colour of whatever ran before it -
# a gold list bullet repainted the whole item that followed it. Every run
# states its colour, and black is stated explicitly when the source never set
# one, which is the colour a content stream starts in anyway.
DEFAULT_COLOUR_INSTRUCTION = "0 g 0 G"
FILL_TO_STROKE_OPERATORS = {
    "g": "G",
    "rg": "RG",
    "k": "K",
    "sc": "SC",
    "scn": "SCN",
    "cs": "CS",
}


def stroke_colour_from_fill(instruction: str) -> str:
    """Mirror a fill colour onto the stroking colour.

    Synthetic bold thickens a glyph by stroking its outline. That stroke is our
    own device rather than something the source drew, so it has to use the
    colour the glyph is filled with, or a red heading comes out red with a
    black outline. Only the operator tokens change; operands and colourspace
    names pass through untouched.
    """
    return " ".join(
        FILL_TO_STROKE_OPERATORS.get(token, token) for token in instruction.split()
    )


def styled_text_matrix(
    orientation: tuple[float, float, float, float],
    style: int,
    synthetic: bool,
) -> tuple[float, float, float, float]:
    """Compose an italic shear with the source orientation when needed."""
    a, b, c, d = orientation
    if synthetic and style in (TextStyle.ITALIC, TextStyle.BOLD_ITALIC):
        shear = 0.2
        c, d = c + a * shear, d + b * shear
    return (a, b, c, d)


def uses_synthetic_bold(style: int, synthetic: bool) -> bool:
    return synthetic and style in (TextStyle.BOLD, TextStyle.BOLD_ITALIC)


def matrix_font_size(matrix) -> float:
    """Recover font size from a text matrix even when LTChar.size is advance."""
    return math.hypot(float(matrix[0]), float(matrix[1]))


def strip_style_tags(text: str) -> str:
    return STYLE_TAG_PATTERN.sub("", text)


def needs_model_translation(
    segment: str,
    terminology: Mapping[str, str] | None = None,
) -> bool:
    """Conservatively remove code/token-only work before a provider sees it."""
    if is_standalone_verified_proper_name(segment, terminology):
        return False
    visible = PLACEHOLDER_ONLY_PATTERN.sub("", strip_style_tags(segment)).strip()
    if not visible:
        return False
    if COMMON_STANDALONE_TECHNICAL_TERM_PATTERN.fullmatch(visible):
        return False
    lines = [line.strip() for line in visible.splitlines() if line.strip()]
    if lines and all(is_executable_code_line(line) for line in lines):
        return False
    remainder = TECHNICAL_ONLY_PATTERN.sub("", visible)
    remainder = re.sub(r"[\s,;:|/()\[\]{}.+\-=×±≤≥]+", "", remainder)
    if not remainder:
        return False
    return any(character.isalpha() for character in remainder)


def is_safe_to_deduplicate(segment: str) -> bool:
    """Reuse only substantial exact text, avoiding ambiguous short labels."""
    visible = PLACEHOLDER_ONLY_PATTERN.sub("", strip_style_tags(segment)).strip()
    words = re.findall(r"[A-Za-z\u3400-\u9fff\uac00-\ud7a3]+", visible)
    return len(visible) >= 24 or len(words) >= 4 or bool(
        len(visible) >= 12 and re.search(r"[.!?:。！？]\s*$", visible)
    )


def should_share_translation(segment: str, service: str) -> bool:
    """Keep existing sharing except for context-sensitive Korean Handoff units."""
    return is_safe_to_deduplicate(segment) and not (
        service == "handoff" and is_context_sensitive_korean(segment)
    )


def bounded_korean_context(
    segments: Sequence[str],
    paragraphs: Sequence[object],
    index: int,
    *,
    max_characters: int = 300,
) -> dict[str, str] | None:
    """Return at most one safe same-region neighbor as untrusted Handoff context."""
    source = segments[index]
    current = paragraphs[index]
    if not is_context_sensitive_korean(source) or current.layout_bound is not None:
        return None
    for neighbor in (index - 1, index + 1):
        if not 0 <= neighbor < len(segments):
            continue
        candidate_paragraph = paragraphs[neighbor]
        candidate = strip_style_tags(segments[neighbor]).strip()
        if (
            candidate_paragraph.layout_bound is not None
            or candidate_paragraph.cls != current.cls
            or not candidate
            or candidate == strip_style_tags(source).strip()
            or len(candidate) > max_characters
        ):
            continue
        return {
            "type": "untrusted_context",
            "kind": "adjacent_segment",
            "text": candidate,
        }
    return None


def is_translatable_segment(
    segment: str,
    preserved: Iterable[str],
    terminology: Mapping[str, str] | None = None,
) -> bool:
    """Whether the translator should actually be asked for this segment.

    Preserved runs, blank space, and bare formula placeholders are copied
    through untouched. They are not failures and they are not work, so a page
    made only of those is a page with nothing to translate rather than a page
    the translator gave up on.
    """
    if segment in preserved:
        return False
    visible = strip_style_tags(segment).strip()
    return (
        bool(visible)
        and PLACEHOLDER_ONLY_PATTERN.fullmatch(visible) is None
        and needs_model_translation(segment, terminology)
    )


def approved_preserve_reason(
    segment: str,
    *,
    explicitly_preserved: bool,
    terminology: Mapping[str, str] | None = None,
) -> str | None:
    """Explain an existing filter decision using only approved policy categories."""
    if explicitly_preserved:
        return "immutable_metadata"
    if is_standalone_verified_proper_name(segment, terminology):
        return "reviewed_proper_name"
    visible = strip_style_tags(segment).strip()
    if not visible:
        return None
    if PLACEHOLDER_ONLY_PATTERN.fullmatch(visible):
        return "technical_invariant"
    if not needs_model_translation(segment, terminology):
        if any(
            term and target == term and term in visible
            for term, target in (terminology or {}).items()
        ):
            return "explicit_technical_term"
        return "technical_invariant"
    return None


def styled_character_text(characters: list[LTChar]) -> str:
    """Serialize source character styles into translator-safe inline markers."""
    parts: list[str] = []
    active = TextStyle.REGULAR
    for character in characters:
        style = text_style_of(character)
        if style != active:
            if active:
                parts.append(f"</s{int(active)}>")
            if style:
                parts.append(f"<s{int(style)}>")
            active = style
        parts.append(character.get_text())
    if active:
        parts.append(f"</s{int(active)}>")
    return "".join(parts)


def size_should_follow_body(
    paragraph_size: float, character_size: float, visible_length: int, character: str
) -> bool:
    """Whether a paragraph should take its font size from this character.

    A list item opens with an oversized bullet and a tab set in the bullet's
    font, so the paragraph inherited 14pt for 8.75pt body text. Counting that
    tab made the old rule believe the paragraph had already started, and
    against 14pt the body then satisfied the subscript test: whole list items
    were preserved as formulas, left untranslated, and redrawn at the bullet's
    size straight over their neighbours. Only characters that draw ink count as
    the paragraph having started.
    """
    if character == " ":
        return False
    return character_size > paragraph_size or visible_length <= 1


def preferred_translation(text: str, language: str) -> str | None:
    """Return stable Vietnamese terminology for the three rotated table headers."""
    if language.lower() != "vi":
        return None
    visible = strip_style_tags(text).strip()
    replacement = {
        "Designation": "Tên gọi",
        "Abbreviation": "Viết tắt",
        "Unit": "Đơn vị",
    }.get(visible)
    if replacement is None:
        return None
    leading = re.match(r"^\s*", text).group(0)
    trailing = re.search(r"\s*$", text).group(0)
    style = re.fullmatch(r"\s*(<s[123]>).*?(</s[123]>)\s*", text, re.DOTALL)
    if style:
        return f"{leading}{style.group(1)}{replacement}{style.group(2)}{trailing}"
    return f"{leading}{replacement}{trailing}"


def should_translate_rotated_text(text: str) -> bool:
    """Keep rotated document-control identifiers such as reference numbers."""
    visible = strip_style_tags(text).strip()
    if re.search(r"\d", visible) and re.search(
        r"\b(ref|no|rev|code|version)\b", visible, re.IGNORECASE
    ):
        return False
    return True


def iter_layout_items(container):
    """Yield page items in content-stream order and retain figure boundaries."""
    container_id = id(container)
    for child in container:
        if isinstance(child, LTFigure):
            yield from iter_layout_items(child)
            continue
        child.source_container_id = container_id
        yield child


class PDFConverterEx(PDFConverter):
    def __init__(
        self,
        rsrcmgr: PDFResourceManager,
    ) -> None:
        PDFConverter.__init__(self, rsrcmgr, None, "utf-8", 1, None)
        self.page_clip: tuple[float, float, float, float] | None = None
        # Form XObjects may own fonts that are absent from the page resource
        # dictionary. Page-level atomic fallback needs stable aliases for those
        # fonts so their original glyph codes can be replayed after the forms'
        # text operators have been removed.
        self.source_font_names: dict[object, str] = {}
        self.source_fonts_by_name: dict[str, object] = {}
        self.source_font_xrefs_by_page: dict[int, dict[str, int]] = {}
        # One entry per piece of colour state, and a saved stack for q/Q.
        self.graphic_operators: dict[str, str] = {}
        self.graphic_stack: list[dict[str, str]] = []

    def record_graphic_operator(
        self, slot: str, text: str, self_contained: bool
    ) -> None:
        self.graphic_operators[slot] = text
        if self_contained:
            # `rg` and friends name their own space, so a space selected
            # earlier must not be replayed in front of them.
            self.graphic_operators.pop(slot.replace("_colour", "_space"), None)
        elif slot.endswith("_space"):
            # A new space resets its colour; the components that follow will
            # arrive as their own operator.
            self.graphic_operators.pop(slot.replace("_space", "_colour"), None)

    def knows_colour_space(self, slot: str) -> bool:
        return slot in self.graphic_operators

    def forget_colour(self, slot: str) -> None:
        """Drop a colour that cannot be replayed, so the run falls back to black."""
        self.graphic_operators.pop(slot, None)
        self.graphic_operators.pop(slot.replace("_colour", "_space"), None)

    def push_graphic_state(self) -> None:
        self.graphic_stack.append(dict(self.graphic_operators))

    def pop_graphic_state(self) -> None:
        if self.graphic_stack:
            self.graphic_operators = self.graphic_stack.pop()

    @property
    def graphic_instruction(self) -> str:
        """The colour in force, as operators to replay before drawing text."""
        from pdf2zh.pdfinterp import COLOUR_SLOT_ORDER

        return " ".join(
            self.graphic_operators[slot]
            for slot in COLOUR_SLOT_ORDER
            if slot in self.graphic_operators
        )

    def begin_page(self, page, ctm) -> None:
        x0, y0, x1, y1 = page.cropbox
        x0, y0 = apply_matrix_pt(ctm, (x0, y0))
        x1, y1 = apply_matrix_pt(ctm, (x1, y1))
        mediabox = (0, 0, abs(x0 - x1), abs(y0 - y1))
        # A figure reports its own bbox, so the page rectangle has to be kept
        # here: it is what decides whether a glyph is on the paper at all.
        self.page_clip = mediabox
        self.graphic_operators = {}
        self.graphic_stack = []
        self.cur_item = LTPage(page.pageno, mediabox)

    def register_source_font(self, font: object, objid: int | None) -> None:
        """Record an indirect source font under a page-safe resource alias."""
        if objid is None or not isinstance(self.cur_item, (LTPage, LTFigure)):
            return
        pageid = int(self.cur_item.pageid)
        name = f"CodexSrc{objid}"
        self.source_font_names[font] = name
        self.source_fonts_by_name[name] = font
        self.source_font_xrefs_by_page.setdefault(pageid, {})[name] = objid

    def end_page(self, page):
        return self.receive_layout(self.cur_item)

    def begin_figure(self, name, bbox, matrix) -> None:
        self.push_graphic_state()
        self._stack.append(self.cur_item)
        self.cur_item = LTFigure(name, bbox, mult_matrix(matrix, self.ctm))
        self.cur_item.pageid = self._stack[-1].pageid

    def end_figure(self, _: str) -> str:
        """Buffer a Form XObject for one page-level translation pass.

        The interpreter still needs an empty replacement stream for the form so
        its source text is removed before the page-level replacement is drawn.
        Returning ``None`` serializes the word ``None`` into the PDF stream.
        """
        self.pop_graphic_state()
        fig = self.cur_item
        assert isinstance(self.cur_item, LTFigure), str(type(self.cur_item))
        self.cur_item = self._stack.pop()
        self.cur_item.add(fig)
        return ""


    def render_char(
        self,
        matrix,
        font,
        fontsize: float,
        scaling: float,
        rise: float,
        cid: int,
        ncs,
        graphicstate: PDFGraphicState,
    ) -> float:
        try:
            text = font.to_unichr(cid)
            assert isinstance(text, str), str(type(text))
        except PDFUnicodeNotDefined:
            text = self.handle_undefined_char(font, cid)
        textwidth = font.char_width(cid)
        textdisp = font.char_disp(cid)
        item = LTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            text,
            textwidth,
            textdisp,
            ncs,
            graphicstate,
        )
        self.cur_item.add(item)
        item.cid = cid
        item.font = font
        item.graphic_instruction = self.graphic_instruction
        return item.adv


class Paragraph:
    def __init__(self, y, x, x0, x1, y0, y1, size, brk, cls=-1, matrix=None):
        self.y: float = y
        self.x: float = x
        self.x0: float = x0
        self.x1: float = x1
        self.y0: float = y0
        self.y1: float = y1
        self.size: float = size
        self.brk: bool = brk
        self.cls: int = int(cls)
        self.layout_bound: tuple[float, float, float, float] | None = None
        self.source_bound: tuple[float, float, float, float] | None = None
        self.orientation = text_orientation(matrix or (1, 0, 0, 1))
        self.rotated_chars: list[LTChar] = []
        self.source_characters: list[LTChar] = []
        self.formula_ids: list[int] = []
        self.open_style = TextStyle.REGULAR
        # Replayed in front of this paragraph's runs. A translation cannot
        # carry a colour change through the translator the way it carries a
        # style marker, so the paragraph gets the colour most of its own ink is
        # drawn in; a short coloured term inside black prose then leaves the
        # prose alone instead of repainting all of it.
        self.graphic_ink: Counter[str] = Counter()
        self.text_length = 0
        # Characters that actually draw ink. Spacing inserted between source
        # glyphs must not count, or it hides how much text a paragraph holds.
        self.visible_length = 0
        self.anchor: tuple[float, float] = (x, y)

    @property
    def graphic_instruction(self) -> str:
        """The colour most of this paragraph's own ink is drawn in."""
        if not self.graphic_ink:
            return ""
        return self.graphic_ink.most_common(1)[0][0]


def text_fits_box_at_minimum_size(
    text: str,
    width: float,
    height: float,
    source_size: float,
    formula_widths: list[float],
    measure_char: Callable[[str, float], float],
) -> bool:
    """Conservatively check whether a cell translation can fit at 50% size."""
    size = source_size * 0.5
    if width <= 0 or height <= 0 or size <= 0:
        return False

    text = strip_style_tags(text)
    chunks = re.findall(r"\{\s*v[\d\s]+\}|\S+|\s+", text, re.IGNORECASE)

    def chunk_width(chunk: str) -> float:
        marker = re.fullmatch(r"\{\s*v([\d\s]+)\}", chunk, re.IGNORECASE)
        if marker:
            try:
                return formula_widths[int(marker.group(1).replace(" ", ""))]
            except (IndexError, ValueError):
                return 0.0
        if chunk.isspace():
            chunk = " "
        return sum(measure_char(character, size) for character in chunk)

    lines = 1
    current = 0.0
    for chunk in chunks:
        measured = chunk_width(chunk)
        is_formula = re.fullmatch(
            r"\{\s*v([\d\s]+)\}", chunk, re.IGNORECASE
        ) is not None
        if is_formula and measured > width:
            return False
        if chunk.isspace():
            if current:
                current += measured
            continue
        if current and current + measured > width:
            lines += 1
            current = 0.0
        if measured > width:
            extra_lines = max(0, math.ceil(measured / width) - 1)
            lines += extra_lines
            current = measured - extra_lines * width
        else:
            current += measured

    occupied_height = size + max(0, lines - 1) * size * 0.8
    return occupied_height <= height + 0.01


def _cell_wrap_plan(
    text: str,
    first_x: float,
    left_x: float,
    right_x: float,
    size: float,
    formula_widths: list[float],
    measure_styled_char: Callable[[str, int, float], float],
) -> tuple[set[int], int] | None:
    """Return the renderer's word breaks, refusing a mid-token split."""
    if min(right_x - first_x, right_x - left_x, size) <= 0:
        return None
    style = int(TextStyle.REGULAR)
    pointer = 0
    line_count = 1
    break_positions: set[int] = set()
    current_x = first_x
    last_space_pointer = -1
    last_space_x_after = current_x
    tolerance = 0.1 * size

    while pointer < len(text):
        style_tag = STYLE_TAG_PATTERN.match(text, pointer)
        if style_tag:
            closing, identifier = style_tag.groups()
            style = int(TextStyle.REGULAR) if closing else int(identifier)
            pointer = style_tag.end()
            continue
        formula = re.match(r"\{\s*v([\d\s]+)\}", text[pointer:], re.IGNORECASE)
        if formula:
            try:
                identifier = int(formula.group(1).replace(" ", ""))
                width = formula_widths[identifier]
            except (IndexError, ValueError):
                width = 0.0
            pointer += len(formula.group(0))
        else:
            character = text[pointer]
            if character == "\n":
                line_count += 1
                current_x = left_x
                last_space_pointer = -1
                last_space_x_after = left_x
                pointer += 1
                continue
            width = measure_styled_char(character, style, size)
            if character == " ":
                last_space_pointer = pointer
                last_space_x_after = current_x + width
            pointer += 1

        if current_x + width > right_x + tolerance and current_x > left_x + tolerance:
            if last_space_pointer < 0:
                return None
            break_positions.add(last_space_pointer)
            current_x = left_x + (current_x - last_space_x_after)
            last_space_pointer = -1
            last_space_x_after = left_x
            line_count += 1
        if current_x + width > right_x + tolerance:
            return None
        current_x += width
    return break_positions, line_count


def _wrapped_cell_line_count(
    text: str,
    first_width: float,
    width: float,
    size: float,
    formula_widths: list[float],
    measure_char: Callable[[str, float], float],
    measure_styled_char: Callable[[str, int, float], float] | None = None,
) -> int | None:
    """Measure word-wrapped cell text using the renderer's exact break rules."""
    styled_measure = measure_styled_char or (
        lambda character, _style, candidate: measure_char(character, candidate)
    )
    plan = _cell_wrap_plan(
        text,
        width - first_width,
        0.0,
        width,
        size,
        formula_widths,
        styled_measure,
    )
    return None if plan is None else plan[1]


def largest_fitting_cell_font_size(
    text: str,
    first_width: float,
    width: float,
    height: float,
    source_size: float,
    formula_widths: list[float],
    measure_char: Callable[[str, float], float],
    line_height: float,
    measure_styled_char: Callable[[str, int, float], float] | None = None,
) -> float | None:
    """Wrap first, then choose the largest size between 50% and source size."""
    if min(first_width, width, height, source_size, line_height) <= 0:
        return None

    def fits(candidate: float) -> bool:
        lines = _wrapped_cell_line_count(
            text,
            first_width,
            width,
            candidate,
            formula_widths,
            measure_char,
            measure_styled_char,
        )
        return lines is not None and lines * candidate * line_height <= height + 0.01

    minimum = source_size * 0.5
    if not fits(minimum):
        return None
    if fits(source_size):
        return source_size
    low, high = minimum, source_size
    for _attempt in range(12):
        candidate = (low + high) / 2
        if fits(candidate):
            low = candidate
        else:
            high = candidate
    return low


def partition_shared_cell_bounds(
    source_bounds: list[tuple[float, float, float, float]],
    cell_bound: tuple[float, float, float, float],
) -> list[tuple[float, float, float, float]]:
    """Give separate occurrences in one cell non-overlapping vertical slices."""
    if len(source_bounds) <= 1:
        return [cell_bound for _bound in source_bounds]
    ordered = sorted(enumerate(source_bounds), key=lambda item: (item[1][1] + item[1][3]) / 2)
    bands: list[list[tuple[int, tuple[float, float, float, float]]]] = []
    for item in ordered:
        centre = (item[1][1] + item[1][3]) / 2
        if bands:
            previous = bands[-1][-1][1]
            previous_centre = (previous[1] + previous[3]) / 2
            tolerance = max(item[1][3] - item[1][1], previous[3] - previous[1]) * 0.5
            if abs(centre - previous_centre) <= tolerance:
                bands[-1].append(item)
                continue
        bands.append([item])
    centres = [
        sum((bound[1] + bound[3]) / 2 for _index, bound in band) / len(band)
        for band in bands
    ]
    cuts = [(first + second) / 2 for first, second in zip(centres, centres[1:])]
    result = [cell_bound for _bound in source_bounds]
    for position, band in enumerate(bands):
        vertical_bound = (
            cell_bound[0],
            cell_bound[1] if position == 0 else cuts[position - 1],
            cell_bound[2],
            cell_bound[3] if position + 1 == len(bands) else cuts[position],
        )
        if len(band) == 1:
            result[band[0][0]] = vertical_bound
            continue
        horizontal = sorted(
            band, key=lambda item: (item[1][0] + item[1][2]) / 2
        )
        horizontal_centres = [
            (bound[0] + bound[2]) / 2 for _index, bound in horizontal
        ]
        horizontal_cuts = [
            (first + second) / 2
            for first, second in zip(horizontal_centres, horizontal_centres[1:])
        ]
        for horizontal_position, (original_index, _bound) in enumerate(horizontal):
            result[original_index] = (
                vertical_bound[0]
                if horizontal_position == 0
                else horizontal_cuts[horizontal_position - 1],
                vertical_bound[1],
                vertical_bound[2]
                if horizontal_position + 1 == len(horizontal)
                else horizontal_cuts[horizontal_position],
                vertical_bound[3],
            )
    return result


# fmt: off
class TranslateConverter(PDFConverterEx):
    def __init__(
        self,
        rsrcmgr,
        vfont: str = None,
        vchar: str = None,
        thread: int = 0,
        layout={},
        lang_in: str = "",
        lang_out: str = "",
        service: str = "",
        noto_name: str = "",
        noto: Font = None,
        envs: Dict = None,
        prompt: Template = None,
        ignore_cache: bool = False,
        layout_bounds: Dict | None = None,
        style_font_names: Dict | None = None,
        style_fonts: Dict | None = None,
        synthetic_styles: set[int] | None = None,
        class_bounds: Dict | None = None,
        logical_classes: Dict | None = None,
    ) -> None:
        super().__init__(rsrcmgr)
        self.vfont = vfont
        self.vchar = vchar
        self.thread = thread
        self.layout = layout
        # high_level fills this mapping after the converter is constructed.
        # Preserve an empty mapping by identity instead of replacing it.
        self.layout_bounds = layout_bounds if layout_bounds is not None else {}
        self.class_bounds = class_bounds if class_bounds is not None else {}
        self.logical_classes = logical_classes if logical_classes is not None else {}
        self.noto_name = noto_name
        self.noto = noto
        self.style_font_names = style_font_names or {0: noto_name}
        self.style_fonts = style_fonts or {0: noto}
        self.synthetic_styles = synthetic_styles or set()
        self.output_fonts_by_name = {
            self.style_font_names[style]: font
            for style, font in self.style_fonts.items()
        }
        self.used_output_font_names: set[str] = set()
        self.translator: BaseTranslator = None
        self.scanned_pages: set = set()
        # Segments whose retries ran out; reported as a partial translation.
        self.translation_failures: list[str] = []
        # Why each of those was left alone. The caller used to see only a count
        # and told every user their network was down, including when the real
        # reason was a line that could not be made to fit or a formula the
        # translator would have mangled.
        self.failure_reasons: Counter[str] = Counter()
        self._failure_lock = threading.Lock()
        # Segments the translator was actually asked for. Zero across a whole
        # document means there was no text to translate, not that translation
        # failed - the difference between "run OCR first" and "try again".
        self.translatable_segments: int = 0
        # Exact substantial repeats share one worker result. Ambiguous short
        # labels remain separate so context-sensitive meanings are not merged.
        self.unique_translation_units: int = 0
        # Number of logical units for which the existing tenacity policy
        # scheduled at least one additional attempt.
        self.retry_units: int = 0
        self.translation_wall_seconds = 0.0
        self.profile = profile_from_env(envs)
        # Accounting uses the converter's actual paragraph segmentation. Every
        # entry in sstk is translated, deliberately preserved, or unresolved.
        self.extracted_segments: int = 0
        self.logical_unit_char_counts: list[int] = []
        self.integrity_ledger = OccurrenceLedger()
        self._failed_occurrence_ids: set[str] = set()
        self._page_occurrence_offsets: Counter[int] = Counter()
        # Pages carrying a raster image, filled in by the caller.
        self.pages_with_images: set[int] = set()
        self.segments_by_page: Counter[int] = Counter()
        # One lookup per distinct character rather than per glyph drawn.
        self.unrenderable_characters: dict[str, bool] = {}
        # e.g. "handoff:model" -> ["handoff", "model"]; model is unused by both engines
        param = service.split(":", 1)
        service_name = param[0]
        service_model = param[1] if len(param) > 1 else None
        if not envs:
            envs = {}
        if service_name not in ENGINES:
            supported = ", ".join(sorted(ENGINES))
            raise ValueError(
                f"Unsupported translation service {service_name!r}; supported: {supported}"
            )
        self.translator = ENGINES[service_name](
            lang_in,
            lang_out,
            service_model,
            envs=envs,
            prompt=prompt,
            ignore_cache=ignore_cache,
        )

    @property
    def image_only_pages(self) -> set[int]:
        """Pages that carry an image and no text this run could translate.

        Decided once at the end, never as each page arrives: receive_layout
        runs many times for a single page - once per nested container - and
        every call but the last sees an empty stack, so a page judged on
        arrival looks empty no matter what is printed on it.
        """
        return {
            page
            for page in self.pages_with_images
            if not self.segments_by_page[page]
        }

    def record_translation_failure(
        self,
        segment: str,
        reason: str,
        *,
        occurrence_id: str | None = None,
        failure_codes: Iterable[str] = (),
    ) -> None:
        with self._failure_lock:
            if occurrence_id is not None and occurrence_id in self._failed_occurrence_ids:
                return
            if occurrence_id is not None:
                self._failed_occurrence_ids.add(occurrence_id)
            self.translation_failures.append(segment)
            self.failure_reasons[reason] += 1
        if occurrence_id is not None:
            self.integrity_ledger.mark_unresolved(
                occurrence_id, reason, failure_codes
            )
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "Leaving segment %s in source language (%s): %r",
                segment_identifier(segment),
                reason,
                strip_style_tags(segment)[:200],
            )
        else:
            log.warning(
                "Leaving segment %s in source language (%s)",
                segment_identifier(segment),
                reason,
            )

    def receive_layout(self, ltpage: LTPage):
        extraction_token = self.profile.start("extract_seconds")
        sstk: list[str] = []
        pstk: list[Paragraph] = []
        vbkt: int = 0
        vstk: list[LTChar] = []
        vlstk: list[LTLine] = []
        # Whether everything held back so far was held back only for being
        # smaller than the text that opened its paragraph.
        vstk_size_only: bool = True
        vfix: float = 0
        var: list[list[LTChar]] = []
        varl: list[list[LTLine]] = []
        varf: list[float] = []
        vlen: list[float] = []
        lstk: list[LTLine] = []
        xt: LTChar = None
        xt_cls: int = -1
        xt_container: int | None = None
        vmax: float = ltpage.width / 4
        page_class_bounds = self.class_bounds.get(ltpage.pageid, {})
        ops: str = ""
        preserved_segments: set[str] = set()
        explicitly_preserved_indices: set[int] = set()

        def vflag(font: str, char: str):
            if isinstance(font, bytes):
                try:
                    font = font.decode('utf-8')
                except UnicodeDecodeError:
                    font = ""
            font = font.split("+")[-1]
            if re.match(r"\(cid:", char):
                return True
            if char:
                lacks = self.unrenderable_characters.get(char[0])
                if lacks is None:
                    lacks = output_font_lacks_glyph(char, self.noto)
                    self.unrenderable_characters[char[0]] = lacks
                if lacks and not is_translatable_source_script_character(char):
                    return True
            if self.vfont:
                if re.match(self.vfont, font):
                    return True
            else:
                if is_formula_font(font):
                    return True
            if self.vchar:
                if re.match(self.vchar, char):
                    return True
            else:
                if (
                    char
                    and char != " "
                    and (
                        unicodedata.category(char[0])
                        in ["Lm", "Mn", "Sk", "Sm", "Zl", "Zp", "Zs"]
                        or ord(char[0]) in range(0x370, 0x400)
                    )
                ):
                    return True
            return False

        def close_style(index: int) -> None:
            paragraph = pstk[index]
            if paragraph.open_style:
                sstk[index] += f"</s{int(paragraph.open_style)}>"
                paragraph.open_style = TextStyle.REGULAR

        def append_styled(index: int, text: str, style: TextStyle) -> None:
            paragraph = pstk[index]
            if style != paragraph.open_style:
                close_style(index)
                if style:
                    sstk[index] += f"<s{int(style)}>"
                paragraph.open_style = style
            sstk[index] += text
            paragraph.text_length += len(text)
            paragraph.visible_length += sum(
                1 for character in text if not character.isspace()
            )

        def adopt_graphic(paragraph: Paragraph, child: LTChar) -> None:
            """Count this glyph's colour towards the paragraph's own."""
            text = child.get_text()
            if text.isspace():
                return
            paragraph.graphic_ink[
                getattr(child, "graphic_instruction", "")
            ] += len(text)

        def adopt_prose_run(index: int, characters: list[LTChar]) -> None:
            """Put a run back into the paragraph as the body text it is."""
            paragraph = pstk[index]
            size = min(character.size for character in characters)
            if size < paragraph.size:
                paragraph.y -= size - paragraph.size
                paragraph.size = size
            for character in characters:
                adopt_graphic(paragraph, character)
                append_styled(index, character.get_text(), text_style_of(character))

        def append_formula(index: int, identifier: int) -> None:
            close_style(index)
            sstk[index] += f"{{v{identifier}}}"
            pstk[index].formula_ids.append(identifier)

        def new_paragraph(child: LTChar, cls: int) -> None:
            orientation = text_orientation(child.matrix)
            size = (
                matrix_font_size(child.matrix)
                if orientation not in (None, IDENTITY_ORIENTATION)
                else child.size
            )
            sstk.append("")
            pstk.append(
                Paragraph(
                    child.y0,
                    child.x0,
                    child.x0,
                    child.x0,
                    child.y0,
                    child.y1,
                    size,
                    False,
                    cls,
                    child.matrix,
                )
            )

        ############################################################
        page_logical_classes = self.logical_classes.get(ltpage.pageid, {})
        page_logical_metadata = (
            page_logical_classes if isinstance(page_logical_classes, dict) else {}
        )
        for child in iter_layout_items(ltpage):
            if isinstance(child, LTChar):
                if is_outside_page(child, self.page_clip):
                    continue
                cur_v = False
                layout = self.layout[ltpage.pageid]
                cls = layout_class_for_bounds(layout, child.bbox)
                if is_bullet_character(child.get_text(), child.fontname):
                    cls = 0
                orientation = text_orientation(child.matrix)
                # Kept apart because they are not equally final. Small text may
                # turn out to be body text set under a larger label; a formula
                # font or a protected region never does.
                smaller_than_body = (
                    cls == xt_cls
                    and pstk[-1].text_length > 1
                    and orientation == IDENTITY_ORIENTATION
                    and child.size < pstk[-1].size * 0.79
                )
                must_preserve = (
                    cls == 0
                    or vflag(child.fontname, child.get_text())
                    or orientation is None
                )
                if smaller_than_body or must_preserve:
                    cur_v = True
                if not cur_v:
                    # Keep brackets with a formula only when the formula starts
                    # the segment. In prose such as "Factor C1 (applies...)" a
                    # subscript leaves vstk populated; capturing the following
                    # bracket then strands it at its source coordinate.
                    if vstk and not pstk[-1].text_length and child.get_text() == "(":
                        cur_v = True
                        must_preserve = True
                        vbkt += 1
                    if vbkt and child.get_text() == ")":
                        cur_v = True
                        must_preserve = True
                        vbkt -= 1
                if (
                    not cur_v
                    or cls != xt_cls
                    or (pstk[-1].text_length and abs(child.x0 - xt.x0) > vmax)
                ):
                    if vstk:
                        if vstk_size_only and run_is_prose(
                            "".join(vch.get_text() for vch in vstk)
                        ):
                            adopt_prose_run(len(sstk) - 1, vstk)
                            lstk.extend(vlstk)
                        else:
                            if (
                                not cur_v
                                and cls == xt_cls
                                and child.x0 > max([vch.x0 for vch in vstk])
                            ):
                                vfix = vstk[0].y0 - child.y0
                            if not pstk[-1].text_length:
                                xt_cls = -1
                            append_formula(len(sstk) - 1, len(var))
                            var.append(vstk)
                            varl.append(vlstk)
                            varf.append(vfix)
                        vstk = []
                        vlstk = []
                        vfix = 0
                        vstk_size_only = True
                if not vstk:
                    if cls == xt_cls:
                        crosses_container = (
                            xt_container is not None
                            and child.source_container_id != xt_container
                            and int(cls) not in page_logical_classes
                        )
                        # Force paragraph break for list items: when text wraps back
                        # to left AND there's a significant vertical gap (> 1.5x font size),
                        # it's likely a new list item, not a continuation
                        if crosses_container:
                            close_style(len(sstk) - 1)
                            new_paragraph(child, cls)
                        elif (
                            int(cls) not in page_logical_classes
                            and child.x1 < xt.x0
                            and abs(child.y0 - xt.y0) > pstk[-1].size * 1.5
                        ):
                            close_style(len(sstk) - 1)
                            new_paragraph(child, cls)
                        elif child.x0 > xt.x1 + 1:
                            if pstk[-1].orientation == IDENTITY_ORIENTATION:
                                append_styled(
                                    len(sstk) - 1,
                                    " ",
                                    text_style_of(child),
                                )
                        elif child.x1 < xt.x0:
                            if pstk[-1].text_length > 1 and line_ends_paragraph(
                                xt.x1, page_class_bounds.get(int(cls))
                            ):
                                close_style(len(sstk) - 1)
                                new_paragraph(child, cls)
                            else:
                                if pstk[-1].orientation == IDENTITY_ORIENTATION:
                                    append_styled(
                                        len(sstk) - 1,
                                        " ",
                                        text_style_of(child),
                                    )
                                pstk[-1].brk = True
                    else:
                        if sstk:
                            close_style(len(sstk) - 1)
                        new_paragraph(child, cls)
                if not cur_v:
                    adopt_graphic(pstk[-1], child)
                    if pstk[-1].orientation != IDENTITY_ORIENTATION:
                        pstk[-1].rotated_chars.append(child)
                        pstk[-1].text_length += len(child.get_text())
                    else:
                        if size_should_follow_body(
                            pstk[-1].size,
                            child.size,
                            pstk[-1].visible_length,
                            child.get_text(),
                        ):
                            pstk[-1].y -= child.size - pstk[-1].size
                            pstk[-1].size = child.size
                        append_styled(
                            len(sstk) - 1,
                            child.get_text(),
                            text_style_of(child),
                        )
                else:
                    if (
                        not vstk
                        and cls == xt_cls
                        and child.x0 > xt.x0
                    ):
                        vfix = child.y0 - xt.y0
                    if must_preserve:
                        vstk_size_only = False
                    vstk.append(child)
                pstk[-1].source_characters.append(child)
                pstk[-1].x0 = min(pstk[-1].x0, child.x0)
                pstk[-1].x1 = max(pstk[-1].x1, child.x1)
                pstk[-1].y0 = min(pstk[-1].y0, child.y0)
                pstk[-1].y1 = max(pstk[-1].y1, child.y1)
                xt = child
                xt_cls = cls
                xt_container = child.source_container_id
            elif isinstance(child, LTFigure):
                pass
            elif isinstance(child, LTLine):
                layout = self.layout[ltpage.pageid]
                cls = layout_class_for_bounds(layout, child.bbox)
                if (
                    vstk
                    and cls == xt_cls
                    and (
                        child.source_container_id == xt_container
                        or int(cls) in page_logical_classes
                    )
                ):
                    vlstk.append(child)
                else:
                    lstk.append(child)
            else:
                pass
        if vstk:
            if vstk_size_only and run_is_prose(
                "".join(vch.get_text() for vch in vstk)
            ):
                adopt_prose_run(len(sstk) - 1, vstk)
                lstk.extend(vlstk)
            else:
                append_formula(len(sstk) - 1, len(var))
                var.append(vstk)
                varl.append(vlstk)
                varf.append(vfix)

        for index, paragraph in enumerate(pstk):
            close_style(index)
            if not paragraph.rotated_chars:
                continue
            a, b, _c, _d = paragraph.orientation
            ordered = sorted(
                paragraph.rotated_chars,
                key=lambda character: (
                    float(character.matrix[4]) * a
                    + float(character.matrix[5]) * b
                ),
            )
            sstk[index] = styled_character_text(ordered)
            paragraph.text_length = sum(len(char.get_text()) for char in ordered)
            first = ordered[0]
            paragraph.anchor = (float(first.matrix[4]), float(first.matrix[5]))
            paragraph.x, paragraph.y = paragraph.anchor
            paragraph.size = matrix_font_size(first.matrix)
            paragraph.brk = False
            if not should_translate_rotated_text(sstk[index]):
                preserved_segments.add(sstk[index])
                explicitly_preserved_indices.add(index)

        page_bounds = self.layout_bounds.get(ltpage.pageid, {})
        for paragraph in pstk:
            bound = page_bounds.get(paragraph.cls)
            if bound is None:
                continue
            paragraph.source_bound = (
                paragraph.x0,
                paragraph.y0,
                paragraph.x1,
                paragraph.y1,
            )
            paragraph.layout_bound = bound
        paragraphs_by_cell: dict[
            tuple[float, float, float, float], list[Paragraph]
        ] = {}
        for paragraph in pstk:
            if paragraph.layout_bound is not None:
                paragraphs_by_cell.setdefault(paragraph.layout_bound, []).append(paragraph)
        for bound, paragraphs in paragraphs_by_cell.items():
            source_bounds = [
                paragraph.source_bound or bound for paragraph in paragraphs
            ]
            safe_bounds = partition_shared_cell_bounds(source_bounds, bound)
            for paragraph, safe_bound in zip(paragraphs, safe_bounds):
                paragraph.x0, paragraph.y0, paragraph.x1, paragraph.y1 = safe_bound
                paragraph.layout_bound = safe_bound
                if paragraph.orientation == IDENTITY_ORIENTATION:
                    paragraph.x = max(paragraph.x, paragraph.x0)
        log.debug("\n==========[VSTACK]==========\n")
        for id, v in enumerate(var):
            l = max([vch.x1 for vch in v]) - v[0].x0
            log.debug(f'< {l:.1f} {v[0].x0:.1f} {v[0].y0:.1f} {v[0].cid} {v[0].fontname} {len(varl[id])} > v{id} = {"".join([ch.get_text() for ch in v])}')
            vlen.append(l)

        ############################################################
        log.debug("\n==========[SSTACK]==========\n")

        self.profile.stop(extraction_token)
        accounting_token = self.profile.start("coverage_accounting_seconds")
        source_span_ids: list[str | None] = []
        occurrence_ids: list[str | None] = []
        logical_unit_ids: list[str | None] = []
        routing_metadata: list[RoutingMetadata | None] = []
        formula_texts_by_occurrence: list[tuple[str, ...]] = []
        ledger_source_texts: list[str] = []

        def materialize_formula_text(
            text: str,
            formula_ids: Sequence[int],
            formula_texts: tuple[str, ...],
        ) -> str:
            by_identifier = {
                identifier: formula_text
                for identifier, formula_text in zip(
                    formula_ids,
                    formula_texts,
                )
            }
            return re.sub(
                r"\{\s*v([\d\s]+)\}",
                lambda match: by_identifier.get(
                    int(match.group(1).replace(" ", "")), match.group(0)
                ),
                text,
                flags=re.IGNORECASE,
            )

        for index, source in enumerate(sstk):
            formula_texts = tuple(
                (
                    "".join(character.get_text() for character in var[identifier])
                    if 0 <= identifier < len(var)
                    else ""
                )
                for identifier in pstk[index].formula_ids
            )
            formula_texts_by_occurrence.append(formula_texts)
            ledger_source_texts.append(
                materialize_formula_text(source, pstk[index].formula_ids, formula_texts)
            )

        page_occurrence_offset = self._page_occurrence_offsets[ltpage.pageid]
        self._page_occurrence_offsets[ltpage.pageid] += len(sstk)
        for index, source in enumerate(ledger_source_texts):
            if not source.strip():
                source_span_ids.append(None)
                occurrence_ids.append(None)
                logical_unit_ids.append(None)
                routing_metadata.append(None)
                continue
            ordinal = page_occurrence_offset + index
            source_span_id = stable_source_span_id(ltpage.pageid, ordinal, source)
            occurrence_id = stable_occurrence_id(ltpage.pageid, ordinal, source)
            patch_a = page_logical_metadata.get(pstk[index].cls, {})
            if not isinstance(patch_a, dict):
                patch_a = {}
            logical_unit_id = patch_a.get("logical_unit_id") or stable_logical_unit_id(
                ltpage.pageid, ordinal, source
            )
            self.integrity_ledger.add_eligible_span(
                EligibleSourceSpan(
                    source_span_id=source_span_id,
                    page=ltpage.pageid,
                    source_text=source,
                    region_id=str(pstk[index].cls),
                    logical_unit_id=logical_unit_id,
                )
            )
            source_span_ids.append(source_span_id)
            occurrence_ids.append(occurrence_id)
            logical_unit_ids.append(logical_unit_id)
            routing_metadata.append(
                RoutingMetadata(
                    was_fragment_reconstructed=bool(
                        patch_a.get("was_fragment_reconstructed", False)
                    ),
                    reconstruction_complexity=patch_a.get("reconstruction_complexity"),
                    is_callout=bool(patch_a.get("is_callout", False)),
                    is_dense_table_text=(
                        pstk[index].layout_bound is not None
                        and len(strip_style_tags(source)) >= 120
                    ),
                    logical_unit_id=logical_unit_id,
                    occurrence_id=occurrence_id,
                    source_fragment_ids=tuple(
                        patch_a.get("source_fragment_ids") or (source_span_id,)
                    ),
                )
            )

        if self.profile.enabled:
            self.profile.count("total_source_chars", sum(len(strip_style_tags(source)) for source in ledger_source_texts))
        self.profile.stop(accounting_token)

        # Google throttles a long document, so back off instead of hammering it.
        # Roughly two minutes of patience per segment, then give up rather than
        # hang the run forever the way an unbounded retry used to.
        retried_identities: set[str] = set()
        retry_identity_lock = threading.Lock()

        def record_retry(retry_state: object) -> None:
            arguments = getattr(retry_state, "args", ())
            if len(arguments) > 1:
                with retry_identity_lock:
                    retried_identities.add(str(arguments[1]))

        @retry(
            wait=wait_exponential(multiplier=1, min=1, max=60),
            stop=stop_after_attempt(8),
            before_sleep=record_retry,
            before=self.profile.retry_attempt,
            sleep=self.profile.sleep,
            reraise=True,
        )
        def request_translation(
            s: str,
            identity: str,
            context: dict[str, object] | None,
        ) -> str:
            return self.translator.translate_with_identity(s, identity, context=context)

        def translate_segment(
            s: str,
            identity: str,
            context: dict[str, object] | None,
        ) -> tuple[str, bool]:
            preferred = preferred_translation(s, self.translator.lang_out)
            if preferred is not None:
                self.translator.validate(s, preferred)
                if self.translator.name == "auto" and context is not None:
                    decision = context.get("routing_decision")
                    metadata = context.get("routing_metadata")
                    if decision is not None and metadata is not None:
                        self.translator.record_local_translation(
                            identity, decision, metadata
                        )
                return preferred, True
            encoded = encode_formula_placeholders(s)
            translated = request_translation(encoded, identity, context)
            return (
                restore_formula_placeholders(s, translated),
                self.translator.has_translation_for_identity(encoded, identity),
            )

        def worker(
            job: tuple[str, str, dict[str, str] | None]
        ) -> tuple[str, str, str | None, tuple[str, ...]]:
            s, identity, context = job
            try:
                if self.profile.plan_only:
                    preferred = preferred_translation(s, self.translator.lang_out)
                    if preferred is not None:
                        self.translator.validate(s, preferred)
                        if self.translator.name == "auto" and context is not None:
                            self.translator.record_local_translation(
                                identity, context["routing_decision"], context["routing_metadata"]
                            )
                        return preferred, "translated", None, ()
                    planned = self.translator.plan_with_identity(
                        encode_formula_placeholders(s), identity, context=context
                    )
                    if planned is not None:
                        return restore_formula_placeholders(s, planned), "translated", None, ()
                    return s, "unresolved", "PERFORMANCE_PLAN_ONLY", ()
                translated, resolved = translate_segment(s, identity, context)
                if not resolved:
                    unresolved_reason = getattr(
                        self.translator, "unresolved_reason_for_identity", None
                    )
                    reason = (
                        unresolved_reason(identity)
                        if callable(unresolved_reason)
                        else "UnresolvedSegmentError"
                    )
                    return s, "unresolved", reason, ()
                return translated, "translated", None, ()
            except BaseException as e:
                # A book is thousands of segments over tens of minutes, so one
                # dead connection must not throw the whole document away. Keep
                # the source text and let the caller report how much is missing.
                if log.isEnabledFor(logging.DEBUG):
                    log.exception(e)
                else:
                    log.error(
                        "Translation failed for segment %s (%s)",
                        segment_identifier(s),
                        type(e).__name__,
                    )
                failure_codes: tuple[str, ...] = ()
                if isinstance(e, TranslationIntegrityError):
                    failure_codes = e.failure_codes
                elif isinstance(
                    e,
                    (
                        FormulaPlaceholderError,
                        TechnicalInvariantError,
                        TerminologyConsistencyError,
                        VerifiedProperNameError,
                    ),
                ):
                    failure_codes = (IntegrityFailure.TECHNICAL_INVARIANT.value,)
                record_unresolved = getattr(
                    self.translator, "record_unresolved_identity", None
                )
                if callable(record_unresolved):
                    record_unresolved(identity, type(e).__name__)
                return s, "unresolved", type(e).__name__, failure_codes
        # Counted here rather than inside worker: worker runs on the pool, and
        # "+= 1" from several threads drops updates.
        batch_token = self.profile.start("batch_build_seconds")
        translatable = sum(
            1
            for index, source in enumerate(sstk)
            if is_translatable_segment(
                source,
                {source} if index in explicitly_preserved_indices else (),
                self.translator.terminology,
            )
        )
        self.translatable_segments += translatable
        self.extracted_segments += len(sstk)
        self.logical_unit_char_counts.extend(
            len(strip_style_tags(source)) for source in sstk
        )
        self.segments_by_page[ltpage.pageid] += translatable

        outcomes: list[tuple[str, str, str | None, tuple[str, ...]] | None] = [
            None
        ] * len(sstk)
        preserve_reasons: list[str | None] = [None] * len(sstk)
        jobs: dict[
            tuple[str, object],
            tuple[str, list[int], str, dict[str, object] | None],
        ] = {}
        for index, source in enumerate(sstk):
            explicitly_preserved = index in explicitly_preserved_indices
            is_translatable = is_translatable_segment(
                source,
                {source} if explicitly_preserved else (),
                self.translator.terminology,
            )
            metadata = routing_metadata[index]
            decision = None
            if self.translator.name == "auto" and metadata is not None:
                metadata = RoutingMetadata(
                    preserve_eligible=not is_translatable,
                    was_fragment_reconstructed=metadata.was_fragment_reconstructed,
                    reconstruction_complexity=metadata.reconstruction_complexity,
                    is_callout=metadata.is_callout,
                    is_dense_table_text=metadata.is_dense_table_text,
                    logical_unit_id=metadata.logical_unit_id,
                    occurrence_id=metadata.occurrence_id,
                    source_fragment_ids=metadata.source_fragment_ids,
                )
                routing_metadata[index] = metadata
                decision = self.profile.call("routing_seconds", route_logical_unit,
                    source,
                    metadata,
                    terminology=self.translator.terminology,
                    requested_engine="auto",
                )
            if not is_translatable:
                preserve_reasons[index] = approved_preserve_reason(
                    source,
                    explicitly_preserved=explicitly_preserved,
                    terminology=self.translator.terminology,
                )
                outcomes[index] = (source, "preserved", None, ())
                if decision is not None and metadata is not None:
                    self.translator.record_preserve_route(
                        metadata.logical_unit_id or str(index), decision, metadata
                    )
                continue
            if self.profile.enabled:
                self.profile.count("provider_bound_chars", len(strip_style_tags(source)))
            key: tuple[str, object]
            handoff_context = (
                bounded_korean_context(sstk, pstk, index)
                if self.translator.name in {"handoff", "auto"}
                else None
            )
            if self.translator.name == "auto":
                key = ("logical-unit", metadata.logical_unit_id)
                identity = metadata.logical_unit_id
                context = {
                    "routing_metadata": metadata,
                    "routing_decision": decision,
                    "handoff_context": handoff_context,
                }
            elif should_share_translation(source, self.translator.name):
                key = ("shared", source)
                identity = segment_identifier(encode_formula_placeholders(source))
                context = handoff_context
            else:
                key = ("occurrence", index)
                identity = segment_identifier(
                    f"{encode_formula_placeholders(source)}\0"
                    f"page={ltpage.pageid}\0index={index}"
                )
                context = handoff_context
            if key not in jobs:
                jobs[key] = (source, [], identity, context)
            jobs[key][1].append(index)

        self.unique_translation_units += len(jobs)
        self.profile.stop(batch_token)
        translation_started = time.perf_counter()
        translation_token = self.profile.start("translation_wall_seconds")
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.thread
        ) as executor:
            job_results = list(
                executor.map(
                    worker,
                    (
                        (source, identity, context)
                        for source, _indices, identity, context in jobs.values()
                    ),
                )
            )
        self.translation_wall_seconds += time.perf_counter() - translation_started
        self.profile.stop(translation_token)
        batch_token = self.profile.start("batch_build_seconds")
        for (_source, indices, _identity, _context), outcome in zip(jobs.values(), job_results):
            for index in indices:
                outcomes[index] = outcome
        self.retry_units += len(retried_identities)
        if any(outcome is None for outcome in outcomes):
            raise RuntimeError("translation scheduling lost an extracted segment")
        outcomes = [outcome for outcome in outcomes if outcome is not None]
        news = [translated for translated, _status, _reason, _codes in outcomes]
        if len(outcomes) != len(sstk):
            raise RuntimeError("segment accounting lost an extracted segment")
        self.profile.stop(batch_token)

        accounting_token = self.profile.start("coverage_accounting_seconds")
        for index, (target, outcome_status, unresolved_reason, failure_codes) in enumerate(
            outcomes
        ):
            source_span_id = source_span_ids[index]
            occurrence_id = occurrence_ids[index]
            logical_unit_id = logical_unit_ids[index]
            if source_span_id is None or occurrence_id is None:
                continue
            allowed_spans = approved_spans_for_translation(
                sstk[index], self.translator.terminology
            )
            allowed_spans += tuple(
                AllowedPreserveSpan(text, "technical_invariant", True)
                for text in formula_texts_by_occurrence[index]
                if text
            )
            preserve_reason = preserve_reasons[index]
            if outcome_status == "translated":
                status = OccurrenceStatus.TRANSLATED
            elif outcome_status == "unresolved":
                status = OccurrenceStatus.UNRESOLVED
            else:
                status = OccurrenceStatus.ALLOWED_PRESERVE
                if preserve_reason in APPROVED_PRESERVE_REASONS:
                    allowed_spans = (
                        AllowedPreserveSpan(
                            ledger_source_texts[index], preserve_reason, True
                        ),
                    )
            occurrence = self.integrity_ledger.record_occurrence(
                Occurrence(
                    occurrence_id=occurrence_id,
                    page=ltpage.pageid,
                    source_span_ids=(source_span_id,),
                    source_text=ledger_source_texts[index],
                    logical_unit_id=logical_unit_id,
                    status=status,
                    target_text=materialize_formula_text(
                        target,
                        pstk[index].formula_ids,
                        formula_texts_by_occurrence[index],
                    ),
                    preserve_reason=preserve_reason,
                    preserve_reason_is_approved=(
                        preserve_reason in APPROVED_PRESERVE_REASONS
                    ),
                    unresolved_reason=unresolved_reason,
                    allowed_preserve_spans=allowed_spans,
                    validation_failures=failure_codes,
                )
            )
            if occurrence.status == OccurrenceStatus.UNRESOLVED:
                reason = occurrence.unresolved_reason or "UnresolvedSegmentError"
                self.record_translation_failure(
                    sstk[index],
                    reason,
                    occurrence_id=occurrence_id,
                    failure_codes=occurrence.validation_failures,
                )
                news[index] = sstk[index]
        self.profile.stop(accounting_token)
        if self.profile.plan_only:
            return ""
        layout_token = self.profile.start("layout_seconds")

        ############################################################
        def raw_string(fcur: str, cstk: str):
            if fcur in self.output_fonts_by_name:
                font = self.output_fonts_by_name[fcur]
                return "".join(["%04x" % font.has_glyph(ord(c)) for c in cstk])
            source_font = self.fontmap.get(fcur, self.source_fonts_by_name.get(fcur))
            if isinstance(source_font, PDFCIDFont):
                return "".join(["%04x" % ord(c) for c in cstk])
            if source_font is not None:
                return "".join(["%02x" % ord(c) for c in cstk])
            raise KeyError(f"font resource {fcur!r} is unavailable")

        def output_font(
            character: str,
            style: int,
            size: float,
            *,
            record_usage: bool = True,
        ) -> tuple[str, float]:
            base_name = BASE14_STYLE_FONTS.get(style, "tiro")
            try:
                base = self.fontmap.get(base_name)
                if base is not None and base.to_unichr(ord(character)) == character:
                    return base_name, base.char_width(ord(character)) * size
            except Exception:
                pass
            font_name = self.style_font_names.get(style, self.noto_name)
            font = self.style_fonts.get(style, self.noto)
            try:
                if record_usage:
                    self.used_output_font_names.add(font_name)
                return font_name, font.char_lengths(character, size)[0]
            except Exception:
                return font_name, size * 0.5

        source_cmap_codes: dict[object, dict[int, bytes]] = {}

        def source_character_encoding(
            character: LTChar,
        ) -> tuple[object, str]:
            """Encode a source glyph even when it came from a Form resource.

            At end_page the interpreter exposes the page resource map. Fonts
            private to nested Form XObjects are therefore absent. Use the
            installed output font for those Unicode characters instead of
            crashing while composing an atomic unresolved fallback.
            """
            source_font = self.source_font_names.get(character.font)
            if source_font is None:
                source_font = self.fontid.get(character.font)
            if source_font is not None:
                font = character.font
                if isinstance(font, PDFCIDFont):
                    cmap_name = str(
                        getattr(getattr(font, "cmap", None), "attrs", {}).get(
                            "CMapName", ""
                        )
                    ).upper()
                    if "UTF16" in cmap_name:
                        return source_font, character.get_text().encode(
                            "utf-16-be"
                        ).hex()
                    if cmap_name.startswith("IDENTITY-"):
                        return source_font, f"{int(character.cid):04x}"

                    reverse = source_cmap_codes.get(font)
                    if reverse is None:
                        reverse = {}

                        def collect_codes(node: dict, prefix: bytes = b"") -> None:
                            for code, value in node.items():
                                encoded = prefix + bytes((int(code),))
                                if isinstance(value, dict):
                                    collect_codes(value, encoded)
                                else:
                                    reverse.setdefault(int(value), encoded)

                        collect_codes(getattr(font.cmap, "code2cid", {}))
                        source_cmap_codes[font] = reverse
                    code = reverse.get(int(character.cid))
                    if code is not None:
                        return source_font, code.hex()
                else:
                    return source_font, f"{int(character.cid):02x}"
            source_text = character.get_text()
            replacement_font, _advance = output_font(
                source_text,
                int(TextStyle.REGULAR),
                character.size,
            )
            return replacement_font, raw_string(replacement_font, source_text)

        def measure_styled_text(text: str, size: float) -> float:
            total = 0.0
            style = TextStyle.REGULAR
            pointer = 0
            while pointer < len(text):
                style_tag = STYLE_TAG_PATTERN.match(text, pointer)
                if style_tag:
                    closing, identifier = style_tag.groups()
                    style = TextStyle.REGULAR if closing else TextStyle(int(identifier))
                    pointer = style_tag.end()
                    continue
                formula = re.match(
                    r"\{\s*v([\d\s]+)\}", text[pointer:], re.IGNORECASE
                )
                if formula:
                    try:
                        total += vlen[int(formula.group(1).replace(" ", ""))]
                    except (IndexError, ValueError):
                        pass
                    pointer += len(formula.group(0))
                    continue
                _font_name, advance = output_font(text[pointer], int(style), size)
                total += advance
                pointer += 1
            return total

        default_line_height = line_height_for_language(self.translator.lang_out)
        _x, _y = 0, 0
        ops_list = []

        # Draw white rectangles to cover original text in background image (scanned PDFs only)
        white_rects = ""
        if ltpage.pageid in self.scanned_pages:
            pad = 3  # padding to fully cover original text with descenders/ascenders
            for id, new in enumerate(news):
                if new != sstk[id]:  # Only cover areas that were translated
                    rx0 = pstk[id].x0 - pad
                    ry0 = pstk[id].y0 - pad
                    rw = pstk[id].x1 - pstk[id].x0 + pad * 2
                    rh = pstk[id].y1 - pstk[id].y0 + pad * 2
                    white_rects += f"q 1 1 1 rg {rx0:f} {ry0:f} {rw:f} {rh:f} re f Q "
            # Also cover formula areas
            for v in var:
                if v:
                    fx0 = min(ch.x0 for ch in v) - pad
                    fy0 = min(ch.y0 for ch in v) - pad
                    fx1 = max(ch.x1 for ch in v) + pad
                    fy1 = max(ch.y1 for ch in v) + pad
                    white_rects += f"q 1 1 1 rg {fx0:f} {fy0:f} {fx1-fx0:f} {fy1-fy0:f} re f Q "

        def gen_op_txt(
            font,
            size,
            x,
            y,
            rtxt,
            style=TextStyle.REGULAR,
            orientation=IDENTITY_ORIENTATION,
            graphic="",
        ):
            synthetic = int(style) in self.synthetic_styles
            a, b, c, d = styled_text_matrix(orientation, int(style), synthetic)
            render = ""
            reset = ""
            if uses_synthetic_bold(int(style), synthetic):
                render = f"2 Tr {max(0.15, size * 0.025):f} w "
                reset = "0 Tr "
            colour = graphic or DEFAULT_COLOUR_INSTRUCTION
            if uses_synthetic_bold(int(style), synthetic):
                colour = f"{colour} {stroke_colour_from_fill(colour)}"
            colour = f"{colour} "
            return (
                f"{colour}/{font} {size:f} Tf {render}{a:f} {b:f} {c:f} {d:f} "
                f"{x:f} {y:f} Tm [<{rtxt}>] TJ {reset}"
            )

        def gen_op_line(x, y, xlen, ylen, linewidth):
            return f"ET q 1 0 0 1 {x:f} {y:f} cm [] 0 d 0 J {linewidth:f} w 0 0 m {xlen:f} {ylen:f} l S Q BT "

        def has_source_script(paragraph: Paragraph) -> bool:
            return any(
                is_translatable_source_script_character(character.get_text())
                for character in paragraph.source_characters
            )

        def replay_source_paragraph(paragraph: Paragraph) -> list[str]:
            """Replay unresolved source-script text with its embedded glyphs."""
            operations: list[str] = []
            for character in paragraph.source_characters:
                font_name, encoded = source_character_encoding(character)
                orientation = text_orientation(character.matrix)
                if orientation in (None, IDENTITY_ORIENTATION):
                    x = character.x0
                    y = character.y0
                    size = character.size
                else:
                    x = float(character.matrix[4])
                    y = float(character.matrix[5])
                    size = matrix_font_size(character.matrix)
                operations.append(
                    gen_op_txt(
                        font_name,
                        size,
                        x,
                        y,
                        encoded,
                        TextStyle.REGULAR,
                        normalised_text_matrix(character.matrix),
                        getattr(character, "graphic_instruction", ""),
                    )
                )
            for identifier in paragraph.formula_ids:
                for line in varl[identifier]:
                    if line.linewidth < 5:
                        operations.append(
                            gen_op_line(
                                line.pts[0][0],
                                line.pts[0][1],
                                line.pts[1][0] - line.pts[0][0],
                                line.pts[1][1] - line.pts[0][1],
                                line.linewidth,
                            )
                        )
            return operations

        def rotated_available_length(paragraph: Paragraph) -> float:
            a, b, _c, _d = paragraph.orientation
            bounds = paragraph.layout_bound or (
                paragraph.x0,
                paragraph.y0,
                paragraph.x1,
                paragraph.y1,
            )
            x0, y0, x1, y1 = bounds
            projections = [
                x * a + y * b
                for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1))
            ]
            anchor_projection = paragraph.anchor[0] * a + paragraph.anchor[1] * b
            return max(0.0, max(projections) - anchor_projection)

        def render_rotated_text(
            paragraph: Paragraph,
            source: str,
            translated: str,
        ) -> list[str]:
            size = paragraph.size
            available = rotated_available_length(paragraph)
            measured = measure_styled_text(translated, size)
            if measured > available * 1.05 and available > 0:
                ratio = available / measured
                if ratio < 0.5:
                    if translated != source:
                        self.record_translation_failure(
                            source, "rotated text needs less than 50% font size"
                        )
                    if has_source_script(paragraph):
                        return replay_source_paragraph(paragraph)
                    translated = source
                    measured = measure_styled_text(translated, size)
                    ratio = min(1.0, available / measured) if measured else 1.0
                size *= max(0.5, min(1.0, ratio))

            a, b, _c, _d = paragraph.orientation
            anchor_x, anchor_y = paragraph.anchor
            cursor = 0.0
            pointer = 0
            active_style = TextStyle.REGULAR
            run_font: str | None = None
            run_style = TextStyle.REGULAR
            run_text = ""
            run_start = 0.0
            operations: list[str] = []

            def flush() -> None:
                nonlocal run_text
                if not run_text or run_font is None:
                    run_text = ""
                    return
                operations.append(
                    gen_op_txt(
                        run_font,
                        size,
                        anchor_x + a * run_start,
                        anchor_y + b * run_start,
                        raw_string(run_font, run_text),
                        run_style,
                        paragraph.orientation,
                        paragraph.graphic_instruction,
                    )
                )
                run_text = ""

            while pointer < len(translated):
                style_tag = STYLE_TAG_PATTERN.match(translated, pointer)
                if style_tag:
                    flush()
                    closing, identifier = style_tag.groups()
                    active_style = (
                        TextStyle.REGULAR
                        if closing
                        else TextStyle(int(identifier))
                    )
                    pointer = style_tag.end()
                    continue
                formula = re.match(
                    r"\{\s*v([\d\s]+)\}", translated[pointer:], re.IGNORECASE
                )
                if formula:
                    flush()
                    try:
                        vid = int(formula.group(1).replace(" ", ""))
                        formula_chars = var[vid]
                    except (IndexError, ValueError):
                        pointer += len(formula.group(0))
                        continue
                    first = formula_chars[0]
                    first_origin = (float(first.matrix[4]), float(first.matrix[5]))
                    for formula_char in formula_chars:
                        formula_font, formula_raw = source_character_encoding(
                            formula_char
                        )
                        dx = float(formula_char.matrix[4]) - first_origin[0]
                        dy = float(formula_char.matrix[5]) - first_origin[1]
                        operations.append(
                            gen_op_txt(
                                formula_font,
                                matrix_font_size(formula_char.matrix),
                                anchor_x + a * cursor + dx,
                                anchor_y + b * cursor + dy,
                                formula_raw,
                                TextStyle.REGULAR,
                                normalised_text_matrix(formula_char.matrix),
                                getattr(formula_char, "graphic_instruction", ""),
                            )
                        )
                    cursor += vlen[vid]
                    pointer += len(formula.group(0))
                    continue
                character = translated[pointer]
                font_name, advance = output_font(character, int(active_style), size)
                if font_name != run_font or active_style != run_style:
                    flush()
                    run_font = font_name
                    run_style = active_style
                    run_start = cursor
                run_text += character
                cursor += advance
                pointer += 1
            flush()
            return operations

        # What sits below a paragraph decides how far it may grow. Table cells
        # are excluded: a cell owns exactly its cell and must never lean into
        # the row beneath it.
        obstacles = [
            (paragraph.x0, paragraph.y0, paragraph.x1, paragraph.y1)
            for paragraph in pstk
        ]
        obstacles.extend(
            (
                min(character.x0 for character in characters),
                min(character.y0 for character in characters),
                max(character.x1 for character in characters),
                max(character.y1 for character in characters),
            )
            for characters in var
            if characters
        )
        fit_budgets = [
            paragraph.y1 - paragraph.y0
            if paragraph.layout_bound is not None
            else available_height_below(
                (paragraph.x0, paragraph.y0, paragraph.x1, paragraph.y1),
                obstacles,
            )
            for paragraph in pstk
        ]
        minimum_line_height = min_line_height_for_language(self.translator.lang_out)

        for id, new in enumerate(news):
            x: float = pstk[id].x
            y: float = pstk[id].y
            x0: float = pstk[id].x0
            x1: float = pstk[id].x1
            height: float = pstk[id].y1 - pstk[id].y0
            size: float = pstk[id].size
            brk: bool = pstk[id].brk

            if new == sstk[id] and has_source_script(pstk[id]):
                ops_list.extend(replay_source_paragraph(pstk[id]))
                continue

            if pstk[id].orientation not in (None, IDENTITY_ORIENTATION):
                ops_list.extend(render_rotated_text(pstk[id], sstk[id], new))
                continue

            fitted_cell_translation = False
            if pstk[id].layout_bound is not None and new != sstk[id]:
                def _cell_measure(character: str, candidate_size: float) -> float:
                    widths = (
                        output_font(character, style, candidate_size, record_usage=False)[1]
                        for style in self.style_font_names
                    )
                    return max(widths, default=output_font(
                        character,
                        int(TextStyle.REGULAR),
                        candidate_size,
                        record_usage=False,
                    )[1])

                # The table detector already supplied the safe horizontal
                # cluster and full cell height. Start at that region's inset,
                # wrap there, and only then lower the font size.
                fitted_size = largest_fitting_cell_font_size(
                    new,
                    max(0.0, x1 - x - 1.0),
                    max(0.0, x1 - x0 - 1.0),
                    max(0.0, height - 1.0),
                    size,
                    vlen,
                    _cell_measure,
                    minimum_line_height,
                    lambda character, style, candidate_size: output_font(
                        character, style, candidate_size, record_usage=False
                    )[1],
                )
                full_width_size = largest_fitting_cell_font_size(
                    new,
                    max(0.0, x1 - x0 - 1.0),
                    max(0.0, x1 - x0 - 1.0),
                    max(0.0, height - 1.0),
                    size,
                    vlen,
                    _cell_measure,
                    minimum_line_height,
                    lambda character, style, candidate_size: output_font(
                        character, style, candidate_size, record_usage=False
                    )[1],
                )
                if full_width_size is not None and (
                    fitted_size is None or full_width_size > fitted_size * 1.05
                ):
                    # A centred/right-aligned source label can leave too little
                    # room for the target's first word. Move only to the safe
                    # cell inset when doing so materially raises the fitted size.
                    x = x0
                    fitted_size = full_width_size
                if fitted_size is None:
                    self.record_translation_failure(
                        sstk[id],
                        "table cell cannot fit at 50% font size",
                        occurrence_id=occurrence_ids[id],
                    )
                    new = sstk[id]
                else:
                    size = fitted_size
                    brk = True
                    fitted_cell_translation = True

            if new == sstk[id] and has_source_script(pstk[id]):
                ops_list.extend(replay_source_paragraph(pstk[id]))
                continue

            # Auto-scale text to the footprint of the source. This is also
            # required for a single-line title: without it a longer target
            # string ignores x1 completely and runs into the neighbouring
            # column.
            #
            # A paragraph left in the source language is measured too. It is
            # still re-drawn in the output font, which is wider than many
            # source faces, so skipping the fit let untranslated English grow
            # an extra line and print straight over the paragraph below it -
            # in a textbook whose paragraph boxes are stacked half a point
            # apart, there is nowhere else for that line to go.
            # Count how many lines the original text occupied
            orig_lines = (
                max(1, round(height / (pstk[id].size * default_line_height)))
                if brk
                else 1
            )
            total_avail = paragraph_width_budget(x, x0, x1, orig_lines)
            # Measure actual width of translated text (excluding formula tags)
            total_new_width = 0
            tmp_ptr = 0
            plain_new = new
            measure_style = TextStyle.REGULAR
            while tmp_ptr < len(plain_new):
                style_tag = STYLE_TAG_PATTERN.match(plain_new, tmp_ptr)
                if style_tag:
                    closing, identifier = style_tag.groups()
                    measure_style = (
                        TextStyle.REGULAR
                        if closing
                        else TextStyle(int(identifier))
                    )
                    tmp_ptr = style_tag.end()
                    continue
                vm = re.match(r"\{\s*v([\d\s]+)\}", plain_new[tmp_ptr:], re.IGNORECASE)
                if vm:
                    try:
                        vid_tmp = int(vm.group(1).replace(" ", ""))
                        total_new_width += vlen[vid_tmp]
                    except Exception:
                        pass
                    tmp_ptr += len(vm.group(0))
                else:
                    ch = plain_new[tmp_ptr]
                    total_new_width += output_font(
                        ch, int(measure_style), pstk[id].size
                    )[1]
                    tmp_ptr += 1
            if (
                not fitted_cell_translation
                and total_avail > 0
                and total_new_width > total_avail * 1.05
            ):
                ratio = total_avail / total_new_width
                if not brk and ratio < 0.5 and new != sstk[id]:
                    # Only a translation can fall back; source text has
                    # nowhere to fall back to, and reporting it as an
                    # untranslated segment twice would overstate the loss.
                    self.record_translation_failure(
                        sstk[id],
                        "single line needs less than 50% font size",
                        occurrence_id=occurrence_ids[id],
                    )
                    new = sstk[id]
                else:
                    size = pstk[id].size * max(ratio, 0.5)

            if new == sstk[id] and has_source_script(pstk[id]):
                ops_list.extend(replay_source_paragraph(pstk[id]))
                continue

            # Pre-compute word-boundary line breaks to avoid mid-word splits
            if brk:
                def _measure_char(c, style):
                    return output_font(c, int(style), size)[1]

                break_positions = set()
                if fitted_cell_translation:
                    plan = _cell_wrap_plan(
                        new,
                        x,
                        x0,
                        x1,
                        size,
                        vlen,
                        lambda character, style, candidate: output_font(
                            character, style, candidate
                        )[1],
                    )
                    if plan is not None:
                        break_positions = plan[0]
                else:
                    cur_x = x
                    last_space_ptr = -1
                    last_space_x_after = cur_x
                    p2 = 0
                    wrap_style = TextStyle.REGULAR
                    while p2 < len(new):
                        style_tag = STYLE_TAG_PATTERN.match(new, p2)
                        if style_tag:
                            closing, identifier = style_tag.groups()
                            wrap_style = (
                                TextStyle.REGULAR
                                if closing
                                else TextStyle(int(identifier))
                            )
                            p2 = style_tag.end()
                            continue
                        vr2 = re.match(r"\{\s*v([\d\s]+)\}", new[p2:], re.IGNORECASE)
                        if vr2:
                            try:
                                vid_t = int(vr2.group(1).replace(" ", ""))
                                cw = vlen[vid_t]
                            except Exception:
                                cw = 0
                            if cur_x + cw > x1 + 0.1 * size and cur_x > x0 + 0.1 * size:
                                if last_space_ptr >= 0:
                                    break_positions.add(last_space_ptr + 1)
                                    cur_x = x0 + (cur_x - last_space_x_after)
                                    last_space_ptr = -1
                                    last_space_x_after = x0
                            cur_x += cw
                            p2 += len(vr2.group(0))
                        else:
                            ch2 = new[p2]
                            cw = _measure_char(ch2, wrap_style)
                            if ch2 == ' ':
                                last_space_ptr = p2
                                last_space_x_after = cur_x + cw
                            if cur_x + cw > x1 + 0.1 * size and cur_x > x0 + 0.1 * size:
                                if last_space_ptr >= 0:
                                    break_positions.add(last_space_ptr + 1)
                                    cur_x = x0 + (cur_x - last_space_x_after)
                                    last_space_ptr = -1
                                    last_space_x_after = x0
                            cur_x += cw
                            p2 += 1
                # Replace spaces at break positions with newlines (process in reverse)
                for bp in sorted(break_positions, reverse=True):
                    if fitted_cell_translation:
                        new = new[:bp] + '\n' + new[bp + 1:]
                    else:
                        new = new[:bp - 1] + '\n' + new[bp:]

            cstk: str = ""
            fcur: str = None
            lidx = 0
            tx = x
            fcur_ = fcur
            ptr = 0
            active_style = TextStyle.REGULAR
            cstyle = TextStyle.REGULAR
            log.debug(f"< {y} {x} {x0} {x1} {size} {brk} > {sstk[id]} | {new}")

            # Where each line begins.  A font size chosen after these
            # operations are built has to put every run back at the coordinate
            # the new size implies; without an origin to measure from, the runs
            # keep the old size's positions and the line pulls apart.  Line 0
            # may be indented, every later line starts at x0.
            first_line_x = x
            ops_vals: list[dict] = []

            while ptr < len(new):
                style_tag = STYLE_TAG_PATTERN.match(new, ptr)
                if style_tag:
                    if cstk:
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": fcur,
                            "size": size,
                            "x": tx,
                            "dy": 0,
                            "rtxt": raw_string(fcur, cstk),
                            "lidx": lidx,
                            "style": cstyle,
                            "graphic": pstk[id].graphic_instruction,
                        })
                        cstk = ""
                    closing, identifier = style_tag.groups()
                    active_style = (
                        TextStyle.REGULAR
                        if closing
                        else TextStyle(int(identifier))
                    )
                    ptr = style_tag.end()
                    continue
                vy_regex = re.match(
                    r"\{\s*v([\d\s]+)\}", new[ptr:], re.IGNORECASE
                )
                mod = 0
                if vy_regex:
                    ptr += len(vy_regex.group(0))
                    try:
                        vid = int(vy_regex.group(1).replace(" ", ""))
                        adv = vlen[vid]
                    except Exception:
                        continue
                    if var[vid][-1].get_text() and unicodedata.category(var[vid][-1].get_text()[0]) in ["Lm", "Mn", "Sk"]:
                        mod = var[vid][-1].width
                else:
                    ch = new[ptr]
                    if ch == '\n':  # Forced line break from word-wrap pre-computation
                        if cstk:
                            ops_vals.append({
                                "type": OpType.TEXT,
                                "font": fcur,
                                "size": size,
                                "x": tx,
                                "dy": 0,
                                "rtxt": raw_string(fcur, cstk),
                                "lidx": lidx,
                                "style": cstyle,
                                "graphic": pstk[id].graphic_instruction,
                            })
                            cstk = ""
                        x = x0
                        lidx += 1
                        ptr += 1
                        continue
                    fcur_, adv = output_font(ch, int(active_style), size)
                    ptr += 1
                if (
                    fcur_ != fcur
                    or vy_regex
                    or x + adv > x1 + 0.1 * size
                ):
                    if cstk:
                        # Word-wrap: if hitting right boundary, break at last space
                        if brk and x + adv > x1 + 0.1 * size and ' ' in cstk:
                            last_space = cstk.rfind(' ')
                            before = cstk[:last_space]
                            after = cstk[last_space + 1:]
                            if before:
                                ops_vals.append({
                                    "type": OpType.TEXT,
                                    "font": fcur,
                                    "size": size,
                                    "x": tx,
                                    "dy": 0,
                                    "rtxt": raw_string(fcur, before),
                                    "lidx": lidx,
                                    "style": cstyle,
                                    "graphic": pstk[id].graphic_instruction,
                                })
                            # Move remainder to new line
                            lidx += 1
                            x = x0
                            tx = x
                            # Recalculate x for the remaining text
                            for rc in after:
                                x += output_font(rc, int(cstyle), size)[1]
                            cstk = after
                        else:
                            ops_vals.append({
                                "type": OpType.TEXT,
                                "font": fcur,
                                "size": size,
                                "x": tx,
                                "dy": 0,
                                "rtxt": raw_string(fcur, cstk),
                                "lidx": lidx,
                                "style": cstyle,
                                "graphic": pstk[id].graphic_instruction,
                            })
                            cstk = ""
                if brk and x + adv > x1 + 0.1 * size:
                    x = x0
                    lidx += 1
                if vy_regex:
                    fix = 0
                    if fcur is not None:
                        fix = varf[vid]
                    for vch in var[vid]:
                        source_font, source_raw = source_character_encoding(vch)
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": source_font,
                            "size": vch.size,
                            "x": x + vch.x0 - var[vid][0].x0,
                            "dy": fix + vch.y0 - var[vid][0].y0,
                            "rtxt": source_raw,
                            "lidx": lidx,
                            "style": TextStyle.REGULAR,
                            "orientation": normalised_text_matrix(vch.matrix),
                            "graphic": getattr(vch, "graphic_instruction", ""),
                        })
                        if log.isEnabledFor(logging.DEBUG):
                            lstk.append(LTLine(0.1, (_x, _y), (x + vch.x0 - var[vid][0].x0, fix + y + vch.y0 - var[vid][0].y0)))
                            _x, _y = x + vch.x0 - var[vid][0].x0, fix + y + vch.y0 - var[vid][0].y0
                    for l in varl[vid]:
                        if l.linewidth < 5:
                            ops_vals.append({
                                "type": OpType.LINE,
                                "x": l.pts[0][0] + x - var[vid][0].x0,
                                "dy": l.pts[0][1] + fix - var[vid][0].y0,
                                "linewidth": l.linewidth,
                                "xlen": l.pts[1][0] - l.pts[0][0],
                                "ylen": l.pts[1][1] - l.pts[0][1],
                                "lidx": lidx
                            })
                else:
                    if not cstk:
                        tx = x
                        cstyle = active_style
                        if x == x0 and ch == " ":
                            adv = 0
                        else:
                            cstk += ch
                    else:
                        cstk += ch
                adv -= mod
                fcur = fcur_
                x += adv
                if log.isEnabledFor(logging.DEBUG):
                    lstk.append(LTLine(0.1, (_x, _y), (x, y)))
                    _x, _y = x, y
            if cstk:
                ops_vals.append({
                    "type": OpType.TEXT,
                    "font": fcur,
                    "size": size,
                    "x": tx,
                    "dy": 0,
                    "rtxt": raw_string(fcur, cstk),
                    "lidx": lidx,
                    "style": cstyle,
                    "graphic": pstk[id].graphic_instruction,
                })

            line_height = default_line_height
            fit_height = fit_budgets[id]

            # Fit the prose to the box on its own. Charging the formula's extra
            # room to this loop drops the leading for every line in the
            # paragraph, until they collide with each other instead.
            #
            # The floor is the measured ink of the target script, not a round
            # number: Vietnamese stacked tone marks need 1.10 em, so the old
            # 0.75 floor bought room by drawing lines through each other.
            while (
                (lidx + 1) * size * line_height > fit_height
                and line_height > minimum_line_height
            ):
                line_height = max(minimum_line_height, line_height - 0.05)

            # If still overflowing after reducing line_height, shrink font to fit
            if lidx > 0 and (lidx + 1) * size * line_height > fit_height:
                shrink = fit_height / ((lidx + 1) * size * line_height)
                shrink = max(shrink, 0.5)  # Don't go below 50%
                size *= shrink
                rescale_operations(ops_vals, shrink, first_line_x, x0)

            # Measure ink only after the final font-size adjustment.  Measuring
            # before shrinking left the old line gaps in place, so dense table
            # cells used smaller glyphs but still crossed the row below.
            ink = operation_ink(ops_vals)

            if ink:
                # Preserved codes and formula placeholders can be larger than
                # the surrounding translated prose.  The prose-only line count
                # above cannot see that, so fit the union of the actual glyph
                # extents to the available room as a final guard.  The formula
                # slack stays tied to the paragraph's own box; only the
                # collision test may use the room borrowed from below.
                for _attempt in range(3):
                    preview_offsets = line_offsets(
                        ink,
                        lidx,
                        size,
                        line_height,
                        budget=height - (lidx + 1) * size * line_height,
                    )
                    occupied = vertical_ink_extent(ink, preview_offsets)
                    available_height = max(0.0, fit_height - 1.0)
                    if occupied <= available_height + 0.01 or occupied <= 0:
                        break
                    minimum_size = pstk[id].size * 0.5
                    scale = max(minimum_size / max(size, 1e-6), available_height / occupied)
                    scale = min(1.0, scale)
                    if scale >= 0.999:
                        break
                    size *= scale
                    rescale_operations(ops_vals, scale, first_line_x, x0)
                    ink = operation_ink(ops_vals)

            # Formula slack stays charged to the paragraph's own box, so a
            # formula in an already tight paragraph is still somewhat cramped.
            # The room borrowed from below is only spent on not colliding.
            offsets = line_offsets(ink, lidx, size, line_height,
                                   budget=height - (lidx + 1) * size * line_height)

            if pstk[id].layout_bound is not None:
                cell_shift = vertical_shift_to_bounds(
                    y,
                    ink,
                    offsets,
                    pstk[id].y0 + 0.5,
                    pstk[id].y1 - 0.5,
                )
                y += cell_shift

            for vals in ops_vals:
                if vals["type"] == OpType.TEXT:
                    ops_list.append(
                        gen_op_txt(
                            vals["font"],
                            vals["size"],
                            vals["x"],
                            vals["dy"] + y - offsets[vals["lidx"]],
                            vals["rtxt"],
                            vals.get("style", TextStyle.REGULAR),
                            vals.get("orientation", IDENTITY_ORIENTATION),
                            vals.get("graphic", ""),
                        )
                    )
                elif vals["type"] == OpType.LINE:
                    ops_list.append(gen_op_line(vals["x"], vals["dy"] + y - offsets[vals["lidx"]], vals["xlen"], vals["ylen"], vals["linewidth"]))

        for l in lstk:
            if l.linewidth < 5:
                ops_list.append(gen_op_line(l.pts[0][0], l.pts[0][1], l.pts[1][0] - l.pts[0][0], l.pts[1][1] - l.pts[0][1], l.linewidth))

        ops = f"{white_rects}BT {''.join(ops_list)}ET "
        self.profile.stop(layout_token)
        return ops


def available_height_below(
    bounds: tuple[float, float, float, float],
    obstacles: Iterable[tuple[float, float, float, float]],
    floor: float = 0.0,
) -> float:
    """Return the height a paragraph may fill before it reaches what is below.

    A translation is often a line or two longer than its source, and charging
    that to the source box alone made paragraphs shrink even with white space
    underneath them - or, once shrinking hit its floor, draw straight over the
    next paragraph. Only what actually sits below and shares this paragraph's
    column counts; a neighbour in another column is not an obstacle.
    """
    x0, y0, x1, y1 = bounds
    width = x1 - x0
    limit = floor
    for ox0, _oy0, ox1, oy1 in obstacles:
        if oy1 > y0 + 0.5:
            continue  # beside or above this paragraph, so it cannot be hit
        overlap = min(x1, ox1) - max(x0, ox0)
        if width > 0 and overlap <= 0.1 * width:
            continue  # a hairline touch is a different column, not a collision
        limit = max(limit, oy1)
    return max(y1 - limit, y1 - y0)


def line_offsets(
    ink: dict[int, tuple[float, float]],
    lines: int,
    size: float,
    line_height: float,
    budget: float | None = None,
) -> list[float]:
    """Distance from a paragraph's first baseline down to each later baseline.

    `ink[i]` is how far line i's glyphs reach below and above its own baseline.
    Prose lines get the usual leading; a line holding a tall inline formula gets
    the extra room its glyphs and its neighbour's need, so a fraction's
    denominator no longer lands on the line underneath. `budget` caps that extra
    at the space the paragraph has left, because spilling onto the paragraph
    below looks worse than a formula that is still a little tight.
    """
    base = size * line_height
    want = [
        max(0.0, (ink.get(i + 1, (0.0, 0.0))[1] - ink.get(i, (0.0, 0.0))[0]) - base)
        for i in range(lines)
    ]
    total = sum(want)
    if budget is not None and total > 0 and total > budget:
        # Not enough slack for every tall formula. Share out what there is
        # rather than growing the paragraph down over the text below it.
        scale = max(0.0, budget) / total
        want = [w * scale for w in want]
    offsets = [0.0]
    for extra in want:
        offsets.append(offsets[-1] + base + extra)
    return offsets


class OpType(Enum):
    TEXT = "text"
    LINE = "line"


def operation_ink(
    operations: list[dict],
) -> dict[int, tuple[float, float]]:
    """Measure each rendered line using its final glyph sizes and offsets."""
    ink: dict[int, tuple[float, float]] = {}
    for values in operations:
        size = values["size"] if values["type"] == OpType.TEXT else 0.0
        low = (
            values["dy"]
            + min(0.0, values.get("ylen", 0.0))
            - 0.22 * size
        )
        high = (
            values["dy"]
            + max(0.0, values.get("ylen", 0.0))
            + 0.78 * size
        )
        previous_low, previous_high = ink.get(values["lidx"], (low, high))
        ink[values["lidx"]] = (
            min(previous_low, low),
            max(previous_high, high),
        )
    return ink


def rescale_operations(
    operations: list[dict],
    factor: float,
    first_line_x: float,
    left_x: float,
) -> None:
    """Re-place built operations after their font size changed.

    Every advance this converter measures is linear in the font size, so a
    paragraph that shrinks by `factor` needs each run at `factor` of its former
    distance from the start of its line.  Scaling only the size left each run at
    the old size's coordinate: the gap in front of it grew by the width the run
    no longer occupies, and the gaps accumulated along the line.  Vietnamese
    showed it worst, because every accented letter starts a new run in the
    Unicode font while the ASCII around it stays in the base-14 face, so a
    shrunk paragraph pulled apart between the letters of single words.

    Formula glyphs are moved the same way.  Their size was already being scaled
    while the offsets holding them together were not, which spread a shrunk
    inline formula out over its original width.
    """
    for values in operations:
        origin = first_line_x if values["lidx"] == 0 else left_x
        values["x"] = origin + (values["x"] - origin) * factor
        values["dy"] *= factor
        if values["type"] == OpType.TEXT:
            values["size"] *= factor
        else:
            values["xlen"] *= factor
            values["ylen"] *= factor
            values["linewidth"] *= factor


def vertical_ink_extent(
    ink: dict[int, tuple[float, float]], offsets: list[float]
) -> float:
    """Return total vertical glyph span after applying per-line offsets."""
    extents = [
        (low - offsets[index], high - offsets[index])
        for index, (low, high) in ink.items()
        if index < len(offsets)
    ]
    if not extents:
        return 0.0
    return max(high for _low, high in extents) - min(low for low, _high in extents)


def vertical_shift_to_bounds(
    baseline: float,
    ink: dict[int, tuple[float, float]],
    offsets: list[float],
    lower: float,
    upper: float,
) -> float:
    """Move a fitted paragraph back inside its cell without changing layout."""
    extents = [
        (baseline + low - offsets[index], baseline + high - offsets[index])
        for index, (low, high) in ink.items()
        if index < len(offsets)
    ]
    if not extents or upper <= lower:
        return 0.0
    minimum = min(low for low, _high in extents)
    maximum = max(high for _low, high in extents)
    if maximum - minimum > upper - lower + 0.01:
        return 0.0
    if minimum < lower:
        return lower - minimum
    if maximum > upper:
        return upper - maximum
    return 0.0
