#!/usr/bin/env python3
"""Train and evaluate the oracle-boundary explicit baseline on train/dev only.

This runner consumes the privacy-safe caches emitted by
``build_explicit_feature_cache.py``.  It validates the cache report and
Parquet metadata before loading model inputs, refuses any evaluation split,
and writes the complete experiment directory atomically.

Phone models use numeric target-segment features plus one-hot target/context
phones.  Gold realized phones, gold alignment operations, and error tags are
never loaded as features.  Utterance models compare Ridge regression with a
simple cumulative-threshold ordinal logistic model.  All reported model
selection and metrics are development-set results, not locked-test results.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import shutil
import subprocess
from typing import Any
from uuid import uuid4

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from voice_coach.evaluation import (
    auprc,
    icc_2_1,
    macro_f1,
    mae,
    quadratic_weighted_kappa,
    slice_metric,
    spearman_correlation,
)
from voice_coach.experiments import experiment_run


REPORT_SCHEMA_VERSION = "voice_coach.explicit_baseline_report.v1"
CHECKPOINT_SCHEMA_VERSION = "voice_coach.explicit_baseline_checkpoint.v1"
PREDICTION_SCHEMA_VERSION = "voice_coach.explicit_baseline_predictions.v1"
ALLOWED_SPLITS = frozenset({"train", "dev"})
FORBIDDEN_SPLITS = frozenset({"stack", "calibration", "locked_test"})
SHA256_LENGTH = 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

PHONE_CATEGORICAL_FEATURES = (
    "target_label",
    "previous_target_label",
    "next_target_label",
)
PHONE_NUMERIC_FEATURES = (
    "oracle_duration_seconds",
    "segment_rms",
    "segment_zero_crossing_rate",
)
UTTERANCE_NUMERIC_FEATURES = (
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
    "speech_rate_phones_per_second",
    "articulation_rate_phones_per_second",
    "target_phone_label_duration_npvi",
)
UTTERANCE_OUTCOMES = (
    "pronunciation_score",
    "fluency_score",
    "comprehensibility_score",
)
PROHIBITED_PHONE_INPUTS = frozenset(
    {
        "realized_label",
        "alignment_operation",
        "binary_error_label",
        "oracle_start_seconds",
        "oracle_end_seconds",
        "error_tag",
        "error_tags",
    }
)
PROHIBITED_UTTERANCE_INPUTS = frozenset(
    {
        "has_phone_error",
        "error_interval_count",
        "realized_phone_count",
        "pronunciation_score",
        "fluency_score",
        "comprehensibility_score",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


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


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                _json_safe(value),
                handle,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_pickle(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
        handle.flush()
        os.fsync(handle.fileno())


def load_config(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("experiment config must be a mapping")
    return document


def _metadata_text(metadata: Mapping[bytes, bytes] | None, key: str) -> str:
    if not metadata or key.encode("utf-8") not in metadata:
        raise ValueError(f"feature cache metadata is missing {key}")
    return metadata[key.encode("utf-8")].decode("utf-8")


def _metadata_json_list(
    metadata: Mapping[bytes, bytes] | None,
    key: str,
) -> list[str]:
    try:
        value = json.loads(_metadata_text(metadata, key))
    except json.JSONDecodeError as error:
        raise ValueError(f"feature cache metadata {key} is invalid JSON") from error
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"feature cache metadata {key} must be a string list")
    return value


def validate_experiment_config(config: Mapping[str, Any]) -> None:
    data = config.get("data")
    evaluation = config.get("evaluation")
    features = config.get("features")
    models = config.get("models")
    experiment = config.get("experiment")
    if not all(
        isinstance(value, Mapping)
        for value in (data, evaluation, features, models, experiment)
    ):
        raise ValueError("config is missing a required mapping")

    if list(experiment.get("seeds", [])) != [42, 43, 44]:
        raise ValueError("explicit baseline seeds must be exactly [42, 43, 44]")
    if set(data.get("training_splits", [])) != {"train"}:
        raise ValueError("training_splits must contain only train")
    if set(data.get("tuning_splits", [])) != {"dev"}:
        raise ValueError("tuning_splits must contain only dev")
    if not FORBIDDEN_SPLITS.issubset(
        set(data.get("forbidden_metric_splits", []))
    ):
        raise ValueError("all evaluation splits must be explicitly forbidden")
    if evaluation.get("development_only") is not True:
        raise ValueError("evaluation.development_only must be true")
    if evaluation.get("locked_test_evaluation_allowed") is not False:
        raise ValueError("locked-test evaluation must be disabled")
    if data.get("exclude_qc_flagged") is not True:
        raise ValueError("QC-flagged rows must be excluded")
    if data.get("exclude_reserved_prompts") is not True:
        raise ValueError("reserved prompts must be excluded")
    expected_primary_scope = {
        "l1": ["Chinese", "Japanese"],
        "gender": ["F", "M"],
    }
    if data.get("primary_analysis_scope") != expected_primary_scope:
        raise ValueError(
            "primary_analysis_scope must be Chinese/Japanese and F/M"
        )

    phone_features = tuple(features.get("phone", []))
    utterance_features = tuple(features.get("utterance", []))
    if phone_features != PHONE_CATEGORICAL_FEATURES + PHONE_NUMERIC_FEATURES:
        raise ValueError(
            "config phone features do not match the reviewed cache allowlist"
        )
    if utterance_features != UTTERANCE_NUMERIC_FEATURES:
        raise ValueError(
            "config utterance features do not match the reviewed cache allowlist"
        )
    if PROHIBITED_PHONE_INPUTS & set(phone_features):
        raise ValueError("phone feature list contains a gold label/oracle target")
    if PROHIBITED_UTTERANCE_INPUTS & set(utterance_features):
        raise ValueError("utterance feature list contains an outcome/error label")

    phone_model = models.get("phone")
    utterance_model = models.get("utterance")
    if not isinstance(phone_model, Mapping) or not isinstance(
        utterance_model, Mapping
    ):
        raise ValueError("config model definitions are missing")
    if phone_model.get("family") != "logistic_regression":
        raise ValueError("phone model must be logistic_regression")
    if phone_model.get("class_weight") != "balanced":
        raise ValueError("phone model class_weight must be balanced")
    if not phone_model.get("c_grid"):
        raise ValueError("phone c_grid must not be empty")
    if "ridge" not in utterance_model.get("families", []):
        raise ValueError("utterance families must include ridge")
    if not utterance_model.get("ridge_alpha_grid"):
        raise ValueError("ridge alpha grid must not be empty")


def validate_cache_contract(
    *,
    config: Mapping[str, Any],
    utterance_cache: Path,
    phone_cache: Path,
    cache_report_path: Path,
) -> dict[str, Any]:
    """Validate cache provenance without reading any model outcomes."""

    report = json.loads(cache_report_path.read_text(encoding="utf-8"))
    if report.get("status") != "completed":
        raise ValueError("feature cache report status is not completed")
    policy = report.get("policy")
    progress = report.get("progress")
    outputs = report.get("outputs")
    source = report.get("source")
    if not all(
        isinstance(value, Mapping)
        for value in (policy, progress, outputs, source)
    ):
        raise ValueError("feature cache report is missing contract sections")
    required_policy = {
        "metric_evaluation_performed": False,
        "rows_with_qc_flags_excluded": True,
        "reserved_prompts_excluded_from_every_split": True,
        "privacy_safe": True,
        "oracle_alignment": True,
        "speaker_id_sha256_is_pseudonymous_hash_not_raw_user_id": True,
        "utterance_id_sha256_is_namespaced_hash_not_raw_file_stem": True,
        "raw_file_stem_contains_user_id_and_is_never_exported": True,
    }
    for key, expected in required_policy.items():
        if policy.get(key) is not expected:
            raise ValueError(f"feature cache policy {key} is not {expected}")
    reported_splits = set(policy.get("allowed_splits", []))
    if reported_splits != ALLOWED_SPLITS:
        raise ValueError("cache report must allow exactly train and dev")
    if reported_splits & FORBIDDEN_SPLITS:
        raise ValueError("cache report references a forbidden evaluation split")
    roles = policy.get("column_roles")
    if not isinstance(roles, Mapping):
        raise ValueError("cache report has no reviewed column-role contract")
    utterance_roles = roles.get("utterance")
    phone_roles = roles.get("phone")
    if not isinstance(utterance_roles, Mapping) or not isinstance(
        phone_roles, Mapping
    ):
        raise ValueError("cache report column roles are incomplete")
    if not set(UTTERANCE_NUMERIC_FEATURES).issubset(
        set(utterance_roles.get("input_feature_columns", []))
    ):
        raise ValueError("cache report does not approve utterance feature allowlist")
    if set(phone_roles.get("input_feature_columns", [])) != set(
        PHONE_CATEGORICAL_FEATURES + PHONE_NUMERIC_FEATURES
    ):
        raise ValueError("cache report phone feature allowlist differs")
    if set(UTTERANCE_OUTCOMES) != set(
        utterance_roles.get("label_columns", [])
    ):
        raise ValueError("cache report utterance label roles differ")
    if set(phone_roles.get("label_columns", [])) != {"binary_error_label"}:
        raise ValueError("cache report phone label role differs")
    if progress.get("source_scan_complete") is not True:
        raise ValueError("partial feature caches cannot train a baseline")
    configuration = report.get("configuration", {})
    if not isinstance(configuration, Mapping) or configuration.get("limit") is not None:
        raise ValueError("limited/smoke feature caches cannot train a baseline")

    manifest_sha = source.get("modeling_manifest_sha256")
    reservation_sha = source.get("prompt_reservation_sha256")
    expected_manifest_sha = config["data"].get("modeling_manifest_sha256")
    if manifest_sha != expected_manifest_sha or not _is_sha256(manifest_sha):
        raise ValueError("cache report modeling manifest hash does not match config")
    if not _is_sha256(reservation_sha):
        raise ValueError("cache report prompt reservation hash is invalid")

    utterance_output = outputs.get("utterance")
    phone_output = outputs.get("phone")
    if not isinstance(utterance_output, Mapping) or not isinstance(
        phone_output, Mapping
    ):
        raise ValueError("cache report output records are missing")
    canonical_utterance_sha = configuration.get(
        "canonical_utterance_cache_sha256"
    )
    expected_utterance_sha = (
        canonical_utterance_sha
        if canonical_utterance_sha is not None
        else utterance_output.get("sha256")
    )
    if not _is_sha256(expected_utterance_sha):
        raise ValueError("cache report canonical utterance hash is invalid")
    if sha256_file(utterance_cache) != expected_utterance_sha:
        raise ValueError("utterance cache hash differs from cache report")
    utterance_parquet = pq.ParquetFile(utterance_cache)
    if utterance_parquet.metadata.num_rows != utterance_output.get("row_count"):
        raise ValueError("utterance cache row count differs from cache report")

    if phone_output.get("enabled") is not True:
        raise ValueError("phone cache was not enabled")
    reported_shards = phone_output.get("shards")
    if not isinstance(reported_shards, list) or not reported_shards:
        raise ValueError("phone cache report has no shards")
    actual_paths = sorted(phone_cache.glob("part-*.parquet"))
    expected_names: list[str] = []
    total_phone_rows = 0
    for item in reported_shards:
        if not isinstance(item, Mapping):
            raise ValueError("invalid phone shard report item")
        name = item.get("path")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("invalid phone shard path in report")
        expected_names.append(name)
        shard = phone_cache / name
        if not shard.is_file():
            raise ValueError(f"reported phone shard does not exist: {name}")
        if sha256_file(shard) != item.get("sha256"):
            raise ValueError(f"phone shard hash differs from report: {name}")
        rows = pq.ParquetFile(shard).metadata.num_rows
        if rows != item.get("row_count"):
            raise ValueError(f"phone shard row count differs from report: {name}")
        total_phone_rows += rows
    if [path.name for path in actual_paths] != sorted(expected_names):
        raise ValueError("phone cache shard set differs from cache report")
    if total_phone_rows != phone_output.get("row_count"):
        raise ValueError("phone cache total row count differs from report")

    utterance_metadata = utterance_parquet.schema_arrow.metadata
    phone_metadata = pq.ParquetFile(actual_paths[0]).schema_arrow.metadata
    for cache_name, metadata in (
        ("utterance", utterance_metadata),
        ("phone", phone_metadata),
    ):
        if _metadata_text(metadata, "voice_coach.oracle_alignment") != "true":
            raise ValueError(f"{cache_name} cache is not marked oracle-alignment")
        if _metadata_text(metadata, "voice_coach.privacy_safe") != "true":
            raise ValueError(f"{cache_name} cache is not marked privacy-safe")
        if (
            _metadata_text(metadata, "voice_coach.source_manifest_sha256")
            != manifest_sha
        ):
            raise ValueError(f"{cache_name} cache source manifest hash differs")
        metadata_splits = set(
            _metadata_text(metadata, "voice_coach.allowed_splits").split(",")
        )
        if metadata_splits != ALLOWED_SPLITS:
            raise ValueError(
                f"{cache_name} cache metadata must allow exactly train and dev"
            )
        if (
            _metadata_text(
                metadata, "voice_coach.metric_evaluation_performed"
            )
            != "false"
        ):
            raise ValueError(f"{cache_name} cache already evaluated metrics")
        approved_inputs = set(
            _metadata_json_list(metadata, "voice_coach.input_feature_columns")
        )
        labels = set(_metadata_json_list(metadata, "voice_coach.label_columns"))
        forbidden_inputs = set(
            _metadata_json_list(metadata, "voice_coach.forbidden_as_input")
        )
        expected_inputs = (
            set(UTTERANCE_NUMERIC_FEATURES)
            if cache_name == "utterance"
            else set(PHONE_CATEGORICAL_FEATURES + PHONE_NUMERIC_FEATURES)
        )
        expected_labels = (
            set(UTTERANCE_OUTCOMES)
            if cache_name == "utterance"
            else {"binary_error_label"}
        )
        if not expected_inputs.issubset(approved_inputs):
            raise ValueError(f"{cache_name} metadata does not approve all inputs")
        if labels != expected_labels:
            raise ValueError(f"{cache_name} metadata label roles differ")
        if expected_inputs & forbidden_inputs:
            raise ValueError(f"{cache_name} metadata marks an input as forbidden")

    return {
        "validated": True,
        "cache_report_sha256": sha256_file(cache_report_path),
        "modeling_manifest_sha256": manifest_sha,
        "prompt_reservation_sha256": reservation_sha,
        "utterance_cache_sha256": expected_utterance_sha,
        "utterance_cache_role": (
            "canonical_reused_by_phone_run"
            if canonical_utterance_sha is not None
            else "cache_report_output"
        ),
        "utterance_rows": utterance_output["row_count"],
        "phone_rows": phone_output["row_count"],
        "phone_shards": len(reported_shards),
        "source_scan_complete": True,
        "qc_flagged_rows_excluded": True,
        "reserved_prompts_excluded": True,
        "accepted_splits": ["dev", "train"],
    }


def _require_columns(schema: pa.Schema, required: set[str], name: str) -> None:
    missing = sorted(required - set(schema.names))
    if missing:
        raise ValueError(f"{name} cache is missing columns: {', '.join(missing)}")


def _as_strings(table: pa.Table, name: str) -> np.ndarray:
    return np.asarray(
        [
            "__MISSING__" if value is None else str(value)
            for value in table.column(name).to_pylist()
        ],
        dtype=object,
    )


def _as_floats(table: pa.Table, name: str) -> np.ndarray:
    return np.asarray(
        [
            float("nan") if value is None else float(value)
            for value in table.column(name).to_pylist()
        ],
        dtype=np.float64,
    )


def _as_ints(table: pa.Table, name: str) -> np.ndarray:
    values = table.column(name).to_pylist()
    if any(value is None for value in values):
        raise ValueError(f"{name} must not contain missing values")
    return np.asarray(values, dtype=np.int64)


def _validate_hash_column(table: pa.Table, name: str) -> None:
    values = _as_strings(table, name)
    if any(not _is_sha256(value) for value in values.tolist()):
        raise ValueError(f"{name} must contain only lowercase SHA-256 values")


def _feature_matrix(
    table: pa.Table,
    *,
    categorical: Sequence[str],
    numeric: Sequence[str],
) -> np.ndarray:
    columns: list[np.ndarray] = []
    columns.extend(_as_strings(table, name) for name in categorical)
    columns.extend(_as_floats(table, name) for name in numeric)
    return np.column_stack(columns)


def _validate_split_arrays(
    *,
    utterances: pa.Table,
    phones: pa.Table,
) -> None:
    utterance_splits = _as_strings(utterances, "split")
    phone_splits = _as_strings(phones, "split")
    observed = set(utterance_splits.tolist()) | set(phone_splits.tolist())
    forbidden = observed & FORBIDDEN_SPLITS
    if forbidden:
        raise ValueError(
            "forbidden evaluation split found in feature cache: "
            + ", ".join(sorted(forbidden))
        )
    if observed - ALLOWED_SPLITS:
        raise ValueError(
            "unknown split found in feature cache: "
            + ", ".join(sorted(observed - ALLOWED_SPLITS))
        )
    if set(utterance_splits.tolist()) != ALLOWED_SPLITS:
        raise ValueError("utterance cache must contain both train and dev")
    if set(phone_splits.tolist()) != ALLOWED_SPLITS:
        raise ValueError("phone cache must contain both train and dev")

    utterance_speakers = _as_strings(utterances, "speaker_id_sha256")
    train_speakers = set(utterance_speakers[utterance_splits == "train"])
    dev_speakers = set(utterance_speakers[utterance_splits == "dev"])
    overlap = train_speakers & dev_speakers
    if overlap:
        raise ValueError(
            f"speaker leakage between train and dev: {len(overlap)} speakers"
        )
    utterance_ids = _as_strings(utterances, "utterance_id_sha256")
    train_ids = set(utterance_ids[utterance_splits == "train"])
    dev_ids = set(utterance_ids[utterance_splits == "dev"])
    if train_ids & dev_ids:
        raise ValueError("utterance leakage between train and dev")

    split_by_id = {
        identifier: split
        for identifier, split in zip(
            utterance_ids.tolist(), utterance_splits.tolist(), strict=True
        )
    }
    phone_ids = _as_strings(phones, "utterance_id_sha256")
    for identifier, split in zip(
        phone_ids.tolist(), phone_splits.tolist(), strict=True
    ):
        if identifier not in split_by_id:
            raise ValueError("phone cache contains an unknown utterance_id")
        if split_by_id[identifier] != split:
            raise ValueError("phone/utterance cache split mismatch")
    for table in (utterances, phones):
        _validate_hash_column(table, "utterance_id_sha256")
        _validate_hash_column(table, "speaker_id_sha256")
        oracle = table.column("oracle_alignment").to_pylist()
        if not oracle or any(value is not True for value in oracle):
            raise ValueError("every cache row must be marked oracle_alignment=true")


def load_feature_caches(
    utterance_cache: Path,
    phone_cache: Path,
) -> tuple[pa.Table, pa.Table]:
    utterance_required = {
        "utterance_id_sha256",
        "speaker_id_sha256",
        "oracle_alignment",
        "split",
        "l1",
        "gender",
        "proficiency",
        *UTTERANCE_NUMERIC_FEATURES,
        *UTTERANCE_OUTCOMES,
    }
    phone_required = {
        "utterance_id_sha256",
        "speaker_id_sha256",
        "oracle_alignment",
        "split",
        "phone_index",
        "l1",
        "gender",
        "proficiency",
        "binary_error_label",
        *PHONE_CATEGORICAL_FEATURES,
        *PHONE_NUMERIC_FEATURES,
    }
    utterance_schema = pq.ParquetFile(utterance_cache).schema_arrow
    phone_dataset = pq.ParquetDataset(phone_cache)
    _require_columns(utterance_schema, utterance_required, "utterance")
    _require_columns(phone_dataset.schema, phone_required, "phone")
    # Column selection is also a leakage control: gold realized identities,
    # operations, error intervals/tags, and restricted paths are not loaded.
    utterances = pq.read_table(
        utterance_cache, columns=sorted(utterance_required)
    )
    phones = pq.read_table(phone_cache, columns=sorted(phone_required))
    _validate_split_arrays(utterances=utterances, phones=phones)
    return utterances, phones


def _primary_scope_mask(
    table: pa.Table,
    config: Mapping[str, Any],
) -> tuple[np.ndarray, dict[str, int]]:
    keep = np.ones(len(table), dtype=np.bool_)
    exclusions: dict[str, int] = {}
    rules = config["data"].get("primary_analysis_scope", {})
    if not isinstance(rules, Mapping):
        raise ValueError("primary_analysis_scope must be a mapping")
    for column, included_values in rules.items():
        if column not in table.column_names:
            raise ValueError(f"primary scope column is unavailable: {column}")
        included = {str(value) for value in included_values}
        values = _as_strings(table, str(column))
        outside = ~np.isin(values, list(included))
        exclusions[str(column)] = int(outside.sum())
        keep &= ~outside
    exclusions["outside_union"] = int((~keep).sum())
    return keep, dict(sorted(exclusions.items()))


def make_phone_pipeline(
    *,
    c_value: float,
    max_iter: int,
    seed: int,
) -> Pipeline:
    categorical_count = len(PHONE_CATEGORICAL_FEATURES)
    transformer = ColumnTransformer(
        [
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                list(range(categorical_count)),
            ),
            (
                "numeric",
                Pipeline(
                    [
                        (
                            "impute",
                            SimpleImputer(
                                strategy="median",
                                keep_empty_features=True,
                            ),
                        ),
                        ("scale", StandardScaler()),
                    ]
                ),
                list(
                    range(
                        categorical_count,
                        categorical_count + len(PHONE_NUMERIC_FEATURES),
                    )
                ),
            ),
        ]
    )
    return Pipeline(
        [
            ("features", transformer),
            (
                "model",
                LogisticRegression(
                    C=float(c_value),
                    class_weight="balanced",
                    max_iter=int(max_iter),
                    random_state=int(seed),
                    solver="liblinear",
                ),
            ),
        ]
    )


def make_ridge_pipeline(alpha: float) -> Pipeline:
    return Pipeline(
        [
            (
                "impute",
                SimpleImputer(strategy="median", keep_empty_features=True),
            ),
            ("scale", StandardScaler()),
            ("model", Ridge(alpha=float(alpha))),
        ]
    )


def fit_ordinal_model(
    features: np.ndarray,
    targets: np.ndarray,
    *,
    minimum: int,
    maximum: int,
    seed: int,
    max_iter: int,
) -> dict[str, Any]:
    thresholds: list[dict[str, Any]] = []
    for threshold in range(minimum, maximum):
        binary = np.asarray(targets > threshold, dtype=np.int64)
        if np.unique(binary).size < 2:
            thresholds.append(
                {
                    "threshold": threshold,
                    "constant_probability": float(binary[0]),
                    "pipeline": None,
                }
            )
            continue
        pipeline = Pipeline(
            [
                (
                    "impute",
                    SimpleImputer(strategy="median", keep_empty_features=True),
                ),
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=1.0,
                        class_weight="balanced",
                        max_iter=max_iter,
                        random_state=seed,
                        solver="liblinear",
                    ),
                ),
            ]
        )
        pipeline.fit(features, binary)
        thresholds.append(
            {
                "threshold": threshold,
                "constant_probability": None,
                "pipeline": pipeline,
            }
        )
    return {
        "family": "ordinal_logistic",
        "minimum": minimum,
        "maximum": maximum,
        "thresholds": thresholds,
    }


def predict_ordinal_model(model: Mapping[str, Any], features: np.ndarray) -> np.ndarray:
    probabilities: list[np.ndarray] = []
    for threshold in model["thresholds"]:
        pipeline = threshold["pipeline"]
        if pipeline is None:
            values = np.full(
                features.shape[0],
                float(threshold["constant_probability"]),
                dtype=np.float64,
            )
        else:
            values = pipeline.predict_proba(features)[:, 1]
        probabilities.append(values)
    stacked = np.column_stack(probabilities)
    # Independently fitted cumulative logits can cross.  Enforce the required
    # P(Y>1) >= P(Y>2) >= ... ordering before computing E[Y].
    monotonic = np.minimum.accumulate(stacked, axis=1)
    return float(model["minimum"]) + monotonic.sum(axis=1)


def predict_utterance_model(
    model: Mapping[str, Any],
    features: np.ndarray,
    *,
    minimum: int,
    maximum: int,
) -> np.ndarray:
    if model["family"] == "ridge":
        predictions = model["pipeline"].predict(features)
    elif model["family"] == "ordinal_logistic":
        predictions = predict_ordinal_model(model, features)
    else:
        raise ValueError(f"unsupported utterance model family: {model['family']}")
    return np.clip(predictions, minimum, maximum)


def utterance_metrics(
    targets: np.ndarray,
    predictions: np.ndarray,
    *,
    minimum: int,
    maximum: int,
) -> dict[str, float]:
    clipped = np.clip(predictions, minimum, maximum)
    ordinal_predictions = np.rint(clipped).astype(np.int64)
    integer_targets = np.rint(targets).astype(np.int64)
    return {
        "qwk": quadratic_weighted_kappa(
            integer_targets,
            ordinal_predictions,
            labels=list(range(minimum, maximum + 1)),
        ),
        "spearman": spearman_correlation(targets, clipped),
        "mae": mae(targets, clipped),
        "icc_2_1": icc_2_1(np.column_stack([targets, clipped])),
    }


def _candidate_rank(metrics: Mapping[str, float]) -> tuple[float, float, float]:
    qwk = metrics["qwk"]
    spearman = metrics["spearman"]
    return (
        qwk if math.isfinite(qwk) else -math.inf,
        spearman if math.isfinite(spearman) else -math.inf,
        -metrics["mae"],
    )


def _duration_buckets(durations: np.ndarray) -> np.ndarray:
    return np.asarray(
        [
            "lt_5s"
            if value < 5.0
            else "5_to_lt_10s"
            if value < 10.0
            else "10_to_lt_20s"
            if value < 20.0
            else "ge_20s"
            for value in durations
        ],
        dtype=object,
    )


def phone_slice_metrics(
    table: pa.Table,
    selected: np.ndarray,
    targets: np.ndarray,
    predictions: np.ndarray,
    probabilities: np.ndarray,
    requested_slices: Sequence[str],
) -> dict[str, Any]:
    aliases = {"target_phone": "target_label"}
    output: dict[str, Any] = {}
    selected_table = table.filter(pa.array(selected))
    for requested in requested_slices:
        column = aliases.get(requested, requested)
        if column not in {"l1", "gender", "proficiency", "target_label"}:
            output[requested] = {
                "status": "unavailable",
                "reason": (
                    "not exported by the privacy/leakage-reviewed phone cache"
                ),
            }
            continue
        groups = _as_strings(selected_table, column)
        f1_values = slice_metric(
            lambda y, pred: macro_f1(y, pred, labels=[0, 1]),
            groups,
            targets,
            predictions,
        )
        ap_values = slice_metric(
            lambda y, score: auprc(
                y, score, labels=[0, 1], undefined="nan"
            ),
            groups,
            targets,
            probabilities,
        )
        output[requested] = {
            "status": "computed",
            "groups": [
                {
                    "value": f1.slice_value,
                    "n_samples": f1.n_samples,
                    "macro_f1": f1.value,
                    "auprc": ap.value,
                }
                for f1, ap in zip(f1_values, ap_values, strict=True)
            ],
        }
    return output


def utterance_slice_metrics(
    table: pa.Table,
    selected: np.ndarray,
    valid_outcome: np.ndarray,
    targets: np.ndarray,
    predictions: np.ndarray,
    requested_slices: Sequence[str],
    *,
    minimum: int,
    maximum: int,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    rows = np.flatnonzero(selected)[valid_outcome]
    for requested in requested_slices:
        if requested == "duration_bucket":
            groups = _duration_buckets(
                _as_floats(table, "audio_duration_seconds")[rows]
            )
        elif requested in {"l1", "gender", "proficiency"}:
            groups = _as_strings(table, requested)[rows]
        else:
            output[requested] = {
                "status": "unavailable",
                "reason": "not defined for utterance score evaluation",
            }
            continue
        qwk_results = slice_metric(
            lambda y, pred: quadratic_weighted_kappa(
                np.rint(y).astype(np.int64),
                np.rint(np.clip(pred, minimum, maximum)).astype(np.int64),
                labels=list(range(minimum, maximum + 1)),
            ),
            groups,
            targets,
            predictions,
        )
        spearman_results = slice_metric(
            spearman_correlation,
            groups,
            targets,
            predictions,
            min_samples=2,
        )
        mae_results = slice_metric(
            mae,
            groups,
            targets,
            predictions,
        )
        icc_results = slice_metric(
            lambda y, pred: icc_2_1(np.column_stack([y, pred])),
            groups,
            targets,
            predictions,
            min_samples=2,
        )
        output[requested] = {
            "status": "computed",
            "groups": [
                {
                    "value": qwk.slice_value,
                    "n_samples": qwk.n_samples,
                    "qwk": qwk.value,
                    "spearman": spearman.value,
                    "mae": mae_result.value,
                    "icc_2_1": icc.value,
                }
                for qwk, spearman, mae_result, icc in zip(
                    qwk_results,
                    spearman_results,
                    mae_results,
                    icc_results,
                    strict=True,
                )
            ],
        }
    return output


def _save_phone_predictions(
    path: Path,
    *,
    table: pa.Table,
    dev_mask: np.ndarray,
    seed: int,
    targets: np.ndarray,
    predictions: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, Any]:
    selected_table = table.filter(pa.array(dev_mask))
    row_count = len(selected_table)
    output = pa.table(
        {
            "schema_version": [PREDICTION_SCHEMA_VERSION] * row_count,
            "oracle_alignment": [True] * row_count,
            "development_only": [True] * row_count,
            "seed": np.full(row_count, seed, dtype=np.int32),
            "utterance_id_sha256": np.asarray(
                selected_table.column("utterance_id_sha256").to_pylist(),
                dtype=object,
            ),
            "speaker_id_sha256": np.asarray(
                selected_table.column("speaker_id_sha256").to_pylist(),
                dtype=object,
            ),
            "phone_index": _as_ints(selected_table, "phone_index"),
            "split": ["dev"] * row_count,
            "target_label": _as_strings(selected_table, "target_label"),
            "y_true": targets.astype(np.int8),
            "y_pred": predictions.astype(np.int8),
            "error_probability": probabilities.astype(np.float64),
        }
    )
    metadata = {
        b"voice_coach.development_only": b"true",
        b"voice_coach.oracle_alignment": b"true",
    }
    pq.write_table(output.replace_schema_metadata(metadata), path, compression="zstd")
    return {
        "path": path.name,
        "rows": output.num_rows,
        "sha256": sha256_file(path),
    }


def _save_utterance_predictions(
    path: Path,
    *,
    table: pa.Table,
    dev_mask: np.ndarray,
    seed: int,
    outcome_predictions: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    selected_table = table.filter(pa.array(dev_mask))
    row_count = len(selected_table)
    columns: dict[str, Any] = {
        "schema_version": [PREDICTION_SCHEMA_VERSION] * row_count,
        "oracle_alignment": [True] * row_count,
        "development_only": [True] * row_count,
        "seed": np.full(row_count, seed, dtype=np.int32),
        "utterance_id_sha256": np.asarray(
            selected_table.column("utterance_id_sha256").to_pylist(),
            dtype=object,
        ),
        "speaker_id_sha256": np.asarray(
            selected_table.column("speaker_id_sha256").to_pylist(),
            dtype=object,
        ),
        "split": ["dev"] * row_count,
    }
    for outcome in UTTERANCE_OUTCOMES:
        truth = _as_floats(selected_table, outcome)
        columns[f"{outcome}_true"] = truth
        columns[f"{outcome}_pred"] = outcome_predictions[outcome]
        columns[f"{outcome}_observed"] = np.isfinite(truth)
    output = pa.table(columns)
    metadata = {
        b"voice_coach.development_only": b"true",
        b"voice_coach.oracle_alignment": b"true",
    }
    pq.write_table(output.replace_schema_metadata(metadata), path, compression="zstd")
    return {
        "path": path.name,
        "rows": output.num_rows,
        "sha256": sha256_file(path),
    }


def _git_state(workdir: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return {
            "commit": commit,
            "working_tree_dirty": bool(status.strip()),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "working_tree_dirty": None}


def train_explicit_baseline(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    validate_experiment_config(config)
    if args.output_dir.exists():
        raise FileExistsError(f"output directory already exists: {args.output_dir}")
    contract = validate_cache_contract(
        config=config,
        utterance_cache=args.utterance_cache,
        phone_cache=args.phone_cache,
        cache_report_path=args.cache_report,
    )
    utterances, phones = load_feature_caches(
        args.utterance_cache, args.phone_cache
    )

    utterance_primary_scope, utterance_exclusions = _primary_scope_mask(
        utterances, config
    )
    phone_primary_scope, phone_exclusions = _primary_scope_mask(phones, config)
    utterance_splits = _as_strings(utterances, "split")
    phone_splits = _as_strings(phones, "split")
    utterance_train = (
        (utterance_splits == "train") & utterance_primary_scope
    )
    utterance_dev_all = utterance_splits == "dev"
    utterance_dev_primary = utterance_dev_all & utterance_primary_scope
    phone_train = (phone_splits == "train") & phone_primary_scope
    phone_dev_all = phone_splits == "dev"
    phone_dev_primary = phone_dev_all & phone_primary_scope
    if min(
        int(utterance_train.sum()),
        int(utterance_dev_primary.sum()),
        int(phone_train.sum()),
        int(phone_dev_primary.sum()),
    ) == 0:
        raise ValueError("train/dev rows must remain in the primary scope")

    phone_features = _feature_matrix(
        phones,
        categorical=PHONE_CATEGORICAL_FEATURES,
        numeric=PHONE_NUMERIC_FEATURES,
    )
    phone_targets = _as_ints(phones, "binary_error_label")
    if set(np.unique(phone_targets[phone_train]).tolist()) != {0, 1}:
        raise ValueError("phone training rows must contain both binary classes")
    if not set(np.unique(phone_targets).tolist()).issubset({0, 1}):
        raise ValueError("binary_error_label contains a value outside {0, 1}")

    utterance_features = _feature_matrix(
        utterances,
        categorical=(),
        numeric=UTTERANCE_NUMERIC_FEATURES,
    ).astype(np.float64)
    minimum, maximum = [
        int(value)
        for value in config["supervision"]["utterance"]["valid_score_range"]
    ]
    if minimum >= maximum:
        raise ValueError("utterance score range is invalid")
    for outcome in UTTERANCE_OUTCOMES:
        values = _as_floats(utterances, outcome)
        observed = values[np.isfinite(values)]
        if np.any((observed < minimum) | (observed > maximum)):
            raise ValueError(f"{outcome} contains a score outside valid range")
        if np.any(observed != np.rint(observed)):
            raise ValueError(f"{outcome} must contain ordinal integer scores")

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output_dir.with_name(
        f".{args.output_dir.name}.part-{uuid4().hex}"
    )
    temporary.mkdir()
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "running",
        "started_at_utc": utc_now(),
        "completed_at_utc": None,
        "experiment_id": config["experiment"]["id"],
        "development_only": True,
        "oracle_alignment": True,
        "deployable_alignment_claim_allowed": False,
        "warning": (
            "All results use gold target-phone boundaries and are an "
            "oracle-alignment diagnostic baseline, not deployable performance."
        ),
        "experiment_registry": {
            "integrated": False,
            "t03_complete": False,
            "reason": (
                "Direct library execution does not create registry metadata; "
                "use the CLI/registered wrapper for an engineering run."
            ),
        },
        "leakage_controls": {
            "locked_test_accessed": False,
            "stack_accessed": False,
            "calibration_accessed": False,
            "accepted_splits": ["train", "dev"],
            "speaker_overlap_train_dev": 0,
            "utterance_overlap_train_dev": 0,
            "phone_feature_allowlist": list(
                PHONE_CATEGORICAL_FEATURES + PHONE_NUMERIC_FEATURES
            ),
            "utterance_feature_allowlist": list(UTTERANCE_NUMERIC_FEATURES),
            "prohibited_phone_inputs": sorted(PROHIBITED_PHONE_INPUTS),
            "prohibited_utterance_inputs": sorted(PROHIBITED_UTTERANCE_INPUTS),
            "gold_realized_phone_loaded": False,
            "gold_alignment_operation_loaded": False,
            "gold_error_tags_loaded": False,
            "dev_used_for_hyperparameter_and_family_selection": True,
            "primary_dev_scope_used_for_selection_and_gates": True,
            "all_dev_predictions_preserved_for_audit": True,
        },
        "cache_contract": contract,
        "sources": {
            "config_sha256": sha256_file(args.config),
            "cache_report_sha256": sha256_file(args.cache_report),
            "utterance_cache_sha256": sha256_file(args.utterance_cache),
            "phone_shard_hashes": [
                {
                    "path": path.name,
                    "sha256": sha256_file(path),
                }
                for path in sorted(args.phone_cache.glob("part-*.parquet"))
            ],
        },
        "git": _git_state(args.config.parents[2]),
        "data": {
            "primary_analysis_scope": config["data"]["primary_analysis_scope"],
            "utterance_rows": {
                "train_primary": int(utterance_train.sum()),
                "dev_primary": int(utterance_dev_primary.sum()),
                "dev_all_audit": int(utterance_dev_all.sum()),
                "dev_outside_primary": int(
                    (utterance_dev_all & ~utterance_primary_scope).sum()
                ),
            },
            "phone_rows": {
                "train_primary": int(phone_train.sum()),
                "dev_primary": int(phone_dev_primary.sum()),
                "dev_all_audit": int(phone_dev_all.sum()),
                "dev_outside_primary": int(
                    (phone_dev_all & ~phone_primary_scope).sum()
                ),
            },
            "outside_primary_scope_counts_all_train_dev": {
                "utterance": utterance_exclusions,
                "phone": phone_exclusions,
            },
            "duration_bucket_definition": [
                "lt_5s",
                "5_to_lt_10s",
                "10_to_lt_20s",
                "ge_20s",
            ],
        },
        "runs": [],
    }

    try:
        c_grid = [float(value) for value in config["models"]["phone"]["c_grid"]]
        max_iter = int(config["models"]["phone"]["max_iter"])
        alpha_grid = [
            float(value)
            for value in config["models"]["utterance"]["ridge_alpha_grid"]
        ]
        requested_slices = list(config["evaluation"]["slices"])
        for seed in config["experiment"]["seeds"]:
            seed_dir = temporary / f"seed_{seed}"
            seed_dir.mkdir()

            phone_candidates: list[dict[str, Any]] = []
            best_phone: tuple[float, Pipeline, np.ndarray, np.ndarray] | None = None
            best_f1 = -math.inf
            train_phone_features = phone_features[phone_train]
            train_phone_targets = phone_targets[phone_train]
            primary_dev_phone_features = phone_features[phone_dev_primary]
            primary_dev_phone_targets = phone_targets[phone_dev_primary]
            for c_value in c_grid:
                pipeline = make_phone_pipeline(
                    c_value=c_value,
                    max_iter=max_iter,
                    seed=int(seed),
                )
                pipeline.fit(train_phone_features, train_phone_targets)
                predictions = pipeline.predict(
                    primary_dev_phone_features
                ).astype(np.int64)
                probabilities = pipeline.predict_proba(
                    primary_dev_phone_features
                )[:, 1]
                metrics = {
                    "macro_f1": macro_f1(
                        primary_dev_phone_targets, predictions, labels=[0, 1]
                    ),
                    "auprc": auprc(
                        primary_dev_phone_targets,
                        probabilities,
                        labels=[0, 1],
                        undefined="nan",
                    ),
                }
                phone_candidates.append({"c": c_value, "metrics": metrics})
                if metrics["macro_f1"] > best_f1:
                    best_f1 = metrics["macro_f1"]
                    best_phone = (
                        c_value,
                        pipeline,
                        predictions,
                        probabilities,
                    )
            if best_phone is None:
                raise RuntimeError("phone model selection produced no candidate")
            selected_c, selected_phone, phone_predictions, phone_probabilities = (
                best_phone
            )
            phone_metrics = {
                "macro_f1": macro_f1(
                    primary_dev_phone_targets,
                    phone_predictions,
                    labels=[0, 1],
                ),
                "auprc": auprc(
                    primary_dev_phone_targets,
                    phone_probabilities,
                    labels=[0, 1],
                    undefined="nan",
                ),
            }
            all_dev_phone_features = phone_features[phone_dev_all]
            all_dev_phone_targets = phone_targets[phone_dev_all]
            all_dev_phone_predictions = selected_phone.predict(
                all_dev_phone_features
            ).astype(np.int64)
            all_dev_phone_probabilities = selected_phone.predict_proba(
                all_dev_phone_features
            )[:, 1]
            all_dev_phone_metrics = {
                "macro_f1": macro_f1(
                    all_dev_phone_targets,
                    all_dev_phone_predictions,
                    labels=[0, 1],
                ),
                "auprc": auprc(
                    all_dev_phone_targets,
                    all_dev_phone_probabilities,
                    labels=[0, 1],
                    undefined="nan",
                ),
            }
            phone_slices = phone_slice_metrics(
                phones,
                phone_dev_all,
                all_dev_phone_targets,
                all_dev_phone_predictions,
                all_dev_phone_probabilities,
                requested_slices,
            )
            phone_prediction_artifact = _save_phone_predictions(
                seed_dir / "phone_dev_predictions.parquet",
                table=phones,
                dev_mask=phone_dev_all,
                seed=int(seed),
                targets=all_dev_phone_targets,
                predictions=all_dev_phone_predictions,
                probabilities=all_dev_phone_probabilities,
            )
            phone_prediction_artifact["path"] = str(
                (Path(f"seed_{seed}") / phone_prediction_artifact["path"])
            )

            outcome_reports: dict[str, Any] = {}
            outcome_models: dict[str, Any] = {}
            outcome_predictions_full: dict[str, np.ndarray] = {}
            for outcome in UTTERANCE_OUTCOMES:
                all_targets = _as_floats(utterances, outcome)
                train_observed = np.isfinite(all_targets[utterance_train])
                primary_dev_observed = np.isfinite(
                    all_targets[utterance_dev_primary]
                )
                all_dev_observed = np.isfinite(
                    all_targets[utterance_dev_all]
                )
                train_targets = all_targets[utterance_train][train_observed]
                primary_dev_targets = all_targets[utterance_dev_primary][
                    primary_dev_observed
                ]
                all_dev_targets = all_targets[utterance_dev_all][all_dev_observed]
                if train_targets.size < 2 or primary_dev_targets.size < 2:
                    raise ValueError(
                        f"{outcome} needs at least two observed primary "
                        "train/dev scores"
                    )
                train_features = utterance_features[utterance_train][train_observed]
                primary_dev_features = utterance_features[
                    utterance_dev_primary
                ][primary_dev_observed]
                candidates: list[dict[str, Any]] = []
                candidate_models: list[tuple[dict[str, Any], np.ndarray]] = []
                for alpha in alpha_grid:
                    ridge = make_ridge_pipeline(alpha)
                    ridge.fit(train_features, train_targets)
                    predictions = np.clip(
                        ridge.predict(primary_dev_features), minimum, maximum
                    )
                    metrics = utterance_metrics(
                        primary_dev_targets,
                        predictions,
                        minimum=minimum,
                        maximum=maximum,
                    )
                    descriptor = {
                        "family": "ridge",
                        "alpha": alpha,
                        "metrics": metrics,
                    }
                    candidates.append(descriptor)
                    candidate_models.append(
                        (
                            {
                                "family": "ridge",
                                "alpha": alpha,
                                "pipeline": ridge,
                            },
                            predictions,
                        )
                    )

                if "ordinal_logistic" in config["models"]["utterance"].get(
                    "families", []
                ):
                    ordinal = fit_ordinal_model(
                        train_features,
                        train_targets,
                        minimum=minimum,
                        maximum=maximum,
                        seed=int(seed),
                        max_iter=max_iter,
                    )
                    ordinal_predictions = np.clip(
                        predict_ordinal_model(ordinal, primary_dev_features),
                        minimum,
                        maximum,
                    )
                    ordinal_metrics = utterance_metrics(
                        primary_dev_targets,
                        ordinal_predictions,
                        minimum=minimum,
                        maximum=maximum,
                    )
                    candidates.append(
                        {
                            "family": "ordinal_logistic",
                            "metrics": ordinal_metrics,
                        }
                    )
                    candidate_models.append((ordinal, ordinal_predictions))

                best_index = max(
                    range(len(candidates)),
                    key=lambda index: _candidate_rank(
                        candidates[index]["metrics"]
                    ),
                )
                selected_descriptor = candidates[best_index]
                selected_model, _ = candidate_models[best_index]
                outcome_models[outcome] = selected_model
                all_dev_predictions = predict_utterance_model(
                    selected_model,
                    utterance_features[utterance_dev_all],
                    minimum=minimum,
                    maximum=maximum,
                )
                outcome_predictions_full[outcome] = all_dev_predictions
                outcome_reports[outcome] = {
                    "observed_rows": {
                        "train_primary": int(train_targets.size),
                        "dev_primary": int(primary_dev_targets.size),
                        "dev_all_audit": int(all_dev_targets.size),
                        "dev_all_missing": int((~all_dev_observed).sum()),
                    },
                    "selection_rule": (
                        "maximize primary-scope dev QWK, then Spearman, "
                        "then minimize MAE"
                    ),
                    "metric_scope": "primary_dev",
                    "selected": {
                        key: value
                        for key, value in selected_descriptor.items()
                        if key != "metrics"
                    },
                    "metrics": selected_descriptor["metrics"],
                    "all_dev_audit_metrics": utterance_metrics(
                        all_dev_targets,
                        all_dev_predictions[all_dev_observed],
                        minimum=minimum,
                        maximum=maximum,
                    ),
                    "candidates": candidates,
                    "slices": utterance_slice_metrics(
                        utterances,
                        utterance_dev_all,
                        all_dev_observed,
                        all_dev_targets,
                        all_dev_predictions[all_dev_observed],
                        requested_slices,
                        minimum=minimum,
                        maximum=maximum,
                    ),
                }

            utterance_prediction_artifact = _save_utterance_predictions(
                seed_dir / "utterance_dev_predictions.parquet",
                table=utterances,
                dev_mask=utterance_dev_all,
                seed=int(seed),
                outcome_predictions=outcome_predictions_full,
            )
            utterance_prediction_artifact["path"] = str(
                (
                    Path(f"seed_{seed}")
                    / utterance_prediction_artifact["path"]
                )
            )
            checkpoint = {
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "seed": int(seed),
                "development_only": True,
                "oracle_alignment": True,
                "deployable_alignment_claim_allowed": False,
                "primary_analysis_scope": config["data"][
                    "primary_analysis_scope"
                ],
                "phone": {
                    "features": list(
                        PHONE_CATEGORICAL_FEATURES + PHONE_NUMERIC_FEATURES
                    ),
                    "selected_c": selected_c,
                    "pipeline": selected_phone,
                },
                "utterance": {
                    "features": list(UTTERANCE_NUMERIC_FEATURES),
                    "score_range": [minimum, maximum],
                    "outcomes": outcome_models,
                },
            }
            checkpoint_path = seed_dir / "checkpoint.pkl"
            write_pickle(checkpoint_path, checkpoint)
            slices_path = seed_dir / "slice_metrics.json"
            write_json(
                slices_path,
                {
                    "development_only": True,
                    "metric_scope": "all_dev_audit_slices",
                    "primary_gate_scope": config["data"][
                        "primary_analysis_scope"
                    ],
                    "seed": int(seed),
                    "phone": phone_slices,
                    "utterance": {
                        outcome: result["slices"]
                        for outcome, result in outcome_reports.items()
                    },
                },
            )
            report["runs"].append(
                {
                    "seed": int(seed),
                    "phone": {
                        "selected_c": selected_c,
                        "metric_scope": "primary_dev",
                        "metrics": phone_metrics,
                        "all_dev_audit_metrics": all_dev_phone_metrics,
                        "candidates": phone_candidates,
                        "confusion_matrix_labels": [0, 1],
                        "confusion_matrix": confusion_matrix(
                            primary_dev_phone_targets,
                            phone_predictions,
                            labels=[0, 1],
                        ).tolist(),
                        "all_dev_audit_confusion_matrix": confusion_matrix(
                            all_dev_phone_targets,
                            all_dev_phone_predictions,
                            labels=[0, 1],
                        ).tolist(),
                        "predictions": phone_prediction_artifact,
                    },
                    "utterance": outcome_reports,
                    "artifacts": {
                        "checkpoint": {
                            "path": str(
                                checkpoint_path.relative_to(temporary)
                            ),
                            "sha256": sha256_file(checkpoint_path),
                        },
                        "slice_metrics": {
                            "path": str(slices_path.relative_to(temporary)),
                            "sha256": sha256_file(slices_path),
                        },
                        "utterance_predictions": utterance_prediction_artifact,
                    },
                }
            )

        report["status"] = "completed"
        report["completed_at_utc"] = utc_now()
        phone_macro_f1_values = np.asarray(
            [
                run["phone"]["metrics"]["macro_f1"]
                for run in report["runs"]
            ],
            dtype=np.float64,
        )
        phone_auprc_values = np.asarray(
            [run["phone"]["metrics"]["auprc"] for run in report["runs"]],
            dtype=np.float64,
        )
        all_dev_phone_f1_values = np.asarray(
            [
                run["phone"]["all_dev_audit_metrics"]["macro_f1"]
                for run in report["runs"]
            ],
            dtype=np.float64,
        )
        all_dev_phone_auprc_values = np.asarray(
            [
                run["phone"]["all_dev_audit_metrics"]["auprc"]
                for run in report["runs"]
            ],
            dtype=np.float64,
        )
        utterance_summary: dict[str, Any] = {}
        utterance_all_dev_summary: dict[str, Any] = {}
        for outcome in UTTERANCE_OUTCOMES:
            utterance_summary[outcome] = {}
            utterance_all_dev_summary[outcome] = {}
            for metric_name in ("qwk", "spearman", "mae", "icc_2_1"):
                values = np.asarray(
                    [
                        run["utterance"][outcome]["metrics"][metric_name]
                        for run in report["runs"]
                    ],
                    dtype=np.float64,
                )
                finite = values[np.isfinite(values)]
                utterance_summary[outcome][metric_name] = {
                    "mean": float(finite.mean()) if finite.size else float("nan"),
                    "sample_std": (
                        float(finite.std(ddof=1))
                        if finite.size > 1
                        else float("nan")
                    ),
                    "valid_seeds": int(finite.size),
                }
                audit_values = np.asarray(
                    [
                        run["utterance"][outcome]["all_dev_audit_metrics"][
                            metric_name
                        ]
                        for run in report["runs"]
                    ],
                    dtype=np.float64,
                )
                finite_audit = audit_values[np.isfinite(audit_values)]
                utterance_all_dev_summary[outcome][metric_name] = {
                    "mean": (
                        float(finite_audit.mean())
                        if finite_audit.size
                        else float("nan")
                    ),
                    "sample_std": (
                        float(finite_audit.std(ddof=1))
                        if finite_audit.size > 1
                        else float("nan")
                    ),
                    "valid_seeds": int(finite_audit.size),
                }
        smoke_gate = float(
            config["evaluation"]["smoke_gate_phone_macro_f1"]
        )
        engineering_gate = float(
            config["evaluation"]["engineering_gate_phone_macro_f1"]
        )
        report["summary"] = {
            "seeds_completed": [run["seed"] for run in report["runs"]],
            "phone_macro_f1_by_seed": {
                str(run["seed"]): run["phone"]["metrics"]["macro_f1"]
                for run in report["runs"]
            },
            "phone": {
                "macro_f1": {
                    "mean": float(phone_macro_f1_values.mean()),
                    "sample_std": float(phone_macro_f1_values.std(ddof=1)),
                },
                "auprc": {
                    "mean": float(phone_auprc_values.mean()),
                    "sample_std": float(phone_auprc_values.std(ddof=1)),
                },
                "smoke_gate": smoke_gate,
                "smoke_gate_passed_by_mean": (
                    float(phone_macro_f1_values.mean()) >= smoke_gate
                ),
                "engineering_gate": engineering_gate,
                "engineering_gate_passed_by_mean": (
                    float(phone_macro_f1_values.mean()) >= engineering_gate
                ),
                "metric_scope": "primary_dev",
                "all_dev_audit": {
                    "macro_f1": {
                        "mean": float(all_dev_phone_f1_values.mean()),
                        "sample_std": float(
                            all_dev_phone_f1_values.std(ddof=1)
                        ),
                    },
                    "auprc": {
                        "mean": float(all_dev_phone_auprc_values.mean()),
                        "sample_std": float(
                            all_dev_phone_auprc_values.std(ddof=1)
                        ),
                    },
                },
            },
            "utterance": utterance_summary,
            "utterance_all_dev_audit": utterance_all_dev_summary,
            "speaker_cluster_bootstrap_performed": False,
            "speaker_cluster_bootstrap_reason": (
                "This run is dev-only model selection. Cluster bootstrap and "
                "Holm-corrected final comparisons remain reserved for the "
                "single locked-test evaluation."
            ),
            "gate_assessment": {
                "scope": "development_provisional",
                "primary_population": config["data"][
                    "primary_analysis_scope"
                ],
                "all_dev_audit_excluded_from_gate": True,
                "experiment_status": config["experiment"]["status"],
                "promotion_decision_allowed": False,
                "locked_test_evidence_available": False,
                "interpretation": (
                    "Dev thresholds are engineering signals only. They do not "
                    "promote a model before manual G0 and the pre-authorized "
                    "single locked-test evaluation."
                ),
            },
            "results_are_development_only": True,
            "locked_test_remains_unread": True,
            "oracle_alignment": True,
        }
        write_json(temporary / "report.json", report)
        os.replace(temporary, args.output_dir)
        return report
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _registry_metric_value(value: object) -> float | int | bool | None:
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    number = float(value)
    return number if math.isfinite(number) else None


def run_registered_experiment(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], Path]:
    """Run the baseline and register all reproducibility evidence.

    Only the explicit feature caches, their report, and the experiment config
    are fingerprinted.  The modeling manifest and every ``.env`` file remain
    outside this runner.
    """

    config = load_config(args.config)
    validate_experiment_config(config)
    phone_shards = sorted(args.phone_cache.glob("part-*.parquet"))
    seeds = {
        f"experiment_seed_{seed}": int(seed)
        for seed in config["experiment"]["seeds"]
    }
    data_files = [
        args.utterance_cache,
        args.cache_report,
        *phone_shards,
    ]
    registry = experiment_run(
        args.registry_root,
        run_id=args.run_id,
        config_files=args.config,
        data_files=data_files,
        seeds=seeds,
        repo_path=REPOSITORY_ROOT,
    )
    with registry as active_run:
        report = train_explicit_baseline(args)
        registry_metadata_path = active_run.metadata_path
        try:
            recorded_registry_path = registry_metadata_path.resolve().relative_to(
                REPOSITORY_ROOT
            )
        except ValueError:
            recorded_registry_path = registry_metadata_path.resolve()
        report["experiment_registry"] = {
            "integrated": True,
            "t03_complete": False,
            "run_id": active_run.run_id,
            "metadata_path": recorded_registry_path.as_posix(),
            "status_at_report_write": "running",
            "input_roles": {
                "config": 1,
                "data": len(data_files),
                "model": 0,
                "checkpoint": 0,
            },
            "runtime_packages_gpu_git_recorded": True,
            "seeds_recorded": True,
            "metrics_recorded": False,
            "artifacts_recorded": False,
        }
        write_json(args.output_dir / "report.json", report)

        for run in report["runs"]:
            metric_values: dict[str, Any] = {
                "phone_macro_f1": _registry_metric_value(
                    run["phone"]["metrics"]["macro_f1"]
                ),
                "phone_auprc": _registry_metric_value(
                    run["phone"]["metrics"]["auprc"]
                ),
            }
            for outcome in UTTERANCE_OUTCOMES:
                for metric_name in ("qwk", "spearman", "mae", "icc_2_1"):
                    metric_values[f"{outcome}_{metric_name}"] = (
                        _registry_metric_value(
                            run["utterance"][outcome]["metrics"][metric_name]
                        )
                    )
            active_run.log_metrics(
                metric_values,
                step=int(run["seed"]),
                split="dev",
            )

            seed = int(run["seed"])
            artifact_paths = {
                f"seed_{seed}_checkpoint": run["artifacts"]["checkpoint"]["path"],
                f"seed_{seed}_phone_predictions": run["phone"]["predictions"][
                    "path"
                ],
                f"seed_{seed}_utterance_predictions": run["artifacts"][
                    "utterance_predictions"
                ]["path"],
                f"seed_{seed}_slice_metrics": run["artifacts"]["slice_metrics"][
                    "path"
                ],
            }
            for name, relative_path in artifact_paths.items():
                active_run.add_artifact(
                    name,
                    args.output_dir / relative_path,
                    kind=(
                        "checkpoint"
                        if name.endswith("_checkpoint")
                        else "evaluation"
                    ),
                )

        active_run.log_metrics(
            {
                "phone_macro_f1_mean": _registry_metric_value(
                    report["summary"]["phone"]["macro_f1"]["mean"]
                ),
                "phone_macro_f1_sample_std": _registry_metric_value(
                    report["summary"]["phone"]["macro_f1"]["sample_std"]
                ),
                "phone_auprc_mean": _registry_metric_value(
                    report["summary"]["phone"]["auprc"]["mean"]
                ),
                "phone_auprc_sample_std": _registry_metric_value(
                    report["summary"]["phone"]["auprc"]["sample_std"]
                ),
                "smoke_gate_passed_by_mean": report["summary"]["phone"][
                    "smoke_gate_passed_by_mean"
                ],
                "engineering_gate_passed_by_mean": report["summary"]["phone"][
                    "engineering_gate_passed_by_mean"
                ],
            },
            split="dev",
        )
        report["experiment_registry"].update(
            {
                "t03_complete": True,
                "metrics_recorded": True,
                "artifacts_recorded": True,
                "registered_artifact_count": len(report["runs"]) * 4 + 1,
            }
        )
        write_json(args.output_dir / "report.json", report)
        active_run.add_artifact(
            "final_report",
            args.output_dir / "report.json",
            kind="evaluation_report",
        )
    return report, registry.metadata_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train the explicit oracle-boundary baseline on train and evaluate "
            "model selection on dev; evaluation splits are forbidden."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/explicit_baseline_v1.yaml"),
    )
    parser.add_argument("--utterance-cache", type=Path, required=True)
    parser.add_argument("--phone-cache", type=Path, required=True)
    parser.add_argument("--cache-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--registry-root",
        type=Path,
        default=Path("artifacts/experiment_registry"),
    )
    parser.add_argument(
        "--run-id",
        help="Optional stable registry run ID; generated when omitted",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, registry_metadata_path = run_registered_experiment(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "experiment_id": report["experiment_id"],
                "seeds": report["summary"]["seeds_completed"],
                "development_only": True,
                "oracle_alignment": True,
                "report": str(args.output_dir / "report.json"),
                "registry_metadata": str(registry_metadata_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
