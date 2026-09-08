#!/usr/bin/env python3
"""Create conservative observed-phone CTC manifests from audited inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

import pyarrow.parquet as pq

from korean_phone_ctc.data.ctc_manifest import prepare_ctc_record
from korean_phone_ctc.data.phone_vocab import build_vocabulary, encode_phones
from korean_phone_ctc.io_utils import atomic_write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--raw-manifest", type=Path, required=True)
    parser.add_argument("--enriched-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-duration-seconds", type=float, default=20.0)
    return parser.parse_args()


def raw_records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"raw line {line_number} is not an object")
            yield value


def temporary_stream(path: Path) -> tuple[Path, TextIO]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    return Path(name), os.fdopen(descriptor, "w", encoding="utf-8")


def distribution_key(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    return str(value) if value not in (None, "") else "<missing>"


def main() -> int:
    args = parse_args()
    if args.max_duration_seconds <= 0:
        raise SystemExit("--max-duration-seconds must be positive")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {split: output_dir / f"{split}.jsonl" for split in ("train", "dev", "test")}
    temporary: dict[str, Path] = {}
    streams: dict[str, TextIO] = {}
    for split, path in paths.items():
        temporary[split], streams[split] = temporary_stream(path)

    parquet = pq.ParquetFile(args.enriched_manifest)
    columns = [
        "id",
        "speaker_id",
        "split",
        "duration_seconds",
        "sample_rate_hz",
        "pcm_sha256",
        "realized_phone_intervals",
        "error_presence",
        "qc_flags",
    ]
    raw_iterator = raw_records(args.raw_manifest)
    exclusions: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    split_duration: Counter[str] = Counter()
    split_speakers: dict[str, set[str]] = defaultdict(set)
    split_distributions: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    train_phone_sequences: list[list[str]] = []
    seen_pcm: set[str] = set()
    input_count = 0
    try:
        for batch in parquet.iter_batches(batch_size=4096, columns=columns):
            for enriched in batch.to_pylist():
                input_count += 1
                try:
                    raw = next(raw_iterator)
                except StopIteration as exc:
                    raise ValueError("raw manifest ended before Parquet") from exc
                prepared = prepare_ctc_record(
                    enriched,
                    raw,
                    max_duration_seconds=args.max_duration_seconds,
                )
                if prepared.record is None:
                    exclusions[prepared.exclusion_reason or "unknown"] += 1
                    continue
                record = prepared.record
                pcm_hash = record["pcm_sha256"]
                if pcm_hash in seen_pcm:
                    exclusions["duplicate_pcm"] += 1
                    continue
                seen_pcm.add(pcm_hash)
                split = record["split"]
                streams[split].write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                )
                split_counts[split] += 1
                split_duration[split] += record["duration_seconds"]
                split_speakers[split].add(record["speaker_id"])
                for key in (
                    "language",
                    "nationality",
                    "gender",
                    "proficiency",
                    "error_presence",
                ):
                    split_distributions[split][key][distribution_key(record, key)] += 1
                if split == "train":
                    train_phone_sequences.append(record["phones"])
        try:
            extra = next(raw_iterator)
        except StopIteration:
            extra = None
        if extra is not None:
            raise ValueError("raw manifest has records after Parquet ended")
    except BaseException:
        for stream in streams.values():
            stream.close()
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    else:
        for stream in streams.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        for split, path in paths.items():
            os.replace(temporary[split], path)

    vocabulary, frequencies = build_vocabulary(train_phone_sequences)
    atomic_write_json(output_dir / "vocab.json", vocabulary)
    atomic_write_json(
        output_dir / "phone_inventory.json",
        {
            "inventory": [
                token for token in vocabulary if token not in {"<pad>", "<unk>"}
            ],
            "frequencies": frequencies,
            "source": "normalized TextGrid phones tier, training split only",
        },
    )
    assignments = {
        speaker: split
        for split, speakers in split_speakers.items()
        for speaker in sorted(speakers)
    }
    atomic_write_json(output_dir / "speaker_split.json", assignments)
    overlaps = {
        "train_dev": len(split_speakers["train"] & split_speakers["dev"]),
        "train_test": len(split_speakers["train"] & split_speakers["test"]),
        "dev_test": len(split_speakers["dev"] & split_speakers["test"]),
    }
    example_phones = train_phone_sequences[0]
    report = {
        "schema_version": "korean_phone_ctc.preparation.v1",
        "dataset_root": str(args.dataset_root.resolve()),
        "raw_manifest": str(args.raw_manifest.resolve()),
        "enriched_manifest": str(args.enriched_manifest.resolve()),
        "max_duration_seconds": args.max_duration_seconds,
        "input_records": input_count,
        "included_records": sum(split_counts.values()),
        "excluded_records": sum(exclusions.values()),
        "exclusion_counts": dict(sorted(exclusions.items())),
        "split_record_counts": dict(sorted(split_counts.items())),
        "split_speaker_counts": {
            split: len(speakers) for split, speakers in sorted(split_speakers.items())
        },
        "split_duration_hours": {
            split: seconds / 3600 for split, seconds in sorted(split_duration.items())
        },
        "speaker_overlap_counts": overlaps,
        "speaker_disjoint": not any(overlaps.values()),
        "split_distributions": {
            split: {
                key: dict(sorted(counter.items()))
                for key, counter in sorted(distributions.items())
            }
            for split, distributions in sorted(split_distributions.items())
        },
        "vocab_size": len(vocabulary),
        "ctc_blank_token": "<pad>",
        "ctc_blank_id": vocabulary["<pad>"],
        "unknown_token": "<unk>",
        "unknown_id": vocabulary["<unk>"],
        "label_id_example": {
            "phones": example_phones,
            "ids": encode_phones(example_phones, vocabulary),
        },
        "source_split_mapping": {
            "train": "train",
            "stack": "train",
            "calibration": "train",
            "dev": "dev",
            "locked_test": "test",
        },
        "source_split_assignment_sha256": hashlib.sha256(
            json.dumps(
                sorted(assignments.items()),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "manual_alignment_gate": {
            "status": "pending",
            "required_samples": 240,
            "minimum_accuracy": 0.95,
            "training_allowed": False,
        },
    }
    atomic_write_json(output_dir / "preparation_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
