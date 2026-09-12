from __future__ import annotations

import unittest

from pdf2zh.integrity import (
    AllowedPreserveSpan,
    EligibleSourceSpan,
    IntegrityFailure,
    Occurrence,
    OccurrenceLedger,
    OccurrenceStatus,
    TranslationIntegrityError,
    approved_spans_for_translation,
    audit_final_text_layer,
    stable_source_span_id,
    validate_translation_integrity,
)
from pdf2zh.invariants import TechnicalInvariantError
from pdf2zh.translator import BaseTranslator, validate_translation_result


def validate(source: str, target: str, source_language: str = "ko") -> None:
    validate_translation_integrity(
        source,
        target,
        source_language=source_language,
        target_language="vi",
        translation_required=True,
        allowed_preserve_spans=approved_spans_for_translation(source),
    )


def assert_failure(
    case: unittest.TestCase,
    failure: IntegrityFailure,
    source: str,
    target: str,
    source_language: str = "ko",
) -> None:
    with case.assertRaises(TranslationIntegrityError) as raised:
        validate(source, target, source_language)
    case.assertIn(failure.value, raised.exception.failure_codes)


class PatchBTranslationGuardTests(unittest.TestCase):
    def test_01_clean_korean_translation_passes(self) -> None:
        validate("통신할 수 있습니다.", "Có thể giao tiếp.")

    def test_02_unexpected_hangul_fails(self) -> None:
        assert_failure(
            self,
            IntegrityFailure.HANGUL_LEAK,
            "TCP / UDP 통신 가능",
            "Có thể 통신 bằng TCP / UDP",
        )

    def test_03_approved_korean_span_passes(self) -> None:
        source = r"C:\J1C\오늘.txt 파일을 연다."
        target = r"Mở tệp C:\J1C\오늘.txt."
        validate_translation_result(
            source,
            target,
            source_language="ko",
            target_language="vi",
            translation_required=True,
        )

    def test_04_unexpected_han_fails(self) -> None:
        assert_failure(
            self,
            IntegrityFailure.HAN_LEAK,
            "可以通信",
            "Có thể 通信",
            "zh",
        )

    def test_05_approved_han_span_passes(self) -> None:
        validate_translation_integrity(
            "设备名称 X",
            "Tên 设备 X",
            source_language="zh",
            target_language="vi",
            translation_required=True,
            allowed_preserve_spans=(
                AllowedPreserveSpan("设备", "explicit_technical_term", True),
            ),
        )

    def test_06_unchanged_english_prose_fails(self) -> None:
        assert_failure(
            self,
            IntegrityFailure.UNCHANGED_PROSE,
            "Safety requirements",
            "Safety requirements",
            "en",
        )

    def test_07_mixed_technical_sentence_passes(self) -> None:
        validate(
            "Servo Motor 위치 확인 후 Start 버튼을 누른다.",
            "Sau khi kiểm tra vị trí Servo Motor, nhấn nút Start.",
        )

    def test_08_repeated_short_token_garbage_fails(self) -> None:
        assert_failure(
            self,
            IntegrityFailure.REPEATED_TOKEN,
            "TCP / UDP 통신 상태",
            "Trạng thái TCP / UDP 1 1 1 1 1 1",
        )

    def test_09_source_repetition_is_not_a_false_positive(self) -> None:
        validate("상태 1 1 1 1", "Trạng thái 1 1 1 1")

    def test_10_repeated_character_garbage_fails(self) -> None:
        assert_failure(
            self,
            IntegrityFailure.REPEATED_CHAR,
            "프로토콜 상태",
            "Trạng thái giao thức ;;;;;;;;",
        )

    def test_11_source_character_run_is_not_a_false_positive(self) -> None:
        validate("구분선 ----", "Đường phân cách ----")

    def test_12_length_explosion_fails(self) -> None:
        source = "장치를 시작하기 전에 안전 상태를 확인합니다."
        target = (
            "Kiểm tra trạng thái an toàn trước khi khởi động thiết bị với phần "
            "giải thích dư thừa hoàn toàn không có trong nội dung nguồn. " * 5
        )
        assert_failure(self, IntegrityFailure.LENGTH_EXPLOSION, source, target)

    def test_13_unexpected_unicode_script_fails(self) -> None:
        assert_failure(
            self,
            IntegrityFailure.UNEXPECTED_SCRIPT,
            "Safety requirements",
            "Yêu cầu an toàn тест",
            "en",
        )

    def test_14_windows_path_preserved_passes(self) -> None:
        validate_translation_result(
            r"Open C:\J1C\config.ini before startup.",
            r"Mở C:\J1C\config.ini trước khi khởi động.",
            source_language="en",
            target_language="vi",
            translation_required=True,
        )

    def test_15_windows_path_corruption_fails(self) -> None:
        with self.assertRaises(TechnicalInvariantError):
            validate_translation_result(
                r"Open C:\J1C\config.ini before startup.",
                r"Mở C:\J1C\config trước khi khởi động.",
                source_language="en",
                target_language="vi",
                translation_required=True,
            )

    def test_16_protocol_syntax_preserved_passes(self) -> None:
        validate_translation_result(
            "Append `;` to the protocol string.",
            "Thêm `;` vào chuỗi giao thức.",
            source_language="en",
            target_language="vi",
            translation_required=True,
        )

    def test_17_protocol_syntax_removed_fails(self) -> None:
        with self.assertRaises(TechnicalInvariantError):
            validate_translation_result(
                "Append `;` to the protocol string.",
                "Thêm vào chuỗi giao thức.",
                source_language="en",
                target_language="vi",
                translation_required=True,
            )


class PatchBLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.span = EligibleSourceSpan("span-1", 0, "Safety requirements", "body", "unit-1")

    def occurrence(
        self,
        identifier: str,
        status: OccurrenceStatus | None,
        **kwargs: object,
    ) -> Occurrence:
        return Occurrence(
            occurrence_id=identifier,
            page=0,
            source_span_ids=("span-1",),
            source_text="Safety requirements",
            logical_unit_id="unit-1",
            status=status,
            **kwargs,
        )

    def test_18_unapproved_preserve_becomes_unresolved(self) -> None:
        ledger = OccurrenceLedger()
        ledger.add_eligible_span(self.span)
        occurrence = ledger.record_occurrence(
            self.occurrence(
                "occ-1",
                OccurrenceStatus.ALLOWED_PRESERVE,
                target_text="Safety requirements",
                preserve_reason="unknown",
                preserve_reason_is_approved=False,
            )
        )
        metrics = ledger.reconcile()
        self.assertEqual(occurrence.status, OccurrenceStatus.UNRESOLVED)
        self.assertEqual(metrics.unapproved_preserve_failures, 1)
        self.assertEqual(metrics.delivery_status, "PARTIAL")

    def test_19_technical_code_can_be_approved_preserve(self) -> None:
        ledger = OccurrenceLedger()
        span = EligibleSourceSpan("span-code", 0, "D100", "body", "unit-code")
        ledger.add_eligible_span(span)
        ledger.record_occurrence(
            Occurrence(
                "occ-code",
                0,
                ("span-code",),
                "D100",
                "unit-code",
                OccurrenceStatus.ALLOWED_PRESERVE,
                target_text="D100",
                preserve_reason="technical_invariant",
                preserve_reason_is_approved=True,
                allowed_preserve_spans=(
                    AllowedPreserveSpan("D100", "technical_invariant", True),
                ),
            )
        )
        metrics = ledger.reconcile()
        metrics.assert_reconciled()
        self.assertEqual(metrics.delivery_status, "SUCCESS")

    def test_20_silent_source_span_fails_reconciliation(self) -> None:
        ledger = OccurrenceLedger()
        ledger.add_eligible_span(self.span)
        metrics = ledger.reconcile()
        self.assertEqual(metrics.unassigned_source_spans, 1)
        self.assertEqual(metrics.delivery_status, "FAIL")
        with self.assertRaises(RuntimeError):
            metrics.assert_reconciled()

    def test_21_duplicate_assignment_fails_reconciliation(self) -> None:
        ledger = OccurrenceLedger()
        ledger.add_eligible_span(self.span)
        ledger.record_occurrence(self.occurrence("occ-1", OccurrenceStatus.TRANSLATED))
        ledger.record_occurrence(self.occurrence("occ-2", OccurrenceStatus.TRANSLATED))
        metrics = ledger.reconcile()
        self.assertEqual(metrics.duplicate_source_span_assignments, 1)
        self.assertEqual(metrics.delivery_status, "FAIL")

    def test_22_source_fallback_remains_unresolved(self) -> None:
        ledger = OccurrenceLedger()
        ledger.add_eligible_span(self.span)
        ledger.record_occurrence(
            self.occurrence(
                "occ-1",
                OccurrenceStatus.UNRESOLVED,
                target_text="Safety requirements",
                unresolved_reason="TranslationIntegrityError",
                validation_failures=(IntegrityFailure.UNCHANGED_PROSE.value,),
            )
        )
        metrics = ledger.reconcile()
        metrics.assert_reconciled()
        self.assertEqual(metrics.unresolved_occurrences, 1)
        self.assertEqual(metrics.translation_completion_rate, 0.0)
        self.assertEqual(metrics.delivery_status, "PARTIAL")

    def test_23_unknown_occurrence_fails(self) -> None:
        ledger = OccurrenceLedger()
        ledger.add_eligible_span(self.span)
        ledger.record_occurrence(self.occurrence("occ-1", None))
        metrics = ledger.reconcile()
        self.assertEqual(metrics.unknown_occurrences, 1)
        self.assertEqual(metrics.delivery_status, "FAIL")

    def test_24_page_ordinals_keep_identical_container_text_distinct(self) -> None:
        first = stable_source_span_id(0, 0, "Status")
        second = stable_source_span_id(0, 1, "Status")
        self.assertNotEqual(first, second)

    def test_25_failed_validation_cannot_remain_translated(self) -> None:
        ledger = OccurrenceLedger()
        ledger.add_eligible_span(self.span)
        ledger.record_occurrence(
            self.occurrence(
                "occ-1",
                OccurrenceStatus.TRANSLATED,
                target_text="Safety requirements",
                validation_failures=(IntegrityFailure.UNCHANGED_PROSE.value,),
            )
        )
        metrics = ledger.reconcile()
        self.assertEqual(metrics.delivery_status, "FAIL")
        with self.assertRaises(RuntimeError):
            metrics.assert_reconciled()


class PatchBCacheAndFinalAuditTests(unittest.TestCase):
    class FakeCache:
        def __init__(self, cached: str) -> None:
            self.cached = cached
            self.stored: tuple[str, str] | None = None

        def get(self, _source: str) -> str | None:
            value, self.cached = self.cached, ""
            return value or None

        def set(self, source: str, target: str) -> None:
            self.stored = (source, target)

    class CleanTranslator(BaseTranslator):
        name = "patch-b-test"

        def __init__(self) -> None:
            super().__init__("en", "vi")
            self.calls = 0

        def do_translate(self, text: str) -> str:
            self.calls += 1
            return "Yêu cầu an toàn cho quá trình vận hành."

    def test_25_invalid_cache_hit_is_rejected_and_retranslated(self) -> None:
        translator = self.CleanTranslator()
        source = "Safety requirements for normal operation."
        cache = self.FakeCache(source)
        translator.cache = cache
        result = translator.translate(source)
        self.assertEqual(result, "Yêu cầu an toàn cho quá trình vận hành.")
        self.assertEqual(translator.calls, 1)
        self.assertEqual(translator.metrics()["cache_validation_failures"], 1)
        self.assertEqual(cache.stored, (source, result))

    def test_26_final_text_layer_catches_reintroduced_hangul(self) -> None:
        occurrence = Occurrence(
            "occ-1",
            0,
            ("span-1",),
            "통신 상태",
            "unit-1",
            OccurrenceStatus.TRANSLATED,
            target_text="Trạng thái giao tiếp",
        )
        audit = audit_final_text_layer(
            {0: "Trạng thái giao tiếp 통신"},
            (occurrence,),
            source_language="ko",
            target_language="vi",
        )
        self.assertGreater(audit.final_output_script_leaks, 0)
        self.assertEqual(audit.pages_with_leaks, (0,))

    def test_27_final_text_layer_allows_explicit_unresolved_fallback(self) -> None:
        occurrence = Occurrence(
            "occ-1",
            0,
            ("span-1",),
            "통신 상태",
            "unit-1",
            OccurrenceStatus.UNRESOLVED,
            target_text="통신 상태",
            unresolved_reason="provider failure",
        )
        audit = audit_final_text_layer(
            {0: "통신 상태"},
            (occurrence,),
            source_language="ko",
            target_language="vi",
        )
        self.assertEqual(audit.final_output_script_leaks, 0)


if __name__ == "__main__":
    unittest.main()
