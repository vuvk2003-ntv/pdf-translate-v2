# Patch D2 follow-up: restored quality retry budget and residual cache risk

**Historical D2 follow-up report.** Superseded for current cache trust behavior
by [Patch D3 recovery-window report](PATCH_D3_RECOVERY_WINDOW.md). The D2 probe
results below and their JSON are retained as historical evidence. Current D3
fixes that one-strike probe; general Criterion 16b remains NOT_ESTABLISHED.

Current implementation: 2026-09-13, following the user's pasted
`Patch D2 follow-up — de-risk CONTENT_QUALITY_MAX_ATTEMPTS` request.
This document supersedes the earlier D2 v2 acceptance report. The original
2-attempt quality budget and its attempt-3 counterexample are historical;
the current budget is 8. No separate current D2 specification/acceptance table
exists in the repository. The user's attached original specification is not
edited. Maintained skill and profiling references reflect this follow-up.

**First-occurrence recovery passes; full D2 outcome equivalence remains
NOT_ESTABLISHED. Offline negative-cache equivalence FAILS.**

No real PDF was opened, translated, extracted, rendered or generated. No live
provider was called. Checks use strings, JSONL, in-memory caches, ledger/geometry
fixtures, injected sleeps and fake Google HTTP responses. Real-PDF comparison
and timing/layout gates are NOT_RUN. No commit, push or output overwrite was
performed.

## Exact follow-up code scope

`pdf2zh/retry_policy.py` now defines:

```python
PRE_PROVIDER_MAX_ATTEMPTS = 1
TRANSPORT_MAX_ATTEMPTS = 8
CONTENT_QUALITY_MAX_ATTEMPTS = TRANSPORT_MAX_ATTEMPTS
```

The alias is declared after the transport constant so it is initialized safely.
Both existing pre-provider/transport values, both exception tuples, typed
classification, `_stop_by_failure_class`, wait curve, Patch D hooks and all
other production behavior are unchanged.

Content-quality FIRST occurrences now receive the legacy eight-attempt window:
waits 1, 2, 4, 8, 16, 32, 60 (123 seconds nominal backoff on full exhaustion).
The earlier quality-cap counter remains additive but records zero capped jobs
with this restored budget. The unchanged estimate helper now yields 123 seconds
of nominal backoff avoided per negative-cache skip. This is **not total
wall-clock time saved**.

Part 2 retains its existing default production behavior. The only converter
addition is the explicitly requested test control:

```python
self._disable_negative_cache_for_tests: bool = False
```

The flag gates only the negative-cache READ at `pdf2zh/converter.py:1233`.
Default False preserves normal cache use. No CLI option exposes that flag.
Cache writes, locking, first reason, validation, provider execution, job keys,
ledger results, positive cache and rendering are unchanged. The repeat probe
sets the flag internally for its control; it does not use plan-only execution.

Patch A reconstruction, Patch B validators/accounting/final audit, Patch C
routing/ENGINES/AUTO, and other Patch D behavior were not edited. No Part 2
mitigation is introduced despite the residual counterexample.

## Preserved inspect-before-change evidence

The original D2 inspection remains applicable:

- `pdf2zh/translator.py:216` defines SegmentTooLongError; its sole production
  raise at line 465 is Google's >5,000-character source guard before provider
  request profiling at line 469 and `session.get` at line 470. It remains
  PRE_PROVIDER_DETERMINISTIC: one local evaluation, no HTTP or sleep, no unsafe
  cache entry. There is no post-provider length variant to split.
- `pdf2zh/high_level.py:481` creates a local TranslateConverter for each
  `translate_patch` document call; `translate_stream` calls it at line 1176 and
  the per-file loop calls the stream function at line 1380. Each fresh converter
  initializes its map and lock at `pdf2zh/converter.py:1112`. Fixture 5b is N/A;
  no artificial converter reuse path was created.
- `Occurrence.unresolved_reason` exists at `pdf2zh/integrity.py:151`;
  `unresolved_kind` does not exist and is not needed. `record_occurrence` at line
  445 records assignments once. `reconcile` at line 490 computes unassigned,
  duplicate and outcome counts; accounting coverage is at line 180.
  Cache hits return the ordinary unresolved tuple into the existing converter
  consumer (`record_occurrence` now line 1980), without a worker ledger write.
- `audit_final_text_layer` at `pdf2zh/integrity.py:537` still handles expected
  unresolved source fallback at line 567. It is unchanged and remains mandatory.
  The pure audit fixture still detects one unexplained additional Korean glyph.

## First-occurrence quality recovery probe

Re-ran the existing `--quality-recovery-probe` with identical isolated cold
inputs. The fake provider returns invalid swapped register values on calls 1–2
and correct values on call 3. The baseline uses the original eight-attempt
policy; D2 uses its actual worker with the restored budget. Both FIRST
occurrences reach call 3 and are TRANSLATED. Later valid positive-cache reuse
also remains normal.

```text
QUALITY_RECOVERY_PROBE: PASS
attempts_baseline: 3
attempts_d2: 3
first_status_baseline: TRANSLATED
first_status_d2: TRANSLATED
identity_sets_equal: true
CRITERION_16a: PASS
```

The full before/after rows, exact sets and physical call counts are saved under
`quality_recovery_probe` in `patch-d2-synthetic-after.json`. This resolves the
specific first-occurrence retry-cap counterexample. It does not establish
Part 2 or entire-patch equivalence.

## New repeat-occurrence probe: only cache READ differs

`run_negative_cache_repeat_recovery_probe` in
`scripts/benchmark_patch_d2_synthetic.py` creates two independent fresh Google
converters with isolated memory caches. Both execute the same `_translate_job`
and `_make_translation_request` methods with the same shared source/identity.
Only the control's test read flag is True. Both profiles have plan_only=False.
Fake provider calls 1–8 fail TechnicalInvariantError; call 9 returns valid
values. Both first occurrences make eight calls, remain unresolved and write
the identity and first reason to their negative cache, proving writes stay active
in the control.

On occurrence #2, D2 reads the committed unsafe identity and skips. The control
bypasses only that read, reaches the ninth provider call and succeeds.

```text
NEGATIVE_CACHE_REPEAT_RECOVERY_PROBE: FAIL
 d2_provider_call_count: 8
 d2_negative_cache_skips: 1
 d2_status: UNRESOLVED
 control_provider_call_count: 9
 control_negative_cache_skips: 0
 control_status: TRANSLATED
 identity_sets_equal: false
OFFLINE_NEGATIVE_CACHE_EQUIVALENCE: FAIL
CRITERION_16b: NOT_ESTABLISHED
```

Identity `054bab82120e9740` belongs only to unresolved in D2. In the control it
belongs to unresolved for the first occurrence AND translated for the second.
The probe also preserves exact per-occurrence rows, so these distinct outcomes
are visible independently of deduplicated identity sets. Both paths have coverage
1.0, duplicate assignments 0 and unassigned spans 0. D2 has two unresolved
occurrences; control has one unresolved and one translated.

The probe's assertions PASS because they reproduce the specified counterexample;
its **equivalence verdict is FAIL**, not PASS. Results, cache-write verification,
control flag, plan-only status and accounting are saved under
`negative_cache_repeat_recovery_probe` in the AFTER JSON. Part 2 remains active
and is not changed in response to this finding.

## Fixtures 1–9 and selected regressions

Fixture 1 was updated, retained and strengthened to require the full legacy
call/wait ladder. Only expectations intentionally affected by restoring the
budget were changed; semantic assertions and existing regressions were retained.

| Fixture | Result | Current verified behavior |
| --- | --- | --- |
| 1: persistent output rejection | PASS | Exactly 8 HTTP calls, waits [1,2,4,8,16,32,60], 7 retry requests, UNRESOLVED, quality capped=0 |
| 2: later safe shared failure | PASS | First job exhausts 8; repeat adds no call/sleep, first reason reused, skip=1, nominal estimated backoff=123 seconds |
| 3: transient transport | PASS | Two ConnectionErrors then success on call 3; full eight-call exhaustion also passes |
| 4: occurrence/context independence | PASS | Two independent jobs make 16 calls; no unsafe entry or suppression |
| 5a: fresh converter isolation | PASS | New converter starts empty and pays its own full 8 calls and 123 nominal seconds |
| 5b: reusable converter integration | N/A | Production lifecycle guarantees a fresh converter per document |
| 6: first reason preserved | PASS | Atomic setdefault retains the original reason; cache hit returns it |
| 7: concurrency | PASS | Concurrent writes and 20 committed-entry reads stay consistent; no HTTP on hits |
| 8: fit exclusion | PASS, geometry fixture | Narrow fit fails; roomier normal positive-cache path fits; no negative-cache entry/skip; native PDF rendering NOT_RUN |
| 9: Patch B coverage/audit | PASS, ledger fixture | Each repeat recorded once; coverage 1.0, duplicates/unassigned 0; expected fallback accepted and unexplained glyph detected |

```text
NO_PDF_FIXTURES: PASS
scripts/run_patch_d2_no_pdf_checks.py: 376 tests PASS
D2 module: 23 tests PASS
Existing selected regressions: 353 tests PASS
```

The unchanged explicit runner selects full pure Patch A/B/C/D, Google/Handoff,
preservation, technical invariant, proper name, terminology, Korean and
formula/style/orientation/fit suites, plus selected pure targeted correctness,
layout QA and metadata/duplicate tests. PDF/HTTP guards cover import and
execution. The new D2 test asserts the exact 8-versus-9 repeat counterexample,
cache writes in both paths and normal accounting. No discovery or PDF-native
suite is represented as passing.

Compile, pip check, repository Ruff hard-error checks, full Ruff on the changed
probe/test/policy files, import ordering, skill frontmatter validation and
`git diff --check` pass. The previous D2 GUI smoke result is historical;
no unrelated GUI or native PDF work was added in this follow-up.

## Fixed-response synthetic baseline comparison

The original `patch-d2-synthetic-before.json` remains unchanged and was captured
before initial D2 production edits. AFTER and the exact comparison were regenerated
using the same fake source occurrences/outputs, Google settings, four configured
workers and isolated cold memory caches. The script executes strings sequentially,
not as a measured concurrent PDF run. Its injected clock adds 0.02 seconds per
HTTP request and nominal retry sleeps.

| Metric | Original legacy BEFORE | Current D2 AFTER |
| --- | ---: | ---: |
| Physical HTTP calls | 36 | 28 |
| Legacy request counter including local length checks | 44 | 29 |
| Injected provider wait seconds | 0.72 | 0.56 |
| Injected retry seconds | 618 | 372 |
| Retry requests / sleeps | 37 | 23 |
| Injected total seconds | 618.72 | 372.56 |
| Quality jobs capped | N/A | 0 |
| Pre-provider retry suppression | N/A | 1 |
| Negative-cache skips | N/A | 1 |
| Estimated negative-cache retry backoff avoided | N/A | 123 seconds |
| Translated / preserved / unresolved occurrences | 3 / 2 / 5 | 3 / 2 / 5 |
| Coverage / duplicate assignments / unassigned spans | 1.0 / 0 / 0 | 1.0 / 0 / 0 |

Exact fixed-response occurrence identity/status/reason rows, bucket sets and
accounting match. `patch-d2-synthetic-comparison.json` still has all three equality
checks true and no added/removed identities; regeneration produces the same
comparison content. These controlled successes do not override the separate
repeat-recovery counterexample. Diagnostic failure-code counts are not claimed
universally identical: cache skips retain the patch-specified empty code tuple.

Synthetic retry/provider values are injected, not real PDF time or measured
wall-clock savings. Remaining actual provider/layout/render bottlenecks are
NOT_MEASURED. Real PDF benchmark and final layout acceptance are NOT_RUN.

## All 17 acceptance checks: follow-up interpretation

The restored budget supersedes the original criterion 2 cap expectation. Other
original guards remain in force. PASS below is the stated scoped code/fixture
evidence, not a real-PDF acceptance claim.

| # | Criterion | Current result |
| --- | --- | --- |
| 1 | Inspect-before-change origin/lifecycle/B evidence | PASS: retained evidence above |
| 2 | Content-quality budget | PASS for follow-up: full 8-attempt legacy window; original 2-attempt requirement superseded |
| 3 | No redundant pre-provider retries | PASS: one local check, zero HTTP/sleep |
| 4 | Transport 8/exponential waits | PASS: recovery and exhaustion fixtures |
| 5 | Shared negative skip only after qualifying failure | PASS: first shared quality failure exhausts 8 and writes; later skip uses normal default |
| 6 | No occurrence/context suppression | PASS |
| 7 | No fit/render/layout negative caching | PASS: provider-only write boundary and geometry fixture; native PDF rendering NOT_RUN |
| 8 | Document cache isolation | PASS: fresh-converter fixture/lifecycle; 5b N/A |
| 9 | Nonempty first reason preserved | PASS: setdefault and empty-reason diagnostics |
| 10 | Concurrent consistent reads/writes | PASS |
| 11 | Patch B exact-once accounting | PASS: ordinary ledger path and repeat probe coverage |
| 12 | Final audit not bypassed | PASS: unchanged code and pure script-audit fixture; native PDF audit NOT_RUN |
| 13 | Existing A/B/C/D regressions | PASS: selected pure suites; full PDF-native discovery NOT_RUN |
| 14 | Additive correctly labeled counters | PASS: cap=0; unchanged estimate helper yields nominal 123 seconds per skip |
| 15 | Real benchmark retry seconds decrease | NOT_RUN |
| 16 | Outcome equivalence, split below | 16a PASS; 16b NOT_ESTABLISHED; offline negative-cache equivalence FAIL |
| 17 | No unsupported wall-clock speedup claim | PASS |

**Criterion 16a — First-occurrence retry equivalence: PASS.** The budget is
restored and the first-occurrence attempt-3 probe reaches recovery with equal
identity sets.

**Criterion 16b — Full Patch D2 outcome equivalence: NOT_ESTABLISHED.** This
cannot pass based on item 1 or any synthetic result. The same real PDF must be
run with Part 2 enabled and disabled under identical bytes, page range, engine,
threads, terminology and cache conditions, comparing translated/preserved/
unresolved identity sets and per-occurrence outcomes. That comparison has not
been authorized or run. The offline repeat probe additionally establishes
**OFFLINE NEGATIVE-CACHE EQUIVALENCE: FAIL** for its constructed scenario.

Overall full D2 acceptance is not PASS/ship-ready. The requested follow-up is
implemented and its permitted re-verification is complete; remaining negative
cache outcome risk is reported without modifying Part 2.

## Exact files changed in this follow-up

| File | Follow-up change |
| --- | --- |
| `pdf2zh/retry_policy.py` | One quality-budget alias; pre/transport constants and policy body unchanged |
| `pdf2zh/converter.py` | Default-False test-only flag and cache READ gate only |
| `tests/test_patch_d2_retry.py` | Full-budget expectation updates; one repeat-recovery assertion fixture |
| `scripts/benchmark_patch_d2_synthetic.py` | Correct first-occurrence probe verdict/counts and same-worker repeat/control probe |
| `docs/patch-d2-synthetic-after.json` | Regenerated current results and both probes |
| `docs/PATCH_D2_RETRY_NEGATIVE_CACHE.md` | Superseding current report and criterion 16a/16b split |
| `SKILL.md` | Current eight-attempt quality policy and separate later-cache risk |
| `references/performance-profiling.md` | Current budget/counters, both probes and distinct acceptance levels |
| `agent-knowledge/pdf-engine.md` | Current 1/8/8 scope and distinct probe findings |
| `agent-knowledge/validation.md` | Current test count and probe/criterion workflow |

The explicit runner, BEFORE JSON and exact comparison JSON are unchanged in Git.
No Patch A/B/C, translator, renderer, other Patch D code or production cache was
changed. Changes remain uncommitted for review.

## Reproduce the permitted checks

Use the skill's Python 3.12 virtual environment and absolute paths:

```text
<python> <skill-root>/scripts/run_patch_d2_no_pdf_checks.py
<python> <skill-root>/scripts/benchmark_patch_d2_synthetic.py --output <separate-after.json> --compare-before <skill-root>/docs/patch-d2-synthetic-before.json --comparison-output <separate-comparison.json> --quality-recovery-probe --negative-cache-repeat-recovery-probe
```

The benchmark returns success when the fixed comparison and first-recovery
assertions pass and the requested repeat counterexample is correctly reproduced;
its output still records the repeat-equivalence verdict as FAIL. It does not
translate or process a PDF. A real-PDF comparison requires separate authorization.
