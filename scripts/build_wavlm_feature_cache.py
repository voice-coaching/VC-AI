#!/usr/bin/env python3
"""Build a frozen, pinned WavLM Base+ feature cache.

The builder is deliberately development-only.  It reads no holistic score
columns, rejects every evaluation split, applies the same QC and reserved
prompt exclusions as the explicit baseline, and exports neither restricted
source locators nor realized-phone/alignment identities.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import sys
import time
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch
import yaml

from voice_coach.data import Interval
from voice_coach.data.audio import decode_audio, make_label_aware_chunks
from voice_coach.features.ssl import (
    WAVLM_BASE_PLUS_FRAME_GEOMETRY,
    prepare_wavlm_input,
)


MODEL_ID = "microsoft/wavlm-base-plus"
MODEL_REVISION = "4c66d4806a428f2e922ccfa1a962776e232d487b"
PINNED_PREPROCESSOR_URL = (
    "https://huggingface.co/microsoft/wavlm-base-plus/blob/"
    f"{MODEL_REVISION}/preprocessor_config.json"
)
HIDDEN_SIZE = 768
SAMPLE_RATE_HZ = 16_000
MAX_CHUNK_SECONDS = 20.0
DO_NORMALIZE = False
ALLOWED_SPLITS = ("train", "dev")
EVALUATION_SPLITS = frozenset({"stack", "calibration", "locked_test"})
UTTERANCE_HASH_NAMESPACE = "voice-coach.aihub71490.utterance.v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

UTTERANCE_SCHEMA_VERSION = "voice_coach.wavlm_utterance_features.v1"
PHONE_SCHEMA_VERSION = "voice_coach.wavlm_phone_features.v1"
WORD_SCHEMA_VERSION = "voice_coach.wavlm_word_features.v1"
REPORT_SCHEMA_VERSION = "voice_coach.wavlm_feature_cache_report.v1"

PROHIBITED_OUTPUT_COLUMNS = frozenset(
    {
        "id",
        "utterance_id",
        "raw_utterance_id",
        "audio_path",
        "json_path",
        "textgrid_path",
        "source_path",
        "source_file_stem",
        "file_stem",
        "word_label",
        "realized_label",
        "realized_phone_identity",
        "alignment_operation",
        "operation",
        "error_tag",
        "error_tags",
        "pronunciation_score",
        "fluency_score",
        "comprehensibility_score",
    }
)

SOURCE_COLUMNS = (
    "id",
    "speaker_id",
    "split",
    "audio_path",
    "prompt_sha256",
    "qc_flags",
    "word_intervals",
    "target_phone_intervals",
    "phone_alignments",
    "l1",
    "gender",
    "proficiency",
)


@dataclass(slots=True)
class FeatureBuildError(Exception):
    stage: str
    utterance_id_sha256: str | None
    cause_type: str

    def __str__(self) -> str:
        identifier = self.utterance_id_sha256 or "<unknown>"
        return (
            f"WavLM feature extraction failed at {self.stage} for "
            f"{identifier} ({self.cause_type})"
        )


@dataclass(frozen=True, slots=True)
class ExtractedRows:
    utterance: dict[str, Any]
    phones: tuple[dict[str, Any], ...]
    words: tuple[dict[str, Any], ...]
    decoded_audio_seconds: float
    model_frames: int
    chunk_count: int
    padded_short_chunks: int
    output_dtypes: tuple[str, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def hash_utterance_id(source_id: object) -> str:
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("manifest row is missing id")
    payload = f"{UTTERANCE_HASH_NAMESPACE}:{source_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def resolve_audio_path(dataset_root: Path, relative_path: object) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("audio_path must be a non-empty relative locator")
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError("audio_path must be relative to dataset_root")
    root = dataset_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("audio_path escapes dataset_root") from error
    return candidate


def load_reserved_prompt_hashes(path: Path) -> tuple[set[str], str]:
    document = json.loads(path.read_text(encoding="utf-8"))
    try:
        source_sha256 = document["source"]["modeling_manifest_sha256"]
        entries = document["reserved_prompts"]
    except (KeyError, TypeError) as error:
        raise ValueError("invalid prompt reservation artifact") from error
    if not isinstance(source_sha256, str) or not SHA256_PATTERN.fullmatch(
        source_sha256
    ):
        raise ValueError("reservation source manifest SHA-256 is invalid")
    if not isinstance(entries, list) or not entries:
        raise ValueError("reservation artifact has no reserved prompts")
    reserved: set[str] = set()
    for index, entry in enumerate(entries):
        prompt_hash = entry.get("prompt_sha256") if isinstance(entry, Mapping) else None
        if not isinstance(prompt_hash, str) or not SHA256_PATTERN.fullmatch(
            prompt_hash
        ):
            raise ValueError(f"reserved_prompts[{index}].prompt_sha256 is invalid")
        reserved.add(prompt_hash)
    if len(reserved) != len(entries):
        raise ValueError("reservation artifact contains duplicate prompt hashes")
    return reserved, source_sha256


def _expect_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def load_and_validate_config(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    root = dict(_expect_mapping(loaded, "config"))
    model = _expect_mapping(root.get("model"), "model")
    data = _expect_mapping(root.get("data"), "data")
    features = _expect_mapping(root.get("features"), "features")
    output = _expect_mapping(root.get("output"), "output")
    gate = _expect_mapping(root.get("full_run_gate"), "full_run_gate")

    expected_model = {
        "id": MODEL_ID,
        "revision": MODEL_REVISION,
        "local_files_only": True,
        "frozen": True,
        "hidden_size": HIDDEN_SIZE,
        "conv_kernel": list(WAVLM_BASE_PLUS_FRAME_GEOMETRY.conv_kernels),
        "conv_stride": list(WAVLM_BASE_PLUS_FRAME_GEOMETRY.conv_strides),
        "do_normalize": DO_NORMALIZE,
        "return_attention_mask": True,
    }
    for key, expected in expected_model.items():
        if model.get(key) != expected:
            raise ValueError(f"model.{key} must equal pinned value {expected!r}")

    expected_data = {
        "allowed_splits": list(ALLOWED_SPLITS),
        "forbidden_metric_splits": sorted(EVALUATION_SPLITS),
        "exclude_qc_flagged": True,
        "exclude_reserved_prompts": True,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "max_chunk_seconds": MAX_CHUNK_SECONDS,
        "chunk_boundary_source": "target_phone_intervals",
    }
    for key, expected in expected_data.items():
        actual = data.get(key)
        if key == "forbidden_metric_splits":
            actual = sorted(actual) if isinstance(actual, list) else actual
        if actual != expected:
            raise ValueError(f"data.{key} must equal {expected!r}")

    expected_features = {
        "inference_precision": "bfloat16",
        "storage_precision": "float16",
        "utterance_pooling": "mean_std",
        "target_phone_pooling": "mean",
        "word_pooling": "mean",
    }
    for key, expected in expected_features.items():
        if features.get(key) != expected:
            raise ValueError(f"features.{key} must equal {expected!r}")
    supervision = _expect_mapping(
        features.get("phone_supervision"),
        "features.phone_supervision",
    )
    if (
        supervision.get("negative_operations") != ["match"]
        or supervision.get("positive_operations") != ["substitution", "deletion"]
        or supervision.get("ignored_operations") != ["insertion"]
        or supervision.get("label_role_only") is not True
    ):
        raise ValueError("phone supervision policy differs from the pin")

    for key in (
        "utterance_shard_rows",
        "phone_shard_rows",
        "word_shard_rows",
        "utterance_row_group_rows",
        "phone_row_group_rows",
        "word_row_group_rows",
    ):
        if not isinstance(output.get(key), int) or output[key] <= 0:
            raise ValueError(f"output.{key} must be a positive integer")
    for kind in ("utterance", "phone", "word"):
        row_group_rows = output[f"{kind}_row_group_rows"]
        shard_rows = output[f"{kind}_shard_rows"]
        if row_group_rows > shard_rows or row_group_rows > 4_096:
            raise ValueError(
                f"output.{kind}_row_group_rows must not exceed its shard size or 4096"
            )
    if output.get("compression") != "zstd":
        raise ValueError("output.compression must equal 'zstd'")
    if output.get("atomic_publish") is not True:
        raise ValueError("output.atomic_publish must be true")
    if (
        gate.get("require_explicit_baseline_dev_result") is not True
        or gate.get("require_completed_smoke_review") is not True
        or gate.get("require_resource_review") is not True
        or gate.get("smoke_utterances") != 100
    ):
        raise ValueError("full-run gate policy differs from the pin")
    reference_rows = gate.get("resource_estimate_reference_rows")
    if not isinstance(reference_rows, Mapping) or any(
        not isinstance(reference_rows.get(kind), int) or reference_rows[kind] <= 0
        for kind in ("utterance", "phone", "word")
    ):
        raise ValueError("full-run resource reference rows are invalid")
    safety_factor = gate.get("resource_estimate_safety_factor")
    if (
        not isinstance(safety_factor, (int, float))
        or isinstance(safety_factor, bool)
        or safety_factor < 1
    ):
        raise ValueError("resource estimate safety factor must be at least 1")
    return root


def _load_gate_document(path: Path, *, kind: str) -> Mapping[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ValueError(f"{kind} gate document must be a mapping")
    return document


def validate_full_run_gate(
    *,
    explicit_dev_report: Path | None,
    smoke_review: Path | None,
    expected_config_sha256: str,
    expected_builder_sha256: str,
) -> dict[str, str]:
    if explicit_dev_report is None or smoke_review is None:
        raise ValueError("full run requires --explicit-dev-report and --smoke-review")
    explicit = _load_gate_document(
        explicit_dev_report,
        kind="explicit dev report",
    )
    if explicit.get("status") != "completed":
        raise ValueError("explicit dev report is not completed")
    if explicit.get("development_only") is not True:
        raise ValueError("explicit result is not development-only")
    leakage = explicit.get("leakage_controls")
    if not isinstance(leakage, Mapping):
        raise ValueError("explicit dev report has no leakage controls")
    allowed = leakage.get("accepted_splits")
    if not isinstance(allowed, list) or set(allowed) != set(ALLOWED_SPLITS):
        raise ValueError("explicit dev report is not train/dev-only")
    if any(
        leakage.get(key) is not False
        for key in (
            "locked_test_accessed",
            "stack_accessed",
            "calibration_accessed",
        )
    ):
        raise ValueError("explicit dev report accessed an evaluation split")
    summary = explicit.get("summary")
    if (
        not isinstance(summary, Mapping)
        or summary.get("locked_test_remains_unread") is not True
        or summary.get("results_are_development_only") is not True
        or summary.get("seeds_completed") != [42, 43, 44]
    ):
        raise ValueError("explicit dev completion summary is invalid")

    review = _load_gate_document(smoke_review, kind="smoke review")
    if (
        review.get("status") != "approved"
        or review.get("full_run_approved") is not True
    ):
        raise ValueError("smoke review has not approved a full run")
    if review.get("experiment_config_sha256") != expected_config_sha256:
        raise ValueError("smoke review does not pin the current config")
    if review.get("builder_sha256") != expected_builder_sha256:
        raise ValueError("smoke review does not pin the current builder")
    smoke_report_sha256 = review.get("smoke_report_sha256")
    if not isinstance(smoke_report_sha256, str) or not SHA256_PATTERN.fullmatch(
        smoke_report_sha256
    ):
        raise ValueError("smoke review does not pin a smoke report SHA-256")
    resource_review = review.get("resource_review")
    if (
        not isinstance(resource_review, Mapping)
        or resource_review.get("approved") is not True
        or not isinstance(resource_review.get("peak_rss_bytes"), int)
        or resource_review["peak_rss_bytes"] <= 0
        or not isinstance(
            resource_review.get("estimated_full_cache_upper_bytes"),
            int,
        )
        or resource_review["estimated_full_cache_upper_bytes"] <= 0
        or not isinstance(resource_review.get("max_row_group_rows"), int)
        or not 1 <= resource_review["max_row_group_rows"] <= 4_096
    ):
        raise ValueError("smoke resource review is incomplete")
    explicit_sha256 = sha256_file(explicit_dev_report)
    smoke_review_sha256 = sha256_file(smoke_review)
    if not SHA256_PATTERN.fullmatch(explicit_sha256):
        raise AssertionError("explicit report SHA-256 computation failed")
    if not SHA256_PATTERN.fullmatch(smoke_review_sha256):
        raise AssertionError("smoke review SHA-256 computation failed")
    return {
        "explicit_dev_report_sha256": explicit_sha256,
        "smoke_review_sha256": smoke_review_sha256,
        "approved_smoke_report_sha256": smoke_report_sha256,
        "approved_config_sha256": expected_config_sha256,
        "approved_builder_sha256": expected_builder_sha256,
    }


def _fixed_embedding_schema(
    *,
    kind: str,
    dimensions: int,
) -> pa.Schema:
    common = [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("oracle_alignment", pa.bool_(), nullable=False),
        pa.field("utterance_id_sha256", pa.string(), nullable=False),
        pa.field("speaker_id_sha256", pa.string(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("prompt_sha256", pa.string(), nullable=False),
        pa.field("l1", pa.string()),
        pa.field("gender", pa.string()),
        pa.field("proficiency", pa.string()),
    ]
    if kind == "utterance":
        fields = [
            *common,
            pa.field("audio_duration_seconds", pa.float64(), nullable=False),
            pa.field("chunk_count", pa.int16(), nullable=False),
            pa.field("frame_count", pa.int32(), nullable=False),
            pa.field(
                "embedding_mean_std",
                pa.list_(pa.float16(), dimensions),
                nullable=False,
            ),
        ]
    elif kind == "phone":
        fields = [
            *common,
            pa.field("phone_index", pa.int32(), nullable=False),
            pa.field("target_label", pa.string(), nullable=False),
            pa.field("oracle_start_seconds", pa.float64(), nullable=False),
            pa.field("oracle_end_seconds", pa.float64(), nullable=False),
            pa.field("pooled_frame_count", pa.int16(), nullable=False),
            pa.field(
                "embedding_mean",
                pa.list_(pa.float16(), dimensions),
                nullable=False,
            ),
            pa.field("binary_error_label", pa.int8(), nullable=False),
        ]
    elif kind == "word":
        fields = [
            *common,
            pa.field("word_index", pa.int32(), nullable=False),
            pa.field("oracle_start_seconds", pa.float64(), nullable=False),
            pa.field("oracle_end_seconds", pa.float64(), nullable=False),
            pa.field("pooled_frame_count", pa.int16(), nullable=False),
            pa.field(
                "embedding_mean",
                pa.list_(pa.float16(), dimensions),
                nullable=False,
            ),
        ]
    else:
        raise ValueError(f"unknown schema kind: {kind}")
    return pa.schema(fields)


UTTERANCE_SCHEMA = _fixed_embedding_schema(
    kind="utterance",
    dimensions=HIDDEN_SIZE * 2,
)
PHONE_SCHEMA = _fixed_embedding_schema(
    kind="phone",
    dimensions=HIDDEN_SIZE,
)
WORD_SCHEMA = _fixed_embedding_schema(
    kind="word",
    dimensions=HIDDEN_SIZE,
)


def schema_with_metadata(
    schema: pa.Schema,
    *,
    kind: str,
    source_manifest_sha256: str,
    config_sha256: str,
) -> pa.Schema:
    if kind == "utterance":
        version = UTTERANCE_SCHEMA_VERSION
        inputs = ["embedding_mean_std"]
        labels: list[str] = []
    elif kind == "phone":
        version = PHONE_SCHEMA_VERSION
        inputs = ["embedding_mean", "target_label"]
        labels = ["binary_error_label"]
    elif kind == "word":
        version = WORD_SCHEMA_VERSION
        inputs = ["embedding_mean"]
        labels = []
    else:
        raise ValueError(f"unknown schema kind: {kind}")
    slices = ["split", "l1", "gender", "proficiency"]
    metadata = {
        b"voice_coach.schema_version": version.encode("ascii"),
        b"voice_coach.model_id": MODEL_ID.encode("ascii"),
        b"voice_coach.model_revision": MODEL_REVISION.encode("ascii"),
        b"voice_coach.frozen_encoder": b"true",
        b"voice_coach.oracle_alignment": b"true",
        b"voice_coach.storage_precision": b"float16",
        b"voice_coach.allowed_splits": b"train,dev",
        b"voice_coach.metric_evaluation_performed": b"false",
        b"voice_coach.privacy_reviewed": b"true",
        b"voice_coach.contains_direct_identifiers": b"false",
        b"voice_coach.contains_transcript_text": b"false",
        b"voice_coach.contains_audio_bytes": b"false",
        b"voice_coach.contains_realized_phone_identity": b"false",
        b"voice_coach.contains_alignment_operation": b"false",
        b"voice_coach.access_scope": b"restricted_internal",
        b"voice_coach.shareable": b"false",
        b"voice_coach.source_manifest_sha256": (source_manifest_sha256.encode("ascii")),
        b"voice_coach.config_sha256": config_sha256.encode("ascii"),
        b"voice_coach.input_feature_columns": json.dumps(
            inputs,
            separators=(",", ":"),
        ).encode("utf-8"),
        b"voice_coach.label_columns": json.dumps(
            labels,
            separators=(",", ":"),
        ).encode("utf-8"),
        b"voice_coach.slice_only_columns": json.dumps(
            slices,
            separators=(",", ":"),
        ).encode("utf-8"),
        b"voice_coach.forbidden_as_input": json.dumps(
            [
                "binary_error_label",
                "realized_phone_identity",
                "alignment_operation",
                "error_tag",
                "holistic_score",
            ],
            separators=(",", ":"),
        ).encode("utf-8"),
    }
    return schema.with_metadata(metadata)


def assert_schema_privacy(schema: pa.Schema) -> None:
    prohibited = PROHIBITED_OUTPUT_COLUMNS & set(schema.names)
    if prohibited:
        raise AssertionError(
            "feature schema contains prohibited columns: "
            + ", ".join(sorted(prohibited))
        )
    lowered = [name.lower() for name in schema.names]
    prohibited_tokens = (
        "path",
        "stem",
        "realized",
        "operation",
        "word_label",
    )
    for name in lowered:
        if any(token in name for token in prohibited_tokens):
            raise AssertionError(f"feature schema leaks prohibited role: {name}")


class ShardedWriter:
    def __init__(
        self,
        directory: Path,
        schema: pa.Schema,
        *,
        shard_rows: int,
        row_group_rows: int,
    ) -> None:
        if shard_rows <= 0 or row_group_rows <= 0 or row_group_rows > shard_rows:
            raise ValueError("invalid shard or row-group row limit")
        directory.mkdir(parents=True)
        self.directory = directory
        self.schema = schema
        self.shard_rows = shard_rows
        self.row_group_rows = row_group_rows
        self.buffer: list[dict[str, Any]] = []
        self.shard_count = 0
        self.current_shard_rows = 0
        self.current_writer: pq.ParquetWriter | None = None
        self.current_temporary: Path | None = None
        self.max_buffered_rows = 0

    def append(self, rows: Iterable[dict[str, Any]]) -> None:
        for row in rows:
            self.buffer.append(row)
            self.max_buffered_rows = max(
                self.max_buffered_rows,
                len(self.buffer),
            )
            shard_capacity = self.shard_rows - self.current_shard_rows
            flush_at = min(self.row_group_rows, shard_capacity)
            if len(self.buffer) >= flush_at:
                self._flush(flush_at)

    def _flush(self, count: int) -> None:
        if count <= 0 or count > len(self.buffer):
            raise ValueError("invalid buffered row flush")
        if self.current_writer is None:
            final = self.directory / f"part-{self.shard_count:05d}.parquet"
            self.current_temporary = final.with_suffix(".parquet.part")
            self.current_writer = pq.ParquetWriter(
                self.current_temporary,
                self.schema,
                compression="zstd",
                use_dictionary=True,
            )
        rows = self.buffer[:count]
        del self.buffer[:count]
        table = pa.Table.from_pylist(rows, schema=self.schema)
        self.current_writer.write_table(table, row_group_size=count)
        self.current_shard_rows += count
        if self.current_shard_rows == self.shard_rows:
            self._finalize_current_shard()

    def _finalize_current_shard(self) -> None:
        if self.current_writer is None or self.current_temporary is None:
            return
        self.current_writer.close()
        final = self.directory / f"part-{self.shard_count:05d}.parquet"
        os.replace(self.current_temporary, final)
        self.current_writer = None
        self.current_temporary = None
        self.current_shard_rows = 0
        self.shard_count += 1

    def finalize(self) -> list[dict[str, Any]]:
        while self.buffer:
            shard_capacity = self.shard_rows - self.current_shard_rows
            self._flush(
                min(
                    len(self.buffer),
                    self.row_group_rows,
                    shard_capacity,
                )
            )
        self._finalize_current_shard()
        output = []
        for path in sorted(self.directory.glob("part-*.parquet")):
            parquet = pq.ParquetFile(path)
            output.append(
                {
                    "name": path.name,
                    "row_count": parquet.metadata.num_rows,
                    "row_group_count": parquet.metadata.num_row_groups,
                    "byte_count": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        return output

    def abort(self) -> None:
        if self.current_writer is not None:
            self.current_writer.close()
            self.current_writer = None
        if self.current_temporary is not None:
            self.current_temporary.unlink(missing_ok=True)
            self.current_temporary = None
        self.buffer.clear()


def _validate_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{field} is not a lowercase SHA-256")
    return value


def _clean_intervals(
    values: object,
    *,
    duration_seconds: float,
) -> list[Interval]:
    if not isinstance(values, list):
        raise ValueError("interval annotation must be a list")
    output: list[Interval] = []
    tolerance = 1 / SAMPLE_RATE_HZ + 1e-6
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("interval entry must be a mapping")
        label = str(value.get("label") or "").strip()
        if not label:
            continue
        start = float(value["start"])
        end = float(value["end"])
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end < start
            or end > duration_seconds + tolerance
        ):
            raise ValueError("interval has invalid bounds")
        start = min(start, duration_seconds)
        end = min(end, duration_seconds)
        if end > start:
            output.append(Interval(start=start, end=end, label=label))
    return output


def target_binary_labels(
    row: Mapping[str, Any],
    targets: Sequence[Interval],
) -> list[int]:
    """Derive the sole phone target without exporting its source operation."""

    values = row.get("phone_alignments")
    if not isinstance(values, list):
        raise ValueError("phone_alignments must be a list")
    target_bearing = [
        value
        for value in values
        if isinstance(value, Mapping) and value.get("target_label") is not None
    ]
    if len(target_bearing) != len(targets):
        raise ValueError("target_phone_intervals and target-bearing alignments differ")
    labels: list[int] = []
    for index, (target, alignment) in enumerate(
        zip(targets, target_bearing, strict=True)
    ):
        if str(alignment.get("target_label")) != target.label:
            raise ValueError(f"target alignment label mismatch at index {index}")
        operation = alignment.get("operation")
        if operation == "match":
            labels.append(0)
        elif operation in {"substitution", "deletion"}:
            labels.append(1)
        else:
            raise ValueError(f"invalid target-bearing alignment at index {index}")
    return labels


def _base_identifiers(
    row: Mapping[str, Any],
    *,
    utterance_id_sha256: str,
) -> dict[str, Any]:
    speaker = _validate_sha256(row.get("speaker_id"), "speaker_id")
    prompt = _validate_sha256(row.get("prompt_sha256"), "prompt_sha256")
    split = str(row.get("split") or "")
    if split not in ALLOWED_SPLITS:
        raise ValueError("feature extraction received a forbidden split")
    return {
        "utterance_id_sha256": utterance_id_sha256,
        "speaker_id_sha256": speaker,
        "split": split,
        "prompt_sha256": prompt,
        "l1": row.get("l1"),
        "gender": row.get("gender"),
        "proficiency": row.get("proficiency"),
    }


def _embedding_list(values: torch.Tensor, expected_size: int) -> list[float]:
    vector = values.detach().to(device="cpu", dtype=torch.float32).numpy()
    if vector.ndim != 1 or vector.size != expected_size:
        raise ValueError("pooled WavLM embedding has an unexpected shape")
    if not np.isfinite(vector).all():
        raise ValueError("pooled WavLM embedding contains NaN or Inf")
    return vector.astype(np.float16).tolist()


def _pool_interval_parts(
    parts: Sequence[Sequence[torch.Tensor]],
    *,
    hidden_size: int,
) -> tuple[list[torch.Tensor], list[int]]:
    pooled: list[torch.Tensor] = []
    frame_counts: list[int] = []
    for index, interval_parts in enumerate(parts):
        if not interval_parts:
            raise ValueError(f"interval {index} received no WavLM frame")
        frames = torch.cat(tuple(interval_parts), dim=0)
        if frames.ndim != 2 or frames.shape[1] != hidden_size:
            raise ValueError("interval WavLM frames have an unexpected shape")
        pooled.append(frames.mean(dim=0))
        frame_counts.append(frames.shape[0])
    return pooled, frame_counts


def _interval_slices_for_chunk(
    intervals: Sequence[Interval],
    *,
    chunk_start_seconds: float,
    chunk_end_seconds: float,
    model_input_sample_count: int,
    hidden: torch.Tensor,
) -> list[tuple[int, torch.Tensor]]:
    results: list[tuple[int, torch.Tensor]] = []
    for index, interval in enumerate(intervals):
        overlap_start = max(interval.start, chunk_start_seconds)
        overlap_end = min(interval.end, chunk_end_seconds)
        if overlap_end <= overlap_start:
            continue
        first, stop = WAVLM_BASE_PLUS_FRAME_GEOMETRY.interval_frame_span(
            start_seconds=overlap_start - chunk_start_seconds,
            end_seconds=overlap_end - chunk_start_seconds,
            input_sample_count=model_input_sample_count,
        )
        if stop <= first or stop > hidden.shape[0]:
            raise ValueError("frame-to-time mapping produced an invalid span")
        results.append((index, hidden[first:stop]))
    return results


def extract_rows(
    row: Mapping[str, Any],
    *,
    dataset_root: Path,
    model: torch.nn.Module,
    device: torch.device,
    autocast_dtype: torch.dtype | None,
) -> ExtractedRows:
    utterance_id_sha256 = hash_utterance_id(row.get("id"))
    identifiers = _base_identifiers(
        row,
        utterance_id_sha256=utterance_id_sha256,
    )
    audio = decode_audio(
        resolve_audio_path(dataset_root, row.get("audio_path")),
        target_sample_rate=SAMPLE_RATE_HZ,
    )
    targets = _clean_intervals(
        row.get("target_phone_intervals"),
        duration_seconds=audio.duration_seconds,
    )
    words = _clean_intervals(
        row.get("word_intervals"),
        duration_seconds=audio.duration_seconds,
    )
    binary_labels = target_binary_labels(row, targets)
    chunks = make_label_aware_chunks(
        audio.duration_seconds,
        targets,
        max_duration_seconds=MAX_CHUNK_SECONDS,
    )

    utterance_frames: list[torch.Tensor] = []
    phone_parts: list[list[torch.Tensor]] = [[] for _ in targets]
    word_parts: list[list[torch.Tensor]] = [[] for _ in words]
    padded_short_chunks = 0
    output_dtypes: list[str] = []

    for chunk in chunks:
        start_sample = round(chunk.start_seconds * SAMPLE_RATE_HZ)
        end_sample = round(chunk.end_seconds * SAMPLE_RATE_HZ)
        waveform = audio.samples[start_sample:end_sample]
        values = prepare_wavlm_input(
            torch.from_numpy(waveform),
            do_normalize=DO_NORMALIZE,
        )
        actual_sample_count = values.numel()
        receptive_field = WAVLM_BASE_PLUS_FRAME_GEOMETRY.receptive_field_samples
        if actual_sample_count < receptive_field:
            values = torch.nn.functional.pad(
                values,
                (0, receptive_field - actual_sample_count),
            )
            padded_short_chunks += 1
        input_sample_count = values.numel()
        attention_mask = torch.zeros(
            (1, input_sample_count),
            dtype=torch.long,
            device=device,
        )
        attention_mask[:, :actual_sample_count] = 1
        input_values = values.unsqueeze(0).to(device=device)
        context = (
            torch.autocast(device_type="cuda", dtype=autocast_dtype)
            if device.type == "cuda" and autocast_dtype is not None
            else nullcontext()
        )
        with torch.inference_mode(), context:
            output = model(
                input_values=input_values,
                attention_mask=attention_mask,
            )
        hidden = output.last_hidden_state[0]
        output_dtypes.append(str(hidden.dtype).removeprefix("torch."))
        expected_frames = WAVLM_BASE_PLUS_FRAME_GEOMETRY.output_frame_count(
            input_sample_count
        )
        if hidden.ndim != 2 or hidden.shape != (expected_frames, HIDDEN_SIZE):
            raise ValueError("WavLM output shape disagrees with pinned frame geometry")
        hidden_cpu = hidden.detach().to(device="cpu", dtype=torch.float32)
        utterance_frames.append(hidden_cpu)
        for index, frames in _interval_slices_for_chunk(
            targets,
            chunk_start_seconds=chunk.start_seconds,
            chunk_end_seconds=chunk.end_seconds,
            model_input_sample_count=input_sample_count,
            hidden=hidden_cpu,
        ):
            phone_parts[index].append(frames)
        for index, frames in _interval_slices_for_chunk(
            words,
            chunk_start_seconds=chunk.start_seconds,
            chunk_end_seconds=chunk.end_seconds,
            model_input_sample_count=input_sample_count,
            hidden=hidden_cpu,
        ):
            word_parts[index].append(frames)

    all_frames = torch.cat(tuple(utterance_frames), dim=0)
    utterance_embedding = torch.cat(
        (
            all_frames.mean(dim=0),
            all_frames.std(dim=0, correction=0),
        ),
        dim=0,
    )
    phone_embeddings, phone_frame_counts = _pool_interval_parts(
        phone_parts,
        hidden_size=HIDDEN_SIZE,
    )
    word_embeddings, word_frame_counts = _pool_interval_parts(
        word_parts,
        hidden_size=HIDDEN_SIZE,
    )

    utterance = {
        "schema_version": UTTERANCE_SCHEMA_VERSION,
        "oracle_alignment": True,
        **identifiers,
        "audio_duration_seconds": audio.duration_seconds,
        "chunk_count": len(chunks),
        "frame_count": all_frames.shape[0],
        "embedding_mean_std": _embedding_list(
            utterance_embedding,
            HIDDEN_SIZE * 2,
        ),
    }
    phone_rows = tuple(
        {
            "schema_version": PHONE_SCHEMA_VERSION,
            "oracle_alignment": True,
            **identifiers,
            "phone_index": index,
            "target_label": target.label,
            "oracle_start_seconds": target.start,
            "oracle_end_seconds": target.end,
            "pooled_frame_count": phone_frame_counts[index],
            "embedding_mean": _embedding_list(
                phone_embeddings[index],
                HIDDEN_SIZE,
            ),
            "binary_error_label": binary_labels[index],
        }
        for index, target in enumerate(targets)
    )
    word_rows = tuple(
        {
            "schema_version": WORD_SCHEMA_VERSION,
            "oracle_alignment": True,
            **identifiers,
            "word_index": index,
            "oracle_start_seconds": word.start,
            "oracle_end_seconds": word.end,
            "pooled_frame_count": word_frame_counts[index],
            "embedding_mean": _embedding_list(
                word_embeddings[index],
                HIDDEN_SIZE,
            ),
        }
        for index, word in enumerate(words)
    )
    return ExtractedRows(
        utterance=utterance,
        phones=phone_rows,
        words=word_rows,
        decoded_audio_seconds=audio.duration_seconds,
        model_frames=all_frames.shape[0],
        chunk_count=len(chunks),
        padded_short_chunks=padded_short_chunks,
        output_dtypes=tuple(output_dtypes),
    )


def resolve_local_model_files(
    *,
    cache_dir: Path,
) -> dict[str, Path]:
    repository_cache_name = "models--microsoft--wavlm-base-plus"
    snapshot = cache_dir / repository_cache_name / "snapshots" / MODEL_REVISION
    files = {
        "config": snapshot / "config.json",
        "checkpoint": snapshot / "pytorch_model.bin",
    }
    for role, path in files.items():
        if not path.is_file():
            raise FileNotFoundError(f"pinned local WavLM {role} is unavailable")
    return files


def load_frozen_model(
    *,
    cache_dir: Path,
    device: torch.device,
) -> tuple[torch.nn.Module, dict[str, Any]]:
    from transformers import WavLMModel

    files = resolve_local_model_files(cache_dir=cache_dir)
    model = WavLMModel.from_pretrained(
        MODEL_ID,
        revision=MODEL_REVISION,
        cache_dir=str(cache_dir),
        local_files_only=True,
    )
    commit_hash = getattr(model.config, "_commit_hash", None)
    if commit_hash != MODEL_REVISION:
        raise ValueError("loaded WavLM config does not match pinned revision")
    if (
        model.config.hidden_size != HIDDEN_SIZE
        or tuple(model.config.conv_kernel)
        != WAVLM_BASE_PLUS_FRAME_GEOMETRY.conv_kernels
        or tuple(model.config.conv_stride)
        != WAVLM_BASE_PLUS_FRAME_GEOMETRY.conv_strides
    ):
        raise ValueError("loaded WavLM architecture differs from the pin")
    model.eval()
    model.requires_grad_(False)
    model.to(device)
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise AssertionError("WavLM encoder is not frozen")
    return model, {
        "id": MODEL_ID,
        "revision": MODEL_REVISION,
        "config_sha256": sha256_file(files["config"]),
        "checkpoint_sha256": sha256_file(files["checkpoint"]),
        "hidden_size": HIDDEN_SIZE,
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "frozen": True,
        "local_files_only": True,
        "do_normalize": DO_NORMALIZE,
        "preprocessor_contract": {
            "sampling_rate_hz": SAMPLE_RATE_HZ,
            "do_normalize": DO_NORMALIZE,
            "return_attention_mask": True,
            "source": "official_pinned_revision",
            "source_url": PINNED_PREPROCESSOR_URL,
            "local_preprocessor_config_present": (
                (
                    cache_dir
                    / "models--microsoft--wavlm-base-plus"
                    / "snapshots"
                    / MODEL_REVISION
                    / "preprocessor_config.json"
                ).is_file()
            ),
            "auto_feature_extractor_used": False,
        },
    }


def _validate_source_schema(parquet: pq.ParquetFile) -> None:
    missing = sorted(set(SOURCE_COLUMNS) - set(parquet.schema_arrow.names))
    if missing:
        raise ValueError(
            "modeling manifest is missing required columns: " + ", ".join(missing)
        )


def _gpu_metadata(device: torch.device) -> dict[str, Any]:
    if device.type != "cuda":
        raise RuntimeError("the frozen WavLM cache requires a CUDA GPU")
    index = device.index if device.index is not None else torch.cuda.current_device()
    properties = torch.cuda.get_device_properties(index)
    return {
        "device_index": index,
        "name": properties.name,
        "compute_capability": (f"{properties.major}.{properties.minor}"),
        "total_memory_bytes": properties.total_memory,
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
    }


def _base_report(
    *,
    manifest_sha256: str,
    reservation_sha256: str,
    config_sha256: str,
    builder_sha256: str,
    model_metadata: Mapping[str, Any],
    gpu: Mapping[str, Any],
    output_config: Mapping[str, Any],
    limit: int | None,
    full_gate: Mapping[str, str] | None,
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "running",
        "started_at_utc": utc_now(),
        "completed_at_utc": None,
        "source": {
            "modeling_manifest_sha256": manifest_sha256,
            "prompt_reservation_sha256": reservation_sha256,
            "experiment_config_sha256": config_sha256,
            "builder_sha256": builder_sha256,
        },
        "model": dict(model_metadata),
        "runtime": {
            "inference_precision": "bfloat16",
            "storage_precision": "float16",
            "gpu": dict(gpu),
            "oom_count": 0,
        },
        "policy": {
            "allowed_splits": list(ALLOWED_SPLITS),
            "evaluation_splits": sorted(EVALUATION_SPLITS),
            "evaluation_split_metric_access": "forbidden",
            "holistic_score_columns_read": [],
            "metric_evaluation_performed": False,
            "rows_with_qc_flags_excluded": True,
            "reserved_prompts_excluded_from_every_split": True,
            "oracle_alignment": True,
            "utterance_id_namespace": UTTERANCE_HASH_NAMESPACE,
            "contains_direct_identifiers": False,
            "contains_transcript_text": False,
            "contains_audio_bytes": False,
            "contains_realized_phone_identity": False,
            "contains_alignment_operation": False,
            "access_scope": "restricted_internal",
            "shareable": False,
            "prohibited_output_columns": sorted(PROHIBITED_OUTPUT_COLUMNS),
            "column_roles": {
                "utterance": {
                    "input_feature_columns": ["embedding_mean_std"],
                    "label_columns": [],
                },
                "phone": {
                    "input_feature_columns": [
                        "embedding_mean",
                        "target_label",
                    ],
                    "label_columns": ["binary_error_label"],
                },
                "word": {
                    "input_feature_columns": ["embedding_mean"],
                    "label_columns": [],
                },
            },
        },
        "configuration": {
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "max_chunk_seconds": MAX_CHUNK_SECONDS,
            "chunking": "target_phone_label_aware",
            "limit": limit,
            "full_run": limit is None,
            "full_run_gate": dict(full_gate or {}),
            "utterance_pooling": "mean_std",
            "target_phone_pooling": "mean",
            "word_pooling": "mean",
            "frame_stride_samples": (
                WAVLM_BASE_PLUS_FRAME_GEOMETRY.frame_stride_samples
            ),
            "receptive_field_samples": (
                WAVLM_BASE_PLUS_FRAME_GEOMETRY.receptive_field_samples
            ),
            "streaming_writer": {
                kind: {
                    "shard_rows": int(output_config[f"{kind}_shard_rows"]),
                    "row_group_rows": int(output_config[f"{kind}_row_group_rows"]),
                }
                for kind in ("utterance", "phone", "word")
            },
        },
        "progress": {
            "source_rows_scanned": 0,
            "utterance_rows_written": 0,
            "phone_rows_written": 0,
            "word_rows_written": 0,
            "source_scan_complete": False,
            "source_rows_by_split": {},
            "included_rows_by_split": {},
            "exclusions": {},
        },
        "outputs": {},
    }


def _output_summary(
    *,
    shards: Sequence[Mapping[str, Any]],
    row_count: int,
) -> dict[str, Any]:
    return {
        "row_count": row_count,
        "shard_count": len(shards),
        "byte_count": sum(int(shard["byte_count"]) for shard in shards),
        "content_sha256": sha256_json(
            [
                {
                    "name": shard["name"],
                    "row_count": shard["row_count"],
                    "sha256": shard["sha256"],
                }
                for shard in shards
            ]
        ),
        "shards": list(shards),
    }


def peak_rss_bytes() -> int:
    """Return process peak RSS with platform-correct ru_maxrss units."""

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    multiplier = 1 if sys.platform == "darwin" else 1_024
    return int(value * multiplier)


def build_resource_estimate(
    *,
    outputs: Mapping[str, Mapping[str, Any]],
    full_run_gate: Mapping[str, Any],
) -> dict[str, Any]:
    reference_rows = _expect_mapping(
        full_run_gate["resource_estimate_reference_rows"],
        "resource_estimate_reference_rows",
    )
    safety_factor = float(full_run_gate["resource_estimate_safety_factor"])
    bytes_per_row: dict[str, float] = {}
    projected_by_kind: dict[str, int] = {}
    for kind in ("utterance", "phone", "word"):
        observed_rows = int(outputs[kind]["row_count"])
        observed_bytes = int(outputs[kind]["byte_count"])
        if observed_rows <= 0 or observed_bytes <= 0:
            raise ValueError(f"cannot estimate full cache from empty {kind} output")
        bytes_per_row[kind] = observed_bytes / observed_rows
        projected_by_kind[kind] = math.ceil(
            bytes_per_row[kind] * int(reference_rows[kind])
        )
    projected_total = sum(projected_by_kind.values())
    embedding_dimensions = {
        "utterance": HIDDEN_SIZE * 2,
        "phone": HIDDEN_SIZE,
        "word": HIDDEN_SIZE,
    }
    raw_embedding_bytes_by_kind = {
        kind: int(reference_rows[kind])
        * embedding_dimensions[kind]
        * np.dtype(np.float16).itemsize
        for kind in ("utterance", "phone", "word")
    }
    return {
        "method": "smoke compressed bytes per row scaled to audited full rows",
        "reference_rows": {
            kind: int(reference_rows[kind]) for kind in ("utterance", "phone", "word")
        },
        "observed_compressed_bytes_per_row": bytes_per_row,
        "projected_compressed_bytes_by_kind": projected_by_kind,
        "projected_compressed_bytes_total": projected_total,
        "fixed_float16_embedding_bytes_by_kind": (raw_embedding_bytes_by_kind),
        "fixed_float16_embedding_bytes_total": sum(
            raw_embedding_bytes_by_kind.values()
        ),
        "fixed_embedding_estimate_excludes_metadata_and_parquet_overhead": (True),
        "safety_factor": safety_factor,
        "estimated_full_cache_upper_bytes": math.ceil(projected_total * safety_factor),
        "estimate_is_capacity_planning_not_measured_full_output": True,
    }


def build_cache(args: argparse.Namespace) -> dict[str, Any]:
    config = load_and_validate_config(args.config)
    data_config = _expect_mapping(config["data"], "data")
    model_config = _expect_mapping(config["model"], "model")
    output_config = _expect_mapping(config["output"], "output")
    full_run_gate_config = _expect_mapping(
        config["full_run_gate"],
        "full_run_gate",
    )
    config_sha256 = sha256_file(args.config)
    builder_sha256 = sha256_file(Path(__file__).resolve())
    modeling_manifest = (
        args.modeling_manifest
        if args.modeling_manifest is not None
        else Path(str(data_config["modeling_manifest"]))
    )
    dataset_root = (
        args.dataset_root
        if args.dataset_root is not None
        else Path(str(data_config["dataset_root"]))
    )
    prompt_reservation = (
        args.prompt_reservation
        if args.prompt_reservation is not None
        else Path(str(data_config["prompt_challenge_reservation"]))
    )
    output_root = (
        args.output if args.output is not None else Path(str(output_config["root"]))
    )
    report_output = (
        args.report_output
        if args.report_output is not None
        else Path(str(output_config["report"]))
    )
    cache_dir = (
        args.cache_dir
        if args.cache_dir is not None
        else Path(str(model_config["cache_dir"]))
    )
    if args.limit is not None and args.limit <= 0:
        raise ValueError("limit must be positive")
    full_gate = (
        validate_full_run_gate(
            explicit_dev_report=args.explicit_dev_report,
            smoke_review=args.smoke_review,
            expected_config_sha256=config_sha256,
            expected_builder_sha256=builder_sha256,
        )
        if args.allow_full
        else None
    )
    if output_root.exists():
        raise FileExistsError("feature output root already exists")

    manifest_sha256 = sha256_file(modeling_manifest)
    if manifest_sha256 != data_config["modeling_manifest_sha256"]:
        raise ValueError("modeling manifest SHA-256 differs from the config")
    reserved, reservation_source_sha256 = load_reserved_prompt_hashes(
        prompt_reservation
    )
    if reservation_source_sha256 != manifest_sha256:
        raise ValueError("prompt reservation was built from a different manifest")
    reservation_sha256 = sha256_file(prompt_reservation)

    device = torch.device("cuda")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    gpu = _gpu_metadata(device)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats(device)
    model, model_metadata = load_frozen_model(
        cache_dir=cache_dir,
        device=device,
    )
    report = _base_report(
        manifest_sha256=manifest_sha256,
        reservation_sha256=reservation_sha256,
        config_sha256=config_sha256,
        builder_sha256=builder_sha256,
        model_metadata=model_metadata,
        gpu=gpu,
        output_config=output_config,
        limit=args.limit,
        full_gate=full_gate,
    )
    atomic_write_json(report_output, report)

    parquet = pq.ParquetFile(modeling_manifest)
    _validate_source_schema(parquet)
    schemas = {
        "utterance": schema_with_metadata(
            UTTERANCE_SCHEMA,
            kind="utterance",
            source_manifest_sha256=manifest_sha256,
            config_sha256=config_sha256,
        ),
        "phone": schema_with_metadata(
            PHONE_SCHEMA,
            kind="phone",
            source_manifest_sha256=manifest_sha256,
            config_sha256=config_sha256,
        ),
        "word": schema_with_metadata(
            WORD_SCHEMA,
            kind="word",
            source_manifest_sha256=manifest_sha256,
            config_sha256=config_sha256,
        ),
    }
    for schema in schemas.values():
        assert_schema_privacy(schema)

    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = output_root.with_name(f".{output_root.name}.part-{uuid4().hex}")
    temporary_root.mkdir()
    writers = {
        "utterance": ShardedWriter(
            temporary_root / "utterance",
            schemas["utterance"],
            shard_rows=int(output_config["utterance_shard_rows"]),
            row_group_rows=int(output_config["utterance_row_group_rows"]),
        ),
        "phone": ShardedWriter(
            temporary_root / "phone",
            schemas["phone"],
            shard_rows=int(output_config["phone_shard_rows"]),
            row_group_rows=int(output_config["phone_row_group_rows"]),
        ),
        "word": ShardedWriter(
            temporary_root / "word",
            schemas["word"],
            shard_rows=int(output_config["word_shard_rows"]),
            row_group_rows=int(output_config["word_row_group_rows"]),
        ),
    }
    source_split_counts: Counter[str] = Counter()
    included_split_counts: Counter[str] = Counter()
    exclusions: Counter[str] = Counter()
    output_dtypes: Counter[str] = Counter()
    scanned = 0
    utterance_count = 0
    phone_count = 0
    word_count = 0
    decoded_audio_seconds = 0.0
    model_frames = 0
    chunk_count = 0
    padded_short_chunks = 0
    started = time.monotonic()
    current_stage = "source_scan"
    current_id: str | None = None
    oom_count = 0

    try:
        stop = False
        for batch in parquet.iter_batches(
            batch_size=args.batch_size,
            columns=list(SOURCE_COLUMNS),
        ):
            for row in batch.to_pylist():
                scanned += 1
                split = str(row.get("split") or "")
                source_split_counts[split] += 1
                if split not in ALLOWED_SPLITS:
                    exclusions["split_not_allowed"] += 1
                    continue
                if row.get("qc_flags"):
                    exclusions["qc_flags_present"] += 1
                    continue
                if row.get("prompt_sha256") in reserved:
                    exclusions["reserved_prompt"] += 1
                    continue

                current_id = hash_utterance_id(row.get("id"))
                current_stage = "wavlm_inference"
                try:
                    extracted = extract_rows(
                        row,
                        dataset_root=dataset_root,
                        model=model,
                        device=device,
                        autocast_dtype=torch.bfloat16,
                    )
                except torch.cuda.OutOfMemoryError:
                    oom_count += 1
                    raise
                current_stage = "cache_write"
                writers["utterance"].append([extracted.utterance])
                writers["phone"].append(extracted.phones)
                writers["word"].append(extracted.words)
                utterance_count += 1
                phone_count += len(extracted.phones)
                word_count += len(extracted.words)
                decoded_audio_seconds += extracted.decoded_audio_seconds
                model_frames += extracted.model_frames
                chunk_count += extracted.chunk_count
                padded_short_chunks += extracted.padded_short_chunks
                included_split_counts[split] += 1
                output_dtypes.update(extracted.output_dtypes)

                if args.progress_every and utterance_count % args.progress_every == 0:
                    print(
                        "Frozen WavLM: "
                        f"{utterance_count:,} utterances, "
                        f"{phone_count:,} phones, {word_count:,} words",
                        flush=True,
                    )
                if args.limit is not None and utterance_count >= args.limit:
                    stop = True
                    break
            if stop:
                break

        current_stage = "finalize_shards"
        shard_results = {kind: writer.finalize() for kind, writer in writers.items()}
        os.replace(temporary_root, output_root)
        elapsed_seconds = time.monotonic() - started
        source_scan_complete = scanned == parquet.metadata.num_rows
        report["status"] = "completed"
        report["completed_at_utc"] = utc_now()
        report["elapsed_seconds"] = elapsed_seconds
        report["progress"] = {
            "source_rows_scanned": scanned,
            "utterance_rows_written": utterance_count,
            "phone_rows_written": phone_count,
            "word_rows_written": word_count,
            "decoded_audio_seconds": decoded_audio_seconds,
            "model_frame_count": model_frames,
            "chunk_count": chunk_count,
            "padded_short_chunk_count": padded_short_chunks,
            "source_scan_complete": source_scan_complete,
            "source_rows_by_split": dict(sorted(source_split_counts.items())),
            "included_rows_by_split": dict(sorted(included_split_counts.items())),
            "exclusions": dict(sorted(exclusions.items())),
        }
        report["throughput"] = {
            "utterances_per_second": utterance_count / max(elapsed_seconds, 1e-12),
            "decoded_audio_realtime_factor": elapsed_seconds
            / max(decoded_audio_seconds, 1e-12),
            "audio_realtime_multiplier": decoded_audio_seconds
            / max(elapsed_seconds, 1e-12),
            "model_frames_per_second": model_frames / max(elapsed_seconds, 1e-12),
        }
        report["runtime"].update(
            {
                "oom_count": oom_count,
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
                "peak_reserved_bytes": torch.cuda.max_memory_reserved(device),
                "peak_process_rss_bytes": peak_rss_bytes(),
                "streaming_writer_max_buffered_rows": {
                    kind: writer.max_buffered_rows for kind, writer in writers.items()
                },
                "observed_model_output_dtypes": dict(sorted(output_dtypes.items())),
            }
        )
        report["outputs"] = {
            kind: _output_summary(
                shards=shard_results[kind],
                row_count={
                    "utterance": utterance_count,
                    "phone": phone_count,
                    "word": word_count,
                }[kind],
            )
            for kind in ("utterance", "phone", "word")
        }
        report["resource_estimate"] = build_resource_estimate(
            outputs=report["outputs"],
            full_run_gate=full_run_gate_config,
        )
        report["frame_mapping_audit"] = {
            "validated_chunk_count": chunk_count,
            "all_hidden_shapes_matched_pinned_geometry": True,
            "frame_stride_samples": (
                WAVLM_BASE_PLUS_FRAME_GEOMETRY.frame_stride_samples
            ),
            "receptive_field_samples": (
                WAVLM_BASE_PLUS_FRAME_GEOMETRY.receptive_field_samples
            ),
            "frame_center_mapping": "half_open_interval_by_frame_center",
        }
        report["privacy_audit"] = {
            "schema_prohibited_column_hits": {
                kind: sorted(PROHIBITED_OUTPUT_COLUMNS & set(schema.names))
                for kind, schema in schemas.items()
            },
            "raw_locator_values_exported": False,
            "raw_file_stem_values_exported": False,
            "realized_phone_values_exported": False,
            "alignment_operation_values_exported": False,
            "word_transcript_values_exported": False,
            "phone_binary_target_is_separate_label_role": True,
        }
        atomic_write_json(report_output, report)
        return report
    except Exception as error:
        for writer in writers.values():
            writer.abort()
        shutil.rmtree(temporary_root, ignore_errors=True)
        failure_id = current_id
        if failure_id is None and isinstance(error, FeatureBuildError):
            failure_id = error.utterance_id_sha256
        report["status"] = "failed"
        report["completed_at_utc"] = utc_now()
        report["runtime"]["oom_count"] = oom_count
        report["failure"] = {
            "stage": current_stage,
            "cause_type": type(error).__name__,
            "utterance_id_sha256": failure_id,
        }
        report["progress"] = {
            "source_rows_scanned": scanned,
            "utterance_rows_written": utterance_count,
            "phone_rows_written": phone_count,
            "word_rows_written": word_count,
            "source_scan_complete": False,
            "source_rows_by_split": dict(sorted(source_split_counts.items())),
            "included_rows_by_split": dict(sorted(included_split_counts.items())),
            "exclusions": dict(sorted(exclusions.items())),
        }
        atomic_write_json(report_output, report)
        raise
    finally:
        del model
        torch.cuda.empty_cache()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Build a local-only frozen WavLM Base+ train/dev feature cache.")
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/wavlm_base_plus_frozen_v1.yaml"),
    )
    parser.add_argument("--modeling-manifest", type=Path)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--prompt-reservation", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report-output", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--limit",
        type=int,
        help="Post-filter smoke limit; use 100 for the required first smoke",
    )
    mode.add_argument(
        "--allow-full",
        action="store_true",
        help="Allow a gated full run after explicit-dev and smoke approval",
    )
    parser.add_argument("--explicit-dev-report", type=Path)
    parser.add_argument("--smoke-review", type=Path)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--progress-every", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size <= 0:
        raise ValueError("batch-size must be positive")
    if args.progress_every < 0:
        raise ValueError("progress-every must be non-negative")
    report = build_cache(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "utterance_rows": report["progress"]["utterance_rows_written"],
                "phone_rows": report["progress"]["phone_rows_written"],
                "word_rows": report["progress"]["word_rows_written"],
                "oom_count": report["runtime"]["oom_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
