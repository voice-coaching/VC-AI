"""Bounded production runner and deep validator for B07 GOP.

The canonical path audits two aligned train/dev phone caches without opening a
raw manifest.  Embeddings are read from Arrow fixed-size-list value buffers in
bounded float32 batches.  Train and dev scanners are physically separate:
temperature, C, and threshold selection finish before the single dev
projection pass for each seed.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import zip_longest
import math
import os
from pathlib import Path
import resource
import shutil
import sys
import tempfile
import time
from typing import Any, Final, Protocol
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.dataset as ds  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scipy import sparse  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import (  # type: ignore[import-untyped]
    average_precision_score,
    f1_score,
)
from threadpoolctl import (  # type: ignore[import-untyped]
    threadpool_info,
    threadpool_limits,
)

from voice_coach.experiments.registry import (
    collect_git_metadata,
    collect_runtime_metadata,
    fingerprint_file,
)
from voice_coach.features.gop import (
    PHONE_INVENTORY,
    assign_speaker_fold,
    gop_features_from_log_probabilities,
    l2_normalize_rows,
)
from voice_coach.training.b07_gop_contract import (
    ALL_NUMERIC_FEATURES,
    ARTIFACT_LAYOUT,
    CATEGORICAL_FEATURES,
    DETECTOR_VALIDATION_FOLDS,
    EXPECTED_ROWS,
    EXPERIMENT_ID,
    EXPLICIT_NUMERIC_FEATURES,
    FOLD_COUNT,
    FOLD_NAMESPACE,
    GOP_NUMERIC_FEATURES,
    PRIMARY_GENDER,
    PRIMARY_L1,
    REGULARIZATION_CS,
    SEEDS,
    TEMPERATURES,
    canonical_json_bytes,
    load_config,
    load_json_strict,
    sha256_file,
    sha256_json,
    threshold_values,
    validate_config,
    validate_implementation_sources,
)


FloatArray = NDArray[np.float64]
Float32Array = NDArray[np.float32]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]
SCHEMA_VERSIONS: Final = {
    "execution_lock": "voice_coach.b07_gop_execution_lock.v1",
    "acoustic_checkpoint": "voice_coach.b07_gop_acoustic_checkpoint.v1",
    "error_checkpoint": "voice_coach.b07_gop_error_checkpoint.v1",
    "selection_trace": "voice_coach.b07_gop_selection_trace.v1",
    "predictions": "voice_coach.b07_gop_dev_predictions.v1",
    "metrics": "voice_coach.b07_gop_metrics.v1",
    "slices": "voice_coach.b07_gop_slices.v1",
    "aggregate": "voice_coach.b07_gop_aggregate.v1",
    "paired": "voice_coach.b07_gop_paired_explicit_refit_control.v1",
    "registry": "voice_coach.b07_gop_registry.v1",
    "commit": "voice_coach.b07_gop_commit.v1",
}
WAVLM_AUDIT_COLUMNS: Final = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "split",
    "phone_index",
    "target_label",
    "binary_error_label",
    "l1",
    "gender",
    "proficiency",
)
EXPLICIT_COLUMNS: Final = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "split",
    "phone_index",
    "target_label",
    "binary_error_label",
    "previous_target_label",
    "next_target_label",
    "oracle_duration_seconds",
    "segment_rms",
    "segment_zero_crossing_rate",
    "l1",
    "gender",
    "proficiency",
)
JOIN_EQUAL_COLUMNS: Final = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "split",
    "phone_index",
    "target_label",
    "binary_error_label",
)
PREDICTION_COLUMNS: Final = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "target_label",
    "binary_error_label",
    "l1",
    "gender",
    "proficiency",
    "target_log_posterior",
    "gop_margin",
    "posterior_entropy",
    "target_is_top1",
    "gop_probability",
    "gop_prediction",
    "explicit_refit_probability",
    "explicit_refit_prediction",
)


class B07TrainingError(RuntimeError):
    """Raised when a training, publication, or validation invariant fails."""


@dataclass(frozen=True, slots=True)
class CacheEvidence:
    role: str
    cache_path: str
    report_path: str
    report_size_bytes: int
    report_sha256: str
    content_sha256: str
    shard_records: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class EncodedColumn:
    codes: NDArray[np.int32]
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetadataArrays:
    split: NDArray[np.str_]
    target_codes: NDArray[np.int16]
    binary_labels: NDArray[np.int8]
    speaker_codes: NDArray[np.int32]
    speaker_values: tuple[str, ...]
    primary: BoolArray
    categorical: Mapping[str, EncodedColumn]
    numeric_explicit: Float32Array
    slices: Mapping[str, EncodedColumn]


class EmbeddingSource(Protocol):
    dimension: int
    maximum_rows_resident: int

    def iter_split(self, split: str) -> Iterator[tuple[IntArray, Float32Array]]:
        """Yield global row indexes and a bounded float32 embedding block."""


@dataclass(frozen=True, slots=True)
class InputBundle:
    table: pa.Table
    arrays: MetadataArrays
    embedding_source: EmbeddingSource
    cache_evidence: tuple[CacheEvidence, ...]
    synthetic: bool = False


@dataclass(frozen=True, slots=True)
class PrototypeStatistics:
    fold_sums: NDArray[np.float64]
    fold_counts: NDArray[np.int64]
    full_sums: NDArray[np.float64]
    full_counts: NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class EncoderParameters:
    category_values: Mapping[str, tuple[str, ...]]
    numeric_medians: FloatArray
    numeric_means: FloatArray
    numeric_scales: FloatArray


@dataclass(frozen=True, slots=True)
class LinearParameters:
    encoder: EncoderParameters
    coefficients: FloatArray
    intercept: float
    regularization_c: float
    class_weights: tuple[float, float]


@dataclass(frozen=True, slots=True)
class DetectorSelection:
    selected_c: float
    selected_threshold: float
    selected_macro_f1: float
    pooled_probabilities: FloatArray
    pooled_indexes: IntArray
    candidates: tuple[Mapping[str, Any], ...]
    outer_temperatures: Mapping[int, float]
    temperature_traces: Mapping[int, tuple[Mapping[str, Any], ...]]


@dataclass(frozen=True, slots=True)
class SeedArtifacts:
    seed: int
    documents: Mapping[str, Mapping[str, Any]]
    prediction_table: pa.Table
    metric_summary: Mapping[str, Any]
    selected_c: float
    selected_threshold: float
    selected_temperature: float
    dev_predict_calls: int
    dev_metric_opens: int
    dev_hyperparameter_accesses: int


@dataclass(frozen=True, slots=True)
class PublishedGeneration:
    generation_id: str
    path: Path
    records: tuple[Mapping[str, Any], ...]
    created: bool


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _process_thread_count() -> int:
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Threads:"):
                return int(line.split(":", maxsplit=1)[1].strip())
    except (OSError, UnicodeError, ValueError):
        pass
    return 1


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value * 1024


def _validate_cpu_only_boundary(
    *, allow_preimported_test_harness: bool = False
) -> None:
    if "torch" in sys.modules and not allow_preimported_test_harness:
        raise B07TrainingError(
            "torch must remain unimported in the CPU-only B07 runner"
        )
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise B07TrainingError(
            "CUDA_VISIBLE_DEVICES must be the empty string before B07 execution"
        )


def _bounded_threadpool_observation(limit: int) -> list[Mapping[str, Any]]:
    observations: list[Mapping[str, Any]] = []
    for item in threadpool_info():
        threads = item.get("num_threads")
        if (
            isinstance(threads, bool)
            or not isinstance(threads, int)
            or threads < 1
            or threads > limit
        ):
            raise B07TrainingError(
                f"thread pool exceeds deterministic cap {limit}: {item!r}"
            )
        observations.append(
            {
                "user_api": item.get("user_api"),
                "internal_api": item.get("internal_api"),
                "num_threads": threads,
                "prefix": item.get("prefix"),
            }
        )
    return observations


def _safe_output_path(workspace_root: Path, relative: object, name: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise B07TrainingError(f"{name} must be a non-empty path")
    candidate_relative = Path(relative)
    if (
        candidate_relative.is_absolute()
        or relative != candidate_relative.as_posix()
        or any(part in {"", ".", ".."} for part in candidate_relative.parts)
        or any(part.startswith(".env") for part in candidate_relative.parts)
    ):
        raise B07TrainingError(f"{name} must be a safe workspace-relative path")
    current = workspace_root
    for part in candidate_relative.parts:
        current /= part
        if current.is_symlink():
            raise B07TrainingError(f"{name} cannot traverse a symlink")
    return current


def _atomic_write_json(path: Path, value: object, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise B07TrainingError(f"refusing to write through symlink: {path}")
    if not overwrite and path.exists():
        raise B07TrainingError(f"immutable JSON already exists: {path}")
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(value, pretty=True))
            handle.flush()
            os.fsync(handle.fileno())
        if not overwrite and path.exists():
            raise B07TrainingError(f"immutable JSON already exists: {path}")
        os.replace(temporary, path)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value, pretty=True))


def _parquet_shard_records(
    *,
    workspace_root: Path,
    cache: Mapping[str, Any],
    role: str,
    output_kind: str,
) -> tuple[CacheEvidence, tuple[Path, ...]]:
    report_relative = str(cache["report_path"])
    report_path = _safe_output_path(
        workspace_root,
        report_relative,
        f"{role}.report_path",
    ).resolve(strict=True)
    expected_report_sha = str(cache["report_sha256"])
    actual_report_sha = sha256_file(report_path)
    if actual_report_sha != expected_report_sha:
        raise B07TrainingError(f"{role} report SHA mismatch")
    report = load_json_strict(report_path, f"{role} cache report")
    if report.get("status") != "completed":
        raise B07TrainingError(f"{role} cache report is not completed")
    policy = report.get("policy")
    if (
        isinstance(policy, Mapping)
        and policy.get("metric_evaluation_performed") is not False
    ):
        raise B07TrainingError(f"{role} cache report metric policy is not false")
    outputs = report.get("outputs")
    if not isinstance(outputs, Mapping):
        raise B07TrainingError(f"{role} cache report outputs are absent")
    output = outputs.get(output_kind)
    if not isinstance(output, Mapping):
        raise B07TrainingError(f"{role} cache report {output_kind} output is absent")
    raw_shards = output.get("shards")
    if not isinstance(raw_shards, list) or not raw_shards:
        raise B07TrainingError(f"{role} shard records are absent")
    cache_relative = str(cache["path"])
    cache_path = _safe_output_path(
        workspace_root,
        cache_relative,
        f"{role}.path",
    ).resolve(strict=True)
    records: list[Mapping[str, Any]] = []
    paths: list[Path] = []
    for raw in raw_shards:
        if not isinstance(raw, Mapping):
            raise B07TrainingError(f"{role} shard record is invalid")
        name = raw.get("name", raw.get("path"))
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.startswith("part-")
            or not name.endswith(".parquet")
        ):
            raise B07TrainingError(f"{role} shard name is invalid")
        row_count = raw.get("row_count")
        shard_sha = raw.get("sha256")
        if (
            isinstance(row_count, bool)
            or not isinstance(row_count, int)
            or row_count <= 0
            or not isinstance(shard_sha, str)
        ):
            raise B07TrainingError(f"{role} shard record values are invalid")
        path = cache_path / name
        if not path.is_file() or path.is_symlink():
            raise B07TrainingError(f"{role} shard is absent or unsafe: {name}")
        actual_sha = sha256_file(path)
        if actual_sha != shard_sha:
            raise B07TrainingError(f"{role} shard SHA mismatch: {name}")
        records.append(
            {
                "name": name,
                "row_count": row_count,
                "size_bytes": path.stat().st_size,
                "sha256": shard_sha,
            }
        )
        paths.append(path)
    content_sha = sha256_json(
        [
            {
                "name": record["name"],
                "row_count": record["row_count"],
                "sha256": record["sha256"],
            }
            for record in records
        ]
    )
    if content_sha != cache.get("content_sha256"):
        raise B07TrainingError(f"{role} content SHA mismatch")
    evidence = CacheEvidence(
        role=role,
        cache_path=cache_relative,
        report_path=report_relative,
        report_size_bytes=report_path.stat().st_size,
        report_sha256=actual_report_sha,
        content_sha256=content_sha,
        shard_records=tuple(records),
    )
    return evidence, tuple(paths)


def _require_schema_columns(
    schema: pa.Schema,
    required: Sequence[str],
    forbidden: Sequence[str],
    role: str,
) -> None:
    names = set(schema.names)
    missing = sorted(set(required).difference(names))
    if missing:
        raise B07TrainingError(f"{role} schema misses columns: {missing!r}")
    leaked = sorted(set(forbidden).intersection(names))
    forbidden_fragments = (
        "realized",
        "alignment_operation",
        "error_tag",
        "error_type",
        "substitution_tag",
        "deletion_tag",
        "insertion_tag",
        "raw_locator",
        "raw_audio_path",
        "audio_path",
        "label_path",
        "source_path",
    )
    leaked.extend(
        sorted(
            name
            for name in names
            if any(fragment in name.casefold() for fragment in forbidden_fragments)
            and name not in leaked
        )
    )
    if leaked:
        raise B07TrainingError(f"{role} schema contains forbidden columns: {leaked!r}")


def _batch_columns_equal(
    left: pa.RecordBatch,
    right: pa.RecordBatch,
    columns: Sequence[str],
) -> None:
    for column in columns:
        left_column = left.column(left.schema.get_field_index(column))
        right_column = right.column(right.schema.get_field_index(column))
        if not left_column.equals(right_column):
            raise B07TrainingError(f"exact phone join mismatch in column {column!r}")


def _validate_key_sequence(
    batch: pa.RecordBatch,
    *,
    state: dict[str, Any],
) -> None:
    utterances = batch.column(
        batch.schema.get_field_index("utterance_id_sha256")
    ).to_pylist()
    indexes = batch.column(batch.schema.get_field_index("phone_index")).to_numpy(
        zero_copy_only=False
    )
    for utterance, raw_index in zip(utterances, indexes, strict=True):
        if not isinstance(utterance, str) or len(utterance) != 64:
            raise B07TrainingError("utterance key is not a SHA-256")
        index = int(raw_index)
        current = state.get("current")
        if utterance != current:
            if utterance in state["closed"]:
                raise B07TrainingError("utterance key block repeats")
            if current is not None:
                state["closed"].add(current)
            state["current"] = utterance
            state["last_index"] = -1
        if index <= int(state["last_index"]):
            raise B07TrainingError("phone key is duplicate or non-increasing")
        state["last_index"] = index


def _encoded_column(column: pa.ChunkedArray, *, missing_token: str) -> EncodedColumn:
    combined = column.combine_chunks()
    filled = pc.fill_null(combined, pa.scalar(missing_token))
    encoded = pc.dictionary_encode(filled)
    if not pa.types.is_dictionary(encoded.type):
        raise B07TrainingError("categorical encoding did not produce a dictionary")
    values = tuple(str(value) for value in encoded.dictionary.to_pylist())
    codes = np.asarray(encoded.indices.to_numpy(zero_copy_only=False), dtype=np.int32)
    return EncodedColumn(codes=codes, values=values)


def _build_metadata_arrays(table: pa.Table) -> MetadataArrays:
    rows = table.num_rows
    split = np.asarray(
        table["split"].combine_chunks().to_numpy(zero_copy_only=False),
        dtype=np.str_,
    )
    labels = np.asarray(
        table["binary_error_label"].combine_chunks().to_numpy(),
        dtype=np.int8,
    )
    target_values = table["target_label"].combine_chunks()
    target_indexes = pc.index_in(
        target_values,
        value_set=pa.array(PHONE_INVENTORY),
    )
    if target_indexes.null_count:
        raise B07TrainingError("target_label contains an out-of-inventory value")
    target_codes = np.asarray(
        target_indexes.to_numpy(zero_copy_only=False),
        dtype=np.int16,
    )
    if target_codes.shape[0] != rows:
        raise B07TrainingError("target encoding row count differs")

    speakers = _encoded_column(
        table["speaker_id_sha256"],
        missing_token="__MISSING__",
    )
    if "__MISSING__" in speakers.values:
        raise B07TrainingError("speaker_id_sha256 contains null")
    for value in speakers.values:
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise B07TrainingError("speaker_id_sha256 is not a lowercase SHA-256")

    l1 = _encoded_column(table["l1"], missing_token="__MISSING__")
    gender = _encoded_column(table["gender"], missing_token="__MISSING__")
    proficiency = _encoded_column(
        table["proficiency"],
        missing_token="__MISSING__",
    )
    primary_arrow = pc.and_(
        pc.is_in(
            table["l1"].combine_chunks(),
            value_set=pa.array(PRIMARY_L1),
        ),
        pc.is_in(
            table["gender"].combine_chunks(),
            value_set=pa.array(PRIMARY_GENDER),
        ),
    )
    primary = np.asarray(
        pc.fill_null(primary_arrow, False).to_numpy(zero_copy_only=False),
        dtype=np.bool_,
    )
    categorical = {
        "target_label": _encoded_column(
            table["target_label"],
            missing_token="__MISSING__",
        ),
        "previous_target_label": _encoded_column(
            table["previous_target_label"],
            missing_token="__MISSING__",
        ),
        "next_target_label": _encoded_column(
            table["next_target_label"],
            missing_token="__MISSING__",
        ),
    }
    numeric = np.column_stack(
        [
            np.asarray(
                table[name].combine_chunks().to_numpy(zero_copy_only=False),
                dtype=np.float32,
            )
            for name in EXPLICIT_NUMERIC_FEATURES
        ]
    )
    if numeric.shape != (rows, len(EXPLICIT_NUMERIC_FEATURES)):
        raise B07TrainingError("explicit numeric feature shape differs")
    return MetadataArrays(
        split=split,
        target_codes=target_codes,
        binary_labels=labels,
        speaker_codes=speakers.codes,
        speaker_values=speakers.values,
        primary=np.asarray(primary, dtype=np.bool_),
        categorical=categorical,
        numeric_explicit=np.asarray(numeric, dtype=np.float32),
        slices={"l1": l1, "gender": gender, "proficiency": proficiency},
    )


class ParquetEmbeddingSource:
    """Re-open the pinned WavLM shards for each bounded train/dev pass."""

    def __init__(
        self,
        *,
        paths: Sequence[Path],
        metadata_table: pa.Table,
        dimension: int,
        batch_rows: int,
    ) -> None:
        self._paths = tuple(paths)
        self._table = metadata_table
        self.dimension = dimension
        self.maximum_rows_resident = batch_rows

    def iter_split(self, split: str) -> Iterator[tuple[IntArray, Float32Array]]:
        if split not in {"train", "dev"}:
            raise B07TrainingError("embedding split must be train or dev")
        split_values = np.asarray(
            self._table["split"].combine_chunks().to_numpy(zero_copy_only=False),
            dtype=np.str_,
        )
        global_indexes = np.flatnonzero(split_values == split).astype(np.int64)
        dataset = ds.dataset([str(path) for path in self._paths], format="parquet")
        scanner = dataset.scanner(
            columns=["utterance_id_sha256", "phone_index", "embedding_mean"],
            filter=ds.field("split") == split,
            batch_size=self.maximum_rows_resident,
            use_threads=False,
        )
        offset = 0
        for batch in scanner.to_batches():
            rows = batch.num_rows
            if rows == 0:
                continue
            indexes = global_indexes[offset : offset + rows]
            if indexes.shape[0] != rows:
                raise B07TrainingError("embedding scanner produced excess rows")
            expected_ids = (
                self._table["utterance_id_sha256"]
                .take(pa.array(indexes))
                .combine_chunks()
            )
            expected_phone = (
                self._table["phone_index"].take(pa.array(indexes)).combine_chunks()
            )
            actual_ids = batch.column(
                batch.schema.get_field_index("utterance_id_sha256")
            )
            actual_phone = batch.column(batch.schema.get_field_index("phone_index"))
            if not actual_ids.equals(expected_ids) or not actual_phone.equals(
                expected_phone
            ):
                raise B07TrainingError("embedding scanner row order/key mismatch")
            embedding = batch.column(batch.schema.get_field_index("embedding_mean"))
            if not pa.types.is_fixed_size_list(embedding.type):
                raise B07TrainingError("embedding_mean is not a fixed-size list")
            if embedding.type.list_size != self.dimension:
                raise B07TrainingError("embedding dimension differs")
            values = embedding.values.to_numpy(zero_copy_only=False)
            block = np.asarray(values, dtype=np.float32).reshape(
                rows,
                self.dimension,
            )
            if block.shape[0] > self.maximum_rows_resident:
                raise B07TrainingError("embedding resident-row bound exceeded")
            yield indexes, block
            offset += rows
        if offset != global_indexes.shape[0]:
            raise B07TrainingError("embedding scanner split row count differs")


class ArrayEmbeddingSource:
    """Bounded source used only by synthetic integration tests."""

    def __init__(
        self,
        embeddings: object,
        splits: object,
        *,
        batch_rows: int = 64,
    ) -> None:
        values = np.asarray(embeddings, dtype=np.float32)
        split_values = np.asarray(splits, dtype=np.str_)
        if values.ndim != 2 or values.shape[0] != split_values.shape[0]:
            raise B07TrainingError("synthetic embedding/source shapes differ")
        self._values = values
        self._splits = split_values
        self.dimension = values.shape[1]
        self.maximum_rows_resident = batch_rows

    def iter_split(self, split: str) -> Iterator[tuple[IntArray, Float32Array]]:
        indexes = np.flatnonzero(self._splits == split).astype(np.int64)
        for start in range(0, indexes.shape[0], self.maximum_rows_resident):
            selected = indexes[start : start + self.maximum_rows_resident]
            yield selected, self._values[selected]


def build_synthetic_input_bundle(
    *,
    table: pa.Table,
    embeddings: object,
    batch_rows: int = 64,
) -> InputBundle:
    """Build an explicitly synthetic bundle for bounded integration tests."""

    if set(EXPLICIT_COLUMNS).difference(table.column_names):
        raise B07TrainingError("synthetic table misses explicit contract columns")
    selected = table.select(EXPLICIT_COLUMNS)
    arrays = _build_metadata_arrays(selected)
    source = ArrayEmbeddingSource(
        embeddings,
        arrays.split,
        batch_rows=batch_rows,
    )
    row_count = selected.num_rows
    evidence = (
        CacheEvidence(
            role="synthetic_wavlm_phone_cache",
            cache_path="tests/synthetic/wavlm_phone",
            report_path="tests/synthetic/wavlm_report.json",
            report_size_bytes=1,
            report_sha256="a" * 64,
            content_sha256="b" * 64,
            shard_records=(
                {
                    "name": "part-00000.parquet",
                    "row_count": row_count,
                    "size_bytes": 1,
                    "sha256": "c" * 64,
                },
            ),
        ),
        CacheEvidence(
            role="synthetic_explicit_phone_cache",
            cache_path="tests/synthetic/explicit_phone",
            report_path="tests/synthetic/explicit_report.json",
            report_size_bytes=1,
            report_sha256="d" * 64,
            content_sha256="e" * 64,
            shard_records=(
                {
                    "name": "part-00000.parquet",
                    "row_count": row_count,
                    "size_bytes": 1,
                    "sha256": "f" * 64,
                },
            ),
        ),
    )
    return InputBundle(
        table=selected,
        arrays=arrays,
        embedding_source=source,
        cache_evidence=evidence,
        synthetic=True,
    )


def load_canonical_input(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> InputBundle:
    """Audit pinned metadata and construct a bounded re-openable source."""

    data = config["data"]
    if not isinstance(data, Mapping):
        raise B07TrainingError("data config is invalid")
    wavlm = data["wavlm_phone_cache"]
    explicit = data["explicit_phone_cache"]
    if not isinstance(wavlm, Mapping) or not isinstance(explicit, Mapping):
        raise B07TrainingError("cache config is invalid")
    wavlm_evidence, wavlm_paths = _parquet_shard_records(
        workspace_root=workspace_root,
        cache=wavlm,
        role="wavlm_phone_cache",
        output_kind="phone",
    )
    explicit_evidence, explicit_paths = _parquet_shard_records(
        workspace_root=workspace_root,
        cache=explicit,
        role="explicit_phone_cache",
        output_kind="phone",
    )
    if len(wavlm_paths) != len(explicit_paths):
        raise B07TrainingError("cache shard counts differ")

    forbidden = tuple(str(value) for value in data["forbidden_columns"])
    batches: list[pa.RecordBatch] = []
    key_state: dict[str, Any] = {
        "current": None,
        "last_index": -1,
        "closed": set(),
    }
    batch_rows = int(config["resource_contract"]["record_batch_rows"])
    for wavlm_path, explicit_path in zip(wavlm_paths, explicit_paths, strict=True):
        wavlm_file = pq.ParquetFile(wavlm_path)
        explicit_file = pq.ParquetFile(explicit_path)
        _require_schema_columns(
            wavlm_file.schema_arrow,
            (*WAVLM_AUDIT_COLUMNS, "embedding_mean"),
            forbidden,
            "WavLM",
        )
        _require_schema_columns(
            explicit_file.schema_arrow,
            EXPLICIT_COLUMNS,
            forbidden,
            "explicit",
        )
        wavlm_batches = wavlm_file.iter_batches(
            batch_size=batch_rows,
            columns=WAVLM_AUDIT_COLUMNS,
            use_threads=False,
        )
        explicit_batches = explicit_file.iter_batches(
            batch_size=batch_rows,
            columns=EXPLICIT_COLUMNS,
            use_threads=False,
        )
        for left, right in zip_longest(wavlm_batches, explicit_batches):
            if left is None or right is None or left.num_rows != right.num_rows:
                raise B07TrainingError("cache streamed batch universes differ")
            _batch_columns_equal(left, right, JOIN_EQUAL_COLUMNS)
            _validate_key_sequence(right, state=key_state)
            batches.append(right)
    table = pa.Table.from_batches(batches)
    arrays = _build_metadata_arrays(table)
    counts: dict[str, dict[str, int]] = {}
    for split in ("train", "dev"):
        mask = arrays.split == split
        primary = mask & arrays.primary
        counts[split] = {
            "total": int(np.count_nonzero(mask)),
            "match": int(np.count_nonzero(mask & (arrays.binary_labels == 0))),
            "error": int(np.count_nonzero(mask & (arrays.binary_labels == 1))),
            "primary_total": int(np.count_nonzero(primary)),
            "primary_match": int(
                np.count_nonzero(primary & (arrays.binary_labels == 0))
            ),
            "primary_error": int(
                np.count_nonzero(primary & (arrays.binary_labels == 1))
            ),
        }
    if counts != EXPECTED_ROWS:
        raise B07TrainingError(f"canonical row audit differs: {counts!r}")
    for split in ("train", "dev"):
        inventory = set(arrays.target_codes[arrays.split == split].astype(int).tolist())
        if inventory != set(range(len(PHONE_INVENTORY))):
            raise B07TrainingError(f"{split} target inventory is not exact")
    source = ParquetEmbeddingSource(
        paths=wavlm_paths,
        metadata_table=table,
        dimension=int(wavlm["embedding_dimension"]),
        batch_rows=batch_rows,
    )
    return InputBundle(
        table=table,
        arrays=arrays,
        embedding_source=source,
        cache_evidence=(wavlm_evidence, explicit_evidence),
        synthetic=False,
    )


def _seed_folds(arrays: MetadataArrays, seed: int) -> NDArray[np.int8]:
    speaker_folds = np.asarray(
        [
            assign_speaker_fold(
                speaker,
                seed=seed,
                fold_count=FOLD_COUNT,
                namespace=FOLD_NAMESPACE,
            )
            for speaker in arrays.speaker_values
        ],
        dtype=np.int8,
    )
    folds = speaker_folds[arrays.speaker_codes]
    for fold in range(FOLD_COUNT):
        if not np.any(folds == fold):
            raise B07TrainingError(f"seed {seed} has empty speaker fold {fold}")
    return folds


def _accumulate_prototype_statistics(
    bundle: InputBundle,
    folds: NDArray[np.int8],
) -> PrototypeStatistics:
    dimension = bundle.embedding_source.dimension
    fold_sums = np.zeros(
        (FOLD_COUNT, len(PHONE_INVENTORY), dimension),
        dtype=np.float64,
    )
    fold_counts = np.zeros((FOLD_COUNT, len(PHONE_INVENTORY)), dtype=np.int64)
    arrays = bundle.arrays
    primary_match_train = (
        (arrays.split == "train") & arrays.primary & (arrays.binary_labels == 0)
    )
    maximum_seen = 0
    for indexes, embeddings in bundle.embedding_source.iter_split("train"):
        maximum_seen = max(maximum_seen, embeddings.shape[0])
        selected_local = primary_match_train[indexes]
        if not np.any(selected_local):
            continue
        normalized = l2_normalize_rows(
            embeddings[selected_local],
            name="streamed train embeddings",
        )
        selected_global = indexes[selected_local]
        selected_folds = folds[selected_global].astype(np.int64)
        selected_targets = arrays.target_codes[selected_global].astype(np.int64)
        np.add.at(
            fold_sums,
            (selected_folds, selected_targets),
            normalized,
        )
        np.add.at(fold_counts, (selected_folds, selected_targets), 1)
    if maximum_seen > bundle.embedding_source.maximum_rows_resident:
        raise B07TrainingError("streaming prototype pass exceeded resident-row bound")
    full_sums = np.sum(fold_sums, axis=0, dtype=np.float64)
    full_counts = np.sum(fold_counts, axis=0, dtype=np.int64)
    if np.any(full_counts == 0):
        empty = np.flatnonzero(full_counts == 0).tolist()
        raise B07TrainingError(f"full match prototype classes are empty: {empty!r}")
    return PrototypeStatistics(
        fold_sums=fold_sums,
        fold_counts=fold_counts,
        full_sums=full_sums,
        full_counts=full_counts,
    )


def _prototypes_excluding(
    statistics: PrototypeStatistics,
    excluded_folds: Sequence[int],
) -> FloatArray:
    excluded = tuple(sorted(set(int(value) for value in excluded_folds)))
    if any(value < 0 or value >= FOLD_COUNT for value in excluded):
        raise B07TrainingError("prototype excluded fold is invalid")
    included = tuple(fold for fold in range(FOLD_COUNT) if fold not in set(excluded))
    if not included:
        raise B07TrainingError("prototype complement has no included folds")
    # Sum only allowed folds.  Subtracting excluded folds from a full sum would
    # retain floating-point cancellation dependence on forbidden outer rows.
    sums = np.sum(
        statistics.fold_sums[np.asarray(included, dtype=np.int64)],
        axis=0,
        dtype=np.float64,
    )
    counts = np.sum(
        statistics.fold_counts[np.asarray(included, dtype=np.int64)],
        axis=0,
        dtype=np.int64,
    )
    if np.any(counts <= 0):
        empty = np.flatnonzero(counts <= 0).tolist()
        raise B07TrainingError(
            f"prototype complement has empty classes for folds {excluded!r}: {empty!r}"
        )
    means = sums / counts[:, np.newaxis]
    norms = np.linalg.norm(means, axis=1)
    if not np.isfinite(norms).all() or np.any(norms <= 0.0):
        raise B07TrainingError("prototype complement has invalid norms")
    prototypes = means / norms[:, np.newaxis]
    if not np.isfinite(prototypes).all():
        raise B07TrainingError("prototype complement contains NaN or Inf")
    return np.asarray(prototypes, dtype=np.float64)


def _log_softmax_scores(scores: FloatArray, temperature: float) -> FloatArray:
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise B07TrainingError("temperature must be positive and finite")
    logits = scores / temperature
    maxima = np.max(logits, axis=1, keepdims=True)
    shifted = logits - maxima
    return shifted - np.log(np.sum(np.exp(shifted), axis=1, keepdims=True))


def _temperature_trace_for_prototypes(
    bundle: InputBundle,
    *,
    folds: NDArray[np.int8],
    prototypes_by_role: Mapping[str, FloatArray],
) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    arrays = bundle.arrays
    validation = (
        (arrays.split == "train")
        & arrays.primary
        & (arrays.binary_labels == 0)
        & (folds == 0)
    )
    sums = {
        role: np.zeros(len(TEMPERATURES), dtype=np.float64)
        for role in prototypes_by_role
    }
    counts = {role: 0 for role in prototypes_by_role}
    for indexes, embeddings in bundle.embedding_source.iter_split("train"):
        selected_local = validation[indexes]
        if not np.any(selected_local):
            continue
        normalized = l2_normalize_rows(
            embeddings[selected_local],
            name="temperature validation embeddings",
        )
        selected_global = indexes[selected_local]
        targets = arrays.target_codes[selected_global].astype(np.int64)
        rows = np.arange(targets.shape[0], dtype=np.int64)
        for role, prototypes in prototypes_by_role.items():
            scores = np.asarray(normalized @ prototypes.T, dtype=np.float64)
            for candidate_index, temperature in enumerate(TEMPERATURES):
                log_posterior = _log_softmax_scores(scores, temperature)
                sums[role][candidate_index] += float(
                    -np.sum(log_posterior[rows, targets], dtype=np.float64)
                )
            counts[role] += targets.shape[0]
    traces: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for role in prototypes_by_role:
        if counts[role] <= 0:
            raise B07TrainingError(f"temperature role {role!r} has no match rows")
        traces[role] = tuple(
            {
                "temperature": temperature,
                "match_negative_log_likelihood": float(
                    sums[role][index] / counts[role]
                ),
                "match_row_count": counts[role],
            }
            for index, temperature in enumerate(TEMPERATURES)
        )
    return traces


def _selected_temperature(trace: Sequence[Mapping[str, Any]]) -> float:
    if not trace:
        raise B07TrainingError("temperature trace is empty")
    selected = min(
        trace,
        key=lambda item: (
            float(item["match_negative_log_likelihood"]),
            float(item["temperature"]),
        ),
    )
    return float(selected["temperature"])


def _select_all_temperatures(
    bundle: InputBundle,
    *,
    folds: NDArray[np.int8],
    statistics: PrototypeStatistics,
) -> tuple[
    float,
    Mapping[int, float],
    Mapping[str, tuple[Mapping[str, Any], ...]],
]:
    prototypes: dict[str, FloatArray] = {
        "final": _prototypes_excluding(statistics, (0,))
    }
    for outer_fold in DETECTOR_VALIDATION_FOLDS:
        prototypes[f"outer_{outer_fold}"] = _prototypes_excluding(
            statistics,
            (0, outer_fold),
        )
    traces = _temperature_trace_for_prototypes(
        bundle,
        folds=folds,
        prototypes_by_role=prototypes,
    )
    final_temperature = _selected_temperature(traces["final"])
    outer = {
        fold: _selected_temperature(traces[f"outer_{fold}"])
        for fold in DETECTOR_VALIDATION_FOLDS
    }
    return final_temperature, outer, traces


def _gop_block(
    embeddings: Float32Array,
    target_codes: NDArray[np.int16],
    prototypes: FloatArray,
    temperature: float,
) -> Float32Array:
    normalized = l2_normalize_rows(embeddings, name="GOP embedding block")
    scores = np.asarray(normalized @ prototypes.T, dtype=np.float64)
    log_posterior = _log_softmax_scores(scores, temperature)
    labels = np.asarray(
        [PHONE_INVENTORY[int(index)] for index in target_codes],
        dtype=np.str_,
    )
    features = gop_features_from_log_probabilities(
        log_posterior,
        labels,
        inventory=PHONE_INVENTORY,
    ).as_matrix()
    return np.asarray(features, dtype=np.float32)


def _project_crossfit_train(
    bundle: InputBundle,
    *,
    folds: NDArray[np.int8],
    statistics: PrototypeStatistics,
    temperature: float,
) -> Float32Array:
    arrays = bundle.arrays
    output = np.full(
        (bundle.table.num_rows, len(GOP_NUMERIC_FEATURES)),
        np.nan,
        dtype=np.float32,
    )
    prototypes = {
        fold: _prototypes_excluding(statistics, (fold,)) for fold in range(FOLD_COUNT)
    }
    selected = (arrays.split == "train") & arrays.primary
    for indexes, embeddings in bundle.embedding_source.iter_split("train"):
        for fold in range(FOLD_COUNT):
            local = selected[indexes] & (folds[indexes] == fold)
            if not np.any(local):
                continue
            global_rows = indexes[local]
            output[global_rows] = _gop_block(
                embeddings[local],
                arrays.target_codes[global_rows],
                prototypes[fold],
                temperature,
            )
    if not np.isfinite(output[selected]).all():
        raise B07TrainingError("final cross-fit GOP feature coverage differs")
    return output


def _project_nested_outer_train(
    bundle: InputBundle,
    *,
    folds: NDArray[np.int8],
    statistics: PrototypeStatistics,
    outer_fold: int,
    temperature: float,
) -> Float32Array:
    """Build outer-fold-isolated features for one detector candidate fit."""

    arrays = bundle.arrays
    output = np.full(
        (bundle.table.num_rows, len(GOP_NUMERIC_FEATURES)),
        np.nan,
        dtype=np.float32,
    )
    prototypes: dict[int, FloatArray] = {}
    for row_fold in range(FOLD_COUNT):
        excluded = (outer_fold,) if row_fold == outer_fold else (outer_fold, row_fold)
        prototypes[row_fold] = _prototypes_excluding(statistics, excluded)
    selected = (arrays.split == "train") & arrays.primary
    for indexes, embeddings in bundle.embedding_source.iter_split("train"):
        for row_fold in range(FOLD_COUNT):
            local = selected[indexes] & (folds[indexes] == row_fold)
            if not np.any(local):
                continue
            global_rows = indexes[local]
            output[global_rows] = _gop_block(
                embeddings[local],
                arrays.target_codes[global_rows],
                prototypes[row_fold],
                temperature,
            )
    if not np.isfinite(output[selected]).all():
        raise B07TrainingError(
            f"nested GOP feature coverage differs for outer fold {outer_fold}"
        )
    return output


def _category_vocabularies(
    arrays: MetadataArrays,
    training_indexes: IntArray,
) -> Mapping[str, tuple[str, ...]]:
    output: dict[str, tuple[str, ...]] = {}
    for name in CATEGORICAL_FEATURES:
        encoded = arrays.categorical[name]
        present_codes = np.unique(encoded.codes[training_indexes]).tolist()
        present_values = {encoded.values[int(code)] for code in present_codes}
        present_values.update({"__MISSING__", "__UNKNOWN__"})
        output[name] = tuple(sorted(present_values))
    return output


def _numeric_parameters(
    values: FloatArray,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    if values.ndim != 2 or values.shape[0] == 0:
        raise B07TrainingError("numeric training matrix is empty")
    finite_or_nan = np.isfinite(values) | np.isnan(values)
    if not finite_or_nan.all():
        raise B07TrainingError("numeric features contain Inf")
    medians = np.nanmedian(values, axis=0)
    if not np.isfinite(medians).all():
        raise B07TrainingError("numeric feature is entirely missing")
    imputed = np.where(np.isnan(values), medians[np.newaxis, :], values)
    means = np.mean(imputed, axis=0, dtype=np.float64)
    scales = np.std(imputed, axis=0, dtype=np.float64)
    scales = np.where(scales <= 0.0, 1.0, scales)
    if not np.isfinite(means).all() or not np.isfinite(scales).all():
        raise B07TrainingError("numeric preprocessing contains NaN or Inf")
    return (
        np.asarray(medians, dtype=np.float64),
        np.asarray(means, dtype=np.float64),
        np.asarray(scales, dtype=np.float64),
    )


def _fit_encoder(
    arrays: MetadataArrays,
    numeric_full: FloatArray,
    training_indexes: IntArray,
) -> EncoderParameters:
    medians, means, scales = _numeric_parameters(numeric_full[training_indexes])
    return EncoderParameters(
        category_values=_category_vocabularies(arrays, training_indexes),
        numeric_medians=medians,
        numeric_means=means,
        numeric_scales=scales,
    )


def _transform_features(
    arrays: MetadataArrays,
    numeric_full: FloatArray,
    indexes: IntArray,
    parameters: EncoderParameters,
) -> sparse.csr_matrix:
    rows = indexes.shape[0]
    if rows == 0:
        raise B07TrainingError("feature transform indexes are empty")
    blocks: list[sparse.csr_matrix] = []
    for name in CATEGORICAL_FEATURES:
        vocabulary = parameters.category_values[name]
        local = {value: index for index, value in enumerate(vocabulary)}
        unknown = local["__UNKNOWN__"]
        source = arrays.categorical[name]
        source_to_local = np.asarray(
            [local.get(value, unknown) for value in source.values],
            dtype=np.int32,
        )
        columns = source_to_local[source.codes[indexes]]
        block = sparse.csr_matrix(
            (
                np.ones(rows, dtype=np.float64),
                (np.arange(rows, dtype=np.int64), columns),
            ),
            shape=(rows, len(vocabulary)),
        )
        blocks.append(block)
    numeric = np.asarray(numeric_full[indexes], dtype=np.float64)
    numeric = np.where(
        np.isnan(numeric),
        parameters.numeric_medians[np.newaxis, :],
        numeric,
    )
    standardized = (numeric - parameters.numeric_means) / parameters.numeric_scales
    if not np.isfinite(standardized).all():
        raise B07TrainingError("standardized features contain NaN or Inf")
    blocks.append(sparse.csr_matrix(standardized))
    return sparse.hstack(blocks, format="csr")


def _balanced_class_weights(labels: NDArray[np.int8]) -> tuple[float, float]:
    counts = np.bincount(labels.astype(np.int64), minlength=2)
    if counts.shape[0] != 2 or np.any(counts <= 0):
        raise B07TrainingError("detector fit requires both binary classes")
    total = int(np.sum(counts))
    return (total / (2.0 * counts[0]), total / (2.0 * counts[1]))


def _fit_linear(
    matrix: sparse.csr_matrix,
    labels: NDArray[np.int8],
    *,
    encoder: EncoderParameters,
    regularization_c: float,
    seed: int,
    maximum_iterations: int,
    tolerance: float,
) -> LinearParameters:
    weights = _balanced_class_weights(labels)
    estimator = LogisticRegression(
        l1_ratio=0.0,
        C=regularization_c,
        solver="lbfgs",
        class_weight={0: weights[0], 1: weights[1]},
        max_iter=maximum_iterations,
        tol=tolerance,
        random_state=seed,
    )
    estimator.fit(matrix, labels)
    if np.any(estimator.n_iter_ >= maximum_iterations):
        raise B07TrainingError("detector solver reached the iteration limit")
    if estimator.classes_.tolist() != [0, 1]:
        raise B07TrainingError("detector class order differs")
    coefficients = np.asarray(estimator.coef_[0], dtype=np.float64)
    intercept = float(estimator.intercept_[0])
    if not np.isfinite(coefficients).all() or not math.isfinite(intercept):
        raise B07TrainingError("detector parameters contain NaN or Inf")
    return LinearParameters(
        encoder=encoder,
        coefficients=coefficients,
        intercept=intercept,
        regularization_c=regularization_c,
        class_weights=weights,
    )


def _predict_linear(
    matrix: sparse.csr_matrix,
    parameters: LinearParameters,
) -> FloatArray:
    logits = np.asarray(
        matrix @ parameters.coefficients + parameters.intercept,
        dtype=np.float64,
    ).reshape(-1)
    positive = logits >= 0
    probabilities = np.empty_like(logits)
    probabilities[positive] = 1.0 / (1.0 + np.exp(-logits[positive]))
    negative_exp = np.exp(logits[~positive])
    probabilities[~positive] = negative_exp / (1.0 + negative_exp)
    if not np.isfinite(probabilities).all():
        raise B07TrainingError("detector prediction contains NaN or Inf")
    return probabilities


def _macro_f1(
    labels: NDArray[np.int8],
    probabilities: FloatArray,
    threshold: float,
) -> float:
    predictions = (probabilities >= threshold).astype(np.int8)
    return float(
        f1_score(
            labels,
            predictions,
            average="macro",
            labels=[0, 1],
            zero_division=0.0,
        )
    )


def _select_from_pooled_probabilities(
    *,
    labels: NDArray[np.int8],
    probabilities_by_c: Mapping[float, FloatArray],
    indexes: IntArray,
    outer_temperatures: Mapping[int, float],
    temperature_traces: Mapping[int, tuple[Mapping[str, Any], ...]],
) -> DetectorSelection:
    candidates: list[Mapping[str, Any]] = []
    for regularization_c in REGULARIZATION_CS:
        probabilities = probabilities_by_c[regularization_c]
        for threshold in threshold_values(
            {
                "detector": {
                    "threshold_grid": {
                        "start": 0.2,
                        "stop": 0.8,
                        "step": 0.05,
                        "stop_inclusive": True,
                    }
                }
            }
        ):
            candidates.append(
                {
                    "regularization_c": regularization_c,
                    "threshold": threshold,
                    "phone_macro_f1": _macro_f1(
                        labels,
                        probabilities,
                        threshold,
                    ),
                    "row_count": int(labels.shape[0]),
                }
            )
    selected = min(
        candidates,
        key=lambda item: (
            -float(item["phone_macro_f1"]),
            float(item["regularization_c"]),
            abs(float(item["threshold"]) - 0.5),
            float(item["threshold"]),
        ),
    )
    selected_c = float(selected["regularization_c"])
    return DetectorSelection(
        selected_c=selected_c,
        selected_threshold=float(selected["threshold"]),
        selected_macro_f1=float(selected["phone_macro_f1"]),
        pooled_probabilities=probabilities_by_c[selected_c],
        pooled_indexes=indexes,
        candidates=tuple(candidates),
        outer_temperatures=dict(outer_temperatures),
        temperature_traces=dict(temperature_traces),
    )


def _detector_selection(
    bundle: InputBundle,
    *,
    seed: int,
    folds: NDArray[np.int8],
    statistics: PrototypeStatistics,
    outer_temperatures: Mapping[int, float],
    temperature_traces: Mapping[str, tuple[Mapping[str, Any], ...]],
    maximum_iterations: int,
    tolerance: float,
) -> tuple[DetectorSelection, DetectorSelection]:
    arrays = bundle.arrays
    primary_train = (arrays.split == "train") & arrays.primary
    gop_probabilities: dict[float, list[FloatArray]] = {
        value: [] for value in REGULARIZATION_CS
    }
    control_probabilities: dict[float, list[FloatArray]] = {
        value: [] for value in REGULARIZATION_CS
    }
    validation_indexes: list[IntArray] = []
    for outer_fold in DETECTOR_VALIDATION_FOLDS:
        training_indexes = np.flatnonzero(primary_train & (folds != outer_fold)).astype(
            np.int64
        )
        validation = np.flatnonzero(primary_train & (folds == outer_fold)).astype(
            np.int64
        )
        if training_indexes.shape[0] == 0 or validation.shape[0] == 0:
            raise B07TrainingError(f"detector outer fold {outer_fold} is empty")
        if set(arrays.speaker_codes[training_indexes]).intersection(
            set(arrays.speaker_codes[validation])
        ):
            raise B07TrainingError(
                f"detector outer fold {outer_fold} has speaker overlap"
            )
        nested_gop = _project_nested_outer_train(
            bundle,
            folds=folds,
            statistics=statistics,
            outer_fold=outer_fold,
            temperature=outer_temperatures[outer_fold],
        )
        gop_numeric = np.column_stack((arrays.numeric_explicit, nested_gop)).astype(
            np.float64
        )
        control_numeric = np.asarray(arrays.numeric_explicit, dtype=np.float64)
        gop_encoder = _fit_encoder(arrays, gop_numeric, training_indexes)
        control_encoder = _fit_encoder(arrays, control_numeric, training_indexes)
        gop_train = _transform_features(
            arrays,
            gop_numeric,
            training_indexes,
            gop_encoder,
        )
        gop_validation = _transform_features(
            arrays,
            gop_numeric,
            validation,
            gop_encoder,
        )
        control_train = _transform_features(
            arrays,
            control_numeric,
            training_indexes,
            control_encoder,
        )
        control_validation = _transform_features(
            arrays,
            control_numeric,
            validation,
            control_encoder,
        )
        labels = arrays.binary_labels[training_indexes]
        for regularization_c in REGULARIZATION_CS:
            gop_model = _fit_linear(
                gop_train,
                labels,
                encoder=gop_encoder,
                regularization_c=regularization_c,
                seed=seed,
                maximum_iterations=maximum_iterations,
                tolerance=tolerance,
            )
            gop_probabilities[regularization_c].append(
                _predict_linear(gop_validation, gop_model)
            )
            control_model = _fit_linear(
                control_train,
                labels,
                encoder=control_encoder,
                regularization_c=regularization_c,
                seed=seed,
                maximum_iterations=maximum_iterations,
                tolerance=tolerance,
            )
            control_probabilities[regularization_c].append(
                _predict_linear(control_validation, control_model)
            )
        validation_indexes.append(validation)
        del (
            nested_gop,
            gop_numeric,
            gop_train,
            gop_validation,
            control_train,
            control_validation,
        )
    pooled_indexes = np.concatenate(validation_indexes)
    pooled_labels = arrays.binary_labels[pooled_indexes]
    pooled_gop = {
        value: np.concatenate(probabilities)
        for value, probabilities in gop_probabilities.items()
    }
    pooled_control = {
        value: np.concatenate(probabilities)
        for value, probabilities in control_probabilities.items()
    }
    outer_trace = {
        fold: temperature_traces[f"outer_{fold}"] for fold in DETECTOR_VALIDATION_FOLDS
    }
    gop_selection = _select_from_pooled_probabilities(
        labels=pooled_labels,
        probabilities_by_c=pooled_gop,
        indexes=pooled_indexes,
        outer_temperatures=outer_temperatures,
        temperature_traces=outer_trace,
    )
    control_selection = _select_from_pooled_probabilities(
        labels=pooled_labels,
        probabilities_by_c=pooled_control,
        indexes=pooled_indexes,
        outer_temperatures={},
        temperature_traces={},
    )
    return gop_selection, control_selection


def _encoder_document(parameters: EncoderParameters) -> Mapping[str, Any]:
    return {
        "categorical_vocabularies": {
            name: list(parameters.category_values[name])
            for name in CATEGORICAL_FEATURES
        },
        "categorical_missing_token": "__MISSING__",
        "categorical_unknown_token": "__UNKNOWN__",
        "numeric_imputation_medians": parameters.numeric_medians.tolist(),
        "numeric_standardization_means": parameters.numeric_means.tolist(),
        "numeric_standardization_scales": parameters.numeric_scales.tolist(),
    }


def _encoded_feature_order(
    parameters: EncoderParameters,
    numeric_names: Sequence[str],
) -> list[str]:
    categorical = [
        f"{name}=={value}"
        for name in CATEGORICAL_FEATURES
        for value in parameters.category_values[name]
    ]
    return [*categorical, *numeric_names]


def _linear_document(
    parameters: LinearParameters,
    *,
    threshold: float,
    numeric_names: Sequence[str],
) -> Mapping[str, Any]:
    return {
        "feature_order": [
            *CATEGORICAL_FEATURES,
            *numeric_names,
        ],
        "categorical_feature_order": list(CATEGORICAL_FEATURES),
        "numeric_feature_order": list(numeric_names),
        "encoded_feature_order": _encoded_feature_order(
            parameters.encoder,
            numeric_names,
        ),
        **_encoder_document(parameters.encoder),
        "class_order": [0, 1],
        "coefficients": parameters.coefficients.tolist(),
        "intercept": parameters.intercept,
        "selected_c": parameters.regularization_c,
        "selected_threshold": threshold,
        "class_weights": {
            "0": parameters.class_weights[0],
            "1": parameters.class_weights[1],
        },
        "solver": "lbfgs",
        "penalty": "l2",
        "class_weight_policy": "balanced_fit_labels_only",
    }


def restore_linear_parameters(
    document: Mapping[str, Any],
) -> tuple[LinearParameters, float, tuple[str, ...]]:
    """Restore a stack-inference-sufficient serialized detector."""

    vocabularies = document.get("categorical_vocabularies")
    if not isinstance(vocabularies, Mapping):
        raise B07TrainingError("checkpoint categorical vocabularies are absent")
    category_values: dict[str, tuple[str, ...]] = {}
    for name in CATEGORICAL_FEATURES:
        raw = vocabularies.get(name)
        if not isinstance(raw, list) or not raw:
            raise B07TrainingError(f"checkpoint vocabulary is invalid: {name}")
        values = tuple(str(value) for value in raw)
        if len(set(values)) != len(values):
            raise B07TrainingError(f"checkpoint vocabulary repeats: {name}")
        if "__MISSING__" not in values or "__UNKNOWN__" not in values:
            raise B07TrainingError(
                f"checkpoint vocabulary omits reserved token: {name}"
            )
        category_values[name] = values
    numeric_names_raw = document.get("numeric_feature_order")
    if not isinstance(numeric_names_raw, list):
        raise B07TrainingError("checkpoint numeric feature order is absent")
    numeric_names = tuple(str(value) for value in numeric_names_raw)
    medians = np.asarray(document.get("numeric_imputation_medians"), dtype=np.float64)
    means = np.asarray(document.get("numeric_standardization_means"), dtype=np.float64)
    scales = np.asarray(
        document.get("numeric_standardization_scales"), dtype=np.float64
    )
    if (
        medians.shape != (len(numeric_names),)
        or means.shape != medians.shape
        or scales.shape != medians.shape
        or not np.isfinite(medians).all()
        or not np.isfinite(means).all()
        or not np.isfinite(scales).all()
        or np.any(scales <= 0.0)
    ):
        raise B07TrainingError("checkpoint numeric preprocessing is invalid")
    encoder = EncoderParameters(
        category_values=category_values,
        numeric_medians=medians,
        numeric_means=means,
        numeric_scales=scales,
    )
    coefficients = np.asarray(document.get("coefficients"), dtype=np.float64)
    expected_dimension = len(_encoded_feature_order(encoder, numeric_names))
    if (
        coefficients.shape != (expected_dimension,)
        or not np.isfinite(coefficients).all()
    ):
        raise B07TrainingError("checkpoint coefficient dimension is invalid")
    intercept = float(document["intercept"])
    regularization_c = float(document["selected_c"])
    threshold = float(document["selected_threshold"])
    weights = document.get("class_weights")
    if not isinstance(weights, Mapping):
        raise B07TrainingError("checkpoint class weights are absent")
    class_weights = (float(weights["0"]), float(weights["1"]))
    if (
        not math.isfinite(intercept)
        or regularization_c not in REGULARIZATION_CS
        or not (0.0 < threshold < 1.0)
        or not all(math.isfinite(value) and value > 0.0 for value in class_weights)
    ):
        raise B07TrainingError("checkpoint linear values are invalid")
    return (
        LinearParameters(
            encoder=encoder,
            coefficients=coefficients,
            intercept=intercept,
            regularization_c=regularization_c,
            class_weights=class_weights,
        ),
        threshold,
        numeric_names,
    )


def predict_serialized_detector(
    *,
    arrays: MetadataArrays,
    numeric_full: FloatArray,
    indexes: IntArray,
    document: Mapping[str, Any],
) -> FloatArray:
    """Predict using only serialized checkpoint fields."""

    parameters, _, numeric_names = restore_linear_parameters(document)
    if numeric_full.shape[1] != len(numeric_names):
        raise B07TrainingError("checkpoint inference numeric dimension differs")
    matrix = _transform_features(
        arrays,
        numeric_full,
        indexes,
        parameters.encoder,
    )
    return _predict_linear(matrix, parameters)


def project_serialized_acoustic_checkpoint(
    *,
    embeddings: object,
    target_labels: Sequence[str],
    document: Mapping[str, Any],
) -> Float32Array:
    """Replay acoustic GOP using no error label or realized-phone field."""

    if document.get("schema_version") != SCHEMA_VERSIONS["acoustic_checkpoint"]:
        raise B07TrainingError("acoustic checkpoint schema differs")
    inventory = document.get("inventory")
    if inventory != list(PHONE_INVENTORY):
        raise B07TrainingError("acoustic checkpoint inventory differs")
    dimension = document.get("embedding_dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
        raise B07TrainingError("acoustic checkpoint dimension is invalid")
    prototypes = np.asarray(
        document.get("full_primary_train_match_prototypes"),
        dtype=np.float64,
    )
    if prototypes.shape != (len(PHONE_INVENTORY), dimension):
        raise B07TrainingError("acoustic checkpoint prototype shape differs")
    norms = np.linalg.norm(prototypes, axis=1)
    if not np.isfinite(prototypes).all() or not np.allclose(
        norms,
        1.0,
        rtol=1e-8,
        atol=1e-10,
    ):
        raise B07TrainingError("acoustic checkpoint prototypes are not unit norm")
    values = np.asarray(embeddings, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != dimension:
        raise B07TrainingError("acoustic checkpoint input dimension differs")
    if len(target_labels) != values.shape[0]:
        raise B07TrainingError("acoustic checkpoint target row count differs")
    target_index = {label: index for index, label in enumerate(PHONE_INVENTORY)}
    try:
        target_codes = np.asarray(
            [target_index[str(label)] for label in target_labels],
            dtype=np.int16,
        )
    except KeyError as error:
        raise B07TrainingError(
            "acoustic checkpoint target is out of inventory"
        ) from error
    return _gop_block(
        values,
        target_codes,
        prototypes,
        float(document["selected_temperature"]),
    )


def predict_serialized_detector_rows(
    *,
    categorical_rows: Mapping[str, Sequence[str | None]],
    numeric_rows: object,
    document: Mapping[str, Any],
) -> FloatArray:
    """Replay an error checkpoint from label-free stack inference inputs."""

    parameters, _, numeric_names = restore_linear_parameters(document)
    numeric = np.asarray(numeric_rows, dtype=np.float64)
    if numeric.ndim != 2 or numeric.shape[1] != len(numeric_names):
        raise B07TrainingError("serialized detector numeric input shape differs")
    row_count = numeric.shape[0]
    blocks: list[sparse.csr_matrix] = []
    for name in CATEGORICAL_FEATURES:
        raw = categorical_rows.get(name)
        if raw is None or len(raw) != row_count:
            raise B07TrainingError(f"serialized detector category rows differ: {name}")
        vocabulary = parameters.encoder.category_values[name]
        local = {value: index for index, value in enumerate(vocabulary)}
        unknown = local["__UNKNOWN__"]
        missing = "__MISSING__"
        columns = np.asarray(
            [
                local.get(missing if value is None else str(value), unknown)
                for value in raw
            ],
            dtype=np.int32,
        )
        blocks.append(
            sparse.csr_matrix(
                (
                    np.ones(row_count, dtype=np.float64),
                    (np.arange(row_count, dtype=np.int64), columns),
                ),
                shape=(row_count, len(vocabulary)),
            )
        )
    numeric = np.where(
        np.isnan(numeric),
        parameters.encoder.numeric_medians[np.newaxis, :],
        numeric,
    )
    standardized = (
        numeric - parameters.encoder.numeric_means
    ) / parameters.encoder.numeric_scales
    if not np.isfinite(standardized).all():
        raise B07TrainingError("serialized detector numeric input contains NaN or Inf")
    blocks.append(sparse.csr_matrix(standardized))
    matrix = sparse.hstack(blocks, format="csr")
    return _predict_linear(matrix, parameters)


def _binary_metrics(
    labels: NDArray[np.int8],
    probabilities: FloatArray,
    threshold: float,
) -> Mapping[str, Any]:
    predictions = (probabilities >= threshold).astype(np.int8)
    true_negative = int(np.count_nonzero((labels == 0) & (predictions == 0)))
    false_positive = int(np.count_nonzero((labels == 0) & (predictions == 1)))
    false_negative = int(np.count_nonzero((labels == 1) & (predictions == 0)))
    true_positive = int(np.count_nonzero((labels == 1) & (predictions == 1)))
    return {
        "row_count": int(labels.shape[0]),
        "error_row_count": int(np.count_nonzero(labels == 1)),
        "phone_macro_f1": _macro_f1(labels, probabilities, threshold),
        "error_auprc": float(average_precision_score(labels, probabilities)),
        "threshold": threshold,
        "confusion_matrix": {
            "true_negative": true_negative,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "true_positive": true_positive,
        },
    }


def _slice_report(
    bundle: InputBundle,
    *,
    dev_indexes: IntArray,
    primary_mask: BoolArray,
    probabilities: FloatArray,
    threshold: float,
) -> Mapping[str, Any]:
    arrays = bundle.arrays
    labels = arrays.binary_labels[dev_indexes]
    groups: dict[str, list[Mapping[str, Any]]] = {}
    slice_sources = {
        **arrays.slices,
        "target_label": arrays.categorical["target_label"],
    }
    for name, encoded in slice_sources.items():
        codes = encoded.codes[dev_indexes]
        rows: list[Mapping[str, Any]] = []
        for code in sorted(np.unique(codes[primary_mask]).tolist()):
            selected = primary_mask & (codes == code)
            selected_labels = labels[selected]
            selected_probabilities = probabilities[selected]
            error_count = int(np.count_nonzero(selected_labels == 1))
            metrics: dict[str, Any] = {
                "value": encoded.values[int(code)],
                "row_count": int(np.count_nonzero(selected)),
                "error_row_count": error_count,
                "phone_macro_f1": _macro_f1(
                    selected_labels,
                    selected_probabilities,
                    threshold,
                ),
                "error_auprc": (
                    float(
                        average_precision_score(
                            selected_labels,
                            selected_probabilities,
                        )
                    )
                    if 0 < error_count < selected_labels.shape[0]
                    else None
                ),
            }
            rows.append(metrics)
        groups[name] = rows
    return groups


def _paired_summary(
    labels: NDArray[np.int8],
    gop_probabilities: FloatArray,
    gop_threshold: float,
    control_probabilities: FloatArray,
    control_threshold: float,
) -> Mapping[str, Any]:
    gop_predictions = (gop_probabilities >= gop_threshold).astype(np.int8)
    control_predictions = (control_probabilities >= control_threshold).astype(np.int8)
    gop_correct = gop_predictions == labels
    control_correct = control_predictions == labels
    gop_f1 = _macro_f1(labels, gop_probabilities, gop_threshold)
    control_f1 = _macro_f1(labels, control_probabilities, control_threshold)
    return {
        "comparison_role": (
            "within_run_explicit_feature_refit_control_not_canonical_b03_metric"
        ),
        "same_row_universe": True,
        "row_count": int(labels.shape[0]),
        "gop_phone_macro_f1": gop_f1,
        "explicit_refit_phone_macro_f1": control_f1,
        "phone_macro_f1_delta": gop_f1 - control_f1,
        "gop_only_correct": int(np.count_nonzero(gop_correct & ~control_correct)),
        "explicit_refit_only_correct": int(
            np.count_nonzero(~gop_correct & control_correct)
        ),
        "both_correct": int(np.count_nonzero(gop_correct & control_correct)),
        "both_incorrect": int(np.count_nonzero(~gop_correct & ~control_correct)),
    }


def _fit_final_models_and_predict_dev(
    bundle: InputBundle,
    *,
    seed: int,
    folds: NDArray[np.int8],
    statistics: PrototypeStatistics,
    final_temperature: float,
    gop_selection: DetectorSelection,
    control_selection: DetectorSelection,
    maximum_iterations: int,
    tolerance: float,
) -> tuple[
    LinearParameters,
    LinearParameters,
    FloatArray,
    FloatArray,
    Float32Array,
    IntArray,
    FloatArray,
]:
    arrays = bundle.arrays
    train_gop = _project_crossfit_train(
        bundle,
        folds=folds,
        statistics=statistics,
        temperature=final_temperature,
    )
    primary_train_indexes = np.flatnonzero(
        (arrays.split == "train") & arrays.primary
    ).astype(np.int64)
    gop_numeric = np.column_stack((arrays.numeric_explicit, train_gop)).astype(
        np.float64
    )
    control_numeric = np.asarray(arrays.numeric_explicit, dtype=np.float64)
    gop_encoder = _fit_encoder(arrays, gop_numeric, primary_train_indexes)
    control_encoder = _fit_encoder(
        arrays,
        control_numeric,
        primary_train_indexes,
    )
    gop_train = _transform_features(
        arrays,
        gop_numeric,
        primary_train_indexes,
        gop_encoder,
    )
    control_train = _transform_features(
        arrays,
        control_numeric,
        primary_train_indexes,
        control_encoder,
    )
    train_labels = arrays.binary_labels[primary_train_indexes]
    gop_model = _fit_linear(
        gop_train,
        train_labels,
        encoder=gop_encoder,
        regularization_c=gop_selection.selected_c,
        seed=seed,
        maximum_iterations=maximum_iterations,
        tolerance=tolerance,
    )
    control_model = _fit_linear(
        control_train,
        train_labels,
        encoder=control_encoder,
        regularization_c=control_selection.selected_c,
        seed=seed,
        maximum_iterations=maximum_iterations,
        tolerance=tolerance,
    )

    dev_indexes = np.flatnonzero(arrays.split == "dev").astype(np.int64)
    dev_gop_full = np.full(
        (bundle.table.num_rows, len(GOP_NUMERIC_FEATURES)),
        np.nan,
        dtype=np.float32,
    )
    full_prototypes = _prototypes_excluding(statistics, ())
    # This is the one and only dev embedding scanner/predict pass for the seed.
    for indexes, embeddings in bundle.embedding_source.iter_split("dev"):
        dev_gop_full[indexes] = _gop_block(
            embeddings,
            arrays.target_codes[indexes],
            full_prototypes,
            final_temperature,
        )
    if not np.isfinite(dev_gop_full[dev_indexes]).all():
        raise B07TrainingError("dev GOP projection coverage differs")
    dev_gop_numeric = np.column_stack((arrays.numeric_explicit, dev_gop_full)).astype(
        np.float64
    )
    gop_dev_matrix = _transform_features(
        arrays,
        dev_gop_numeric,
        dev_indexes,
        gop_model.encoder,
    )
    control_dev_matrix = _transform_features(
        arrays,
        control_numeric,
        dev_indexes,
        control_model.encoder,
    )
    gop_probabilities = _predict_linear(gop_dev_matrix, gop_model)
    control_probabilities = _predict_linear(control_dev_matrix, control_model)
    return (
        gop_model,
        control_model,
        gop_probabilities,
        control_probabilities,
        dev_gop_full[dev_indexes],
        dev_indexes,
        full_prototypes,
    )


def _prediction_table(
    bundle: InputBundle,
    *,
    dev_indexes: IntArray,
    dev_gop: Float32Array,
    gop_probabilities: FloatArray,
    gop_threshold: float,
    control_probabilities: FloatArray,
    control_threshold: float,
) -> pa.Table:
    source = bundle.table.select(
        [
            "utterance_id_sha256",
            "speaker_id_sha256",
            "phone_index",
            "split",
            "target_label",
            "binary_error_label",
            "l1",
            "gender",
            "proficiency",
        ]
    ).take(pa.array(dev_indexes))
    output = source
    for index, name in enumerate(GOP_NUMERIC_FEATURES):
        output = output.append_column(name, pa.array(dev_gop[:, index]))
    output = output.append_column(
        "gop_probability",
        pa.array(gop_probabilities, type=pa.float64()),
    )
    output = output.append_column(
        "gop_prediction",
        pa.array((gop_probabilities >= gop_threshold).astype(np.int8)),
    )
    output = output.append_column(
        "explicit_refit_probability",
        pa.array(control_probabilities, type=pa.float64()),
    )
    output = output.append_column(
        "explicit_refit_prediction",
        pa.array((control_probabilities >= control_threshold).astype(np.int8)),
    )
    if tuple(output.column_names) != PREDICTION_COLUMNS:
        raise B07TrainingError("prediction table columns differ")
    metadata = {
        b"voice_coach.schema_version": SCHEMA_VERSIONS["predictions"].encode(),
        b"voice_coach.split": b"dev",
        b"voice_coach.oracle_alignment": b"true",
        b"voice_coach.realized_phone_identity_used": b"false",
        b"voice_coach.canonical_b03_metric_claimed": b"false",
    }
    return output.replace_schema_metadata(metadata)


def train_seed(
    bundle: InputBundle,
    *,
    seed: int,
    config: Mapping[str, Any],
) -> SeedArtifacts:
    if seed not in SEEDS:
        raise B07TrainingError(f"seed is not preregistered: {seed}")
    detector = config["detector"]
    if not isinstance(detector, Mapping):
        raise B07TrainingError("detector config is invalid")
    maximum_iterations = int(detector["maximum_iterations"])
    tolerance = float(detector["tolerance"])
    folds = _seed_folds(bundle.arrays, seed)
    statistics = _accumulate_prototype_statistics(bundle, folds)
    final_temperature, outer_temperatures, temperature_traces = (
        _select_all_temperatures(
            bundle,
            folds=folds,
            statistics=statistics,
        )
    )
    gop_selection, control_selection = _detector_selection(
        bundle,
        seed=seed,
        folds=folds,
        statistics=statistics,
        outer_temperatures=outer_temperatures,
        temperature_traces=temperature_traces,
        maximum_iterations=maximum_iterations,
        tolerance=tolerance,
    )
    (
        gop_model,
        control_model,
        gop_probabilities,
        control_probabilities,
        dev_gop,
        dev_indexes,
        full_prototypes,
    ) = _fit_final_models_and_predict_dev(
        bundle,
        seed=seed,
        folds=folds,
        statistics=statistics,
        final_temperature=final_temperature,
        gop_selection=gop_selection,
        control_selection=control_selection,
        maximum_iterations=maximum_iterations,
        tolerance=tolerance,
    )
    # The labels are first consumed for a dev metric only after both probability
    # vectors are frozen by the single dev projection call above.
    primary_dev = bundle.arrays.primary[dev_indexes]
    labels = bundle.arrays.binary_labels[dev_indexes]
    primary_labels = labels[primary_dev]
    gop_primary = gop_probabilities[primary_dev]
    control_primary = control_probabilities[primary_dev]
    gop_metrics = _binary_metrics(
        primary_labels,
        gop_primary,
        gop_selection.selected_threshold,
    )
    control_metrics = _binary_metrics(
        primary_labels,
        control_primary,
        control_selection.selected_threshold,
    )
    paired = _paired_summary(
        primary_labels,
        gop_primary,
        gop_selection.selected_threshold,
        control_primary,
        control_selection.selected_threshold,
    )
    prediction_table = _prediction_table(
        bundle,
        dev_indexes=dev_indexes,
        dev_gop=dev_gop,
        gop_probabilities=gop_probabilities,
        gop_threshold=gop_selection.selected_threshold,
        control_probabilities=control_probabilities,
        control_threshold=control_selection.selected_threshold,
    )
    acoustic_checkpoint = {
        "schema_version": SCHEMA_VERSIONS["acoustic_checkpoint"],
        "seed": seed,
        "inventory": list(PHONE_INVENTORY),
        "embedding_dimension": bundle.embedding_source.dimension,
        "selected_temperature": final_temperature,
        "full_primary_train_match_prototypes": full_prototypes.tolist(),
        "full_primary_train_match_class_counts": statistics.full_counts.tolist(),
        "fold_namespace": FOLD_NAMESPACE,
        "fold_count": FOLD_COUNT,
        "input_column_contract": {
            "embedding": "embedding_mean",
            "target": "target_label",
            "fit_label": "binary_error_label==0",
            "fit_scope": "primary_train_only",
            "realized_identity": "forbidden",
        },
        "stack_inference_sufficient": True,
    }
    gop_linear = _linear_document(
        gop_model,
        threshold=gop_selection.selected_threshold,
        numeric_names=ALL_NUMERIC_FEATURES,
    )
    control_linear = _linear_document(
        control_model,
        threshold=control_selection.selected_threshold,
        numeric_names=EXPLICIT_NUMERIC_FEATURES,
    )
    error_checkpoint = {
        "schema_version": SCHEMA_VERSIONS["error_checkpoint"],
        "seed": seed,
        **gop_linear,
        "explicit_refit_control": control_linear,
        "stack_inference_sufficient": True,
    }
    primary_train_mask = (bundle.arrays.split == "train") & bundle.arrays.primary
    selection_trace = {
        "schema_version": SCHEMA_VERSIONS["selection_trace"],
        "seed": seed,
        "fold_namespace": FOLD_NAMESPACE,
        "fold_row_counts": {
            str(fold): int(np.count_nonzero(primary_train_mask & (folds == fold)))
            for fold in range(FOLD_COUNT)
        },
        "fold_speaker_counts": {
            str(fold): int(
                np.unique(
                    bundle.arrays.speaker_codes[primary_train_mask & (folds == fold)]
                ).shape[0]
            )
            for fold in range(FOLD_COUNT)
        },
        "outer_detector_counts": {
            str(fold): {
                "fit_rows": int(np.count_nonzero(primary_train_mask & (folds != fold))),
                "validation_rows": int(
                    np.count_nonzero(primary_train_mask & (folds == fold))
                ),
                "fit_speakers": int(
                    np.unique(
                        bundle.arrays.speaker_codes[
                            primary_train_mask & (folds != fold)
                        ]
                    ).shape[0]
                ),
                "validation_speakers": int(
                    np.unique(
                        bundle.arrays.speaker_codes[
                            primary_train_mask & (folds == fold)
                        ]
                    ).shape[0]
                ),
                "speaker_overlap": 0,
            }
            for fold in DETECTOR_VALIDATION_FOLDS
        },
        "final_temperature_candidates": list(temperature_traces["final"]),
        "selected_final_temperature": final_temperature,
        "outer_temperature_candidates": {
            str(fold): list(temperature_traces[f"outer_{fold}"])
            for fold in DETECTOR_VALIDATION_FOLDS
        },
        "selected_outer_temperatures": {
            str(fold): outer_temperatures[fold] for fold in DETECTOR_VALIDATION_FOLDS
        },
        "gop_detector_candidates": list(gop_selection.candidates),
        "gop_selected": {
            "regularization_c": gop_selection.selected_c,
            "threshold": gop_selection.selected_threshold,
            "pooled_outer_oof_phone_macro_f1": gop_selection.selected_macro_f1,
        },
        "explicit_refit_control_candidates": list(control_selection.candidates),
        "explicit_refit_control_selected": {
            "regularization_c": control_selection.selected_c,
            "threshold": control_selection.selected_threshold,
            "pooled_outer_oof_phone_macro_f1": (control_selection.selected_macro_f1),
        },
        "detector_outer_validation_folds": list(DETECTOR_VALIDATION_FOLDS),
        "dev_hyperparameter_access_count": 0,
    }
    metric_document = {
        "schema_version": SCHEMA_VERSIONS["metrics"],
        "seed": seed,
        "split": "dev",
        "scope": {
            "l1": list(PRIMARY_L1),
            "gender": list(PRIMARY_GENDER),
        },
        "all_dev_prediction_row_count": int(dev_indexes.shape[0]),
        "primary_dev_metric_row_count": int(np.count_nonzero(primary_dev)),
        "gop_acoustic_posterior": gop_metrics,
        "within_run_explicit_feature_refit_control": control_metrics,
        "paired": paired,
        "dev_access_audit": {
            "predict_call_count": 1,
            "metric_open_count": 1,
            "hyperparameter_selection_count": 0,
            "stack_access_count": 0,
            "calibration_access_count": 0,
            "locked_test_access_count": 0,
            "raw_manifest_access_count": 0,
            "environment_file_access_count": 0,
        },
        "oracle_alignment": True,
        "deployable": False,
    }
    slice_document = {
        "schema_version": SCHEMA_VERSIONS["slices"],
        "seed": seed,
        "split": "dev",
        "scope": "primary",
        "gop_acoustic_posterior": _slice_report(
            bundle,
            dev_indexes=dev_indexes,
            primary_mask=primary_dev,
            probabilities=gop_probabilities,
            threshold=gop_selection.selected_threshold,
        ),
        "within_run_explicit_feature_refit_control": _slice_report(
            bundle,
            dev_indexes=dev_indexes,
            primary_mask=primary_dev,
            probabilities=control_probabilities,
            threshold=control_selection.selected_threshold,
        ),
    }
    return SeedArtifacts(
        seed=seed,
        documents={
            "acoustic_checkpoint.json": acoustic_checkpoint,
            "error_checkpoint.json": error_checkpoint,
            "inner_selection_trace.json": selection_trace,
            "metrics.json": metric_document,
            "slices.json": slice_document,
        },
        prediction_table=prediction_table,
        metric_summary=metric_document,
        selected_c=gop_selection.selected_c,
        selected_threshold=gop_selection.selected_threshold,
        selected_temperature=final_temperature,
        dev_predict_calls=1,
        dev_metric_opens=1,
        dev_hyperparameter_accesses=0,
    )


def _cache_evidence_document(
    evidence: Sequence[CacheEvidence],
) -> list[Mapping[str, Any]]:
    return [
        {
            "role": item.role,
            "cache_path": item.cache_path,
            "report_path": item.report_path,
            "report_size_bytes": item.report_size_bytes,
            "report_sha256": item.report_sha256,
            "content_sha256": item.content_sha256,
            "shards": [dict(record) for record in item.shard_records],
        }
        for item in evidence
    ]


def _execution_lock(
    *,
    config_path: Path,
    workspace_root: Path,
    source_records: Sequence[Mapping[str, str]],
    bundle: InputBundle,
) -> Mapping[str, Any]:
    return {
        "schema_version": SCHEMA_VERSIONS["execution_lock"],
        "experiment_id": EXPERIMENT_ID,
        "status": "locked",
        "config": {
            "path": config_path.relative_to(workspace_root).as_posix(),
            "sha256": sha256_file(config_path),
        },
        "canonical_sources": [dict(record) for record in source_records],
        "input_caches": _cache_evidence_document(bundle.cache_evidence),
        "seeds": list(SEEDS),
        "phone_inventory": list(PHONE_INVENTORY),
        "temperature_grid": list(TEMPERATURES),
        "regularization_c_grid": list(REGULARIZATION_CS),
        "threshold_grid": {
            "start": 0.2,
            "stop": 0.8,
            "step": 0.05,
            "stop_inclusive": True,
        },
        "fold_contract": {
            "count": FOLD_COUNT,
            "namespace": FOLD_NAMESPACE,
            "temperature_validation_fold": 0,
            "detector_validation_folds": list(DETECTOR_VALIDATION_FOLDS),
            "nested_outer_exclusion": True,
        },
        "data_boundary": {
            "fit": ["train"],
            "metric": ["dev"],
            "forbidden": ["stack", "calibration", "locked_test"],
            "raw_manifest_access": False,
            "environment_file_access": False,
            "realized_phone_identity_access": False,
        },
        "resource_contract": {
            "maximum_embedding_rows_resident": (
                bundle.embedding_source.maximum_rows_resident
            ),
            "embedding_compute_dtype": "float32",
            "prototype_accumulator_dtype": "float64",
            "cpu_thread_cap": 4,
            "cuda_initialized": False,
        },
        "synthetic": bundle.synthetic,
    }


def _aggregate_documents(
    seeds: Sequence[SeedArtifacts],
    *,
    gate: float,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    gop_f1 = np.asarray(
        [
            float(item.metric_summary["gop_acoustic_posterior"]["phone_macro_f1"])
            for item in seeds
        ],
        dtype=np.float64,
    )
    gop_auprc = np.asarray(
        [
            float(item.metric_summary["gop_acoustic_posterior"]["error_auprc"])
            for item in seeds
        ],
        dtype=np.float64,
    )
    control_f1 = np.asarray(
        [
            float(
                item.metric_summary["within_run_explicit_feature_refit_control"][
                    "phone_macro_f1"
                ]
            )
            for item in seeds
        ],
        dtype=np.float64,
    )
    deltas = gop_f1 - control_f1
    aggregate = {
        "schema_version": SCHEMA_VERSIONS["aggregate"],
        "experiment_id": EXPERIMENT_ID,
        "status": "completed",
        "seeds": [item.seed for item in seeds],
        "gop_acoustic_posterior": {
            "mean_seed_phone_macro_f1": float(np.mean(gop_f1)),
            "sample_std_seed_phone_macro_f1": float(np.std(gop_f1, ddof=1)),
            "mean_seed_error_auprc": float(np.mean(gop_auprc)),
            "sample_std_seed_error_auprc": float(np.std(gop_auprc, ddof=1)),
            "seed_summary_ddof": 1,
        },
        "adoption_gate": {
            "metric": "mean_seed_phone_macro_f1",
            "operator": "greater_than_or_equal",
            "value": gate,
            "passed": bool(float(np.mean(gop_f1)) >= gate),
        },
        "dev_access_audit": {
            "predict_call_count_total": sum(item.dev_predict_calls for item in seeds),
            "metric_open_count_total": sum(item.dev_metric_opens for item in seeds),
            "hyperparameter_selection_count_total": sum(
                item.dev_hyperparameter_accesses for item in seeds
            ),
            "expected_per_seed": {
                "predict_call_count": 1,
                "metric_open_count": 1,
                "hyperparameter_selection_count": 0,
            },
        },
        "oracle_alignment": True,
        "deployable": False,
        "canonical_b03_metric_claimed": False,
    }
    paired = {
        "schema_version": SCHEMA_VERSIONS["paired"],
        "comparison_role": (
            "within_run_explicit_feature_refit_control_not_canonical_b03_metric"
        ),
        "same_dev_row_universe": True,
        "seed_rows": [
            {
                "seed": item.seed,
                **dict(item.metric_summary["paired"]),
            }
            for item in seeds
        ],
        "mean_seed_gop_phone_macro_f1": float(np.mean(gop_f1)),
        "mean_seed_explicit_refit_phone_macro_f1": float(np.mean(control_f1)),
        "mean_seed_phone_macro_f1_delta": float(np.mean(deltas)),
        "sample_std_seed_phone_macro_f1_delta": float(np.std(deltas, ddof=1)),
        "seed_summary_ddof": 1,
        "canonical_b03_checkpoint_or_prediction_read": False,
    }
    return aggregate, paired


def _build_artifact_objects(
    *,
    config_path: Path,
    workspace_root: Path,
    config: Mapping[str, Any],
    source_records: Sequence[Mapping[str, str]],
    bundle: InputBundle,
    seed_artifacts: Sequence[SeedArtifacts],
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    if tuple(item.seed for item in seed_artifacts) != SEEDS:
        raise B07TrainingError("seed artifact order differs")
    evaluation = config["evaluation"]
    if not isinstance(evaluation, Mapping):
        raise B07TrainingError("evaluation config is invalid")
    gate_record = evaluation["adoption_gate"]
    if not isinstance(gate_record, Mapping):
        raise B07TrainingError("adoption gate config is invalid")
    aggregate, paired = _aggregate_documents(
        seed_artifacts,
        gate=float(gate_record["value"]),
    )
    lock = _execution_lock(
        config_path=config_path,
        workspace_root=workspace_root,
        source_records=source_records,
        bundle=bundle,
    )
    return lock, aggregate, paired


def _write_generation_tree(
    temporary: Path,
    *,
    lock: Mapping[str, Any],
    aggregate: Mapping[str, Any],
    paired: Mapping[str, Any],
    seed_artifacts: Sequence[SeedArtifacts],
) -> None:
    _write_json(temporary / "execution_lock.json", lock)
    for seed_result in seed_artifacts:
        directory = temporary / f"seed_{seed_result.seed}"
        directory.mkdir(parents=True, exist_ok=False)
        for name, document in seed_result.documents.items():
            _write_json(directory / name, document)
        pq.write_table(
            seed_result.prediction_table,
            directory / "dev_predictions.parquet",
            compression="zstd",
            use_dictionary=True,
            write_statistics=True,
            row_group_size=65_536,
            version="2.6",
        )
    _write_json(temporary / "aggregate_report.json", aggregate)
    _write_json(
        temporary / "paired_explicit_refit_control_comparison.json",
        paired,
    )
    actual = tuple(
        path.relative_to(temporary).as_posix()
        for path in sorted(temporary.rglob("*"))
        if path.is_file()
    )
    if actual != tuple(sorted(ARTIFACT_LAYOUT)):
        raise B07TrainingError(
            f"generation layout differs: {actual!r} != {tuple(sorted(ARTIFACT_LAYOUT))!r}"
        )


def _generation_records(directory: Path) -> tuple[Mapping[str, Any], ...]:
    records = tuple(
        {
            "path": relative,
            "size_bytes": (directory / relative).stat().st_size,
            "sha256": sha256_file(directory / relative),
        }
        for relative in sorted(ARTIFACT_LAYOUT)
    )
    actual = tuple(
        path.relative_to(directory).as_posix()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    )
    if actual != tuple(sorted(ARTIFACT_LAYOUT)):
        raise B07TrainingError("published generation layout differs")
    return records


def publish_generation(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    lock: Mapping[str, Any],
    aggregate: Mapping[str, Any],
    paired: Mapping[str, Any],
    seed_artifacts: Sequence[SeedArtifacts],
) -> PublishedGeneration:
    artifacts = config["artifacts"]
    if not isinstance(artifacts, Mapping):
        raise B07TrainingError("artifact config is invalid")
    root = _safe_output_path(
        workspace_root,
        artifacts["generation_root"],
        "generation_root",
    )
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise B07TrainingError("generation root is not a safe directory")
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".b07-generation-", dir=root.parent))
    try:
        _write_generation_tree(
            temporary,
            lock=lock,
            aggregate=aggregate,
            paired=paired,
            seed_artifacts=seed_artifacts,
        )
        records = _generation_records(temporary)
        generation_id = sha256_json(records)
        destination = root / generation_id
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise B07TrainingError("existing generation destination is unsafe")
            existing = _generation_records(destination)
            if existing != records:
                raise B07TrainingError("existing generation content differs")
            shutil.rmtree(temporary)
            return PublishedGeneration(
                generation_id=generation_id,
                path=destination,
                records=existing,
                created=False,
            )
        os.replace(temporary, destination)
        directory_descriptor = os.open(
            root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return PublishedGeneration(
            generation_id=generation_id,
            path=destination,
            records=records,
            created=True,
        )
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _registry_base(
    *,
    config_path: Path,
    workspace_root: Path,
    source_records: Sequence[Mapping[str, str]],
    start_time: float,
    threads_start: int,
    synthetic_test_harness: bool,
) -> dict[str, Any]:
    torch_preimported = "torch" in sys.modules
    return {
        "schema_version": 1,
        "contract_schema_version": SCHEMA_VERSIONS["registry"],
        "run_id": "aihub71490-b07-gop-acoustic-posterior-v1",
        "experiment_id": EXPERIMENT_ID,
        "status": "running",
        "timestamps": {
            "started": _utc_now(),
            "updated": _utc_now(),
            "ended": None,
        },
        "seeds": {f"experiment_seed_{seed}": seed for seed in SEEDS},
        "argv": [
            "scripts/train_b07_gop_acoustic_posterior.py",
            "--execute-canonical",
        ],
        "inputs": {
            "config": [fingerprint_file(config_path, relative_to=workspace_root)],
            "data": [],
            "model": [],
            "checkpoint": [],
        },
        "canonical_sources": [dict(record) for record in source_records],
        "runtime": {
            **collect_runtime_metadata(),
            "b07_observations": {
                "elapsed_seconds": time.monotonic() - start_time,
                "peak_rss_bytes": _peak_rss_bytes(),
                "configured_cpu_thread_cap": 4,
                "observed_process_thread_count_at_start": threads_start,
                "observed_process_thread_count_at_finish": _process_thread_count(),
            },
        },
        "accelerator": {
            "requested": False,
            "probe_performed": False,
            "torch_imported": torch_preimported,
            "torch_preimported_by_synthetic_test_harness": (
                synthetic_test_harness and torch_preimported
            ),
            "production_cpu_boundary_claim_applicable": not synthetic_test_harness,
            "cuda_initialized": False,
            "cuda_visible_devices": "empty_string",
        },
        "git": collect_git_metadata(workspace_root),
        "metrics": [],
        "artifacts": [],
        "failure": None,
        "generation_id": None,
        "generation_records": [],
        "precommit_validation": None,
        "precommit_chain_revalidation": None,
        "data_boundary": {
            "fit_split": "train",
            "metric_split": "dev",
            "stack_access_count": 0,
            "calibration_access_count": 0,
            "locked_test_access_count": 0,
            "raw_manifest_access_count": 0,
            "environment_file_access_count": 0,
        },
    }


def _refresh_registry_observations(
    registry: dict[str, Any],
    *,
    start_time: float,
    threads_start: int,
) -> None:
    runtime = registry["runtime"]
    if not isinstance(runtime, dict):
        raise B07TrainingError("registry runtime is invalid")
    observations = runtime["b07_observations"]
    if not isinstance(observations, dict):
        raise B07TrainingError("registry observations are invalid")
    observations.update(
        {
            "elapsed_seconds": time.monotonic() - start_time,
            "peak_rss_bytes": _peak_rss_bytes(),
            "configured_cpu_thread_cap": 4,
            "observed_process_thread_count_at_start": threads_start,
            "observed_process_thread_count_at_finish": _process_thread_count(),
        }
    )
    registry["timestamps"]["updated"] = _utc_now()


def _registry_data_records(
    bundle: InputBundle,
) -> list[Mapping[str, Any]]:
    output: list[Mapping[str, Any]] = []
    for cache in bundle.cache_evidence:
        output.append(
            {
                "path": cache.report_path,
                "size_bytes": cache.report_size_bytes,
                "sha256": cache.report_sha256,
                "role": f"{cache.role}_report",
            }
        )
        for shard in cache.shard_records:
            output.append(
                {
                    "path": f"{cache.cache_path}/{shard['name']}",
                    "size_bytes": shard["size_bytes"],
                    "sha256": shard["sha256"],
                    "row_count": shard["row_count"],
                    "role": f"{cache.role}_shard",
                }
            )
    return output


def _complete_registry(
    registry: dict[str, Any],
    *,
    bundle: InputBundle,
    generation: PublishedGeneration,
    seed_artifacts: Sequence[SeedArtifacts],
    workspace_root: Path,
    start_time: float,
    threads_start: int,
) -> None:
    registry["inputs"]["data"] = _registry_data_records(bundle)
    registry["generation_id"] = generation.generation_id
    registry["generation_records"] = [dict(record) for record in generation.records]
    registry["artifacts"] = [
        {
            "name": record["path"].replace("/", "__"),
            "kind": ("parquet" if str(record["path"]).endswith(".parquet") else "json"),
            "path": (
                generation.path.relative_to(workspace_root) / str(record["path"])
            ).as_posix(),
            "size_bytes": record["size_bytes"],
            "sha256": record["sha256"],
        }
        for record in generation.records
    ]
    registry["metrics"] = [
        {
            "step": item.seed,
            "split": "dev",
            "values": {
                "phone_macro_f1": item.metric_summary["gop_acoustic_posterior"][
                    "phone_macro_f1"
                ],
                "error_auprc": item.metric_summary["gop_acoustic_posterior"][
                    "error_auprc"
                ],
            },
        }
        for item in seed_artifacts
    ]
    registry["status"] = "completed"
    registry["timestamps"]["ended"] = _utc_now()
    _refresh_registry_observations(
        registry,
        start_time=start_time,
        threads_start=threads_start,
    )


def _failed_registry(
    registry: dict[str, Any],
    *,
    error: BaseException,
    stage: str,
    start_time: float,
    threads_start: int,
    orphan_generation_id: str | None,
) -> None:
    registry["status"] = "failed"
    registry["failure"] = {
        "exception_type": f"{type(error).__module__}.{type(error).__qualname__}",
        "stage": stage,
        "oom": isinstance(error, MemoryError),
    }
    registry["generation_id"] = orphan_generation_id
    registry["timestamps"]["ended"] = _utc_now()
    _refresh_registry_observations(
        registry,
        start_time=start_time,
        threads_start=threads_start,
    )


def _commit_document(
    *,
    config_path: Path,
    workspace_root: Path,
    source_records: Sequence[Mapping[str, str]],
    bundle: InputBundle,
    generation: PublishedGeneration,
    registry_path: Path,
) -> Mapping[str, Any]:
    return {
        "schema_version": SCHEMA_VERSIONS["commit"],
        "experiment_id": EXPERIMENT_ID,
        "status": "committed",
        "config": {
            "path": config_path.relative_to(workspace_root).as_posix(),
            "sha256": sha256_file(config_path),
        },
        "canonical_sources": [dict(record) for record in source_records],
        "input_caches": _cache_evidence_document(bundle.cache_evidence),
        "generation": {
            "id": generation.generation_id,
            "path": generation.path.relative_to(workspace_root).as_posix(),
            "artifact_count": len(generation.records),
            "records": [dict(record) for record in generation.records],
        },
        "registry": {
            "path": registry_path.relative_to(workspace_root).as_posix(),
            "sha256": sha256_file(registry_path),
            "required_status": "completed",
        },
        "seeds": list(SEEDS),
        "dev_access_audit": {
            "predict_call_count_total": len(SEEDS),
            "metric_open_count_total": len(SEEDS),
            "hyperparameter_selection_count_total": 0,
        },
        "oracle_alignment": True,
        "deployable": False,
    }


def _revalidate_precommit_chain(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    source_records: Sequence[Mapping[str, str]],
    bundle: InputBundle,
    generation: PublishedGeneration,
) -> Mapping[str, Any]:
    if sha256_json(generation.records) != generation.generation_id:
        raise B07TrainingError("generation ID changed before commit")
    current_records = _generation_records(generation.path)
    if current_records != generation.records:
        raise B07TrainingError("generation changed before commit")
    current_sources = validate_implementation_sources(
        workspace_root=workspace_root,
        config=config,
    )
    if tuple(current_sources) != tuple(source_records):
        raise B07TrainingError("canonical sources changed before commit")
    lock = load_json_strict(
        generation.path / "execution_lock.json",
        "execution lock",
    )
    lock_config = lock.get("config")
    if not isinstance(lock_config, Mapping) or lock_config.get("sha256") != sha256_file(
        config_path
    ):
        raise B07TrainingError("config changed before commit")
    if lock.get("canonical_sources") != [dict(item) for item in current_sources]:
        raise B07TrainingError("execution-lock source chain changed")
    if bundle.synthetic:
        current_evidence = bundle.cache_evidence
    else:
        data = config["data"]
        if not isinstance(data, Mapping):
            raise B07TrainingError("data config is invalid")
        wavlm = data["wavlm_phone_cache"]
        explicit = data["explicit_phone_cache"]
        if not isinstance(wavlm, Mapping) or not isinstance(explicit, Mapping):
            raise B07TrainingError("cache config is invalid")
        wavlm_evidence, _ = _parquet_shard_records(
            workspace_root=workspace_root,
            cache=wavlm,
            role="wavlm_phone_cache",
            output_kind="phone",
        )
        explicit_evidence, _ = _parquet_shard_records(
            workspace_root=workspace_root,
            cache=explicit,
            role="explicit_phone_cache",
            output_kind="phone",
        )
        current_evidence = (wavlm_evidence, explicit_evidence)
    if tuple(current_evidence) != tuple(bundle.cache_evidence):
        raise B07TrainingError("input cache evidence changed before commit")
    if lock.get("input_caches") != _cache_evidence_document(current_evidence):
        raise B07TrainingError("execution-lock input chain changed")
    return {
        "status": "valid",
        "generation_rehashed": True,
        "config_rehashed": True,
        "sources_rehashed": True,
        "cache_reports_and_shards_rehashed": not bundle.synthetic,
        "synthetic_input_attested": bundle.synthetic,
    }


def _registry_input_records_from_commit(
    input_caches: object,
) -> list[Mapping[str, Any]]:
    if not isinstance(input_caches, list) or len(input_caches) != 2:
        raise B07TrainingError("registry input cache lineage is invalid")
    records: list[Mapping[str, Any]] = []
    for raw_cache in input_caches:
        if not isinstance(raw_cache, Mapping):
            raise B07TrainingError("registry cache record is invalid")
        required = {
            "role",
            "cache_path",
            "report_path",
            "report_size_bytes",
            "report_sha256",
            "content_sha256",
            "shards",
        }
        if set(raw_cache) != required:
            raise B07TrainingError("registry cache record keys differ")
        role = str(raw_cache["role"])
        records.append(
            {
                "path": raw_cache["report_path"],
                "size_bytes": raw_cache["report_size_bytes"],
                "sha256": raw_cache["report_sha256"],
                "role": f"{role}_report",
            }
        )
        shards = raw_cache["shards"]
        if not isinstance(shards, list) or not shards:
            raise B07TrainingError("registry cache shard lineage is invalid")
        for shard in shards:
            if not isinstance(shard, Mapping) or set(shard) != {
                "name",
                "row_count",
                "size_bytes",
                "sha256",
            }:
                raise B07TrainingError("registry shard record keys differ")
            records.append(
                {
                    "path": f"{raw_cache['cache_path']}/{shard['name']}",
                    "size_bytes": shard["size_bytes"],
                    "sha256": shard["sha256"],
                    "row_count": shard["row_count"],
                    "role": f"{role}_shard",
                }
            )
    return records


def _validate_registry_semantics(
    *,
    registry: Mapping[str, Any],
    workspace_root: Path,
    config_path: Path,
    source_records: Sequence[Mapping[str, str]],
    generation_path: Path,
    generation_records: Sequence[Mapping[str, Any]],
    input_caches: object,
    synthetic: bool,
) -> None:
    if set(registry) != {
        "schema_version",
        "contract_schema_version",
        "run_id",
        "experiment_id",
        "status",
        "timestamps",
        "seeds",
        "argv",
        "inputs",
        "canonical_sources",
        "runtime",
        "accelerator",
        "git",
        "metrics",
        "artifacts",
        "failure",
        "generation_id",
        "generation_records",
        "precommit_validation",
        "precommit_chain_revalidation",
        "data_boundary",
    }:
        raise B07TrainingError("registry top-level keys differ")
    if (
        registry.get("schema_version") != 1
        or registry.get("contract_schema_version") != SCHEMA_VERSIONS["registry"]
        or registry.get("run_id") != "aihub71490-b07-gop-acoustic-posterior-v1"
        or registry.get("experiment_id") != EXPERIMENT_ID
        or registry.get("status") != "completed"
        or registry.get("failure") is not None
    ):
        raise B07TrainingError("registry header differs")
    if registry.get("seeds") != {f"experiment_seed_{seed}": seed for seed in SEEDS}:
        raise B07TrainingError("registry seeds differ")
    if registry.get("argv") != [
        "scripts/train_b07_gop_acoustic_posterior.py",
        "--execute-canonical",
    ]:
        raise B07TrainingError("registry argv differs")
    if registry.get("canonical_sources") != [dict(record) for record in source_records]:
        raise B07TrainingError("registry source lineage differs")
    inputs = registry.get("inputs")
    if not isinstance(inputs, Mapping) or set(inputs) != {
        "config",
        "data",
        "model",
        "checkpoint",
    }:
        raise B07TrainingError("registry input roles differ")
    expected_config = fingerprint_file(config_path, relative_to=workspace_root)
    if (
        inputs.get("config") != [expected_config]
        or inputs.get("data") != _registry_input_records_from_commit(input_caches)
        or inputs.get("model") != []
        or inputs.get("checkpoint") != []
    ):
        raise B07TrainingError("registry input lineage differs")
    expected_artifacts = [
        {
            "name": record["path"].replace("/", "__"),
            "kind": ("parquet" if str(record["path"]).endswith(".parquet") else "json"),
            "path": (
                generation_path.relative_to(workspace_root) / str(record["path"])
            ).as_posix(),
            "size_bytes": record["size_bytes"],
            "sha256": record["sha256"],
        }
        for record in generation_records
    ]
    if registry.get("artifacts") != expected_artifacts:
        raise B07TrainingError("registry artifact records differ")
    if registry.get("generation_records") != list(generation_records):
        raise B07TrainingError("registry generation records differ")
    if registry.get("generation_id") != sha256_json(generation_records):
        raise B07TrainingError("registry generation ID differs")
    expected_metrics = []
    for seed in SEEDS:
        metric = load_json_strict(
            generation_path / f"seed_{seed}" / "metrics.json",
            "registry seed metric",
        )
        expected_metrics.append(
            {
                "step": seed,
                "split": "dev",
                "values": {
                    "phone_macro_f1": metric["gop_acoustic_posterior"][
                        "phone_macro_f1"
                    ],
                    "error_auprc": metric["gop_acoustic_posterior"]["error_auprc"],
                },
            }
        )
    if registry.get("metrics") != expected_metrics:
        raise B07TrainingError("registry metric records differ")
    accelerator = registry.get("accelerator")
    if not isinstance(accelerator, Mapping) or set(accelerator) != {
        "requested",
        "probe_performed",
        "torch_imported",
        "torch_preimported_by_synthetic_test_harness",
        "production_cpu_boundary_claim_applicable",
        "cuda_initialized",
        "cuda_visible_devices",
    }:
        raise B07TrainingError("registry accelerator boundary differs")
    if (
        accelerator.get("requested") is not False
        or accelerator.get("probe_performed") is not False
        or accelerator.get("cuda_initialized") is not False
        or accelerator.get("cuda_visible_devices") != "empty_string"
        or accelerator.get("production_cpu_boundary_claim_applicable")
        != (not synthetic)
        or (
            not synthetic
            and (
                accelerator.get("torch_imported") is not False
                or accelerator.get("torch_preimported_by_synthetic_test_harness")
                is not False
            )
        )
        or (
            synthetic
            and accelerator.get("torch_preimported_by_synthetic_test_harness")
            != accelerator.get("torch_imported")
        )
    ):
        raise B07TrainingError("registry accelerator observation differs")
    if registry.get("data_boundary") != {
        "fit_split": "train",
        "metric_split": "dev",
        "stack_access_count": 0,
        "calibration_access_count": 0,
        "locked_test_access_count": 0,
        "raw_manifest_access_count": 0,
        "environment_file_access_count": 0,
    }:
        raise B07TrainingError("registry data boundary differs")
    precommit = registry.get("precommit_validation")
    if not isinstance(precommit, Mapping) or precommit.get("status") != "valid":
        raise B07TrainingError("registry precommit validation differs")
    chain = registry.get("precommit_chain_revalidation")
    if not isinstance(chain, Mapping) or chain.get("status") != "valid":
        raise B07TrainingError("registry precommit chain validation differs")
    timestamps = registry.get("timestamps")
    if not isinstance(timestamps, Mapping) or set(timestamps) != {
        "started",
        "updated",
        "ended",
    }:
        raise B07TrainingError("registry timestamps differ")
    if not all(isinstance(timestamps[key], str) for key in timestamps):
        raise B07TrainingError("registry timestamps are incomplete")
    runtime = registry.get("runtime")
    if not isinstance(runtime, Mapping) or set(runtime) != {
        "python",
        "platform",
        "packages",
        "b07_observations",
    }:
        raise B07TrainingError("registry runtime differs")
    observations = runtime.get("b07_observations")
    if not isinstance(observations, Mapping) or set(observations) != {
        "elapsed_seconds",
        "peak_rss_bytes",
        "configured_cpu_thread_cap",
        "observed_process_thread_count_at_start",
        "observed_process_thread_count_at_finish",
        "threadpools_inside_cap",
    }:
        raise B07TrainingError("registry resource observations differ")
    if (
        not math.isfinite(float(observations["elapsed_seconds"]))
        or float(observations["elapsed_seconds"]) < 0.0
        or int(observations["peak_rss_bytes"]) > 8 * 1024**3
        or observations["configured_cpu_thread_cap"] != 4
        or int(observations["observed_process_thread_count_at_start"]) < 1
        or int(observations["observed_process_thread_count_at_finish"]) < 1
    ):
        raise B07TrainingError("registry resource gate differs")
    pools = observations["threadpools_inside_cap"]
    if not isinstance(pools, list) or any(
        not isinstance(item, Mapping)
        or not isinstance(item.get("num_threads"), int)
        or int(item["num_threads"]) > 4
        or int(item["num_threads"]) < 1
        for item in pools
    ):
        raise B07TrainingError("registry threadpool gate differs")
    if not isinstance(registry.get("git"), Mapping):
        raise B07TrainingError("registry git metadata differs")


def _shallow_validate_commit(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    artifacts = config["artifacts"]
    if not isinstance(artifacts, Mapping):
        raise B07TrainingError("artifact config is invalid")
    commit_path = _safe_output_path(
        workspace_root,
        artifacts["commit_path"],
        "commit path",
    )
    commit = load_json_strict(commit_path, "B07 commit")
    expected_commit_keys = {
        "schema_version",
        "experiment_id",
        "status",
        "config",
        "canonical_sources",
        "input_caches",
        "generation",
        "registry",
        "seeds",
        "dev_access_audit",
        "oracle_alignment",
        "deployable",
    }
    if set(commit) != expected_commit_keys:
        raise B07TrainingError("B07 commit keys differ")
    if (
        commit.get("schema_version") != SCHEMA_VERSIONS["commit"]
        or commit.get("status") != "committed"
        or commit.get("experiment_id") != EXPERIMENT_ID
    ):
        raise B07TrainingError("B07 commit header differs")
    config_record = commit.get("config")
    if not isinstance(config_record, Mapping) or dict(config_record) != {
        "path": config_path.relative_to(workspace_root).as_posix(),
        "sha256": sha256_file(config_path),
    }:
        raise B07TrainingError("B07 commit config hash differs")
    current_sources = validate_implementation_sources(
        workspace_root=workspace_root,
        config=config,
    )
    if commit.get("canonical_sources") != [dict(record) for record in current_sources]:
        raise B07TrainingError("B07 commit source lineage differs")
    if commit.get("seeds") != list(SEEDS):
        raise B07TrainingError("B07 commit seeds differ")
    if commit.get("dev_access_audit") != {
        "predict_call_count_total": len(SEEDS),
        "metric_open_count_total": len(SEEDS),
        "hyperparameter_selection_count_total": 0,
    }:
        raise B07TrainingError("B07 commit dev access audit differs")
    if (
        commit.get("oracle_alignment") is not True
        or commit.get("deployable") is not False
    ):
        raise B07TrainingError("B07 commit claim boundary differs")
    generation = commit.get("generation")
    if not isinstance(generation, Mapping):
        raise B07TrainingError("B07 commit generation is absent")
    generation_path = _safe_output_path(
        workspace_root,
        generation.get("path"),
        "commit generation path",
    ).resolve(strict=True)
    expected_generation_path = f"{artifacts['generation_root']}/{generation.get('id')}"
    if generation.get("path") != expected_generation_path:
        raise B07TrainingError("B07 generation path differs")
    records = _generation_records(generation_path)
    if (
        list(records) != generation.get("records")
        or sha256_json(records) != generation.get("id")
        or len(records) != len(ARTIFACT_LAYOUT)
    ):
        raise B07TrainingError("B07 generation hash chain differs")
    lock = load_json_strict(generation_path / "execution_lock.json", "execution lock")
    if commit.get("input_caches") != lock.get("input_caches"):
        raise B07TrainingError("B07 commit input lineage differs")
    if lock.get("canonical_sources") != commit.get("canonical_sources"):
        raise B07TrainingError("B07 execution-lock source lineage differs")
    if lock.get("config") != commit.get("config"):
        raise B07TrainingError("B07 execution-lock config lineage differs")
    synthetic = lock.get("synthetic")
    if not isinstance(synthetic, bool):
        raise B07TrainingError("B07 execution-lock synthetic flag differs")
    registry = commit.get("registry")
    if not isinstance(registry, Mapping):
        raise B07TrainingError("B07 commit registry is absent")
    registry_path = _safe_output_path(
        workspace_root,
        registry.get("path"),
        "commit registry path",
    ).resolve(strict=True)
    if (
        registry.get("path") != artifacts["registry_path"]
        or registry.get("required_status") != "completed"
    ):
        raise B07TrainingError("B07 commit registry contract differs")
    if sha256_file(registry_path) != registry.get("sha256"):
        raise B07TrainingError("B07 registry hash differs")
    registry_document = load_json_strict(registry_path, "B07 registry")
    _validate_registry_semantics(
        registry=registry_document,
        workspace_root=workspace_root,
        config_path=config_path,
        source_records=current_sources,
        generation_path=generation_path,
        generation_records=records,
        input_caches=commit["input_caches"],
        synthetic=synthetic,
    )
    return {
        "status": "valid",
        "generation_id": generation["id"],
        "artifact_count": len(records),
        "commit_sha256": sha256_file(commit_path),
        "registry_sha256": sha256_file(registry_path),
    }


def _selected_detector_candidate(
    candidates: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    if len(candidates) != len(REGULARIZATION_CS) * 13:
        raise B07TrainingError("detector selection candidate count differs")
    return min(
        candidates,
        key=lambda item: (
            -float(item["phone_macro_f1"]),
            float(item["regularization_c"]),
            abs(float(item["threshold"]) - 0.5),
            float(item["threshold"]),
        ),
    )


def _validate_selection_trace(
    trace: Mapping[str, Any],
    acoustic: Mapping[str, Any],
    error: Mapping[str, Any],
) -> None:
    final_candidates = trace.get("final_temperature_candidates")
    if not isinstance(final_candidates, list):
        raise B07TrainingError("final temperature trace is absent")
    selected_temperature = _selected_temperature(final_candidates)
    if selected_temperature != trace.get(
        "selected_final_temperature"
    ) or selected_temperature != acoustic.get("selected_temperature"):
        raise B07TrainingError("selected final temperature is not reproducible")
    outer_candidates = trace.get("outer_temperature_candidates")
    selected_outer = trace.get("selected_outer_temperatures")
    if not isinstance(outer_candidates, Mapping) or not isinstance(
        selected_outer,
        Mapping,
    ):
        raise B07TrainingError("outer temperature trace is absent")
    for fold in DETECTOR_VALIDATION_FOLDS:
        raw = outer_candidates.get(str(fold))
        if not isinstance(raw, list):
            raise B07TrainingError("outer temperature candidate rows are absent")
        if _selected_temperature(raw) != selected_outer.get(str(fold)):
            raise B07TrainingError("outer selected temperature is not reproducible")
    gop_candidates = trace.get("gop_detector_candidates")
    control_candidates = trace.get("explicit_refit_control_candidates")
    if not isinstance(gop_candidates, list) or not isinstance(
        control_candidates,
        list,
    ):
        raise B07TrainingError("detector candidate traces are absent")
    selected_gop = _selected_detector_candidate(gop_candidates)
    selected_control = _selected_detector_candidate(control_candidates)
    trace_gop = trace.get("gop_selected")
    trace_control = trace.get("explicit_refit_control_selected")
    if not isinstance(trace_gop, Mapping) or not isinstance(trace_control, Mapping):
        raise B07TrainingError("detector selected records are absent")
    if (
        float(selected_gop["regularization_c"]) != float(trace_gop["regularization_c"])
        or float(selected_gop["threshold"]) != float(trace_gop["threshold"])
        or float(selected_gop["regularization_c"]) != float(error["selected_c"])
        or float(selected_gop["threshold"]) != float(error["selected_threshold"])
    ):
        raise B07TrainingError("GOP detector selection is not reproducible")
    control_checkpoint = error.get("explicit_refit_control")
    if not isinstance(control_checkpoint, Mapping):
        raise B07TrainingError("explicit refit checkpoint is absent")
    if (
        float(selected_control["regularization_c"])
        != float(trace_control["regularization_c"])
        or float(selected_control["threshold"]) != float(trace_control["threshold"])
        or float(selected_control["regularization_c"])
        != float(control_checkpoint["selected_c"])
        or float(selected_control["threshold"])
        != float(control_checkpoint["selected_threshold"])
    ):
        raise B07TrainingError("explicit refit selection is not reproducible")


def _precommit_validate_generation(
    *,
    bundle: InputBundle,
    generation: PublishedGeneration,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Deeply validate published raw outputs without a second dev embedding scan."""

    arrays = bundle.arrays
    dev_indexes = np.flatnonzero(arrays.split == "dev").astype(np.int64)
    expected_identifiers = bundle.table.select(
        [
            "utterance_id_sha256",
            "speaker_id_sha256",
            "phone_index",
            "split",
            "target_label",
            "binary_error_label",
            "l1",
            "gender",
            "proficiency",
        ]
    ).take(pa.array(dev_indexes))
    metric_rows: list[Mapping[str, Any]] = []
    for seed in SEEDS:
        directory = generation.path / f"seed_{seed}"
        acoustic = load_json_strict(
            directory / "acoustic_checkpoint.json",
            "acoustic checkpoint",
        )
        error = load_json_strict(
            directory / "error_checkpoint.json",
            "error checkpoint",
        )
        trace = load_json_strict(
            directory / "inner_selection_trace.json",
            "selection trace",
        )
        metrics = load_json_strict(directory / "metrics.json", "seed metrics")
        slices = load_json_strict(directory / "slices.json", "seed slices")
        predictions = pq.read_table(directory / "dev_predictions.parquet")
        if predictions.num_rows != dev_indexes.shape[0]:
            raise B07TrainingError("raw prediction row count differs")
        if not predictions.select(expected_identifiers.column_names).equals(
            expected_identifiers
        ):
            raise B07TrainingError("raw prediction keyed dev universe differs")
        _validate_selection_trace(trace, acoustic, error)

        categorical_rows = {
            name: bundle.table[name]
            .take(pa.array(dev_indexes))
            .combine_chunks()
            .to_pylist()
            for name in CATEGORICAL_FEATURES
        }
        explicit_numeric = np.asarray(
            arrays.numeric_explicit[dev_indexes],
            dtype=np.float64,
        )
        gop_features = np.column_stack(
            [
                np.asarray(
                    predictions[name].combine_chunks().to_numpy(zero_copy_only=False),
                    dtype=np.float64,
                )
                for name in GOP_NUMERIC_FEATURES
            ]
        )
        gop_numeric = np.column_stack((explicit_numeric, gop_features))
        replayed_gop = predict_serialized_detector_rows(
            categorical_rows=categorical_rows,
            numeric_rows=gop_numeric,
            document=error,
        )
        stored_gop = np.asarray(
            predictions["gop_probability"]
            .combine_chunks()
            .to_numpy(zero_copy_only=False),
            dtype=np.float64,
        )
        if not np.allclose(replayed_gop, stored_gop, rtol=1e-12, atol=1e-12):
            raise B07TrainingError("serialized GOP error checkpoint replay differs")
        control_checkpoint = error.get("explicit_refit_control")
        if not isinstance(control_checkpoint, Mapping):
            raise B07TrainingError("explicit refit checkpoint is absent")
        replayed_control = predict_serialized_detector_rows(
            categorical_rows=categorical_rows,
            numeric_rows=explicit_numeric,
            document=control_checkpoint,
        )
        stored_control = np.asarray(
            predictions["explicit_refit_probability"]
            .combine_chunks()
            .to_numpy(zero_copy_only=False),
            dtype=np.float64,
        )
        if not np.allclose(
            replayed_control,
            stored_control,
            rtol=1e-12,
            atol=1e-12,
        ):
            raise B07TrainingError("serialized explicit checkpoint replay differs")

        primary = arrays.primary[dev_indexes]
        labels = arrays.binary_labels[dev_indexes]
        gop_metric = _binary_metrics(
            labels[primary],
            stored_gop[primary],
            float(error["selected_threshold"]),
        )
        control_metric = _binary_metrics(
            labels[primary],
            stored_control[primary],
            float(control_checkpoint["selected_threshold"]),
        )
        paired = _paired_summary(
            labels[primary],
            stored_gop[primary],
            float(error["selected_threshold"]),
            stored_control[primary],
            float(control_checkpoint["selected_threshold"]),
        )
        if (
            metrics.get("gop_acoustic_posterior") != gop_metric
            or metrics.get("within_run_explicit_feature_refit_control")
            != control_metric
            or metrics.get("paired") != paired
        ):
            raise B07TrainingError("raw prediction metric recomputation differs")
        expected_gop_slices = _slice_report(
            bundle,
            dev_indexes=dev_indexes,
            primary_mask=primary,
            probabilities=stored_gop,
            threshold=float(error["selected_threshold"]),
        )
        expected_control_slices = _slice_report(
            bundle,
            dev_indexes=dev_indexes,
            primary_mask=primary,
            probabilities=stored_control,
            threshold=float(control_checkpoint["selected_threshold"]),
        )
        if (
            slices.get("gop_acoustic_posterior") != expected_gop_slices
            or slices.get("within_run_explicit_feature_refit_control")
            != expected_control_slices
        ):
            raise B07TrainingError("raw prediction slice recomputation differs")
        # Shape/norm validation uses no dev embedding and is therefore safe here.
        project_serialized_acoustic_checkpoint(
            embeddings=np.eye(
                len(PHONE_INVENTORY),
                int(acoustic["embedding_dimension"]),
                dtype=np.float32,
            ),
            target_labels=list(PHONE_INVENTORY),
            document=acoustic,
        )
        metric_rows.append(metrics)

    aggregate = load_json_strict(
        generation.path / "aggregate_report.json",
        "aggregate report",
    )
    paired_aggregate = load_json_strict(
        generation.path / "paired_explicit_refit_control_comparison.json",
        "paired comparison",
    )
    gate = config["evaluation"]["adoption_gate"]
    if not isinstance(gate, Mapping):
        raise B07TrainingError("adoption gate config is invalid")
    stubs = tuple(
        SeedArtifacts(
            seed=seed,
            documents={},
            prediction_table=pa.table({}),
            metric_summary=metrics,
            selected_c=0.0,
            selected_threshold=0.0,
            selected_temperature=0.0,
            dev_predict_calls=1,
            dev_metric_opens=1,
            dev_hyperparameter_accesses=0,
        )
        for seed, metrics in zip(SEEDS, metric_rows, strict=True)
    )
    expected_aggregate, expected_paired = _aggregate_documents(
        stubs,
        gate=float(gate["value"]),
    )
    if aggregate != expected_aggregate:
        raise B07TrainingError("full aggregate recomputation differs")
    if paired_aggregate != expected_paired:
        raise B07TrainingError("full paired aggregate recomputation differs")
    return {
        "status": "valid",
        "raw_prediction_metric_recomputed": True,
        "selection_rules_recomputed": True,
        "serialized_error_checkpoint_replayed": True,
        "additional_dev_embedding_scan": False,
        "seed_count": len(SEEDS),
        "artifact_count": len(generation.records),
    }


def _replay_acoustic_checkpoints(
    *,
    bundle: InputBundle,
    generation_path: Path,
) -> None:
    arrays = bundle.arrays
    dev_indexes = np.flatnonzero(arrays.split == "dev").astype(np.int64)
    for seed in SEEDS:
        acoustic = load_json_strict(
            generation_path / f"seed_{seed}" / "acoustic_checkpoint.json",
            "acoustic checkpoint",
        )
        predictions = pq.read_table(
            generation_path / f"seed_{seed}" / "dev_predictions.parquet",
            columns=list(GOP_NUMERIC_FEATURES),
        )
        stored = np.column_stack(
            [
                np.asarray(
                    predictions[name].combine_chunks().to_numpy(zero_copy_only=False),
                    dtype=np.float32,
                )
                for name in GOP_NUMERIC_FEATURES
            ]
        )
        offset = 0
        for indexes, embeddings in bundle.embedding_source.iter_split("dev"):
            labels = (
                bundle.table["target_label"]
                .take(pa.array(indexes))
                .combine_chunks()
                .to_pylist()
            )
            replayed = project_serialized_acoustic_checkpoint(
                embeddings=embeddings,
                target_labels=labels,
                document=acoustic,
            )
            expected_indexes = dev_indexes[offset : offset + indexes.shape[0]]
            if not np.array_equal(indexes, expected_indexes):
                raise B07TrainingError("acoustic replay dev indexes differ")
            if not np.allclose(
                replayed,
                stored[offset : offset + indexes.shape[0]],
                rtol=1e-6,
                atol=1e-6,
            ):
                raise B07TrainingError("serialized acoustic checkpoint replay differs")
            offset += indexes.shape[0]
        if offset != dev_indexes.shape[0]:
            raise B07TrainingError("serialized acoustic replay row count differs")


def validate_b07_commit(
    *,
    workspace_root: Path,
    config_path: Path,
    input_bundle: InputBundle | None = None,
    allow_synthetic_bundle: bool = False,
) -> Mapping[str, Any]:
    """Read-only full recomputation validator for an immutable B07 commit."""

    workspace_root = workspace_root.resolve(strict=True)
    config_path = config_path.resolve(strict=True)
    config = load_config(config_path)
    validate_config(config)
    validate_implementation_sources(workspace_root=workspace_root, config=config)
    if input_bundle is not None and (
        not allow_synthetic_bundle or not input_bundle.synthetic
    ):
        raise B07TrainingError("validator input override is synthetic-test-only")
    thread_cap = int(config["resource_contract"]["deterministic_cpu_thread_cap"])
    synthetic_harness = input_bundle is not None
    _validate_cpu_only_boundary(allow_preimported_test_harness=synthetic_harness)
    with threadpool_limits(limits=thread_cap):
        _bounded_threadpool_observation(thread_cap)
        shallow = _shallow_validate_commit(
            workspace_root=workspace_root,
            config_path=config_path,
            config=config,
        )
        bundle = (
            input_bundle
            if input_bundle is not None
            else load_canonical_input(workspace_root=workspace_root, config=config)
        )
        if bundle is None:
            raise B07TrainingError("validator input bundle is absent")
        artifacts = config["artifacts"]
        if not isinstance(artifacts, Mapping):
            raise B07TrainingError("artifact config is invalid")
        generation_path = _safe_output_path(
            workspace_root,
            f"{artifacts['generation_root']}/{shallow['generation_id']}",
            "generation path",
        ).resolve(strict=True)
        generation = PublishedGeneration(
            generation_id=str(shallow["generation_id"]),
            path=generation_path,
            records=_generation_records(generation_path),
            created=False,
        )
        _precommit_validate_generation(
            bundle=bundle,
            generation=generation,
            config=config,
        )
        _replay_acoustic_checkpoints(
            bundle=bundle,
            generation_path=generation_path,
        )
        recomputed_seeds = tuple(
            train_seed(bundle, seed=seed, config=config) for seed in SEEDS
        )
        source_records = validate_implementation_sources(
            workspace_root=workspace_root,
            config=config,
        )
        lock, aggregate, paired = _build_artifact_objects(
            config_path=config_path,
            workspace_root=workspace_root,
            config=config,
            source_records=source_records,
            bundle=bundle,
            seed_artifacts=recomputed_seeds,
        )
        if (
            load_json_strict(
                generation_path / "execution_lock.json",
                "execution lock",
            )
            != lock
        ):
            raise B07TrainingError("full recomputation execution lock differs")
        if (
            load_json_strict(
                generation_path / "aggregate_report.json",
                "aggregate report",
            )
            != aggregate
        ):
            raise B07TrainingError("full recomputation aggregate differs")
        if (
            load_json_strict(
                generation_path / "paired_explicit_refit_control_comparison.json",
                "paired report",
            )
            != paired
        ):
            raise B07TrainingError("full recomputation paired report differs")
        for seed_result in recomputed_seeds:
            directory = generation_path / f"seed_{seed_result.seed}"
            for name, document in seed_result.documents.items():
                if load_json_strict(directory / name, name) != document:
                    raise B07TrainingError(
                        f"full recomputation differs for seed {seed_result.seed} {name}"
                    )
            stored_predictions = pq.read_table(directory / "dev_predictions.parquet")
            if not stored_predictions.equals(seed_result.prediction_table):
                raise B07TrainingError(
                    f"full recomputation predictions differ for seed {seed_result.seed}"
                )
        if _peak_rss_bytes() > int(
            float(config["resource_contract"]["canonical_peak_rss_gate_gib"]) * 1024**3
        ):
            raise B07TrainingError("validator peak RSS gate exceeded")
        _bounded_threadpool_observation(thread_cap)
    _validate_cpu_only_boundary(allow_preimported_test_harness=synthetic_harness)
    return {
        **dict(shallow),
        "status": "valid",
        "mode": "validate_commit_only",
        "read_only": True,
        "full_train_selection_dev_recomputed": True,
        "serialized_acoustic_checkpoint_replayed": True,
        "serialized_error_checkpoint_replayed": True,
        "dev_hyperparameter_access_count": 0,
        "stack_access_count": 0,
        "calibration_access_count": 0,
        "locked_test_access_count": 0,
        "raw_manifest_access_count": 0,
        "environment_file_access_count": 0,
    }


def run_b07_training(
    *,
    workspace_root: Path,
    config_path: Path,
    execute_canonical: bool,
    input_bundle: InputBundle | None = None,
    allow_synthetic_bundle: bool = False,
    fault_stage: str | None = None,
) -> Mapping[str, Any]:
    """Train, publish, and commit B07 or recover the same generation."""

    workspace_root = workspace_root.resolve(strict=True)
    config_path = config_path.resolve(strict=True)
    config = load_config(config_path)
    validate_config(config)
    source_records = validate_implementation_sources(
        workspace_root=workspace_root,
        config=config,
    )
    artifacts = config["artifacts"]
    if not isinstance(artifacts, Mapping):
        raise B07TrainingError("artifact config is invalid")
    commit_path = _safe_output_path(
        workspace_root,
        artifacts["commit_path"],
        "commit path",
    )
    if commit_path.exists():
        return _shallow_validate_commit(
            workspace_root=workspace_root,
            config_path=config_path,
            config=config,
        )
    experiment = config["experiment"]
    execution = config["execution"]
    if not isinstance(experiment, Mapping) or not isinstance(execution, Mapping):
        raise B07TrainingError("execution permission config is invalid")
    permission_flags = (
        bool(experiment["canonical_execution_allowed_by_preflight"]),
        bool(execution["canonical_run_permitted_in_this_change"]),
    )
    if permission_flags[0] != permission_flags[1]:
        raise B07TrainingError("mixed canonical execution permission mode is forbidden")
    if input_bundle is None and (
        not execute_canonical
        or permission_flags != (True, True)
        or experiment.get("status") != "approved_for_canonical_execution"
    ):
        raise B07TrainingError(
            "canonical input access requires reviewed config permission transition "
            "and the explicit --execute-canonical gate"
        )
    if input_bundle is not None and (
        not allow_synthetic_bundle or not input_bundle.synthetic
    ):
        raise B07TrainingError("input bundle override is restricted to synthetic tests")
    if fault_stage not in {None, "after_generation", "after_registry"}:
        raise B07TrainingError("fault_stage is invalid")
    synthetic_harness = input_bundle is not None
    _validate_cpu_only_boundary(allow_preimported_test_harness=synthetic_harness)

    registry_path = _safe_output_path(
        workspace_root,
        artifacts["registry_path"],
        "registry path",
    )
    start_time = time.monotonic()
    threads_start = _process_thread_count()
    registry = _registry_base(
        config_path=config_path,
        workspace_root=workspace_root,
        source_records=source_records,
        start_time=start_time,
        threads_start=threads_start,
        synthetic_test_harness=synthetic_harness,
    )
    _atomic_write_json(registry_path, registry, overwrite=True)
    stage = "input_audit"
    generation: PublishedGeneration | None = None
    try:
        bundle = (
            input_bundle
            if input_bundle is not None
            else load_canonical_input(workspace_root=workspace_root, config=config)
        )
        if bundle is None:
            raise B07TrainingError("input bundle is absent")
        stage = "train_selection_dev_once"
        thread_cap = int(config["resource_contract"]["deterministic_cpu_thread_cap"])
        with threadpool_limits(limits=thread_cap):
            threadpool_observation = _bounded_threadpool_observation(thread_cap)
            seed_artifacts = tuple(
                train_seed(bundle, seed=seed, config=config) for seed in SEEDS
            )
            _bounded_threadpool_observation(thread_cap)
        registry["runtime"]["b07_observations"]["threadpools_inside_cap"] = (
            threadpool_observation
        )
        _validate_cpu_only_boundary(allow_preimported_test_harness=synthetic_harness)
        peak_gate = float(config["resource_contract"]["canonical_peak_rss_gate_gib"])
        if _peak_rss_bytes() > int(peak_gate * 1024**3):
            raise B07TrainingError("canonical peak RSS gate exceeded")
        lock, aggregate, paired = _build_artifact_objects(
            config_path=config_path,
            workspace_root=workspace_root,
            config=config,
            source_records=source_records,
            bundle=bundle,
            seed_artifacts=seed_artifacts,
        )
        stage = "generation_publish"
        generation = publish_generation(
            workspace_root=workspace_root,
            config=config,
            lock=lock,
            aggregate=aggregate,
            paired=paired,
            seed_artifacts=seed_artifacts,
        )
        if fault_stage == "after_generation":
            raise B07TrainingError("injected fault after generation publication")
        stage = "precommit_deep_validation"
        with threadpool_limits(limits=thread_cap):
            _bounded_threadpool_observation(thread_cap)
            registry["precommit_validation"] = _precommit_validate_generation(
                bundle=bundle,
                generation=generation,
                config=config,
            )
            _bounded_threadpool_observation(thread_cap)
        stage = "precommit_chain_revalidation"
        registry["precommit_chain_revalidation"] = _revalidate_precommit_chain(
            workspace_root=workspace_root,
            config_path=config_path,
            config=config,
            source_records=source_records,
            bundle=bundle,
            generation=generation,
        )
        stage = "registry_complete"
        _complete_registry(
            registry,
            bundle=bundle,
            generation=generation,
            seed_artifacts=seed_artifacts,
            workspace_root=workspace_root,
            start_time=start_time,
            threads_start=threads_start,
        )
        _atomic_write_json(registry_path, registry, overwrite=True)
        if fault_stage == "after_registry":
            raise B07TrainingError("injected fault after completed registry")
        stage = "commit_publish"
        _revalidate_precommit_chain(
            workspace_root=workspace_root,
            config_path=config_path,
            config=config,
            source_records=source_records,
            bundle=bundle,
            generation=generation,
        )
        if _peak_rss_bytes() > int(peak_gate * 1024**3):
            raise B07TrainingError("final canonical peak RSS gate exceeded")
        _validate_cpu_only_boundary(allow_preimported_test_harness=synthetic_harness)
        _refresh_registry_observations(
            registry,
            start_time=start_time,
            threads_start=threads_start,
        )
        _atomic_write_json(registry_path, registry, overwrite=True)
        if load_json_strict(registry_path, "completed registry") != registry:
            raise B07TrainingError("completed registry changed before commit")
        commit = _commit_document(
            config_path=config_path,
            workspace_root=workspace_root,
            source_records=source_records,
            bundle=bundle,
            generation=generation,
            registry_path=registry_path,
        )
        _atomic_write_json(commit_path, commit, overwrite=False)
    except BaseException as error:
        if not commit_path.exists():
            _failed_registry(
                registry,
                error=error,
                stage=stage,
                start_time=start_time,
                threads_start=threads_start,
                orphan_generation_id=(
                    generation.generation_id if generation is not None else None
                ),
            )
            _atomic_write_json(registry_path, registry, overwrite=True)
        raise
    return _shallow_validate_commit(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
    )
