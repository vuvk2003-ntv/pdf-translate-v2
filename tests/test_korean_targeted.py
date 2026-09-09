from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pdf2zh.cache import clean_test_db, init_test_db
from pdf2zh.converter import (
    bounded_korean_context,
    needs_model_translation,
    should_share_translation,
)
from pdf2zh.handoff import (
    BATCH_INSTRUCTIONS,
    assess_handoff_translations,
    build_handoff_batches,
    load_source_segments,
)
from pdf2zh.invariants import TechnicalInvariantError, is_context_sensitive_korean
from pdf2zh.translator import HandoffTranslator, validate_translation_result


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


class KoreanRealWorldRegressionTests(unittest.TestCase):
    """Targeted Korean cases; these are not a general Korean grammar benchmark."""

    def test_supported_korean_translations_pass(self):
        pairs = [
            ("검수 전 Manual 작성", "Soạn Manual trước khi nghiệm thu."),
            ("SMS 1~4단계 대응 필요", "Cần đáp ứng các giai đoạn SMS 1–4."),
            (
                "Servo Motor 위치 확인 후 Start 버튼을 누른다.",
                "Sau khi kiểm tra vị trí Servo Motor, nhấn nút Start.",
            ),
            ("PCW Interlock 설정", "Thiết lập Interlock PCW."),
            ("D100 값을 250으로 설정하십시오.", "Đặt giá trị D100 thành 250."),
            ("1차 Utility 공사", "Thi công Utility đợt 1."),
            ("시운전 완료 후 검사", "Kiểm tra sau khi hoàn tất chạy thử."),
            ("Servo ON 전 위치 확인", "Kiểm tra vị trí trước khi Servo ON."),
            (
                "안전문이 열려 있을 경우 운전 금지",
                "Nếu cửa an toàn đang mở, không được vận hành.",
            ),
            ("설정값 변경을 권장합니다.", "Khuyến nghị thay đổi giá trị cài đặt."),
            ("설정값 변경이 필요합니다.", "Cần thay đổi giá trị cài đặt."),
            ("설정 변경 필수", "Bắt buộc thay đổi cài đặt."),
            ("설비 Qualification 수행", "Thực hiện Qualification cho thiết bị."),
            ("먼저 공용화 설계를 검토한다.", "Trước tiên, xem xét thiết kế dùng chung."),
            ("운전 불가", "Không thể vận hành."),
            ('"Node Address" 항목을 선택한다.', 'Chọn mục "Node Address".'),
            (
                '"Control configuration" 메뉴를 연다.',
                'Mở menu "Control configuration".',
            ),
        ]
        for source, target in pairs:
            with self.subTest(source=source):
                validate_translation_result(source, target, target_language="vi")

    def test_supported_korean_semantic_corruption_is_rejected(self):
        pairs = [
            ("검수 이전 Manual 작성", "Soạn Manual sau khi nghiệm thu."),
            ("시운전 완료 이후 검사", "Kiểm tra trước khi hoàn tất chạy thử."),
            ("설정값 변경이 필요합니다.", "Có thể thay đổi giá trị cài đặt."),
            ("설정값 변경을 권장합니다.", "Bắt buộc thay đổi giá trị cài đặt."),
            ("설정 변경을 해야 함", "Khuyến nghị thay đổi cài đặt."),
            ("운전 금지", "Hãy vận hành."),
            ("안전문이 열려 있을 경우 운전 금지", "Không được vận hành."),
            ("운전할 수 있음", "Bắt buộc vận hành."),
            ("운전할 수 없음", "Có thể vận hành."),
            (
                "SMS 1~4단계 대응 필요",
                "Cần người phụ trách chịu trách nhiệm cho các giai đoạn SMS 1–4.",
            ),
            (
                "Servo Motor 위치 확인 후 Start 버튼을 누른다.",
                "Sau khi kiểm tra vị trí Servo Motor, nhấn nút Khởi động.",
            ),
            ("검수 전 Manual 작성", "Soạn hướng dẫn trước khi nghiệm thu."),
        ]
        for source, target in pairs:
            with self.subTest(source=source), self.assertRaises(TechnicalInvariantError):
                validate_translation_result(source, target, target_language="vi")

    def test_document_terminology_can_override_a_known_english_term(self):
        validate_translation_result(
            "검수 체크리스트에서 Manual 항목 확인",
            "Kiểm tra mục hướng dẫn trong checklist kiểm tra.",
            target_language="vi",
            terminology={"Manual": "hướng dẫn"},
        )

    def test_mixed_korean_english_still_needs_translation(self):
        for source in (
            "검수 전 Manual 작성",
            "PCW Interlock 설정",
            "Servo Motor 위치 확인 후 Start 버튼을 누른다.",
            "D100 값을 250으로 설정하십시오.",
        ):
            with self.subTest(source=source):
                self.assertTrue(needs_model_translation(source))

    def test_korean_context_protects_a_technical_acronym(self):
        with self.assertRaises(TechnicalInvariantError):
            validate_translation_result(
                "SMS 1~4단계 대응 필요",
                "Cần đáp ứng các giai đoạn EMS 1–4.",
                target_language="vi",
            )

    def test_korean_cues_do_not_match_negative_compounds_by_substring(self):
        pairs = [
            ("변경이 불필요합니다.", "Không cần thay đổi."),
            ("비필수 항목입니다.", "Đây là hạng mục không bắt buộc."),
            ("비권장 설정입니다.", "Đây là cài đặt không được khuyến nghị."),
        ]
        for source, target in pairs:
            with self.subTest(source=source):
                validate_translation_result(source, target, target_language="vi")

    def test_positive_korean_modality_rejects_a_negated_vietnamese_cue(self):
        pairs = [
            ("설정값 변경이 필요합니다.", "Không cần thay đổi giá trị cài đặt."),
            ("설정 변경 필수", "Không bắt buộc thay đổi cài đặt."),
            ("설정값 변경을 권장합니다.", "Không được khuyến nghị thay đổi cài đặt."),
            ("운전할 수 있음", "Không được phép vận hành."),
        ]
        for source, target in pairs:
            with self.subTest(source=source), self.assertRaises(TechnicalInvariantError):
                validate_translation_result(source, target, target_language="vi")

    def test_contextual_english_terms_are_not_globally_frozen(self):
        pairs = [
            ("직원 Qualification 검토", "Xem xét trình độ của nhân viên."),
            ("모바일 Utility 기능 비교", "So sánh chức năng tiện ích di động."),
            ('"시작" 항목을 선택한다.', 'Chọn mục "Bắt đầu".'),
        ]
        for source, target in pairs:
            with self.subTest(source=source):
                validate_translation_result(source, target, target_language="vi")

    def test_remaining_requested_korean_cue_forms_are_supported(self):
        pairs = [
            ("운전 가능", "Có thể vận hành."),
            ("변경을 권고합니다.", "Khuyến cáo thay đổi."),
            ("설정을 변경하여야 함", "Phải thay đổi cài đặt."),
            ("운전하지 마십시오", "Không vận hành."),
            ("설정을 변경하지 말 것", "Không thay đổi cài đặt."),
        ]
        for source, target in pairs:
            with self.subTest(source=source):
                validate_translation_result(source, target, target_language="vi")


class KoreanContextAndDedupTests(unittest.TestCase):
    def test_only_risky_korean_is_context_sensitive(self):
        self.assertTrue(is_context_sensitive_korean("검수 절차를 고객 인수 조건에 따라 수행한다."))
        self.assertTrue(is_context_sensitive_korean("설비 Manual 내용을 현장 조건에 맞게 작성한다."))
        self.assertFalse(is_context_sensitive_korean("안전 점검 절차를 순서대로 수행한다."))
        self.assertFalse(is_context_sensitive_korean("D100 값을 기준에 따라 설정하고 확인한다."))
        self.assertFalse(is_context_sensitive_korean("Manual preparation for acceptance."))

    def test_handoff_only_disables_sharing_for_context_sensitive_korean(self):
        source = "검수 절차를 고객 인수 조건에 따라 수행한다."
        self.assertFalse(should_share_translation(source, "handoff"))
        self.assertTrue(should_share_translation(source, "google"))
        english = "WARNING: Disconnect power before maintenance."
        self.assertTrue(should_share_translation(english, "handoff"))
        self.assertTrue(should_share_translation(english, "google"))

    def test_one_same_region_adjacent_segment_is_bounded_context(self):
        segments = ["검수 준비", "검수 전 Manual 작성", "다음 절차"]
        paragraphs = [
            SimpleNamespace(cls=7, layout_bound=None),
            SimpleNamespace(cls=7, layout_bound=None),
            SimpleNamespace(cls=7, layout_bound=None),
        ]
        self.assertEqual(
            bounded_korean_context(segments, paragraphs, 1),
            {"type": "untrusted_context", "kind": "adjacent_segment", "text": "검수 준비"},
        )

    def test_context_never_crosses_a_table_cell_or_layout_region(self):
        segments = ["검수 준비", "검수 전 Manual 작성"]
        table = [
            SimpleNamespace(cls=7, layout_bound=(0, 0, 10, 10)),
            SimpleNamespace(cls=7, layout_bound=(0, 0, 10, 10)),
        ]
        different_regions = [
            SimpleNamespace(cls=6, layout_bound=None),
            SimpleNamespace(cls=7, layout_bound=None),
        ]
        self.assertIsNone(bounded_korean_context(segments, table, 1))
        self.assertIsNone(bounded_korean_context(segments, different_regions, 1))

    def test_long_adjacent_text_is_skipped_instead_of_truncated(self):
        segments = ["가" * 301, "검수 전 Manual 작성"]
        paragraphs = [
            SimpleNamespace(cls=7, layout_bound=None),
            SimpleNamespace(cls=7, layout_bound=None),
        ]
        self.assertIsNone(bounded_korean_context(segments, paragraphs, 1))

    def test_context_survives_prepare_and_targeted_retry_without_merging(self):
        context = {"type": "untrusted_context", "kind": "adjacent_segment", "text": "검수 준비"}
        sources = [
            {"segment_id": "a", "src": "검수 전 Manual 작성", "context": context},
            {"segment_id": "b", "src": "PCW Interlock 설정"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            loaded = load_source_segments(write_jsonl(root / "source.jsonl", sources))
            batch = build_handoff_batches(loaded)[0]
            self.assertEqual([item["src"] for item in batch["segments"]], [s["src"] for s in sources])
            self.assertEqual(batch["segments"][0]["context"], context)
            translations = write_jsonl(
                root / "translations.jsonl",
                [{"segment_id": "b", "src": sources[1]["src"], "dst": "Thiết lập Interlock PCW."}],
            )
            retry = assess_handoff_translations(loaded, translations).retry
            self.assertEqual(retry[0]["context"], context)

    def test_context_counts_toward_batch_budget(self):
        context = {"type": "untrusted_context", "kind": "adjacent_segment", "text": "가" * 20}
        segments = [
            {"segment_id": "a", "src": "검수 " + "나" * 25, "context": context},
            {"segment_id": "b", "src": "대응 " + "다" * 25},
        ]
        batches = build_handoff_batches(segments, max_characters=60)
        self.assertEqual([len(batch["segments"]) for batch in batches], [1, 1])

    def test_korean_batch_guidance_is_short_and_not_repeated_per_segment(self):
        batches = build_handoff_batches(
            [
                {"segment_id": "a", "src": "검수 전 Manual 작성"},
                {"segment_id": "b", "src": "PCW Interlock 설정"},
            ]
        )
        self.assertIn("For Korean segments", batches[0]["instructions"])
        english_batch = build_handoff_batches(
            [{"segment_id": "english", "src": "Translate this English sentence."}]
        )[0]
        self.assertEqual(english_batch["instructions"], BATCH_INSTRUCTIONS)
        self.assertNotIn("instructions", batches[0]["segments"][0])


class HandoffContextEmissionTests(unittest.TestCase):
    def setUp(self):
        self.db = init_test_db()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "misses.jsonl"

    def tearDown(self):
        self.temp.cleanup()
        clean_test_db(self.db)

    def test_handoff_emits_context_as_untrusted_metadata(self):
        translator = HandoffTranslator("auto", "vi", envs={"segments_out": str(self.path)})
        context = {"type": "untrusted_context", "kind": "adjacent_segment", "text": "검수 준비"}
        translator.translate_with_identity("검수 전 Manual 작성", "occurrence", context=context)
        record = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(record["context"], context)


class FrozenEnglishRegressionTests(unittest.TestCase):
    def test_required_english_classifier_behavior_is_unchanged(self):
        for source in ("Servo Motor", "SCARA Robot", "D100"):
            with self.subTest(source=source):
                self.assertFalse(needs_model_translation(source))
        for source in ("Horizontal stroke 800mm", "Wait to next step", "Set D100 to 250."):
            with self.subTest(source=source):
                self.assertTrue(needs_model_translation(source))

    def test_required_english_translations_still_validate(self):
        pairs = [
            ("Horizontal stroke 800mm", "Hành trình ngang 800 mm"),
            ("Wait to next step", "Chờ bước tiếp theo"),
            ("Set D100 to 250.", "Đặt D100 thành 250."),
        ]
        for source, target in pairs:
            with self.subTest(source=source):
                validate_translation_result(source, target, target_language="vi")


if __name__ == "__main__":
    unittest.main()
