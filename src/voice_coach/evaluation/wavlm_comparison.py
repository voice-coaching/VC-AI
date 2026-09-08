"""Strict paired comparison of frozen-head WavLM development predictions.

The comparator reads only completed aggregate reports, their completed run
registries, and privacy-safe dev prediction Parquet files.  It never reads a
feature cache, raw audio, manifests, stack, calibration, locked-test, or
environment files.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

from voice_coach.evaluation.metrics import (
    auprc,
    holm_correction,
    icc_2_1,
    macro_f1,
    mae,
    quadratic_weighted_kappa,
    spearman_correlation,
)


REPORT_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_comparison.v1"
EXECUTION_RECEIPT_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_comparison_receipt.v1"
COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_comparison_commit.v1"
HEAD_REPORT_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_report.v1"
PREDICTION_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_predictions.v1"
SEEDS = (42, 43, 44)
VARIANTS = ("base_plus", "large")
OUTCOMES = (
    "pronunciation_score",
    "fluency_score",
    "comprehensibility_score",
)
UTTERANCE_METRICS = ("qwk", "spearman", "mae", "icc_2_1")
PHONE_METRICS = ("macro_f1", "auprc", "event_f1")
FORBIDDEN_PATH_PARTS = frozenset(
    {
        "raw",
        "restricted",
        ".env",
        "stack",
        "calibration",
        "locked",
        "locked-test",
        "locked_test",
    }
)
FORBIDDEN_PATH_PREFIXES = (
    "raw",
    "restricted",
    "stack",
    "calibration",
    "locked",
)
SHA256_LENGTH = 64
PREDICTION_METADATA = {
    b"voice_coach.schema_version": PREDICTION_SCHEMA_VERSION.encode(),
    b"voice_coach.development_only": b"true",
    b"voice_coach.oracle_alignment": b"true",
    b"voice_coach.contains_direct_identifiers": b"false",
    b"voice_coach.access_scope": b"restricted_internal",
    b"voice_coach.shareable": b"false",
}


class ComparisonContractError(ValueError):
    """Raised when a source artifact violates the paired comparison contract."""


@dataclass(frozen=True, slots=True)
class PhoneIdentity:
    utterance_ids: np.ndarray
    speaker_ids: np.ndarray
    phone_indices: np.ndarray
    target_labels: np.ndarray
    targets: np.ndarray


@dataclass(frozen=True, slots=True)
class PhonePrediction:
    probabilities: np.ndarray
    predictions: np.ndarray
    threshold: float


@dataclass(frozen=True, slots=True)
class UtteranceIdentity:
    utterance_ids: np.ndarray
    speaker_ids: np.ndarray
    targets: np.ndarray
    observed: np.ndarray


@dataclass(frozen=True, slots=True)
class UtterancePrediction:
    predictions: np.ndarray


@dataclass(frozen=True, slots=True)
class SourceRun:
    variant: str
    report_path: Path
    report: Mapping[str, Any]
    report_sha256: str
    registry_path: Path
    registry: Mapping[str, Any]
    registry_sha256: str
    phone: Mapping[int, PhonePrediction]
    utterance: Mapping[int, UtterancePrediction]
    prediction_sources: Mapping[str, Mapping[str, Any]]
    protected_paths: frozenset[Path]


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    estimate: float
    confidence_lower: float
    confidence_upper: float
    confidence_level: float
    n_resamples: int
    n_valid_resamples: int
    n_speakers: int
    seed: int
    p_value_two_sided_zero: float


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ComparisonContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ComparisonContractError(f"{name} must be a list")
    return value


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ComparisonContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ComparisonContractError(f"{name} must be finite")
    return result


def _integer(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ComparisonContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ComparisonContractError(f"{name} must be at least {minimum}")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ComparisonContractError(f"{name} must be a non-empty string")
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ComparisonContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_nonfinite_tree(item, name)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_nonfinite_tree(item, name)


def _canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
    }
    if pretty:
        options["indent"] = 2
    return (json.dumps(_json_safe(value), **options) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _relative_file_record(
    workspace_root: Path,
    path: Path,
    payload: bytes,
) -> dict[str, Any]:
    return {
        "path": path.relative_to(workspace_root).as_posix(),
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _write_immutable_bytes(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ComparisonContractError("comparison generation path is not immutable")
        return False
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    published = False
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        published = True
        try:
            _fsync_directory(path.parent)
        except BaseException:
            path.unlink(missing_ok=True)
            try:
                _fsync_directory(path.parent)
            except OSError:
                pass
            raise
        return True
    finally:
        temporary.unlink(missing_ok=True)
        if published and not path.exists():
            try:
                _fsync_directory(path.parent)
            except OSError:
                pass


def _remove_created_file(path: Path) -> None:
    if not path.exists():
        return
    path.unlink()
    try:
        _fsync_directory(path.parent)
    except OSError:
        pass


def _statistics_sha256(report: Mapping[str, Any]) -> str:
    statistics_payload = {
        "metrics": report["metrics"],
        "primary_comparison": report["primary_comparison"],
        "multiplicity": report["multiplicity"],
    }
    return _sha256_bytes(_canonical_json_bytes(statistics_payload, pretty=False))


def _publish_comparison_generation(
    *,
    workspace_root: Path,
    generation_directory: Path,
    commit_path: Path,
    report: Mapping[str, Any],
    sources: Mapping[str, Any],
    protected_paths: Collection[Path] = (),
) -> Mapping[str, Any]:
    report_bytes = _canonical_json_bytes(report, pretty=True)
    generation_id = _sha256_bytes(report_bytes)
    generation_root = generation_directory / generation_id
    report_path = generation_root / "report.json"
    receipt_path = generation_root / "execution_receipt.json"
    if len({report_path, receipt_path, commit_path}) != 3:
        raise ComparisonContractError("comparison output paths collide")
    protected = set(protected_paths)
    if {report_path, receipt_path, commit_path} & protected:
        raise ComparisonContractError("comparison output would overwrite an input")
    report_record = _relative_file_record(
        workspace_root,
        report_path,
        report_bytes,
    )
    receipt_core: dict[str, Any] = {
        "schema_version": EXECUTION_RECEIPT_SCHEMA_VERSION,
        "status": "completed",
        "generation_id": generation_id,
        "comparison_report": report_record,
        "execution_sources": sources,
        "statistics_sha256": _statistics_sha256(report),
    }
    receipt_core["binding_sha256"] = _sha256_bytes(
        _canonical_json_bytes(receipt_core, pretty=False)
    )
    receipt_bytes = _canonical_json_bytes(receipt_core, pretty=True)
    receipt_record = _relative_file_record(
        workspace_root,
        receipt_path,
        receipt_bytes,
    )
    commit_core: dict[str, Any] = {
        "schema_version": COMMIT_SCHEMA_VERSION,
        "status": "committed",
        "generation_id": generation_id,
        "comparison_report": report_record,
        "execution_receipt": receipt_record,
        "statistics_sha256": receipt_core["statistics_sha256"],
    }
    commit_core["binding_sha256"] = _sha256_bytes(
        _canonical_json_bytes(commit_core, pretty=False)
    )

    created: list[Path] = []
    commit_replaced = False
    try:
        if _write_immutable_bytes(report_path, report_bytes):
            created.append(report_path)
        if _write_immutable_bytes(receipt_path, receipt_bytes):
            created.append(receipt_path)
        commit_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = commit_path.with_name(f".{commit_path.name}.part-{uuid4().hex}")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(
                    commit_core,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, commit_path)
            commit_replaced = True
            _fsync_directory(commit_path.parent)
        finally:
            temporary.unlink(missing_ok=True)
    except BaseException:
        if not commit_replaced:
            for path in reversed(created):
                _remove_created_file(path)
            try:
                generation_root.rmdir()
                _fsync_directory(generation_root.parent)
            except OSError:
                pass
        raise
    return commit_core


def _load_json(path: Path, name: str) -> Mapping[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ComparisonContractError(
                    f"{name} contains duplicate JSON key {key!r}"
                )
            output[key] = value
        return output

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ComparisonContractError(f"{name} contains {token}")
            ),
        )
        _reject_nonfinite_tree(value, name)
    except ComparisonContractError:
        raise
    except json.JSONDecodeError as error:
        raise ComparisonContractError(f"{name} is invalid JSON") from error
    return _mapping(value, name)


def _path_parts_are_safe(path: Path) -> bool:
    for part in path.parts:
        lowered = part.casefold()
        if (
            lowered in FORBIDDEN_PATH_PARTS
            or any(
                lowered.startswith(f"{prefix}{separator}")
                for prefix in FORBIDDEN_PATH_PREFIXES
                for separator in ("_", "-")
            )
            or lowered.startswith(".env")
        ):
            return False
    return True


def _safe_path(
    workspace_root: Path,
    configured: str | Path,
    *,
    name: str,
    must_exist: bool = True,
) -> Path:
    if not isinstance(configured, (str, Path)):
        raise ComparisonContractError(f"{name} path must be a string or Path")
    candidate = Path(configured)
    if not _path_parts_are_safe(candidate):
        raise ComparisonContractError(f"{name} violates privacy path policy")
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (workspace_root / candidate).resolve()
    )
    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError as error:
        raise ComparisonContractError(f"{name} must stay inside workspace") from error
    if not _path_parts_are_safe(relative):
        raise ComparisonContractError(f"{name} violates privacy path policy")
    if must_exist and not resolved.is_file():
        raise ComparisonContractError(f"{name} is not a regular file")
    return resolved


def _safe_relative_artifact(
    workspace_root: Path,
    report_path: Path,
    relative: object,
    *,
    name: str,
) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ComparisonContractError(f"{name} path must be a non-empty string")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ComparisonContractError(f"{name} path must be safe and relative")
    return _safe_path(
        workspace_root,
        report_path.parent / candidate,
        name=name,
    )


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    output: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in output:
            raise ComparisonContractError(
                f"comparison config contains duplicate YAML key {key!r}"
            )
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_and_validate_config(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=_UniqueKeyLoader,
        )
    except ComparisonContractError:
        raise
    except yaml.YAMLError as error:
        raise ComparisonContractError("comparison config is invalid YAML") from error
    _reject_nonfinite_tree(loaded, "comparison config")
    root = dict(_mapping(loaded, "comparison config"))
    if root.get("schema_version") != 1:
        raise ComparisonContractError("comparison config schema_version must be 1")
    experiment = _mapping(root.get("experiment"), "experiment")
    if (
        experiment.get("stage") != "frozen_head_comparison"
        or experiment.get("development_only") is not True
        or experiment.get("oracle_alignment") is not True
    ):
        raise ComparisonContractError("experiment scope differs from S103")
    inputs = _mapping(root.get("inputs"), "inputs")
    for key in (
        "base_plus_report",
        "large_report",
        "expected_trainer_source_path",
        "expected_trainer_source_sha256",
        "expected_head_source_path",
        "expected_head_source_sha256",
        "expected_modeling_manifest_sha256",
        "expected_explicit_utterance_labels_sha256",
    ):
        if not isinstance(inputs.get(key), str) or not inputs[key]:
            raise ComparisonContractError(f"inputs.{key} must be a string")
    for key in (
        "expected_trainer_source_sha256",
        "expected_head_source_sha256",
        "expected_modeling_manifest_sha256",
        "expected_explicit_utterance_labels_sha256",
    ):
        if not is_sha256(inputs[key]):
            raise ComparisonContractError(f"inputs.{key} must be SHA-256")
    variants = _mapping(inputs.get("expected_variants"), "expected_variants")
    expected_variant_ids = {
        "base_plus": "microsoft/wavlm-base-plus",
        "large": "microsoft/wavlm-large",
    }
    for variant, encoder_id in expected_variant_ids.items():
        variant_contract = _mapping(variants.get(variant), variant)
        if variant_contract.get("encoder_id") != encoder_id:
            raise ComparisonContractError(f"{variant} encoder ID differs")
        training_config_path = variant_contract.get("training_config_path")
        if not isinstance(training_config_path, str) or not training_config_path:
            raise ComparisonContractError(
                f"{variant} training config path must be a string"
            )
        if not is_sha256(variant_contract.get("training_config_sha256")):
            raise ComparisonContractError(
                f"{variant} training config SHA-256 is invalid"
            )
    rows = _mapping(inputs.get("expected_cache_rows"), "expected_cache_rows")
    for kind in ("utterance", "phone", "word"):
        value = rows.get(kind)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ComparisonContractError(f"expected_cache_rows.{kind} is invalid")

    comparison = _mapping(root.get("comparison"), "comparison")
    if comparison.get("seeds") != list(SEEDS):
        raise ComparisonContractError("comparison seeds must be 42/43/44")
    expected_comparison = {
        "primary_scope": "primary_dev_paired",
        "effect_direction": "large_minus_base",
        "require_identical_prediction_order": True,
        "require_completed_source_registries": True,
        "allowed_metric_split": "dev",
        "forbidden_splits": ["stack", "calibration", "locked_test"],
    }
    for key, expected in expected_comparison.items():
        if comparison.get(key) != expected:
            raise ComparisonContractError(f"comparison.{key} differs")

    bootstrap = _mapping(root.get("bootstrap"), "bootstrap")
    if (
        bootstrap.get("method") != "paired_speaker_cluster_percentile"
        or bootstrap.get("n_resamples") != 5_000
        or bootstrap.get("confidence_level") != 0.95
        or bootstrap.get("p_value") != "two_sided_zero_with_plus_one_correction"
        or bootstrap.get("seed") != 42
    ):
        raise ComparisonContractError("bootstrap contract differs")
    gate = _mapping(root.get("gate"), "gate")
    if gate != {
        "metric": "phone_macro_f1",
        "absolute_improvement": 0.03,
        "rule": "point_estimate_gte_threshold",
    }:
        raise ComparisonContractError("Large adoption gate differs")
    multiplicity = _mapping(root.get("multiplicity"), "multiplicity")
    if (
        multiplicity.get("family") != "utterance_icc_2_1_across_outcomes"
        or multiplicity.get("outcomes") != list(OUTCOMES)
        or multiplicity.get("method") != "holm"
        or multiplicity.get("alpha") != 0.05
    ):
        raise ComparisonContractError("Holm family differs")
    event = _mapping(root.get("event_f1"), "event_f1")
    if event != {
        "unit": "maximal_contiguous_positive_phone_index_run_within_utterance",
        "match": "one_to_one_any_phone_index_overlap",
        "zero_event_policy": "undefined",
    }:
        raise ComparisonContractError("event F1 definition differs")
    privacy = _mapping(root.get("privacy"), "privacy")
    if privacy != {
        "aggregate_only_output": True,
        "input_access_scope": "restricted_internal",
        "shareable": False,
        "environment_files_forbidden": True,
        "raw_data_access_forbidden": True,
    }:
        raise ComparisonContractError("privacy contract differs")
    output = _mapping(root.get("output"), "output")
    if (
        not isinstance(output.get("generation_directory"), str)
        or not output.get("generation_directory")
        or not isinstance(output.get("commit_manifest"), str)
        or not output.get("commit_manifest")
        or output.get("atomic_publish") is not True
        or output.get("create_execution_receipt") is not True
    ):
        raise ComparisonContractError("output contract differs")
    return root


def _prediction_schema(kind: str) -> pa.Schema:
    shared = [
        pa.field("schema_version", pa.string(), nullable=True),
        pa.field("development_only", pa.bool_(), nullable=True),
        pa.field("oracle_alignment", pa.bool_(), nullable=True),
        pa.field("seed", pa.int32(), nullable=True),
        pa.field("split", pa.string(), nullable=True),
        pa.field("utterance_id_sha256", pa.string(), nullable=True),
        pa.field("speaker_id_sha256", pa.string(), nullable=True),
    ]
    if kind == "phone":
        fields = [
            *shared,
            pa.field("phone_index", pa.int32(), nullable=True),
            pa.field("target_label", pa.string(), nullable=True),
            pa.field("y_true", pa.int8(), nullable=True),
            pa.field("y_pred", pa.int8(), nullable=True),
            pa.field("error_probability", pa.float32(), nullable=True),
            pa.field("decision_threshold", pa.float32(), nullable=True),
        ]
    elif kind == "utterance":
        fields = list(shared)
        for outcome in OUTCOMES:
            fields.extend(
                (
                    pa.field(f"{outcome}_true", pa.float64(), nullable=True),
                    pa.field(f"{outcome}_pred", pa.float32(), nullable=True),
                    pa.field(f"{outcome}_observed", pa.bool_(), nullable=True),
                )
            )
    else:  # pragma: no cover - internal programming error
        raise AssertionError(f"unknown prediction kind: {kind}")
    return pa.schema(fields, metadata=PREDICTION_METADATA)


def _read_prediction_table(path: Path, *, kind: str) -> pa.Table:
    parquet = pq.ParquetFile(path)
    expected = _prediction_schema(kind)
    if not parquet.schema_arrow.equals(expected, check_metadata=True):
        raise ComparisonContractError(f"{kind} prediction Arrow schema differs")
    table = parquet.read()
    null_columns = [
        name
        for name, column in zip(
            table.column_names,
            table.columns,
            strict=True,
        )
        if column.null_count
    ]
    if null_columns:
        raise ComparisonContractError(
            f"{kind} prediction contains nulls in {','.join(null_columns)}"
        )
    return table


def _numpy_column(table: pa.Table, name: str, dtype: Any) -> np.ndarray:
    values = table.column(name).combine_chunks().to_numpy(zero_copy_only=False)
    return np.asarray(values, dtype=dtype)


def _string_column(table: pa.Table, name: str) -> np.ndarray:
    return np.asarray(table.column(name).to_pylist(), dtype=object)


def _validate_hash_array(values: np.ndarray, name: str) -> None:
    if any(not is_sha256(value) for value in values.tolist()):
        raise ComparisonContractError(f"{name} contains a non-SHA-256 value")


def _validate_phone_key_order(identity: PhoneIdentity) -> None:
    seen_utterances: set[str] = set()
    current: str | None = None
    expected_index = 0
    for utterance, raw_index in zip(
        identity.utterance_ids,
        identity.phone_indices,
        strict=True,
    ):
        uid = str(utterance)
        index = int(raw_index)
        if uid != current:
            if uid in seen_utterances:
                raise ComparisonContractError(
                    "phone prediction utterance rows are not contiguous"
                )
            seen_utterances.add(uid)
            current = uid
            expected_index = 0
        if index != expected_index:
            raise ComparisonContractError(
                "phone indices are not contiguous from zero within utterance"
            )
        expected_index += 1


def load_phone_prediction(
    path: Path,
    *,
    expected_sha256: str,
    expected_rows: int,
    seed: int,
    expected_threshold: float,
) -> tuple[PhoneIdentity, PhonePrediction]:
    if sha256_file(path) != expected_sha256:
        raise ComparisonContractError("phone prediction SHA-256 differs")
    table = _read_prediction_table(path, kind="phone")
    if table.num_rows != expected_rows or table.num_rows <= 0:
        raise ComparisonContractError("phone prediction row count differs")

    schema_versions = _string_column(table, "schema_version")
    development = _numpy_column(table, "development_only", np.bool_)
    oracle = _numpy_column(table, "oracle_alignment", np.bool_)
    seeds = _numpy_column(table, "seed", np.int64)
    splits = _string_column(table, "split")
    if (
        set(schema_versions.tolist()) != {PREDICTION_SCHEMA_VERSION}
        or not development.all()
        or not oracle.all()
        or set(seeds.tolist()) != {seed}
        or set(splits.tolist()) != {"dev"}
    ):
        raise ComparisonContractError("phone prediction row policy differs")

    utterance_ids = _string_column(table, "utterance_id_sha256")
    speaker_ids = _string_column(table, "speaker_id_sha256")
    _validate_hash_array(utterance_ids, "phone utterance IDs")
    _validate_hash_array(speaker_ids, "phone speaker IDs")
    phone_indices = _numpy_column(table, "phone_index", np.int64)
    if np.any(phone_indices < 0):
        raise ComparisonContractError("phone prediction has a negative index")
    target_labels = _string_column(table, "target_label")
    if any(not isinstance(value, str) or not value for value in target_labels):
        raise ComparisonContractError("phone target label is empty")
    targets = _numpy_column(table, "y_true", np.int64)
    predictions = _numpy_column(table, "y_pred", np.int64)
    if not np.isin(targets, [0, 1]).all() or not np.isin(predictions, [0, 1]).all():
        raise ComparisonContractError("phone prediction labels are not binary")
    probabilities = _numpy_column(table, "error_probability", np.float64)
    if (
        not np.isfinite(probabilities).all()
        or np.any(probabilities < 0)
        or np.any(probabilities > 1)
    ):
        raise ComparisonContractError("phone probabilities are invalid")
    thresholds = _numpy_column(table, "decision_threshold", np.float64)
    if not np.isfinite(thresholds).all() or not np.allclose(
        thresholds,
        expected_threshold,
        rtol=0.0,
        atol=1e-6,
    ):
        raise ComparisonContractError("phone decision threshold differs")
    recomputed = (probabilities >= expected_threshold).astype(np.int64)
    if not np.array_equal(predictions, recomputed):
        raise ComparisonContractError(
            "stored phone decisions do not match probability and threshold"
        )
    identity = PhoneIdentity(
        utterance_ids=utterance_ids,
        speaker_ids=speaker_ids,
        phone_indices=phone_indices,
        target_labels=target_labels,
        targets=targets,
    )
    _validate_phone_key_order(identity)
    return identity, PhonePrediction(
        probabilities=probabilities,
        predictions=predictions,
        threshold=expected_threshold,
    )


def load_utterance_prediction(
    path: Path,
    *,
    expected_sha256: str,
    expected_rows: int,
    seed: int,
) -> tuple[UtteranceIdentity, UtterancePrediction]:
    if sha256_file(path) != expected_sha256:
        raise ComparisonContractError("utterance prediction SHA-256 differs")
    table = _read_prediction_table(path, kind="utterance")
    if table.num_rows != expected_rows or table.num_rows <= 0:
        raise ComparisonContractError("utterance prediction row count differs")
    if (
        set(_string_column(table, "schema_version").tolist())
        != {PREDICTION_SCHEMA_VERSION}
        or not _numpy_column(table, "development_only", np.bool_).all()
        or not _numpy_column(table, "oracle_alignment", np.bool_).all()
        or set(_numpy_column(table, "seed", np.int64).tolist()) != {seed}
        or set(_string_column(table, "split").tolist()) != {"dev"}
    ):
        raise ComparisonContractError("utterance prediction row policy differs")

    utterance_ids = _string_column(table, "utterance_id_sha256")
    speaker_ids = _string_column(table, "speaker_id_sha256")
    _validate_hash_array(utterance_ids, "utterance prediction IDs")
    _validate_hash_array(speaker_ids, "utterance prediction speaker IDs")
    if len(set(utterance_ids.tolist())) != len(utterance_ids):
        raise ComparisonContractError("utterance prediction IDs are duplicated")
    targets = np.empty((table.num_rows, len(OUTCOMES)), dtype=np.float64)
    predictions = np.empty_like(targets)
    observed = np.empty_like(targets, dtype=np.bool_)
    for index, outcome in enumerate(OUTCOMES):
        targets[:, index] = _numpy_column(
            table,
            f"{outcome}_true",
            np.float64,
        )
        predictions[:, index] = _numpy_column(
            table,
            f"{outcome}_pred",
            np.float64,
        )
        observed[:, index] = _numpy_column(
            table,
            f"{outcome}_observed",
            np.bool_,
        )
        if (
            not np.isfinite(predictions[:, index]).all()
            or np.any(predictions[:, index] < 1)
            or np.any(predictions[:, index] > 5)
        ):
            raise ComparisonContractError(
                f"{outcome} utterance predictions are outside 1-5"
            )
        if not np.isfinite(targets[observed[:, index], index]).all():
            raise ComparisonContractError(f"{outcome} observed targets are non-finite")
        if not np.isnan(targets[~observed[:, index], index]).all():
            raise ComparisonContractError(f"{outcome} unobserved targets must be NaN")
        valid_targets = targets[observed[:, index], index]
        if not np.isin(valid_targets, [1, 2, 3, 4, 5]).all():
            raise ComparisonContractError(f"{outcome} targets are outside 1-5")
    return (
        UtteranceIdentity(
            utterance_ids=utterance_ids,
            speaker_ids=speaker_ids,
            targets=targets,
            observed=observed,
        ),
        UtterancePrediction(predictions=predictions),
    )


def _same_array(left: np.ndarray, right: np.ndarray, name: str) -> None:
    if left.dtype.kind == "f" or right.dtype.kind == "f":
        equal = np.array_equal(left, right, equal_nan=True)
    else:
        equal = np.array_equal(left, right)
    if not equal:
        raise ComparisonContractError(f"paired prediction {name} differs")


def _validate_phone_identity(
    canonical: PhoneIdentity,
    candidate: PhoneIdentity,
) -> None:
    _same_array(canonical.utterance_ids, candidate.utterance_ids, "phone IDs")
    _same_array(canonical.speaker_ids, candidate.speaker_ids, "phone speakers")
    _same_array(canonical.phone_indices, candidate.phone_indices, "phone indices")
    _same_array(canonical.target_labels, candidate.target_labels, "phone targets")
    _same_array(canonical.targets, candidate.targets, "phone labels")


def _validate_utterance_identity(
    canonical: UtteranceIdentity,
    candidate: UtteranceIdentity,
) -> None:
    _same_array(
        canonical.utterance_ids,
        candidate.utterance_ids,
        "utterance IDs",
    )
    _same_array(
        canonical.speaker_ids,
        candidate.speaker_ids,
        "utterance speakers",
    )
    _same_array(canonical.targets, candidate.targets, "utterance targets")
    _same_array(canonical.observed, candidate.observed, "utterance masks")


def _validate_registry(
    *,
    workspace_root: Path,
    report_path: Path,
    report: Mapping[str, Any],
    report_sha256: str,
    variant: str,
    config: Mapping[str, Any],
) -> tuple[Path, Mapping[str, Any], str, frozenset[Path]]:
    registry_contract = _mapping(
        report.get("experiment_registry"),
        "experiment_registry",
    )
    if (
        registry_contract.get("integrated") is not True
        or registry_contract.get("t03_complete") is not True
        or registry_contract.get("metrics_recorded") is not True
        or registry_contract.get("artifacts_recorded") is not True
        or registry_contract.get("implementation_source_hashes_match_registry_inputs")
        is not True
        or registry_contract.get("runtime_packages_gpu_git_recorded") is not True
        or registry_contract.get("seeds_recorded") is not True
    ):
        raise ComparisonContractError("source experiment registry is incomplete")
    metadata_path = _string(
        registry_contract.get("metadata_path"),
        "source registry metadata_path",
    )
    registry_path = _safe_path(
        workspace_root,
        metadata_path,
        name="source registry",
    )
    registry = _load_json(registry_path, "source registry")
    if (
        registry.get("schema_version") != 1
        or registry.get("status") != "completed"
        or registry.get("failure") is not None
        or registry.get("run_id") != registry_contract.get("run_id")
    ):
        raise ComparisonContractError("source registry completion differs")
    timestamps = _mapping(registry.get("timestamps"), "registry timestamps")
    if timestamps.get("ended") is None:
        raise ComparisonContractError("source registry has no completion timestamp")
    seeds = _mapping(registry.get("seeds"), "registry seeds")
    if seeds != {f"experiment_seed_{seed}": seed for seed in SEEDS}:
        raise ComparisonContractError("source registry seeds differ")
    metrics = _list(registry.get("metrics"), "registry metrics")
    if len(metrics) != len(SEEDS) + 1 or any(
        _mapping(event, "registry metric").get("split") != "dev" for event in metrics
    ):
        raise ComparisonContractError("source registry metric split is not dev-only")
    if [_mapping(event, "registry metric").get("step") for event in metrics] != [
        *SEEDS,
        None,
    ]:
        raise ComparisonContractError("source registry metric steps differ")
    artifacts = _list(registry.get("artifacts"), "registry artifacts")
    registered_artifact_count = _integer(
        registry_contract.get("registered_artifact_count"),
        "source registered artifact count",
        minimum=1,
    )
    expected_artifact_count = len(SEEDS) * 4 + 1
    if (
        len(artifacts) != registered_artifact_count
        or registered_artifact_count != expected_artifact_count
    ):
        raise ComparisonContractError("source registry artifact count differs")
    final_reports = [
        _mapping(item, "registry artifact")
        for item in artifacts
        if _mapping(item, "registry artifact").get("name") == "final_report"
    ]
    if len(final_reports) != 1 or final_reports[0].get("sha256") != report_sha256:
        raise ComparisonContractError("registry does not pin the source report")
    final_report_path = _safe_path(
        workspace_root,
        _string(
            final_reports[0].get("path"),
            "registry final report path",
        ),
        name="registry final report",
    )
    if final_report_path != report_path:
        raise ComparisonContractError("registry final report path differs")
    registry_inputs = _mapping(registry.get("inputs"), "registry inputs")
    variant_contract = _mapping(
        _mapping(config["inputs"]["expected_variants"], "expected variants").get(
            variant
        ),
        f"{variant} expected encoder",
    )
    expected_config_path_value = _string(
        variant_contract.get("training_config_path"),
        f"{variant} expected training config path",
    )
    expected_config_sha256 = _string(
        variant_contract.get("training_config_sha256"),
        f"{variant} expected training config SHA-256",
    )
    expected_config_path = _safe_path(
        workspace_root,
        expected_config_path_value,
        name=f"{variant} expected training config",
    )
    if sha256_file(expected_config_path) != expected_config_sha256:
        raise ComparisonContractError(
            f"{variant} expected training config bytes differ"
        )
    config_inputs = _list(
        registry_inputs.get("config"),
        "registry config inputs",
    )
    if len(config_inputs) != 1:
        raise ComparisonContractError(
            "registry must contain exactly one training config input"
        )
    config_input = _mapping(config_inputs[0], "registry training config input")
    if set(config_input) != {"path", "sha256", "size_bytes"}:
        raise ComparisonContractError("registry training config metadata differs")
    registered_config_path_value = _string(
        config_input.get("path"),
        "registry training config path",
    )
    registered_config_path = _safe_path(
        workspace_root,
        registered_config_path_value,
        name="registry training config",
    )
    if (
        registered_config_path_value != expected_config_path_value
        or registered_config_path != expected_config_path
    ):
        raise ComparisonContractError("registry training config path differs")
    if (
        config_input.get("sha256") != expected_config_sha256
        or sha256_file(registered_config_path) != expected_config_sha256
    ):
        raise ComparisonContractError("registry training config SHA-256 differs")
    registered_config_size = _integer(
        config_input.get("size_bytes"),
        "registry training config size",
        minimum=1,
    )
    if registered_config_size != registered_config_path.stat().st_size:
        raise ComparisonContractError("registry training config size differs")

    model_inputs = _list(
        registry_inputs.get("model"),
        "registry model inputs",
    )
    if len(model_inputs) != 2:
        raise ComparisonContractError("registry model input count differs")
    expected_models = {
        _string(
            config["inputs"]["expected_trainer_source_path"],
            "expected trainer source path",
        ): _string(
            config["inputs"]["expected_trainer_source_sha256"],
            "expected trainer source SHA-256",
        ),
        _string(
            config["inputs"]["expected_head_source_path"],
            "expected head source path",
        ): _string(
            config["inputs"]["expected_head_source_sha256"],
            "expected head source SHA-256",
        ),
    }
    seen_model_paths: set[str] = set()
    for raw_model_input in model_inputs:
        model_input = _mapping(raw_model_input, "registry model input")
        if set(model_input) != {"path", "sha256", "size_bytes"}:
            raise ComparisonContractError("registry model metadata differs")
        model_path_value = _string(
            model_input.get("path"),
            "registry model path",
        )
        if model_path_value in seen_model_paths:
            raise ComparisonContractError("registry model path set differs")
        model_path = _safe_path(
            workspace_root,
            model_path_value,
            name="registry model source",
        )
        if model_path_value not in expected_models:
            raise ComparisonContractError("registry model path set differs")
        seen_model_paths.add(model_path_value)
        expected_model_sha256 = expected_models[model_path_value]
        if (
            model_input.get("sha256") != expected_model_sha256
            or sha256_file(model_path) != expected_model_sha256
        ):
            raise ComparisonContractError("registry model SHA-256 differs")
        model_size = _integer(
            model_input.get("size_bytes"),
            "registry model size",
            minimum=1,
        )
        if model_size != model_path.stat().st_size:
            raise ComparisonContractError("registry model size differs")
    if seen_model_paths != set(expected_models):
        raise ComparisonContractError("registry model path set differs")

    protected_paths = {report_path, registry_path}
    for role, raw_records in registry_inputs.items():
        role_name = _string(role, "registry input role")
        records = _list(raw_records, f"registry {role_name} inputs")
        for index, raw_record in enumerate(records):
            record = _mapping(
                raw_record,
                f"registry {role_name} input {index}",
            )
            protected_paths.add(
                _safe_path(
                    workspace_root,
                    _string(
                        record.get("path"),
                        f"registry {role_name} input {index} path",
                    ),
                    name=f"registry {role_name} input {index}",
                    must_exist=False,
                )
            )
    for index, raw_artifact in enumerate(artifacts):
        artifact = _mapping(raw_artifact, f"registry artifact {index}")
        protected_paths.add(
            _safe_path(
                workspace_root,
                _string(
                    artifact.get("path"),
                    f"registry artifact {index} path",
                ),
                name=f"registry artifact {index}",
                must_exist=False,
            )
        )
    return (
        registry_path,
        registry,
        sha256_file(registry_path),
        frozenset(protected_paths),
    )


def _validate_report_policy(
    report: Mapping[str, Any],
    *,
    variant: str,
    config: Mapping[str, Any],
) -> None:
    if (
        report.get("schema_version") != HEAD_REPORT_SCHEMA_VERSION
        or report.get("status") != "completed"
        or report.get("development_only") is not True
        or report.get("oracle_alignment") is not True
        or report.get("deployable_alignment_claim_allowed") is not False
    ):
        raise ComparisonContractError(f"{variant} head report policy differs")
    encoder = _mapping(report.get("encoder"), f"{variant} encoder")
    expected_id = _mapping(
        _mapping(config["inputs"]["expected_variants"], "expected variants").get(
            variant
        ),
        f"{variant} expected encoder",
    )["encoder_id"]
    if (
        encoder.get("variant") != variant
        or encoder.get("current_experiment_variant") != variant
        or encoder.get("id") != expected_id
        or encoder.get("frozen") is not True
    ):
        raise ComparisonContractError(f"{variant} encoder contract differs")
    sources = _mapping(report.get("sources"), f"{variant} sources")
    variant_contract = _mapping(
        _mapping(config["inputs"]["expected_variants"], "expected variants").get(
            variant
        ),
        f"{variant} expected encoder",
    )
    expected_sources = {
        "training_config_sha256": variant_contract["training_config_sha256"],
        "trainer_source_sha256": config["inputs"]["expected_trainer_source_sha256"],
        "head_source_sha256": config["inputs"]["expected_head_source_sha256"],
        "modeling_manifest_sha256": config["inputs"][
            "expected_modeling_manifest_sha256"
        ],
        "explicit_utterance_labels_sha256": config["inputs"][
            "expected_explicit_utterance_labels_sha256"
        ],
    }
    for key, expected in expected_sources.items():
        if sources.get(key) != expected:
            raise ComparisonContractError(f"{variant} source {key} differs")
    for key, value in sources.items():
        if key.endswith("_sha256") and not is_sha256(value):
            raise ComparisonContractError(f"{variant} source hash {key} is invalid")

    cache = _mapping(report.get("cache_contract"), f"{variant} cache contract")
    if (
        cache.get("full_cache_validated") is not True
        or cache.get("cache_report_status") != "completed"
        or cache.get("source_scan_complete") is not True
        or cache.get("smoke_cache_rejected") is not True
        or cache.get("every_shard_hash_validated") is not True
        or cache.get("every_shard_schema_validated") is not True
        or cache.get("every_shard_split_policy_validated") is not True
        or cache.get("every_shard_privacy_metadata_validated") is not True
        or cache.get("speaker_overlap_train_dev") != 0
        or cache.get("access_scope") != "restricted_internal"
        or cache.get("shareable") is not False
        or cache.get("rows") != config["inputs"]["expected_cache_rows"]
    ):
        raise ComparisonContractError(f"{variant} full-cache contract differs")

    leakage = _mapping(report.get("leakage_controls"), f"{variant} leakage")
    expected_leakage = {
        "accepted_splits": ["train", "dev"],
        "stack_accessed": False,
        "calibration_accessed": False,
        "locked_test_accessed": False,
        "dev_used_for_selection": False,
        "dev_inference_passes_per_seed": 1,
        "train_inner_validation_used": True,
        "inner_validation_speaker_disjoint": True,
        "phone_threshold_selected_on_train_inner_validation": True,
        "early_stopping_selected_on_train_inner_validation": True,
        "raw_manifest_read": False,
        "raw_path_columns_read": [],
        "environment_files_read": [],
        "environment_files_registered": [],
    }
    for key, expected in expected_leakage.items():
        if leakage.get(key) != expected:
            raise ComparisonContractError(f"{variant} leakage control {key} differs")
    summary = _mapping(report.get("summary"), f"{variant} summary")
    if (
        summary.get("seeds_completed") != list(SEEDS)
        or summary.get("results_are_development_only") is not True
        or summary.get("locked_test_remains_unread") is not True
        or summary.get("stack_remains_unread") is not True
        or summary.get("calibration_remains_unread") is not True
        or summary.get("dev_used_for_selection") is not False
        or summary.get("dev_inference_passes_per_seed") != 1
        or summary.get("oracle_alignment") is not True
        or summary.get("promotion_decision_allowed") is not False
    ):
        raise ComparisonContractError(f"{variant} summary policy differs")
    head = _mapping(report.get("head_contract"), f"{variant} head contract")
    if (
        head.get("same_hyperparameters_for_every_seed") is not True
        or head.get("task_rng_reinitialized_per_head") is not True
        or head.get("optimizer") != "AdamW"
    ):
        raise ComparisonContractError(f"{variant} head contract differs")
    runtime = _mapping(report.get("runtime"), f"{variant} runtime")
    device = _mapping(runtime.get("device"), f"{variant} device")
    determinism = _mapping(runtime.get("determinism"), f"{variant} determinism")
    if (
        device.get("type") != "cuda"
        or device.get("production_evidence_allowed") is not True
        or determinism.get("deterministic_algorithms") is not True
        or determinism.get("cublas_workspace_config") != ":4096:8"
        or determinism.get("configured_before_cuda_initialization") is not True
        or determinism.get("fresh_process_contract_enforced") is not True
    ):
        raise ComparisonContractError(f"{variant} production runtime differs")


def _validate_cross_report_contract(
    base_report: Mapping[str, Any],
    large_report: Mapping[str, Any],
) -> None:
    equal_paths = (
        ("cache_contract", "rows"),
        ("cache_contract", "split_rows"),
        ("utterance_label_contract", "sha256"),
        ("utterance_label_contract", "row_count"),
        ("utterance_label_contract", "split_rows"),
        ("utterance_label_contract", "missing_labels"),
        ("data", "inner_validation"),
        ("data", "phone"),
        ("data", "utterance"),
        ("leakage_controls", "primary_analysis_scope"),
        ("head_contract", "phone"),
        ("head_contract", "utterance"),
        ("head_contract", "optimization"),
    )
    for parent, child in equal_paths:
        base_value = _mapping(base_report.get(parent), f"base {parent}").get(child)
        large_value = _mapping(large_report.get(parent), f"large {parent}").get(child)
        if base_value != large_value:
            raise ComparisonContractError(
                f"Base+/Large paired contract differs at {parent}.{child}"
            )


def _artifact_contract(
    run: Mapping[str, Any],
    *,
    kind: str,
) -> Mapping[str, Any]:
    if kind == "phone":
        return _mapping(
            _mapping(run.get("phone"), "run phone").get("predictions"),
            "phone prediction artifact",
        )
    return _mapping(
        _mapping(run.get("utterance"), "run utterance").get("predictions"),
        "utterance prediction artifact",
    )


def load_source_run(
    *,
    workspace_root: Path,
    report_path: Path,
    variant: str,
    config: Mapping[str, Any],
    canonical_phone: PhoneIdentity | None,
    canonical_utterance: UtteranceIdentity | None,
) -> tuple[SourceRun, PhoneIdentity, UtteranceIdentity]:
    report = _load_json(report_path, f"{variant} head report")
    report_sha256 = sha256_file(report_path)
    _validate_report_policy(report, variant=variant, config=config)
    registry_path, registry, registry_sha256, protected_paths = _validate_registry(
        workspace_root=workspace_root,
        report_path=report_path,
        report=report,
        report_sha256=report_sha256,
        variant=variant,
        config=config,
    )
    runs = _list(report.get("runs"), f"{variant} runs")
    if len(runs) != len(SEEDS):
        raise ComparisonContractError(f"{variant} must contain three seed runs")
    by_seed: dict[int, Mapping[str, Any]] = {}
    for raw_run in runs:
        run = _mapping(raw_run, f"{variant} run")
        seed = run.get("seed")
        if (
            isinstance(seed, bool)
            or not isinstance(seed, int)
            or seed not in SEEDS
            or seed in by_seed
        ):
            raise ComparisonContractError(f"{variant} run seeds differ")
        selection = _mapping(run.get("selection"), f"{variant} selection")
        if selection.get("dev_values_used_for_selection") is not False:
            raise ComparisonContractError(f"{variant} used dev for selection")
        by_seed[int(seed)] = run

    phone_predictions: dict[int, PhonePrediction] = {}
    utterance_predictions: dict[int, UtterancePrediction] = {}
    prediction_sources: dict[str, Mapping[str, Any]] = {}
    all_protected_paths = set(protected_paths)
    current_phone = canonical_phone
    current_utterance = canonical_utterance
    for seed in SEEDS:
        run = by_seed[seed]
        phone_report = _mapping(run.get("phone"), f"{variant} phone run")
        utterance_report = _mapping(run.get("utterance"), f"{variant} utterance run")
        if (
            phone_report.get("metric_scope") != "primary_dev_single_final_pass"
            or utterance_report.get("metric_scope") != "primary_dev_single_final_pass"
        ):
            raise ComparisonContractError(f"{variant} metric scope differs")
        phone_artifact = _artifact_contract(run, kind="phone")
        utterance_artifact = _artifact_contract(run, kind="utterance")
        for artifact, name in (
            (phone_artifact, "phone"),
            (utterance_artifact, "utterance"),
        ):
            if not is_sha256(artifact.get("sha256")):
                raise ComparisonContractError(
                    f"{variant} seed {seed} {name} hash is invalid"
                )
        phone_path = _safe_relative_artifact(
            workspace_root,
            report_path,
            phone_artifact.get("path"),
            name=f"{variant} seed {seed} phone prediction",
        )
        utterance_path = _safe_relative_artifact(
            workspace_root,
            report_path,
            utterance_artifact.get("path"),
            name=f"{variant} seed {seed} utterance prediction",
        )
        all_protected_paths.update((phone_path, utterance_path))
        phone_metrics = _mapping(
            phone_report.get("metrics"),
            f"{variant} phone metrics",
        )
        threshold = _finite_number(
            phone_metrics.get("decision_threshold"),
            f"{variant} phone threshold",
        )
        phone_rows = _integer(
            phone_report.get("rows"),
            f"{variant} seed {seed} phone rows",
            minimum=1,
        )
        utterance_rows = _integer(
            utterance_report.get("rows"),
            f"{variant} seed {seed} utterance rows",
            minimum=1,
        )
        identity, phone_prediction = load_phone_prediction(
            phone_path,
            expected_sha256=cast(str, phone_artifact["sha256"]),
            expected_rows=phone_rows,
            seed=seed,
            expected_threshold=threshold,
        )
        if current_phone is None:
            current_phone = identity
        else:
            _validate_phone_identity(current_phone, identity)
        utterance_identity, utterance_prediction = load_utterance_prediction(
            utterance_path,
            expected_sha256=cast(str, utterance_artifact["sha256"]),
            expected_rows=utterance_rows,
            seed=seed,
        )
        if current_utterance is None:
            current_utterance = utterance_identity
        else:
            _validate_utterance_identity(current_utterance, utterance_identity)
        phone_predictions[seed] = phone_prediction
        utterance_predictions[seed] = utterance_prediction
        prediction_sources[f"seed_{seed}"] = {
            "phone": {
                "path": phone_path.relative_to(workspace_root).as_posix(),
                "sha256": phone_artifact["sha256"],
                "size_bytes": phone_path.stat().st_size,
                "rows": phone_rows,
            },
            "utterance": {
                "path": utterance_path.relative_to(workspace_root).as_posix(),
                "sha256": utterance_artifact["sha256"],
                "size_bytes": utterance_path.stat().st_size,
                "rows": utterance_rows,
            },
        }
    if current_phone is None or current_utterance is None:
        raise ComparisonContractError(f"{variant} predictions are empty")
    utterance_speaker_by_id = dict(
        zip(
            current_utterance.utterance_ids.tolist(),
            current_utterance.speaker_ids.tolist(),
            strict=True,
        )
    )
    for uid, speaker in zip(
        current_phone.utterance_ids,
        current_phone.speaker_ids,
        strict=True,
    ):
        if utterance_speaker_by_id.get(str(uid)) != str(speaker):
            raise ComparisonContractError(
                f"{variant} phone/utterance identity join differs"
            )
    return (
        SourceRun(
            variant=variant,
            report_path=report_path,
            report=report,
            report_sha256=report_sha256,
            registry_path=registry_path,
            registry=registry,
            registry_sha256=registry_sha256,
            phone=phone_predictions,
            utterance=utterance_predictions,
            prediction_sources=prediction_sources,
            protected_paths=frozenset(all_protected_paths),
        ),
        current_phone,
        current_utterance,
    )


def _binary_runs(indices: np.ndarray, labels: np.ndarray) -> list[tuple[int, int]]:
    positive = indices[labels == 1]
    if not len(positive):
        return []
    runs: list[tuple[int, int]] = []
    start = int(positive[0])
    end = start
    for raw_index in positive[1:]:
        index = int(raw_index)
        if index == end + 1:
            end = index
            continue
        runs.append((start, end))
        start = index
        end = index
    runs.append((start, end))
    return runs


def _overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def _maximum_overlap_matches(
    gold: Sequence[tuple[int, int]],
    predicted: Sequence[tuple[int, int]],
) -> int:
    """Return deterministic maximum one-to-one overlap matching."""

    assigned_prediction = [-1] * len(predicted)

    def assign(gold_index: int, visited: set[int]) -> bool:
        for prediction_index, prediction in enumerate(predicted):
            if prediction_index in visited or not _overlap(
                gold[gold_index],
                prediction,
            ):
                continue
            visited.add(prediction_index)
            previous = assigned_prediction[prediction_index]
            if previous < 0 or assign(previous, visited):
                assigned_prediction[prediction_index] = gold_index
                return True
        return False

    matches = 0
    for gold_index in range(len(gold)):
        if assign(gold_index, set()):
            matches += 1
    return matches


def phone_event_counts(
    identity: PhoneIdentity,
    predictions: np.ndarray,
) -> dict[str, int]:
    if predictions.shape != identity.targets.shape:
        raise ComparisonContractError("event prediction shape differs")
    true_positive = 0
    false_positive = 0
    false_negative = 0
    start = 0
    while start < len(identity.targets):
        utterance = identity.utterance_ids[start]
        stop = start + 1
        while (
            stop < len(identity.targets) and identity.utterance_ids[stop] == utterance
        ):
            stop += 1
        indices = identity.phone_indices[start:stop]
        gold = _binary_runs(indices, identity.targets[start:stop])
        predicted = _binary_runs(indices, predictions[start:stop])
        matches = _maximum_overlap_matches(gold, predicted)
        true_positive += matches
        false_positive += len(predicted) - matches
        false_negative += len(gold) - matches
        start = stop
    return {
        "tp": true_positive,
        "fp": false_positive,
        "fn": false_negative,
    }


def event_f1_from_counts(counts: Mapping[str, int]) -> float:
    denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
    if denominator == 0:
        raise ComparisonContractError("event F1 is undefined with zero events")
    return float(2 * counts["tp"] / denominator)


def phone_metric_values(
    identity: PhoneIdentity,
    prediction: PhonePrediction,
) -> dict[str, Any]:
    event_counts = phone_event_counts(identity, prediction.predictions)
    return {
        "macro_f1": macro_f1(
            identity.targets,
            prediction.predictions,
            labels=[0, 1],
        ),
        "auprc": auprc(
            identity.targets,
            prediction.probabilities,
            labels=[0, 1],
            undefined="nan",
        ),
        "event_f1": event_f1_from_counts(event_counts),
        "event_counts": event_counts,
    }


def utterance_metric_values(
    identity: UtteranceIdentity,
    prediction: UtterancePrediction,
) -> dict[str, Mapping[str, float]]:
    output: dict[str, Mapping[str, float]] = {}
    for index, outcome in enumerate(OUTCOMES):
        selected = identity.observed[:, index]
        targets = identity.targets[selected, index]
        predicted = prediction.predictions[selected, index]
        output[outcome] = {
            "qwk": quadratic_weighted_kappa(
                np.rint(targets).astype(np.int64),
                np.rint(np.clip(predicted, 1, 5)).astype(np.int64),
                labels=[1, 2, 3, 4, 5],
            ),
            "spearman": spearman_correlation(targets, predicted),
            "mae": mae(targets, predicted),
            "icc_2_1": icc_2_1(np.column_stack((targets, predicted))),
        }
    return output


def _assert_close(
    observed: object,
    expected: float,
    *,
    name: str,
    tolerance: float = 1e-5,
) -> None:
    observed_number = _finite_number(observed, name)
    if not math.isclose(
        observed_number,
        expected,
        rel_tol=tolerance,
        abs_tol=tolerance,
    ):
        raise ComparisonContractError(f"{name} differs from prediction artifact")


def validate_report_metrics(
    source: SourceRun,
    *,
    per_seed_phone: Mapping[int, Mapping[str, Any]],
    per_seed_utterance: Mapping[int, Mapping[str, Mapping[str, float]]],
) -> None:
    runs = {
        int(_mapping(run, "source run")["seed"]): _mapping(run, "source run")
        for run in _list(source.report.get("runs"), "source runs")
    }
    for seed in SEEDS:
        phone = _mapping(
            _mapping(runs[seed].get("phone"), "source phone").get("metrics"),
            "source phone metrics",
        )
        for metric in ("macro_f1", "auprc"):
            _assert_close(
                phone.get(metric),
                float(per_seed_phone[seed][metric]),
                name=f"{source.variant} seed {seed} phone {metric}",
            )
        outcomes = _mapping(
            _mapping(runs[seed].get("utterance"), "source utterance").get("outcomes"),
            "source utterance outcomes",
        )
        for outcome in OUTCOMES:
            stored = _mapping(
                _mapping(outcomes.get(outcome), f"stored {outcome}").get("metrics"),
                f"stored {outcome} metrics",
            )
            for metric in UTTERANCE_METRICS:
                _assert_close(
                    stored.get(metric),
                    float(per_seed_utterance[seed][outcome][metric]),
                    name=(f"{source.variant} seed {seed} {outcome} {metric}"),
                )


def _aggregate(values: Sequence[float]) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    finite = array[np.isfinite(array)]
    return {
        "mean": float(finite.mean()) if finite.size else float("nan"),
        "sample_std": (float(finite.std(ddof=1)) if finite.size > 1 else float("nan")),
        "valid_seeds": int(finite.size),
    }


def _bootstrap_p_value(distribution: np.ndarray) -> float:
    if distribution.size == 0:
        raise ComparisonContractError("bootstrap distribution is empty")
    less_equal = int(np.count_nonzero(distribution <= 0))
    greater_equal = int(np.count_nonzero(distribution >= 0))
    tail = min(
        (less_equal + 1) / (distribution.size + 1),
        (greater_equal + 1) / (distribution.size + 1),
    )
    return float(min(1.0, 2.0 * tail))


def _bootstrap_result(
    distribution: Sequence[float],
    *,
    estimate: float,
    confidence_level: float,
    n_resamples: int,
    n_speakers: int,
    seed: int,
) -> BootstrapResult:
    values = np.asarray(distribution, dtype=np.float64)
    values = values[np.isfinite(values)]
    if not values.size or not math.isfinite(estimate):
        raise ComparisonContractError("paired bootstrap produced no finite values")
    tail = (1.0 - confidence_level) / 2
    lower, upper = np.quantile(values, [tail, 1.0 - tail])
    return BootstrapResult(
        estimate=float(estimate),
        confidence_lower=float(lower),
        confidence_upper=float(upper),
        confidence_level=float(confidence_level),
        n_resamples=int(n_resamples),
        n_valid_resamples=int(values.size),
        n_speakers=int(n_speakers),
        seed=int(seed),
        p_value_two_sided_zero=_bootstrap_p_value(values),
    )


def _macro_f1_from_confusion(values: np.ndarray) -> float:
    true_negative, false_positive, false_negative, true_positive = (
        float(item) for item in values
    )
    shared = false_positive + false_negative
    negative_denominator = 2 * true_negative + shared
    positive_denominator = 2 * true_positive + shared
    negative_f1 = (
        2 * true_negative / negative_denominator if negative_denominator else 0.0
    )
    positive_f1 = (
        2 * true_positive / positive_denominator if positive_denominator else 0.0
    )
    return float((negative_f1 + positive_f1) / 2)


def paired_phone_macro_f1_bootstrap(
    *,
    speaker_ids: np.ndarray,
    targets: np.ndarray,
    base_predictions: Mapping[int, np.ndarray],
    large_predictions: Mapping[int, np.ndarray],
    n_resamples: int,
    confidence_level: float,
    seed: int,
) -> BootstrapResult:
    unique_speakers, inverse = np.unique(speaker_ids, return_inverse=True)
    if len(unique_speakers) < 2:
        raise ComparisonContractError("phone bootstrap needs two speakers")
    counts = np.zeros(
        (len(unique_speakers), len(SEEDS), len(VARIANTS), 4),
        dtype=np.int64,
    )
    for seed_index, seed_value in enumerate(SEEDS):
        for variant_index, predictions in enumerate(
            (base_predictions[seed_value], large_predictions[seed_value])
        ):
            categories = targets * 2 + predictions
            np.add.at(
                counts[:, seed_index, variant_index, :],
                (inverse, categories),
                1,
            )

    def effect(weights: np.ndarray) -> float:
        total = np.tensordot(weights, counts, axes=(0, 0))
        deltas = [
            _macro_f1_from_confusion(total[index, 1])
            - _macro_f1_from_confusion(total[index, 0])
            for index in range(len(SEEDS))
        ]
        return float(np.mean(deltas))

    estimate = effect(np.ones(len(unique_speakers), dtype=np.int64))
    generator = np.random.default_rng(seed)
    distribution: list[float] = []
    for _ in range(n_resamples):
        sampled = generator.integers(
            0,
            len(unique_speakers),
            size=len(unique_speakers),
        )
        weights = np.bincount(sampled, minlength=len(unique_speakers))
        distribution.append(effect(weights))
    return _bootstrap_result(
        distribution,
        estimate=estimate,
        confidence_level=confidence_level,
        n_resamples=n_resamples,
        n_speakers=len(unique_speakers),
        seed=seed,
    )


def _icc_sufficient_by_speaker(
    *,
    inverse_speakers: np.ndarray,
    n_speakers: int,
    targets: np.ndarray,
    predictions: np.ndarray,
) -> np.ndarray:
    means = (targets + predictions) / 2
    values = np.column_stack(
        (
            np.ones(len(targets), dtype=np.float64),
            targets + predictions,
            targets**2 + predictions**2,
            means,
            means**2,
            targets,
            predictions,
        )
    )
    output = np.zeros((n_speakers, values.shape[1]), dtype=np.float64)
    for column in range(values.shape[1]):
        np.add.at(output[:, column], inverse_speakers, values[:, column])
    return output


def _icc_from_sufficient(values: np.ndarray) -> float:
    (
        n_targets,
        sum_total,
        sum_sq_total,
        sum_means,
        sum_mean_squares,
        sum_targets,
        sum_predictions,
    ) = (float(value) for value in values)
    if n_targets < 2:
        return float("nan")
    n_raters = 2.0
    grand_mean = sum_total / (n_raters * n_targets)
    ss_total = (
        sum_sq_total - 2 * grand_mean * sum_total + n_raters * n_targets * grand_mean**2
    )
    ss_targets = n_raters * (
        sum_mean_squares - 2 * grand_mean * sum_means + n_targets * grand_mean**2
    )
    target_mean = sum_targets / n_targets
    prediction_mean = sum_predictions / n_targets
    ss_raters = n_targets * (
        (target_mean - grand_mean) ** 2 + (prediction_mean - grand_mean) ** 2
    )
    ss_error = ss_total - ss_targets - ss_raters
    scale = max(abs(ss_total), abs(ss_targets), abs(ss_raters), 1.0)
    if ss_error < 0 and abs(ss_error) <= np.finfo(float).eps * scale * 32:
        ss_error = 0.0
    ms_targets = ss_targets / (n_targets - 1)
    ms_raters = ss_raters
    ms_error = ss_error / (n_targets - 1)
    denominator = ms_targets + ms_error + n_raters * (ms_raters - ms_error) / n_targets
    if np.isclose(
        denominator,
        0.0,
        rtol=0.0,
        atol=np.finfo(float).eps
        * max(abs(ms_targets), abs(ms_raters), abs(ms_error), 1.0),
    ):
        return float("nan")
    return float((ms_targets - ms_error) / denominator)


def paired_utterance_icc_bootstrap(
    *,
    speaker_ids: np.ndarray,
    targets: np.ndarray,
    base_predictions: Mapping[int, np.ndarray],
    large_predictions: Mapping[int, np.ndarray],
    n_resamples: int,
    confidence_level: float,
    seed: int,
) -> BootstrapResult:
    unique_speakers, inverse = np.unique(speaker_ids, return_inverse=True)
    if len(unique_speakers) < 2:
        raise ComparisonContractError("utterance bootstrap needs two speakers")
    sufficient = np.zeros(
        (len(unique_speakers), len(SEEDS), len(VARIANTS), 7),
        dtype=np.float64,
    )
    for seed_index, seed_value in enumerate(SEEDS):
        for variant_index, predictions in enumerate(
            (base_predictions[seed_value], large_predictions[seed_value])
        ):
            sufficient[:, seed_index, variant_index, :] = _icc_sufficient_by_speaker(
                inverse_speakers=inverse,
                n_speakers=len(unique_speakers),
                targets=targets,
                predictions=predictions,
            )

    def effect(weights: np.ndarray) -> float:
        total = np.tensordot(weights, sufficient, axes=(0, 0))
        deltas = [
            _icc_from_sufficient(total[index, 1])
            - _icc_from_sufficient(total[index, 0])
            for index in range(len(SEEDS))
        ]
        return float(np.mean(deltas))

    estimate = effect(np.ones(len(unique_speakers), dtype=np.int64))
    generator = np.random.default_rng(seed)
    distribution: list[float] = []
    for _ in range(n_resamples):
        sampled = generator.integers(
            0,
            len(unique_speakers),
            size=len(unique_speakers),
        )
        weights = np.bincount(sampled, minlength=len(unique_speakers))
        value = effect(weights)
        if math.isfinite(value):
            distribution.append(value)
    return _bootstrap_result(
        distribution,
        estimate=estimate,
        confidence_level=confidence_level,
        n_resamples=n_resamples,
        n_speakers=len(unique_speakers),
        seed=seed,
    )


def _bootstrap_mapping(result: BootstrapResult) -> dict[str, Any]:
    return {
        "estimate": result.estimate,
        "confidence_lower": result.confidence_lower,
        "confidence_upper": result.confidence_upper,
        "confidence_level": result.confidence_level,
        "n_resamples": result.n_resamples,
        "n_valid_resamples": result.n_valid_resamples,
        "n_speakers": result.n_speakers,
        "seed": result.seed,
        "p_value_two_sided_zero": result.p_value_two_sided_zero,
    }


def large_adoption_gate(
    estimate: float,
    *,
    threshold: float = 0.03,
) -> tuple[bool, str]:
    """Apply the predeclared inclusive absolute-improvement gate."""

    if not math.isfinite(estimate) or not math.isfinite(threshold):
        raise ComparisonContractError("gate values must be finite")
    passed = bool(estimate >= threshold)
    return passed, "passed" if passed else "failed"


def _per_seed_metrics(
    *,
    base: SourceRun,
    large: SourceRun,
    phone_identity: PhoneIdentity,
    utterance_identity: UtteranceIdentity,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[int, Mapping[str, Any]]],
    dict[str, dict[int, Mapping[str, Mapping[str, float]]]],
]:
    phone_by_variant: dict[str, dict[int, Mapping[str, Any]]] = {
        variant: {} for variant in VARIANTS
    }
    utterance_by_variant: dict[
        str,
        dict[int, Mapping[str, Mapping[str, float]]],
    ] = {variant: {} for variant in VARIANTS}
    sources = {"base_plus": base, "large": large}
    for variant, source in sources.items():
        for seed in SEEDS:
            phone_by_variant[variant][seed] = phone_metric_values(
                phone_identity,
                source.phone[seed],
            )
            utterance_by_variant[variant][seed] = utterance_metric_values(
                utterance_identity,
                source.utterance[seed],
            )
        validate_report_metrics(
            source,
            per_seed_phone=phone_by_variant[variant],
            per_seed_utterance=utterance_by_variant[variant],
        )

    per_seed: dict[str, Any] = {}
    for seed in SEEDS:
        phone: dict[str, Any] = {
            variant: dict(phone_by_variant[variant][seed]) for variant in VARIANTS
        }
        phone["large_minus_base"] = {
            metric: float(
                phone_by_variant["large"][seed][metric]
                - phone_by_variant["base_plus"][seed][metric]
            )
            for metric in PHONE_METRICS
        }
        utterance: dict[str, Any] = {}
        for outcome in OUTCOMES:
            utterance[outcome] = {
                variant: dict(utterance_by_variant[variant][seed][outcome])
                for variant in VARIANTS
            }
            utterance[outcome]["large_minus_base"] = {
                metric: float(
                    utterance_by_variant["large"][seed][outcome][metric]
                    - utterance_by_variant["base_plus"][seed][outcome][metric]
                )
                for metric in UTTERANCE_METRICS
            }
        per_seed[f"seed_{seed}"] = {
            "phone": phone,
            "utterance": utterance,
        }

    aggregate_phone: dict[str, Any] = {}
    for metric in PHONE_METRICS:
        aggregate_phone[metric] = {}
        for variant in (*VARIANTS, "large_minus_base"):
            aggregate_phone[metric][variant] = _aggregate(
                [
                    float(per_seed[f"seed_{seed}"]["phone"][variant][metric])
                    for seed in SEEDS
                ]
            )
    aggregate_utterance: dict[str, Any] = {}
    for outcome in OUTCOMES:
        aggregate_utterance[outcome] = {}
        for metric in UTTERANCE_METRICS:
            aggregate_utterance[outcome][metric] = {}
            for variant in (*VARIANTS, "large_minus_base"):
                aggregate_utterance[outcome][metric][variant] = _aggregate(
                    [
                        float(
                            per_seed[f"seed_{seed}"]["utterance"][outcome][variant][
                                metric
                            ]
                        )
                        for seed in SEEDS
                    ]
                )
    aggregate = {
        "phone": aggregate_phone,
        "utterance": aggregate_utterance,
    }
    return (
        per_seed,
        aggregate,
        phone_by_variant,
        utterance_by_variant,
    )


def _comparison_sources(
    *,
    workspace_root: Path,
    config_path: Path,
    script_path: Path,
    module_path: Path,
    base: SourceRun,
    large: SourceRun,
) -> dict[str, Any]:
    def implementation(path: Path) -> dict[str, Any]:
        return {
            "path": path.relative_to(workspace_root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }

    output: dict[str, Any] = {
        "config": implementation(config_path),
        "comparison_script": implementation(script_path),
        "comparison_module": implementation(module_path),
    }
    for variant, source in (("base_plus", base), ("large", large)):
        output[variant] = {
            "report": {
                "path": source.report_path.relative_to(workspace_root).as_posix(),
                "sha256": source.report_sha256,
                "size_bytes": source.report_path.stat().st_size,
            },
            "registry": {
                "path": source.registry_path.relative_to(workspace_root).as_posix(),
                "sha256": source.registry_sha256,
                "size_bytes": source.registry_path.stat().st_size,
                "run_id": _string(
                    source.registry.get("run_id"),
                    f"{variant} registry run_id",
                ),
                "status": "completed",
            },
            "predictions": source.prediction_sources,
        }
    return output


def _assert_sources_unchanged(
    *,
    workspace_root: Path,
    sources: Mapping[str, Any],
) -> None:
    implementation_names = ("config", "comparison_script", "comparison_module")
    for name in implementation_names:
        record = _mapping(sources.get(name), f"{name} source")
        path = _safe_path(
            workspace_root,
            _string(record.get("path"), f"{name} source path"),
            name=f"{name} source",
        )
        if sha256_file(path) != record.get("sha256"):
            raise ComparisonContractError(f"{name} source changed during comparison")
        if path.stat().st_size != record.get("size_bytes"):
            raise ComparisonContractError(
                f"{name} source size changed during comparison"
            )
    for variant in VARIANTS:
        source = _mapping(sources.get(variant), f"{variant} sources")
        for kind in ("report", "registry"):
            record = _mapping(
                source.get(kind),
                f"{variant} {kind} source",
            )
            path = _safe_path(
                workspace_root,
                _string(
                    record.get("path"),
                    f"{variant} {kind} source path",
                ),
                name=f"{variant} {kind} source",
            )
            if sha256_file(path) != record.get("sha256"):
                raise ComparisonContractError(
                    f"{variant} {kind} changed during comparison"
                )
            if path.stat().st_size != record.get("size_bytes"):
                raise ComparisonContractError(
                    f"{variant} {kind} size changed during comparison"
                )
        predictions = _mapping(
            source.get("predictions"),
            f"{variant} prediction sources",
        )
        for seed in SEEDS:
            per_seed = _mapping(
                predictions.get(f"seed_{seed}"),
                f"{variant} seed {seed} prediction sources",
            )
            for task in ("phone", "utterance"):
                record = _mapping(
                    per_seed.get(task),
                    f"{variant} seed {seed} {task} prediction source",
                )
                path = _safe_path(
                    workspace_root,
                    _string(
                        record.get("path"),
                        f"{variant} seed {seed} {task} path",
                    ),
                    name=f"{variant} seed {seed} {task} prediction",
                )
                if sha256_file(path) != record.get("sha256"):
                    raise ComparisonContractError(
                        f"{variant} seed {seed} {task} changed during comparison"
                    )
                if path.stat().st_size != record.get("size_bytes"):
                    raise ComparisonContractError(
                        f"{variant} seed {seed} {task} size changed during comparison"
                    )


def _registry_completion(source: SourceRun) -> dict[str, Any]:
    report_registry = _mapping(
        source.report.get("experiment_registry"),
        f"{source.variant} experiment registry",
    )
    return {
        "status": "completed",
        "run_id": _string(
            source.registry.get("run_id"),
            f"{source.variant} registry run_id",
        ),
        "registered_artifact_count": _integer(
            report_registry.get("registered_artifact_count"),
            f"{source.variant} registered artifact count",
            minimum=1,
        ),
    }


def compare_wavlm_frozen_heads(
    *,
    config_path: str | Path,
    workspace_root: str | Path,
    script_path: str | Path,
    commit_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build and transactionally commit a strict aggregate-only S103 report."""

    started_at = utc_now()
    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise ComparisonContractError("workspace_root must be a directory")
    resolved_config = _safe_path(
        root,
        config_path,
        name="comparison config",
    )
    resolved_script = _safe_path(
        root,
        script_path,
        name="comparison script",
    )
    module_path = _safe_path(
        root,
        Path(__file__).resolve(),
        name="comparison module",
    )
    config = load_and_validate_config(resolved_config)
    inputs = _mapping(config.get("inputs"), "comparison inputs")
    base_report_path = _safe_path(
        root,
        _string(inputs.get("base_plus_report"), "base_plus_report"),
        name="base_plus report",
    )
    large_report_path = _safe_path(
        root,
        _string(inputs.get("large_report"), "large_report"),
        name="large report",
    )
    output_config = _mapping(config.get("output"), "comparison output")
    configured_commit = (
        commit_path
        if commit_path is not None
        else _string(
            output_config.get("commit_manifest"),
            "comparison commit manifest",
        )
    )
    resolved_commit = _safe_path(
        root,
        configured_commit,
        name="comparison commit manifest",
        must_exist=False,
    )
    generation_directory = _safe_path(
        root,
        _string(
            output_config.get("generation_directory"),
            "comparison generation directory",
        ),
        name="comparison generation directory",
        must_exist=False,
    )
    source_paths = {
        resolved_config,
        resolved_script,
        module_path,
        base_report_path,
        large_report_path,
    }
    if resolved_commit in source_paths or generation_directory in source_paths:
        raise ComparisonContractError("comparison output would overwrite an input")
    if resolved_commit.exists() and not resolved_commit.is_file():
        raise ComparisonContractError(
            "comparison commit exists and is not a regular file"
        )
    if generation_directory.exists() and not generation_directory.is_dir():
        raise ComparisonContractError(
            "comparison generation path exists and is not a directory"
        )

    base, phone_identity, utterance_identity = load_source_run(
        workspace_root=root,
        report_path=base_report_path,
        variant="base_plus",
        config=config,
        canonical_phone=None,
        canonical_utterance=None,
    )
    large, paired_phone_identity, paired_utterance_identity = load_source_run(
        workspace_root=root,
        report_path=large_report_path,
        variant="large",
        config=config,
        canonical_phone=phone_identity,
        canonical_utterance=utterance_identity,
    )
    _validate_phone_identity(phone_identity, paired_phone_identity)
    _validate_utterance_identity(
        utterance_identity,
        paired_utterance_identity,
    )
    _validate_cross_report_contract(base.report, large.report)

    protected_paths = {
        *source_paths,
        *base.protected_paths,
        *large.protected_paths,
    }
    if resolved_commit in protected_paths or generation_directory in protected_paths:
        raise ComparisonContractError("comparison output would overwrite an input")

    per_seed, aggregate, _, _ = _per_seed_metrics(
        base=base,
        large=large,
        phone_identity=phone_identity,
        utterance_identity=utterance_identity,
    )
    bootstrap_config = _mapping(config.get("bootstrap"), "bootstrap")
    n_resamples = _integer(
        bootstrap_config.get("n_resamples"),
        "bootstrap n_resamples",
        minimum=1,
    )
    confidence_level = _finite_number(
        bootstrap_config.get("confidence_level"),
        "bootstrap confidence_level",
    )
    bootstrap_seed = _integer(
        bootstrap_config.get("seed"),
        "bootstrap seed",
        minimum=0,
    )
    primary_bootstrap = paired_phone_macro_f1_bootstrap(
        speaker_ids=phone_identity.speaker_ids,
        targets=phone_identity.targets,
        base_predictions={seed: base.phone[seed].predictions for seed in SEEDS},
        large_predictions={seed: large.phone[seed].predictions for seed in SEEDS},
        n_resamples=n_resamples,
        confidence_level=confidence_level,
        seed=bootstrap_seed,
    )
    aggregate_primary = float(
        aggregate["phone"]["macro_f1"]["large_minus_base"]["mean"]
    )
    if not math.isclose(
        primary_bootstrap.estimate,
        aggregate_primary,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ComparisonContractError(
            "primary bootstrap estimate differs from seed aggregate"
        )
    gate_config = _mapping(config.get("gate"), "gate")
    threshold = _finite_number(
        gate_config.get("absolute_improvement"),
        "gate threshold",
    )
    gate_passed, gate_status = large_adoption_gate(
        primary_bootstrap.estimate,
        threshold=threshold,
    )

    multiplicity_config = _mapping(
        config.get("multiplicity"),
        "multiplicity",
    )
    alpha = _finite_number(
        multiplicity_config.get("alpha"),
        "multiplicity alpha",
    )
    outcome_bootstraps: dict[str, BootstrapResult] = {}
    for outcome_index, outcome in enumerate(OUTCOMES):
        selected = utterance_identity.observed[:, outcome_index]
        outcome_bootstraps[outcome] = paired_utterance_icc_bootstrap(
            speaker_ids=utterance_identity.speaker_ids[selected],
            targets=utterance_identity.targets[selected, outcome_index],
            base_predictions={
                seed: base.utterance[seed].predictions[
                    selected,
                    outcome_index,
                ]
                for seed in SEEDS
            },
            large_predictions={
                seed: large.utterance[seed].predictions[
                    selected,
                    outcome_index,
                ]
                for seed in SEEDS
            },
            n_resamples=n_resamples,
            confidence_level=confidence_level,
            seed=bootstrap_seed,
        )
        aggregate_effect = float(
            aggregate["utterance"][outcome]["icc_2_1"]["large_minus_base"]["mean"]
        )
        if not math.isclose(
            outcome_bootstraps[outcome].estimate,
            aggregate_effect,
            rel_tol=1e-10,
            abs_tol=1e-10,
        ):
            raise ComparisonContractError(
                f"{outcome} bootstrap estimate differs from seed aggregate"
            )
    raw_p_values = [
        outcome_bootstraps[outcome].p_value_two_sided_zero for outcome in OUTCOMES
    ]
    corrected = holm_correction(raw_p_values, alpha=alpha)
    comparisons = {
        outcome: {
            "effect": outcome_bootstraps[outcome].estimate,
            "raw_p_value": raw_p_values[index],
            "adjusted_p_value": float(corrected.adjusted_p_values[index]),
            "rejected": bool(corrected.rejected[index]),
            "paired_speaker_bootstrap": _bootstrap_mapping(outcome_bootstraps[outcome]),
        }
        for index, outcome in enumerate(OUTCOMES)
    }

    sources = _comparison_sources(
        workspace_root=root,
        config_path=resolved_config,
        script_path=resolved_script,
        module_path=module_path,
        base=base,
        large=large,
    )
    _assert_sources_unchanged(workspace_root=root, sources=sources)
    experiment = _mapping(config.get("experiment"), "experiment")
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "completed",
        "experiment_id": _string(experiment.get("id"), "experiment id"),
        "started_at_utc": started_at,
        "completed_at_utc": utc_now(),
        "development_only": True,
        "oracle_alignment": True,
        "comparison_scope": "primary_dev_paired",
        "sources": sources,
        "contract": {
            "seeds": list(SEEDS),
            "registry_completion": {
                "base_plus": _registry_completion(base),
                "large": _registry_completion(large),
            },
            "paired_identity": {
                "phone_ids_targets_speakers_equal": True,
                "utterance_ids_targets_speakers_equal": True,
                "cross_seed_equal": True,
            },
            "leakage": {
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
                "raw_data_read": False,
                "environment_files_read": [],
            },
        },
        "metrics": {
            "per_seed": per_seed,
            "aggregate": aggregate,
        },
        "primary_comparison": {
            "metric": "phone_macro_f1",
            "effect": "large_minus_base",
            "paired_speaker_bootstrap": _bootstrap_mapping(primary_bootstrap),
            "gate": {
                "threshold_absolute": threshold,
                "rule": "point_estimate_gte_threshold",
                "point_estimate_passed": gate_passed,
                "confidence_lower_gte_threshold": (
                    primary_bootstrap.confidence_lower >= threshold
                ),
                "status": gate_status,
            },
        },
        "multiplicity": {
            "family": "utterance_icc_2_1_across_outcomes",
            "method": "holm",
            "alpha": alpha,
            "comparisons": comparisons,
        },
        "summary": {
            "large_adoption_gate_passed": gate_passed,
            "large_adoption_gate_status": gate_status,
            "results_are_development_only": True,
            "locked_test_remains_unread": True,
            "stack_remains_unread": True,
            "calibration_remains_unread": True,
        },
        "privacy": {
            "aggregate_only_output": True,
            "direct_identifiers_written": False,
            "pseudonymous_row_ids_written": False,
            "prediction_rows_written": False,
            "raw_data_read": False,
            "source_feature_caches_read": False,
            "environment_files_read": [],
            "input_access_scope": "restricted_internal",
            "shareable": False,
        },
        "reproducibility": {
            "execution_receipt_created": True,
            "atomic_commit_manifest_created": True,
            "minimum_hash_chain_complete": True,
            "source_reports_registry_completed": True,
            "implementation_sources_hashed": True,
            "all_prediction_artifacts_hashed": True,
        },
    }
    _assert_sources_unchanged(workspace_root=root, sources=sources)
    _publish_comparison_generation(
        workspace_root=root,
        generation_directory=generation_directory,
        commit_path=resolved_commit,
        report=report,
        sources=sources,
        protected_paths=protected_paths,
    )
    return report


__all__ = [
    "BootstrapResult",
    "ComparisonContractError",
    "HEAD_REPORT_SCHEMA_VERSION",
    "OUTCOMES",
    "PREDICTION_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "SEEDS",
    "compare_wavlm_frozen_heads",
    "event_f1_from_counts",
    "large_adoption_gate",
    "load_and_validate_config",
    "load_phone_prediction",
    "load_source_run",
    "load_utterance_prediction",
    "paired_phone_macro_f1_bootstrap",
    "paired_utterance_icc_bootstrap",
    "phone_event_counts",
]
