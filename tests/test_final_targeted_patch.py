from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pymupdf

from pdf2zh.handoff import assess_handoff_translations
from pdf2zh.layout_qa import analyze_metadata_page, classify_cjk_residual
from pdf2zh.rules import immutable_metadata_regions


def write_jsonl(path: Path, records: list[dict[str, str]]) -> Path:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


class DuplicateOccurrenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def assess(self, source: str, target: str):
        records = [{"segment_id": "occurrence-1", "src": source}]
        translations = write_jsonl(
            self.root / "translations.jsonl",
            [{"segment_id": "occurrence-1", "src": source, "dst": target}],
        )
        return assess_handoff_translations(records, translations)

    def test_repeated_full_target_for_one_occurrence_is_retried(self):
        sentence = (
            "Nếu không thể dùng Gap Gauge Ring, sau Hand Tight phải tuân thủ "
            "tiêu chuẩn quay 1-1/4 vòng."
        )
        result = self.assess(
            "Gap Gauge Ring 적용 불가 시 Hand Tight 후 1-1/4 회전 기준 준수",
            sentence + sentence,
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.retry[0]["retry_reason"], "DuplicateTargetFragmentError")

    def test_full_target_plus_redundant_child_fragment_is_retried(self):
        result = self.assess(
            "USC Unit의 경우 On/Off 진동에 따른 Hose Risk 고려한 곡률 확보할 것.",
            "Với USC Unit, phải bảo đảm bán kính cong có xét Risk rách Hose do rung "
            "khi On/Off. Phải bảo đảm bán kính cong.",
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.retry[0]["retry_reason"], "DuplicateTargetFragmentError")

    def test_same_translation_in_distinct_occurrences_is_kept_twice(self):
        records = [
            {"segment_id": "cell-left", "src": "항목 이름"},
            {"segment_id": "cell-right", "src": "항목 이름"},
        ]
        translations = write_jsonl(
            self.root / "translations.jsonl",
            [
                {"segment_id": record["segment_id"], "src": record["src"], "dst": "Tên hạng mục"}
                for record in records
            ],
        )
        result = assess_handoff_translations(records, translations)
        self.assertEqual(len(result.accepted), 2)
        self.assertFalse(result.retry)


class MetadataProtectionTests(unittest.TestCase):
    def test_only_immutable_values_are_selected_from_mixed_korean_lines(self):
        def raw_line(text: str, y: float) -> dict:
            return {
                "spans": [
                    {
                        "chars": [
                            {"c": character, "bbox": (index * 5, y, index * 5 + 5, y + 10)}
                            for index, character in enumerate(text)
                        ]
                    }
                ]
            }

        blocks = [
            {
                "lines": [
                    raw_line("문서유형 : 규격    제정일자 : 2007. 07. 10", 20),
                    raw_line("사내 한(2)54/117", 760),
                    raw_line("SHSEO79 2025-09-19 13:11:27", 400),
                ]
            }
        ]
        values = [
            region.text
            for region in immutable_metadata_regions(blocks)
        ]
        self.assertIn("2007. 07. 10", values)
        self.assertIn("54/117", values)
        self.assertIn("SHSEO79", values)
        self.assertIn("2025-09-19", values)
        self.assertIn("13:11:27", values)
        self.assertFalse(any("문서유형" in value or "사내" in value for value in values))

    def test_metadata_qa_keeps_categories_separate(self):
        source = pymupdf.open()
        source_page = source.new_page(width=600, height=800)
        source_page.insert_text((20, 30), "CONFIDENTIAL LG Display Co., Ltd 2025")
        source_page.insert_text((360, 30), "KB-SP-TCCO-COSP0012")
        source_page.insert_text((360, 60), "Rev. : 12.0")
        source_page.insert_text((300, 400), "SHSEO79 2025-09-19 13:11:27", rotate=90)
        source_page.insert_text((20, 780), "사내 한", fontname="korea")
        source_page.insert_text((270, 780), "1/117")

        target = pymupdf.open()
        target_page = target.new_page(width=600, height=800)
        target_page.insert_text((20, 30), "CONFIDENTIAL LG Display Co., Ltd 2025")
        target_page.insert_text((360, 30), "KB-SP-TCCO-COSP0012")
        target_page.insert_text((360, 60), "Rev. : 12.0")
        target_page.insert_text((300, 400), "SHSEO79 2025-", rotate=90)
        target_page.insert_text((20, 780), "사내 한", fontname="korea")
        target_page.insert_text((270, 780), "2/117")

        kinds = {warning.kind for warning in analyze_metadata_page(source_page, target_page, 1)}
        source.close()
        target.close()
        self.assertIn("immutable_metadata_mismatch", kinds)
        self.assertIn("metadata_fragmentation", kinds)
        self.assertIn("page_number_mismatch", kinds)
        self.assertIn("header_footer_untranslated_prose", kinds)

    def test_explicit_proper_names_and_official_tokens_are_allowed(self):
        self.assertEqual(
            classify_cjk_residual("원에스티", proper_names={"원에스티"}),
            "ALLOWED_PROPER_NAME",
        )
        self.assertEqual(
            classify_cjk_residual("JST社", official_tokens={"JST社"}),
            "ALLOWED_OFFICIAL_TOKEN",
        )
        self.assertEqual(classify_cjk_residual("안전사양"), "UNTRANSLATED_PROSE")


if __name__ == "__main__":
    unittest.main()
