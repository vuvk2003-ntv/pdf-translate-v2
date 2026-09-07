# PDF Engine Architecture and Invariants

## Pipeline

1. `scripts/translate_pdf.py` validates a text-based PDF and stages output.
2. `pdf2zh/high_level.py` loads fonts/model, predicts layout, matches tables,
   detects preserved structures, patches pages, and serializes the mono PDF.
3. `pdf2zh/converter.py` groups glyphs into paragraphs, carries formulas and
   styles through translation markers, reflows text, and emits PDF operators.
4. `pdf2zh/translator.py` validates placeholders/style markers plus the
   deterministic technical tokens in `pdf2zh/invariants.py`, and caches only
   safe translations.
5. `pdf2zh/pdfinterp.py` retains source graphics while replacing selected text.

Handoff extraction is followed by `scripts/prepare_handoff.py`. It groups at
most 30 segments / 12,000 source characters, carries identity through every
batch, includes an optional confirmed terminology map once per batch, and
validates all results before the single final rebuild. Valid units survive a
partial failure; only failed units are retried, up to three attempts.
An oversized single source is excluded intact into an adjacent oversized JSONL,
not sent in an over-budget batch. Rebuild matches segment IDs; safe shared IDs
can reuse validated, identity-bound cache entries with the same terminology and
translation-rules fingerprint. Bump `TRANSLATION_RULES_VERSION` when classifier
or validation semantics change. Cache is not disabled by default.
ASCII single-token terminology uses word boundaries on source and target;
`pin` does not match `shipping`. CJK, phrases, and punctuation-bearing terms
retain substring matching. Matcher revision scopes only nonempty terminology
maps in the cache fingerprint.

Each run returns a `TranslationReport`: total, translated, preserved, and
unresolved segment counts; the segments left in the source language and why;
the image-only pages; and how much text was translatable at all. The accounting
must cover every converter-extracted segment. Callers must report the reason rather than the count -
a fit failure, a damaged formula marker, and a dead connection each need the
user to do something different. The layout model's stride and class names
come from the onnxruntime session, so nothing imports `onnx` directly.

## Preservation Invariants

- Formula glyphs and rules retain source fonts and relative geometry. Ordinary
  fonts can still contain formulas, so detection also uses operators and
  stacked-token geometry.
- `{vN}` is the internal formula placeholder. Translators receive safe
  `<bN></bN>` tags. Missing or reordered formula tags reject the translation.
- `<s1>`, `<s2>`, and `<s3>` carry bold, italic, and bold-italic runs. Pairs may
  move with their phrase but must stay balanced and non-cross-nested.
- Technical identifiers, numeric literals, engineering units/ranges, network
  addresses, paths/URLs, document/standard IDs, versions, generic placeholders,
  structural tags, and literal ON/OFF states are validated before caching.
- Token-only labels and executable PLC/robot blocks are filtered before
  translation. Substantial exact repeats may share work; ambiguous short labels
  bypass deduplication and persistent cache reuse.
- Tables translate per reliable cell only when the model region and
  `PyMuPDF.find_tables()` overlap by at least 50%. Grid, fill, and border
  operators remain source content. Unreliable tables stay protected.
- Quarter-turn text uses logical baseline orientation. Reflected matrices used
  with negative font sizes are normalized before classification.
- Symbol/Wingdings private-use bullets remain source glyphs in their embedded
  dingbat font; prose fonts must not receive those code points.
- Text fitting accounts for first-line indentation, final glyph ink, formula
  offsets, and cell borders. The minimum translated size is 50% of source;
  unsafe overflow falls back to source text and records a partial result.
- Leading is never compressed below `min_line_height_for_language`, measured
  from real glyph ink (`vi` = 1.10 em). A paragraph short of room reduces
  leading to that floor, then borrows the clear gap below it
  (`available_height_below`), and only then shrinks the font.
- Output fonts are never subset. `raw_string` writes glyph IDs into Identity-H
  fonts, so renumbering them silently repoints every translated character.
  A glyph-stable alternative would be a fontTools subset with `retain_gids`.
- Base-14 faces are shared through every page resource dictionary. Unicode
  faces are installed only after conversion identifies the styles that emitted
  fallback glyphs. Width probes, including the worst-case table-cell fit probe,
  must not mark a face as rendered; otherwise an ordinary document embeds four
  full Unicode faces it never uses.
- A paragraph takes its size from the first characters that draw ink, so an
  oversized bullet and its tab cannot set the size for a whole list item.
- Scanned image-only pages are not OCRed. Source pixels under translated text
  receive backing only where required by the scan path. A page carrying an
  image that yields no translatable segment is reported as image-only, and a
  document with no translatable segment at all is refused rather than
  delivered as a translation of nothing. Note the two different questions:
  `is_scanned_page` (one image over half the page) drives backing rectangles;
  `page_has_image` (any image at all) drives the image-only report, because a
  scanner routinely emits one page as dozens of small tiles.

## Colour and Emphasis

The colour in force is captured as the source wrote it and replayed in front of
each run, the way BabelDOC carries colour: reducing everything to RGB would
guess at a conversion of an ICCBased or Separation space the document never
asked for. One entry is kept per piece of colour state, not per operator, since
`g`, `rg`, `k`, `sc` and `scn` all write the same slot. `sc`/`scn` are never
replayed without a space that explains their operands - a device space is
supplied from the operand count, and a colour that still cannot be explained is
dropped so the run falls back to black rather than to whatever DeviceGray makes
of four CMYK components. An ExtGState travels with the text only when it
carries no soft mask, transfer function or partial alpha. A paragraph takes the
colour most of its own ink uses, because a colour change cannot travel through
the translator the way a style marker can.

Emphasis comes from the font descriptor's own flags before the font name, since
the Adobe Pro families abbreviate the slanted face as `-It`.

## Large Documents

The app requests mono output only; do not construct the unused interleaved
dual-language document. A PDF is large at 200 pages or 50 MiB. Large PDFs use
light serialization (`garbage=1`, no recompression/object streams) because
aggressive cleanup can hold the GIL for tens of seconds after page progress
reaches 100%. Font subsetting is off for every size, not just large documents.

## Damaged Sources

`pymupdf_can_round_trip` decides whether a document needs repair, because
pikepdf opens damage that MuPDF only refuses on write. The repaired copy is
re-checked; a document that still fails is reported, never silently translated.

The product-level authority is
[`references/preservation-rules.md`](../references/preservation-rules.md).
