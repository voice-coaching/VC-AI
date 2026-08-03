#!/usr/bin/env python3
"""Build privacy-safe, train/dev-only explicit feature caches.

The utterance cache contains waveform features and privacy-reviewed metadata.
The optional phone cache uses the target (gold) phone boundaries from the
AI Hub annotation.  It is therefore an oracle-alignment diagnostic baseline,
not a deployable feature extractor.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from voice_coach.data.audio import DecodedAudio, decode_audio
from voice_coach.features.explicit import extract_explicit_features, npvi


SCHEMA_VERSION = "voice_coach.explicit_utterance_features.v1"
PHONE_SCHEMA_VERSION = "voice_coach.explicit_phone_features.v1"
REPORT_SCHEMA_VERSION = "voice_coach.explicit_feature_cache_report.v1"
DEFAULT_SPLITS = ("train", "dev")
EVALUATION_SPLITS = frozenset({"stack", "calibration", "locked_test"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
UTTERANCE_HASH_NAMESPACE = "voice-coach.aihub71490.utterance.v1"

UTTERANCE_SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("oracle_alignment", pa.bool_(), nullable=False),
        pa.field("utterance_id_sha256", pa.string(), nullable=False),
        pa.field("speaker_id_sha256", pa.string(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("prompt_sha256", pa.string(), nullable=False),
        pa.field("l1", pa.string()),
        pa.field("gender", pa.string()),
        pa.field("proficiency", pa.string()),
        pa.field("sound_environment", pa.string()),
        pa.field("recording_level", pa.string()),
        pa.field("topik_level", pa.int16()),
        pa.field("model_sample_rate_hz", pa.int32(), nullable=False),
        pa.field("source_sample_rate_hz", pa.int32(), nullable=False),
        pa.field("source_channels", pa.int16(), nullable=False),
        pa.field("audio_duration_seconds", pa.float64(), nullable=False),
        pa.field("rms", pa.float64(), nullable=False),
        pa.field("peak", pa.float64(), nullable=False),
        pa.field("clipping_ratio", pa.float64(), nullable=False),
        pa.field("zero_crossing_rate", pa.float64(), nullable=False),
        pa.field("pause_count", pa.int32(), nullable=False),
        pa.field("pause_ratio", pa.float64(), nullable=False),
        pa.field("voiced_ratio", pa.float64(), nullable=False),
        pa.field("f0_median_hz", pa.float64()),
        pa.field("f0_range_hz", pa.float64()),
        pa.field("word_count", pa.int32(), nullable=False),
        pa.field("target_phone_count", pa.int32(), nullable=False),
        pa.field("speech_rate_words_per_second", pa.float64(), nullable=False),
        pa.field(
            "articulation_rate_words_per_second",
            pa.float64(),
        ),
        pa.field("speech_rate_phones_per_second", pa.float64(), nullable=False),
        pa.field(
            "articulation_rate_phones_per_second",
            pa.float64(),
        ),
        pa.field("word_label_duration_total_seconds", pa.float64(), nullable=False),
        pa.field("word_label_duration_mean_seconds", pa.float64()),
        pa.field("word_label_duration_std_seconds", pa.float64()),
        pa.field("word_label_duration_npvi", pa.float64()),
        pa.field(
            "target_phone_label_duration_total_seconds",
            pa.float64(),
            nullable=False,
        ),
        pa.field("target_phone_label_duration_mean_seconds", pa.float64()),
        pa.field("target_phone_label_duration_std_seconds", pa.float64()),
        pa.field("target_phone_label_duration_npvi", pa.float64()),
        pa.field("pronunciation_score", pa.int8()),
        pa.field("fluency_score", pa.int8()),
        pa.field("comprehensibility_score", pa.int8()),
    ]
)

PHONE_SCHEMA = pa.schema(
    [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("oracle_alignment", pa.bool_(), nullable=False),
        pa.field("utterance_id_sha256", pa.string(), nullable=False),
        pa.field("speaker_id_sha256", pa.string(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("phone_index", pa.int32(), nullable=False),
        pa.field("target_label", pa.string(), nullable=False),
        pa.field("previous_target_label", pa.string()),
        pa.field("next_target_label", pa.string()),
        pa.field("binary_error_label", pa.int8(), nullable=False),
        pa.field("oracle_start_seconds", pa.float64(), nullable=False),
        pa.field("oracle_end_seconds", pa.float64(), nullable=False),
        pa.field("oracle_duration_seconds", pa.float64(), nullable=False),
        pa.field("segment_rms", pa.float64(), nullable=False),
        pa.field("segment_zero_crossing_rate", pa.float64(), nullable=False),
        pa.field("l1", pa.string()),
        pa.field("gender", pa.string()),
        pa.field("proficiency", pa.string()),
    ]
)

# These columns are intentionally not exported.  Keep this list in the report
# so a downstream reviewer can verify that the cache is not a disguised copy
# of restricted paths or raw annotations.
PROHIBITED_OUTPUT_COLUMNS = frozenset(
    {
        "audio_path",
        "id",
        "utterance_id",
        "json_path",
        "textgrid_path",
        "prompt",
        "json_metadata",
        "source_paths",
        "pcm_sha256",
        "UserID",
        "nationality",
        "word_intervals",
        "target_phone_intervals",
        "phone_alignments",
        "qc_flags",
        "realized_label",
        "alignment_operation",
        "error_count",
        "error_tags",
    }
)
UTTERANCE_INPUT_FEATURE_COLUMNS = (
    "audio_duration_seconds",
    "rms",
    "peak",
    "clipping_ratio",
    "zero_crossing_rate",
    "pause_count",
    "pause_ratio",
    "voiced_ratio",
    "f0_median_hz",
    "f0_range_hz",
    "word_count",
    "target_phone_count",
    "speech_rate_words_per_second",
    "articulation_rate_words_per_second",
    "speech_rate_phones_per_second",
    "articulation_rate_phones_per_second",
    "word_label_duration_total_seconds",
    "word_label_duration_mean_seconds",
    "word_label_duration_std_seconds",
    "word_label_duration_npvi",
    "target_phone_label_duration_total_seconds",
    "target_phone_label_duration_mean_seconds",
    "target_phone_label_duration_std_seconds",
    "target_phone_label_duration_npvi",
)
PHONE_INPUT_FEATURE_COLUMNS = (
    "target_label",
    "previous_target_label",
    "next_target_label",
    "oracle_duration_seconds",
    "segment_rms",
    "segment_zero_crossing_rate",
)
LABEL_COLUMNS = (
    "pronunciation_score",
    "fluency_score",
    "comprehensibility_score",
)
SLICE_ONLY_COLUMNS = (
    "split",
    "l1",
    "gender",
    "proficiency",
    "sound_environment",
    "recording_level",
    "topik_level",
)


@dataclass(slots=True)
class FeatureBuildError(Exception):
    stage: str
    utterance_id_sha256: str | None
    cause_type: str

    def __str__(self) -> str:
        identifier = self.utterance_id_sha256 or "<unknown>"
        return (
            f"feature extraction failed at {self.stage} for {identifier} "
            f"({self.cause_type})"
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def hash_utterance_id(source_id: object) -> str:
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("manifest row is missing id")
    payload = f"{UTTERANCE_HASH_NAMESPACE}:{source_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def resolve_audio_path(dataset_root: Path, relative_path: object) -> Path:
    """Resolve a manifest path without allowing absolute/traversal escapes."""

    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("audio_path must be a non-empty relative path")
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError("audio_path must be relative to dataset_root")
    root = dataset_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("audio_path escapes dataset_root") from exc
    return candidate


def load_reserved_prompt_hashes(path: Path) -> tuple[set[str], str]:
    document = json.loads(path.read_text(encoding="utf-8"))
    try:
        source_sha256 = document["source"]["modeling_manifest_sha256"]
        entries = document["reserved_prompts"]
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid prompt reservation artifact") from exc
    if not isinstance(source_sha256, str) or not SHA256_PATTERN.fullmatch(
        source_sha256
    ):
        raise ValueError("reservation source manifest SHA-256 is invalid")
    if not isinstance(entries, list) or not entries:
        raise ValueError("reservation artifact has no reserved prompts")
    reserved: set[str] = set()
    for index, entry in enumerate(entries):
        prompt_hash = (
            entry.get("prompt_sha256") if isinstance(entry, Mapping) else None
        )
        if not isinstance(prompt_hash, str) or not SHA256_PATTERN.fullmatch(
            prompt_hash
        ):
            raise ValueError(
                f"reserved_prompts[{index}].prompt_sha256 is invalid"
            )
        reserved.add(prompt_hash)
    if len(reserved) != len(entries):
        raise ValueError("reservation artifact contains duplicate prompt hashes")
    return reserved, source_sha256


def _clean_intervals(values: object) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    output = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        label = str(value.get("label") or "").strip()
        if not label:
            continue
        start = float(value["start"])
        end = float(value["end"])
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end):
            raise ValueError("label interval has invalid bounds")
        output.append({"start": start, "end": end, "label": label})
    return output


def _duration_statistics(
    intervals: Sequence[Mapping[str, Any]],
) -> dict[str, float | None]:
    durations = np.asarray(
        [
            float(interval["end"]) - float(interval["start"])
            for interval in intervals
        ],
        dtype=np.float64,
    )
    positive = durations[durations > 0]
    return {
        "total": float(np.sum(durations)) if durations.size else 0.0,
        "mean": float(np.mean(durations)) if durations.size else None,
        "std": float(np.std(durations)) if durations.size else None,
        "npvi": npvi(positive) if positive.size >= 2 else None,
    }


def _rate(count: int, duration: float) -> float | None:
    return count / duration if duration > 0 else None


def _segment_features(
    audio: DecodedAudio,
    *,
    start_seconds: float,
    end_seconds: float,
) -> tuple[float, float]:
    start_index = max(0, min(audio.samples.size, round(start_seconds * 16_000)))
    end_index = max(start_index, min(audio.samples.size, round(end_seconds * 16_000)))
    segment = audio.samples[start_index:end_index]
    if segment.size == 0:
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean(np.square(segment, dtype=np.float64))))
    signs = np.signbit(segment)
    zcr = float(np.mean(signs[1:] != signs[:-1])) if segment.size > 1 else 0.0
    return rms, zcr


def _target_alignments(
    row: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    values = row.get("phone_alignments")
    if not isinstance(values, list):
        raise ValueError("phone_alignments must be a list")
    alignments = [
        value
        for value in values
        if isinstance(value, Mapping) and value.get("target_label") is not None
    ]
    if len(alignments) != len(targets):
        raise ValueError(
            "target_phone_intervals and target-bearing alignments differ"
        )
    allowed_operations = {"match", "substitution", "deletion"}
    for index, (target, alignment) in enumerate(
        zip(targets, alignments, strict=True)
    ):
        if str(alignment.get("target_label")) != str(target["label"]):
            raise ValueError(f"target alignment label mismatch at index {index}")
        if alignment.get("operation") not in allowed_operations:
            raise ValueError(f"invalid target alignment operation at index {index}")
    return alignments


def extract_feature_rows(
    row: Mapping[str, Any],
    *,
    dataset_root: Path,
    include_phone_rows: bool,
    compute_f0: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extract one utterance and its optional oracle-boundary phone rows."""

    utterance_id_sha256 = hash_utterance_id(row.get("id"))
    speaker_id_sha256 = row.get("speaker_id")
    prompt_sha256 = row.get("prompt_sha256")
    if (
        not isinstance(speaker_id_sha256, str)
        or not SHA256_PATTERN.fullmatch(speaker_id_sha256)
    ):
        raise ValueError("speaker_id is not a privacy-safe SHA-256 hash")
    if (
        not isinstance(prompt_sha256, str)
        or not SHA256_PATTERN.fullmatch(prompt_sha256)
    ):
        raise ValueError("prompt_sha256 is invalid")
    audio = decode_audio(
        resolve_audio_path(dataset_root, row.get("audio_path")),
        target_sample_rate=16_000,
    )
    waveform = extract_explicit_features(
        audio.samples,
        audio.sample_rate,
        compute_f0=compute_f0,
    )
    words = _clean_intervals(row.get("word_intervals"))
    targets = _clean_intervals(row.get("target_phone_intervals"))
    word_stats = _duration_statistics(words)
    target_stats = _duration_statistics(targets)
    duration = audio.duration_seconds

    utterance = {
        "schema_version": SCHEMA_VERSION,
        # Label durations and rates below use gold annotation boundaries.
        "oracle_alignment": True,
        "utterance_id_sha256": utterance_id_sha256,
        "speaker_id_sha256": speaker_id_sha256,
        "split": str(row.get("split") or ""),
        "prompt_sha256": prompt_sha256,
        "l1": row.get("l1"),
        "gender": row.get("gender"),
        "proficiency": row.get("proficiency"),
        "sound_environment": row.get("sound_environment"),
        "recording_level": row.get("recording_level"),
        "topik_level": row.get("topik_level"),
        "model_sample_rate_hz": audio.sample_rate,
        "source_sample_rate_hz": audio.source_sample_rate,
        "source_channels": audio.source_channels,
        "audio_duration_seconds": duration,
        **waveform.to_dict(),
        "word_count": len(words),
        "target_phone_count": len(targets),
        "speech_rate_words_per_second": len(words) / duration,
        "articulation_rate_words_per_second": _rate(
            len(words), float(word_stats["total"])
        ),
        "speech_rate_phones_per_second": len(targets) / duration,
        "articulation_rate_phones_per_second": _rate(
            len(targets), float(target_stats["total"])
        ),
        "word_label_duration_total_seconds": word_stats["total"],
        "word_label_duration_mean_seconds": word_stats["mean"],
        "word_label_duration_std_seconds": word_stats["std"],
        "word_label_duration_npvi": word_stats["npvi"],
        "target_phone_label_duration_total_seconds": target_stats["total"],
        "target_phone_label_duration_mean_seconds": target_stats["mean"],
        "target_phone_label_duration_std_seconds": target_stats["std"],
        "target_phone_label_duration_npvi": target_stats["npvi"],
        "pronunciation_score": row.get("pronunciation_score"),
        "fluency_score": row.get("fluency_score"),
        "comprehensibility_score": row.get("comprehensibility_score"),
    }

    phone_rows: list[dict[str, Any]] = []
    if include_phone_rows:
        alignments = _target_alignments(row, targets)
        for index, (target, alignment) in enumerate(
            zip(targets, alignments, strict=True)
        ):
            start = float(target["start"])
            end = float(target["end"])
            # Polyphase resampling rounds to a whole 16 kHz sample.  Permit one
            # model-sample of endpoint quantization, but no annotation overrun.
            if end > duration + (1 / audio.sample_rate) + 1e-6:
                raise ValueError("oracle phone boundary exceeds decoded audio")
            rms, zcr = _segment_features(
                audio,
                start_seconds=start,
                end_seconds=end,
            )
            operation = str(alignment["operation"])
            phone_rows.append(
                {
                    "schema_version": PHONE_SCHEMA_VERSION,
                    "oracle_alignment": True,
                    "utterance_id_sha256": utterance_id_sha256,
                    "speaker_id_sha256": utterance["speaker_id_sha256"],
                    "split": utterance["split"],
                    "phone_index": index,
                    "target_label": target["label"],
                    "previous_target_label": (
                        targets[index - 1]["label"] if index else None
                    ),
                    "next_target_label": (
                        targets[index + 1]["label"]
                        if index + 1 < len(targets)
                        else None
                    ),
                    "binary_error_label": int(operation != "match"),
                    "oracle_start_seconds": start,
                    "oracle_end_seconds": end,
                    "oracle_duration_seconds": end - start,
                    "segment_rms": rms,
                    "segment_zero_crossing_rate": zcr,
                    "l1": row.get("l1"),
                    "gender": row.get("gender"),
                    "proficiency": row.get("proficiency"),
                }
            )
    return utterance, phone_rows


def _extract_feature_rows_worker(
    task: tuple[dict[str, Any], str, bool, bool],
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    str | None,
    str | None,
]:
    """Pickle-safe worker that never returns a restricted source path."""

    row, dataset_root, include_phone_rows, compute_f0 = task
    try:
        utterance, phones = extract_feature_rows(
            row,
            dataset_root=Path(dataset_root),
            include_phone_rows=include_phone_rows,
            compute_f0=compute_f0,
        )
        return utterance, phones, None, None
    except Exception as exc:
        source_id = row.get("id")
        identifier = hash_utterance_id(source_id) if source_id else None
        return None, [], identifier, type(exc).__name__


def schema_with_metadata(
    schema: pa.Schema,
    *,
    source_manifest_sha256: str,
    allowed_splits: Sequence[str],
) -> pa.Schema:
    if schema == PHONE_SCHEMA:
        input_features = list(PHONE_INPUT_FEATURE_COLUMNS)
        label_columns = ["binary_error_label"]
        slice_only_columns = ["split", "l1", "gender", "proficiency"]
    else:
        input_features = list(UTTERANCE_INPUT_FEATURE_COLUMNS)
        label_columns = list(LABEL_COLUMNS)
        slice_only_columns = list(SLICE_ONLY_COLUMNS)
    missing_role_columns = (
        set(input_features) | set(label_columns) | set(slice_only_columns)
    ) - set(schema.names)
    if missing_role_columns:
        raise AssertionError(
            "feature role metadata references missing columns: "
            + ", ".join(sorted(missing_role_columns))
        )
    metadata = {
        b"voice_coach.schema_version": (
            PHONE_SCHEMA_VERSION
            if schema == PHONE_SCHEMA
            else SCHEMA_VERSION
        ).encode("ascii"),
        b"voice_coach.oracle_alignment": b"true",
        b"voice_coach.privacy_safe": b"true",
        b"voice_coach.source_manifest_sha256": source_manifest_sha256.encode(
            "ascii"
        ),
        b"voice_coach.allowed_splits": ",".join(allowed_splits).encode("ascii"),
        b"voice_coach.metric_evaluation_performed": b"false",
        b"voice_coach.input_feature_columns": json.dumps(
            input_features,
            separators=(",", ":"),
        ).encode("utf-8"),
        b"voice_coach.label_columns": json.dumps(
            label_columns,
            separators=(",", ":"),
        ).encode("utf-8"),
        b"voice_coach.slice_only_columns": json.dumps(
            slice_only_columns,
            separators=(",", ":"),
        ).encode("utf-8"),
        b"voice_coach.forbidden_as_input": json.dumps(
            [
                "binary_error_label",
                "pronunciation_score",
                "fluency_score",
                "comprehensibility_score",
                "realized_label",
                "alignment_operation",
                "error_intervals",
                "error_tags",
                "error_count",
            ],
            separators=(",", ":"),
        ).encode("utf-8"),
    }
    return schema.with_metadata(metadata)


class PhoneShardWriter:
    def __init__(
        self,
        output: Path,
        schema: pa.Schema,
        *,
        shard_size: int,
    ) -> None:
        if shard_size <= 0:
            raise ValueError("phone shard size must be positive")
        if output.exists():
            raise FileExistsError(f"phone output already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        self.output = output
        self.temporary = output.with_name(
            f".{output.name}.part-{uuid4().hex}"
        )
        self.temporary.mkdir()
        self.schema = schema
        self.shard_size = shard_size
        self.buffer: list[dict[str, Any]] = []
        self.shard_paths: list[Path] = []

    def append(self, rows: Iterable[dict[str, Any]]) -> None:
        self.buffer.extend(rows)
        while len(self.buffer) >= self.shard_size:
            self._flush(self.shard_size)

    def _flush(self, count: int) -> None:
        rows = self.buffer[:count]
        del self.buffer[:count]
        final = self.temporary / f"part-{len(self.shard_paths):05d}.parquet"
        temporary = final.with_suffix(".parquet.part")
        pq.write_table(
            pa.Table.from_pylist(rows, schema=self.schema),
            temporary,
            compression="zstd",
            use_dictionary=True,
        )
        os.replace(temporary, final)
        self.shard_paths.append(final)

    def finalize(self) -> list[dict[str, Any]]:
        if self.buffer:
            self._flush(len(self.buffer))
        os.replace(self.temporary, self.output)
        results = []
        for path in sorted(self.output.glob("part-*.parquet")):
            parquet = pq.ParquetFile(path)
            results.append(
                {
                    "path": path.name,
                    "row_count": parquet.metadata.num_rows,
                    "sha256": sha256_file(path),
                }
            )
        return results

    def abort(self) -> None:
        shutil.rmtree(self.temporary, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build train/dev explicit features without reading evaluation "
            "metrics or exporting restricted source paths."
        )
    )
    parser.add_argument("--modeling-manifest", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--prompt-reservation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument(
        "--phone-output",
        type=Path,
        help="Optional output directory for sharded oracle-boundary phone rows",
    )
    parser.add_argument(
        "--canonical-utterance-cache",
        type=Path,
        help=(
            "For a phone-cache run, identify an existing canonical utterance "
            "cache. The required --output is then marked as an incidental "
            "duplicate (for example, a cheap profile with F0 disabled)."
        ),
    )
    parser.add_argument(
        "--splits",
        nargs="+",
        default=list(DEFAULT_SPLITS),
        help="Source split allowlist (default: train dev)",
    )
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Bounded process workers for decode/feature extraction "
            "(default: 1; use a measured value such as 8 for a full run)"
        ),
    )
    parser.add_argument("--phone-shard-size", type=int, default=250_000)
    parser.add_argument("--progress-every", type=int, default=2_000)
    parser.add_argument(
        "--disable-f0",
        action="store_true",
        help=(
            "Skip frame-autocorrelation F0 for a cheap throughput smoke run; "
            "the two F0 columns are emitted as null and the report records it"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Smoke-test limit on emitted utterances after all exclusions",
    )
    return parser.parse_args()


def _validate_source_schema(parquet: pq.ParquetFile) -> None:
    required = {
        "id",
        "speaker_id",
        "split",
        "audio_path",
        "prompt_sha256",
        "qc_flags",
        "word_intervals",
        "target_phone_intervals",
        "realized_phone_intervals",
        "error_intervals",
        "phone_alignments",
        "pronunciation_score",
        "fluency_score",
        "comprehensibility_score",
    }
    missing = sorted(required - set(parquet.schema_arrow.names))
    if missing:
        raise ValueError(
            "modeling manifest is missing required columns: " + ", ".join(missing)
        )


def _base_report(
    *,
    manifest_sha256: str,
    reservation_sha256: str,
    allowed_splits: Sequence[str],
    limit: int | None,
    phone_enabled: bool,
    compute_f0: bool,
    canonical_utterance_cache_sha256: str | None,
    workers: int,
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "running",
        "started_at_utc": utc_now(),
        "completed_at_utc": None,
        "source": {
            "modeling_manifest_sha256": manifest_sha256,
            "prompt_reservation_sha256": reservation_sha256,
        },
        "policy": {
            "allowed_splits": list(allowed_splits),
            "default_allowed_splits": list(DEFAULT_SPLITS),
            "evaluation_splits": sorted(EVALUATION_SPLITS),
            "metric_evaluation_performed": False,
            "rows_with_qc_flags_excluded": True,
            "reserved_prompts_excluded_from_every_split": True,
            "privacy_safe": True,
            "speaker_id_sha256_is_pseudonymous_hash_not_raw_user_id": True,
            "utterance_id_sha256_is_namespaced_hash_not_raw_file_stem": True,
            "raw_file_stem_contains_user_id_and_is_never_exported": True,
            "identity_transforms": {
                "utterance_id_sha256": (
                    "SHA-256(UTF-8('"
                    + UTTERANCE_HASH_NAMESPACE
                    + ":' || raw_file_stem))"
                ),
                "speaker_id_sha256": (
                    "pre-hashed pseudonymous speaker key inherited from the "
                    "modeling manifest; raw UserID is never read"
                ),
            },
            "prohibited_output_columns": sorted(PROHIBITED_OUTPUT_COLUMNS),
            "oracle_alignment": True,
            "oracle_alignment_scope": (
                "gold target-phone boundaries; utterance label-duration "
                "features and optional phone segments"
            ),
            "column_roles": {
                "utterance": {
                    "input_feature_columns": list(
                        UTTERANCE_INPUT_FEATURE_COLUMNS
                    ),
                    "label_columns": list(LABEL_COLUMNS),
                    "slice_only_columns": list(SLICE_ONLY_COLUMNS),
                },
                "phone": {
                    "input_feature_columns": list(PHONE_INPUT_FEATURE_COLUMNS),
                    "label_columns": ["binary_error_label"],
                    "slice_only_columns": [
                        "split",
                        "l1",
                        "gender",
                        "proficiency",
                    ],
                },
            },
        },
        "definitions": {
            "speech_rate_words_per_second": (
                "nonblank word interval count / decoded utterance duration"
            ),
            "articulation_rate_words_per_second": (
                "nonblank word interval count / sum of word label durations"
            ),
            "speech_rate_phones_per_second": (
                "nonblank target-phone count / decoded utterance duration"
            ),
            "articulation_rate_phones_per_second": (
                "nonblank target-phone count / sum of target-phone durations"
            ),
            "duration_npvi": (
                "100 * mean absolute adjacent duration difference divided by "
                "the adjacent mean; positive-duration labels only"
            ),
            "binary_error_label": (
                "1 for target substitution/deletion; 0 for target match; "
                "realized-only insertions are not phone-cache rows"
            ),
            "input_feature_policy": (
                "Only schema metadata input_feature_columns may enter a model. "
                "Scores, binary_error_label, realized labels, alignment "
                "operations, and error tags/counts are targets or forbidden."
            ),
        },
        "configuration": {
            "target_sample_rate_hz": 16_000,
            "limit": limit,
            "phone_cache_enabled": phone_enabled,
            "compute_f0": compute_f0,
            "feature_profile": "full" if compute_f0 else "cheap_without_f0",
            "disabled_features": (
                [] if compute_f0 else ["f0_median_hz", "f0_range_hz"]
            ),
            "utterance_output_role": (
                "incidental_duplicate_for_phone_cache"
                if canonical_utterance_cache_sha256 is not None
                else "canonical"
            ),
            "canonical_utterance_cache_sha256": (
                canonical_utterance_cache_sha256
            ),
            "process_workers": workers,
        },
        "progress": {
            "source_rows_scanned": 0,
            "utterance_rows_written": 0,
            "phone_rows_written": 0,
            "decoded_audio_seconds": 0.0,
            "source_scan_complete": False,
            "source_rows_by_split": {},
            "included_rows_by_split": {},
            "exclusions": {},
        },
        "outputs": {},
    }


def build_cache(args: argparse.Namespace) -> dict[str, Any]:
    if args.batch_size <= 0:
        raise ValueError("batch-size must be positive")
    if args.workers <= 0:
        raise ValueError("workers must be positive")
    if args.progress_every < 0:
        raise ValueError("progress-every must be non-negative")
    if args.limit is not None and args.limit <= 0:
        raise ValueError("limit must be positive")
    if args.canonical_utterance_cache is not None:
        if args.phone_output is None:
            raise ValueError(
                "canonical-utterance-cache is only valid with phone-output"
            )
        if not args.canonical_utterance_cache.is_file():
            raise ValueError("canonical utterance cache does not exist")
        if args.canonical_utterance_cache.resolve() == args.output.resolve():
            raise ValueError(
                "canonical utterance cache and incidental output must differ"
            )
    if args.output.exists():
        raise FileExistsError(f"utterance output already exists: {args.output}")
    allowed_splits = tuple(dict.fromkeys(args.splits))
    if not allowed_splits or any(not split for split in allowed_splits):
        raise ValueError("splits must contain at least one non-empty value")

    manifest_sha256 = sha256_file(args.modeling_manifest)
    reserved, expected_manifest_sha256 = load_reserved_prompt_hashes(
        args.prompt_reservation
    )
    if expected_manifest_sha256 != manifest_sha256:
        raise ValueError(
            "prompt reservation was built from a different modeling manifest"
        )
    reservation_sha256 = sha256_file(args.prompt_reservation)
    canonical_utterance_cache_sha256 = (
        sha256_file(args.canonical_utterance_cache)
        if args.canonical_utterance_cache is not None
        else None
    )
    report = _base_report(
        manifest_sha256=manifest_sha256,
        reservation_sha256=reservation_sha256,
        allowed_splits=allowed_splits,
        limit=args.limit,
        phone_enabled=args.phone_output is not None,
        compute_f0=not args.disable_f0,
        canonical_utterance_cache_sha256=(
            canonical_utterance_cache_sha256
        ),
        workers=args.workers,
    )
    atomic_write_json(args.report_output, report)

    parquet = pq.ParquetFile(args.modeling_manifest)
    _validate_source_schema(parquet)
    utterance_schema = schema_with_metadata(
        UTTERANCE_SCHEMA,
        source_manifest_sha256=manifest_sha256,
        allowed_splits=allowed_splits,
    )
    phone_schema = schema_with_metadata(
        PHONE_SCHEMA,
        source_manifest_sha256=manifest_sha256,
        allowed_splits=allowed_splits,
    )
    if PROHIBITED_OUTPUT_COLUMNS & set(utterance_schema.names):
        raise AssertionError("utterance schema contains prohibited columns")
    if PROHIBITED_OUTPUT_COLUMNS & set(phone_schema.names):
        raise AssertionError("phone schema contains prohibited columns")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_temporary = args.output.with_name(
        f".{args.output.name}.part-{uuid4().hex}"
    )
    output_writer: pq.ParquetWriter | None = None
    phone_writer: PhoneShardWriter | None = None
    executor: ProcessPoolExecutor | None = None
    row_buffer: list[dict[str, Any]] = []
    source_split_counts: Counter[str] = Counter()
    included_split_counts: Counter[str] = Counter()
    exclusions: Counter[str] = Counter()
    scanned = 0
    written = 0
    phone_rows_written = 0
    decoded_audio_seconds = 0.0
    started = time.monotonic()
    utterance_published = False
    phone_published = False

    try:
        output_writer = pq.ParquetWriter(
            output_temporary,
            utterance_schema,
            compression="zstd",
            use_dictionary=True,
        )
        if args.phone_output is not None:
            phone_writer = PhoneShardWriter(
                args.phone_output,
                phone_schema,
                shard_size=args.phone_shard_size,
            )
        if args.workers > 1:
            # PyArrow can create background threads.  Python 3.12 warns that
            # forking a multi-threaded process may deadlock, so use an explicit
            # clean interpreter for each worker pool.
            executor = ProcessPoolExecutor(
                max_workers=args.workers,
                mp_context=multiprocessing.get_context("spawn"),
            )

        stop = False
        for batch in parquet.iter_batches(batch_size=args.batch_size):
            eligible_rows: list[dict[str, Any]] = []
            for row in batch.to_pylist():
                scanned += 1
                split = str(row.get("split") or "")
                source_split_counts[split] += 1
                if split not in allowed_splits:
                    exclusions["split_not_allowed"] += 1
                    continue
                qc_flags = row.get("qc_flags")
                if qc_flags:
                    exclusions["qc_flags_present"] += 1
                    continue
                prompt_hash = row.get("prompt_sha256")
                if prompt_hash in reserved:
                    exclusions["reserved_prompt"] += 1
                    continue
                eligible_rows.append(row)
                if (
                    args.limit is not None
                    and written + len(eligible_rows) >= args.limit
                ):
                    stop = True
                    break

            tasks = [
                (
                    row,
                    str(args.dataset_root),
                    phone_writer is not None,
                    not args.disable_f0,
                )
                for row in eligible_rows
            ]
            if executor is None:
                results = map(_extract_feature_rows_worker, tasks)
            else:
                chunksize = max(
                    1,
                    len(tasks) // max(1, args.workers * 4),
                )
                results = executor.map(
                    _extract_feature_rows_worker,
                    tasks,
                    chunksize=chunksize,
                )

            for row, result in zip(eligible_rows, results, strict=True):
                utterance, phones, failed_id, cause_type = result
                if utterance is None:
                    raise FeatureBuildError(
                        stage="utterance",
                        utterance_id_sha256=failed_id,
                        cause_type=cause_type or "UnknownError",
                    )
                row_buffer.append(utterance)
                if phone_writer is not None:
                    phone_writer.append(phones)
                written += 1
                phone_rows_written += len(phones)
                decoded_audio_seconds += float(
                    utterance["audio_duration_seconds"]
                )
                split = str(row.get("split") or "")
                included_split_counts[split] += 1

                if len(row_buffer) >= args.batch_size:
                    output_writer.write_table(
                        pa.Table.from_pylist(
                            row_buffer,
                            schema=utterance_schema,
                        )
                    )
                    row_buffer.clear()
                if args.progress_every and written % args.progress_every == 0:
                    report["progress"] = {
                        "source_rows_scanned": scanned,
                        "utterance_rows_written": written,
                        "phone_rows_written": phone_rows_written,
                        "decoded_audio_seconds": decoded_audio_seconds,
                        "source_scan_complete": False,
                        "source_rows_by_split": dict(
                            sorted(source_split_counts.items())
                        ),
                        "included_rows_by_split": dict(
                            sorted(included_split_counts.items())
                        ),
                        "exclusions": dict(sorted(exclusions.items())),
                    }
                    atomic_write_json(args.report_output, report)
                    print(
                        f"Explicit features: {written:,} utterances, "
                        f"{phone_rows_written:,} phone rows "
                        f"({scanned:,} source rows scanned)",
                        flush=True,
                    )
            if stop:
                break

        if executor is not None:
            executor.shutdown()
            executor = None
        if row_buffer:
            output_writer.write_table(
                pa.Table.from_pylist(row_buffer, schema=utterance_schema)
            )
            row_buffer.clear()
        output_writer.close()
        output_writer = None
        os.replace(output_temporary, args.output)
        utterance_published = True

        phone_shards: list[dict[str, Any]] = []
        if phone_writer is not None:
            phone_shards = phone_writer.finalize()
            phone_published = True
            phone_writer = None
        source_scan_complete = scanned == parquet.metadata.num_rows
        report["status"] = "completed"
        report["completed_at_utc"] = utc_now()
        report["elapsed_seconds"] = time.monotonic() - started
        elapsed_seconds = max(float(report["elapsed_seconds"]), 1e-12)
        report["progress"] = {
            "source_rows_scanned": scanned,
            "utterance_rows_written": written,
            "phone_rows_written": phone_rows_written,
            "decoded_audio_seconds": decoded_audio_seconds,
            "source_scan_complete": source_scan_complete,
            "source_rows_by_split": dict(sorted(source_split_counts.items())),
            "included_rows_by_split": dict(
                sorted(included_split_counts.items())
            ),
            "exclusions": dict(sorted(exclusions.items())),
        }
        report["outputs"] = {
            "utterance": {
                "row_count": written,
                "column_count": len(utterance_schema),
                "sha256": sha256_file(args.output),
                "role": report["configuration"]["utterance_output_role"],
                "canonical_utterance_cache_sha256": (
                    canonical_utterance_cache_sha256
                ),
            },
            "phone": {
                "enabled": args.phone_output is not None,
                "row_count": phone_rows_written,
                "oracle_alignment": True,
                "shards": phone_shards,
            },
        }
        report["throughput"] = {
            "utterances_per_wall_second": written / elapsed_seconds,
            "audio_seconds_per_wall_second": (
                decoded_audio_seconds / elapsed_seconds
            ),
            "audio_hours_per_wall_hour": decoded_audio_seconds / elapsed_seconds,
        }
        atomic_write_json(args.report_output, report)
        return report
    except BaseException as exc:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if output_writer is not None:
            output_writer.close()
        output_temporary.unlink(missing_ok=True)
        if phone_writer is not None:
            phone_writer.abort()
        if utterance_published:
            args.output.unlink(missing_ok=True)
        if phone_published and args.phone_output is not None:
            shutil.rmtree(args.phone_output, ignore_errors=True)
        report["status"] = "failed"
        report["completed_at_utc"] = utc_now()
        report["elapsed_seconds"] = time.monotonic() - started
        report["progress"] = {
            "source_rows_scanned": scanned,
            "utterance_rows_written": written,
            "phone_rows_written": phone_rows_written,
            "decoded_audio_seconds": decoded_audio_seconds,
            "source_scan_complete": False,
            "source_rows_by_split": dict(sorted(source_split_counts.items())),
            "included_rows_by_split": dict(
                sorted(included_split_counts.items())
            ),
            "exclusions": dict(sorted(exclusions.items())),
        }
        report["failure"] = {
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        if isinstance(exc, FeatureBuildError):
            report["failure"].update(
                {
                    "stage": exc.stage,
                    "utterance_id_sha256": exc.utterance_id_sha256,
                    "cause_type": exc.cause_type,
                }
            )
        atomic_write_json(args.report_output, report)
        raise


def main() -> int:
    args = parse_args()
    report = build_cache(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "utterance_rows": report["outputs"]["utterance"]["row_count"],
                "phone_rows": report["outputs"]["phone"]["row_count"],
                "utterance_sha256": report["outputs"]["utterance"]["sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
