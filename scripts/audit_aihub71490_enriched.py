#!/usr/bin/env python3
"""Recompute automated G0 gates from an enriched AI Hub Parquet manifest."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-records", type=int, default=322_380)
    parser.add_argument("--min-pairing-rate", type=float, default=0.99)
    parser.add_argument("--max-decoder-failure-rate", type=float, default=0.001)
    parser.add_argument("--max-time-bound-error-rate", type=float, default=0.01)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    records = pairing_failures = decoder_failures = time_bound_errors = 0
    annotation_parse_failures = 0
    flags: Counter[str] = Counter()
    operations: Counter[str] = Counter()
    splits: Counter[str] = Counter()
    speaker_split: dict[str, str] = {}
    pcm_split: dict[str, str] = {}
    speaker_overlaps: list[dict[str, str]] = []
    pcm_overlaps: list[dict[str, str]] = []

    parquet = pq.ParquetFile(args.manifest)
    columns = [
        "speaker_id",
        "split",
        "pcm_sha256",
        "qc_flags",
        "phone_alignments",
    ]
    for batch in parquet.iter_batches(batch_size=4096, columns=columns):
        for row in batch.to_pylist():
            records += 1
            split = row["split"]
            splits[split] += 1
            row_flags = row["qc_flags"] or []
            flags.update(row_flags)
            operations.update(
                item["operation"] for item in (row["phone_alignments"] or [])
            )
            pairing_failed = any(
                flag == "missing_realized_phone_tier"
                or flag.startswith("annotation_parse_error:")
                or flag.startswith("textgrid_parse_error:")
                for flag in row_flags
            )
            decoder_failed = any(
                flag.startswith("pcm_hash_error:") for flag in row_flags
            )
            annotation_failed = any(
                flag.startswith("annotation_parse_error:") for flag in row_flags
            )
            pairing_failures += pairing_failed
            decoder_failures += decoder_failed
            annotation_parse_failures += annotation_failed
            time_bound_errors += "interval_out_of_bounds" in row_flags

            speaker = row["speaker_id"]
            previous_split = speaker_split.setdefault(speaker, split)
            if (
                previous_split != split
                and len(speaker_overlaps) < 100
            ):
                speaker_overlaps.append(
                    {
                        "speaker_id": speaker,
                        "first_split": previous_split,
                        "second_split": split,
                    }
                )
            pcm_hash = row["pcm_sha256"]
            if pcm_hash:
                previous_pcm_split = pcm_split.setdefault(pcm_hash, split)
                if (
                    previous_pcm_split != split
                    and len(pcm_overlaps) < 100
                ):
                    pcm_overlaps.append(
                        {
                            "pcm_sha256": pcm_hash,
                            "first_split": previous_pcm_split,
                            "second_split": split,
                        }
                    )

    pairing_rate = (records - pairing_failures) / records
    decoder_failure_rate = decoder_failures / records
    time_bound_error_rate = time_bound_errors / records
    assignment_payload = json.dumps(
        sorted(speaker_split.items()),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    failed: list[str] = []
    if records != args.expected_records:
        failed.append("record_count")
    if pairing_rate < args.min_pairing_rate:
        failed.append("pairing_rate")
    if decoder_failure_rate > args.max_decoder_failure_rate:
        failed.append("decoder_failure_rate")
    if time_bound_error_rate > args.max_time_bound_error_rate:
        failed.append("time_bound_error_rate")
    if annotation_parse_failures:
        failed.append("annotation_parse_failure")
    if speaker_overlaps:
        failed.append("cross_split_speaker_overlap")
    if pcm_overlaps:
        failed.append("cross_split_pcm_duplicate")

    result = {
        "schema_version": "aihub71490.g0.audit.v1",
        "automated_status": "passed" if not failed else "failed",
        "overall_g0_status": (
            "pending_manual_alignment_audit"
            if not failed
            else "failed_automated_gates"
        ),
        "record_count": records,
        "speaker_count": len(speaker_split),
        "unique_pcm_count": len(pcm_split),
        "split_counts": dict(sorted(splits.items())),
        "pairing_rate": pairing_rate,
        "decoder_failure_rate": decoder_failure_rate,
        "time_bound_error_rate": time_bound_error_rate,
        "annotation_parse_failures": annotation_parse_failures,
        "qc_flag_counts": dict(sorted(flags.items())),
        "phone_alignment_operations": dict(sorted(operations.items())),
        "cross_split_speaker_overlap_examples": speaker_overlaps,
        "cross_split_pcm_duplicate_examples": pcm_overlaps,
        "split_assignment_sha256": hashlib.sha256(
            assignment_payload
        ).hexdigest(),
        "parquet_sha256": sha256_file(args.manifest),
        "thresholds": {
            "expected_records": args.expected_records,
            "min_pairing_rate": args.min_pairing_rate,
            "max_decoder_failure_rate": args.max_decoder_failure_rate,
            "max_time_bound_error_rate": args.max_time_bound_error_rate,
        },
        "manual_alignment_audit": {
            "status": "pending",
            "required_samples": 240,
            "minimum_accuracy": 0.95,
        },
        "failed_automated_gates": failed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
