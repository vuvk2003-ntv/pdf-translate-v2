# Patch A: Logical Translation Unit Reconstruction

## Implemented scope

- `pdf2zh.logical_units` is the single deterministic builder used by the
  production extraction path and synthetic fixtures.
- Every candidate fragment carries a stable child ID, page, source bounds,
  reading order, region/cell/column/paragraph/callout ownership, structural
  role, inline style, and font size when available.
- Known different cells, columns, roles, paragraphs, callouts, and pages are
  hard boundaries. Unknown ownership never becomes same ownership from
  proximity alone.
- Same-line style runs and conservative wrapped lines can form one unit.
  `<s1>`, `<s2>`, and `<s3>` retain inline emphasis.
- One reconstructed unit receives one converter paragraph and translation
  identity. Existing Handoff batching remains unchanged. Existing retry and
  fallback receive the complete unit.
- Candidate assignment is checked on every production run. Missing or duplicate
  ownership raises before a report is returned.
- Run reports expose every Patch A counter, rejection reason, unit-size metric,
  provider request/retry count, and timing field. Debug logging exposes merged
  unit provenance.
- Cross-page continuation remains intentionally unsupported and is reported as
  `KNOWN CROSS-PAGE CONTINUATION LIMITATION`.

## Verification completed

- `python -m unittest discover -s tests -v`: **416 passed**.
- Eleven exact logical-boundary fixtures: **passed**.
- Candidate conservation positive and duplicate-ID negative tests: **passed**.
- Deliberately under-merging health-gate negative test: **passed**.
- Synthetic separate-Form-XObject atomic emit/fallback/rebuild regression:
  **passed**.
- `python -m pip check`: **passed**.
- `python -m compileall -q pdf2zh scripts tests`: **passed**.
- Ruff hard-error selection `E9,F63,F7,F82`: **passed**.
- `git diff --check`: **passed**.

No source PDF was translated and no provider was called. A provider-free
extraction check had started before the user clarified the constraint; it was
stopped, all generated segment/log/render files were deleted, and its results
are not used as delivery evidence. No further real-document benchmark or render
review was performed.

## Acceptance status

| # | Criterion | Status |
| --- | --- | --- |
| 1 | J1C reconstruction regression and dropped-occurrence trace | PARTIAL - synthetic regression passes; real J1C trace not run |
| 2 | No translated/source child mixture in one occurrence | PARTIAL - structurally enforced and synthetic integration passes; real PDFs not run |
| 3 | Inline emphasis does not split a sentence | PASS |
| 4 | Style-marker invariants remain valid | PASS |
| 5 | No cross-cell merge | PARTIAL - hard barrier and fixtures pass; real table review not run |
| 6 | No cross-column/role/callout merge | PARTIAL - hard barriers and fixtures pass; real-page review not run |
| 7 | Cross-page continuation remains separate and reported | PASS |
| 8 | Both cross-page halves remain independently accounted | PASS in fixture; real-page review not run |
| 9 | Equivalent before/after J1C and Mitsubishi metrics | NOT RUN per user instruction |
| 10 | Unit/request reduction on fragmented documents | PARTIAL - unit reduction passes in fixtures; request measurement not run |
| 11 | Reconstruction-health negative test | PASS |
| 12 | Zero confirmed structural false merges | PASS in fixtures; real-document confirmation not run |
| 13 | Existing P0/P1/P2 safety and correctness regressions | PASS - 416 tests |

The production benchmark path remains available through Handoff extraction with
`--emit-segments` and no output directory. It executes the same builder and
stops at the provider boundary, but it was not invoked for the final verification
above.
