#!/usr/bin/env python3
"""Batch Handoff segments and validate translations before rebuilding a PDF."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from pdf2zh.handoff import (  # noqa: E402
    MAX_RETRY_ATTEMPTS,
    assess_handoff_translations,
    build_handoff_batches,
    load_source_segments,
    load_terminology,
    write_jsonl,
)


def _positive(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare bounded Handoff batches or validate targeted retries."
    )
    parser.add_argument("segments", type=Path, help="JSONL emitted by translate_pdf.py")
    parser.add_argument("--output-batches", type=Path)
    parser.add_argument("--translations", type=Path)
    parser.add_argument("--accepted", type=Path)
    parser.add_argument("--retry-batches", type=Path)
    parser.add_argument("--oversized", type=Path, help="unresolved sources over the batch cap; default: adjacent .oversized.jsonl")
    parser.add_argument("--terminology", type=Path, help="confirmed source-to-target JSON map")
    parser.add_argument("--max-segments", type=_positive, default=30)
    parser.add_argument("--max-characters", type=_positive, default=12_000)
    parser.add_argument("--attempt", type=_positive, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    batch_path = args.output_batches if args.translations is None else args.retry_batches
    if batch_path is not None:
        args.oversized = args.oversized or batch_path.with_suffix(".oversized.jsonl")
    inputs = [p.expanduser().resolve() for p in (args.segments, args.translations, args.terminology) if p is not None]
    outputs = [p.expanduser().resolve() for p in (args.output_batches, args.accepted, args.retry_batches, args.oversized) if p is not None]
    for index, output in enumerate(outputs):
        if any(output == other or (output.exists() and other.exists() and output.samefile(other))
               for other in inputs + outputs[:index]):
            raise SystemExit(f"Output aliases an input or another output: {output}")
    sources = load_source_segments(args.segments.expanduser().resolve())
    terminology = load_terminology(
        args.terminology.expanduser().resolve() if args.terminology else None
    )
    if args.attempt > MAX_RETRY_ATTEMPTS:
        raise SystemExit(f"--attempt cannot exceed {MAX_RETRY_ATTEMPTS}")

    if args.translations is None:
        if args.output_batches is None:
            raise SystemExit("--output-batches is required when preparing batches")
        oversized = []
        batches = build_handoff_batches(
            sources,
            terminology=terminology,
            max_segments=args.max_segments,
            max_characters=args.max_characters,
            attempt=args.attempt,
            oversized=oversized,
        )
        oversized_path = args.oversized or args.output_batches.with_suffix(".oversized.jsonl")
        write_jsonl(oversized_path.expanduser().resolve(), oversized)
        write_jsonl(args.output_batches.expanduser().resolve(), batches)
        print(
            f"Prepared {len(sources) - len(oversized)} unique translation units in {len(batches)} batches; "
            f"oversized={len(oversized)} ({oversized_path})"
        )
        return 0

    if args.accepted is None or args.retry_batches is None:
        raise SystemExit("--accepted and --retry-batches are required for validation")
    assessment = assess_handoff_translations(
        sources,
        args.translations.expanduser().resolve(),
        terminology=terminology,
    )
    write_jsonl(args.accepted.expanduser().resolve(), assessment.accepted)
    retries = []
    oversized = []
    exhausted = bool(assessment.retry) and args.attempt >= MAX_RETRY_ATTEMPTS
    if not exhausted:
        retries = build_handoff_batches(
            assessment.retry,
            terminology=terminology,
            max_segments=args.max_segments,
            max_characters=args.max_characters,
            attempt=args.attempt + 1,
            oversized=oversized,
        )
    oversized_path = args.oversized or args.retry_batches.with_suffix(".oversized.jsonl")
    if exhausted:
        oversized = [dict(record) for record in assessment.retry if len(record["src"]) > args.max_characters]
    write_jsonl(oversized_path.expanduser().resolve(), oversized)
    write_jsonl(args.retry_batches.expanduser().resolve(), retries)
    print(
        f"Validated {len(sources)} units: accepted={len(assessment.accepted)}, "
        f"retry={len(assessment.retry)}, retry_batches={len(retries)}, "
        f"exhausted={int(exhausted)}, oversized={len(oversized)} ({oversized_path})"
    )
    return 3 if exhausted else 0


if __name__ == "__main__":
    raise SystemExit(main())
