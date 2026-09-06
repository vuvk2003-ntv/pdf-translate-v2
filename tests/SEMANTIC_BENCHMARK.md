# Tier B semantic benchmark

This benchmark is intentionally separate from `unittest` discovery and is not a
deterministic CI gate. Translate every `source` in
`semantic_benchmark_cases.jsonl` to Vietnamese with the approved provider, then
have a qualified human reviewer or an approved semantic evaluator judge every
listed invariant independently.

A case passes only when all invariants are present with the same polarity,
modality, and temporal/conditional direction. Exact Vietnamese wording is not
required. Immutable tokens such as `D100` and `250` remain exact-match checks.
Record provider/model/version, date, candidate translation, per-invariant
pass/fail, and reviewer/evaluator identity. Do not merge Tier B results into the
deterministic Tier A pass count.
