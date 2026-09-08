from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]


REPORT_SCHEMA_VERSION = "voice_coach.wavlm_feature_cache_validation.v1"
CACHE_REPORT_SCHEMA_VERSION = "voice_coach.wavlm_feature_cache_report.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHARD_RE = re.compile(r"^part-(\d{5})\.parquet$")
ALLOWED_SPLITS = ("train", "dev")
AUDITED_FULL_ROWS = {
    "utterance": 169_837,
    "phone": 2_233_039,
    "word": 408_790,
}
SCHEMA_VERSIONS = {
    "utterance": "voice_coach.wavlm_utterance_features.v1",
    "phone": "voice_coach.wavlm_phone_features.v1",
    "word": "voice_coach.wavlm_word_features.v1",
}
EMBEDDING_COLUMNS = {
    "utterance": "embedding_mean_std",
    "phone": "embedding_mean",
    "word": "embedding_mean",
}
FORBIDDEN_EXACT_COLUMNS = frozenset(
    {
        "id",
        "utterance_id",
        "raw_utterance_id",
        "raw_speaker_identity",
        "audio_path",
        "json_path",
        "textgrid_path",
        "source_path",
        "source_file_stem",
        "file_stem",
        "word_label",
        "word_transcript",
        "transcript",
        "realized_label",
        "realized_phone_identity",
        "alignment_operation",
        "operation",
        "error_tag",
        "error_tags",
        "holistic_score",
        "pronunciation_score",
        "fluency_score",
        "comprehensibility_score",
    }
)
FORBIDDEN_COLUMN_TOKENS = (
    "raw_",
    "path",
    "stem",
    "realized",
    "operation",
    "transcript",
    "word_label",
    "holistic",
)
REQUIRED_METADATA = {
    "voice_coach.frozen_encoder": "true",
    "voice_coach.oracle_alignment": "true",
    "voice_coach.storage_precision": "float16",
    "voice_coach.allowed_splits": "train,dev",
    "voice_coach.metric_evaluation_performed": "false",
    "voice_coach.privacy_reviewed": "true",
    "voice_coach.contains_direct_identifiers": "false",
    "voice_coach.contains_transcript_text": "false",
    "voice_coach.contains_audio_bytes": "false",
    "voice_coach.contains_realized_phone_identity": "false",
    "voice_coach.contains_alignment_operation": "false",
    "voice_coach.access_scope": "restricted_internal",
    "voice_coach.shareable": "false",
}
EXPECTED_ROLE_METADATA = {
    "utterance": {
        "voice_coach.input_feature_columns": ["embedding_mean_std"],
        "voice_coach.label_columns": [],
    },
    "phone": {
        "voice_coach.input_feature_columns": ["embedding_mean", "target_label"],
        "voice_coach.label_columns": ["binary_error_label"],
    },
    "word": {
        "voice_coach.input_feature_columns": ["embedding_mean"],
        "voice_coach.label_columns": [],
    },
}
EXPECTED_SLICE_COLUMNS = ["split", "l1", "gender", "proficiency"]
EXPECTED_FORBIDDEN_INPUTS = [
    "binary_error_label",
    "realized_phone_identity",
    "alignment_operation",
    "error_tag",
    "holistic_score",
]
_FORBIDDEN_PATH_PARTS = {"raw", "restricted", ".env"}


class CacheValidationError(ValueError):
    """Raised for an invalid validator invocation or unreadable contract."""


@dataclass
class _Issues:
    counts: Counter[str] = field(default_factory=Counter)

    def add(self, code: str, count: int = 1) -> None:
        self.counts[code] += count

    def check(self, condition: bool, code: str) -> bool:
        if not condition:
            self.add(code)
        return condition

    def report(self) -> list[dict[str, Any]]:
        return [
            {"code": code, "count": count}
            for code, count in sorted(self.counts.items())
        ]


@dataclass
class _ScanState:
    utterances: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    child_last_id: dict[str, str | None] = field(
        default_factory=lambda: {"phone": None, "word": None}
    )
    child_next_index: dict[str, int] = field(
        default_factory=lambda: {"phone": 0, "word": 0}
    )
    child_closed_ids: dict[str, set[str]] = field(
        default_factory=lambda: {"phone": set(), "word": set()}
    )
    rows_checked: Counter[str] = field(default_factory=Counter)
    split_counts: dict[str, Counter[str]] = field(
        default_factory=lambda: {
            "utterance": Counter(),
            "phone": Counter(),
            "word": Counter(),
        }
    )
    sha256_values_checked: Counter[str] = field(default_factory=Counter)
    embedding_vectors_checked: Counter[str] = field(default_factory=Counter)
    embedding_values_checked: Counter[str] = field(default_factory=Counter)
    nonfinite_embedding_values: Counter[str] = field(default_factory=Counter)
    all_zero_embedding_vectors: Counter[str] = field(default_factory=Counter)
    joins_checked: Counter[str] = field(default_factory=Counter)
    max_batch_rows: int = 0
    max_embedding_batch_values: int = 0
    max_embedding_batch_bytes: int = 0
    max_closed_child_ids: int = 0
    utterance_frame_count_total: int = 0
    utterance_chunk_count_total: int = 0
    utterance_audio_duration_seconds_total: float = 0.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
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


def _assert_finite_tree(value: Any, *, location: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CacheValidationError(f"non-finite number in {location}")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _assert_finite_tree(child, location=f"{location}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_finite_tree(child, location=f"{location}/{index}")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(
                handle,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    CacheValidationError(f"non-finite JSON token: {token}")
                ),
            )
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheValidationError("cannot read JSON validation input") from exc
    if not isinstance(value, dict):
        raise CacheValidationError("JSON validation input must be an object")
    _assert_finite_tree(value, location="json")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CacheValidationError("cannot read YAML validation input") from exc
    if not isinstance(value, dict):
        raise CacheValidationError("YAML validation input must be an object")
    _assert_finite_tree(value, location="yaml")
    return value


def _safe_path(
    workspace_root: Path,
    configured: str | Path,
    *,
    role: str,
    require_exists: bool = True,
) -> tuple[Path, str]:
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise CacheValidationError(f"{role} must stay inside workspace") from exc
    lowered = [part.lower() for part in relative.parts]
    if any(
        part in _FORBIDDEN_PATH_PARTS or part.startswith(".env") for part in lowered
    ):
        raise CacheValidationError(f"{role} is forbidden by privacy policy")
    if require_exists and not resolved.exists():
        raise CacheValidationError(f"{role} does not exist")
    return resolved, relative.as_posix()


def _mapping(value: Any, role: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CacheValidationError(f"{role} must be an object")
    return value


def _positive_int(value: Any, role: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CacheValidationError(f"{role} must be a positive integer")
    return value


def _expected_schema(kind: str, hidden_size: int) -> pa.Schema:
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
        return pa.schema(
            [
                *common,
                pa.field("audio_duration_seconds", pa.float64(), nullable=False),
                pa.field("chunk_count", pa.int16(), nullable=False),
                pa.field("frame_count", pa.int32(), nullable=False),
                pa.field(
                    "embedding_mean_std",
                    pa.list_(pa.float16(), hidden_size * 2),
                    nullable=False,
                ),
            ]
        )
    if kind == "phone":
        return pa.schema(
            [
                *common,
                pa.field("phone_index", pa.int32(), nullable=False),
                pa.field("target_label", pa.string(), nullable=False),
                pa.field("oracle_start_seconds", pa.float64(), nullable=False),
                pa.field("oracle_end_seconds", pa.float64(), nullable=False),
                pa.field("pooled_frame_count", pa.int16(), nullable=False),
                pa.field(
                    "embedding_mean",
                    pa.list_(pa.float16(), hidden_size),
                    nullable=False,
                ),
                pa.field("binary_error_label", pa.int8(), nullable=False),
            ]
        )
    if kind == "word":
        return pa.schema(
            [
                *common,
                pa.field("word_index", pa.int32(), nullable=False),
                pa.field("oracle_start_seconds", pa.float64(), nullable=False),
                pa.field("oracle_end_seconds", pa.float64(), nullable=False),
                pa.field("pooled_frame_count", pa.int16(), nullable=False),
                pa.field(
                    "embedding_mean",
                    pa.list_(pa.float16(), hidden_size),
                    nullable=False,
                ),
            ]
        )
    raise CacheValidationError(f"unsupported cache kind: {kind}")


def _decode_metadata(schema: pa.Schema, issues: _Issues) -> dict[str, str]:
    output: dict[str, str] = {}
    for raw_key, raw_value in (schema.metadata or {}).items():
        try:
            output[raw_key.decode("utf-8")] = raw_value.decode("utf-8")
        except UnicodeDecodeError:
            issues.add("schema_metadata_not_utf8")
    return output


def _validate_schema(
    *,
    schema: pa.Schema,
    kind: str,
    hidden_size: int,
    model: Mapping[str, Any],
    source: Mapping[str, Any],
    config_sha256: str,
    issues: _Issues,
) -> None:
    expected = _expected_schema(kind, hidden_size)
    issues.check(
        schema.equals(expected, check_metadata=False),
        f"{kind}_schema_mismatch",
    )
    lowered_names = [name.lower() for name in schema.names]
    prohibited_hits = {
        name
        for name in lowered_names
        if name in FORBIDDEN_EXACT_COLUMNS
        or any(token in name for token in FORBIDDEN_COLUMN_TOKENS)
    }
    if prohibited_hits:
        issues.add(f"{kind}_privacy_prohibited_column", len(prohibited_hits))
    metadata = _decode_metadata(schema, issues)
    expected_metadata = {
        **REQUIRED_METADATA,
        "voice_coach.schema_version": SCHEMA_VERSIONS[kind],
        "voice_coach.model_id": str(model.get("id")),
        "voice_coach.model_revision": str(model.get("revision")),
        "voice_coach.source_manifest_sha256": str(
            source.get("modeling_manifest_sha256")
        ),
        "voice_coach.config_sha256": config_sha256,
    }
    for key, expected_value in expected_metadata.items():
        issues.check(
            metadata.get(key) == expected_value,
            f"{kind}_schema_metadata_mismatch",
        )
    for key, expected_role_value in EXPECTED_ROLE_METADATA[kind].items():
        try:
            actual = json.loads(metadata.get(key, "null"))
        except json.JSONDecodeError:
            actual = None
        issues.check(
            actual == expected_role_value,
            f"{kind}_schema_role_metadata_mismatch",
        )
    for key, expected_role_value in (
        ("voice_coach.slice_only_columns", EXPECTED_SLICE_COLUMNS),
        ("voice_coach.forbidden_as_input", EXPECTED_FORBIDDEN_INPUTS),
    ):
        try:
            actual = json.loads(metadata.get(key, "null"))
        except json.JSONDecodeError:
            actual = None
        issues.check(
            actual == expected_role_value,
            f"{kind}_schema_role_metadata_mismatch",
        )


def _arrow_values(array: pa.Array) -> np.ndarray[Any, Any]:
    return np.asarray(array.to_numpy(zero_copy_only=False))


def _check_sha_values(
    values: Sequence[Any],
    *,
    role: str,
    state: _ScanState,
    issues: _Issues,
) -> None:
    state.sha256_values_checked[role] += len(values)
    invalid = sum(
        not isinstance(value, str) or SHA256_RE.fullmatch(value) is None
        for value in values
    )
    if invalid:
        issues.add(f"{role}_invalid_sha256", invalid)


def _scan_embedding(
    array: pa.Array,
    *,
    kind: str,
    dimension: int,
    state: _ScanState,
    issues: _Issues,
) -> None:
    if not pa.types.is_fixed_size_list(array.type):
        issues.add(f"{kind}_embedding_not_fixed_size_list")
        return
    if array.type.list_size != dimension or not pa.types.is_float16(
        array.type.value_type
    ):
        issues.add(f"{kind}_embedding_type_mismatch")
        return
    if array.null_count:
        issues.add(f"{kind}_embedding_null_vector", array.null_count)
    values = array.values.slice(array.offset * dimension, len(array) * dimension)
    if values.null_count:
        issues.add(f"{kind}_embedding_null_value", values.null_count)
    matrix = np.asarray(values.to_numpy(zero_copy_only=False), dtype=np.float16)
    state.embedding_vectors_checked[kind] += len(array)
    state.embedding_values_checked[kind] += matrix.size
    state.max_embedding_batch_values = max(
        state.max_embedding_batch_values,
        int(matrix.size),
    )
    state.max_embedding_batch_bytes = max(
        state.max_embedding_batch_bytes,
        int(matrix.nbytes),
    )
    finite = np.isfinite(matrix)
    nonfinite = int(matrix.size - np.count_nonzero(finite))
    if nonfinite:
        state.nonfinite_embedding_values[kind] += nonfinite
        issues.add(f"{kind}_embedding_nonfinite", nonfinite)
    if matrix.size == len(array) * dimension and len(array):
        reshaped = matrix.reshape(len(array), dimension)
        all_zero = int(np.count_nonzero(np.all(reshaped == 0, axis=1)))
        if all_zero:
            state.all_zero_embedding_vectors[kind] += all_zero
            issues.add(f"{kind}_embedding_all_zero", all_zero)


def _check_child_sequence(
    *,
    kind: str,
    utterance_id: Any,
    index: Any,
    state: _ScanState,
    issues: _Issues,
) -> None:
    if not isinstance(utterance_id, str):
        issues.add(f"{kind}_invalid_utterance_id")
        return
    last = state.child_last_id[kind]
    if utterance_id != last:
        if last is not None:
            state.child_closed_ids[kind].add(last)
        if utterance_id in state.child_closed_ids[kind]:
            issues.add(f"{kind}_duplicate_or_reappearing_key")
        state.child_last_id[kind] = utterance_id
        state.child_next_index[kind] = 0
    expected = state.child_next_index[kind]
    if isinstance(index, bool) or not isinstance(index, int) or index != expected:
        issues.add(f"{kind}_index_not_contiguous")
    state.child_next_index[kind] = expected + 1
    state.max_closed_child_ids = max(
        state.max_closed_child_ids,
        len(state.child_closed_ids[kind]),
    )


def _scan_batch(
    batch: pa.RecordBatch,
    *,
    kind: str,
    hidden_size: int,
    state: _ScanState,
    issues: _Issues,
) -> None:
    row_count = batch.num_rows
    state.rows_checked[kind] += row_count
    state.max_batch_rows = max(state.max_batch_rows, row_count)
    for index, schema_field in enumerate(batch.schema):
        if not schema_field.nullable and batch.column(index).null_count:
            issues.add(f"{kind}_nonnullable_column_has_nulls")
    columns = {
        name: batch.column(index) for index, name in enumerate(batch.schema.names)
    }
    schema_versions = columns["schema_version"].to_pylist()
    invalid_versions = sum(value != SCHEMA_VERSIONS[kind] for value in schema_versions)
    if invalid_versions:
        issues.add(f"{kind}_row_schema_version_mismatch", invalid_versions)
    oracle_values = columns["oracle_alignment"].to_pylist()
    invalid_oracle = sum(value is not True for value in oracle_values)
    if invalid_oracle:
        issues.add(f"{kind}_oracle_alignment_not_true", invalid_oracle)
    utterance_ids = columns["utterance_id_sha256"].to_pylist()
    speaker_ids = columns["speaker_id_sha256"].to_pylist()
    prompt_ids = columns["prompt_sha256"].to_pylist()
    splits = columns["split"].to_pylist()
    _check_sha_values(
        utterance_ids,
        role="utterance_id",
        state=state,
        issues=issues,
    )
    _check_sha_values(
        speaker_ids,
        role="speaker_id",
        state=state,
        issues=issues,
    )
    _check_sha_values(
        prompt_ids,
        role="prompt_id",
        state=state,
        issues=issues,
    )
    for split in splits:
        state.split_counts[kind][str(split)] += 1
        if split not in ALLOWED_SPLITS:
            issues.add(f"{kind}_forbidden_split_row")
    dimension = hidden_size * 2 if kind == "utterance" else hidden_size
    _scan_embedding(
        columns[EMBEDDING_COLUMNS[kind]],
        kind=kind,
        dimension=dimension,
        state=state,
        issues=issues,
    )
    if kind == "utterance":
        durations = _arrow_values(columns["audio_duration_seconds"])
        chunks = _arrow_values(columns["chunk_count"])
        frames = _arrow_values(columns["frame_count"])
        if np.all(np.isfinite(durations)):
            state.utterance_audio_duration_seconds_total += float(durations.sum())
        state.utterance_chunk_count_total += int(chunks.sum())
        state.utterance_frame_count_total += int(frames.sum())
        invalid_numeric = int(
            np.count_nonzero(~np.isfinite(durations))
            + np.count_nonzero(durations <= 0)
            + np.count_nonzero(chunks <= 0)
            + np.count_nonzero(frames <= 0)
        )
        if invalid_numeric:
            issues.add("utterance_invalid_numeric_contract", invalid_numeric)
        for uid, split, speaker, prompt in zip(
            utterance_ids,
            splits,
            speaker_ids,
            prompt_ids,
            strict=True,
        ):
            if not isinstance(uid, str):
                continue
            if uid in state.utterances:
                issues.add("utterance_duplicate_key")
            else:
                state.utterances[uid] = (
                    str(split),
                    str(speaker),
                    str(prompt),
                )
        return
    index_column = "phone_index" if kind == "phone" else "word_index"
    indexes = columns[index_column].to_pylist()
    starts = _arrow_values(columns["oracle_start_seconds"])
    ends = _arrow_values(columns["oracle_end_seconds"])
    pooled_frames = _arrow_values(columns["pooled_frame_count"])
    invalid_numeric = int(
        np.count_nonzero(~np.isfinite(starts))
        + np.count_nonzero(~np.isfinite(ends))
        + np.count_nonzero(starts < 0)
        + np.count_nonzero(ends <= starts)
        + np.count_nonzero(pooled_frames <= 0)
    )
    if invalid_numeric:
        issues.add(f"{kind}_invalid_numeric_contract", invalid_numeric)
    if kind == "phone":
        labels = columns["target_label"].to_pylist()
        invalid_labels = sum(
            not isinstance(value, str) or not value for value in labels
        )
        if invalid_labels:
            issues.add("phone_empty_target_label", invalid_labels)
        binary = _arrow_values(columns["binary_error_label"])
        invalid_binary = int(np.count_nonzero(~np.isin(binary, [0, 1])))
        if invalid_binary:
            issues.add("phone_invalid_binary_label", invalid_binary)
    for uid, split, speaker, prompt, child_index in zip(
        utterance_ids,
        splits,
        speaker_ids,
        prompt_ids,
        indexes,
        strict=True,
    ):
        _check_child_sequence(
            kind=kind,
            utterance_id=uid,
            index=child_index,
            state=state,
            issues=issues,
        )
        state.joins_checked[kind] += 1
        parent = state.utterances.get(uid) if isinstance(uid, str) else None
        if parent is None:
            issues.add(f"{kind}_utterance_join_missing")
            continue
        if parent != (str(split), str(speaker), str(prompt)):
            issues.add(f"{kind}_utterance_join_mismatch")


def _content_hash_items(shards: Sequence[Mapping[str, Any]]) -> str:
    return sha256_json(
        [
            {
                "name": item.get("name"),
                "row_count": item.get("row_count"),
                "sha256": item.get("sha256"),
            }
            for item in shards
        ]
    )


def _validate_kind(
    *,
    kind: str,
    cache_root: Path,
    output_report: Mapping[str, Any],
    config_output: Mapping[str, Any],
    hidden_size: int,
    model: Mapping[str, Any],
    source: Mapping[str, Any],
    config_sha256: str,
    batch_rows: int,
    state: _ScanState,
    issues: _Issues,
) -> dict[str, Any]:
    directory = cache_root / kind
    if not directory.is_dir() or directory.is_symlink():
        issues.add(f"{kind}_directory_missing_or_symlink")
        return {
            "shards_checked": 0,
            "rows_checked": 0,
            "bytes_checked": 0,
            "max_row_group_rows": 0,
        }
    reported = output_report.get(kind)
    if not isinstance(reported, Mapping):
        issues.add(f"{kind}_output_report_missing")
        return {
            "shards_checked": 0,
            "rows_checked": 0,
            "bytes_checked": 0,
            "max_row_group_rows": 0,
        }
    reported_shards = reported.get("shards")
    if not isinstance(reported_shards, list) or not reported_shards:
        issues.add(f"{kind}_reported_shards_invalid")
        return {
            "shards_checked": 0,
            "rows_checked": 0,
            "bytes_checked": 0,
            "max_row_group_rows": 0,
        }
    report_names = [
        item.get("name") if isinstance(item, Mapping) else None
        for item in reported_shards
    ]
    expected_names = [f"part-{index:05d}.parquet" for index in range(len(report_names))]
    issues.check(report_names == expected_names, f"{kind}_shard_names_not_contiguous")
    actual_paths = sorted(directory.glob("*.parquet"))
    actual_names = [path.name for path in actual_paths]
    issues.check(actual_names == report_names, f"{kind}_shard_set_mismatch")
    unexpected_entries = [
        path
        for path in directory.iterdir()
        if not path.is_file() or SHARD_RE.fullmatch(path.name) is None
    ]
    if unexpected_entries:
        issues.add(f"{kind}_unexpected_cache_entry", len(unexpected_entries))
    shard_limit = _positive_int(config_output.get(f"{kind}_shard_rows"), "shard limit")
    row_group_limit = _positive_int(
        config_output.get(f"{kind}_row_group_rows"),
        "row-group limit",
    )
    actual_total_rows = 0
    actual_total_bytes = 0
    max_row_group_rows = 0
    for shard_index, item in enumerate(reported_shards):
        if not isinstance(item, Mapping):
            issues.add(f"{kind}_shard_report_invalid")
            continue
        name = item.get("name")
        if not isinstance(name, str) or SHARD_RE.fullmatch(name) is None:
            issues.add(f"{kind}_shard_name_invalid")
            continue
        path = directory / name
        if not path.is_file() or path.is_symlink():
            issues.add(f"{kind}_shard_missing_or_symlink")
            continue
        actual_bytes = path.stat().st_size
        actual_sha256 = sha256_file(path)
        actual_total_bytes += actual_bytes
        issues.check(
            item.get("byte_count") == actual_bytes, f"{kind}_shard_size_mismatch"
        )
        issues.check(item.get("sha256") == actual_sha256, f"{kind}_shard_hash_mismatch")
        try:
            parquet = pq.ParquetFile(path)
        except Exception:
            issues.add(f"{kind}_parquet_open_failed")
            continue
        metadata = parquet.metadata
        actual_rows = metadata.num_rows
        actual_total_rows += actual_rows
        issues.check(
            item.get("row_count") == actual_rows, f"{kind}_shard_rows_mismatch"
        )
        issues.check(
            item.get("row_group_count") == metadata.num_row_groups,
            f"{kind}_row_group_count_mismatch",
        )
        issues.check(
            actual_rows <= shard_limit,
            f"{kind}_shard_row_limit_exceeded",
        )
        if shard_index < len(reported_shards) - 1:
            issues.check(
                actual_rows == shard_limit,
                f"{kind}_nonfinal_shard_not_full",
            )
        _validate_schema(
            schema=parquet.schema_arrow,
            kind=kind,
            hidden_size=hidden_size,
            model=model,
            source=source,
            config_sha256=config_sha256,
            issues=issues,
        )
        for row_group_index in range(metadata.num_row_groups):
            row_group_rows = metadata.row_group(row_group_index).num_rows
            max_row_group_rows = max(max_row_group_rows, row_group_rows)
            issues.check(
                0 < row_group_rows <= row_group_limit,
                f"{kind}_row_group_limit_exceeded",
            )
            try:
                batches = parquet.iter_batches(
                    batch_size=min(batch_rows, row_group_limit),
                    row_groups=[row_group_index],
                    columns=parquet.schema_arrow.names,
                    use_threads=False,
                )
                for batch in batches:
                    _scan_batch(
                        batch,
                        kind=kind,
                        hidden_size=hidden_size,
                        state=state,
                        issues=issues,
                    )
            except Exception:
                issues.add(f"{kind}_row_group_scan_failed")
    issues.check(
        reported.get("shard_count") == len(reported_shards),
        f"{kind}_reported_shard_count_mismatch",
    )
    issues.check(
        reported.get("row_count") == actual_total_rows,
        f"{kind}_reported_total_rows_mismatch",
    )
    issues.check(
        reported.get("byte_count") == actual_total_bytes,
        f"{kind}_reported_total_bytes_mismatch",
    )
    issues.check(
        reported.get("content_sha256") == _content_hash_items(reported_shards),
        f"{kind}_content_hash_mismatch",
    )
    return {
        "shards_checked": len(actual_paths),
        "rows_checked": state.rows_checked[kind],
        "bytes_checked": actual_total_bytes,
        "max_row_group_rows": max_row_group_rows,
        "configured_shard_row_limit": shard_limit,
        "configured_row_group_row_limit": row_group_limit,
    }


def _validate_model_files(
    *,
    workspace_root: Path,
    config_model: Mapping[str, Any],
    report_model: Mapping[str, Any],
    issues: _Issues,
) -> dict[str, Any]:
    model_id = config_model.get("id")
    revision = config_model.get("revision")
    if (
        not isinstance(model_id, str)
        or not model_id
        or not isinstance(revision, str)
        or not revision
        or "/" in revision
    ):
        raise CacheValidationError("model id/revision contract is invalid")
    cache_dir, _ = _safe_path(
        workspace_root,
        str(config_model.get("cache_dir")),
        role="model cache",
    )
    repository = "models--" + model_id.replace("/", "--")
    snapshot = cache_dir / repository / "snapshots" / revision
    config_path = snapshot / str(config_model.get("config_file", "config.json"))
    expected_config_sha = report_model.get("config_sha256")
    config_ok = config_path.is_file()
    actual_config_sha: str | None = None
    if config_ok:
        resolved = config_path.resolve()
        try:
            resolved.relative_to(workspace_root)
        except ValueError:
            config_ok = False
    if config_ok:
        actual_config_sha = sha256_file(config_path)
    issues.check(config_ok, "model_config_file_missing_or_unsafe")
    issues.check(
        isinstance(expected_config_sha, str)
        and expected_config_sha == actual_config_sha,
        "model_config_hash_mismatch",
    )
    expected_checkpoint_sha = report_model.get("checkpoint_sha256")
    checkpoint_file = config_model.get("checkpoint_file")
    if isinstance(checkpoint_file, str):
        candidates = [snapshot / checkpoint_file]
    else:
        candidates = sorted(
            {
                *snapshot.glob("*.bin"),
                *snapshot.glob("*.safetensors"),
            }
        )
    checkpoint_match = False
    checkpoint_bytes = 0
    candidates_hashed = 0
    for candidate in candidates:
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(workspace_root)
        except ValueError:
            issues.add("model_checkpoint_file_unsafe")
            continue
        candidates_hashed += 1
        actual_sha = sha256_file(candidate)
        if actual_sha == expected_checkpoint_sha:
            checkpoint_match = True
            checkpoint_bytes = candidate.stat().st_size
            break
    issues.check(
        isinstance(expected_checkpoint_sha, str)
        and SHA256_RE.fullmatch(expected_checkpoint_sha) is not None,
        "model_checkpoint_report_hash_invalid",
    )
    issues.check(checkpoint_match, "model_checkpoint_hash_mismatch")
    return {
        "config_file_checked": config_ok,
        "config_sha256": actual_config_sha,
        "checkpoint_candidates_hashed": candidates_hashed,
        "checkpoint_match": checkpoint_match,
        "checkpoint_bytes": checkpoint_bytes,
    }


def validate_wavlm_feature_cache(
    *,
    cache_root: str | Path,
    report_path: str | Path,
    config_path: str | Path,
    builder_path: str | Path,
    workspace_root: str | Path,
    batch_rows: int = 2_048,
    expected_rows: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    if batch_rows <= 0:
        raise CacheValidationError("batch_rows must be positive")
    cache, cache_relative = _safe_path(root, cache_root, role="cache root")
    source_report_path, source_report_relative = _safe_path(
        root,
        report_path,
        role="cache report",
    )
    config_file, config_relative = _safe_path(root, config_path, role="config")
    builder_file, builder_relative = _safe_path(root, builder_path, role="builder")
    if not cache.is_dir():
        raise CacheValidationError("cache root must be a directory")
    report = _load_json(source_report_path)
    config = _load_yaml(config_file)
    issues = _Issues()
    config_sha256 = sha256_file(config_file)
    builder_sha256 = sha256_file(builder_file)
    source_report_sha256 = sha256_file(source_report_path)
    config_model = _mapping(config.get("model"), "config.model")
    config_data = _mapping(config.get("data"), "config.data")
    config_features = _mapping(config.get("features"), "config.features")
    config_output = _mapping(config.get("output"), "config.output")
    full_gate = _mapping(config.get("full_run_gate"), "config.full_run_gate")
    report_source = _mapping(report.get("source"), "report.source")
    report_model = _mapping(report.get("model"), "report.model")
    report_policy = _mapping(report.get("policy"), "report.policy")
    report_configuration = _mapping(
        report.get("configuration"),
        "report.configuration",
    )
    progress = _mapping(report.get("progress"), "report.progress")
    outputs = _mapping(report.get("outputs"), "report.outputs")
    hidden_size = _positive_int(config_model.get("hidden_size"), "model.hidden_size")
    rows_expected = dict(expected_rows or AUDITED_FULL_ROWS)
    if set(rows_expected) != {"utterance", "phone", "word"} or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in rows_expected.values()
    ):
        raise CacheValidationError("expected row contract is invalid")
    reference_rows = _mapping(
        full_gate.get("resource_estimate_reference_rows"),
        "full-run reference rows",
    )
    for kind, expected in rows_expected.items():
        issues.check(
            reference_rows.get(kind) == expected,
            f"{kind}_config_audited_row_count_mismatch",
        )
    issues.check(
        report.get("schema_version") == CACHE_REPORT_SCHEMA_VERSION,
        "cache_report_schema_version_mismatch",
    )
    issues.check(report.get("status") == "completed", "cache_report_not_completed")
    issues.check(
        report_configuration.get("full_run") is True,
        "cache_report_not_full_run",
    )
    issues.check(
        report_configuration.get("limit") is None,
        "cache_report_limit_not_null",
    )
    reported_writer = report_configuration.get("streaming_writer")
    if not isinstance(reported_writer, Mapping):
        issues.add("report_streaming_writer_missing")
    else:
        for kind in ("utterance", "phone", "word"):
            reported_kind = reported_writer.get(kind)
            issues.check(
                isinstance(reported_kind, Mapping)
                and reported_kind.get("shard_rows")
                == config_output.get(f"{kind}_shard_rows")
                and reported_kind.get("row_group_rows")
                == config_output.get(f"{kind}_row_group_rows"),
                f"{kind}_report_streaming_writer_mismatch",
            )
    reported_full_gate = report_configuration.get("full_run_gate")
    issues.check(
        isinstance(reported_full_gate, Mapping)
        and reported_full_gate.get("approved_config_sha256") == config_sha256
        and reported_full_gate.get("approved_builder_sha256") == builder_sha256,
        "full_run_gate_hash_mismatch",
    )
    issues.check(
        progress.get("source_scan_complete") is True,
        "source_scan_not_complete",
    )
    issues.check(
        list(config_data.get("allowed_splits", [])) == list(ALLOWED_SPLITS),
        "config_allowed_splits_mismatch",
    )
    issues.check(
        report_policy.get("allowed_splits") == list(ALLOWED_SPLITS),
        "report_allowed_splits_mismatch",
    )
    issues.check(
        report_policy.get("evaluation_split_metric_access") == "forbidden",
        "evaluation_split_metric_access_not_forbidden",
    )
    issues.check(
        report_policy.get("metric_evaluation_performed") is False,
        "metric_evaluation_was_performed",
    )
    issues.check(
        report_policy.get("holistic_score_columns_read") == [],
        "holistic_score_column_accessed",
    )
    for key in (
        "contains_direct_identifiers",
        "contains_transcript_text",
        "contains_audio_bytes",
        "contains_realized_phone_identity",
        "contains_alignment_operation",
        "shareable",
    ):
        issues.check(report_policy.get(key) is False, f"report_policy_{key}_invalid")
    issues.check(
        report_policy.get("oracle_alignment") is True,
        "report_oracle_alignment_not_true",
    )
    issues.check(
        config_features.get("storage_precision") == "float16",
        "config_storage_precision_not_float16",
    )
    issues.check(
        report_source.get("experiment_config_sha256") == config_sha256,
        "config_hash_mismatch",
    )
    issues.check(
        report_source.get("builder_sha256") == builder_sha256,
        "builder_hash_mismatch",
    )
    pinned_manifest_hash = config_data.get("modeling_manifest_sha256")
    issues.check(
        isinstance(pinned_manifest_hash, str)
        and SHA256_RE.fullmatch(pinned_manifest_hash) is not None,
        "config_source_manifest_hash_invalid",
    )
    issues.check(
        report_source.get("modeling_manifest_sha256") == pinned_manifest_hash,
        "source_manifest_hash_mismatch",
    )
    prompt_path, prompt_relative = _safe_path(
        root,
        str(config_data.get("prompt_challenge_reservation")),
        role="prompt reservation",
    )
    prompt_sha256 = sha256_file(prompt_path)
    issues.check(
        report_source.get("prompt_reservation_sha256") == prompt_sha256,
        "prompt_reservation_hash_mismatch",
    )
    for key in ("id", "revision", "hidden_size"):
        issues.check(
            report_model.get(key) == config_model.get(key),
            f"model_{key}_mismatch",
        )
    issues.check(report_model.get("frozen") is True, "model_not_frozen")
    issues.check(
        report_model.get("local_files_only") is True,
        "model_not_local_only",
    )
    model_files = _validate_model_files(
        workspace_root=root,
        config_model=config_model,
        report_model=report_model,
        issues=issues,
    )
    privacy_audit = report.get("privacy_audit")
    if not isinstance(privacy_audit, Mapping):
        issues.add("privacy_audit_missing")
    else:
        hits = privacy_audit.get("schema_prohibited_column_hits")
        issues.check(
            isinstance(hits, Mapping)
            and all(hits.get(kind) == [] for kind in rows_expected),
            "reported_schema_prohibited_column_hits",
        )
        for key in (
            "raw_locator_values_exported",
            "raw_file_stem_values_exported",
            "realized_phone_values_exported",
            "alignment_operation_values_exported",
            "word_transcript_values_exported",
        ):
            issues.check(
                privacy_audit.get(key) is False,
                f"privacy_audit_{key}_invalid",
            )
    if any(cache.rglob("*.part")) or any(cache.parent.glob(f".{cache.name}.part-*")):
        issues.add("partial_publish_artifact_present")
    state = _ScanState()
    shard_reports: dict[str, Any] = {}
    for kind in ("utterance", "phone", "word"):
        shard_reports[kind] = _validate_kind(
            kind=kind,
            cache_root=cache,
            output_report=outputs,
            config_output=config_output,
            hidden_size=hidden_size,
            model=config_model,
            source=report_source,
            config_sha256=config_sha256,
            batch_rows=batch_rows,
            state=state,
            issues=issues,
        )
        issues.check(
            state.rows_checked[kind] == rows_expected[kind],
            f"{kind}_audited_row_count_mismatch",
        )
        issues.check(
            state.embedding_vectors_checked[kind] == state.rows_checked[kind],
            f"{kind}_embedding_vector_count_mismatch",
        )
        for split in ALLOWED_SPLITS:
            issues.check(
                state.split_counts[kind].get(split, 0) > 0,
                f"{kind}_required_split_missing",
            )
        forbidden_count = sum(
            count
            for split, count in state.split_counts[kind].items()
            if split not in ALLOWED_SPLITS
        )
        issues.check(forbidden_count == 0, f"{kind}_forbidden_split_count_nonzero")
    included_by_split = progress.get("included_rows_by_split")
    issues.check(
        isinstance(included_by_split, Mapping)
        and dict(included_by_split) == dict(state.split_counts["utterance"]),
        "report_included_split_counts_mismatch",
    )
    for kind, progress_key in (
        ("utterance", "utterance_rows_written"),
        ("phone", "phone_rows_written"),
        ("word", "word_rows_written"),
    ):
        issues.check(
            progress.get(progress_key) == rows_expected[kind],
            f"{kind}_progress_row_count_mismatch",
        )
    expected_dimensions = {
        "utterance": hidden_size * 2,
        "phone": hidden_size,
        "word": hidden_size,
    }
    errors = issues.report()
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "passed" if not errors else "failed",
        "privacy": {
            "raw_data_read": False,
            "source_modeling_manifest_read": False,
            "environment_files_read": False,
            "aggregate_only_output": True,
            "row_identifiers_exported": False,
        },
        "inputs": {
            "cache_root": cache_relative,
            "cache_report": {
                "path": source_report_relative,
                "sha256": source_report_sha256,
            },
            "config": {
                "path": config_relative,
                "sha256": config_sha256,
            },
            "builder": {
                "path": builder_relative,
                "sha256": builder_sha256,
            },
            "prompt_reservation": {
                "path": prompt_relative,
                "sha256": prompt_sha256,
            },
            "source_modeling_manifest": {
                "read": False,
                "pinned_sha256": pinned_manifest_hash,
            },
        },
        "contract": {
            "full_run": True,
            "allowed_splits": list(ALLOWED_SPLITS),
            "expected_rows": rows_expected,
            "hidden_size": hidden_size,
            "embedding_dimensions": expected_dimensions,
            "storage_type": "float16",
        },
        "files": {
            "model": model_files,
            "cache": shard_reports,
        },
        "scan": {
            "rows_checked": dict(state.rows_checked),
            "split_counts": {
                kind: dict(sorted(counts.items()))
                for kind, counts in state.split_counts.items()
            },
            "forbidden_split_rows": {
                kind: sum(
                    count
                    for split, count in counts.items()
                    if split not in ALLOWED_SPLITS
                )
                for kind, counts in state.split_counts.items()
            },
            "sha256_values_checked": dict(state.sha256_values_checked),
            "embedding_vectors_checked": dict(state.embedding_vectors_checked),
            "embedding_values_checked": dict(state.embedding_values_checked),
            "nonfinite_embedding_values": {
                kind: state.nonfinite_embedding_values[kind]
                for kind in ("utterance", "phone", "word")
            },
            "all_zero_embedding_vectors": {
                kind: state.all_zero_embedding_vectors[kind]
                for kind in ("utterance", "phone", "word")
            },
            "child_joins_checked": dict(state.joins_checked),
            "utterance_join_entries": len(state.utterances),
        },
        "memory": {
            "strategy": "one Parquet row group at a time",
            "full_embedding_matrix_materialized": False,
            "max_batch_rows": state.max_batch_rows,
            "max_embedding_batch_values": state.max_embedding_batch_values,
            "max_embedding_batch_bytes": state.max_embedding_batch_bytes,
            "utterance_join_entries": len(state.utterances),
            "max_closed_child_id_entries": state.max_closed_child_ids,
        },
        "errors": errors,
    }


def write_json_atomic(path: str | Path, value: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.part")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "AUDITED_FULL_ROWS",
    "CacheValidationError",
    "validate_wavlm_feature_cache",
    "write_json_atomic",
]
