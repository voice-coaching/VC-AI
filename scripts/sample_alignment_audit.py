#!/usr/bin/env python3
"""Create a deterministic, stratified manual alignment-audit worksheet."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import heapq
import json
import math
from pathlib import Path
import sys

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from voice_coach.data.intervals import Interval, align_intervals  # noqa: E402


DEFAULT_STRATUM_COLUMNS = ("l1", "gender", "proficiency", "error_presence")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--details-output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=240)
    parser.add_argument("--minimum-per-stratum", type=int, default=2)
    parser.add_argument("--seed", default="aihub71490-g0-alignment-audit-v1")
    parser.add_argument(
        "--stratum-columns",
        default=",".join(DEFAULT_STRATUM_COLUMNS),
        help=(
            "comma-separated columns to stratify by; a smaller sample "
            "size needs a coarser choice than the default 4-way split "
            "(49 strata), e.g. 'error_presence' alone"
        ),
    )
    parser.add_argument(
        "--min-duration-seconds",
        type=float,
        default=0.0,
        help="only sample utterances at or above this duration",
    )
    parser.add_argument(
        "--recompute-alignments",
        action="store_true",
        help=(
            "recompute automatic_phone_alignments with the current "
            "align_intervals() implementation instead of trusting the "
            "manifest's possibly-stale phone_alignments column"
        ),
    )
    return parser.parse_args()


def to_intervals(entries: list[dict]) -> list[Interval]:
    return [
        Interval(start=entry["start"], end=entry["end"], label=entry["label"])
        for entry in entries
    ]


def alignment_to_dict(alignment) -> dict:
    starts = [
        interval.start
        for interval in (alignment.target, alignment.realized)
        if interval is not None
    ]
    ends = [
        interval.end
        for interval in (alignment.target, alignment.realized)
        if interval is not None
    ]
    return {
        "target_label": (
            alignment.target.label if alignment.target is not None else None
        ),
        "realized_label": (
            alignment.realized.label if alignment.realized is not None else None
        ),
        "start": min(starts),
        "end": max(ends),
        "overlap_seconds": alignment.overlap_seconds,
        "overlap_ratio": alignment.overlap_ratio,
        "operation": alignment.operation,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stratum_for(row: dict, stratum_columns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(row[column]) for column in stratum_columns)


def allocate_quotas(
    counts: Counter[tuple[str, ...]], sample_size: int, minimum: int
) -> dict[tuple[str, ...], int]:
    if sample_size <= 0:
        raise ValueError("sample size must be positive")
    if sample_size > sum(counts.values()):
        raise ValueError("sample size exceeds manifest size")
    quotas = {key: min(minimum, count) for key, count in counts.items()}
    if sum(quotas.values()) > sample_size:
        raise ValueError(
            "sample size is too small for the requested minimum per stratum"
        )

    remaining = sample_size - sum(quotas.values())
    while remaining:
        capacities = {
            key: counts[key] - quotas[key]
            for key in counts
            if counts[key] > quotas[key]
        }
        capacity_total = sum(capacities.values())
        if not capacity_total:
            break
        ideals = {
            key: remaining * capacity / capacity_total
            for key, capacity in capacities.items()
        }
        additions = {
            key: min(capacities[key], math.floor(ideal))
            for key, ideal in ideals.items()
        }
        added = sum(additions.values())
        for key, addition in additions.items():
            quotas[key] += addition
        remaining -= added
        if not remaining:
            break
        ranking = sorted(
            capacities,
            key=lambda key: (-(ideals[key] - math.floor(ideals[key])), key),
        )
        for key in ranking:
            if remaining == 0:
                break
            if quotas[key] < counts[key]:
                quotas[key] += 1
                remaining -= 1
    return quotas


def selection_score(seed: str, utterance_id: str) -> int:
    payload = f"{seed}\0{utterance_id}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


def main() -> int:
    args = parse_args()
    stratum_columns = tuple(
        column.strip() for column in args.stratum_columns.split(",") if column.strip()
    )
    if not stratum_columns:
        raise SystemExit("--stratum-columns must name at least one column")
    parquet = pq.ParquetFile(args.manifest)
    columns = [
        "id",
        "split",
        "audio_path",
        "json_path",
        "textgrid_path",
        "duration_seconds",
        "l1",
        "gender",
        "proficiency",
        "error_presence",
        "target_phone_intervals",
        "realized_phone_intervals",
        "phone_alignments",
    ]

    def eligible(row: dict) -> bool:
        return row["duration_seconds"] >= args.min_duration_seconds

    counts: Counter[tuple[str, ...]] = Counter()
    for batch in parquet.iter_batches(
        batch_size=8192, columns=[*stratum_columns, "duration_seconds"]
    ):
        counts.update(
            stratum_for(row, stratum_columns)
            for row in batch.to_pylist()
            if eligible(row)
        )
    quotas = allocate_quotas(counts, args.sample_size, args.minimum_per_stratum)

    selected: dict[tuple[str, ...], list[tuple[int, str, dict]]] = {
        key: [] for key in quotas
    }
    for batch in parquet.iter_batches(batch_size=4096, columns=columns):
        for row in batch.to_pylist():
            if not eligible(row):
                continue
            key = stratum_for(row, stratum_columns)
            score = selection_score(args.seed, row["id"])
            item = (-score, row["id"], row)
            heap = selected[key]
            if len(heap) < quotas[key]:
                heapq.heappush(heap, item)
            elif score < -heap[0][0]:
                heapq.heapreplace(heap, item)

    rows = []
    for key, heap in selected.items():
        for negative_score, _, row in heap:
            rows.append((key, -negative_score, row))
    rows.sort(key=lambda item: (item[0], item[1], item[2]["id"]))
    if len(rows) != args.sample_size:
        raise RuntimeError(f"selected {len(rows)} rows, expected {args.sample_size}")

    fields = [
        "audit_index",
        "utterance_id",
        "split",
        "l1",
        "gender",
        "proficiency",
        "error_presence",
        "duration_seconds",
        "target_phone_count",
        "realized_phone_count",
        "audio_path",
        "json_path",
        "textgrid_path",
        "alignment_detail_line",
        "reviewer_id",
        "alignment_correct",
        "maximum_boundary_error_ms",
        "incorrect_interval_count",
        "notes",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for index, (_, _, row) in enumerate(rows, 1):
            writer.writerow(
                {
                    "audit_index": index,
                    "utterance_id": row["id"],
                    "split": row["split"],
                    "l1": row["l1"],
                    "gender": row["gender"],
                    "proficiency": row["proficiency"],
                    "error_presence": str(row["error_presence"]).lower(),
                    "duration_seconds": f"{row['duration_seconds']:.6f}",
                    "target_phone_count": len(row["target_phone_intervals"] or []),
                    "realized_phone_count": len(row["realized_phone_intervals"] or []),
                    "audio_path": row["audio_path"],
                    "json_path": row["json_path"],
                    "textgrid_path": row["textgrid_path"],
                    "alignment_detail_line": index,
                    "reviewer_id": "",
                    "alignment_correct": "",
                    "maximum_boundary_error_ms": "",
                    "incorrect_interval_count": "",
                    "notes": "",
                }
            )

    args.details_output.parent.mkdir(parents=True, exist_ok=True)
    with args.details_output.open("w", encoding="utf-8") as handle:
        for index, (_, _, row) in enumerate(rows, 1):
            target_intervals = row["target_phone_intervals"] or []
            realized_intervals = row["realized_phone_intervals"] or []
            if args.recompute_alignments:
                alignments = [
                    alignment_to_dict(alignment)
                    for alignment in align_intervals(
                        to_intervals(target_intervals),
                        to_intervals(realized_intervals),
                        tolerance_seconds=0.03,
                        min_overlap_ratio=0.50,
                        preserve_one_to_many=False,
                    )
                ]
            else:
                alignments = row["phone_alignments"] or []
            detail = {
                "audit_index": index,
                "utterance_id": row["id"],
                "target_phone_intervals": target_intervals,
                "realized_phone_intervals": realized_intervals,
                "automatic_phone_alignments": alignments,
            }
            handle.write(json.dumps(detail, ensure_ascii=False) + "\n")

    metadata = {
        "schema_version": "aihub71490.manual_alignment_sample.v1",
        "manifest": str(args.manifest),
        "manifest_sha256": sha256_file(args.manifest),
        "worksheet": str(args.output),
        "worksheet_sha256": sha256_file(args.output),
        "alignment_details": str(args.details_output),
        "alignment_details_sha256": sha256_file(args.details_output),
        "sample_size": args.sample_size,
        "seed": args.seed,
        "min_duration_seconds": args.min_duration_seconds,
        "alignments_recomputed_with_current_align_intervals": (
            args.recompute_alignments
        ),
        "stratum_columns": list(stratum_columns),
        "minimum_per_stratum": args.minimum_per_stratum,
        "stratum_count": len(counts),
        "stratum_population_counts": {
            "|".join(key): counts[key] for key in sorted(counts)
        },
        "stratum_sample_quotas": {"|".join(key): quotas[key] for key in sorted(quotas)},
    }
    args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_output.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
