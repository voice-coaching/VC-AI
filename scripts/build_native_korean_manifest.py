#!/usr/bin/env python3
"""Build a reviewed restricted manifest and aggregate native-corpus report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from voice_coach.data.native_korean import (
    DEFAULT_ROOTS,
    NativeCorpusRoots,
    build_native_korean_manifest,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output Parquet path outside the raw corpus roots.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="Aggregate JSON report path outside the raw corpus roots.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Maximum records per source. Use for a four-source smoke build; "
            "omit only when intentionally building the full manifest."
        ),
    )
    parser.add_argument(
        "--fleurs-root",
        type=Path,
        default=DEFAULT_ROOTS.fleurs,
    )
    parser.add_argument(
        "--kss-root",
        type=Path,
        default=DEFAULT_ROOTS.kss,
    )
    parser.add_argument(
        "--seoul-root",
        type=Path,
        default=DEFAULT_ROOTS.seoul,
    )
    parser.add_argument(
        "--zeroth-root",
        type=Path,
        default=DEFAULT_ROOTS.zeroth,
    )
    arguments = parser.parse_args(argv)
    if arguments.limit is not None and arguments.limit <= 0:
        parser.error("--limit must be positive")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    report = build_native_korean_manifest(
        roots=NativeCorpusRoots(
            fleurs=arguments.fleurs_root,
            kss=arguments.kss_root,
            seoul=arguments.seoul_root,
            zeroth=arguments.zeroth_root,
        ),
        output_path=arguments.output,
        report_path=arguments.report,
        limit_per_source=arguments.limit,
    )
    print(
        json.dumps(
            {
                "manifest": str(arguments.output),
                "report": str(arguments.report),
                "records": report["manifest"]["records"],
                "source_counts": report["counts"]["source"],
                "limit_per_source": report["limit_per_source"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
