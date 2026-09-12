"""Controlled string/ledger retry benchmark; never opens PDFs or calls HTTP."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pdfminer.pdfinterp import PDFResourceManager
from tenacity import retry, stop_after_attempt, wait_exponential

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from pdf2zh.converter import (
    TranslateConverter,
    approved_preserve_reason,
    is_translatable_segment,
    preferred_translation,
    should_share_translation,
)
from pdf2zh.integrity import EligibleSourceSpan, Occurrence, OccurrenceStatus
from pdf2zh.performance import PerformanceProfile
from pdf2zh.translator import (
    encode_formula_placeholders,
    restore_formula_placeholders,
    segment_identifier,
)
from scripts.benchmark_patch_d_synthetic import MemoryCache

FAILED_SOURCE = "Set D100 to 10 and D200 to 20."
GOOD_SOURCE = "Set the communication parameters."
TRANSPORT_SOURCE = "Check the connection before using the controller."
LONG_SOURCE = "A long source paragraph with unchanged words. " * 120
TARGETS = {
    FAILED_SOURCE: "Đặt D100 thành 20 và D200 thành 10.",
    GOOD_SOURCE: "Thiết lập các tham số truyền thông.",
    TRANSPORT_SOURCE: "Kiểm tra kết nối trước khi sử dụng bộ điều khiển.",
    "Manual": "Manual",
}


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def legacy_worker(converter, source, identity, context, _key):
    """Replay the pre-D2 Tenacity policy for the identical controlled workload."""
    request = retry(wait=wait_exponential(multiplier=1, min=1, max=60),
                    stop=stop_after_attempt(8), sleep=converter.profile.sleep,
                    before=converter.profile.retry_attempt, reraise=True)(
        converter.translator.translate_with_identity
    )
    try:
        preferred = preferred_translation(source, converter.translator.lang_out)
        if preferred is not None:
            converter.translator.validate(source, preferred)
            return preferred, "translated", None, ()
        translated = request(encode_formula_placeholders(source), identity, context=context)
        return restore_formula_placeholders(source, translated), "translated", None, ()
    except Exception as error:  # noqa: BLE001 - reproduce the legacy unresolved fallback
        return source, "unresolved", type(error).__name__, ()


def run_workload(*, legacy=False, quality_recovers=False):
    clock = Clock()
    profile = PerformanceProfile(enabled=True, clock=clock, cache_mode="cold-isolated")
    sleep = profile.sleep
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        sleep(seconds, sleeper=clock.advance)

    profile.sleep = fake_sleep
    with patch("pdf2zh.translator.TranslationCache", MemoryCache):
        converter = TranslateConverter(PDFResourceManager(), service="google", lang_in="en",
                                       lang_out="vi", thread=4, envs={"performance_profile": profile})
    requests_by_source = {}

    def fake_get(_endpoint, *, params, **_kwargs):
        from requests import ConnectionError

        source = params["q"]
        requests_by_source[source] = requests_by_source.get(source, 0) + 1
        clock.advance(0.02)
        if source == TRANSPORT_SOURCE and requests_by_source[source] < 3:
            raise ConnectionError("injected transport failure")
        target = TARGETS[source]
        if quality_recovers and source == FAILED_SOURCE and requests_by_source[source] >= 3:
            target = "Đặt D100 thành 10 và D200 thành 20."
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               text='<div class="result-container">' + html.escape(target) + '</div>')

    converter.translator.session.get = fake_get
    d2 = hasattr(converter, "_translate_job") and not legacy
    request = converter._make_translation_request() if d2 else None
    pages = [[FAILED_SOURCE, GOOD_SOURCE, TRANSPORT_SOURCE, "Manual", "D100", LONG_SOURCE],
             [FAILED_SOURCE, GOOD_SOURCE, "Manual", "D100"]]
    outcomes = []
    started = time.perf_counter()
    with patch("requests.sessions.Session.request", side_effect=AssertionError("live HTTP prohibited")):
        for page, sources in enumerate(pages):
            for index, source in enumerate(sources):
                encoded = encode_formula_placeholders(source)
                shared = should_share_translation(source, "google")
                key = ("shared", source) if shared else ("occurrence", index)
                identity = segment_identifier(encoded if shared else f"{encoded}\0page={page}\0index={index}")
                preserve_reason = None
                if not is_translatable_segment(source, ()):
                    result = (source, "preserved", None, ())
                    preserve_reason = approved_preserve_reason(source, explicitly_preserved=False)
                elif d2:
                    result = converter._translate_job(source, identity, None, key, request)
                else:
                    result = legacy_worker(converter, source, identity, None, key)
                target, status, reason, failure_codes = result
                occurrence_id = f"page-{page}-occ-{index}"
                span_id = f"page-{page}-span-{index}"
                converter.integrity_ledger.add_eligible_span(EligibleSourceSpan(
                    source_span_id=span_id, page=page, source_text=source, logical_unit_id=identity))
                converter.integrity_ledger.record_occurrence(Occurrence(
                    occurrence_id=occurrence_id, page=page, source_span_ids=(span_id,), source_text=source,
                    logical_unit_id=identity, status={"translated":OccurrenceStatus.TRANSLATED,
                    "preserved":OccurrenceStatus.ALLOWED_PRESERVE,
                    "unresolved":OccurrenceStatus.UNRESOLVED}[status], target_text=target,
                    preserve_reason=preserve_reason, preserve_reason_is_approved=preserve_reason is not None,
                    unresolved_reason=reason, validation_failures=failure_codes))
                outcomes.append({"occurrence_id":occurrence_id, "identity":identity,
                                 "status":status, "reason":reason})
    real_elapsed = time.perf_counter() - started
    metrics = converter.integrity_ledger.reconcile()
    translator_metrics = converter.translator.metrics()
    profile.set_workload({"source_pages":2, "translated_occurrences":metrics.translated_occurrences,
                          "allowed_preserve_occurrences":metrics.allowed_preserve_occurrences,
                          "unresolved_occurrences":metrics.unresolved_occurrences,
                          "google_provider_requests":translator_metrics["translation_requests"],
                          "google_cache_hits":translator_metrics["cache_hits"]})
    return {"policy":"D2" if d2 else "legacy8", "synthetic_total_seconds":clock.value,
            "local_test_elapsed_seconds":real_elapsed, "provider_calls":sum(requests_by_source.values()),
            "provider_calls_by_source":requests_by_source, "retry_sleep_schedule":sleeps,
            "outcomes":outcomes,
            "identity_sets":{bucket:sorted({row["identity"] for row in outcomes if row["status"]==bucket})
                             for bucket in ("translated","preserved","unresolved")},
            "accounting":{"accounting_coverage":metrics.accounting_coverage,
                          "duplicate_source_span_assignments":metrics.duplicate_source_span_assignments,
                          "unassigned_source_spans":metrics.unassigned_source_spans,
                          "translated_occurrences":metrics.translated_occurrences,
                          "allowed_preserve_occurrences":metrics.allowed_preserve_occurrences,
                          "unresolved_occurrences":metrics.unresolved_occurrences},
            "profile":profile.snapshot(total_seconds=clock.value,
                                        translation_seconds=translator_metrics["translation_seconds"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--legacy", action="store_true", help="Replay the old 8-attempt policy")
    parser.add_argument("--compare-before", type=Path, help="Compare exact outcomes/sets/accounting to saved baseline")
    parser.add_argument("--comparison-output", type=Path)
    parser.add_argument("--quality-recovery-probe", action="store_true",
                        help="Offline counterexample: quality fails twice, then succeeds")
    args = parser.parse_args()
    if bool(args.compare_before) != bool(args.comparison_output):
        parser.error("--compare-before and --comparison-output must be supplied together")
    result = {"real_pdf":False,"live_provider":False,"cache_mode":"cold-isolated",
              "workload":"identical strings, fake HTTP responses, temporary in-memory cache, synthetic ledger",
              "timing_note":"Synthetic clock advances 0.02s/request and nominal sleep; not a PDF speedup",
              "converter_source_sha256":hashlib.sha256((SKILL_ROOT/"pdf2zh/converter.py").read_bytes()).hexdigest(),
              "result":run_workload(legacy=args.legacy)}
    if args.quality_recovery_probe:
        before = run_workload(legacy=True, quality_recovers=True)
        after = run_workload(quality_recovers=True)
        result["quality_recovery_probe"] = {
            "real_pdf": False, "live_provider": False,
            "behavior": "For the same source, provider outputs fail quality twice and succeed on call 3",
            "before": before, "after": after,
            "identity_sets_equal": before["identity_sets"] == after["identity_sets"],
            "finding": "UNIVERSAL_OUTCOME_EQUIVALENCE_COUNTEREXAMPLE",
        }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding="utf-8")
    comparison_ok = True
    if args.compare_before:
        before = json.loads(args.compare_before.read_text(encoding="utf-8"))["result"]
        after = result["result"]
        checks = {key:before[key] == after[key] for key in ("outcomes", "identity_sets", "accounting")}
        comparison_ok = all(checks.values())
        comparison = {
            "scope": "controlled synthetic workload only; real PDF benchmark NOT_RUN",
            "checks": checks,
            "identity_sets": {"before": before["identity_sets"], "after": after["identity_sets"]},
            "identity_set_differences": {
                bucket: {"removed": sorted(set(before["identity_sets"][bucket]) - set(after["identity_sets"][bucket])),
                         "added": sorted(set(after["identity_sets"][bucket]) - set(before["identity_sets"][bucket]))}
                for bucket in ("translated", "preserved", "unresolved")
            },
            "accounting": {"before": before["accounting"], "after": after["accounting"]},
            "status": "PASS_SYNTHETIC_ONLY" if comparison_ok else "FAIL",
        }
        args.comparison_output.parent.mkdir(parents=True, exist_ok=True)
        args.comparison_output.write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n',encoding="utf-8")
    print(json.dumps({key:result["result"][key] for key in
                      ("policy","synthetic_total_seconds","provider_calls","accounting")},ensure_ascii=True))
    return 0 if comparison_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
