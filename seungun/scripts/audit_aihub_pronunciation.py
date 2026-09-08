#!/usr/bin/env python3
"""Audit AI Hub pronunciation ZIPs without extracting small files."""

from __future__ import annotations

import argparse
from pathlib import Path

from korean_phone_ctc.data.audit import run_audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    parser.add_argument(
        "--partition",
        choices=("training", "validation", "all"),
        default="all",
    )
    parser.add_argument("--sample-error-count", type=int, default=100)
    parser.add_argument("--skip-audio-headers", action="store_true")
    parser.add_argument("--verify-crc", action="store_true")
    parser.add_argument("--requested-dataset-id", default="71479")
    parser.add_argument("--actual-dataset-id", default="71490")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    partitions = {
        "training": ("Training",),
        "validation": ("Validation",),
        "all": ("Training", "Validation"),
    }[args.partition]
    report = run_audit(
        args.dataset_root,
        args.output_dir,
        partitions=partitions,
        sample_error_count=args.sample_error_count,
        inspect_audio_headers=not args.skip_audio_headers,
        verify_crc=args.verify_crc,
        requested_dataset_id=args.requested_dataset_id,
        actual_dataset_id=args.actual_dataset_id,
    )
    print(f"audit={args.output_dir / 'data_audit.json'}")
    print(f"matched_triplets={report['matched_triplets']}")
    print(f"label_semantics={report['label_semantics']['conclusion']}")


if __name__ == "__main__":
    main()
