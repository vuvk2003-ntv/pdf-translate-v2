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
    parser.add_argument("--terminology", type=Path, help="confirmed source-to-target JSON map")
    parser.add_argument("--max-segments", type=_positive, default=30)
    parser.add_argument("--max-characters", type=_positive, default=12_000)
    parser.add_argument("--attempt", type=_positive, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    sources = load_source_segments(args.segments.expanduser().resolve())
    terminology = load_terminology(
        args.terminology.expanduser().resolve() if args.terminology else None
    )
    if args.attempt > MAX_RETRY_ATTEMPTS:
        raise SystemExit(f"--attempt cannot exceed {MAX_RETRY_ATTEMPTS}")

    if args.translations is None:
        if args.output_batches is None:
            raise SystemExit("--output-batches is required when preparing batches")
        batches = build_handoff_batches(
            sources,
            terminology=terminology,
            max_segments=args.max_segments,
            max_characters=args.max_characters,
            attempt=args.attempt,
        )
        write_jsonl(args.output_batches.expanduser().resolve(), batches)
        print(
            f"Prepared {len(sources)} unique translation units in {len(batches)} batches"
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
    exhausted = bool(assessment.retry) and args.attempt >= MAX_RETRY_ATTEMPTS
    if not exhausted:
        retries = build_handoff_batches(
            assessment.retry,
            terminology=terminology,
            max_segments=args.max_segments,
            max_characters=args.max_characters,
            attempt=args.attempt + 1,
        )
    write_jsonl(args.retry_batches.expanduser().resolve(), retries)
    print(
        f"Validated {len(sources)} units: accepted={len(assessment.accepted)}, "
        f"retry={len(assessment.retry)}, retry_batches={len(retries)}, "
        f"exhausted={int(exhausted)}"
    )
    return 3 if exhausted else 0


if __name__ == "__main__":
    raise SystemExit(main())
