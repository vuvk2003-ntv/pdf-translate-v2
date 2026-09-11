"""Regressions for the Form XObject and Type 3 layout used by the J1C manual."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pymupdf

from pdf2zh.converter import layout_class_for_bounds
from pdf2zh.high_level import translate_stream
from pdf2zh.layout_qa import classify_cjk_residual, run_layout_qa
from pdf2zh.rules import (
    TableTextCluster,
    anchored_translatable_lines,
    group_translatable_line_clusters,
    structural_page_text_clusters,
    upright_line_bounds,
)
from pdf2zh.translator import mask_provider_literals, restore_provider_literals


def _type3_line(text: str, y: float = 100.0) -> dict:
    return {
        "dir": (1.0, 0.0),
        "bbox": (20.0, y, 220.0, y + 120.0),
        "spans": [
            {
                "text": text,
                "bbox": (20.0, y, 220.0, y + 120.0),
                "origin": (20.0, y + 10.0),
                "size": 11.0,
                "ascender": 0.9,
                "descender": -0.2,
            }
        ],
    }


def _toc_words() -> list[tuple]:
    tokens = ("3", "Protocol", "Capture", "........", "18")
    words = []
    x = 20.0
    for index, token in enumerate(tokens):
        width = max(8.0, len(token) * 5.0)
        words.append((x, 30.0, x + width, 42.0, token, 0, 0, index))
        x += width + 5.0
    return words


class J1CGeometryTests(unittest.TestCase):
    def test_pathological_type3_bbox_uses_the_visual_baseline_band(self):
        bounds = upright_line_bounds(_type3_line("시리얼 통신 프로토콜"))
        self.assertIsNotNone(bounds)
        self.assertAlmostEqual(bounds[1], 100.1, places=1)
        self.assertAlmostEqual(bounds[3], 112.2, places=1)
        self.assertLess(bounds[3] - bounds[1], 15.0)

    def test_anchor_ownership_uses_the_normalized_type3_region(self):
        anchors = anchored_translatable_lines(
            [{"lines": [_type3_line("시리얼 통신 프로토콜 캡쳐 모드")]}]
        )
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].regions, (anchors[0].bbox,))
        self.assertLess(anchors[0].bbox[3] - anchors[0].bbox[1], 15.0)

    def test_three_physical_lines_form_one_logical_occurrence(self):
        clusters = [
            TableTextCluster((20, 10, 230, 20), "특정 장비간에 시리얼 (", (), ((20, 10, 230, 20),)),
            TableTextCluster((20, 22, 245, 32), "RS 232 / 422 / 485 ) 통신 중 일 때,", (), ((20, 22, 245, 32),)),
            TableTextCluster((20, 34, 220, 44), "J1C 를 이용하여 캡쳐한다.", (), ((20, 34, 220, 44),)),
        ]
        grouped = group_translatable_line_clusters(clusters)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(len(grouped[0].regions), 3)
        self.assertIn("RS 232 / 422 / 485", grouped[0].text)

    def test_sentence_list_and_rule_boundaries_remain_separate(self):
        punctuation = [
            TableTextCluster((20, 10, 180, 20), "첫 문장이 끝난다.", ()),
            TableTextCluster((20, 22, 180, 32), "다음 문장이 시작된다", ()),
        ]
        bullet = [
            TableTextCluster((20, 10, 180, 20), "첫 번째 설명", ()),
            TableTextCluster((20, 22, 180, 32), "- 두 번째 설명", ()),
        ]
        ruled = [
            TableTextCluster((20, 10, 180, 20), "첫 번째 설명", ()),
            TableTextCluster((20, 22, 180, 32), "계속되는 설명", ()),
        ]
        self.assertEqual(len(group_translatable_line_clusters(punctuation)), 2)
        self.assertEqual(len(group_translatable_line_clusters(bullet)), 2)
        self.assertEqual(
            len(group_translatable_line_clusters(ruled, barriers=[(10, 21, 200, 21)])),
            2,
        )

    def test_glyph_class_comes_from_its_interior(self):
        layout = np.zeros((30, 30), dtype=int)
        layout[10:20, 10:20] = 7
        layout[15, 15] = 0
        self.assertEqual(layout_class_for_bounds(layout, (10, 10, 20, 20)), 7)
        self.assertEqual(layout_class_for_bounds(layout, (0, 0, 5, 5)), 0)

    def test_toc_render_bound_can_use_space_before_the_leader(self):
        cluster = structural_page_text_clusters(_toc_words(), "TOC")[0]
        self.assertEqual(cluster.text, "Protocol Capture")
        self.assertIsNotNone(cluster.render_bbox)
        self.assertGreater(cluster.render_bbox[2], cluster.bbox[2])
        self.assertLess(cluster.render_bbox[2], _toc_words()[3][0])


class J1CLiteralTests(unittest.TestCase):
    def test_provider_masks_path_ui_label_and_document_label_together(self):
        source = (
            "[ C:\\J1C\\ 오늘날짜-시’분’초’.txt ]에서 “ Send HEX ” 버튼을 누른다. "
            "Page No 17"
        )
        masked, masks = mask_provider_literals(source)
        self.assertEqual({mask.kind for mask in masks}, {
            "Windows path",
            "Korean UI label",
            "document-control label",
        })
        self.assertNotIn("오늘날짜", masked)
        self.assertNotIn("Send HEX", masked)
        self.assertNotIn("Page No", masked)
        self.assertEqual(restore_provider_literals(masked, masks), source)

    def test_cjk_inside_a_validated_windows_path_is_allowed(self):
        self.assertEqual(
            classify_cjk_residual("C:\\J1C\\오늘날짜-시’분’초’.txt"),
            "ALLOWED_TECHNICAL_LITERAL",
        )
        self.assertEqual(classify_cjk_residual("오늘날짜를 입력한다"), "UNTRANSLATED_PROSE")


class _TextRegionModel:
    def predict(self, *_args, **_kwargs):
        layout = SimpleNamespace(
            names={0: "text"},
            boxes=[
                SimpleNamespace(
                    cls=0,
                    xyxy=np.array([[0.0, 0.0, 420.0, 150.0]], dtype=float),
                )
            ],
        )
        return [layout]


class J1CFormXObjectIntegrationTests(unittest.TestCase):
    SOURCE = "시리얼 통신 프로토콜 캡쳐 모드 동작을 확인한다"
    TARGET = "Kiểm tra hoạt động của chế độ chụp giao thức truyền thông nối tiếp."

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "separate-form-lines.pdf"
        fragments = []
        for index, text in enumerate((
            "시리얼 통신 프로토콜 캡쳐",
            "모드 동작을 확인한다",
        )):
            fragment = pymupdf.open()
            page = fragment.new_page(width=380, height=40)
            page.insert_text((5, 25), text, fontsize=11, fontname="korea")
            path = self.root / f"fragment-{index}.pdf"
            fragment.save(path)
            fragment.close()
            fragments.append(path)

        document = pymupdf.open()
        page = document.new_page(width=420, height=300)
        for index, path in enumerate(fragments):
            fragment = pymupdf.open(path)
            page.show_pdf_page(
                pymupdf.Rect(20, 40 + index * 25, 400, 80 + index * 25),
                fragment,
                0,
            )
            fragment.close()
        document.save(self.source)
        document.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _translate(self, envs: dict[str, str]):
        return translate_stream(
            self.source.read_bytes(),
            lang_in="auto",
            lang_out="vi",
            service="handoff",
            thread=1,
            model=_TextRegionModel(),
            envs=envs,
            create_dual=False,
            ignore_cache=True,
        )

    def test_separate_forms_emit_and_fallback_as_one_complete_occurrence(self):
        source_doc = pymupdf.open(self.source)
        self.assertGreaterEqual(len(source_doc[0].get_xobjects()), 2)
        source_doc.close()

        misses = self.root / "misses.jsonl"
        mono, _dual, report = self._translate({"segments_out": str(misses)})
        records = [json.loads(line) for line in misses.read_text("utf-8").splitlines()]
        self.assertEqual([record["src"] for record in records], [self.SOURCE])
        self.assertEqual((report.translatable_segments, report.unresolved_segments), (1, 1))
        self.assertEqual(report.raw_text_spans, 2)
        self.assertEqual(report.candidate_fragments, 2)
        self.assertEqual(report.assigned_candidate_fragments, 2)
        self.assertEqual(report.logical_units, 1)
        self.assertEqual(report.provider_bound_units, 1)
        self.assertEqual(report.merged_fragment_count, 1)
        self.assertEqual(report.unassigned_candidate_fragments, 0)
        self.assertEqual(report.duplicate_fragment_assignments, 0)

        fallback = pymupdf.open(stream=mono, filetype="pdf")
        self.assertEqual(" ".join(fallback[0].get_text("text").split()), self.SOURCE)
        fallback.close()
        fallback_path = self.root / "fallback.pdf"
        fallback_path.write_bytes(mono)
        self.assertFalse(run_layout_qa(self.source, fallback_path).warnings)

        records[0]["dst"] = self.TARGET
        supplied = self.root / "translations.jsonl"
        supplied.write_text(
            json.dumps(records[0], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        translated, _dual, final = self._translate({"segments_in": str(supplied)})
        target = pymupdf.open(stream=translated, filetype="pdf")
        target_text = " ".join(target[0].get_text("text").split())
        target.close()
        self.assertEqual(target_text, self.TARGET)
        self.assertNotRegex(target_text, r"[\uac00-\ud7a3]")
        self.assertEqual((final.translated_segments, final.unresolved_segments), (1, 0))


if __name__ == "__main__":
    unittest.main()
