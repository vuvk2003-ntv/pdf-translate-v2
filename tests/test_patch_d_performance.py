"""Pure strings/fake clocks/temporary JSONL only: no PDF or live provider."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pdf2zh.performance import PerformanceProfile
from pdf2zh.routing import RoutingMetadata, route_logical_unit
from pdf2zh.translator import AutoTranslator, GoogleTranslator
from scripts.benchmark_patch_d_synthetic import MemoryCache
from scripts.prepare_handoff import main as prepare_handoff
from scripts.translate_pdf import (
    Translation,
    TranslationError,
    _parser,
    _validate_arguments,
)
from scripts.translate_pdf import main as translate_main


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class TimerTests(unittest.TestCase):
    def test_plan_only_implies_enabled_profile(self):
        self.assertTrue(PerformanceProfile(plan_only=True).snapshot()["enabled"])

    def test_disabled_does_not_read_clock_or_accumulate(self):
        clock = Mock(side_effect=AssertionError("disabled clock"))
        profile = PerformanceProfile(clock=clock)
        with profile.stage("layout_seconds"):
            self.assertEqual(profile.call("extract_seconds", lambda: 7), 7)
        profile.count("units")
        profile.record_batch("google", 1, 100, 5000)
        self.assertEqual(profile.snapshot()["enabled"], False)
        clock.assert_not_called()

    def test_nested_wait_and_retry_excluded_from_layout(self):
        clock = Clock()
        profile = PerformanceProfile(enabled=True, clock=clock)
        with profile.stage("layout_seconds"):
            clock.advance(2)
            with profile.stage("translation_wall_seconds"):
                with profile.stage("google_wait_seconds"):
                    clock.advance(10)
                profile.sleep(8, clock.advance)
            clock.advance(3)
        report = profile.snapshot(total_seconds=23)
        self.assertEqual(report["timings"]["layout_seconds"], 5)
        self.assertEqual(report["timings"]["google_wait_seconds"], 10)
        self.assertEqual(report["retry"]["retry_seconds"], 8)

    def test_exception_closes_stage(self):
        clock = Clock()
        profile = PerformanceProfile(enabled=True, clock=clock)
        with self.assertRaises(ValueError), profile.stage("extract_seconds"):
            clock.advance(3)
            raise ValueError("injected")
        with profile.stage("layout_seconds"):
            clock.advance(2)
        self.assertEqual(profile.snapshot()["timings"]["extract_seconds"], 3)

    def test_threads_keep_independent_stage_stacks(self):
        local = threading.local()
        barrier = threading.Barrier(2)
        profile = PerformanceProfile(enabled=True, clock=lambda: local.value)
        failures = []

        def worker():
            try:
                local.value = 0
                with profile.stage("google_wait_seconds"):
                    barrier.wait(timeout=5)
                    local.value = 4
            except (AssertionError, RuntimeError, threading.BrokenBarrierError) as error:
                failures.append(error)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(failures)
        self.assertEqual(profile.snapshot()["timings"]["google_wait_seconds"], 8)

    def test_retry_counts_attempts_and_measured_sleep(self):
        clock = Clock()
        profile = PerformanceProfile(enabled=True, clock=clock)
        for attempt in (1, 2, 3):
            profile.retry_attempt(SimpleNamespace(attempt_number=attempt))
        profile.sleep(1, clock.advance)
        profile.sleep(3, clock.advance)
        report = profile.snapshot()
        self.assertEqual(report["workload"]["retry_requests"], 2)
        self.assertEqual(report["retry"]["average_retry_seconds"], 2)
        self.assertEqual(report["retry"]["max_retry_seconds"], 3)
        self.assertFalse(report["handoff_external_wait_measured"])

    def test_batch_percentiles_and_capacity(self):
        profile = PerformanceProfile(enabled=True)
        for chars in (100, 200, 300, 400):
            profile.record_batch("google", 1, chars, 5000)
        batch = profile.snapshot()["batches"]["google"]
        self.assertEqual(batch["chars_per_batch_p50"], 250)
        self.assertEqual(batch["chars_per_batch_p90"], 370)
        self.assertEqual(batch["chars_per_batch_p99"], 397)
        self.assertEqual(batch["batch_capacity_utilization"], 0.05)

    def test_histogram_memory_does_not_grow_with_repeated_batches(self):
        profile = PerformanceProfile(enabled=True)
        profile.record_batch("google", 1, 100, 5000)
        first = profile.snapshot()["batch_histogram_memory_bytes"]
        for _ in range(1000):
            profile.record_batch("google", 1, 100, 5000)
        self.assertLessEqual(profile.snapshot()["batch_histogram_memory_bytes"], first + 64)

    def test_handoff_estimate_uses_existing_caps_and_deduplicates_identity(self):
        profile = PerformanceProfile(enabled=True)
        for index in range(65):
            record = {"segment_id": str(index), "src": f"Source sentence {index}."}
            profile.queue_handoff(record, planned=True)
            profile.queue_handoff(record, planned=True)
        profile.finish_handoff_plan()
        report = profile.snapshot()
        self.assertEqual(report["workload"]["handoff_planned_units"], 65)
        self.assertEqual(report["batches"]["handoff_planned"]["batch_count"], 3)
        self.assertEqual(report["batches"]["handoff_planned"]["character_capacity"], 12000)
        profile.finish_handoff_plan()
        self.assertEqual(profile.snapshot()["batches"], report["batches"])


class CacheAndPlanTests(unittest.TestCase):
    source = "Set the communication parameters."
    target = "Thiết lập các tham số truyền thông."

    def setUp(self):
        self.cache_patch = patch("pdf2zh.translator.TranslationCache", MemoryCache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.network_patch = patch("requests.sessions.Session.request",
                                   side_effect=AssertionError("live provider prohibited"))
        self.network_patch.start()
        self.addCleanup(self.network_patch.stop)

    def case(self, enabled, mode, *, invalid=False, bypass=False):
        profile = PerformanceProfile(enabled=enabled, cache_mode=mode)
        auto = AutoTranslator("en", "vi", ignore_cache=bypass,
                              envs={"performance_profile": profile})
        if mode == "warm":
            auto.google.cache.values[self.source] = self.source if invalid else self.target
        auto.google.do_translate = Mock(return_value=self.target)
        result = auto.translate_with_identity(self.source, "unit-1")
        return (result, auto.google.cache.reads, auto.google.cache.writes,
                auto.handoff.cache.reads, auto.google.do_translate.call_count,
                auto.metrics()["google_cache_hits"], auto.routing_traces[0]["selected_route"],
                auto.metrics()["google_to_handoff_escalations"])

    def test_cold_cache_equivalence(self):
        self.assertEqual(self.case(False, "cold-isolated"), self.case(True, "cold-isolated"))

    def test_warm_cache_equivalence(self):
        self.assertEqual(self.case(False, "warm"), self.case(True, "warm"))

    def test_invalid_cache_equivalence(self):
        self.assertEqual(self.case(False, "warm", invalid=True),
                         self.case(True, "warm", invalid=True))

    def test_bypassed_cache_equivalence(self):
        self.assertEqual(self.case(False, "warm", bypass=True),
                         self.case(True, "warm", bypass=True))

    def test_plan_google_miss_never_calls_provider_or_writes_cache(self):
        profile = PerformanceProfile(enabled=True, plan_only=True)
        auto = AutoTranslator("en", "vi", envs={"performance_profile": profile})
        auto.google.do_translate = Mock(side_effect=AssertionError("provider"))
        auto.handoff._record_miss = Mock(side_effect=AssertionError("queue write"))
        self.assertIsNone(auto.plan_with_identity(self.source, "unit-1"))
        self.assertEqual(auto.google.cache.reads, 2)
        self.assertEqual(auto.google.cache.writes, 0)
        self.assertEqual(profile.snapshot()["workload"]["google_planned_units"], 1)
        self.assertIsNone(auto.routing_traces[0]["actual_provider"])

    def test_plan_warm_cache_reuses_valid_target(self):
        profile = PerformanceProfile(enabled=True, plan_only=True)
        google = GoogleTranslator("en", "vi", envs={"performance_profile": profile})
        google.cache.values[self.source] = self.target
        google.do_translate = Mock(side_effect=AssertionError("provider"))
        self.assertEqual(google.plan_with_identity(self.source, "unit-1"), self.target)
        self.assertNotIn("google_planned_units", profile.snapshot()["workload"])

    def test_direct_handoff_plan_preserves_identity_without_google_work(self):
        profile = PerformanceProfile(enabled=True, plan_only=True)
        auto = AutoTranslator("en", "vi", envs={"performance_profile": profile})
        source = "Connect the controller after checking the terminal assignment."
        metadata = RoutingMetadata(logical_unit_id="unit-1", occurrence_id="occ-1",
                                   source_fragment_ids=("f-1", "f-2"),
                                   was_fragment_reconstructed=True,
                                   reconstruction_complexity="structural_merge")
        context = {"routing_metadata": metadata,
                   "routing_decision": route_logical_unit(source, metadata)}
        auto.handoff._record_miss = Mock(side_effect=AssertionError("queue write"))
        self.assertIsNone(auto.plan_with_identity(source, "unit-1", context=context))
        self.assertEqual(auto.google.cache.reads, 0)
        record = profile._handoff_sources["unit-1"]
        self.assertEqual(record["logical_unit_id"], "unit-1")
        self.assertEqual(record["occurrence_id"], "occ-1")
        self.assertEqual(record["source_fragment_ids"], ("f-1", "f-2"))

    def test_http_boundary_counts_failed_requests_and_exact_wait(self):
        clock = Clock()
        profile = PerformanceProfile(enabled=True, clock=clock)
        google = GoogleTranslator("en", "vi", envs={"performance_profile": profile})

        def failed_get(*_args, **_kwargs):
            clock.advance(3)
            raise RuntimeError("injected failure; no HTTP")

        google.session.get = failed_get
        with self.assertRaises(RuntimeError):
            google.do_translate(self.source)
        report = profile.snapshot()
        self.assertEqual(report["timings"]["google_wait_seconds"], 3)
        self.assertEqual(report["workload"]["google_http_requests"], 1)
        self.assertEqual(report["workload"]["google_units_sent"], 1)
        self.assertEqual(report["workload"]["google_chars_sent"], len(self.source))


class CliTests(unittest.TestCase):
    def test_profile_only_requires_no_output(self):
        args = _parser().parse_args(["unused.pdf", "--engine", "auto", "--profile-only"])
        _validate_arguments(args)

    def test_profile_only_rejects_pdf_and_queue_outputs(self):
        for flag in ("--output-dir", "--emit-segments"):
            args = _parser().parse_args(["unused.pdf", "--profile-only", flag, "unused"])
            with self.assertRaises(TranslationError):
                _validate_arguments(args)

    def test_handoff_cli_profiles_temporary_jsonl_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.jsonl"
            source.write_text(json.dumps({"segment_id": "u1", "src": "Set the parameters."}) + "\n",
                              encoding="utf-8")
            with patch("builtins.print") as printed:
                result = prepare_handoff([str(source), "--output-batches", str(root / "batches.jsonl"),
                                          "--profile-performance"])
            self.assertEqual(result, 0)
            report = json.loads(printed.call_args.args[0])
            self.assertEqual(report["batches"]["handoff_prepared"]["batch_count"], 1)
            self.assertEqual(report["workload"]["handoff_units_sent"], 0)
            self.assertFalse(report["handoff_external_wait_measured"])

    def test_translate_cli_emits_profile_without_pdf_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report_path = root / "profile.json"
            with patch("scripts.translate_pdf.translate_pdf", return_value=Translation(
                path=None, performance_profile={"enabled": True, "plan_only": True}
            )) as engine:
                result = translate_main([str(root / "unused.pdf"), "--profile-only",
                                         "--performance-report", str(report_path)])
            self.assertEqual(result, 0)
            self.assertTrue(engine.call_args.kwargs["profile_only"])
            self.assertTrue(json.loads(report_path.read_text(encoding="utf-8"))["enabled"])

    def test_profile_output_alias_rejected_before_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protected = root / "terminology.json"
            protected.write_text("{}", encoding="utf-8")
            with patch("scripts.translate_pdf.translate_pdf") as engine, patch("builtins.print"):
                result = translate_main([str(root / "unused.pdf"), "--profile-only",
                                         "--terminology", str(protected),
                                         "--performance-report", str(protected)])
            self.assertEqual(result, 2)
            engine.assert_not_called()
            self.assertEqual(protected.read_text(encoding="utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
