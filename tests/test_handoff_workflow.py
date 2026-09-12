from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pdf2zh.handoff import (
    assess_handoff_translations,
    build_handoff_batches,
    load_source_segments,
)
from pdf2zh.translator import segment_identifier


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


class HandoffWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def _sources(self, count: int) -> list[dict[str, str]]:
        return [
            {"segment_id": segment_identifier(f"Source sentence {index}."), "src": f"Source sentence {index}."}
            for index in range(count)
        ]

    def test_batch_mapping_respects_count_and_character_limits(self):
        sources = self._sources(65)
        batches = build_handoff_batches(sources, max_segments=30, max_characters=10_000)
        self.assertEqual([len(batch["segments"]) for batch in batches], [30, 30, 5])
        identifiers = [
            item["segment_id"] for batch in batches for item in batch["segments"]
        ]
        self.assertEqual(identifiers, [record["segment_id"] for record in sources])
        self.assertTrue(all("instructions" in batch for batch in batches))
        self.assertTrue(all("instructions" not in item for batch in batches for item in batch["segments"]))
        self.assertTrue(all(batch["attempt"] == 1 for batch in batches))

    def test_retry_attempts_have_a_hard_limit(self):
        with self.assertRaises(ValueError):
            build_handoff_batches(self._sources(1), attempt=4)

    def test_exact_source_duplicates_are_emitted_once(self):
        source = "Repeated document warning with enough context."
        path = write_jsonl(
            self.root / "segments.jsonl",
            [
                {"segment_id": segment_identifier(source), "src": source},
                {"segment_id": segment_identifier(source), "src": source},
            ],
        )
        self.assertEqual(len(load_source_segments(path)), 1)

    def test_ambiguous_duplicate_source_is_kept_when_identities_differ(self):
        path = write_jsonl(
            self.root / "segments.jsonl",
            [
                {"segment_id": "manual-heading", "src": "Manual"},
                {"segment_id": "operation-mode", "src": "Manual"},
            ],
        )
        self.assertEqual(len(load_source_segments(path)), 2)

    def test_patch_c_identity_metadata_survives_batch_acceptance_and_retry(self):
        extraction_records = [
            {
                "segment_id": "unit-accepted",
                "logical_unit_id": "unit-accepted",
                "occurrence_id": "occurrence-accepted",
                "source_fragment_ids": ["fragment-a", "fragment-b"],
                "src": "Source sentence 1.",
            },
            {
                "segment_id": "unit-retry",
                "logical_unit_id": "unit-retry",
                "occurrence_id": "occurrence-retry",
                "source_fragment_ids": ["fragment-c"],
                "src": "Source sentence 2.",
            },
        ]
        source_path = write_jsonl(self.root / "identity-segments.jsonl", extraction_records)
        sources = load_source_segments(source_path)
        batch_records = build_handoff_batches(sources)[0]["segments"]
        for expected, actual in zip(extraction_records, batch_records):
            for name in ("logical_unit_id", "occurrence_id", "source_fragment_ids"):
                self.assertEqual(actual[name], expected[name])

        translations_path = write_jsonl(
            self.root / "identity-translations.jsonl",
            [
                {
                    "segment_id": "unit-accepted",
                    "src": "Source sentence 1.",
                    "dst": "Câu nguồn 1.",
                }
            ],
        )
        assessment = assess_handoff_translations(sources, translations_path)
        self.assertEqual(len(assessment.accepted), 1)
        self.assertEqual(len(assessment.retry), 1)
        for expected, actual in zip(
            extraction_records, (assessment.accepted[0], assessment.retry[0])
        ):
            for name in ("logical_unit_id", "occurrence_id", "source_fragment_ids"):
                self.assertEqual(actual[name], expected[name])
        retry_batch_record = build_handoff_batches(assessment.retry, attempt=2)[0][
            "segments"
        ][0]
        for name in ("logical_unit_id", "occurrence_id", "source_fragment_ids"):
            self.assertEqual(retry_batch_record[name], extraction_records[1][name])

    def test_patch_c_identity_metadata_rejects_invalid_values(self):
        invalid_records = (
            {
                "segment_id": "unit-1",
                "logical_unit_id": "different-unit",
                "src": "Source sentence.",
            },
            {
                "segment_id": "unit-1",
                "source_fragment_ids": ["fragment-1", ""],
                "src": "Source sentence.",
            },
        )
        for index, record in enumerate(invalid_records):
            with self.subTest(index=index):
                path = write_jsonl(self.root / f"invalid-identity-{index}.jsonl", [record])
                with self.assertRaises(ValueError):
                    load_source_segments(path)
        conflicting = write_jsonl(
            self.root / "conflicting-identity.jsonl",
            [
                {
                    "segment_id": "unit-1",
                    "logical_unit_id": "unit-1",
                    "occurrence_id": "occurrence-1",
                    "source_fragment_ids": ["fragment-1"],
                    "src": "Source sentence.",
                },
                {
                    "segment_id": "unit-1",
                    "logical_unit_id": "unit-1",
                    "occurrence_id": "occurrence-2",
                    "source_fragment_ids": ["fragment-1"],
                    "src": "Source sentence.",
                },
            ],
        )
        with self.assertRaises(ValueError):
            load_source_segments(conflicting)

    def test_partial_retry_accepts_valid_units_and_retries_only_failures(self):
        sources = self._sources(30)
        translations = [
            {
                "segment_id": record["segment_id"],
                "src": record["src"],
                "dst": f"Câu nguồn {index}.",
            }
            for index, record in enumerate(sources[:29])
        ]
        translations[4]["dst"] = "Câu nguồn 999."
        path = write_jsonl(self.root / "translations.jsonl", translations)
        assessment = assess_handoff_translations(sources, path)
        self.assertEqual(len(assessment.accepted), 28)
        self.assertEqual(len(assessment.retry), 2)
        self.assertEqual(
            {record["segment_id"] for record in assessment.retry},
            {sources[4]["segment_id"], sources[29]["segment_id"]},
        )

    def test_confirmed_document_terminology_covers_t1_through_t8(self):
        terminology = {
            "Integration Test": "kiểm thử tích hợp",
            "대응 필요": "cần xử lý",
            "Manual": "hướng dẫn",
            "공용화": "tiêu chuẩn hóa dùng chung",
            "Utility": "tiện ích nhà máy",
            "Qualification": "đánh giá năng lực",
            "검수": "nghiệm thu",
            "시운전": "chạy thử",
        }
        sources = [
            {"segment_id": segment_identifier(term), "src": term}
            for term in terminology
        ]
        translations = [
            {
                "segment_id": record["segment_id"],
                "src": record["src"],
                "dst": terminology[record["src"]],
            }
            for record in sources
        ]
        translations[-1]["dst"] = "vận hành"
        path = write_jsonl(self.root / "terminology.jsonl", translations)
        assessment = assess_handoff_translations(
            sources, path, terminology=terminology
        )
        self.assertEqual(len(assessment.accepted), 7)
        self.assertEqual(len(assessment.retry), 1)
        self.assertEqual(assessment.retry[0]["src"], "시운전")


if __name__ == "__main__":
    unittest.main()
