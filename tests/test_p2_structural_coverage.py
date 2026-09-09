from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pymupdf

from pdf2zh.high_level import translate_stream
from pdf2zh.rules import (
    anchored_translatable_lines,
    structural_page_text_clusters,
)


def _dict_line(text: str, y: float) -> dict:
    return {
        "dir": (1.0, 0.0),
        "bbox": (20.0, y, 240.0, y + 12.0),
        "spans": [{"text": text}],
    }


def _words(lines: list[str]) -> list[tuple]:
    result = []
    for block_number, line in enumerate(lines):
        x = 20.0
        for word_number, token in enumerate(line.split()):
            width = max(8.0, len(token) * 5.0)
            result.append(
                (
                    x,
                    20.0 + block_number * 20.0,
                    x + width,
                    32.0 + block_number * 20.0,
                    token,
                    block_number,
                    0,
                    word_number,
                )
            )
            x += width + 5.0
    return result


class _FixedLayoutModel:
    def __init__(self, regions: list[tuple[str, tuple[float, float, float, float]]]):
        names = {index: name for index, (name, _bounds) in enumerate(regions)}
        boxes = [
            SimpleNamespace(cls=index, xyxy=np.array([bounds], dtype=float))
            for index, (_name, bounds) in enumerate(regions)
        ]
        self.layout = SimpleNamespace(names=names, boxes=boxes)

    def predict(self, *_args, **_kwargs):
        return [self.layout]


def _write_pdf(path: Path, lines: list[tuple[float, str]]) -> None:
    document = pymupdf.open()
    page = document.new_page(width=420, height=300)
    for y, text in lines:
        page.insert_text((30, y), text, fontsize=12, fontname="tiro")
    document.save(path)
    document.close()


def _write_multiscript_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=420, height=300)
    page.insert_text((30, 60), "안전사양 확인", fontsize=12, fontname="korea")
    page.insert_text((30, 120), "安全要求确认", fontsize=12, fontname="china-s")
    document.save(path)
    document.close()


def _handoff_records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class ProtectedRegionCoverageTests(unittest.TestCase):
    def test_shared_anchor_policy_covers_english_korean_and_chinese(self):
        lines = [
            "Safety requirements",
            "Check robot position",
            "Wait to next step",
            "안전사양",
            "설정값 변경 권장",
            "运转禁止",
            "安全要求",
            "Inovance Technology Safety Instructions",
            "D100",
            "IRCB50x",
            "Rev. 12.0",
            "42",
            "F = m * a",
        ]
        anchors = anchored_translatable_lines(
            [{"lines": [_dict_line(text, 20.0 * index) for index, text in enumerate(lines)]}]
        )
        self.assertEqual(
            [anchor.text for anchor in anchors],
            lines[:8],
        )


class StructuralPagePolicyTests(unittest.TestCase):
    def test_toc_translates_entries_but_excludes_leaders_and_locators(self):
        clusters = structural_page_text_clusters(
            _words(
                [
                    "Contents",
                    "1 Overview ........ 3",
                    "2 Motion Instructions ........ 42",
                    "3 D100 Parameters ........ 57",
                ]
            ),
            "TOC",
        )
        self.assertEqual(
            [cluster.text for cluster in clusters],
            ["Contents", "Overview", "Motion Instructions", "D100 Parameters"],
        )
        self.assertFalse(any("........" in cluster.text for cluster in clusters))
        self.assertFalse(any(cluster.text.endswith((" 3", " 42", " 57")) for cluster in clusters))

    def test_index_translates_terms_but_excludes_locators_and_code_only_rows(self):
        clusters = structural_page_text_clusters(
            _words(
                [
                    "Index",
                    "Safety requirements ........ 31, 42",
                    "D100 ........ 55",
                ]
            ),
            "INDEX",
        )
        self.assertEqual(
            [cluster.text for cluster in clusters],
            ["Index", "Safety requirements"],
        )

    def test_nomenclature_preserves_symbols_and_translates_definitions(self):
        clusters = structural_page_text_clusters(
            _words(
                [
                    "Nomenclature",
                    "v Velocity",
                    "P Pressure",
                    "D100 Register value",
                ]
            ),
            "NOMENCLATURE",
        )
        self.assertEqual(
            [cluster.text for cluster in clusters],
            ["Nomenclature", "Velocity", "Pressure", "Register value"],
        )

    def test_reference_heading_translates_while_citation_identity_is_excluded(self):
        clusters = structural_page_text_clusters(
            _words(
                [
                    "References",
                    "[1] John Smith, Example Technical Manual, 2024.",
                    "Smith, J. (2023). Another Technical Manual.",
                    "DOI: 10.xxxx/example",
                    "Consult the safety appendix before operation.",
                ]
            ),
            "REFERENCES",
        )
        self.assertEqual(
            [cluster.text for cluster in clusters],
            ["References", "Consult the safety appendix before operation."],
        )

    def test_each_selected_phrase_keeps_one_source_line_bound(self):
        clusters = structural_page_text_clusters(
            _words(["1 Overview ........ 3", "2 Motion Instructions ........ 42"]),
            "TOC",
        )
        self.assertEqual(len(clusters), 2)
        self.assertLess(clusters[0].bbox[3], clusters[1].bbox[1])


class NativeCoverageIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_figure_unmatched_table_and_header_footer_emit_shared_prose_only(self):
        source = self.root / "protected-regions.pdf"
        _write_pdf(
            source,
            [
                (30, "Safety requirements"),
                (48, "Rev. 12.0"),
                (100, "Check robot position"),
                (118, "IRCB50x"),
                (170, "Wait to next step"),
                (188, "D100"),
                (270, "Safety notice"),
            ],
        )
        model = _FixedLayoutModel(
            [
                ("abandon", (10, 5, 410, 60)),
                ("figure", (10, 70, 410, 130)),
                ("table", (10, 140, 410, 200)),
                ("abandon", (10, 240, 410, 290)),
            ]
        )
        misses = self.root / "misses.jsonl"
        _mono, _dual, report = translate_stream(
            source.read_bytes(),
            lang_in="en",
            lang_out="vi",
            service="handoff",
            thread=1,
            model=model,
            envs={"segments_out": str(misses)},
            create_dual=False,
            ignore_cache=True,
        )
        emitted = [record["src"].strip() for record in _handoff_records(misses)]
        self.assertEqual(
            emitted,
            [
                "Safety requirements",
                "Check robot position",
                "Wait to next step",
                "Safety notice",
            ],
        )
        self.assertEqual(report.translatable_segments, 4)
        self.assertEqual(report.unresolved_segments, 4)
        self.assertFalse({"Rev. 12.0", "IRCB50x", "D100"} & set(emitted))

    def test_source_script_missing_from_target_font_still_reaches_handoff(self):
        source = self.root / "multiscript-source.pdf"
        _write_multiscript_pdf(source)
        misses = self.root / "misses.jsonl"
        mono, _dual, report = translate_stream(
            source.read_bytes(),
            lang_in="auto",
            lang_out="vi",
            service="handoff",
            thread=1,
            model=_FixedLayoutModel([]),
            envs={"segments_out": str(misses)},
            create_dual=False,
            ignore_cache=True,
        )

        emitted = "\n".join(record["src"] for record in _handoff_records(misses))
        self.assertIn("안전사양 확인", emitted)
        self.assertIn("安全要求确认", emitted)
        self.assertEqual(report.translatable_segments, 2)
        self.assertEqual(report.unresolved_segments, 2)

        target = pymupdf.open(stream=mono, filetype="pdf")
        self.assertEqual(target.page_count, 1)
        self.assertEqual(target[0].rect, pymupdf.Rect(0, 0, 420, 300))
        self.assertEqual(target[0].rotation, 0)
        target.close()

    def test_toc_native_round_trip_translates_once_and_keeps_fixed_geometry(self):
        source = self.root / "toc-source.pdf"
        _write_pdf(
            source,
            [
                (40, "Contents"),
                (80, "1 Overview ........ 3"),
                (120, "2 Motion Instructions ........ 42"),
                (160, "3 D100 Parameters ........ 57"),
            ],
        )
        model = _FixedLayoutModel([])
        misses = self.root / "misses.jsonl"
        _mono, _dual, first = translate_stream(
            source.read_bytes(),
            lang_in="en",
            lang_out="vi",
            service="handoff",
            thread=1,
            model=model,
            envs={"segments_out": str(misses)},
            create_dual=False,
            ignore_cache=True,
        )
        translations = {
            "Contents": "Mục lục",
            "Overview": "Tổng quan",
            "Motion Instructions": "Hướng dẫn chuyển động",
            "D100 Parameters": "Tham số D100",
        }
        records = _handoff_records(misses)
        for record in records:
            record["dst"] = translations[record["src"].strip()]
        supplied = self.root / "translations.jsonl"
        supplied.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
        mono, _dual, final = translate_stream(
            source.read_bytes(),
            lang_in="en",
            lang_out="vi",
            service="handoff",
            thread=1,
            model=model,
            envs={"segments_in": str(supplied)},
            create_dual=False,
            ignore_cache=True,
        )

        target = pymupdf.open(stream=mono, filetype="pdf")
        original = pymupdf.open(source)
        text = target[0].get_text("text")
        self.assertEqual(target.page_count, original.page_count)
        self.assertEqual(target[0].rect, original[0].rect)
        self.assertEqual(target[0].rotation, original[0].rotation)
        self.assertEqual(final.translated_segments, 4)
        self.assertEqual(final.preserved_segments, 4)
        self.assertEqual(final.unresolved_segments, 0)
        self.assertEqual(
            final.translated_segments + final.preserved_segments + final.unresolved_segments,
            final.total_segments,
        )
        for translated in translations.values():
            self.assertEqual(text.count(translated), 1)
        self.assertNotIn("Overview", text)
        self.assertNotIn("Motion Instructions", text)
        self.assertEqual(text.count("........"), 3)
        self.assertRegex(text, r"\bD100\b")
        for locator in ("3", "42", "57"):
            self.assertRegex(text, rf"\b{locator}\b")
        for block in target[0].get_text("blocks"):
            x0, y0, x1, y1 = block[:4]
            self.assertGreaterEqual(min(x0, y0), 0)
            self.assertLessEqual(x1, target[0].rect.width)
            self.assertLessEqual(y1, target[0].rect.height)
        pixmap = target[0].get_pixmap(alpha=False)
        self.assertEqual((pixmap.width, pixmap.height), (420, 300))
        self.assertTrue(any(value < 250 for value in pixmap.samples))
        original.close()
        target.close()
        self.assertEqual(first.translatable_segments, 4)


if __name__ == "__main__":
    unittest.main()
