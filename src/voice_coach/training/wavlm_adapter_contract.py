"""Fail-closed CPU preflight for the WP4 S104 WavLM adapter stage.

The preflight never imports ``torch``, initializes CUDA, downloads a model, or
opens raw audio.  It validates the immutable S103 branch decision, pinned
implementation and data artifacts, protected-split scanner, deterministic
speaker partition, and preregistered S104 policy before publishing one
aggregate-only execution lock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.dataset as ds  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
EXECUTION_LOCK_SCHEMA_VERSION = "voice_coach.wavlm_s104_execution_lock.v1"
S103_COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_comparison_commit.v1"
S103_REPORT_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_comparison.v1"
S103_RECEIPT_SCHEMA_VERSION = "voice_coach.wavlm_frozen_head_comparison_receipt.v1"
ALLOWED_SPLITS = ("train", "dev")
FORBIDDEN_SPLITS = ("stack", "calibration", "locked_test")
SEEDS = (42, 43, 44)
OUTCOMES = (
    "pronunciation_score",
    "fluency_score",
    "comprehensibility_score",
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REGISTRY_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UTTERANCE_HASH_NAMESPACE = "voice-coach.aihub71490.utterance.v1"
FORBIDDEN_OUTPUT_PARTS = frozenset(
    {
        ".env",
        "raw",
        "restricted",
        "stack",
        "calibration",
        "locked",
        "locked_test",
        "locked-test",
    }
)
FORBIDDEN_OUTPUT_PREFIXES = (
    "raw",
    "restricted",
    "stack",
    "calibration",
    "locked",
)
MANIFEST_REQUIRED_FIELDS = {
    "id",
    "speaker_id",
    "split",
    "audio_path",
    "pcm_sha256",
    "prompt_sha256",
    "qc_flags",
    "target_phone_intervals",
    "phone_alignments",
    "l1",
    "gender",
    "proficiency",
}
EXPLICIT_REQUIRED_FIELDS = {
    "utterance_id_sha256",
    "speaker_id_sha256",
    "split",
    *OUTCOMES,
}
CANONICAL_TRAINABLE_NAME_PATTERNS = (
    (
        r"^wavlm\.encoder\.layers\.[0-9]+\.adapter\."
        r"(layer_norm|down|up)\.(weight|bias)$"
    ),
    r"^phone_head\.",
    r"^utterance_head\.",
)
IMPLEMENTATION_ROLES = (
    "preflight_script",
    "trainer_script",
    "contract_module",
    "training_module",
    "adapter_module",
    "head_module",
    "registry_module",
    "audio_module",
    "wav_qc_module",
    "interval_module",
    "ssl_module",
    "metrics_module",
    "project_manifest",
    "scorer_lock",
)


class S104ContractError(ValueError):
    """Raised when S104 readiness cannot be established safely."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    output: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in output:
            raise S104ContractError(f"YAML contains duplicate key: {key!r}")
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class S103Decision:
    selected_candidate: str
    gate_status: str
    gate_passed: bool
    estimate: float
    threshold: float
    commit_sha256: str
    report_sha256: str
    receipt_sha256: str
    generation_id: str
    protected_source_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class DataPreflight:
    included_utterances: int
    split_rows: Mapping[str, int]
    split_speakers: Mapping[str, int]
    primary_split_rows: Mapping[str, int]
    primary_split_speakers: Mapping[str, int]
    inner_train_rows: int
    inner_validation_rows: int
    inner_train_speakers: int
    inner_validation_speakers: int
    utterance_score_observed_rows: Mapping[str, int]
    source_paths: tuple[Path, ...]
    source_hashes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ModelCachePreflight:
    model_id: str
    revision: str
    config_sha256: str
    checkpoint_sha256: str
    checkpoint_size_bytes: int
    source_paths: tuple[Path, ...]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S104ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S104ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S104ContractError(f"{name} must be a non-empty string")
    return value


def _integer(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S104ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S104ContractError(f"{name} must be at least {minimum}")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S104ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise S104ContractError(f"{name} must be finite")
    return result


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise S104ContractError(f"{name} must be boolean")
    return value


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise S104ContractError(f"{name} must be a lowercase SHA-256")
    return value


def _expect_equal(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise S104ContractError(f"{name} must equal {expected!r}")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise S104ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_nonfinite_tree(nested, name)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_nonfinite_tree(nested, name)


def _json_object_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise S104ContractError(f"JSON contains duplicate key: {key!r}")
        output[key] = value
    return output


def load_json_strict(path: Path, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                S104ContractError(f"{name} contains {token}")
            ),
        )
    except S104ContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise S104ContractError(f"{name} is not valid UTF-8 JSON") from error
    _reject_nonfinite_tree(value, name)
    return _mapping(value, name)


def load_config(path: Path) -> dict[str, Any]:
    try:
        document = yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=_UniqueKeyLoader,
        )
    except S104ContractError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise S104ContractError("S104 config is not valid UTF-8 YAML") from error
    return dict(_mapping(document, "S104 config"))


def sha256_file(path: Path) -> str:
    if not path.is_file():
        raise S104ContractError(f"required file is absent: {path}")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after:
        raise S104ContractError(f"file changed while hashing: {path}")
    return digest.hexdigest()


def _canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
    }
    if pretty:
        options["indent"] = 2
    return (json.dumps(value, **options) + "\n").encode("utf-8")


def _binding_sha256(document: Mapping[str, Any]) -> str:
    core = dict(document)
    core.pop("binding_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(core, pretty=False)).hexdigest()


def _hash_utterance_id(source_id: object) -> str:
    value = _string(source_id, "manifest id")
    return hashlib.sha256(
        f"{UTTERANCE_HASH_NAMESPACE}:{value}".encode("utf-8")
    ).hexdigest()


def _is_env_path(path: Path) -> bool:
    return any(part.casefold().startswith(".env") for part in path.parts)


def _path_value(value: object, name: str) -> Path:
    if isinstance(value, Path):
        return value
    return Path(_string(value, name))


def _resolve_input(
    workspace_root: Path,
    value: object,
    name: str,
    *,
    kind: str = "file",
) -> Path:
    raw = _path_value(value, name)
    if _is_env_path(raw):
        raise S104ContractError(f"{name} may not reference an environment file")
    candidate = raw if raw.is_absolute() else workspace_root / raw
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise S104ContractError(f"{name} does not exist") from error
    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError as error:
        raise S104ContractError(f"{name} escapes the workspace") from error
    if _is_env_path(relative):
        raise S104ContractError(f"{name} may not reference an environment file")
    if kind == "file" and not resolved.is_file():
        raise S104ContractError(f"{name} must be a regular file")
    if kind == "directory" and not resolved.is_dir():
        raise S104ContractError(f"{name} must be a directory")
    return resolved


def _output_path_is_safe(path: Path) -> bool:
    for part in path.parts:
        lowered = part.casefold()
        if (
            lowered in FORBIDDEN_OUTPUT_PARTS
            or lowered.startswith(".env")
            or any(
                lowered.startswith(f"{prefix}{separator}")
                for prefix in FORBIDDEN_OUTPUT_PREFIXES
                for separator in ("_", "-")
            )
        ):
            return False
    return True


def _resolve_output(
    workspace_root: Path,
    value: object,
    name: str,
) -> Path:
    raw = _path_value(value, name)
    if _is_env_path(raw):
        raise S104ContractError(f"{name} is in a protected data path")
    if raw.is_absolute():
        candidate = raw
    else:
        candidate = workspace_root / raw
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError as error:
        raise S104ContractError(f"{name} escapes the workspace") from error
    if not _output_path_is_safe(relative):
        raise S104ContractError(f"{name} is in a protected data path")
    if resolved.exists() and not resolved.is_file():
        raise S104ContractError(f"{name} exists and is not a regular file")
    return resolved


def _resolve_output_directory(
    workspace_root: Path,
    value: object,
    name: str,
) -> Path:
    raw = _path_value(value, name)
    if _is_env_path(raw):
        raise S104ContractError(f"{name} is in a protected data path")
    candidate = raw if raw.is_absolute() else workspace_root / raw
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError as error:
        raise S104ContractError(f"{name} escapes the workspace") from error
    if not _output_path_is_safe(relative):
        raise S104ContractError(f"{name} is in a protected data path")
    if resolved.exists() and not resolved.is_dir():
        raise S104ContractError(f"{name} exists and is not a directory")
    return resolved


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _registry_run_id(value: object, name: str) -> str:
    run_id = _string(value, name)
    if not REGISTRY_RUN_ID_PATTERN.fullmatch(run_id) or run_id in {".", ".."}:
        raise S104ContractError(
            f"{name} must contain only letters, digits, '.', '_', or '-'"
        )
    return run_id


def _validate_candidate(
    candidate: Mapping[str, Any],
    *,
    name: str,
) -> None:
    expected = {
        "base_plus": {
            "model_id": "microsoft/wavlm-base-plus",
            "revision": "4c66d4806a428f2e922ccfa1a962776e232d487b",
            "hidden_size": 768,
            "encoder_layer_count": 12,
            "do_normalize": False,
        },
        "large": {
            "model_id": "microsoft/wavlm-large",
            "revision": "c1423ed94bb01d80a3f5ce5bc39f6026a0f4828c",
            "hidden_size": 1024,
            "encoder_layer_count": 24,
            "do_normalize": True,
        },
    }[name]
    for key, expected_value in expected.items():
        _expect_equal(candidate.get(key), expected_value, f"candidates.{name}.{key}")
    _string(candidate.get("cache_dir"), f"candidates.{name}.cache_dir")
    _sha256(
        candidate.get("source_config_sha256"),
        f"candidates.{name}.source_config_sha256",
    )
    _sha256(
        candidate.get("source_checkpoint_sha256"),
        f"candidates.{name}.source_checkpoint_sha256",
    )


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate policy choices without touching S103, data, or a GPU."""

    _expect_equal(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")
    experiment = _mapping(config.get("experiment"), "experiment")
    expected_experiment = {
        "stage": "s104_adapter",
        "development_only": True,
        "oracle_alignment": True,
        "silver_label": True,
        "promotion_decision_allowed": False,
        "seeds": list(SEEDS),
    }
    _string(experiment.get("id"), "experiment.id")
    for key, expected in expected_experiment.items():
        _expect_equal(experiment.get(key), expected, f"experiment.{key}")

    s103 = _mapping(config.get("s103"), "s103")
    expected_s103 = {
        "commit_schema_version": S103_COMMIT_SCHEMA_VERSION,
        "report_schema_version": S103_REPORT_SCHEMA_VERSION,
        "receipt_schema_version": S103_RECEIPT_SCHEMA_VERSION,
        "metric": "phone_macro_f1",
        "effect": "large_minus_base",
        "gate_rule": "point_estimate_gte_threshold",
        "threshold_absolute": 0.03,
        "passed_candidate": "large",
        "failed_candidate": "base_plus",
    }
    _string(s103.get("commit_manifest"), "s103.commit_manifest")
    for key, expected in expected_s103.items():
        _expect_equal(s103.get(key), expected, f"s103.{key}")

    candidates = _mapping(config.get("candidates"), "candidates")
    if set(candidates) != {"base_plus", "large"}:
        raise S104ContractError("candidates must contain only base_plus and large")
    for name in ("base_plus", "large"):
        _validate_candidate(_mapping(candidates[name], f"candidates.{name}"), name=name)

    data = _mapping(config.get("data"), "data")
    zones = _mapping(data.get("zone_contract"), "data.zone_contract")
    expected_zones = {
        "dataset_root": "R0",
        "modeling_manifest": "R1",
        "prompt_challenge_reservation": "R1",
        "explicit_utterance_labels": "D1",
        "predictions": "D1",
        "reports": "A1",
    }
    if dict(zones) != expected_zones:
        raise S104ContractError("data.zone_contract differs from the privacy contract")
    for name in (
        "dataset_root",
        "modeling_manifest",
        "prompt_challenge_reservation",
        "explicit_utterance_labels",
    ):
        _string(data.get(name), f"data.{name}")
    for name in (
        "modeling_manifest_sha256",
        "prompt_challenge_reservation_sha256",
        "explicit_utterance_labels_sha256",
    ):
        _sha256(data.get(name), f"data.{name}")
    _integer(
        data.get("expected_included_utterances"),
        "data.expected_included_utterances",
        minimum=1,
    )
    _expect_equal(
        data.get("representation_source"),
        "online_raw_audio",
        "data.representation_source",
    )
    _expect_equal(
        data.get("pooled_feature_cache_allowed_for_encoder_gradient"),
        False,
        "data.pooled_feature_cache_allowed_for_encoder_gradient",
    )
    _expect_equal(data.get("allowed_splits"), list(ALLOWED_SPLITS), "allowed_splits")
    _expect_equal(data.get("training_split"), "train", "training_split")
    _expect_equal(data.get("development_split"), "dev", "development_split")
    _expect_equal(
        data.get("forbidden_splits"),
        list(FORBIDDEN_SPLITS),
        "forbidden_splits",
    )
    _expect_equal(data.get("exclude_qc_flagged"), True, "exclude_qc_flagged")
    _expect_equal(
        data.get("exclude_reserved_prompts"),
        True,
        "exclude_reserved_prompts",
    )
    _expect_equal(
        data.get("primary_analysis_scope"),
        {
            "l1": ["Chinese", "Japanese"],
            "gender": ["F", "M"],
        },
        "data.primary_analysis_scope",
    )
    inner = _mapping(data.get("inner_validation"), "data.inner_validation")
    _expect_equal(
        inner.get("source"),
        "train_speaker_sha256",
        "inner_validation.source",
    )
    modulus = _integer(inner.get("modulus"), "inner_validation.modulus", minimum=2)
    buckets = _list(
        inner.get("validation_buckets"),
        "inner_validation.validation_buckets",
    )
    if (
        not buckets
        or len(set(buckets)) != len(buckets)
        or any(
            isinstance(bucket, bool)
            or not isinstance(bucket, int)
            or bucket < 0
            or bucket >= modulus
            for bucket in buckets
        )
    ):
        raise S104ContractError("inner validation buckets are invalid")

    adapter = _mapping(config.get("adapter"), "adapter")
    expected_adapter = {
        "family": "residual_bottleneck",
        "placement": "every_encoder_layer",
        "activation": "gelu",
        "zero_initialize_output": True,
        "full_fine_tuning_allowed": False,
        "top_block_partial_allowed_in_this_run": False,
        "trainable_parameter_roles": [
            "encoder_adapter",
            "phone_head",
            "utterance_head",
        ],
    }
    for key, expected in expected_adapter.items():
        _expect_equal(adapter.get(key), expected, f"adapter.{key}")
    _integer(adapter.get("bottleneck_dim"), "adapter.bottleneck_dim", minimum=1)
    dropout = _finite(adapter.get("dropout"), "adapter.dropout")
    if not 0 <= dropout < 1:
        raise S104ContractError("adapter.dropout must be in [0, 1)")
    patterns = _list(
        adapter.get("trainable_name_patterns"),
        "adapter.trainable_name_patterns",
    )
    if tuple(patterns) != CANONICAL_TRAINABLE_NAME_PATTERNS:
        raise S104ContractError(
            "adapter trainable_name_patterns differs from the canonical allowlist"
        )
    for pattern in patterns:
        try:
            re.compile(_string(pattern, "trainable parameter pattern"))
        except re.error as error:
            raise S104ContractError("invalid trainable parameter regex") from error

    heads = _mapping(config.get("heads"), "heads")
    phone = _mapping(heads.get("phone"), "heads.phone")
    utterance = _mapping(heads.get("utterance"), "heads.utterance")
    _expect_equal(phone.get("family"), "embedding_target_mlp", "heads.phone.family")
    _expect_equal(
        utterance.get("family"),
        "bounded_multitask_mlp",
        "heads.utterance.family",
    )
    _expect_equal(utterance.get("outcomes"), list(OUTCOMES), "utterance outcomes")
    _expect_equal(utterance.get("score_range"), [1, 5], "utterance score range")

    optimization = _mapping(config.get("optimization"), "optimization")
    expected_optimization = {
        "optimizer": "AdamW",
        "selection_data": "train_inner_validation_only",
        "oom_policy": "fail_and_record_no_automatic_retry",
        "phone_threshold_tie_break": [
            "closest_to_0_5",
            "lower_threshold",
        ],
        "early_stopping_order": [
            "phone_macro_f1",
            "comprehensibility_score_icc_2_1",
            "earliest_epoch",
        ],
        "undefined_secondary_metric_policy": "no_improvement",
        "refit_after_inner_selection": False,
        "last_partial_accumulation_policy": "divide_by_actual_utterances",
        "fit_metadata_source": "train_inner_fit_only",
        "phone_loss_reduction": "mean_per_utterance",
        "utterance_loss_reduction": "mean_observed_outcomes_per_utterance",
    }
    for key, expected in expected_optimization.items():
        _expect_equal(optimization.get(key), expected, f"optimization.{key}")
    for name in (
        "adapter_learning_rate",
        "head_learning_rate",
        "weight_decay",
        "gradient_clip_norm",
        "phone_loss_weight",
        "utterance_loss_weight",
    ):
        if _finite(optimization.get(name), f"optimization.{name}") <= 0:
            raise S104ContractError(f"optimization.{name} must be positive")
    for name in (
        "maximum_epochs",
        "minimum_epochs",
        "early_stopping_patience",
        "gradient_accumulation_utterances",
        "bounded_shuffle_buffer_utterances",
    ):
        _integer(optimization.get(name), f"optimization.{name}", minimum=1)
    if int(optimization["minimum_epochs"]) > int(optimization["maximum_epochs"]):
        raise S104ContractError("minimum_epochs exceeds maximum_epochs")
    threshold_grid = _mapping(
        optimization.get("phone_threshold_grid"),
        "optimization.phone_threshold_grid",
    )
    if dict(threshold_grid) != {"start": 0.10, "stop": 0.90, "step": 0.05}:
        raise S104ContractError(
            "phone threshold grid differs from the train-inner preregistration"
        )

    evaluation = _mapping(config.get("evaluation"), "evaluation")
    expected_evaluation = {
        "development_only": True,
        "dev_inference_passes_per_seed": 1,
        "primary_phone_metric": "phone_macro_f1",
        "primary_utterance_outcome": "comprehensibility_score",
        "primary_utterance_metric": "icc_2_1",
        "word_metric_status": "not_evaluable",
        "dev_may_select_candidate_or_hyperparameters": False,
        "stack_access_allowed": False,
        "calibration_access_allowed": False,
        "locked_test_access_allowed": False,
    }
    for key, expected in expected_evaluation.items():
        _expect_equal(evaluation.get(key), expected, f"evaluation.{key}")
    thresholds = _mapping(
        evaluation.get("engineering_thresholds"),
        "evaluation.engineering_thresholds",
    )
    _expect_equal(thresholds.get("phone_macro_f1"), 0.75, "phone threshold")
    _expect_equal(
        thresholds.get("primary_utterance_icc_2_1"),
        0.65,
        "utterance threshold",
    )

    runtime = _mapping(config.get("runtime"), "runtime")
    expected_runtime = {
        "require_cuda_for_training": True,
        "cpu_preflight_only": True,
        "sample_rate_hz": 16000,
        "maximum_chunk_seconds": 20.0,
        "wavlm_conv_kernels": [10, 3, 3, 3, 3, 2, 2],
        "wavlm_conv_strides": [5, 2, 2, 2, 2, 2, 2],
        "minimum_model_input_samples": 400,
        "short_chunk_attention_policy": ("padded_receptive_field_marked_valid"),
        "encoder_layerdrop": 0.0,
        "apply_spec_augment": False,
        "freeze_feature_encoder_internal_gradient": True,
        "frozen_backbone_mode_during_training": "train_with_dropout",
        "evaluation_module_mode": "eval",
        "mixed_precision": "bfloat16",
        "gradient_checkpointing": True,
        "deterministic_algorithms": True,
        "cublas_workspace_config": ":4096:8",
        "environment_files_forbidden": True,
    }
    for key, expected in expected_runtime.items():
        _expect_equal(runtime.get(key), expected, f"runtime.{key}")

    implementation = _mapping(config.get("implementation"), "implementation")
    if set(implementation) != set(IMPLEMENTATION_ROLES):
        raise S104ContractError("implementation source roles differ")
    for role in IMPLEMENTATION_ROLES:
        record = _mapping(implementation[role], f"implementation.{role}")
        _string(record.get("path"), f"implementation.{role}.path")
        _sha256(record.get("sha256"), f"implementation.{role}.sha256")

    outputs = _mapping(config.get("outputs"), "outputs")
    for name in (
        "execution_lock",
        "generation_root",
        "commit_manifest",
        "registry_root",
    ):
        _string(outputs.get(name), f"outputs.{name}")
    _registry_run_id(
        outputs.get("planned_registry_run_id"),
        "outputs.planned_registry_run_id",
    )
    _expect_equal(outputs.get("atomic_publish"), True, "outputs.atomic_publish")
    _expect_equal(
        outputs.get("content_addressed_generation"),
        True,
        "outputs.content_addressed_generation",
    )
    _expect_equal(
        outputs.get("commit_pointer_required_for_visibility"),
        True,
        "outputs.commit_pointer_required_for_visibility",
    )
    _expect_equal(
        outputs.get("immutable_execution_lock"),
        True,
        "outputs.immutable_execution_lock",
    )
    _expect_equal(
        outputs.get("row_level_outputs_access_scope"),
        "restricted_internal",
        "outputs.row_level_outputs_access_scope",
    )
    _expect_equal(
        outputs.get("aggregate_report_shareable"),
        False,
        "outputs.aggregate_report_shareable",
    )
    _expect_equal(
        outputs.get("expected_completed_registry_artifact_count"),
        16,
        "outputs.expected_completed_registry_artifact_count",
    )
    artifact_contract = _mapping(
        outputs.get("artifact_contract"),
        "outputs.artifact_contract",
    )
    _expect_equal(
        artifact_contract.get("per_seed"),
        [
            "adapter_and_heads_checkpoint",
            "phone_dev_predictions",
            "utterance_dev_predictions",
            "dev_slice_metrics",
            "resource_and_trainable_parameter_report",
        ],
        "outputs.artifact_contract.per_seed",
    )
    _expect_equal(
        artifact_contract.get("aggregate"),
        ["experiment_report"],
        "outputs.artifact_contract.aggregate",
    )
    _expect_equal(
        artifact_contract.get("per_seed_filenames"),
        {
            "adapter_and_heads_checkpoint": "adapter_and_heads.safetensors",
            "phone_dev_predictions": "phone_dev_predictions.parquet",
            "utterance_dev_predictions": "utterance_dev_predictions.parquet",
            "dev_slice_metrics": "dev_slice_metrics.json",
            "resource_and_trainable_parameter_report": (
                "resource_and_trainable_parameter_report.json"
            ),
        },
        "outputs.artifact_contract.per_seed_filenames",
    )
    _expect_equal(
        artifact_contract.get("aggregate_filenames"),
        {"experiment_report": "experiment_report.json"},
        "outputs.artifact_contract.aggregate_filenames",
    )
    _expect_equal(
        artifact_contract.get("checkpoint_contains_full_backbone"),
        False,
        "outputs.artifact_contract.checkpoint_contains_full_backbone",
    )
    _expect_equal(
        artifact_contract.get("unexpected_artifacts_allowed"),
        False,
        "outputs.artifact_contract.unexpected_artifacts_allowed",
    )
    failure_registry = _mapping(
        outputs.get("failure_registry"),
        "outputs.failure_registry",
    )
    expected_failure_registry = {
        "required": True,
        "status": "failed",
        "failure_fields": ["stage", "exception_type", "oom"],
        "raw_identifier_or_path_allowed": False,
        "partial_prediction_publish_allowed": False,
    }
    if dict(failure_registry) != expected_failure_registry:
        raise S104ContractError("outputs.failure_registry differs from the contract")


def _validate_file_record(
    workspace_root: Path,
    record_value: object,
    name: str,
) -> tuple[Path, str]:
    record = _mapping(record_value, name)
    path = _resolve_input(workspace_root, record.get("path"), f"{name}.path")
    expected_sha = _sha256(record.get("sha256"), f"{name}.sha256")
    size = _integer(record.get("size_bytes"), f"{name}.size_bytes", minimum=0)
    if path.stat().st_size != size:
        raise S104ContractError(f"{name} size differs")
    actual_sha = sha256_file(path)
    if actual_sha != expected_sha:
        raise S104ContractError(f"{name} SHA-256 differs")
    return path, actual_sha


def _validate_registry_transitive_files(
    workspace_root: Path,
    registry_path: Path,
    *,
    name: str,
) -> tuple[Path, ...]:
    registry = load_json_strict(registry_path, name)
    _expect_equal(registry.get("status"), "completed", f"{name} status")
    _string(registry.get("run_id"), f"{name} run_id")
    inputs = _mapping(registry.get("inputs"), f"{name} inputs")
    expected_input_counts = {
        "config": 1,
        "data": 33,
        "model": 2,
        "checkpoint": 0,
    }
    if set(inputs) != set(expected_input_counts):
        raise S104ContractError(f"{name} input roles differ")
    paths: list[Path] = []
    seen_paths: set[Path] = set()
    for role in sorted(expected_input_counts):
        records = _list(inputs.get(role), f"{name} inputs.{role}")
        if len(records) != expected_input_counts[role]:
            raise S104ContractError(f"{name} inputs.{role} count differs")
        for index, record in enumerate(records):
            path, _ = _validate_file_record(
                workspace_root,
                record,
                f"{name} inputs.{role}[{index}]",
            )
            if path in seen_paths:
                raise S104ContractError(f"{name} has duplicate transitive input")
            seen_paths.add(path)
            paths.append(path)
    artifacts = _list(registry.get("artifacts"), f"{name} artifacts")
    expected_artifact_names = {
        "final_report",
        *{
            f"seed_{seed}_{suffix}"
            for seed in SEEDS
            for suffix in (
                "checkpoint",
                "phone_predictions",
                "utterance_predictions",
                "slice_metrics",
            )
        },
    }
    if len(artifacts) != 13:
        raise S104ContractError(f"{name} artifact count differs")
    artifact_names: set[str] = set()
    for index, record in enumerate(artifacts):
        artifact = _mapping(record, f"{name} artifacts[{index}]")
        artifact_name = _string(
            artifact.get("name"),
            f"{name} artifacts[{index}].name",
        )
        if artifact_name in artifact_names:
            raise S104ContractError(f"{name} has duplicate artifact name")
        artifact_names.add(artifact_name)
        path, _ = _validate_file_record(
            workspace_root,
            artifact,
            f"{name} artifacts[{index}]",
        )
        if path in seen_paths:
            raise S104ContractError(f"{name} reuses an input as an artifact")
        seen_paths.add(path)
        paths.append(path)
    if artifact_names != expected_artifact_names:
        raise S104ContractError(f"{name} artifact names differ")
    return tuple(paths)


def _validate_s103_sources(
    workspace_root: Path,
    receipt: Mapping[str, Any],
) -> tuple[Path, ...]:
    sources = _mapping(receipt.get("execution_sources"), "S103 execution sources")
    if set(sources) != {
        "config",
        "comparison_script",
        "comparison_module",
        "base_plus",
        "large",
    }:
        raise S104ContractError("S103 execution source roles differ")
    paths: list[Path] = []
    for role in ("config", "comparison_script", "comparison_module"):
        path, _ = _validate_file_record(
            workspace_root,
            sources.get(role),
            f"S103 source {role}",
        )
        paths.append(path)
    for variant in ("base_plus", "large"):
        source = _mapping(sources.get(variant), f"S103 {variant} sources")
        if set(source) != {"report", "registry", "predictions"}:
            raise S104ContractError(f"S103 {variant} source roles differ")
        registry_path: Path | None = None
        for role in ("report", "registry"):
            path, _ = _validate_file_record(
                workspace_root,
                source.get(role),
                f"S103 {variant} {role}",
            )
            paths.append(path)
            if role == "registry":
                registry_path = path
        if registry_path is None:
            raise S104ContractError(f"S103 {variant} registry is absent")
        registry_record = _mapping(
            source.get("registry"),
            f"S103 {variant} registry source",
        )
        registry = load_json_strict(
            registry_path,
            f"S103 {variant} registry",
        )
        _expect_equal(
            registry_record.get("status"),
            "completed",
            f"S103 {variant} registry source status",
        )
        _expect_equal(
            registry_record.get("run_id"),
            registry.get("run_id"),
            f"S103 {variant} registry source run_id",
        )
        paths.extend(
            _validate_registry_transitive_files(
                workspace_root,
                registry_path,
                name=f"S103 {variant} registry",
            )
        )
        predictions = _mapping(
            source.get("predictions"),
            f"S103 {variant} predictions",
        )
        if set(predictions) != {f"seed_{seed}" for seed in SEEDS}:
            raise S104ContractError(f"S103 {variant} prediction seeds differ")
        for seed in SEEDS:
            per_seed = _mapping(
                predictions[f"seed_{seed}"],
                f"S103 {variant} seed {seed}",
            )
            if set(per_seed) != {"phone", "utterance"}:
                raise S104ContractError(
                    f"S103 {variant} seed {seed} prediction tasks differ"
                )
            for task in ("phone", "utterance"):
                path, _ = _validate_file_record(
                    workspace_root,
                    per_seed.get(task),
                    f"S103 {variant} seed {seed} {task}",
                )
                paths.append(path)
    return tuple(paths)


def validate_s103_decision(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> S103Decision:
    """Validate the committed aggregate comparison and resolve one branch."""

    s103 = _mapping(config.get("s103"), "s103")
    commit_path = _resolve_input(
        workspace_root,
        s103.get("commit_manifest"),
        "S103 commit manifest",
    )
    commit = load_json_strict(commit_path, "S103 commit")
    _expect_equal(
        commit.get("schema_version"),
        S103_COMMIT_SCHEMA_VERSION,
        "S103 commit schema_version",
    )
    _expect_equal(commit.get("status"), "committed", "S103 commit status")
    commit_binding = _sha256(
        commit.get("binding_sha256"),
        "S103 commit binding_sha256",
    )
    if _binding_sha256(commit) != commit_binding:
        raise S104ContractError("S103 commit binding is invalid")
    generation_id = _sha256(commit.get("generation_id"), "S103 generation_id")
    report_path, report_sha = _validate_file_record(
        workspace_root,
        commit.get("comparison_report"),
        "S103 comparison report",
    )
    receipt_path, receipt_sha = _validate_file_record(
        workspace_root,
        commit.get("execution_receipt"),
        "S103 execution receipt",
    )
    if report_sha != generation_id:
        raise S104ContractError("S103 generation id differs from report bytes")
    report = load_json_strict(report_path, "S103 report")
    receipt = load_json_strict(receipt_path, "S103 receipt")
    _expect_equal(
        report.get("schema_version"),
        S103_REPORT_SCHEMA_VERSION,
        "S103 report schema_version",
    )
    _expect_equal(report.get("status"), "completed", "S103 report status")
    _expect_equal(
        receipt.get("schema_version"),
        S103_RECEIPT_SCHEMA_VERSION,
        "S103 receipt schema_version",
    )
    _expect_equal(receipt.get("status"), "completed", "S103 receipt status")
    _expect_equal(
        receipt.get("generation_id"),
        generation_id,
        "S103 receipt generation_id",
    )
    receipt_binding = _sha256(
        receipt.get("binding_sha256"),
        "S103 receipt binding_sha256",
    )
    if _binding_sha256(receipt) != receipt_binding:
        raise S104ContractError("S103 receipt binding is invalid")
    if _mapping(receipt.get("comparison_report"), "receipt report") != _mapping(
        commit.get("comparison_report"),
        "commit report",
    ):
        raise S104ContractError("S103 receipt and commit report records differ")
    report_sources = _mapping(report.get("sources"), "S103 report sources")
    receipt_sources = _mapping(
        receipt.get("execution_sources"),
        "S103 receipt execution sources",
    )
    if report_sources != receipt_sources:
        raise S104ContractError("S103 report and receipt execution sources differ")
    statistics = {
        "metrics": report.get("metrics"),
        "primary_comparison": report.get("primary_comparison"),
        "multiplicity": report.get("multiplicity"),
    }
    statistics_sha = hashlib.sha256(
        _canonical_json_bytes(statistics, pretty=False)
    ).hexdigest()
    _expect_equal(
        receipt.get("statistics_sha256"),
        statistics_sha,
        "S103 receipt statistics_sha256",
    )
    _expect_equal(
        commit.get("statistics_sha256"),
        statistics_sha,
        "S103 commit statistics_sha256",
    )

    _expect_equal(report.get("development_only"), True, "S103 development_only")
    _expect_equal(report.get("oracle_alignment"), True, "S103 oracle_alignment")
    primary = _mapping(report.get("primary_comparison"), "S103 primary comparison")
    _expect_equal(primary.get("metric"), s103.get("metric"), "S103 metric")
    _expect_equal(primary.get("effect"), s103.get("effect"), "S103 effect")
    bootstrap = _mapping(
        primary.get("paired_speaker_bootstrap"),
        "S103 primary bootstrap",
    )
    estimate = _finite(bootstrap.get("estimate"), "S103 primary estimate")
    gate = _mapping(primary.get("gate"), "S103 gate")
    threshold = _finite(gate.get("threshold_absolute"), "S103 gate threshold")
    _expect_equal(
        threshold,
        s103.get("threshold_absolute"),
        "S103 gate threshold pin",
    )
    _expect_equal(gate.get("rule"), s103.get("gate_rule"), "S103 gate rule")
    passed = _boolean(
        gate.get("point_estimate_passed"),
        "S103 point_estimate_passed",
    )
    if passed != (estimate >= threshold):
        raise S104ContractError("S103 point-estimate gate is inconsistent")
    status = _string(gate.get("status"), "S103 gate status")
    if status != ("passed" if passed else "failed"):
        raise S104ContractError("S103 gate status is inconsistent")
    summary = _mapping(report.get("summary"), "S103 summary")
    _expect_equal(
        summary.get("large_adoption_gate_passed"),
        passed,
        "S103 summary gate",
    )
    _expect_equal(
        summary.get("locked_test_remains_unread"),
        True,
        "S103 locked guard",
    )
    _expect_equal(summary.get("stack_remains_unread"), True, "S103 stack guard")
    _expect_equal(
        summary.get("calibration_remains_unread"),
        True,
        "S103 calibration guard",
    )
    contract = _mapping(report.get("contract"), "S103 contract")
    leakage = _mapping(contract.get("leakage"), "S103 leakage")
    expected_leakage = {
        "stack_accessed": False,
        "calibration_accessed": False,
        "locked_test_accessed": False,
        "raw_data_read": False,
        "environment_files_read": [],
    }
    for key, expected in expected_leakage.items():
        _expect_equal(leakage.get(key), expected, f"S103 leakage.{key}")

    protected = [
        commit_path,
        report_path,
        receipt_path,
        *_validate_s103_sources(workspace_root, receipt),
    ]
    selected = _string(
        s103.get("passed_candidate" if passed else "failed_candidate"),
        "S103 selected candidate",
    )
    return S103Decision(
        selected_candidate=selected,
        gate_status=status,
        gate_passed=passed,
        estimate=estimate,
        threshold=threshold,
        commit_sha256=sha256_file(commit_path),
        report_sha256=report_sha,
        receipt_sha256=receipt_sha,
        generation_id=generation_id,
        protected_source_paths=tuple(protected),
    )


def _load_reserved_prompts(
    path: Path,
    *,
    expected_manifest_sha256: str,
) -> set[str]:
    document = load_json_strict(path, "prompt reservation")
    source = _mapping(document.get("source"), "prompt reservation source")
    _expect_equal(
        source.get("modeling_manifest_sha256"),
        expected_manifest_sha256,
        "prompt reservation source manifest SHA-256",
    )
    entries = _list(document.get("reserved_prompts"), "reserved prompts")
    if not entries:
        raise S104ContractError("prompt reservation contains no prompt")
    hashes: set[str] = set()
    for index, entry_value in enumerate(entries):
        entry = _mapping(entry_value, f"reserved_prompts[{index}]")
        prompt = _sha256(
            entry.get("prompt_sha256"),
            f"reserved_prompts[{index}].prompt_sha256",
        )
        if prompt in hashes:
            raise S104ContractError("prompt reservation contains a duplicate")
        hashes.add(prompt)
    return hashes


def _metadata_text(
    metadata: Mapping[bytes, bytes] | None,
    key: str,
) -> str | None:
    if metadata is None:
        return None
    value = metadata.get(key.encode())
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeError as error:
        raise S104ContractError(f"Parquet metadata {key} is not UTF-8") from error


def _validate_parquet_schema(
    path: Path,
    *,
    required_fields: set[str],
    name: str,
) -> pa.Schema:
    try:
        schema = pq.ParquetFile(path).schema_arrow
    except (OSError, pa.ArrowException) as error:
        raise S104ContractError(f"{name} is not readable Parquet") from error
    missing = required_fields - set(schema.names)
    if missing:
        raise S104ContractError(f"{name} misses fields: {sorted(missing)!r}")
    return schema


def _speaker_bucket(speaker_id_sha256: str, modulus: int) -> int:
    _sha256(speaker_id_sha256, "speaker_id_sha256")
    return int(speaker_id_sha256[:16], 16) % modulus


def _scan_manifest_allowed_rows(
    *,
    path: Path,
    reserved_prompts: set[str],
    modulus: int,
    validation_buckets: set[int],
    allowed_l1: set[str],
    allowed_gender: set[str],
) -> tuple[
    dict[str, tuple[str, str]],
    dict[str, int],
    dict[str, set[str]],
    dict[str, int],
    dict[str, set[str]],
    int,
    int,
    set[str],
    set[str],
]:
    dataset = ds.dataset(path, format="parquet")
    scanner = dataset.scanner(
        columns=[
            "id",
            "speaker_id",
            "split",
            "prompt_sha256",
            "qc_flags",
            "l1",
            "gender",
            "pcm_sha256",
        ],
        filter=ds.field("split").isin(list(ALLOWED_SPLITS)),
        batch_size=8_192,
        use_threads=False,
    )
    identities: dict[str, tuple[str, str]] = {}
    split_rows = {split: 0 for split in ALLOWED_SPLITS}
    split_speakers: dict[str, set[str]] = {split: set() for split in ALLOWED_SPLITS}
    primary_split_rows = {split: 0 for split in ALLOWED_SPLITS}
    primary_split_speakers: dict[str, set[str]] = {
        split: set() for split in ALLOWED_SPLITS
    }
    inner_train_rows = 0
    inner_validation_rows = 0
    inner_train_speakers: set[str] = set()
    inner_validation_speakers: set[str] = set()
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            split = _string(row.get("split"), "manifest split")
            if split not in ALLOWED_SPLITS:
                raise S104ContractError(
                    "protected split escaped the train/dev scanner filter"
                )
            if row.get("qc_flags"):
                continue
            prompt = _sha256(row.get("prompt_sha256"), "manifest prompt_sha256")
            if prompt in reserved_prompts:
                continue
            speaker = _sha256(row.get("speaker_id"), "manifest speaker_id")
            _sha256(row.get("pcm_sha256"), "manifest pcm_sha256")
            utterance = _hash_utterance_id(row.get("id"))
            if utterance in identities:
                raise S104ContractError("manifest has duplicate included utterance")
            identities[utterance] = (speaker, split)
            split_rows[split] += 1
            split_speakers[split].add(speaker)
            in_primary_scope = (
                row.get("l1") in allowed_l1 and row.get("gender") in allowed_gender
            )
            if not in_primary_scope:
                continue
            primary_split_rows[split] += 1
            primary_split_speakers[split].add(speaker)
            if split == "train":
                if _speaker_bucket(speaker, modulus) in validation_buckets:
                    inner_validation_rows += 1
                    inner_validation_speakers.add(speaker)
                else:
                    inner_train_rows += 1
                    inner_train_speakers.add(speaker)
    return (
        identities,
        split_rows,
        split_speakers,
        primary_split_rows,
        primary_split_speakers,
        inner_train_rows,
        inner_validation_rows,
        inner_train_speakers,
        inner_validation_speakers,
    )


def _scan_explicit_labels(
    *,
    path: Path,
    manifest_identities: Mapping[str, tuple[str, str]],
    expected_manifest_sha256: str,
) -> dict[str, int]:
    schema = _validate_parquet_schema(
        path,
        required_fields=EXPLICIT_REQUIRED_FIELDS,
        name="explicit utterance labels",
    )
    _expect_equal(
        _metadata_text(schema.metadata, "voice_coach.allowed_splits"),
        "train,dev",
        "explicit label allowed_splits metadata",
    )
    _expect_equal(
        _metadata_text(schema.metadata, "voice_coach.source_manifest_sha256"),
        expected_manifest_sha256,
        "explicit label source manifest metadata",
    )
    observed = {outcome: 0 for outcome in OUTCOMES}
    seen: set[str] = set()
    dataset = ds.dataset(path, format="parquet")
    scanner = dataset.scanner(
        columns=[
            "utterance_id_sha256",
            "speaker_id_sha256",
            "split",
            *OUTCOMES,
        ],
        filter=ds.field("split").isin(list(ALLOWED_SPLITS)),
        batch_size=8_192,
        use_threads=False,
    )
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            utterance = _sha256(
                row.get("utterance_id_sha256"),
                "explicit utterance_id_sha256",
            )
            if utterance in seen:
                raise S104ContractError("explicit labels have duplicate utterance")
            seen.add(utterance)
            speaker = _sha256(
                row.get("speaker_id_sha256"),
                "explicit speaker_id_sha256",
            )
            split = _string(row.get("split"), "explicit split")
            if split not in ALLOWED_SPLITS:
                raise S104ContractError(
                    "protected split escaped explicit-label scanner filter"
                )
            expected = manifest_identities.get(utterance)
            if expected is None:
                raise S104ContractError(
                    "explicit label utterance is absent from included manifest rows"
                )
            if expected != (speaker, split):
                raise S104ContractError(
                    "explicit label speaker/split differs from manifest"
                )
            for outcome in OUTCOMES:
                value = row.get(outcome)
                if value is None or value == 0:
                    continue
                numeric = _finite(value, f"explicit {outcome}")
                if numeric not in (1.0, 2.0, 3.0, 4.0, 5.0):
                    raise S104ContractError(
                        f"explicit {outcome} is not an ordinal score 1-5"
                    )
                observed[outcome] += 1
    if seen != set(manifest_identities):
        raise S104ContractError(
            "explicit labels and included manifest identities are not one-to-one"
        )
    if any(count == 0 for count in observed.values()):
        raise S104ContractError("an utterance outcome has no observed label")
    return observed


def validate_data_preflight(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> DataPreflight:
    """Validate only train/dev metadata and labels; raw audio stays unopened."""

    data = _mapping(config.get("data"), "data")
    dataset_root = _resolve_input(
        workspace_root,
        data.get("dataset_root"),
        "dataset root",
        kind="directory",
    )
    manifest = _resolve_input(
        workspace_root,
        data.get("modeling_manifest"),
        "modeling manifest",
    )
    reservation = _resolve_input(
        workspace_root,
        data.get("prompt_challenge_reservation"),
        "prompt challenge reservation",
    )
    explicit = _resolve_input(
        workspace_root,
        data.get("explicit_utterance_labels"),
        "explicit utterance labels",
    )
    expected_hashes = {
        "modeling_manifest": _sha256(
            data.get("modeling_manifest_sha256"),
            "modeling manifest SHA-256",
        ),
        "prompt_challenge_reservation": _sha256(
            data.get("prompt_challenge_reservation_sha256"),
            "prompt reservation SHA-256",
        ),
        "explicit_utterance_labels": _sha256(
            data.get("explicit_utterance_labels_sha256"),
            "explicit labels SHA-256",
        ),
    }
    paths = {
        "modeling_manifest": manifest,
        "prompt_challenge_reservation": reservation,
        "explicit_utterance_labels": explicit,
    }
    actual_hashes = {role: sha256_file(path) for role, path in paths.items()}
    for role, expected in expected_hashes.items():
        if actual_hashes[role] != expected:
            raise S104ContractError(f"{role} SHA-256 differs from the config")
    _validate_parquet_schema(
        manifest,
        required_fields=MANIFEST_REQUIRED_FIELDS,
        name="modeling manifest",
    )
    reserved = _load_reserved_prompts(
        reservation,
        expected_manifest_sha256=actual_hashes["modeling_manifest"],
    )
    inner = _mapping(data.get("inner_validation"), "data.inner_validation")
    modulus = _integer(inner.get("modulus"), "inner modulus", minimum=2)
    validation_buckets = {
        _integer(item, "inner validation bucket", minimum=0)
        for item in _list(inner.get("validation_buckets"), "validation buckets")
    }
    primary_scope = _mapping(
        data.get("primary_analysis_scope"),
        "primary analysis scope",
    )
    allowed_l1 = {
        _string(value, "primary scope l1")
        for value in _list(primary_scope.get("l1"), "primary scope l1")
    }
    allowed_gender = {
        _string(value, "primary scope gender")
        for value in _list(primary_scope.get("gender"), "primary scope gender")
    }
    (
        identities,
        split_rows,
        split_speaker_sets,
        primary_split_rows,
        primary_split_speaker_sets,
        inner_train_rows,
        inner_validation_rows,
        inner_train_speakers,
        inner_validation_speakers,
    ) = _scan_manifest_allowed_rows(
        path=manifest,
        reserved_prompts=reserved,
        modulus=modulus,
        validation_buckets=validation_buckets,
        allowed_l1=allowed_l1,
        allowed_gender=allowed_gender,
    )
    expected_rows = _integer(
        data.get("expected_included_utterances"),
        "expected included utterances",
        minimum=1,
    )
    if len(identities) != expected_rows:
        raise S104ContractError("included manifest row count differs from the pin")
    if not split_rows["train"] or not split_rows["dev"]:
        raise S104ContractError("train and dev must both be non-empty")
    if split_speaker_sets["train"] & split_speaker_sets["dev"]:
        raise S104ContractError("train/dev speaker overlap is non-zero")
    if (
        not primary_split_rows["train"]
        or not primary_split_rows["dev"]
        or not primary_split_speaker_sets["train"]
        or not primary_split_speaker_sets["dev"]
    ):
        raise S104ContractError("primary-scope train and dev must be non-empty")
    if primary_split_speaker_sets["train"] & primary_split_speaker_sets["dev"]:
        raise S104ContractError("primary-scope train/dev speaker overlap is non-zero")
    if inner_train_speakers & inner_validation_speakers:
        raise S104ContractError("inner train/validation speaker overlap is non-zero")
    if not inner_train_rows or not inner_validation_rows:
        raise S104ContractError("inner train/validation must both be non-empty")
    observed = _scan_explicit_labels(
        path=explicit,
        manifest_identities=identities,
        expected_manifest_sha256=actual_hashes["modeling_manifest"],
    )
    return DataPreflight(
        included_utterances=len(identities),
        split_rows=dict(split_rows),
        split_speakers={
            split: len(values) for split, values in split_speaker_sets.items()
        },
        primary_split_rows=dict(primary_split_rows),
        primary_split_speakers={
            split: len(values) for split, values in primary_split_speaker_sets.items()
        },
        inner_train_rows=inner_train_rows,
        inner_validation_rows=inner_validation_rows,
        inner_train_speakers=len(inner_train_speakers),
        inner_validation_speakers=len(inner_validation_speakers),
        utterance_score_observed_rows=observed,
        source_paths=(dataset_root, manifest, reservation, explicit),
        source_hashes=actual_hashes,
    )


def validate_implementation_sources(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], tuple[Path, ...]]:
    implementation = _mapping(config.get("implementation"), "implementation")
    records: dict[str, dict[str, Any]] = {}
    paths: list[Path] = []
    for role in IMPLEMENTATION_ROLES:
        configured = _mapping(implementation.get(role), f"implementation.{role}")
        path = _resolve_input(
            workspace_root,
            configured.get("path"),
            f"implementation.{role}.path",
        )
        expected = _sha256(
            configured.get("sha256"),
            f"implementation.{role}.sha256",
        )
        actual = sha256_file(path)
        if actual != expected:
            raise S104ContractError(
                f"implementation.{role} SHA-256 differs from the config"
            )
        records[role] = {
            "sha256": actual,
            "size_bytes": path.stat().st_size,
        }
        paths.append(path)
    return records, tuple(paths)


def _resolve_huggingface_snapshot_file(
    *,
    workspace_root: Path,
    cache_dir: Path,
    model_id: str,
    revision: str,
    filename: str,
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", model_id):
        raise S104ContractError("selected model_id is not a safe Hugging Face id")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise S104ContractError("selected model revision must be a 40-hex commit")
    repository_name = f"models--{model_id.replace('/', '--')}"
    lexical_repository = cache_dir / repository_name
    if lexical_repository.is_symlink():
        raise S104ContractError("selected model repository may not be a symlink")
    repository = lexical_repository.resolve(strict=True)
    try:
        relative_repository = repository.relative_to(workspace_root)
    except ValueError as error:
        raise S104ContractError(
            "selected model repository escapes the workspace"
        ) from error
    if _is_env_path(relative_repository):
        raise S104ContractError(
            "selected model repository resolves through an environment-like path"
        )
    snapshot = repository / "snapshots" / revision
    if (
        not snapshot.is_dir()
        or snapshot.is_symlink()
        or snapshot.resolve(strict=True) != snapshot
    ):
        raise S104ContractError("selected model revision snapshot is absent")
    leaf = snapshot / filename
    if not leaf.is_symlink():
        raise S104ContractError(
            f"selected model {filename} must be an immutable cache symlink"
        )
    link_target = Path(os.readlink(leaf))
    if link_target.is_absolute():
        raise S104ContractError("selected model cache symlink must be relative")
    resolved = leaf.resolve(strict=True)
    lexical_blobs = repository / "blobs"
    if lexical_blobs.is_symlink():
        raise S104ContractError("selected model blobs directory may not be a symlink")
    blobs = lexical_blobs.resolve(strict=True)
    try:
        relative_blobs = blobs.relative_to(workspace_root)
    except ValueError as error:
        raise S104ContractError(
            "selected model blobs directory escapes the workspace"
        ) from error
    if _is_env_path(relative_blobs):
        raise S104ContractError(
            "selected model blobs resolves through an environment-like path"
        )
    try:
        relative_blob = resolved.relative_to(blobs)
    except ValueError as error:
        raise S104ContractError(
            "selected model cache symlink escapes the repository blobs"
        ) from error
    if (
        len(relative_blob.parts) != 1
        or _is_env_path(relative_blob)
        or not resolved.is_file()
        or resolved.is_symlink()
    ):
        raise S104ContractError(
            "selected model cache symlink must resolve to one regular blob"
        )
    return leaf


def validate_selected_model_cache(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    selected_candidate: str,
) -> ModelCachePreflight:
    """Verify the selected revision's exact local config and checkpoint blobs."""

    candidate = _mapping(
        _mapping(config.get("candidates"), "candidates").get(selected_candidate),
        "selected candidate",
    )
    cache_dir = _resolve_input(
        workspace_root,
        candidate.get("cache_dir"),
        "selected candidate cache_dir",
        kind="directory",
    )
    model_id = _string(candidate.get("model_id"), "selected model_id")
    revision = _string(candidate.get("revision"), "selected model revision")
    config_path = _resolve_huggingface_snapshot_file(
        workspace_root=workspace_root,
        cache_dir=cache_dir,
        model_id=model_id,
        revision=revision,
        filename="config.json",
    )
    checkpoint_path = _resolve_huggingface_snapshot_file(
        workspace_root=workspace_root,
        cache_dir=cache_dir,
        model_id=model_id,
        revision=revision,
        filename="pytorch_model.bin",
    )
    config_sha = sha256_file(config_path)
    checkpoint_sha = sha256_file(checkpoint_path)
    _expect_equal(
        config_sha,
        candidate.get("source_config_sha256"),
        "selected model config SHA-256",
    )
    _expect_equal(
        checkpoint_sha,
        candidate.get("source_checkpoint_sha256"),
        "selected model checkpoint SHA-256",
    )
    model_config = load_json_strict(config_path, "selected WavLM config")
    _expect_equal(model_config.get("model_type"), "wavlm", "WavLM model_type")
    _expect_equal(
        model_config.get("hidden_size"),
        candidate.get("hidden_size"),
        "WavLM hidden_size",
    )
    _expect_equal(
        model_config.get("num_hidden_layers"),
        candidate.get("encoder_layer_count"),
        "WavLM num_hidden_layers",
    )
    runtime = _mapping(config.get("runtime"), "runtime")
    _expect_equal(
        model_config.get("conv_kernel"),
        runtime.get("wavlm_conv_kernels"),
        "WavLM conv_kernel",
    )
    _expect_equal(
        model_config.get("conv_stride"),
        runtime.get("wavlm_conv_strides"),
        "WavLM conv_stride",
    )
    source_layerdrop = _finite(
        model_config.get("layerdrop"),
        "WavLM source layerdrop",
    )
    if not 0 <= source_layerdrop < 1:
        raise S104ContractError("WavLM source layerdrop is outside [0, 1)")
    _boolean(
        model_config.get("apply_spec_augment"),
        "WavLM source apply_spec_augment",
    )
    checkpoint_size = checkpoint_path.stat().st_size
    if checkpoint_size <= 0:
        raise S104ContractError("selected model checkpoint is empty")
    return ModelCachePreflight(
        model_id=model_id,
        revision=revision,
        config_sha256=config_sha,
        checkpoint_sha256=checkpoint_sha,
        checkpoint_size_bytes=checkpoint_size,
        source_paths=(
            cache_dir,
            config_path,
            config_path.resolve(strict=True),
            checkpoint_path,
            checkpoint_path.resolve(strict=True),
        ),
    )


def _safe_fraction(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise S104ContractError("fraction denominator must be positive")
    return numerator / denominator


def build_execution_lock(
    *,
    config_path: Path,
    config: Mapping[str, Any],
    decision: S103Decision,
    data: DataPreflight,
    model_cache: ModelCachePreflight,
    implementation: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    candidate = _mapping(
        _mapping(config.get("candidates"), "candidates").get(
            decision.selected_candidate
        ),
        "selected candidate",
    )
    adapter = _mapping(config.get("adapter"), "adapter")
    data_config = _mapping(config.get("data"), "data")
    evaluation = _mapping(config.get("evaluation"), "evaluation")
    optimization = _mapping(config.get("optimization"), "optimization")
    outputs = _mapping(config.get("outputs"), "outputs")
    return {
        "schema_version": EXECUTION_LOCK_SCHEMA_VERSION,
        "status": "ready_for_s104_gpu_training",
        "experiment_id": _mapping(config.get("experiment"), "experiment").get("id"),
        "config_sha256": sha256_file(config_path),
        "selected_candidate": {
            "variant": decision.selected_candidate,
            "model_id": candidate.get("model_id"),
            "revision": candidate.get("revision"),
            "hidden_size": candidate.get("hidden_size"),
            "encoder_layer_count": candidate.get("encoder_layer_count"),
            "do_normalize": candidate.get("do_normalize"),
            "source_config_sha256": candidate.get("source_config_sha256"),
            "source_checkpoint_sha256": candidate.get("source_checkpoint_sha256"),
            "local_model_config_verified": (
                model_cache.config_sha256 == candidate.get("source_config_sha256")
            ),
            "local_model_checkpoint_verified": (
                model_cache.checkpoint_sha256
                == candidate.get("source_checkpoint_sha256")
            ),
            "local_checkpoint_size_bytes": model_cache.checkpoint_size_bytes,
        },
        "s103_branch_evidence": {
            "commit_sha256": decision.commit_sha256,
            "comparison_report_sha256": decision.report_sha256,
            "execution_receipt_sha256": decision.receipt_sha256,
            "generation_id": decision.generation_id,
            "metric": "phone_macro_f1",
            "effect": "large_minus_base",
            "estimate": decision.estimate,
            "threshold_absolute": decision.threshold,
            "gate_status": decision.gate_status,
            "gate_passed": decision.gate_passed,
        },
        "data_contract": {
            "source_hashes": dict(data.source_hashes),
            "included_utterances": data.included_utterances,
            "split_rows": dict(data.split_rows),
            "split_speakers": dict(data.split_speakers),
            "primary_analysis_scope": data_config.get("primary_analysis_scope"),
            "primary_split_rows": dict(data.primary_split_rows),
            "primary_split_speakers": dict(data.primary_split_speakers),
            "speaker_overlap_train_dev": 0,
            "inner_train_rows": data.inner_train_rows,
            "inner_validation_rows": data.inner_validation_rows,
            "inner_train_speakers": data.inner_train_speakers,
            "inner_validation_speakers": data.inner_validation_speakers,
            "speaker_overlap_inner_train_validation": 0,
            "inner_validation_fraction": _safe_fraction(
                data.inner_validation_rows,
                data.primary_split_rows["train"],
            ),
            "utterance_score_observed_rows": dict(data.utterance_score_observed_rows),
            "allowed_splits": list(ALLOWED_SPLITS),
            "forbidden_splits": list(FORBIDDEN_SPLITS),
            "representation_source": "online_raw_audio",
            "pooled_feature_cache_allowed_for_encoder_gradient": False,
        },
        "adapter_contract": {
            "family": adapter.get("family"),
            "placement": adapter.get("placement"),
            "bottleneck_dim": adapter.get("bottleneck_dim"),
            "trainable_parameter_roles": adapter.get("trainable_parameter_roles"),
            "trainable_name_patterns": adapter.get("trainable_name_patterns"),
            "full_fine_tuning_allowed": False,
            "top_block_partial_allowed_in_this_run": False,
            "trainable_parameter_audit_required_before_optimizer": True,
        },
        "selection_and_evaluation_contract": {
            "seeds": list(SEEDS),
            "selection_data": optimization.get("selection_data"),
            "primary_phone_metric": evaluation.get("primary_phone_metric"),
            "primary_utterance_outcome": evaluation.get("primary_utterance_outcome"),
            "primary_utterance_metric": evaluation.get("primary_utterance_metric"),
            "dev_inference_passes_per_seed": 1,
            "word_metric_status": "not_evaluable",
            "development_only": True,
            "oracle_alignment": True,
            "silver_label": True,
            "promotion_decision_allowed": False,
            "primary_analysis_scope": data_config.get("primary_analysis_scope"),
            "early_stopping_order": optimization.get("early_stopping_order"),
            "undefined_secondary_metric_policy": optimization.get(
                "undefined_secondary_metric_policy"
            ),
            "refit_after_inner_selection": False,
            "phone_loss_reduction": optimization.get("phone_loss_reduction"),
            "utterance_loss_reduction": optimization.get("utterance_loss_reduction"),
        },
        "runtime_contract": {
            "gpu_training_executed_by_preflight": False,
            "cuda_initialized_by_preflight": False,
            "raw_audio_files_opened_by_preflight": False,
            "manifest_row_filter": "split IN ('train','dev')",
            "oom_policy": optimization.get("oom_policy"),
            "mixed_precision": "bfloat16",
            "gradient_checkpointing": True,
            "wavlm_conv_kernels": [10, 3, 3, 3, 3, 2, 2],
            "wavlm_conv_strides": [5, 2, 2, 2, 2, 2, 2],
            "minimum_model_input_samples": 400,
            "short_chunk_attention_policy": ("padded_receptive_field_marked_valid"),
            "encoder_layerdrop_override": 0.0,
            "apply_spec_augment": False,
            "freeze_feature_encoder_internal_gradient": True,
            "frozen_backbone_mode_during_training": "train_with_dropout",
            "evaluation_module_mode": "eval",
            "atomic_failure_policy": (
                "immutable execution lock plus content-addressed 16-file "
                "generation and commit pointer"
            ),
        },
        "artifact_and_failure_contract": {
            "expected_completed_registry_artifact_count": 16,
            "generation_root": outputs.get("generation_root"),
            "commit_manifest": outputs.get("commit_manifest"),
            "content_addressed_generation": True,
            "commit_pointer_required_for_visibility": True,
            "per_seed_artifacts": [
                "adapter_and_heads_checkpoint",
                "phone_dev_predictions",
                "utterance_dev_predictions",
                "dev_slice_metrics",
                "resource_and_trainable_parameter_report",
            ],
            "aggregate_artifacts": ["experiment_report"],
            "checkpoint_contains_full_backbone": False,
            "unexpected_artifacts_allowed": False,
            "failure_registry_required": True,
            "failure_registry_fields": ["stage", "exception_type", "oom"],
            "raw_identifier_or_path_in_failure_registry_allowed": False,
            "partial_prediction_publish_allowed": False,
        },
        "privacy": {
            "scope": "execution_lock_payload_only",
            "aggregate_only": True,
            "direct_identifiers_written": False,
            "pseudonymous_row_ids_written": False,
            "raw_locators_written": False,
            "environment_files_read": [],
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
        },
        "implementation": {
            role: dict(record) for role, record in implementation.items()
        },
        "remaining_prerequisites": {
            "online_gpu_training_required": True,
            "dev_evaluation_required": True,
            "completed_registry_required": True,
            "manual_g04_required_for_promotion": True,
            "expert_d07_required_for_promotion": True,
        },
    }


def publish_immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Publish one deterministic JSON without deleting a renamed valid file.

    Once ``os.replace`` succeeds, an exception from directory ``fsync`` means
    durability is uncertain, not that publication failed transactionally.
    The final file is therefore retained and can be verified idempotently by
    the next invocation.
    """

    encoded = _canonical_json_bytes(payload, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != encoded:
            raise S104ContractError("execution lock already exists with other bytes")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".part",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_descriptor = os.open(path.parent, flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)


def prepare_s104_execution(
    *,
    workspace_root: Path,
    config_path: Path,
    output_path: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Run the complete CPU-only readiness audit and publish its lock."""

    root = workspace_root.resolve(strict=True)
    if not root.is_dir():
        raise S104ContractError("workspace_root must be a directory")
    resolved_config = _resolve_input(root, config_path, "S104 config")
    config = load_config(resolved_config)
    validate_config(config)
    implementation, implementation_paths = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )
    decision = validate_s103_decision(workspace_root=root, config=config)
    model_cache = validate_selected_model_cache(
        workspace_root=root,
        config=config,
        selected_candidate=decision.selected_candidate,
    )
    data = validate_data_preflight(workspace_root=root, config=config)
    configured_output = _mapping(config.get("outputs"), "outputs").get("execution_lock")
    outputs = _mapping(config.get("outputs"), "outputs")
    resolved_output = _resolve_output(
        root,
        output_path if output_path is not None else configured_output,
        "S104 execution lock",
    )
    generation_root = _resolve_output_directory(
        root,
        outputs.get("generation_root"),
        "S104 generation root",
    )
    commit_manifest = _resolve_output(
        root,
        outputs.get("commit_manifest"),
        "S104 commit manifest",
    )
    registry_root = _resolve_output_directory(
        root,
        outputs.get("registry_root"),
        "S104 registry root",
    )
    planned_registry_run_directory = _resolve_output_directory(
        root,
        registry_root
        / _registry_run_id(
            outputs.get("planned_registry_run_id"),
            "outputs.planned_registry_run_id",
        ),
        "S104 planned registry run directory",
    )
    if planned_registry_run_directory.parent != registry_root:
        raise S104ContractError(
            "S104 planned registry run directory escapes the registry root"
        )
    protected = {
        resolved_config,
        *implementation_paths,
        *decision.protected_source_paths,
        *model_cache.source_paths,
        *data.source_paths,
    }
    if resolved_output in protected:
        raise S104ContractError("S104 execution lock would overwrite an input")
    for output_name, output in (
        ("generation root", generation_root),
        ("commit manifest", commit_manifest),
    ):
        if any(_paths_overlap(output, source) for source in protected):
            raise S104ContractError(f"S104 {output_name} overlaps an input")
    if any(
        _paths_overlap(planned_registry_run_directory, source) for source in protected
    ):
        raise S104ContractError("S104 planned registry run directory overlaps an input")
    if (
        len(
            {
                resolved_output,
                generation_root,
                commit_manifest,
                registry_root,
            }
        )
        != 4
    ):
        raise S104ContractError("S104 output paths must be distinct")
    for output_file in (resolved_output, commit_manifest):
        if _paths_overlap(generation_root, output_file):
            raise S104ContractError(
                "S104 generation root overlaps an output control file"
            )
        if _paths_overlap(registry_root, output_file):
            raise S104ContractError(
                "S104 registry root overlaps an output control file"
            )
    if _paths_overlap(generation_root, registry_root):
        raise S104ContractError("S104 generation and registry roots overlap")
    payload = build_execution_lock(
        config_path=resolved_config,
        config=config,
        decision=decision,
        data=data,
        model_cache=model_cache,
        implementation=implementation,
    )
    publish_immutable_json(resolved_output, payload)
    if (
        sha256_file(resolved_output)
        != hashlib.sha256(_canonical_json_bytes(payload, pretty=True)).hexdigest()
    ):
        raise S104ContractError("published S104 execution lock failed verification")
    return payload, resolved_output
