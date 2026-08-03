"""Fail-closed S203 expert-holdout audit readiness contract.

This module is intentionally *not* an evaluator.  It cannot open the S203
holdout, combine row-level predictions, compute a metric, publish a result, or
change a frozen S1/S2 model.  It validates a static C1 policy and, in full
mode, proves this predecessor order before an expert attestation can even be
resolved:

1. S108 final S1 ``research_candidate_passed``;
2. the S200 independence-readiness lock bound to S108;
3. the completed S201 content hard gate bound to S108/S200;
4. public and deep validation of the completed, frozen S202 commits.

The documented Phase-3 numerical gates are preserved, but their
numerator/denominator, severity taxonomy, confidence interval, clustering,
slice, minimum-support, multiplicity, adjudication, and one-shot transaction
details remain ``TBD_PREREGISTRATION``.  A future human-approved
preregistration must resolve those choices before the holdout attestation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

import yaml  # type: ignore[import-untyped]

from voice_coach.training import s2_s202_auditor_contract as s202


CONFIG_SCHEMA_VERSION = 1
STATIC_STATUS = "static_preflight_only"
TBD = "TBD_PREREGISTRATION"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
S202_AGGREGATE_SCHEMA = "voice_coach.s2_s202_independent_auditor_commit.v1"
S202_SEED_SCHEMA = "voice_coach.s2_s202_independent_auditor_seed_commit.v1"
S203_PREREGISTRATION_SCHEMA = "voice_coach.s2_s203_preregistration.v1"
S203_ATTESTATION_SCHEMA = "voice_coach.s2_s203_expert_holdout_attestation.v1"

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessors",
    "frozen_evaluation_inputs",
    "expert_holdout",
    "d07_separation",
    "evaluation_contract",
    "privacy_and_license",
    "execution_order",
    "outputs",
}

DOCUMENTED_GATES = {
    "serious_s1_misjudgment_recall": ("greater_than_or_equal", 0.80),
    "false_high_score_relative_reduction": ("greater_than_or_equal", 0.30),
    "normal_sample_referral_rate": ("less_than_or_equal", 0.15),
    "disagreement_expert_error_hit_rate": ("greater_than_or_equal", 0.60),
}

S202_AGGREGATE_ARTIFACTS = (
    "s202_config",
    "data_and_label_card",
    "aggregate_dev_report",
    "ensemble_freeze_manifest",
    "independent_validation_receipt",
)

S202_SEED_ARTIFACTS = (
    "seed_checkpoint",
    "privacy_safe_seed_dev_predictions",
    "seed_dev_report",
    "seed_freeze_manifest",
)

EVALUATION_JOIN_FIELDS = (
    "item_id_sha256",
    "speaker_id_sha256",
    "frozen_s1_decision",
    "frozen_s2_decision",
    "adjudicated_s2_expert_gold",
)

UNRESOLVED_PREREGISTRATION_FIELDS = (
    "primary_metric_family",
    "serious_misjudgment_definition",
    "severe_label_taxonomy",
    "severe_label_threshold",
    "false_high_score_definition",
    "normal_sample_definition",
    "referral_definition",
    "disagreement_definition",
    "numerator_denominator_rules",
    "confidence_interval_method",
    "confidence_level",
    "resampling_unit",
    "resampling_replicates",
    "cluster_definition",
    "slice_dimensions",
    "minimum_slice_support",
    "small_slice_policy",
    "multiplicity_control",
    "missing_label_policy",
    "abstention_policy",
    "rater_qualification",
    "rater_blinding",
    "adjudication_trigger",
    "adjudication_protocol",
    "agreement_metric_and_gate",
    "one_shot_access_family",
    "one_shot_transaction_protocol",
    "prediction_aggregation_rule",
    "gate_statistic",
)

EXPECTED_ORDER = (
    "validate_s203_c1_config_and_source_pins",
    "public_validate_s202_static_contract",
    "deep_validate_s108_final_passed_commit",
    "deep_validate_s200_readiness_lock",
    "deep_validate_s201_completed_content_hard_gate_commit",
    "public_validate_s202_completed_commits",
    "deep_validate_s202_completed_commits_and_artifacts",
    "validate_frozen_s203_preregistration",
    "validate_s203_expert_holdout_attestation",
    "stop_before_holdout_data_resolution_until_evaluator_is_implemented",
)


class S203ContractError(ValueError):
    """Raised before an unsafe or unpreregistered S203 action."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "static_contract",
        audit: AccessAudit | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.audit = audit


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
            raise S203ContractError(f"YAML contains duplicate key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(slots=True)
class AccessAudit:
    """Counters that prove the predecessor and no-access ordering."""

    source_pin_read_count: int = 0
    predecessor_public_source_read_count: int = 0
    predecessor_public_validation_count: int = 0
    predecessor_metadata_read_attempt_count: int = 0
    predecessor_metadata_read_count: int = 0
    predecessor_artifact_read_count: int = 0
    s108_validation_count: int = 0
    s200_validation_count: int = 0
    s201_validation_count: int = 0
    s202_public_validation_count: int = 0
    s202_deep_validation_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    expert_attestation_read_attempt_count: int = 0
    expert_attestation_read_count: int = 0
    d07_artifact_read_count: int = 0
    data_path_resolution_count: int = 0
    data_manifest_read_count: int = 0
    data_row_read_count: int = 0
    audio_read_count: int = 0
    protected_data_read_count: int = 0
    expert_holdout_read_count: int = 0
    holdout_access_transaction_count: int = 0
    model_path_resolution_count: int = 0
    model_weight_read_count: int = 0
    model_retrain_count: int = 0
    model_reselection_count: int = 0
    s1_prediction_read_count: int = 0
    s2_prediction_read_count: int = 0
    prediction_join_count: int = 0
    locked_test_read_count: int = 0
    gpu_access_count: int = 0
    metric_count: int = 0
    result_count: int = 0
    output_path_resolution_count: int = 0
    output_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    sha256: str
    size_bytes: int
    device: int
    inode: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class StaticValidation:
    config_sha256: str
    policy_sha256: str
    source_snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True, slots=True)
class PredecessorEvidence:
    s202_config: Mapping[str, Any]
    s202_static: s202.StaticValidation
    s108: s202.S108Evidence
    s200: s202.S200Evidence
    s201: s202.S201Evidence


@dataclass(frozen=True, slots=True)
class S202PublicEvidence:
    aggregate_commit: Mapping[str, Any]
    aggregate_snapshot: FileSnapshot
    seed_commits: tuple[Mapping[str, Any], ...]
    seed_snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True, slots=True)
class S202DeepEvidence:
    public: S202PublicEvidence
    registry_snapshots: tuple[FileSnapshot, ...]
    artifact_snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True, slots=True)
class PreregistrationEvidence:
    value: Mapping[str, Any]
    snapshot: FileSnapshot


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S203ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S203ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S203ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S203ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S203ContractError(f"{name} must be >= {minimum}")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S203ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise S203ContractError(f"{name} must be finite")
    return result


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if SHA256_RE.fullmatch(text) is None:
        raise S203ContractError(f"{name} must be lowercase SHA-256")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise S203ContractError(f"{name} must be {expected!r}, got {actual!r}")


def _expect_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise S203ContractError(f"{name} keys differ; missing={missing}, extra={extra}")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise S203ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_nonfinite_tree(nested, f"{name}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_nonfinite_tree(nested, f"{name}[{index}]")


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise S203ContractError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise S203ContractError(f"JSON contains non-finite constant: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    _reject_nonfinite_tree(value, "canonical JSON")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _binding_sha256(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("binding_sha256", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _relative_string(value: object, name: str, *, allow_tbd: bool = False) -> str:
    text = _string(value, name)
    if allow_tbd and text == TBD:
        return text
    path = PurePosixPath(text)
    if path.is_absolute() or text != path.as_posix() or ".." in path.parts:
        raise S203ContractError(f"{name} must be a normalized relative path")
    if not path.parts or any(part in {"", "."} for part in path.parts):
        raise S203ContractError(f"{name} must be a normalized relative path")
    return text


def _reject_symlink_chain(root: Path, relative: str, name: str) -> Path:
    target = root / relative
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise S203ContractError(f"{name} path contains symlink: {relative}")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise S203ContractError(f"{name} escapes workspace root") from exc
    return target


def _read_file(root: Path, relative: str, name: str) -> tuple[bytes, FileSnapshot]:
    normalized = _relative_string(relative, name)
    path = _reject_symlink_chain(root, normalized, name)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise S203ContractError(f"{name} unavailable: {normalized}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise S203ContractError(f"{name} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
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
        raise S203ContractError(f"{name} changed while being read")
    data = b"".join(chunks)
    if len(data) != after.st_size:
        raise S203ContractError(f"{name} size changed while being read")
    return data, FileSnapshot(
        path=normalized,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        device=after.st_dev,
        inode=after.st_ino,
        modified_ns=after.st_mtime_ns,
    )


def _snapshot_file(root: Path, relative: str, name: str) -> FileSnapshot:
    return _read_file(root, relative, name)[1]


def _verify_snapshot(root: Path, snapshot: Any, name: str) -> None:
    current = _snapshot_file(root, snapshot.path, name)
    if current != snapshot:
        raise S203ContractError(f"{name} changed after validation")


def _parse_json(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S203ContractError(f"{name} is not strict UTF-8 JSON: {exc}") from exc
    _reject_nonfinite_tree(value, name)
    return _mapping(value, name)


def _read_json_metadata(
    *,
    root: Path,
    relative: str,
    name: str,
    audit: AccessAudit,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    audit.predecessor_metadata_read_attempt_count += 1
    data, snapshot = _read_file(root, relative, name)
    audit.predecessor_metadata_read_count += 1
    return _parse_json(data, name), snapshot


def load_config(path: Path) -> Mapping[str, Any]:
    if path.is_symlink():
        raise S203ContractError("S203 config may not be a symlink")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise S203ContractError(f"cannot read S203 config: {exc}") from exc
    try:
        value = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise S203ContractError(f"invalid S203 YAML: {exc}") from exc
    _reject_nonfinite_tree(value, "S203 config")
    return _mapping(value, "S203 config")


def _validate_experiment(config: Mapping[str, Any]) -> None:
    experiment = _mapping(config["experiment"], "experiment")
    _expect_keys(
        experiment,
        {
            "id",
            "stage",
            "implementation_status",
            "production_evaluator_implemented",
            "actual_holdout_access_allowed",
            "actual_metric_generation_allowed",
            "actual_prediction_join_allowed",
            "actual_result_generation_allowed",
            "gpu_access_allowed",
            "model_retraining_allowed",
            "model_reselection_allowed",
            "completion_claim_allowed",
            "deployment_approval_allowed",
        },
        "experiment",
    )
    _expect(experiment.get("id"), "s2_s203_expert_holdout_audit_v1", "experiment id")
    _expect(experiment.get("stage"), "s203_expert_holdout_one_shot_audit", "stage")
    _expect(experiment.get("implementation_status"), STATIC_STATUS, "status")
    for field in (
        "production_evaluator_implemented",
        "actual_holdout_access_allowed",
        "actual_metric_generation_allowed",
        "actual_prediction_join_allowed",
        "actual_result_generation_allowed",
        "gpu_access_allowed",
        "model_retraining_allowed",
        "model_reselection_allowed",
        "completion_claim_allowed",
        "deployment_approval_allowed",
    ):
        _expect(experiment.get(field), False, f"experiment {field}")


def _validate_predecessors(config: Mapping[str, Any]) -> None:
    predecessors = _mapping(config["predecessors"], "predecessors")
    _expect_keys(
        predecessors,
        {
            "validate_before_preregistration",
            "validate_before_expert_attestation",
            "validate_before_data_path_resolution",
            "validate_before_output_path_resolution",
            "s108_final",
            "s200_readiness",
            "s201_content_gate",
            "s202_auditor",
        },
        "predecessors",
    )
    for field in (
        "validate_before_preregistration",
        "validate_before_expert_attestation",
        "validate_before_data_path_resolution",
        "validate_before_output_path_resolution",
    ):
        _expect(predecessors.get(field), True, f"predecessor {field}")
    s108 = _mapping(predecessors.get("s108_final"), "S108 predecessor")
    _expect_keys(
        s108,
        {
            "commit_path",
            "registry_path",
            "required_decision",
            "deep_validation_required",
        },
        "S108 predecessor",
    )
    _relative_string(s108.get("commit_path"), "S108 commit path")
    _relative_string(s108.get("registry_path"), "S108 registry path")
    _expect(s108.get("required_decision"), "research_candidate_passed", "S108 decision")
    _expect(s108.get("deep_validation_required"), True, "S108 deep validation")
    s200 = _mapping(predecessors.get("s200_readiness"), "S200 predecessor")
    _expect_keys(
        s200,
        {
            "config_path",
            "lock_path",
            "required_status",
            "deep_validation_required",
        },
        "S200 predecessor",
    )
    _relative_string(s200.get("config_path"), "S200 config path")
    _relative_string(s200.get("lock_path"), "S200 lock path")
    _expect(s200.get("required_status"), "readiness_contract_published", "S200 status")
    _expect(s200.get("deep_validation_required"), True, "S200 deep validation")
    s201 = _mapping(predecessors.get("s201_content_gate"), "S201 predecessor")
    _expect_keys(
        s201,
        {
            "config_path",
            "commit_path",
            "registry_path",
            "required_decision",
            "deep_validation_required",
        },
        "S201 predecessor",
    )
    _relative_string(s201.get("config_path"), "S201 config path")
    _relative_string(s201.get("commit_path"), "S201 commit path")
    _relative_string(s201.get("registry_path"), "S201 registry path")
    _expect(s201.get("required_decision"), "content_hard_gate_passed", "S201 decision")
    _expect(s201.get("deep_validation_required"), True, "S201 deep validation")
    s202_contract = _mapping(predecessors.get("s202_auditor"), "S202 predecessor")
    _expect_keys(
        s202_contract,
        {
            "config_path",
            "artifact_root",
            "aggregate_commit_path",
            "aggregate_schema_version",
            "seed_schema_version",
            "required_status",
            "required_decision",
            "public_validation_required",
            "deep_validation_required",
            "aggregate_artifact_names",
            "seed_artifact_names",
            "seeds",
        },
        "S202 predecessor",
    )
    _relative_string(s202_contract.get("config_path"), "S202 config path")
    artifact_root = _relative_string(
        s202_contract.get("artifact_root"),
        "S202 artifact root",
    )
    aggregate_path = _relative_string(
        s202_contract.get("aggregate_commit_path"),
        "S202 aggregate commit path",
    )
    if not aggregate_path.startswith(artifact_root + "/"):
        raise S203ContractError("S202 aggregate commit escapes artifact root")
    _expect(
        s202_contract.get("aggregate_schema_version"),
        S202_AGGREGATE_SCHEMA,
        "S202 aggregate schema",
    )
    _expect(
        s202_contract.get("seed_schema_version"),
        S202_SEED_SCHEMA,
        "S202 seed schema",
    )
    _expect(s202_contract.get("required_status"), "committed", "S202 status")
    _expect(
        s202_contract.get("required_decision"),
        "independent_auditor_frozen",
        "S202 decision",
    )
    _expect(s202_contract.get("public_validation_required"), True, "S202 public")
    _expect(s202_contract.get("deep_validation_required"), True, "S202 deep")
    _expect(
        tuple(s202_contract.get("aggregate_artifact_names", [])),
        S202_AGGREGATE_ARTIFACTS,
        "S202 aggregate artifacts",
    )
    _expect(
        tuple(s202_contract.get("seed_artifact_names", [])),
        S202_SEED_ARTIFACTS,
        "S202 seed artifacts",
    )
    seeds = _list(s202_contract.get("seeds"), "S202 seeds")
    if len(seeds) != 3:
        raise S203ContractError("S202 must have exactly three seed contracts")
    observed: list[int] = []
    for index, raw in enumerate(seeds):
        seed = _mapping(raw, f"S202 seed {index}")
        _expect_keys(
            seed,
            {"seed", "commit_path", "registry_path", "registry_run_id"},
            f"S202 seed {index}",
        )
        value = _integer(seed.get("seed"), f"S202 seed {index}", minimum=0)
        observed.append(value)
        commit_path = _relative_string(
            seed.get("commit_path"),
            f"S202 seed {value} commit",
        )
        if not commit_path.startswith(artifact_root + "/"):
            raise S203ContractError(f"S202 seed {value} commit escapes artifact root")
        _relative_string(seed.get("registry_path"), f"S202 seed {value} registry")
        _expect(
            seed.get("registry_run_id"),
            f"aihub71490-s2-s202-auditor-v1-seed-{value}",
            f"S202 seed {value} run id",
        )
    _expect(observed, [42, 43, 44], "S202 seed order")


def _validate_frozen_inputs(config: Mapping[str, Any]) -> None:
    frozen = _mapping(config["frozen_evaluation_inputs"], "frozen inputs")
    _expect_keys(
        frozen,
        {
            "s1_source",
            "s2_source",
            "s1_retraining_allowed",
            "s1_reselection_allowed",
            "s2_retraining_allowed",
            "s2_reselection_allowed",
            "result_based_threshold_change_allowed",
            "predictions_allowed_for_evaluation_join_only",
            "predictions_allowed_as_training_features",
            "predictions_allowed_as_model_features",
            "predictions_allowed_as_selection_inputs",
            "locked_test_row_or_prediction_reuse_allowed",
            "evaluation_join_fields",
            "s1_prediction_path",
            "s2_prediction_path",
        },
        "frozen inputs",
    )
    _expect(frozen.get("s1_source"), "s108_frozen_research_candidate", "S1 source")
    _expect(frozen.get("s2_source"), "s202_frozen_seed_ensemble", "S2 source")
    for field in (
        "s1_retraining_allowed",
        "s1_reselection_allowed",
        "s2_retraining_allowed",
        "s2_reselection_allowed",
        "result_based_threshold_change_allowed",
        "predictions_allowed_as_training_features",
        "predictions_allowed_as_model_features",
        "predictions_allowed_as_selection_inputs",
        "locked_test_row_or_prediction_reuse_allowed",
    ):
        _expect(frozen.get(field), False, f"frozen input {field}")
    _expect(
        frozen.get("predictions_allowed_for_evaluation_join_only"),
        True,
        "evaluation-only prediction join",
    )
    _expect(
        tuple(frozen.get("evaluation_join_fields", [])),
        EVALUATION_JOIN_FIELDS,
        "evaluation join fields",
    )
    _expect(frozen.get("s1_prediction_path"), TBD, "S1 prediction path")
    _expect(frozen.get("s2_prediction_path"), TBD, "S2 prediction path")


def _validate_holdout_and_d07(config: Mapping[str, Any]) -> None:
    holdout = _mapping(config["expert_holdout"], "expert holdout")
    _expect_keys(
        holdout,
        {
            "owner",
            "split_name",
            "preregistration_path",
            "attestation_path",
            "data_manifest_path",
            "maximum_access_count",
            "current_access_count",
            "access_family_definition",
            "one_shot_transaction_protocol",
            "minimum_item_count",
            "maximum_item_count",
            "independent_rater_count",
            "adjudicator_count_documented",
            "adjudication_protocol",
            "require_item_disjoint",
            "require_speaker_disjoint",
            "require_prompt_disjoint",
            "require_rater_disjoint",
            "item_overlap_attestation_status",
            "speaker_overlap_attestation_status",
            "prompt_overlap_attestation_status",
            "rater_overlap_attestation_status",
            "all_overlap_counts_required",
            "forbidden_reuse",
        },
        "expert holdout",
    )
    _expect(holdout.get("owner"), "s203_only", "holdout owner")
    _expect(holdout.get("split_name"), "s2_expert_holdout", "holdout split")
    _relative_string(holdout.get("attestation_path"), "attestation path")
    _expect(holdout.get("data_manifest_path"), TBD, "holdout data manifest")
    _relative_string(
        holdout.get("preregistration_path"),
        "preregistration path",
    )
    _expect(holdout.get("maximum_access_count"), 1, "holdout maximum access")
    _expect(holdout.get("current_access_count"), 0, "holdout current access")
    _expect(holdout.get("access_family_definition"), TBD, "access family")
    _expect(holdout.get("one_shot_transaction_protocol"), TBD, "transaction")
    _expect(holdout.get("minimum_item_count"), 1000, "minimum item count")
    _expect(holdout.get("maximum_item_count"), 2000, "maximum item count")
    _expect(holdout.get("independent_rater_count"), 2, "rater count")
    _expect(holdout.get("adjudicator_count_documented"), 1, "adjudicator count")
    _expect(holdout.get("adjudication_protocol"), TBD, "adjudication protocol")
    for dimension in ("item", "speaker", "prompt", "rater"):
        _expect(
            holdout.get(f"require_{dimension}_disjoint"),
            True,
            f"holdout {dimension} disjoint",
        )
        _expect(
            holdout.get(f"{dimension}_overlap_attestation_status"),
            TBD,
            f"holdout {dimension} attestation",
        )
    _expect(holdout.get("all_overlap_counts_required"), 0, "overlap count")
    forbidden = set(_list(holdout.get("forbidden_reuse"), "forbidden reuse"))
    required = {
        "s1_train",
        "s1_dev",
        "s1_stack",
        "s1_calibration",
        "s1_locked_test",
        "s2_auditor_train",
        "s2_auditor_validation",
        "d07_s1_gold",
    }
    _expect(forbidden, required, "holdout forbidden reuse")
    d07 = _mapping(config["d07_separation"], "D07 separation")
    _expect_keys(
        d07,
        {
            "d07_role",
            "s203_role",
            "d07_may_satisfy_s203_attestation",
            "d07_rows_may_be_reused",
            "d07_labels_may_be_reused",
            "d07_raters_may_be_reused",
            "d07_predictions_may_be_joined",
            "cross_cohort_overlap_attestation_status",
        },
        "D07 separation",
    )
    _expect(d07.get("d07_role"), "s1_gold_and_alignment_audit", "D07 role")
    _expect(d07.get("s203_role"), "distinct_s2_expert_holdout", "S203 role")
    for field in (
        "d07_may_satisfy_s203_attestation",
        "d07_rows_may_be_reused",
        "d07_labels_may_be_reused",
        "d07_raters_may_be_reused",
        "d07_predictions_may_be_joined",
    ):
        _expect(d07.get(field), False, f"D07 separation {field}")
    _expect(d07.get("cross_cohort_overlap_attestation_status"), TBD, "D07 overlap")


def _validate_evaluation(config: Mapping[str, Any]) -> None:
    evaluation = _mapping(config["evaluation_contract"], "evaluation contract")
    _expect_keys(
        evaluation,
        {
            "documented_primary_gates",
            "additional_primary_metrics",
            "metamorphic_expectation_gate_scope",
            "documented_non_holdout_gate",
            "unresolved_preregistration",
            "all_primary_gates_required",
            "single_evaluation_family",
            "actual_metric_implementation_present",
        },
        "evaluation contract",
    )
    gates = _mapping(evaluation.get("documented_primary_gates"), "primary gates")
    _expect(set(gates), set(DOCUMENTED_GATES), "documented gate names")
    for name, (operator, value) in DOCUMENTED_GATES.items():
        metric = _mapping(gates[name], f"gate {name}")
        expected_metric_keys = {
            "status",
            "gate",
            "definition",
            "numerator",
            "denominator",
        }
        if name == "serious_s1_misjudgment_recall":
            expected_metric_keys.add("reporting_alias")
        _expect_keys(metric, expected_metric_keys, f"gate {name}")
        _expect(metric.get("status"), TBD, f"{name} status")
        if name == "serious_s1_misjudgment_recall":
            _expect(
                metric.get("reporting_alias"),
                "severe_false_negative_recall",
                "serious-misjudgment reporting alias",
            )
        gate = _mapping(metric.get("gate"), f"{name} gate")
        _expect_keys(gate, {"operator", "value"}, f"{name} gate")
        _expect(gate.get("operator"), operator, f"{name} operator")
        _expect(_number(gate.get("value"), f"{name} value"), value, f"{name} value")
        _expect(metric.get("definition"), TBD, f"{name} definition")
        _expect(metric.get("numerator"), TBD, f"{name} numerator")
        _expect(metric.get("denominator"), TBD, f"{name} denominator")
    _expect(evaluation.get("additional_primary_metrics"), TBD, "extra metrics")
    _expect(
        evaluation.get("metamorphic_expectation_gate_scope"),
        "phase3_non_holdout",
        "metamorphic gate scope",
    )
    metamorphic = _mapping(
        evaluation.get("documented_non_holdout_gate"),
        "non-holdout gate",
    )
    _expect_keys(
        metamorphic,
        {"metric", "operator", "value", "s203_holdout_primary_metric"},
        "non-holdout gate",
    )
    _expect(metamorphic.get("metric"), "metamorphic_expectation_pass_rate", "metric")
    _expect(metamorphic.get("operator"), "greater_than_or_equal", "operator")
    _expect(_number(metamorphic.get("value"), "metamorphic value"), 0.95, "value")
    _expect(metamorphic.get("s203_holdout_primary_metric"), False, "holdout role")
    unresolved = _mapping(
        evaluation.get("unresolved_preregistration"),
        "unresolved preregistration",
    )
    _expect(
        tuple(unresolved),
        UNRESOLVED_PREREGISTRATION_FIELDS,
        "unresolved field order",
    )
    for name in UNRESOLVED_PREREGISTRATION_FIELDS:
        _expect(unresolved.get(name), TBD, f"unresolved {name}")
    _expect(evaluation.get("all_primary_gates_required"), True, "all gates")
    _expect(evaluation.get("single_evaluation_family"), TBD, "one-shot family")
    _expect(evaluation.get("actual_metric_implementation_present"), False, "metrics")


def _validate_governance_and_outputs(config: Mapping[str, Any]) -> None:
    governance = _mapping(config["privacy_and_license"], "privacy and license")
    _expect_keys(
        governance,
        {
            "raw_audio_zone",
            "rater_mapping_zone",
            "pseudonymous_prediction_zone",
            "aggregate_report_zone",
            "public_row_level_release_allowed",
            "raw_identifier_export_allowed",
            "free_text_note_model_input_allowed",
            "holdout_redistribution_allowed",
            "locked_test_reuse_allowed",
            "expert_data_governance_attestation_status",
        },
        "privacy and license",
    )
    _expect(governance.get("raw_audio_zone"), "R0", "raw audio zone")
    _expect(governance.get("rater_mapping_zone"), "R1", "rater mapping zone")
    _expect(governance.get("pseudonymous_prediction_zone"), "D1", "prediction zone")
    _expect(governance.get("aggregate_report_zone"), "A1", "report zone")
    for field in (
        "public_row_level_release_allowed",
        "raw_identifier_export_allowed",
        "free_text_note_model_input_allowed",
        "holdout_redistribution_allowed",
        "locked_test_reuse_allowed",
    ):
        _expect(governance.get(field), False, f"governance {field}")
    _expect(
        governance.get("expert_data_governance_attestation_status"),
        TBD,
        "expert governance",
    )
    _expect(
        tuple(config["execution_order"]),
        EXPECTED_ORDER,
        "S203 execution order",
    )
    outputs = _mapping(config["outputs"], "outputs")
    _expect_keys(
        outputs,
        {
            "production_evaluator_in_scope",
            "current_output_count",
            "output_path_resolution_allowed",
            "atomic_publish_implemented",
            "completion_claim_allowed",
            "deployment_approval_created_by_s203",
            "future_atomic_publish_required",
            "future_no_overwrite_required",
            "future_no_symlink_required",
        },
        "outputs",
    )
    for field in (
        "production_evaluator_in_scope",
        "output_path_resolution_allowed",
        "atomic_publish_implemented",
        "completion_claim_allowed",
        "deployment_approval_created_by_s203",
    ):
        _expect(outputs.get(field), False, f"output {field}")
    _expect(outputs.get("current_output_count"), 0, "output count")
    _expect(outputs.get("future_atomic_publish_required"), True, "future atomic")
    _expect(outputs.get("future_no_overwrite_required"), True, "future no-overwrite")
    _expect(outputs.get("future_no_symlink_required"), True, "future no-symlink")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> StaticValidation:
    """Validate the S203 C1 contract without resolving runtime paths."""

    root = workspace_root.resolve(strict=True)
    if config_path.is_symlink():
        raise S203ContractError("S203 config may not be a symlink")
    _expect_keys(config, TOP_LEVEL_KEYS, "S203 top level")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema version")
    _validate_experiment(config)
    _validate_predecessors(config)
    _validate_frozen_inputs(config)
    _validate_holdout_and_d07(config)
    _validate_evaluation(config)
    _validate_governance_and_outputs(config)

    sources = _mapping(config["canonical_sources"], "canonical sources")
    _expect_keys(sources, {"access_scope", "pins"}, "canonical sources")
    _expect(sources.get("access_scope"), "C1_code_config_docs_only", "source scope")
    pins = _list(sources.get("pins"), "source pins")
    if not pins:
        raise S203ContractError("at least one canonical source pin is required")
    snapshots: list[FileSnapshot] = []
    seen: set[str] = set()
    for index, raw in enumerate(pins):
        pin = _mapping(raw, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        if relative in seen:
            raise S203ContractError(f"duplicate canonical source pin: {relative}")
        seen.add(relative)
        expected_hash = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        snapshot = _snapshot_file(root, relative, f"source pin {index}")
        _expect(snapshot.sha256, expected_hash, f"source pin {relative}")
        snapshots.append(snapshot)
    config_relative = config_path.resolve(strict=True).relative_to(root).as_posix()
    config_snapshot = _snapshot_file(root, config_relative, "S203 config")
    policy = {
        name: config[name]
        for name in (
            "predecessors",
            "frozen_evaluation_inputs",
            "expert_holdout",
            "d07_separation",
            "evaluation_contract",
            "privacy_and_license",
            "execution_order",
            "outputs",
        )
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        source_snapshots=tuple(snapshots),
    )


def _copy_s202_audit(source: s202.AccessAudit, target: AccessAudit) -> None:
    """Copy only common, no-access-relevant counters from the S202 validator."""

    target.predecessor_metadata_read_attempt_count += (
        source.predecessor_metadata_read_attempt_count
    )
    target.predecessor_metadata_read_count += source.predecessor_metadata_read_count
    target.predecessor_artifact_read_count += source.predecessor_artifact_read_count
    target.s108_validation_count = max(
        target.s108_validation_count,
        source.s108_validation_count,
    )
    target.s200_validation_count = max(
        target.s200_validation_count,
        source.s200_validation_count,
    )
    target.s201_validation_count = max(
        target.s201_validation_count,
        source.s201_validation_count,
    )
    target.data_path_resolution_count += source.data_path_resolution_count
    target.data_manifest_read_count += source.data_manifest_read_count
    target.data_row_read_count += source.data_row_read_count
    target.audio_read_count += source.audio_read_count
    target.model_path_resolution_count += source.model_path_resolution_count
    target.model_weight_read_count += source.model_weight_read_count
    target.protected_data_read_count += source.protected_data_read_count
    target.expert_holdout_read_count += source.expert_holdout_read_count
    target.s1_prediction_read_count += source.s1_prediction_read_count
    target.locked_test_read_count += source.locked_test_read_count
    target.gpu_access_count += source.gpu_access_count
    target.metric_count += source.metric_count
    target.result_count += source.result_count
    target.output_path_resolution_count += source.output_path_resolution_count
    target.output_count += source.output_count


def validate_predecessor_chain(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> PredecessorEvidence:
    """Public-validate S202, then deep-validate S108 -> S200 -> S201."""

    predecessor = _mapping(config["predecessors"], "predecessors")
    s202_contract = _mapping(predecessor["s202_auditor"], "S202 predecessor")
    config_relative = _relative_string(
        s202_contract["config_path"],
        "S202 config path",
    )
    s202_config = s202.load_config(root / config_relative)
    try:
        s202_static = s202.validate_static_config(
            workspace_root=root,
            config_path=root / config_relative,
            config=s202_config,
        )
    except s202.S202ContractError as exc:
        raise S203ContractError(
            f"S202 public static validation failed: {exc}",
            stage="predecessor_s202_public",
            audit=audit,
        ) from exc
    audit.predecessor_public_source_read_count += len(s202_static.source_snapshots)
    audit.predecessor_public_validation_count = 1

    local_s108 = _mapping(predecessor["s108_final"], "S203 S108")
    local_s200 = _mapping(predecessor["s200_readiness"], "S203 S200")
    local_s201 = _mapping(predecessor["s201_content_gate"], "S203 S201")
    s202_predecessors = _mapping(s202_config["predecessors"], "S202 predecessors")
    pinned_s108 = _mapping(s202_predecessors["s108_final"], "S202 S108")
    pinned_s200 = _mapping(s202_predecessors["s200_readiness"], "S202 S200")
    pinned_s201 = _mapping(s202_predecessors["s201_content_gate"], "S202 S201")
    for field in ("commit_path", "registry_path"):
        _expect(
            local_s108.get(field), pinned_s108.get(field), f"S108 {field} agreement"
        )
    for field in ("config_path", "lock_path"):
        _expect(
            local_s200.get(field), pinned_s200.get(field), f"S200 {field} agreement"
        )
    for field in ("config_path", "commit_path", "registry_path"):
        _expect(
            local_s201.get(field), pinned_s201.get(field), f"S201 {field} agreement"
        )

    inner_audit = s202.AccessAudit()
    try:
        s108 = s202.deep_validate_s108_final(
            root=root,
            config=s202_config,
            audit=inner_audit,
        )
        s200 = s202.deep_validate_s200_readiness(
            root=root,
            s108=s108,
            audit=inner_audit,
        )
        s201 = s202.deep_validate_s201_completed(
            root=root,
            config=s202_config,
            s108=s108,
            s200=s200,
            audit=inner_audit,
        )
    except s202.S202ContractError as exc:
        _copy_s202_audit(inner_audit, audit)
        stage = exc.stage
        raise S203ContractError(
            f"predecessor deep validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _copy_s202_audit(inner_audit, audit)
    return PredecessorEvidence(
        s202_config=s202_config,
        s202_static=s202_static,
        s108=s108,
        s200=s200,
        s201=s201,
    )


def _validate_s202_aggregate_public(
    *,
    commit: Mapping[str, Any],
    config: Mapping[str, Any],
    predecessors: PredecessorEvidence,
    seed_snapshots: Mapping[int, FileSnapshot] | None = None,
) -> None:
    _expect_keys(
        commit,
        {
            "schema_version",
            "status",
            "decision",
            "production_trainer_implemented",
            "training_completed",
            "seed_count",
            "seeds",
            "expert_holdout_access_count",
            "s1_prediction_feature_count",
            "s1_label_feature_count",
            "s1_alignment_feature_count",
            "whisper_asr_score_feature_count",
            "locked_test_access_count",
            "result_based_reselection_count",
            "preregistration_sha256",
            "expert_label_attestation_sha256",
            "split_attestation_sha256",
            "model_bundle_attestation_sha256",
            "predecessors",
            "artifact_hashes",
            "artifact_paths",
            "seed_commits",
            "binding_sha256",
        },
        "S202 aggregate commit",
    )
    _expect(commit.get("schema_version"), S202_AGGREGATE_SCHEMA, "S202 schema")
    _expect(commit.get("status"), "committed", "S202 status")
    _expect(commit.get("decision"), "independent_auditor_frozen", "S202 decision")
    _expect(commit.get("production_trainer_implemented"), True, "S202 trainer")
    _expect(commit.get("training_completed"), True, "S202 training")
    _expect(commit.get("seed_count"), 3, "S202 seed count")
    _expect(commit.get("seeds"), [42, 43, 44], "S202 seeds")
    for field in (
        "expert_holdout_access_count",
        "s1_prediction_feature_count",
        "s1_label_feature_count",
        "s1_alignment_feature_count",
        "whisper_asr_score_feature_count",
        "locked_test_access_count",
        "result_based_reselection_count",
    ):
        _expect(commit.get(field), 0, f"S202 {field}")
    _sha256(commit.get("preregistration_sha256"), "S202 preregistration SHA")
    _sha256(commit.get("expert_label_attestation_sha256"), "S202 label SHA")
    _sha256(commit.get("split_attestation_sha256"), "S202 split SHA")
    _sha256(commit.get("model_bundle_attestation_sha256"), "S202 model SHA")
    bindings = _mapping(commit.get("predecessors"), "S202 predecessor bindings")
    _expect_keys(
        bindings,
        {
            "s108_commit_sha256",
            "s200_readiness_lock_sha256",
            "s201_commit_sha256",
            "s202_config_sha256",
        },
        "S202 predecessor bindings",
    )
    _expect(
        bindings.get("s108_commit_sha256"),
        predecessors.s108.inner.commit_snapshot.sha256,
        "S202 S108 binding",
    )
    _expect(
        bindings.get("s200_readiness_lock_sha256"),
        predecessors.s200.inner.lock_snapshot.sha256,
        "S202 S200 binding",
    )
    _expect(
        bindings.get("s201_commit_sha256"),
        predecessors.s201.commit_snapshot.sha256,
        "S202 S201 binding",
    )
    _expect(
        bindings.get("s202_config_sha256"),
        predecessors.s202_static.config_sha256,
        "S202 config binding",
    )
    artifact_hashes = _mapping(commit.get("artifact_hashes"), "S202 artifacts")
    artifact_paths = _mapping(commit.get("artifact_paths"), "S202 artifact paths")
    _expect(set(artifact_hashes), set(S202_AGGREGATE_ARTIFACTS), "S202 artifacts")
    _expect(set(artifact_paths), set(S202_AGGREGATE_ARTIFACTS), "S202 paths")
    for name in S202_AGGREGATE_ARTIFACTS:
        _sha256(artifact_hashes[name], f"S202 {name} SHA")
        _relative_string(artifact_paths[name], f"S202 {name} path")
    seeds = _mapping(commit.get("seed_commits"), "S202 seed commits")
    _expect(set(seeds), {"42", "43", "44"}, "S202 seed commit keys")
    for seed in (42, 43, 44):
        binding = _mapping(seeds[str(seed)], f"S202 seed {seed} binding")
        _expect_keys(binding, {"path", "sha256"}, f"S202 seed {seed} binding")
        _relative_string(binding.get("path"), f"S202 seed {seed} path")
        expected_hash = _sha256(binding.get("sha256"), f"S202 seed {seed} SHA")
        if seed_snapshots is not None:
            _expect(
                expected_hash, seed_snapshots[seed].sha256, f"S202 seed {seed} hash"
            )
    observed_binding = _sha256(commit.get("binding_sha256"), "S202 binding")
    _expect(observed_binding, _binding_sha256(commit), "S202 binding")


def _validate_s202_seed_public(
    *,
    commit: Mapping[str, Any],
    seed: int,
    config: Mapping[str, Any],
    predecessors: PredecessorEvidence,
) -> None:
    _expect_keys(
        commit,
        {
            "schema_version",
            "status",
            "decision",
            "seed",
            "production_trainer_implemented",
            "expert_holdout_access_count",
            "s1_prediction_feature_count",
            "s1_label_feature_count",
            "s1_alignment_feature_count",
            "whisper_asr_score_feature_count",
            "locked_test_access_count",
            "result_based_reselection_count",
            "predecessors",
            "artifact_hashes",
            "registry",
            "binding_sha256",
        },
        f"S202 seed {seed} commit",
    )
    _expect(commit.get("schema_version"), S202_SEED_SCHEMA, f"seed {seed} schema")
    _expect(commit.get("status"), "committed", f"seed {seed} status")
    _expect(commit.get("decision"), "seed_model_frozen", f"seed {seed} decision")
    _expect(commit.get("seed"), seed, f"seed {seed}")
    _expect(commit.get("production_trainer_implemented"), True, f"seed {seed} trainer")
    for field in (
        "expert_holdout_access_count",
        "s1_prediction_feature_count",
        "s1_label_feature_count",
        "s1_alignment_feature_count",
        "whisper_asr_score_feature_count",
        "locked_test_access_count",
        "result_based_reselection_count",
    ):
        _expect(commit.get(field), 0, f"seed {seed} {field}")
    bindings = _mapping(commit.get("predecessors"), f"seed {seed} predecessors")
    _expect_keys(
        bindings,
        {
            "s108_commit_sha256",
            "s200_readiness_lock_sha256",
            "s201_commit_sha256",
            "s202_config_sha256",
            "s202_preregistration_sha256",
        },
        f"seed {seed} predecessors",
    )
    _expect(
        bindings.get("s108_commit_sha256"),
        predecessors.s108.inner.commit_snapshot.sha256,
        f"seed {seed} S108 binding",
    )
    _expect(
        bindings.get("s200_readiness_lock_sha256"),
        predecessors.s200.inner.lock_snapshot.sha256,
        f"seed {seed} S200 binding",
    )
    _expect(
        bindings.get("s201_commit_sha256"),
        predecessors.s201.commit_snapshot.sha256,
        f"seed {seed} S201 binding",
    )
    _expect(
        bindings.get("s202_config_sha256"),
        predecessors.s202_static.config_sha256,
        f"seed {seed} config binding",
    )
    _sha256(bindings.get("s202_preregistration_sha256"), f"seed {seed} prereg")
    artifact_hashes = _mapping(commit.get("artifact_hashes"), f"seed {seed} artifacts")
    _expect(set(artifact_hashes), set(S202_SEED_ARTIFACTS), f"seed {seed} artifacts")
    for name in S202_SEED_ARTIFACTS:
        _sha256(artifact_hashes[name], f"seed {seed} {name} SHA")
    registry = _mapping(commit.get("registry"), f"seed {seed} registry")
    _expect_keys(
        registry,
        {"path", "sha256", "run_id", "status"},
        f"seed {seed} registry binding",
    )
    expected_contracts = _mapping(
        _mapping(config["predecessors"], "predecessors")["s202_auditor"],
        "S202 contract",
    )
    entries = {
        _integer(_mapping(item, "seed entry").get("seed"), "seed entry"): _mapping(
            item,
            "seed entry",
        )
        for item in _list(expected_contracts["seeds"], "S202 seeds")
    }
    expected = entries[seed]
    _expect(registry.get("path"), expected.get("registry_path"), f"seed {seed} path")
    _expect(
        registry.get("run_id"),
        expected.get("registry_run_id"),
        f"seed {seed} run id",
    )
    _expect(registry.get("status"), "completed", f"seed {seed} registry status")
    _sha256(registry.get("sha256"), f"seed {seed} registry SHA")
    observed_binding = _sha256(commit.get("binding_sha256"), f"seed {seed} binding")
    _expect(observed_binding, _binding_sha256(commit), f"seed {seed} binding")


def validate_s202_completed_public(
    *,
    root: Path,
    config: Mapping[str, Any],
    predecessors: PredecessorEvidence,
    audit: AccessAudit,
) -> S202PublicEvidence:
    """Validate S202 commit metadata without opening registered artifacts."""

    contract = _mapping(
        _mapping(config["predecessors"], "predecessors")["s202_auditor"],
        "S202 predecessor",
    )
    aggregate_path = _relative_string(
        contract["aggregate_commit_path"],
        "S202 aggregate commit",
    )
    aggregate, aggregate_snapshot = _read_json_metadata(
        root=root,
        relative=aggregate_path,
        name="S202 aggregate commit",
        audit=audit,
    )
    seed_values: list[Mapping[str, Any]] = []
    seed_snapshots: list[FileSnapshot] = []
    snapshots_by_seed: dict[int, FileSnapshot] = {}
    for raw in _list(contract["seeds"], "S202 seed contracts"):
        seed_contract = _mapping(raw, "S202 seed contract")
        seed = _integer(seed_contract["seed"], "S202 seed")
        relative = _relative_string(
            seed_contract["commit_path"],
            f"S202 seed {seed} commit",
        )
        value, snapshot = _read_json_metadata(
            root=root,
            relative=relative,
            name=f"S202 seed {seed} commit",
            audit=audit,
        )
        _validate_s202_seed_public(
            commit=value,
            seed=seed,
            config=config,
            predecessors=predecessors,
        )
        seed_values.append(value)
        seed_snapshots.append(snapshot)
        snapshots_by_seed[seed] = snapshot
    _validate_s202_aggregate_public(
        commit=aggregate,
        config=config,
        predecessors=predecessors,
        seed_snapshots=snapshots_by_seed,
    )
    audit.s202_public_validation_count = 1
    return S202PublicEvidence(
        aggregate_commit=aggregate,
        aggregate_snapshot=aggregate_snapshot,
        seed_commits=tuple(seed_values),
        seed_snapshots=tuple(seed_snapshots),
    )


def _registry_artifacts(
    *,
    root: Path,
    registry: Mapping[str, Any],
    expected_names: tuple[str, ...],
    expected_hashes: Mapping[str, Any],
    artifact_root: str,
    audit: AccessAudit,
    name: str,
) -> tuple[FileSnapshot, ...]:
    artifacts = _list(registry.get("artifacts"), f"{name} artifacts")
    by_name: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(artifacts):
        artifact = _mapping(raw, f"{name} artifact {index}")
        artifact_name = _string(artifact.get("name"), f"{name} artifact name")
        if artifact_name in by_name:
            raise S203ContractError(f"duplicate {name} artifact: {artifact_name}")
        by_name[artifact_name] = artifact
    _expect(set(by_name), set(expected_names), f"{name} artifact set")
    snapshots: list[FileSnapshot] = []
    for artifact_name in expected_names:
        artifact = by_name[artifact_name]
        relative = _relative_string(artifact.get("path"), f"{name} {artifact_name}")
        if not relative.startswith(artifact_root + "/"):
            raise S203ContractError(f"{name} artifact escapes S202 root")
        expected_hash = _sha256(
            artifact.get("sha256"),
            f"{name} {artifact_name} SHA",
        )
        _expect(
            expected_hash,
            _sha256(expected_hashes[artifact_name], f"{name} commit artifact SHA"),
            f"{name} {artifact_name} commit hash",
        )
        expected_size = _integer(
            artifact.get("size_bytes"),
            f"{name} {artifact_name} size",
            minimum=1,
        )
        snapshot = _snapshot_file(root, relative, f"{name} {artifact_name}")
        audit.predecessor_artifact_read_count += 1
        _expect(snapshot.sha256, expected_hash, f"{name} {artifact_name} hash")
        _expect(snapshot.size_bytes, expected_size, f"{name} {artifact_name} size")
        snapshots.append(snapshot)
    return tuple(snapshots)


def deep_validate_s202_completed(
    *,
    root: Path,
    config: Mapping[str, Any],
    public: S202PublicEvidence,
    audit: AccessAudit,
) -> S202DeepEvidence:
    """Deep-validate all S202 registries and registered artifacts."""

    contract = _mapping(
        _mapping(config["predecessors"], "predecessors")["s202_auditor"],
        "S202 predecessor",
    )
    artifact_root = _relative_string(contract["artifact_root"], "S202 artifact root")
    registry_snapshots: list[FileSnapshot] = []
    artifact_snapshots: list[FileSnapshot] = []
    seed_contracts = {
        _integer(_mapping(raw, "seed contract")["seed"], "seed"): _mapping(
            raw,
            "seed contract",
        )
        for raw in _list(contract["seeds"], "S202 seed contracts")
    }
    for commit in public.seed_commits:
        seed = _integer(commit["seed"], "S202 seed")
        registry_binding = _mapping(commit["registry"], f"seed {seed} registry")
        registry_path = _relative_string(
            registry_binding["path"],
            f"seed {seed} registry path",
        )
        _expect(
            registry_path,
            seed_contracts[seed]["registry_path"],
            f"seed {seed} registry path",
        )
        registry, snapshot = _read_json_metadata(
            root=root,
            relative=registry_path,
            name=f"S202 seed {seed} registry",
            audit=audit,
        )
        _expect(
            snapshot.sha256,
            _sha256(registry_binding["sha256"], f"seed {seed} registry SHA"),
            f"seed {seed} registry hash",
        )
        _expect(registry.get("schema_version"), 1, f"seed {seed} registry schema")
        _expect(registry.get("run_id"), registry_binding["run_id"], f"seed {seed} run")
        _expect(registry.get("status"), "completed", f"seed {seed} completed")
        _expect(registry.get("failure"), None, f"seed {seed} failure")
        registry_snapshots.append(snapshot)
        artifact_snapshots.extend(
            _registry_artifacts(
                root=root,
                registry=registry,
                expected_names=S202_SEED_ARTIFACTS,
                expected_hashes=_mapping(
                    commit["artifact_hashes"],
                    f"seed {seed} artifact hashes",
                ),
                artifact_root=artifact_root,
                audit=audit,
                name=f"S202 seed {seed}",
            )
        )
    aggregate_hashes = _mapping(
        public.aggregate_commit["artifact_hashes"],
        "S202 aggregate hashes",
    )
    aggregate_paths = _mapping(
        public.aggregate_commit["artifact_paths"],
        "S202 aggregate paths",
    )
    for name in S202_AGGREGATE_ARTIFACTS:
        relative = _relative_string(aggregate_paths[name], f"S202 aggregate {name}")
        if not relative.startswith(artifact_root + "/"):
            raise S203ContractError(f"S202 aggregate {name} escapes artifact root")
        snapshot = _snapshot_file(root, relative, f"S202 aggregate {name}")
        audit.predecessor_artifact_read_count += 1
        _expect(
            snapshot.sha256,
            _sha256(aggregate_hashes[name], f"S202 aggregate {name} SHA"),
            f"S202 aggregate {name} hash",
        )
        artifact_snapshots.append(snapshot)
    audit.s202_deep_validation_count = 1
    return S202DeepEvidence(
        public=public,
        registry_snapshots=tuple(registry_snapshots),
        artifact_snapshots=tuple(artifact_snapshots),
    )


def _read_preregistration(
    *,
    root: Path,
    config: Mapping[str, Any],
    predecessors: PredecessorEvidence,
    s202_deep: S202DeepEvidence,
    audit: AccessAudit,
) -> PreregistrationEvidence:
    holdout = _mapping(config["expert_holdout"], "expert holdout")
    relative = _relative_string(
        holdout["preregistration_path"],
        "S203 preregistration path",
    )
    audit.preregistration_read_attempt_count += 1
    try:
        data, snapshot = _read_file(root, relative, "S203 preregistration")
    except S203ContractError as exc:
        raise S203ContractError(
            f"S203 preregistration unavailable: {exc}",
            stage="preregistration",
            audit=audit,
        ) from exc
    value = _parse_json(data, "S203 preregistration")
    audit.preregistration_read_count += 1
    _expect(value.get("schema_version"), S203_PREREGISTRATION_SCHEMA, "prereg schema")
    _expect(value.get("status"), "frozen", "prereg status")
    _expect(value.get("unresolved_field_count"), 0, "prereg unresolved count")
    _expect(value.get("holdout_access_count_at_freeze"), 0, "prereg holdout access")
    bindings = _mapping(value.get("predecessors"), "prereg predecessors")
    _expect(
        bindings.get("s108_commit_sha256"),
        predecessors.s108.inner.commit_snapshot.sha256,
        "prereg S108 binding",
    )
    _expect(
        bindings.get("s200_readiness_lock_sha256"),
        predecessors.s200.inner.lock_snapshot.sha256,
        "prereg S200 binding",
    )
    _expect(
        bindings.get("s201_commit_sha256"),
        predecessors.s201.commit_snapshot.sha256,
        "prereg S201 binding",
    )
    _expect(
        bindings.get("s202_commit_sha256"),
        s202_deep.public.aggregate_snapshot.sha256,
        "prereg S202 binding",
    )
    gates = _mapping(value.get("documented_primary_gates"), "prereg gates")
    _expect(set(gates), set(DOCUMENTED_GATES), "prereg gate names")
    for name, (operator, threshold) in DOCUMENTED_GATES.items():
        gate = _mapping(gates[name], f"prereg {name}")
        _expect(gate.get("operator"), operator, f"prereg {name} operator")
        _expect(_number(gate.get("value"), f"prereg {name} value"), threshold, name)
    for field in UNRESOLVED_PREREGISTRATION_FIELDS:
        resolved = value.get(field)
        if resolved in (None, "", TBD):
            raise S203ContractError(
                f"preregistration field remains unresolved: {field}",
                stage="preregistration",
                audit=audit,
            )
    _expect(value.get("maximum_holdout_access_count"), 1, "prereg max access")
    _expect(value.get("result_based_reselection_allowed"), False, "prereg reselection")
    _expect(value.get("model_retraining_after_open_allowed"), False, "prereg retrain")
    observed_binding = _sha256(value.get("binding_sha256"), "prereg binding")
    _expect(observed_binding, _binding_sha256(value), "prereg binding")
    return PreregistrationEvidence(value=value, snapshot=snapshot)


def _validate_attestation(
    *,
    root: Path,
    config: Mapping[str, Any],
    preregistration: PreregistrationEvidence,
    audit: AccessAudit,
) -> FileSnapshot:
    holdout = _mapping(config["expert_holdout"], "expert holdout")
    relative = _relative_string(holdout["attestation_path"], "S203 attestation path")
    audit.expert_attestation_read_attempt_count += 1
    try:
        data, snapshot = _read_file(root, relative, "S203 holdout attestation")
    except S203ContractError as exc:
        raise S203ContractError(
            f"S203 holdout attestation unavailable: {exc}",
            stage="expert_attestation",
            audit=audit,
        ) from exc
    value = _parse_json(data, "S203 holdout attestation")
    audit.expert_attestation_read_count += 1
    _expect(value.get("schema_version"), S203_ATTESTATION_SCHEMA, "attestation schema")
    _expect(value.get("status"), "ready", "attestation status")
    count = _integer(value.get("item_count"), "attestation item count", minimum=1000)
    if count > 2000:
        raise S203ContractError("attestation item count must be <= 2000")
    _expect(value.get("independent_rater_count"), 2, "attestation raters")
    _expect(value.get("adjudicator_count"), 1, "attestation adjudicator")
    for dimension in ("item", "speaker", "prompt", "rater"):
        _expect(value.get(f"{dimension}_overlap_count"), 0, f"{dimension} overlap")
    for field in (
        "s1_locked_test_overlap_count",
        "d07_item_overlap_count",
        "d07_rater_overlap_count",
        "s2_train_overlap_count",
        "s2_validation_overlap_count",
    ):
        _expect(value.get(field), 0, f"attestation {field}")
    _expect(value.get("prior_holdout_access_count"), 0, "prior holdout access")
    _expect(
        value.get("preregistration_sha256"),
        preregistration.snapshot.sha256,
        "attestation prereg binding",
    )
    _sha256(value.get("governance_attestation_sha256"), "governance SHA")
    _sha256(value.get("disjointness_ledger_sha256"), "disjointness SHA")
    observed_binding = _sha256(value.get("binding_sha256"), "attestation binding")
    _expect(observed_binding, _binding_sha256(value), "attestation binding")
    return snapshot


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    before_final_verify_hook: Callable[[], None] | None = None,
) -> None:
    """Validate all available evidence, then stop before any holdout data access."""

    root = workspace_root.resolve(strict=True)
    audit = AccessAudit()
    try:
        static = validate_static_config(
            workspace_root=root,
            config_path=config_path,
            config=config,
        )
        audit.source_pin_read_count = len(static.source_snapshots)
        predecessors = validate_predecessor_chain(
            root=root,
            config=config,
            audit=audit,
        )
        public = validate_s202_completed_public(
            root=root,
            config=config,
            predecessors=predecessors,
            audit=audit,
        )
        deep = deep_validate_s202_completed(
            root=root,
            config=config,
            public=public,
            audit=audit,
        )
        if before_final_verify_hook is not None:
            before_final_verify_hook()
        for source_snapshot in static.source_snapshots:
            _verify_snapshot(
                root,
                source_snapshot,
                f"preflight input {source_snapshot.path}",
            )
            audit.source_pin_read_count += 1
        for metadata_snapshot in (
            predecessors.s108.inner.commit_snapshot,
            predecessors.s108.inner.registry_snapshot,
            predecessors.s200.inner.lock_snapshot,
            predecessors.s201.commit_snapshot,
            predecessors.s201.registry_snapshot,
            public.aggregate_snapshot,
            *public.seed_snapshots,
            *deep.registry_snapshots,
        ):
            _verify_snapshot(
                root,
                metadata_snapshot,
                f"preflight input {getattr(metadata_snapshot, 'path')}",
            )
            audit.predecessor_metadata_read_count += 1
        for artifact_snapshot in (
            *predecessors.s108.inner.artifact_snapshots,
            *predecessors.s201.artifact_snapshots,
            *deep.artifact_snapshots,
        ):
            _verify_snapshot(
                root,
                artifact_snapshot,
                f"preflight input {getattr(artifact_snapshot, 'path')}",
            )
            audit.predecessor_artifact_read_count += 1
        preregistration = _read_preregistration(
            root=root,
            config=config,
            predecessors=predecessors,
            s202_deep=deep,
            audit=audit,
        )
        _validate_attestation(
            root=root,
            config=config,
            preregistration=preregistration,
            audit=audit,
        )
        raise S203ContractError(
            "S203 production evaluator is intentionally not implemented; "
            "holdout data path remains unresolved",
            stage="evaluator_not_implemented",
            audit=audit,
        )
    except S203ContractError as exc:
        if exc.audit is None:
            exc.audit = audit
        raise
    except OSError as exc:
        raise S203ContractError(
            str(exc),
            stage="filesystem",
            audit=audit,
        ) from exc


def config_only_result(static: StaticValidation) -> Mapping[str, Any]:
    return {
        "status": "valid_config_only",
        "config_sha256": static.config_sha256,
        "policy_sha256": static.policy_sha256,
        **AccessAudit().as_dict(),
        "source_pin_read_count": len(static.source_snapshots),
        "production_evaluator_implemented": False,
        "holdout_access_allowed": False,
        "metric_generation_allowed": False,
        "completion_claim_allowed": False,
    }


def failure_result(exc: S203ContractError) -> Mapping[str, Any]:
    audit = exc.audit.as_dict() if exc.audit is not None else AccessAudit().as_dict()
    return {
        "status": "failed_closed",
        "stage": exc.stage,
        "error": str(exc),
        **audit,
        "production_evaluator_implemented": False,
        "holdout_access_allowed": False,
        "metric_generation_allowed": False,
        "completion_claim_allowed": False,
    }
