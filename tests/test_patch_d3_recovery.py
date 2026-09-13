"""D3 recovery windows using actual workers, fake HTTP and document-local state."""
from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from requests import ConnectionError

from pdf2zh.converter import RECOVERY_WINDOW
from pdf2zh.translator import encode_formula_placeholders, segment_identifier
from scripts.benchmark_patch_d2_synthetic import (
    FAILED_SOURCE,
    GOOD_SOURCE,
    TARGETS,
    run_two_strike_trust_probe,
)
from tests import test_patch_d2_retry as d2_fixtures


class D3RecoveryTests(unittest.TestCase):
    setUp = d2_fixtures.D2WorkerTests.setUp
    make_converter = d2_fixtures.D2WorkerTests.make_converter
    mock_output = d2_fixtures.D2WorkerTests.mock_output
    run_job = d2_fixtures.D2WorkerTests.run_job
    count = d2_fixtures.D2WorkerTests.count

    def identity(self, source=FAILED_SOURCE):
        return segment_identifier(encode_formula_placeholders(source))

    def provider_sequence(self, targets):
        responses = [self.mock_output(target).return_value for target in targets]
        get = Mock(side_effect=responses)
        self.converter.translator.session.get = get
        return get

    def strike(self, identity, reason):
        error = type(reason, (Exception,), {})()
        self.converter._remember_unsafe_identity_strike(identity, error)

    def test_01_one_strike_recovers_on_call_nine_and_clears_pending(self):
        get = self.provider_sequence([TARGETS[FAILED_SOURCE]] * 8 + ["Đặt D100 thành 10 và D200 thành 20."])
        first = self.run_job()
        identity = self.identity()
        self.assertEqual(first[1], "unresolved")
        self.assertEqual(get.call_count, 8)
        self.assertEqual(self.converter.identity_failure_strikes, {identity: (1, "TechnicalInvariantError")})
        self.assertFalse(self.converter.known_unsafe_identities)
        self.assertEqual(self.run_job()[1], "translated")
        self.assertEqual(get.call_count, 9)
        self.assertEqual(self.count("negative_cache_skips"), 0)
        self.assertFalse(self.converter.identity_failure_strikes)
        self.assertFalse(self.converter.known_unsafe_identities)

    def test_02_two_full_ladders_promote_and_third_skips_defined_control(self):
        self.assertEqual(RECOVERY_WINDOW, 2)
        probe = run_two_strike_trust_probe()
        self.assertEqual(probe["d2_provider_call_count"], 16)
        self.assertEqual(probe["control_provider_call_count"], 24)
        self.assertEqual(probe["d2_negative_cache_skips"], 1)
        self.assertEqual(probe["calls_avoided_on_third_occurrence"], 8)
        self.assertEqual(probe["d2_status"], "UNRESOLVED")
        self.assertEqual(probe["control_status"], "UNRESOLVED")
        self.assertTrue(probe["identity_sets_equal"])
        for path in ("d2", "control"):
            self.assertTrue(probe[path]["trusted_after_second_occurrence"])
            self.assertFalse(probe[path]["pending_after_last_occurrence"])

    def test_03_success_resets_window_then_failure_starts_with_new_reason(self):
        self.converter.translator.ignore_cache = True  # Only the fake scenario bypasses positive reuse.
        get = self.provider_sequence([TARGETS[FAILED_SOURCE]] * 8
                                     + ["Đặt D100 thành 10 và D200 thành 20."] + [TARGETS[FAILED_SOURCE]] * 8)
        identity = self.identity()
        with patch("pdf2zh.converter._reason_from_exception", return_value="A"):
            self.run_job()
        self.assertEqual(self.converter.identity_failure_strikes[identity], (1, "A"))
        self.assertEqual(self.run_job()[1], "translated")
        self.assertFalse(self.converter.identity_failure_strikes)
        with patch("pdf2zh.converter._reason_from_exception", return_value="C"):
            self.run_job()
        self.assertEqual(get.call_count, 17)
        self.assertEqual(self.converter.identity_failure_strikes, {identity: (1, "C")})
        self.assertFalse(self.converter.known_unsafe_identities)

    def test_04_strikes_are_per_identity(self):
        self.strike("A", "FirstA")
        self.strike("B", "FirstB")
        self.strike("A", "SecondA")
        self.assertEqual(self.converter.known_unsafe_identities, {"A": "FirstA"})
        self.assertEqual(self.converter.identity_failure_strikes, {"B": (1, "FirstB")})

    def test_05_twenty_concurrent_writes_keep_promotion_invariant(self):
        # User-approved interpretation: audit all 20 calls in test-only state;
        # production pending evidence is removed at promotion, never stores 20.
        barrier = threading.Barrier(20)
        audit_lock = threading.Lock()
        completed = []
        increments = []

        class AuditedStrikes(dict):
            def get(self, key, default=None):
                value = super().get(key, default)
                time.sleep(0.001)  # Expose a lost read/update if locking is absent.
                return value

            def __setitem__(self, key, value):
                increments.append(value)
                super().__setitem__(key, value)

        self.converter.identity_failure_strikes = AuditedStrikes()

        def write(index):
            barrier.wait(timeout=10)
            self.strike("X", "SharedReason")
            with audit_lock:
                completed.append(index)

        with ThreadPoolExecutor(max_workers=20) as pool:
            list(pool.map(write, range(20)))
        self.assertEqual(len(completed), 20)
        self.assertEqual(set(completed), set(range(20)))
        self.assertEqual(self.converter.known_unsafe_identities, {"X": "SharedReason"})
        self.assertFalse(self.converter.identity_failure_strikes)
        self.assertEqual(increments, [(1, "SharedReason"), (2, "SharedReason")])

    def test_06_first_reason_is_promoted_and_pending_removed(self):
        self.strike("X", "ReasonA")
        self.assertEqual(self.converter.identity_failure_strikes["X"], (1, "ReasonA"))
        self.strike("X", "ReasonB")
        self.assertEqual(self.converter.known_unsafe_identities["X"], "ReasonA")
        self.assertNotIn("X", self.converter.identity_failure_strikes)
        self.strike("X", "ReasonC")
        self.assertEqual(self.converter.known_unsafe_identities["X"], "ReasonA")
        self.assertNotIn("X", self.converter.identity_failure_strikes)

    def test_08_bypass_success_keeps_trusted_latch_and_fourth_skips(self):
        get = self.provider_sequence([TARGETS[FAILED_SOURCE]] * 16 + ["Đặt D100 thành 10 và D200 thành 20."])
        self.run_job()
        self.run_job()
        identity = self.identity()
        self.assertIn(identity, self.converter.known_unsafe_identities)
        self.assertFalse(self.converter.identity_failure_strikes)
        self.converter._disable_negative_cache_for_tests = True
        self.assertEqual(self.run_job()[1], "translated")
        self.assertEqual(get.call_count, 17)
        self.assertIn(identity, self.converter.known_unsafe_identities)
        self.assertFalse(self.converter.identity_failure_strikes)
        self.converter._disable_negative_cache_for_tests = False
        self.assertEqual(self.run_job()[1], "unresolved")
        self.assertEqual(get.call_count, 17)
        self.assertEqual(self.count("negative_cache_skips"), 1)

    def test_09_empty_reason_does_not_increment_or_promote(self):
        for reason in (None, "", " \t"):
            with patch("pdf2zh.converter._reason_from_exception", return_value=reason):
                self.strike("X", "SomeError")
        self.assertFalse(self.converter.identity_failure_strikes)
        self.assertFalse(self.converter.known_unsafe_identities)
        self.assertEqual(self.count("negative_cache_invalid_reason_rejected"), 3)

    def test_10_transport_failure_does_not_reset_or_increment_pending(self):
        identity = self.identity()
        self.strike(identity, "ReasonA")
        get = Mock(side_effect=ConnectionError())
        self.converter.translator.session.get = get
        self.run_job()
        self.assertEqual(get.call_count, 8)
        self.assertEqual(self.converter.identity_failure_strikes, {identity: (1, "ReasonA")})
        self.assertFalse(self.converter.known_unsafe_identities)

    def test_11_local_preferred_translation_does_not_reset_pending(self):
        identity = self.identity(GOOD_SOURCE)
        self.strike(identity, "ReasonA")
        with patch("pdf2zh.converter.preferred_translation", return_value=TARGETS[GOOD_SOURCE]):
            self.assertEqual(self.run_job(GOOD_SOURCE)[1], "translated")
        self.assertEqual(self.converter.identity_failure_strikes, {identity: (1, "ReasonA")})

    def test_12_positive_cache_hit_is_not_a_new_provider_success(self):
        get = self.mock_output(TARGETS[GOOD_SOURCE])
        self.run_job(GOOD_SOURCE)
        identity = self.identity(GOOD_SOURCE)
        self.strike(identity, "ReasonA")
        self.assertEqual(self.run_job(GOOD_SOURCE)[1], "translated")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(self.converter.identity_failure_strikes, {identity: (1, "ReasonA")})

    def test_13_context_and_occurrence_jobs_never_record_pending_strikes(self):
        get = self.mock_output(TARGETS[FAILED_SOURCE])
        self.run_job(key=("occurrence", 0))
        self.run_job(context={"type": "untrusted_context", "text": "context"})
        self.assertEqual(get.call_count, 16)
        self.assertFalse(self.converter.identity_failure_strikes)
        self.assertFalse(self.converter.known_unsafe_identities)


if __name__ == "__main__":
    unittest.main()
