# Validation and PDF QA

## Automated Gate

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
