#!/usr/bin/env python3
"""Train simple heads over a completed frozen-WavLM feature cache.

Only the reviewed train/dev feature cache and the privacy-safe explicit
utterance cache are read.  Hyperparameters, early stopping, and the phone
decision threshold are selected exclusively on a deterministic speaker-hash
partition inside ``train``.  Each seed makes exactly one final inference pass
over ``dev``; stack, calibration, and locked-test inputs are rejected.
"""

# The deterministic cuBLAS environment guard intentionally precedes imports
# that can load torch.
# ruff: noqa: E402

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import shutil
import sys
from typing import Any, cast
from uuid import uuid4

# CUDA >= 10.2 requires this value to be present before CUDA runtime
# initialization when deterministic cuBLAS algorithms are requested.  The CLI
# is a fresh process, but the import-time guard also makes library use explicit.
CUBLAS_WORKSPACE_CONFIG = ":4096:8"
_torch_before_setup = sys.modules.get("torch")
_torch_cuda = getattr(_torch_before_setup, "cuda", None)
_torch_cuda_is_initialized = getattr(
    _torch_cuda,
    "is_initialized",
    lambda: False,
)
_CUDA_INITIALIZED_BEFORE_DETERMINISM_SETUP = bool(
    _torch_before_setup is not None and _torch_cuda_is_initialized()
)
_existing_cublas_workspace_config = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
if _existing_cublas_workspace_config not in (None, CUBLAS_WORKSPACE_CONFIG):
    raise RuntimeError(
        "CUBLAS_WORKSPACE_CONFIG must be :4096:8 before importing the "
        "frozen-head runner"
    )
os.environ["CUBLAS_WORKSPACE_CONFIG"] = CUBLAS_WORKSPACE_CONFIG

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import torch
import yaml  # type: ignore[import-untyped]
from sklearn.metrics import confusion_matrix  # type: ignore[import-untyped]

from voice_coach.evaluation import (
    auprc,
    icc_2_1,
    macro_f1,
    mae,
    quadratic_weighted_kappa,
    spearman_correlation,
)
from voice_coach.experiments import experiment_run
from voice_coach.models.wavlm_frozen_head import (
    FrozenPhoneHead,
    FrozenUtteranceHead,
)


REPORT_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_report.v1"
CHECKPOINT_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_checkpoint.v1"
PREDICTION_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_predictions.v1"
CACHE_REPORT_SCHEMA_VERSION = "voice_coach.wavlm_feature_cache_report.v1"
ALLOWED_SPLITS = frozenset({"train", "dev"})
FORBIDDEN_SPLITS = frozenset({"stack", "calibration", "locked_test"})
OUTCOMES = (
    "pronunciation_score",
    "fluency_score",
    "comprehensibility_score",
)
PHONE_INPUTS = ("embedding_mean", "target_label")
PHONE_LABELS = ("binary_error_label",)
UTTERANCE_INPUTS = ("embedding_mean_std",)
PROHIBITED_CACHE_COLUMNS = frozenset(
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
SHA256_LENGTH = 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TRAINER_SOURCE_PATH = Path(__file__).resolve()
HEAD_SOURCE_PATH = (
    REPOSITORY_ROOT / "src" / "voice_coach" / "models" / "wavlm_frozen_head.py"
)


@dataclass(frozen=True, slots=True)
class UtteranceLabel:
    speaker_id_sha256: str
    split: str
    l1: str
    gender: str
    proficiency: str
    scores: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class CacheContract:
    report_sha256: str
    manifest_sha256: str
    prompt_reservation_sha256: str
    builder_config_sha256: str
    builder_sha256: str
    model_config_sha256: str
    model_checkpoint_sha256: str
    paths: Mapping[str, tuple[Path, ...]]
    rows: Mapping[str, int]
    split_rows: Mapping[str, Mapping[str, int]]
    shard_fingerprints: Mapping[str, tuple[Mapping[str, Any], ...]]
    speaker_overlap_train_dev: int


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


def source_code_hashes() -> dict[str, str]:
    """Fingerprint the exact trainer and head implementation used by a run."""

    return {
        "trainer_source_sha256": sha256_file(TRAINER_SOURCE_PATH),
        "head_source_sha256": sha256_file(HEAD_SOURCE_PATH),
    }


def validate_deterministic_runtime(
    config: Mapping[str, Any],
    *,
    production_run: bool,
) -> dict[str, Any]:
    """Verify the allowlisted deterministic-CUDA process contract."""

    configured = config["runtime"]["cublas_workspace_config"]
    actual = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
    if configured != CUBLAS_WORKSPACE_CONFIG or actual != CUBLAS_WORKSPACE_CONFIG:
        raise RuntimeError("deterministic cuBLAS workspace configuration differs")
    if production_run and _CUDA_INITIALIZED_BEFORE_DETERMINISM_SETUP:
        raise RuntimeError(
            "CUDA was initialized before the deterministic cuBLAS contract"
        )
    return {
        "deterministic_algorithms": True,
        "cublas_workspace_config": CUBLAS_WORKSPACE_CONFIG,
        "configured_before_cuda_initialization": (
            not _CUDA_INITIALIZED_BEFORE_DETERMINISM_SETUP
        ),
        "fresh_process_contract_enforced": production_run,
    }


def is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    multiplier = 1 if sys.platform == "darwin" else 1_024
    return int(value * multiplier)


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


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
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


def load_config(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("experiment config must be a mapping")
    return document


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def validate_experiment_config(config: Mapping[str, Any]) -> None:
    experiment = _mapping(config.get("experiment"), "experiment")
    encoder = _mapping(config.get("encoder"), "encoder")
    data = _mapping(config.get("data"), "data")
    inputs = _mapping(config.get("inputs"), "inputs")
    heads = _mapping(config.get("heads"), "heads")
    optimization = _mapping(config.get("optimization"), "optimization")
    evaluation = _mapping(config.get("evaluation"), "evaluation")
    runtime = _mapping(config.get("runtime"), "runtime")
    outputs = _mapping(config.get("outputs"), "outputs")

    if experiment.get("seeds") != [42, 43, 44]:
        raise ValueError("seeds must be exactly [42, 43, 44]")
    if experiment.get("development_only") is not True:
        raise ValueError("the frozen-head experiment must be development-only")
    if not isinstance(encoder.get("id"), str) or not encoder["id"]:
        raise ValueError("encoder.id must be a non-empty string")
    variant = encoder.get("variant")
    if not isinstance(variant, str):
        raise ValueError("encoder.variant must be base_plus or large")
    expected_model_suffix = {
        "base_plus": "wavlm-base-plus",
        "large": "wavlm-large",
    }.get(variant)
    if expected_model_suffix is None:
        raise ValueError("encoder.variant must be base_plus or large")
    if not str(encoder["id"]).endswith(expected_model_suffix):
        raise ValueError("encoder.variant does not match encoder.id")
    if not isinstance(encoder.get("revision"), str) or not encoder["revision"]:
        raise ValueError("encoder.revision must be pinned")
    hidden_size = _positive_int(encoder.get("hidden_size"), "encoder.hidden_size")
    if encoder.get("utterance_embedding_size") != hidden_size * 2:
        raise ValueError("utterance embedding size must be twice hidden_size")
    if encoder.get("frozen") is not True:
        raise ValueError("encoder must be frozen")
    for key in ("source_config_sha256", "source_checkpoint_sha256"):
        if not is_sha256(encoder.get(key)):
            raise ValueError(f"encoder.{key} must be a lowercase SHA-256")

    if set(data.get("training_splits", [])) != {"train"}:
        raise ValueError("training_splits must contain only train")
    if set(data.get("development_splits", [])) != {"dev"}:
        raise ValueError("development_splits must contain only dev")
    if not FORBIDDEN_SPLITS.issubset(set(data.get("forbidden_metric_splits", []))):
        raise ValueError("all protected evaluation splits must be forbidden")
    if data.get("primary_analysis_scope") != {
        "l1": ["Chinese", "Japanese"],
        "gender": ["F", "M"],
    }:
        raise ValueError("primary scope must be Chinese/Japanese and F/M")
    for key in (
        "modeling_manifest_sha256",
        "cache_builder_config_sha256",
        "cache_builder_sha256",
        "explicit_utterance_labels_sha256",
    ):
        if not is_sha256(data.get(key)):
            raise ValueError(f"data.{key} must be a lowercase SHA-256")
    _positive_int(data.get("expected_source_rows"), "data.expected_source_rows")
    expected_rows = _mapping(
        data.get("expected_output_rows"),
        "data.expected_output_rows",
    )
    for kind in ("utterance", "phone", "word"):
        _positive_int(expected_rows.get(kind), f"expected_output_rows.{kind}")
    inner = _mapping(data.get("inner_validation"), "data.inner_validation")
    modulus = _positive_int(inner.get("modulus"), "inner_validation.modulus")
    buckets = inner.get("validation_buckets")
    if (
        inner.get("source") != "train_speaker_sha256"
        or not isinstance(buckets, list)
        or not buckets
        or len(set(buckets)) != len(buckets)
        or any(
            isinstance(bucket, bool)
            or not isinstance(bucket, int)
            or not 0 <= bucket < modulus
            for bucket in buckets
        )
    ):
        raise ValueError("inner validation must be a valid speaker-hash partition")
    if len(buckets) == modulus:
        raise ValueError("inner validation cannot consume every train speaker")

    phone_inputs = _mapping(inputs.get("phone"), "inputs.phone")
    utterance_inputs = _mapping(inputs.get("utterance"), "inputs.utterance")
    if phone_inputs.get("feature_columns") != list(PHONE_INPUTS):
        raise ValueError("phone inputs must be embedding_mean and target_label")
    if phone_inputs.get("label_columns") != list(PHONE_LABELS):
        raise ValueError("binary_error_label must be the sole phone label")
    if utterance_inputs.get("feature_columns") != list(UTTERANCE_INPUTS):
        raise ValueError("embedding_mean_std must be the sole utterance input")
    if utterance_inputs.get("label_source") != (
        "explicit_privacy_safe_utterance_cache"
    ):
        raise ValueError("utterance labels must come from the explicit safe cache")
    if utterance_inputs.get("label_columns") != list(OUTCOMES):
        raise ValueError("utterance outcome roles differ from the fixed contract")
    prohibited = set(inputs.get("prohibited_as_input", []))
    if not {
        "binary_error_label",
        *OUTCOMES,
        "realized_phone_identity",
        "alignment_operation",
        "error_tag",
        "raw_utterance_id",
        "audio_path",
    }.issubset(prohibited):
        raise ValueError("prohibited input roles are incomplete")
    if prohibited & set(PHONE_INPUTS + UTTERANCE_INPUTS):
        raise ValueError("an approved feature is also marked prohibited")

    phone_head = _mapping(heads.get("phone"), "heads.phone")
    utterance_head = _mapping(heads.get("utterance"), "heads.utterance")
    if phone_head.get("family") != "embedding_target_mlp":
        raise ValueError("phone head family differs from the fixed head")
    if utterance_head.get("family") != "bounded_multitask_mlp":
        raise ValueError("utterance head family differs from the fixed head")
    for name, value in (
        ("phone.target_embedding_dim", phone_head.get("target_embedding_dim")),
        ("phone.hidden_dim", phone_head.get("hidden_dim")),
        ("utterance.hidden_dim", utterance_head.get("hidden_dim")),
    ):
        _positive_int(value, name)
    for name, value in (
        ("phone.dropout", phone_head.get("dropout")),
        ("utterance.dropout", utterance_head.get("dropout")),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= float(value) < 1
        ):
            raise ValueError(f"{name} must be in [0, 1)")
    if utterance_head.get("score_range") != [1, 5]:
        raise ValueError("utterance score range must be [1, 5]")

    if optimization.get("optimizer") != "AdamW":
        raise ValueError("optimizer must be CUDA AdamW")
    for name in (
        "phone_batch_size",
        "utterance_batch_size",
        "parquet_batch_rows",
        "phone_max_epochs",
        "utterance_max_epochs",
        "minimum_epochs",
        "early_stopping_patience",
    ):
        _positive_int(optimization.get(name), f"optimization.{name}")
    for name in ("learning_rate", "weight_decay", "gradient_clip_norm"):
        value = optimization.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or float(value) < 0
        ):
            raise ValueError(f"optimization.{name} must be non-negative")
    if float(optimization["learning_rate"]) == 0:
        raise ValueError("learning rate must be positive")
    if optimization["minimum_epochs"] > min(
        optimization["phone_max_epochs"],
        optimization["utterance_max_epochs"],
    ):
        raise ValueError("minimum_epochs exceeds a task maximum")
    threshold_grid(config)

    if (
        evaluation.get("development_only") is not True
        or evaluation.get("dev_inference_passes_per_seed") != 1
        or evaluation.get("locked_test_evaluation_allowed") is not False
    ):
        raise ValueError("dev must be evaluated once and locked test disabled")
    if evaluation.get("phone_metrics") != ["macro_f1", "auprc"]:
        raise ValueError("phone metric contract differs")
    if evaluation.get("utterance_metrics") != [
        "qwk",
        "spearman",
        "mae",
        "icc_2_1",
    ]:
        raise ValueError("utterance metric contract differs")
    streaming = _mapping(runtime.get("streaming"), "runtime.streaming")
    if (
        runtime.get("require_cuda") is not True
        or runtime.get("deterministic_algorithms") is not True
        or runtime.get("cublas_workspace_config") != CUBLAS_WORKSPACE_CONFIG
        or runtime.get("environment_files_forbidden") is not True
        or streaming.get("materialize_full_embedding_matrix") is not False
        or streaming.get("embedding_storage_dtype") != "float16"
        or streaming.get("training_compute_dtype") != "float32"
        or streaming.get("maximum_parquet_batch_rows")
        != optimization.get("parquet_batch_rows")
    ):
        raise ValueError("CUDA/streaming/environment-file policy differs")
    if outputs.get("atomic_publish") is not True:
        raise ValueError("experiment outputs must be published atomically")


def threshold_grid(config: Mapping[str, Any]) -> np.ndarray:
    grid = _mapping(
        config["optimization"].get("phone_threshold_grid"),
        "phone_threshold_grid",
    )
    start = float(grid["start"])
    stop = float(grid["stop"])
    step = float(grid["step"])
    if not 0 < start <= stop < 1 or step <= 0:
        raise ValueError("phone threshold grid is invalid")
    values = np.arange(start, stop + step / 2, step, dtype=np.float64)
    if values.size == 0 or values[-1] > 1:
        raise ValueError("phone threshold grid is empty or out of range")
    return values


def _metadata_text(metadata: Mapping[bytes, bytes] | None, key: str) -> str:
    encoded = key.encode("utf-8")
    if not metadata or encoded not in metadata:
        raise ValueError(f"Parquet metadata is missing {key}")
    return metadata[encoded].decode("utf-8")


def _metadata_json_list(
    metadata: Mapping[bytes, bytes] | None,
    key: str,
) -> list[str]:
    try:
        value = json.loads(_metadata_text(metadata, key))
    except json.JSONDecodeError as error:
        raise ValueError(f"Parquet metadata {key} is invalid JSON") from error
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Parquet metadata {key} must be a string list")
    return value


def _expected_columns(kind: str) -> set[str]:
    common = {
        "schema_version",
        "oracle_alignment",
        "utterance_id_sha256",
        "speaker_id_sha256",
        "split",
        "prompt_sha256",
        "l1",
        "gender",
        "proficiency",
    }
    if kind == "utterance":
        return common | {
            "audio_duration_seconds",
            "chunk_count",
            "frame_count",
            "embedding_mean_std",
        }
    if kind == "phone":
        return common | {
            "phone_index",
            "target_label",
            "oracle_start_seconds",
            "oracle_end_seconds",
            "pooled_frame_count",
            "embedding_mean",
            "binary_error_label",
        }
    if kind == "word":
        return common | {
            "word_index",
            "oracle_start_seconds",
            "oracle_end_seconds",
            "pooled_frame_count",
            "embedding_mean",
        }
    raise ValueError(f"unknown cache kind: {kind}")


def _validate_embedding_field(
    schema: pa.Schema,
    *,
    name: str,
    dimensions: int,
) -> None:
    field = schema.field(name)
    if (
        not pa.types.is_fixed_size_list(field.type)
        or field.type.list_size != dimensions
        or not pa.types.is_float16(field.type.value_type)
        or field.nullable
    ):
        raise ValueError(
            f"{name} must be a non-null float16 fixed-size list[{dimensions}]"
        )


def _validate_cache_schema(
    schema: pa.Schema,
    *,
    kind: str,
    config: Mapping[str, Any],
) -> None:
    expected = _expected_columns(kind)
    actual = set(schema.names)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"{kind} cache schema differs; missing={missing}, extra={extra}"
        )
    prohibited = PROHIBITED_CACHE_COLUMNS & actual
    if prohibited:
        raise ValueError(
            f"{kind} cache exposes prohibited columns: {sorted(prohibited)}"
        )
    hidden_size = int(config["encoder"]["hidden_size"])
    if kind == "utterance":
        _validate_embedding_field(
            schema,
            name="embedding_mean_std",
            dimensions=hidden_size * 2,
        )
    else:
        _validate_embedding_field(
            schema,
            name="embedding_mean",
            dimensions=hidden_size,
        )
    metadata = schema.metadata
    expected_metadata = {
        "voice_coach.model_id": str(config["encoder"]["id"]),
        "voice_coach.model_revision": str(config["encoder"]["revision"]),
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
        "voice_coach.source_manifest_sha256": str(
            config["data"]["modeling_manifest_sha256"]
        ),
        "voice_coach.config_sha256": str(config["data"]["cache_builder_config_sha256"]),
    }
    for key, expected_value in expected_metadata.items():
        if _metadata_text(metadata, key) != expected_value:
            raise ValueError(f"{kind} cache metadata {key} differs")
    expected_inputs = {
        "utterance": ["embedding_mean_std"],
        "phone": ["embedding_mean", "target_label"],
        "word": ["embedding_mean"],
    }[kind]
    expected_labels = {
        "utterance": [],
        "phone": ["binary_error_label"],
        "word": [],
    }[kind]
    if (
        _metadata_json_list(metadata, "voice_coach.input_feature_columns")
        != expected_inputs
    ):
        raise ValueError(f"{kind} input-role metadata differs")
    if _metadata_json_list(metadata, "voice_coach.label_columns") != expected_labels:
        raise ValueError(f"{kind} label-role metadata differs")
    forbidden_inputs = set(
        _metadata_json_list(metadata, "voice_coach.forbidden_as_input")
    )
    if kind == "phone" and "binary_error_label" not in forbidden_inputs:
        raise ValueError("phone label is not explicitly forbidden as input")


def _validation_columns(kind: str) -> list[str]:
    columns = [
        "oracle_alignment",
        "utterance_id_sha256",
        "speaker_id_sha256",
        "split",
    ]
    if kind == "phone":
        columns.append("binary_error_label")
    return columns


def _validate_hash_values(values: Sequence[object], field: str) -> None:
    if any(not is_sha256(value) for value in values):
        raise ValueError(f"{field} contains a non-SHA-256 value")


def validate_full_feature_cache(
    *,
    config: Mapping[str, Any],
    cache_root: Path,
    cache_report_path: Path,
) -> CacheContract:
    """Validate the complete three-kind cache before any head is initialized."""

    report = json.loads(cache_report_path.read_text(encoding="utf-8"))
    if report.get("schema_version") != CACHE_REPORT_SCHEMA_VERSION:
        raise ValueError("feature cache report schema version differs")
    if report.get("status") != "completed":
        raise ValueError("feature cache report is not completed")
    source = _mapping(report.get("source"), "cache report source")
    model = _mapping(report.get("model"), "cache report model")
    runtime = _mapping(report.get("runtime"), "cache report runtime")
    policy = _mapping(report.get("policy"), "cache report policy")
    configuration = _mapping(
        report.get("configuration"),
        "cache report configuration",
    )
    progress = _mapping(report.get("progress"), "cache report progress")
    outputs = _mapping(report.get("outputs"), "cache report outputs")
    privacy_audit = _mapping(
        report.get("privacy_audit"),
        "cache report privacy_audit",
    )
    data_config = config["data"]
    encoder_config = config["encoder"]

    expected_source = {
        "modeling_manifest_sha256": data_config["modeling_manifest_sha256"],
        "experiment_config_sha256": data_config["cache_builder_config_sha256"],
        "builder_sha256": data_config["cache_builder_sha256"],
    }
    for key, expected in expected_source.items():
        if source.get(key) != expected:
            raise ValueError(f"cache source {key} differs from training config")
    if not is_sha256(source.get("prompt_reservation_sha256")):
        raise ValueError("cache prompt reservation hash is invalid")
    expected_model = {
        "id": encoder_config["id"],
        "revision": encoder_config["revision"],
        "hidden_size": encoder_config["hidden_size"],
        "config_sha256": encoder_config["source_config_sha256"],
        "checkpoint_sha256": encoder_config["source_checkpoint_sha256"],
        "frozen": True,
        "local_files_only": True,
    }
    for key, expected in expected_model.items():
        if model.get(key) != expected:
            raise ValueError(f"cache model {key} differs from the pin")
    if runtime.get("storage_precision") != "float16":
        raise ValueError("cache storage precision is not float16")
    if runtime.get("oom_count") != 0:
        raise ValueError("cache build reported a CUDA OOM")
    expected_policy = {
        "allowed_splits": ["train", "dev"],
        "evaluation_splits": ["calibration", "locked_test", "stack"],
        "evaluation_split_metric_access": "forbidden",
        "holistic_score_columns_read": [],
        "metric_evaluation_performed": False,
        "rows_with_qc_flags_excluded": True,
        "reserved_prompts_excluded_from_every_split": True,
        "oracle_alignment": True,
        "contains_direct_identifiers": False,
        "contains_transcript_text": False,
        "contains_audio_bytes": False,
        "contains_realized_phone_identity": False,
        "contains_alignment_operation": False,
        "access_scope": "restricted_internal",
        "shareable": False,
    }
    for key, expected in expected_policy.items():
        if policy.get(key) != expected:
            raise ValueError(f"cache policy {key} differs")
    roles = _mapping(policy.get("column_roles"), "cache column roles")
    expected_roles = {
        "utterance": {
            "input_feature_columns": ["embedding_mean_std"],
            "label_columns": [],
        },
        "phone": {
            "input_feature_columns": ["embedding_mean", "target_label"],
            "label_columns": ["binary_error_label"],
        },
        "word": {
            "input_feature_columns": ["embedding_mean"],
            "label_columns": [],
        },
    }
    if roles != expected_roles:
        raise ValueError("cache report column-role contract differs")
    if (
        configuration.get("full_run") is not True
        or configuration.get("limit") is not None
    ):
        raise ValueError("limited/smoke WavLM caches cannot train a head")
    if not _mapping(
        configuration.get("full_run_gate"),
        "cache full_run_gate",
    ):
        raise ValueError("full cache has no recorded approval gate")
    if progress.get("source_scan_complete") is not True:
        raise ValueError("partial source scans cannot train a head")
    if progress.get("source_rows_scanned") != data_config["expected_source_rows"]:
        raise ValueError("cache did not scan the expected complete source")
    privacy_expectations = {
        "raw_locator_values_exported": False,
        "raw_file_stem_values_exported": False,
        "realized_phone_values_exported": False,
        "alignment_operation_values_exported": False,
        "word_transcript_values_exported": False,
        "phone_binary_target_is_separate_label_role": True,
    }
    for key, expected in privacy_expectations.items():
        if privacy_audit.get(key) is not expected:
            raise ValueError(f"cache privacy audit {key} differs")

    expected_output_rows = data_config["expected_output_rows"]
    paths: dict[str, tuple[Path, ...]] = {}
    rows: dict[str, int] = {}
    split_rows: dict[str, Mapping[str, int]] = {}
    fingerprints: dict[str, tuple[Mapping[str, Any], ...]] = {}
    train_speakers: set[str] = set()
    dev_speakers: set[str] = set()
    for kind in ("utterance", "phone", "word"):
        output = _mapping(outputs.get(kind), f"cache output {kind}")
        reported_shards = output.get("shards")
        if not isinstance(reported_shards, list) or not reported_shards:
            raise ValueError(f"cache output {kind} has no shards")
        directory = cache_root / kind
        actual_paths = sorted(directory.glob("part-*.parquet"))
        expected_names: list[str] = []
        for item in reported_shards:
            if not isinstance(item, Mapping):
                raise ValueError(f"cache output {kind} has an invalid shard entry")
            name = item.get("name")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not name.startswith("part-")
                or not name.endswith(".parquet")
            ):
                raise ValueError(f"cache output {kind} has an unsafe shard name")
            expected_names.append(name)
        if [path.name for path in actual_paths] != sorted(expected_names):
            raise ValueError(f"cache output {kind} shard set differs from report")
        report_by_name = {
            str(item["name"]): item
            for item in reported_shards
            if isinstance(item, Mapping)
        }
        kind_rows = 0
        kind_splits: Counter[str] = Counter()
        kind_fingerprints: list[Mapping[str, Any]] = []
        for path in actual_paths:
            item = report_by_name[path.name]
            digest = sha256_file(path)
            if digest != item.get("sha256"):
                raise ValueError(f"{kind} shard hash differs: {path.name}")
            parquet = pq.ParquetFile(path)
            if parquet.metadata.num_rows != item.get("row_count"):
                raise ValueError(f"{kind} shard row count differs: {path.name}")
            if parquet.metadata.num_row_groups != item.get("row_group_count"):
                raise ValueError(f"{kind} shard row-group count differs: {path.name}")
            if path.stat().st_size != item.get("byte_count"):
                raise ValueError(f"{kind} shard byte count differs: {path.name}")
            _validate_cache_schema(
                parquet.schema_arrow,
                kind=kind,
                config=config,
            )
            for batch in parquet.iter_batches(
                batch_size=65_536,
                columns=_validation_columns(kind),
            ):
                split_values = _batch_column(batch, "split").to_pylist()
                observed_splits = {str(value) for value in split_values}
                if observed_splits - ALLOWED_SPLITS:
                    raise ValueError(f"{kind} cache contains a forbidden/unknown split")
                kind_splits.update(str(value) for value in split_values)
                utterance_ids = _batch_column(batch, "utterance_id_sha256").to_pylist()
                speaker_ids = _batch_column(batch, "speaker_id_sha256").to_pylist()
                _validate_hash_values(utterance_ids, "utterance_id_sha256")
                _validate_hash_values(speaker_ids, "speaker_id_sha256")
                oracle = _batch_column(batch, "oracle_alignment").to_pylist()
                if any(value is not True for value in oracle):
                    raise ValueError(f"{kind} cache has non-oracle rows")
                if kind == "utterance":
                    for split, speaker in zip(
                        split_values,
                        speaker_ids,
                        strict=True,
                    ):
                        (train_speakers if split == "train" else dev_speakers).add(
                            str(speaker)
                        )
                if kind == "phone":
                    labels = _batch_column(batch, "binary_error_label").to_pylist()
                    if any(value not in (0, 1) for value in labels):
                        raise ValueError("phone cache has a non-binary label")
            kind_rows += parquet.metadata.num_rows
            kind_fingerprints.append(
                {
                    "name": path.name,
                    "row_count": parquet.metadata.num_rows,
                    "sha256": digest,
                }
            )
        if set(kind_splits) != ALLOWED_SPLITS:
            raise ValueError(f"{kind} cache must contain train and dev")
        if kind_rows != output.get("row_count"):
            raise ValueError(f"{kind} total rows differ from cache report")
        if kind_rows != expected_output_rows[kind]:
            raise ValueError(f"{kind} rows differ from the predeclared full count")
        if output.get("shard_count") != len(actual_paths):
            raise ValueError(f"{kind} shard count differs from cache report")
        if output.get("byte_count") != sum(
            path.stat().st_size for path in actual_paths
        ):
            raise ValueError(f"{kind} byte total differs from cache report")
        if output.get("content_sha256") != sha256_json(kind_fingerprints):
            raise ValueError(f"{kind} content hash differs from cache report")
        progress_key = f"{kind}_rows_written"
        if progress.get(progress_key) != kind_rows:
            raise ValueError(f"cache progress {progress_key} differs")
        paths[kind] = tuple(actual_paths)
        rows[kind] = kind_rows
        split_rows[kind] = dict(sorted(kind_splits.items()))
        fingerprints[kind] = tuple(kind_fingerprints)
    overlap = train_speakers & dev_speakers
    if overlap:
        raise ValueError("speaker leakage exists between train and dev")
    return CacheContract(
        report_sha256=sha256_file(cache_report_path),
        manifest_sha256=str(source["modeling_manifest_sha256"]),
        prompt_reservation_sha256=str(source["prompt_reservation_sha256"]),
        builder_config_sha256=str(source["experiment_config_sha256"]),
        builder_sha256=str(source["builder_sha256"]),
        model_config_sha256=str(model["config_sha256"]),
        model_checkpoint_sha256=str(model["checkpoint_sha256"]),
        paths=paths,
        rows=rows,
        split_rows=split_rows,
        shard_fingerprints=fingerprints,
        speaker_overlap_train_dev=0,
    )


def load_explicit_utterance_labels(
    *,
    config: Mapping[str, Any],
    path: Path,
) -> tuple[dict[str, UtteranceLabel], dict[str, Any]]:
    """Load only pseudonymous join keys, slices, and three outcome labels."""

    expected_sha256 = config["data"]["explicit_utterance_labels_sha256"]
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise ValueError("explicit utterance label cache hash differs")
    parquet = pq.ParquetFile(path)
    if parquet.metadata.num_rows != config["data"]["expected_output_rows"]["utterance"]:
        raise ValueError("explicit label cache row count differs")
    schema = parquet.schema_arrow
    required = {
        "utterance_id_sha256",
        "speaker_id_sha256",
        "split",
        "l1",
        "gender",
        "proficiency",
        *OUTCOMES,
    }
    missing = sorted(required - set(schema.names))
    if missing:
        raise ValueError(
            "explicit label cache is missing columns: " + ", ".join(missing)
        )
    metadata = schema.metadata
    expected_metadata = {
        "voice_coach.oracle_alignment": "true",
        "voice_coach.privacy_safe": "true",
        "voice_coach.allowed_splits": "train,dev",
        "voice_coach.metric_evaluation_performed": "false",
        "voice_coach.source_manifest_sha256": str(
            config["data"]["modeling_manifest_sha256"]
        ),
    }
    for key, expected in expected_metadata.items():
        if _metadata_text(metadata, key) != expected:
            raise ValueError(f"explicit label cache metadata {key} differs")
    if _metadata_json_list(metadata, "voice_coach.label_columns") != list(OUTCOMES):
        raise ValueError("explicit cache outcome role metadata differs")
    approved_inputs = set(
        _metadata_json_list(metadata, "voice_coach.input_feature_columns")
    )
    if set(OUTCOMES) & approved_inputs:
        raise ValueError("explicit outcome labels are incorrectly marked as inputs")
    forbidden = set(_metadata_json_list(metadata, "voice_coach.forbidden_as_input"))
    if not set(OUTCOMES).issubset(forbidden):
        raise ValueError("explicit outcomes are not forbidden as inputs")

    columns = [
        "utterance_id_sha256",
        "speaker_id_sha256",
        "split",
        "l1",
        "gender",
        "proficiency",
        *OUTCOMES,
    ]
    labels: dict[str, UtteranceLabel] = {}
    split_counts: Counter[str] = Counter()
    missing_by_outcome: Counter[str] = Counter()
    for batch in parquet.iter_batches(batch_size=16_384, columns=columns):
        rows = batch.to_pylist()
        for row in rows:
            utterance_id = row["utterance_id_sha256"]
            speaker_id = row["speaker_id_sha256"]
            if not is_sha256(utterance_id) or not is_sha256(speaker_id):
                raise ValueError("explicit cache join keys are not SHA-256 values")
            split = str(row["split"])
            if split not in ALLOWED_SPLITS:
                raise ValueError("explicit cache contains a forbidden split")
            scores: list[float] = []
            for outcome in OUTCOMES:
                value = row[outcome]
                if value is None:
                    scores.append(float("nan"))
                    missing_by_outcome[outcome] += 1
                    continue
                score = float(value)
                if score not in (1, 2, 3, 4, 5):
                    raise ValueError(f"{outcome} lies outside ordinal range 1-5")
                scores.append(score)
            if utterance_id in labels:
                raise ValueError("explicit cache has a duplicate utterance join key")
            labels[str(utterance_id)] = UtteranceLabel(
                speaker_id_sha256=str(speaker_id),
                split=split,
                l1="" if row["l1"] is None else str(row["l1"]),
                gender="" if row["gender"] is None else str(row["gender"]),
                proficiency=(
                    "" if row["proficiency"] is None else str(row["proficiency"])
                ),
                scores=tuple(scores),  # type: ignore[arg-type]
            )
            split_counts[split] += 1
    if set(split_counts) != ALLOWED_SPLITS:
        raise ValueError("explicit cache must contain train and dev")
    return labels, {
        "sha256": actual_sha256,
        "row_count": len(labels),
        "split_rows": dict(sorted(split_counts.items())),
        "missing_labels": {
            outcome: int(missing_by_outcome[outcome]) for outcome in OUTCOMES
        },
        "selected_columns": columns,
        "raw_manifest_read": False,
        "path_columns_read": [],
        "input_feature_columns_read": [],
        "label_columns_read": list(OUTCOMES),
    }


def speaker_hash_bucket(speaker_id_sha256: str, modulus: int) -> int:
    if not is_sha256(speaker_id_sha256):
        raise ValueError("speaker ID must be a lowercase SHA-256")
    if modulus <= 0:
        raise ValueError("speaker hash modulus must be positive")
    return int(speaker_id_sha256[:16], 16) % modulus


def _is_primary_scope(
    l1: object,
    gender: object,
    config: Mapping[str, Any],
) -> bool:
    scope = config["data"]["primary_analysis_scope"]
    return str(l1) in scope["l1"] and str(gender) in scope["gender"]


def _partition_for_row(
    *,
    split: str,
    speaker_id_sha256: str,
    config: Mapping[str, Any],
) -> str:
    if split == "dev":
        return "dev"
    if split != "train":
        raise ValueError("a forbidden split reached the partitioner")
    inner = config["data"]["inner_validation"]
    bucket = speaker_hash_bucket(
        speaker_id_sha256,
        int(inner["modulus"]),
    )
    return "inner_val" if bucket in inner["validation_buckets"] else "train_fit"


def _parquet_batches(
    paths: Sequence[Path],
    *,
    columns: Sequence[str],
    batch_rows: int,
) -> Iterator[pa.RecordBatch]:
    for path in paths:
        parquet = pq.ParquetFile(path)
        yield from parquet.iter_batches(
            batch_size=batch_rows,
            columns=list(columns),
        )


def _batch_column(batch: pa.RecordBatch, name: str) -> pa.Array:
    index = batch.schema.get_field_index(name)
    if index < 0:
        raise ValueError(f"record batch is missing column {name}")
    return batch.column(index)


def scan_training_metadata(
    *,
    config: Mapping[str, Any],
    cache_contract: CacheContract,
    utterance_labels: Mapping[str, UtteranceLabel],
) -> dict[str, Any]:
    """Audit joins and derive train-only statistics without loading embeddings."""

    phone_counts: Counter[str] = Counter()
    phone_class_counts: dict[str, Counter[int]] = {
        "train_fit": Counter(),
        "inner_val": Counter(),
        "dev": Counter(),
    }
    target_vocabulary: set[str] = set()
    primary_phone_speakers: dict[str, set[str]] = {
        "train_fit": set(),
        "inner_val": set(),
        "dev": set(),
    }
    for batch in _parquet_batches(
        cache_contract.paths["phone"],
        columns=[
            "split",
            "speaker_id_sha256",
            "l1",
            "gender",
            "target_label",
            "binary_error_label",
        ],
        batch_rows=int(config["optimization"]["parquet_batch_rows"]),
    ):
        rows = batch.to_pylist()
        for row in rows:
            if not _is_primary_scope(row["l1"], row["gender"], config):
                phone_counts["outside_primary"] += 1
                continue
            partition = _partition_for_row(
                split=str(row["split"]),
                speaker_id_sha256=str(row["speaker_id_sha256"]),
                config=config,
            )
            binary_label = int(row["binary_error_label"])
            if binary_label not in (0, 1):
                raise ValueError("phone label lies outside {0, 1}")
            phone_counts[partition] += 1
            phone_class_counts[partition][binary_label] += 1
            primary_phone_speakers[partition].add(str(row["speaker_id_sha256"]))
            if partition == "train_fit":
                target_vocabulary.add(str(row["target_label"]))
    for partition in ("train_fit", "inner_val", "dev"):
        if phone_counts[partition] == 0:
            raise ValueError(f"phone partition is empty: {partition}")
        if set(phone_class_counts[partition]) != {0, 1}:
            raise ValueError(f"phone partition lacks both binary classes: {partition}")
    if primary_phone_speakers["train_fit"] & primary_phone_speakers["inner_val"]:
        raise ValueError("phone speakers cross train-fit/inner-validation")
    if (
        primary_phone_speakers["train_fit"] | primary_phone_speakers["inner_val"]
    ) & primary_phone_speakers["dev"]:
        raise ValueError("phone train speakers overlap dev")

    utterance_counts: Counter[str] = Counter()
    observed_scores: dict[str, Counter[str]] = {
        partition: Counter() for partition in ("train_fit", "inner_val", "dev")
    }
    primary_utterance_speakers: dict[str, set[str]] = {
        "train_fit": set(),
        "inner_val": set(),
        "dev": set(),
    }
    remaining_label_ids = set(utterance_labels)
    for batch in _parquet_batches(
        cache_contract.paths["utterance"],
        columns=[
            "utterance_id_sha256",
            "speaker_id_sha256",
            "split",
            "l1",
            "gender",
            "proficiency",
        ],
        batch_rows=int(config["optimization"]["parquet_batch_rows"]),
    ):
        rows = batch.to_pylist()
        for row in rows:
            identifier = str(row["utterance_id_sha256"])
            utterance_label = utterance_labels.get(identifier)
            if utterance_label is None:
                raise ValueError("WavLM utterance has no explicit label-cache join")
            if (
                utterance_label.speaker_id_sha256 != row["speaker_id_sha256"]
                or utterance_label.split != row["split"]
                or utterance_label.l1 != ("" if row["l1"] is None else str(row["l1"]))
                or utterance_label.gender
                != ("" if row["gender"] is None else str(row["gender"]))
                or utterance_label.proficiency
                != ("" if row["proficiency"] is None else str(row["proficiency"]))
            ):
                raise ValueError("WavLM/explicit cache join metadata differs")
            remaining_label_ids.discard(identifier)
            if not _is_primary_scope(row["l1"], row["gender"], config):
                utterance_counts["outside_primary"] += 1
                continue
            partition = _partition_for_row(
                split=str(row["split"]),
                speaker_id_sha256=str(row["speaker_id_sha256"]),
                config=config,
            )
            utterance_counts[partition] += 1
            primary_utterance_speakers[partition].add(str(row["speaker_id_sha256"]))
            for outcome, score in zip(
                OUTCOMES,
                utterance_label.scores,
                strict=True,
            ):
                if math.isfinite(score):
                    observed_scores[partition][outcome] += 1
    if remaining_label_ids:
        raise ValueError("explicit label cache contains IDs absent from WavLM cache")
    for partition in ("train_fit", "inner_val", "dev"):
        if utterance_counts[partition] == 0:
            raise ValueError(f"utterance partition is empty: {partition}")
        for outcome in OUTCOMES:
            if observed_scores[partition][outcome] < 2:
                raise ValueError(f"{partition} has fewer than two {outcome} labels")
    if (
        primary_utterance_speakers["train_fit"]
        & primary_utterance_speakers["inner_val"]
    ):
        raise ValueError("utterance speakers cross train-fit/inner-validation")
    if (
        primary_utterance_speakers["train_fit"]
        | primary_utterance_speakers["inner_val"]
    ) & primary_utterance_speakers["dev"]:
        raise ValueError("utterance train speakers overlap dev")
    vocabulary = ["<UNK>", *sorted(target_vocabulary)]
    if len(vocabulary) <= 1:
        raise ValueError("train-fit target phone vocabulary is empty")
    return {
        "inner_validation": {
            "source": "train_speaker_sha256",
            "modulus": int(config["data"]["inner_validation"]["modulus"]),
            "validation_buckets": list(
                config["data"]["inner_validation"]["validation_buckets"]
            ),
            "speaker_disjoint": True,
        },
        "phone": {
            "rows": {
                key: int(phone_counts[key])
                for key in (
                    "train_fit",
                    "inner_val",
                    "dev",
                    "outside_primary",
                )
            },
            "class_rows": {
                partition: {
                    str(label): int(count)
                    for label, count in sorted(phone_class_counts[partition].items())
                }
                for partition in ("train_fit", "inner_val", "dev")
            },
            "speaker_counts": {
                partition: len(primary_phone_speakers[partition])
                for partition in ("train_fit", "inner_val", "dev")
            },
            "target_vocabulary": vocabulary,
            "target_vocabulary_source": "primary_train_fit_only",
        },
        "utterance": {
            "rows": {
                key: int(utterance_counts[key])
                for key in (
                    "train_fit",
                    "inner_val",
                    "dev",
                    "outside_primary",
                )
            },
            "observed_score_rows": {
                partition: {
                    outcome: int(observed_scores[partition][outcome])
                    for outcome in OUTCOMES
                }
                for partition in ("train_fit", "inner_val", "dev")
            },
            "speaker_counts": {
                partition: len(primary_utterance_speakers[partition])
                for partition in ("train_fit", "inner_val", "dev")
            },
            "join_complete": True,
            "joined_by": "utterance_id_sha256",
        },
    }


def _fixed_list_numpy(array: pa.Array, dimensions: int) -> np.ndarray:
    if (
        not pa.types.is_fixed_size_list(array.type)
        or array.type.list_size != dimensions
        or array.null_count
    ):
        raise ValueError("embedding batch has an invalid fixed-list layout")
    flat = array.flatten()
    values = flat.to_numpy(zero_copy_only=False)
    matrix = values.reshape(len(array), dimensions)
    if matrix.dtype != np.float16:
        raise ValueError("embedding batch is not stored as float16")
    if not np.isfinite(matrix).all():
        raise ValueError("embedding batch contains NaN or Inf")
    return matrix


def _selected_indices(
    batch: pa.RecordBatch,
    *,
    partition: str,
    config: Mapping[str, Any],
) -> np.ndarray:
    splits = _batch_column(batch, "split").to_pylist()
    speakers = _batch_column(batch, "speaker_id_sha256").to_pylist()
    l1_values = _batch_column(batch, "l1").to_pylist()
    genders = _batch_column(batch, "gender").to_pylist()
    selected = [
        index
        for index, (split, speaker, l1, gender) in enumerate(
            zip(splits, speakers, l1_values, genders, strict=True)
        )
        if _is_primary_scope(l1, gender, config)
        and _partition_for_row(
            split=str(split),
            speaker_id_sha256=str(speaker),
            config=config,
        )
        == partition
    ]
    return np.asarray(selected, dtype=np.int64)


def _mini_batches(
    indices: np.ndarray,
    *,
    batch_size: int,
) -> Iterator[np.ndarray]:
    for start in range(0, len(indices), batch_size):
        yield indices[start : start + batch_size]


def _phone_stream(
    *,
    paths: Sequence[Path],
    config: Mapping[str, Any],
    partition: str,
    vocabulary: Mapping[str, int],
    shuffle_seed: int | None,
) -> Iterator[dict[str, Any]]:
    columns = [
        "utterance_id_sha256",
        "speaker_id_sha256",
        "split",
        "l1",
        "gender",
        "proficiency",
        "phone_index",
        "target_label",
        "embedding_mean",
        "binary_error_label",
    ]
    dimensions = int(config["encoder"]["hidden_size"])
    parquet_batch_rows = int(config["optimization"]["parquet_batch_rows"])
    batch_size = int(config["optimization"]["phone_batch_size"])
    rng = np.random.default_rng(shuffle_seed)
    for batch in _parquet_batches(
        paths,
        columns=columns,
        batch_rows=parquet_batch_rows,
    ):
        selected = _selected_indices(
            batch,
            partition=partition,
            config=config,
        )
        if not len(selected):
            continue
        if shuffle_seed is not None:
            selected = rng.permutation(selected)
        matrix = _fixed_list_numpy(
            _batch_column(batch, "embedding_mean"),
            dimensions,
        )
        target_labels = [
            str(value) for value in _batch_column(batch, "target_label").to_pylist()
        ]
        label_values = np.asarray(
            _batch_column(batch, "binary_error_label").to_numpy(zero_copy_only=False),
            dtype=np.int64,
        )
        for mini in _mini_batches(selected, batch_size=batch_size):
            output: dict[str, Any] = {
                "embeddings": np.asarray(matrix[mini], dtype=np.float16),
                "target_indices": np.asarray(
                    [vocabulary.get(target_labels[index], 0) for index in mini],
                    dtype=np.int64,
                ),
                "targets": label_values[mini],
            }
            if shuffle_seed is None:
                output["metadata"] = {
                    name: [
                        _batch_column(batch, name)[int(index)].as_py() for index in mini
                    ]
                    for name in (
                        "utterance_id_sha256",
                        "speaker_id_sha256",
                        "l1",
                        "gender",
                        "proficiency",
                        "phone_index",
                        "target_label",
                    )
                }
            yield output


def _utterance_stream(
    *,
    paths: Sequence[Path],
    config: Mapping[str, Any],
    partition: str,
    labels: Mapping[str, UtteranceLabel],
    shuffle_seed: int | None,
) -> Iterator[dict[str, Any]]:
    columns = [
        "utterance_id_sha256",
        "speaker_id_sha256",
        "split",
        "l1",
        "gender",
        "proficiency",
        "embedding_mean_std",
    ]
    dimensions = int(config["encoder"]["utterance_embedding_size"])
    parquet_batch_rows = int(config["optimization"]["parquet_batch_rows"])
    batch_size = int(config["optimization"]["utterance_batch_size"])
    rng = np.random.default_rng(shuffle_seed)
    for batch in _parquet_batches(
        paths,
        columns=columns,
        batch_rows=parquet_batch_rows,
    ):
        selected = _selected_indices(
            batch,
            partition=partition,
            config=config,
        )
        if not len(selected):
            continue
        if shuffle_seed is not None:
            selected = rng.permutation(selected)
        matrix = _fixed_list_numpy(
            _batch_column(batch, "embedding_mean_std"),
            dimensions,
        )
        identifiers = [
            str(value)
            for value in _batch_column(batch, "utterance_id_sha256").to_pylist()
        ]
        score_matrix = np.asarray(
            [labels[identifier].scores for identifier in identifiers],
            dtype=np.float32,
        )
        for mini in _mini_batches(selected, batch_size=batch_size):
            output: dict[str, Any] = {
                "embeddings": np.asarray(matrix[mini], dtype=np.float16),
                "targets": score_matrix[mini],
                "observed": np.isfinite(score_matrix[mini]),
            }
            if shuffle_seed is None:
                output["metadata"] = {
                    name: [
                        _batch_column(batch, name)[int(index)].as_py() for index in mini
                    ]
                    for name in (
                        "utterance_id_sha256",
                        "speaker_id_sha256",
                        "l1",
                        "gender",
                        "proficiency",
                    )
                }
            yield output


def set_deterministic_seed(
    seed: int,
    *,
    deterministic_algorithms: bool,
) -> None:
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(deterministic_algorithms)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = deterministic_algorithms


def select_device(
    config: Mapping[str, Any],
    *,
    override: torch.device | None = None,
) -> torch.device:
    """Select CUDA in production; ``override`` exists only for isolated tests."""

    if override is not None:
        return override
    if config["runtime"]["require_cuda"] is not True:
        raise ValueError("production frozen-head training must require CUDA")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; refusing CPU production training")
    return torch.device("cuda")


def _cpu_state_dict(model: torch.nn.Module) -> dict[str, torch.Tensor]:
    return {
        name: value.detach().cpu().clone() for name, value in model.state_dict().items()
    }


def _optimizer(
    model: torch.nn.Module,
    config: Mapping[str, Any],
) -> torch.optim.AdamW:
    optimization = config["optimization"]
    return torch.optim.AdamW(
        model.parameters(),
        lr=float(optimization["learning_rate"]),
        weight_decay=float(optimization["weight_decay"]),
    )


def _binary_log_loss(targets: np.ndarray, probabilities: np.ndarray) -> float:
    clipped = np.clip(probabilities, 1e-7, 1 - 1e-7)
    values = -(targets * np.log(clipped) + (1 - targets) * np.log(1 - clipped))
    return float(values.mean())


def select_phone_threshold(
    *,
    targets: np.ndarray,
    probabilities: np.ndarray,
    candidates: np.ndarray,
) -> tuple[float, float, list[dict[str, float]]]:
    if set(np.unique(targets).tolist()) != {0, 1}:
        raise ValueError("threshold selection requires both binary classes")
    results: list[dict[str, float]] = []
    for threshold in candidates:
        predictions = (probabilities >= threshold).astype(np.int64)
        score = macro_f1(targets, predictions, labels=[0, 1])
        results.append({"threshold": float(threshold), "macro_f1": float(score)})
    best = max(
        results,
        key=lambda item: (
            item["macro_f1"],
            -abs(item["threshold"] - 0.5),
            -item["threshold"],
        ),
    )
    return float(best["threshold"]), float(best["macro_f1"]), results


def _predict_phone_partition(
    *,
    model: FrozenPhoneHead,
    paths: Sequence[Path],
    config: Mapping[str, Any],
    partition: str,
    vocabulary: Mapping[str, int],
    device: torch.device,
    include_metadata: bool,
) -> dict[str, Any]:
    model.eval()
    probability_parts: list[np.ndarray] = []
    target_parts: list[np.ndarray] = []
    metadata: dict[str, list[Any]] = {
        name: []
        for name in (
            "utterance_id_sha256",
            "speaker_id_sha256",
            "l1",
            "gender",
            "proficiency",
            "phone_index",
            "target_label",
        )
    }
    with torch.inference_mode():
        for batch in _phone_stream(
            paths=paths,
            config=config,
            partition=partition,
            vocabulary=vocabulary,
            shuffle_seed=None,
        ):
            embeddings = torch.from_numpy(batch["embeddings"]).to(
                device=device,
                dtype=torch.float32,
            )
            target_indices = torch.from_numpy(batch["target_indices"]).to(
                device=device,
                dtype=torch.long,
            )
            logits = model(embeddings, target_indices)
            probabilities = torch.sigmoid(logits).detach().cpu().numpy()
            probability_parts.append(probabilities.astype(np.float64))
            target_parts.append(batch["targets"].astype(np.int64))
            if include_metadata:
                for name, values in batch["metadata"].items():
                    metadata[name].extend(values)
    if not probability_parts:
        raise ValueError(f"phone prediction partition is empty: {partition}")
    output: dict[str, Any] = {
        "probabilities": np.concatenate(probability_parts),
        "targets": np.concatenate(target_parts),
    }
    if include_metadata:
        output["metadata"] = metadata
    return output


def train_phone_head(
    *,
    config: Mapping[str, Any],
    cache_contract: CacheContract,
    metadata: Mapping[str, Any],
    seed: int,
    device: torch.device,
) -> tuple[FrozenPhoneHead, dict[str, Any]]:
    vocabulary_values = list(metadata["phone"]["target_vocabulary"])
    vocabulary = {label: index for index, label in enumerate(vocabulary_values)}
    head_config = config["heads"]["phone"]
    model = FrozenPhoneHead(
        embedding_dim=int(config["encoder"]["hidden_size"]),
        target_vocabulary_size=len(vocabulary),
        target_embedding_dim=int(head_config["target_embedding_dim"]),
        hidden_dim=int(head_config["hidden_dim"]),
        dropout=float(head_config["dropout"]),
    ).to(device)
    optimizer = _optimizer(model, config)
    train_classes = metadata["phone"]["class_rows"]["train_fit"]
    negative_count = int(train_classes["0"])
    positive_count = int(train_classes["1"])
    positive_weight = torch.tensor(
        [negative_count / positive_count],
        device=device,
        dtype=torch.float32,
    )
    criterion = torch.nn.BCEWithLogitsLoss(pos_weight=positive_weight)
    max_epochs = int(config["optimization"]["phone_max_epochs"])
    minimum_epochs = int(config["optimization"]["minimum_epochs"])
    patience = int(config["optimization"]["early_stopping_patience"])
    gradient_clip = float(config["optimization"]["gradient_clip_norm"])
    candidate_thresholds = threshold_grid(config)
    history: list[dict[str, Any]] = []
    best_state: dict[str, torch.Tensor] | None = None
    best_epoch = 0
    best_threshold = 0.5
    best_f1 = -math.inf
    best_val_loss = math.inf
    epochs_without_improvement = 0
    rows_per_epoch = int(metadata["phone"]["rows"]["train_fit"])

    for epoch in range(1, max_epochs + 1):
        model.train()
        loss_total = 0.0
        row_total = 0
        for batch in _phone_stream(
            paths=cache_contract.paths["phone"],
            config=config,
            partition="train_fit",
            vocabulary=vocabulary,
            shuffle_seed=seed * 100_000 + epoch,
        ):
            embeddings = torch.from_numpy(batch["embeddings"]).to(
                device=device,
                dtype=torch.float32,
            )
            target_indices = torch.from_numpy(batch["target_indices"]).to(
                device=device,
                dtype=torch.long,
            )
            targets = torch.from_numpy(batch["targets"]).to(
                device=device,
                dtype=torch.float32,
            )
            optimizer.zero_grad(set_to_none=True)
            logits = model(embeddings, target_indices)
            loss = criterion(logits, targets)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip)
            optimizer.step()
            count = int(targets.numel())
            loss_total += float(loss.detach().cpu()) * count
            row_total += count
        if row_total != rows_per_epoch:
            raise ValueError("phone train-fit streaming row count changed")
        validation = _predict_phone_partition(
            model=model,
            paths=cache_contract.paths["phone"],
            config=config,
            partition="inner_val",
            vocabulary=vocabulary,
            device=device,
            include_metadata=False,
        )
        selected_threshold, validation_f1, threshold_candidates = (
            select_phone_threshold(
                targets=validation["targets"],
                probabilities=validation["probabilities"],
                candidates=candidate_thresholds,
            )
        )
        validation_loss = _binary_log_loss(
            validation["targets"],
            validation["probabilities"],
        )
        validation_auprc = auprc(
            validation["targets"],
            validation["probabilities"],
            labels=[0, 1],
            undefined="nan",
        )
        improved = validation_f1 > best_f1 + 1e-12 or (
            abs(validation_f1 - best_f1) <= 1e-12
            and validation_loss < best_val_loss - 1e-12
        )
        if improved:
            best_state = _cpu_state_dict(model)
            best_epoch = epoch
            best_threshold = selected_threshold
            best_f1 = validation_f1
            best_val_loss = validation_loss
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
        history.append(
            {
                "epoch": epoch,
                "train_weighted_bce": loss_total / row_total,
                "inner_val_binary_log_loss": validation_loss,
                "inner_val_macro_f1": validation_f1,
                "inner_val_auprc": validation_auprc,
                "selected_threshold": selected_threshold,
                "threshold_candidates": threshold_candidates,
                "became_best": improved,
            }
        )
        if epoch >= minimum_epochs and epochs_without_improvement >= patience:
            break
    if best_state is None:
        raise RuntimeError("phone early stopping selected no checkpoint")
    model.load_state_dict(best_state)
    return model, {
        "best_epoch": best_epoch,
        "epochs_completed": len(history),
        "selection_source": "primary_train_inner_validation_only",
        "early_stopping_metric": "inner_val_macro_f1_then_binary_log_loss",
        "decision_threshold_source": "primary_train_inner_validation_only",
        "decision_threshold": best_threshold,
        "best_inner_val_macro_f1": best_f1,
        "best_inner_val_binary_log_loss": best_val_loss,
        "positive_class_weight": float(positive_weight.item()),
        "target_vocabulary": vocabulary_values,
        "target_vocabulary_source": "primary_train_fit_only",
        "history": history,
    }


def _predict_utterance_partition(
    *,
    model: FrozenUtteranceHead,
    paths: Sequence[Path],
    config: Mapping[str, Any],
    partition: str,
    labels: Mapping[str, UtteranceLabel],
    device: torch.device,
    include_metadata: bool,
) -> dict[str, Any]:
    model.eval()
    prediction_parts: list[np.ndarray] = []
    target_parts: list[np.ndarray] = []
    observed_parts: list[np.ndarray] = []
    metadata: dict[str, list[Any]] = {
        name: []
        for name in (
            "utterance_id_sha256",
            "speaker_id_sha256",
            "l1",
            "gender",
            "proficiency",
        )
    }
    with torch.inference_mode():
        for batch in _utterance_stream(
            paths=paths,
            config=config,
            partition=partition,
            labels=labels,
            shuffle_seed=None,
        ):
            embeddings = torch.from_numpy(batch["embeddings"]).to(
                device=device,
                dtype=torch.float32,
            )
            predictions = model(embeddings).detach().cpu().numpy()
            prediction_parts.append(predictions.astype(np.float64))
            target_parts.append(batch["targets"].astype(np.float64))
            observed_parts.append(batch["observed"].astype(np.bool_))
            if include_metadata:
                for name, values in batch["metadata"].items():
                    metadata[name].extend(values)
    if not prediction_parts:
        raise ValueError(f"utterance prediction partition is empty: {partition}")
    output: dict[str, Any] = {
        "predictions": np.concatenate(prediction_parts),
        "targets": np.concatenate(target_parts),
        "observed": np.concatenate(observed_parts),
    }
    if include_metadata:
        output["metadata"] = metadata
    return output


def _masked_mse(
    targets: np.ndarray,
    predictions: np.ndarray,
    observed: np.ndarray,
) -> float:
    errors = (predictions - targets) ** 2
    values = errors[observed]
    if not values.size:
        raise ValueError("masked MSE has no observed targets")
    return float(values.mean())


def train_utterance_head(
    *,
    config: Mapping[str, Any],
    cache_contract: CacheContract,
    labels: Mapping[str, UtteranceLabel],
    metadata: Mapping[str, Any],
    seed: int,
    device: torch.device,
) -> tuple[FrozenUtteranceHead, dict[str, Any]]:
    del metadata
    head_config = config["heads"]["utterance"]
    score_minimum, score_maximum = head_config["score_range"]
    model = FrozenUtteranceHead(
        embedding_dim=int(config["encoder"]["utterance_embedding_size"]),
        hidden_dim=int(head_config["hidden_dim"]),
        dropout=float(head_config["dropout"]),
        outcome_count=len(OUTCOMES),
        score_minimum=float(score_minimum),
        score_maximum=float(score_maximum),
    ).to(device)
    optimizer = _optimizer(model, config)
    max_epochs = int(config["optimization"]["utterance_max_epochs"])
    minimum_epochs = int(config["optimization"]["minimum_epochs"])
    patience = int(config["optimization"]["early_stopping_patience"])
    gradient_clip = float(config["optimization"]["gradient_clip_norm"])
    history: list[dict[str, Any]] = []
    best_state: dict[str, torch.Tensor] | None = None
    best_epoch = 0
    best_val_loss = math.inf
    epochs_without_improvement = 0

    for epoch in range(1, max_epochs + 1):
        model.train()
        squared_error_total = 0.0
        observed_total = 0
        for batch in _utterance_stream(
            paths=cache_contract.paths["utterance"],
            config=config,
            partition="train_fit",
            labels=labels,
            shuffle_seed=seed * 100_000 + epoch,
        ):
            embeddings = torch.from_numpy(batch["embeddings"]).to(
                device=device,
                dtype=torch.float32,
            )
            targets = torch.from_numpy(np.nan_to_num(batch["targets"], nan=0.0)).to(
                device=device, dtype=torch.float32
            )
            observed = torch.from_numpy(batch["observed"]).to(
                device=device,
                dtype=torch.bool,
            )
            optimizer.zero_grad(set_to_none=True)
            predictions = model(embeddings)
            squared = (predictions - targets).square()
            loss = squared[observed].mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip)
            optimizer.step()
            count = int(observed.sum().detach().cpu())
            squared_error_total += float(squared[observed].sum().detach().cpu())
            observed_total += count
        if observed_total == 0:
            raise ValueError("utterance train-fit has no observed score labels")
        validation = _predict_utterance_partition(
            model=model,
            paths=cache_contract.paths["utterance"],
            config=config,
            partition="inner_val",
            labels=labels,
            device=device,
            include_metadata=False,
        )
        validation_loss = _masked_mse(
            validation["targets"],
            validation["predictions"],
            validation["observed"],
        )
        improved = validation_loss < best_val_loss - 1e-12
        if improved:
            best_state = _cpu_state_dict(model)
            best_epoch = epoch
            best_val_loss = validation_loss
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
        history.append(
            {
                "epoch": epoch,
                "train_masked_mse": squared_error_total / observed_total,
                "inner_val_masked_mse": validation_loss,
                "became_best": improved,
            }
        )
        if epoch >= minimum_epochs and epochs_without_improvement >= patience:
            break
    if best_state is None:
        raise RuntimeError("utterance early stopping selected no checkpoint")
    model.load_state_dict(best_state)
    return model, {
        "best_epoch": best_epoch,
        "epochs_completed": len(history),
        "selection_source": "primary_train_inner_validation_only",
        "early_stopping_metric": "inner_val_masked_mse",
        "best_inner_val_masked_mse": best_val_loss,
        "history": history,
    }


def phone_metric_bundle(
    targets: np.ndarray,
    probabilities: np.ndarray,
    *,
    threshold: float,
) -> dict[str, Any]:
    predictions = (probabilities >= threshold).astype(np.int64)
    return {
        "macro_f1": macro_f1(targets, predictions, labels=[0, 1]),
        "auprc": auprc(
            targets,
            probabilities,
            labels=[0, 1],
            undefined="nan",
        ),
        "decision_threshold": threshold,
        "confusion_matrix_labels": [0, 1],
        "confusion_matrix": confusion_matrix(
            targets,
            predictions,
            labels=[0, 1],
        ).tolist(),
    }


def utterance_metric_bundle(
    targets: np.ndarray,
    predictions: np.ndarray,
) -> dict[str, Any]:
    clipped = np.clip(predictions, 1, 5)
    integer_targets = np.rint(targets).astype(np.int64)
    integer_predictions = np.rint(clipped).astype(np.int64)
    return {
        "qwk": quadratic_weighted_kappa(
            integer_targets,
            integer_predictions,
            labels=[1, 2, 3, 4, 5],
        ),
        "spearman": spearman_correlation(targets, clipped),
        "mae": mae(targets, clipped),
        "icc_2_1": icc_2_1(np.column_stack((targets, clipped))),
        "confusion_matrix_labels": [1, 2, 3, 4, 5],
        "confusion_matrix": confusion_matrix(
            integer_targets,
            integer_predictions,
            labels=[1, 2, 3, 4, 5],
        ).tolist(),
    }


def phone_slice_metrics(
    *,
    metadata: Mapping[str, Sequence[Any]],
    targets: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
    requested: Sequence[str],
) -> dict[str, Any]:
    predictions = (probabilities >= threshold).astype(np.int64)
    output: dict[str, Any] = {}
    for name in requested:
        if name not in {"l1", "gender", "proficiency", "target_label"}:
            output[name] = {"status": "unavailable"}
            continue
        groups = np.asarray(
            [
                "__MISSING__" if value is None else str(value)
                for value in metadata[name]
            ],
            dtype=object,
        )
        entries: list[dict[str, Any]] = []
        for group in sorted(set(groups.tolist())):
            selected = groups == group
            entries.append(
                {
                    "value": group,
                    "n_samples": int(selected.sum()),
                    "positive_rows": int(targets[selected].sum()),
                    "macro_f1": macro_f1(
                        targets[selected],
                        predictions[selected],
                        labels=[0, 1],
                    ),
                    "auprc": auprc(
                        targets[selected],
                        probabilities[selected],
                        labels=[0, 1],
                        undefined="nan",
                    ),
                }
            )
        output[name] = {"status": "computed", "groups": entries}
    return output


def utterance_slice_metrics(
    *,
    metadata: Mapping[str, Sequence[Any]],
    targets: np.ndarray,
    predictions: np.ndarray,
    observed: np.ndarray,
    requested: Sequence[str],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for outcome_index, outcome in enumerate(OUTCOMES):
        outcome_output: dict[str, Any] = {}
        for name in requested:
            if name not in {"l1", "gender", "proficiency"}:
                outcome_output[name] = {"status": "unavailable"}
                continue
            groups = np.asarray(
                [
                    "__MISSING__" if value is None else str(value)
                    for value in metadata[name]
                ],
                dtype=object,
            )
            entries: list[dict[str, Any]] = []
            for group in sorted(set(groups.tolist())):
                selected = (groups == group) & observed[:, outcome_index]
                count = int(selected.sum())
                if count < 2:
                    entries.append(
                        {
                            "value": group,
                            "n_samples": count,
                            "metrics": None,
                            "reason": "fewer_than_two_observed_labels",
                        }
                    )
                    continue
                entries.append(
                    {
                        "value": group,
                        "n_samples": count,
                        "metrics": utterance_metric_bundle(
                            targets[selected, outcome_index],
                            predictions[selected, outcome_index],
                        ),
                    }
                )
            outcome_output[name] = {"status": "computed", "groups": entries}
        output[outcome] = outcome_output
    return output


def _prediction_metadata() -> dict[bytes, bytes]:
    return {
        b"voice_coach.schema_version": PREDICTION_SCHEMA_VERSION.encode(),
        b"voice_coach.development_only": b"true",
        b"voice_coach.oracle_alignment": b"true",
        b"voice_coach.contains_direct_identifiers": b"false",
        b"voice_coach.access_scope": b"restricted_internal",
        b"voice_coach.shareable": b"false",
    }


def save_phone_predictions(
    path: Path,
    *,
    seed: int,
    result: Mapping[str, Any],
    threshold: float,
) -> dict[str, Any]:
    metadata = result["metadata"]
    targets = result["targets"].astype(np.int8)
    probabilities = result["probabilities"].astype(np.float32)
    predictions = (probabilities >= threshold).astype(np.int8)
    row_count = len(targets)
    table = pa.table(
        {
            "schema_version": [PREDICTION_SCHEMA_VERSION] * row_count,
            "development_only": [True] * row_count,
            "oracle_alignment": [True] * row_count,
            "seed": np.full(row_count, seed, dtype=np.int32),
            "split": ["dev"] * row_count,
            "utterance_id_sha256": metadata["utterance_id_sha256"],
            "speaker_id_sha256": metadata["speaker_id_sha256"],
            "phone_index": np.asarray(metadata["phone_index"], dtype=np.int32),
            "target_label": metadata["target_label"],
            "y_true": targets,
            "y_pred": predictions,
            "error_probability": probabilities,
            "decision_threshold": np.full(
                row_count,
                threshold,
                dtype=np.float32,
            ),
        }
    ).replace_schema_metadata(_prediction_metadata())
    pq.write_table(table, path, compression="zstd", row_group_size=16_384)
    return {
        "path": path.name,
        "rows": row_count,
        "sha256": sha256_file(path),
    }


def save_utterance_predictions(
    path: Path,
    *,
    seed: int,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = result["metadata"]
    targets = result["targets"]
    predictions = result["predictions"]
    observed = result["observed"]
    row_count = targets.shape[0]
    columns: dict[str, Any] = {
        "schema_version": [PREDICTION_SCHEMA_VERSION] * row_count,
        "development_only": [True] * row_count,
        "oracle_alignment": [True] * row_count,
        "seed": np.full(row_count, seed, dtype=np.int32),
        "split": ["dev"] * row_count,
        "utterance_id_sha256": metadata["utterance_id_sha256"],
        "speaker_id_sha256": metadata["speaker_id_sha256"],
    }
    for index, outcome in enumerate(OUTCOMES):
        columns[f"{outcome}_true"] = targets[:, index]
        columns[f"{outcome}_pred"] = predictions[:, index].astype(np.float32)
        columns[f"{outcome}_observed"] = observed[:, index]
    table = pa.table(columns).replace_schema_metadata(_prediction_metadata())
    pq.write_table(table, path, compression="zstd", row_group_size=8_192)
    return {
        "path": path.name,
        "rows": row_count,
        "sha256": sha256_file(path),
    }


def save_checkpoint(
    path: Path,
    *,
    seed: int,
    config: Mapping[str, Any],
    phone_model: FrozenPhoneHead,
    phone_training: Mapping[str, Any],
    utterance_model: FrozenUtteranceHead,
    utterance_training: Mapping[str, Any],
    cache_contract: CacheContract,
) -> dict[str, Any]:
    checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "seed": seed,
        "development_only": True,
        "oracle_alignment": True,
        "encoder": {
            "variant": config["encoder"]["variant"],
            "id": config["encoder"]["id"],
            "revision": config["encoder"]["revision"],
            "hidden_size": config["encoder"]["hidden_size"],
            "frozen": True,
            "source_config_sha256": cache_contract.model_config_sha256,
            "source_checkpoint_sha256": (cache_contract.model_checkpoint_sha256),
        },
        "feature_roles": {
            "phone_inputs": list(PHONE_INPUTS),
            "phone_labels": list(PHONE_LABELS),
            "utterance_inputs": list(UTTERANCE_INPUTS),
            "utterance_labels": list(OUTCOMES),
        },
        "phone": {
            "head_config": dict(config["heads"]["phone"]),
            "state_dict": _cpu_state_dict(phone_model),
            "target_vocabulary": list(phone_training["target_vocabulary"]),
            "decision_threshold": phone_training["decision_threshold"],
            "best_epoch": phone_training["best_epoch"],
        },
        "utterance": {
            "head_config": dict(config["heads"]["utterance"]),
            "state_dict": _cpu_state_dict(utterance_model),
            "outcomes": list(OUTCOMES),
            "best_epoch": utterance_training["best_epoch"],
        },
    }
    with path.open("wb") as handle:
        torch.save(checkpoint, handle)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "path": path.name,
        "sha256": sha256_file(path),
    }


def _aggregate(values: Sequence[object]) -> dict[str, Any]:
    numeric = np.asarray(
        [float(cast(Any, value)) for value in values if value is not None],
        dtype=np.float64,
    )
    finite = numeric[np.isfinite(numeric)]
    return {
        "mean": float(finite.mean()) if finite.size else float("nan"),
        "sample_std": (float(finite.std(ddof=1)) if finite.size > 1 else float("nan")),
        "valid_seeds": int(finite.size),
    }


def _device_metadata(device: torch.device) -> dict[str, Any]:
    if device.type != "cuda":
        return {
            "type": device.type,
            "test_override": True,
            "production_evidence_allowed": False,
        }
    index = device.index if device.index is not None else torch.cuda.current_device()
    properties = torch.cuda.get_device_properties(index)
    return {
        "type": "cuda",
        "index": index,
        "name": properties.name,
        "compute_capability": f"{properties.major}.{properties.minor}",
        "total_memory_bytes": int(properties.total_memory),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "test_override": False,
        "production_evidence_allowed": True,
    }


def _memory_contract(config: Mapping[str, Any]) -> dict[str, Any]:
    parquet_rows = int(config["optimization"]["parquet_batch_rows"])
    phone_batch = int(config["optimization"]["phone_batch_size"])
    utterance_batch = int(config["optimization"]["utterance_batch_size"])
    hidden = int(config["encoder"]["hidden_size"])
    utterance_hidden = int(config["encoder"]["utterance_embedding_size"])
    return {
        "full_embedding_matrix_materialized": False,
        "parquet_streaming": True,
        "maximum_parquet_batch_rows": parquet_rows,
        "storage_dtype": "float16",
        "compute_dtype": "float32",
        "maximum_float16_embedding_batch_bytes": max(
            parquet_rows * hidden * np.dtype(np.float16).itemsize,
            parquet_rows * utterance_hidden * np.dtype(np.float16).itemsize,
        ),
        "maximum_float32_training_tensor_bytes": max(
            phone_batch * hidden * np.dtype(np.float32).itemsize,
            utterance_batch * utterance_hidden * np.dtype(np.float32).itemsize,
        ),
        "explicit_label_map_rows": int(
            config["data"]["expected_output_rows"]["utterance"]
        ),
        "dev_prediction_buffers_contain_no_embeddings": True,
        "actual_peak_process_rss_reported_after_run": True,
    }


def resolve_paths(
    args: argparse.Namespace,
    config: Mapping[str, Any],
) -> argparse.Namespace:
    data = config["data"]
    outputs = config["outputs"]
    if getattr(args, "cache_root", None) is None:
        args.cache_root = Path(str(data["feature_cache_root"]))
    if getattr(args, "cache_report", None) is None:
        args.cache_report = Path(str(data["feature_cache_report"]))
    if getattr(args, "explicit_label_cache", None) is None:
        args.explicit_label_cache = Path(str(data["explicit_utterance_labels"]))
    if getattr(args, "output_dir", None) is None:
        args.output_dir = Path(str(outputs["artifact_root"]))
    if getattr(args, "registry_root", None) is None:
        args.registry_root = Path(str(outputs["registry_root"]))
    return args


def train_frozen_head(
    args: argparse.Namespace,
    *,
    device_override: torch.device | None = None,
) -> dict[str, Any]:
    config = load_config(args.config)
    validate_experiment_config(config)
    resolve_paths(args, config)
    determinism_contract = validate_deterministic_runtime(
        config,
        production_run=device_override is None,
    )
    implementation_hashes = source_code_hashes()
    if args.output_dir.exists():
        raise FileExistsError(f"output directory already exists: {args.output_dir}")
    cache_contract = validate_full_feature_cache(
        config=config,
        cache_root=args.cache_root,
        cache_report_path=args.cache_report,
    )
    utterance_labels, label_contract = load_explicit_utterance_labels(
        config=config,
        path=args.explicit_label_cache,
    )
    training_metadata = scan_training_metadata(
        config=config,
        cache_contract=cache_contract,
        utterance_labels=utterance_labels,
    )
    device = select_device(config, override=device_override)
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output_dir.with_name(f".{args.output_dir.name}.part-{uuid4().hex}")
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
            "The phone head uses oracle target-phone boundaries. This is a "
            "development diagnostic result, not deployable alignment evidence."
        ),
        "encoder": {
            "variant": config["encoder"]["variant"],
            "id": config["encoder"]["id"],
            "revision": config["encoder"]["revision"],
            "hidden_size": config["encoder"]["hidden_size"],
            "utterance_embedding_size": config["encoder"]["utterance_embedding_size"],
            "frozen": True,
            "source_config_sha256": cache_contract.model_config_sha256,
            "source_checkpoint_sha256": (cache_contract.model_checkpoint_sha256),
            "head_code_reusable_with_configured_hidden_dimension": True,
            "current_experiment_variant": config["encoder"]["variant"],
        },
        "sources": {
            "training_config_sha256": sha256_file(args.config),
            **implementation_hashes,
            "feature_cache_report_sha256": cache_contract.report_sha256,
            "explicit_utterance_labels_sha256": label_contract["sha256"],
            "modeling_manifest_sha256": cache_contract.manifest_sha256,
            "prompt_reservation_sha256": (cache_contract.prompt_reservation_sha256),
            "cache_builder_config_sha256": (cache_contract.builder_config_sha256),
            "cache_builder_sha256": cache_contract.builder_sha256,
            "encoder_config_sha256": cache_contract.model_config_sha256,
            "encoder_checkpoint_sha256": (cache_contract.model_checkpoint_sha256),
            "model_hashes_bound_by_validated_config_and_cache_report": True,
        },
        "cache_contract": {
            "full_cache_validated": True,
            "cache_report_status": "completed",
            "source_scan_complete": True,
            "smoke_cache_rejected": True,
            "every_shard_hash_validated": True,
            "every_shard_schema_validated": True,
            "every_shard_split_policy_validated": True,
            "every_shard_privacy_metadata_validated": True,
            "rows": dict(cache_contract.rows),
            "split_rows": {
                kind: dict(counts) for kind, counts in cache_contract.split_rows.items()
            },
            "shard_fingerprints": {
                kind: list(values)
                for kind, values in cache_contract.shard_fingerprints.items()
            },
            "speaker_overlap_train_dev": (cache_contract.speaker_overlap_train_dev),
            "access_scope": "restricted_internal",
            "shareable": False,
        },
        "utterance_label_contract": label_contract,
        "leakage_controls": {
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
            "primary_analysis_scope": config["data"]["primary_analysis_scope"],
            "phone_input_allowlist": list(PHONE_INPUTS),
            "phone_label_only_columns": list(PHONE_LABELS),
            "utterance_input_allowlist": list(UTTERANCE_INPUTS),
            "utterance_label_only_columns": list(OUTCOMES),
            "raw_manifest_read": False,
            "raw_path_columns_read": [],
            "environment_files_read": [],
            "environment_files_registered": [],
        },
        "head_contract": {
            "phone": dict(config["heads"]["phone"]),
            "utterance": dict(config["heads"]["utterance"]),
            "optimization": dict(config["optimization"]),
            "same_hyperparameters_for_every_seed": True,
            "task_rng_reinitialized_per_head": True,
            "optimizer": "AdamW",
            "optimizer_device": device.type,
        },
        "data": training_metadata,
        "memory_contract": _memory_contract(config),
        "runtime": {
            "device": _device_metadata(device),
            "determinism": determinism_contract,
            "peak_process_rss_bytes": None,
            "peak_cuda_allocated_bytes": None,
            "peak_cuda_reserved_bytes": None,
        },
        "experiment_registry": {
            "integrated": False,
            "t03_complete": False,
            "reason": (
                "Direct library execution does not create registry metadata; "
                "use the registered CLI wrapper for an engineering run."
            ),
        },
        "runs": [],
    }

    try:
        if device.type == "cuda":
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats(device)
        for seed_value in config["experiment"]["seeds"]:
            seed = int(seed_value)
            set_deterministic_seed(
                seed,
                deterministic_algorithms=bool(
                    config["runtime"]["deterministic_algorithms"]
                ),
            )
            seed_directory = temporary / f"seed_{seed}"
            seed_directory.mkdir()

            phone_model, phone_training = train_phone_head(
                config=config,
                cache_contract=cache_contract,
                metadata=training_metadata,
                seed=seed,
                device=device,
            )
            # Keep utterance initialization/dropout independent of how many
            # epochs the phone task happened to consume for this seed.
            set_deterministic_seed(
                seed,
                deterministic_algorithms=True,
            )
            utterance_model, utterance_training = train_utterance_head(
                config=config,
                cache_contract=cache_contract,
                labels=utterance_labels,
                metadata=training_metadata,
                seed=seed,
                device=device,
            )

            # These are the only two dev inference passes for this seed: one
            # per task.  No value returned here influences the checkpoint,
            # stopping epoch, vocabulary, or phone threshold.
            phone_dev = _predict_phone_partition(
                model=phone_model,
                paths=cache_contract.paths["phone"],
                config=config,
                partition="dev",
                vocabulary={
                    label: index
                    for index, label in enumerate(phone_training["target_vocabulary"])
                },
                device=device,
                include_metadata=True,
            )
            utterance_dev = _predict_utterance_partition(
                model=utterance_model,
                paths=cache_contract.paths["utterance"],
                config=config,
                partition="dev",
                labels=utterance_labels,
                device=device,
                include_metadata=True,
            )
            threshold = float(phone_training["decision_threshold"])
            phone_metrics = phone_metric_bundle(
                phone_dev["targets"],
                phone_dev["probabilities"],
                threshold=threshold,
            )
            outcome_metrics: dict[str, Any] = {}
            for outcome_index, outcome in enumerate(OUTCOMES):
                observed = utterance_dev["observed"][:, outcome_index]
                outcome_metrics[outcome] = {
                    "observed_rows": int(observed.sum()),
                    "metrics": utterance_metric_bundle(
                        utterance_dev["targets"][observed, outcome_index],
                        utterance_dev["predictions"][observed, outcome_index],
                    ),
                }
            phone_slices = phone_slice_metrics(
                metadata=phone_dev["metadata"],
                targets=phone_dev["targets"],
                probabilities=phone_dev["probabilities"],
                threshold=threshold,
                requested=config["evaluation"]["phone_slices"],
            )
            utterance_slices = utterance_slice_metrics(
                metadata=utterance_dev["metadata"],
                targets=utterance_dev["targets"],
                predictions=utterance_dev["predictions"],
                observed=utterance_dev["observed"],
                requested=config["evaluation"]["utterance_slices"],
            )

            phone_prediction = save_phone_predictions(
                seed_directory / "phone_dev_predictions.parquet",
                seed=seed,
                result=phone_dev,
                threshold=threshold,
            )
            utterance_prediction = save_utterance_predictions(
                seed_directory / "utterance_dev_predictions.parquet",
                seed=seed,
                result=utterance_dev,
            )
            slices_path = seed_directory / "slice_metrics.json"
            atomic_write_json(
                slices_path,
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "development_only": True,
                    "seed": seed,
                    "metric_scope": "primary_dev_single_final_pass",
                    "phone": phone_slices,
                    "utterance": utterance_slices,
                },
            )
            checkpoint = save_checkpoint(
                seed_directory / "checkpoint.pt",
                seed=seed,
                config=config,
                phone_model=phone_model,
                phone_training=phone_training,
                utterance_model=utterance_model,
                utterance_training=utterance_training,
                cache_contract=cache_contract,
            )
            for artifact in (
                phone_prediction,
                utterance_prediction,
                checkpoint,
            ):
                artifact["path"] = str(Path(f"seed_{seed}") / str(artifact["path"]))
            slices_artifact = {
                "path": str(Path(f"seed_{seed}") / slices_path.name),
                "sha256": sha256_file(slices_path),
            }
            report["runs"].append(
                {
                    "seed": seed,
                    "selection": {
                        "phone": phone_training,
                        "utterance": utterance_training,
                        "dev_values_used_for_selection": False,
                    },
                    "phone": {
                        "metric_scope": "primary_dev_single_final_pass",
                        "rows": int(len(phone_dev["targets"])),
                        "metrics": phone_metrics,
                        "slices": phone_slices,
                        "predictions": phone_prediction,
                    },
                    "utterance": {
                        "metric_scope": "primary_dev_single_final_pass",
                        "rows": int(utterance_dev["targets"].shape[0]),
                        "outcomes": outcome_metrics,
                        "slices": utterance_slices,
                        "predictions": utterance_prediction,
                    },
                    "artifacts": {
                        "checkpoint": checkpoint,
                        "slice_metrics": slices_artifact,
                    },
                }
            )
            del phone_model, utterance_model, phone_dev, utterance_dev
            if device.type == "cuda":
                torch.cuda.empty_cache()

        phone_f1 = [run["phone"]["metrics"]["macro_f1"] for run in report["runs"]]
        phone_auprc_values = [
            run["phone"]["metrics"]["auprc"] for run in report["runs"]
        ]
        utterance_summary: dict[str, Any] = {}
        for outcome in OUTCOMES:
            utterance_summary[outcome] = {
                metric: _aggregate(
                    [
                        run["utterance"]["outcomes"][outcome]["metrics"][metric]
                        for run in report["runs"]
                    ]
                )
                for metric in ("qwk", "spearman", "mae", "icc_2_1")
            }
        report["summary"] = {
            "seeds_completed": [int(run["seed"]) for run in report["runs"]],
            "phone": {
                "macro_f1": _aggregate(phone_f1),
                "auprc": _aggregate(phone_auprc_values),
                "thresholds_by_seed": {
                    str(run["seed"]): run["phone"]["metrics"]["decision_threshold"]
                    for run in report["runs"]
                },
            },
            "utterance": utterance_summary,
            "results_are_development_only": True,
            "locked_test_remains_unread": True,
            "stack_remains_unread": True,
            "calibration_remains_unread": True,
            "dev_used_for_selection": False,
            "train_inner_validation_used": True,
            "dev_inference_passes_per_seed": 1,
            "oracle_alignment": True,
            "promotion_decision_allowed": False,
        }
        report["runtime"]["peak_process_rss_bytes"] = peak_rss_bytes()
        if device.type == "cuda":
            report["runtime"]["peak_cuda_allocated_bytes"] = int(
                torch.cuda.max_memory_allocated(device)
            )
            report["runtime"]["peak_cuda_reserved_bytes"] = int(
                torch.cuda.max_memory_reserved(device)
            )
        if source_code_hashes() != implementation_hashes:
            raise RuntimeError("trainer or head source changed during the run")
        report["status"] = "completed"
        report["completed_at_utc"] = utc_now()
        atomic_write_json(temporary / "report.json", report)
        os.replace(temporary, args.output_dir)
        return report
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _metric_value(value: object) -> float | int | bool | None:
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    number = float(cast(Any, value))
    return number if math.isfinite(number) else None


def _discard_published_output(output_dir: Path) -> None:
    """Atomically hide and then remove output published by a failed wrapper."""

    if not output_dir.exists():
        return
    quarantine = output_dir.with_name(
        f".{output_dir.name}.part-registry-failed-{uuid4().hex}"
    )
    try:
        os.replace(output_dir, quarantine)
    except OSError:
        shutil.rmtree(output_dir, ignore_errors=True)
        return
    shutil.rmtree(quarantine, ignore_errors=True)


def run_registered_experiment(
    args: argparse.Namespace,
    *,
    device_override: torch.device | None = None,
) -> tuple[dict[str, Any], Path]:
    config = load_config(args.config)
    validate_experiment_config(config)
    resolve_paths(args, config)
    shard_paths = [
        path
        for kind in ("utterance", "phone", "word")
        for path in sorted((args.cache_root / kind).glob("part-*.parquet"))
    ]
    if not shard_paths:
        raise ValueError("feature cache has no shards to register")
    data_files = [
        args.cache_report,
        args.explicit_label_cache,
        *shard_paths,
    ]
    model_files = [TRAINER_SOURCE_PATH, HEAD_SOURCE_PATH]
    seeds = {
        f"experiment_seed_{seed}": int(seed) for seed in config["experiment"]["seeds"]
    }
    registry = experiment_run(
        args.registry_root,
        run_id=getattr(args, "run_id", None),
        config_files=args.config,
        data_files=data_files,
        model_files=model_files,
        seeds=seeds,
        repo_path=REPOSITORY_ROOT,
    )
    output_published_by_this_run = False
    try:
        with registry as active_run:
            report = train_frozen_head(args, device_override=device_override)
            output_published_by_this_run = True
            registered_model_hashes = {
                str(item["sha256"]) for item in active_run.metadata["inputs"]["model"]
            }
            reported_source_hashes = {
                str(report["sources"]["trainer_source_sha256"]),
                str(report["sources"]["head_source_sha256"]),
            }
            if registered_model_hashes != reported_source_hashes:
                raise RuntimeError(
                    "registered implementation hashes differ from the run report"
                )
            try:
                registry_path = active_run.metadata_path.resolve().relative_to(
                    REPOSITORY_ROOT
                )
            except ValueError:
                registry_path = active_run.metadata_path.resolve()
            report["experiment_registry"] = {
                "integrated": True,
                "t03_complete": False,
                "run_id": active_run.run_id,
                "metadata_path": registry_path.as_posix(),
                "status_at_report_write": "running",
                "input_roles": {
                    "config": 1,
                    "data": len(data_files),
                    "model": len(model_files),
                    "checkpoint": 0,
                },
                "cache_config_and_model_hashes_recorded_in_validated_report": True,
                "implementation_source_hashes_match_registry_inputs": True,
                "runtime_packages_gpu_git_recorded": True,
                "seeds_recorded": True,
                "metrics_recorded": False,
                "artifacts_recorded": False,
            }
            atomic_write_json(args.output_dir / "report.json", report)
            for run in report["runs"]:
                seed = int(run["seed"])
                metrics: dict[str, Any] = {
                    "phone_macro_f1": _metric_value(
                        run["phone"]["metrics"]["macro_f1"]
                    ),
                    "phone_auprc": _metric_value(run["phone"]["metrics"]["auprc"]),
                    "phone_decision_threshold": _metric_value(
                        run["phone"]["metrics"]["decision_threshold"]
                    ),
                }
                for outcome in OUTCOMES:
                    for metric in ("qwk", "spearman", "mae", "icc_2_1"):
                        metrics[f"{outcome}_{metric}"] = _metric_value(
                            run["utterance"]["outcomes"][outcome]["metrics"][metric]
                        )
                active_run.log_metrics(metrics, step=seed, split="dev")
                artifact_paths = {
                    f"seed_{seed}_checkpoint": (run["artifacts"]["checkpoint"]["path"]),
                    f"seed_{seed}_phone_predictions": (
                        run["phone"]["predictions"]["path"]
                    ),
                    f"seed_{seed}_utterance_predictions": (
                        run["utterance"]["predictions"]["path"]
                    ),
                    f"seed_{seed}_slice_metrics": (
                        run["artifacts"]["slice_metrics"]["path"]
                    ),
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
                    "phone_macro_f1_mean": _metric_value(
                        report["summary"]["phone"]["macro_f1"]["mean"]
                    ),
                    "phone_macro_f1_sample_std": _metric_value(
                        report["summary"]["phone"]["macro_f1"]["sample_std"]
                    ),
                    "phone_auprc_mean": _metric_value(
                        report["summary"]["phone"]["auprc"]["mean"]
                    ),
                    "phone_auprc_sample_std": _metric_value(
                        report["summary"]["phone"]["auprc"]["sample_std"]
                    ),
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
            atomic_write_json(args.output_dir / "report.json", report)
            active_run.add_artifact(
                "final_report",
                args.output_dir / "report.json",
                kind="evaluation_report",
            )
    except BaseException:
        if output_published_by_this_run:
            _discard_published_output(args.output_dir)
        raise
    return report, registry.metadata_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train three seeded CUDA AdamW heads from a completed frozen "
            "WavLM cache; dev is evaluated once per seed."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/wavlm_base_plus_frozen_head_v1.yaml"),
    )
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--cache-report", type=Path)
    parser.add_argument("--explicit-label-cache", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--registry-root", type=Path)
    parser.add_argument("--run-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, registry_path = run_registered_experiment(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "experiment_id": report["experiment_id"],
                "seeds": report["summary"]["seeds_completed"],
                "development_only": True,
                "oracle_alignment": True,
                "report": str(args.output_dir / "report.json"),
                "registry_metadata": str(registry_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
