#!/usr/bin/env python3
"""Join G0-enriched rows with modeling labels from the raw manifest.

The output deliberately retains restricted source paths so downstream feature
builders can decode audio.  It is therefore an access-controlled operational
manifest, not a publication-safe artifact.  Exportable feature caches must
replace the source stem with a namespaced hash and omit all source paths.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import unicodedata

import pyarrow as pa
import pyarrow.parquet as pq


ADDED_SCHEMA = pa.schema(
    [
        pa.field("prompt_sha256", pa.string(), nullable=False),
        pa.field("prompt_length_chars", pa.int32(), nullable=False),
        pa.field("sound_environment", pa.string()),
        pa.field("recording_level", pa.string()),
        pa.field("topik_level", pa.int16()),
        pa.field("pronunciation_score", pa.int8()),
        pa.field("fluency_score", pa.int8()),
        pa.field("comprehensibility_score", pa.int8()),
    ]
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enriched-manifest", type=Path, required=True)
    parser.add_argument("--raw-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--progress-every", type=int, default=25_000)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_prompt(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    # JSON numbers may arrive as floats, but silently truncating a fractional
    # value (or treating a JSON boolean as 0/1) would create a false label.
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def optional_score(value: object) -> int | None:
    parsed = optional_int(value)
    return parsed if parsed is not None and 1 <= parsed <= 5 else None


def extract_model_fields(raw: dict) -> dict:
    metadata = raw.get("json_metadata") or {}
    speaker = metadata.get("SpeakerMetadata") or {}
    recording = metadata.get("RecordingMetadata") or {}
    evaluation = metadata.get("EvaluationMetadata") or {}
    prompt = normalize_prompt(recording.get("prompt"))
    return {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_length_chars": len(prompt),
        "sound_environment": recording.get("sound_environment"),
        "recording_level": speaker.get("recording_level"),
        "topik_level": optional_int(speaker.get("topik_level")),
        # The official human-rating scale is 1–5. Zero is an unrated sentinel,
        # not a valid outcome, and must never be learned as the lowest score.
        "pronunciation_score": optional_score(evaluation.get("PronunProfEval")),
        "fluency_score": optional_score(evaluation.get("FluencyEval")),
        "comprehensibility_score": optional_score(
            evaluation.get("ComprehendEval")
        ),
    }


def main() -> int:
    args = parse_args()
    if args.batch_size <= 0:
        raise ValueError("batch size must be positive")
    parquet = pq.ParquetFile(args.enriched_manifest)
    output_schema = pa.schema(list(parquet.schema_arrow) + list(ADDED_SCHEMA))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")

    records = 0
    split_counts: Counter[str] = Counter()
    prompt_counts: Counter[str] = Counter()
    score_counts = {
        "pronunciation_score": Counter(),
        "fluency_score": Counter(),
        "comprehensibility_score": Counter(),
    }
    missing_scores = Counter()
    writer = pq.ParquetWriter(
        temporary,
        output_schema,
        compression="zstd",
        use_dictionary=True,
    )
    try:
        with args.raw_manifest.open(encoding="utf-8") as raw_handle:
            for batch in parquet.iter_batches(batch_size=args.batch_size):
                output_rows = []
                for enriched in batch.to_pylist():
                    raw_line = raw_handle.readline()
                    if not raw_line:
                        raise ValueError(
                            "raw manifest ended before enriched manifest"
                        )
                    raw = json.loads(raw_line)
                    if raw.get("id") != enriched.get("id"):
                        raise ValueError(
                            "manifest order/id mismatch: "
                            f"{raw.get('id')} != {enriched.get('id')}"
                        )
                    fields = extract_model_fields(raw)
                    row = {**enriched, **fields}
                    output_rows.append(row)
                    records += 1
                    split_counts[str(enriched.get("split"))] += 1
                    prompt_counts[fields["prompt_sha256"]] += 1
                    for name, counts in score_counts.items():
                        value = fields[name]
                        if value is None:
                            missing_scores[name] += 1
                        else:
                            counts[str(value)] += 1
                    if args.progress_every and records % args.progress_every == 0:
                        print(f"Modeling records: {records:,}", flush=True)
                writer.write_table(
                    pa.Table.from_pylist(output_rows, schema=output_schema)
                )
            if raw_handle.readline():
                raise ValueError("raw manifest contains trailing records")
    finally:
        writer.close()
    temporary.replace(args.output)

    report = {
        "schema_version": "aihub71490.modeling.report.v1",
        "record_count": records,
        "column_count": len(output_schema),
        "unique_prompt_count": len(prompt_counts),
        "duplicate_prompt_record_count": sum(
            count for count in prompt_counts.values() if count > 1
        ),
        "split_counts": dict(sorted(split_counts.items())),
        "score_distributions": {
            name: dict(sorted(counts.items()))
            for name, counts in score_counts.items()
        },
        "missing_score_counts": dict(sorted(missing_scores.items())),
        "enriched_manifest_sha256": sha256_file(args.enriched_manifest),
        "raw_manifest_sha256": sha256_file(args.raw_manifest),
        "modeling_manifest_sha256": sha256_file(args.output),
    }
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
