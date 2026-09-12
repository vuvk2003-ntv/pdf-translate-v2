# Patch D — profiling implementation and acceptance report

State: **PATCH_D_FAIL (real-document acceptance not established)**.
The requested skill/code changes are implemented. The user's explicit
instruction forbids PDF processing/translation, so no PDF was opened, extracted,
rendered or translated and no live provider was called for this patch. Required
J1C/Mitsubishi benchmarks and measured optimization gates remain `NOT_RUN`.
No production speedup, external-latency dominance or absence of a real
bottleneck is claimed. No Patch E, renderer replacement or concurrency refactor
was introduced.

## Exact old timer boundaries

LAYOUT_TIMER_SCOPE:
`high_level.translate_stream`: `prepared` immediately before `translate_patch`,
`patched` immediately after it; old value was
`max(0, patched - prepared - report.translation_seconds)`.
The patch traverses `PDFPageInterpreterEx.process_page` →
`TranslateConverter.receive_layout` → existing `ThreadPoolExecutor` workers →
Tenacity `request_translation` → translator, before native fitting returns.

RETRY_TIMER_SCOPE:
`TranslateConverter.receive_layout.request_translation` wraps
`translate_with_identity` with eight attempts and exponential backoff
(minimum 1s, maximum 60s). Sleep occurs between attempts within the worker/pool
wait. The old translator timer covered an individual translation attempt and
did not cover Tenacity sleep. Nominal backoff before eight failed attempts is
1+2+4+8+16+32+60 = 123s per unit, in addition to request work. Scheduling may
increase actual sleep; the new report measures elapsed sleep.

PROVIDER_WAIT_TIMER_SCOPE:
`BaseTranslator.translate` starts its legacy timer after cache lookup, before
masking/provider/restore, and stops in `finally` before result validation.
Google's `do_translate` calls `session.get(..., timeout=30)` inside that scope.
That timer sums attempts across concurrently executing workers; it is not
elapsed document wall time.

DOES_LAYOUT_SECONDS_INCLUDE_RETRY_OR_PROVIDER_WAIT: **YES** for retry/backoff
and residual pool/translation work. HTTP wait is inside the patch and is also
included in the subtracted legacy request sum; it is therefore not cleanly
attributable to layout. Concurrent request sums can exceed patch wall time and
clamp the old layout report to zero. The supplied 50-page baseline
(9990.765 total / 7922.427 layout / 2064.240 translation seconds) is evidence
of a suspicious report, not a verified 79.3% layout CPU bottleneck. Its exact
retry/provider decomposition cannot be recovered from aggregate numbers.

## New measured boundaries

The profiler uses nested per-thread boundaries, excluding child elapsed time
from parents. Main-thread `translation_wall_seconds` removes the entire pool
wait from layout. Worker stages overlap that wall interval; summed stages and
their wall-time ratios must not be treated as an additive pie chart.

| Stage | Boundary / basis |
| --- | --- |
| prepare | Runner dependency/input/terminology checks; core file/model/font/internal document preparation; nested stages excluded |
| extract | Existing PDF parser/document construction, existing page text/drawing reads and interpreter traversal; nested converter stages excluded |
| protection | Existing table/formula/metadata/anchoring protection region plus provider literal masking; nested reads/reconstruction excluded |
| reconstruction | Existing `group_translatable_line_clusters`, unchanged inputs/merge rules |
| coverage_accounting | Eligible-span/occurrence ledger construction, assignment/outcomes and final reconcile |
| routing | Existing `route_logical_unit`; existing decisions are passed through and reused |
| cache_lookup | Physical existing SQLite/cache `get` calls; validation separate |
| batch_build | Existing per-page identity/job planning and fanout; pending Handoff batch estimate |
| google_wait | Actual `session.get`, including failures, excluding local response parsing |
| handoff_prepare | Existing pending-record enqueue/write; `prepare_handoff.py` batch building |
| handoff_import | Existing validated segment-table/source/result loading and assessment |
| handoff_wait | Unknown external MODEL_A interval: 0 with `handoff_external_wait_measured=false` |
| retry | Actual Tenacity sleeper elapsed time; attempts counted independently |
| layout | Exclusive remaining model/geometry/native fitting work; no translation pool wait |
| render | Existing font installation/content replacement/resource inventory/final serialization |
| validation | Existing mandatory translator validators, also on cache reuse |
| final_qa | Source immutability/runtime checks, candidate facts/page gates, final text-layer audit, layout QA, staging/final hashes |
| translation | Legacy request-duration sum retained; not additive with detailed stages |
| total | Profile runner elapsed through final QA/promotion; core-only for direct core calls |

Legacy `TranslationReport.total_seconds` remains the core interval. Detailed
profile total includes the outer runner. Report JSON writing is excluded.
The disabled layout value is now a **local patch pipeline aggregate**, obtained
by subtracting measured pool wall time; enable profiling to split local stages.

## BEFORE / CHANGE / AFTER evidence

BEFORE was saved before production edits in
[`patch-d-synthetic-before.json`](patch-d-synthetic-before.json).
AFTER is [`patch-d-synthetic-after.json`](patch-d-synthetic-after.json).
Both use the same strings, retry attempts and in-memory isolated cache cases.
Short waits are injected, not provider latency. Profiling hooks were added to
the AFTER harness to expose individual categories; no timing difference between
the runs is attributed to optimization.

| Synthetic retry fixture | BEFORE seconds | AFTER seconds |
| --- | ---: | ---: |
| Patch wall | 0.047105 | 0.047563 |
| Legacy request sum | 0.012996 | 0.013960 |
| Legacy layout residual | 0.034109 | 0.033603 |
| Separately measured local fake work | 0.004142 | 0.004032 |
| New exclusive layout report | unavailable | 0.004044 |
| New measured retry sleep | unavailable | 0.024758 |
| New fake provider work | unavailable | 0.013706 |

OPTIMIZATION: **none**. CHANGE: timer attribution/reporting and safe local
planning, with no retry removal, provider scheduling change or cache shortcuts.
SPEEDUP: **not claimed**. A lower correctly attributed layout number is not
less elapsed work.

Ranked injected contributors, using AFTER wall time: RETRY_BACKOFF 52.1%,
PROVIDER_WAIT 28.8%, LAYOUT/local fake work 8.5%, validation LOCAL_CPU 3.6%.
These fixture shares demonstrate the timer error only; they do not rank the
Mitsubishi pipeline. The concurrency fixture independently shows about
0.016s elapsed versus 0.031s summed provider work and a zero old residual.

Retry blocking analysis: a sleeping retry occupies its existing worker slot.
Other active workers continue; queued unrelated jobs can wait if all bounded
slots are occupied. The retry closure receives one source and identity and
retries that unit, not a page/batch/document. This behavior and eight-attempt
policy were preserved. No actual dominant retry bottleneck was measured.

## Cold/warm and cache equivalence

Exact `cache_cases` JSON equality was checked BEFORE versus AFTER.

| Isolated fixture | Google cache reads | Writes | Handoff reads | Fake provider calls | Google cache hits | Route |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Cold isolated | 2 | 1 | 1 | 1 | 0 | GOOGLE |
| Warm | 1 | 0 | 0 | 0 | 1 | GOOGLE |

The cold Auto path intentionally retains both Google probes; skipping the
second probe could change cache observations under concurrent updates. No
in-memory index, key weakening, namespace merging, TTL change or acceptance of
invalid cache entries was introduced. Added tests compare enabled/disabled
outcomes, probe/write/request counts, routing and escalation for cold, warm,
invalid and bypassed cache states. This is fixture equivalence, not exhaustive
proof over every real-document identity. No production cache was cleared.

## Batch/workload and overhead limits

All requested stage/workload fields are present in enabled snapshots. Original
A/B/C counters keep their definitions. New physical HTTP attempts are
`google_http_requests`; existing `google_provider_requests` counts translation
attempts, including attempts that might fail before HTTP. Actual sent
units/chars and planned/enqueued work are separate. Handoff MODEL_A sends zero
provider batches/units/chars in this process; queue/planned batches are local
workflow evidence only.

For each observed batch path, report count, units/chars average/median,
character p50/p90/p99/max and character-capacity utilization. A deterministic
test with 100/200/300/400 characters verifies p50=250, p90=370, p99=397 and
5% utilization at capacity 5000. A 65-unit Handoff fixture plans 30/30/5 using
the unchanged 30-unit/12,000-character limits and retains identities.

Google's existing `/m` endpoint accepts one unit per request (maximum 5000
characters). No established safe multi-unit protocol exists in this path.
Singleton requests remain a baseline limitation, not a newly introduced
regression. The prompt's safe multi-unit Google batching objective remains
**unmet**; no fabricated concatenation/batch implementation was added.

Disabled profiles do not call their system clock or collect histograms;
expensive added character totals are guarded by `enabled`. Existing raw wall
timers remain at pool/page boundaries. No character/span-level clock was added.
The alternating three-trial injected-wait overhead fixture measured disabled
median 0.526770s, enabled 0.534055s, observed delta **+1.38%**. Scheduler noise
is included. The practical enabled <1% overhead target is **not established**,
and no favorable trial is substituted. Production overhead requires permitted
representative measurements. Histogram storage is compact; index memory is
zero, and temporary Handoff planning records are cleared after batch planning.

## Investigated but not optimized

| Investigated / category | Measured share | Decision and reason |
| --- | --- | --- |
| Retry / RETRY_BACKOFF | 52.1% injected only | NOT_OPTIMIZED: fake waits do not justify production scheduling changes |
| Google / PROVIDER_WAIT | 28.8% injected only | NOT_OPTIMIZED: no real external critical-path measurement or >50% evidence |
| Cache / CACHE | <0.1% injected fixture | NOT_OPTIMIZED: preserve exact observations; no meaningful measured real bottleneck |
| Protection, routing, validation / LOCAL_CPU | Validation 3.6% injected; rest NOT_MEASURED on PDF | NOT_OPTIMIZED: current validators/policy preserved; no confirmed expensive matcher |
| Extraction/reconstruction / LOCAL_CPU, LOCAL_IO | NOT_MEASURED on PDF | NOT_OPTIMIZED: existing multiple extraction reads observed, but no measured cost; no new preprocessing pipeline |
| Handoff / HANDOFF_WORKFLOW | NOT_MEASURED on PDF; external unknown | NOT_OPTIMIZED: retain targeted identity workflow and existing caps |
| Layout / LAYOUT | 8.5% fake work; real NOT_MEASURED | NOT_OPTIMIZED: old 7922s residual is insufficient CPU evidence |
| Render/final QA / RENDER, LOCAL_IO | NOT_MEASURED on PDF | NOT_OPTIMIZED: native renderer/page gates unchanged; no PDF verification permitted |

No new A/B/C quality defect was established by permitted fixtures. Real
short-continuation/fallback/preservation/invariant/overlap defects remain
outside this patch and cannot be excluded without PDF evidence.

## Acceptance gates

251 selected pure tests pass, including 22 Patch D tests, plus A/B/C,
Handoff/cache/identity, terminology, proper-name, preservation and Korean
fixtures. Tests use mock providers, strings/ledgers and temporary JSONL/cache;
none opens or creates a PDF. Whole-suite discovery/render QA was not run.

Validation: Python compilation, repository hard-error Ruff checks, full Ruff on
new Python files, skill `quick_validate`, `pip check` and `git diff --check`
pass. Broader import-order checking also found existing formatting issues in
untouched app/test files; those unrelated files were retained. The output PDF
directory contains zero PDFs. The user's pre-existing `rules.py` confidentiality
word-boundary edit was preserved. No commit or push was performed.

| Gate | Status / evidence |
| --- | --- |
| Patch A/B/C pure regression | PASS |
| Cache/routing/protection fixture equivalence | PASS |
| Timer nesting, thread safety, failed HTTP timing, retry timing/counts | PASS |
| Planned misses do not call providers/write pending queues | PASS |
| Handoff batch identity/caps and percentile statistics | PASS |
| Native renderer, merge rules, validation rules, retry limits, worker count unchanged | PASS by code inspection; real layout remains unverified |
| No new full-document extraction/reconstruction pipeline | PASS by code inspection |
| No unrelated retransmission / semantic pass / OCR / reflow fallback | PASS in inspected code and fixtures |
| J1C full 19-page regression | NOT_RUN: user forbids PDF processing |
| Mitsubishi full 248-page provider-free plan | NOT_RUN: still requires PDF processing |
| Pinned representative-range real-provider BEFORE/AFTER | NOT_RUN: PDF/providers prohibited; no matching range metadata located; no replacement ranges selected |
| Optional full 248-page provider run | NOT_RUN; separate authorization required |
| Page count/size/rotation/mapping, coverage and real unresolved/layout regression | NOT_RUN |
| Confirmed real bottleneck >10%, optimized stage halves/becomes negligible | NOT_RUN |
| Real elapsed/provider work improves; no local stage >20% | NOT_RUN |
| Practical enabled instrumentation overhead <1% | NOT_ESTABLISHED |
| Safe multi-unit provider batching | UNMET for existing Google mobile path; Handoff planning PASS |

Final state remains **PATCH_D_FAIL**, specifically because full Patch D
performance/layout acceptance cannot be met under the current no-PDF
instruction. The installed skill now provides the instrumentation, report
definitions and stopping rules needed for a later authorized measurement.
