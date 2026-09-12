"""Measure timer attribution and cache decisions without PDFs or live providers."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from tenacity import retry, stop_after_attempt, wait_fixed

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from pdf2zh.translator import AutoTranslator, GoogleTranslator


class MemoryCache:
    def __init__(self, *_args, **_kwargs):
        self.values: dict[str, str] = {}
        self.reads = 0
        self.writes = 0

    def get(self, source):
        self.reads += 1
        return self.values.get(source)

    def set(self, source, target):
        self.writes += 1
        self.values[source] = target


def timer_case():
    profile = None
    try:
        from pdf2zh.performance import PerformanceProfile

        profile = PerformanceProfile(enabled=True, cache_mode="cold-isolated")
    except ImportError:
        pass
    envs = {"performance_profile": profile} if profile else None
    with patch("pdf2zh.translator.TranslationCache", MemoryCache):
        google = GoogleTranslator("en", "vi", envs=envs)
    attempts = 0
    sleep_seconds = 0.0

    def provider(_text):
        nonlocal attempts
        attempts += 1
        if profile:
            profile.provider_request("google", len(_text), 5000)
        started = time.perf_counter()
        time.sleep(0.004)
        if profile:
            profile.add_seconds("google_wait_seconds", time.perf_counter() - started)
        if attempts < 3:
            raise RuntimeError("injected temporary failure")
        return "Thiết lập các tham số truyền thông."

    def backoff(seconds):
        nonlocal sleep_seconds
        started = time.perf_counter()
        if profile:
            profile.sleep(seconds)
        else:
            time.sleep(seconds)
        elapsed = time.perf_counter() - started
        sleep_seconds += elapsed

    google.do_translate = provider
    request = retry(wait=wait_fixed(0.012), stop=stop_after_attempt(3),
                    sleep=backoff, before=profile.retry_attempt if profile else lambda _state: None,
                    reraise=True)(google.translate)
    started = time.perf_counter()
    token = profile.start("layout_seconds") if profile else None
    local_started = time.perf_counter()
    time.sleep(0.004)
    local_seconds = time.perf_counter() - local_started
    wait_token = profile.start("translation_wall_seconds") if profile else None
    request("Set the communication parameters.")
    if profile:
        profile.stop(wait_token)
        profile.stop(token)
    total = time.perf_counter() - started
    return {
        "patch_wall_seconds": total,
        "legacy_translation_seconds": google.translation_seconds,
        "legacy_layout_seconds": max(0.0, total - google.translation_seconds),
        "measured_local_work_seconds": local_seconds,
        "measured_retry_sleep_seconds": sleep_seconds,
        "request_attempts": attempts,
        "profile": profile.snapshot(total_seconds=total,
                                    translation_seconds=google.translation_seconds) if profile else None,
    }


def concurrent_timer_case():
    def provider():
        started = time.perf_counter()
        time.sleep(0.015)
        return time.perf_counter() - started

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        work_sum = sum(pool.map(lambda _index: provider(), range(2)))
    wall = time.perf_counter() - started
    return {"patch_wall_seconds": wall, "provider_work_sum_seconds": work_sum,
            "legacy_layout_seconds": max(0.0, wall - work_sum)}


def cache_cases():
    text = "Set the communication parameters."
    target = "Thiết lập các tham số truyền thông."
    cases = {}
    for mode in ("cold-isolated", "warm"):
        with patch("pdf2zh.translator.TranslationCache", MemoryCache):
            auto = AutoTranslator("en", "vi")
        if mode == "warm":
            auto.google.cache.values[text] = target
        calls = []

        def provider(source, calls=calls):
            calls.append(source)
            return target

        auto.google.do_translate = provider
        result = auto.translate_with_identity(text, "synthetic-unit-1")
        cases[mode] = {
            "result": result,
            "google_cache_reads": auto.google.cache.reads,
            "google_cache_writes": auto.google.cache.writes,
            "handoff_cache_reads": auto.handoff.cache.reads,
            "provider_requests": len(calls),
            "google_cache_hits": auto.metrics()["google_cache_hits"],
            "selected_route": auto.routing_traces[0]["selected_route"],
        }
    return cases


def instrumentation_overhead():
    from pdf2zh.performance import PerformanceProfile

    trials = {"disabled": [], "enabled": []}
    for trial in range(3):
        order = (False, True) if trial % 2 == 0 else (True, False)
        for enabled in order:
            profile = PerformanceProfile(enabled=enabled)
            started = time.perf_counter()
            for _ in range(100):
                with profile.stage("batch_build_seconds"):
                    profile.record_batch("google", 1, 100, 5000)
                with profile.stage("google_wait_seconds"):
                    time.sleep(0.005)
                profile.count("google_http_requests")
            trials["enabled" if enabled else "disabled"].append(time.perf_counter() - started)
    disabled, enabled = (statistics.median(trials[key]) for key in ("disabled", "enabled"))
    return {"workload": "100 injected 5ms waits; one batch/two stage boundaries per wait; 3 alternating trials",
            "trials_seconds": trials, "disabled_median_seconds": disabled,
            "enabled_median_seconds": enabled, "observed_delta_fraction": enabled / disabled - 1,
            "real_pdf_overhead_verified": False,
            "interpretation": "synthetic scheduling noise included; no production <1% assertion"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with patch("requests.sessions.Session.request", side_effect=AssertionError("live provider prohibited")):
        result = {"workload": "synthetic strings; injected waits; in-memory isolated cache",
                  "real_pdf": False, "live_provider": False,
                  "timer_case": timer_case(), "concurrent_timer_case": concurrent_timer_case(),
                  "cache_cases": cache_cases(), "instrumentation_overhead": instrumentation_overhead()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
