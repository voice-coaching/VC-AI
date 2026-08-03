"""Fail-closed preregistration and CPU preflight for WP4 S105.

Static validation reads only the S105 YAML and pinned implementation sources.
The full preflight first requires the public S104 commit and independently
re-runs the S104 validator.  Only after that succeeds may it scan the
train/dev metadata needed by the hierarchy.  Stack, calibration, locked-test,
raw audio, environment files, and S104 metric values used for configuration
are outside this contract.
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
from typing import Any, cast

import pyarrow.dataset as ds  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
EXECUTION_LOCK_SCHEMA_VERSION = "voice_coach.wavlm_s105_execution_lock.v1"
S104_COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s104_adapter_commit.v1"
S104_REPORT_SCHEMA_VERSION = "voice_coach.wavlm_s104_adapter_report.v1"
SEEDS = (42, 43, 44)
VARIANTS = (
    "simple_head_control",
    "flat_shared_multitask",
    "final_layer_hierarchy",
    "layerwise_weighted_hierarchy",
    "levelwise_multi_aspect_hierarchy",
)
TASKS = ("phone_binary_error", "utterance_scores")
ALLOWED_SPLITS = ("train", "dev")
FORBIDDEN_SPLITS = ("stack", "calibration", "locked_test")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IMPLEMENTATION_ROLES = (
    "preflight_script",
    "trainer_script",
    "contract_module",
    "training_module",
    "execution_module",
    "model_module",
    "metrics_module",
    "s104_validator_module",
)
FORBIDDEN_PATH_PARTS = frozenset(
    {
        ".env",
        "stack",
        "calibration",
        "locked",
        "locked_test",
        "locked-test",
    }
)


class S105ContractError(ValueError):
    """Raised when S105 readiness cannot be established safely."""


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
            raise S105ContractError(f"YAML contains duplicate key: {key!r}")
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class S104Evidence:
    commit_sha256: str
    generation_id: str
    registry_sha256: str
    execution_lock_sha256: str
    report_sha256: str
    selected_candidate: Mapping[str, Any]
    checkpoint_records: tuple[Mapping[str, Any], ...]
    protected_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class HierarchyDataAudit:
    split_utterances: Mapping[str, int]
    split_speakers: Mapping[str, int]
    phone_intervals: int
    word_intervals: int
    orphan_phones: int
    zero_word_utterances: int
    inner_fit_utterances: int
    inner_validation_utterances: int
    inner_fit_speakers: int
    inner_validation_speakers: int
    source_records: Mapping[str, Mapping[str, Any]]
    protected_paths: tuple[Path, ...]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S105ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S105ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S105ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S105ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S105ContractError(f"{name} must be at least {minimum}")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S105ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise S105ContractError(f"{name} must be finite")
    return result


def _expect(value: object, expected: object, name: str) -> None:
    if value != expected:
        raise S105ContractError(f"{name} must equal {expected!r}")


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise S105ContractError(f"{name} must be a lowercase SHA-256")
    return value


def _reject_nonfinite(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise S105ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_nonfinite(child, name)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite(child, name)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise S105ContractError(f"JSON contains duplicate key: {key!r}")
        output[key] = value
    return output


def load_json_strict(path: Path, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                S105ContractError(f"{name} contains {token}")
            ),
        )
    except S105ContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise S105ContractError(f"{name} is not valid UTF-8 JSON") from error
    _reject_nonfinite(value, name)
    return _mapping(value, name)


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except S105ContractError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise S105ContractError("S105 config is not valid UTF-8 YAML") from error
    return dict(_mapping(value, "S105 config"))


def sha256_file(path: Path) -> str:
    if not path.is_file():
        raise S105ContractError(f"required file is absent: {path}")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise S105ContractError("file changed while hashing")
    return digest.hexdigest()


def canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + "\n").encode("utf-8")


def _is_environment_path(path: Path) -> bool:
    return any(part.casefold().startswith(".env") for part in path.parts)


def _resolve_input(
    workspace_root: Path,
    value: object,
    name: str,
    *,
    directory: bool = False,
) -> Path:
    raw = value if isinstance(value, Path) else Path(_string(value, name))
    if _is_environment_path(raw):
        raise S105ContractError(f"{name} may not reference an environment file")
    candidate = raw if raw.is_absolute() else workspace_root / raw
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(workspace_root)
    except (OSError, ValueError) as error:
        raise S105ContractError(f"{name} is absent or escapes the workspace") from error
    if _is_environment_path(relative):
        raise S105ContractError(f"{name} resolves through an environment file")
    if directory and not resolved.is_dir():
        raise S105ContractError(f"{name} must be a directory")
    if not directory and not resolved.is_file():
        raise S105ContractError(f"{name} must be a file")
    return resolved


def _resolve_output(workspace_root: Path, value: object, name: str) -> Path:
    raw = value if isinstance(value, Path) else Path(_string(value, name))
    candidate = raw if raw.is_absolute() else workspace_root / raw
    lexical = Path(os.path.abspath(candidate))
    try:
        relative = lexical.relative_to(workspace_root)
    except ValueError as error:
        raise S105ContractError(f"{name} escapes the workspace") from error
    for part in relative.parts:
        lowered = part.casefold()
        if (
            lowered in FORBIDDEN_PATH_PARTS
            or lowered.startswith(".env")
            or lowered.startswith(("stack_", "stack-", "calibration_", "calibration-"))
            or lowered.startswith(("locked_", "locked-"))
        ):
            raise S105ContractError(f"{name} is in a protected path")
    _assert_no_symlink_chain(lexical)
    resolved = lexical.resolve(strict=False)
    try:
        resolved.relative_to(workspace_root)
    except ValueError as error:
        raise S105ContractError(f"{name} resolves outside the workspace") from error
    return resolved


def _assert_no_symlink_chain(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if os.path.lexists(current) and current.is_symlink():
            raise S105ContractError("S105 output path contains a symlink component")


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate all preregistered choices without reading S104 or data."""

    if set(config) != {
        "schema_version",
        "experiment",
        "predecessor",
        "data",
        "variants",
        "optimization",
        "evaluation",
        "adoption",
        "runtime",
        "implementation",
        "outputs",
    }:
        raise S105ContractError("S105 top-level config keys differ")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")
    experiment = _mapping(config.get("experiment"), "experiment")
    _string(experiment.get("id"), "experiment.id")
    for key, expected in {
        "stage": "s105_incremental_hierarchy",
        "development_only": True,
        "oracle_alignment": True,
        "silver_label": True,
        "promotion_decision_allowed": False,
        "seeds": list(SEEDS),
    }.items():
        _expect(experiment.get(key), expected, f"experiment.{key}")

    predecessor = _mapping(config.get("predecessor"), "predecessor")
    for key, expected in {
        "stage": "s104_adapter",
        "commit_schema_version": S104_COMMIT_SCHEMA_VERSION,
        "report_schema_version": S104_REPORT_SCHEMA_VERSION,
        "checkpoint_schema_version": ("voice_coach.wavlm_s104_adapter_checkpoint.v1"),
        "required_status": "committed",
        "independent_validator": (
            "voice_coach.training.wavlm_adapter_training.validate_s104_commit"
        ),
        "seed_checkpoint_binding": "same_seed",
        "s104_dev_metrics_may_configure_s105": False,
    }.items():
        _expect(predecessor.get(key), expected, f"predecessor.{key}")
    _string(predecessor.get("config"), "predecessor.config")
    _sha256(predecessor.get("config_sha256"), "predecessor.config_sha256")
    _string(predecessor.get("commit_manifest"), "predecessor.commit_manifest")

    data = _mapping(config.get("data"), "data")
    zones = _mapping(data.get("zone_contract"), "data.zone_contract")
    if dict(zones) != {
        "dataset_root": "R0",
        "modeling_manifest": "R1",
        "prompt_challenge_reservation": "R1",
        "explicit_utterance_labels": "D1",
        "predictions": "D1",
        "reports": "A1",
    }:
        raise S105ContractError("S105 data zone contract differs")
    _string(data.get("dataset_root"), "data.dataset_root")
    for name in (
        "modeling_manifest",
        "prompt_challenge_reservation",
        "explicit_utterance_labels",
    ):
        _string(data.get(name), f"data.{name}")
        _sha256(data.get(f"{name}_sha256"), f"data.{name}_sha256")
    for key, expected in {
        "allowed_splits": list(ALLOWED_SPLITS),
        "training_split": "train",
        "development_split": "dev",
        "forbidden_splits": list(FORBIDDEN_SPLITS),
        "exclude_qc_flagged": True,
        "exclude_reserved_prompts": True,
        "representation_source": "online_raw_audio_all_encoder_layers",
        "pooled_final_layer_cache_allowed": False,
        "primary_analysis_scope": {
            "l1": ["Chinese", "Japanese"],
            "gender": ["F", "M"],
        },
    }.items():
        _expect(data.get(key), expected, f"data.{key}")
    inner = _mapping(data.get("inner_validation"), "data.inner_validation")
    _expect(inner.get("source"), "train_speaker_sha256", "inner.source")
    _expect(inner.get("modulus"), 10, "inner.modulus")
    _expect(inner.get("validation_buckets"), [0, 1], "inner.validation_buckets")
    interval = _mapping(data.get("interval_mapping"), "data.interval_mapping")
    for key, expected in {
        "phone_source": "target_phone_intervals",
        "word_source": "word_intervals",
        "rule": "maximum_positive_overlap",
        "tie_break": "lowest_word_index",
        "unassigned_phone_policy": "utterance_orphan_bucket",
        "zero_phone_word_policy": "retain_unlabeled_representation",
    }.items():
        _expect(interval.get(key), expected, f"interval_mapping.{key}")
    word = _mapping(data.get("word_supervision"), "data.word_supervision")
    expected_word = {
        "role": "unlabeled_intermediate_representation_only",
        "word_loss_allowed": False,
        "word_head_allowed": False,
        "word_label_synthesis_allowed": False,
        "word_pcc_status": "not_evaluable",
    }
    if dict(word) != expected_word:
        raise S105ContractError("word supervision boundary differs")

    variants = _mapping(config.get("variants"), "variants")
    _expect(variants.get("order"), list(VARIANTS), "variants.order")
    expected_variants: Mapping[str, Mapping[str, Any]] = {
        "simple_head_control": {
            "encoder_input": "s104_same_seed_adapter",
            "hidden_source": "final_layer",
            "phone_to_word_aggregation": "none",
            "word_to_utterance_aggregation": "none",
            "shared_multitask_projection": False,
            "multi_aspect_attention": False,
        },
        "flat_shared_multitask": {
            "encoder_input": "s104_same_seed_adapter",
            "hidden_source": "final_layer",
            "phone_to_word_aggregation": "none",
            "word_to_utterance_aggregation": "none",
            "shared_multitask_projection": True,
            "multi_aspect_attention": False,
        },
        "final_layer_hierarchy": {
            "encoder_input": "s104_same_seed_adapter",
            "hidden_source": "final_layer",
            "phone_to_word_aggregation": "masked_attention",
            "word_to_utterance_aggregation": "masked_attention",
            "shared_multitask_projection": True,
            "multi_aspect_attention": False,
        },
        "layerwise_weighted_hierarchy": {
            "encoder_input": "s104_same_seed_adapter",
            "hidden_source": "learned_softmax_all_encoder_layers",
            "phone_to_word_aggregation": "masked_attention",
            "word_to_utterance_aggregation": "masked_attention",
            "shared_multitask_projection": True,
            "multi_aspect_attention": False,
        },
        "levelwise_multi_aspect_hierarchy": {
            "encoder_input": "s104_same_seed_adapter",
            "hidden_source": "learned_softmax_all_encoder_layers",
            "phone_to_word_aggregation": "levelwise_multi_aspect_attention",
            "word_to_utterance_aggregation": "levelwise_multi_aspect_attention",
            "shared_multitask_projection": True,
            "multi_aspect_attention": True,
        },
    }
    for variant, expected in expected_variants.items():
        if dict(_mapping(variants.get(variant), f"variants.{variant}")) != expected:
            raise S105ContractError(f"variants.{variant} contract differs")
    dimensions = _mapping(variants.get("dimensions"), "variants.dimensions")
    for name in (
        "projection_dim",
        "phone_target_embedding_dim",
        "attention_hidden_dim",
        "attention_aspects",
    ):
        _integer(dimensions.get(name), f"dimensions.{name}", minimum=1)
    dropout = _finite(dimensions.get("dropout"), "dimensions.dropout")
    if not 0 <= dropout < 1:
        raise S105ContractError("dimensions.dropout must be in [0, 1)")
    _expect(dimensions.get("score_range"), [1, 5], "dimensions.score_range")
    encoder_update = _mapping(variants.get("encoder_update"), "encoder_update")
    if dict(encoder_update) != {
        "s104_adapter_frozen": True,
        "backbone_frozen": True,
        "hierarchy_heads_only": True,
    }:
        raise S105ContractError("S105 encoder freeze contract differs")

    optimization = _mapping(config.get("optimization"), "optimization")
    if dict(optimization) != {
        "optimizer": "AdamW",
        "learning_rate": 0.0003,
        "weight_decay": 0.01,
        "gradient_clip_norm": 1.0,
        "gradient_accumulation_utterances": 8,
        "bounded_shuffle_buffer_utterances": 2048,
        "maximum_epochs": 12,
        "minimum_epochs": 3,
        "early_stopping_patience": 3,
        "phone_loss_weight": 1.0,
        "utterance_loss_weight": 1.0,
        "word_loss_weight": 0.0,
        "phone_loss_reduction": "mean_per_utterance",
        "utterance_loss_reduction": ("mean_observed_outcomes_per_utterance"),
        "selection_data": "train_inner_validation_only",
        "refit_after_inner_selection": False,
        "undefined_secondary_metric_policy": "no_improvement",
        "early_stopping_order": [
            "phone_macro_f1",
            "comprehensibility_score_icc_2_1",
            "earliest_epoch",
        ],
        "phone_threshold_grid": {"start": 0.10, "stop": 0.90, "step": 0.05},
        "dev_may_select_variant_or_hyperparameters": False,
        "oom_policy": "fail_and_record_no_automatic_retry",
    }:
        raise S105ContractError("S105 optimization contract differs")

    evaluation = _mapping(config.get("evaluation"), "evaluation")
    for key, expected in {
        "dev_inference_passes_per_variant_task_seed": 1,
        "tasks": list(TASKS),
        "primary_phone_metric": "phone_macro_f1",
        "primary_utterance_outcome": "comprehensibility_score",
        "primary_utterance_metric": "icc_2_1",
        "utterance_outcomes": [
            "pronunciation_score",
            "fluency_score",
            "comprehensibility_score",
        ],
        "word_metric_status": "not_evaluable",
        "stack_access_allowed": False,
        "calibration_access_allowed": False,
        "locked_test_access_allowed": False,
    }.items():
        _expect(evaluation.get(key), expected, f"evaluation.{key}")

    adoption = _mapping(config.get("adoption"), "adoption")
    for key, expected in {
        "control_variant": "simple_head_control",
        "candidate_variant": "levelwise_multi_aspect_hierarchy",
        "primary_family": [
            "phone_macro_f1",
            "comprehensibility_score_icc_2_1",
        ],
        "effect": "candidate_minus_control",
        "minimum_absolute_improvement": 0.03,
        "improvement_rule": "any_primary_point_estimate_gte_threshold",
        "confidence_interval_use": "descriptive_not_gate",
        "p_value_use": "descriptive_not_gate",
    }.items():
        _expect(adoption.get(key), expected, f"adoption.{key}")
    bootstrap = _mapping(adoption.get("bootstrap"), "adoption.bootstrap")
    if dict(bootstrap) != {
        "method": "paired_speaker_cluster_percentile",
        "unit": "speaker_id_sha256",
        "n_resamples": 5000,
        "confidence_level": 0.95,
        "seed": 42,
        "p_value": "two_sided_zero_with_plus_one_correction",
    }:
        raise S105ContractError("S105 bootstrap contract differs")
    multiplicity = _mapping(adoption.get("multiplicity"), "adoption.multiplicity")
    if dict(multiplicity) != {
        "primary_method": "holm",
        "primary_alpha": 0.05,
        "primary_family_size": 2,
        "primary_family_scope": "candidate_vs_control_two_primary_outcomes",
        "incremental_method": "holm",
        "incremental_alpha": 0.05,
        "incremental_family_size": 8,
        "incremental_family_scope": (
            "four_adjacent_variant_pairs_by_two_primary_outcomes"
        ),
    }:
        raise S105ContractError("S105 multiplicity contract differs")
    subgroup = _mapping(adoption.get("subgroup"), "adoption.subgroup")
    for key, expected in {
        "metric": "phone_macro_f1",
        "axes": ["l1", "gender", "proficiency"],
        "fixed_groups": {
            "l1": ["Chinese", "Japanese"],
            "gender": ["F", "M"],
            "proficiency": ["Beginner", "Intermediate", "Advance", "Fluent"],
        },
        "minimum_unique_speakers": 20,
        "minimum_positive_phone_rows": 20,
        "minimum_negative_phone_rows": 20,
        "ineligible_group_policy": "report_exclude_from_gate",
        "difference": "control_minus_candidate",
        "worst_group_drop_definition": "max_eligible_control_minus_candidate",
        "maximum_exclusive": 0.05,
    }.items():
        _expect(subgroup.get(key), expected, f"subgroup.{key}")
    if dict(_mapping(adoption.get("adoption_gate"), "adoption_gate")) != {
        "require_point_improvement": True,
        "require_worst_group_drop_lt_threshold": True,
        "require_holm_significance": False,
    }:
        raise S105ContractError("S105 adoption gate differs")

    runtime = _mapping(config.get("runtime"), "runtime")
    for key, expected in {
        "require_cuda_for_training": True,
        "cpu_preflight_only": True,
        "mixed_precision": "bfloat16",
        "gradient_checkpointing": True,
        "deterministic_algorithms": True,
        "cublas_workspace_config": ":4096:8",
        "sample_rate_hz": 16000,
        "maximum_chunk_seconds": 20.0,
        "wavlm_conv_kernels": [10, 3, 3, 3, 3, 2, 2],
        "wavlm_conv_strides": [5, 2, 2, 2, 2, 2, 2],
        "minimum_model_input_samples": 400,
        "short_chunk_attention_policy": ("padded_receptive_field_marked_valid"),
        "frozen_s104_encoder_mode": "eval",
        "s104_adapter_gradient_allowed": False,
        "shared_encoder_forward_across_variants": True,
        "hidden_states_required": True,
        "pooled_final_layer_cache_allowed": False,
        "online_raw_audio_or_reviewed_all_layer_cache_required": True,
        "environment_files_forbidden": True,
    }.items():
        _expect(runtime.get(key), expected, f"runtime.{key}")

    implementation = _mapping(config.get("implementation"), "implementation")
    if tuple(implementation) != IMPLEMENTATION_ROLES:
        raise S105ContractError("implementation role order/set differs")
    for role in IMPLEMENTATION_ROLES:
        record = _mapping(implementation.get(role), f"implementation.{role}")
        if set(record) != {"path", "sha256"}:
            raise S105ContractError(f"implementation.{role} schema differs")
        _string(record.get("path"), f"implementation.{role}.path")
        _sha256(record.get("sha256"), f"implementation.{role}.sha256")

    outputs = _mapping(config.get("outputs"), "outputs")
    for name in (
        "execution_lock",
        "generation_root",
        "commit_manifest",
        "registry_root",
        "registry_run_id_prefix",
    ):
        _string(outputs.get(name), f"outputs.{name}")
    for key, expected in {
        "expected_variant_count": 5,
        "expected_seed_count": 3,
        "expected_artifacts_per_variant_seed": 5,
        "expected_variant_aggregate_artifacts": 1,
        "expected_global_artifacts": 2,
        "expected_total_artifact_count": 82,
        "expected_completed_registry_count": 5,
        "expected_registry_artifact_count_per_variant": 16,
        "per_variant_seed_artifacts": [
            "hierarchy_checkpoint",
            "phone_dev_predictions",
            "utterance_dev_predictions",
            "dev_slice_metrics",
            "resource_report",
        ],
        "per_variant_aggregate_artifacts": ["variant_report"],
        "global_artifacts": [
            "ablation_comparison_report",
            "ablation_table",
        ],
        "atomic_publish": True,
        "content_addressed_generation": True,
        "commit_pointer_required_for_visibility": True,
        "failure_registry": {
            "required": True,
            "status": "failed",
            "failure_fields": ["stage", "exception_type", "oom"],
            "raw_identifier_or_path_allowed": False,
            "partial_prediction_publish_allowed": False,
        },
        "unexpected_artifacts_allowed": False,
        "row_level_outputs_access_scope": "restricted_internal",
        "aggregate_report_shareable": False,
    }.items():
        _expect(outputs.get(key), expected, f"outputs.{key}")


def validate_implementation_sources(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> tuple[Mapping[str, Mapping[str, Any]], tuple[Path, ...]]:
    implementation = _mapping(config.get("implementation"), "implementation")
    output: dict[str, Mapping[str, Any]] = {}
    paths: list[Path] = []
    for role in IMPLEMENTATION_ROLES:
        record = _mapping(implementation.get(role), f"implementation.{role}")
        path = _resolve_input(
            workspace_root,
            record.get("path"),
            f"implementation.{role}.path",
        )
        actual = sha256_file(path)
        _expect(actual, record.get("sha256"), f"implementation.{role}.sha256")
        output[role] = {
            "path": path.relative_to(workspace_root).as_posix(),
            "sha256": actual,
            "size_bytes": path.stat().st_size,
        }
        paths.append(path)
    return output, tuple(paths)


def _workspace_record(workspace_root: Path, path: Path) -> Mapping[str, Any]:
    return {
        "path": path.relative_to(workspace_root).as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def validate_s104_predecessor(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> S104Evidence:
    """Require and independently validate the complete predecessor commit."""

    predecessor = _mapping(config.get("predecessor"), "predecessor")
    s104_config = _resolve_input(
        workspace_root,
        predecessor.get("config"),
        "S104 config",
    )
    _expect(
        sha256_file(s104_config),
        predecessor.get("config_sha256"),
        "S104 config SHA-256",
    )
    # Resolve the commit before importing the torch-bearing S104 validator.  A
    # missing commit therefore fails without CUDA initialization or data reads.
    commit = _resolve_input(
        workspace_root,
        predecessor.get("commit_manifest"),
        "completed S104 commit",
    )
    from voice_coach.training.wavlm_adapter_training import validate_s104_commit

    validation = validate_s104_commit(
        workspace_root=workspace_root,
        config_path=s104_config,
    )
    if (
        validation.get("status") != "valid"
        or validation.get("artifact_count") != 16
        or validation.get("development_only") is not True
        or validation.get("locked_test_accessed") is not False
    ):
        raise S105ContractError("independent S104 validation did not pass")
    document = load_json_strict(commit, "S104 commit")
    if (
        document.get("schema_version") != S104_COMMIT_SCHEMA_VERSION
        or document.get("status") != "committed"
        or document.get("generation_id") != validation.get("generation_id")
    ):
        raise S105ContractError("S104 commit identity differs")
    generation = _resolve_input(
        workspace_root,
        document.get("generation_path"),
        "S104 generation",
        directory=True,
    )
    artifacts = _list(document.get("artifacts"), "S104 artifacts")
    checkpoints: list[Mapping[str, Any]] = []
    protected: list[Path] = [s104_config, commit, generation]
    for seed in SEEDS:
        name = f"seed_{seed}_adapter_and_heads_checkpoint"
        matches = [
            _mapping(value, "S104 artifact")
            for value in artifacts
            if isinstance(value, Mapping) and value.get("name") == name
        ]
        if len(matches) != 1:
            raise S105ContractError("S104 same-seed checkpoint is absent")
        record = matches[0]
        checkpoint = _resolve_input(
            generation,
            record.get("path"),
            f"S104 seed {seed} checkpoint",
        )
        if checkpoint.parent != generation / f"seed_{seed}":
            raise S105ContractError("S104 checkpoint path differs")
        _expect(sha256_file(checkpoint), record.get("sha256"), "checkpoint SHA-256")
        _expect(checkpoint.stat().st_size, record.get("size_bytes"), "checkpoint size")
        checkpoints.append(
            {
                "seed": seed,
                **_workspace_record(workspace_root, checkpoint),
            }
        )
        protected.append(checkpoint)
    report = generation / "experiment_report.json"
    report_document = load_json_strict(report, "S104 experiment report")
    leakage = _mapping(report_document.get("leakage"), "S104 leakage")
    if (
        report_document.get("schema_version") != S104_REPORT_SCHEMA_VERSION
        or report_document.get("status") != "completed"
        or report_document.get("development_only") is not True
        or report_document.get("oracle_alignment") is not True
        or report_document.get("silver_label") is not True
        or report_document.get("promotion_decision_allowed") is not False
        or leakage.get("stack_accessed") is not False
        or leakage.get("calibration_accessed") is not False
        or leakage.get("locked_test_accessed") is not False
    ):
        raise S105ContractError("S104 report is not an eligible predecessor")
    selected = _mapping(
        report_document.get("selected_candidate"),
        "S104 selected candidate",
    )
    if set(selected) != {
        "model_id",
        "revision",
        "cache_dir",
        "hidden_size",
        "encoder_layer_count",
        "do_normalize",
        "source_config_sha256",
        "source_checkpoint_sha256",
    }:
        raise S105ContractError("S104 selected-candidate schema differs")
    selected_candidate = {
        "model_id": _string(selected.get("model_id"), "S104 selected model_id"),
        "revision": _string(selected.get("revision"), "S104 selected revision"),
        "cache_dir": _string(selected.get("cache_dir"), "S104 selected cache_dir"),
        "hidden_size": _integer(
            selected.get("hidden_size"),
            "S104 selected hidden_size",
            minimum=1,
        ),
        "encoder_layer_count": _integer(
            selected.get("encoder_layer_count"),
            "S104 selected encoder_layer_count",
            minimum=1,
        ),
        "do_normalize": selected.get("do_normalize"),
        "source_config_sha256": _sha256(
            selected.get("source_config_sha256"),
            "S104 selected source config SHA",
        ),
        "source_checkpoint_sha256": _sha256(
            selected.get("source_checkpoint_sha256"),
            "S104 selected source checkpoint SHA",
        ),
    }
    if not isinstance(selected_candidate["do_normalize"], bool):
        raise S105ContractError("S104 selected do_normalize must be boolean")
    protected.append(report)
    return S104Evidence(
        commit_sha256=sha256_file(commit),
        generation_id=_sha256(document.get("generation_id"), "generation_id"),
        registry_sha256=_sha256(validation.get("registry_sha256"), "registry SHA"),
        execution_lock_sha256=_sha256(
            validation.get("execution_lock_sha256"),
            "execution lock SHA",
        ),
        report_sha256=sha256_file(report),
        selected_candidate=selected_candidate,
        checkpoint_records=tuple(checkpoints),
        protected_paths=tuple(protected),
    )


def _speaker_bucket(identifier: str, modulus: int) -> int:
    if not SHA256_PATTERN.fullmatch(identifier):
        raise S105ContractError("speaker identifier is not a SHA-256")
    return int(identifier[:16], 16) % modulus


def _intervals(value: object, name: str) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        raise S105ContractError(f"{name} is not a list")
    output: list[tuple[float, float]] = []
    for index, item in enumerate(value):
        record = _mapping(item, f"{name}[{index}]")
        start = _finite(record.get("start"), f"{name}[{index}].start")
        end = _finite(record.get("end"), f"{name}[{index}].end")
        if start < 0 or end <= start:
            raise S105ContractError(f"{name}[{index}] bounds are invalid")
        output.append((start, end))
    if output != sorted(output):
        raise S105ContractError(f"{name} is not ordered")
    return output


def validate_hierarchy_data(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> HierarchyDataAudit:
    """Scan only train/dev metadata and audit phone↔word grouping support."""

    data = _mapping(config.get("data"), "data")
    paths = {
        name: _resolve_input(workspace_root, data.get(name), f"data.{name}")
        for name in (
            "modeling_manifest",
            "prompt_challenge_reservation",
            "explicit_utterance_labels",
        )
    }
    records: dict[str, Mapping[str, Any]] = {}
    for name, path in paths.items():
        actual = sha256_file(path)
        _expect(actual, data.get(f"{name}_sha256"), f"data.{name}_sha256")
        records[name] = _workspace_record(workspace_root, path)
    reservation = load_json_strict(
        paths["prompt_challenge_reservation"],
        "prompt reservation",
    )
    entries = _list(reservation.get("reserved_prompts"), "reserved prompts")
    reserved = {
        _sha256(_mapping(value, "reserved prompt").get("prompt_sha256"), "prompt")
        for value in entries
    }
    manifest_schema = pq.read_schema(paths["modeling_manifest"])
    required = {
        "speaker_id",
        "split",
        "prompt_sha256",
        "qc_flags",
        "l1",
        "gender",
        "word_intervals",
        "target_phone_intervals",
    }
    if not required.issubset(manifest_schema.names):
        raise S105ContractError("modeling manifest hierarchy schema differs")
    scanner = ds.dataset(paths["modeling_manifest"], format="parquet").scanner(
        columns=sorted(required),
        filter=ds.field("split").isin(list(ALLOWED_SPLITS)),
        batch_size=2_048,
        use_threads=False,
    )
    scope = _mapping(data.get("primary_analysis_scope"), "primary scope")
    allowed_l1 = set(cast(list[str], scope["l1"]))
    allowed_gender = set(cast(list[str], scope["gender"]))
    split_rows = {split: 0 for split in ALLOWED_SPLITS}
    speakers: dict[str, set[str]] = {split: set() for split in ALLOWED_SPLITS}
    phone_count = 0
    word_count = 0
    orphan_count = 0
    zero_word = 0
    fit_speakers: set[str] = set()
    validation_speakers: set[str] = set()
    fit_utterances = 0
    validation_utterances = 0
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            split = row.get("split")
            if split not in ALLOWED_SPLITS:
                raise S105ContractError("protected split escaped manifest filter")
            if row.get("qc_flags") or row.get("prompt_sha256") in reserved:
                continue
            if (
                row.get("l1") not in allowed_l1
                or row.get("gender") not in allowed_gender
            ):
                continue
            speaker = _sha256(row.get("speaker_id"), "manifest speaker")
            words = _intervals(row.get("word_intervals"), "word intervals")
            phones = _intervals(
                row.get("target_phone_intervals"),
                "target phone intervals",
            )
            split_rows[str(split)] += 1
            speakers[str(split)].add(speaker)
            phone_count += len(phones)
            word_count += len(words)
            if not words:
                zero_word += 1
            for phone_start, phone_end in phones:
                if not any(
                    min(phone_end, word_end) > max(phone_start, word_start)
                    for word_start, word_end in words
                ):
                    orphan_count += 1
            if split == "train":
                if _speaker_bucket(speaker, 10) in {0, 1}:
                    validation_speakers.add(speaker)
                    validation_utterances += 1
                else:
                    fit_speakers.add(speaker)
                    fit_utterances += 1
    if (
        not split_rows["train"]
        or not split_rows["dev"]
        or not phone_count
        or not word_count
        or not fit_utterances
        or not validation_utterances
        or speakers["train"] & speakers["dev"]
        or fit_speakers & validation_speakers
    ):
        raise S105ContractError("S105 hierarchy data audit failed")
    explicit_schema = pq.read_schema(paths["explicit_utterance_labels"])
    if not {
        "utterance_id_sha256",
        "speaker_id_sha256",
        "split",
        "pronunciation_score",
        "fluency_score",
        "comprehensibility_score",
    }.issubset(explicit_schema.names):
        raise S105ContractError("explicit utterance schema differs")
    return HierarchyDataAudit(
        split_utterances=split_rows,
        split_speakers={key: len(value) for key, value in speakers.items()},
        phone_intervals=phone_count,
        word_intervals=word_count,
        orphan_phones=orphan_count,
        zero_word_utterances=zero_word,
        inner_fit_utterances=fit_utterances,
        inner_validation_utterances=validation_utterances,
        inner_fit_speakers=len(fit_speakers),
        inner_validation_speakers=len(validation_speakers),
        source_records=records,
        protected_paths=tuple(paths.values()),
    )


def build_execution_lock(
    *,
    config_path: Path,
    config: Mapping[str, Any],
    implementation: Mapping[str, Mapping[str, Any]],
    predecessor: S104Evidence,
    data: HierarchyDataAudit,
) -> Mapping[str, Any]:
    adoption = _mapping(config.get("adoption"), "adoption")
    return {
        "schema_version": EXECUTION_LOCK_SCHEMA_VERSION,
        "status": "ready_for_s105_gpu_training",
        "experiment_id": _mapping(config.get("experiment"), "experiment").get("id"),
        "config_sha256": sha256_file(config_path),
        "predecessor": {
            "stage": "s104_adapter",
            "independent_validation_status": "valid",
            "commit_sha256": predecessor.commit_sha256,
            "generation_id": predecessor.generation_id,
            "registry_sha256": predecessor.registry_sha256,
            "execution_lock_sha256": predecessor.execution_lock_sha256,
            "report_sha256": predecessor.report_sha256,
            "selected_candidate": predecessor.selected_candidate,
            "same_seed_checkpoints": list(predecessor.checkpoint_records),
            "s104_dev_metrics_used_for_s105_configuration": False,
        },
        "data": {
            "sources": data.source_records,
            "allowed_splits": list(ALLOWED_SPLITS),
            "forbidden_splits": list(FORBIDDEN_SPLITS),
            "split_utterances": dict(data.split_utterances),
            "split_speakers": dict(data.split_speakers),
            "speaker_overlap_train_dev": 0,
            "inner_fit_utterances": data.inner_fit_utterances,
            "inner_validation_utterances": data.inner_validation_utterances,
            "inner_fit_speakers": data.inner_fit_speakers,
            "inner_validation_speakers": data.inner_validation_speakers,
            "speaker_overlap_inner_fit_validation": 0,
            "phone_intervals": data.phone_intervals,
            "word_intervals": data.word_intervals,
            "orphan_phones": data.orphan_phones,
            "zero_word_utterances": data.zero_word_utterances,
            "word_role": "unlabeled_intermediate_representation_only",
        },
        "variants": list(VARIANTS),
        "selection_and_evaluation": {
            "selection_data": "train_inner_validation_only",
            "refit_after_inner_selection": False,
            "dev_passes_per_variant_task_seed": 1,
            "tasks": list(TASKS),
            "seeds": list(SEEDS),
            "dev_used_for_variant_or_hyperparameter_selection": False,
            "primary_phone_metric": "phone_macro_f1",
            "primary_utterance_outcome": "comprehensibility_score",
            "primary_utterance_metric": "icc_2_1",
            "word_loss_performed": False,
            "word_pcc_status": "not_evaluable",
        },
        "adoption": dict(adoption),
        "artifact_contract": {
            "expected_total_artifact_count": 82,
            "expected_variant_registry_count": 5,
            "unexpected_artifacts_allowed": False,
            "content_addressed_generation": True,
        },
        "privacy": {
            "aggregate_only_execution_lock": True,
            "raw_audio_opened_by_preflight": False,
            "environment_files_read": [],
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
        },
        "implementation": {
            role: dict(record) for role, record in implementation.items()
        },
        "remaining": {
            "gpu_training_required": True,
            "actual_s105_result_exists": False,
            "promotion_decision_allowed": False,
        },
    }


def publish_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_chain(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".part",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != encoded:
                raise S105ContractError("S105 execution lock already differs")
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    if path.is_symlink() or not path.is_file() or path.read_bytes() != encoded:
        raise S105ContractError("published S105 execution lock bytes differ")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_s105_execution(
    *,
    workspace_root: Path,
    config_path: Path,
    output_path: Path | None = None,
) -> tuple[Mapping[str, Any], Path]:
    """Validate S104 first, then train/dev metadata, then publish one lock."""

    root = workspace_root.resolve(strict=True)
    resolved_config = _resolve_input(root, config_path, "S105 config")
    config = load_config(resolved_config)
    validate_config(config)
    implementation, implementation_paths = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )
    predecessor = validate_s104_predecessor(workspace_root=root, config=config)
    data = validate_hierarchy_data(workspace_root=root, config=config)
    outputs = _mapping(config.get("outputs"), "outputs")
    destination = _resolve_output(
        root,
        output_path if output_path is not None else outputs.get("execution_lock"),
        "S105 execution lock",
    )
    generation_root = _resolve_output(
        root,
        outputs.get("generation_root"),
        "S105 generation root",
    )
    commit_path = _resolve_output(
        root,
        outputs.get("commit_manifest"),
        "S105 commit manifest",
    )
    protected = {
        resolved_config,
        *implementation_paths,
        *predecessor.protected_paths,
        *data.protected_paths,
    }
    if destination in protected or commit_path in protected:
        raise S105ContractError("S105 output would overwrite an input")
    if any(
        source == generation_root
        or source in generation_root.parents
        or generation_root in source.parents
        for source in protected
    ):
        raise S105ContractError("S105 generation root overlaps an input")
    payload = build_execution_lock(
        config_path=resolved_config,
        config=config,
        implementation=implementation,
        predecessor=predecessor,
        data=data,
    )
    publish_immutable_json(destination, payload)
    if (
        sha256_file(destination)
        != hashlib.sha256(canonical_json_bytes(payload, pretty=True)).hexdigest()
    ):
        raise S105ContractError("published S105 execution lock failed verification")
    return payload, destination


__all__ = [
    "ALLOWED_SPLITS",
    "CONFIG_SCHEMA_VERSION",
    "EXECUTION_LOCK_SCHEMA_VERSION",
    "FORBIDDEN_SPLITS",
    "HierarchyDataAudit",
    "S104Evidence",
    "S105ContractError",
    "SEEDS",
    "TASKS",
    "VARIANTS",
    "build_execution_lock",
    "canonical_json_bytes",
    "load_config",
    "load_json_strict",
    "prepare_s105_execution",
    "publish_immutable_json",
    "sha256_file",
    "validate_config",
    "validate_hierarchy_data",
    "validate_implementation_sources",
    "validate_s104_predecessor",
]
