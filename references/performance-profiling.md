# Performance profiling (Patch D and D2)

Contents: execution modes; timer definitions; workload/cache/batches; benchmark
workflow; acceptance and stopping conditions; D2 retry/cache policy.

## Execution modes

Use the skill's virtual-environment interpreter and absolute script paths.

- `translate_pdf.py --profile-performance` enables the detailed report during
  an already authorized translation. Without profiling, no detailed stage
  clocks/histograms are collected.
- `--performance-report <separate.json>` implies profiling and saves the JSON;
  otherwise it prints to stdout. Reports must not alias source PDFs, segments,
  terminology, or output artifacts.
- `--profile-only` implies profiling and runs extraction, existing protection,
  Patch A reconstruction, Patch B accounting, Patch C routing and validated
  cache/batch planning. It stops before translation providers, pending-queue
  writes, native text fitting and output PDF serialization. It rejects
  `--output-dir` and `--emit-segments`. Preparation still opens and processes
  the PDF, uses the existing model/fonts and may serialize internal working
  copies. It is **not** a no-PDF execution mode.
- `prepare_handoff.py --profile-performance` reports local source/result import,
  batch preparation and validation. It retains the existing JSONL outputs and
  retry workflow; it never measures the agent's external translation time.
- `--cache-mode-label production|cold-isolated|warm` labels measurements only.
  It does not isolate, warm, clear or relocate the cache. A cold claim requires
  a genuinely isolated cache fixture. Never delete the production cache.

If PDF processing is forbidden, do not invoke the translation runner, even with
`--profile-only`. Run `tests.test_patch_d_performance` and the synthetic
`benchmark_patch_d_synthetic.py` instead. They use strings, injected clocks or
short fake waits, in-memory caches and temporary JSONL. Do not run `unittest
discover`: other suites construct/open PDFs. Record prohibited benchmarks as
`NOT_RUN`, without manufacturing speedups or inferred quality passes.

## Timer definitions

The original layout report subtracted summed request durations from elapsed
`translate_patch` wall time. Tenacity's sleep occurs outside request timing but
inside the patch, contaminating the residual. Concurrent request time can exceed
elapsed time, causing that residual to clamp to zero. The Mitsubishi 50-page
values supplied by the user therefore do not prove that layout computation
consumed 7,922 seconds. See [the boundary analysis and evidence](../docs/PATCH_D_PERFORMANCE.md).

Detailed timers are exclusive within each thread. Nested child elapsed time is
removed from the parent. Worker timers are summed across workers and can exceed
wall time. The main thread's `translation_wall_seconds` excludes the complete
pool wait, including retries, from layout. Worker stages overlap that wall
interval; stage shares are diagnostic ratios, **not additive percentages**.
Do not use summed provider time alone to authorize a concurrency refactor;
verify external-wait dominance on the actual wall-clock critical path.

`translation_seconds` retains the old request-duration sum for compatibility.
It includes masking/provider/restore work per attempt and excludes retry sleep,
cache lookup and final validation. Detailed stages separately report prepare,
extract, protection, reconstruction, coverage accounting, routing, cache lookup,
batch build, Google wait, Handoff prepare/import, retry, layout, render,
validation and final QA. `handoff_wait_seconds=0` with
`handoff_external_wait_measured=false` means MODEL_A external time is unknown.

`performance_profile.timings.total_seconds` from the runner is elapsed time
through preparation and final QA/promotion. Legacy report `total_seconds` is
the core interval and is not silently redefined. JSON formatting/writing is
outside both. Direct core calls report their core interval only.

## Workload, cache and batches

Preserve A/B/C occurrence and route definitions. Source/provider-bound unit
counts are eligibility counts, not necessarily unique HTTP requests. Physical
Google attempts are `google_http_requests`; `google_provider_requests` retains
the existing request counter. Actual units/chars sent include repeated attempts
and count the masked payload. Physical cache misses include absent or rejected
entries, while bypasses and Handoff table hits have separate meanings. Cache
hits use existing validated-hit counters. Never compare differently defined
fields to claim less provider work.

Actual Google request batches and planned Google/Handoff batches are separate
paths. Batch statistics include count, units/chars average and median, character
p50/p90/p99/max, known character capacity and utilization. Planned units/batches
are estimates, not sent work. Oversized units are excluded intact by existing
limits. Google `/m` remains capped at 5,000 characters per unit; Handoff planning
uses the existing 30 units / 12,000 source-plus-context characters. Do not
concatenate identities or invent an unsupported Google batch protocol.

The profiler introduces no cache index (`index_memory_bytes=0`). Batch
histograms store value/count pairs rather than one record per request.
Handoff planning temporarily retains one record per pending identity, then
clears them after batching; it is proportional to pending source text.

## Benchmark and optimization workflow

Inspect boundaries and capture BEFORE before changing performance-sensitive
behavior. Rank real contributors by measured impact. Confirm a bottleneck
above 10% of elapsed time or provider-bound work; optimize the smallest safe
implementation detail, then measure identical inputs/cache modes again.
Do not optimize sub-1% stages for cosmetic gains.

Keep cold isolated and warm measurements separate. For identical source,
engine/provider configuration, rules/terminology revisions and cache state,
prove the same identity-to-cache-hit/miss mapping. Preserve mandatory current
validators, routing-before-cache, targeted escalation, retry identity and native
fixed-page rendering. A/B/C defects discovered during profiling belong in a
separate defect report; do not silently fix them in performance code.

When permitted, run the full J1C 19-page regression, the full Mitsubishi
248-page provider-free plan, and pinned representative ranges with providers.
Reuse existing benchmark range metadata; never silently select new ranges.
If ranges must change, record `BENCHMARK_RANGE_CHANGE` with the reason and run
both BEFORE and AFTER on the same new ranges. A full 248-page provider run is
optional and needs separate explicit authorization.

No concurrency refactor unless actual provider wait dominates more than 50%
of elapsed wall time. Keep any justified concurrency bounded, configurable,
provider-safe, identity-safe and order-safe, with no duplicate requests.
Do not redesign the renderer or add OCR, reflow, semantic review, quality rules
or a Patch E.

## Acceptance and stopping

Each optimization needs measured BEFORE/CHANGE/AFTER, elapsed/provider-work
improvement, A/B/C and PDF layout regression evidence. Optimized stage time
must halve or fall below 2% of baseline elapsed time. No remaining local stage
may exceed 20% without evidence of unavoidable external latency. Check page
count/size/rotation/mapping, coverage, unresolved outcomes, cache/routing,
technical protection, batching and targeted retries unchanged.

For every untouched stage, report measured share (or `NOT_MEASURED`),
`NOT_OPTIMIZED` and a concrete reason. Categorize remaining contributors as
LOCAL_CPU, LOCAL_IO, CACHE, PROVIDER_WAIT, RETRY_BACKOFF, HANDOFF_WORKFLOW,
LAYOUT or RENDER. Report only measured speedups.

Use `PATCH_D_COMPLETE` only after all gates pass. Use
`PATCH_D_NO_SIGNIFICANT_BOTTLENECK` only after adequate real measurements show
none above the threshold; use `PATCH_D_PROVIDER_LATENCY_DOMINATED` only with
measured external dominance. Otherwise use `PATCH_D_FAIL` and specify missing
gates. Synthetic timing correctness alone cannot satisfy the real-PDF gates.

## D2 typed retry and document negative cache

`pdf2zh/retry_policy.py` defines the worker's Tenacity stop policy. The only
current `SegmentTooLongError` raise is Google's length check before HTTP:
one local evaluation, zero HTTP calls and zero sleeps. Post-provider
`VerifiedProperNameError`, `TechnicalInvariantError`, `FormulaPlaceholderError`,
`TranslationIntegrityError` and `TerminologyConsistencyError` use two total
attempts with one 1-second wait. Unknown/transport exceptions retain eight
attempts and waits 1, 2, 4, 8, 16, 32, 60. Classification uses exception types.
The policy uses the terminal failure class and total Tenacity attempt number,
as specified in D2; mixed failures do not have separate per-class ladders.

One fresh `TranslateConverter` is created in each document's `translate_patch`
call. Its `known_unsafe_identities` mapping and lock exist only for that run.
Only an existing safe `("shared", source)` Google provider job without
context-sensitive Korean or attached context can write the mapping, after
terminal post-provider quality rejection. Store the first nonempty exception
class reason with atomic `setdefault`. Read the reason atomically and return
the ordinary unresolved result; the normal occurrence consumer records it
once and the existing final script audit remains mandatory.

Do not cache source-length checks, transport/internal errors, cancellations,
occurrence/context-sensitive jobs, Handoff/MODEL_A or AUTO jobs, local preferred
validation, post-request restoration, or fit/render/geometry failures.
Positive cache reuse, worker ordering/concurrency, Patch A reconstruction,
Patch B validators/accounting and Patch C routing remain their existing paths.
The separate Handoff JSONL three-attempt workflow is unchanged.

Additive profile workload counters are `content_quality_retry_attempts_capped`,
`pre_provider_deterministic_retries_suppressed`, `negative_cache_skips` and
`negative_cache_estimated_retry_backoff_seconds_saved`. The last counter is
only **1 second of estimated retry backoff per skip** at the two-attempt
budget; it is not measured wall-clock savings. Empty-reason rejection also
increments a diagnostic counter. A quality error on legacy attempt 8 does not
count as an earlier cap.

For a no-PDF D2 check, run:

```text
<python> <skill-root>/scripts/run_patch_d2_no_pdf_checks.py
<python> <skill-root>/scripts/benchmark_patch_d2_synthetic.py --output <separate-after.json> --compare-before <skill-root>/docs/patch-d2-synthetic-before.json --comparison-output <separate-comparison.json> --quality-recovery-probe
```

The runner selects 375 string/JSONL/ledger/geometry checks and guards PDF opening
and live HTTP. The benchmark uses fixed fake Google responses, an injected
clock and an isolated memory cache. Its comparison proves exact identity sets,
occurrence outcomes and coverage only for that controlled workload.

**D2 full acceptance is not established.** Real-document timing and identity
comparison are `NOT_RUN` in the current no-PDF implementation. An offline
counterexample also shows that a provider failing quality twice and succeeding
on call 3 changes translated/unresolved membership under the cap. Treat the
patch's universal outcome-preservation claim as `FAIL` for that probe; do not
claim `PATCH_D2_PASS`, ship-ready status or a real PDF speedup. A repeated shared
identity may likewise receive a different provider output later. See the
[implementation evidence, fixtures, comparison and 17 acceptance gates](../docs/PATCH_D2_RETRY_NEGATIVE_CACHE.md).
