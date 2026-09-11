"""Functions that can be used for the most common use-cases for pdf2zh.six"""

import asyncio
import io
import logging
import os
import re
import sys
import tempfile
import time
from asyncio import CancelledError
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import mean, median
from string import Template
from typing import Any, BinaryIO, Dict, List, Optional

import numpy as np
import pikepdf
import tqdm
from babeldoc.assets.assets import get_font_and_metadata
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfexceptions import PDFValueError
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pymupdf import Document, Font

from pdf2zh.converter import TranslateConverter
from pdf2zh.doclayout import OnnxModel
from pdf2zh.logical_units import ReconstructionMetrics
from pdf2zh.pdfinterp import PDFPageInterpreterEx
from pdf2zh.rules import (
    anchored_translatable_lines,
    anchored_prose_bounds,
    classify_preserved_page,
    cluster_table_words,
    formula_regions,
    group_translatable_line_clusters,
    immutable_metadata_regions,
    is_scanned_page,
    matching_table_cells,
    page_has_image,
    should_translate_table_cell,
    structural_page_text_clusters,
    upright_line_bounds,
    upright_table_words,
)

PAGE_ID_KEY = "PDFTranslatePageID"


def install_page_identities(document: Document, envs: Dict | None) -> None:
    """Carry runner-issued page identities through the native mono document."""
    identities = (envs or {}).get("page_identity")
    if identities is None:
        return
    if (
        not isinstance(identities, (list, tuple))
        or len(identities) != document.page_count
        or not all(isinstance(value, str) and value for value in identities)
    ):
        raise PDFValueError("Invalid native page-identity provenance")
    for page, identity in zip(document, identities):
        document.xref_set_key(page.xref, PAGE_ID_KEY, f"({identity})")


def expose_page_source_fonts(
    document: Document,
    page_xref: int,
    fonts: dict[str, int],
) -> None:
    """Expose Form-private font objects to a page-level replacement stream."""
    if not fonts:
        return

    owner_xref = page_xref
    resources_type = "null"
    resources_value = "null"
    seen: set[int] = set()
    while owner_xref not in seen:
        seen.add(owner_xref)
        resources_type, resources_value = document.xref_get_key(
            owner_xref, "Resources"
        )
        if resources_type != "null":
            break
        parent_type, parent_value = document.xref_get_key(owner_xref, "Parent")
        if parent_type != "xref":
            break
        owner_xref = int(parent_value.split()[0])

    if resources_type == "xref":
        resources_xref = int(resources_value.split()[0])
    else:
        resources_xref = document.get_new_xref()
        resource_dictionary = resources_value if resources_type == "dict" else "<<>>"
        document.update_object(resources_xref, resource_dictionary)
        document.xref_set_key(owner_xref, "Resources", f"{resources_xref} 0 R")

    font_type, font_value = document.xref_get_key(resources_xref, "Font")
    if font_type == "xref":
        font_dictionary_xref = int(font_value.split()[0])
    else:
        font_dictionary_xref = document.get_new_xref()
        font_dictionary = font_value if font_type == "dict" else "<<>>"
        document.update_object(font_dictionary_xref, font_dictionary)
        document.xref_set_key(
            resources_xref, "Font", f"{font_dictionary_xref} 0 R"
        )

    for font_name, font_xref in fonts.items():
        document.xref_set_key(
            font_dictionary_xref,
            font_name,
            f"{font_xref} 0 R",
        )


@dataclass(frozen=True)
class TranslationReport:
    """What a run could not translate, and why.

    The count on its own was ambiguous: a page of diagrams, a document that is
    all scans, and a dropped connection all arrived as the same number, and the
    caller had to guess. Each of those needs the user to do something
    different, so the reasons travel with the count.
    """

    failures: list[str] = field(default_factory=list)
    reasons: Counter = field(default_factory=Counter)
    image_only_pages: set[int] = field(default_factory=set)
    translatable_segments: int = 0
    pages_processed: int = 0
    total_segments: int = 0
    translated_segments: int = 0
    preserved_segments: int = 0
    unresolved_segments: int = 0
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
    translation_seconds: float = 0.0
    prepare_seconds: float = 0.0
    layout_seconds: float = 0.0
    render_seconds: float = 0.0
    total_seconds: float = 0.0
    input_bytes: int = 0
    output_bytes: int = 0
    output_font_references: int = 0
    unique_output_font_objects: int = 0
    used_output_font_names: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.failures)

    @property
    def accounted_segments(self) -> int:
        return (
            self.translated_segments
            + self.preserved_segments
            + self.unresolved_segments
        )


def validate_segment_accounting(
    total: int, translated: int, preserved: int, unresolved: int
) -> None:
    """Require one final status for every converter-extracted segment."""
    if min(total, translated, preserved, unresolved) < 0:
        raise RuntimeError("segment accounting produced a negative status count")
    if translated + preserved + unresolved != total:
        raise RuntimeError("segment accounting does not cover every extracted segment")


NOTO_NAME = "noto"
STYLE_FONT_NAMES = {
    0: NOTO_NAME,
    1: "noto-bold",
    2: "noto-italic",
    3: "noto-bolditalic",
}
BASE14_STYLE_FONTS = {0: "tiro", 1: "tibo", 2: "tiit", 3: "tibi"}

logger = logging.getLogger(__name__)
LARGE_DOCUMENT_SUBSET_PAGE_LIMIT = 200
LARGE_DOCUMENT_BYTE_LIMIT = 50 * 1024 * 1024


def table_cluster_render_bounds(
    bounds: tuple[float, float, float, float], page_height: float
) -> tuple[float, float, float, float] | None:
    """Convert an existing cell/cluster region to a padded PDF render box."""
    x0, y0, x1, y1 = bounds
    padded = (x0 + 2.0, page_height - y1 + 1.0, x1 - 2.0, page_height - y0 - 1.0)
    return padded if padded[2] > padded[0] and padded[3] > padded[1] else None


def is_large_document(page_count: int, source_size: int = 0) -> bool:
    return (
        page_count >= LARGE_DOCUMENT_SUBSET_PAGE_LIMIT
        or source_size >= LARGE_DOCUMENT_BYTE_LIMIT
    )


def should_subset_fonts(
    page_count: int, skip_subset_fonts: bool, source_size: int = 0
) -> bool:
    """Never subset: the converter writes raw glyph IDs into Identity-H fonts.

    `raw_string()` emits `font.has_glyph(ord(c))` as the CID, so any pass that
    renumbers glyphs repoints every translated character at a different
    outline. It cost Vietnamese every stacked-diacritic letter ("Viet Nam"
    where "Viet" needed U+1EC7) on documents small enough to fall under the old
    page/size threshold. The parameters are kept so the call site still reads
    as a decision rather than a silent omission.
    """
    return False


def pdf_write_options(page_count: int, source_size: int = 0) -> dict[str, int | bool]:
    """Choose fast, low-memory serialization for large documents.

    Recompressing and garbage-collecting every object in a long textbook can
    hold the CPython GIL for tens of seconds.  A light cleanup is almost the same
    size for image-heavy books and lets the GUI finish promptly.
    """
    if is_large_document(page_count, source_size):
        return {"deflate": False, "garbage": 1, "use_objstms": 0}
    return {"deflate": True, "garbage": 3, "use_objstms": 1}


def output_style_font_paths(language: str, regular_path: str) -> dict[int, str]:
    """Resolve regular/bold/italic/bold-italic fonts for translated prose.

    Vietnamese desktop builds run on Windows, where Times New Roman ships with
    all four faces. Other environments retain the existing Unicode font and let
    the converter synthesize missing weight/slant instead of downloading a new
    family at render time.
    """
    regular = str(Path(regular_path))
    result = {style: regular for style in STYLE_FONT_NAMES}
    if Path(regular).name.lower() != "times.ttf":
        return result
    windows_variants = {
        1: Path("C:/Windows/Fonts/timesbd.ttf"),
        2: Path("C:/Windows/Fonts/timesi.ttf"),
        3: Path("C:/Windows/Fonts/timesbi.ttf"),
    }
    for style, path in windows_variants.items():
        if path.is_file():
            result[style] = str(path)
    return result


def output_font_resource_counts(
    document: Document, font_names: set[str]
) -> tuple[int, int]:
    """Count page references and unique xrefs for the installed output fonts."""
    references: list[int] = []
    for page in document:
        for font in page.get_fonts(full=True):
            if len(font) > 4 and font[4] in font_names:
                references.append(int(font[0]))
    return len(references), len(set(references))


def install_document_fonts(
    document: Document, fonts: list[tuple[str, str | None]]
) -> dict[str, int]:
    """Embed each font once and share its xref through page resource dictionaries."""
    if not fonts or document.page_count == 0:
        return {}
    first_page = document[0]
    font_ids = {
        name: first_page.insert_font(name, path)
        for name, path in fonts
    }
    xref_length = document.xref_length()
    for object_xref in range(1, xref_length):
        for label in ("Resources/", ""):
            try:
                font_resource = document.xref_get_key(object_xref, f"{label}Font")
                target_xref = object_xref
                target_prefix = f"{label}Font/"
                if font_resource[0] == "xref":
                    match = re.search(r"(\d+) 0 R", font_resource[1])
                    if match is None:
                        continue
                    target_xref = int(match.group(1))
                    font_resource = ("dict", document.xref_object(target_xref))
                    target_prefix = ""
                if font_resource[0] != "dict":
                    continue
                for name, _path in fonts:
                    target_key = f"{target_prefix}{name}"
                    if document.xref_get_key(target_xref, target_key)[0] == "null":
                        document.xref_set_key(
                            target_xref,
                            target_key,
                            f"{font_ids[name]} 0 R",
                        )
            except Exception:
                continue
    return font_ids

noto_list = [
    "am",  # Amharic
    "ar",  # Arabic
    "bn",  # Bengali
    "bg",  # Bulgarian
    "chr",  # Cherokee
    "el",  # Greek
    "gu",  # Gujarati
    "iw",  # Hebrew
    "hi",  # Hindi
    "kn",  # Kannada
    "ml",  # Malayalam
    "mr",  # Marathi
    "ru",  # Russian
    "sr",  # Serbian
    "ta",  # Tamil
    "te",  # Telugu
    "th",  # Thai
    "ur",  # Urdu
    "uk",  # Ukrainian
]


def pymupdf_can_round_trip(path: Path) -> bool:
    """Report whether the engine can both read and rewrite this document.

    pikepdf tolerates structural damage that MuPDF later refuses on write, so
    probing with pikepdf let a malformed 517-page book reach `translate_stream`
    and die there with "invalid key in dict". The engine's own round trip is
    the only probe that predicts the failure it is meant to prevent, and it
    costs under a second even on a 48 MB book.
    """
    document = None
    try:
        document = Document(str(path))
        document.save(io.BytesIO())
    except Exception:
        return False
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass  # a document that failed to save also fails to close
    return True


def check_files(files: List[str]) -> List[str]:
    files = [
        f for f in files if not f.startswith("http://")
    ]  # exclude online files, http
    files = [
        f for f in files if not f.startswith("https://")
    ]  # exclude online files, https
    missing_files = [file for file in files if not os.path.exists(file)]
    return missing_files


def translate_patch(
    inf: BinaryIO,
    pages: Optional[list[int]] = None,
    vfont: str = "",
    vchar: str = "",
    thread: int = 0,
    doc_zh: Document = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    noto_name: str = "",
    noto: Font = None,
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    ignore_cache: bool = False,
    style_font_names: Dict | None = None,
    style_fonts: Dict | None = None,
    synthetic_styles: set[int] | None = None,
    **kwarg: Any,
) -> None:
    rsrcmgr = PDFResourceManager()
    layout = {}
    layout_bounds = {}
    # The full extent of each ordinary text region, kept apart from
    # layout_bounds because that one marks a table cell and changes how a
    # paragraph is fitted. This is only a measure to compare line ends against.
    class_bounds = {}
    # Fresh class ids whose physical lines form one logical occurrence, even
    # when the source PDF stores those lines in separate Form XObjects.
    logical_classes = {}
    reconstruction = ReconstructionMetrics()
    scanned_pages = set()
    pages_with_images = set()
    device = TranslateConverter(
        rsrcmgr,
        vfont,
        vchar,
        thread,
        layout,
        lang_in,
        lang_out,
        service,
        noto_name,
        noto,
        envs,
        prompt,
        ignore_cache,
        layout_bounds,
        style_font_names,
        style_fonts,
        synthetic_styles,
        class_bounds,
        logical_classes,
    )

    assert device is not None
    obj_patch = {}
    interpreter = PDFPageInterpreterEx(rsrcmgr, device, obj_patch)
    if pages:
        total_pages = len(pages)
    else:
        total_pages = doc_zh.page_count

    parser = PDFParser(inf)
    doc = PDFDocument(parser)
    with tqdm.tqdm(total=total_pages) as progress:
        for pageno, page in enumerate(PDFPage.create_pages(doc)):
            if cancellation_event and cancellation_event.is_set():
                raise CancelledError("task cancelled")
            if pages and (pageno not in pages):
                continue
            progress.update()
            if callback:
                callback(progress)
            page.pageno = pageno
            page_rect = doc_zh[page.pageno].rect
            page_area = page_rect.width * page_rect.height
            page_blocks = doc_zh[page.pageno].get_text("dict")["blocks"]
            if is_scanned_page(page_blocks, page_area):
                scanned_pages.add(pageno)
            if page_has_image(page_blocks):
                pages_with_images.add(pageno)
            pix = doc_zh[page.pageno].get_pixmap()
            image = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, 3
            )[:, :, ::-1]
            page_layout = model.predict(image, imgsz=int(pix.height / 32) * 32)[0]
            box = np.ones((pix.height, pix.width))
            h, w = box.shape
            vcls = ["abandon", "figure", "table", "isolate_formula", "formula_caption"]
            model_table_bounds = []
            # Process non-vcls boxes in ascending confidence order so that
            # higher-confidence boxes overwrite lower-confidence ones
            non_vcls_boxes = [
                (i, d) for i, d in enumerate(page_layout.boxes)
                if page_layout.names[int(d.cls)] not in vcls
            ]
            page_class_bounds = class_bounds.setdefault(page.pageno, {})
            page_logical_classes = logical_classes.setdefault(page.pageno, set())
            for i, d in reversed(non_vcls_boxes):
                x0, y0, x1, y1 = d.xyxy.squeeze()
                page_class_bounds[i + 2] = (
                    float(x0),
                    float(page_rect.height) - float(y1),
                    float(x1),
                    float(page_rect.height) - float(y0),
                )
                x0, y0, x1, y1 = (
                    np.clip(int(x0 - 1), 0, w - 1),
                    np.clip(int(h - y1 - 1), 0, h - 1),
                    np.clip(int(x1 + 1), 0, w - 1),
                    np.clip(int(h - y0 + 1), 0, h - 1),
                )
                box[y0:y1, x0:x1] = i + 2
            for i, d in enumerate(page_layout.boxes):
                name = page_layout.names[int(d.cls)]
                if name in vcls:
                    raw_x0, raw_y0, raw_x1, raw_y1 = (
                        float(value) for value in d.xyxy.squeeze()
                    )
                    if name == "table":
                        model_table_bounds.append(
                            (raw_x0, raw_y0, raw_x1, raw_y1)
                        )
                    x0, y0, x1, y1 = raw_x0, raw_y0, raw_x1, raw_y1
                    x0, y0, x1, y1 = (
                        np.clip(int(x0 - 1), 0, w - 1),
                        np.clip(int(h - y1 - 1), 0, h - 1),
                        np.clip(int(x1 + 1), 0, w - 1),
                        np.clip(int(h - y0 + 1), 0, h - 1),
                    )
                    box[y0:y1, x0:x1] = 0

            # A model-detected table stays protected unless PyMuPDF can split
            # that same region into cells. Each reliable cell gets its own class
            # so its text is translated independently while the original grid,
            # fills and borders remain untouched.
            source_page = doc_zh[page.pageno]
            try:
                detected_tables = source_page.find_tables().tables
            except Exception as error:
                logger.warning(
                    "Page %s table-cell detection failed; preserving tables: %s",
                    pageno + 1,
                    error,
                )
                detected_tables = []
            next_class = len(page_layout.boxes) + 2
            page_bounds = layout_bounds.setdefault(page.pageno, {})
            page_cell_ids: dict[int, tuple[Any, ...]] = {}
            page_logical_classes = logical_classes.setdefault(page.pageno, set())
            page_height = float(page_rect.height)
            page_words = upright_table_words(
                source_page.get_text("words", sort=True),
                source_page.get_text("dict")["blocks"],
            )
            for table_bounds in model_table_bounds:
                for cell in matching_table_cells(table_bounds, detected_tables):
                    cx0 = max(float(cell[0]), table_bounds[0])
                    cy0 = max(float(cell[1]), table_bounds[1])
                    cx1 = min(float(cell[2]), table_bounds[2])
                    cy1 = min(float(cell[3]), table_bounds[3])
                    if cx1 - cx0 <= 4 or cy1 - cy0 <= 1:
                        continue
                    cell_words = [
                        word
                        for word in page_words
                        if cx0 <= (float(word[0]) + float(word[2])) / 2 <= cx1
                        and cy0 <= (float(word[1]) + float(word[3])) / 2 <= cy1
                    ]
                    for cluster in cluster_table_words(
                        cell_words, (cx0, cy0, cx1, cy1)
                    ):
                        if not should_translate_table_cell(cluster.text):
                            continue
                        for word in cluster.words:
                            wx0, wy0, wx1, wy1 = (
                                float(value) for value in word[:4]
                            )
                            px0, py0, px1, py1 = (
                                np.clip(int(wx0 - 1), 0, w - 1),
                                np.clip(int(h - wy1 - 1), 0, h - 1),
                                np.clip(int(wx1 + 1), 0, w - 1),
                                np.clip(int(h - wy0 + 1), 0, h - 1),
                            )
                            box[py0:py1, px0:px1] = next_class
                        padded = table_cluster_render_bounds(cluster.bbox, page_height)
                        if padded is not None:
                            page_bounds[next_class] = padded
                        page_cell_ids[next_class] = (
                            "table-cell",
                            round(cx0, 2),
                            round(cy0, 2),
                            round(cx1, 2),
                            round(cy1, 2),
                            tuple(round(value, 2) for value in cluster.bbox),
                        )
                        next_class += 1

            # Technical documents often use ordinary prose fonts for equations.
            # Protect operator-only blocks and stacked identifiers before the
            # converter can reflow them. A one-point pad catches their rules and
            # small subscripts without swallowing adjacent prose.
            fallback_formulas = formula_regions(
                source_page.get_text("blocks", sort=True),
                page_words,
                stacked_exclusions=model_table_bounds,
            )
            for fx0, fy0, fx1, fy1 in fallback_formulas:
                bx0, by0, bx1, by1 = (
                    np.clip(int(fx0 - 1), 0, w - 1),
                    np.clip(int(h - fy1 - 1), 0, h - 1),
                    np.clip(int(fx1 + 1), 0, w - 1),
                    np.clip(int(h - fy0 + 1), 0, h - 1),
                )
                box[by0:by1, bx0:bx1] = 0

            page_text = source_page.get_text("text")
            preservation = classify_preserved_page(page_text)
            if preservation is not None:
                logger.info(
                    "Page %s detected as %s (%s); anchoring translatable text",
                    pageno + 1,
                    preservation.kind,
                    preservation.detail,
                )
                # Structural-page identity is carried by the original glyphs.
                # Start protected, then carve only natural-language phrases
                # into source-line-sized classes below.
                box[:, :] = 0
                page_bounds.clear()

            # Restore extractable prose hidden by a figure, unmatched table,
            # header/footer/abandon region, or structural page. Reliable cells
            # above keep their existing bounds and fitting behavior on ordinary
            # pages. Every recovered line receives one class and one fixed bound.
            anchor_blocks = source_page.get_text("dict")["blocks"]
            reconstruction.raw_text_spans += sum(
                len(line.get("spans", ()))
                for block in anchor_blocks
                for line in block.get("lines", ())
            )
            anchor_lines = [
                bounds
                for block in anchor_blocks
                for line in block.get("lines", ())
                if abs(line.get("dir", (1, 0))[1]) < 0.01
                if (bounds := upright_line_bounds(line)) is not None
            ]
            anchor_barriers = []
            for drawing in source_page.get_drawings():
                for item in drawing["items"]:
                    if item[0] == "l":
                        a,b = item[1:3]
                        anchor_barriers.append((min(a.x,b.x),min(a.y,b.y),max(a.x,b.x),max(a.y,b.y)))
                    elif item[0] == "re":
                        r=item[1]
                        anchor_barriers.extend(((r.x0,r.y0,r.x1,r.y0),(r.x0,r.y1,r.x1,r.y1),(r.x0,r.y0,r.x0,r.y1),(r.x1,r.y0,r.x1,r.y1)))
            for block in anchor_blocks:
                if block.get("type") == 1:
                    r=block["bbox"]
                    anchor_barriers.extend(((r[0],r[1],r[2],r[1]),(r[0],r[3],r[2],r[3]),(r[0],r[1],r[0],r[3]),(r[2],r[1],r[2],r[3])))
            protected_names = {"figure", "table", "abandon", "formula_caption"}
            if preservation is None:
                physical_anchors = anchored_translatable_lines(anchor_blocks)
                # A PDF producer may wrap one paragraph into several Form
                # XObjects.  Give conservative continuations inside the same
                # model region a fresh shared class; the converter can then
                # translate/fallback the complete occurrence atomically even
                # though the source containers are separate.
                ordinary_by_class: dict[tuple[int, Any], list[Any]] = {}
                for anchor in physical_anchors:
                    ax0, ay0, ax1, ay1 = anchor.bbox
                    cx = int(np.clip((ax0 + ax1) / 2, 0, w - 1))
                    cy = int(np.clip(h - (ay0 + ay1) / 2, 0, h - 1))
                    original_class = int(box[cy, cx])
                    if original_class > 0:
                        # Class 1 is text outside a model box. Its extracted
                        # source block is the only established parent; never
                        # treat the whole unclassified page as one region.
                        parent = anchor.parent_id if original_class == 1 else None
                        ordinary_by_class.setdefault(
                            (original_class, parent), []
                        ).append(anchor)
                for (original_class, source_parent), class_anchors in ordinary_by_class.items():
                    region_identity = (
                        ("source-parent", source_parent)
                        if original_class == 1
                        else ("layout", original_class)
                    )
                    for group in group_translatable_line_clusters(
                        class_anchors,
                        barriers=anchor_barriers,
                        page=page.pageno,
                        region_id=region_identity,
                        cell_id=page_cell_ids.get(original_class),
                        metrics=reconstruction,
                        fragment_id_prefix=f"p{page.pageno + 1}-anchor",
                    ):
                        for sx0, sy0, sx1, sy1 in group.regions or (group.bbox,):
                            px0, py0, px1, py1 = (
                                int(np.clip(sx0 - 1, 0, w - 1)),
                                int(np.clip(h - sy1 - 1, 0, h - 1)),
                                int(np.clip(sx1 + 1, 0, w - 1)),
                                int(np.clip(h - sy0 + 1, 0, h - 1)),
                            )
                            box[py0:py1, px0:px1] = next_class
                        layout_bound = page_bounds.get(original_class)
                        class_bound = page_class_bounds.get(original_class)
                        if layout_bound is not None:
                            page_bounds[next_class] = layout_bound
                        elif len(group.regions) > 1 and class_bound is not None:
                            # A merged result needs the original region as one
                            # fitting surface for atomic render/fallback.
                            page_bounds[next_class] = class_bound
                        elif class_bound is not None:
                            page_class_bounds[next_class] = class_bound
                        if len(group.regions) > 1:
                            page_logical_classes.add(next_class)
                        next_class += 1
                protected_parents = [
                    tuple(float(value) for value in detection.xyxy.squeeze())
                    for detection in page_layout.boxes
                    if page_layout.names[int(detection.cls)] in protected_names
                ]
                anchors_by_parent: dict[
                    tuple[float, float, float, float], list[Any]
                ] = {}
                for anchor in physical_anchors:
                    ax0, ay0, ax1, ay1 = anchor.bbox
                    cx = int(np.clip((ax0 + ax1) / 2, 0, w - 1))
                    cy = int(np.clip(h - (ay0 + ay1) / 2, 0, h - 1))
                    if box[cy, cx] != 0:
                        continue
                    parents = [
                        parent
                        for parent in protected_parents
                        if parent[0] <= (ax0 + ax1) / 2 <= parent[2]
                        and parent[1] <= (ay0 + ay1) / 2 <= parent[3]
                    ]
                    if not parents:
                        continue
                    parent = min(
                        parents,
                        key=lambda value: (value[2] - value[0])
                        * (value[3] - value[1]),
                    )
                    anchors_by_parent.setdefault(parent, []).append(anchor)
                anchors_with_parents = [
                    (anchor, parent)
                    for parent, parent_anchors in anchors_by_parent.items()
                    for anchor in group_translatable_line_clusters(
                        parent_anchors,
                        barriers=anchor_barriers,
                        page=page.pageno,
                        region_id=("protected", parent),
                        callout_id=("protected", parent),
                        metrics=reconstruction,
                        fragment_id_prefix=f"p{page.pageno + 1}-anchor",
                    )
                ]
            else:
                structural_anchors = structural_page_text_clusters(
                    page_words, preservation.kind
                )
                anchors_with_parents = [
                    (unit, None)
                    for index, anchor in enumerate(structural_anchors)
                    for unit in group_translatable_line_clusters(
                        [anchor],
                        page=page.pageno,
                        region_id=("structural", index),
                        structural_role=preservation.kind,
                        metrics=reconstruction,
                        fragment_id_prefix=f"p{page.pageno + 1}-structural-{index}",
                    )
                ]
            for anchor, parent in anchors_with_parents:
                ax0, ay0, ax1, ay1 = anchor.bbox
                if preservation is None:
                    assert parent is not None
                    source_regions = anchor.regions or (anchor.bbox,)
                    other_lines = [
                        line
                        for line in anchor_lines
                        if not any(tuple(line) == tuple(region) for region in source_regions)
                    ]
                    rx0,ry0,rx1,ry1=anchored_prose_bounds(
                        anchor.bbox,parent,other_lines,anchor_barriers
                    )
                else:
                    rx0, ry0, rx1, ry1 = anchor.render_bbox or anchor.bbox
                for sx0, sy0, sx1, sy1 in anchor.regions or (anchor.bbox,):
                    px0, py0, px1, py1 = (
                        int(np.clip(sx0-1,0,w-1)), int(np.clip(h-sy1-1,0,h-1)),
                        int(np.clip(sx1+1,0,w-1)), int(np.clip(h-sy0+1,0,h-1)),
                    )
                    box[py0:py1,px0:px1] = next_class
                page_bounds[next_class] = (rx0, page_height-ry1, rx1, page_height-ry0)
                if len(anchor.regions) > 1:
                    page_logical_classes.add(next_class)
                next_class += 1

            # Reapply exact metadata after carving translatable labels out
            # of protected header/footer regions. Character-level rectangles
            # preserve values without freezing Korean wording beside them.
            raw_blocks = source_page.get_text("rawdict")["blocks"]
            for metadata in immutable_metadata_regions(raw_blocks):
                mx0, my0, mx1, my1 = metadata.bbox
                # These syntax patterns describe headers, footers and title
                # blocks.  Applying them across body prose mistakes values such
                # as serial standards ("RS 232 / 422 / 485") for page metadata
                # and cuts one logical sentence into three translation units.
                if not (
                    my1 <= page_height * 0.2
                    or my0 >= page_height * 0.8
                ):
                    continue
                px0, py0, px1, py1 = (
                    int(np.clip(mx0 - 0.5, 0, w - 1)),
                    int(np.clip(h - my1 - 0.5, 0, h - 1)),
                    int(np.clip(mx1 + 0.5, 0, w - 1)),
                    int(np.clip(h - my0 + 0.5, 0, h - 1)),
                )
                box[py0:py1, px0:px1] = 0

            layout[page.pageno] = box
            if pageno in scanned_pages:
                device.scanned_pages.add(pageno)
            if pageno in pages_with_images:
                device.pages_with_images.add(pageno)
            page.page_xref = doc_zh.get_new_xref()
            doc_zh.update_object(page.page_xref, "<<>>")
            doc_zh.update_stream(page.page_xref, b"")
            doc_zh[page.pageno].set_contents(page.page_xref)
            interpreter.process_page(page)
            # Atomic page-level fallback can replay glyphs from several Form
            # XObjects. Expose those existing font objects to the page stream
            # under collision-resistant aliases chosen by the converter.
            expose_page_source_fonts(
                doc_zh,
                doc_zh[page.pageno].xref,
                device.source_font_xrefs_by_page.get(page.pageno, {}),
            )

    device.close()
    unresolved_segments = len(device.translation_failures)
    preserved_segments = device.extracted_segments - device.translatable_segments
    translated_segments = device.translatable_segments - unresolved_segments
    validate_segment_accounting(
        device.extracted_segments,
        translated_segments,
        preserved_segments,
        unresolved_segments,
    )
    reconstruction.assert_conservation()
    if device.translatable_segments > device.extracted_segments:
        raise RuntimeError("provider-bound units exceed reconstructed logical units")
    merged_unit_count = (
        reconstruction.logical_units - reconstruction.singleton_unit_count
    )
    singleton_unit_count = max(
        0, device.extracted_segments - merged_unit_count
    )
    unit_char_counts = device.logical_unit_char_counts

    metrics = device.translator.metrics()
    return obj_patch, TranslationReport(
        failures=device.translation_failures,
        reasons=device.failure_reasons,
        image_only_pages=device.image_only_pages,
        translatable_segments=device.translatable_segments,
        pages_processed=total_pages,
        total_segments=device.extracted_segments,
        translated_segments=translated_segments,
        preserved_segments=preserved_segments,
        unresolved_segments=unresolved_segments,
        unique_translation_units=device.unique_translation_units,
        raw_text_spans=reconstruction.raw_text_spans,
        candidate_fragments=reconstruction.candidate_fragments,
        assigned_candidate_fragments=reconstruction.assigned_candidate_fragments,
        unassigned_candidate_fragments=reconstruction.unassigned_candidate_fragments,
        duplicate_fragment_assignments=reconstruction.duplicate_fragment_assignments,
        logical_units=device.extracted_segments,
        provider_bound_units=device.translatable_segments,
        merged_fragment_count=reconstruction.merged_fragment_count,
        singleton_unit_count=singleton_unit_count,
        continuation_candidate_count=reconstruction.continuation_candidate_count,
        accepted_merge_count=reconstruction.accepted_merge_count,
        rejected_merge_count=reconstruction.rejected_merge_count,
        rejected_cross_cell=reconstruction.rejected_cross_cell,
        rejected_cross_column=reconstruction.rejected_cross_column,
        rejected_structural_role=reconstruction.rejected_structural_role,
        rejected_page_boundary=reconstruction.rejected_page_boundary,
        rejected_geometry=reconstruction.rejected_geometry,
        rejected_linguistic_boundary=reconstruction.rejected_linguistic_boundary,
        average_chars_per_unit=mean(unit_char_counts) if unit_char_counts else 0.0,
        median_chars_per_unit=median(unit_char_counts) if unit_char_counts else 0.0,
        max_chars_per_unit=max(unit_char_counts, default=0),
        cache_hits=int(metrics.get("cache_hits", 0)),
        provider_requests=int(metrics.get("translation_requests", 0)),
        retry_units=device.retry_units,
        handoff_table_hits=int(metrics.get("handoff_table_hits", 0)),
        handoff_misses=int(metrics.get("handoff_misses", 0)),
        translation_seconds=float(metrics.get("translation_seconds", 0.0)),
        used_output_font_names=tuple(sorted(device.used_output_font_names)),
    )


def translate_stream(
    stream: bytes,
    pages: Optional[list[int]] = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    skip_subset_fonts: bool = False,
    create_dual: bool = True,
    ignore_cache: bool = False,
    **kwarg: Any,
):
    started = time.perf_counter()
    source_size = len(stream)
    font_path = download_remote_fonts(lang_out.lower())
    style_paths = output_style_font_paths(lang_out.lower(), font_path)
    style_font_names = dict(STYLE_FONT_NAMES)
    style_fonts = {
        style: Font(style_font_names[style], path)
        for style, path in style_paths.items()
    }
    synthetic_styles = {
        style for style, path in style_paths.items() if style and path == style_paths[0]
    }
    base_font_list = [(name, None) for name in BASE14_STYLE_FONTS.values()]
    noto_name = NOTO_NAME
    noto = style_fonts[0]
    output_font_list = list(
        (style_font_names[style], path) for style, path in style_paths.items()
    )
    font_list = base_font_list + output_font_list

    doc_en = Document(stream=stream)
    stream = io.BytesIO()
    doc_en.save(stream)
    doc_zh = Document(stream=stream)
    if not create_dual:
        doc_en.close()
    page_count = doc_zh.page_count
    install_page_identities(doc_zh, envs)
    # Base-14 fonts must exist while pdfminer builds its font map. Unicode
    # output faces can wait until the converter tells us which styles it used.
    install_document_fonts(doc_zh, base_font_list)

    fp = io.BytesIO()

    doc_zh.save(fp)
    prepared = time.perf_counter()
    obj_patch, report = translate_patch(fp, **locals())
    patched = time.perf_counter()

    used_output_fonts = [
        font for font in output_font_list if font[0] in report.used_output_font_names
    ]
    install_document_fonts(doc_zh, used_output_fonts)

    for obj_id, ops_new in obj_patch.items():
        # ops_old=doc_en.xref_stream(obj_id)
        # print(obj_id)
        # print(ops_old)
        # print(ops_new.encode())
        doc_zh.update_stream(obj_id, ops_new.encode())

    if create_dual:
        doc_en.insert_file(doc_zh)
        for id in range(page_count):
            doc_en.move_page(page_count + id, id * 2 + 1)

    # Off for every document; see should_subset_fonts. It was also the
    # expensive half of finalizing a textbook, so dropping it costs nothing but
    # the size of the four embedded output faces.
    if should_subset_fonts(page_count, skip_subset_fonts, source_size):
        doc_zh.subset_fonts(fallback=True)
        if create_dual:
            doc_en.subset_fonts(fallback=True)
    write_options = pdf_write_options(page_count, source_size)
    font_references, unique_font_objects = output_font_resource_counts(
        doc_zh, {font[0] for font in base_font_list + used_output_fonts}
    )
    mono = doc_zh.write(**write_options)
    dual = (
        doc_en.write(**write_options)
        if create_dual
        else None
    )
    finished = time.perf_counter()
    report = replace(
        report,
        prepare_seconds=prepared - started,
        layout_seconds=max(0.0, patched - prepared - report.translation_seconds),
        render_seconds=finished - patched,
        total_seconds=finished - started,
        input_bytes=source_size,
        output_bytes=len(mono),
        output_font_references=font_references,
        unique_output_font_objects=unique_font_objects,
    )
    return (
        mono,
        dual,
        report,
    )


def convert_to_pdfa(input_path, output_path):
    """
    Convert PDF to PDF/A format

    Args:
        input_path: Path to source PDF file
        output_path: Path to save PDF/A file
    """
    from pikepdf import Dictionary, Name, Pdf

    # Open the PDF file
    pdf = Pdf.open(input_path)

    # Add PDF/A conformance metadata
    metadata = {
        "pdfa_part": "2",
        "pdfa_conformance": "B",
        "title": pdf.docinfo.get("/Title", ""),
        "author": pdf.docinfo.get("/Author", ""),
        "creator": "PDF Math Translate",
    }

    with pdf.open_metadata() as meta:
        meta.load_from_docinfo(pdf.docinfo)
        meta["pdfaid:part"] = metadata["pdfa_part"]
        meta["pdfaid:conformance"] = metadata["pdfa_conformance"]

    # Create OutputIntent dictionary
    output_intent = Dictionary(
        {
            "/Type": Name("/OutputIntent"),
            "/S": Name("/GTS_PDFA1"),
            "/OutputConditionIdentifier": "sRGB IEC61966-2.1",
            "/RegistryName": "http://www.color.org",
            "/Info": "sRGB IEC61966-2.1",
        }
    )

    # Add output intent to PDF root
    if "/OutputIntents" not in pdf.Root:
        pdf.Root.OutputIntents = [output_intent]
    else:
        pdf.Root.OutputIntents.append(output_intent)

    # Save as PDF/A
    pdf.save(output_path, linearize=True)
    pdf.close()


def translate(
    files: list[str],
    output: str = "",
    pages: Optional[list[int]] = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    compatible: bool = False,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    skip_subset_fonts: bool = False,
    ignore_cache: bool = False,
    **kwarg: Any,
):
    if not files:
        raise PDFValueError("No files to process.")

    missing_files = check_files(files)

    if missing_files:
        print("The following files do not exist:", file=sys.stderr)
        for file in missing_files:
            print(f"  {file}", file=sys.stderr)
        raise PDFValueError("Some files do not exist.")

    result_files = []

    for file in files:
        source_path = Path(file).resolve()
        if source_path.suffix.lower() != ".pdf":
            raise PDFValueError(f"Only PDF input is supported: {source_path}")
        filename = source_path.stem
        processing_path = source_path
        temporary_paths: list[Path] = []

        if not pymupdf_can_round_trip(source_path):
            logger.warning(
                "PDF structure issue detected in %s; translating a repaired temporary copy",
                source_path,
            )
            try:
                with tempfile.NamedTemporaryFile(suffix="-fixed.pdf", delete=False) as temporary:
                    fixed_path = Path(temporary.name)
                with pikepdf.open(source_path, suppress_warnings=True) as fixed_pdf:
                    fixed_pdf.save(fixed_path)
                processing_path = fixed_path
                temporary_paths.append(fixed_path)
            except Exception as error:
                raise PDFValueError(f"Could not repair PDF structure: {source_path}") from error
            if not pymupdf_can_round_trip(processing_path):
                # Say so here rather than letting the same MuPDF syntax error
                # resurface from deep inside the conversion, where it reads as
                # an engine bug instead of an unreadable source document.
                raise PDFValueError(
                    f"PDF structure is damaged beyond repair: {source_path}"
                )

        if compatible:
            with tempfile.NamedTemporaryFile(suffix="-pdfa.pdf", delete=False) as temporary:
                pdfa_path = Path(temporary.name)
            convert_to_pdfa(processing_path, pdfa_path)
            processing_path = pdfa_path
            temporary_paths.append(pdfa_path)

        s_raw = processing_path.read_bytes()
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)

        try:
            s_mono, _s_dual, report = translate_stream(
                s_raw,
                create_dual=False,
                **locals(),
            )
            if report.failures:
                logger.warning(
                    "%d of the segments in %s could not be translated and were left "
                    "in the source language (%s)",
                    len(report.failures),
                    source_path,
                    ", ".join(
                        f"{reason} x{count}"
                        for reason, count in report.reasons.most_common()
                    ),
                )
            file_mono = Path(output) / f"{filename}-mono.pdf"
            doc_mono = open(file_mono, "wb")
            doc_mono.write(s_mono)
            doc_mono.close()
            result_files.append((str(file_mono), report))
        except Exception as error:
            raise PDFValueError(f"Failed to translate {source_path}") from error

    return result_files


def download_remote_fonts(lang: str):
    lang = lang.lower()
    LANG_NAME_MAP = {
        **{la: "GoNotoKurrent-Regular.ttf" for la in noto_list},
        **{
            la: f"SourceHanSerif{region}-Regular.ttf"
            for region, langs in {
                "CN": ["zh-cn", "zh-hans", "zh"],
                "TW": ["zh-tw", "zh-hant"],
                "JP": ["ja"],
                "KR": ["ko"],
            }.items()
            for la in langs
        },
    }

    # Use Times New Roman for Vietnamese
    if lang == "vi":
        times_path = Path("C:/Windows/Fonts/times.ttf")
        if times_path.exists():
            logger.info(f"use font: {times_path.as_posix()}")
            return times_path.as_posix()

    font_name = LANG_NAME_MAP.get(lang, "GoNotoKurrent-Regular.ttf")

    # docker
    font_path = os.environ.get("NOTO_FONT_PATH", Path("/app", font_name).as_posix())
    if not Path(font_path).exists():
        font_path, _ = get_font_and_metadata(font_name)
        font_path = font_path.as_posix()

    logger.info(f"use font: {font_path}")

    return font_path
