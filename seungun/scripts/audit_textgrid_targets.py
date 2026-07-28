#!/usr/bin/env python3
"""Audit extracted TextGrid phone tiers as candidate observed CTC targets."""

from __future__ import annotations

import argparse
from pathlib import Path

from korean_phone_ctc.data.textgrid_target_audit import (
    extracted_partition_directories,
    run_textgrid_target_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--special-sample-limit", type=int, default=200)
    parser.add_argument("--progress-every", type=int, default=50_000)
    args = parser.parse_args()
    report = run_textgrid_target_audit(
        partition_directories=extracted_partition_directories(args.dataset_root),
        output_dir=args.output_dir,
        special_sample_limit=args.special_sample_limit,
        progress_every=args.progress_every,
    )
    print(f"audit={args.output_dir / 'textgrid_target_audit.json'}")
    print(f"gate={report['gate']['status']}")


if __name__ == "__main__":
    main()
