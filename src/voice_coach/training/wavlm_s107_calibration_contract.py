"""Fail-closed preregistration and preflight for WP4 S107 calibration.

The config-only path validates only static policy and pinned source files.
Normal preflight first invokes the frozen S106 public deep validator and binds
its immutable commit/checkpoint.  Only after that succeeds may it resolve the
calibration prediction bundle.  Preflight hashes that bundle but never reads a
Parquet row, imports torch, initializes CUDA, fits a calibrator, or writes a
metric.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
EXECUTION_LOCK_SCHEMA_VERSION = "voice_coach.wavlm_s107_execution_lock.v1"
BUNDLE_SCHEMA_VERSION = "voice_coach.wavlm_s107_calibration_bundle.v1"
S106_COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s106_stack_fusion_commit.v2"
S106_CHECKPOINT_SCHEMA_VERSION = "voice_coach.wavlm_s106_fusion_checkpoint.v1"
INPUT_SCHEMA_VERSION = "voice_coach.wavlm_s107_calibration_input.v1"
ALLOWED_SPLITS = ("calibration",)
FORBIDDEN_SPLITS = ("train", "dev", "stack", "locked_test")
INPUT_COLUMNS = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "uncalibrated_probability",
    "reference_binary_error_label",
    "l1_group",
    "gender_group",
    "proficiency_group",
    "duration_bucket",
    "error_type_group",
    "qc_flagged",
    "reserved_prompt",
)
SLICE_AXES = (
    "l1_group",
    "gender_group",
    "proficiency_group",
    "duration_bucket",
    "error_type_group",
)
ARTIFACT_LAYOUT = (
    (
        "calibration_predictions",
        "calibration_predictions.parquet",
        "parquet",
        "D1_restricted_internal",
    ),
    (
        "calibrator_parameters",
        "calibrator_parameters.json",
        "json",
        "D1_restricted_internal",
    ),
    (
        "threshold_policy_report",
        "threshold_policy_report.json",
        "json",
        "A1_aggregate",
    ),
    (
        "reliability_report",
        "reliability_report.json",
        "json",
        "A1_aggregate",
    ),
    (
        "risk_coverage_report",
        "risk_coverage_report.json",
        "json",
        "A1_aggregate",
    ),
    (
        "slice_report",
        "slice_report.json",
        "json",
        "A1_aggregate",
    ),
    (
        "calibration_only_report",
        "calibration_only_report.json",
        "json",
        "A1_aggregate",
    ),
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SYMBOL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*\.[A-Za-z_][A-Za-z0-9_]*$")


class S107ContractError(ValueError):
    """Raised before protected calibration-row access when policy is invalid."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise S107ContractError(f"YAML contains duplicate key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class PredecessorEvidence:
    commit_path: Path
    commit_sha256: str
    checkpoint_path: Path
    checkpoint_sha256: str
    generation_id: str
    validator_result: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CalibrationBundle:
    path: Path
    sha256: str
    prediction_path: Path
    prediction_sha256: str
    prediction_size_bytes: int
    prediction_row_count: int
    label_status: str
    document: Mapping[str, Any]


PredecessorValidator = Callable[..., Mapping[str, Any]]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S107ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S107ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S107ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S107ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S107ContractError(f"{name} must be at least {minimum}")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S107ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise S107ContractError(f"{name} must be finite")
    return result


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise S107ContractError(f"{name} must be boolean")
    return value


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise S107ContractError(f"{name} must be a lowercase SHA-256")
    return value


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise S107ContractError(f"{name} must equal {expected!r}")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise S107ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_nonfinite_tree(nested, name)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_nonfinite_tree(nested, name)


def _json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise S107ContractError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ).encode("utf-8") + (b"\n" if pretty else b"")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_strict(path: Path, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                S107ContractError(f"{name} contains {token}")
            ),
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise S107ContractError(f"{name} is not strict UTF-8 JSON") from error
    result = _mapping(value, name)
    _reject_nonfinite_tree(result, name)
    return result


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        raise S107ContractError("S107 config is not valid unique-key YAML") from error
    result = _mapping(value, "S107 config")
    _reject_nonfinite_tree(result, "S107 config")
    return result


def _relative_path(value: object, name: str) -> str:
    text = _string(value, name)
    path = Path(text)
    if (
        path.is_absolute()
        or text != path.as_posix()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.startswith(".env") for part in path.parts)
    ):
        raise S107ContractError(f"{name} must be a safe workspace-relative path")
    return text


def _reject_symlink_chain(root: Path, relative: str, name: str) -> Path:
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise S107ContractError(f"{name} cannot traverse a symlink")
    return root / relative


def resolve_file(root: Path, value: object, name: str) -> Path:
    relative = _relative_path(value, name)
    unresolved = _reject_symlink_chain(root, relative, name)
    try:
        path = unresolved.resolve(strict=True)
    except FileNotFoundError as error:
        raise S107ContractError(f"{name} does not exist") from error
    try:
        path.relative_to(root)
    except ValueError as error:
        raise S107ContractError(f"{name} escapes the workspace") from error
    if not path.is_file() or path.is_symlink():
        raise S107ContractError(f"{name} must be a regular non-symlink file")
    return path


def resolve_output(root: Path, value: object, name: str) -> Path:
    relative = _relative_path(value, name)
    unresolved = _reject_symlink_chain(root, relative, name)
    path = unresolved.resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as error:
        raise S107ContractError(f"{name} escapes the workspace") from error
    if path.exists() and path.is_symlink():
        raise S107ContractError(f"{name} cannot be a symlink")
    return path


def workspace_relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as error:
        raise S107ContractError("path is outside the workspace") from error


def file_record(root: Path, path: Path) -> Mapping[str, Any]:
    return {
        "path": workspace_relative(root, path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _binding(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("binding_sha256", None)
    return hashlib.sha256(canonical_json_bytes(unsigned, pretty=False)).hexdigest()


def _validate_grid(start: float, stop: float, step: float) -> None:
    if not (0.0 <= start <= stop <= 1.0 and step > 0.0):
        raise S107ContractError("threshold grid is invalid")
    steps = (stop - start) / step
    if not math.isclose(steps, round(steps), abs_tol=1e-10):
        raise S107ContractError("threshold grid endpoints are not integral")


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate every consequential S107 policy before path resolution."""

    if set(config) != {
        "schema_version",
        "experiment",
        "predecessor",
        "inputs",
        "data",
        "frozen_model_policy",
        "calibrator",
        "threshold_policy",
        "uncertainty",
        "ece",
        "risk_coverage",
        "slices",
        "label_evidence",
        "reporting",
        "runtime",
        "implementation",
        "outputs",
    }:
        raise S107ContractError("S107 config top-level schema differs")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")
    experiment = _mapping(config.get("experiment"), "experiment")
    for name, expected in {
        "id": "aihub71490_wavlm_s107_calibration_v1",
        "stage": "s107_calibration_only_frozen_scorer",
        "task": "phone_binary_error",
        "calibration_only": True,
        "development_only": True,
        "oracle_alignment": True,
        "default_label_status": "silver_oracle_provisional",
        "promotion_decision_allowed": False,
        "s108_readiness_claim_allowed": False,
        "gpu_allowed": False,
    }.items():
        _expect(experiment.get(name), expected, f"experiment.{name}")

    predecessor = _mapping(config.get("predecessor"), "predecessor")
    _expect(
        predecessor.get("stage"),
        "s106_stack_only_confidence_aware_fusion",
        "predecessor.stage",
    )
    _expect(
        predecessor.get("validation_must_complete_before_calibration_path_resolution"),
        True,
        "predecessor ordering",
    )
    predecessor_config = _mapping(predecessor.get("config"), "predecessor.config")
    _relative_path(predecessor_config.get("path"), "predecessor.config.path")
    _sha256(predecessor_config.get("sha256"), "predecessor.config.sha256")
    _relative_path(
        predecessor.get("commit_manifest"),
        "predecessor.commit_manifest",
    )
    _expect(
        predecessor.get("commit_schema_version"),
        S106_COMMIT_SCHEMA_VERSION,
        "predecessor.commit_schema_version",
    )
    _expect(
        predecessor.get("required_commit_status"),
        "committed",
        "predecessor.required_commit_status",
    )
    validator = _mapping(predecessor.get("validator"), "predecessor.validator")
    symbol = _string(validator.get("symbol"), "predecessor.validator.symbol")
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise S107ContractError("predecessor validator symbol is invalid")
    _expect(validator.get("required_status"), "valid", "validator required status")
    validator_module = _mapping(validator.get("module"), "validator.module")
    _relative_path(validator_module.get("path"), "validator.module.path")
    _sha256(validator_module.get("sha256"), "validator.module.sha256")
    checkpoint = _mapping(
        predecessor.get("final_checkpoint"),
        "predecessor.final_checkpoint",
    )
    for name, expected in {
        "artifact_name": "final_fusion_checkpoint",
        "relative_path": "final_fusion_checkpoint.json",
        "schema_version": S106_CHECKPOINT_SCHEMA_VERSION,
        "required_status": "completed",
        "required_family": "standardized_l2_logistic_regression",
    }.items():
        _expect(checkpoint.get(name), expected, f"final_checkpoint.{name}")

    inputs = _mapping(config.get("inputs"), "inputs")
    _relative_path(
        inputs.get("calibration_bundle_commit"),
        "inputs.calibration_bundle_commit",
    )
    _expect(
        inputs.get("bundle_schema_version"),
        BUNDLE_SCHEMA_VERSION,
        "inputs.bundle_schema_version",
    )
    _expect(inputs.get("required_bundle_status"), "committed", "bundle status")
    matrix = _mapping(inputs.get("prediction_matrix"), "inputs.prediction_matrix")
    _expect(matrix.get("format"), "parquet", "prediction matrix format")
    _expect(matrix.get("schema_version"), INPUT_SCHEMA_VERSION, "input schema")
    _expect(matrix.get("exact_columns"), list(INPUT_COLUMNS), "input columns")
    _expect(
        matrix.get("row_key"),
        ["utterance_id_sha256", "phone_index"],
        "input row key",
    )
    _expect(
        matrix.get("probability_column"),
        "uncalibrated_probability",
        "input probability column",
    )
    _expect(
        matrix.get("label_column"),
        "reference_binary_error_label",
        "input label column",
    )

    data = _mapping(config.get("data"), "data")
    _expect(data.get("allowed_splits"), list(ALLOWED_SPLITS), "allowed splits")
    _expect(data.get("forbidden_splits"), list(FORBIDDEN_SPLITS), "forbidden splits")
    for name, expected in {
        "calibration_path_resolution_requires_valid_s106": True,
        "calibration_row_read_requires_valid_s106": True,
        "preflight_semantic_row_read": False,
        "runner_filter_pushdown_required": True,
        "reject_non_calibration_rows": True,
        "exclude_qc_flagged": True,
        "exclude_reserved_prompts": True,
        "require_namespaced_sha256_ids": True,
        "environment_files_allowed": False,
        "raw_audio_access_allowed": False,
    }.items():
        _expect(data.get(name), expected, f"data.{name}")
    _expect(data.get("duplicate_row_key_policy"), "fail", "duplicate row policy")

    frozen = _mapping(config.get("frozen_model_policy"), "frozen_model_policy")
    for name in (
        "encoder_retraining_allowed",
        "head_retraining_allowed",
        "fusion_retraining_allowed",
        "fusion_feature_change_allowed",
        "loss_change_allowed",
    ):
        _expect(frozen.get(name), False, f"frozen_model_policy.{name}")
    _expect(
        frozen.get("permitted_fitted_parameters"),
        ["binary_temperature", "issue_threshold"],
        "permitted fitted parameters",
    )

    calibrator = _mapping(config.get("calibrator"), "calibrator")
    for name, expected in {
        "family": "binary_logit_temperature_scaling",
        "input": "uncalibrated_error_probability",
        "objective": "binary_negative_log_likelihood",
        "optimizer": "scipy_bounded_scalar",
        "intercept_fitted": False,
        "family_search_allowed": False,
        "fit_count": 1,
    }.items():
        _expect(calibrator.get(name), expected, f"calibrator.{name}")
    clip = _list(calibrator.get("probability_clip"), "probability clip")
    bounds = _list(calibrator.get("temperature_bounds"), "temperature bounds")
    _expect(clip, [0.000001, 0.999999], "probability clip")
    _expect(bounds, [0.05, 20.0], "temperature bounds")
    if len(clip) != 2 or not (
        0.0 < _finite(clip[0], "clip") < _finite(clip[1], "clip") < 1.0
    ):
        raise S107ContractError("probability clip is invalid")
    if len(bounds) != 2 or not (
        0.0 < _finite(bounds[0], "temperature") < _finite(bounds[1], "temperature")
    ):
        raise S107ContractError("temperature bounds are invalid")

    threshold = _mapping(config.get("threshold_policy"), "threshold_policy")
    for name, expected in {
        "status": "provisional_engineering_not_promotion",
        "issue_definition": "calibrated_error_probability_gte_issue_threshold",
        "abstention_definition": "every_eligible_phone_not_issued",
        "target_point_precision": 0.90,
        "selection": "maximum_coverage_among_point_precision_eligible",
        "tie_break": ["lower_issue_threshold"],
        "no_eligible_candidate": "abstain_all",
        "promotion_gate_use_allowed": False,
    }.items():
        _expect(threshold.get(name), expected, f"threshold_policy.{name}")
    _expect(
        threshold.get("provisional_minimum_issued_phone_count"),
        30,
        "provisional minimum count",
    )
    coverage_min = _finite(
        threshold.get("provisional_minimum_issued_coverage"),
        "provisional minimum coverage",
    )
    if not 0.0 < coverage_min <= 1.0:
        raise S107ContractError("provisional minimum coverage is invalid")
    _expect(coverage_min, 0.01, "provisional minimum coverage")
    grid = _mapping(threshold.get("grid"), "threshold grid")
    _expect(
        dict(grid),
        {"start": 0.50, "stop": 0.99, "step": 0.01},
        "threshold grid",
    )
    _validate_grid(
        _finite(grid.get("start"), "threshold start"),
        _finite(grid.get("stop"), "threshold stop"),
        _finite(grid.get("step"), "threshold step"),
    )
    _expect(
        threshold.get("unresolved_final_preregistration"),
        [
            "minimum_issued_phone_count",
            "minimum_issued_coverage",
            "ci_method_and_confidence_level",
            "point_estimate_or_ci_lower_bound_gate",
            "within_utterance_cluster_uncertainty",
        ],
        "unresolved threshold preregistration",
    )

    uncertainty = _mapping(config.get("uncertainty"), "uncertainty")
    for name, expected in {
        "status": "provisional_engineering_not_promotion",
        "method": "percentile_cluster_bootstrap",
        "cluster_unit": "speaker_id_sha256",
        "confidence_level": 0.95,
        "replicates": 2000,
        "seed": 107,
        "used_for_threshold_selection": False,
        "used_for_promotion_gate": False,
    }.items():
        _expect(uncertainty.get(name), expected, f"uncertainty.{name}")

    ece = _mapping(config.get("ece"), "ece")
    for name, expected in {
        "bins": 15,
        "binning": "equal_width",
        "interval_policy": "left_closed_right_open_last_closed",
        "empty_bin_policy": "retain_with_count_zero_null_statistics_zero_contribution",
        "primary_probability": "calibrated_error_probability",
        "event": "reference_binary_error_label_equals_one",
        "gate_pass_maximum": 0.10,
        "gray_zone_maximum": 0.15,
        "silver_result_is_promotion_evidence": False,
    }.items():
        _expect(ece.get(name), expected, f"ece.{name}")

    risk = _mapping(config.get("risk_coverage"), "risk_coverage")
    _expect(
        risk.get("ranking"),
        "descending_calibrated_error_probability",
        "risk ranking",
    )
    _expect(risk.get("risk"), "one_minus_issued_precision", "risk definition")
    _expect(
        risk.get("target_coverages"),
        [0.01, 0.02, 0.05, 0.10, 0.20, 0.40, 0.60, 0.80, 1.00],
        "risk target coverages",
    )
    _expect(risk.get("include_selected_issue_policy_point"), True, "risk policy point")

    slices = _mapping(config.get("slices"), "slices")
    _expect(slices.get("axes"), list(SLICE_AXES), "slice axes")
    _integer(
        slices.get("provisional_minimum_phone_rows"),
        "slice minimum rows",
        minimum=1,
    )
    _expect(
        slices.get("small_cell_policy"),
        "aggregate_suppressed_without_group_names",
        "small cell policy",
    )
    _expect(slices.get("promotion_gap_gate_allowed"), False, "slice promotion gate")
    _expect(
        slices.get("final_minimum_speaker_positive_negative_support"),
        "TBD_PREREGISTRATION",
        "slice final support",
    )

    labels = _mapping(config.get("label_evidence"), "label_evidence")
    _expect(
        labels.get("allowed_statuses"),
        ["silver_oracle_provisional", "expert_gold_d07"],
        "label statuses",
    )
    _expect(
        labels.get("canonical_run_required_status"),
        "silver_oracle_provisional",
        "canonical label status",
    )
    _expect(
        labels.get("final_promotion_without_expert_gold"),
        "fail_closed",
        "expert promotion policy",
    )
    expert = _mapping(labels.get("expert_gold"), "expert_gold")
    _expect(expert.get("d07_completed_evidence_required"), True, "D07 required")
    _expect(
        expert.get("evidence_schema"),
        "TBD_PREREGISTRATION",
        "D07 evidence schema",
    )

    reporting = _mapping(config.get("reporting"), "reporting")
    _expect(reporting.get("result_status"), "provisional_engineering", "result status")
    _expect(
        reporting.get("s108_ready_status"),
        "blocked_preregistration_and_d07",
        "S108 status",
    )
    for name in (
        "error_type_claim_allowed",
        "coaching_claim_allowed",
        "expert_valid_claim_allowed",
    ):
        _expect(reporting.get(name), False, f"reporting.{name}")

    runtime = _mapping(config.get("runtime"), "runtime")
    for name, expected in {
        "device": "cpu",
        "cuda_allowed": False,
        "deterministic": True,
        "thread_count": 1,
        "environment_files_forbidden": True,
        "temporary_generation_cleanup_required": True,
    }.items():
        _expect(runtime.get(name), expected, f"runtime.{name}")

    implementation = _mapping(config.get("implementation"), "implementation")
    for name in (
        "preflight_script",
        "calibration_script",
        "contract_module",
        "calibration_module",
        "project_manifest",
        "scorer_lock",
    ):
        record = _mapping(implementation.get(name), f"implementation.{name}")
        _relative_path(record.get("path"), f"implementation.{name}.path")
        _sha256(record.get("sha256"), f"implementation.{name}.sha256")

    outputs = _mapping(config.get("outputs"), "outputs")
    for name in (
        "execution_lock",
        "generation_root",
        "commit_manifest",
        "registry_root",
    ):
        _relative_path(outputs.get(name), f"outputs.{name}")
    _string(outputs.get("planned_registry_run_id"), "planned registry run id")
    for name in (
        "atomic_publish",
        "content_addressed_generation",
    ):
        _expect(outputs.get(name), True, f"outputs.{name}")
    for name in ("unexpected_artifacts_allowed",):
        _expect(outputs.get(name), False, f"outputs.{name}")
    _expect(
        outputs.get("expected_artifact_count"), len(ARTIFACT_LAYOUT), "artifact count"
    )
    expected_contract = [
        {
            "name": name,
            "relative_path": path,
            "format": format_name,
            "access_scope": scope,
        }
        for name, path, format_name, scope in ARTIFACT_LAYOUT
    ]
    _expect(outputs.get("artifact_contract"), expected_contract, "artifact contract")
    _expect(outputs.get("registry_schema_version"), 1, "registry schema")
    _expect(
        outputs.get("commit_schema_version"),
        "voice_coach.wavlm_s107_calibration_commit.v1",
        "commit schema",
    )


def validate_implementation_sources(
    *, workspace_root: Path, config: Mapping[str, Any]
) -> Mapping[str, Mapping[str, Any]]:
    implementation = _mapping(config.get("implementation"), "implementation")
    result: dict[str, Mapping[str, Any]] = {}
    for name in (
        "preflight_script",
        "calibration_script",
        "contract_module",
        "calibration_module",
        "project_manifest",
        "scorer_lock",
    ):
        record = _mapping(implementation.get(name), f"implementation.{name}")
        path = resolve_file(
            workspace_root, record.get("path"), f"implementation.{name}"
        )
        expected = _sha256(record.get("sha256"), f"implementation.{name}.sha256")
        actual = sha256_file(path)
        if actual != expected:
            raise S107ContractError(f"implementation source hash differs: {name}")
        result[name] = file_record(workspace_root, path)
    return result


def _load_validator(symbol: str) -> PredecessorValidator:
    module_name, attribute = symbol.rsplit(".", 1)
    try:
        module = importlib.import_module(module_name)
        validator = getattr(module, attribute)
    except (ImportError, AttributeError) as error:
        raise S107ContractError("cannot import the frozen S106 validator") from error
    if not callable(validator):
        raise S107ContractError("S106 validator is not callable")
    return validator


def validate_s106_predecessor(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    validator: PredecessorValidator | None = None,
) -> PredecessorEvidence:
    """Validate S106 completely before touching a calibration locator."""

    predecessor = _mapping(config.get("predecessor"), "predecessor")
    predecessor_config = _mapping(predecessor.get("config"), "predecessor.config")
    s106_config_path = resolve_file(
        workspace_root,
        predecessor_config.get("path"),
        "S106 config",
    )
    if sha256_file(s106_config_path) != predecessor_config.get("sha256"):
        raise S107ContractError("frozen S106 config hash differs")
    validator_contract = _mapping(predecessor.get("validator"), "validator")
    validator_module = _mapping(validator_contract.get("module"), "validator.module")
    validator_path = resolve_file(
        workspace_root,
        validator_module.get("path"),
        "S106 validator module",
    )
    if sha256_file(validator_path) != validator_module.get("sha256"):
        raise S107ContractError("frozen S106 validator module hash differs")

    # This existence/regular-file check is intentionally before importing or
    # invoking the validator and, critically, before resolving the calibration
    # bundle path.
    commit_path = resolve_file(
        workspace_root,
        predecessor.get("commit_manifest"),
        "S106 commit",
    )
    commit_sha = sha256_file(commit_path)
    invoker = validator or _load_validator(
        _string(validator_contract.get("symbol"), "validator.symbol")
    )
    try:
        result = invoker(
            workspace_root=workspace_root,
            config_path=s106_config_path,
        )
    except Exception as error:
        raise S107ContractError("independent S106 validation failed") from error
    if (
        result.get("status") != "valid"
        or result.get("commit_sha256") != commit_sha
        or result.get("stack_only") is not True
        or result.get("calibration_accessed") is not False
        or result.get("locked_test_accessed") is not False
        or result.get("full_stack_in_sample_metric_reported") is not False
    ):
        raise S107ContractError("S106 validator result does not satisfy S107")

    commit = load_json_strict(commit_path, "S106 commit")
    if set(commit) != {
        "schema_version",
        "status",
        "generation_id",
        "generation_path",
        "artifacts",
        "config",
        "execution_lock",
        "component_bundle_commit",
        "registry",
        "binding_sha256",
    }:
        raise S107ContractError("S106 commit schema differs")
    if (
        commit.get("schema_version") != S106_COMMIT_SCHEMA_VERSION
        or commit.get("status") != "committed"
        or not SHA256_PATTERN.fullmatch(str(commit.get("generation_id", "")))
        or commit.get("binding_sha256") != _binding(commit)
    ):
        raise S107ContractError("S106 commit binding differs")
    config_record = _mapping(commit.get("config"), "S106 commit config")
    if config_record != file_record(workspace_root, s106_config_path):
        raise S107ContractError("S106 commit config record differs")
    artifacts = _list(commit.get("artifacts"), "S106 commit artifacts")
    if len(artifacts) != 7:
        raise S107ContractError("S106 commit artifact count differs")
    checkpoint_contract = _mapping(
        predecessor.get("final_checkpoint"),
        "final checkpoint contract",
    )
    matches = [
        _mapping(record, "S106 artifact")
        for record in artifacts
        if isinstance(record, Mapping)
        and record.get("name") == checkpoint_contract.get("artifact_name")
    ]
    if len(matches) != 1:
        raise S107ContractError("S106 final checkpoint artifact is not unique")
    checkpoint_record = matches[0]
    if checkpoint_record.get("path") != checkpoint_contract.get("relative_path"):
        raise S107ContractError("S106 final checkpoint relative path differs")
    generation_path = resolve_output(
        workspace_root,
        commit.get("generation_path"),
        "S106 generation path",
    )
    if (
        not generation_path.is_dir()
        or generation_path.is_symlink()
        or generation_path.name != commit.get("generation_id")
    ):
        raise S107ContractError("S106 generation path differs")
    checkpoint_path = resolve_file(
        workspace_root,
        workspace_relative(
            workspace_root, generation_path / str(checkpoint_record["path"])
        ),
        "S106 final checkpoint",
    )
    if (
        checkpoint_record.get("sha256") != sha256_file(checkpoint_path)
        or checkpoint_record.get("size_bytes") != checkpoint_path.stat().st_size
        or checkpoint_record.get("format") != "json"
        or checkpoint_record.get("access_scope") != "D1_restricted_internal"
    ):
        raise S107ContractError("S106 final checkpoint artifact binding differs")
    checkpoint = load_json_strict(checkpoint_path, "S106 final checkpoint")
    if (
        checkpoint.get("schema_version") != checkpoint_contract.get("schema_version")
        or checkpoint.get("status") != checkpoint_contract.get("required_status")
        or checkpoint.get("family") != checkpoint_contract.get("required_family")
        or checkpoint.get("fit_source") != "full_stack_after_oof_selection"
        or checkpoint.get("fit_count") != 1
        or checkpoint.get("in_sample_prediction_computed") is not False
        or checkpoint.get("in_sample_metric_computed") is not False
        or checkpoint.get("in_sample_metric_reported") is not False
    ):
        raise S107ContractError("S106 final checkpoint policy differs")
    if sha256_file(commit_path) != commit_sha:
        raise S107ContractError("S106 commit changed during validation")
    return PredecessorEvidence(
        commit_path=commit_path,
        commit_sha256=commit_sha,
        checkpoint_path=checkpoint_path,
        checkpoint_sha256=sha256_file(checkpoint_path),
        generation_id=_sha256(commit.get("generation_id"), "S106 generation id"),
        validator_result=dict(result),
    )


def _validate_file_record(
    *,
    root: Path,
    value: object,
    name: str,
    expected_path: Path,
    row_count_required: bool,
) -> Mapping[str, Any]:
    record = _mapping(value, name)
    expected_keys = {"path", "sha256", "size_bytes"}
    if row_count_required:
        expected_keys.add("row_count")
    if set(record) != expected_keys:
        raise S107ContractError(f"{name} record schema differs")
    path = resolve_file(root, record.get("path"), f"{name}.path")
    if path != expected_path:
        raise S107ContractError(f"{name} path differs")
    if (
        record.get("sha256") != sha256_file(path)
        or record.get("size_bytes") != path.stat().st_size
    ):
        raise S107ContractError(f"{name} file binding differs")
    if row_count_required:
        _integer(record.get("row_count"), f"{name}.row_count", minimum=1)
    return record


def validate_calibration_bundle(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    predecessor: PredecessorEvidence,
) -> CalibrationBundle:
    """Resolve/hash the bundle only after valid predecessor evidence exists."""

    inputs = _mapping(config.get("inputs"), "inputs")
    path = resolve_file(
        workspace_root,
        inputs.get("calibration_bundle_commit"),
        "S107 calibration bundle commit",
    )
    bundle_sha = sha256_file(path)
    document = load_json_strict(path, "S107 calibration bundle commit")
    if set(document) != {
        "schema_version",
        "status",
        "s106_commit",
        "s106_checkpoint",
        "label_status",
        "d07_evidence",
        "prediction_matrix",
        "binding_sha256",
    }:
        raise S107ContractError("S107 calibration bundle schema differs")
    if (
        document.get("schema_version") != BUNDLE_SCHEMA_VERSION
        or document.get("status") != "committed"
        or document.get("binding_sha256") != _binding(document)
    ):
        raise S107ContractError("S107 calibration bundle binding differs")
    _validate_file_record(
        root=workspace_root,
        value=document.get("s106_commit"),
        name="bundle S106 commit",
        expected_path=predecessor.commit_path,
        row_count_required=False,
    )
    _validate_file_record(
        root=workspace_root,
        value=document.get("s106_checkpoint"),
        name="bundle S106 checkpoint",
        expected_path=predecessor.checkpoint_path,
        row_count_required=False,
    )
    labels = _mapping(config.get("label_evidence"), "label_evidence")
    label_status = _string(document.get("label_status"), "bundle label status")
    if label_status not in labels.get("allowed_statuses", []):
        raise S107ContractError("bundle label status is not allowed")
    if label_status != labels.get("canonical_run_required_status"):
        raise S107ContractError(
            "expert-gold execution is blocked until D07 evidence schema is frozen"
        )
    if document.get("d07_evidence") is not None:
        raise S107ContractError("canonical silver run cannot claim D07 evidence")
    prediction_record = _mapping(
        document.get("prediction_matrix"),
        "bundle prediction matrix",
    )
    prediction_path = resolve_file(
        workspace_root,
        prediction_record.get("path"),
        "calibration prediction matrix",
    )
    _validate_file_record(
        root=workspace_root,
        value=prediction_record,
        name="bundle prediction matrix",
        expected_path=prediction_path,
        row_count_required=True,
    )
    if sha256_file(path) != bundle_sha:
        raise S107ContractError("calibration bundle changed during validation")
    return CalibrationBundle(
        path=path,
        sha256=bundle_sha,
        prediction_path=prediction_path,
        prediction_sha256=sha256_file(prediction_path),
        prediction_size_bytes=prediction_path.stat().st_size,
        prediction_row_count=_integer(
            prediction_record.get("row_count"),
            "prediction row count",
            minimum=1,
        ),
        label_status=label_status,
        document=document,
    )


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _publish_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise S107ContractError(
                f"immutable output already exists: {path.name}"
            ) from error
    finally:
        temporary.unlink(missing_ok=True)


def build_execution_lock(
    *,
    workspace_root: Path,
    config_path: Path,
    implementation: Mapping[str, Mapping[str, Any]],
    predecessor: PredecessorEvidence,
    bundle: CalibrationBundle,
) -> Mapping[str, Any]:
    return {
        "schema_version": EXECUTION_LOCK_SCHEMA_VERSION,
        "status": "prepared",
        "created_at_utc": _now_utc(),
        "experiment_id": "aihub71490_wavlm_s107_calibration_v1",
        "stage": "s107_calibration_only_frozen_scorer",
        "config": file_record(workspace_root, config_path),
        "implementation": dict(implementation),
        "predecessor": {
            "stage": "s106_stack_only_confidence_aware_fusion",
            "validation_status": "valid",
            "commit": file_record(workspace_root, predecessor.commit_path),
            "checkpoint": file_record(workspace_root, predecessor.checkpoint_path),
            "generation_id": predecessor.generation_id,
            "full_stack_in_sample_metric_reported": False,
        },
        "calibration_bundle": file_record(workspace_root, bundle.path),
        "prediction_matrix": {
            **file_record(workspace_root, bundle.prediction_path),
            "row_count": bundle.prediction_row_count,
            "semantic_rows_read_by_preflight": False,
        },
        "label_status": bundle.label_status,
        "split_access": {
            "train": False,
            "dev": False,
            "stack": False,
            "calibration": False,
            "locked_test": False,
        },
        "raw_audio_accessed": False,
        "environment_files_read": [],
        "gpu_initialized": False,
        "metrics_computed": False,
        "output_role": "no_metric_execution_lock",
    }


def prepare_s107_execution(
    *,
    workspace_root: Path,
    config_path: Path,
    output_path: Path | None = None,
    predecessor_validator: PredecessorValidator | None = None,
) -> tuple[Mapping[str, Any], Path]:
    root = workspace_root.resolve(strict=True)
    resolved_config = config_path
    if not resolved_config.is_absolute():
        resolved_config = root / resolved_config
    resolved_config = resolved_config.resolve(strict=True)
    config = load_config(resolved_config)
    validate_config(config)
    implementation = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )
    predecessor = validate_s106_predecessor(
        workspace_root=root,
        config=config,
        validator=predecessor_validator,
    )
    bundle = validate_calibration_bundle(
        workspace_root=root,
        config=config,
        predecessor=predecessor,
    )
    lock = build_execution_lock(
        workspace_root=root,
        config_path=resolved_config,
        implementation=implementation,
        predecessor=predecessor,
        bundle=bundle,
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    if output_path is not None:
        candidate = output_path
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            relative_output = candidate.relative_to(root).as_posix()
        except ValueError as error:
            raise S107ContractError(
                "execution lock output escapes workspace"
            ) from error
        destination = resolve_output(
            root,
            relative_output,
            "execution lock output override",
        )
    else:
        destination = resolve_output(
            root,
            outputs.get("execution_lock"),
            "execution lock",
        )
    _publish_immutable_json(destination, lock)
    return lock, destination


__all__ = [
    "ALLOWED_SPLITS",
    "ARTIFACT_LAYOUT",
    "BUNDLE_SCHEMA_VERSION",
    "CalibrationBundle",
    "EXECUTION_LOCK_SCHEMA_VERSION",
    "FORBIDDEN_SPLITS",
    "INPUT_COLUMNS",
    "INPUT_SCHEMA_VERSION",
    "PredecessorEvidence",
    "S107ContractError",
    "SLICE_AXES",
    "build_execution_lock",
    "canonical_json_bytes",
    "file_record",
    "load_config",
    "load_json_strict",
    "prepare_s107_execution",
    "resolve_file",
    "resolve_output",
    "sha256_file",
    "validate_calibration_bundle",
    "validate_config",
    "validate_implementation_sources",
    "validate_s106_predecessor",
    "workspace_relative",
]
