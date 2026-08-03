"""Fail-closed production-static contract for the WP8 E00 closed-loop demo.

This module deliberately does not record audio, run a model, invoke an API,
deliver coaching or a reference, compute a comparison, or write an artifact.
It validates immutable public/static policy first and stops before the first
unresolved predecessor path.  Pure validators describe the future restricted
executor handoff without turning synthetic fixtures into research evidence.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml  # type: ignore[import-untyped]

CONFIG_SCHEMA_VERSION = 1
TBD_PREFIX = "TBD_"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")

PREREG_SCHEMA_VERSION = "voice_coach.e00_closed_loop_preregistration.v1"
AUTHORIZATION_SCHEMA_VERSION = "voice_coach.e00_execution_authorization.v1"
REFERENCE_SCHEMA_VERSION = "voice_coach.e00_reference_handoff.v1"
LINEAGE_SCHEMA_VERSION = "voice_coach.e00_d08_r03_lineage_receipt.v1"
SESSION_SCHEMA_VERSION = "voice_coach.e00_closed_loop_session.v1"
TRACE_SCHEMA_VERSION = "voice_coach.e00_closed_loop_trace.v1"
COMPLETION_SCHEMA_VERSION = "voice_coach.e00_closed_loop_completion.v1"
S108_HANDOFF_SCHEMA_VERSION = "voice_coach.e00_s108_diagnostic_handoff.v1"

EXECUTION_MODES = {"human_authorized_demo", "nonhuman_test_fixture"}
BLOCK_REASONS = {
    "out_of_domain",
    "low_confidence",
    "unsupported",
    "abstain_all",
}
SAFE_NEXT_ACTIONS = {"rerecord", "expert_review"}
EVENT_ORDER = (
    "attempt_1",
    "s108_diagnostic_1",
    "e02_guard",
    "c02_top1_coaching",
    "r03_d08_reference",
    "attempt_2",
    "same_scorer_comparison",
)

EXPECTED_SOURCE_PATHS = {
    "configs/demo/e00_closed_loop_completion_v1.schema.json",
    "configs/demo/e00_closed_loop_preregistration_v1.template.json",
    "configs/demo/e00_closed_loop_session_v1.schema.json",
    "configs/demo/e00_closed_loop_trace_v1.schema.json",
    "configs/demo/e00_execution_authorization_v1.schema.json",
    "configs/demo/e00_execution_authorization_v1.template.json",
    "configs/demo/e00_d08_r03_lineage_receipt_v1.schema.json",
    "configs/demo/e00_reference_handoff_v1.schema.json",
    "configs/demo/e00_s108_diagnostic_handoff_v1.schema.json",
    "scripts/prepare_e00_closed_loop_demo.py",
    "src/voice_coach/evaluation/e00_closed_loop_contract.py",
    "configs/experiments/wavlm_s108_no_locked_dry_run_v1.yaml",
    "configs/experiments/e01_metamorphic_robustness_v1.yaml",
    "src/voice_coach/evaluation/e01_metamorphic_contract.py",
    "configs/experiments/c02_deterministic_coaching_selection_v1.yaml",
    "src/voice_coach/coaching/c02_selection_contract.py",
    "configs/coaching/e02_c02_coaching_bundle_commit_v1.schema.json",
    "configs/experiments/c03_expert_coaching_audit_v1.yaml",
    "configs/coaching/c03_expert_audit_report_v1.schema.json",
    "src/voice_coach/coaching/c03_expert_audit_contract.py",
    "configs/experiments/e02_confidence_guard_v1.yaml",
    "configs/coaching/e02_confidence_guard_input_v1.schema.json",
    "configs/coaching/e02_confidence_guard_api_response_v1.schema.json",
    "src/voice_coach/evaluation/e02_confidence_guard_contract.py",
    "configs/experiments/r03_reference_bank_selection_v1.yaml",
    "configs/reference/r03_reference_bank_manifest_v1.schema.json",
    "src/voice_coach/references/r03_selection_contract.py",
    "configs/experiments/d08_native_multispeaker_reference_v1.yaml",
    "configs/reference/d08_completion_v1.schema.json",
    "src/voice_coach/references/d08_native_reference_contract.py",
    "configs/experiments/h00_human_research_approval_v1.yaml",
    "src/voice_coach/human_research/h00_approval_contract.py",
    "docs/IMPLEMENTATION_PLAN_KO.md",
    "docs/HIGH_PERFORMANCE_GPU_EXECUTION_PLAN_KO.md",
    "docs/LEARNING_EFFECT_PREREGISTRATION_KO.md",
    "docs/DATA_PRIVACY_BOUNDARY_KO.md",
    "docs/architecture/architecture.md",
    "docs/architecture/component.md",
    "docs/architecture/directory.md",
    "docs/architecture/flow.md",
    "docs/architecture/state.md",
    "docs/api/endpoints.md",
    "docs/api/specification.md",
    "docs/WAVLM_S108_NO_LOCKED_DRY_RUN_READINESS_KO.md",
    "docs/C02_DETERMINISTIC_COACHING_SELECTION_READINESS_KO.md",
    "docs/C03_EXPERT_COACHING_AUDIT_READINESS_KO.md",
    "docs/E01_METAMORPHIC_READINESS_KO.md",
    "docs/E02_CONFIDENCE_GUARD_READINESS_KO.md",
    "docs/R03_REFERENCE_BANK_SELECTION_READINESS_KO.md",
    "docs/D08_NATIVE_MULTI_SPEAKER_REFERENCE_READINESS_KO.md",
}

SCHEMA_PATHS = {
    "configs/demo/e00_closed_loop_completion_v1.schema.json": (
        COMPLETION_SCHEMA_VERSION
    ),
    "configs/demo/e00_closed_loop_session_v1.schema.json": SESSION_SCHEMA_VERSION,
    "configs/demo/e00_closed_loop_trace_v1.schema.json": TRACE_SCHEMA_VERSION,
    "configs/demo/e00_execution_authorization_v1.schema.json": (
        AUTHORIZATION_SCHEMA_VERSION
    ),
    "configs/demo/e00_d08_r03_lineage_receipt_v1.schema.json": (LINEAGE_SCHEMA_VERSION),
    "configs/demo/e00_reference_handoff_v1.schema.json": REFERENCE_SCHEMA_VERSION,
    "configs/demo/e00_s108_diagnostic_handoff_v1.schema.json": (
        S108_HANDOFF_SCHEMA_VERSION
    ),
}

ROOT_CONFIG_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessors",
    "execution_preregistration",
    "execution_authorization",
    "reference_handoff",
    "data_contract",
    "flow_contract",
    "privacy_and_safety",
    "human_governance",
    "execution_order",
    "outputs",
    "runtime",
}

EXPERIMENT_KEYS = {
    "id",
    "stage",
    "implementation_status",
    "actual_input_count",
    "actual_audio_read_count",
    "actual_model_inference_count",
    "actual_api_count",
    "actual_coaching_delivery_count",
    "actual_reference_delivery_count",
    "actual_rerecord_count",
    "actual_metric_count",
    "actual_output_count",
    "completion_claim_allowed",
    "production_deployment_claim_allowed",
}

PREREG_KEYS = {
    "schema_version",
    "preregistration_id",
    "status",
    "execution_mode",
    "frozen_before_session_or_audio_access",
    "predecessor_bindings",
    "execution_population",
    "closed_loop_contract",
    "blocked_policy",
    "comparison",
    "claims",
    "privacy_and_safety",
    "approved_by_protocol_owner",
    "approved_by_privacy_owner",
    "approved_by_statistics_owner",
    "binding_sha256",
}

AUTHORIZATION_KEYS = {
    "schema_version",
    "authorization_id",
    "status",
    "execution_mode",
    "preregistration_sha256",
    "human_authorization",
    "fixture_authorization",
    "privacy_zone",
    "contains_direct_identifiers",
    "contains_audio_or_locators",
    "contains_free_text",
    "binding_sha256",
}

REFERENCE_KEYS = {
    "schema_version",
    "handoff_id",
    "status",
    "target_text_sha256",
    "selected_candidate_row_sha256",
    "r03_bank_manifest_sha256",
    "d08_completion_sha256",
    "d08_recording_rights_attestation_sha256",
    "source_rights_attestation_sha256",
    "selected_audio_object_id_sha256",
    "selected_audio_sha256",
    "d08_r03_lineage_receipt_sha256",
    "source_kind",
    "licensed_for_closed_loop_demo",
    "r03_row_lineage_verified",
    "d08_rights_binding_verified",
    "exact_candidate_audio_rights_cross_match_verified",
    "audio_locator_embedded",
    "speaker_identity_embedded",
    "source_condition_exposed_to_learner",
    "score_payload_embedded",
    "privacy_zone",
    "binding_sha256",
}

LINEAGE_KEYS = {
    "schema_version",
    "receipt_id",
    "status",
    "r03_bank_manifest_sha256",
    "selected_candidate_row_sha256",
    "target_text_sha256",
    "selected_audio_object_id_sha256",
    "selected_audio_sha256",
    "source_audio_sha256",
    "d08_completion_sha256",
    "d08_metadata_manifest_sha256",
    "d08_recording_metadata_row_sha256",
    "d08_recording_audio_sha256",
    "d08_recording_rights_attestation_sha256",
    "source_rights_attestation_sha256",
    "d08_recording_license_grant_sha256",
    "source_recording_license_grant_sha256",
    "candidate_audio_matches_d08_recording",
    "candidate_rights_match_d08_rights",
    "independent_r01_human_assumed_to_be_d08",
    "audio_locator_embedded",
    "speaker_identity_embedded",
    "privacy_zone",
    "binding_sha256",
}

SESSION_KEYS = {
    "schema_version",
    "session_id",
    "execution_mode",
    "privacy_zone",
    "target_text_sha256",
    "authorization_sha256",
    "preregistration_sha256",
    "s108_completion_commit_sha256",
    "s108_production_handoff_sha256",
    "e01_completion_commit_sha256",
    "c02_bundle_commit_sha256",
    "c03_report_sha256",
    "e02_preregistration_sha256",
    "r03_bank_manifest_sha256",
    "d08_completion_sha256",
    "reference_handoff_sha256",
    "attempt_1",
    "attempt_2",
    "same_scorer_contract",
    "contains_direct_identifiers",
    "contains_plain_target_or_transcript",
    "contains_audio_bytes_or_paths",
    "contains_numeric_model_scores",
    "contains_locked_result_copy",
    "contains_free_text",
    "user_or_participant_data_fabricated",
    "binding_sha256",
}

TRACE_KEYS = {
    "schema_version",
    "trace_id",
    "status",
    "execution_mode",
    "privacy_zone",
    "session_manifest_sha256",
    "event_order",
    "attempt_1",
    "diagnostic",
    "guard",
    "coaching",
    "reference",
    "attempt_2",
    "comparison",
    "safety",
    "claims",
    "binding_sha256",
}

COMPLETION_KEYS = {
    "schema_version",
    "completion_id",
    "status",
    "config_sha256",
    "preregistration_sha256",
    "authorization_sha256",
    "predecessor_chain_sha256",
    "execution_mode",
    "privacy_zone",
    "trace_count",
    "eligible_completed_loop_count",
    "blocked_safe_exit_count",
    "no_correction_safe_exit_count",
    "same_scorer_comparison_count",
    "traces_sha256",
    "safety_counts",
    "actual_execution",
    "claims",
    "contains_rows_or_pseudonymous_ids",
    "contains_audio_or_locator_hashes",
    "contains_numeric_scores_thresholds_or_effects",
    "contains_source_identity_or_condition",
    "contains_free_text",
    "completion_claim_allowed",
    "publication_allowed",
    "implementation_git_commit",
    "binding_sha256",
}

S108_HANDOFF_KEYS = {
    "schema_version",
    "handoff_id",
    "status",
    "attempt_id_sha256",
    "audio_content_sha256",
    "s108_completion_commit_sha256",
    "model_identity_sha256",
    "checkpoint_sha256",
    "calibrator_sha256",
    "threshold_policy_sha256",
    "production_inference_receipt_sha256",
    "diagnostic_issue_manifest_sha256",
    "input_role",
    "actual_production_inference_executed",
    "locked_test_row_audio_prediction_or_result_copied",
    "locked_result_replay_used",
    "score_or_threshold_embedded",
    "source_split_or_condition_embedded",
    "raw_audio_or_locator_embedded",
    "eligible_for_e02_guard",
    "privacy_zone",
    "binding_sha256",
}


class E00ContractError(ValueError):
    """Fail-closed contract error with the stage and access audit."""

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
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise E00ContractError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(slots=True)
class AccessAudit:
    source_pin_read_count: int = 0
    source_toctou_verification_count: int = 0
    predecessor_module_load_count: int = 0
    s108_path_resolution_count: int = 0
    s108_read_attempt_count: int = 0
    s108_read_count: int = 0
    s108_deep_validation_count: int = 0
    e01_path_resolution_count: int = 0
    e01_read_attempt_count: int = 0
    e01_read_count: int = 0
    e01_deep_validation_count: int = 0
    c02_path_resolution_count: int = 0
    c02_read_attempt_count: int = 0
    c02_read_count: int = 0
    c02_deep_validation_count: int = 0
    c03_path_resolution_count: int = 0
    c03_read_attempt_count: int = 0
    c03_read_count: int = 0
    c03_deep_validation_count: int = 0
    e02_path_resolution_count: int = 0
    e02_read_attempt_count: int = 0
    e02_read_count: int = 0
    e02_deep_validation_count: int = 0
    r03_path_resolution_count: int = 0
    r03_read_attempt_count: int = 0
    r03_read_count: int = 0
    r03_deep_validation_count: int = 0
    d08_path_resolution_count: int = 0
    d08_read_attempt_count: int = 0
    d08_read_count: int = 0
    d08_deep_validation_count: int = 0
    preregistration_path_resolution_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    h00_module_load_count: int = 0
    h00_approval_path_resolution_count: int = 0
    h00_approval_read_count: int = 0
    h00_attestation_path_resolution_count: int = 0
    h00_attestation_read_count: int = 0
    h00_restricted_document_path_resolution_count: int = 0
    h00_restricted_document_read_count: int = 0
    authorization_path_resolution_count: int = 0
    authorization_read_attempt_count: int = 0
    authorization_read_count: int = 0
    lineage_receipt_path_resolution_count: int = 0
    lineage_receipt_read_attempt_count: int = 0
    lineage_receipt_read_count: int = 0
    reference_handoff_path_resolution_count: int = 0
    reference_handoff_read_attempt_count: int = 0
    reference_handoff_read_count: int = 0
    session_path_resolution_count: int = 0
    session_manifest_read_count: int = 0
    input_count: int = 0
    raw_audio_read_count: int = 0
    microphone_access_count: int = 0
    model_access_count: int = 0
    model_inference_count: int = 0
    gpu_access_count: int = 0
    network_access_count: int = 0
    api_invocation_count: int = 0
    coaching_delivery_count: int = 0
    reference_delivery_count: int = 0
    rerecord_count: int = 0
    metric_count: int = 0
    result_count: int = 0
    output_path_resolution_count: int = 0
    output_write_count: int = 0
    registry_write_count: int = 0
    commit_write_count: int = 0
    publication_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return {key: int(value) for key, value in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    sha256: str
    size: int
    device: int
    inode: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class StaticValidation:
    config_sha256: str
    policy_sha256: str
    source_snapshots: tuple[FileSnapshot, ...]
    source_payloads: Mapping[str, bytes]


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: str
    config_sha256: str
    execution_mode: str
    preregistration_sha256: str
    authorization_sha256: str
    reference_handoff_sha256: str
    predecessor_chain_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise E00ContractError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise E00ContractError(f"{name} must be an array")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise E00ContractError(f"{name} must be a non-empty string")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise E00ContractError(f"{name} must be boolean")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise E00ContractError(f"{name} must be an integer >= {minimum}")
    return value


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if SHA256_RE.fullmatch(text) is None:
        raise E00ContractError(f"{name} must be lowercase SHA-256")
    return text


def _safe_id(value: object, name: str, *, pattern: str | None = None) -> str:
    text = _string(value, name)
    regex = re.compile(pattern) if pattern is not None else SAFE_ID_RE
    if regex.fullmatch(text) is None:
        raise E00ContractError(f"{name} has invalid format")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise E00ContractError(f"{name}: expected {expected!r}, got {actual!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise E00ContractError(
            f"{name} keys mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise E00ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_nonfinite_tree(child, f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_nonfinite_tree(child, f"{name}[{index}]")


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise E00ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise E00ContractError(f"invalid JSON numeric constant: {value}")


def _parse_json(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise E00ContractError(f"{name} is not strict UTF-8 JSON: {exc}") from exc
    result = _mapping(value, name)
    _reject_nonfinite_tree(result, name)
    return result


def canonical_json_bytes(
    value: Mapping[str, Any],
    *,
    exclude_binding: bool = False,
) -> bytes:
    payload = dict(value)
    if exclude_binding:
        payload.pop("binding_sha256", None)
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def binding_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value, exclude_binding=True)).hexdigest()


def _contains_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith(TBD_PREFIX) or TBD_PREFIX in value
    if isinstance(value, Mapping):
        return any(_contains_tbd(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tbd(item) for item in value)
    return False


def _collect_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str):
                result.add(key.lower())
            result.update(_collect_keys(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_collect_keys(child))
    return result


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    if "\\" in text or "\x00" in text:
        raise E00ContractError(f"{name} must use a normalized POSIX path")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise E00ContractError(f"{name} must be workspace-relative")
    normalized = path.as_posix()
    if normalized != text:
        raise E00ContractError(f"{name} is not normalized")
    return normalized


def _reject_symlink_chain(
    root: Path,
    relative: str,
    name: str,
    *,
    require_leaf: bool,
) -> Path:
    normalized = _relative_string(relative, name)
    current = root
    parts = PurePosixPath(normalized).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if require_leaf or index < len(parts) - 1:
                raise E00ContractError(f"{name} does not exist: {normalized}") from None
            return current
        if stat.S_ISLNK(mode):
            raise E00ContractError(f"{name} crosses a symlink: {normalized}")
    return current


def _read_file(root: Path, relative: str, name: str) -> tuple[bytes, FileSnapshot]:
    path = _reject_symlink_chain(root, relative, name, require_leaf=True)
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise E00ContractError(f"{name} must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_mtime_ns != before.st_mtime_ns
        ):
            raise E00ContractError(f"{name} changed before read")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        after.st_dev != opened.st_dev
        or after.st_ino != opened.st_ino
        or after.st_size != opened.st_size
        or after.st_mtime_ns != opened.st_mtime_ns
    ):
        raise E00ContractError(f"{name} changed during read")
    data = b"".join(chunks)
    if len(data) != opened.st_size:
        raise E00ContractError(f"{name} short read")
    snapshot = FileSnapshot(
        path=relative,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        device=int(opened.st_dev),
        inode=int(opened.st_ino),
        mtime_ns=int(opened.st_mtime_ns),
    )
    return data, snapshot


def _verify_snapshot(root: Path, snapshot: FileSnapshot, name: str) -> None:
    _, observed = _read_file(root, snapshot.path, name)
    if observed != snapshot:
        raise E00ContractError(f"{name} changed during preflight")


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise E00ContractError(f"cannot load E00 config: {exc}") from exc
    config = _mapping(value, "E00 config")
    _reject_nonfinite_tree(config, "E00 config")
    return config


def _schema_object_nodes(value: Any, name: str) -> None:
    if isinstance(value, Mapping):
        if value.get("type") == "object":
            _expect(value.get("additionalProperties"), False, f"{name} strictness")
            properties = _mapping(value.get("properties"), f"{name} properties")
            required = set(_list(value.get("required"), f"{name} required"))
            if required != set(properties):
                raise E00ContractError(
                    f"{name} required/properties mismatch: "
                    f"required={sorted(required)}, properties={sorted(properties)}"
                )
        for key, child in value.items():
            _schema_object_nodes(child, f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _schema_object_nodes(child, f"{name}[{index}]")


def _validate_schema(
    payload: Mapping[str, Any],
    *,
    version: str,
    name: str,
) -> None:
    _expect(
        payload.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        f"{name} dialect",
    )
    _expect(payload.get("type"), "object", f"{name} root type")
    _expect(payload.get("additionalProperties"), False, f"{name} root strictness")
    properties = _mapping(payload.get("properties"), f"{name} properties")
    version_schema = _mapping(
        properties.get("schema_version"),
        f"{name} schema_version",
    )
    _expect(version_schema.get("const"), version, f"{name} version")
    _schema_object_nodes(payload, name)


def _validate_preregistration_template(payload: Mapping[str, Any]) -> None:
    _expect_keys(payload, PREREG_KEYS, "E00 preregistration template")
    _expect(
        payload.get("schema_version"),
        PREREG_SCHEMA_VERSION,
        "preregistration template schema",
    )
    _expect(payload.get("status"), "draft_not_approved", "prereg template status")
    if not _contains_tbd(payload):
        raise E00ContractError("preregistration template must retain unresolved TBD")
    closed = _mapping(payload.get("closed_loop_contract"), "template closed loop")
    _expect(closed.get("attempt_count_per_eligible_loop"), 2, "template attempts")
    _expect(closed.get("s108_actual_production_handoff_required"), True, "S108")
    _expect(closed.get("locked_result_copy_or_replay_allowed"), False, "locked")
    _expect(closed.get("e02_guard_before_any_delivery_required"), True, "E02 order")
    _expect(closed.get("c02_selected_count"), 1, "C02 selected count")
    for field in (
        "catalog_only",
        "r03_d08_licensed_reference_required",
        "same_scorer_pre_post_required",
    ):
        _expect(closed.get(field), True, f"template {field}")
    for field in (
        "free_generation_allowed",
        "result_based_scorer_change_allowed",
    ):
        _expect(closed.get(field), False, f"template {field}")
    claims = _mapping(payload.get("claims"), "template claims")
    _expect(claims.get("noninferential_demo_only"), True, "template noninferential")
    for field in (
        "pedagogical_effect_claim_allowed",
        "clinical_claim_allowed",
        "production_deployment_claim_allowed",
    ):
        _expect(claims.get(field), False, f"template claims.{field}")


def _validate_authorization_template(payload: Mapping[str, Any]) -> None:
    _expect_keys(payload, AUTHORIZATION_KEYS, "authorization template")
    _expect(
        payload.get("schema_version"),
        AUTHORIZATION_SCHEMA_VERSION,
        "authorization template schema",
    )
    _expect(payload.get("status"), "draft_not_active", "authorization template")
    if not _contains_tbd(payload):
        raise E00ContractError("authorization template must retain unresolved TBD")
    fixture = _mapping(payload.get("fixture_authorization"), "fixture template")
    _expect(
        fixture.get("scope"),
        "learner_attempt_input_fixture_only",
        "fixture template scope",
    )
    _expect(
        fixture.get("licensed_d08_human_reference_outside_fixture_scope"),
        True,
        "fixture/reference scope separation",
    )
    for field in (
        "contains_human_voice",
        "contains_participant_or_user_data",
        "represents_a_real_learner",
        "human_user_data_fabricated",
        "synthetic_or_nonhuman_label_explicit",
        "pedagogical_or_clinical_claim_allowed",
    ):
        _expect(fixture.get(field), False, f"fixture {field}")
    for field in (
        "contains_direct_identifiers",
        "contains_audio_or_locators",
        "contains_free_text",
    ):
        _expect(payload.get(field), False, f"authorization template {field}")


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, ROOT_CONFIG_KEYS, "E00 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "config schema")
    experiment = _mapping(config.get("experiment"), "experiment")
    _expect_keys(experiment, EXPERIMENT_KEYS, "experiment")
    _expect(experiment.get("id"), "e00_closed_loop_demo_v1", "experiment ID")
    _expect(experiment.get("stage"), "wp8_e00_closed_loop_demo", "stage")
    _expect(
        experiment.get("implementation_status"),
        "production_static_readiness_only",
        "implementation status",
    )
    for field in (
        "actual_input_count",
        "actual_audio_read_count",
        "actual_model_inference_count",
        "actual_api_count",
        "actual_coaching_delivery_count",
        "actual_reference_delivery_count",
        "actual_rerecord_count",
        "actual_metric_count",
        "actual_output_count",
    ):
        _expect(experiment.get(field), 0, f"experiment.{field}")
    for field in (
        "completion_claim_allowed",
        "production_deployment_claim_allowed",
    ):
        _expect(experiment.get(field), False, f"experiment.{field}")

    canonical = _mapping(config.get("canonical_sources"), "canonical_sources")
    _expect_keys(canonical, {"access_scope", "pins"}, "canonical_sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_static_contracts_only",
        "source access scope",
    )
    pins = _list(canonical.get("pins"), "source pins")
    seen: set[str] = set()
    for index, raw in enumerate(pins):
        pin = _mapping(raw, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        if relative in seen:
            raise E00ContractError(f"duplicate source pin: {relative}")
        seen.add(relative)
        _sha256(pin.get("sha256"), f"source pin {index} SHA")
    if seen != EXPECTED_SOURCE_PATHS:
        raise E00ContractError(
            "source path set mismatch: "
            f"missing={sorted(EXPECTED_SOURCE_PATHS - seen)}, "
            f"extra={sorted(seen - EXPECTED_SOURCE_PATHS)}"
        )

    predecessors = _mapping(config.get("predecessors"), "predecessors")
    expected_stages = {"s108", "e01", "c02_e02", "c03", "r03", "d08"}
    _expect(set(predecessors), expected_stages, "predecessor stages")
    expected_order = {
        "s108": 1,
        "e01": 2,
        "c02_e02": 3,
        "c03": 4,
        "r03": 5,
        "d08": 6,
    }
    for stage, order in expected_order.items():
        section = _mapping(predecessors.get(stage), f"predecessor {stage}")
        _expect(section.get("validation_order"), order, f"{stage} order")
        for key, value in section.items():
            if key.endswith("_path"):
                _relative_string(value, f"{stage}.{key}")
            elif key.endswith("_sha256"):
                text = _string(value, f"{stage}.{key}")
                if not text.startswith(TBD_PREFIX):
                    _sha256(text, f"{stage}.{key}")
    _expect(
        _mapping(predecessors["s108"], "S108").get("required_decision"),
        "research_candidate_passed",
        "S108 decision",
    )
    _expect(
        _mapping(predecessors["e01"], "E01").get("required_decision"),
        "passed",
        "E01 decision",
    )
    _expect(
        _mapping(predecessors["c03"], "C03").get("required_decision"),
        "passed",
        "C03 decision",
    )
    _expect(
        _mapping(predecessors["r03"], "R03").get("required_bank_status"),
        "r03_selected_pending_r04",
        "R03 bank status",
    )
    _expect(
        _mapping(predecessors["d08"], "D08").get("required_status"),
        "completed",
        "D08 status",
    )

    prereg = _mapping(
        config.get("execution_preregistration"),
        "execution preregistration",
    )
    _expect(
        prereg.get("required_schema_version"),
        PREREG_SCHEMA_VERSION,
        "prereg schema",
    )
    _expect(prereg.get("required_status"), "approved_and_frozen", "prereg status")
    for field in ("template_path", "approved_artifact_path"):
        _relative_string(prereg.get(field), f"prereg.{field}")
    for field in ("template_sha256", "approved_artifact_sha256"):
        text = _string(prereg.get(field), f"prereg.{field}")
        if not text.startswith(TBD_PREFIX):
            _sha256(text, f"prereg.{field}")

    authorization = _mapping(
        config.get("execution_authorization"),
        "execution authorization",
    )
    _expect(
        authorization.get("required_schema_version"),
        AUTHORIZATION_SCHEMA_VERSION,
        "authorization schema",
    )
    _expect(
        authorization.get("required_status"),
        "active_authorization",
        "authorization status",
    )
    for field in ("schema_path", "template_path", "artifact_path"):
        _relative_string(authorization.get(field), f"authorization.{field}")
    for field in (
        "schema_sha256",
        "template_sha256",
        "artifact_sha256",
    ):
        text = _string(authorization.get(field), f"authorization.{field}")
        if not text.startswith(TBD_PREFIX):
            _sha256(text, f"authorization.{field}")

    reference = _mapping(config.get("reference_handoff"), "reference handoff")
    _expect(
        reference.get("required_schema_version"),
        REFERENCE_SCHEMA_VERSION,
        "reference schema",
    )
    _expect(
        reference.get("required_status"),
        "authorized_for_single_demo_handoff",
        "reference status",
    )
    _expect(
        reference.get("required_lineage_schema_version"),
        LINEAGE_SCHEMA_VERSION,
        "lineage schema",
    )
    _expect(
        reference.get("exact_candidate_audio_rights_cross_match_required"),
        True,
        "exact D08/R03 cross-match",
    )
    _expect(
        reference.get("independent_r01_human_assumed_to_be_d08_allowed"),
        False,
        "independent R01/D08 assumption",
    )
    for field in (
        "schema_path",
        "artifact_path",
        "lineage_schema_path",
        "lineage_receipt_path",
    ):
        _relative_string(reference.get(field), f"reference.{field}")
    for field in (
        "schema_sha256",
        "artifact_sha256",
        "lineage_schema_sha256",
        "lineage_receipt_sha256",
    ):
        text = _string(reference.get(field), f"reference.{field}")
        if not text.startswith(TBD_PREFIX):
            _sha256(text, f"reference.{field}")

    data_contract = _mapping(config.get("data_contract"), "data contract")
    schema_fields = {
        "session_schema_version": SESSION_SCHEMA_VERSION,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "completion_schema_version": COMPLETION_SCHEMA_VERSION,
        "s108_handoff_schema_version": S108_HANDOFF_SCHEMA_VERSION,
    }
    for field, expected in schema_fields.items():
        _expect(data_contract.get(field), expected, f"data_contract.{field}")
    for field, value in data_contract.items():
        if field.endswith("_path"):
            _relative_string(value, f"data_contract.{field}")
        elif field.endswith("_sha256"):
            text = _string(value, f"data_contract.{field}")
            if not text.startswith(TBD_PREFIX):
                _sha256(text, f"data_contract.{field}")

    flow = _mapping(config.get("flow_contract"), "flow contract")
    _expect(tuple(flow.get("event_order", [])), EVENT_ORDER, "event order")
    _expect(flow.get("attempt_count_per_eligible_loop"), 2, "attempt count")
    _expect(flow.get("c02_selected_count"), 1, "C02 selected count")
    for field in (
        "s108_actual_production_handoff_required",
        "e02_guard_before_delivery_required",
        "catalog_only_coaching_required",
        "licensed_r03_d08_reference_required",
        "same_scorer_pre_post_required",
    ):
        _expect(flow.get(field), True, f"flow.{field}")
    for field in (
        "locked_result_copy_or_replay_allowed",
        "blocked_delivery_override_allowed",
        "free_generation_allowed",
        "result_based_scorer_change_allowed",
    ):
        _expect(flow.get(field), False, f"flow.{field}")
    _expect(
        set(flow.get("blocked_reasons", [])),
        BLOCK_REASONS,
        "blocked reasons",
    )
    _expect(
        set(flow.get("safe_next_actions", [])),
        SAFE_NEXT_ACTIONS,
        "safe next actions",
    )

    privacy = _mapping(config.get("privacy_and_safety"), "privacy and safety")
    for field, value in privacy.items():
        if field.endswith("_allowed"):
            _expect(value, False, f"privacy.{field}")
    _expect(
        privacy.get("fixture_must_be_explicitly_nonhuman"),
        True,
        "fixture boundary",
    )
    _expect(
        privacy.get("human_mode_requires_h00_and_individual_consent"),
        True,
        "human boundary",
    )
    _expect(
        privacy.get("fixture_human_voice_false_scope"),
        "learner_attempt_input_fixture_only",
        "fixture voice scope",
    )
    _expect(
        privacy.get("d08_licensed_human_reference_is_separate"),
        True,
        "licensed reference scope",
    )

    governance = _mapping(config.get("human_governance"), "human governance")
    for field, value in governance.items():
        if field.endswith("_path"):
            _relative_string(value, f"human_governance.{field}")
        elif field.endswith("_sha256"):
            text = _string(value, f"human_governance.{field}")
            if not text.startswith(TBD_PREFIX):
                _sha256(text, f"human_governance.{field}")
    _expect(
        governance.get("h00_alone_is_not_individual_consent"),
        True,
        "H00 consent boundary",
    )

    execution_order = tuple(_list(config.get("execution_order"), "execution order"))
    _expect(
        execution_order,
        (
            "validate_static_sources_schemas_templates_and_zero_runtime",
            "deep_validate_actual_s108_final_before_any_demo_path",
            "deep_validate_e01_completed_pass",
            "deep_validate_c02_bundle_and_frozen_e02_guard",
            "deep_validate_c03_expert_audit_pass",
            "deep_validate_r03_private_bank",
            "validate_d08_aggregate_completion_and_rights",
            "validate_frozen_e00_preregistration",
            "branch_human_h00_consent_or_nonhuman_fixture_authorization",
            "validate_exact_d08_r03_lineage_receipt_then_reference_handoff",
            "stop_before_session_audio_model_api_delivery_rerecord_metric_or_output",
        ),
        "execution order",
    )

    outputs = _mapping(config.get("outputs"), "outputs")
    _expect(outputs.get("write_enabled_in_this_package"), False, "output write")
    _expect(outputs.get("no_overwrite"), True, "output no-overwrite")
    for field, value in outputs.items():
        if field.endswith("_path"):
            _relative_string(value, f"outputs.{field}")

    runtime = _mapping(config.get("runtime"), "runtime")
    for field, value in runtime.items():
        if field.endswith("_allowed"):
            _expect(value, False, f"runtime.{field}")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    root = workspace_root.resolve(strict=True)
    active_audit = audit if audit is not None else AccessAudit()
    _validate_config_values(config)
    canonical = _mapping(config.get("canonical_sources"), "canonical sources")
    snapshots: list[FileSnapshot] = []
    payloads: dict[str, bytes] = {}
    for index, raw in enumerate(_list(canonical.get("pins"), "source pins")):
        pin = _mapping(raw, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        active_audit.source_pin_read_count += 1
        _expect(
            snapshot.sha256,
            pin.get("sha256"),
            f"source pin {relative} SHA",
        )
        snapshots.append(snapshot)
        payloads[relative] = data

    for path, version in SCHEMA_PATHS.items():
        _validate_schema(_parse_json(payloads[path], path), version=version, name=path)
    _validate_preregistration_template(
        _parse_json(
            payloads["configs/demo/e00_closed_loop_preregistration_v1.template.json"],
            "E00 preregistration template",
        )
    )
    _validate_authorization_template(
        _parse_json(
            payloads["configs/demo/e00_execution_authorization_v1.template.json"],
            "E00 authorization template",
        )
    )

    try:
        config_relative = config_path.resolve(strict=True).relative_to(root).as_posix()
    except (OSError, ValueError) as exc:
        raise E00ContractError("config path must be inside the workspace") from exc
    config_data, config_snapshot = _read_file(root, config_relative, "E00 config")
    canonical_config = yaml.load(config_data.decode("utf-8"), Loader=_UniqueKeyLoader)
    _expect(canonical_config, config, "config reload")
    policy = dict(config)
    policy.pop("canonical_sources", None)
    policy_sha = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=policy_sha,
        source_snapshots=tuple(snapshots),
        source_payloads=payloads,
    )


def _require_binding(payload: Mapping[str, Any], name: str) -> None:
    _expect(
        _sha256(payload.get("binding_sha256"), f"{name} binding"),
        binding_sha256(payload),
        f"{name} binding",
    )


def _require_false_fields(
    payload: Mapping[str, Any],
    fields: Sequence[str],
    name: str,
) -> None:
    for field in fields:
        _expect(payload.get(field), False, f"{name}.{field}")


def _predecessor_expected_hashes(config: Mapping[str, Any]) -> dict[str, str]:
    predecessors = _mapping(config.get("predecessors"), "predecessors")
    result: dict[str, str] = {}
    fields = (
        ("s108_completion_commit_sha256", "s108", "completion_commit_sha256"),
        ("e01_completion_commit_sha256", "e01", "completion_commit_sha256"),
        ("c02_bundle_commit_sha256", "c02_e02", "c02_bundle_commit_sha256"),
        ("c03_report_sha256", "c03", "report_sha256"),
        ("e02_preregistration_sha256", "c02_e02", "e02_preregistration_sha256"),
        ("r03_bank_manifest_sha256", "r03", "bank_manifest_sha256"),
        ("d08_completion_sha256", "d08", "completion_sha256"),
    )
    for output, stage, field in fields:
        result[output] = _sha256(
            _mapping(predecessors.get(stage), stage).get(field),
            f"{stage}.{field}",
        )
    return result


def validate_preregistration_payload(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate a resolved noninferential E00 execution preregistration."""

    _reject_nonfinite_tree(payload, "E00 preregistration")
    _expect_keys(payload, PREREG_KEYS, "E00 preregistration")
    if _contains_tbd(payload):
        raise E00ContractError("E00 preregistration contains unresolved TBD")
    _expect(
        payload.get("schema_version"),
        PREREG_SCHEMA_VERSION,
        "preregistration schema",
    )
    _safe_id(
        payload.get("preregistration_id"),
        "preregistration ID",
        pattern=r"^e00prereg_[0-9a-f]{32}$",
    )
    _expect(
        payload.get("status"),
        "approved_and_frozen",
        "preregistration status",
    )
    mode = _string(payload.get("execution_mode"), "execution mode")
    if mode not in EXECUTION_MODES:
        raise E00ContractError("invalid E00 execution mode")
    _expect(
        payload.get("frozen_before_session_or_audio_access"),
        True,
        "preregistration freeze",
    )

    bindings = _mapping(
        payload.get("predecessor_bindings"),
        "prereg predecessor bindings",
    )
    expected_bindings = _predecessor_expected_hashes(config)
    _expect(set(bindings), set(expected_bindings), "prereg predecessor keys")
    for key, expected in expected_bindings.items():
        _expect(
            _sha256(bindings.get(key), f"prereg {key}"),
            expected,
            f"prereg {key}",
        )

    population = _mapping(payload.get("execution_population"), "population")
    _expect_keys(
        population,
        {
            "demo_trace_count",
            "target_sentence_selection_policy_sha256",
            "human_eligibility_policy_sha256",
            "fixture_provenance_policy_sha256",
            "human_and_fixture_results_mixed",
        },
        "execution population",
    )
    _integer(population.get("demo_trace_count"), "demo trace count", minimum=1)
    for field in (
        "target_sentence_selection_policy_sha256",
        "human_eligibility_policy_sha256",
        "fixture_provenance_policy_sha256",
    ):
        _sha256(population.get(field), f"population {field}")
    _expect(
        population.get("human_and_fixture_results_mixed"),
        False,
        "human/fixture mixing",
    )

    closed = _mapping(payload.get("closed_loop_contract"), "closed loop")
    _expect_keys(
        closed,
        {
            "attempt_count_per_eligible_loop",
            "s108_actual_production_handoff_required",
            "locked_result_copy_or_replay_allowed",
            "e02_guard_before_any_delivery_required",
            "c02_selected_count",
            "catalog_only",
            "free_generation_allowed",
            "r03_d08_licensed_reference_required",
            "same_scorer_pre_post_required",
            "result_based_scorer_change_allowed",
        },
        "closed loop contract",
    )
    _expect(closed.get("attempt_count_per_eligible_loop"), 2, "attempt count")
    _expect(closed.get("c02_selected_count"), 1, "top-1")
    for field in (
        "s108_actual_production_handoff_required",
        "e02_guard_before_any_delivery_required",
        "catalog_only",
        "r03_d08_licensed_reference_required",
        "same_scorer_pre_post_required",
    ):
        _expect(closed.get(field), True, f"closed loop {field}")
    for field in (
        "locked_result_copy_or_replay_allowed",
        "free_generation_allowed",
        "result_based_scorer_change_allowed",
    ):
        _expect(closed.get(field), False, f"closed loop {field}")

    blocked = _mapping(payload.get("blocked_policy"), "blocked policy")
    _expect_keys(
        blocked,
        {
            "blocked_reasons",
            "automatic_coaching_allowed",
            "automatic_reference_allowed",
            "automatic_corrective_action_allowed",
            "safe_next_actions",
            "reason_precedence_sha256",
            "reason_to_action_policy_sha256",
        },
        "blocked policy",
    )
    _expect(set(blocked.get("blocked_reasons", [])), BLOCK_REASONS, "block reasons")
    _expect(
        set(blocked.get("safe_next_actions", [])),
        SAFE_NEXT_ACTIONS,
        "safe actions",
    )
    _require_false_fields(
        blocked,
        (
            "automatic_coaching_allowed",
            "automatic_reference_allowed",
            "automatic_corrective_action_allowed",
        ),
        "blocked policy",
    )
    _sha256(blocked.get("reason_precedence_sha256"), "reason precedence")
    _sha256(blocked.get("reason_to_action_policy_sha256"), "reason/action policy")

    comparison = _mapping(payload.get("comparison"), "comparison")
    _expect_keys(
        comparison,
        {
            "paired_metric_family",
            "paired_metric_definition_sha256",
            "attempt_qc_policy_sha256",
            "missing_or_failed_attempt_policy_sha256",
            "exact_effect_estimand",
            "confidence_interval_method",
            "confidence_level",
            "multiplicity_method",
            "minimum_support",
            "inferential_analysis_enabled_for_e00_demo",
        },
        "comparison",
    )
    _safe_id(comparison.get("paired_metric_family"), "paired metric family")
    for field in (
        "paired_metric_definition_sha256",
        "attempt_qc_policy_sha256",
        "missing_or_failed_attempt_policy_sha256",
    ):
        _sha256(comparison.get(field), f"comparison {field}")
    for field in (
        "exact_effect_estimand",
        "confidence_interval_method",
        "confidence_level",
        "multiplicity_method",
        "minimum_support",
    ):
        _expect(
            comparison.get(field),
            "not_applicable_noninferential_demo",
            f"comparison {field}",
        )
    _expect(
        comparison.get("inferential_analysis_enabled_for_e00_demo"),
        False,
        "inferential analysis",
    )

    claims = _mapping(payload.get("claims"), "claims")
    _expect_keys(
        claims,
        {
            "noninferential_demo_only",
            "pedagogical_effect_claim_allowed",
            "clinical_claim_allowed",
            "production_deployment_claim_allowed",
        },
        "claims",
    )
    _expect(claims.get("noninferential_demo_only"), True, "noninferential claim")
    _require_false_fields(
        claims,
        (
            "pedagogical_effect_claim_allowed",
            "clinical_claim_allowed",
            "production_deployment_claim_allowed",
        ),
        "claims",
    )
    privacy = _mapping(payload.get("privacy_and_safety"), "privacy and safety")
    _expect_keys(
        privacy,
        {
            "user_or_participant_data_fabrication_allowed",
            "raw_audio_or_locator_in_public_trace_allowed",
            "numeric_score_or_threshold_in_public_trace_allowed",
            "source_identity_or_condition_in_public_trace_allowed",
            "free_text_in_public_trace_allowed",
            "human_mode_requires_h00_and_individual_consent",
            "fixture_mode_requires_no_human_voice_or_user_data",
        },
        "privacy and safety",
    )
    _require_false_fields(
        privacy,
        (
            "user_or_participant_data_fabrication_allowed",
            "raw_audio_or_locator_in_public_trace_allowed",
            "numeric_score_or_threshold_in_public_trace_allowed",
            "source_identity_or_condition_in_public_trace_allowed",
            "free_text_in_public_trace_allowed",
        ),
        "privacy and safety",
    )
    _expect(
        privacy.get("human_mode_requires_h00_and_individual_consent"),
        True,
        "human governance",
    )
    _expect(
        privacy.get("fixture_mode_requires_no_human_voice_or_user_data"),
        True,
        "fixture governance",
    )
    for field in (
        "approved_by_protocol_owner",
        "approved_by_privacy_owner",
        "approved_by_statistics_owner",
    ):
        _expect(payload.get(field), True, f"preregistration {field}")
    _require_binding(payload, "preregistration")
    return {
        "status": "valid_frozen_preregistration",
        "execution_mode": mode,
        "demo_trace_count": int(population["demo_trace_count"]),
        "paired_metric_definition_sha256": str(
            comparison["paired_metric_definition_sha256"]
        ),
    }


def validate_execution_authorization_payload(
    payload: Mapping[str, Any],
    *,
    expected_preregistration_sha256: str,
    expected_mode: str,
    h00_approval_sha256: str | None = None,
    h00_attestation_sha256: str | None = None,
) -> Mapping[str, Any]:
    """Validate mutually exclusive human or explicitly nonhuman authorization."""

    _reject_nonfinite_tree(payload, "execution authorization")
    _expect_keys(payload, AUTHORIZATION_KEYS, "execution authorization")
    if _contains_tbd(payload):
        raise E00ContractError("execution authorization contains unresolved TBD")
    _expect(
        payload.get("schema_version"),
        AUTHORIZATION_SCHEMA_VERSION,
        "authorization schema",
    )
    _safe_id(
        payload.get("authorization_id"),
        "authorization ID",
        pattern=r"^e00auth_[0-9a-f]{32}$",
    )
    _expect(payload.get("status"), "active_authorization", "authorization status")
    if expected_mode not in EXECUTION_MODES:
        raise E00ContractError("invalid expected execution mode")
    _expect(payload.get("execution_mode"), expected_mode, "authorization mode")
    _expect(
        _sha256(payload.get("preregistration_sha256"), "authorization prereg"),
        expected_preregistration_sha256,
        "authorization prereg binding",
    )
    _expect(
        payload.get("privacy_zone"),
        "A1_C1_public_summary_only",
        "authorization zone",
    )
    _require_false_fields(
        payload,
        (
            "contains_direct_identifiers",
            "contains_audio_or_locators",
            "contains_free_text",
        ),
        "authorization",
    )
    if expected_mode == "human_authorized_demo":
        _expect(payload.get("fixture_authorization"), None, "fixture branch")
        human = _mapping(payload.get("human_authorization"), "human authorization")
        _expect_keys(
            human,
            {
                "h00_approval_sha256",
                "h00_official_document_attestation_sha256",
                "e00_scope_or_amendment_sha256",
                "individual_consent_manifest_sha256",
                "consent_active_for_recording_diagnostic_coaching_reference_rerecord",
                "withdrawal_and_deletion_policy_active",
                "human_escalation_available",
                "authorization_current",
            },
            "human authorization",
        )
        if h00_approval_sha256 is None or h00_attestation_sha256 is None:
            raise E00ContractError("human authorization requires validated H00 hashes")
        _expect(
            _sha256(human.get("h00_approval_sha256"), "H00 approval"),
            h00_approval_sha256,
            "H00 approval binding",
        )
        _expect(
            _sha256(
                human.get("h00_official_document_attestation_sha256"),
                "H00 attestation",
            ),
            h00_attestation_sha256,
            "H00 attestation binding",
        )
        _sha256(human.get("e00_scope_or_amendment_sha256"), "E00 scope amendment")
        _sha256(
            human.get("individual_consent_manifest_sha256"),
            "individual consent manifest",
        )
        for field in (
            "consent_active_for_recording_diagnostic_coaching_reference_rerecord",
            "withdrawal_and_deletion_policy_active",
            "human_escalation_available",
            "authorization_current",
        ):
            _expect(human.get(field), True, f"human authorization {field}")
    else:
        _expect(payload.get("human_authorization"), None, "human branch")
        fixture = _mapping(
            payload.get("fixture_authorization"), "fixture authorization"
        )
        _expect_keys(
            fixture,
            {
                "fixture_provenance_sha256",
                "fixture_license_sha256",
                "scope",
                "contains_human_voice",
                "contains_participant_or_user_data",
                "represents_a_real_learner",
                "human_user_data_fabricated",
                "synthetic_or_nonhuman_label_explicit",
                "licensed_d08_human_reference_outside_fixture_scope",
                "pedagogical_or_clinical_claim_allowed",
            },
            "fixture authorization",
        )
        _sha256(fixture.get("fixture_provenance_sha256"), "fixture provenance")
        _sha256(fixture.get("fixture_license_sha256"), "fixture license")
        _expect(
            fixture.get("scope"),
            "learner_attempt_input_fixture_only",
            "fixture authorization scope",
        )
        _require_false_fields(
            fixture,
            (
                "contains_human_voice",
                "contains_participant_or_user_data",
                "represents_a_real_learner",
                "human_user_data_fabricated",
                "pedagogical_or_clinical_claim_allowed",
            ),
            "fixture authorization",
        )
        _expect(
            fixture.get("synthetic_or_nonhuman_label_explicit"),
            True,
            "fixture labeling",
        )
        _expect(
            fixture.get("licensed_d08_human_reference_outside_fixture_scope"),
            True,
            "fixture/reference scope separation",
        )
    _require_binding(payload, "authorization")
    return {"status": "valid_execution_authorization", "execution_mode": expected_mode}


def validate_s108_diagnostic_handoff_payload(
    payload: Mapping[str, Any],
    *,
    expected_attempt_id_sha256: str,
    expected_audio_content_sha256: str,
    expected_s108_commit_sha256: str,
    expected_model_identity_sha256: str,
    expected_checkpoint_sha256: str,
    expected_calibrator_sha256: str,
    expected_threshold_policy_sha256: str,
    execution_mode: str,
) -> None:
    """Validate a real per-attempt production handoff, never a locked replay."""

    _reject_nonfinite_tree(payload, "S108 production handoff")
    _expect_keys(payload, S108_HANDOFF_KEYS, "S108 production handoff")
    _expect(
        payload.get("schema_version"),
        S108_HANDOFF_SCHEMA_VERSION,
        "S108 handoff schema",
    )
    _safe_id(
        payload.get("handoff_id"),
        "S108 handoff ID",
        pattern=r"^e00s108_[0-9a-f]{32}$",
    )
    _expect(
        payload.get("status"),
        "actual_production_diagnostic_handoff",
        "S108 handoff status",
    )
    bindings = (
        ("attempt_id_sha256", expected_attempt_id_sha256),
        ("audio_content_sha256", expected_audio_content_sha256),
        ("s108_completion_commit_sha256", expected_s108_commit_sha256),
        ("model_identity_sha256", expected_model_identity_sha256),
        ("checkpoint_sha256", expected_checkpoint_sha256),
        ("calibrator_sha256", expected_calibrator_sha256),
        ("threshold_policy_sha256", expected_threshold_policy_sha256),
    )
    for field, expected in bindings:
        _expect(
            _sha256(payload.get(field), f"S108 handoff {field}"),
            expected,
            f"S108 handoff {field}",
        )
    _sha256(
        payload.get("production_inference_receipt_sha256"),
        "production inference receipt",
    )
    _sha256(
        payload.get("diagnostic_issue_manifest_sha256"),
        "diagnostic issue manifest",
    )
    expected_role = (
        "authorized_human_demo_attempt"
        if execution_mode == "human_authorized_demo"
        else "nonhuman_test_fixture_attempt"
    )
    _expect(payload.get("input_role"), expected_role, "S108 handoff input role")
    _expect(
        payload.get("actual_production_inference_executed"),
        True,
        "actual production inference",
    )
    _require_false_fields(
        payload,
        (
            "locked_test_row_audio_prediction_or_result_copied",
            "locked_result_replay_used",
            "score_or_threshold_embedded",
            "source_split_or_condition_embedded",
            "raw_audio_or_locator_embedded",
        ),
        "S108 handoff",
    )
    _expect(payload.get("eligible_for_e02_guard"), True, "E02 eligibility")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_production_handoff",
        "S108 handoff zone",
    )
    _require_binding(payload, "S108 handoff")


def _r03_selected_candidates(
    bank: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = []
    for raw_sentence in _list(bank.get("sentences"), "R03 bank sentences"):
        sentence = _mapping(raw_sentence, "R03 bank sentence")
        for raw in _list(
            sentence.get("selected_candidates"),
            "R03 selected candidates",
        ):
            candidates.append(_mapping(raw, "R03 selected candidate"))
    return candidates


def validate_d08_r03_lineage_receipt_payload(
    payload: Mapping[str, Any],
    *,
    r03_bank: Mapping[str, Any],
    r03_bank_sha256: str,
    d08_completion: Mapping[str, Any],
    d08_completion_sha256: str,
) -> Mapping[str, Any]:
    """Validate an exact restricted D08-row-to-R03-candidate lineage receipt.

    The independent R01 human source label is not evidence that a candidate is
    a D08 recording.  The receipt must instead bind the exact R03 row, source
    audio hash, restricted D08 metadata-row hash, aggregate D08 metadata
    manifest, D08 rights attestation, and per-recording license grant.
    """

    _reject_nonfinite_tree(payload, "D08/R03 lineage receipt")
    _expect_keys(payload, LINEAGE_KEYS, "D08/R03 lineage receipt")
    _expect(
        payload.get("schema_version"),
        LINEAGE_SCHEMA_VERSION,
        "lineage receipt schema",
    )
    _safe_id(
        payload.get("receipt_id"),
        "lineage receipt ID",
        pattern=r"^e00lineage_[0-9a-f]{32}$",
    )
    _expect(
        payload.get("status"),
        "exact_cross_lineage_verified",
        "lineage receipt status",
    )
    _expect(
        _sha256(payload.get("r03_bank_manifest_sha256"), "lineage R03 bank SHA"),
        r03_bank_sha256,
        "lineage R03 bank binding",
    )
    _expect(
        _sha256(payload.get("d08_completion_sha256"), "lineage D08 completion SHA"),
        d08_completion_sha256,
        "lineage D08 completion binding",
    )
    _expect(
        _sha256(
            payload.get("d08_metadata_manifest_sha256"),
            "lineage D08 metadata manifest SHA",
        ),
        _sha256(
            d08_completion.get("metadata_manifest_sha256"),
            "D08 completion metadata manifest SHA",
        ),
        "lineage D08 metadata manifest binding",
    )
    d08_rights_sha = _sha256(
        d08_completion.get("recording_rights_attestation_sha256"),
        "D08 completion rights SHA",
    )
    _expect(
        _sha256(
            payload.get("d08_recording_rights_attestation_sha256"),
            "lineage D08 rights SHA",
        ),
        d08_rights_sha,
        "lineage D08 rights binding",
    )
    _expect(
        _sha256(
            payload.get("source_rights_attestation_sha256"),
            "lineage source rights SHA",
        ),
        d08_rights_sha,
        "lineage source/D08 rights binding",
    )

    row_sha = _sha256(
        payload.get("selected_candidate_row_sha256"),
        "lineage selected candidate row SHA",
    )
    matches: list[Mapping[str, Any]] = []
    for candidate in _r03_selected_candidates(r03_bank):
        observed_sha = hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()
        if observed_sha == row_sha:
            matches.append(candidate)
    if len(matches) != 1:
        raise E00ContractError(
            "lineage receipt must bind exactly one selected R03 candidate row"
        )
    candidate = matches[0]
    target_sha = _sha256(payload.get("target_text_sha256"), "lineage target SHA")
    selected_object_sha = _sha256(
        payload.get("selected_audio_object_id_sha256"),
        "lineage selected audio object SHA",
    )
    selected_audio_sha = _sha256(
        payload.get("selected_audio_sha256"),
        "lineage selected audio SHA",
    )
    _expect(candidate.get("target_text_sha256"), target_sha, "lineage target")
    _expect(
        candidate.get("audio_object_id_sha256"),
        selected_object_sha,
        "lineage selected audio object",
    )
    _expect(
        candidate.get("audio_sha256"),
        selected_audio_sha,
        "lineage selected candidate audio",
    )
    _expect(candidate.get("source_kind"), "human_recording", "lineage source kind")
    _expect(
        candidate.get("source_attestation_sha256"),
        d08_rights_sha,
        "R03 candidate/D08 rights receipt",
    )
    _expect(
        _sha256(payload.get("source_audio_sha256"), "lineage source audio SHA"),
        selected_audio_sha,
        "candidate/source audio cross-match",
    )
    _expect(
        _sha256(
            payload.get("d08_recording_audio_sha256"),
            "lineage D08 recording audio SHA",
        ),
        selected_audio_sha,
        "candidate/D08 recording audio cross-match",
    )
    _sha256(
        payload.get("d08_recording_metadata_row_sha256"),
        "lineage D08 metadata row SHA",
    )
    d08_grant_sha = _sha256(
        payload.get("d08_recording_license_grant_sha256"),
        "lineage D08 recording license grant SHA",
    )
    _expect(
        _sha256(
            payload.get("source_recording_license_grant_sha256"),
            "lineage source recording license grant SHA",
        ),
        d08_grant_sha,
        "source/D08 per-recording rights receipt",
    )
    for field in (
        "candidate_audio_matches_d08_recording",
        "candidate_rights_match_d08_rights",
    ):
        _expect(payload.get(field), True, f"lineage {field}")
    _expect(
        payload.get("independent_r01_human_assumed_to_be_d08"),
        False,
        "independent R01 human/D08 assumption",
    )
    _require_false_fields(
        payload,
        ("audio_locator_embedded", "speaker_identity_embedded"),
        "D08/R03 lineage receipt",
    )
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_lineage",
        "lineage receipt zone",
    )
    _require_binding(payload, "D08/R03 lineage receipt")
    return {
        "status": "valid_exact_d08_r03_lineage_receipt",
        "target_text_sha256": target_sha,
        "selected_candidate_row_sha256": row_sha,
        "selected_audio_object_id_sha256": selected_object_sha,
        "selected_audio_sha256": selected_audio_sha,
        "d08_recording_rights_attestation_sha256": d08_rights_sha,
    }


def validate_reference_handoff_payload(
    payload: Mapping[str, Any],
    *,
    r03_bank: Mapping[str, Any],
    r03_bank_sha256: str,
    d08_completion: Mapping[str, Any],
    d08_completion_sha256: str,
    lineage_receipt: Mapping[str, Any],
    lineage_receipt_sha256: str,
) -> Mapping[str, Any]:
    """Bind one score-free handoff to an exactly verified D08-origin R03 row."""

    _reject_nonfinite_tree(payload, "reference handoff")
    _expect_keys(payload, REFERENCE_KEYS, "reference handoff")
    _expect(
        payload.get("schema_version"),
        REFERENCE_SCHEMA_VERSION,
        "reference schema",
    )
    _safe_id(
        payload.get("handoff_id"),
        "reference handoff ID",
        pattern=r"^e00ref_[0-9a-f]{32}$",
    )
    _expect(
        payload.get("status"),
        "authorized_for_single_demo_handoff",
        "reference status",
    )
    lineage = validate_d08_r03_lineage_receipt_payload(
        lineage_receipt,
        r03_bank=r03_bank,
        r03_bank_sha256=r03_bank_sha256,
        d08_completion=d08_completion,
        d08_completion_sha256=d08_completion_sha256,
    )
    target_sha = _sha256(payload.get("target_text_sha256"), "target text SHA")
    row_sha = _sha256(
        payload.get("selected_candidate_row_sha256"),
        "selected candidate row SHA",
    )
    _expect(
        _sha256(payload.get("r03_bank_manifest_sha256"), "R03 bank SHA"),
        r03_bank_sha256,
        "R03 bank binding",
    )
    _expect(
        _sha256(payload.get("d08_completion_sha256"), "D08 completion SHA"),
        d08_completion_sha256,
        "D08 completion binding",
    )
    d08_rights_sha = _sha256(
        d08_completion.get("recording_rights_attestation_sha256"),
        "D08 completion rights SHA",
    )
    _expect(
        _sha256(
            payload.get("d08_recording_rights_attestation_sha256"),
            "handoff D08 rights SHA",
        ),
        d08_rights_sha,
        "D08 rights binding",
    )
    _expect(
        _sha256(
            payload.get("source_rights_attestation_sha256"),
            "source rights SHA",
        ),
        d08_rights_sha,
        "source/D08 rights binding",
    )
    _expect(payload.get("source_kind"), "human_recording", "reference source kind")
    _expect(
        lineage.get("target_text_sha256"),
        target_sha,
        "reference/lineage target",
    )
    _expect(
        lineage.get("selected_candidate_row_sha256"),
        row_sha,
        "reference/lineage row",
    )
    _expect(
        _sha256(
            payload.get("selected_audio_object_id_sha256"),
            "reference selected audio object SHA",
        ),
        lineage.get("selected_audio_object_id_sha256"),
        "reference/lineage audio object",
    )
    _expect(
        _sha256(payload.get("selected_audio_sha256"), "reference selected audio SHA"),
        lineage.get("selected_audio_sha256"),
        "reference/lineage audio content",
    )
    _expect(
        _sha256(
            payload.get("d08_r03_lineage_receipt_sha256"),
            "reference lineage receipt SHA",
        ),
        _sha256(lineage_receipt_sha256, "expected lineage receipt SHA"),
        "reference lineage receipt binding",
    )
    for field in (
        "licensed_for_closed_loop_demo",
        "r03_row_lineage_verified",
        "d08_rights_binding_verified",
        "exact_candidate_audio_rights_cross_match_verified",
    ):
        _expect(payload.get(field), True, f"reference {field}")
    _require_false_fields(
        payload,
        (
            "audio_locator_embedded",
            "speaker_identity_embedded",
            "source_condition_exposed_to_learner",
            "score_payload_embedded",
        ),
        "reference handoff",
    )
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_handoff",
        "reference zone",
    )
    _require_binding(payload, "reference handoff")
    return {
        "status": "valid_licensed_reference_handoff",
        "target_text_sha256": target_sha,
        "selected_candidate_row_sha256": row_sha,
    }


def _validate_attempt(payload: Mapping[str, Any], name: str) -> dict[str, str]:
    _expect_keys(
        payload,
        {
            "attempt_id_sha256",
            "audio_object_id_sha256",
            "audio_content_sha256",
            "diagnostic_handoff_sha256",
            "scorer_identity_sha256",
            "checkpoint_sha256",
            "calibrator_sha256",
            "threshold_policy_sha256",
            "model_score_embedded",
            "raw_audio_locator_embedded",
        },
        name,
    )
    result = {
        field: _sha256(payload.get(field), f"{name}.{field}")
        for field in (
            "attempt_id_sha256",
            "audio_object_id_sha256",
            "audio_content_sha256",
            "diagnostic_handoff_sha256",
            "scorer_identity_sha256",
            "checkpoint_sha256",
            "calibrator_sha256",
            "threshold_policy_sha256",
        )
    }
    _require_false_fields(
        payload,
        ("model_score_embedded", "raw_audio_locator_embedded"),
        name,
    )
    return result


def validate_session_manifest_payload(
    payload: Mapping[str, Any],
    *,
    expected_mode: str,
    expected_hashes: Mapping[str, str],
) -> Mapping[str, Any]:
    """Validate two immutable attempt records and the same-scorer invariant."""

    _reject_nonfinite_tree(payload, "session manifest")
    _expect_keys(payload, SESSION_KEYS, "session manifest")
    _expect(payload.get("schema_version"), SESSION_SCHEMA_VERSION, "session schema")
    _safe_id(
        payload.get("session_id"),
        "session ID",
        pattern=r"^e00session_[0-9a-f]{32}$",
    )
    _expect(payload.get("execution_mode"), expected_mode, "session mode")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_demo",
        "session zone",
    )
    _sha256(payload.get("target_text_sha256"), "session target text SHA")
    binding_fields = {
        "authorization_sha256",
        "preregistration_sha256",
        "s108_completion_commit_sha256",
        "s108_production_handoff_sha256",
        "e01_completion_commit_sha256",
        "c02_bundle_commit_sha256",
        "c03_report_sha256",
        "e02_preregistration_sha256",
        "r03_bank_manifest_sha256",
        "d08_completion_sha256",
        "reference_handoff_sha256",
    }
    if set(expected_hashes) != binding_fields:
        raise E00ContractError("session expected hash set is incomplete")
    for field in sorted(binding_fields):
        _expect(
            _sha256(payload.get(field), f"session {field}"),
            _sha256(expected_hashes[field], f"expected {field}"),
            f"session {field}",
        )
    attempt1 = _validate_attempt(
        _mapping(payload.get("attempt_1"), "attempt 1"),
        "attempt 1",
    )
    attempt2 = _validate_attempt(
        _mapping(payload.get("attempt_2"), "attempt 2"),
        "attempt 2",
    )
    for field in (
        "attempt_id_sha256",
        "audio_object_id_sha256",
        "audio_content_sha256",
        "diagnostic_handoff_sha256",
    ):
        if attempt1[field] == attempt2[field]:
            raise E00ContractError(f"attempt 1/2 must differ for {field}")
    for field in (
        "scorer_identity_sha256",
        "checkpoint_sha256",
        "calibrator_sha256",
        "threshold_policy_sha256",
    ):
        _expect(attempt2[field], attempt1[field], f"same scorer {field}")
    same_scorer = _mapping(
        payload.get("same_scorer_contract"),
        "same scorer contract",
    )
    _expect_keys(
        same_scorer,
        {
            "same_model_identity",
            "same_checkpoint",
            "same_calibrator",
            "same_threshold_policy",
            "result_based_model_or_threshold_change_allowed",
        },
        "same scorer contract",
    )
    for field in (
        "same_model_identity",
        "same_checkpoint",
        "same_calibrator",
        "same_threshold_policy",
    ):
        _expect(same_scorer.get(field), True, f"same scorer {field}")
    _expect(
        same_scorer.get("result_based_model_or_threshold_change_allowed"),
        False,
        "result-based scorer change",
    )
    _require_false_fields(
        payload,
        (
            "contains_direct_identifiers",
            "contains_plain_target_or_transcript",
            "contains_audio_bytes_or_paths",
            "contains_numeric_model_scores",
            "contains_locked_result_copy",
            "contains_free_text",
            "user_or_participant_data_fabricated",
        ),
        "session",
    )
    _require_binding(payload, "session")
    return {
        "status": "valid_closed_loop_session",
        "attempt_1": attempt1,
        "attempt_2": attempt2,
    }


def _nullable_sha(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _sha256(value, name)


def validate_trace_payload(
    payload: Mapping[str, Any],
    *,
    expected_mode: str,
    expected_session_manifest_sha256: str,
    expected_s108_handoff_sha256: str,
    expected_reference_handoff_sha256: str,
    expected_paired_metric_definition_sha256: str,
) -> str:
    """Validate allowed, blocked and no-correction branches without scores."""

    _reject_nonfinite_tree(payload, "E00 trace")
    _expect_keys(payload, TRACE_KEYS, "E00 trace")
    _expect(payload.get("schema_version"), TRACE_SCHEMA_VERSION, "trace schema")
    _safe_id(
        payload.get("trace_id"),
        "trace ID",
        pattern=r"^e00trace_[0-9a-f]{32}$",
    )
    status_value = _string(payload.get("status"), "trace status")
    if status_value not in {
        "completed_eligible_loop",
        "blocked_safe_exit",
        "no_correction_safe_exit",
    }:
        raise E00ContractError("invalid E00 trace status")
    _expect(payload.get("execution_mode"), expected_mode, "trace mode")
    _expect(
        payload.get("privacy_zone"),
        "A1_aggregate_score_free_demo_trace",
        "trace zone",
    )
    _expect(
        _sha256(payload.get("session_manifest_sha256"), "trace session SHA"),
        expected_session_manifest_sha256,
        "trace session binding",
    )
    _expect(tuple(payload.get("event_order", [])), EVENT_ORDER, "trace event order")

    attempt1 = _mapping(payload.get("attempt_1"), "trace attempt 1")
    attempt2 = _mapping(payload.get("attempt_2"), "trace attempt 2")
    for name, attempt in (("attempt 1", attempt1), ("attempt 2", attempt2)):
        _expect_keys(attempt, {"completed", "attempt_receipt_sha256"}, name)
        _boolean(attempt.get("completed"), f"{name} completed")
        receipt = _nullable_sha(
            attempt.get("attempt_receipt_sha256"), f"{name} receipt"
        )
        if bool(attempt["completed"]) != (receipt is not None):
            raise E00ContractError(f"{name} completion/receipt mismatch")

    diagnostic = _mapping(payload.get("diagnostic"), "trace diagnostic")
    _expect_keys(
        diagnostic,
        {
            "s108_actual_production_handoff_used",
            "s108_handoff_sha256",
            "locked_result_copied_or_replayed",
            "diagnostic_receipt_sha256",
        },
        "trace diagnostic",
    )
    _expect(
        diagnostic.get("s108_actual_production_handoff_used"),
        True,
        "S108 production handoff",
    )
    _expect(
        _sha256(diagnostic.get("s108_handoff_sha256"), "trace S108 handoff"),
        expected_s108_handoff_sha256,
        "trace S108 handoff binding",
    )
    _expect(
        diagnostic.get("locked_result_copied_or_replayed"),
        False,
        "locked result replay",
    )
    _sha256(diagnostic.get("diagnostic_receipt_sha256"), "diagnostic receipt")

    guard = _mapping(payload.get("guard"), "trace guard")
    _expect_keys(
        guard,
        {"decision", "blocked_reasons", "safe_next_action", "e02_response_sha256"},
        "trace guard",
    )
    decision = _string(guard.get("decision"), "guard decision")
    reasons = _list(guard.get("blocked_reasons"), "blocked reasons")
    if len(reasons) != len(set(reasons)) or not set(reasons).issubset(BLOCK_REASONS):
        raise E00ContractError("guard blocked reasons are invalid")
    action = guard.get("safe_next_action")
    if action is not None and action not in SAFE_NEXT_ACTIONS:
        raise E00ContractError("guard safe next action is invalid")
    _sha256(guard.get("e02_response_sha256"), "E02 response SHA")

    coaching = _mapping(payload.get("coaching"), "trace coaching")
    _expect_keys(
        coaching,
        {
            "delivered",
            "selected_count",
            "top1_catalog_advice_id_sha256",
            "catalog_only",
            "free_generation_used",
            "llm_used",
            "blocked_top_k_backfill_used",
        },
        "trace coaching",
    )
    delivered = _boolean(coaching.get("delivered"), "coaching delivered")
    selected_count = _integer(coaching.get("selected_count"), "selected count")
    if selected_count > 1:
        raise E00ContractError("E00 may deliver at most top-1 coaching")
    advice_sha = _nullable_sha(
        coaching.get("top1_catalog_advice_id_sha256"),
        "top-1 advice SHA",
    )
    _expect(coaching.get("catalog_only"), True, "catalog-only")
    _require_false_fields(
        coaching,
        ("free_generation_used", "llm_used", "blocked_top_k_backfill_used"),
        "coaching",
    )

    reference = _mapping(payload.get("reference"), "trace reference")
    _expect_keys(
        reference,
        {
            "delivered",
            "handoff_sha256",
            "licensed_r03_d08_handoff",
            "source_or_locator_exposed",
            "score_exposed",
        },
        "trace reference",
    )
    reference_delivered = _boolean(reference.get("delivered"), "reference delivered")
    handoff_sha = _nullable_sha(reference.get("handoff_sha256"), "reference handoff")
    _boolean(
        reference.get("licensed_r03_d08_handoff"),
        "licensed reference marker",
    )
    _require_false_fields(
        reference,
        ("source_or_locator_exposed", "score_exposed"),
        "reference",
    )

    comparison = _mapping(payload.get("comparison"), "trace comparison")
    _expect_keys(
        comparison,
        {
            "completed",
            "same_scorer_verified",
            "paired_metric_definition_sha256",
            "comparison_receipt_sha256",
            "numeric_scores_embedded",
            "direction_or_effect_embedded",
        },
        "trace comparison",
    )
    comparison_completed = _boolean(
        comparison.get("completed"),
        "comparison completed",
    )
    same_scorer = _boolean(
        comparison.get("same_scorer_verified"),
        "same scorer",
    )
    metric_sha = _nullable_sha(
        comparison.get("paired_metric_definition_sha256"),
        "comparison metric definition",
    )
    comparison_sha = _nullable_sha(
        comparison.get("comparison_receipt_sha256"),
        "comparison receipt",
    )
    _require_false_fields(
        comparison,
        ("numeric_scores_embedded", "direction_or_effect_embedded"),
        "comparison",
    )

    if status_value == "completed_eligible_loop":
        _expect(decision, "allowed_for_separate_renderer", "eligible guard")
        _expect(reasons, [], "eligible blocked reasons")
        _expect(action, None, "eligible safe action")
        _expect(attempt1.get("completed"), True, "eligible attempt 1")
        _expect(delivered, True, "eligible coaching")
        _expect(selected_count, 1, "eligible top-1")
        if advice_sha is None:
            raise E00ContractError("eligible loop requires catalog advice SHA")
        _expect(reference_delivered, True, "eligible reference")
        _expect(
            reference.get("licensed_r03_d08_handoff"),
            True,
            "licensed reference",
        )
        _expect(handoff_sha, expected_reference_handoff_sha256, "reference handoff")
        _expect(attempt2.get("completed"), True, "eligible attempt 2")
        _expect(comparison_completed, True, "eligible comparison")
        _expect(same_scorer, True, "eligible same scorer")
        _expect(metric_sha, expected_paired_metric_definition_sha256, "metric")
        if comparison_sha is None:
            raise E00ContractError("eligible loop requires comparison receipt")
    elif status_value == "blocked_safe_exit":
        _expect(decision, "blocked", "blocked guard")
        if not reasons or action not in SAFE_NEXT_ACTIONS:
            raise E00ContractError("blocked exit requires reason and safe action")
        _expect(attempt1.get("completed"), True, "blocked attempt 1")
        _expect(delivered, False, "blocked coaching")
        _expect(selected_count, 0, "blocked selected count")
        _expect(advice_sha, None, "blocked advice")
        _expect(reference_delivered, False, "blocked reference")
        _expect(reference.get("licensed_r03_d08_handoff"), False, "blocked license")
        _expect(handoff_sha, None, "blocked reference handoff")
        _expect(attempt2.get("completed"), False, "blocked attempt 2")
        _expect(comparison_completed, False, "blocked comparison")
        _expect(same_scorer, False, "blocked same scorer")
        _expect(metric_sha, None, "blocked metric")
        _expect(comparison_sha, None, "blocked comparison receipt")
    else:
        _expect(decision, "no_correction", "no-correction guard")
        _expect(reasons, [], "no-correction reasons")
        _expect(action, None, "no-correction action")
        _expect(attempt1.get("completed"), True, "no-correction attempt 1")
        _expect(delivered, False, "no-correction coaching")
        _expect(selected_count, 0, "no-correction selected count")
        _expect(advice_sha, None, "no-correction advice")
        _expect(reference_delivered, False, "no-correction reference")
        _expect(
            reference.get("licensed_r03_d08_handoff"), False, "no-correction license"
        )
        _expect(handoff_sha, None, "no-correction handoff")
        _expect(attempt2.get("completed"), False, "no-correction attempt 2")
        _expect(comparison_completed, False, "no-correction comparison")
        _expect(same_scorer, False, "no-correction same scorer")
        _expect(metric_sha, None, "no-correction metric")
        _expect(comparison_sha, None, "no-correction receipt")

    safety = _mapping(payload.get("safety"), "trace safety")
    _expect_keys(
        safety,
        {
            "user_or_participant_data_fabricated",
            "model_score_or_threshold_leaked",
            "source_identity_or_condition_leaked",
            "raw_audio_or_locator_leaked",
            "free_text_or_generated_advice_present",
            "blocked_capability_override_used",
        },
        "trace safety",
    )
    _require_false_fields(safety, tuple(safety), "trace safety")
    claims = _mapping(payload.get("claims"), "trace claims")
    _expect_keys(
        claims,
        {
            "noninferential_demo_only",
            "pedagogical_effect_claim_allowed",
            "clinical_claim_allowed",
            "deployment_claim_allowed",
        },
        "trace claims",
    )
    _expect(claims.get("noninferential_demo_only"), True, "trace noninferential")
    _require_false_fields(
        claims,
        (
            "pedagogical_effect_claim_allowed",
            "clinical_claim_allowed",
            "deployment_claim_allowed",
        ),
        "trace claims",
    )
    _require_binding(payload, "trace")
    return status_value


def validate_completion_payload(
    payload: Mapping[str, Any],
    *,
    expected_config_sha256: str,
    expected_preregistration_sha256: str,
    expected_authorization_sha256: str,
    expected_predecessor_chain_sha256: str,
    expected_execution_mode: str,
    expected_trace_count: int,
) -> Mapping[str, Any]:
    """Validate a score-free E00 completion; no effect can be inferred."""

    _reject_nonfinite_tree(payload, "E00 completion")
    _expect_keys(payload, COMPLETION_KEYS, "E00 completion")
    _expect(
        payload.get("schema_version"),
        COMPLETION_SCHEMA_VERSION,
        "completion schema",
    )
    _safe_id(
        payload.get("completion_id"),
        "completion ID",
        pattern=r"^e00completion_[0-9a-f]{32}$",
    )
    _expect(
        payload.get("status"),
        "completed_noninferential_demo",
        "completion status",
    )
    for field, expected in (
        ("config_sha256", expected_config_sha256),
        ("preregistration_sha256", expected_preregistration_sha256),
        ("authorization_sha256", expected_authorization_sha256),
        ("predecessor_chain_sha256", expected_predecessor_chain_sha256),
    ):
        _expect(
            _sha256(payload.get(field), f"completion {field}"),
            expected,
            f"completion {field}",
        )
    _expect(payload.get("execution_mode"), expected_execution_mode, "completion mode")
    _expect(payload.get("privacy_zone"), "A1_aggregate_only", "completion zone")
    trace_count = _integer(payload.get("trace_count"), "trace count", minimum=1)
    _expect(trace_count, expected_trace_count, "preregistered trace count")
    eligible = _integer(
        payload.get("eligible_completed_loop_count"),
        "eligible loop count",
        minimum=1,
    )
    blocked = _integer(payload.get("blocked_safe_exit_count"), "blocked count")
    no_correction = _integer(
        payload.get("no_correction_safe_exit_count"),
        "no-correction count",
    )
    if eligible + blocked + no_correction != trace_count:
        raise E00ContractError("completion trace counts do not sum")
    comparisons = _integer(
        payload.get("same_scorer_comparison_count"),
        "same-scorer comparison count",
        minimum=1,
    )
    _expect(comparisons, eligible, "eligible/comparison count")
    _sha256(payload.get("traces_sha256"), "trace collection SHA")

    safety = _mapping(payload.get("safety_counts"), "completion safety counts")
    _expect_keys(
        safety,
        {
            "user_data_fabrication_count",
            "free_generation_count",
            "blocked_override_count",
            "locked_result_copy_count",
            "score_or_threshold_leak_count",
            "source_or_locator_leak_count",
        },
        "completion safety counts",
    )
    for field in safety:
        _expect(safety.get(field), 0, f"completion safety {field}")

    actual = _mapping(payload.get("actual_execution"), "actual execution")
    _expect_keys(
        actual,
        {
            "attempt1_recording_count",
            "s108_diagnostic_handoff_count",
            "e02_guard_count",
            "c02_top1_coaching_delivery_count",
            "licensed_reference_delivery_count",
            "attempt2_recording_count",
            "same_scorer_comparison_count",
        },
        "actual execution",
    )
    expected_actual = {
        "attempt1_recording_count": trace_count,
        "s108_diagnostic_handoff_count": trace_count + eligible,
        "e02_guard_count": trace_count,
        "c02_top1_coaching_delivery_count": eligible,
        "licensed_reference_delivery_count": eligible,
        "attempt2_recording_count": eligible,
        "same_scorer_comparison_count": eligible,
    }
    for field, expected_count in expected_actual.items():
        _expect(
            _integer(actual.get(field), f"actual {field}"),
            expected_count,
            f"actual {field}",
        )

    claims = _mapping(payload.get("claims"), "completion claims")
    _expect_keys(
        claims,
        {
            "noninferential_demo_only",
            "effect_estimated",
            "confidence_interval_computed",
            "pedagogical_effect_claimed",
            "clinical_claimed",
            "production_deployment_claimed",
        },
        "completion claims",
    )
    _expect(claims.get("noninferential_demo_only"), True, "noninferential demo")
    _require_false_fields(
        claims,
        (
            "effect_estimated",
            "confidence_interval_computed",
            "pedagogical_effect_claimed",
            "clinical_claimed",
            "production_deployment_claimed",
        ),
        "completion claims",
    )
    _require_false_fields(
        payload,
        (
            "contains_rows_or_pseudonymous_ids",
            "contains_audio_or_locator_hashes",
            "contains_numeric_scores_thresholds_or_effects",
            "contains_source_identity_or_condition",
            "contains_free_text",
            "publication_allowed",
        ),
        "completion",
    )
    _expect(payload.get("completion_claim_allowed"), True, "completion claim")
    commit = _string(payload.get("implementation_git_commit"), "implementation commit")
    if GIT_COMMIT_RE.fullmatch(commit) is None or commit == "0" * 40:
        raise E00ContractError("implementation commit must be nonzero 40-hex")
    _require_binding(payload, "completion")
    return {
        "status": "valid_completed_noninferential_demo",
        "trace_count": trace_count,
        "eligible_completed_loop_count": eligible,
        "completion_claim_allowed": True,
        "publication_allowed": False,
    }


def validate_d08_aggregate_completion_payload(
    payload: Mapping[str, Any],
    *,
    expected_config_sha256: str,
) -> Mapping[str, Any]:
    """Validate only D08's A1 completion handoff, never R0/R1/D1 rows."""

    forbidden = {
        "speaker_id",
        "speaker_id_sha256",
        "recording_id",
        "audio_sha256",
        "audio_object_id_sha256",
        "audio_path",
        "recording_path",
        "locator",
        "free_text",
    }.intersection(_collect_keys(payload))
    if forbidden:
        raise E00ContractError(
            f"D08 aggregate completion leaks protected fields: {sorted(forbidden)}"
        )
    required = {
        "schema_version",
        "completion_id",
        "status",
        "config_sha256",
        "r00_card_sha256",
        "h00_approval_sha256",
        "individual_consent_attestation_sha256",
        "recording_rights_attestation_sha256",
        "qc_preregistration_sha256",
        "metadata_manifest_sha256",
        "qc_report_sha256",
        "privacy_zone",
        "contains_row_level_data",
        "contains_direct_or_pseudonymous_ids",
        "contains_audio_or_locator_hashes",
        "contains_free_text",
        "sentence_count",
        "binding_minimum_distinct_speakers_per_sentence",
        "planning_target_minimum_distinct_speakers_per_sentence",
        "planning_target_met_all_sentences",
        "same_equipment_and_space_verified",
        "format_and_distance_verified",
        "source_license_honesty_verified",
        "qc_adjudication_contract_verified",
        "sentences",
        "totals",
        "actual_recruitment_completed",
        "actual_consent_completed",
        "actual_recording_completed",
        "actual_qc_completed",
        "completion_claim_allowed",
        "publication_allowed",
        "implementation_git_commit",
    }
    _expect_keys(payload, required, "D08 aggregate completion")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_completion.v1",
        "D08 schema",
    )
    _expect(payload.get("status"), "completed", "D08 status")
    _expect(
        _sha256(payload.get("config_sha256"), "D08 config SHA"),
        expected_config_sha256,
        "D08 config binding",
    )
    for field in (
        "r00_card_sha256",
        "h00_approval_sha256",
        "individual_consent_attestation_sha256",
        "recording_rights_attestation_sha256",
        "qc_preregistration_sha256",
        "metadata_manifest_sha256",
        "qc_report_sha256",
    ):
        _sha256(payload.get(field), f"D08 {field}")
    _expect(
        payload.get("privacy_zone"),
        "A1_aggregate_review_required",
        "D08 zone",
    )
    _require_false_fields(
        payload,
        (
            "contains_row_level_data",
            "contains_direct_or_pseudonymous_ids",
            "contains_audio_or_locator_hashes",
            "contains_free_text",
            "publication_allowed",
        ),
        "D08 completion",
    )
    _expect(payload.get("sentence_count"), 20, "D08 sentence count")
    _integer(
        payload.get("binding_minimum_distinct_speakers_per_sentence"),
        "D08 speaker floor",
        minimum=1,
    )
    _expect(
        payload.get("planning_target_minimum_distinct_speakers_per_sentence"),
        4,
        "D08 planning speaker target",
    )
    for field in (
        "same_equipment_and_space_verified",
        "format_and_distance_verified",
        "source_license_honesty_verified",
        "qc_adjudication_contract_verified",
        "actual_recruitment_completed",
        "actual_consent_completed",
        "actual_recording_completed",
        "actual_qc_completed",
        "completion_claim_allowed",
    ):
        _expect(payload.get(field), True, f"D08 {field}")
    sentences = _list(payload.get("sentences"), "D08 sentence summaries")
    if len(sentences) != 20:
        raise E00ContractError("D08 completion must summarize 20 sentences")
    _mapping(payload.get("totals"), "D08 totals")
    commit = _string(payload.get("implementation_git_commit"), "D08 commit")
    if GIT_COMMIT_RE.fullmatch(commit) is None or commit == "0" * 40:
        raise E00ContractError("D08 implementation commit must be nonzero")
    return {
        "status": "valid_d08_aggregate_completion_handoff",
        "recording_rights_attestation_sha256": str(
            payload["recording_rights_attestation_sha256"]
        ),
    }


def _require_resolved_sha(
    value: object,
    name: str,
    *,
    stage: str,
    audit: AccessAudit,
) -> str:
    text = _string(value, name)
    if text.startswith(TBD_PREFIX):
        raise E00ContractError(
            f"{name} is {text}",
            stage=stage,
            audit=audit,
        )
    try:
        return _sha256(text, name)
    except E00ContractError as exc:
        raise E00ContractError(str(exc), stage=stage, audit=audit) from exc


def _read_exact_json(
    *,
    root: Path,
    relative: str,
    expected_sha256: str,
    name: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    data, snapshot = _read_file(root, relative, name)
    _expect(snapshot.sha256, expected_sha256, f"{name} SHA")
    return _parse_json(data, name), snapshot


def _load_module(name: str, *, stage: str, audit: AccessAudit) -> Any:
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        raise E00ContractError(
            f"cannot load pinned {name}: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    audit.predecessor_module_load_count += 1
    return module


def _verify_static_sources(
    *,
    root: Path,
    static: StaticValidation,
    audit: AccessAudit,
) -> None:
    for snapshot in static.source_snapshots:
        _verify_snapshot(root, snapshot, f"static source {snapshot.path}")
        audit.source_toctou_verification_count += 1


def _deep_validate_s108(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> dict[str, str]:
    stage = "s108_final"
    section = _mapping(
        _mapping(config.get("predecessors"), "predecessors").get("s108"),
        "S108 predecessor",
    )
    expected_commit = _require_resolved_sha(
        section.get("completion_commit_sha256"),
        "S108 completion commit SHA",
        stage=stage,
        audit=audit,
    )
    expected_model = _require_resolved_sha(
        section.get("model_identity_sha256"),
        "S108 model identity SHA",
        stage=stage,
        audit=audit,
    )
    expected_checkpoint = _require_resolved_sha(
        section.get("checkpoint_sha256"),
        "S108 checkpoint SHA",
        stage=stage,
        audit=audit,
    )
    expected_calibrator = _require_resolved_sha(
        section.get("calibrator_sha256"),
        "S108 calibrator SHA",
        stage=stage,
        audit=audit,
    )
    expected_threshold = _require_resolved_sha(
        section.get("threshold_policy_sha256"),
        "S108 threshold policy SHA",
        stage=stage,
        audit=audit,
    )
    module = _load_module(
        "voice_coach.evaluation.e01_metamorphic_contract",
        stage=stage,
        audit=audit,
    )
    delegate_relative = _relative_string(
        section.get("delegate_config_path"),
        "S108 delegate config path",
    )
    audit.s108_path_resolution_count += 1
    audit.s108_read_attempt_count += 1
    try:
        delegate_config = module.load_config(root / delegate_relative)
        audit.s108_read_count += 1
        delegate_predecessor = _mapping(
            delegate_config.get("predecessor_s108"),
            "E01 S108 predecessor",
        )
        _expect(
            delegate_predecessor.get("completion_commit_sha256"),
            expected_commit,
            "E00/E01 S108 commit binding",
        )
        _expect(
            delegate_predecessor.get("selected_model_identity_sha256"),
            expected_model,
            "E00/E01 S108 model binding",
        )
        _expect(
            delegate_predecessor.get("selected_checkpoint_sha256"),
            expected_checkpoint,
            "E00/E01 S108 checkpoint binding",
        )
        _expect(
            delegate_predecessor.get("selected_calibrator_sha256"),
            expected_calibrator,
            "E00/E01 S108 calibrator binding",
        )
        _expect(
            delegate_predecessor.get("selected_threshold_policy_sha256"),
            expected_threshold,
            "E00/E01 S108 threshold binding",
        )
        delegate_audit = module.AccessAudit()
        evidence = module.deep_validate_s108_final(
            root=root,
            config=delegate_config,
            audit=delegate_audit,
        )
    except Exception as exc:
        raise E00ContractError(
            f"S108 final deep validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _expect(
        getattr(evidence, "selected_model_identity_sha256", None),
        expected_model,
        "S108 selected model",
    )
    _expect(
        getattr(evidence, "selected_checkpoint_sha256", None),
        expected_checkpoint,
        "S108 selected checkpoint",
    )
    audit.s108_deep_validation_count += 1
    return {
        "completion_commit_sha256": expected_commit,
        "model_identity_sha256": expected_model,
        "checkpoint_sha256": expected_checkpoint,
        "calibrator_sha256": expected_calibrator,
        "threshold_policy_sha256": expected_threshold,
    }


def _deep_validate_e01(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> str:
    stage = "e01_completion"
    section = _mapping(
        _mapping(config.get("predecessors"), "predecessors").get("e01"),
        "E01 predecessor",
    )
    expected_sha = _require_resolved_sha(
        section.get("completion_commit_sha256"),
        "E01 completion commit SHA",
        stage=stage,
        audit=audit,
    )
    relative = _relative_string(
        section.get("completion_commit_path"),
        "E01 completion commit path",
    )
    audit.e01_path_resolution_count += 1
    audit.e01_read_attempt_count += 1
    try:
        _, snapshot = _read_exact_json(
            root=root,
            relative=relative,
            expected_sha256=expected_sha,
            name="E01 completion commit",
        )
        audit.e01_read_count += 1
        module = _load_module(
            "voice_coach.evaluation.e01_metamorphic_contract",
            stage=stage,
            audit=audit,
        )
        delegate_relative = _relative_string(
            section.get("config_path"),
            "E01 config path",
        )
        delegate_config = module.load_config(root / delegate_relative)
        decision = module.validate_completed_bundle(
            workspace_root=root,
            config_path=root / delegate_relative,
            config=delegate_config,
        )
    except Exception as exc:
        raise E00ContractError(
            f"E01 completion deep validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _expect(decision, section.get("required_decision"), "E01 completion decision")
    _verify_snapshot(root, snapshot, "E01 completion commit")
    audit.e01_deep_validation_count += 1
    return expected_sha


def _deep_validate_c02_e02(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> tuple[str, str]:
    stage = "c02_e02_completion"
    section = _mapping(
        _mapping(config.get("predecessors"), "predecessors").get("c02_e02"),
        "C02/E02 predecessor",
    )
    expected_c02 = _require_resolved_sha(
        section.get("c02_bundle_commit_sha256"),
        "C02 completed bundle SHA",
        stage=stage,
        audit=audit,
    )
    expected_e02 = _require_resolved_sha(
        section.get("e02_preregistration_sha256"),
        "E02 preregistration SHA",
        stage=stage,
        audit=audit,
    )
    module = _load_module(
        "voice_coach.evaluation.e02_confidence_guard_contract",
        stage=stage,
        audit=audit,
    )
    relative = _relative_string(section.get("config_path"), "E02 config path")
    audit.e02_path_resolution_count += 1
    audit.e02_read_attempt_count += 1
    try:
        delegate_config = module.load_config(root / relative)
        audit.e02_read_count += 1
        delegate_c02 = _mapping(
            delegate_config.get("predecessor_c02"),
            "E02 C02 predecessor",
        )
        delegate_prereg = _mapping(
            delegate_config.get("preregistration"),
            "E02 preregistration",
        )
        _expect(
            delegate_c02.get("completion_commit_sha256"),
            expected_c02,
            "E00/E02 C02 binding",
        )
        _expect(
            delegate_prereg.get("approved_artifact_sha256"),
            expected_e02,
            "E00/E02 preregistration binding",
        )
        result = module.run_full_preflight(
            workspace_root=root,
            config_path=root / relative,
            config=delegate_config,
        )
    except Exception as exc:
        raise E00ContractError(
            f"C02/E02 deep validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _expect(
        getattr(result, "c02_completion_commit_sha256", None),
        expected_c02,
        "C02 completion result",
    )
    _expect(
        getattr(result, "e02_preregistration_sha256", None),
        expected_e02,
        "E02 preregistration result",
    )
    audit.c02_deep_validation_count += 1
    audit.e02_deep_validation_count += 1
    return expected_c02, expected_e02


def _deep_validate_c03(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> str:
    stage = "c03_completion"
    section = _mapping(
        _mapping(config.get("predecessors"), "predecessors").get("c03"),
        "C03 predecessor",
    )
    expected_sha = _require_resolved_sha(
        section.get("report_sha256"),
        "C03 report SHA",
        stage=stage,
        audit=audit,
    )
    relative = _relative_string(section.get("report_path"), "C03 report path")
    audit.c03_path_resolution_count += 1
    audit.c03_read_attempt_count += 1
    try:
        report, snapshot = _read_exact_json(
            root=root,
            relative=relative,
            expected_sha256=expected_sha,
            name="C03 expert audit report",
        )
        audit.c03_read_count += 1
        module = _load_module(
            "voice_coach.coaching.c03_expert_audit_contract",
            stage=stage,
            audit=audit,
        )
        validated = module.validate_report_payload(report)
    except Exception as exc:
        raise E00ContractError(
            f"C03 report deep validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    decision = _mapping(validated.get("decision"), "C03 decision")
    _expect(decision.get("status"), section.get("required_decision"), "C03 status")
    _expect(decision.get("overall_passed"), True, "C03 overall pass")
    _expect(
        decision.get("production_release_allowed"),
        True,
        "C03 production release",
    )
    _verify_snapshot(root, snapshot, "C03 expert audit report")
    audit.c03_deep_validation_count += 1
    return expected_sha


def _deep_validate_r03(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> tuple[str, Mapping[str, Any]]:
    stage = "r03_completion"
    section = _mapping(
        _mapping(config.get("predecessors"), "predecessors").get("r03"),
        "R03 predecessor",
    )
    expected_sha = _require_resolved_sha(
        section.get("bank_manifest_sha256"),
        "R03 bank manifest SHA",
        stage=stage,
        audit=audit,
    )
    relative = _relative_string(
        section.get("bank_manifest_path"),
        "R03 bank manifest path",
    )
    audit.r03_path_resolution_count += 1
    audit.r03_read_attempt_count += 1
    try:
        bank, snapshot = _read_exact_json(
            root=root,
            relative=relative,
            expected_sha256=expected_sha,
            name="R03 reference bank",
        )
        audit.r03_read_count += 1
        module = _load_module(
            "voice_coach.references.r03_selection_contract",
            stage=stage,
            audit=audit,
        )
        summary = module.validate_bank_manifest_payload(bank)
    except Exception as exc:
        raise E00ContractError(
            f"R03 bank deep validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _expect(
        bank.get("status"),
        section.get("required_bank_status"),
        "R03 bank status",
    )
    _expect(
        summary.get("status"),
        "valid_private_bank_pending_r04",
        "R03 validation status",
    )
    _verify_snapshot(root, snapshot, "R03 reference bank")
    audit.r03_deep_validation_count += 1
    return expected_sha, bank


def _deep_validate_d08(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> tuple[str, Mapping[str, Any]]:
    stage = "d08_completion"
    section = _mapping(
        _mapping(config.get("predecessors"), "predecessors").get("d08"),
        "D08 predecessor",
    )
    expected_sha = _require_resolved_sha(
        section.get("completion_sha256"),
        "D08 completion SHA",
        stage=stage,
        audit=audit,
    )
    relative = _relative_string(section.get("completion_path"), "D08 completion path")
    audit.d08_path_resolution_count += 1
    audit.d08_read_attempt_count += 1
    try:
        completion, snapshot = _read_exact_json(
            root=root,
            relative=relative,
            expected_sha256=expected_sha,
            name="D08 aggregate completion",
        )
        audit.d08_read_count += 1
        validate_d08_aggregate_completion_payload(
            completion,
            expected_config_sha256=_sha256(
                section.get("config_sha256"),
                "D08 config SHA",
            ),
        )
    except Exception as exc:
        raise E00ContractError(
            f"D08 aggregate completion validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _verify_snapshot(root, snapshot, "D08 aggregate completion")
    audit.d08_deep_validation_count += 1
    return expected_sha, completion


def _read_frozen_preregistration(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    stage = "e00_preregistration"
    section = _mapping(
        config.get("execution_preregistration"),
        "execution preregistration",
    )
    expected_sha = _require_resolved_sha(
        section.get("approved_artifact_sha256"),
        "E00 preregistration SHA",
        stage=stage,
        audit=audit,
    )
    relative = _relative_string(
        section.get("approved_artifact_path"),
        "E00 preregistration path",
    )
    audit.preregistration_path_resolution_count += 1
    audit.preregistration_read_attempt_count += 1
    try:
        payload, snapshot = _read_exact_json(
            root=root,
            relative=relative,
            expected_sha256=expected_sha,
            name="E00 frozen preregistration",
        )
        audit.preregistration_read_count += 1
        summary = validate_preregistration_payload(payload, config=config)
    except Exception as exc:
        raise E00ContractError(
            f"E00 preregistration validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _verify_snapshot(root, snapshot, "E00 frozen preregistration")
    return expected_sha, payload, summary


def _deep_validate_h00(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> tuple[str, str]:
    stage = "h00_human_authorization"
    governance = _mapping(config.get("human_governance"), "human governance")
    expected_approval = _require_resolved_sha(
        governance.get("h00_approval_sha256"),
        "H00 approval SHA",
        stage=stage,
        audit=audit,
    )
    expected_attestation = _require_resolved_sha(
        governance.get("h00_attestation_sha256"),
        "H00 attestation SHA",
        stage=stage,
        audit=audit,
    )
    module = _load_module(
        "voice_coach.human_research.h00_approval_contract",
        stage=stage,
        audit=audit,
    )
    audit.h00_module_load_count += 1
    relative = _relative_string(governance.get("h00_config_path"), "H00 config path")
    try:
        h00_config = module.load_config(root / relative)
        approval = _mapping(h00_config.get("official_approval"), "H00 approval")
        attestation = _mapping(
            h00_config.get("official_document_attestation"),
            "H00 attestation",
        )
        _expect(
            approval.get("approval_sha256"),
            expected_approval,
            "E00/H00 approval binding",
        )
        _expect(
            attestation.get("attestation_sha256"),
            expected_attestation,
            "E00/H00 attestation binding",
        )
        result = module.run_full_preflight(
            workspace_root=root,
            config_path=root / relative,
            config=h00_config,
        )
    except Exception as exc:
        raise E00ContractError(
            f"H00 human authorization validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _expect(
        getattr(result, "approval_sha256", None),
        expected_approval,
        "H00 approval result",
    )
    _expect(
        getattr(result, "attestation_sha256", None),
        expected_attestation,
        "H00 attestation result",
    )
    result_audit = getattr(result, "audit", {})
    if isinstance(result_audit, Mapping):
        audit.h00_approval_path_resolution_count += int(
            result_audit.get("approval_path_resolution_count", 0)
        )
        audit.h00_approval_read_count += int(result_audit.get("approval_read_count", 0))
        audit.h00_attestation_path_resolution_count += int(
            result_audit.get("attestation_path_resolution_count", 0)
        )
        audit.h00_attestation_read_count += int(
            result_audit.get("attestation_read_count", 0)
        )
        audit.h00_restricted_document_path_resolution_count += int(
            result_audit.get("restricted_document_path_resolution_count", 0)
        )
        audit.h00_restricted_document_read_count += int(
            result_audit.get("restricted_document_read_count", 0)
        )
    return expected_approval, expected_attestation


def _read_execution_authorization(
    *,
    root: Path,
    config: Mapping[str, Any],
    preregistration_sha256: str,
    execution_mode: str,
    audit: AccessAudit,
    h00_hashes: tuple[str, str] | None,
) -> tuple[str, Mapping[str, Any]]:
    stage = "e00_execution_authorization"
    section = _mapping(config.get("execution_authorization"), "authorization")
    expected_sha = _require_resolved_sha(
        section.get("artifact_sha256"),
        "E00 authorization SHA",
        stage=stage,
        audit=audit,
    )
    relative = _relative_string(
        section.get("artifact_path"),
        "E00 authorization path",
    )
    audit.authorization_path_resolution_count += 1
    audit.authorization_read_attempt_count += 1
    try:
        payload, snapshot = _read_exact_json(
            root=root,
            relative=relative,
            expected_sha256=expected_sha,
            name="E00 execution authorization",
        )
        audit.authorization_read_count += 1
        validate_execution_authorization_payload(
            payload,
            expected_preregistration_sha256=preregistration_sha256,
            expected_mode=execution_mode,
            h00_approval_sha256=(h00_hashes[0] if h00_hashes else None),
            h00_attestation_sha256=(h00_hashes[1] if h00_hashes else None),
        )
    except Exception as exc:
        raise E00ContractError(
            f"E00 execution authorization validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _verify_snapshot(root, snapshot, "E00 execution authorization")
    return expected_sha, payload


def _read_reference_handoff(
    *,
    root: Path,
    config: Mapping[str, Any],
    r03_sha256: str,
    r03_bank: Mapping[str, Any],
    d08_sha256: str,
    d08_completion: Mapping[str, Any],
    audit: AccessAudit,
) -> str:
    section = _mapping(config.get("reference_handoff"), "reference handoff")
    lineage_stage = "lineage_receipt"
    lineage_sha = _require_resolved_sha(
        section.get("lineage_receipt_sha256"),
        "D08/R03 lineage receipt SHA",
        stage=lineage_stage,
        audit=audit,
    )
    lineage_relative = _relative_string(
        section.get("lineage_receipt_path"),
        "D08/R03 lineage receipt path",
    )
    audit.lineage_receipt_path_resolution_count += 1
    audit.lineage_receipt_read_attempt_count += 1
    try:
        lineage_payload, lineage_snapshot = _read_exact_json(
            root=root,
            relative=lineage_relative,
            expected_sha256=lineage_sha,
            name="E00 D08/R03 lineage receipt",
        )
        audit.lineage_receipt_read_count += 1
        validate_d08_r03_lineage_receipt_payload(
            lineage_payload,
            r03_bank=r03_bank,
            r03_bank_sha256=r03_sha256,
            d08_completion=d08_completion,
            d08_completion_sha256=d08_sha256,
        )
    except Exception as exc:
        raise E00ContractError(
            f"E00 D08/R03 lineage receipt validation failed: {exc}",
            stage=lineage_stage,
            audit=audit,
        ) from exc
    _verify_snapshot(root, lineage_snapshot, "E00 D08/R03 lineage receipt")

    stage = "reference_handoff"
    expected_sha = _require_resolved_sha(
        section.get("artifact_sha256"),
        "reference handoff SHA",
        stage=stage,
        audit=audit,
    )
    relative = _relative_string(
        section.get("artifact_path"),
        "reference handoff path",
    )
    audit.reference_handoff_path_resolution_count += 1
    audit.reference_handoff_read_attempt_count += 1
    try:
        payload, snapshot = _read_exact_json(
            root=root,
            relative=relative,
            expected_sha256=expected_sha,
            name="E00 reference handoff",
        )
        audit.reference_handoff_read_count += 1
        validate_reference_handoff_payload(
            payload,
            r03_bank=r03_bank,
            r03_bank_sha256=r03_sha256,
            d08_completion=d08_completion,
            d08_completion_sha256=d08_sha256,
            lineage_receipt=lineage_payload,
            lineage_receipt_sha256=lineage_sha,
        )
    except Exception as exc:
        raise E00ContractError(
            f"E00 reference handoff validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _verify_snapshot(root, snapshot, "E00 reference handoff")
    return expected_sha


def _predecessor_chain_sha256(
    *,
    s108: Mapping[str, str],
    e01_sha256: str,
    c02_sha256: str,
    e02_sha256: str,
    c03_sha256: str,
    r03_sha256: str,
    d08_sha256: str,
) -> str:
    chain = {
        "s108_completion_commit_sha256": s108["completion_commit_sha256"],
        "s108_model_identity_sha256": s108["model_identity_sha256"],
        "s108_checkpoint_sha256": s108["checkpoint_sha256"],
        "s108_calibrator_sha256": s108["calibrator_sha256"],
        "s108_threshold_policy_sha256": s108["threshold_policy_sha256"],
        "e01_completion_commit_sha256": e01_sha256,
        "c02_bundle_commit_sha256": c02_sha256,
        "e02_preregistration_sha256": e02_sha256,
        "c03_report_sha256": c03_sha256,
        "r03_bank_manifest_sha256": r03_sha256,
        "d08_completion_sha256": d08_sha256,
    }
    return hashlib.sha256(canonical_json_bytes(chain)).hexdigest()


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Validate every predecessor and stop before E00 session/runtime access."""

    root = workspace_root.resolve(strict=True)
    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=root,
        config_path=config_path,
        config=config,
        audit=audit,
    )
    _verify_static_sources(root=root, static=static, audit=audit)
    s108 = _deep_validate_s108(root=root, config=config, audit=audit)
    e01_sha = _deep_validate_e01(
        root=root,
        config_path=config_path,
        config=config,
        audit=audit,
    )
    c02_sha, e02_sha = _deep_validate_c02_e02(
        root=root,
        config=config,
        audit=audit,
    )
    c03_sha = _deep_validate_c03(root=root, config=config, audit=audit)
    r03_sha, r03_bank = _deep_validate_r03(root=root, config=config, audit=audit)
    d08_sha, d08_completion = _deep_validate_d08(
        root=root,
        config=config,
        audit=audit,
    )
    prereg_sha, _, prereg_summary = _read_frozen_preregistration(
        root=root,
        config=config,
        audit=audit,
    )
    execution_mode = _string(
        prereg_summary.get("execution_mode"),
        "validated execution mode",
    )
    h00_hashes = (
        _deep_validate_h00(root=root, config=config, audit=audit)
        if execution_mode == "human_authorized_demo"
        else None
    )
    authorization_sha, _ = _read_execution_authorization(
        root=root,
        config=config,
        preregistration_sha256=prereg_sha,
        execution_mode=execution_mode,
        audit=audit,
        h00_hashes=h00_hashes,
    )
    reference_sha = _read_reference_handoff(
        root=root,
        config=config,
        r03_sha256=r03_sha,
        r03_bank=r03_bank,
        d08_sha256=d08_sha,
        d08_completion=d08_completion,
        audit=audit,
    )
    predecessor_chain = _predecessor_chain_sha256(
        s108=s108,
        e01_sha256=e01_sha,
        c02_sha256=c02_sha,
        e02_sha256=e02_sha,
        c03_sha256=c03_sha,
        r03_sha256=r03_sha,
        d08_sha256=d08_sha,
    )
    return PreflightResult(
        status="ready_for_separately_authorized_e00_executor",
        config_sha256=static.config_sha256,
        execution_mode=execution_mode,
        preregistration_sha256=prereg_sha,
        authorization_sha256=authorization_sha,
        reference_handoff_sha256=reference_sha,
        predecessor_chain_sha256=predecessor_chain,
        audit=audit.as_dict(),
    )


def ensure_output_targets_absent(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> None:
    """Executor-side no-overwrite check; the static/full CLI never calls it."""

    root = workspace_root.resolve(strict=True)
    outputs = _mapping(config.get("outputs"), "outputs")
    seen: set[str] = set()
    for field, raw in outputs.items():
        if not field.endswith("_path"):
            continue
        relative = _relative_string(raw, f"outputs.{field}")
        if relative in seen:
            raise E00ContractError("duplicate E00 output target")
        seen.add(relative)
        target = _reject_symlink_chain(
            root,
            relative,
            f"outputs.{field}",
            require_leaf=False,
        )
        if target.exists() or target.is_symlink():
            raise E00ContractError(f"E00 output already exists: {relative}")


def config_only_result(
    static: StaticValidation,
    audit: AccessAudit,
) -> Mapping[str, Any]:
    return {
        "status": "valid_config_only",
        "implementation_status": "production_static_readiness_only",
        "config_sha256": static.config_sha256,
        "policy_sha256": static.policy_sha256,
        "source_pin_count": len(static.source_snapshots),
        **audit.as_dict(),
        "actual_input_audio_model_gpu_api_coaching_reference_rerecord_metric": False,
        "artifact_created": False,
        "completion_claim_allowed": False,
        "production_deployment_claim_allowed": False,
    }


def failure_result(error: E00ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "actual_input_audio_model_gpu_api_coaching_reference_rerecord_metric": False,
        "artifact_created": False,
        "completion_claim_allowed": False,
        "production_deployment_claim_allowed": False,
    }
