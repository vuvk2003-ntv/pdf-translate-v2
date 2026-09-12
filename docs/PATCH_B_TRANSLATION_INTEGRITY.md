# Patch B — Translation Integrity

Patch B consumes Patch A logical units without changing their reconstruction,
ownership, merge thresholds, page boundaries, provider routing, batching, or
rendering architecture.

## Implementation

- `pdf2zh/integrity.py` provides the stable eligible-span inventory,
  exact-once occurrence ledger, approved-preserve provenance, three-state final
  status model, deterministic translation validators, reconciliation metrics,
  delivery status, and final text-layer audit.
- `pdf2zh/converter.py` inventories every non-empty production occurrence before
  translation filtering. It records provider results, explicit preservation,
  provider/cache failures, and render-fit source fallbacks in the same ledger.
- `pdf2zh/translator.py` validates cached and fresh results identically. Invalid
  cache entries become misses. Handoff input records also pass Patch B
  validation. The cache rules revision is `code4life-translation-v5`.
- `pdf2zh/invariants.py` remains the single technical/literal protection system.
  Patch B adds explicit code-span/protocol syntax tokens and comparison-safe
  handling of sentence punctuation after Windows paths.
- `pdf2zh/high_level.py` and `scripts/translate_pdf.py` expose every required
  counter, accounting/completion rates, and `SUCCESS`/`PARTIAL`/`FAIL` status.
  Before publication the runner audits the generated PDF's existing text layer;
  it does not render again and does not use OCR.

## Deterministic test evidence

`tests/test_patch_b_integrity.py` contains 28 provider-free string and ledger
tests covering all 22 requested synthetic scenarios plus source-side
false-positive controls, unknown status, and explicit unresolved fallback in
the final audit. The Patch A logical-unit suite remains separate and unchanged.

No real PDF, provider translation, document extraction, visual render, or paid
benchmark was run for this patch, as explicitly requested by the user.

## Acceptance criteria

| # | Status | Evidence / limitation |
|---:|:---:|---|
| 1 | PASS | Patch A logical-unit tests pass; reconstruction code was not changed. |
| 2–8 | PASS | Exact-once ledger, reconciliation, valid status enum, and unapproved-preserve tests. |
| 9–11 | PASS | Korean/Chinese leakage and occurrence-specific approved-span tests. |
| 12–13 | PASS | Existing invariant suite plus path and explicit protocol-syntax tests. |
| 14–18 | PASS | Identity, repeated token/character, length, and unrelated-script tests. |
| 19 | PASS | Source fallback is recorded as `UNRESOLVED`. |
| 20 | PASS | Invalid cache-hit test proves rejection followed by the normal miss path. |
| 21 | PASS | Pure text-layer audit test catches reintroduced Hangul. |
| 22 | PASS | Delivery status cannot be `SUCCESS` with unresolved or broken accounting. |
| 23 | NOT RUN | J1C full-document regression was prohibited by the user's no-PDF instruction. |
| 24 | NOT RUN | Mitsubishi provider-free full-document accounting was prohibited by the user's no-PDF instruction. |
| 25 | PARTIAL | Code-level Patch A/invariant regressions pass; PDF P0/P1/P2 gates were not executed. |
| 26–28 | PASS | No hybrid router, semantic/LLM reviewer, OCR, or new renderer was introduced. |

Because criteria 23–25 require document execution, overall real-document Patch
B acceptance remains `NOT RUN`. This does not change the completed code-level
implementation.

## Unresolved occurrences and adjacent-patch findings

There is no document occurrence list because no document was processed. The
synthetic suite intentionally creates unresolved records to prove status and
counter behavior; they are test fixtures, not user-document findings.

No Patch A merge issue, Patch C routing issue, or Patch D performance issue was
investigated or changed in this patch.
