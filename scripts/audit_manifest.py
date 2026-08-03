#!/usr/bin/env python3
"""Audit JSONL or Parquet manifests against the G0 quality gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

from voice_coach.data import audit_manifest_records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-pairing-rate", type=float, default=0.99)
    parser.add_argument("--max-decoder-failure-rate", type=float, default=0.001)
    parser.add_argument("--max-time-bound-error-rate", type=float, default=0.01)
    return parser.parse_args()


def read_records(path: Path) -> Iterator[dict[str, Any]]:
    if path.suffix.casefold() == ".parquet":
        import pyarrow.parquet as parquet

        yield from parquet.read_table(path).to_pylist()
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"line {line_number} is not a JSON object")
                yield value


def main() -> int:
    args = parse_args()
    result = audit_manifest_records(
        read_records(args.manifest),
        min_pairing_rate=args.min_pairing_rate,
        max_decoder_failure_rate=args.max_decoder_failure_rate,
        max_time_bound_error_rate=args.max_time_bound_error_rate,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
