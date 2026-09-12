"""D2 real worker/policy fixtures with strings and ledger; no PDF/network."""
from __future__ import annotations

import html
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pdfminer.pdfinterp import PDFResourceManager
from requests import ConnectionError

from pdf2zh.converter import TranslateConverter, text_fits_box_at_minimum_size
from pdf2zh.integrity import (
    EligibleSourceSpan,
    Occurrence,
    OccurrenceStatus,
    TranslationIntegrityError,
    audit_final_text_layer,
)
from pdf2zh.performance import PerformanceProfile
from pdf2zh.retry_policy import _estimated_content_retry_backoff_seconds
from pdf2zh.translator import encode_formula_placeholders, segment_identifier
from scripts.benchmark_patch_d2_synthetic import (
    FAILED_SOURCE,
    GOOD_SOURCE,
    LONG_SOURCE,
    TARGETS,
    Clock,
)
from scripts.benchmark_patch_d_synthetic import MemoryCache


class D2WorkerTests(unittest.TestCase):
    def setUp(self):
        self.cache_patch = patch("pdf2zh.translator.TranslationCache", MemoryCache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.http_patch = patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP forbidden"))
        self.http_patch.start()
        self.addCleanup(self.http_patch.stop)
        self.pdf_patch = patch("pymupdf.open", side_effect=AssertionError("PDF forbidden"))
        self.pdf_patch.start()
        self.addCleanup(self.pdf_patch.stop)
        self.converter, self.clock, self.sleeps = self.make_converter()

    def make_converter(self, service="google"):
        clock = Clock()
        profile = PerformanceProfile(enabled=True, clock=clock)
        sleep = profile.sleep
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            sleep(seconds, sleeper=clock.advance)

        profile.sleep = fake_sleep
        converter = TranslateConverter(PDFResourceManager(), service=service, lang_in="en", lang_out="vi",
                                       thread=4, envs={"performance_profile":profile})
        if service == "google":
            converter.translator.session.get = Mock(side_effect=AssertionError("HTTP not mocked"))
        return converter, clock, sleeps

    def mock_output(self, target, converter=None):
        converter = converter or self.converter
        response = SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   text='<div class="result-container">'+html.escape(target)+'</div>')
        converter.translator.session.get = Mock(return_value=response)
        return converter.translator.session.get

    def run_job(self, source=FAILED_SOURCE, *, converter=None, key=None, identity=None, context=None):
        converter = converter or self.converter
        identity = identity or segment_identifier(encode_formula_placeholders(source))
        key = key or ("shared",source)
        return converter._translate_job(source,identity,context,key,converter._make_translation_request())

    def count(self, key, converter=None):
        return (converter or self.converter).profile.snapshot()["workload"].get(key,0)

    def test_01_deterministic_quality_two_calls_one_sleep_unresolved(self):
        get = self.mock_output(TARGETS[FAILED_SOURCE])
        result = self.run_job()
        self.assertEqual(get.call_count,2)
        self.assertEqual(self.sleeps,[1])
        self.assertEqual(result[1:3],("unresolved","TechnicalInvariantError"))
        self.assertEqual(self.count("content_quality_retry_attempts_capped"),1)
        self.assertEqual(self.count("retry_requests"),1)

    def test_02_shared_page_b_has_no_request_or_sleep(self):
        get = self.mock_output(TARGETS[FAILED_SOURCE])
        first = self.run_job()
        sleeps = list(self.sleeps)
        second = self.run_job()
        self.assertEqual(first[:3],second[:3])
        self.assertEqual(get.call_count,2)
        self.assertEqual(self.sleeps,sleeps)
        self.assertEqual(self.count("negative_cache_skips"),1)
        self.assertEqual(self.count("negative_cache_estimated_retry_backoff_seconds_saved"),1.0)

    def test_03_transport_recovers_on_third_call(self):
        response = self.mock_output(TARGETS[GOOD_SOURCE]).return_value
        get = Mock(side_effect=[ConnectionError(),ConnectionError(),response])
        self.converter.translator.session.get = get
        result = self.run_job(GOOD_SOURCE)
        self.assertEqual(result[1],"translated")
        self.assertEqual(get.call_count,3)
        self.assertEqual(self.sleeps,[1,2])
        self.assertFalse(self.converter.known_unsafe_identities)
        self.assertEqual(self.count("content_quality_retry_attempts_capped"),0)

    def test_03b_transport_exhausts_all_eight_attempts(self):
        get = Mock(side_effect=ConnectionError())
        self.converter.translator.session.get = get
        self.assertEqual(self.run_job()[1:3],("unresolved","ConnectionError"))
        self.assertEqual(get.call_count,8)
        self.assertEqual(self.sleeps,[1,2,4,8,16,32,60])
        self.assertFalse(self.converter.known_unsafe_identities)
        self.assertEqual(self.count("negative_cache_skips"),0)

    def test_04_occurrences_independently_fail(self):
        get = self.mock_output(TARGETS[FAILED_SOURCE])
        for index in range(2):
            self.run_job(key=("occurrence",index),identity=f"occ-{index}")
        self.assertEqual(get.call_count,4)
        self.assertEqual(self.sleeps,[1,1])
        self.assertFalse(self.converter.known_unsafe_identities)

    def test_04b_context_bearing_shared_key_never_cached(self):
        get = self.mock_output(TARGETS[FAILED_SOURCE])
        for _ in range(2):
            self.run_job(context={"type":"untrusted_context","kind":"adjacent_segment","text":"context"})
        self.assertEqual(get.call_count,4)
        self.assertFalse(self.converter.known_unsafe_identities)

    def test_05_fresh_converter_starts_empty_and_pays_own_ladder(self):
        self.mock_output(TARGETS[FAILED_SOURCE])
        self.run_job()
        self.assertTrue(self.converter.known_unsafe_identities)
        second, _, second_sleeps = self.make_converter()
        self.assertFalse(second.known_unsafe_identities)
        get = self.mock_output(TARGETS[FAILED_SOURCE], second)
        self.run_job(converter=second)
        self.assertEqual(get.call_count,2)
        self.assertEqual(second_sleeps,[1])
        self.assertEqual(self.count("negative_cache_skips",second),0)

    def test_06_first_reason_preserved(self):
        identity = segment_identifier(FAILED_SOURCE)
        self.converter._remember_unsafe_identity(identity,"First provider reason")
        self.converter._remember_unsafe_identity(identity,"Later provider reason")
        get = self.mock_output(TARGETS[FAILED_SOURCE])
        self.assertEqual(self.run_job()[2],"First provider reason")
        get.assert_not_called()

    def test_07_concurrent_committed_cache_hits_do_not_call_provider(self):
        identity = segment_identifier(FAILED_SOURCE)
        self.converter._remember_unsafe_identity(identity,"First reason")
        get = self.mock_output(TARGETS[FAILED_SOURCE])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.run_job(),range(20)))
        self.assertTrue(all(result[1:3]==("unresolved","First reason") for result in results))
        get.assert_not_called()
        self.assertEqual(self.count("negative_cache_skips"),20)

    def test_07b_concurrent_writes_keep_one_first_reason(self):
        barrier = threading.Barrier(4)
        def write(index):
            barrier.wait(timeout=5)
            self.converter._remember_unsafe_identity("id",str(index))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write,range(4)))
        first = self.converter.known_unsafe_identities["id"]
        self.converter._remember_unsafe_identity("id","replacement")
        self.assertEqual(self.converter.known_unsafe_identities,{"id":first})

    def record_result(self, page, source, result):
        target,status,reason,codes = result
        span = f"span-{page}"
        self.converter.integrity_ledger.add_eligible_span(EligibleSourceSpan(span,page,source))
        self.converter.integrity_ledger.record_occurrence(Occurrence(
            occurrence_id=f"occ-{page}",page=page,source_span_ids=(span,),source_text=source,
            logical_unit_id=segment_identifier(source),status=OccurrenceStatus.UNRESOLVED
            if status=="unresolved" else OccurrenceStatus.TRANSLATED,target_text=target,
            unresolved_reason=reason,validation_failures=codes))

    def test_08_fit_failure_does_not_skip_roomier_geometry(self):
        get = self.mock_output(TARGETS[GOOD_SOURCE])
        first = self.run_job(GOOD_SOURCE)
        self.record_result(0,GOOD_SOURCE,first)
        measure = lambda _char,size:size
        self.assertFalse(text_fits_box_at_minimum_size(first[0],5,2,10,[],measure))
        self.converter.record_translation_failure(GOOD_SOURCE,"single line needs less than 50% font size",
                                                  occurrence_id="occ-0")
        self.assertFalse(self.converter.known_unsafe_identities)
        second = self.run_job(GOOD_SOURCE)
        self.assertEqual(second[1],"translated")
        self.assertTrue(text_fits_box_at_minimum_size(second[0],1000,100,10,[],measure))
        self.assertEqual(self.count("negative_cache_skips"),0)
        self.assertEqual(get.call_count,1)  # Valid positive cache reuse remains normal.

    def test_09_skipped_occurrence_has_coverage_and_normal_script_audit(self):
        source = "통신 매개변수 설정을 확인하십시오."
        self.mock_output(source)  # Real Patch B rejects unexpected Korean in vi output.
        first = self.run_job(source)
        second = self.run_job(source)
        self.assertEqual(first[1:3],second[1:3])
        for page,result in enumerate((first,second)):
            self.record_result(page,source,result)
        metrics = self.converter.integrity_ledger.reconcile()
        self.assertEqual(metrics.accounting_coverage,1.0)
        self.assertEqual(metrics.unresolved_occurrences,2)
        self.assertEqual(metrics.duplicate_source_span_assignments,0)
        self.assertEqual(metrics.unassigned_source_spans,0)
        self.assertEqual(self.count("negative_cache_skips"),1)
        audit = audit_final_text_layer({0:source,1:source},self.converter.integrity_ledger.occurrences,
                                      source_language="ko",target_language="vi")
        self.assertEqual(audit.final_output_script_leaks,0)
        unexpected = audit_final_text_layer({0:source,1:source+"가"},self.converter.integrity_ledger.occurrences,
                                           source_language="ko",target_language="vi")
        self.assertEqual(unexpected.final_output_script_leaks,1)

    def test_10_pre_provider_length_check_zero_http_no_sleep(self):
        get = self.mock_output(TARGETS[GOOD_SOURCE])
        do_translate = self.converter.translator.do_translate
        self.converter.translator.do_translate = Mock(wraps=do_translate)
        self.assertEqual(self.run_job(LONG_SOURCE)[1:3],("unresolved","SegmentTooLongError"))
        get.assert_not_called()
        self.assertEqual(self.converter.translator.do_translate.call_count,1)
        self.assertFalse(self.sleeps)
        self.assertFalse(self.converter.known_unsafe_identities)
        self.assertEqual(self.count("pre_provider_deterministic_retries_suppressed"),1)

    def test_11_empty_reasons_rejected_without_changing_normal_result(self):
        for reason in (None,""," \t"):
            with self.subTest(reason=reason):
                self.converter.known_unsafe_identities.clear()
                self.mock_output(TARGETS[FAILED_SOURCE])
                with patch("pdf2zh.converter._reason_from_exception",return_value=reason):
                    result = self.run_job()
                self.assertEqual(result[1:3],("unresolved","TechnicalInvariantError"))
                self.assertFalse(self.converter.known_unsafe_identities)
        self.assertEqual(self.count("negative_cache_invalid_reason_rejected"),3)

    def test_12_preferred_validation_error_not_negative_cached(self):
        self.converter.translator.validate = Mock(side_effect=TranslationIntegrityError(("unchanged_prose",)))
        with patch("pdf2zh.converter.preferred_translation",return_value="local result"):
            self.run_job()
        self.assertFalse(self.converter.known_unsafe_identities)
        self.assertEqual(self.count("content_quality_retry_attempts_capped"),0)

    def test_13_post_request_restore_error_not_negative_cached(self):
        self.mock_output(TARGETS[GOOD_SOURCE])
        with patch("pdf2zh.converter.restore_formula_placeholders",side_effect=TranslationIntegrityError(("unchanged_prose",))):
            self.assertEqual(self.run_job(GOOD_SOURCE)[1],"unresolved")
        self.assertFalse(self.converter.known_unsafe_identities)
        self.assertEqual(self.count("content_quality_retry_attempts_capped"),0)

    def test_14_unexpected_internal_exception_keeps_legacy_budget_no_cache(self):
        get = Mock(side_effect=RuntimeError("TechnicalInvariantError in message only"))
        self.converter.translator.session.get = get
        self.run_job()
        self.assertEqual(get.call_count,8)
        self.assertEqual(self.sleeps,[1,2,4,8,16,32,60])
        self.assertFalse(self.converter.known_unsafe_identities)

    def test_15_first_attempt_success_has_no_cap_or_negative_cache(self):
        self.mock_output(TARGETS[GOOD_SOURCE])
        self.assertEqual(self.run_job(GOOD_SOURCE)[1],"translated")
        self.assertFalse(self.sleeps)
        self.assertFalse(self.converter.known_unsafe_identities)
        self.assertEqual(self.count("content_quality_retry_attempts_capped"),0)
        self.assertEqual(_estimated_content_retry_backoff_seconds(2),1)
        self.assertEqual(_estimated_content_retry_backoff_seconds(8),123)

    def test_16_cancellation_keeps_existing_fallback_and_never_cached(self):
        get = Mock(side_effect=KeyboardInterrupt())
        self.converter.translator.session.get = get
        self.assertEqual(self.run_job()[1:3], ("unresolved", "KeyboardInterrupt"))
        self.assertFalse(self.converter.known_unsafe_identities)
        self.assertEqual(self.count("content_quality_retry_attempts_capped"), 0)

    def test_17_handoff_missing_unit_uses_normal_unresolved_path(self):
        converter, _, sleeps = self.make_converter("handoff")
        result = self.run_job(GOOD_SOURCE, converter=converter)
        self.assertEqual(result[1], "unresolved")
        self.assertFalse(converter.known_unsafe_identities)
        self.assertFalse(sleeps)

    def test_18_plan_only_ignores_negative_cache(self):
        identity = segment_identifier(FAILED_SOURCE)
        self.converter._remember_unsafe_identity(identity, "First reason")
        self.converter.profile.plan_only = True
        get = self.mock_output(TARGETS[FAILED_SOURCE])
        result = self.run_job()
        self.assertEqual(result[1:3], ("unresolved", "PERFORMANCE_PLAN_ONLY"))
        get.assert_not_called()
        self.assertEqual(self.count("negative_cache_skips"), 0)

    def test_19_quality_failure_on_legacy_last_attempt_is_not_counted_as_capped(self):
        response = self.mock_output(TARGETS[FAILED_SOURCE]).return_value
        get = Mock(side_effect=[ConnectionError()] * 7 + [response])
        self.converter.translator.session.get = get
        result = self.run_job(key=("occurrence", 0))
        self.assertEqual(result[1:3], ("unresolved", "TechnicalInvariantError"))
        self.assertEqual(get.call_count, 8)
        self.assertEqual(self.count("content_quality_retry_attempts_capped"), 0)


if __name__ == "__main__":
    unittest.main()
