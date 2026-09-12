# Patch D2 v2 implementation and acceptance evidence

Implemented on 2026-09-13 against the user's `Patch_D2_v2_Ship_Ready.md`.
The user explicitly requested skill changes **without PDF translation**.
This run opened, extracted, rendered and generated no PDFs and contacted no
live translation provider. Tests used strings, JSONL, a synthetic ledger,
mock Google responses, geometry functions and font probes.

**Implementation complete; full patch acceptance is FAIL / NOT ESTABLISHED.**
The specified 1/2/8 retry policy and document negative cache are installed.
375 selected regressions pass and the fixed-response workload preserves exact
outcomes and identity sets. Real benchmark gates are `NOT_RUN`. A separate
offline counterexample disproves unconditional outcome equivalence when a
provider quality rejection recovers on attempt 3. This report does not label
the patch ship-ready or claim a measured PDF speedup.

## Files changed

| File | Change |
| --- | --- |
| `pdf2zh/retry_policy.py` | New typed Tenacity stop policy, 1/2/8 constants, reason and backoff helpers |
| `pdf2zh/converter.py` | Document mapping/lock; typed retry adapter; eligible shared-job lookup/write; normal worker result reuse |
| `pdf2zh/performance.py` | Allow an additive float counter amount for the explicitly estimated backoff metric |
| `tests/test_patch_d2_retry.py` | 22 worker/policy/cache/ledger fixtures with fake HTTP and sleep |
| `scripts/run_patch_d2_no_pdf_checks.py` | Explicit selection of 375 regressions under PDF/HTTP guards |
| `scripts/benchmark_patch_d2_synthetic.py` | Controlled baseline replay, actual D2 worker, exact comparison and recovery probe |
| `docs/patch-d2-synthetic-before.json` | Baseline captured before production edits, with converter source hash |
| `docs/patch-d2-synthetic-after.json` | D2 controlled result and separate nondeterministic recovery probe |
| `docs/patch-d2-synthetic-comparison.json` | Exact controlled outcome/set/accounting comparison and empty set differences |
| `docs/PATCH_D2_RETRY_NEGATIVE_CACHE.md` | This implementation/acceptance report |
| `SKILL.md` | D2 routing and concise retry/acceptance scope |
| `references/performance-profiling.md` | Maintained D2 policy, instrumentation and no-PDF verification workflow |
| `agent-knowledge/pdf-engine.md` | Document cache ownership and engine invariants |
| `agent-knowledge/validation.md` | Selected no-PDF D2 checks and acceptance limits |

No commit, push or user-output overwrite was performed. Existing regression
assertions were not edited or weakened. Production edits are isolated to the
retry policy, converter adapter and additive profiling counter typing.

## Mandatory inspect-before-change evidence

These locations were inspected before production edits. Unchanged files retain
their line numbers; converter BEFORE and AFTER locations are distinguished.

### Source-length error

`pdf2zh/translator.py:216` defines `SegmentTooLongError`.
A repository raise-site search found one production raise:
`pdf2zh/translator.py:465`, inside `GoogleTranslator.do_translate`, after
`len(text) > MAXIMUM_SEGMENT_CHARACTERS` (5,000). It precedes profiling of a
provider request at line 469 and `session.get` at line 470.

Classification is **PRE_PROVIDER_DETERMINISTIC**. There is no post-provider
raise requiring a new subclass. Keep the existing exception name; evaluate
once, send zero HTTP requests, sleep zero times and never negative-cache it.
The legacy request counter may include a local evaluation; use physical
`google_http_requests` to assess HTTP work.

`BaseTranslator.translate` at `pdf2zh/translator.py:334` calls `do_translate`
at line 352, restores protected literals at line 353 and validates at line 359.
The typed quality exceptions originate in output restoration/validation.
Rejected positive-cache values are caught by the existing local cache lookup
at line 318 and become misses; they do not themselves populate the D2 map.

### Converter lifecycle

`pdf2zh/high_level.py:440` defines `translate_patch`; line 481 constructs its
local `device = TranslateConverter(...)`. `translate_stream` at line 1116
calls that function at line 1176. The per-file loop calls `translate_stream`
at line 1380. There is no converter parameter or reuse across independent
production document runs. Consequently each constructor initializes an empty
mapping and lock (`pdf2zh/converter.py:1112` AFTER).

Fixture 5b is **N/A**: current production creates a fresh converter per document.
No artificial multi-document reuse path was added.

### Patch B shape and consumption

`pdf2zh/integrity.py:151` already carries `Occurrence.unresolved_reason`.
There is no `unresolved_kind` field, and no new field is required.
`record_occurrence` at line 445 rejects duplicate occurrence IDs/unknown spans
and updates assignments once. `reconcile` at line 490 computes unassigned spans
at line 501, duplicate assignments at line 502 and unresolved count at line 508.
The existing `accounting_coverage` property is at line 180.

The converter's result-consumption block was at approximately lines 1907–1968
BEFORE D2; its `record_occurrence` call is at line 1978 AFTER. It maps the normal
worker status to `OccurrenceStatus.UNRESOLVED` and retains the reason.
D2 returns `(source, "unresolved", first_reason, ())` into that same block;
it never mutates the ledger from a worker/cache branch.

`audit_final_text_layer` at `pdf2zh/integrity.py:537` handles ordinary unresolved
source fallback in its existing allowed-status branch at line 567. D2 does not
modify or bypass this audit. Fixture 9 confirms equivalent expected Korean
fallback is allowed, while one unexplained extra Korean glyph is still rejected.

## Retry and cache implementation

`pdf2zh/retry_policy.py:11` defines quality=2, pre-provider=1 and transport=8.
The typed quality tuple includes verified proper-name, technical invariant,
formula placeholder, translation-integrity and confirmed-terminology errors.
`SegmentTooLongError` belongs only to the pre-provider tuple. Other errors keep
the legacy transport budget; message strings never determine classification.

`pdf2zh/converter.py:1158` builds the existing Tenacity request with the same
exponential waits, `before=self.profile.retry_attempt`,
`sleep=self.profile.sleep`, the existing page retry-identity callback and
`reraise=True`. There is no second production retry loop. The stop adapter
counts an earlier quality cap only if it actually stops before attempt 8.

The policy follows the patch's example: classify the current terminal exception
and compare **total attempt number**, rather than count separate per-class
attempts. A mixed transport/quality sequence therefore does not promise two
additional quality attempts after transport failures. A quality failure on
attempt 8 is not counted as a cap saving attempts.

The existing inner request/segment/worker body was exposed as small private
methods so tests call the actual production worker. The page adapter passes the
existing job key as a fourth value. Job construction, identities, pool capacity,
executor ordering, result fan-out and occurrence consumption retain their
existing definitions. AUTO local/plan behavior and unresolved recording remain
the same worker branches.

`_translate_segment` at line 1191 catches only terminal typed exceptions from
the Tenacity provider request. Eligible quality failures write through
`_remember_unsafe_identity` at line 1183. Local preferred validation and later
formula restoration are outside that catch. Fit/render code remains outside it.

`_translate_job` at line 1221 reads the map only for an existing shared Google
job with no context and no context-sensitive Korean. A single lock scope
retrieves the reason. Provider calls, validation, sleeps and ledger work occur
outside the lock. Writes reject `None`, empty and whitespace reasons, log/count
the diagnostic and preserve normal unresolved handling. Valid reasons use
atomic `setdefault`, preserving the first recorded exception-class reason.

No map read/write applies to occurrence jobs, context-bearing jobs, Handoff,
AUTO, plan-only execution, transport/internal errors, cancellations, length
checks or fit/render/geometry errors. The map is memory-only and document-owned.
Concurrent jobs already in flight may still complete; the lock is not a new
provider single-flight scheduler. Reads after an unsafe entry is committed skip
the request consistently.

New profile workload counters are additive:

- `content_quality_retry_attempts_capped`: jobs stopped before the legacy limit
  by a terminal quality failure; first-attempt success/transport are excluded.
- `pre_provider_deterministic_retries_suppressed`: one per terminal length-check
  job whose redundant local retries are removed.
- `negative_cache_skips`: later qualifying shared jobs skipped.
- `negative_cache_estimated_retry_backoff_seconds_saved`: **1 second per skip**
  for the two-attempt budget. This is nominal avoided retry backoff only.
- `negative_cache_invalid_reason_rejected`: diagnostic for an invalid reason.

Existing Patch D timers and fields retain their meanings. The estimated counter
excludes provider latency, validation, scheduling, queues and rendering.

## Required fixtures and regressions

| Required fixture | Result | Evidence |
| --- | --- | --- |
| 1: deterministic output rejection | PASS | Actual Google worker: 2 calls, sleep `[1]`, unresolved technical error, capped=1 |
| 2: repeated shared occurrence | PASS | Second synthetic page job: zero new calls/sleeps, same first reason, skip=1 |
| 3: transport recovery | PASS | Two ConnectionErrors then success on call 3; full eight-failure ladder also verified |
| 4: independent occurrence/context jobs | PASS | Two jobs each pay their own ladder; map stays empty |
| 5a: fresh converter isolation | PASS | Converter B starts empty and pays its own two-attempt failure |
| 5b: production converter reuse | N/A | Fresh-converter lifecycle evidence above |
| 6: first reason | PASS | Later write cannot replace first reason; cache hit returns that reason |
| 7: concurrency | PASS | Concurrent atomic writes and 20 committed-entry reads; stable reason, no HTTP on hits |
| 8: fit exclusion | PASS, geometry fixture | Narrow fit fails, roomier fit succeeds through normal positive-cache path; no unsafe entry or skip; actual PDF rendering NOT_RUN |
| 9: Patch B accounting/audit | PASS, ledger fixture | Two unresolved occurrences, coverage 1.0, duplicates/unassigned 0; expected fallback accepted, extra glyph detected |

Additional D2 checks cover zero-HTTP length rejection, invalid reason diagnostics,
local preferred-validation exclusion, post-request restore exclusion, typed
classification despite misleading message text, ordinary success, cancellation,
Handoff missing-unit handling, plan-only behavior and no false cap on attempt 8.

`scripts/run_patch_d2_no_pdf_checks.py` ran **375 tests: PASS**, including all
22 D2 tests and full pure suites for Patch A/B/C/D, Google/Handoff, preservation,
technical invariants, proper names, terminology boundaries and Korean regressions.
It also ran formula/orientation/style/fit helpers, selected targeted correctness,
pure layout QA and duplicate/metadata fixtures. The exact selection is saved in
the runner. PDF-dependent CLI, native delivery/render and real-document suites
were excluded, not marked passing. Existing tests were not changed.

Other checks: Python 3.12 virtual environment; compile checks; `pip check`;
repository Ruff hard errors (`E9,F63,F7,F82`); full Ruff on the four new Python
files; `git diff --check`; skill frontmatter validator. All passed. GUI
`--smoke-test` also passed under PDF/HTTP guards; it loads the existing native
stack/model and does not exercise a translation.

## Controlled BEFORE / AFTER

The baseline JSON was captured before production edits. The legacy replay uses
the original eight-attempt Tenacity policy; AFTER calls the actual D2 worker.
Both use identical two-page string occurrences, Google configuration, four
configured workers, fake HTTP outputs and cold isolated in-memory caches.
The benchmark executes its strings sequentially; it is not a concurrent PDF
critical-path measurement. A fake clock advances 0.02 seconds per HTTP call
and the nominal retry sleeps. The same fixed errors reject on every attempt.

| Metric | BEFORE | AFTER |
| --- | ---: | ---: |
| Physical Google HTTP calls | 36 | 10 |
| Legacy provider-request counter, including local length evaluation | 44 | 11 |
| Injected provider wait seconds | 0.72 | 0.20 |
| Injected retry seconds | 618 | 6 |
| Retry requests / sleeps | 37 | 5 |
| Injected total seconds | 618.72 | 6.20 |
| Quality jobs capped | N/A | 3 |
| Pre-provider retries suppressed | N/A | 1 |
| Negative-cache skips | N/A | 1 |
| Estimated backoff avoided by negative-cache skip only | N/A | 1 second |
| Translated / allowed preserve / unresolved occurrences | 3 / 2 / 5 | 3 / 2 / 5 |
| Accounting coverage | 1.0 | 1.0 |
| Duplicate assignments / unassigned spans | 0 / 0 | 0 / 0 |

These injected seconds are **not real wall-clock savings or a PDF speedup**.
The local elapsed test interval is saved separately and is not a translation
benchmark. Provider time and layout/render measurements on a real PDF are absent.

The comparison JSON asserts equality of all occurrence identity/status/reason
rows, exact bucket identity sets and coverage/accounting. Every set's added and
removed lists is empty. Unique identity sets BEFORE = AFTER:

| Bucket | Exact unique IDs |
| --- | --- |
| translated | `0498eb860a31be4a`, `e67164f06704e82a` |
| preserved | `62467b3af8813fe1`, `f03d273f811cb088` |
| unresolved | `054bab82120e9740`, `383c3ebaaebf44c9`, `70f3bde3b194f961`, `f4caad59c792ed10` |

Counts and unique sets differ because shared/independent occurrences are tracked
separately. Negative-cache hits return the patch-specified empty failure-code
tuple; the first provider failure's diagnostic codes are not revalidated or
duplicated on the skip. Ordinary reason/status/coverage are preserved; universal
equality of all diagnostic failure counters is not claimed.

## Outcome-equivalence counterexample

`--quality-recovery-probe` changes only the fake provider's failed source output:
calls 1–2 swap register values, but call 3 returns the correct values. Both
policies still run all existing validators and use isolated cold caches.

The legacy policy accepts identity `054bab82120e9740` on attempt 3 and reuses its
valid positive entry later. D2 stops after attempt 2, then negatively caches its
unresolved reason. Thus the probe changes occurrence counts from 5/2/3 to 3/2/5
and moves that exact identity from translated to unresolved. Coverage stays 1.0
and duplicate/unassigned counts remain zero. The full probe is saved under
`quality_recovery_probe` in the AFTER JSON with
`UNIVERSAL_OUTCOME_EQUIVALENCE_COUNTEREXAMPLE` and `identity_sets_equal=false`.

This demonstrates a limitation of the specified policy, not a validator bypass.
Identical input and a deterministic validator do not guarantee identical provider
output. Unconditional outcome preservation requires additional evidence or a
different approved policy; neither is supplied by a two-attempt cap alone.

## Real benchmark and remaining bottleneck

`bcnp59991060b.pdf` / `sh080811engy.pdf` controlled real BEFORE/AFTER:
**NOT_RUN**, honoring the user's no-PDF request. No existing completed log was
presented as a new controlled baseline. Real retry improvement, total elapsed
improvement, final identity/reason equivalence and native PDF layout acceptance
are therefore **NOT_MEASURED / NOT_RUN**.

In the injected workload only, remaining retry backoff is 6/6.2 seconds
(about 96.8%), mostly the retained recoverable transport ladder plus the three
required quality waits. Remaining real provider/local/layout/render bottlenecks
are NOT_MEASURED. No follow-up optimization is inferred from this synthetic
percentage.

## All 17 acceptance criteria

PASS below means the stated code/fixture gate, not full real-document acceptance.

| # | Criterion | Result |
| --- | --- | --- |
| 1 | Inspect-before-change evidence | PASS: raise origin, lifecycle and Patch B shape recorded |
| 2 | Quality budget at most 2 by default | PASS: typed policy and persistent-quality worker fixture; total-attempt semantics for mixed failures |
| 3 | Suppress pre-provider redundant retry | PASS: 1 local evaluation, no HTTP/sleep |
| 4 | Transport retains 8 and exponential waits | PASS: recovery and full exhaustion fixtures |
| 5 | Qualifying later shared failure can skip | PASS: Google shared provider path fixture |
| 6 | No occurrence/context suppression | PASS: exclusions and independent-job fixtures |
| 7 | No fit/render/layout negative entries | PASS: provider-only write boundary and geometry fixture; native PDF render NOT_RUN |
| 8 | Document cache isolation | PASS: fresh-converter production lifecycle and fixture 5a; 5b N/A |
| 9 | First reason, no empty entries | PASS: setdefault and invalid-reason fixtures |
| 10 | Concurrent consistent access | PASS: atomic read/write fixtures |
| 11 | Patch B exact-once coverage | PASS: ledger fixture and unchanged result consumer |
| 12 | Final audit not bypassed | PASS: unchanged audit call path and pure audit fixture; native PDF audit NOT_RUN |
| 13 | Existing A/B/C/D regression suites | PASS: selected pure suites; full PDF-dependent discovery NOT_RUN |
| 14 | Additive, correctly labeled counters | PASS: profile fixtures and comparison |
| 15 | Real benchmark retry seconds decrease | NOT_RUN |
| 16 | Real benchmark exact outcome identity mapping | NOT_RUN; unconditional equivalence additionally FAILS the offline recovery probe |
| 17 | No unsupported wall-clock claim | PASS: injected/estimated metrics explicitly separated |

Overall: **do not mark PATCH_D2_PASS / ship-ready**. The requested implementation
and permitted checks are complete; real gates are missing and the universal
outcome claim has a concrete counterexample.

Patch A reconstruction, Patch B validator/acceptance/accounting/audit rules and
Patch C `ENGINES`/AUTO routing were not edited. Existing controlled fixtures pass.
This statement does not imply unconditional equality of final provider outcomes.

Rollback has no commit ID yet. When an authorized commit is created, keep the
listed D2 edits isolated so a later `git revert <D2-commit>` restores the prior
retry/cache behavior without touching A/B/C. No rollback or destructive worktree
reset was performed during this task.
