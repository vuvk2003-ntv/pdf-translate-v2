# Validation and PDF QA

## Automated Gate

For Patch D, follow [performance profiling](../references/performance-profiling.md)
for timer meanings and cache isolation. If the user forbids PDF processing,
replace PDF suites/render QA with explicitly selected pure string/JSONL tests;
do not run discovery or `--profile-only`. Mark real-document acceptance gates
`NOT_RUN`. Profiling correctness and synthetic overhead are separate from PDF
performance/layout acceptance.

For D2 without PDF processing, run `scripts/run_patch_d2_no_pdf_checks.py`:
375 explicitly selected tests, including 22 D2 fixtures, with PDF/HTTP guards.
Use `scripts/benchmark_patch_d2_synthetic.py` for exact controlled before/after
identity-set and accounting comparison. `--quality-recovery-probe` demonstrates
the outcome drift possible when a quality rejection recovers after attempt 2.
Report that finding separately from passing fixed-response fixtures. Real
benchmark/layout gates remain `NOT_RUN`; do not infer ship-ready acceptance.

Run from the repository root with the platform virtual environment:

```text
python -m unittest discover -s tests -v
python -m pip check
python -m ruff check pdf2zh scripts app tests --select E9,F63,F7,F82
```

This is Tier A and must not call a live translation provider. Tier B semantic
cases live in `tests/semantic_benchmark_cases.jsonl`; follow
`tests/SEMANTIC_BENCHMARK.md` and report them separately from CI.

Proper-name regression tests use mock providers. Require exact boundary and
occurrence behavior, mixed-prose translation, standalone bypass, terminology
precedence, Handoff rejection/acceptance, and cache revision isolation.

`git diff --check` must be clean. Run `app/gui.py --smoke-test`; after packaging,
run the packaged executable with `--smoke-test` and require exit code 0.

For Handoff performance evidence, record the emitted source count, unique
translation units, prepared batch count, accepted/retry count per attempt,
cache/table hits, provider requests, and phase timings printed by the CLI.
Validate retries with `scripts/prepare_handoff.py` before the final PDF rebuild;
do not count synthetic unit benchmarks as a measured PDF speedup.
Record output bytes, page font references, and unique output-font objects before
and after a font change. Render every page after reducing fonts: a clean font
inventory alone does not prove that the remaining shared xrefs draw on every
page.

For logical-reconstruction changes, use the production Handoff extraction path
without `--output-dir` as the provider-free benchmark. Record raw spans,
candidate/assigned/unassigned/duplicate fragments, logical/provider-bound
units, merged/singleton counts, every continuation rejection reason, unit
character statistics, requests, retries, translation time, and total time.
Candidate assignment must be conserved exactly. Run all eleven fixtures in
`tests/test_logical_units.py`; any source-confirmed cross-cell, cross-column,
heading/body, bullet, callout, or paragraph false merge is a hard failure.
`reconstruction_health_review` may request manual review for suspiciously low
acceptance or high singleton rates, but is benchmark guidance and must not gate
arbitrary production PDFs. Cross-page continuations intentionally remain two
accounted units and must be reported as `KNOWN CROSS-PAGE CONTINUATION
LIMITATION`.

For Patch B translation-integrity changes, run
`tests/test_patch_b_integrity.py` and retain the Patch A logical-unit fixtures.

For Patch C provider-routing changes, run `tests/test_patch_c_routing.py` and
`tests/test_handoff_workflow.py` together with Patch A, Patch B, Handoff/cache,
terminology, proper-name, and preservation tests. These are provider-free
string/ledger fixtures. Confirm
that explicit Google/Handoff modes retain their meaning, AUTO routes before
cache lookup, invalid Google results enqueue only the same identity, and
MODEL_A pending units remain unresolved. Require `logical_unit_id`,
`occurrence_id`, and `source_fragment_ids` to survive source loading, batching,
accepted/retry output, and retry batching unchanged.
The synthetic suite must cover approved and unapproved preservation, Korean and
Chinese source-script leakage, unchanged English prose, exact technical/path
and protocol-syntax preservation, repeated-token/character corruption, length
explosion, unrelated Unicode scripts, exact-once source-span assignment, source
fallback status, invalid cache hits, and final text-layer leakage. These checks
are deterministic and provider-free.

For a production run, require the Patch B report counters to reconcile exactly:
eligible spans equal uniquely ledger-assigned spans; unassigned, duplicate, and
unknown counts are zero; and all occurrences sum across `TRANSLATED`,
`ALLOWED_PRESERVE`, and `UNRESOLVED`. Every allowed preserve must carry an
approved existing-policy reason. Report `accounting_coverage` separately from
`translation_completion_rate`; unresolved fallback lowers only the latter.
After native generation and before atomic promotion, audit the selected pages'
extractable text layer for Hangul/Han beyond approved preserve and explicit
unresolved-fallback provenance. Do not add OCR, another renderer pass, provider
routing, or semantic review for Patch B.

## PDF Gate

Keep source and output hashes/paths separate. For every delivered PDF:

1. Reopen it and require the same page count and canvas sizes as the source.
2. Search extracted text for `{vN}`, `<bN>`, `<sN>`, `U+0000`, and `U+FFFD`.
3. Check span boxes against the page canvas and cell text against detected cell
   boundaries. Extraction is diagnostic, not proof of visual correctness.
4. Render every page. Review contact sheets for global anomalies, then inspect
   formula-, table-, rotation-, style-, first/middle/last-page regressions at
   readable resolution.
5. Confirm formulas, technical identifiers, URLs, figures, borders, bullets,
   bold/italic runs, and page numbers remain legible and correctly placed.
6. Report every fallback segment or materially untranslated region as partial.
7. For protected regions and structural pages, verify extractable prose is
   translated or unresolved while immutable metadata, TOC/index locators,
   nomenclature symbols, citation identity, and raster-only text are explicitly
   preserved. Confirm each recovered source occurrence renders once within its
   original fixed region.

Production-boundary tests must also simulate native-core failure, source mutation,
wrong public engine values, page-count/size/rotation changes, reversed page order,
validator failure, staged-byte corruption, and failed replacement of an existing
destination. Every structural case must raise before atomic promotion; ordinary
layout warnings remain diagnostic when the validator itself completes.

Use `tmp/pdfs/` for render/diagnostic intermediates and `output/pdf/` for final
PDFs. Never overwrite a source or an existing user output without explicit
authorization. For expensive translations, validate representative pages
first, then regenerate the complete document with the final code.
