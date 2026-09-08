#!/usr/bin/env python3
"""Create the G0 enriched Parquet manifest for AI Hub dataset 71490."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from voice_coach.data import (
    align_intervals,
    load_aihub71490_annotations,
    parse_textgrid,
    pcm_sha256,
    stratified_group_split,
)
from voice_coach.data.aihub71490 import interval_to_dict


SPLIT_RATIOS = {
    "train": 0.50,
    "dev": 0.10,
    "stack": 0.10,
    "calibration": 0.10,
    "locked_test": 0.20,
}

INTERVAL_TYPE = pa.list_(
    pa.struct(
        [
            pa.field("start", pa.float64()),
            pa.field("end", pa.float64()),
            pa.field("label", pa.string()),
        ]
    )
)
ALIGNMENT_TYPE = pa.list_(
    pa.struct(
        [
            pa.field("target_label", pa.string()),
            pa.field("realized_label", pa.string()),
            pa.field("start", pa.float64()),
            pa.field("end", pa.float64()),
            pa.field("overlap_seconds", pa.float64()),
            pa.field("overlap_ratio", pa.float64()),
            pa.field("operation", pa.string()),
        ]
    )
)
SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.string()),
        pa.field("id", pa.string()),
        pa.field("speaker_id", pa.string()),
        pa.field("split", pa.string()),
        pa.field("official_partitions", pa.list_(pa.string())),
        pa.field("audio_path", pa.string()),
        pa.field("json_path", pa.string()),
        pa.field("textgrid_path", pa.string()),
        pa.field("duration_seconds", pa.float64()),
        pa.field("sample_rate_hz", pa.int32()),
        pa.field("channels", pa.int16()),
        pa.field("pcm_sha256", pa.string()),
        pa.field("l1", pa.string()),
        pa.field("gender", pa.string()),
        pa.field("proficiency", pa.string()),
        pa.field("error_presence", pa.bool_()),
        pa.field("word_intervals", INTERVAL_TYPE),
        pa.field("target_phone_intervals", INTERVAL_TYPE),
        pa.field("realized_phone_intervals", INTERVAL_TYPE),
        pa.field("error_intervals", INTERVAL_TYPE),
        pa.field("phone_alignments", ALIGNMENT_TYPE),
        pa.field("qc_flags", pa.list_(pa.string())),
    ]
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-manifest", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qc-output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--progress-every", type=int, default=5000)
    return parser.parse_args()


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number} is not a JSON object")
            yield value


def nested_metadata(record: dict[str, Any]) -> tuple[str, str, str]:
    metadata = record.get("json_metadata")
    speaker = metadata.get("SpeakerMetadata", {}) if isinstance(metadata, dict) else {}
    if not isinstance(speaker, dict):
        speaker = {}
    return tuple(
        str(speaker.get(key, "<missing>"))
        for key in ("language", "gender", "proficiency")
    )


def build_assignments(
    manifest: Path, seed: int
) -> tuple[dict[str, str], str, int]:
    strata_records: list[dict[str, Any]] = []
    records = 0
    for record in read_jsonl(manifest):
        l1, gender, proficiency = nested_metadata(record)
        strata_records.append(
            {
                "speaker_id": record.get("speaker_id"),
                "language": l1,
                "gender": gender,
                "proficiency": proficiency,
                "error_presence": bool(
                    record.get("annotation_counts", {}).get("error_tags", 0)
                ),
            }
        )
        records += 1
    assignments = stratified_group_split(
        strata_records,
        group_key="speaker_id",
        stratify_keys=["language", "gender", "proficiency", "error_presence"],
        ratios=SPLIT_RATIOS,
        seed=seed,
    )
    canonical = json.dumps(
        sorted(assignments.items()),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return assignments, hashlib.sha256(canonical).hexdigest(), records


def resolve(root: Path, relative: str | None) -> Path | None:
    return root.joinpath(*Path(relative).parts) if relative else None


def tier_casefold(
    tiers: dict[str, list[Any]], name: str
) -> list[Any]:
    for tier_name, intervals in tiers.items():
        if tier_name.casefold() == name.casefold():
            return intervals
    return []


def in_bounds(intervals: list[Any], duration: float) -> bool:
    return all(
        interval.start >= 0
        and interval.end >= interval.start
        and interval.end <= duration + 1e-6
        for interval in intervals
    )


def alignment_dict(alignment: Any) -> dict[str, Any]:
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


def enrich_record(
    record: dict[str, Any],
    root: Path,
    assignments: dict[str, str],
) -> dict[str, Any]:
    paths = record.get("source_paths", {})
    audio_relative = paths.get("wav")
    json_relative = paths.get("json")
    textgrid_relative = paths.get("textgrid")
    audio_path = resolve(root, audio_relative)
    json_path = resolve(root, json_relative)
    textgrid_path = resolve(root, textgrid_relative)
    flags: list[str] = []
    annotations = {"words": [], "target_phones": [], "error_tags": []}
    realized_phones: list[Any] = []

    try:
        if json_path is None:
            raise ValueError("missing JSON path")
        annotations = load_aihub71490_annotations(json_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        flags.append(f"annotation_parse_error:{type(exc).__name__}")
    try:
        if textgrid_path is None:
            raise ValueError("missing TextGrid path")
        realized_phones = tier_casefold(parse_textgrid(textgrid_path), "phones")
        if not realized_phones:
            flags.append("missing_realized_phone_tier")
    except (OSError, UnicodeError, ValueError) as exc:
        flags.append(f"textgrid_parse_error:{type(exc).__name__}")

    audio = record.get("audio") or {}
    duration = float(audio.get("duration_seconds") or 0.0)
    all_intervals = (
        annotations["words"]
        + annotations["target_phones"]
        + annotations["error_tags"]
        + realized_phones
    )
    if not in_bounds(all_intervals, duration):
        flags.append("interval_out_of_bounds")
    try:
        if audio_path is None:
            raise ValueError("missing WAV path")
        pcm_hash = pcm_sha256(audio_path)
    except (OSError, EOFError, ValueError) as exc:
        pcm_hash = None
        flags.append(f"pcm_hash_error:{type(exc).__name__}")

    alignments = align_intervals(
        annotations["target_phones"],
        realized_phones,
        tolerance_seconds=0.03,
        min_overlap_ratio=0.50,
        # A target phone may be within the tolerance window of both adjacent
        # realized phones. Keep only the strongest unused overlap so shared
        # boundaries do not become duplicate substitutions.
        preserve_one_to_many=False,
    )
    l1, gender, proficiency = nested_metadata(record)
    speaker_id = record.get("speaker_id")
    return {
        "schema_version": "aihub71490.enriched.v1",
        "id": record.get("id"),
        "speaker_id": speaker_id,
        "split": assignments.get(str(speaker_id)),
        "official_partitions": record.get("official_partitions") or [],
        "audio_path": audio_relative,
        "json_path": json_relative,
        "textgrid_path": textgrid_relative,
        "duration_seconds": duration,
        "sample_rate_hz": audio.get("sample_rate_hz"),
        "channels": audio.get("channels"),
        "pcm_sha256": pcm_hash,
        "l1": l1,
        "gender": gender,
        "proficiency": proficiency,
        "error_presence": bool(annotations["error_tags"]),
        "word_intervals": [interval_to_dict(x) for x in annotations["words"]],
        "target_phone_intervals": [
            interval_to_dict(x) for x in annotations["target_phones"]
        ],
        "realized_phone_intervals": [
            interval_to_dict(x) for x in realized_phones
        ],
        "error_intervals": [
            interval_to_dict(x) for x in annotations["error_tags"]
        ],
        "phone_alignments": [alignment_dict(x) for x in alignments],
        "qc_flags": flags,
    }


def main() -> int:
    args = parse_args()
    if args.batch_size <= 0 or args.progress_every <= 0:
        raise SystemExit("batch-size and progress-every must be positive")
    root = args.dataset_root.resolve()
    assignments, split_hash, expected_records = build_assignments(
        args.raw_manifest, args.seed
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.qc_output.parent.mkdir(parents=True, exist_ok=True)

    writer = pq.ParquetWriter(args.output, SCHEMA, compression="zstd")
    batch: list[dict[str, Any]] = []
    records = 0
    flag_counts: Counter[str] = Counter()
    operation_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    pcm_split: dict[str, str] = {}
    cross_split_pcm: list[dict[str, str]] = []
    try:
        for raw in read_jsonl(args.raw_manifest):
            row = enrich_record(raw, root, assignments)
            batch.append(row)
            records += 1
            flag_counts.update(row["qc_flags"])
            operation_counts.update(
                item["operation"] for item in row["phone_alignments"]
            )
            split_counts[row["split"]] += 1
            pcm_hash = row["pcm_sha256"]
            if pcm_hash:
                previous = pcm_split.setdefault(pcm_hash, row["split"])
                if previous != row["split"] and len(cross_split_pcm) < 100:
                    cross_split_pcm.append(
                        {
                            "pcm_sha256": pcm_hash,
                            "first_split": previous,
                            "second_split": row["split"],
                        }
                    )
            if len(batch) >= args.batch_size:
                writer.write_table(pa.Table.from_pylist(batch, schema=SCHEMA))
                batch.clear()
            if records % args.progress_every == 0:
                print(f"Enriched records: {records:,}", flush=True)
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=SCHEMA))
    finally:
        writer.close()

    pairing_failures = sum(
        count
        for flag, count in flag_counts.items()
        if flag == "missing_realized_phone_tier"
        or flag.startswith("annotation_parse_error:")
        or flag.startswith("textgrid_parse_error:")
    )
    decoder_failures = sum(
        count
        for flag, count in flag_counts.items()
        if flag.startswith("pcm_hash_error:")
    )
    time_bound_errors = flag_counts["interval_out_of_bounds"]
    pairing_rate = (records - pairing_failures) / records
    decoder_failure_rate = decoder_failures / records
    time_bound_error_rate = time_bound_errors / records
    failed_gates: list[str] = []
    if records != expected_records:
        failed_gates.append("record_count")
    if pairing_rate < 0.99:
        failed_gates.append("pairing_rate")
    if decoder_failure_rate > 0.001:
        failed_gates.append("decoder_failure_rate")
    if time_bound_error_rate > 0.01:
        failed_gates.append("time_bound_error_rate")
    if any(
        flag.startswith("annotation_parse_error:") for flag in flag_counts
    ):
        failed_gates.append("annotation_parse_failure")
    if cross_split_pcm:
        failed_gates.append("cross_split_pcm_duplicate")
    qc = {
        "schema_version": "aihub71490.g0.v1",
        "status": "passed" if not failed_gates else "failed",
        "record_count": records,
        "expected_record_count": expected_records,
        "speaker_count": len(assignments),
        "split_counts": dict(sorted(split_counts.items())),
        "split_assignment_sha256": split_hash,
        "pairing_rate": pairing_rate,
        "decoder_failure_rate": decoder_failure_rate,
        "time_bound_error_rate": time_bound_error_rate,
        "qc_flag_counts": dict(sorted(flag_counts.items())),
        "phone_alignment_operations": dict(sorted(operation_counts.items())),
        "cross_split_pcm_duplicate_examples": cross_split_pcm,
        "failed_gates": failed_gates,
    }
    args.qc_output.write_text(
        json.dumps(qc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(qc, ensure_ascii=False, indent=2))
    return 0 if qc["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
