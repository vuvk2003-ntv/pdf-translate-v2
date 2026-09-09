from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf

from pdf2zh.layout_qa import SpanGeometry, _page_spans, analyze_layout_page


class LayoutQATests(unittest.TestCase):
    PAGE = (0.0, 0.0, 200.0, 200.0)

    def test_new_overlap_with_retained_text_is_reported(self):
        source = [SpanGeometry("D100", (10, 10, 40, 20), 10, 0, 0)]
        target = source + [SpanGeometry("Áp suất", (20, 10, 70, 20), 10, 1, 0)]
        kinds = {warning.kind for warning in analyze_layout_page(source, target, [], self.PAGE, 1)}
        self.assertIn("translated_retained_overlap", kinds)

    def test_source_overlap_is_not_reported_as_new(self):
        spans = [
            SpanGeometry("A", (10, 10, 40, 20), 10, 0, 0),
            SpanGeometry("B", (20, 10, 50, 20), 10, 1, 0),
        ]
        self.assertFalse(analyze_layout_page(spans, spans, [], self.PAGE, 1))

    def test_cross_cell_spill_and_page_overflow_are_reported(self):
        source = [SpanGeometry("Label", (10, 10, 30, 20), 10)]
        target = [SpanGeometry("Nhãn rất dài", (10, 10, 65, 20), 10)]
        kinds = {warning.kind for warning in analyze_layout_page(
            source, target, [(0, 0, 50, 30)], (0, 0, 40, 40), 3
        )}
        self.assertIn("table_cell_spill", kinds)
        self.assertIn("page_out_of_bounds", kinds)

    def test_large_empty_cell_with_half_size_text_is_suspicious(self):
        source = [SpanGeometry("Component", (10, 10, 70, 22), 10)]
        target = [SpanGeometry("Thành phần", (10, 10, 35, 16), 5)]
        kinds = {warning.kind for warning in analyze_layout_page(
            source, target, [(0, 0, 120, 60)], self.PAGE, 4
        )}
        self.assertIn("suspicious_micro_font", kinds)

    def test_same_occurrence_drawn_twice_is_reported_by_text_and_geometry(self):
        source = [SpanGeometry("Release Mode", (10, 10, 80, 20), 10)]
        target = [
            SpanGeometry("Chế độ phát hành", (10, 10, 90, 20), 10, 0, 0),
            SpanGeometry("Chế độ phát hành", (10, 10, 90, 20), 10, 1, 0),
        ]
        kinds = {warning.kind for warning in analyze_layout_page(source, target, [], self.PAGE, 5)}
        self.assertIn("duplicate_local_render", kinds)

    def test_rotated_watermark_is_excluded_from_flow_text(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "rotated.pdf"
            document = pymupdf.open()
            page = document.new_page(width=200, height=200)
            page.insert_text((20, 30), "Table label")
            page.insert_text((80, 160), "CONFIDENTIAL", rotate=90)
            document.save(path)
            document.close()
            with pymupdf.open(path) as reopened:
                texts = [span.normalized_text for span in _page_spans(reopened[0])]
        self.assertEqual(texts, ["Table label"])


if __name__ == "__main__":
    unittest.main()
