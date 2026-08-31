"""Fail-closed, aggregate-only WP7 R05 GRPO entry decision contract.

This module is intentionally read-only.  It does not download or open model
weights, run F5/StyleTTS/GRPO, execute a reward, use a GPU, estimate an
effect, read row-level participant/rating/audio data, or publish an artifact.

The security-relevant production order is fixed:

1. validate R05 C1/static pins, schema, and draft templates;
2. deep-validate the final S108 completion;
3. deep-validate the actual S203 expert-holdout completion;
4. deep-validate the actual R04 blind evaluation completion;
5. deep-validate H00 approval and the actual H04 pilot report;
6. validate an independently frozen R05 preregistration and authorization;
7. evaluate exactly five gates from A1 aggregate evidence only.

Every operational detail that is not fixed by the implementation plan remains
``TBD_PREREGISTRATION`` or ``TBD_EXTERNAL``.  The canonical configuration
therefore stops at the unresolved S108 completion hash before resolving any
predecessor, protected-data, model, or output path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
STATIC_STATUS = "production_static_preflight_only"
TBD_PREFIX = "TBD_"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPORT_ID_RE = re.compile(r"^r05_[0-9a-f]{32}$")

GATE_IDS = (
    "s1_phone_f1",
    "s1_utterance_icc",
    "s2_severe_error_recall",
    "multi_reference_blind_superiority",
    "actual_pilot_learning_effect_signal",
)
FIXED_GATE_RULES: Mapping[str, tuple[str, float | None]] = {
    "s1_phone_f1": ("greater_than_or_equal", 0.80),
    "s1_utterance_icc": ("greater_than_or_equal", 0.70),
    "s2_severe_error_recall": ("greater_than_or_equal", 0.80),
    "multi_reference_blind_superiority": ("preregistered_superiority", None),
    "actual_pilot_learning_effect_signal": ("preregistered_signal", None),
}

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "gate_contract",
    "predecessors",
    "external_evidence",
    "independent_preregistration",
    "human_authorization",
    "model_reward_license_safety",
    "privacy_and_decision",
    "execution_order",
    "outputs",
    "runtime",
}

SOURCE_PATHS = (
    "configs/reference/r05_grpo_decision_report_v1.schema.json",
    "configs/reference/r05_grpo_gate_preregistration_v1.template.json",
    "configs/reference/r05_grpo_human_authorization_v1.template.json",
    "src/voice_coach/references/r05_grpo_decision_contract.py",
    "scripts/prepare_r05_grpo_go_no_go.py",
    "configs/experiments/s2_s202_independent_auditor_v1.yaml",
    "src/voice_coach/training/s2_s202_auditor_contract.py",
    "configs/experiments/s2_s203_expert_holdout_audit_v1.yaml",
    "src/voice_coach/training/s2_s203_holdout_contract.py",
    "configs/experiments/learning_effect_power_v1.yaml",
    "scripts/check_project_constraints.py",
)

EXECUTION_ORDER = (
    "validate_r05_c1_config_sources_schema_and_templates",
    "deep_validate_s108_final_completion",
    "deep_validate_s203_actual_expert_holdout_completion",
    "deep_validate_r04_actual_blind_evaluation_completion",
    "deep_validate_h00_actual_human_research_approval",
    "deep_validate_h04_actual_pilot_statistical_report",
    "validate_independent_frozen_r05_go_no_go_preregistration",
    "validate_independent_r05_human_authorization",
    "evaluate_five_gates_from_a1_aggregate_evidence_only",
    "stop_before_any_grpo_download_model_gpu_reward_or_training_execution",
)

RUNTIME_COUNTER_FIELDS = (
    "predecessor_path_resolution_count",
    "predecessor_read_attempt_count",
    "predecessor_read_count",
    "external_evidence_path_resolution_count",
    "external_evidence_read_attempt_count",
    "external_evidence_read_count",
    "preregistration_path_resolution_count",
    "preregistration_read_attempt_count",
    "preregistration_read_count",
    "authorization_path_resolution_count",
    "authorization_read_attempt_count",
    "authorization_read_count",
    "aggregate_evidence_read_count",
    "row_level_data_read_count",
    "protected_data_read_count",
    "audio_read_count",
    "rating_read_count",
    "participant_record_read_count",
    "model_path_resolution_count",
    "model_weight_read_count",
    "model_download_count",
    "network_access_count",
    "gpu_access_count",
    "reward_execution_count",
    "grpo_training_count",
    "f5_or_styletts_execution_count",
    "metric_computation_count",
    "effect_estimation_count",
    "decision_evaluation_count",
    "output_path_resolution_count",
    "output_count",
    "publication_count",
)

FORBIDDEN_A1_FIELDS = {
    "row_id",
    "row_ids",
    "row_id_sha256",
    "item_id",
    "item_ids",
    "item_id_sha256",
    "participant_id",
    "participant_ids",
    "participant_id_sha256",
    "listener_id",
    "listener_ids",
    "listener_id_sha256",
    "speaker_id",
    "speaker_ids",
    "speaker_id_sha256",
    "candidate_id",
    "candidate_ids",
    "candidate_id_sha256",
    "name",
    "email",
    "phone",
    "address",
    "free_text",
    "free_text_note",
    "comment",
    "transcript",
    "target_text",
    "audio_path",
    "model_path",
    "raw_rating",
    "rating_rows",
    "participant_rows",
    "audio_rows",
}

REPORT_KEYS = {
    "schema_version",
    "report_id",
    "status",
    "privacy_zone",
    "contains_row_level_data",
    "contains_direct_identifiers",
    "contains_free_text",
    "training_or_download_executed",
    "config_sha256",
    "preregistration_sha256",
    "human_authorization_sha256",
    "evidence_bindings",
    "gate_results",
    "assurance_results",
    "overall_decision",
    "grpo_entry_authorized",
    "publication_allowed",
}
EVIDENCE_BINDING_KEYS = {
    "s108_commit_sha256",
    "s203_commit_sha256",
    "r04_commit_sha256",
    "h00_approval_sha256",
    "h04_report_sha256",
}
GATE_RESULT_KEYS = {
    "gate_id",
    "evidence_sha256",
    "metric_or_estimand_id",
    "population_id",
    "alignment_id",
    "confidence_interval_method",
    "decision_rule_id",
    "operator",
    "threshold",
    "point_estimate",
    "confidence_interval_lower",
    "confidence_interval_upper",
    "superiority_margin",
    "effect_size",
    "decision",
}
ASSURANCE_KEYS = {
    "exact_model_identity_verified",
    "code_license_verified",
    "weight_license_verified",
    "training_data_license_verified",
    "reward_directionality_verified",
    "reward_hacking_audit_passed",
    "safety_review_passed",
    "human_authorization_valid",
}


class R05ContractError(ValueError):
    """A fail-closed R05 contract violation with stage and access evidence."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "static",
        audit: AccessAudit | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.audit = audit


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> Mapping[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise R05ContractError("YAML mapping keys must be strings")
        if key in result:
            raise R05ContractError(f"YAML contains duplicate key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass
class AccessAudit:
    source_pin_read_count: int = 0
    source_pin_verify_count: int = 0
    s108_deep_validation_count: int = 0
    s203_deep_validation_count: int = 0
    r04_deep_validation_count: int = 0
    h00_deep_validation_count: int = 0
    h04_deep_validation_count: int = 0
    predecessor_path_resolution_count: int = 0
    predecessor_read_attempt_count: int = 0
    predecessor_read_count: int = 0
    external_evidence_path_resolution_count: int = 0
    external_evidence_read_attempt_count: int = 0
    external_evidence_read_count: int = 0
    preregistration_path_resolution_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    authorization_path_resolution_count: int = 0
    authorization_read_attempt_count: int = 0
    authorization_read_count: int = 0
    aggregate_evidence_read_count: int = 0
    row_level_data_read_count: int = 0
    protected_data_read_count: int = 0
    audio_read_count: int = 0
    rating_read_count: int = 0
    participant_record_read_count: int = 0
    model_path_resolution_count: int = 0
    model_weight_read_count: int = 0
    model_download_count: int = 0
    network_access_count: int = 0
    gpu_access_count: int = 0
    reward_execution_count: int = 0
    grpo_training_count: int = 0
    f5_or_styletts_execution_count: int = 0
    metric_computation_count: int = 0
    effect_estimation_count: int = 0
    decision_evaluation_count: int = 0
    output_path_resolution_count: int = 0
    output_count: int = 0
    publication_count: int = 0


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    sha256: str
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class StaticValidation:
    config_sha256: str
    policy_sha256: str
    source_snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True)
class AggregateCompletionEvidence:
    stage: str
    commit_snapshot: FileSnapshot
    registry_snapshot: FileSnapshot
    report_snapshot: FileSnapshot
    report: Mapping[str, Any]


@dataclass(frozen=True)
class ExternalEvidence:
    stage: str
    snapshot: FileSnapshot
    document: Mapping[str, Any]


@dataclass(frozen=True)
class PreregistrationEvidence:
    snapshot: FileSnapshot
    document: Mapping[str, Any]


@dataclass(frozen=True)
class AuthorizationEvidence:
    snapshot: FileSnapshot
    document: Mapping[str, Any]


@dataclass(frozen=True)
class PreflightResult:
    status: str
    config_sha256: str
    decision: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise R05ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise R05ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise R05ContractError(f"{name} must be a non-empty string")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise R05ContractError(f"{name} must be boolean")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise R05ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise R05ContractError(f"{name} must be at least {minimum}")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R05ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise R05ContractError(f"{name} must be finite")
    return result


def _sha256(value: object, name: str) -> str:
    result = _string(value, name)
    if not SHA256_RE.fullmatch(result) or result == "0" * 64:
        raise R05ContractError(f"{name} must be a nonzero lowercase SHA-256")
    return result


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise R05ContractError(f"{name} must equal {expected!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise R05ContractError(f"{name} keys differ; missing={missing}, extra={extra}")


def _contains_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith(TBD_PREFIX)
    if isinstance(value, Mapping):
        return any(_contains_tbd(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_tbd(item) for item in value)
    return False


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise R05ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_nonfinite_tree(item, f"{name}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_nonfinite_tree(item, f"{name}[{index}]")


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise R05ContractError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise R05ContractError(f"JSON contains forbidden numeric constant: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _binding_sha256(value: Mapping[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "binding_sha256"}
    return hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()


def _parse_json(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError as exc:
        raise R05ContractError(f"{name} is not UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise R05ContractError(f"{name} is not strict JSON") from exc
    result = _mapping(value, name)
    _reject_nonfinite_tree(result, name)
    return result


def _relative_string(value: object, name: str) -> str:
    result = _string(value, name)
    path = PurePosixPath(result)
    if (
        path.is_absolute()
        or result != path.as_posix()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise R05ContractError(f"{name} must be a normalized workspace-relative path")
    return result


def _reject_symlink_chain(
    root: Path,
    relative: str,
    name: str,
    *,
    leaf_may_not_exist: bool = False,
) -> Path:
    path = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        path = path / part
        is_leaf = index == len(parts) - 1
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if leaf_may_not_exist and is_leaf:
                return path
            raise R05ContractError(f"{name} does not exist")
        if stat.S_ISLNK(metadata.st_mode):
            raise R05ContractError(f"{name} cannot traverse a symlink")
        if not is_leaf and not stat.S_ISDIR(metadata.st_mode):
            raise R05ContractError(f"{name} parent must be a directory")
    return path


def _read_file(
    root: Path,
    relative: str,
    name: str,
    *,
    before_second_stat_hook: Callable[[], None] | None = None,
) -> tuple[bytes, FileSnapshot]:
    normalized = _relative_string(relative, f"{name} path")
    path = _reject_symlink_chain(root, normalized, name)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise R05ContractError(f"{name} is not a readable non-symlink file") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise R05ContractError(f"{name} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        if before_second_stat_hook is not None:
            before_second_stat_hook()
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
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
        raise R05ContractError(f"{name} changed while it was read")
    try:
        path_after = path.lstat()
    except FileNotFoundError as exc:
        raise R05ContractError(f"{name} disappeared after it was read") from exc
    if (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_size,
        path_after.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise R05ContractError(f"{name} changed while it was read")
    data = b"".join(chunks)
    return data, FileSnapshot(
        path=normalized,
        sha256=hashlib.sha256(data).hexdigest(),
        device=after.st_dev,
        inode=after.st_ino,
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
    )


def _verify_snapshot(root: Path, expected: FileSnapshot, name: str) -> None:
    _, observed = _read_file(root, expected.path, name)
    if observed != expected:
        raise R05ContractError(f"{name} changed after validation")


def load_config(path: Path) -> Mapping[str, Any]:
    if path.is_symlink():
        raise R05ContractError("R05 config may not be a symlink")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise R05ContractError("R05 config is not readable") from exc
    try:
        loaded = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except UnicodeDecodeError as exc:
        raise R05ContractError("R05 config must be UTF-8") from exc
    except yaml.YAMLError as exc:
        raise R05ContractError("R05 config is not valid unique-key YAML") from exc
    result = _mapping(loaded, "R05 config")
    _reject_nonfinite_tree(result, "R05 config")
    return result


def load_json(path: Path) -> Mapping[str, Any]:
    if path.is_symlink():
        raise R05ContractError("JSON input may not be a symlink")
    try:
        return _parse_json(path.read_bytes(), str(path))
    except OSError as exc:
        raise R05ContractError(f"JSON input is not readable: {path}") from exc


def _validate_report_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$id"),
        "voice_coach.r05_grpo_decision_report.v1",
        "decision schema ID",
    )
    _expect(schema.get("type"), "object", "decision schema root type")
    _expect(schema.get("additionalProperties"), False, "decision schema strictness")
    _expect(
        set(_list(schema.get("required"), "decision schema required")),
        REPORT_KEYS,
        "decision schema required fields",
    )
    properties = _mapping(schema.get("properties"), "decision schema properties")
    _expect(set(properties), REPORT_KEYS, "decision schema property names")
    privacy = _mapping(properties["privacy_zone"], "decision schema privacy")
    _expect(privacy.get("const"), "A1_aggregate_only", "decision schema privacy")
    for field in (
        "contains_row_level_data",
        "contains_direct_identifiers",
        "contains_free_text",
        "training_or_download_executed",
        "publication_allowed",
    ):
        _expect(
            _mapping(properties[field], f"decision schema {field}").get("const"),
            False,
            f"decision schema {field}",
        )
    gate = _mapping(
        _mapping(
            _mapping(schema.get("$defs"), "schema definitions").get("gateResult"),
            "gate definition",
        ).get("properties"),
        "gate properties",
    )
    allowed_ids = _list(
        _mapping(gate.get("gate_id"), "gate ID schema").get("enum"),
        "gate ID enum",
    )
    _expect(allowed_ids, list(GATE_IDS), "decision schema gate IDs")
    serialized = json.dumps(schema, ensure_ascii=False, sort_keys=True).casefold()
    for forbidden in (
        '"participant_id"',
        '"listener_id"',
        '"speaker_id"',
        '"candidate_id"',
        '"row_id"',
        '"free_text"',
        '"audio_path"',
    ):
        if forbidden in serialized:
            raise R05ContractError(
                f"decision schema exposes forbidden A1 field {forbidden}"
            )


def _validate_gate_template(
    template: Mapping[str, Any],
    *,
    require_tbd: bool,
) -> None:
    _expect(
        template.get("schema_version"),
        "voice_coach.r05_grpo_gate_preregistration.v1",
        "preregistration template schema",
    )
    gates = _list(template.get("gate_definitions"), "preregistration gates")
    if len(gates) != len(GATE_IDS):
        raise R05ContractError("preregistration must define exactly five gates")
    for index, (raw, expected_id) in enumerate(zip(gates, GATE_IDS, strict=True)):
        gate = _mapping(raw, f"preregistration gate {index}")
        _expect(gate.get("gate_id"), expected_id, f"gate {index} ID")
        operator, threshold = FIXED_GATE_RULES[expected_id]
        _expect(gate.get("operator"), operator, f"{expected_id} operator")
        _expect(gate.get("threshold"), threshold, f"{expected_id} threshold")
        for field in (
            "metric_or_estimand_id",
            "artifact_pointer",
            "population_id",
            "alignment_id",
            "confidence_interval_method",
            "confidence_level",
            "decision_rule_id",
            "missing_or_invalid_policy",
        ):
            value = gate.get(field)
            if require_tbd:
                _expect(value, "TBD_PREREGISTRATION", f"{expected_id} {field}")
            else:
                _string(value, f"{expected_id} {field}")
                if _contains_tbd(value):
                    raise R05ContractError(
                        f"approved preregistration leaves {expected_id} {field} TBD"
                    )
        if expected_id == "multi_reference_blind_superiority":
            for field in (
                "superiority_comparator",
                "superiority_estimand",
                "superiority_margin",
                "blindness_attestation_sha256",
            ):
                value = gate.get(field)
                if require_tbd:
                    _expect(value, "TBD_PREREGISTRATION", f"blind {field}")
                elif _contains_tbd(value):
                    raise R05ContractError(
                        f"approved preregistration leaves blind {field} TBD"
                    )
        if expected_id == "actual_pilot_learning_effect_signal":
            for field in (
                "effect_estimand",
                "effect_size_definition",
                "signal_definition",
            ):
                value = gate.get(field)
                if require_tbd:
                    _expect(value, "TBD_PREREGISTRATION", f"pilot {field}")
                elif _contains_tbd(value):
                    raise R05ContractError(
                        f"approved preregistration leaves pilot {field} TBD"
                    )
            _expect(
                gate.get("actual_participant_report_required"),
                True,
                "actual participant evidence requirement",
            )
            _expect(
                gate.get("h01_planning_document_may_satisfy_gate"),
                False,
                "H01 substitution policy",
            )
    overall = _mapping(
        template.get("overall_decision_rule"),
        "overall decision rule",
    )
    _expect(
        overall.get("all_five_gates_required_for_go"),
        True,
        "all-five GO rule",
    )
    _expect(
        overall.get("posthoc_threshold_or_estimand_change_allowed"),
        False,
        "posthoc decision rule",
    )
    _expect(
        overall.get("automatic_score_may_replace_blind_or_learning_gate"),
        False,
        "automatic-score substitution",
    )
    integrity = _mapping(template.get("integrity"), "preregistration integrity")
    for field in ("aggregate_only_decision",):
        _expect(integrity.get(field), True, f"preregistration {field}")
    for field in (
        "row_level_data_allowed",
        "direct_identifiers_allowed",
        "free_text_allowed",
        "training_download_model_gpu_or_reward_execution_allowed",
    ):
        _expect(integrity.get(field), False, f"preregistration {field}")
    if require_tbd and not _contains_tbd(template):
        raise R05ContractError("draft preregistration must expose unresolved fields")
    if not require_tbd and _contains_tbd(template):
        raise R05ContractError("approved preregistration contains TBD")


def _validate_authorization_template(
    template: Mapping[str, Any],
    *,
    require_draft: bool,
) -> None:
    _expect(
        template.get("schema_version"),
        "voice_coach.r05_grpo_human_authorization.v1",
        "authorization schema",
    )
    assurances = _mapping(
        template.get("reviewed_assurances"),
        "authorization assurances",
    )
    expected_assurances = {
        "all_five_gate_evidence_reviewed",
        *ASSURANCE_KEYS - {"human_authorization_valid"},
        "privacy_and_retention_reviewed",
        "rollback_plan_reviewed",
    }
    _expect(set(assurances), expected_assurances, "authorization assurances")
    scope = _mapping(template.get("authorization_scope"), "authorization scope")
    _expect(
        set(scope),
        {
            "go_no_go_decision_authorized",
            "grpo_training_authorized",
            "model_download_authorized",
            "gpu_execution_authorized",
            "deployment_authorized",
            "publication_authorized",
        },
        "authorization scope",
    )
    integrity = _mapping(template.get("integrity"), "authorization integrity")
    _expect(integrity.get("contains_direct_identifier"), False, "auth identifier")
    _expect(integrity.get("contains_free_text"), False, "auth free text")
    if require_draft:
        _expect(template.get("status"), "not_authorized", "draft auth status")
        if not _contains_tbd(template):
            raise R05ContractError("draft authorization must expose unresolved fields")
        if any(bool(value) for value in assurances.values()):
            raise R05ContractError("draft authorization assurance must be false")
        if any(bool(value) for value in scope.values()):
            raise R05ContractError("draft authorization scope must be false")
    else:
        _expect(template.get("status"), "authorized", "authorization status")
        if _contains_tbd(template):
            raise R05ContractError("authorization contains unresolved fields")
        if not all(value is True for value in assurances.values()):
            raise R05ContractError("all authorization assurances must be true")
        _expect(
            scope.get("go_no_go_decision_authorized"),
            True,
            "GO/no-go authorization",
        )
        for field in (
            "grpo_training_authorized",
            "model_download_authorized",
            "gpu_execution_authorized",
            "deployment_authorized",
            "publication_authorized",
        ):
            _boolean(scope.get(field), f"authorization scope {field}")


def _validate_experiment(config: Mapping[str, Any]) -> None:
    experiment = _mapping(config.get("experiment"), "experiment")
    expected = {
        "id": "r05_grpo_go_no_go_v1",
        "stage": "wp7_r05_grpo_entry_decision",
        "implementation_status": STATIC_STATUS,
        "actual_grpo_training_allowed": False,
        "actual_f5_or_styletts_execution_allowed": False,
        "actual_model_or_weight_download_allowed": False,
        "actual_model_access_allowed": False,
        "actual_gpu_access_allowed": False,
        "actual_reward_execution_allowed": False,
        "actual_metric_computation_allowed": False,
        "actual_effect_estimation_allowed": False,
        "actual_decision_report_write_allowed": False,
        "deployment_allowed": False,
        "publication_allowed": False,
        "completion_claim_allowed": False,
    }
    _expect(dict(experiment), expected, "R05 static experiment policy")


def _validate_gate_config(config: Mapping[str, Any]) -> None:
    contract = _mapping(config.get("gate_contract"), "gate contract")
    _expect(
        contract.get("fixed_gate_ids"),
        list(GATE_IDS),
        "fixed R05 gate IDs",
    )
    common_tbd = (
        "exact_artifact_pointer",
        "metric_or_estimand_id",
        "population_id",
        "alignment_id",
        "confidence_interval_method",
        "decision_rule_id",
    )
    for gate_id in GATE_IDS:
        gate = _mapping(contract.get(gate_id), f"gate {gate_id}")
        operator, threshold = FIXED_GATE_RULES[gate_id]
        _expect(gate.get("operator"), operator, f"{gate_id} operator")
        _expect(gate.get("threshold"), threshold, f"{gate_id} threshold")
        for field in common_tbd:
            _expect(
                gate.get(field),
                "TBD_PREREGISTRATION",
                f"{gate_id} {field}",
            )
    blind = _mapping(
        contract.get("multi_reference_blind_superiority"),
        "blind superiority gate",
    )
    for field in (
        "superiority_comparator",
        "superiority_estimand",
        "superiority_margin",
    ):
        _expect(blind.get(field), "TBD_PREREGISTRATION", f"blind {field}")
    pilot = _mapping(
        contract.get("actual_pilot_learning_effect_signal"),
        "actual pilot gate",
    )
    for field in ("effect_estimand", "effect_size_definition", "signal_definition"):
        _expect(pilot.get(field), "TBD_PREREGISTRATION", f"pilot {field}")
    _expect(
        pilot.get("actual_participant_report_required"),
        True,
        "actual participant report requirement",
    )
    _expect(
        pilot.get("h01_planning_document_may_satisfy_gate"),
        False,
        "H01 substitution",
    )
    for field in ("all_five_required_for_go",):
        _expect(contract.get(field), True, f"gate contract {field}")
    for field in (
        "automatic_score_substitution_allowed",
        "posthoc_rule_change_allowed",
    ):
        _expect(contract.get(field), False, f"gate contract {field}")
    _expect(
        contract.get("missing_or_invalid_outcome"),
        "TBD_PREREGISTRATION",
        "missing outcome",
    )
    _expect(
        contract.get("failed_gate_outcome"),
        "TBD_PREREGISTRATION",
        "failed outcome",
    )


def _validate_predecessor_config(config: Mapping[str, Any]) -> None:
    predecessors = _mapping(config.get("predecessors"), "predecessors")
    _expect(
        set(predecessors),
        {"s108_final", "s203_expert_holdout", "r04_blind_evaluation"},
        "predecessor names",
    )
    s108 = _mapping(predecessors["s108_final"], "S108 predecessor")
    _expect(s108.get("validation_order"), 1, "S108 order")
    _expect(s108.get("deep_validation_required"), True, "S108 deep validation")
    _relative_string(s108.get("delegate_config_path"), "S108 delegate config")
    _sha256(s108.get("delegate_config_sha256"), "S108 delegate config SHA")
    _relative_string(s108.get("delegate_contract_path"), "S108 delegate contract")
    _sha256(s108.get("delegate_contract_sha256"), "S108 delegate contract SHA")
    _relative_string(s108.get("completion_commit_path"), "S108 completion commit")
    _expect(
        s108.get("completion_commit_sha256"),
        "TBD_S108_COMPLETION",
        "canonical S108 completion SHA",
    )
    _relative_string(s108.get("registry_path"), "S108 registry")
    _expect(
        s108.get("required_schema_version"),
        "voice_coach.wavlm_s108_final_s1_commit.v1",
        "S108 commit schema",
    )
    _expect(s108.get("required_status"), "committed", "S108 status")
    _expect(
        s108.get("required_decision"),
        "research_candidate_passed",
        "S108 decision",
    )
    _expect(s108.get("aggregate_only"), True, "S108 aggregate boundary")

    s203 = _mapping(predecessors["s203_expert_holdout"], "S203 predecessor")
    _expect(s203.get("validation_order"), 2, "S203 order")
    _expect(s203.get("deep_validation_required"), True, "S203 deep validation")
    _relative_string(s203.get("static_config_path"), "S203 static config")
    _sha256(s203.get("static_config_sha256"), "S203 static config SHA")
    _relative_string(s203.get("static_contract_path"), "S203 static contract")
    _sha256(s203.get("static_contract_sha256"), "S203 static contract SHA")
    for field in (
        "completion_commit_path",
        "registry_path",
        "aggregate_report_path",
        "required_commit_schema_version",
        "required_report_schema_version",
    ):
        _expect(s203.get(field), "TBD_PREREGISTRATION", f"S203 {field}")
    _expect(
        s203.get("completion_commit_sha256"),
        "TBD_S203_COMPLETION",
        "S203 completion SHA",
    )
    _expect(s203.get("required_actual_expert_holdout"), True, "actual S203")
    _expect(
        s203.get("h01_or_planning_artifact_may_satisfy"),
        False,
        "S203 planning substitution",
    )
    _expect(s203.get("aggregate_only_for_r05"), True, "S203 aggregate boundary")

    r04 = _mapping(predecessors["r04_blind_evaluation"], "R04 predecessor")
    _expect(r04.get("validation_order"), 3, "R04 order")
    _expect(r04.get("deep_validation_required"), True, "R04 deep validation")
    _relative_string(r04.get("config_path"), "R04 config")
    _expect(r04.get("config_sha256"), "TBD_R04_FINAL_SHA256", "R04 config SHA")
    _relative_string(r04.get("contract_path"), "R04 contract")
    _expect(
        r04.get("contract_sha256"),
        "TBD_R04_FINAL_SHA256",
        "R04 contract SHA",
    )
    for field in ("completion_commit_path", "registry_path", "aggregate_report_path"):
        _relative_string(r04.get(field), f"R04 {field}")
    for field in (
        "completion_commit_sha256",
        "required_commit_schema_version",
        "required_report_schema_version",
    ):
        _expect(r04.get(field), "TBD_R04_COMPLETION", f"R04 {field}")
    _expect(
        r04.get("required_blind_superiority_result"),
        True,
        "R04 superiority evidence",
    )
    _expect(r04.get("aggregate_only_for_r05"), True, "R04 aggregate boundary")


def _validate_external_and_authorization_config(config: Mapping[str, Any]) -> None:
    external = _mapping(config.get("external_evidence"), "external evidence")
    _expect(
        set(external),
        {"h00_human_research_approval", "h04_actual_pilot_report"},
        "external evidence names",
    )
    h00 = _mapping(external["h00_human_research_approval"], "H00")
    _expect(h00.get("validation_order"), 4, "H00 order")
    _expect(h00.get("deep_validation_required"), True, "H00 deep validation")
    _expect(h00.get("approval_path"), "TBD_PREREGISTRATION", "H00 path")
    _expect(h00.get("approval_sha256"), "TBD_EXTERNAL", "H00 SHA")
    _expect(
        h00.get("required_schema_version"),
        "TBD_PREREGISTRATION",
        "H00 schema",
    )
    _expect(h00.get("approval_must_cover_actual_pilot"), True, "H00 scope")
    _expect(h00.get("aggregate_only_for_r05"), True, "H00 aggregate boundary")
    h04 = _mapping(external["h04_actual_pilot_report"], "H04")
    _expect(h04.get("validation_order"), 5, "H04 order")
    _expect(h04.get("deep_validation_required"), True, "H04 deep validation")
    _expect(h04.get("report_path"), "TBD_PREREGISTRATION", "H04 path")
    _expect(h04.get("report_sha256"), "TBD_H04_COMPLETION", "H04 SHA")
    _expect(
        h04.get("required_schema_version"),
        "TBD_PREREGISTRATION",
        "H04 schema",
    )
    for field in (
        "actual_participant_data_required",
        "pre_post_and_retention_required",
    ):
        _expect(h04.get(field), True, f"H04 {field}")
    _expect(
        h04.get("h01_power_or_preregistration_may_satisfy"),
        False,
        "H01 H04 substitution",
    )
    _expect(h04.get("aggregate_only_for_r05"), True, "H04 aggregate boundary")

    prereg = _mapping(
        config.get("independent_preregistration"),
        "independent preregistration",
    )
    _expect(prereg.get("validation_order"), 6, "preregistration order")
    _expect(prereg.get("placeholder"), "TBD_PREREGISTRATION", "prereg marker")
    _relative_string(prereg.get("template_path"), "prereg template")
    _sha256(prereg.get("template_sha256"), "prereg template SHA")
    _expect(
        prereg.get("approved_artifact_path"),
        "TBD_PREREGISTRATION",
        "prereg artifact path",
    )
    _expect(
        prereg.get("approved_artifact_sha256"),
        "TBD_PREREGISTRATION",
        "prereg artifact SHA",
    )
    for field in (
        "independent_review_required",
        "unresolved_fields_must_fail_closed",
        "must_bind_exact_predecessor_hashes",
    ):
        _expect(prereg.get(field), True, f"preregistration {field}")

    authorization = _mapping(
        config.get("human_authorization"),
        "human authorization",
    )
    _expect(authorization.get("validation_order"), 7, "authorization order")
    _expect(authorization.get("placeholder"), "TBD_EXTERNAL", "auth marker")
    _relative_string(authorization.get("template_path"), "auth template")
    _sha256(authorization.get("template_sha256"), "auth template SHA")
    _expect(
        authorization.get("approved_artifact_path"),
        "TBD_PREREGISTRATION",
        "auth artifact path",
    )
    _expect(
        authorization.get("approved_artifact_sha256"),
        "TBD_EXTERNAL",
        "auth artifact SHA",
    )
    for field in (
        "independent_authorizer_required",
        "unresolved_fields_must_fail_closed",
        "must_bind_config_preregistration_and_predecessors",
    ):
        _expect(authorization.get(field), True, f"authorization {field}")
    _expect(
        authorization.get("authorization_created_by_this_package"),
        False,
        "authorization creation policy",
    )


def _validate_safety_privacy_outputs_runtime(config: Mapping[str, Any]) -> None:
    safety = _mapping(
        config.get("model_reward_license_safety"),
        "model/reward/license/safety",
    )
    tbd_fields = (
        "exact_model_family",
        "exact_model_artifact_id",
        "exact_model_revision",
        "model_artifact_sha256",
        "code_license_id",
        "code_license_evidence_sha256",
        "weight_license_id",
        "weight_license_evidence_sha256",
        "training_data_license_id",
        "training_data_license_evidence_sha256",
        "reward_implementation_id",
        "reward_implementation_sha256",
        "reward_directionality_evidence_sha256",
        "reward_hacking_audit_sha256",
        "safety_review_sha256",
        "rollback_plan_sha256",
    )
    for field in tbd_fields:
        _expect(safety.get(field), "TBD_PREREGISTRATION", f"safety {field}")
    for field in (
        "best_of_n_or_rejection_sampling_required_before_grpo",
        "content_accuracy_hard_gate_required",
        "key_phone_hard_gate_required",
        "artifact_hard_gate_required",
        "reward_must_not_be_collapsed_to_single_scalar_without_preregistered_rule",
        "all_unresolved_values_fail_closed",
    ):
        _expect(safety.get(field), True, f"safety {field}")

    privacy = _mapping(config.get("privacy_and_decision"), "privacy")
    _expect(privacy.get("decision_input_zone"), "A1_aggregate_only", "input zone")
    _expect(privacy.get("decision_report_zone"), "A1_aggregate_only", "report zone")
    for field in (
        "row_level_data_allowed",
        "direct_identifiers_allowed",
        "pseudonymous_row_ids_allowed",
        "free_text_allowed",
        "raw_audio_allowed",
        "individual_ratings_allowed",
        "participant_records_allowed",
        "model_weights_allowed",
    ):
        _expect(privacy.get(field), False, f"privacy {field}")
    for field in (
        "aggregate_evidence_hashes_required",
        "go_requires_all_five_gates_passed",
        "go_requires_all_assurances_passed",
        "missing_or_invalid_never_inferred_as_pass",
        "publication_requires_separate_promotion",
    ):
        _expect(privacy.get(field), True, f"privacy {field}")
    _expect(
        set(_list(privacy.get("forbidden_recursive_fields"), "forbidden fields")),
        {
            "row_id",
            "row_ids",
            "row_id_sha256",
            "item_id",
            "item_ids",
            "item_id_sha256",
            "participant_id",
            "participant_ids",
            "participant_id_sha256",
            "listener_id",
            "listener_ids",
            "listener_id_sha256",
            "speaker_id",
            "speaker_ids",
            "speaker_id_sha256",
            "candidate_id",
            "candidate_ids",
            "candidate_id_sha256",
            "name",
            "email",
            "phone",
            "address",
            "free_text",
            "free_text_note",
            "comment",
            "transcript",
            "target_text",
            "audio_path",
            "model_path",
        },
        "forbidden privacy fields",
    )
    _expect(config.get("execution_order"), list(EXECUTION_ORDER), "execution order")

    outputs = _mapping(config.get("outputs"), "outputs")
    for field in (
        "decision_schema_path",
        "decision_report_path",
        "commit_path",
        "registry_path",
        "failure_report_path",
    ):
        _relative_string(outputs.get(field), f"output {field}")
    for field in ("no_overwrite", "atomic_completed_bundle_required", "no_symlink"):
        _expect(outputs.get(field), True, f"output {field}")
    _expect(outputs.get("write_enabled_in_this_package"), False, "output write")
    _expect(outputs.get("current_output_count"), 0, "output count")
    _expect(
        outputs.get("completion_claim_allowed"),
        False,
        "completion claim policy",
    )

    runtime = _mapping(config.get("runtime"), "runtime")
    counts = _mapping(
        runtime.get("config_only_required_counts"),
        "config-only counts",
    )
    _expect(set(counts), set(RUNTIME_COUNTER_FIELDS), "config-only count names")
    for field in RUNTIME_COUNTER_FIELDS:
        _expect(counts.get(field), 0, f"config-only {field}")
    _expect(runtime.get("cpu_only_static_validation"), True, "CPU-only static")
    for field in (
        "environment_file_read_allowed",
        "network_allowed",
        "model_allowed",
        "gpu_allowed",
        "reward_execution_allowed",
        "training_allowed",
    ):
        _expect(runtime.get(field), False, f"runtime {field}")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    """Validate R05 C1/static policy without resolving any runtime path."""

    active_audit = audit if audit is not None else AccessAudit()
    root = workspace_root.resolve(strict=True)
    if config_path.is_symlink():
        raise R05ContractError("R05 config may not be a symlink", audit=active_audit)
    _expect_keys(config, TOP_LEVEL_KEYS, "R05 top level")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema version")
    _validate_experiment(config)
    _validate_gate_config(config)
    _validate_predecessor_config(config)
    _validate_external_and_authorization_config(config)
    _validate_safety_privacy_outputs_runtime(config)

    sources = _mapping(config.get("canonical_sources"), "canonical sources")
    _expect(
        sources.get("access_scope"),
        "C1_code_config_docs_only",
        "source access scope",
    )
    pins = _list(sources.get("pins"), "source pins")
    if len(pins) != len(SOURCE_PATHS):
        raise R05ContractError("R05 source pin count differs", audit=active_audit)
    snapshots: list[FileSnapshot] = []
    for index, (raw, expected_path) in enumerate(zip(pins, SOURCE_PATHS, strict=True)):
        pin = _mapping(raw, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        _expect(pin.get("path"), expected_path, f"source pin {index} path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        try:
            data, snapshot = _read_file(root, expected_path, f"source pin {index}")
        except R05ContractError as exc:
            exc.audit = active_audit
            raise
        active_audit.source_pin_read_count += 1
        _expect(snapshot.sha256, expected_sha, f"source pin {expected_path} SHA")
        snapshots.append(snapshot)
        if expected_path.endswith(".schema.json"):
            _validate_report_schema(_parse_json(data, "R05 decision schema"))
        elif expected_path.endswith("gate_preregistration_v1.template.json"):
            _validate_gate_template(
                _parse_json(data, "R05 preregistration template"),
                require_tbd=True,
            )
        elif expected_path.endswith("human_authorization_v1.template.json"):
            _validate_authorization_template(
                _parse_json(data, "R05 authorization template"),
                require_draft=True,
            )

    relative_config = config_path.resolve(strict=True).relative_to(root).as_posix()
    _, config_snapshot = _read_file(root, relative_config, "R05 config")
    policy = {
        key: config[key]
        for key in (
            "gate_contract",
            "predecessors",
            "external_evidence",
            "independent_preregistration",
            "human_authorization",
            "model_reward_license_safety",
            "privacy_and_decision",
            "execution_order",
            "outputs",
            "runtime",
        )
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        source_snapshots=tuple(snapshots),
    )


def _collect_forbidden_a1_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key.casefold() in FORBIDDEN_A1_FIELDS:
                found.add(key)
            found.update(_collect_forbidden_a1_keys(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            found.update(_collect_forbidden_a1_keys(item))
    return found


def _nullable_finite(value: object, name: str) -> float | None:
    if value is None:
        return None
    return _finite(value, name)


def validate_decision_report_payload(
    report: Mapping[str, Any],
    *,
    expected_config_sha256: str | None = None,
    expected_preregistration_sha256: str | None = None,
    expected_authorization_sha256: str | None = None,
    expected_evidence_bindings: Mapping[str, str] | None = None,
) -> str:
    """Validate a future synthetic or actual A1 decision report.

    This function validates already-computed aggregate decisions.  It does not
    compute a metric or effect and does not authorize training.
    """

    _reject_nonfinite_tree(report, "R05 decision report")
    forbidden = _collect_forbidden_a1_keys(report)
    if forbidden:
        raise R05ContractError(
            f"decision report contains forbidden A1 fields: {sorted(forbidden)}"
        )
    _expect_keys(report, REPORT_KEYS, "decision report")
    _expect(
        report.get("schema_version"),
        "voice_coach.r05_grpo_decision_report.v1",
        "decision report schema",
    )
    report_id = _string(report.get("report_id"), "decision report ID")
    if not REPORT_ID_RE.fullmatch(report_id):
        raise R05ContractError("decision report ID is invalid")
    _expect(report.get("status"), "completed", "decision report status")
    _expect(report.get("privacy_zone"), "A1_aggregate_only", "decision privacy")
    for field in (
        "contains_row_level_data",
        "contains_direct_identifiers",
        "contains_free_text",
        "training_or_download_executed",
        "publication_allowed",
    ):
        _expect(report.get(field), False, f"decision report {field}")
    config_sha = _sha256(report.get("config_sha256"), "decision config SHA")
    prereg_sha = _sha256(
        report.get("preregistration_sha256"),
        "decision preregistration SHA",
    )
    authorization_sha = _sha256(
        report.get("human_authorization_sha256"),
        "decision authorization SHA",
    )
    if expected_config_sha256 is not None:
        _expect(config_sha, expected_config_sha256, "decision config binding")
    if expected_preregistration_sha256 is not None:
        _expect(
            prereg_sha,
            expected_preregistration_sha256,
            "decision preregistration binding",
        )
    if expected_authorization_sha256 is not None:
        _expect(
            authorization_sha,
            expected_authorization_sha256,
            "decision authorization binding",
        )

    bindings = _mapping(report.get("evidence_bindings"), "evidence bindings")
    _expect_keys(bindings, EVIDENCE_BINDING_KEYS, "evidence bindings")
    normalized_bindings = {
        key: _sha256(bindings.get(key), f"evidence binding {key}")
        for key in EVIDENCE_BINDING_KEYS
    }
    if expected_evidence_bindings is not None:
        _expect(
            normalized_bindings,
            dict(expected_evidence_bindings),
            "decision evidence bindings",
        )

    gate_results = _list(report.get("gate_results"), "decision gate results")
    if len(gate_results) != len(GATE_IDS):
        raise R05ContractError("decision report must contain exactly five gates")
    decisions: list[str] = []
    for index, (raw, expected_id) in enumerate(
        zip(gate_results, GATE_IDS, strict=True)
    ):
        gate = _mapping(raw, f"gate result {index}")
        _expect_keys(gate, GATE_RESULT_KEYS, f"gate result {index}")
        _expect(gate.get("gate_id"), expected_id, f"gate result {index} ID")
        operator, threshold = FIXED_GATE_RULES[expected_id]
        _expect(gate.get("operator"), operator, f"{expected_id} operator")
        _expect(gate.get("threshold"), threshold, f"{expected_id} threshold")
        decision = _string(gate.get("decision"), f"{expected_id} decision")
        if decision not in {"passed", "failed", "missing"}:
            raise R05ContractError(f"{expected_id} decision is invalid")
        decisions.append(decision)
        values = (
            _nullable_finite(gate.get("point_estimate"), f"{expected_id} estimate"),
            _nullable_finite(
                gate.get("confidence_interval_lower"),
                f"{expected_id} CI lower",
            ),
            _nullable_finite(
                gate.get("confidence_interval_upper"),
                f"{expected_id} CI upper",
            ),
            _nullable_finite(
                gate.get("superiority_margin"),
                f"{expected_id} superiority margin",
            ),
            _nullable_finite(gate.get("effect_size"), f"{expected_id} effect size"),
        )
        if values[1] is not None and values[2] is not None and values[1] > values[2]:
            raise R05ContractError(f"{expected_id} confidence interval is reversed")
        metadata_fields = (
            "metric_or_estimand_id",
            "population_id",
            "alignment_id",
            "confidence_interval_method",
            "decision_rule_id",
        )
        if decision == "missing":
            if gate.get("evidence_sha256") is not None:
                _sha256(gate.get("evidence_sha256"), f"{expected_id} evidence SHA")
        else:
            _sha256(gate.get("evidence_sha256"), f"{expected_id} evidence SHA")
            for field in metadata_fields:
                value = _string(gate.get(field), f"{expected_id} {field}")
                if _contains_tbd(value):
                    raise R05ContractError(f"{expected_id} {field} remains unresolved")
            if values[0] is None:
                raise R05ContractError(
                    f"{expected_id} non-missing decision requires a point estimate"
                )
        if expected_id in {
            "s1_phone_f1",
            "s1_utterance_icc",
            "s2_severe_error_recall",
        }:
            if (
                decision == "passed"
                and values[0] is not None
                and threshold is not None
                and values[0] < threshold
            ):
                raise R05ContractError(
                    f"{expected_id} passed estimate is below its fixed threshold"
                )
            if gate.get("superiority_margin") is not None:
                raise R05ContractError(
                    f"{expected_id} cannot carry a superiority margin"
                )
        elif expected_id == "multi_reference_blind_superiority":
            if decision != "missing" and gate.get("superiority_margin") is None:
                raise R05ContractError(
                    "blind superiority requires the preregistered margin"
                )
        elif decision != "missing" and gate.get("effect_size") is None:
            raise R05ContractError(
                "actual pilot signal requires the preregistered effect size"
            )

    assurances = _mapping(report.get("assurance_results"), "assurance results")
    _expect_keys(assurances, ASSURANCE_KEYS, "assurance results")
    assurance_values = {
        key: _boolean(assurances.get(key), f"assurance {key}") for key in ASSURANCE_KEYS
    }
    overall = _string(report.get("overall_decision"), "overall decision")
    if overall not in {"GO", "NO_GO", "BLOCKED"}:
        raise R05ContractError("overall decision is invalid")
    grpo_authorized = _boolean(
        report.get("grpo_entry_authorized"),
        "GRPO entry authorization",
    )
    if overall == "GO":
        if decisions != ["passed"] * len(GATE_IDS):
            raise R05ContractError("GO requires all five gates to pass")
        if not all(assurance_values.values()):
            raise R05ContractError("GO requires every assurance to pass")
        _expect(grpo_authorized, True, "GO entry authorization")
    elif overall == "NO_GO":
        if "failed" not in decisions and all(assurance_values.values()):
            if "missing" not in decisions:
                raise R05ContractError(
                    "NO_GO requires a failed/missing gate or failed assurance"
                )
        _expect(grpo_authorized, False, "NO_GO entry authorization")
    else:
        if "missing" not in decisions:
            raise R05ContractError("BLOCKED requires at least one missing gate")
        _expect(grpo_authorized, False, "BLOCKED entry authorization")
    return overall


def _require_resolved_sha(
    value: object,
    name: str,
    *,
    stage: str,
    audit: AccessAudit,
) -> str:
    if isinstance(value, str) and value.startswith(TBD_PREFIX):
        raise R05ContractError(
            f"{name} is {value}",
            stage=stage,
            audit=audit,
        )
    try:
        return _sha256(value, name)
    except R05ContractError as exc:
        raise R05ContractError(str(exc), stage=stage, audit=audit) from exc


def _require_resolved_path(
    value: object,
    name: str,
    *,
    stage: str,
    audit: AccessAudit,
) -> str:
    if isinstance(value, str) and value.startswith(TBD_PREFIX):
        raise R05ContractError(
            f"{name} is {value}",
            stage=stage,
            audit=audit,
        )
    try:
        return _relative_string(value, name)
    except R05ContractError as exc:
        raise R05ContractError(str(exc), stage=stage, audit=audit) from exc


def _read_runtime_json(
    *,
    root: Path,
    relative: str,
    name: str,
    audit: AccessAudit,
    category: str,
    stage: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    path_counter = f"{category}_path_resolution_count"
    attempt_counter = f"{category}_read_attempt_count"
    read_counter = f"{category}_read_count"
    setattr(audit, path_counter, int(getattr(audit, path_counter)) + 1)
    setattr(audit, attempt_counter, int(getattr(audit, attempt_counter)) + 1)
    try:
        data, snapshot = _read_file(root, relative, name)
    except R05ContractError as exc:
        raise R05ContractError(
            f"{name} unavailable: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    setattr(audit, read_counter, int(getattr(audit, read_counter)) + 1)
    try:
        return _parse_json(data, name), snapshot
    except R05ContractError as exc:
        raise R05ContractError(str(exc), stage=stage, audit=audit) from exc


def _validate_a1_document(document: Mapping[str, Any], name: str) -> None:
    forbidden = _collect_forbidden_a1_keys(document)
    if forbidden:
        raise R05ContractError(
            f"{name} contains forbidden A1 fields: {sorted(forbidden)}"
        )
    _expect(document.get("privacy_zone"), "A1_aggregate_only", f"{name} privacy")
    for field in (
        "contains_row_level_data",
        "contains_direct_identifiers",
        "contains_free_text",
    ):
        _expect(document.get(field), False, f"{name} {field}")


def _validate_generic_completion_bundle(
    *,
    root: Path,
    stage_name: str,
    stage_config: Mapping[str, Any],
    audit: AccessAudit,
    commit_path_field: str = "completion_commit_path",
    commit_sha_field: str = "completion_commit_sha256",
    report_path_field: str = "aggregate_report_path",
    required_commit_schema_field: str = "required_commit_schema_version",
    required_report_schema_field: str = "required_report_schema_version",
) -> AggregateCompletionEvidence:
    expected_commit_sha = _require_resolved_sha(
        stage_config.get(commit_sha_field),
        f"{stage_name} completion commit SHA",
        stage=stage_name,
        audit=audit,
    )
    commit_path = _require_resolved_path(
        stage_config.get(commit_path_field),
        f"{stage_name} completion commit path",
        stage=stage_name,
        audit=audit,
    )
    registry_path = _require_resolved_path(
        stage_config.get("registry_path"),
        f"{stage_name} registry path",
        stage=stage_name,
        audit=audit,
    )
    report_path = _require_resolved_path(
        stage_config.get(report_path_field),
        f"{stage_name} aggregate report path",
        stage=stage_name,
        audit=audit,
    )
    commit_schema = _string(
        stage_config.get(required_commit_schema_field),
        f"{stage_name} commit schema",
    )
    report_schema = _string(
        stage_config.get(required_report_schema_field),
        f"{stage_name} report schema",
    )
    if commit_schema.startswith(TBD_PREFIX) or report_schema.startswith(TBD_PREFIX):
        raise R05ContractError(
            f"{stage_name} completion schemas remain unresolved",
            stage=stage_name,
            audit=audit,
        )
    commit, commit_snapshot = _read_runtime_json(
        root=root,
        relative=commit_path,
        name=f"{stage_name} completion commit",
        audit=audit,
        category="predecessor",
        stage=stage_name,
    )
    _expect(commit_snapshot.sha256, expected_commit_sha, f"{stage_name} commit SHA")
    _expect(commit.get("schema_version"), commit_schema, f"{stage_name} commit schema")
    _expect(
        commit.get("status"),
        stage_config.get("required_status"),
        f"{stage_name} commit status",
    )
    observed_binding = _sha256(
        commit.get("binding_sha256"),
        f"{stage_name} commit binding",
    )
    _expect(
        observed_binding,
        _binding_sha256(commit),
        f"{stage_name} commit binding",
    )
    report_record = _mapping(commit.get("aggregate_report"), f"{stage_name} report")
    registry_record = _mapping(commit.get("registry"), f"{stage_name} registry")
    _expect(report_record.get("path"), report_path, f"{stage_name} report path binding")
    report_sha = _sha256(report_record.get("sha256"), f"{stage_name} report SHA")
    _expect(
        report_record.get("schema_version"),
        report_schema,
        f"{stage_name} report schema binding",
    )
    _expect(
        registry_record.get("path"),
        registry_path,
        f"{stage_name} registry path binding",
    )
    registry_sha = _sha256(
        registry_record.get("sha256"),
        f"{stage_name} registry SHA",
    )
    registry, registry_snapshot = _read_runtime_json(
        root=root,
        relative=registry_path,
        name=f"{stage_name} completed registry",
        audit=audit,
        category="predecessor",
        stage=stage_name,
    )
    _expect(registry_snapshot.sha256, registry_sha, f"{stage_name} registry SHA")
    _expect(registry.get("status"), "completed", f"{stage_name} registry status")
    artifacts = _list(registry.get("artifacts"), f"{stage_name} registry artifacts")
    report_matches = [
        _mapping(item, f"{stage_name} registry artifact")
        for item in artifacts
        if isinstance(item, Mapping) and item.get("path") == report_path
    ]
    if len(report_matches) != 1:
        raise R05ContractError(
            f"{stage_name} registry must bind the aggregate report exactly once",
            stage=stage_name,
            audit=audit,
        )
    _expect(
        report_matches[0].get("sha256"),
        report_sha,
        f"{stage_name} registry report SHA",
    )
    report, report_snapshot = _read_runtime_json(
        root=root,
        relative=report_path,
        name=f"{stage_name} aggregate report",
        audit=audit,
        category="predecessor",
        stage=stage_name,
    )
    audit.aggregate_evidence_read_count += 1
    _expect(report_snapshot.sha256, report_sha, f"{stage_name} report SHA")
    _expect(report.get("schema_version"), report_schema, f"{stage_name} report schema")
    _expect(report.get("status"), "completed", f"{stage_name} report status")
    _validate_a1_document(report, f"{stage_name} report")
    for snapshot, label in (
        (commit_snapshot, "commit"),
        (registry_snapshot, "registry"),
        (report_snapshot, "report"),
    ):
        _verify_snapshot(root, snapshot, f"{stage_name} {label}")
    return AggregateCompletionEvidence(
        stage=stage_name,
        commit_snapshot=commit_snapshot,
        registry_snapshot=registry_snapshot,
        report_snapshot=report_snapshot,
        report=report,
    )


def _copy_delegate_audit(source: object, target: AccessAudit) -> None:
    metadata_attempts = int(
        getattr(source, "predecessor_metadata_read_attempt_count", 0)
    )
    metadata_reads = int(getattr(source, "predecessor_metadata_read_count", 0))
    artifact_reads = int(getattr(source, "predecessor_artifact_read_count", 0))
    target.predecessor_read_attempt_count += metadata_attempts + artifact_reads
    target.predecessor_read_count += metadata_reads + artifact_reads
    target.aggregate_evidence_read_count += artifact_reads
    for source_name, target_name in (
        ("data_row_read_count", "row_level_data_read_count"),
        ("protected_data_read_count", "protected_data_read_count"),
        ("audio_read_count", "audio_read_count"),
        ("model_path_resolution_count", "model_path_resolution_count"),
        ("model_weight_read_count", "model_weight_read_count"),
        ("model_download_count", "model_download_count"),
        ("network_access_count", "network_access_count"),
        ("gpu_access_count", "gpu_access_count"),
        ("metric_count", "metric_computation_count"),
        ("output_path_resolution_count", "output_path_resolution_count"),
        ("output_count", "output_count"),
    ):
        setattr(
            target,
            target_name,
            int(getattr(target, target_name)) + int(getattr(source, source_name, 0)),
        )


def deep_validate_s108_final(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> FileSnapshot:
    """Delegate the final S108 deep validation after the exact SHA is pinned."""

    stage = "s108_final"
    contract = _mapping(
        _mapping(config.get("predecessors"), "predecessors").get("s108_final"),
        "S108 predecessor",
    )
    expected_sha = _require_resolved_sha(
        contract.get("completion_commit_sha256"),
        "S108 completion commit SHA",
        stage=stage,
        audit=audit,
    )
    delegate_config_path = _require_resolved_path(
        contract.get("delegate_config_path"),
        "S108 delegate config path",
        stage=stage,
        audit=audit,
    )
    audit.predecessor_path_resolution_count += 1
    audit.predecessor_read_attempt_count += 1
    try:
        from voice_coach.training import (  # noqa: PLC0415
            s2_s202_auditor_contract as s202,
        )

        delegate_config = s202.load_config(root / delegate_config_path)
        audit.predecessor_read_count += 1
        delegate_audit = s202.AccessAudit()
        evidence = s202.deep_validate_s108_final(
            root=root,
            config=delegate_config,
            audit=delegate_audit,
        )
    except Exception as exc:
        if "delegate_audit" in locals():
            _copy_delegate_audit(delegate_audit, audit)
        raise R05ContractError(
            f"S108 deep validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _copy_delegate_audit(delegate_audit, audit)
    inner = getattr(evidence, "inner", None)
    delegate_snapshot = getattr(inner, "commit_snapshot", None)
    if delegate_snapshot is None:
        raise R05ContractError(
            "S108 delegate did not return a commit snapshot",
            stage=stage,
            audit=audit,
        )
    snapshot = FileSnapshot(
        path=_relative_string(delegate_snapshot.path, "S108 commit snapshot path"),
        sha256=_sha256(delegate_snapshot.sha256, "S108 commit snapshot SHA"),
        device=int(delegate_snapshot.device),
        inode=int(delegate_snapshot.inode),
        size=int(delegate_snapshot.size),
        mtime_ns=int(delegate_snapshot.mtime_ns),
    )
    _expect(snapshot.sha256, expected_sha, "S108 completion SHA")
    _expect(
        snapshot.path,
        contract.get("completion_commit_path"),
        "S108 completion path",
    )
    _verify_snapshot(root, snapshot, "S108 completion commit")
    audit.s108_deep_validation_count = 1
    return snapshot


def deep_validate_s203_actual(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> AggregateCompletionEvidence:
    """Deep-validate a future actual S203 aggregate completion bundle."""

    contract = _mapping(
        _mapping(config.get("predecessors"), "predecessors").get("s203_expert_holdout"),
        "S203 predecessor",
    )
    evidence = _validate_generic_completion_bundle(
        root=root,
        stage_name="s203_expert_holdout",
        stage_config=contract,
        audit=audit,
    )
    _expect(
        evidence.report.get("actual_expert_holdout"),
        True,
        "S203 actual holdout",
    )
    _mapping(
        evidence.report.get("s2_severe_error_recall"),
        "S203 severe-error recall evidence",
    )
    audit.s203_deep_validation_count = 1
    return evidence


def deep_validate_r04_actual(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> AggregateCompletionEvidence:
    """Deep-validate a future actual, blind R04 aggregate completion bundle."""

    contract = _mapping(
        _mapping(config.get("predecessors"), "predecessors").get(
            "r04_blind_evaluation"
        ),
        "R04 predecessor",
    )
    evidence = _validate_generic_completion_bundle(
        root=root,
        stage_name="r04_blind_evaluation",
        stage_config=contract,
        audit=audit,
    )
    _expect(
        evidence.report.get("actual_blind_evaluation"),
        True,
        "R04 actual blind evaluation",
    )
    blind = _mapping(
        evidence.report.get("blind_superiority"),
        "R04 blind superiority evidence",
    )
    _expect(blind.get("listener_blinding_verified"), True, "R04 listener blinding")
    audit.r04_deep_validation_count = 1
    return evidence


def _deep_validate_single_external(
    *,
    root: Path,
    path: object,
    expected_sha: object,
    expected_schema: object,
    required_status: object,
    name: str,
    stage: str,
    audit: AccessAudit,
) -> ExternalEvidence:
    resolved_sha = _require_resolved_sha(
        expected_sha,
        f"{name} SHA",
        stage=stage,
        audit=audit,
    )
    relative = _require_resolved_path(
        path,
        f"{name} path",
        stage=stage,
        audit=audit,
    )
    schema = _string(expected_schema, f"{name} schema")
    if schema.startswith(TBD_PREFIX):
        raise R05ContractError(
            f"{name} schema is {schema}",
            stage=stage,
            audit=audit,
        )
    document, snapshot = _read_runtime_json(
        root=root,
        relative=relative,
        name=name,
        audit=audit,
        category="external_evidence",
        stage=stage,
    )
    _expect(snapshot.sha256, resolved_sha, f"{name} SHA")
    _expect(document.get("schema_version"), schema, f"{name} schema")
    _expect(document.get("status"), required_status, f"{name} status")
    observed_binding = _sha256(document.get("binding_sha256"), f"{name} binding")
    _expect(observed_binding, _binding_sha256(document), f"{name} binding")
    _validate_a1_document(document, name)
    _verify_snapshot(root, snapshot, name)
    return ExternalEvidence(stage=stage, snapshot=snapshot, document=document)


def deep_validate_h00_approval(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> ExternalEvidence:
    """Deep-validate H00 approval without reading a participant record."""

    contract = _mapping(
        _mapping(config.get("external_evidence"), "external evidence").get(
            "h00_human_research_approval"
        ),
        "H00 approval",
    )
    evidence = _deep_validate_single_external(
        root=root,
        path=contract.get("approval_path"),
        expected_sha=contract.get("approval_sha256"),
        expected_schema=contract.get("required_schema_version"),
        required_status=contract.get("required_status"),
        name="H00 human-research approval",
        stage="h00_approval",
        audit=audit,
    )
    for field in (
        "actual_pilot_covered",
        "consent_approved",
        "privacy_approved",
        "retention_approved",
        "deletion_approved",
    ):
        _expect(evidence.document.get(field), True, f"H00 {field}")
    audit.h00_deep_validation_count = 1
    return evidence


def deep_validate_h04_actual_report(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> ExternalEvidence:
    """Deep-validate an actual H04 aggregate pilot report."""

    contract = _mapping(
        _mapping(config.get("external_evidence"), "external evidence").get(
            "h04_actual_pilot_report"
        ),
        "H04 report",
    )
    evidence = _deep_validate_single_external(
        root=root,
        path=contract.get("report_path"),
        expected_sha=contract.get("report_sha256"),
        expected_schema=contract.get("required_schema_version"),
        required_status=contract.get("required_status"),
        name="H04 actual pilot report",
        stage="h04_actual_pilot",
        audit=audit,
    )
    for field in (
        "actual_participants_included",
        "pre_post_included",
        "retention_included",
        "blind_rating_included",
    ):
        _expect(evidence.document.get(field), True, f"H04 {field}")
    _mapping(
        evidence.document.get("learning_effect_signal"),
        "H04 learning-effect signal",
    )
    audit.aggregate_evidence_read_count += 1
    audit.h04_deep_validation_count = 1
    return evidence


def validate_frozen_preregistration(
    *,
    root: Path,
    config: Mapping[str, Any],
    expected_bindings: Mapping[str, str],
    audit: AccessAudit,
) -> PreregistrationEvidence:
    """Validate an independent, frozen, fully resolved R05 preregistration."""

    stage = "independent_preregistration"
    contract = _mapping(
        config.get("independent_preregistration"),
        "independent preregistration",
    )
    expected_sha = _require_resolved_sha(
        contract.get("approved_artifact_sha256"),
        "R05 preregistration SHA",
        stage=stage,
        audit=audit,
    )
    relative = _require_resolved_path(
        contract.get("approved_artifact_path"),
        "R05 preregistration path",
        stage=stage,
        audit=audit,
    )
    document, snapshot = _read_runtime_json(
        root=root,
        relative=relative,
        name="R05 approved preregistration",
        audit=audit,
        category="preregistration",
        stage=stage,
    )
    _expect(snapshot.sha256, expected_sha, "R05 preregistration SHA")
    _expect(
        document.get("schema_version"),
        contract.get("required_schema_version"),
        "R05 preregistration schema",
    )
    _expect(document.get("status"), "approved_and_frozen", "prereg status")
    _validate_gate_template(document, require_tbd=False)
    bindings = _mapping(document.get("evidence_bindings"), "prereg bindings")
    _expect_keys(bindings, EVIDENCE_BINDING_KEYS, "prereg bindings")
    normalized = {
        key: _sha256(bindings.get(key), f"prereg binding {key}")
        for key in EVIDENCE_BINDING_KEYS
    }
    _expect(normalized, dict(expected_bindings), "prereg predecessor bindings")
    integrity = _mapping(document.get("integrity"), "prereg integrity")
    observed_binding = _sha256(
        integrity.get("binding_sha256"),
        "preregistration binding SHA",
    )
    unsigned = dict(document)
    unsigned_integrity = dict(integrity)
    unsigned_integrity.pop("binding_sha256", None)
    unsigned["integrity"] = unsigned_integrity
    _expect(
        observed_binding,
        hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
        "preregistration binding",
    )
    _verify_snapshot(root, snapshot, "R05 approved preregistration")
    return PreregistrationEvidence(snapshot=snapshot, document=document)


def validate_human_authorization(
    *,
    root: Path,
    config: Mapping[str, Any],
    config_sha256: str,
    preregistration: PreregistrationEvidence,
    expected_bindings: Mapping[str, str],
    audit: AccessAudit,
) -> AuthorizationEvidence:
    """Validate a separate human authorization bound to all evidence."""

    stage = "human_authorization"
    contract = _mapping(config.get("human_authorization"), "human authorization")
    expected_sha = _require_resolved_sha(
        contract.get("approved_artifact_sha256"),
        "R05 authorization SHA",
        stage=stage,
        audit=audit,
    )
    relative = _require_resolved_path(
        contract.get("approved_artifact_path"),
        "R05 authorization path",
        stage=stage,
        audit=audit,
    )
    document, snapshot = _read_runtime_json(
        root=root,
        relative=relative,
        name="R05 human authorization",
        audit=audit,
        category="authorization",
        stage=stage,
    )
    _expect(snapshot.sha256, expected_sha, "R05 authorization SHA")
    _expect(
        document.get("schema_version"),
        contract.get("required_schema_version"),
        "R05 authorization schema",
    )
    _validate_authorization_template(document, require_draft=False)
    bindings = _mapping(document.get("bindings"), "authorization bindings")
    expected = {
        "r05_config_sha256": config_sha256,
        "r05_preregistration_sha256": preregistration.snapshot.sha256,
        **expected_bindings,
    }
    normalized = {
        key: _sha256(bindings.get(key), f"authorization binding {key}")
        for key in expected
    }
    _expect(normalized, expected, "authorization evidence bindings")
    integrity = _mapping(document.get("integrity"), "authorization integrity")
    observed_binding = _sha256(
        integrity.get("binding_sha256"),
        "authorization binding SHA",
    )
    unsigned = dict(document)
    unsigned_integrity = dict(integrity)
    unsigned_integrity.pop("binding_sha256", None)
    unsigned["integrity"] = unsigned_integrity
    _expect(
        observed_binding,
        hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
        "authorization binding",
    )
    _verify_snapshot(root, snapshot, "R05 human authorization")
    return AuthorizationEvidence(snapshot=snapshot, document=document)


def _gate_result(
    value: object,
    *,
    expected_gate_id: str,
    expected_evidence_sha256: str,
) -> Mapping[str, Any]:
    result = _mapping(value, f"{expected_gate_id} aggregate gate result")
    _expect_keys(result, GATE_RESULT_KEYS, f"{expected_gate_id} gate result")
    _expect(result.get("gate_id"), expected_gate_id, f"{expected_gate_id} gate ID")
    _expect(
        result.get("evidence_sha256"),
        expected_evidence_sha256,
        f"{expected_gate_id} evidence SHA",
    )
    return result


def evaluate_aggregate_decision(
    *,
    root: Path,
    config: Mapping[str, Any],
    static: StaticValidation,
    s108_snapshot: FileSnapshot,
    s203: AggregateCompletionEvidence,
    r04: AggregateCompletionEvidence,
    h00: ExternalEvidence,
    h04: ExternalEvidence,
    preregistration: PreregistrationEvidence,
    authorization: AuthorizationEvidence,
    expected_bindings: Mapping[str, str],
    audit: AccessAudit,
) -> str:
    """Evaluate the preregistered decision from five A1 gate records only."""

    s108_commit, observed_s108 = _read_runtime_json(
        root=root,
        relative=s108_snapshot.path,
        name="S108 aggregate commit for R05",
        audit=audit,
        category="predecessor",
        stage="aggregate_decision",
    )
    _expect(observed_s108, s108_snapshot, "S108 commit snapshot")
    s108_gates = _mapping(
        s108_commit.get("r05_gate_results"),
        "S108 R05 gate results",
    )
    gate_results = [
        _gate_result(
            s108_gates.get("s1_phone_f1"),
            expected_gate_id="s1_phone_f1",
            expected_evidence_sha256=s108_snapshot.sha256,
        ),
        _gate_result(
            s108_gates.get("s1_utterance_icc"),
            expected_gate_id="s1_utterance_icc",
            expected_evidence_sha256=s108_snapshot.sha256,
        ),
        _gate_result(
            s203.report.get("s2_severe_error_recall"),
            expected_gate_id="s2_severe_error_recall",
            expected_evidence_sha256=s203.commit_snapshot.sha256,
        ),
        _gate_result(
            r04.report.get("blind_superiority"),
            expected_gate_id="multi_reference_blind_superiority",
            expected_evidence_sha256=r04.commit_snapshot.sha256,
        ),
        _gate_result(
            h04.document.get("learning_effect_signal"),
            expected_gate_id="actual_pilot_learning_effect_signal",
            expected_evidence_sha256=h04.snapshot.sha256,
        ),
    ]
    decisions = [
        _string(item.get("decision"), f"{item.get('gate_id')} decision")
        for item in gate_results
    ]
    prereg_rule = _mapping(
        preregistration.document.get("overall_decision_rule"),
        "preregistered overall decision rule",
    )
    auth_assurances = _mapping(
        authorization.document.get("reviewed_assurances"),
        "authorization assurances",
    )
    assurances: dict[str, bool] = {
        key: _boolean(auth_assurances.get(key), f"authorization {key}")
        for key in ASSURANCE_KEYS
        if key != "human_authorization_valid"
    }
    assurances["human_authorization_valid"] = True
    if decisions == ["passed"] * len(GATE_IDS) and all(assurances.values()):
        decision = "GO"
    elif "missing" in decisions:
        decision = _string(
            prereg_rule.get("any_missing_or_invalid_gate_outcome"),
            "preregistered missing outcome",
        )
        if decision not in {"NO_GO", "BLOCKED"}:
            raise R05ContractError(
                "missing outcome must be preregistered as NO_GO or BLOCKED",
                stage="aggregate_decision",
                audit=audit,
            )
    else:
        decision = _string(
            prereg_rule.get("any_failed_gate_outcome"),
            "preregistered failed outcome",
        )
        if decision not in {"NO_GO", "BLOCKED"}:
            raise R05ContractError(
                "failed outcome must be preregistered as NO_GO or BLOCKED",
                stage="aggregate_decision",
                audit=audit,
            )
    report_id_seed = (
        static.config_sha256
        + preregistration.snapshot.sha256
        + authorization.snapshot.sha256
        + "".join(expected_bindings[key] for key in sorted(expected_bindings))
    )
    report: Mapping[str, Any] = {
        "schema_version": "voice_coach.r05_grpo_decision_report.v1",
        "report_id": "r05_" + hashlib.sha256(report_id_seed.encode()).hexdigest()[:32],
        "status": "completed",
        "privacy_zone": "A1_aggregate_only",
        "contains_row_level_data": False,
        "contains_direct_identifiers": False,
        "contains_free_text": False,
        "training_or_download_executed": False,
        "config_sha256": static.config_sha256,
        "preregistration_sha256": preregistration.snapshot.sha256,
        "human_authorization_sha256": authorization.snapshot.sha256,
        "evidence_bindings": dict(expected_bindings),
        "gate_results": gate_results,
        "assurance_results": assurances,
        "overall_decision": decision,
        "grpo_entry_authorized": decision == "GO",
        "publication_allowed": False,
    }
    try:
        validate_decision_report_payload(
            report,
            expected_config_sha256=static.config_sha256,
            expected_preregistration_sha256=preregistration.snapshot.sha256,
            expected_authorization_sha256=authorization.snapshot.sha256,
            expected_evidence_bindings=expected_bindings,
        )
    except R05ContractError as exc:
        raise R05ContractError(
            f"aggregate decision validation failed: {exc}",
            stage="aggregate_decision",
            audit=audit,
        ) from exc
    audit.aggregate_evidence_read_count += 5
    audit.decision_evaluation_count += 1
    return decision


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Run the ordered read-only preflight; never execute or write GRPO."""

    root = workspace_root.resolve(strict=True)
    audit = AccessAudit()
    try:
        static = validate_static_config(
            workspace_root=root,
            config_path=config_path,
            config=config,
            audit=audit,
        )
        s108 = deep_validate_s108_final(root=root, config=config, audit=audit)
        s203 = deep_validate_s203_actual(root=root, config=config, audit=audit)
        r04 = deep_validate_r04_actual(root=root, config=config, audit=audit)
        h00 = deep_validate_h00_approval(root=root, config=config, audit=audit)
        h04 = deep_validate_h04_actual_report(root=root, config=config, audit=audit)
        bindings = {
            "s108_commit_sha256": s108.sha256,
            "s203_commit_sha256": s203.commit_snapshot.sha256,
            "r04_commit_sha256": r04.commit_snapshot.sha256,
            "h00_approval_sha256": h00.snapshot.sha256,
            "h04_report_sha256": h04.snapshot.sha256,
        }
        preregistration = validate_frozen_preregistration(
            root=root,
            config=config,
            expected_bindings=bindings,
            audit=audit,
        )
        authorization = validate_human_authorization(
            root=root,
            config=config,
            config_sha256=static.config_sha256,
            preregistration=preregistration,
            expected_bindings=bindings,
            audit=audit,
        )
        for snapshot in static.source_snapshots:
            _verify_snapshot(root, snapshot, f"R05 source {snapshot.path}")
            audit.source_pin_verify_count += 1
        decision = evaluate_aggregate_decision(
            root=root,
            config=config,
            static=static,
            s108_snapshot=s108,
            s203=s203,
            r04=r04,
            h00=h00,
            h04=h04,
            preregistration=preregistration,
            authorization=authorization,
            expected_bindings=bindings,
            audit=audit,
        )
    except R05ContractError as exc:
        if exc.audit is None:
            exc.audit = audit
        raise
    return PreflightResult(
        status="decision_evaluated_read_only",
        config_sha256=static.config_sha256,
        decision=decision,
        audit={
            key: int(value)
            for key, value in asdict(audit).items()
            if isinstance(value, int)
        },
    )


def _output_target(root: Path, relative: str, name: str) -> Path:
    normalized = _relative_string(relative, f"{name} path")
    path = root
    for part in PurePosixPath(normalized).parts:
        path = path / part
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return root / normalized
        if stat.S_ISLNK(metadata.st_mode):
            raise R05ContractError(f"{name} cannot traverse a symlink")
    return path


def ensure_output_targets_absent(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> None:
    """Enforce no-overwrite for a separate, future atomic publisher."""

    root = workspace_root.resolve(strict=True)
    outputs = _mapping(config.get("outputs"), "outputs")
    for field in (
        "decision_report_path",
        "commit_path",
        "registry_path",
        "failure_report_path",
    ):
        relative = _relative_string(outputs.get(field), f"output {field}")
        target = _output_target(root, relative, f"output {field}")
        if target.exists():
            raise R05ContractError(f"output {field} already exists")


def validate_completed_decision_bundle(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    before_final_verify_hook: Callable[[], None] | None = None,
) -> str:
    """Deep-validate a future completed R05 bundle atomically and read-only."""

    root = workspace_root.resolve(strict=True)
    static = validate_static_config(
        workspace_root=root,
        config_path=config_path,
        config=config,
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    commit_path = _relative_string(outputs.get("commit_path"), "R05 commit path")
    report_path = _relative_string(
        outputs.get("decision_report_path"),
        "R05 decision report path",
    )
    registry_path = _relative_string(outputs.get("registry_path"), "R05 registry path")
    commit_data, commit_snapshot = _read_file(root, commit_path, "R05 decision commit")
    commit = _parse_json(commit_data, "R05 decision commit")
    _expect_keys(
        commit,
        {
            "schema_version",
            "status",
            "config_sha256",
            "preregistration_sha256",
            "human_authorization_sha256",
            "overall_decision",
            "decision_report",
            "registry",
            "binding_sha256",
        },
        "R05 decision commit",
    )
    _expect(
        commit.get("schema_version"),
        "voice_coach.r05_grpo_decision_commit.v1",
        "R05 commit schema",
    )
    _expect(commit.get("status"), "committed", "R05 commit status")
    _expect(commit.get("config_sha256"), static.config_sha256, "R05 config binding")
    _expect(
        _sha256(commit.get("binding_sha256"), "R05 commit binding"),
        _binding_sha256(commit),
        "R05 commit binding",
    )
    report_record = _mapping(commit.get("decision_report"), "commit report record")
    registry_record = _mapping(commit.get("registry"), "commit registry record")
    _expect(report_record.get("path"), report_path, "commit report path")
    _expect(registry_record.get("path"), registry_path, "commit registry path")
    report_sha = _sha256(report_record.get("sha256"), "commit report SHA")
    registry_sha = _sha256(registry_record.get("sha256"), "commit registry SHA")

    report_data, report_snapshot = _read_file(root, report_path, "R05 decision report")
    _expect(report_snapshot.sha256, report_sha, "R05 decision report SHA")
    report = _parse_json(report_data, "R05 decision report")
    bindings = _mapping(report.get("evidence_bindings"), "decision evidence bindings")
    decision = validate_decision_report_payload(
        report,
        expected_config_sha256=static.config_sha256,
        expected_preregistration_sha256=_sha256(
            commit.get("preregistration_sha256"),
            "commit preregistration SHA",
        ),
        expected_authorization_sha256=_sha256(
            commit.get("human_authorization_sha256"),
            "commit authorization SHA",
        ),
        expected_evidence_bindings={
            key: _sha256(bindings.get(key), f"report evidence binding {key}")
            for key in EVIDENCE_BINDING_KEYS
        },
    )
    _expect(commit.get("overall_decision"), decision, "commit overall decision")

    registry_data, registry_snapshot = _read_file(
        root,
        registry_path,
        "R05 completed registry",
    )
    _expect(registry_snapshot.sha256, registry_sha, "R05 registry SHA")
    registry = _parse_json(registry_data, "R05 completed registry")
    _expect_keys(
        registry,
        {
            "schema_version",
            "run_id",
            "status",
            "config_sha256",
            "artifacts",
            "binding_sha256",
        },
        "R05 completed registry",
    )
    _expect(
        registry.get("schema_version"),
        "voice_coach.r05_grpo_decision_registry.v1",
        "R05 registry schema",
    )
    _expect(registry.get("run_id"), "r05-grpo-go-no-go-v1", "R05 registry run ID")
    _expect(registry.get("status"), "completed", "R05 registry status")
    _expect(registry.get("config_sha256"), static.config_sha256, "registry config")
    _expect(
        _sha256(registry.get("binding_sha256"), "R05 registry binding"),
        _binding_sha256(registry),
        "R05 registry binding",
    )
    artifacts = _list(registry.get("artifacts"), "R05 registry artifacts")
    expected_artifacts = [
        {
            "name": "decision_report",
            "path": report_path,
            "sha256": report_sha,
            "schema_version": "voice_coach.r05_grpo_decision_report.v1",
        }
    ]
    _expect(artifacts, expected_artifacts, "R05 registry artifacts")

    if before_final_verify_hook is not None:
        before_final_verify_hook()
    for snapshot, label in (
        (commit_snapshot, "commit"),
        (report_snapshot, "decision report"),
        (registry_snapshot, "registry"),
        *[(item, f"source {item.path}") for item in static.source_snapshots],
    ):
        _verify_snapshot(root, snapshot, f"R05 completed bundle {label}")
    return decision


def _audit_dict(audit: AccessAudit) -> dict[str, int]:
    return {
        key: int(value)
        for key, value in asdict(audit).items()
        if isinstance(value, int)
    }


def config_only_result(
    static: StaticValidation,
    audit: AccessAudit,
) -> Mapping[str, Any]:
    return {
        "status": "valid_config_only",
        "config_sha256": static.config_sha256,
        "policy_sha256": static.policy_sha256,
        "source_pin_count": len(static.source_snapshots),
        **_audit_dict(audit),
        "actual_training_download_model_gpu_reward_execution": False,
        "actual_metric_or_effect_estimation": False,
        "decision_report_created": False,
        "grpo_entry_authorized": False,
        "r05_completion_claim_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: R05ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **_audit_dict(audit),
        "actual_training_download_model_gpu_reward_execution": False,
        "actual_metric_or_effect_estimation": False,
        "decision_report_created": False,
        "grpo_entry_authorized": False,
        "r05_completion_claim_allowed": False,
        "publication_allowed": False,
    }
