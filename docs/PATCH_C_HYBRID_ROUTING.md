# Patch C — Hybrid Google / Handoff Routing

## Implementation

- `pdf2zh/routing.py` is a deterministic, provider-free router. It evaluates
  approved full preservation first, explicit high-risk rules second, and the
  Google fast path last. It exposes named flags and one reason without weighted
  scoring or semantic inference.
- Patch A now exposes only read-only provenance already produced by its builder:
  logical-unit ID, child fragment IDs, merge reasons, and derived
  `trivial_wrap` / `structural_merge` metadata. Merge behavior is unchanged.
- `AutoTranslator` composes the existing Google adapter and offline/table-based
  Handoff adapter (MODEL_A). The route is chosen before cache access. Provider
  cache namespaces remain separate.
- A Google route checks valid Google cache, then a valid Handoff table/cache
  result for the same identity, then Google. Patch B validation failure or
  provider unavailability escalates only that identity to Handoff.
- Handoff queue rows retain `logical_unit_id`, `occurrence_id`, and
  `source_fragment_ids`. Pending rows are source fallback and therefore remain
  `UNRESOLVED / PENDING_HANDOFF`.
- Explicit `google` and `handoff` modes retain their existing behavior and the
  CLI default remains `google`.

## Validation performed

No PDF was opened, extracted, rendered, translated, or benchmarked. No Google
or other provider was called. Verification used only source code and synthetic
strings/temporary JSONL/cache data, as required by the user's instruction.

- 34 Patch C routing fixtures: PASS.
- 8 Handoff workflow fixtures, including identity/provenance round-trip and
  validation: PASS.
- Patch A + Patch B + Patch C focused set (76 tests): PASS.
- Expanded provider-free regression set covering routing, integrity, logical
  units, Handoff/cache, terminology, technical invariants, proper names,
  preservation, and Korean targeting (223 tests): PASS.

## Instrumentation

The report includes every requested counter: `auto_units`,
`preserve_routed_units`, `google_routed_units`, `handoff_direct_units`, Google
validation/escalation counts, pending Handoff counts, Handoff validation and
unresolved counts, provider-aware cache hits, provider request/unavailability
counts, provider/routing seconds, and the three derived ratios. Debug mode also
retains `requested_engine`, `selected_route`, `actual_provider`,
`routing_reason`, and `routing_flags` per logical identity.

## Acceptance criteria

| # | Status | Evidence |
|---:|:---:|---|
| 1 | PASS | Patch A fixture suite passes. |
| 2 | PASS | Patch B fixture suite passes. |
| 3 | PASS | Explicit Google route and CLI constraints tested. |
| 4 | PASS | Explicit Handoff route tested; existing Handoff tests pass. |
| 5 | PASS | AUTO routes logical-unit identities. |
| 6 | PASS | Fully approved preserve fixture routes without a provider. |
| 7 | PASS | Simple English, Korean, and Chinese route Google. |
| 8 | PASS | Mixed context, structural merge, and dense associations route Handoff. |
| 9 | PASS | Router executes before any provider cache lookup. |
| 10 | PASS | High-risk fixture ignores a valid Google cache entry. |
| 11 | PASS | Passing Google result is accepted without Handoff. |
| 12 | PASS | Google validation failure enqueues the same identity only. |
| 13 | PASS | Two-unit fixture proves no document/batch escalation. |
| 14 | PASS | Repository Handoff architecture is reused as MODEL_A. |
| 15 | PASS | Pending Handoff remains unresolved and is not marked resolved. |
| 16 | N/A | No callable MODEL_B provider exists in this repository. |
| 17 | PASS | Existing Patch B validation is used by both provider adapters. |
| 18 | PASS | Invalid Handoff result is counted and ends unresolved after retry accounting. |
| 19 | PASS | No comparison or dual-provider path exists. |
| 20 | PASS | All three identity fields survive queue, load, batch, accepted/retry output, and retry batching. |
| 21 | PASS | Router consumes Patch A output and contains no reconstruction code. |
| 22 | PASS | Added fields are read-only provenance derived from existing results. |
| 23 | PASS | Existing thread pool and provider-specific Handoff workflow remain. |
| 24 | PASS | Google and Handoff retain separate cache instances/namespaces. |
| 25 | PASS | Valid Google and Handoff reuse paths are tested. |
| 26 | PASS | Confidentiality marker leaves the route unchanged. |
| 27 | PASS | Google timeout enqueues the same unit and stays unresolved. |
| 28 | PASS | Missing Handoff queue/result reports `PROVIDER_UNAVAILABLE`. |
| 29 | PASS | Router uses regular expressions and read-only local metadata only. |
| 30 | PASS | Router performs no LLM or provider call. |
| 31 | NOT RUN | J1C PDF processing was prohibited by the user's no-PDF instruction. |
| 32 | NOT RUN | Mitsubishi PDF processing was prohibited by the user's no-PDF instruction. |
| 33 | PASS | No paid or real-provider Mitsubishi run was performed. |
| 34 | PASS | Pending queue count is separate from translated accounting. |
| 35 | NOT RUN | Page-count runtime verification requires processing a PDF. |
| 36 | NOT RUN | Geometry/rotation/mapping runtime verification requires a PDF. |
| 37 | PASS | Native renderer files/algorithm were not changed by Patch C. |
| 38 | PASS | No OCR, DOCX path, or renderer was introduced. |
| 39 | PASS | No Patch D performance refactor was implemented. |

Overall acceptance is **PARTIAL** solely because criteria 31, 32, 35, and 36
require real-PDF processing that the user explicitly prohibited. There are no
document routing metrics or before/after provider times for the same reason.

## Deferred issues

- Patch A: cross-page reconstruction remains the documented limitation.
- Patch B: real-document final text-layer audit was not exercised here.
- Patch D: provider batching/performance tuning remains out of scope.
