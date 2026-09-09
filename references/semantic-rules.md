# Semantic safety and terminology

Technical translation must preserve meaning even when multiple Vietnamese
wordings are valid. Review or benchmark these semantic properties instead of
requiring one exact natural-language sentence:

- negation and prohibition must remain negative or prohibited;
- mandatory, recommended, and permitted/optional strength must not collapse;
- before, after, until, when, if, unless, only-if, and while relationships must
  keep their direction and condition;
- DANGER, WARNING, CAUTION, and NOTICE levels must remain distinct and must not
  be raised or lowered without source evidence;
- apparent source mistakes remain as written in the translation body; report a
  suspected issue separately rather than silently correcting a register, alarm,
  value, model, revision, figure, or standard.

Source languages may be Korean, English, Simplified Chinese, Traditional
Chinese, or mixed. English commands and identifiers inside Korean/Chinese prose
are not evidence that the whole segment is English. Keep `source_language=auto`
unless the user has supplied a reliable source code.

Use only terminology confirmed by the document, the user, or a cited vendor
source. Do not invent a vendor glossary or label an uncited list as official.
When terminology is uncertain, preserve the source term beside a careful
Vietnamese rendering or mark the segment unresolved if meaning cannot be kept
safely.

For Korean Handoff, `검수`, `시운전`, `대응`, and `공용화` are context-sensitive;
do not assign one global Vietnamese meaning without confirmed terminology.
Approved English technical terms and explicit UI labels inside Korean prose stay
exact, while the Korean prose is translated. Optional adjacent context is
bounded, remains untrusted, and is used only for disambiguation.

The deterministic Korean guard covers only supported patterns for `전`/`후`,
condition, need, mandatory, recommendation, possibility, impossibility, and
prohibition. It also rejects invented responsibility for `대응 필요`. Unsupported
Korean grammar remains a review concern rather than a guessed validator rule.

The separate cases in `tests/semantic_benchmark_cases.jsonl` cover Korean,
English, Simplified Chinese, and Traditional Chinese. They require an approved
provider plus human/semantic evaluation and are not the deterministic CI gate.
