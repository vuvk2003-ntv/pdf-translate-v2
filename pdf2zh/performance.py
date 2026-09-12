"""Optional boundary profiling; never selects routes, providers, or cache keys."""

from __future__ import annotations

import math
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

STAGE_NAMES = (
    "prepare_seconds", "extract_seconds", "protection_seconds",
    "reconstruction_seconds", "coverage_accounting_seconds", "routing_seconds",
    "cache_lookup_seconds", "batch_build_seconds", "google_wait_seconds",
    "handoff_prepare_seconds", "handoff_wait_seconds", "handoff_import_seconds",
    "retry_seconds", "layout_seconds", "render_seconds", "final_qa_seconds",
    "validation_seconds", "translation_wall_seconds",
)


@dataclass
class _Stage:
    name: str
    started: float
    children: float = 0.0


def _percentile(histogram: Counter[int], fraction: float) -> float:
    """Linear percentile from compact value/count data, without expanding it."""
    count = sum(histogram.values())
    if not count:
        return 0.0
    position = (count - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    found: dict[int, int] = {}
    consumed = 0
    for value, occurrences in sorted(histogram.items()):
        for rank in (lower, upper):
            if consumed <= rank < consumed + occurrences:
                found[rank] = value
        consumed += occurrences
        if len(found) == len({lower, upper}):
            break
    return found[lower] + (found[upper] - found[lower]) * (position - lower)


class PerformanceProfile:
    """Accumulate exclusive per-thread stage time and compact workload metrics.

    Worker times are sums and can exceed elapsed time with concurrency. The
    main thread's translation_wall stage excludes the whole pool wait from
    layout, including retry/backoff. No clock is read when disabled.
    """

    def __init__(self, *, enabled: bool = False, clock: Callable[[], float] = time.perf_counter,
                 cache_mode: str = "production", plan_only: bool = False):
        if cache_mode not in {"production", "cold-isolated", "warm"}:
            raise ValueError("unsupported cache measurement label")
        self.enabled = enabled or plan_only
        self.plan_only = plan_only
        self.cache_mode = cache_mode
        self._clock = clock
        self._local = threading.local()
        self._lock = threading.Lock()
        self._seconds: Counter[str] = Counter()
        self._counts: Counter[str] = Counter()
        self._batches: dict[str, tuple[Counter[int], Counter[int], int | None]] = {}
        self._retry_total = 0.0
        self._retry_max = 0.0
        self._retry_sleeps = 0
        self._handoff_sources: dict[str, dict[str, Any]] = {}

    def start(self, name: str) -> _Stage | None:
        if not self.enabled:
            return None
        token = _Stage(name, self._clock())
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = self._local.stack = []
        stack.append(token)
        return token

    def stop(self, token: _Stage | None) -> float:
        if token is None:
            return 0.0
        elapsed = max(0.0, self._clock() - token.started)
        stack = self._local.stack
        if not stack or stack[-1] is not token:
            raise RuntimeError("performance stages must close in stack order")
        stack.pop()
        if stack:
            stack[-1].children += elapsed
        exclusive = max(0.0, elapsed - token.children)
        self.add_seconds(token.name, exclusive)
        return elapsed

    @contextmanager
    def stage(self, name: str):
        token = self.start(name)
        try:
            yield
        finally:
            self.stop(token)

    def call(self, name: str, function: Callable, *args, **kwargs):
        if not self.enabled:
            return function(*args, **kwargs)
        with self.stage(name):
            return function(*args, **kwargs)

    def add_seconds(self, name: str, seconds: float) -> None:
        if self.enabled:
            with self._lock:
                self._seconds[name] += seconds

    def count(self, name: str, amount: int = 1) -> None:
        if self.enabled:
            with self._lock:
                self._counts[name] += amount

    def set_workload(self, values: Mapping[str, int]) -> None:
        if self.enabled:
            with self._lock:
                self._counts.update({name: value - self._counts[name] for name, value in values.items()})

    def cache_result(self, provider: str, status: str) -> None:
        self.count(f"{provider}_cache_probes")
        self.count(f"{provider}_cache_{'misses' if status in {'miss', 'invalid'} else status}")

    def record_batch(self, path: str, units: int, characters: int,
                     character_capacity: int | None = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            units_hist, chars_hist, capacity = self._batches.setdefault(
                path, (Counter(), Counter(), character_capacity)
            )
            if capacity != character_capacity:
                raise ValueError("one batch path cannot mix capacities")
            units_hist[units] += 1
            chars_hist[characters] += 1

    def provider_request(self, provider: str, characters: int, capacity: int | None) -> None:
        self.count(f"{provider}_units_sent")
        self.count(f"{provider}_chars_sent", characters)
        self.count(f"{provider}_http_requests")
        self.record_batch(provider, 1, characters, capacity)

    def queue_handoff(self, record: Mapping[str, Any], *, planned: bool = False) -> None:
        if not self.enabled:
            return
        identifier = record["segment_id"]
        with self._lock:
            if identifier in self._handoff_sources:
                return
            self._handoff_sources[identifier] = dict(record)
            prefix = "planned" if planned else "enqueued"
            self._counts[f"handoff_{prefix}_units"] += 1
            self._counts[f"handoff_{prefix}_chars"] += len(record["src"])

    def finish_handoff_plan(self) -> None:
        if not self.enabled:
            return
        from pdf2zh.handoff import build_handoff_batches

        with self._lock:
            sources = list(self._handoff_sources.values())
            self._handoff_sources.clear()
        if not sources:
            return
        oversized: list[dict[str, Any]] = []
        batches = self.call("batch_build_seconds", build_handoff_batches, sources,
                            oversized=oversized)
        for batch in batches:
            records = batch["segments"]
            characters = sum(len(item["src"]) + len(item.get("context", {}).get("text", ""))
                             for item in records)
            self.record_batch("handoff_planned", len(records), characters, 12_000)
        self.count("handoff_planned_oversized_units", len(oversized))

    def retry_attempt(self, retry_state: Any) -> None:
        if getattr(retry_state, "attempt_number", 1) > 1:
            self.count("retry_requests")

    def sleep(self, seconds: float, sleeper: Callable[[float], None] = time.sleep) -> None:
        if not self.enabled:
            sleeper(seconds)
            return
        token = self.start("retry_seconds")
        try:
            sleeper(seconds)
        finally:
            elapsed = self.stop(token)
            with self._lock:
                self._retry_total += elapsed
                self._retry_max = max(self._retry_max, elapsed)
                self._retry_sleeps += 1

    def snapshot(self, *, total_seconds: float = 0.0,
                 translation_seconds: float = 0.0) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "cache_mode": self.cache_mode,
                    "plan_only": self.plan_only, "detailed_timings_available": False}
        with self._lock:
            timings = {name: float(self._seconds[name]) for name in STAGE_NAMES}
            timings.update(total_seconds=total_seconds, translation_seconds=translation_seconds)
            workload = dict(self._counts)
            for name in ("source_pages", "raw_text_spans", "candidate_fragments", "logical_units",
                         "provider_bound_units", "total_source_chars", "provider_bound_chars",
                         "translated_occurrences", "allowed_preserve_occurrences", "unresolved_occurrences",
                         "preserve_routed_units", "google_routed_units", "handoff_direct_units",
                         "google_to_handoff_escalations", "handoff_provider_batches_or_requests",
                         "retry_units", "retry_requests"):
                workload.setdefault(name, 0)
            for provider in ("google", "handoff"):
                for suffix in ("cache_hits", "cache_misses", "provider_requests", "units_sent", "chars_sent"):
                    workload.setdefault(f"{provider}_{suffix}", 0)
            batches = {}
            histogram_bytes = 0
            for path, (units, chars, capacity) in self._batches.items():
                count = sum(units.values())
                unit_total = sum(value * occurrences for value, occurrences in units.items())
                char_total = sum(value * occurrences for value, occurrences in chars.items())
                batches[path] = {
                    "batch_count": count,
                    "units_per_batch_average": unit_total / count if count else 0.0,
                    "units_per_batch_median": _percentile(units, 0.5),
                    "chars_per_batch_average": char_total / count if count else 0.0,
                    "chars_per_batch_median": _percentile(chars, 0.5),
                    "chars_per_batch_p50": _percentile(chars, 0.5),
                    "chars_per_batch_p90": _percentile(chars, 0.9),
                    "chars_per_batch_p99": _percentile(chars, 0.99),
                    "chars_per_batch_max": max(chars, default=0),
                    "batch_capacity_utilization": char_total / (count * capacity) if capacity and count else None,
                    "character_capacity": capacity,
                }
                histogram_bytes += sys.getsizeof(units) + sys.getsizeof(chars)
                histogram_bytes += sum(sys.getsizeof(key) + sys.getsizeof(value)
                                       for histogram in (units, chars) for key, value in histogram.items())
            return {
                "enabled": True, "cache_mode": self.cache_mode, "plan_only": self.plan_only,
                "timing_basis": "exclusive per thread; worker time summed; translation_wall overlaps worker stages",
                "layout_excludes_provider_wait_and_retry": True,
                "legacy_translation_seconds_is_request_time_sum": True,
                "handoff_execution_model": "MODEL_A",
                "handoff_external_wait_measured": False,
                "timings": timings, "workload": workload, "batches": batches,
                "stage_shares": {name: seconds / total_seconds if total_seconds else 0.0
                                 for name, seconds in timings.items() if name not in {"total_seconds", "translation_seconds"}},
                "retry": {"sleep_count": self._retry_sleeps, "retry_seconds": self._retry_total,
                          "average_retry_seconds": self._retry_total / self._retry_sleeps if self._retry_sleeps else 0.0,
                          "max_retry_seconds": self._retry_max},
                "index_memory_bytes": 0, "batch_histogram_memory_bytes": histogram_bytes,
            }


DISABLED_PROFILE = PerformanceProfile()


def profile_from_env(envs: Mapping[str, Any] | None) -> PerformanceProfile:
    profile = (envs or {}).get("performance_profile")
    return profile if isinstance(profile, PerformanceProfile) else DISABLED_PROFILE
