# Patch D3 — document-scoped recovery window

Implemented 2026-09-13 from the user's final trimmed D3 patch.
This is the current cache-trust report; the D2 follow-up report and its JSON
are historical evidence. The user approved interpreting concurrency fixture 5
as twenty audited test-only writes while preserving empty pending state after
promotion. This resolves the original conflict between a final pending count
of 20 and mandatory removal at count 2.

**D3 acceptance: PASS on all 21 scoped code/fixture criteria.**
**Criterion 16a: PASS. Known one-strike counterexample: FIXED.**
**General Criterion 16b: NOT_ESTABLISHED. Real PDF benchmark: NOT_RUN.**

No real PDF was opened, translated, extracted, rendered or generated. No live
provider was contacted. Checks use fake Google responses, injected sleeps,
isolated memory caches, strings, JSONL, ledger and geometry fixtures. No commit,
push, production-cache change or user-output overwrite was performed.

## Production change and exact call sites

Production edits are limited to `pdf2zh/converter.py`:

| Site | Change / invariant |
| --- | --- |
| Line 74 | Ships `RECOVERY_WINDOW = 2` |
| Line 1115 | Constructor initializes `identity_failure_strikes: dict[str, tuple[int, str]]`; existing trusted map and lock retain their shape/document ownership |
| Lines 1164–1174 | Converter-local per-thread provider activity observer; delegates the same Google `do_translate` call and changes no provider routing, validation, retry or profile counter |
| Line 1201 | `_remember_unsafe_identity_strike` replaces the immediate one-strike write helper |
| Lines 1211–1218 | Under the existing lock, increment pending count, retain first current-window reason, promote at two and remove pending state |
| Lines 1220–1223 | `_reset_pending_identity_strikes` removes pending evidence only if the identity is not trusted |
| Lines 1239 and 1249 | Capture this thread's provider activity before the unchanged Tenacity request; qualifying terminal full-ladder quality failure registers a strike |
| Lines 1253–1255 | After normal restore and resolution, a genuine successful eligible provider translation resets pending evidence |
| Lines 1264–1276 | Negative eligibility and trusted READ remain unchanged; they consult only the trusted map and retain the test read toggle |
| Line 2016 | Existing normal occurrence consumer is unchanged; cache skips still return the ordinary unresolved result and are recorded once |

The observer is provenance for the trust reset only. It prevents a positive-cache
hit or another worker's global request counter from being mistaken for a new
provider success. Local preferred/preserve paths do not enter reset. A raw provider
return that subsequently fails validation/restoration does not reset either.
Transport failures and fit/render/geometry outcomes do not increment or reset
pending strikes. Google `do_translate` is delegated exactly once per existing
evaluation; no second retry loop or alternate provider path is added.

Trusted entries are one-way document latches. Subsequent in-flight/test-bypass
failures do not recreate pending state. Test-bypass provider success does not
delete trust. Therefore:

```text
identity in known_unsafe_identities
    => identity NOT in identity_failure_strikes
```

`pdf2zh/retry_policy.py` is unchanged in Git and was compared to HEAD. Its
pre/quality/transport limits remain 1/8/8. The constructor still creates a fresh
cache owner per `translate_patch` document run. AST comparisons also confirm
`_make_translation_request` and the entire `_translate_job` body are unchanged:
same Tenacity hooks/waits, negative-cache READ, eligibility, plan-only and test
toggle semantics, result/exception handling. Patch A/B/C, translators, renderer,
layout, validation acceptance rules and other Patch D code were not edited.

## Required fixtures 1–8

The explicit runner reports **388 tests PASS**: 12 new D3 tests, all 23 D2 tests
and 353 existing selected pure regressions. The runner excludes real/native PDF
suites and guards PDF opening and live HTTP during import and execution.

| D3 fixture | Result | Evidence |
| --- | --- | --- |
| 1: recoverable second occurrence | PASS | Calls 1–8 reject; strike `(1, TechnicalInvariantError)` without trust; call 9 succeeds; zero skips; both maps empty afterward |
| 2: two exhausted occurrences establish trust | PASS | D3 calls 1–16 reject; second occurrence promotes and clears pending; occurrence 3 skips. Control calls 17–24 are explicitly configured to reject |
| 3: success resets current window | PASS | First eight failures record `(1,A)`; provider success clears it; eight later failures start `(1,C)` without trust. Test alone bypasses positive-cache reuse to exercise that later fake failure |
| 4: per-identity strikes | PASS | A strike 1, B strike 1, A strike 2: only A trusted; B remains `(1,FirstB)` |
| 5: concurrent atomic writes | PASS, user-approved interpretation | Twenty concurrent completed test writes, same reason, no race. Test audits count 20; pending assignments are exactly counts 1 then 2; promotion leaves pending empty and trust stable |
| 6: first reason wins | PASS | First A, then B: trusted reason A; pending removed; later write C cannot overwrite or recreate pending |
| 7: existing D2 fixtures | PASS | All 23 D2 tests retained; only one-strike assumptions updated to evaluated second occurrence and eligible later skip after two failures |
| 8: bypass success after trust | PASS | Two failed ladders trust identity; forced call 17 succeeds; trust stays, pending empty; re-enabled cache skips occurrence 4 without new provider call |

Concurrency fixture 5 adds a test-only scheduling gap between pending read/update
to expose lost increments if locking is absent. It asserts all twenty distinct
calls complete, increments 1/2 are not lost and the latch prevents stale pending
recreation. It does not assert twenty production occurrences pass the cache
READ, and no counter of 20 is persisted in production cache state.

Additional new tests cover empty-reason rejection, transport failure retaining
pending evidence, local preferred-result exclusion, positive-cache-hit exclusion
and occurrence/context ineligibility. Empty, None and whitespace reasons neither
increment nor promote; existing diagnostics and ordinary unresolved handling
are preserved.

D2 assertions remain strict: first-occurrence quality/transport exhaustion still
makes eight calls with waits `[1,2,4,8,16,32,60]`; occurrence/context jobs remain
independent; fresh converter starts empty; reason and concurrent committed-hit
tests remain valid; fit failures never trust; normal script audit still detects
unexplained extra glyphs. The cache-skip ledger test now accounts for two actual
failed occurrences and one later skipped occurrence, each exactly once.

## Known one-strike repeat-recovery probe

Same D3 worker for active and read-disabled control paths, isolated cold memory
caches, plan_only=False. Calls 1–8 are quality failures; call 9 is valid. Historical
`d2_*` field names remain in the script/JSON for compatibility, but represent
current D3 behavior.

| Metric | D3 | Control, only cache READ disabled |
| --- | ---: | ---: |
| Provider calls | 9 | 9 |
| Negative-cache skips | 0 | 0 |
| First occurrence calls | 8 | 8 |
| First occurrence status | UNRESOLVED | UNRESOLVED |
| Final status | TRANSLATED | TRANSLATED |
| Trusted after first occurrence | No | No |
| Pending after first occurrence | `(1, TechnicalInvariantError)` | Same |
| Pending/trusted after recovery | Both empty | Both empty |
| Coverage / duplicate assignments / unassigned spans | 1.0 / 0 / 0 | 1.0 / 0 / 0 |

Exact identity sets on both paths:

```text
translated = {054bab82120e9740}
preserved  = {}
unresolved = {054bab82120e9740}
identity_sets_equal = true
```

The same identity belongs to unresolved for occurrence 1 and translated for
occurrence 2. Per-occurrence status/reason rows are retained in the JSON and match
between paths. The known D2 one-strike counterexample is **FIXED**; outcome
comparison is **PASS ON TESTED PROBE**, not general equivalence.

## Two-strike trust probe

Fake calls 1–24 all fail quality validation. Both paths evaluate the first two
occurrences completely. After the second, trust contains the first reason and
pending state is empty. D3 skips occurrence 3; control executes its defined
third failed ladder through calls 17–24.

| Metric | D3 | Control |
| --- | ---: | ---: |
| Provider calls | 16 | 24 |
| Negative-cache skips | 1 | 0 |
| Final status | UNRESOLVED | UNRESOLVED |
| Pending after promotion/final occurrence | Empty | Empty |
| Unresolved occurrence count | 3 | 3 |
| Coverage / duplicates / unassigned spans | 1.0 / 0 / 0 | 1.0 / 0 / 0 |

```text
translated = {}
preserved  = {}
unresolved = {054bab82120e9740}
identity_sets_equal = true
provider_calls_avoided_on_occurrence_3 = 8
TWO_STRIKE_TRUST_PROBE = PASS ON TESTED PROBE
```

This demonstrates the intended synthetic performance path with a fully defined
control. It does not manufacture a control success/divergence.

Both full probe outputs, exact sets, occurrence rows, pending/trusted snapshots
and accounting are saved in `docs/patch-d3-synthetic.json`.

## First-occurrence and fixed-workload regressions

The existing first-occurrence quality-recovery probe still passes: baseline and
D3 both reject calls 1–2, reach recovery on call 3 and accept the first occurrence
with equal identity sets. Thus Criterion 16a remains PASS.

The original fixed-response two-page synthetic workload has exact matching
occurrence identities/status/reasons, all three bucket sets and accounting versus
the unchanged legacy BEFORE JSON. Differences are empty in
`docs/patch-d3-synthetic-comparison.json`. It makes 36 fake HTTP calls on both
paths; its failing shared source appears only twice and establishes D3 trust
at the end without a third occurrence to skip. Injected retry seconds are 495
versus original 618 because the unchanged D2 pre-provider length guard stops
local retries. These are fake-clock values, not measured PDF time or a new D3
speedup. The specifically constructed two-strike probe demonstrates the skip.

## Synthetic repeat-shape expectation

For the user-supplied repeat shape `9839d40e8091ff14` ×5 and
`268ff6be825f602d` ×2, assume every evaluated occurrence exhausts eight attempts.
This table is theoretical arithmetic from those repeat counts, not a new PDF run:

| Version | Attempts over 7 occurrences | Avoided versus no cache |
| --- | ---: | ---: |
| No-cache / pre-D2 | 56 | 0 |
| Historical one-strike D2 | 16 | 40 |
| D3 two-strike | 32 | 24 |

D3 retains 24/40 = **60% of theoretical provider-attempt savings**. For the ×5
identity, three skipped full ladders have a theoretical nominal backoff ceiling
of 3×123 = 369 seconds. Provider latency, validation, queueing, concurrency and
rendering are excluded; this is not measured elapsed time saved.

## All 21 D3 acceptance criteria

PASS is the scoped code/fixture gate described here. Native PDF acceptance is
excluded from this no-PDF run and is not claimed passing.

| # | Criterion | Result |
| --- | --- | --- |
| 1 | retry_policy.py unchanged | PASS: Git/content comparison |
| 2 | First-occurrence retry unchanged | PASS: same Tenacity AST, full-ladder and attempt-3 recovery tests |
| 3 | One exhaustion creates only strike 1 | PASS: fixture 1 |
| 4 | Repeat-recovery probe reaches call 9 | PASS: both paths 9 |
| 5 | Both repeat paths TRANSLATED | PASS |
| 6 | Repeat sets equal | PASS ON TESTED PROBE |
| 7 | Successful recovery clears pending | PASS: genuine provider success; local/cache hits excluded |
| 8 | Two independent exhausted occurrences trust | PASS: fixture 2 |
| 9 | Later trusted occurrence skips | PASS: occurrence 3 skips, no new call |
| 10 | Per-identity strikes | PASS: fixture 4 |
| 11 | Atomic concurrent updates, no lost increments | PASS: user-approved fixture 5 interpretation; audit count 20, increments 1/2, no stale pending |
| 12 | First reason wins | PASS: fixture 6 |
| 13 | Empty reasons never recorded | PASS: D2 and D3 rejection fixtures |
| 14 | Eligibility restrictions unchanged | PASS: entire `_translate_job` AST unchanged and ineligible-job tests |
| 15 | Existing D2 fixtures retained/pass | PASS: all 23, only one-strike assumptions adjusted |
| 16 | Patch A/B/C unchanged/pass | PASS: unchanged code/tests and selected pure regression suites |
| 17 | Two-strike probe equal outcome sets | PASS ON TESTED PROBE: defined calls 17–24 fail |
| 18 | No finite-window general-equivalence claim | PASS: general Criterion 16b NOT_ESTABLISHED |
| 19 | Performance evidence measured or theoretical label | PASS: fake HTTP counts versus explicit theoretical repeat/backoff table; no PDF speedup |
| 20 | Shipped RECOVERY_WINDOW=2 | PASS: constant and fixture assertion |
| 21 | Promotion clears pending; bypass success retains latch | PASS: fixtures 2, 6, 8 and trusted-write guard |

## General equivalence and remaining risks

```text
Criterion 16a — first-occurrence retry equivalence: PASS
Known one-strike negative-cache counterexample: FIXED
Repeat-recovery probe: PASS ON TESTED PROBE
Two-strike trust probe: PASS ON TESTED PROBE
Real-corpus outcome comparison: NOT_RUN
Real-corpus performance delta: NOT_MEASURED
Criterion 16b — general full outcome equivalence: NOT_ESTABLISHED
```

A nondeterministic provider can still recover after two exhausted occurrences.
Fixture 8 demonstrates call 17 succeeding through the read-disabled path while
the trusted entry remains latched; normal later reads still skip. D3 deliberately
reduces the known one-strike risk without proving recovery can never occur after
trust. Already in-flight parallel jobs may pass the READ before promotion; no
single-flight scheduler or concurrency refactor is introduced. Validators still
have their existing semantic coverage limits. Real timing/layout and corpus
equivalence remain unmeasured.

Any real benchmark requires separate authorization: same PDF bytes, page range,
Google engine, threads, terminology/configuration and isolated cache conditions,
comparing D3 window 2 to the same worker with cache READ disabled. Report exact
sets/per-occurrence outcomes and real timing separately from synthetic results.

## Checks and exact files changed

388 selected tests, five changed/new Python compile checks, repository Ruff hard
errors, full Ruff on probe/runner/test files, converter import ordering, pip check,
skill validator and git diff --check: PASS. No full PDF-native discovery or render
QA was run. Earlier GUI smoke evidence is historical and is not a new D3 result.

| Exact file | Change |
| --- | --- |
| `pdf2zh/converter.py` | Recovery window, pending strike write/promotion/reset and provider-success provenance; READ unchanged |
| `tests/test_patch_d3_recovery.py` | 12 new recovery/latch/concurrency/boundary tests |
| `tests/test_patch_d2_retry.py` | Preserve 23 D2 tests, update one-strike assumptions |
| `scripts/run_patch_d2_no_pdf_checks.py` | Select the new D3 module under existing guards |
| `scripts/benchmark_patch_d2_synthetic.py` | Current recovery and defined two-strike control probes, pending/trusted snapshots |
| `docs/patch-d3-synthetic.json` | Current synthetic fixed/recovery/trust results |
| `docs/patch-d3-synthetic-comparison.json` | Exact fixed-workload baseline outcome/set/accounting comparison |
| `docs/PATCH_D3_RECOVERY_WINDOW.md` | Current report and 21 acceptance criteria |
| `docs/PATCH_D2_RETRY_NEGATIVE_CACHE.md` | Historical-scope notice linking current D3 report |
| `SKILL.md` | Current two-occurrence trust workflow and finite-window limit |
| `references/performance-profiling.md` | Current D2 retry/D3 cache, probe workflow and theoretical expectations |
| `agent-knowledge/pdf-engine.md` | Current strike/trust/reset/latch invariants |
| `agent-knowledge/validation.md` | Current test selection, probes and Criterion 16 scope |

Retry policy, A/B/C production/test files, original D2 baseline/results/comparison,
translator and renderer remain unchanged. Changes are uncommitted for review.

Reproduce with the skill's Python 3.12 virtual environment and absolute paths:

```text
<python> <skill-root>/scripts/run_patch_d2_no_pdf_checks.py
<python> <skill-root>/scripts/benchmark_patch_d2_synthetic.py --output <separate-d3.json> --compare-before <skill-root>/docs/patch-d2-synthetic-before.json --comparison-output <separate-comparison.json> --quality-recovery-probe --negative-cache-repeat-recovery-probe --two-strike-trust-probe
```
