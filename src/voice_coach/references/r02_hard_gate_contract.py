"""Fail-closed production-static contract for the WP7 R02 hard gate.

This module intentionally does not implement audio decoding, model loading,
metric execution, GPU discovery, or artifact publication.  It proves that
R00 and a future completed R01 bundle are immutable and valid before any D1
audio location can be resolved, and it validates future blinded R02 reports.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from types import ModuleType
from typing import Any

import yaml  # type: ignore[import-untyped]

from voice_coach.references import r01_candidate_contract as r01


SCHEMA_VERSION = 1
STATIC_STATUS = "production_static_preflight_only"
TBD_R01 = "TBD_R01_COMPLETION"
TBD_PREREGISTRATION = "TBD_PREREGISTRATION"
TBD_ATTESTATION = "TBD_ATTESTATION"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CANDIDATE_ID_RE = re.compile(r"^r01c_[0-9a-f]{32}$")
SENTENCE_IDS = tuple(f"R00-{index:02d}" for index in range(1, 21))
GATE_NAMES = (
    "asr_content_match",
    "acoustic_artifact",
    "pause_rhythm",
    "target_phone_coverage",
)
SOURCE_KINDS = ("human_recording", "cosyvoice_tts")
COMPARATORS = {"le", "lt", "ge", "gt"}

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_r00",
    "predecessor_r01",
    "prerequisites",
    "hard_gates",
    "blindness",
    "leakage_boundary",
    "candidate_flow",
    "failure_and_r03",
    "execution_order",
    "outputs",
}

SOURCE_PATHS = (
    "artifacts/reference/r00/r00_sentence_card_v1.validation.json",
    "configs/experiments/r01_human_cosyvoice_candidates_v1.yaml",
    "configs/reference/r00_sentence_card_v1.schema.json",
    "configs/reference/r01_candidate_manifest_v1.schema.json",
    "configs/reference/r01_cosyvoice_model_attestation_v1.template.json",
    "configs/reference/r01_human_recording_attestation_v1.template.json",
    "configs/reference/r02_artifact_attestation_v1.template.json",
    "configs/reference/r02_asr_attestation_v1.template.json",
    "configs/reference/r02_gate_preregistration_v1.template.json",
    "configs/reference/r02_hard_gate_report_v1.schema.json",
    "data/reference/r00/r00_sentence_card_v1.json",
    "scripts/check_project_constraints.py",
    "scripts/validate_r00_sentence_card.py",
    "src/voice_coach/references/r01_candidate_contract.py",
)

EXPECTED_EXECUTION_ORDER = (
    "validate_r02_c1_config_source_schema_and_templates",
    "deep_validate_r00_public_or_aggregate_completion",
    "require_exact_r01_completion_commit",
    "deep_validate_r01_registry_manifest_and_human_cosyvoice_attestations",
    "verify_r00_r01_and_r02_source_snapshots_against_toctou",
    "require_locked_r02_preregistration",
    "validate_asr_artifact_pipeline_model_checkpoint_and_license_attestations",
    "resolve_d1_candidate_manifest_and_audio_only_after_all_prior_gates",
    "build_source_blind_execution_manifest_and_separate_lineage_sidecar",
    "stop_ready_for_separate_metric_execution_package",
)

R01_COMPLETION_KEYS = {
    "schema_version",
    "status",
    "run_id",
    "config_sha256",
    "candidate_manifest_sha256",
    "registry_sha256",
    "human_attestation_sha256",
    "cosyvoice_attestation_sha256",
    "sentence_count",
    "candidate_count",
    "audio_count",
    "r02_hard_gate_status",
    "publication_allowed",
}

R01_REGISTRY_KEYS = {
    "schema_version",
    "run_id",
    "status",
    "config_sha256",
    "candidate_manifest_sha256",
    "human_attestation_sha256",
    "cosyvoice_attestation_sha256",
    "sentence_count",
    "candidate_count",
    "audio_count",
    "failure",
    "publication_allowed",
}

REPORT_KEYS = {
    "schema_version",
    "report_id",
    "status",
    "privacy_zone",
    "r01_completion_commit_sha256",
    "r01_candidate_manifest_sha256",
    "preregistration_sha256",
    "asr_attestation_sha256",
    "artifact_attestation_sha256",
    "blind_gate_input",
    "source_labels_present",
    "same_gate_for_human_and_tts",
    "input_candidate_count",
    "results",
    "eligible_candidate_ids",
    "quarantined_candidate_ids",
    "sentence_summaries",
    "aggregate_transcript_error",
    "failed_candidates_preserved_in_d1",
    "r03_may_read_only_eligible_candidates",
    "publication_allowed",
    "r02_completion_claim_allowed",
}

RESULT_KEYS = {
    "candidate_id",
    "sentence_id",
    "target_text_sha256",
    *GATE_NAMES,
    "overall_decision",
}

GATE_RESULT_KEYS = {
    "metric_id",
    "metric_definition_sha256",
    "value",
    "threshold_operator",
    "threshold_value",
    "support",
    "decision",
    "evidence_sha256",
}

SENTENCE_SUMMARY_KEYS = {
    "sentence_id",
    "input_count",
    "eligible_count",
    "quarantined_count",
    "minimum_eligible_support_met",
}

AGGREGATE_METRIC_KEYS = {
    "metric_id",
    "metric_definition_sha256",
    "aggregation",
    "value",
    "threshold_operator",
    "threshold_value",
    "support",
    "decision",
    "evidence_sha256",
}

R02_COMPLETION_KEYS = {
    "schema_version",
    "status",
    "run_id",
    "config_sha256",
    "r01_completion_commit_sha256",
    "r01_candidate_manifest_sha256",
    "preregistration_sha256",
    "asr_attestation_sha256",
    "artifact_attestation_sha256",
    "gate_report_sha256",
    "blinded_execution_manifest_sha256",
    "source_lineage_sidecar_sha256",
    "eligible_manifest_sha256",
    "quarantine_manifest_sha256",
    "registry_sha256",
    "candidate_count",
    "eligible_count",
    "quarantined_count",
    "failed_candidates_preserved",
    "r03_input_pass_only",
    "publication_allowed",
}

R02_REGISTRY_KEYS = {
    "schema_version",
    "run_id",
    "status",
    "config_sha256",
    "r01_completion_commit_sha256",
    "gate_report_sha256",
    "blinded_execution_manifest_sha256",
    "source_lineage_sidecar_sha256",
    "eligible_manifest_sha256",
    "quarantine_manifest_sha256",
    "candidate_count",
    "eligible_count",
    "quarantined_count",
    "failure",
    "publication_allowed",
}

PARTITION_MANIFEST_KEYS = {
    "schema_version",
    "status",
    "privacy_zone",
    "gate_report_sha256",
    "candidate_count",
    "candidate_ids",
    "source_labels_present",
    "r03_input_allowed",
    "publication_allowed",
}

BLINDED_EXECUTION_KEYS = {
    "schema_version",
    "status",
    "privacy_zone",
    "r01_candidate_manifest_sha256",
    "candidate_count",
    "source_labels_present",
    "candidate_rows",
}

BLINDED_EXECUTION_ROW_KEYS = {
    "candidate_id",
    "sentence_id",
    "target_text_sha256",
    "audio_object_id_sha256",
    "audio_sha256",
}

LINEAGE_SIDECAR_KEYS = {
    "schema_version",
    "status",
    "privacy_zone",
    "r01_candidate_manifest_sha256",
    "candidate_count",
    "sealed_from_gate_implementation",
    "unblind_only_after_immutable_gate_commit",
    "candidate_rows",
}

LINEAGE_ROW_KEYS = {
    "candidate_id",
    "source_kind",
    "source_attestation_sha256",
    "speaker_id_sha256",
    "preservation_group_id_sha256",
    "speed_bucket",
    "pitch_bucket",
}

FORBIDDEN_REPORT_KEYS = {
    "source_kind",
    "source_label",
    "speaker_id_sha256",
    "speaker_name",
    "target_text",
    "transcript",
    "audio_path",
    "source_path",
    "recording_path",
    "s1_score",
    "s1_prediction",
    "s2_score",
    "s2_prediction",
    "gold_label",
    "locked_test",
    "mos",
    "cmos",
}

PREREGISTRATION_KEYS = {
    "schema_version",
    "preregistration_id",
    "status",
    "approved_document_sha256",
    "approved_by_external_human",
    "locked_before_r02_audio_access",
    "candidate_policy",
    "gates",
    "multiplicity_and_missingness",
    "blindness_and_leakage",
    "license_decisions",
    "completion_claim_allowed",
}

ASR_ATTESTATION_KEYS = {
    "schema_version",
    "attestation_id",
    "status",
    "role",
    "plan_backed_model_family",
    "exact_model_id",
    "exact_checkpoint_revision",
    "checkpoint_manifest_sha256",
    "code_revision",
    "code_license_spdx",
    "code_license_evidence_sha256",
    "checkpoint_license_spdx",
    "checkpoint_license_evidence_sha256",
    "research_use_authorized",
    "redistribution_authorized",
    "normalization_id",
    "normalization_spec_sha256",
    "segmentation_id",
    "segmentation_spec_sha256",
    "language",
    "task",
    "local_files_only",
    "network_download_allowed",
    "local_bundle_manifest_sha256",
    "runtime_environment_lock_sha256",
    "s1_or_s2_artifact_used",
    "gold_or_locked_test_artifact_used",
    "approved_by_external_human",
    "completion_claim_allowed",
}

ARTIFACT_ATTESTATION_KEYS = {
    "schema_version",
    "attestation_id",
    "status",
    "acoustic_artifact",
    "pause_rhythm_pipeline",
    "target_phone_pipeline",
    "runtime",
    "same_pipeline_for_human_and_tts",
    "source_conditioned_normalization_allowed",
    "approved_by_external_human",
    "completion_claim_allowed",
}


class R02ContractError(ValueError):
    """A fail-closed contract violation with stage and access evidence."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "static_config",
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
            raise R02ContractError(f"duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(slots=True)
class AccessAudit:
    """Counters proving predecessor and protected-input ordering."""

    source_pin_read_count: int = 0
    r00_validator_load_count: int = 0
    r00_deep_validation_count: int = 0
    r00_public_or_aggregate_evidence_read_count: int = 0
    r00_target_rule_read_count: int = 0
    r00_raw_audio_read_count: int = 0
    r00_row_level_read_count: int = 0
    r01_completion_commit_read_attempt_count: int = 0
    r01_completion_commit_read_count: int = 0
    r01_registry_read_count: int = 0
    r01_candidate_manifest_read_count: int = 0
    r01_human_attestation_read_count: int = 0
    r01_cosyvoice_attestation_read_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    asr_attestation_read_attempt_count: int = 0
    asr_attestation_read_count: int = 0
    artifact_attestation_read_attempt_count: int = 0
    artifact_attestation_read_count: int = 0
    d1_candidate_manifest_path_resolution_count: int = 0
    d1_audio_root_path_resolution_count: int = 0
    d1_audio_read_count: int = 0
    model_path_resolution_count: int = 0
    model_metadata_read_count: int = 0
    model_weight_read_count: int = 0
    model_download_count: int = 0
    gpu_access_count: int = 0
    metric_execution_count: int = 0
    s1_artifact_read_count: int = 0
    s2_artifact_read_count: int = 0
    gold_or_locked_test_read_count: int = 0
    source_label_gate_input_read_count: int = 0
    output_path_resolution_count: int = 0
    output_write_count: int = 0
    publication_count: int = 0

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
class R00Evidence:
    report_snapshot: FileSnapshot
    card_snapshot: FileSnapshot
    sentence_hashes: Mapping[str, str]
    target_rules: Mapping[str, Mapping[str, tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class R01Evidence:
    commit_snapshot: FileSnapshot
    registry_snapshot: FileSnapshot
    manifest_snapshot: FileSnapshot
    human_attestation_snapshot: FileSnapshot
    cosyvoice_attestation_snapshot: FileSnapshot
    candidate_rows: tuple[Mapping[str, Any], ...]
    candidate_count: int
    sentence_count: int


@dataclass(frozen=True, slots=True)
class R02CompletedEvidence:
    commit_snapshot: FileSnapshot
    registry_snapshot: FileSnapshot
    report_snapshot: FileSnapshot
    blinded_execution_manifest_snapshot: FileSnapshot
    source_lineage_sidecar_snapshot: FileSnapshot
    eligible_manifest_snapshot: FileSnapshot
    quarantine_manifest_snapshot: FileSnapshot
    report: Mapping[str, Any]
    eligible_candidate_ids: tuple[str, ...]
    quarantined_candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PrerequisiteEvidence:
    preregistration_snapshot: FileSnapshot
    asr_attestation_snapshot: FileSnapshot
    artifact_attestation_snapshot: FileSnapshot
    preregistration: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: str
    config_sha256: str
    policy_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise R02ContractError(f"{name} must be a string-keyed mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise R02ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise R02ContractError(f"{name} must be a non-empty string")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise R02ContractError(f"{name} must be boolean")
    return value


def _integer(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise R02ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise R02ContractError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise R02ContractError(f"{name} must be <= {maximum}")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R02ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise R02ContractError(f"{name} must be finite")
    return result


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if SHA256_RE.fullmatch(text) is None:
        raise R02ContractError(f"{name} must be lowercase SHA-256")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise R02ContractError(
            f"{name} mismatch: expected {expected!r}, observed {actual!r}"
        )


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise R02ContractError(
            f"{name} keys differ: expected {sorted(expected)}, observed {sorted(value)}"
        )


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise R02ContractError(f"{name} contains a non-finite number")
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
            raise R02ContractError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise R02ContractError(f"non-finite JSON constant is prohibited: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    _reject_nonfinite_tree(value, "canonical JSON")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _load_json_bytes(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R02ContractError(f"{name} is not strict UTF-8 JSON: {exc}") from exc
    payload = _mapping(decoded, name)
    _reject_nonfinite_tree(payload, name)
    return payload


def load_json(path: Path) -> Mapping[str, Any]:
    try:
        return _load_json_bytes(path.read_bytes(), str(path))
    except OSError as exc:
        raise R02ContractError(f"cannot read JSON {path}: {exc}") from exc


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        loaded = yaml.load(raw, Loader=_UniqueKeyLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise R02ContractError(f"cannot read strict YAML {path}: {exc}") from exc
    payload = _mapping(loaded, str(path))
    _reject_nonfinite_tree(payload, str(path))
    return payload


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or "\\" in text
        or text in {".", ""}
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise R02ContractError(f"{name} must be a normalized relative POSIX path")
    return text


def _safe_workspace_path(root: Path, relative: str, name: str) -> Path:
    _relative_string(relative, name)
    root_stat = root.stat()
    if not root.is_dir():
        raise R02ContractError("workspace root must be a directory")
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            item_stat = current.lstat()
        except FileNotFoundError:
            return current
        if os.path.islink(current):
            raise R02ContractError(f"{name} contains a symlink: {relative}")
        if item_stat.st_dev != root_stat.st_dev and current != root:
            raise R02ContractError(f"{name} crosses a filesystem boundary: {relative}")
    try:
        current.relative_to(root)
    except ValueError as exc:
        raise R02ContractError(f"{name} escapes workspace") from exc
    return current


def _read_file(root: Path, relative: str, name: str) -> tuple[bytes, FileSnapshot]:
    path = _safe_workspace_path(root, relative, name)
    try:
        before = path.stat()
    except OSError as exc:
        raise R02ContractError(f"cannot stat {name}: {relative}: {exc}") from exc
    if not path.is_file():
        raise R02ContractError(f"{name} must be a regular file: {relative}")
    try:
        data = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise R02ContractError(f"cannot read {name}: {relative}: {exc}") from exc
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
    if identity_before != identity_after or len(data) != after.st_size:
        raise R02ContractError(f"{name} changed during read: {relative}")
    digest = hashlib.sha256(data).hexdigest()
    return data, FileSnapshot(
        path=relative,
        sha256=digest,
        size_bytes=after.st_size,
        device=after.st_dev,
        inode=after.st_ino,
        modified_ns=after.st_mtime_ns,
    )


def _snapshot_file(root: Path, relative: str, name: str) -> FileSnapshot:
    return _read_file(root, relative, name)[1]


def _verify_snapshot(root: Path, expected: FileSnapshot, name: str) -> None:
    observed = _snapshot_file(root, expected.path, name)
    if observed != expected:
        raise R02ContractError(f"{name} changed after validation: {expected.path}")


def _contains_marker(value: Any, markers: set[str]) -> bool:
    if isinstance(value, str):
        return value in markers
    if isinstance(value, Mapping):
        return any(_contains_marker(item, markers) for item in value.values())
    if isinstance(value, list):
        return any(_contains_marker(item, markers) for item in value)
    return False


def _collect_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            result.add(str(key))
            result.update(_collect_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            result.update(_collect_keys(nested))
    return result


def _validate_reference_templates(
    payloads: Mapping[str, Mapping[str, Any]],
) -> None:
    prereg = payloads["configs/reference/r02_gate_preregistration_v1.template.json"]
    _expect(
        prereg.get("schema_version"),
        "voice_coach.r02_gate_preregistration.v1",
        "preregistration template schema",
    )
    _expect(prereg.get("status"), TBD_PREREGISTRATION, "preregistration status")
    _expect(
        prereg.get("approved_by_external_human"),
        False,
        "preregistration approval",
    )
    _expect(
        prereg.get("locked_before_r02_audio_access"),
        False,
        "preregistration lock",
    )
    _expect(
        prereg.get("completion_claim_allowed"),
        False,
        "preregistration completion",
    )
    candidate_policy = _mapping(
        prereg.get("candidate_policy"), "preregistration candidate policy"
    )
    _expect(
        candidate_policy.get("input_candidates_per_sentence_minimum"),
        8,
        "preregistration candidate minimum",
    )
    _expect(
        candidate_policy.get("input_candidates_per_sentence_maximum"),
        16,
        "preregistration candidate maximum",
    )
    _expect(
        candidate_policy.get("same_gate_for_human_and_tts"),
        True,
        "preregistration same source gate",
    )
    _expect(
        candidate_policy.get("source_conditioned_thresholds_allowed"),
        False,
        "source-conditioned thresholds",
    )
    _expect(
        candidate_policy.get("aggregate_transcript_error_completion_boundary"),
        0.02,
        "aggregate transcript boundary",
    )
    _expect(
        candidate_policy.get("aggregate_boundary_is_not_a_candidate_threshold"),
        True,
        "aggregate boundary role",
    )
    gates = _mapping(prereg.get("gates"), "preregistration gates")
    _expect(set(gates), set(GATE_NAMES), "preregistration gate names")
    for gate_name in GATE_NAMES:
        gate = _mapping(gates.get(gate_name), f"preregistration {gate_name}")
        for field_name in (
            "metric_id",
            "metric_definition_sha256",
            "threshold_operator",
            "threshold_value",
            "normalization_id",
            "segmentation_id",
            "minimum_support",
        ):
            _expect(
                gate.get(field_name),
                TBD_PREREGISTRATION,
                f"template {gate_name}.{field_name}",
            )
    licenses = _mapping(
        prereg.get("license_decisions"),
        "preregistration license decisions",
    )
    if not licenses or any(value != TBD_ATTESTATION for value in licenses.values()):
        raise R02ContractError(
            "preregistration license decisions must remain TBD_ATTESTATION"
        )

    asr = payloads["configs/reference/r02_asr_attestation_v1.template.json"]
    _expect(
        asr.get("schema_version"),
        "voice_coach.r02_asr_attestation.v1",
        "ASR template schema",
    )
    _expect(asr.get("status"), TBD_ATTESTATION, "ASR template status")
    _expect(
        asr.get("plan_backed_model_family"),
        "openai/whisper-large-v3",
        "ASR plan-backed family",
    )
    _expect(asr.get("network_download_allowed"), False, "ASR network")
    _expect(asr.get("local_files_only"), True, "ASR local-only")
    _expect(asr.get("s1_or_s2_artifact_used"), False, "ASR S1/S2 use")
    _expect(
        asr.get("gold_or_locked_test_artifact_used"),
        False,
        "ASR gold/locked use",
    )
    _expect(asr.get("completion_claim_allowed"), False, "ASR completion")
    for field_name in (
        "exact_model_id",
        "exact_checkpoint_revision",
        "checkpoint_manifest_sha256",
        "code_revision",
        "code_license_spdx",
        "code_license_evidence_sha256",
        "checkpoint_license_spdx",
        "checkpoint_license_evidence_sha256",
        "normalization_id",
        "normalization_spec_sha256",
        "segmentation_id",
        "segmentation_spec_sha256",
        "local_bundle_manifest_sha256",
        "runtime_environment_lock_sha256",
    ):
        _expect(asr.get(field_name), TBD_ATTESTATION, f"ASR template {field_name}")

    artifact = payloads["configs/reference/r02_artifact_attestation_v1.template.json"]
    _expect(
        artifact.get("schema_version"),
        "voice_coach.r02_artifact_attestation.v1",
        "artifact template schema",
    )
    _expect(artifact.get("status"), TBD_ATTESTATION, "artifact template status")
    _expect(
        artifact.get("same_pipeline_for_human_and_tts"),
        True,
        "artifact same pipeline",
    )
    _expect(
        artifact.get("source_conditioned_normalization_allowed"),
        False,
        "artifact source-conditioned normalization",
    )
    _expect(
        artifact.get("approved_by_external_human"),
        False,
        "artifact approval",
    )
    _expect(
        artifact.get("completion_claim_allowed"),
        False,
        "artifact completion",
    )
    acoustic = _mapping(artifact.get("acoustic_artifact"), "artifact acoustic model")
    _expect(
        acoustic.get("plan_backed_model_family"),
        "UTMOSv2",
        "artifact plan-backed family",
    )
    phone = _mapping(artifact.get("target_phone_pipeline"), "target phone pipeline")
    _expect(
        phone.get("r00_target_rules_are_audit_targets_only"),
        True,
        "R00 target rule role",
    )
    _expect(phone.get("s1_prediction_or_score_used"), False, "phone S1 use")
    _expect(phone.get("s2_prediction_or_score_used"), False, "phone S2 use")
    _expect(
        phone.get("gold_or_locked_test_artifact_used"),
        False,
        "phone gold/locked use",
    )
    runtime = _mapping(artifact.get("runtime"), "artifact runtime")
    _expect(runtime.get("network_download_allowed"), False, "artifact network")
    _expect(runtime.get("local_files_only"), True, "artifact local-only")

    schema = payloads["configs/reference/r02_hard_gate_report_v1.schema.json"]
    _expect(
        schema.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        "report schema dialect",
    )
    _expect(schema.get("additionalProperties"), False, "report additionalProperties")
    required = _list(schema.get("required"), "report schema required")
    _expect(set(required), REPORT_KEYS, "report schema required fields")
    properties = _mapping(schema.get("properties"), "report schema properties")
    _expect(set(properties), REPORT_KEYS, "report schema properties")
    leaked = FORBIDDEN_REPORT_KEYS & _collect_keys(schema)
    if leaked:
        raise R02ContractError(
            f"report schema contains forbidden fields: {sorted(leaked)}"
        )
    _expect(
        _mapping(properties["source_labels_present"], "source label property").get(
            "const"
        ),
        False,
        "report source labels",
    )
    _expect(
        _mapping(properties["publication_allowed"], "publication property").get(
            "const"
        ),
        False,
        "report publication",
    )
    _expect(
        _mapping(
            properties["failed_candidates_preserved_in_d1"],
            "failure preservation property",
        ).get("const"),
        True,
        "failed candidate preservation",
    )


def _validate_static_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_KEYS, "R02 config")
    _expect(config.get("schema_version"), SCHEMA_VERSION, "config schema version")

    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(experiment.get("id"), "r02_candidate_hard_gate_v1", "experiment ID")
    _expect(
        experiment.get("stage"),
        "wp7_r02_private_blinded_hard_gate",
        "experiment stage",
    )
    _expect(
        experiment.get("implementation_status"),
        STATIC_STATUS,
        "implementation status",
    )
    for field_name in (
        "actual_audio_read_allowed",
        "actual_model_read_allowed",
        "actual_model_download_allowed",
        "actual_gpu_access_allowed",
        "actual_metric_execution_allowed",
        "actual_output_write_allowed",
        "publication_allowed",
        "completion_claim_allowed",
    ):
        _expect(experiment.get(field_name), False, f"experiment {field_name}")

    predecessor_r00 = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    _expect(predecessor_r00.get("deep_validation_required"), True, "R00 deep gate")
    _expect(predecessor_r00.get("required_status"), "valid", "R00 status")
    _expect(
        predecessor_r00.get("required_sentence_count"),
        20,
        "R00 sentence count",
    )
    _expect(
        predecessor_r00.get("required_r00_completion_claim_allowed"),
        True,
        "R00 completion",
    )
    _expect(
        predecessor_r00.get("target_phones_and_rules_may_be_read_for_coverage_audit"),
        True,
        "R00 target coverage role",
    )
    _expect(
        predecessor_r00.get("target_phones_and_rules_are_not_gold_test_labels"),
        True,
        "R00 target non-gold role",
    )
    _expect(
        predecessor_r00.get("raw_audio_or_row_level_access_allowed"),
        False,
        "R00 protected access",
    )

    predecessor_r01 = _mapping(config.get("predecessor_r01"), "predecessor_r01")
    _expect(predecessor_r01.get("deep_validation_required"), True, "R01 deep gate")
    _expect(
        predecessor_r01.get("completion_schema_version"),
        "voice_coach.r01_candidate_completion.v1",
        "R01 completion schema",
    )
    _expect(predecessor_r01.get("required_status"), "completed", "R01 status")
    _expect(
        predecessor_r01.get("required_sentence_count"),
        20,
        "R01 sentence count",
    )
    _expect(
        predecessor_r01.get("candidate_count_minimum"),
        160,
        "R01 candidate minimum",
    )
    _expect(
        predecessor_r01.get("candidate_count_maximum"),
        320,
        "R01 candidate maximum",
    )
    _expect(
        predecessor_r01.get("candidates_per_sentence_minimum"),
        8,
        "R01 per-sentence minimum",
    )
    _expect(
        predecessor_r01.get("candidates_per_sentence_maximum"),
        16,
        "R01 per-sentence maximum",
    )
    _expect(
        predecessor_r01.get("exact_equal_source_balance_within_sentence"),
        True,
        "R01 source balance",
    )
    _expect(
        predecessor_r01.get(
            "d1_audio_may_resolve_only_after_all_r01_evidence_is_deep_valid"
        ),
        True,
        "R01 before audio order",
    )
    completion_hashes = (
        predecessor_r01.get("completion_commit_sha256"),
        predecessor_r01.get("registry_sha256"),
        predecessor_r01.get("candidate_manifest_sha256"),
        predecessor_r01.get("human_attestation_sha256"),
        predecessor_r01.get("cosyvoice_attestation_sha256"),
    )
    tbd_count = sum(value == TBD_R01 for value in completion_hashes)
    if tbd_count not in {0, len(completion_hashes)}:
        raise R02ContractError("R01 completion hashes must be all TBD or all exact")
    if tbd_count == 0:
        for index, digest in enumerate(completion_hashes):
            _sha256(digest, f"R01 completion hash {index}")

    prerequisites = _mapping(config.get("prerequisites"), "prerequisites")
    _expect(
        prerequisites.get("resolve_before_d1_audio"),
        True,
        "prerequisites before audio",
    )
    _expect(
        prerequisites.get(
            "exact_metric_threshold_model_checkpoint_normalization_"
            "segmentation_support_and_license_required"
        ),
        True,
        "exact gate prerequisites",
    )
    prereg = _mapping(prerequisites.get("preregistration"), "preregistration")
    asr = _mapping(prerequisites.get("asr_attestation"), "ASR attestation")
    artifact = _mapping(
        prerequisites.get("artifact_attestation"), "artifact attestation"
    )
    prerequisite_hashes = (
        prereg.get("sha256"),
        asr.get("sha256"),
        artifact.get("sha256"),
    )
    expected_markers = (
        TBD_PREREGISTRATION,
        TBD_ATTESTATION,
        TBD_ATTESTATION,
    )
    marker_count = sum(
        value == marker
        for value, marker in zip(prerequisite_hashes, expected_markers, strict=True)
    )
    if marker_count not in {0, len(prerequisite_hashes)}:
        raise R02ContractError("R02 prerequisite hashes must be all TBD or all exact")
    if marker_count == 0:
        for index, digest in enumerate(prerequisite_hashes):
            _sha256(digest, f"R02 prerequisite hash {index}")

    hard_gates = _mapping(config.get("hard_gates"), "hard_gates")
    _expect(
        tuple(_list(hard_gates.get("order"), "gate order")), GATE_NAMES, "gate order"
    )
    _expect(
        hard_gates.get("pass_rule"),
        "all_four_gates_must_pass",
        "gate pass rule",
    )
    _expect(
        hard_gates.get("missing_nonfinite_or_insufficient_support_decision"),
        "fail",
        "missing/support rule",
    )
    _expect(
        hard_gates.get("posthoc_threshold_change_allowed"),
        False,
        "posthoc threshold",
    )
    gate_tbd_count = 0
    for gate_name in GATE_NAMES:
        gate = _mapping(hard_gates.get(gate_name), gate_name)
        gate_fields = (
            gate.get("metric_id"),
            gate.get("threshold"),
            gate.get("normalization_id"),
            gate.get("segmentation_id"),
            gate.get("minimum_support"),
        )
        current_tbd = sum(value == TBD_PREREGISTRATION for value in gate_fields)
        if current_tbd not in {0, len(gate_fields)}:
            raise R02ContractError(f"{gate_name} preregistration is partially fixed")
        if current_tbd:
            gate_tbd_count += 1
        else:
            _string(gate.get("metric_id"), f"{gate_name} metric")
            threshold = _mapping(gate.get("threshold"), f"{gate_name} threshold")
            _expect_keys(threshold, {"operator", "value"}, f"{gate_name} threshold")
            if threshold.get("operator") not in COMPARATORS:
                raise R02ContractError(f"{gate_name} threshold operator is invalid")
            _number(threshold.get("value"), f"{gate_name} threshold value")
            _string(gate.get("normalization_id"), f"{gate_name} normalization")
            _string(gate.get("segmentation_id"), f"{gate_name} segmentation")
            _integer(
                gate.get("minimum_support"),
                f"{gate_name} support",
                minimum=1,
            )
        if current_tbd and not _contains_marker(gate, {TBD_ATTESTATION}):
            raise R02ContractError(
                f"{gate_name} must retain model/license attestation markers"
            )
        if not current_tbd and _contains_marker(
            gate, {TBD_ATTESTATION, TBD_PREREGISTRATION}
        ):
            raise R02ContractError(f"{gate_name} has unresolved attestation fields")
    if gate_tbd_count not in {0, len(GATE_NAMES)}:
        raise R02ContractError("all four gates must be fixed together")
    if (marker_count == 0) != (gate_tbd_count == 0):
        raise R02ContractError(
            "prerequisite hashes and gate definitions must be fixed together"
        )

    blindness = _mapping(config.get("blindness"), "blindness")
    _expect(blindness.get("gate_input_is_source_blind"), True, "blind gate input")
    _expect(
        blindness.get("source_kind_in_gate_input_allowed"),
        False,
        "source label in gate",
    )
    _expect(
        blindness.get(
            "human_and_tts_use_identical_gate_metric_threshold_normalization_and_support"
        ),
        True,
        "same human/TTS gate",
    )
    _expect(
        blindness.get("source_conditioned_exception_allowed"),
        False,
        "source exception",
    )
    _expect(
        blindness.get("unblinding_before_immutable_gate_commit_allowed"),
        False,
        "early unblinding",
    )

    leakage = _mapping(config.get("leakage_boundary"), "leakage boundary")
    for field_name in (
        "s1_prediction_score_checkpoint_or_threshold_allowed",
        "s2_prediction_score_checkpoint_or_threshold_allowed",
        "s1_or_s2_training_dev_stack_calibration_manifest_allowed",
        "gold_test_label_or_annotation_allowed",
        "locked_test_split_allowed",
        "learner_error_label_allowed",
    ):
        _expect(leakage.get(field_name), False, f"leakage {field_name}")
    _expect(
        leakage.get("r00_sentence_target_phones_and_rules_for_coverage_audit_only"),
        True,
        "R00 coverage-only rule",
    )
    _expect(
        leakage.get("r00_target_rules_must_not_be_treated_as_observed_realization"),
        True,
        "R00 target/realization separation",
    )

    flow = _mapping(config.get("candidate_flow"), "candidate_flow")
    _expect(
        tuple(_list(flow.get("sentence_ids"), "sentence IDs")),
        SENTENCE_IDS,
        "sentence IDs",
    )
    per_sentence = _mapping(
        flow.get("input_candidates_per_sentence"), "input candidates per sentence"
    )
    _expect(dict(per_sentence), {"minimum": 8, "maximum": 16}, "input count")
    flow_prereg_values = (
        flow.get("minimum_eligible_candidates_per_sentence"),
        flow.get("aggregate_transcript_error_metric_id"),
        flow.get("aggregate_transcript_error_metric_definition_sha256"),
        flow.get("aggregate_transcript_error_aggregation"),
    )
    flow_tbd_count = sum(value == TBD_PREREGISTRATION for value in flow_prereg_values)
    if flow_tbd_count not in {0, len(flow_prereg_values)}:
        raise R02ContractError("candidate-flow preregistration is partially fixed")
    if flow_tbd_count:
        if gate_tbd_count != len(GATE_NAMES):
            raise R02ContractError("candidate-flow and gates must be fixed together")
    else:
        _integer(
            flow.get("minimum_eligible_candidates_per_sentence"),
            "minimum eligible support",
            minimum=1,
            maximum=16,
        )
        _string(
            flow.get("aggregate_transcript_error_metric_id"),
            "aggregate transcript metric",
        )
        _sha256(
            flow.get("aggregate_transcript_error_metric_definition_sha256"),
            "aggregate transcript definition",
        )
        _string(
            flow.get("aggregate_transcript_error_aggregation"),
            "aggregate transcript aggregation",
        )
    _expect(
        flow.get("aggregate_transcript_error_threshold_operator"),
        "le",
        "aggregate transcript operator",
    )
    _expect(
        flow.get("aggregate_transcript_error_completion_boundary"),
        0.02,
        "aggregate transcript boundary",
    )
    _expect(
        flow.get("aggregate_boundary_is_not_a_candidate_threshold"),
        True,
        "aggregate boundary scope",
    )

    failure = _mapping(config.get("failure_and_r03"), "failure_and_r03")
    _expect(
        failure.get("failed_candidate_audio_and_r01_lineage_preserved_in_d1"),
        True,
        "failure preservation",
    )
    _expect(
        failure.get("failed_candidate_delete_move_or_overwrite_allowed"),
        False,
        "failure mutation",
    )
    _expect(
        failure.get("r03_input_must_be_exact_eligible_partition"),
        True,
        "R03 eligible-only input",
    )
    _expect(
        failure.get("r03_may_read_quarantined_candidates"),
        False,
        "R03 quarantine access",
    )
    _expect(
        failure.get("r02_pass_does_not_allow_publication"),
        True,
        "R02 publication boundary",
    )
    _expect(
        failure.get("tts_and_human_are_subject_to_same_r02_gate"),
        True,
        "human/TTS same gate",
    )

    _expect(
        tuple(_list(config.get("execution_order"), "execution order")),
        EXPECTED_EXECUTION_ORDER,
        "execution order",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    _expect(
        outputs.get("report_schema_path"),
        "configs/reference/r02_hard_gate_report_v1.schema.json",
        "report schema path",
    )
    _expect(outputs.get("atomic_publish_required"), True, "atomic publish")
    _expect(outputs.get("no_overwrite"), True, "no-overwrite")
    _expect(outputs.get("failed_candidates_preserved"), True, "failed preservation")
    _expect(
        outputs.get("write_enabled_in_this_package"),
        False,
        "static package write",
    )


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    """Validate C1/public sources and policy without runtime path resolution."""

    access = audit if audit is not None else AccessAudit()
    _validate_static_values(config)
    try:
        if not config_path.is_absolute():
            raise ValueError
        config_relative = config_path.relative_to(workspace_root).as_posix()
    except ValueError as exc:
        raise R02ContractError("config must be an absolute workspace file") from exc
    config_snapshot = _snapshot_file(workspace_root, config_relative, "R02 config")

    canonical = _mapping(config.get("canonical_sources"), "canonical_sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_or_aggregate_only",
        "canonical source scope",
    )
    pins = _list(canonical.get("pins"), "canonical source pins")
    paths: list[str] = []
    snapshots: list[FileSnapshot] = []
    payloads: dict[str, Mapping[str, Any]] = {}
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index}.path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index}.sha256")
        data, snapshot = _read_file(
            workspace_root, relative, f"canonical source {index}"
        )
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_sha:
            raise R02ContractError(
                f"canonical source SHA-256 mismatch: {relative}",
                audit=access,
            )
        paths.append(relative)
        snapshots.append(snapshot)
        if relative.endswith(".json"):
            payloads[relative] = _load_json_bytes(data, relative)
    _expect(tuple(paths), SOURCE_PATHS, "canonical source paths/order")
    if paths != sorted(paths) or len(set(paths)) != len(paths):
        raise R02ContractError(
            "canonical source pins must be unique and sorted",
            audit=access,
        )
    _validate_reference_templates(payloads)

    source_set = set(paths)
    outputs = _mapping(config.get("outputs"), "outputs")
    output_fields = (
        "blinded_execution_manifest_path",
        "source_lineage_sidecar_path",
        "gate_report_path",
        "eligible_manifest_path",
        "quarantine_manifest_path",
        "registry_path",
        "completion_commit_path",
        "failure_report_path",
    )
    output_paths: list[str] = []
    for field_name in output_fields:
        relative = _relative_string(outputs.get(field_name), f"outputs.{field_name}")
        if relative in source_set:
            raise R02ContractError(f"output overlaps canonical source: {relative}")
        target_parts = PurePosixPath(relative).parts
        if any(
            PurePosixPath(source).parts[: len(target_parts)] == target_parts
            for source in source_set
        ):
            raise R02ContractError(f"output contains canonical source: {relative}")
        if any(
            target_parts[: len(PurePosixPath(source).parts)]
            == PurePosixPath(source).parts
            for source in source_set
        ):
            raise R02ContractError(
                f"output descends from canonical source file: {relative}"
            )
        output_paths.append(relative)
    if len(set(output_paths)) != len(output_paths):
        raise R02ContractError("output paths must be unique")

    policy = {
        key: config[key]
        for key in (
            "experiment",
            "predecessor_r00",
            "predecessor_r01",
            "prerequisites",
            "hard_gates",
            "blindness",
            "leakage_boundary",
            "candidate_flow",
            "failure_and_r03",
            "execution_order",
            "outputs",
        )
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        source_snapshots=tuple(snapshots),
    )


def _source_snapshot(static: StaticValidation, relative: str) -> FileSnapshot:
    for snapshot in static.source_snapshots:
        if snapshot.path == relative:
            return snapshot
    raise R02ContractError(f"required source is not pinned: {relative}")


def _load_r00_validator(
    *,
    workspace_root: Path,
    relative: str,
    audit: AccessAudit,
) -> ModuleType:
    path = _safe_workspace_path(workspace_root, relative, "R00 validator")
    spec = importlib.util.spec_from_file_location(
        "_voice_coach_r02_r00_validator_"
        + hashlib.sha256(str(path).encode()).hexdigest(),
        path,
    )
    if spec is None or spec.loader is None:
        raise R02ContractError(
            "cannot load R00 validator",
            stage="predecessor_r00",
            audit=audit,
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise R02ContractError(
            f"R00 validator import failed: {exc}",
            stage="predecessor_r00",
            audit=audit,
        ) from exc
    audit.r00_validator_load_count += 1
    return module


def deep_validate_r00(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    static: StaticValidation,
    audit: AccessAudit,
) -> R00Evidence:
    """Recompute R00 and expose only declared target coverage rules."""

    raw = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    validator_relative = _relative_string(
        raw.get("validator_path"), "R00 validator path"
    )
    validator_snapshot = _source_snapshot(static, validator_relative)
    _verify_snapshot(workspace_root, validator_snapshot, "R00 validator")
    module = _load_r00_validator(
        workspace_root=workspace_root,
        relative=validator_relative,
        audit=audit,
    )
    validate_bundle = getattr(module, "validate_bundle", None)
    if not callable(validate_bundle):
        raise R02ContractError(
            "R00 validator has no callable validate_bundle",
            stage="predecessor_r00",
            audit=audit,
        )
    try:
        computed_raw = validate_bundle(workspace_root=workspace_root)
    except Exception as exc:
        raise R02ContractError(
            f"R00 deep validation failed: {exc}",
            stage="predecessor_r00",
            audit=audit,
        ) from exc
    computed = _mapping(computed_raw, "computed R00 report")
    audit.r00_deep_validation_count += 1
    data_access = _mapping(computed.get("data_access"), "R00 data_access")
    audit.r00_public_or_aggregate_evidence_read_count += sum(
        _integer(data_access.get(field_name), f"R00 {field_name}", minimum=0)
        for field_name in (
            "public_sentence_card_files_read",
            "public_schema_files_read",
            "public_license_notice_files_read",
            "public_or_aggregate_evidence_files_read",
            "prompt_hash_values_read",
        )
    )
    audit.r00_raw_audio_read_count += _integer(
        data_access.get("raw_audio_files_read"), "R00 raw audio reads", minimum=0
    )
    audit.r00_row_level_read_count += _integer(
        data_access.get("row_level_files_read"), "R00 row reads", minimum=0
    )
    if audit.r00_raw_audio_read_count or audit.r00_row_level_read_count:
        raise R02ContractError(
            "R00 deep validation accessed protected rows or audio",
            stage="predecessor_r00",
            audit=audit,
        )

    report_relative = _relative_string(
        raw.get("validation_report_path"), "R00 report path"
    )
    report_data, report_snapshot = _read_file(
        workspace_root, report_relative, "R00 immutable report"
    )
    if report_snapshot.sha256 != raw.get("validation_report_sha256"):
        raise R02ContractError(
            "R00 immutable report SHA-256 mismatch",
            stage="predecessor_r00",
            audit=audit,
        )
    immutable = _load_json_bytes(report_data, "R00 immutable report")
    if canonical_json_bytes(computed) != canonical_json_bytes(immutable):
        raise R02ContractError(
            "R00 recomputation differs from immutable report",
            stage="predecessor_r00",
            audit=audit,
        )
    _expect(computed.get("status"), "valid", "R00 status")
    completion = _mapping(computed.get("completion"), "R00 completion")
    _expect(
        completion.get("r00_completion_claim_allowed"),
        True,
        "R00 completion claim",
    )
    checks = _mapping(computed.get("checks"), "R00 checks")
    _expect(checks.get("sentence_count"), 20, "R00 sentence count")

    card_relative = _relative_string(raw.get("card_path"), "R00 card path")
    card_data, card_snapshot = _read_file(
        workspace_root, card_relative, "R00 sentence card"
    )
    if card_snapshot.sha256 != raw.get("card_sha256"):
        raise R02ContractError(
            "R00 card SHA-256 mismatch",
            stage="predecessor_r00",
            audit=audit,
        )
    card = _load_json_bytes(card_data, "R00 sentence card")
    sentences = _list(card.get("sentences"), "R00 sentences")
    if len(sentences) != 20:
        raise R02ContractError(
            "R00 card must contain 20 sentences",
            stage="predecessor_r00",
            audit=audit,
        )
    sentence_hashes: dict[str, str] = {}
    target_rules: dict[str, Mapping[str, tuple[str, ...]]] = {}
    for index, raw_sentence in enumerate(sentences):
        sentence = _mapping(raw_sentence, f"R00 sentence {index}")
        sentence_id = _string(sentence.get("sentence_id"), "R00 sentence ID")
        if sentence_id != SENTENCE_IDS[index]:
            raise R02ContractError(
                "R00 sentence order changed",
                stage="predecessor_r00",
                audit=audit,
            )
        sentence_hashes[sentence_id] = _sha256(
            sentence.get("normalized_text_sha256"),
            f"{sentence_id} text SHA",
        )
        target_rules[sentence_id] = {
            "target_phones": tuple(
                _string(value, f"{sentence_id} target phone")
                for value in _list(
                    sentence.get("target_phones"), f"{sentence_id} target phones"
                )
            ),
            "phonological_rule_targets": tuple(
                _string(
                    _mapping(
                        value,
                        f"{sentence_id} phonological rule",
                    ).get("taxonomy_stable_id"),
                    f"{sentence_id} phonological rule ID",
                )
                for value in _list(
                    sentence.get("phonological_rule_targets"),
                    f"{sentence_id} phonological rules",
                )
            ),
            "prosody_targets": tuple(
                _string(value, f"{sentence_id} prosody target")
                for value in _list(
                    sentence.get("prosody_targets"),
                    f"{sentence_id} prosody targets",
                )
            ),
        }
        audit.r00_target_rule_read_count += 1
    return R00Evidence(
        report_snapshot=report_snapshot,
        card_snapshot=card_snapshot,
        sentence_hashes=sentence_hashes,
        target_rules=target_rules,
    )


def _read_exact_json(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
    name: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    data, snapshot = _read_file(workspace_root, relative, name)
    if snapshot.sha256 != expected_sha256:
        raise R02ContractError(f"{name} SHA-256 mismatch")
    return _load_json_bytes(data, name), snapshot


def _validate_r01_completion_payload(
    payload: Mapping[str, Any],
    *,
    predecessor: Mapping[str, Any],
) -> None:
    _expect_keys(payload, R01_COMPLETION_KEYS, "R01 completion commit")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r01_candidate_completion.v1",
        "R01 completion schema",
    )
    _expect(payload.get("status"), "completed", "R01 completion status")
    _string(payload.get("run_id"), "R01 run ID")
    _expect(
        payload.get("config_sha256"),
        predecessor.get("config_sha256"),
        "R01 config binding",
    )
    for field_name in (
        "candidate_manifest_sha256",
        "registry_sha256",
        "human_attestation_sha256",
        "cosyvoice_attestation_sha256",
    ):
        _expect(
            payload.get(field_name),
            predecessor.get(field_name),
            f"R01 {field_name} binding",
        )
        _sha256(payload.get(field_name), f"R01 completion {field_name}")
    _expect(payload.get("sentence_count"), 20, "R01 sentence count")
    candidate_count = _integer(
        payload.get("candidate_count"),
        "R01 candidate count",
        minimum=160,
        maximum=320,
    )
    _expect(payload.get("audio_count"), candidate_count, "R01 audio count")
    _expect(payload.get("r02_hard_gate_status"), "not_run", "R01 R02 status")
    _expect(payload.get("publication_allowed"), False, "R01 publication")


def _validate_r01_registry_payload(
    payload: Mapping[str, Any],
    *,
    completion: Mapping[str, Any],
) -> None:
    _expect_keys(payload, R01_REGISTRY_KEYS, "R01 registry")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r01_candidate_registry.v1",
        "R01 registry schema",
    )
    _expect(payload.get("run_id"), completion.get("run_id"), "R01 registry run ID")
    _expect(payload.get("status"), "completed", "R01 registry status")
    for field_name in (
        "config_sha256",
        "candidate_manifest_sha256",
        "human_attestation_sha256",
        "cosyvoice_attestation_sha256",
        "sentence_count",
        "candidate_count",
        "audio_count",
    ):
        _expect(
            payload.get(field_name),
            completion.get(field_name),
            f"R01 registry {field_name}",
        )
    _expect(payload.get("failure"), None, "R01 registry failure")
    _expect(payload.get("publication_allowed"), False, "R01 registry publication")


def deep_validate_r01_completion(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    r00: R00Evidence,
    audit: AccessAudit,
) -> R01Evidence:
    """Validate a future completed R01 commit, registry, manifest and attestations."""

    predecessor = _mapping(config.get("predecessor_r01"), "predecessor_r01")
    commit_hash = predecessor.get("completion_commit_sha256")
    if commit_hash == TBD_R01:
        raise R02ContractError(
            f"R01 completion commit is {TBD_R01}",
            stage="r01_completion_commit",
            audit=audit,
        )
    expected_commit_sha = _sha256(commit_hash, "R01 completion commit SHA")
    commit_relative = _relative_string(
        predecessor.get("completion_commit_path"), "R01 completion commit path"
    )
    audit.r01_completion_commit_read_attempt_count += 1
    try:
        commit, commit_snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=commit_relative,
            expected_sha256=expected_commit_sha,
            name="R01 completion commit",
        )
    except R02ContractError as exc:
        raise R02ContractError(
            str(exc),
            stage="r01_completion_commit",
            audit=audit,
        ) from exc
    audit.r01_completion_commit_read_count += 1
    try:
        _validate_r01_completion_payload(commit, predecessor=predecessor)
    except R02ContractError as exc:
        raise R02ContractError(
            str(exc),
            stage="r01_completion_commit",
            audit=audit,
        ) from exc

    try:
        registry, registry_snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                predecessor.get("registry_path"), "R01 registry path"
            ),
            expected_sha256=_sha256(
                predecessor.get("registry_sha256"), "R01 registry SHA"
            ),
            name="R01 registry",
        )
        audit.r01_registry_read_count += 1
        _validate_r01_registry_payload(registry, completion=commit)

        manifest, manifest_snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                predecessor.get("candidate_manifest_path"),
                "R01 candidate manifest path",
            ),
            expected_sha256=_sha256(
                predecessor.get("candidate_manifest_sha256"),
                "R01 candidate manifest SHA",
            ),
            name="R01 candidate manifest",
        )
        audit.r01_candidate_manifest_read_count += 1

        human, human_snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                predecessor.get("human_attestation_path"),
                "R01 human attestation path",
            ),
            expected_sha256=_sha256(
                predecessor.get("human_attestation_sha256"),
                "R01 human attestation SHA",
            ),
            name="R01 human attestation",
        )
        audit.r01_human_attestation_read_count += 1
        cosyvoice, cosyvoice_snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                predecessor.get("cosyvoice_attestation_path"),
                "R01 CosyVoice attestation path",
            ),
            expected_sha256=_sha256(
                predecessor.get("cosyvoice_attestation_sha256"),
                "R01 CosyVoice attestation SHA",
            ),
            name="R01 CosyVoice attestation",
        )
        audit.r01_cosyvoice_attestation_read_count += 1
        r01.validate_human_attestation(
            human,
            r00_card_sha256=r00.card_snapshot.sha256,
        )
        r01.validate_cosyvoice_attestation(cosyvoice)
        manifest_summary = r01.validate_candidate_manifest_payload(
            manifest,
            r00_sentence_hashes=r00.sentence_hashes,
            human_attestation_sha256=human_snapshot.sha256,
            cosyvoice_attestation_sha256=cosyvoice_snapshot.sha256,
        )
    except (R02ContractError, r01.R01ContractError) as exc:
        raise R02ContractError(
            str(exc),
            stage="r01_deep_validation",
            audit=audit,
        ) from exc

    _expect(
        manifest_summary.get("candidate_count"),
        commit.get("candidate_count"),
        "R01 manifest/commit candidate count",
    )
    rows = tuple(
        _mapping(row, f"R01 candidate row {index}")
        for index, row in enumerate(
            _list(manifest.get("candidate_rows"), "R01 candidate rows")
        )
    )
    evidence = R01Evidence(
        commit_snapshot=commit_snapshot,
        registry_snapshot=registry_snapshot,
        manifest_snapshot=manifest_snapshot,
        human_attestation_snapshot=human_snapshot,
        cosyvoice_attestation_snapshot=cosyvoice_snapshot,
        candidate_rows=rows,
        candidate_count=len(rows),
        sentence_count=20,
    )
    for snapshot in (
        evidence.commit_snapshot,
        evidence.registry_snapshot,
        evidence.manifest_snapshot,
        evidence.human_attestation_snapshot,
        evidence.cosyvoice_attestation_snapshot,
    ):
        try:
            _verify_snapshot(workspace_root, snapshot, "R01 immutable evidence")
        except R02ContractError as exc:
            raise R02ContractError(
                str(exc),
                stage="r01_toctou",
                audit=audit,
            ) from exc
    return evidence


def _read_prerequisite(
    *,
    workspace_root: Path,
    raw: Mapping[str, Any],
    marker: str,
    name: str,
    stage: str,
    audit: AccessAudit,
    attempt_field: str,
    read_field: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    digest = raw.get("sha256")
    if digest == marker:
        raise R02ContractError(
            f"{name} is {marker}",
            stage=stage,
            audit=audit,
        )
    expected_sha = _sha256(digest, f"{name} SHA")
    relative = _relative_string(raw.get("path"), f"{name} path")
    setattr(audit, attempt_field, int(getattr(audit, attempt_field)) + 1)
    try:
        payload, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=relative,
            expected_sha256=expected_sha,
            name=name,
        )
    except R02ContractError as exc:
        raise R02ContractError(str(exc), stage=stage, audit=audit) from exc
    setattr(audit, read_field, int(getattr(audit, read_field)) + 1)
    return payload, snapshot


def _resolved_gate_definition(
    raw: Mapping[str, Any],
    *,
    name: str,
) -> Mapping[str, Any]:
    expected_keys = {
        "metric_id",
        "metric_definition_sha256",
        "threshold_operator",
        "threshold_value",
        "normalization_id",
        "segmentation_id",
        "minimum_support",
    }
    _expect_keys(raw, expected_keys, f"preregistration {name}")
    if _contains_marker(raw, {TBD_PREREGISTRATION, TBD_ATTESTATION}):
        raise R02ContractError(f"preregistration {name} contains TBD")
    metric_id = _string(raw.get("metric_id"), f"{name} metric ID")
    definition_sha = _sha256(
        raw.get("metric_definition_sha256"), f"{name} metric definition SHA"
    )
    operator = _string(raw.get("threshold_operator"), f"{name} operator")
    if operator not in COMPARATORS:
        raise R02ContractError(f"{name} threshold operator is invalid")
    threshold = _number(raw.get("threshold_value"), f"{name} threshold")
    normalization = _string(raw.get("normalization_id"), f"{name} normalization")
    segmentation = _string(raw.get("segmentation_id"), f"{name} segmentation")
    support = _integer(raw.get("minimum_support"), f"{name} support", minimum=1)
    return {
        "metric_id": metric_id,
        "metric_definition_sha256": definition_sha,
        "threshold_operator": operator,
        "threshold_value": threshold,
        "normalization_id": normalization,
        "segmentation_id": segmentation,
        "minimum_support": support,
    }


def validate_preregistration_payload(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate an externally approved, pre-audio R02 gate preregistration."""

    if _contains_marker(payload, {TBD_PREREGISTRATION, TBD_ATTESTATION, TBD_R01}):
        raise R02ContractError("preregistration contains unresolved markers")
    _expect_keys(payload, PREREGISTRATION_KEYS, "preregistration")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r02_gate_preregistration.v1",
        "preregistration schema",
    )
    _string(payload.get("preregistration_id"), "preregistration ID")
    _expect(
        payload.get("status"),
        "approved_locked_before_r02_audio_access",
        "preregistration status",
    )
    _sha256(payload.get("approved_document_sha256"), "preregistration document SHA")
    _expect(
        payload.get("approved_by_external_human"),
        True,
        "preregistration human approval",
    )
    _expect(
        payload.get("locked_before_r02_audio_access"),
        True,
        "preregistration lock timing",
    )
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "preregistration completion claim",
    )
    policy = _mapping(payload.get("candidate_policy"), "candidate policy")
    _expect_keys(
        policy,
        {
            "input_candidates_per_sentence_minimum",
            "input_candidates_per_sentence_maximum",
            "same_gate_for_human_and_tts",
            "source_conditioned_thresholds_allowed",
            "minimum_eligible_candidates_per_sentence",
            "aggregate_transcript_error_metric_id",
            "aggregate_transcript_error_metric_definition_sha256",
            "aggregate_transcript_error_aggregation",
            "aggregate_transcript_error_threshold_operator",
            "aggregate_transcript_error_completion_boundary",
            "aggregate_boundary_is_not_a_candidate_threshold",
        },
        "candidate policy",
    )
    _expect(policy.get("input_candidates_per_sentence_minimum"), 8, "candidate min")
    _expect(policy.get("input_candidates_per_sentence_maximum"), 16, "candidate max")
    _expect(policy.get("same_gate_for_human_and_tts"), True, "same source gate")
    _expect(
        policy.get("source_conditioned_thresholds_allowed"),
        False,
        "source-conditioned thresholds",
    )
    minimum_eligible = _integer(
        policy.get("minimum_eligible_candidates_per_sentence"),
        "minimum eligible candidates",
        minimum=1,
        maximum=16,
    )
    aggregate_metric_id = _string(
        policy.get("aggregate_transcript_error_metric_id"),
        "aggregate transcript metric ID",
    )
    aggregate_definition = _sha256(
        policy.get("aggregate_transcript_error_metric_definition_sha256"),
        "aggregate transcript metric definition",
    )
    aggregate_aggregation = _string(
        policy.get("aggregate_transcript_error_aggregation"),
        "aggregate transcript aggregation",
    )
    _expect(
        policy.get("aggregate_transcript_error_threshold_operator"),
        "le",
        "aggregate transcript operator",
    )
    _expect(
        policy.get("aggregate_transcript_error_completion_boundary"),
        0.02,
        "aggregate transcript boundary",
    )
    _expect(
        policy.get("aggregate_boundary_is_not_a_candidate_threshold"),
        True,
        "aggregate boundary role",
    )

    gates_raw = _mapping(payload.get("gates"), "preregistered gates")
    _expect(set(gates_raw), set(GATE_NAMES), "preregistered gate names")
    gates = {
        gate_name: _resolved_gate_definition(
            _mapping(gates_raw.get(gate_name), gate_name),
            name=gate_name,
        )
        for gate_name in GATE_NAMES
    }
    missingness = _mapping(
        payload.get("multiplicity_and_missingness"),
        "multiplicity and missingness",
    )
    _expect_keys(
        missingness,
        {
            "missing_metric_decision",
            "nonfinite_metric_decision",
            "insufficient_support_decision",
            "all_four_gates_must_pass",
            "posthoc_threshold_change_allowed",
        },
        "multiplicity and missingness",
    )
    for field_name in (
        "missing_metric_decision",
        "nonfinite_metric_decision",
        "insufficient_support_decision",
    ):
        _expect(missingness.get(field_name), "fail", field_name)
    _expect(
        missingness.get("all_four_gates_must_pass"),
        True,
        "all-gates rule",
    )
    _expect(
        missingness.get("posthoc_threshold_change_allowed"),
        False,
        "posthoc threshold rule",
    )
    blindness = _mapping(
        payload.get("blindness_and_leakage"),
        "preregistration blindness",
    )
    _expect_keys(
        blindness,
        {
            "gate_input_contains_source_kind",
            "source_labels_available_to_gate_implementation",
            "human_and_tts_exception_allowed",
            "s1_prediction_or_score_allowed",
            "s2_prediction_or_score_allowed",
            "gold_test_or_locked_split_allowed",
            "r00_target_rules_for_coverage_audit_only",
        },
        "preregistration blindness",
    )
    _expect(
        blindness.get("gate_input_contains_source_kind"),
        False,
        "source in gate input",
    )
    _expect(
        blindness.get("source_labels_available_to_gate_implementation"),
        False,
        "source labels in implementation",
    )
    _expect(
        blindness.get("human_and_tts_exception_allowed"),
        False,
        "source exceptions",
    )
    for field_name in (
        "s1_prediction_or_score_allowed",
        "s2_prediction_or_score_allowed",
        "gold_test_or_locked_split_allowed",
    ):
        _expect(blindness.get(field_name), False, field_name)
    _expect(
        blindness.get("r00_target_rules_for_coverage_audit_only"),
        True,
        "R00 coverage-only rule",
    )
    licenses = _mapping(payload.get("license_decisions"), "license decisions")
    _expect_keys(
        licenses,
        {
            "asr_code_license",
            "asr_checkpoint_license",
            "artifact_code_license",
            "artifact_checkpoint_license",
            "pause_rhythm_pipeline_license",
            "target_phone_code_license",
            "target_phone_checkpoint_license",
        },
        "license decisions",
    )
    for field_name, value in licenses.items():
        _string(value, f"license decision {field_name}")

    hard_gates = _mapping(config.get("hard_gates"), "config hard gates")
    for gate_name in GATE_NAMES:
        config_gate = _mapping(hard_gates.get(gate_name), f"config {gate_name}")
        expected = gates[gate_name]
        _expect(
            config_gate.get("metric_id"),
            expected["metric_id"],
            f"config/prereg {gate_name} metric",
        )
        _expect(
            dict(
                _mapping(
                    config_gate.get("threshold"),
                    f"config {gate_name} threshold",
                )
            ),
            {
                "operator": expected["threshold_operator"],
                "value": expected["threshold_value"],
            },
            f"config/prereg {gate_name} threshold",
        )
        _expect(
            config_gate.get("normalization_id"),
            expected["normalization_id"],
            f"config/prereg {gate_name} normalization",
        )
        _expect(
            config_gate.get("segmentation_id"),
            expected["segmentation_id"],
            f"config/prereg {gate_name} segmentation",
        )
        _expect(
            config_gate.get("minimum_support"),
            expected["minimum_support"],
            f"config/prereg {gate_name} support",
        )
    flow = _mapping(config.get("candidate_flow"), "candidate flow")
    _expect(
        flow.get("minimum_eligible_candidates_per_sentence"),
        minimum_eligible,
        "config/prereg minimum eligible",
    )
    _expect(
        flow.get("aggregate_transcript_error_metric_id"),
        aggregate_metric_id,
        "config/prereg aggregate metric",
    )
    _expect(
        flow.get("aggregate_transcript_error_metric_definition_sha256"),
        aggregate_definition,
        "config/prereg aggregate definition",
    )
    _expect(
        flow.get("aggregate_transcript_error_aggregation"),
        aggregate_aggregation,
        "config/prereg aggregate aggregation",
    )
    return {
        "minimum_eligible_candidates_per_sentence": minimum_eligible,
        "aggregate_transcript_error_metric_id": aggregate_metric_id,
        "aggregate_transcript_error_metric_definition_sha256": aggregate_definition,
        "aggregate_transcript_error_aggregation": aggregate_aggregation,
        "gates": gates,
    }


def validate_asr_attestation_payload(payload: Mapping[str, Any]) -> None:
    """Validate exact ASR artifact, license, preprocessing and leakage claims."""

    if _contains_marker(payload, {TBD_ATTESTATION, TBD_PREREGISTRATION, TBD_R01}):
        raise R02ContractError("ASR attestation contains unresolved markers")
    _expect_keys(payload, ASR_ATTESTATION_KEYS, "ASR attestation")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r02_asr_attestation.v1",
        "ASR attestation schema",
    )
    _string(payload.get("attestation_id"), "ASR attestation ID")
    _expect(
        payload.get("status"),
        "approved_for_r02_content_gate",
        "ASR attestation status",
    )
    _expect(payload.get("role"), "asr_content_match_only", "ASR role")
    _expect(
        payload.get("plan_backed_model_family"),
        "openai/whisper-large-v3",
        "ASR model family",
    )
    for field_name in (
        "exact_model_id",
        "exact_checkpoint_revision",
        "code_revision",
        "code_license_spdx",
        "checkpoint_license_spdx",
        "normalization_id",
        "segmentation_id",
    ):
        _string(payload.get(field_name), f"ASR {field_name}")
    for field_name in (
        "checkpoint_manifest_sha256",
        "code_license_evidence_sha256",
        "checkpoint_license_evidence_sha256",
        "normalization_spec_sha256",
        "segmentation_spec_sha256",
        "local_bundle_manifest_sha256",
        "runtime_environment_lock_sha256",
    ):
        _sha256(payload.get(field_name), f"ASR {field_name}")
    _expect(payload.get("research_use_authorized"), True, "ASR research use")
    _boolean(payload.get("redistribution_authorized"), "ASR redistribution")
    _expect(payload.get("language"), "ko", "ASR language")
    _expect(payload.get("task"), "transcribe", "ASR task")
    _expect(payload.get("local_files_only"), True, "ASR local-only")
    _expect(payload.get("network_download_allowed"), False, "ASR download")
    _expect(payload.get("s1_or_s2_artifact_used"), False, "ASR S1/S2 leakage")
    _expect(
        payload.get("gold_or_locked_test_artifact_used"),
        False,
        "ASR protected leakage",
    )
    _expect(
        payload.get("approved_by_external_human"),
        True,
        "ASR external approval",
    )
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "ASR completion claim",
    )


def validate_artifact_attestation_payload(payload: Mapping[str, Any]) -> None:
    """Validate non-content gate implementations without touching their bundles."""

    if _contains_marker(payload, {TBD_ATTESTATION, TBD_PREREGISTRATION, TBD_R01}):
        raise R02ContractError("artifact attestation contains unresolved markers")
    _expect_keys(payload, ARTIFACT_ATTESTATION_KEYS, "artifact attestation")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r02_artifact_attestation.v1",
        "artifact attestation schema",
    )
    _string(payload.get("attestation_id"), "artifact attestation ID")
    _expect(
        payload.get("status"),
        "approved_for_r02_noncontent_gates",
        "artifact attestation status",
    )
    acoustic = _mapping(payload.get("acoustic_artifact"), "acoustic artifact")
    _expect_keys(
        acoustic,
        {
            "plan_backed_model_family",
            "exact_model_id",
            "exact_checkpoint_revision",
            "checkpoint_manifest_sha256",
            "code_revision",
            "code_license_spdx",
            "code_license_evidence_sha256",
            "checkpoint_license_spdx",
            "checkpoint_license_evidence_sha256",
            "normalization_id",
            "normalization_spec_sha256",
            "segmentation_id",
            "segmentation_spec_sha256",
            "research_use_authorized",
        },
        "acoustic artifact",
    )
    _expect(
        acoustic.get("plan_backed_model_family"),
        "UTMOSv2",
        "artifact model family",
    )
    for field_name in (
        "exact_model_id",
        "exact_checkpoint_revision",
        "code_revision",
        "code_license_spdx",
        "checkpoint_license_spdx",
    ):
        _string(acoustic.get(field_name), f"artifact {field_name}")
    for field_name in (
        "checkpoint_manifest_sha256",
        "code_license_evidence_sha256",
        "checkpoint_license_evidence_sha256",
        "normalization_spec_sha256",
        "segmentation_spec_sha256",
    ):
        _sha256(acoustic.get(field_name), f"artifact {field_name}")
    _string(acoustic.get("normalization_id"), "artifact normalization ID")
    _string(acoustic.get("segmentation_id"), "artifact segmentation ID")
    _expect(
        acoustic.get("research_use_authorized"),
        True,
        "artifact research use",
    )
    pause = _mapping(payload.get("pause_rhythm_pipeline"), "pause/rhythm pipeline")
    _expect_keys(
        pause,
        {
            "implementation_id",
            "implementation_revision",
            "implementation_sha256",
            "license_spdx",
            "license_evidence_sha256",
            "normalization_id",
            "normalization_spec_sha256",
            "segmentation_id",
            "segmentation_spec_sha256",
            "research_use_authorized",
        },
        "pause/rhythm pipeline",
    )
    for field_name in (
        "implementation_id",
        "implementation_revision",
        "license_spdx",
        "normalization_id",
        "segmentation_id",
    ):
        _string(pause.get(field_name), f"pause/rhythm {field_name}")
    for field_name in (
        "implementation_sha256",
        "license_evidence_sha256",
        "normalization_spec_sha256",
        "segmentation_spec_sha256",
    ):
        _sha256(pause.get(field_name), f"pause/rhythm {field_name}")
    _expect(
        pause.get("research_use_authorized"),
        True,
        "pause/rhythm research use",
    )
    phone = _mapping(payload.get("target_phone_pipeline"), "target phone pipeline")
    _expect_keys(
        phone,
        {
            "implementation_id",
            "implementation_revision",
            "implementation_sha256",
            "model_checkpoint_revision",
            "model_checkpoint_manifest_sha256",
            "code_license_spdx",
            "checkpoint_license_spdx",
            "license_evidence_sha256",
            "normalization_id",
            "normalization_spec_sha256",
            "segmentation_id",
            "segmentation_spec_sha256",
            "r00_target_rules_are_audit_targets_only",
            "s1_prediction_or_score_used",
            "s2_prediction_or_score_used",
            "gold_or_locked_test_artifact_used",
            "research_use_authorized",
        },
        "target phone pipeline",
    )
    for field_name in (
        "implementation_id",
        "implementation_revision",
        "model_checkpoint_revision",
        "code_license_spdx",
        "checkpoint_license_spdx",
        "normalization_id",
        "segmentation_id",
    ):
        _string(phone.get(field_name), f"target phone {field_name}")
    for field_name in (
        "implementation_sha256",
        "model_checkpoint_manifest_sha256",
        "license_evidence_sha256",
        "normalization_spec_sha256",
        "segmentation_spec_sha256",
    ):
        _sha256(phone.get(field_name), f"target phone {field_name}")
    _expect(
        phone.get("r00_target_rules_are_audit_targets_only"),
        True,
        "target phone R00 role",
    )
    _expect(phone.get("s1_prediction_or_score_used"), False, "target phone S1")
    _expect(phone.get("s2_prediction_or_score_used"), False, "target phone S2")
    _expect(
        phone.get("gold_or_locked_test_artifact_used"),
        False,
        "target phone protected data",
    )
    _expect(
        phone.get("research_use_authorized"),
        True,
        "target phone research use",
    )
    runtime = _mapping(payload.get("runtime"), "artifact runtime")
    _expect_keys(
        runtime,
        {
            "local_files_only",
            "network_download_allowed",
            "local_bundle_manifest_sha256",
            "runtime_environment_lock_sha256",
        },
        "artifact runtime",
    )
    _expect(runtime.get("local_files_only"), True, "artifact local-only")
    _expect(runtime.get("network_download_allowed"), False, "artifact download")
    _sha256(
        runtime.get("local_bundle_manifest_sha256"),
        "artifact local bundle manifest",
    )
    _sha256(
        runtime.get("runtime_environment_lock_sha256"),
        "artifact environment lock",
    )
    _expect(
        payload.get("same_pipeline_for_human_and_tts"),
        True,
        "same human/TTS pipeline",
    )
    _expect(
        payload.get("source_conditioned_normalization_allowed"),
        False,
        "source-conditioned normalization",
    )
    _expect(
        payload.get("approved_by_external_human"),
        True,
        "artifact external approval",
    )
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "artifact completion claim",
    )


def _validate_attestation_config_bindings(
    *,
    config: Mapping[str, Any],
    preregistration: Mapping[str, Any],
    asr: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> None:
    hard_gates = _mapping(config.get("hard_gates"), "hard gates")
    asr_gate = _mapping(hard_gates.get("asr_content_match"), "ASR gate")
    for config_field, attestation_field in (
        ("exact_model_id", "exact_model_id"),
        ("exact_checkpoint_revision", "exact_checkpoint_revision"),
        ("normalization_id", "normalization_id"),
        ("segmentation_id", "segmentation_id"),
    ):
        _expect(
            asr_gate.get(config_field),
            asr.get(attestation_field),
            f"ASR config/attestation {config_field}",
        )
    asr_license = _mapping(
        asr_gate.get("code_and_checkpoint_license"),
        "ASR config license binding",
    )
    _expect(
        dict(asr_license),
        {
            "code_spdx": asr.get("code_license_spdx"),
            "code_evidence_sha256": asr.get("code_license_evidence_sha256"),
            "checkpoint_spdx": asr.get("checkpoint_license_spdx"),
            "checkpoint_evidence_sha256": asr.get("checkpoint_license_evidence_sha256"),
        },
        "ASR config license binding",
    )

    acoustic = _mapping(artifact.get("acoustic_artifact"), "acoustic attestation")
    acoustic_gate = _mapping(
        hard_gates.get("acoustic_artifact"), "acoustic artifact gate"
    )
    for field_name in (
        "exact_model_id",
        "exact_checkpoint_revision",
        "normalization_id",
        "segmentation_id",
    ):
        _expect(
            acoustic_gate.get(field_name),
            acoustic.get(field_name),
            f"acoustic config/attestation {field_name}",
        )
    acoustic_license = _mapping(
        acoustic_gate.get("code_and_checkpoint_license"),
        "acoustic config license binding",
    )
    _expect(
        dict(acoustic_license),
        {
            "code_spdx": acoustic.get("code_license_spdx"),
            "code_evidence_sha256": acoustic.get("code_license_evidence_sha256"),
            "checkpoint_spdx": acoustic.get("checkpoint_license_spdx"),
            "checkpoint_evidence_sha256": acoustic.get(
                "checkpoint_license_evidence_sha256"
            ),
        },
        "acoustic config license binding",
    )

    pause = _mapping(artifact.get("pause_rhythm_pipeline"), "pause attestation")
    pause_gate = _mapping(hard_gates.get("pause_rhythm"), "pause/rhythm gate")
    _expect(
        pause_gate.get("exact_implementation_revision"),
        pause.get("implementation_revision"),
        "pause implementation revision",
    )
    _expect(
        pause_gate.get("normalization_id"),
        pause.get("normalization_id"),
        "pause normalization",
    )
    _expect(
        pause_gate.get("segmentation_id"),
        pause.get("segmentation_id"),
        "pause segmentation",
    )
    _expect(
        dict(
            _mapping(
                pause_gate.get("implementation_license"),
                "pause config license binding",
            )
        ),
        {
            "spdx": pause.get("license_spdx"),
            "evidence_sha256": pause.get("license_evidence_sha256"),
        },
        "pause config license binding",
    )

    phone = _mapping(artifact.get("target_phone_pipeline"), "phone attestation")
    phone_gate = _mapping(hard_gates.get("target_phone_coverage"), "target phone gate")
    _expect(
        phone_gate.get("exact_implementation_revision"),
        phone.get("implementation_revision"),
        "phone implementation revision",
    )
    _expect(
        phone_gate.get("exact_model_checkpoint_revision"),
        phone.get("model_checkpoint_revision"),
        "phone checkpoint revision",
    )
    _expect(
        phone_gate.get("normalization_id"),
        phone.get("normalization_id"),
        "phone normalization",
    )
    _expect(
        phone_gate.get("segmentation_id"),
        phone.get("segmentation_id"),
        "phone segmentation",
    )
    _expect(
        dict(
            _mapping(
                phone_gate.get("implementation_and_checkpoint_license"),
                "phone config license binding",
            )
        ),
        {
            "code_spdx": phone.get("code_license_spdx"),
            "checkpoint_spdx": phone.get("checkpoint_license_spdx"),
            "evidence_sha256": phone.get("license_evidence_sha256"),
        },
        "phone config license binding",
    )
    license_decisions = _mapping(
        preregistration.get("license_decisions"),
        "preregistered license decisions",
    )
    _expect(
        dict(license_decisions),
        {
            "asr_code_license": asr.get("code_license_spdx"),
            "asr_checkpoint_license": asr.get("checkpoint_license_spdx"),
            "artifact_code_license": acoustic.get("code_license_spdx"),
            "artifact_checkpoint_license": acoustic.get("checkpoint_license_spdx"),
            "pause_rhythm_pipeline_license": pause.get("license_spdx"),
            "target_phone_code_license": phone.get("code_license_spdx"),
            "target_phone_checkpoint_license": phone.get("checkpoint_license_spdx"),
        },
        "preregistration/attestation license decisions",
    )


def validate_prerequisites(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> PrerequisiteEvidence:
    """Read and validate preregistration before any model/audio path."""

    raw = _mapping(config.get("prerequisites"), "prerequisites")
    preregistration, prereg_snapshot = _read_prerequisite(
        workspace_root=workspace_root,
        raw=_mapping(raw.get("preregistration"), "preregistration"),
        marker=TBD_PREREGISTRATION,
        name="R02 preregistration",
        stage="preregistration",
        audit=audit,
        attempt_field="preregistration_read_attempt_count",
        read_field="preregistration_read_count",
    )
    try:
        validate_preregistration_payload(preregistration, config=config)
    except R02ContractError as exc:
        raise R02ContractError(str(exc), stage="preregistration", audit=audit) from exc
    asr, asr_snapshot = _read_prerequisite(
        workspace_root=workspace_root,
        raw=_mapping(raw.get("asr_attestation"), "ASR attestation"),
        marker=TBD_ATTESTATION,
        name="R02 ASR attestation",
        stage="asr_attestation",
        audit=audit,
        attempt_field="asr_attestation_read_attempt_count",
        read_field="asr_attestation_read_count",
    )
    try:
        validate_asr_attestation_payload(asr)
    except R02ContractError as exc:
        raise R02ContractError(str(exc), stage="asr_attestation", audit=audit) from exc
    artifact, artifact_snapshot = _read_prerequisite(
        workspace_root=workspace_root,
        raw=_mapping(raw.get("artifact_attestation"), "artifact attestation"),
        marker=TBD_ATTESTATION,
        name="R02 artifact attestation",
        stage="artifact_attestation",
        audit=audit,
        attempt_field="artifact_attestation_read_attempt_count",
        read_field="artifact_attestation_read_count",
    )
    try:
        validate_artifact_attestation_payload(artifact)
        _validate_attestation_config_bindings(
            config=config,
            preregistration=preregistration,
            asr=asr,
            artifact=artifact,
        )
    except R02ContractError as exc:
        raise R02ContractError(
            str(exc), stage="artifact_attestation", audit=audit
        ) from exc
    return PrerequisiteEvidence(
        preregistration_snapshot=prereg_snapshot,
        asr_attestation_snapshot=asr_snapshot,
        artifact_attestation_snapshot=artifact_snapshot,
        preregistration=preregistration,
    )


def _comparison_passes(value: float, operator: str, threshold: float) -> bool:
    if operator == "le":
        return value <= threshold
    if operator == "lt":
        return value < threshold
    if operator == "ge":
        return value >= threshold
    if operator == "gt":
        return value > threshold
    raise R02ContractError(f"unsupported threshold operator: {operator}")


def _candidate_index(
    candidate_rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, ...], Mapping[str, Mapping[str, Any]]]:
    ordered: list[str] = []
    indexed: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(candidate_rows):
        candidate_id = _string(row.get("candidate_id"), f"candidate {index} ID")
        if CANDIDATE_ID_RE.fullmatch(candidate_id) is None:
            raise R02ContractError("candidate ID is not an opaque R01 ID")
        if candidate_id in indexed:
            raise R02ContractError("candidate IDs must be unique")
        ordered.append(candidate_id)
        indexed[candidate_id] = row
    return tuple(ordered), indexed


def validate_gate_report_payload(
    payload: Mapping[str, Any],
    *,
    candidate_rows: Sequence[Mapping[str, Any]],
    r01_completion_commit_sha256: str,
    r01_candidate_manifest_sha256: str,
    preregistration_sha256: str,
    asr_attestation_sha256: str,
    artifact_attestation_sha256: str,
    preregistered_policy: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Deep-validate an immutable, source-blind R02 gate report."""

    _expect_keys(payload, REPORT_KEYS, "R02 gate report")
    leaked = FORBIDDEN_REPORT_KEYS & _collect_keys(payload)
    if leaked:
        raise R02ContractError(
            f"R02 report contains forbidden fields: {sorted(leaked)}"
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.r02_hard_gate_report.v1",
        "R02 report schema",
    )
    if (
        re.fullmatch(
            r"r02-hard-gate-[0-9a-f]{32}",
            _string(payload.get("report_id"), "report ID"),
        )
        is None
    ):
        raise R02ContractError("R02 report ID must be opaque")
    _expect(payload.get("status"), "completed_private_gate", "R02 report status")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "R02 report privacy zone",
    )
    expected_hashes = {
        "r01_completion_commit_sha256": r01_completion_commit_sha256,
        "r01_candidate_manifest_sha256": r01_candidate_manifest_sha256,
        "preregistration_sha256": preregistration_sha256,
        "asr_attestation_sha256": asr_attestation_sha256,
        "artifact_attestation_sha256": artifact_attestation_sha256,
    }
    for field_name, expected in expected_hashes.items():
        _sha256(expected, f"expected {field_name}")
        _expect(payload.get(field_name), expected, f"report {field_name}")
    _expect(payload.get("blind_gate_input"), True, "blind gate input")
    _expect(payload.get("source_labels_present"), False, "report source labels")
    _expect(
        payload.get("same_gate_for_human_and_tts"),
        True,
        "same human/TTS gate",
    )
    _expect(
        payload.get("failed_candidates_preserved_in_d1"),
        True,
        "failed-candidate preservation",
    )
    _expect(
        payload.get("r03_may_read_only_eligible_candidates"),
        True,
        "R03 eligible-only policy",
    )
    _expect(payload.get("publication_allowed"), False, "R02 publication")
    _expect(
        payload.get("r02_completion_claim_allowed"),
        True,
        "R02 completion claim",
    )

    ordered_ids, indexed_rows = _candidate_index(candidate_rows)
    candidate_count = len(ordered_ids)
    if not 160 <= candidate_count <= 320:
        raise R02ContractError("R02 input candidate count must be 160-320")
    _expect(
        payload.get("input_candidate_count"),
        candidate_count,
        "R02 input candidate count",
    )
    policy_gates = _mapping(preregistered_policy.get("gates"), "preregistered gates")
    minimum_eligible = _integer(
        preregistered_policy.get("minimum_eligible_candidates_per_sentence"),
        "minimum eligible support",
        minimum=1,
        maximum=16,
    )

    results = _list(payload.get("results"), "R02 results")
    if len(results) != candidate_count:
        raise R02ContractError("R02 result count must equal R01 candidate count")
    eligible: list[str] = []
    quarantined: list[str] = []
    sentence_decisions: dict[str, list[str]] = defaultdict(list)
    for index, raw_result in enumerate(results):
        result = _mapping(raw_result, f"R02 result {index}")
        _expect_keys(result, RESULT_KEYS, f"R02 result {index}")
        candidate_id = _string(result.get("candidate_id"), "result candidate ID")
        if candidate_id != ordered_ids[index]:
            raise R02ContractError("R02 result order must equal R01 manifest order")
        source_row = indexed_rows[candidate_id]
        sentence_id = _string(result.get("sentence_id"), "result sentence ID")
        _expect(sentence_id, source_row.get("sentence_id"), "result sentence binding")
        _expect(
            result.get("target_text_sha256"),
            source_row.get("target_text_sha256"),
            "result target hash binding",
        )
        decisions: list[bool] = []
        for gate_name in GATE_NAMES:
            gate = _mapping(result.get(gate_name), f"{candidate_id}.{gate_name}")
            _expect_keys(gate, GATE_RESULT_KEYS, f"{candidate_id}.{gate_name}")
            policy = _mapping(policy_gates.get(gate_name), f"policy {gate_name}")
            _expect(
                gate.get("metric_id"),
                policy.get("metric_id"),
                f"{candidate_id}.{gate_name} metric",
            )
            _expect(
                gate.get("metric_definition_sha256"),
                policy.get("metric_definition_sha256"),
                f"{candidate_id}.{gate_name} definition",
            )
            _expect(
                gate.get("threshold_operator"),
                policy.get("threshold_operator"),
                f"{candidate_id}.{gate_name} operator",
            )
            observed_threshold = _number(
                gate.get("threshold_value"),
                f"{candidate_id}.{gate_name} threshold",
            )
            expected_threshold = _number(
                policy.get("threshold_value"),
                f"policy {gate_name} threshold",
            )
            _expect(
                observed_threshold,
                expected_threshold,
                f"{candidate_id}.{gate_name} threshold",
            )
            support = _integer(
                gate.get("support"),
                f"{candidate_id}.{gate_name} support",
                minimum=0,
            )
            minimum_support = _integer(
                policy.get("minimum_support"),
                f"policy {gate_name} support",
                minimum=1,
            )
            value = _number(gate.get("value"), f"{candidate_id}.{gate_name} value")
            comparator_pass = _comparison_passes(
                value,
                _string(
                    policy.get("threshold_operator"),
                    f"policy {gate_name} operator",
                ),
                expected_threshold,
            )
            expected_pass = support >= minimum_support and comparator_pass
            _expect(
                gate.get("decision"),
                "pass" if expected_pass else "fail",
                f"{candidate_id}.{gate_name} decision",
            )
            _sha256(
                gate.get("evidence_sha256"),
                f"{candidate_id}.{gate_name} evidence",
            )
            decisions.append(expected_pass)
        expected_overall = "eligible" if all(decisions) else "quarantined"
        _expect(
            result.get("overall_decision"),
            expected_overall,
            f"{candidate_id} overall decision",
        )
        if expected_overall == "eligible":
            eligible.append(candidate_id)
        else:
            quarantined.append(candidate_id)
        sentence_decisions[sentence_id].append(expected_overall)

    _expect(
        _list(payload.get("eligible_candidate_ids"), "eligible candidate IDs"),
        eligible,
        "eligible partition/order",
    )
    _expect(
        _list(payload.get("quarantined_candidate_ids"), "quarantined candidate IDs"),
        quarantined,
        "quarantine partition/order",
    )
    summaries = _list(payload.get("sentence_summaries"), "sentence summaries")
    if len(summaries) != 20:
        raise R02ContractError("sentence summaries must contain exactly 20 rows")
    for index, raw_summary in enumerate(summaries):
        summary = _mapping(raw_summary, f"sentence summary {index}")
        _expect_keys(summary, SENTENCE_SUMMARY_KEYS, f"sentence summary {index}")
        sentence_id = SENTENCE_IDS[index]
        _expect(summary.get("sentence_id"), sentence_id, "summary sentence order")
        sentence_outcomes = sentence_decisions.get(sentence_id, [])
        if not 8 <= len(sentence_outcomes) <= 16:
            raise R02ContractError(f"{sentence_id} input count must be 8-16")
        eligible_count = sentence_outcomes.count("eligible")
        quarantine_count = sentence_outcomes.count("quarantined")
        _expect(
            summary.get("input_count"), len(sentence_outcomes), "summary input count"
        )
        _expect(
            summary.get("eligible_count"),
            eligible_count,
            "summary eligible count",
        )
        _expect(
            summary.get("quarantined_count"),
            quarantine_count,
            "summary quarantine count",
        )
        support_met = eligible_count >= minimum_eligible
        _expect(
            summary.get("minimum_eligible_support_met"),
            support_met,
            "summary eligible support decision",
        )
        if not support_met:
            raise R02ContractError(
                f"{sentence_id} has insufficient eligible candidates for R03"
            )

    aggregate = _mapping(
        payload.get("aggregate_transcript_error"),
        "aggregate transcript error",
    )
    _expect_keys(aggregate, AGGREGATE_METRIC_KEYS, "aggregate transcript error")
    _expect(
        aggregate.get("metric_id"),
        preregistered_policy.get("aggregate_transcript_error_metric_id"),
        "aggregate transcript metric",
    )
    _expect(
        aggregate.get("metric_definition_sha256"),
        preregistered_policy.get("aggregate_transcript_error_metric_definition_sha256"),
        "aggregate transcript definition",
    )
    _expect(
        aggregate.get("aggregation"),
        preregistered_policy.get("aggregate_transcript_error_aggregation"),
        "aggregate transcript aggregation",
    )
    _expect(
        aggregate.get("threshold_operator"),
        "le",
        "aggregate transcript operator",
    )
    _expect(
        _number(aggregate.get("threshold_value"), "aggregate threshold"),
        0.02,
        "aggregate transcript threshold",
    )
    aggregate_value = _number(
        aggregate.get("value"), "aggregate transcript error value"
    )
    _expect(
        aggregate.get("support"),
        candidate_count,
        "aggregate transcript support",
    )
    aggregate_pass = aggregate_value <= 0.02
    _expect(
        aggregate.get("decision"),
        "pass" if aggregate_pass else "fail",
        "aggregate transcript decision",
    )
    _sha256(aggregate.get("evidence_sha256"), "aggregate transcript evidence")
    if not aggregate_pass:
        raise R02ContractError(
            "aggregate transcript error exceeds the plan-backed 0.02 boundary"
        )
    return {
        "status": "valid_completed_private_gate_report",
        "candidate_count": candidate_count,
        "eligible_count": len(eligible),
        "quarantined_count": len(quarantined),
        "eligible_candidate_ids": tuple(eligible),
        "quarantined_candidate_ids": tuple(quarantined),
        "publication_allowed": False,
    }


def validate_blinded_execution_manifest_payload(
    payload: Mapping[str, Any],
    *,
    r01: R01Evidence,
) -> None:
    """Validate that the metric-facing manifest contains no source label."""

    _expect_keys(payload, BLINDED_EXECUTION_KEYS, "blinded execution manifest")
    leaked = FORBIDDEN_REPORT_KEYS & _collect_keys(payload)
    if leaked:
        raise R02ContractError(
            f"blinded execution manifest leaks fields: {sorted(leaked)}"
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.r02_blinded_execution_manifest.v1",
        "blinded execution schema",
    )
    _expect(payload.get("status"), "sealed_for_r02_gate", "blinded status")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "blinded privacy zone",
    )
    _expect(
        payload.get("r01_candidate_manifest_sha256"),
        r01.manifest_snapshot.sha256,
        "blinded R01 binding",
    )
    _expect(payload.get("candidate_count"), r01.candidate_count, "blinded count")
    _expect(payload.get("source_labels_present"), False, "blinded source labels")
    rows = _list(payload.get("candidate_rows"), "blinded candidate rows")
    if len(rows) != r01.candidate_count:
        raise R02ContractError("blinded manifest row count mismatch")
    for index, (raw_blind, source) in enumerate(
        zip(rows, r01.candidate_rows, strict=True)
    ):
        blind = _mapping(raw_blind, f"blinded row {index}")
        _expect_keys(blind, BLINDED_EXECUTION_ROW_KEYS, f"blinded row {index}")
        for field_name in BLINDED_EXECUTION_ROW_KEYS:
            _expect(
                blind.get(field_name),
                source.get(field_name),
                f"blinded row {index}.{field_name}",
            )


def validate_lineage_sidecar_payload(
    payload: Mapping[str, Any],
    *,
    r01: R01Evidence,
) -> None:
    """Validate the sealed source-label sidecar separately from gate input."""

    _expect_keys(payload, LINEAGE_SIDECAR_KEYS, "source lineage sidecar")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r02_source_lineage_sidecar.v1",
        "lineage sidecar schema",
    )
    _expect(payload.get("status"), "sealed_source_labels", "lineage status")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "lineage privacy zone",
    )
    _expect(
        payload.get("r01_candidate_manifest_sha256"),
        r01.manifest_snapshot.sha256,
        "lineage R01 binding",
    )
    _expect(payload.get("candidate_count"), r01.candidate_count, "lineage count")
    _expect(
        payload.get("sealed_from_gate_implementation"),
        True,
        "lineage gate isolation",
    )
    _expect(
        payload.get("unblind_only_after_immutable_gate_commit"),
        True,
        "lineage unblind timing",
    )
    rows = _list(payload.get("candidate_rows"), "lineage rows")
    if len(rows) != r01.candidate_count:
        raise R02ContractError("lineage row count mismatch")
    for index, (raw_lineage, source) in enumerate(
        zip(rows, r01.candidate_rows, strict=True)
    ):
        lineage = _mapping(raw_lineage, f"lineage row {index}")
        _expect_keys(lineage, LINEAGE_ROW_KEYS, f"lineage row {index}")
        for field_name in LINEAGE_ROW_KEYS:
            _expect(
                lineage.get(field_name),
                source.get(field_name),
                f"lineage row {index}.{field_name}",
            )


def validate_partition_manifest_payload(
    payload: Mapping[str, Any],
    *,
    gate_report_sha256: str,
    expected_candidate_ids: Sequence[str],
    eligible: bool,
) -> None:
    """Validate the R03-eligible or preserved quarantine partition."""

    _expect_keys(payload, PARTITION_MANIFEST_KEYS, "partition manifest")
    _expect(
        payload.get("schema_version"),
        (
            "voice_coach.r02_r03_eligible_manifest.v1"
            if eligible
            else "voice_coach.r02_quarantine_manifest.v1"
        ),
        "partition schema",
    )
    _expect(
        payload.get("status"),
        "eligible_for_r03_private_selection"
        if eligible
        else "quarantined_preserved_in_d1",
        "partition status",
    )
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "partition privacy zone",
    )
    _expect(
        payload.get("gate_report_sha256"),
        gate_report_sha256,
        "partition report binding",
    )
    _expect(
        payload.get("candidate_count"),
        len(expected_candidate_ids),
        "partition count",
    )
    _expect(
        _list(payload.get("candidate_ids"), "partition candidate IDs"),
        list(expected_candidate_ids),
        "partition candidate IDs/order",
    )
    _expect(payload.get("source_labels_present"), False, "partition source labels")
    _expect(payload.get("r03_input_allowed"), eligible, "partition R03 access")
    _expect(payload.get("publication_allowed"), False, "partition publication")


def _validate_r02_completion_payload(
    payload: Mapping[str, Any],
    *,
    r02_config_sha256: str,
    r01: R01Evidence,
) -> None:
    _expect_keys(payload, R02_COMPLETION_KEYS, "R02 completion commit")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r02_hard_gate_completion.v1",
        "R02 completion schema",
    )
    _expect(payload.get("status"), "completed", "R02 completion status")
    _string(payload.get("run_id"), "R02 run ID")
    _expect(
        payload.get("config_sha256"),
        r02_config_sha256,
        "R02 completion config binding",
    )
    _expect(
        payload.get("r01_completion_commit_sha256"),
        r01.commit_snapshot.sha256,
        "R02/R01 commit binding",
    )
    _expect(
        payload.get("r01_candidate_manifest_sha256"),
        r01.manifest_snapshot.sha256,
        "R02/R01 manifest binding",
    )
    for field_name in (
        "preregistration_sha256",
        "asr_attestation_sha256",
        "artifact_attestation_sha256",
        "gate_report_sha256",
        "blinded_execution_manifest_sha256",
        "source_lineage_sidecar_sha256",
        "eligible_manifest_sha256",
        "quarantine_manifest_sha256",
        "registry_sha256",
    ):
        _sha256(payload.get(field_name), f"R02 completion {field_name}")
    candidate_count = _integer(
        payload.get("candidate_count"),
        "R02 completion candidate count",
        minimum=160,
        maximum=320,
    )
    _expect(candidate_count, r01.candidate_count, "R02/R01 candidate count")
    eligible_count = _integer(
        payload.get("eligible_count"),
        "R02 eligible count",
        minimum=0,
        maximum=candidate_count,
    )
    quarantined_count = _integer(
        payload.get("quarantined_count"),
        "R02 quarantined count",
        minimum=0,
        maximum=candidate_count,
    )
    _expect(
        eligible_count + quarantined_count,
        candidate_count,
        "R02 completion partition total",
    )
    _expect(
        payload.get("failed_candidates_preserved"),
        True,
        "R02 failure preservation",
    )
    _expect(payload.get("r03_input_pass_only"), True, "R02 R03 pass-only")
    _expect(payload.get("publication_allowed"), False, "R02 publication")


def _validate_r02_registry_payload(
    payload: Mapping[str, Any],
    *,
    completion: Mapping[str, Any],
) -> None:
    _expect_keys(payload, R02_REGISTRY_KEYS, "R02 registry")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r02_hard_gate_registry.v1",
        "R02 registry schema",
    )
    _expect(payload.get("run_id"), completion.get("run_id"), "R02 registry run ID")
    _expect(payload.get("status"), "completed", "R02 registry status")
    for field_name in (
        "config_sha256",
        "r01_completion_commit_sha256",
        "gate_report_sha256",
        "blinded_execution_manifest_sha256",
        "source_lineage_sidecar_sha256",
        "eligible_manifest_sha256",
        "quarantine_manifest_sha256",
        "candidate_count",
        "eligible_count",
        "quarantined_count",
    ):
        _expect(
            payload.get(field_name),
            completion.get(field_name),
            f"R02 registry {field_name}",
        )
    _expect(payload.get("failure"), None, "R02 registry failure")
    _expect(payload.get("publication_allowed"), False, "R02 registry publication")


def deep_validate_r02_completion(
    *,
    workspace_root: Path,
    r02_config: Mapping[str, Any],
    r02_config_sha256: str,
    completion_commit_path: str,
    completion_commit_sha256: str,
    r01: R01Evidence,
    audit: AccessAudit | None = None,
) -> R02CompletedEvidence:
    """Deep validator for R03: R01 must be supplied as already-valid evidence."""

    access = audit if audit is not None else AccessAudit()
    expected_commit_sha = _sha256(
        completion_commit_sha256, "expected R02 completion commit SHA"
    )
    commit_relative = _relative_string(
        completion_commit_path, "R02 completion commit path"
    )
    commit, commit_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=commit_relative,
        expected_sha256=expected_commit_sha,
        name="R02 completion commit",
    )
    _validate_r02_completion_payload(
        commit,
        r02_config_sha256=_sha256(r02_config_sha256, "R02 config SHA"),
        r01=r01,
    )
    prerequisites = validate_prerequisites(
        workspace_root=workspace_root,
        config=r02_config,
        audit=access,
    )
    policy = validate_preregistration_payload(
        prerequisites.preregistration,
        config=r02_config,
    )
    _expect(
        prerequisites.preregistration_snapshot.sha256,
        commit.get("preregistration_sha256"),
        "R02 commit preregistration binding",
    )
    _expect(
        prerequisites.asr_attestation_snapshot.sha256,
        commit.get("asr_attestation_sha256"),
        "R02 commit ASR binding",
    )
    _expect(
        prerequisites.artifact_attestation_snapshot.sha256,
        commit.get("artifact_attestation_sha256"),
        "R02 commit artifact binding",
    )
    outputs = _mapping(r02_config.get("outputs"), "R02 outputs")

    report, report_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(outputs.get("gate_report_path"), "gate report path"),
        expected_sha256=_sha256(
            commit.get("gate_report_sha256"), "R02 gate report SHA"
        ),
        name="R02 gate report",
    )
    report_summary = validate_gate_report_payload(
        report,
        candidate_rows=r01.candidate_rows,
        r01_completion_commit_sha256=r01.commit_snapshot.sha256,
        r01_candidate_manifest_sha256=r01.manifest_snapshot.sha256,
        preregistration_sha256=prerequisites.preregistration_snapshot.sha256,
        asr_attestation_sha256=prerequisites.asr_attestation_snapshot.sha256,
        artifact_attestation_sha256=prerequisites.artifact_attestation_snapshot.sha256,
        preregistered_policy=policy,
    )

    blinded, blinded_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            outputs.get("blinded_execution_manifest_path"),
            "blinded execution manifest path",
        ),
        expected_sha256=_sha256(
            commit.get("blinded_execution_manifest_sha256"),
            "blinded execution manifest SHA",
        ),
        name="R02 blinded execution manifest",
    )
    validate_blinded_execution_manifest_payload(blinded, r01=r01)

    lineage, lineage_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            outputs.get("source_lineage_sidecar_path"),
            "source lineage sidecar path",
        ),
        expected_sha256=_sha256(
            commit.get("source_lineage_sidecar_sha256"),
            "source lineage sidecar SHA",
        ),
        name="R02 source lineage sidecar",
    )
    validate_lineage_sidecar_payload(lineage, r01=r01)

    eligible, eligible_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            outputs.get("eligible_manifest_path"), "eligible manifest path"
        ),
        expected_sha256=_sha256(
            commit.get("eligible_manifest_sha256"), "eligible manifest SHA"
        ),
        name="R02 eligible manifest",
    )
    validate_partition_manifest_payload(
        eligible,
        gate_report_sha256=report_snapshot.sha256,
        expected_candidate_ids=_list(
            report.get("eligible_candidate_ids"), "eligible report IDs"
        ),
        eligible=True,
    )
    quarantine, quarantine_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            outputs.get("quarantine_manifest_path"), "quarantine manifest path"
        ),
        expected_sha256=_sha256(
            commit.get("quarantine_manifest_sha256"), "quarantine manifest SHA"
        ),
        name="R02 quarantine manifest",
    )
    validate_partition_manifest_payload(
        quarantine,
        gate_report_sha256=report_snapshot.sha256,
        expected_candidate_ids=_list(
            report.get("quarantined_candidate_ids"), "quarantine report IDs"
        ),
        eligible=False,
    )
    registry, registry_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(outputs.get("registry_path"), "R02 registry path"),
        expected_sha256=_sha256(commit.get("registry_sha256"), "R02 registry SHA"),
        name="R02 registry",
    )
    _validate_r02_registry_payload(registry, completion=commit)
    for field_name in ("candidate_count", "eligible_count", "quarantined_count"):
        _expect(
            commit.get(field_name),
            report_summary.get(field_name),
            f"R02 commit/report {field_name}",
        )
    evidence = R02CompletedEvidence(
        commit_snapshot=commit_snapshot,
        registry_snapshot=registry_snapshot,
        report_snapshot=report_snapshot,
        blinded_execution_manifest_snapshot=blinded_snapshot,
        source_lineage_sidecar_snapshot=lineage_snapshot,
        eligible_manifest_snapshot=eligible_snapshot,
        quarantine_manifest_snapshot=quarantine_snapshot,
        report=report,
        eligible_candidate_ids=tuple(
            _string(value, "eligible candidate ID")
            for value in _list(
                report.get("eligible_candidate_ids"), "eligible candidate IDs"
            )
        ),
        quarantined_candidate_ids=tuple(
            _string(value, "quarantined candidate ID")
            for value in _list(
                report.get("quarantined_candidate_ids"),
                "quarantined candidate IDs",
            )
        ),
    )
    for snapshot in (
        evidence.commit_snapshot,
        evidence.registry_snapshot,
        evidence.report_snapshot,
        evidence.blinded_execution_manifest_snapshot,
        evidence.source_lineage_sidecar_snapshot,
        evidence.eligible_manifest_snapshot,
        evidence.quarantine_manifest_snapshot,
        prerequisites.preregistration_snapshot,
        prerequisites.asr_attestation_snapshot,
        prerequisites.artifact_attestation_snapshot,
    ):
        _verify_snapshot(workspace_root, snapshot, "R02 immutable evidence")
    return evidence


def _verify_all_snapshots(
    *,
    workspace_root: Path,
    static: StaticValidation,
    r00: R00Evidence,
    r01_evidence: R01Evidence | None,
    prerequisites: PrerequisiteEvidence | None,
    audit: AccessAudit,
) -> None:
    snapshots: list[FileSnapshot] = list(static.source_snapshots)
    snapshots.extend((r00.report_snapshot, r00.card_snapshot))
    if r01_evidence is not None:
        snapshots.extend(
            (
                r01_evidence.commit_snapshot,
                r01_evidence.registry_snapshot,
                r01_evidence.manifest_snapshot,
                r01_evidence.human_attestation_snapshot,
                r01_evidence.cosyvoice_attestation_snapshot,
            )
        )
    if prerequisites is not None:
        snapshots.extend(
            (
                prerequisites.preregistration_snapshot,
                prerequisites.asr_attestation_snapshot,
                prerequisites.artifact_attestation_snapshot,
            )
        )
    for snapshot in snapshots:
        try:
            _verify_snapshot(workspace_root, snapshot, "preflight evidence")
        except R02ContractError as exc:
            raise R02ContractError(str(exc), stage="toctou", audit=audit) from exc


def ensure_output_targets_absent(
    *,
    workspace_root: Path,
    outputs: Mapping[str, Any],
) -> None:
    """No-overwrite and symlink-parent guard for future production outputs."""

    fields = (
        "blinded_execution_manifest_path",
        "source_lineage_sidecar_path",
        "gate_report_path",
        "eligible_manifest_path",
        "quarantine_manifest_path",
        "registry_path",
        "completion_commit_path",
        "failure_report_path",
    )
    for field_name in fields:
        relative = _relative_string(outputs.get(field_name), f"outputs.{field_name}")
        target = _safe_workspace_path(workspace_root, relative, field_name)
        if target.exists() or target.is_symlink():
            raise R02ContractError(f"no-overwrite target already exists: {relative}")


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Run ordered read-only gates; current canonical config stops at missing R01."""

    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
        audit=audit,
    )
    r00_evidence = deep_validate_r00(
        workspace_root=workspace_root,
        config=config,
        static=static,
        audit=audit,
    )
    _verify_all_snapshots(
        workspace_root=workspace_root,
        static=static,
        r00=r00_evidence,
        r01_evidence=None,
        prerequisites=None,
        audit=audit,
    )
    r01_evidence = deep_validate_r01_completion(
        workspace_root=workspace_root,
        config=config,
        r00=r00_evidence,
        audit=audit,
    )
    _verify_all_snapshots(
        workspace_root=workspace_root,
        static=static,
        r00=r00_evidence,
        r01_evidence=r01_evidence,
        prerequisites=None,
        audit=audit,
    )
    prerequisites = validate_prerequisites(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    _verify_all_snapshots(
        workspace_root=workspace_root,
        static=static,
        r00=r00_evidence,
        r01_evidence=r01_evidence,
        prerequisites=prerequisites,
        audit=audit,
    )

    # D1 audio is only located after R00, R01, preregistration and attestations.
    audit.d1_candidate_manifest_path_resolution_count += 1
    audit.d1_audio_root_path_resolution_count += 1
    audit.output_path_resolution_count += 8
    ensure_output_targets_absent(
        workspace_root=workspace_root,
        outputs=_mapping(config.get("outputs"), "outputs"),
    )
    raise R02ContractError(
        "all static gates validated; a separate blinded metric execution package "
        "is required",
        stage="separate_execution_package",
        audit=audit,
    )


def config_only_result(
    static: StaticValidation,
    audit: AccessAudit | None = None,
) -> Mapping[str, Any]:
    access = (
        audit
        if audit is not None
        else AccessAudit(source_pin_read_count=len(static.source_snapshots))
    )
    return {
        "status": "valid_config_only",
        "implementation_status": STATIC_STATUS,
        "config_sha256": static.config_sha256,
        "policy_sha256": static.policy_sha256,
        "source_pin_count": len(static.source_snapshots),
        **access.as_dict(),
        "r02_metric_runner_implemented": False,
        "actual_audio_read": False,
        "actual_model_read": False,
        "actual_gpu_access": False,
        "gate_report_created": False,
        "r02_completion_claim_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: R02ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "r02_metric_runner_implemented": False,
        "actual_audio_read": False,
        "actual_model_read": False,
        "actual_gpu_access": False,
        "gate_report_created": False,
        "r02_completion_claim_allowed": False,
        "publication_allowed": False,
    }
