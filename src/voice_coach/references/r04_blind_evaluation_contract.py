"""Fail-closed R04 blind multi-reference evaluation contract.

This package is deliberately read-only.  It validates static policy and,
only after every predecessor and human-governance artifact is pinned, may
validate future blinded manifests.  It never recruits a listener, opens or
writes audio, collects a rating, runs a model/GPU, computes MOS/CMOS or a
statistic, makes a superiority decision, writes a registry/commit, or
publishes a result.

Security-relevant order:

1. validate R04 pinned C1/static sources, schemas, and fail-closed templates;
2. deep-validate R00, then completed R01, then completed R02;
3. deep-validate the completed R03 bank and its R01/R02 immutable lineage;
4. validate human approval, frozen preregistration, pilot nuisance evidence,
   frozen SAP, and listener attestations, in that order;
5. stop before resolving a D1 audio, randomization, rating, or output path.

The canonical config intentionally contains unresolved predecessor commits,
human approvals, nuisance estimates, sample size, estimand/link/CI/bootstrap,
multiplicity, superiority comparator/margin, rater policy, and adjudication.
Those values are never inferred here.
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
import stat
import sys
from types import ModuleType
from typing import Any

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
STATIC_STATUS = "production_static_preflight_only"
TBD_PREREGISTRATION = "TBD_PREREGISTRATION"
TBD_EXTERNAL = "TBD_EXTERNAL"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SENTENCE_IDS = tuple(f"R00-{index:02d}" for index in range(1, 21))
SOURCE_KINDS = ("human_recording", "cosyvoice_tts")
MOS_DIMENSIONS = (
    "naturalness",
    "clarity",
    "target_phone_accuracy",
    "pedagogical_suitability",
)
BLIND_STIMULUS_RE = re.compile(r"^r04stim_[0-9a-f]{32}$")
ASSIGNMENT_RE = re.compile(r"^r04assign_[0-9a-f]{32}$")
PAIR_RE = re.compile(r"^r04pair_[0-9a-f]{32}$")

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_r00",
    "predecessor_r01",
    "predecessor_r02",
    "predecessor_r03",
    "human_research_approval",
    "preregistration",
    "pilot_nuisance_attestation",
    "statistical_analysis_plan",
    "listener_attestations",
    "evaluation_contract",
    "statistics_contract",
    "privacy_and_blindness",
    "runtime_inputs",
    "execution_order",
    "outputs",
}

SOURCE_PATHS = (
    "artifacts/reference/r00/r00_sentence_card_v1.validation.json",
    "configs/experiments/r01_human_cosyvoice_candidates_v1.yaml",
    "configs/experiments/r02_candidate_hard_gate_v1.yaml",
    "configs/experiments/r03_reference_bank_selection_v1.yaml",
    "configs/reference/r00_sentence_card_v1.schema.json",
    "configs/reference/r01_candidate_manifest_v1.schema.json",
    "configs/reference/r02_hard_gate_report_v1.schema.json",
    "configs/reference/r03_reference_bank_manifest_v1.schema.json",
    "configs/reference/r04_human_research_approval_v1.template.json",
    "configs/reference/r04_blind_evaluation_preregistration_v1.template.json",
    "configs/reference/r04_pilot_nuisance_attestation_v1.template.json",
    "configs/reference/r04_statistical_analysis_plan_v1.template.json",
    "configs/reference/r04_listener_attestation_v1.template.json",
    "configs/reference/r04_randomization_manifest_v1.schema.json",
    "configs/reference/r04_randomization_manifest_v1.template.json",
    "configs/reference/r04_rating_manifest_v1.schema.json",
    "configs/reference/r04_rating_manifest_v1.template.json",
    "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md",
    "scripts/validate_r00_sentence_card.py",
    "src/voice_coach/references/r01_candidate_contract.py",
    "src/voice_coach/references/r02_hard_gate_contract.py",
    "src/voice_coach/references/r03_selection_contract.py",
)

EXPECTED_EXECUTION_ORDER = (
    "validate_r04_static_config_sources_templates_and_schemas",
    "deep_validate_r00_valid_completion",
    "deep_validate_r01_completed_candidate_generation",
    "deep_validate_r02_completed_hard_gate_without_failure_rescue",
    "deep_validate_r03_completed_reference_bank",
    "validate_human_research_approval",
    "validate_frozen_r04_preregistration",
    "validate_approved_pilot_variance_and_intra_listener_correlation",
    "validate_frozen_statistical_analysis_plan",
    "validate_listener_attestations",
    "stop_before_d1_audio_randomization_or_rating_path",
)

RANDOMIZATION_KEYS = {
    "schema_version",
    "randomization_id",
    "status",
    "r03_completion_commit_sha256",
    "r03_bank_manifest_sha256",
    "r04_config_sha256",
    "r04_preregistration_sha256",
    "statistical_analysis_plan_sha256",
    "privacy_zone",
    "per_listener_within_sentence_randomization",
    "fatigue_blocks_present",
    "system_names_present",
    "filenames_or_paths_present",
    "synthesis_status_present",
    "source_labels_present",
    "ratings_present",
    "stimuli",
    "listener_schedules",
}
STIMULUS_KEYS = {
    "stimulus_blind_id",
    "sentence_id",
    "audio_object_id_sha256",
    "audio_sha256",
    "r03_candidate_lineage_sha256",
}
SCHEDULE_KEYS = {
    "listener_id_sha256",
    "schedule_id",
    "randomization_seed_commitment_sha256",
    "blocks",
}
BLOCK_KEYS = {
    "block_id",
    "block_order",
    "mandatory_break_after",
    "assignments",
}
MOS_ASSIGNMENT_KEYS = {
    "task_type",
    "assignment_id",
    "sentence_id",
    "stimulus_blind_id",
    "within_sentence_position",
    "block_position",
}
CMOS_ASSIGNMENT_KEYS = {
    "task_type",
    "assignment_id",
    "pair_blind_id",
    "sentence_id",
    "left_stimulus_blind_id",
    "right_stimulus_blind_id",
    "within_sentence_pair_position",
    "block_position",
}

RATING_KEYS = {
    "schema_version",
    "rating_manifest_id",
    "status",
    "randomization_manifest_sha256",
    "human_research_approval_sha256",
    "r04_preregistration_sha256",
    "statistical_analysis_plan_sha256",
    "listener_attestation_manifest_sha256",
    "privacy_zone",
    "system_names_present",
    "filenames_or_paths_present",
    "synthesis_status_present",
    "source_labels_present",
    "direct_identifiers_present",
    "free_text_present",
    "mos_unit",
    "cmos_unit",
    "mos_ratings",
    "cmos_ratings",
}
MOS_RATING_KEYS = {
    "listener_id_sha256",
    "assignment_id",
    "sentence_id",
    "stimulus_blind_id",
    "submission_sequence",
    *MOS_DIMENSIONS,
}
CMOS_RATING_KEYS = {
    "listener_id_sha256",
    "assignment_id",
    "pair_blind_id",
    "sentence_id",
    "left_stimulus_blind_id",
    "right_stimulus_blind_id",
    "submission_sequence",
    "cmos",
}

FORBIDDEN_BLIND_KEYS = {
    "name",
    "email",
    "phone",
    "address",
    "legal_name",
    "listener_name",
    "raw_listener_id",
    "free_text",
    "free_text_note",
    "comment",
    "source_path",
    "audio_path",
    "recording_path",
    "filename",
    "file_name",
    "system_name",
    "model_name",
    "source_kind",
    "human_or_tts",
    "synthesis_status",
    "synthesized",
    "candidate_id",
}
FORBIDDEN_BLIND_VALUE_TOKENS = (
    "human_recording",
    "cosyvoice",
    "cosyvoice_tts",
    "text-to-speech",
    "synthesized",
    "synthetic voice",
)

HUMAN_APPROVAL_KEYS = {
    "schema_version",
    "approval_id",
    "status",
    "approved_at_utc",
    "approving_body_role",
    "protocol_sha256",
    "consent_form_sha256",
    "listener_eligibility_protocol_sha256",
    "compensation_protocol_sha256",
    "withdrawal_protocol_sha256",
    "data_retention_policy_sha256",
    "privacy_review_sha256",
    "d1_pseudonymized_audio_and_ratings_approved",
    "a1_aggregate_analysis_approved",
    "direct_identifiers_allowed",
    "free_text_collection_allowed",
    "source_label_disclosure_to_listeners_allowed",
    "approval",
}
PREREG_KEYS = {
    "schema_version",
    "preregistration_id",
    "status",
    "registered_at_utc",
    "registered_by_role",
    "frozen_before_pilot_or_audio_or_rating_access",
    "r03_completion_commit_sha256",
    "r04_config_sha256",
    "evaluation_design",
    "blindness_and_randomization",
    "power_and_sampling",
    "analysis_commitment",
    "approval",
}
PILOT_KEYS = {
    "schema_version",
    "attestation_id",
    "status",
    "pilot_protocol_sha256",
    "r04_preregistration_sha256",
    "pilot_completed_at_utc",
    "pilot_blinded_to_system_and_synthesis_status",
    "pilot_data_excluded_from_confirmatory_evaluation",
    "privacy_zone",
    "pilot_listener_count",
    "pilot_sentence_count",
    "pilot_rating_count",
    "variance_estimates",
    "intra_listener_correlation_estimates",
    "individual_ratings_present",
    "listener_ids_present",
    "audio_or_candidate_ids_present",
    "source_labels_present",
    "approval",
}
SAP_KEYS = {
    "schema_version",
    "sap_id",
    "status",
    "frozen_at_utc",
    "frozen_before_confirmatory_audio_or_rating_access",
    "r04_preregistration_sha256",
    "pilot_nuisance_attestation_sha256",
    "sampling",
    "primary_analysis",
    "secondary_bootstrap",
    "confirmatory_decision",
    "approval",
}
LISTENER_ATTESTATION_KEYS = {
    "schema_version",
    "manifest_id",
    "status",
    "human_research_approval_sha256",
    "r04_preregistration_sha256",
    "statistical_analysis_plan_sha256",
    "privacy_zone",
    "direct_identifiers_present",
    "free_text_present",
    "attestations",
    "approval",
}
LISTENER_ROW_KEYS = {
    "listener_id_sha256",
    "consent_receipt_sha256",
    "eligibility_receipt_sha256",
    "training_completion_receipt_sha256",
    "listening_environment_receipt_sha256",
    "headphone_check_receipt_sha256",
    "source_blinding_acknowledged",
    "attestation_status",
}

R03_COMPLETION_KEYS = {
    "schema_version",
    "status",
    "run_id",
    "config_sha256",
    "r01_completion_commit_sha256",
    "r02_completion_commit_sha256",
    "r02_gate_report_sha256",
    "preregistration_sha256",
    "bank_manifest_sha256",
    "selection_trace_sha256",
    "aggregate_report_sha256",
    "registry_sha256",
    "sentence_count",
    "selected_candidate_count",
    "r04_status",
    "publication_allowed",
}
R03_REGISTRY_KEYS = {
    "schema_version",
    "run_id",
    "status",
    "config_sha256",
    "r01_completion_commit_sha256",
    "r02_completion_commit_sha256",
    "r02_gate_report_sha256",
    "preregistration_sha256",
    "bank_manifest_sha256",
    "selection_trace_sha256",
    "aggregate_report_sha256",
    "sentence_count",
    "selected_candidate_count",
    "failure",
    "publication_allowed",
}


class R04ContractError(ValueError):
    """Raised before unsupported, unsafe, unapproved, or unpinned R04 work."""

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
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise R04ContractError("YAML mapping keys must be strings")
        if key in result:
            raise R04ContractError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass
class AccessAudit:
    source_pin_read_count: int = 0
    source_toctou_verification_count: int = 0
    predecessor_static_source_read_count: int = 0
    r03_contract_load_count: int = 0
    r00_deep_validation_count: int = 0
    r00_public_or_aggregate_evidence_read_count: int = 0
    r00_raw_audio_read_count: int = 0
    r00_row_level_read_count: int = 0
    r01_completion_path_resolution_count: int = 0
    r01_completion_read_attempt_count: int = 0
    r01_completion_read_count: int = 0
    r01_manifest_read_count: int = 0
    r01_audio_read_count: int = 0
    r02_completion_path_resolution_count: int = 0
    r02_completion_read_attempt_count: int = 0
    r02_completion_read_count: int = 0
    r02_gate_report_read_count: int = 0
    r02_quarantine_row_read_count: int = 0
    r02_failure_rescue_count: int = 0
    r03_completion_path_resolution_count: int = 0
    r03_completion_read_attempt_count: int = 0
    r03_completion_read_count: int = 0
    r03_registry_read_count: int = 0
    r03_bank_manifest_read_count: int = 0
    r03_selection_trace_read_count: int = 0
    r03_aggregate_read_count: int = 0
    r03_bank_lineage_validation_count: int = 0
    human_approval_path_resolution_count: int = 0
    human_approval_read_attempt_count: int = 0
    human_approval_read_count: int = 0
    preregistration_path_resolution_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    pilot_attestation_path_resolution_count: int = 0
    pilot_attestation_read_attempt_count: int = 0
    pilot_attestation_read_count: int = 0
    sap_path_resolution_count: int = 0
    sap_read_attempt_count: int = 0
    sap_read_count: int = 0
    listener_attestation_path_resolution_count: int = 0
    listener_attestation_read_attempt_count: int = 0
    listener_attestation_read_count: int = 0
    d1_audio_path_resolution_count: int = 0
    d1_audio_read_count: int = 0
    d1_rating_path_resolution_count: int = 0
    d1_rating_read_count: int = 0
    listener_recruitment_count: int = 0
    audio_read_count: int = 0
    audio_write_count: int = 0
    model_access_count: int = 0
    gpu_access_count: int = 0
    randomization_execution_count: int = 0
    rating_collection_count: int = 0
    mos_computation_count: int = 0
    cmos_computation_count: int = 0
    statistics_computation_count: int = 0
    superiority_decision_count: int = 0
    result_generation_count: int = 0
    output_path_resolution_count: int = 0
    output_count: int = 0
    registry_write_count: int = 0
    commit_write_count: int = 0
    publication_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            key: int(value)
            for key, value in vars(self).items()
            if isinstance(value, int)
        }


@dataclass(frozen=True)
class FileSnapshot:
    relative_path: str
    sha256: str
    size: int
    device: int
    inode: int
    mtime_ns: int


@dataclass(frozen=True)
class StaticValidation:
    config_sha256: str
    policy_sha256: str
    source_snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True)
class R03CompletionEvidence:
    commit_sha256: str
    bank_manifest_sha256: str
    selected_candidate_count: int
    snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True)
class ApprovedEvidence:
    sha256: str
    snapshot: FileSnapshot
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PreflightResult:
    status: str
    config_sha256: str
    r00_report_sha256: str
    r01_commit_sha256: str
    r02_commit_sha256: str
    r03_commit_sha256: str
    human_approval_sha256: str
    preregistration_sha256: str
    pilot_attestation_sha256: str
    sap_sha256: str
    listener_attestation_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise R04ContractError(f"{name} must be a string-keyed mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise R04ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise R04ContractError(f"{name} must be a non-empty string")
    return value


def _integer(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise R04ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise R04ContractError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise R04ContractError(f"{name} must be <= {maximum}")
    return value


def _number(
    value: object,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R04ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise R04ContractError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise R04ContractError(f"{name} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise R04ContractError(f"{name} must be <= {maximum}")
    return result


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if not SHA256_RE.fullmatch(text):
        raise R04ContractError(f"{name} must be a lowercase SHA-256")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise R04ContractError(f"{name} drift: expected {expected!r}, got {actual!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise R04ContractError(
            f"{name} keys drift; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _validate_candidate_sources(design: Mapping[str, Any], name: str) -> None:
    """The condition set must name known sources; it need not include a human one.

    The architecture under study is a closed loop between a TTS generator and an
    evaluator, so a TTS-only condition set is a complete design. Requiring a human
    condition made the study unbuildable without changing what it measures. What
    stays is that every condition declares which kind it is, and that the listener
    is blind to it.
    """

    allowed = tuple(_list(design.get("candidate_sources_allowed"), name))
    unknown = set(allowed) - set(SOURCE_KINDS)
    if unknown:
        raise R04ContractError(f"{name} names unknown kinds: {sorted(unknown)}")
    minimum = design.get("candidate_sources_required_minimum_count")
    if not isinstance(minimum, int) or minimum < 1:
        raise R04ContractError(f"{name} minimum count must be a positive integer")
    if len(allowed) < minimum:
        raise R04ContractError(f"{name} allows fewer kinds than it requires")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise R04ContractError(f"{name} contains non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_nonfinite_tree(item, f"{name}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_nonfinite_tree(item, f"{name}[{index}]")


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise R04ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise R04ContractError(f"non-finite JSON constant prohibited: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    _reject_nonfinite_tree(value, "payload")
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


def _contains_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("TBD_")
    if isinstance(value, Mapping):
        return any(_contains_tbd(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tbd(item) for item in value)
    return False


def _collect_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            result.add(str(key))
            result.update(_collect_keys(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_collect_keys(item))
    return result


def _collect_strings(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, str):
        result.append(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            result.extend(_collect_strings(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_collect_strings(item))
    return result


def _reject_blind_leakage(value: Mapping[str, Any], name: str) -> None:
    forbidden = _collect_keys(value).intersection(FORBIDDEN_BLIND_KEYS)
    if forbidden:
        raise R04ContractError(
            f"{name} contains identity/path/source-label fields: {sorted(forbidden)}"
        )
    for text in _collect_strings(value):
        lowered = text.casefold()
        if any(token in lowered for token in FORBIDDEN_BLIND_VALUE_TOKENS):
            raise R04ContractError(f"{name} contains blind source/system value")
        if "/" in text or "\\" in text:
            raise R04ContractError(f"{name} contains a path-like value")


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in ("", ".", "..") for part in pure.parts)
        or "\\" in text
    ):
        raise R04ContractError(f"{name} must be a safe workspace-relative path")
    return text


def _reject_symlink_chain(
    root: Path,
    relative: str,
    name: str,
    *,
    require_leaf: bool,
) -> Path:
    relative = _relative_string(relative, name)
    parts = PurePosixPath(relative).parts
    current = root
    for index, part in enumerate(parts):
        current = current / part
        is_leaf = index == len(parts) - 1
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not require_leaf:
                return root / relative
            raise R04ContractError(f"{name} missing: {relative}") from None
        if stat.S_ISLNK(info.st_mode):
            raise R04ContractError(f"{name} symlink prohibited: {relative}")
        if not is_leaf and not stat.S_ISDIR(info.st_mode):
            raise R04ContractError(f"{name} parent is not a directory: {relative}")
        if is_leaf and require_leaf and not stat.S_ISREG(info.st_mode):
            raise R04ContractError(f"{name} must be a regular file: {relative}")
    return current


def _read_file(
    root: Path,
    relative: str,
    name: str,
) -> tuple[bytes, FileSnapshot]:
    path = _reject_symlink_chain(root, relative, name, require_leaf=True)
    before = path.stat(follow_symlinks=False)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise R04ContractError(f"{name} is not a regular file: {relative}")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise R04ContractError(f"{name} changed before open: {relative}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise R04ContractError(f"{name} changed while reading: {relative}")
    data = b"".join(chunks)
    return data, FileSnapshot(
        relative_path=relative,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        device=after.st_dev,
        inode=after.st_ino,
        mtime_ns=after.st_mtime_ns,
    )


def _verify_snapshot(root: Path, snapshot: FileSnapshot, name: str) -> None:
    _, current = _read_file(root, snapshot.relative_path, name)
    if current != snapshot:
        raise R04ContractError(
            f"{name} immutable snapshot drift: {snapshot.relative_path}"
        )


def _load_json_bytes(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R04ContractError(f"{name} invalid JSON: {exc}") from exc
    payload = _mapping(value, name)
    _reject_nonfinite_tree(payload, name)
    return payload


def load_json(path: Path) -> Mapping[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise R04ContractError(f"cannot read JSON {path}: {exc}") from exc
    return _load_json_bytes(data, str(path))


def _load_config_bytes(data: bytes) -> Mapping[str, Any]:
    try:
        loaded = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise R04ContractError(f"invalid YAML config: {exc}") from exc
    config = _mapping(loaded, "config")
    _reject_nonfinite_tree(config, "config")
    return config


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise R04ContractError(f"cannot read config {path}: {exc}") from exc
    return _load_config_bytes(data)


def _read_exact_json(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
    name: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    data, snapshot = _read_file(workspace_root, relative, name)
    if snapshot.sha256 != _sha256(expected_sha256, f"{name} expected SHA"):
        raise R04ContractError(f"{name} SHA-256 drift")
    return _load_json_bytes(data, name), snapshot


def _load_module(
    *,
    workspace_root: Path,
    relative: str,
    module_name: str,
) -> ModuleType:
    path = _reject_symlink_chain(
        workspace_root,
        relative,
        module_name,
        require_leaf=True,
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise R04ContractError(f"cannot load {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise R04ContractError(f"cannot import {module_name}: {exc}") from exc
    return module


def _validate_randomization_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        "randomization schema dialect",
    )
    _expect(schema.get("type"), "object", "randomization schema root")
    _expect(schema.get("additionalProperties"), False, "randomization strictness")
    _expect(
        set(_list(schema.get("required"), "randomization required")),
        RANDOMIZATION_KEYS,
        "randomization required fields",
    )
    properties = _mapping(schema.get("properties"), "randomization properties")
    _expect(set(properties), RANDOMIZATION_KEYS, "randomization root properties")
    definitions = _mapping(schema.get("$defs"), "randomization definitions")
    for name, expected in (
        ("stimulus", STIMULUS_KEYS),
        ("listenerSchedule", SCHEDULE_KEYS),
        ("block", BLOCK_KEYS),
        ("mosAssignment", MOS_ASSIGNMENT_KEYS),
        ("cmosAssignment", CMOS_ASSIGNMENT_KEYS),
    ):
        definition = _mapping(definitions.get(name), f"randomization {name}")
        _expect(
            definition.get("additionalProperties"),
            False,
            f"randomization {name} strictness",
        )
        _expect(
            set(_list(definition.get("required"), f"{name} required")),
            expected,
            f"randomization {name} required fields",
        )


def _validate_rating_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        "rating schema dialect",
    )
    _expect(schema.get("type"), "object", "rating schema root")
    _expect(schema.get("additionalProperties"), False, "rating strictness")
    _expect(
        set(_list(schema.get("required"), "rating required")),
        RATING_KEYS,
        "rating required fields",
    )
    properties = _mapping(schema.get("properties"), "rating properties")
    _expect(set(properties), RATING_KEYS, "rating root properties")
    definitions = _mapping(schema.get("$defs"), "rating definitions")
    for name, expected in (
        ("mosRating", MOS_RATING_KEYS),
        ("cmosRating", CMOS_RATING_KEYS),
    ):
        definition = _mapping(definitions.get(name), f"rating {name}")
        _expect(
            definition.get("additionalProperties"),
            False,
            f"rating {name} strictness",
        )
        _expect(
            set(_list(definition.get("required"), f"{name} required")),
            expected,
            f"rating {name} required fields",
        )
    mos_score = _mapping(definitions.get("mosScore"), "MOS score schema")
    _expect(mos_score.get("type"), "integer", "MOS integer")
    _expect(mos_score.get("minimum"), 1, "MOS minimum")
    _expect(mos_score.get("maximum"), 5, "MOS maximum")
    cmos = _mapping(
        _mapping(definitions.get("cmosRating"), "CMOS rating").get("properties"),
        "CMOS properties",
    )
    cmos_score = _mapping(cmos.get("cmos"), "CMOS score schema")
    _expect(cmos_score.get("type"), "integer", "CMOS integer")
    _expect(cmos_score.get("minimum"), -3, "CMOS minimum")
    _expect(cmos_score.get("maximum"), 3, "CMOS maximum")


def _validate_templates(payload_by_path: Mapping[str, bytes]) -> None:
    templates = {
        path: _load_json_bytes(payload_by_path[path], path)
        for path in (
            "configs/reference/r04_human_research_approval_v1.template.json",
            "configs/reference/r04_blind_evaluation_preregistration_v1.template.json",
            "configs/reference/r04_pilot_nuisance_attestation_v1.template.json",
            "configs/reference/r04_statistical_analysis_plan_v1.template.json",
            "configs/reference/r04_listener_attestation_v1.template.json",
            "configs/reference/r04_randomization_manifest_v1.template.json",
            "configs/reference/r04_rating_manifest_v1.template.json",
        )
    }
    for path, payload in templates.items():
        if not _contains_tbd(payload):
            raise R04ContractError(f"{path} must fail closed with TBD values")

    human = templates["configs/reference/r04_human_research_approval_v1.template.json"]
    _expect_keys(human, HUMAN_APPROVAL_KEYS, "human approval template")
    _expect(
        human.get("schema_version"),
        "voice_coach.r04_human_research_approval.v1",
        "human approval schema",
    )
    for field in (
        "d1_pseudonymized_audio_and_ratings_approved",
        "a1_aggregate_analysis_approved",
        "direct_identifiers_allowed",
        "free_text_collection_allowed",
        "source_label_disclosure_to_listeners_allowed",
    ):
        _expect(human.get(field), False, f"human template {field}")

    prereg = templates[
        "configs/reference/r04_blind_evaluation_preregistration_v1.template.json"
    ]
    _expect_keys(prereg, PREREG_KEYS, "R04 preregistration template")
    _expect(
        prereg.get("schema_version"),
        "voice_coach.r04_blind_evaluation_preregistration.v1",
        "R04 preregistration schema",
    )
    _expect(
        prereg.get("frozen_before_pilot_or_audio_or_rating_access"),
        True,
        "R04 preregistration ordering",
    )
    design = _mapping(prereg.get("evaluation_design"), "prereg evaluation design")
    _expect(design.get("sentence_count"), 20, "prereg sentence count")
    _validate_candidate_sources(design, "prereg sources")
    _expect(
        tuple(_list(design.get("mos_dimensions"), "prereg MOS dimensions")),
        MOS_DIMENSIONS,
        "prereg MOS dimensions",
    )
    _expect(design.get("mos_scale_minimum"), 1, "prereg MOS minimum")
    _expect(design.get("mos_scale_maximum"), 5, "prereg MOS maximum")
    _expect(design.get("cmos_scale_minimum"), -3, "prereg CMOS minimum")
    _expect(design.get("cmos_scale_maximum"), 3, "prereg CMOS maximum")
    power = _mapping(prereg.get("power_and_sampling"), "prereg power")
    _expect(power.get("target_power"), 0.8, "prereg power")
    _expect(power.get("two_sided_alpha"), 0.05, "prereg alpha")
    analysis = _mapping(prereg.get("analysis_commitment"), "prereg analysis")
    _expect(
        analysis.get("primary_model_family"),
        "ordinal_mixed_effects",
        "prereg primary family",
    )
    _expect(
        analysis.get("listener_random_effect_required"),
        True,
        "prereg listener random effect",
    )
    _expect(
        analysis.get("sentence_random_effect_required"),
        True,
        "prereg sentence random effect",
    )
    _expect(
        analysis.get("bootstrap_is_secondary_only"),
        True,
        "prereg bootstrap role",
    )

    pilot = templates[
        "configs/reference/r04_pilot_nuisance_attestation_v1.template.json"
    ]
    _expect_keys(pilot, PILOT_KEYS, "pilot nuisance template")
    _expect(pilot.get("privacy_zone"), "A1_aggregate_only", "pilot privacy")
    for field in (
        "individual_ratings_present",
        "listener_ids_present",
        "audio_or_candidate_ids_present",
        "source_labels_present",
    ):
        _expect(pilot.get(field), False, f"pilot template {field}")

    sap = templates["configs/reference/r04_statistical_analysis_plan_v1.template.json"]
    _expect_keys(sap, SAP_KEYS, "SAP template")
    _expect(
        sap.get("frozen_before_confirmatory_audio_or_rating_access"),
        True,
        "SAP ordering",
    )
    sampling = _mapping(sap.get("sampling"), "SAP sampling")
    _expect(sampling.get("target_power"), 0.8, "SAP power")
    _expect(sampling.get("two_sided_alpha"), 0.05, "SAP alpha")
    primary = _mapping(sap.get("primary_analysis"), "SAP primary")
    _expect(primary.get("model_family"), "ordinal_mixed_effects", "SAP family")
    _expect(
        primary.get("listener_random_effect_required"),
        True,
        "SAP listener effect",
    )
    _expect(
        primary.get("sentence_random_effect_required"),
        True,
        "SAP sentence effect",
    )
    secondary = _mapping(sap.get("secondary_bootstrap"), "SAP bootstrap")
    _expect(secondary.get("secondary_only"), True, "SAP bootstrap role")

    listener = templates["configs/reference/r04_listener_attestation_v1.template.json"]
    _expect_keys(listener, LISTENER_ATTESTATION_KEYS, "listener template")
    _expect(
        listener.get("direct_identifiers_present"),
        False,
        "listener direct identifiers",
    )
    _expect(listener.get("free_text_present"), False, "listener free text")
    listener_rows = _list(listener.get("attestations"), "listener attestations")
    if len(listener_rows) != 1:
        raise R04ContractError("listener template must contain one placeholder row")
    _expect_keys(
        _mapping(listener_rows[0], "listener template row"),
        LISTENER_ROW_KEYS,
        "listener template row",
    )

    randomization = templates[
        "configs/reference/r04_randomization_manifest_v1.template.json"
    ]
    _expect_keys(randomization, RANDOMIZATION_KEYS, "randomization template")
    for field in (
        "system_names_present",
        "filenames_or_paths_present",
        "synthesis_status_present",
        "source_labels_present",
        "ratings_present",
    ):
        _expect(randomization.get(field), False, f"randomization template {field}")
    _expect(
        randomization.get("per_listener_within_sentence_randomization"),
        True,
        "randomization template scope",
    )
    _expect(
        randomization.get("fatigue_blocks_present"),
        True,
        "randomization fatigue blocks",
    )

    rating = templates["configs/reference/r04_rating_manifest_v1.template.json"]
    _expect_keys(rating, RATING_KEYS, "rating template")
    for field in (
        "system_names_present",
        "filenames_or_paths_present",
        "synthesis_status_present",
        "source_labels_present",
        "direct_identifiers_present",
        "free_text_present",
    ):
        _expect(rating.get(field), False, f"rating template {field}")
    _expect(
        rating.get("mos_unit"),
        "listener_x_sentence_x_candidate",
        "rating MOS unit",
    )
    _expect(
        rating.get("cmos_unit"),
        "listener_x_sentence_x_same_sentence_pair",
        "rating CMOS unit",
    )


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_KEYS, "R04 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")

    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(
        experiment.get("id"),
        "r04_blind_multireference_evaluation_v1",
        "experiment id",
    )
    _expect(
        experiment.get("stage"),
        "wp7_r04_blind_multireference_human_evaluation",
        "experiment stage",
    )
    _expect(
        experiment.get("implementation_status"),
        STATIC_STATUS,
        "implementation status",
    )
    for field in (
        "actual_listener_recruitment_allowed",
        "actual_audio_read_allowed",
        "actual_audio_write_allowed",
        "actual_rating_read_allowed",
        "actual_rating_write_allowed",
        "actual_model_access_allowed",
        "actual_gpu_access_allowed",
        "actual_randomization_execution_allowed",
        "actual_metric_or_statistics_computation_allowed",
        "actual_result_generation_allowed",
        "actual_registry_or_commit_write_allowed",
        "publication_allowed",
        "completion_claim_allowed",
    ):
        _expect(experiment.get(field), False, f"experiment.{field}")

    r00 = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    _expect(r00.get("deep_validation_required"), True, "R00 deep validation")
    _expect(r00.get("required_status"), "valid", "R00 status")
    _expect(r00.get("required_sentence_count"), 20, "R00 sentence count")
    _expect(
        r00.get("required_completion_claim_allowed"),
        True,
        "R00 completion",
    )
    _expect(
        r00.get("raw_audio_or_row_read_allowed"),
        False,
        "R00 data boundary",
    )
    for field in (
        "card_sha256",
        "schema_sha256",
        "validator_sha256",
        "validation_report_sha256",
    ):
        _sha256(r00.get(field), f"predecessor_r00.{field}")

    for stage in ("predecessor_r01", "predecessor_r02", "predecessor_r03"):
        predecessor = _mapping(config.get(stage), stage)
        _expect(
            predecessor.get("deep_validation_required"),
            True,
            f"{stage} deep validation",
        )
        _expect(predecessor.get("required_status"), "completed", f"{stage} status")
        for field in ("config_sha256", "contract_sha256"):
            _sha256(predecessor.get(field), f"{stage}.{field}")
        completion = _string(
            predecessor.get("completion_commit_sha256"),
            f"{stage}.completion_commit_sha256",
        )
        if not completion.startswith("TBD_"):
            _sha256(completion, f"{stage}.completion_commit_sha256")
    r02 = _mapping(config.get("predecessor_r02"), "predecessor_r02")
    _expect(
        r02.get("eligible_only_for_r03_and_r04"),
        True,
        "R02 eligible-only boundary",
    )
    _expect(
        r02.get("hard_gate_failure_rescue_by_r04_allowed"),
        False,
        "R02 no-rescue boundary",
    )
    r03 = _mapping(config.get("predecessor_r03"), "predecessor_r03")
    _sha256(r03.get("bank_schema_sha256"), "R03 bank schema SHA")
    _expect(r03.get("required_sentence_count"), 20, "R03 sentence count")
    _expect(r03.get("selected_per_sentence_minimum"), 3, "R03 minimum selected")
    _expect(r03.get("selected_per_sentence_maximum"), 5, "R03 maximum selected")
    _expect(
        tuple(_list(r03.get("required_sources"), "R03 required sources")),
        SOURCE_KINDS,
        "R03 source coverage",
    )
    _expect(
        r03.get("publication_allowed_must_be_false"),
        True,
        "R03 publication boundary",
    )
    for field in ("completion_commit_schema_version", "registry_schema_version"):
        value = _string(r03.get(field), f"predecessor_r03.{field}")
        if value.startswith("TBD_") and value != "TBD_R03_COMPLETION":
            raise R04ContractError(f"predecessor_r03.{field} invalid TBD")

    approvals = (
        (
            "human_research_approval",
            TBD_EXTERNAL,
            "voice_coach.r04_human_research_approval.v1",
            "approved_and_active",
        ),
        (
            "preregistration",
            TBD_PREREGISTRATION,
            "voice_coach.r04_blind_evaluation_preregistration.v1",
            "approved_and_frozen",
        ),
        (
            "pilot_nuisance_attestation",
            TBD_EXTERNAL,
            "voice_coach.r04_pilot_nuisance_attestation.v1",
            "approved_aggregate_nuisance_estimates",
        ),
        (
            "statistical_analysis_plan",
            TBD_PREREGISTRATION,
            "voice_coach.r04_statistical_analysis_plan.v1",
            "approved_and_frozen",
        ),
        (
            "listener_attestations",
            TBD_EXTERNAL,
            "voice_coach.r04_listener_attestation_manifest.v1",
            "approved_for_confirmatory_rating",
        ),
    )
    for section_name, placeholder, schema_version, status in approvals:
        section = _mapping(config.get(section_name), section_name)
        _expect(section.get("placeholder"), placeholder, f"{section_name} placeholder")
        _sha256(section.get("template_sha256"), f"{section_name} template SHA")
        approved_sha = _string(
            section.get("approved_artifact_sha256"),
            f"{section_name} approved SHA",
        )
        if not approved_sha.startswith("TBD_"):
            _sha256(approved_sha, f"{section_name} approved SHA")
        _expect(
            section.get("required_schema_version"),
            schema_version,
            f"{section_name} schema",
        )
        _expect(section.get("required_status"), status, f"{section_name} status")
        _expect(
            section.get("unresolved_fields_must_fail_closed"),
            True,
            f"{section_name} TBD policy",
        )

    evaluation = _mapping(config.get("evaluation_contract"), "evaluation_contract")
    _expect(
        tuple(_list(evaluation.get("sentence_ids"), "evaluation sentence IDs")),
        SENTENCE_IDS,
        "evaluation sentence IDs",
    )
    _validate_candidate_sources(evaluation, "evaluation sources")
    _expect(
        evaluation.get("r02_hard_gate_failures_excluded_without_rescue"),
        True,
        "evaluation no-rescue rule",
    )
    mos = _mapping(evaluation.get("mos"), "MOS contract")
    _expect(mos.get("unit"), "listener_x_sentence_x_candidate", "MOS unit")
    _expect(mos.get("scale_minimum"), 1, "MOS minimum")
    _expect(mos.get("scale_maximum"), 5, "MOS maximum")
    _expect(mos.get("integer_only"), True, "MOS integer")
    _expect(
        tuple(_list(mos.get("dimensions"), "MOS dimensions")),
        MOS_DIMENSIONS,
        "MOS dimensions",
    )
    cmos = _mapping(evaluation.get("cmos"), "CMOS contract")
    _expect(
        cmos.get("unit"),
        "listener_x_sentence_x_same_sentence_pair",
        "CMOS unit",
    )
    _expect(cmos.get("scale_minimum"), -3, "CMOS minimum")
    _expect(cmos.get("scale_maximum"), 3, "CMOS maximum")
    _expect(cmos.get("integer_only"), True, "CMOS integer")
    _expect(
        cmos.get("pair_construction_policy"),
        TBD_PREREGISTRATION,
        "CMOS pair policy",
    )
    blindness = _mapping(evaluation.get("blindness"), "blindness")
    for field in (
        "conceal_system_names",
        "conceal_filenames_and_paths",
        "conceal_synthesis_status",
        "conceal_source_labels",
        "opaque_stimulus_ids_only",
    ):
        _expect(blindness.get(field), True, f"blindness.{field}")
    randomization = _mapping(evaluation.get("randomization"), "randomization")
    _expect(
        randomization.get("scope"),
        "per_listener_within_sentence",
        "randomization scope",
    )
    _expect(
        randomization.get("fatigue_blocks_required"),
        True,
        "fatigue blocks",
    )
    for field in (
        "algorithm",
        "seed_commitment",
        "maximum_assignments_per_block",
        "maximum_consecutive_same_sentence_assignments",
        "break_policy",
    ):
        _expect(
            randomization.get(field),
            TBD_PREREGISTRATION,
            f"randomization.{field}",
        )

    statistics = _mapping(config.get("statistics_contract"), "statistics_contract")
    _expect(statistics.get("target_power"), 0.8, "target power")
    _expect(statistics.get("two_sided_alpha"), 0.05, "two-sided alpha")
    _expect(statistics.get("pilot_variance"), TBD_EXTERNAL, "pilot variance")
    _expect(
        statistics.get("pilot_intra_listener_correlation"),
        TBD_EXTERNAL,
        "pilot intra-listener correlation",
    )
    _expect(
        statistics.get("listener_count_n"),
        TBD_PREREGISTRATION,
        "listener count N",
    )
    primary = _mapping(statistics.get("primary"), "primary statistics")
    _expect(
        primary.get("model_family"),
        "ordinal_mixed_effects",
        "primary model family",
    )
    _expect(
        primary.get("listener_random_effect_required"),
        True,
        "listener random effect",
    )
    _expect(
        primary.get("sentence_random_effect_required"),
        True,
        "sentence random effect",
    )
    secondary = _mapping(statistics.get("secondary"), "secondary statistics")
    _expect(secondary.get("bootstrap_only"), True, "bootstrap secondary role")
    for section, fields, placeholder in (
        (
            primary,
            (
                "exact_estimand",
                "ordinal_link_function",
                "confidence_interval_definition",
            ),
            TBD_PREREGISTRATION,
        ),
        (
            secondary,
            (
                "resampling_unit",
                "replicates",
                "seed_commitment",
                "confidence_interval_method",
            ),
            TBD_PREREGISTRATION,
        ),
        (
            statistics,
            (
                "multiplicity_strategy",
                "superiority_margin",
                "superiority_comparator",
                "rater_policy",
                "adjudication_policy",
            ),
            TBD_PREREGISTRATION,
        ),
    ):
        for field in fields:
            _expect(section.get(field), placeholder, f"statistics.{field}")

    privacy = _mapping(config.get("privacy_and_blindness"), "privacy")
    _expect(
        privacy.get("r03_bank_zone"),
        "D1_pseudonymized_restricted_experiment",
        "R03 zone",
    )
    _expect(
        privacy.get("randomization_manifest_zone"),
        "D1_pseudonymized_restricted_experiment",
        "randomization zone",
    )
    _expect(
        privacy.get("raw_rating_manifest_zone"),
        "D1_pseudonymized_restricted_experiment",
        "rating zone",
    )
    _expect(
        privacy.get("aggregate_analysis_zone"),
        "A1_aggregate_only",
        "aggregate zone",
    )
    for field in (
        "pii_allowed",
        "direct_identifiers_allowed",
        "free_text_allowed",
        "identity_fields_allowed",
        "audio_or_source_paths_allowed_in_manifests",
        "source_labels_allowed_in_blinded_manifests",
        "system_names_allowed_in_blinded_manifests",
        "synthesis_status_allowed_in_blinded_manifests",
        "individual_ratings_allowed_in_a1",
        "listener_ids_allowed_in_a1",
        "stimulus_ids_allowed_in_a1",
    ):
        _expect(privacy.get(field), False, f"privacy.{field}")
    _expect(
        privacy.get("publication_requires_separate_promotion"),
        True,
        "publication promotion",
    )
    _expect(
        set(_list(privacy.get("forbidden_recursive_fields"), "forbidden fields")),
        FORBIDDEN_BLIND_KEYS,
        "forbidden field set",
    )

    runtime = _mapping(config.get("runtime_inputs"), "runtime_inputs")
    _expect(
        runtime.get("d1_audio_access_attestation_sha256"),
        TBD_EXTERNAL,
        "D1 audio attestation",
    )
    _expect(
        runtime.get("rating_collection_attestation_sha256"),
        TBD_EXTERNAL,
        "rating collection attestation",
    )
    _expect(
        runtime.get("path_or_endpoint_values_must_not_appear_in_blinded_manifests"),
        True,
        "runtime path blindness",
    )
    _expect(
        runtime.get("read_enabled_in_this_package"),
        False,
        "runtime read boundary",
    )
    _expect(
        tuple(_list(config.get("execution_order"), "execution_order")),
        EXPECTED_EXECUTION_ORDER,
        "execution order",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    _expect(outputs.get("no_overwrite"), True, "output no-overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "output atomic publish")
    _expect(
        outputs.get("write_enabled_in_this_package"),
        False,
        "output write boundary",
    )
    for field, value in outputs.items():
        if field.endswith("_path"):
            _relative_string(value, f"outputs.{field}")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    """Validate only pinned static material; never resolve a runtime path."""

    root = workspace_root.resolve(strict=True)
    access = audit if audit is not None else AccessAudit()
    _validate_config_values(config)
    absolute_config = config_path if config_path.is_absolute() else root / config_path
    try:
        config_relative = absolute_config.relative_to(root).as_posix()
    except ValueError as exc:
        raise R04ContractError("R04 config must be inside workspace") from exc
    config_data, config_snapshot = _read_file(root, config_relative, "R04 config")
    if _load_config_bytes(config_data) != config:
        raise R04ContractError("R04 config changed between load and validation")

    canonical = _mapping(config.get("canonical_sources"), "canonical_sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_or_aggregate_static_pins_only",
        "canonical source scope",
    )
    pins = _list(canonical.get("pins"), "canonical source pins")
    if len(pins) != len(SOURCE_PATHS):
        raise R04ContractError("canonical source pin count drift")
    seen: set[str] = set()
    snapshots: list[FileSnapshot] = []
    payload_by_path: dict[str, bytes] = {}
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"canonical_sources.pins[{index}]")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        if relative in seen:
            raise R04ContractError(f"duplicate canonical source pin: {relative}")
        seen.add(relative)
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_sha:
            raise R04ContractError(
                f"canonical source SHA-256 drift: {relative}",
                stage="source_pin",
                audit=access,
            )
        snapshots.append(snapshot)
        payload_by_path[relative] = data
    if tuple(sorted(seen)) != tuple(sorted(SOURCE_PATHS)):
        raise R04ContractError("canonical source path set drift")

    randomization_schema = _load_json_bytes(
        payload_by_path["configs/reference/r04_randomization_manifest_v1.schema.json"],
        "R04 randomization schema",
    )
    rating_schema = _load_json_bytes(
        payload_by_path["configs/reference/r04_rating_manifest_v1.schema.json"],
        "R04 rating schema",
    )
    _validate_randomization_schema(randomization_schema)
    _validate_rating_schema(rating_schema)
    _validate_templates(payload_by_path)

    by_path = {snapshot.relative_path: snapshot for snapshot in snapshots}
    for section_name, config_source, contract_source in (
        (
            "predecessor_r01",
            "configs/experiments/r01_human_cosyvoice_candidates_v1.yaml",
            "src/voice_coach/references/r01_candidate_contract.py",
        ),
        (
            "predecessor_r02",
            "configs/experiments/r02_candidate_hard_gate_v1.yaml",
            "src/voice_coach/references/r02_hard_gate_contract.py",
        ),
        (
            "predecessor_r03",
            "configs/experiments/r03_reference_bank_selection_v1.yaml",
            "src/voice_coach/references/r03_selection_contract.py",
        ),
    ):
        predecessor = _mapping(config.get(section_name), section_name)
        _expect(
            predecessor.get("config_sha256"),
            by_path[config_source].sha256,
            f"{section_name} config/source binding",
        )
        _expect(
            predecessor.get("contract_sha256"),
            by_path[contract_source].sha256,
            f"{section_name} contract/source binding",
        )
    r03 = _mapping(config.get("predecessor_r03"), "predecessor_r03")
    _expect(
        r03.get("bank_schema_sha256"),
        by_path["configs/reference/r03_reference_bank_manifest_v1.schema.json"].sha256,
        "R03 bank schema/source binding",
    )
    r03_source_config = _load_config_bytes(
        payload_by_path["configs/experiments/r03_reference_bank_selection_v1.yaml"]
    )
    r03_outputs = _mapping(r03_source_config.get("outputs"), "pinned R03 outputs")
    for r04_field, r03_field in (
        ("bank_schema_path", "bank_schema_path"),
        ("bank_manifest_path", "bank_manifest_path"),
        ("selection_trace_path", "selection_trace_path"),
        ("aggregate_report_path", "aggregate_report_path"),
        ("completion_commit_path", "commit_path"),
        ("registry_path", "registry_path"),
    ):
        _expect(
            r03.get(r04_field),
            r03_outputs.get(r03_field),
            f"R04/R03 output binding {r04_field}",
        )
    for predecessor_name in ("predecessor_r01", "predecessor_r02"):
        r04_predecessor = _mapping(config.get(predecessor_name), predecessor_name)
        r03_predecessor = _mapping(
            r03_source_config.get(predecessor_name),
            f"pinned R03 {predecessor_name}",
        )
        for field in (
            "config_path",
            "config_sha256",
            "contract_path",
            "contract_sha256",
            "completion_commit_path",
            "completion_commit_sha256",
            "registry_path",
        ):
            _expect(
                r04_predecessor.get(field),
                r03_predecessor.get(field),
                f"R04/R03 {predecessor_name}.{field}",
            )

    template_bindings = (
        (
            "human_research_approval",
            "configs/reference/r04_human_research_approval_v1.template.json",
        ),
        (
            "preregistration",
            "configs/reference/r04_blind_evaluation_preregistration_v1.template.json",
        ),
        (
            "pilot_nuisance_attestation",
            "configs/reference/r04_pilot_nuisance_attestation_v1.template.json",
        ),
        (
            "statistical_analysis_plan",
            "configs/reference/r04_statistical_analysis_plan_v1.template.json",
        ),
        (
            "listener_attestations",
            "configs/reference/r04_listener_attestation_v1.template.json",
        ),
    )
    for section_name, template_path in template_bindings:
        section = _mapping(config.get(section_name), section_name)
        _expect(
            section.get("template_path"),
            template_path,
            f"{section_name} template path",
        )
        _expect(
            section.get("template_sha256"),
            by_path[template_path].sha256,
            f"{section_name} template pin",
        )

    outputs = _mapping(config.get("outputs"), "outputs")
    schema_fields = {"randomization_schema_path", "rating_schema_path"}
    writable_outputs = [
        _relative_string(value, f"outputs.{field}")
        for field, value in outputs.items()
        if field.endswith("_path") and field not in schema_fields
    ]
    if len(writable_outputs) != len(set(writable_outputs)):
        raise R04ContractError("R04 output paths must be unique")
    for output in writable_outputs:
        output_parts = PurePosixPath(output).parts
        for source in seen:
            source_parts = PurePosixPath(source).parts
            if (
                output_parts == source_parts
                or output_parts[: len(source_parts)] == source_parts
                or source_parts[: len(output_parts)] == output_parts
            ):
                raise R04ContractError(
                    f"R04 output/source overlap prohibited: {output} / {source}"
                )

    policy_payload = {
        "evaluation_contract": config["evaluation_contract"],
        "statistics_contract": config["statistics_contract"],
        "privacy_and_blindness": config["privacy_and_blindness"],
        "execution_order": config["execution_order"],
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy_payload)).hexdigest(),
        source_snapshots=tuple(snapshots),
    )


def _verify_snapshots(
    *,
    workspace_root: Path,
    snapshots: Sequence[FileSnapshot],
    audit: AccessAudit,
    stage: str,
) -> None:
    for snapshot in snapshots:
        try:
            _verify_snapshot(workspace_root, snapshot, stage)
        except R04ContractError as exc:
            raise R04ContractError(
                str(exc),
                stage=f"{stage}_toctou",
                audit=audit,
            ) from exc
        audit.source_toctou_verification_count += 1


def validate_randomization_manifest_payload(
    payload: Mapping[str, Any],
    *,
    maximum_assignments_per_block: int | None = None,
    maximum_consecutive_same_sentence: int | None = None,
) -> Mapping[str, Any]:
    """Validate a fully blinded, per-listener, fatigue-blocked assignment."""

    _reject_nonfinite_tree(payload, "R04 randomization manifest")
    _expect_keys(payload, RANDOMIZATION_KEYS, "R04 randomization manifest")
    _reject_blind_leakage(payload, "R04 randomization manifest")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r04_randomization_manifest.v1",
        "randomization schema version",
    )
    randomization_id = _string(
        payload.get("randomization_id"),
        "randomization ID",
    )
    if not re.fullmatch(r"r04rand_[0-9a-f]{32}", randomization_id):
        raise R04ContractError("invalid randomization ID")
    _expect(
        payload.get("status"),
        "randomized_and_locked_before_rating",
        "randomization status",
    )
    for field in (
        "r03_completion_commit_sha256",
        "r03_bank_manifest_sha256",
        "r04_config_sha256",
        "r04_preregistration_sha256",
        "statistical_analysis_plan_sha256",
    ):
        _sha256(payload.get(field), f"randomization.{field}")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "randomization privacy zone",
    )
    _expect(
        payload.get("per_listener_within_sentence_randomization"),
        True,
        "per-listener randomization",
    )
    _expect(payload.get("fatigue_blocks_present"), True, "fatigue blocks")
    for field in (
        "system_names_present",
        "filenames_or_paths_present",
        "synthesis_status_present",
        "source_labels_present",
        "ratings_present",
    ):
        _expect(payload.get(field), False, f"randomization.{field}")

    raw_stimuli = _list(payload.get("stimuli"), "randomization stimuli")
    if not 60 <= len(raw_stimuli) <= 100:
        raise R04ContractError("randomization must contain 60-100 stimuli")
    stimuli: dict[str, tuple[str, str, str]] = {}
    sentence_stimuli: dict[str, list[str]] = defaultdict(list)
    audio_hashes: set[str] = set()
    audio_object_hashes: set[str] = set()
    for index, raw_stimulus in enumerate(raw_stimuli):
        stimulus = _mapping(raw_stimulus, f"stimulus {index}")
        _expect_keys(stimulus, STIMULUS_KEYS, f"stimulus {index}")
        stimulus_id = _string(
            stimulus.get("stimulus_blind_id"),
            f"stimulus {index} blind ID",
        )
        if not BLIND_STIMULUS_RE.fullmatch(stimulus_id):
            raise R04ContractError(f"invalid blind stimulus ID: {stimulus_id}")
        sentence_id = _string(
            stimulus.get("sentence_id"),
            f"stimulus {index} sentence",
        )
        if sentence_id not in SENTENCE_IDS:
            raise R04ContractError(f"invalid sentence ID: {sentence_id}")
        audio_object = _sha256(
            stimulus.get("audio_object_id_sha256"),
            f"stimulus {index} audio object",
        )
        audio_sha = _sha256(
            stimulus.get("audio_sha256"),
            f"stimulus {index} audio",
        )
        lineage = _sha256(
            stimulus.get("r03_candidate_lineage_sha256"),
            f"stimulus {index} R03 lineage",
        )
        if stimulus_id in stimuli:
            raise R04ContractError(f"duplicate blind stimulus ID: {stimulus_id}")
        if audio_object in audio_object_hashes or audio_sha in audio_hashes:
            raise R04ContractError("duplicate randomized audio stimulus")
        stimuli[stimulus_id] = (sentence_id, audio_object, lineage)
        sentence_stimuli[sentence_id].append(stimulus_id)
        audio_object_hashes.add(audio_object)
        audio_hashes.add(audio_sha)
    if set(sentence_stimuli) != set(SENTENCE_IDS):
        raise R04ContractError("randomization must cover exactly R00-01..R00-20")
    for sentence_id, stimulus_ids in sentence_stimuli.items():
        if not 3 <= len(stimulus_ids) <= 5:
            raise R04ContractError(
                f"{sentence_id} randomization stimulus count must be 3-5"
            )

    raw_schedules = _list(
        payload.get("listener_schedules"),
        "listener schedules",
    )
    if not raw_schedules:
        raise R04ContractError("at least one listener schedule is required")
    listener_ids: set[str] = set()
    schedule_ids: set[str] = set()
    global_assignment_ids: set[str] = set()
    listener_assignment_count = 0
    for schedule_index, raw_schedule in enumerate(raw_schedules):
        schedule = _mapping(raw_schedule, f"listener schedule {schedule_index}")
        _expect_keys(
            schedule,
            SCHEDULE_KEYS,
            f"listener schedule {schedule_index}",
        )
        listener_id = _sha256(
            schedule.get("listener_id_sha256"),
            f"listener schedule {schedule_index} listener",
        )
        schedule_id = _string(
            schedule.get("schedule_id"),
            f"listener schedule {schedule_index} ID",
        )
        if not re.fullmatch(r"r04sched_[0-9a-f]{32}", schedule_id):
            raise R04ContractError("invalid listener schedule ID")
        _sha256(
            schedule.get("randomization_seed_commitment_sha256"),
            f"listener schedule {schedule_index} seed commitment",
        )
        if listener_id in listener_ids:
            raise R04ContractError("duplicate listener schedule")
        if schedule_id in schedule_ids:
            raise R04ContractError("duplicate schedule ID")
        listener_ids.add(listener_id)
        schedule_ids.add(schedule_id)

        blocks = _list(schedule.get("blocks"), "fatigue blocks")
        if len(blocks) < 2:
            raise R04ContractError("each listener requires at least two fatigue blocks")
        mos_seen: dict[str, list[str]] = defaultdict(list)
        mos_positions: dict[str, list[int]] = defaultdict(list)
        cmos_positions: dict[str, list[int]] = defaultdict(list)
        cmos_sentences: set[str] = set()
        local_assignment_ids: set[str] = set()
        ordered_sentence_ids: list[str] = []
        for block_index, raw_block in enumerate(blocks, start=1):
            block = _mapping(raw_block, f"listener block {block_index}")
            _expect_keys(block, BLOCK_KEYS, f"listener block {block_index}")
            block_id = _string(block.get("block_id"), "block ID")
            if not re.fullmatch(r"r04block_[0-9a-f]{32}", block_id):
                raise R04ContractError("invalid fatigue block ID")
            _expect(block.get("block_order"), block_index, "fatigue block order")
            expected_break = block_index < len(blocks)
            _expect(
                block.get("mandatory_break_after"),
                expected_break,
                "mandatory fatigue break placement",
            )
            assignments = _list(block.get("assignments"), "block assignments")
            if not assignments:
                raise R04ContractError("fatigue block cannot be empty")
            if (
                maximum_assignments_per_block is not None
                and len(assignments) > maximum_assignments_per_block
            ):
                raise R04ContractError("fatigue block exceeds preregistered maximum")
            for assignment_index, raw_assignment in enumerate(
                assignments,
                start=1,
            ):
                assignment = _mapping(
                    raw_assignment,
                    f"block assignment {assignment_index}",
                )
                task_type = _string(
                    assignment.get("task_type"),
                    "assignment task type",
                )
                expected_keys = (
                    MOS_ASSIGNMENT_KEYS
                    if task_type == "mos"
                    else CMOS_ASSIGNMENT_KEYS
                    if task_type == "cmos"
                    else set()
                )
                if not expected_keys:
                    raise R04ContractError(f"unsupported task type: {task_type}")
                _expect_keys(assignment, expected_keys, f"{task_type} assignment")
                assignment_id = _string(
                    assignment.get("assignment_id"),
                    "assignment ID",
                )
                if not ASSIGNMENT_RE.fullmatch(assignment_id):
                    raise R04ContractError("invalid assignment ID")
                if (
                    assignment_id in local_assignment_ids
                    or assignment_id in global_assignment_ids
                ):
                    raise R04ContractError("duplicate assignment ID")
                local_assignment_ids.add(assignment_id)
                global_assignment_ids.add(assignment_id)
                _expect(
                    assignment.get("block_position"),
                    assignment_index,
                    "assignment block position",
                )
                sentence_id = _string(
                    assignment.get("sentence_id"),
                    "assignment sentence",
                )
                if sentence_id not in sentence_stimuli:
                    raise R04ContractError("assignment has unknown sentence")
                ordered_sentence_ids.append(sentence_id)
                if task_type == "mos":
                    stimulus_id = _string(
                        assignment.get("stimulus_blind_id"),
                        "MOS stimulus ID",
                    )
                    if stimulus_id not in stimuli:
                        raise R04ContractError("MOS assignment has unknown stimulus")
                    if stimuli[stimulus_id][0] != sentence_id:
                        raise R04ContractError(
                            "MOS assignment sentence/stimulus mismatch"
                        )
                    mos_seen[sentence_id].append(stimulus_id)
                    mos_positions[sentence_id].append(
                        _integer(
                            assignment.get("within_sentence_position"),
                            "MOS within-sentence position",
                            minimum=1,
                            maximum=5,
                        )
                    )
                else:
                    pair_id = _string(
                        assignment.get("pair_blind_id"),
                        "CMOS pair ID",
                    )
                    if not PAIR_RE.fullmatch(pair_id):
                        raise R04ContractError("invalid CMOS pair ID")
                    left = _string(
                        assignment.get("left_stimulus_blind_id"),
                        "CMOS left stimulus",
                    )
                    right = _string(
                        assignment.get("right_stimulus_blind_id"),
                        "CMOS right stimulus",
                    )
                    if left == right:
                        raise R04ContractError("CMOS pair stimuli must differ")
                    if left not in stimuli or right not in stimuli:
                        raise R04ContractError("CMOS pair has unknown stimulus")
                    if (
                        stimuli[left][0] != sentence_id
                        or stimuli[right][0] != sentence_id
                    ):
                        raise R04ContractError(
                            "CMOS pair must contain same-sentence stimuli"
                        )
                    cmos_positions[sentence_id].append(
                        _integer(
                            assignment.get("within_sentence_pair_position"),
                            "CMOS within-sentence pair position",
                            minimum=1,
                        )
                    )
                    cmos_sentences.add(sentence_id)
        if set(mos_seen) != set(SENTENCE_IDS):
            raise R04ContractError("each listener MOS schedule must cover 20 sentences")
        for sentence_id in SENTENCE_IDS:
            expected_stimuli = set(sentence_stimuli[sentence_id])
            if set(mos_seen[sentence_id]) != expected_stimuli:
                raise R04ContractError(
                    f"{sentence_id} listener schedule must rate every candidate once"
                )
            if len(mos_seen[sentence_id]) != len(expected_stimuli):
                raise R04ContractError(
                    f"{sentence_id} listener schedule repeats a MOS candidate"
                )
            expected_positions = list(range(1, len(expected_stimuli) + 1))
            if mos_positions[sentence_id] != expected_positions:
                raise R04ContractError(
                    f"{sentence_id} MOS order is not within-sentence randomized order"
                )
        if cmos_sentences != set(SENTENCE_IDS):
            raise R04ContractError(
                "each listener CMOS schedule must cover all 20 sentences"
            )
        for sentence_id, positions in cmos_positions.items():
            if positions != list(range(1, len(positions) + 1)):
                raise R04ContractError(
                    f"{sentence_id} CMOS pair positions must be consecutive"
                )
        if maximum_consecutive_same_sentence is not None:
            run = 0
            previous: str | None = None
            for sentence_id in ordered_sentence_ids:
                run = run + 1 if sentence_id == previous else 1
                previous = sentence_id
                if run > maximum_consecutive_same_sentence:
                    raise R04ContractError(
                        "schedule exceeds preregistered similar-item run limit"
                    )
        listener_assignment_count += len(local_assignment_ids)

    return {
        "status": "valid_blinded_randomization",
        "sentence_count": 20,
        "stimulus_count": len(stimuli),
        "listener_count": len(listener_ids),
        "assignment_count": listener_assignment_count,
        "per_listener_within_sentence_randomization": True,
        "fatigue_blocks_present": True,
        "blind_source_labels_present": False,
        "publication_allowed": False,
    }


def _randomization_assignments(
    payload: Mapping[str, Any],
) -> tuple[
    dict[tuple[str, str], Mapping[str, Any]],
    set[str],
]:
    assignments: dict[tuple[str, str], Mapping[str, Any]] = {}
    listener_ids: set[str] = set()
    for raw_schedule in _list(
        payload.get("listener_schedules"),
        "listener schedules",
    ):
        schedule = _mapping(raw_schedule, "listener schedule")
        listener_id = _sha256(
            schedule.get("listener_id_sha256"),
            "schedule listener ID",
        )
        listener_ids.add(listener_id)
        for raw_block in _list(schedule.get("blocks"), "schedule blocks"):
            block = _mapping(raw_block, "schedule block")
            for raw_assignment in _list(
                block.get("assignments"),
                "schedule assignments",
            ):
                assignment = _mapping(raw_assignment, "schedule assignment")
                assignment_id = _string(
                    assignment.get("assignment_id"),
                    "schedule assignment ID",
                )
                key = (listener_id, assignment_id)
                if key in assignments:
                    raise R04ContractError("duplicate scheduled assignment")
                assignments[key] = assignment
    return assignments, listener_ids


def validate_rating_manifest_payload(
    payload: Mapping[str, Any],
    *,
    randomization_manifest: Mapping[str, Any] | None = None,
    attested_listener_ids: Sequence[str] | None = None,
) -> Mapping[str, Any]:
    """Validate raw D1 ratings without computing MOS, CMOS, or statistics."""

    _reject_nonfinite_tree(payload, "R04 rating manifest")
    _expect_keys(payload, RATING_KEYS, "R04 rating manifest")
    _reject_blind_leakage(payload, "R04 rating manifest")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r04_rating_manifest.v1",
        "rating schema version",
    )
    manifest_id = _string(payload.get("rating_manifest_id"), "rating manifest ID")
    if not re.fullmatch(r"r04ratings_[0-9a-f]{32}", manifest_id):
        raise R04ContractError("invalid rating manifest ID")
    _expect(payload.get("status"), "ratings_locked_blinded", "rating status")
    for field in (
        "randomization_manifest_sha256",
        "human_research_approval_sha256",
        "r04_preregistration_sha256",
        "statistical_analysis_plan_sha256",
        "listener_attestation_manifest_sha256",
    ):
        _sha256(payload.get(field), f"ratings.{field}")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "rating privacy zone",
    )
    for field in (
        "system_names_present",
        "filenames_or_paths_present",
        "synthesis_status_present",
        "source_labels_present",
        "direct_identifiers_present",
        "free_text_present",
    ):
        _expect(payload.get(field), False, f"ratings.{field}")
    _expect(
        payload.get("mos_unit"),
        "listener_x_sentence_x_candidate",
        "MOS unit",
    )
    _expect(
        payload.get("cmos_unit"),
        "listener_x_sentence_x_same_sentence_pair",
        "CMOS unit",
    )

    scheduled: dict[tuple[str, str], Mapping[str, Any]] = {}
    scheduled_listeners: set[str] = set()
    if randomization_manifest is not None:
        validate_randomization_manifest_payload(randomization_manifest)
        expected_randomization_sha = hashlib.sha256(
            canonical_json_bytes(randomization_manifest)
        ).hexdigest()
        _expect(
            payload.get("randomization_manifest_sha256"),
            expected_randomization_sha,
            "rating/randomization hash binding",
        )
        scheduled, scheduled_listeners = _randomization_assignments(
            randomization_manifest
        )

    attested: set[str] | None = None
    if attested_listener_ids is not None:
        attested = {
            _sha256(value, "attested listener ID") for value in attested_listener_ids
        }
        if len(attested) != len(attested_listener_ids):
            raise R04ContractError("duplicate attested listener ID")

    observed_assignments: set[tuple[str, str]] = set()
    observed_listeners: set[str] = set()
    mos_units: set[tuple[str, str, str]] = set()
    cmos_units: set[tuple[str, str, str, str]] = set()
    mos_rows = _list(payload.get("mos_ratings"), "MOS ratings")
    cmos_rows = _list(payload.get("cmos_ratings"), "CMOS ratings")
    if not mos_rows or not cmos_rows:
        raise R04ContractError("both MOS and CMOS ratings are required")
    for index, raw_rating in enumerate(mos_rows):
        rating = _mapping(raw_rating, f"MOS rating {index}")
        _expect_keys(rating, MOS_RATING_KEYS, f"MOS rating {index}")
        listener_id = _sha256(
            rating.get("listener_id_sha256"),
            f"MOS rating {index} listener",
        )
        assignment_id = _string(
            rating.get("assignment_id"),
            f"MOS rating {index} assignment",
        )
        if not ASSIGNMENT_RE.fullmatch(assignment_id):
            raise R04ContractError("invalid MOS assignment ID")
        sentence_id = _string(
            rating.get("sentence_id"),
            f"MOS rating {index} sentence",
        )
        if sentence_id not in SENTENCE_IDS:
            raise R04ContractError("invalid MOS sentence ID")
        stimulus_id = _string(
            rating.get("stimulus_blind_id"),
            f"MOS rating {index} stimulus",
        )
        if not BLIND_STIMULUS_RE.fullmatch(stimulus_id):
            raise R04ContractError("invalid MOS blind stimulus ID")
        _integer(
            rating.get("submission_sequence"),
            f"MOS rating {index} submission sequence",
            minimum=1,
        )
        for dimension in MOS_DIMENSIONS:
            _integer(
                rating.get(dimension),
                f"MOS rating {index}.{dimension}",
                minimum=1,
                maximum=5,
            )
        assignment_key = (listener_id, assignment_id)
        mos_unit = (listener_id, sentence_id, stimulus_id)
        if assignment_key in observed_assignments:
            raise R04ContractError("duplicate rating assignment")
        if mos_unit in mos_units:
            raise R04ContractError("duplicate listener x sentence x candidate MOS")
        observed_assignments.add(assignment_key)
        mos_units.add(mos_unit)
        observed_listeners.add(listener_id)
        if scheduled:
            expected = scheduled.get(assignment_key)
            if expected is None:
                raise R04ContractError("MOS rating was not randomized")
            _expect(expected.get("task_type"), "mos", "MOS task binding")
            _expect(
                expected.get("sentence_id"),
                sentence_id,
                "MOS sentence binding",
            )
            _expect(
                expected.get("stimulus_blind_id"),
                stimulus_id,
                "MOS stimulus binding",
            )

    for index, raw_rating in enumerate(cmos_rows):
        rating = _mapping(raw_rating, f"CMOS rating {index}")
        _expect_keys(rating, CMOS_RATING_KEYS, f"CMOS rating {index}")
        listener_id = _sha256(
            rating.get("listener_id_sha256"),
            f"CMOS rating {index} listener",
        )
        assignment_id = _string(
            rating.get("assignment_id"),
            f"CMOS rating {index} assignment",
        )
        pair_id = _string(
            rating.get("pair_blind_id"),
            f"CMOS rating {index} pair",
        )
        sentence_id = _string(
            rating.get("sentence_id"),
            f"CMOS rating {index} sentence",
        )
        left = _string(
            rating.get("left_stimulus_blind_id"),
            f"CMOS rating {index} left stimulus",
        )
        right = _string(
            rating.get("right_stimulus_blind_id"),
            f"CMOS rating {index} right stimulus",
        )
        if not ASSIGNMENT_RE.fullmatch(assignment_id):
            raise R04ContractError("invalid CMOS assignment ID")
        if not PAIR_RE.fullmatch(pair_id):
            raise R04ContractError("invalid CMOS pair ID")
        if sentence_id not in SENTENCE_IDS:
            raise R04ContractError("invalid CMOS sentence ID")
        if (
            not BLIND_STIMULUS_RE.fullmatch(left)
            or not BLIND_STIMULUS_RE.fullmatch(right)
            or left == right
        ):
            raise R04ContractError("invalid CMOS same-sentence pair")
        _integer(
            rating.get("submission_sequence"),
            f"CMOS rating {index} submission sequence",
            minimum=1,
        )
        _integer(
            rating.get("cmos"),
            f"CMOS rating {index} score",
            minimum=-3,
            maximum=3,
        )
        assignment_key = (listener_id, assignment_id)
        cmos_unit = (listener_id, sentence_id, left, right)
        if assignment_key in observed_assignments:
            raise R04ContractError("duplicate rating assignment")
        if cmos_unit in cmos_units:
            raise R04ContractError(
                "duplicate listener x sentence x same-sentence-pair CMOS"
            )
        observed_assignments.add(assignment_key)
        cmos_units.add(cmos_unit)
        observed_listeners.add(listener_id)
        if scheduled:
            expected = scheduled.get(assignment_key)
            if expected is None:
                raise R04ContractError("CMOS rating was not randomized")
            _expect(expected.get("task_type"), "cmos", "CMOS task binding")
            for field, actual in (
                ("pair_blind_id", pair_id),
                ("sentence_id", sentence_id),
                ("left_stimulus_blind_id", left),
                ("right_stimulus_blind_id", right),
            ):
                _expect(expected.get(field), actual, f"CMOS {field} binding")

    if scheduled and observed_assignments != set(scheduled):
        missing = len(set(scheduled) - observed_assignments)
        extra = len(observed_assignments - set(scheduled))
        raise R04ContractError(
            f"rating/randomization assignment mismatch; missing={missing}, extra={extra}"
        )
    if scheduled_listeners and observed_listeners != scheduled_listeners:
        raise R04ContractError("rating listener set differs from randomization")
    if attested is not None and observed_listeners != attested:
        raise R04ContractError("every and only attested listeners may rate")
    sentence_coverage = {sentence_id for _, sentence_id, _ in mos_units} | {
        sentence_id for _, sentence_id, _, _ in cmos_units
    }
    if sentence_coverage != set(SENTENCE_IDS):
        raise R04ContractError("rating manifest must cover all 20 sentences")
    return {
        "status": "valid_blinded_raw_ratings",
        "listener_count": len(observed_listeners),
        "mos_rating_count": len(mos_rows),
        "cmos_rating_count": len(cmos_rows),
        "mos_or_cmos_computed": False,
        "statistics_computed": False,
        "publication_allowed": False,
    }


def _require_no_tbd(payload: Mapping[str, Any], name: str) -> None:
    if _contains_tbd(payload):
        raise R04ContractError(f"{name} contains unresolved TBD")


def validate_human_research_approval_payload(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    _expect_keys(payload, HUMAN_APPROVAL_KEYS, "human research approval")
    _require_no_tbd(payload, "human research approval")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r04_human_research_approval.v1",
        "human approval schema",
    )
    _expect(payload.get("status"), "approved_and_active", "human approval status")
    for field in (
        "protocol_sha256",
        "consent_form_sha256",
        "listener_eligibility_protocol_sha256",
        "compensation_protocol_sha256",
        "withdrawal_protocol_sha256",
        "data_retention_policy_sha256",
        "privacy_review_sha256",
    ):
        _sha256(payload.get(field), f"human approval.{field}")
    _expect(
        payload.get("d1_pseudonymized_audio_and_ratings_approved"),
        True,
        "human D1 approval",
    )
    _expect(
        payload.get("a1_aggregate_analysis_approved"),
        True,
        "human A1 approval",
    )
    for field in (
        "direct_identifiers_allowed",
        "free_text_collection_allowed",
        "source_label_disclosure_to_listeners_allowed",
    ):
        _expect(payload.get(field), False, f"human approval.{field}")
    approval = _mapping(payload.get("approval"), "human approval receipt")
    _expect_keys(
        approval,
        {
            "decision",
            "signed_receipt_sha256",
            "immutable_archive_receipt_sha256",
        },
        "human approval receipt",
    )
    _expect(approval.get("decision"), "approved", "human approval decision")
    _sha256(approval.get("signed_receipt_sha256"), "human signed receipt")
    _sha256(
        approval.get("immutable_archive_receipt_sha256"),
        "human archive receipt",
    )
    forbidden = _collect_keys(payload).intersection(
        {
            "name",
            "email",
            "phone",
            "address",
            "legal_name",
            "free_text",
            "comment",
        }
    )
    if forbidden:
        raise R04ContractError(
            f"human approval contains PII/free-text fields: {sorted(forbidden)}"
        )
    return {"status": "valid_human_research_approval"}


def validate_preregistration_payload(
    payload: Mapping[str, Any],
    *,
    r03_completion_commit_sha256: str,
    r04_config_sha256: str,
) -> Mapping[str, Any]:
    _expect_keys(payload, PREREG_KEYS, "R04 preregistration")
    _require_no_tbd(payload, "R04 preregistration")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r04_blind_evaluation_preregistration.v1",
        "R04 preregistration schema",
    )
    _expect(
        payload.get("status"),
        "approved_and_frozen",
        "R04 preregistration status",
    )
    _expect(
        payload.get("frozen_before_pilot_or_audio_or_rating_access"),
        True,
        "R04 preregistration ordering",
    )
    _expect(
        payload.get("r03_completion_commit_sha256"),
        _sha256(r03_completion_commit_sha256, "R03 completion SHA"),
        "preregistration R03 binding",
    )
    _expect(
        payload.get("r04_config_sha256"),
        _sha256(r04_config_sha256, "R04 config SHA"),
        "preregistration R04 binding",
    )
    design = _mapping(payload.get("evaluation_design"), "evaluation design")
    _expect_keys(
        design,
        {
            "sentence_count",
            "candidate_sources_allowed",
            "candidate_sources_required_minimum_count",
            "candidate_sources_rationale_ko",
            "r02_hard_gate_failures_excluded_without_rescue",
            "mos_unit",
            "cmos_unit",
            "mos_scale_minimum",
            "mos_scale_maximum",
            "mos_dimensions",
            "cmos_scale_minimum",
            "cmos_scale_maximum",
            "listener_population",
            "listener_inclusion_criteria",
            "listener_exclusion_criteria",
            "rater_training_protocol",
            "adjudication_policy",
        },
        "preregistered evaluation design",
    )
    _expect(design.get("sentence_count"), 20, "preregistered sentence count")
    _validate_candidate_sources(design, "preregistered sources")
    _expect(
        design.get("r02_hard_gate_failures_excluded_without_rescue"),
        True,
        "preregistered no-rescue rule",
    )
    _expect(
        design.get("mos_unit"),
        "listener_x_sentence_x_candidate",
        "preregistered MOS unit",
    )
    _expect(
        design.get("cmos_unit"),
        "listener_x_sentence_x_same_sentence_pair",
        "preregistered CMOS unit",
    )
    _expect(design.get("mos_scale_minimum"), 1, "preregistered MOS minimum")
    _expect(design.get("mos_scale_maximum"), 5, "preregistered MOS maximum")
    _expect(
        tuple(_list(design.get("mos_dimensions"), "MOS dimensions")),
        MOS_DIMENSIONS,
        "preregistered MOS dimensions",
    )
    _expect(design.get("cmos_scale_minimum"), -3, "preregistered CMOS minimum")
    _expect(design.get("cmos_scale_maximum"), 3, "preregistered CMOS maximum")
    blindness = _mapping(
        payload.get("blindness_and_randomization"),
        "preregistered blindness",
    )
    _expect_keys(
        blindness,
        {
            "conceal_system_names",
            "conceal_filenames_and_paths",
            "conceal_synthesis_status",
            "per_listener_within_sentence_randomization",
            "fatigue_blocks_required",
            "randomization_algorithm",
            "randomization_seed_commitment",
            "maximum_assignments_per_fatigue_block",
            "maximum_consecutive_same_sentence_assignments",
            "minimum_break_duration_policy",
            "cmos_pair_construction_policy",
            "carryover_control_policy",
        },
        "preregistered blindness",
    )
    for field in (
        "conceal_system_names",
        "conceal_filenames_and_paths",
        "conceal_synthesis_status",
        "per_listener_within_sentence_randomization",
        "fatigue_blocks_required",
    ):
        _expect(blindness.get(field), True, f"preregistered blindness.{field}")
    power = _mapping(payload.get("power_and_sampling"), "preregistered sampling")
    _expect_keys(
        power,
        {
            "target_power",
            "two_sided_alpha",
            "pilot_variance_source",
            "pilot_intra_listener_correlation_source",
            "sample_size_method",
            "listener_count_n",
            "attrition_policy",
            "missing_rating_policy",
        },
        "preregistered sampling",
    )
    _expect(power.get("target_power"), 0.8, "preregistered power")
    _expect(power.get("two_sided_alpha"), 0.05, "preregistered alpha")
    _integer(power.get("listener_count_n"), "preregistered listener N", minimum=1)
    analysis = _mapping(payload.get("analysis_commitment"), "analysis commitment")
    _expect_keys(
        analysis,
        {
            "primary_model_family",
            "listener_random_effect_required",
            "sentence_random_effect_required",
            "bootstrap_is_secondary_only",
            "exact_primary_estimand",
            "ordinal_link_function",
            "confidence_interval_definition",
            "bootstrap_resampling_unit",
            "bootstrap_replicates",
            "bootstrap_seed_commitment",
            "multiplicity_strategy",
            "superiority_margin",
            "superiority_comparator",
        },
        "analysis commitment",
    )
    _expect(
        analysis.get("primary_model_family"),
        "ordinal_mixed_effects",
        "primary analysis family",
    )
    _expect(
        analysis.get("listener_random_effect_required"),
        True,
        "listener random effect",
    )
    _expect(
        analysis.get("sentence_random_effect_required"),
        True,
        "sentence random effect",
    )
    _expect(
        analysis.get("bootstrap_is_secondary_only"),
        True,
        "bootstrap secondary role",
    )
    _integer(
        analysis.get("bootstrap_replicates"),
        "bootstrap replicate count",
        minimum=1,
    )
    approval = _mapping(payload.get("approval"), "preregistration approval")
    _expect_keys(
        approval,
        {"decision", "protocol_sha256", "signed_receipt_sha256"},
        "preregistration approval",
    )
    _expect(approval.get("decision"), "approved", "preregistration decision")
    _sha256(approval.get("protocol_sha256"), "preregistration protocol")
    _sha256(approval.get("signed_receipt_sha256"), "preregistration receipt")
    return {
        "status": "valid_frozen_preregistration",
        "listener_count_n": _integer(
            power.get("listener_count_n"),
            "listener count",
            minimum=1,
        ),
        "maximum_assignments_per_fatigue_block": _integer(
            blindness.get("maximum_assignments_per_fatigue_block"),
            "fatigue block maximum",
            minimum=1,
        ),
        "maximum_consecutive_same_sentence_assignments": _integer(
            blindness.get("maximum_consecutive_same_sentence_assignments"),
            "same-sentence run maximum",
            minimum=1,
        ),
    }


def validate_pilot_nuisance_payload(
    payload: Mapping[str, Any],
    *,
    preregistration_sha256: str,
) -> Mapping[str, Any]:
    _expect_keys(payload, PILOT_KEYS, "pilot nuisance attestation")
    _require_no_tbd(payload, "pilot nuisance attestation")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r04_pilot_nuisance_attestation.v1",
        "pilot nuisance schema",
    )
    _expect(
        payload.get("status"),
        "approved_aggregate_nuisance_estimates",
        "pilot nuisance status",
    )
    _sha256(payload.get("pilot_protocol_sha256"), "pilot protocol")
    _expect(
        payload.get("r04_preregistration_sha256"),
        _sha256(preregistration_sha256, "preregistration SHA"),
        "pilot preregistration binding",
    )
    _expect(
        payload.get("pilot_blinded_to_system_and_synthesis_status"),
        True,
        "pilot blindness",
    )
    _expect(
        payload.get("pilot_data_excluded_from_confirmatory_evaluation"),
        True,
        "pilot/confirmatory separation",
    )
    _expect(payload.get("privacy_zone"), "A1_aggregate_only", "pilot privacy")
    for field in (
        "pilot_listener_count",
        "pilot_sentence_count",
        "pilot_rating_count",
    ):
        _integer(payload.get(field), field, minimum=1)
    for section_name, minimum, maximum in (
        ("variance_estimates", 0.0, None),
        ("intra_listener_correlation_estimates", 0.0, 1.0),
    ):
        section = _mapping(payload.get(section_name), section_name)
        _expect(
            set(section),
            {*MOS_DIMENSIONS, "cmos"},
            f"{section_name} dimensions",
        )
        for key, value in section.items():
            _number(
                value,
                f"{section_name}.{key}",
                minimum=minimum,
                maximum=maximum,
            )
    for field in (
        "individual_ratings_present",
        "listener_ids_present",
        "audio_or_candidate_ids_present",
        "source_labels_present",
    ):
        _expect(payload.get(field), False, f"pilot nuisance.{field}")
    approval = _mapping(payload.get("approval"), "pilot approval")
    _expect_keys(
        approval,
        {"decision", "statistician_role", "signed_receipt_sha256"},
        "pilot approval",
    )
    _expect(approval.get("decision"), "approved", "pilot approval decision")
    _sha256(approval.get("signed_receipt_sha256"), "pilot approval receipt")
    return {"status": "valid_pilot_nuisance_attestation"}


def validate_statistical_analysis_plan_payload(
    payload: Mapping[str, Any],
    *,
    preregistration_sha256: str,
    pilot_attestation_sha256: str,
    preregistered_listener_count: int,
) -> Mapping[str, Any]:
    _expect_keys(payload, SAP_KEYS, "statistical analysis plan")
    _require_no_tbd(payload, "statistical analysis plan")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r04_statistical_analysis_plan.v1",
        "SAP schema",
    )
    _expect(payload.get("status"), "approved_and_frozen", "SAP status")
    _expect(
        payload.get("frozen_before_confirmatory_audio_or_rating_access"),
        True,
        "SAP ordering",
    )
    _expect(
        payload.get("r04_preregistration_sha256"),
        _sha256(preregistration_sha256, "preregistration SHA"),
        "SAP preregistration binding",
    )
    _expect(
        payload.get("pilot_nuisance_attestation_sha256"),
        _sha256(pilot_attestation_sha256, "pilot attestation SHA"),
        "SAP pilot binding",
    )
    sampling = _mapping(payload.get("sampling"), "SAP sampling")
    _expect_keys(
        sampling,
        {
            "target_power",
            "two_sided_alpha",
            "listener_count_n",
            "exact_sample_size_formula_or_software",
            "pilot_variance_binding",
            "pilot_intra_listener_correlation_binding",
            "rater_population",
            "rater_inclusion_and_exclusion",
            "rater_training_and_quality_control",
            "adjudication_policy",
        },
        "SAP sampling",
    )
    _expect(sampling.get("target_power"), 0.8, "SAP target power")
    _expect(sampling.get("two_sided_alpha"), 0.05, "SAP two-sided alpha")
    listener_count = _integer(
        sampling.get("listener_count_n"),
        "SAP listener N",
        minimum=1,
    )
    _expect(
        listener_count,
        preregistered_listener_count,
        "SAP/preregistration listener N",
    )
    primary = _mapping(payload.get("primary_analysis"), "SAP primary analysis")
    _expect_keys(
        primary,
        {
            "model_family",
            "listener_random_effect_required",
            "sentence_random_effect_required",
            "exact_estimand",
            "ordinal_link_function",
            "fixed_effects_formula",
            "random_effects_structure",
            "confidence_interval_definition",
            "missing_data_policy",
            "model_convergence_policy",
        },
        "SAP primary analysis",
    )
    _expect(
        primary.get("model_family"),
        "ordinal_mixed_effects",
        "SAP primary family",
    )
    _expect(
        primary.get("listener_random_effect_required"),
        True,
        "SAP listener random effect",
    )
    _expect(
        primary.get("sentence_random_effect_required"),
        True,
        "SAP sentence random effect",
    )
    bootstrap = _mapping(payload.get("secondary_bootstrap"), "SAP bootstrap")
    _expect_keys(
        bootstrap,
        {
            "secondary_only",
            "resampling_unit",
            "replicates",
            "seed_commitment",
            "confidence_interval_method",
        },
        "SAP bootstrap",
    )
    _expect(bootstrap.get("secondary_only"), True, "SAP bootstrap role")
    _integer(bootstrap.get("replicates"), "SAP bootstrap replicates", minimum=1)
    decision = _mapping(
        payload.get("confirmatory_decision"),
        "SAP confirmatory decision",
    )
    _expect_keys(
        decision,
        {
            "multiplicity_strategy",
            "superiority_margin",
            "superiority_comparator",
            "mos_dimension_decision_rule",
            "cmos_decision_rule",
        },
        "SAP confirmatory decision",
    )
    approval = _mapping(payload.get("approval"), "SAP approval")
    _expect_keys(
        approval,
        {
            "decision",
            "statistician_role",
            "signed_receipt_sha256",
            "immutable_archive_receipt_sha256",
        },
        "SAP approval",
    )
    _expect(approval.get("decision"), "approved", "SAP approval decision")
    _sha256(approval.get("signed_receipt_sha256"), "SAP signed receipt")
    _sha256(
        approval.get("immutable_archive_receipt_sha256"),
        "SAP archive receipt",
    )
    return {
        "status": "valid_frozen_statistical_analysis_plan",
        "listener_count_n": listener_count,
    }


def validate_listener_attestation_payload(
    payload: Mapping[str, Any],
    *,
    human_approval_sha256: str,
    preregistration_sha256: str,
    sap_sha256: str,
    required_listener_count: int,
) -> Mapping[str, Any]:
    _expect_keys(payload, LISTENER_ATTESTATION_KEYS, "listener attestations")
    _require_no_tbd(payload, "listener attestations")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r04_listener_attestation_manifest.v1",
        "listener attestation schema",
    )
    _expect(
        payload.get("status"),
        "approved_for_confirmatory_rating",
        "listener attestation status",
    )
    _expect(
        payload.get("human_research_approval_sha256"),
        _sha256(human_approval_sha256, "human approval SHA"),
        "listener human-approval binding",
    )
    _expect(
        payload.get("r04_preregistration_sha256"),
        _sha256(preregistration_sha256, "preregistration SHA"),
        "listener preregistration binding",
    )
    _expect(
        payload.get("statistical_analysis_plan_sha256"),
        _sha256(sap_sha256, "SAP SHA"),
        "listener SAP binding",
    )
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "listener privacy zone",
    )
    _expect(
        payload.get("direct_identifiers_present"),
        False,
        "listener direct identifiers",
    )
    _expect(payload.get("free_text_present"), False, "listener free text")
    forbidden = _collect_keys(payload).intersection(
        {
            "name",
            "email",
            "phone",
            "address",
            "legal_name",
            "raw_listener_id",
            "free_text",
            "comment",
        }
    )
    if forbidden:
        raise R04ContractError(
            f"listener attestations contain PII/free-text: {sorted(forbidden)}"
        )
    rows = _list(payload.get("attestations"), "listener attestation rows")
    if len(rows) != required_listener_count:
        raise R04ContractError("listener attestation count must equal preregistered N")
    listener_ids: set[str] = set()
    for index, raw_row in enumerate(rows):
        row = _mapping(raw_row, f"listener attestation {index}")
        _expect_keys(row, LISTENER_ROW_KEYS, f"listener attestation {index}")
        listener_id = _sha256(
            row.get("listener_id_sha256"),
            f"listener attestation {index} ID",
        )
        if listener_id in listener_ids:
            raise R04ContractError("duplicate listener attestation ID")
        listener_ids.add(listener_id)
        for field in (
            "consent_receipt_sha256",
            "eligibility_receipt_sha256",
            "training_completion_receipt_sha256",
            "listening_environment_receipt_sha256",
            "headphone_check_receipt_sha256",
        ):
            _sha256(row.get(field), f"listener attestation {index}.{field}")
        _expect(
            row.get("source_blinding_acknowledged"),
            True,
            "listener source blinding acknowledgement",
        )
        _expect(
            row.get("attestation_status"),
            "eligible_consented_and_ready",
            "listener attestation row status",
        )
    approval = _mapping(payload.get("approval"), "listener approval")
    _expect_keys(
        approval,
        {"decision", "signed_receipt_sha256"},
        "listener approval",
    )
    _expect(approval.get("decision"), "approved", "listener approval decision")
    _sha256(approval.get("signed_receipt_sha256"), "listener approval receipt")
    return {
        "status": "valid_listener_attestations",
        "listener_count": len(listener_ids),
        "listener_ids": tuple(sorted(listener_ids)),
    }


def _snapshot_from_r03(value: object, name: str) -> FileSnapshot:
    try:
        return FileSnapshot(
            relative_path=_string(
                getattr(value, "relative_path"),
                f"{name}.relative_path",
            ),
            sha256=_sha256(getattr(value, "sha256"), f"{name}.sha256"),
            size=_integer(getattr(value, "size"), f"{name}.size", minimum=0),
            device=_integer(getattr(value, "device"), f"{name}.device"),
            inode=_integer(getattr(value, "inode"), f"{name}.inode"),
            mtime_ns=_integer(
                getattr(value, "mtime_ns"),
                f"{name}.mtime_ns",
                minimum=0,
            ),
        )
    except AttributeError as exc:
        raise R04ContractError(f"{name} snapshot interface drift") from exc


def _sync_r03_audit(access: AccessAudit, delegate: object) -> None:
    mappings = {
        "predecessor_static_source_read_count": "source_pin_read_count",
        "r00_deep_validation_count": "r00_deep_validation_count",
        "r00_public_or_aggregate_evidence_read_count": (
            "r00_public_or_aggregate_evidence_read_count"
        ),
        "r00_raw_audio_read_count": "r00_raw_audio_read_count",
        "r00_row_level_read_count": "r00_row_level_read_count",
        "r01_completion_path_resolution_count": (
            "r01_completion_path_resolution_count"
        ),
        "r01_completion_read_attempt_count": "r01_completion_read_attempt_count",
        "r01_completion_read_count": "r01_completion_read_count",
        "r01_manifest_read_count": "r01_manifest_read_count",
        "r01_audio_read_count": "r01_audio_read_count",
        "r02_completion_path_resolution_count": (
            "r02_completion_path_resolution_count"
        ),
        "r02_completion_read_attempt_count": "r02_completion_read_attempt_count",
        "r02_completion_read_count": "r02_completion_read_count",
        "r02_gate_report_read_count": "r02_report_read_count",
        "r02_quarantine_row_read_count": "selection_quarantine_row_read_count",
        "audio_read_count": "audio_read_count",
        "audio_write_count": "audio_write_count",
        "model_access_count": "model_access_count",
        "gpu_access_count": "gpu_access_count",
    }
    for local_name, delegate_name in mappings.items():
        value = getattr(delegate, delegate_name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise R04ContractError(f"R03 audit interface drift: {delegate_name}")
        setattr(access, local_name, value)
    if access.r02_quarantine_row_read_count:
        raise R04ContractError(
            "R04 predecessor validation opened quarantined rows",
            stage="r02_completion",
            audit=access,
        )
    if access.r01_audio_read_count or access.audio_read_count:
        raise R04ContractError(
            "predecessor validation opened audio",
            stage="predecessor_audio_boundary",
            audit=access,
        )


def _delegate_failure(
    exc: Exception,
    *,
    fallback_stage: str,
    delegate_audit: object,
    audit: AccessAudit,
) -> R04ContractError:
    _sync_r03_audit(audit, delegate_audit)
    return R04ContractError(
        str(exc),
        stage=_string(getattr(exc, "stage", fallback_stage), "delegate stage"),
        audit=audit,
    )


def _r03_contract_module(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> ModuleType:
    predecessor = _mapping(config.get("predecessor_r03"), "predecessor_r03")
    module = _load_module(
        workspace_root=workspace_root,
        relative=_relative_string(
            predecessor.get("contract_path"),
            "R03 contract path",
        ),
        module_name="_voice_coach_r04_r03_contract",
    )
    audit.r03_contract_load_count += 1
    return module


def deep_validate_r00_r01_r02(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    r03_module: ModuleType,
    audit: AccessAudit,
) -> tuple[Mapping[str, Any], object, object, object]:
    """Delegate the immutable R00 -> R01 -> R02 order to pinned R03 code."""

    predecessor = _mapping(config.get("predecessor_r03"), "predecessor_r03")
    r03_config_relative = _relative_string(
        predecessor.get("config_path"),
        "R03 config path",
    )
    r03_config_path = workspace_root / r03_config_relative
    try:
        r03_config = r03_module.load_config(r03_config_path)
        delegate_audit = r03_module.AccessAudit()
        r03_static = r03_module.validate_static_config(
            workspace_root=workspace_root,
            config_path=r03_config_path,
            config=r03_config,
            audit=delegate_audit,
        )
        r00 = r03_module.deep_validate_r00(
            workspace_root=workspace_root,
            config=r03_config,
            audit=delegate_audit,
        )
    except Exception as exc:
        delegated = locals().get("delegate_audit")
        if delegated is None:
            raise R04ContractError(
                f"R03 predecessor interface failed: {exc}",
                stage="predecessor_static",
                audit=audit,
            ) from exc
        raise _delegate_failure(
            exc,
            fallback_stage="r00",
            delegate_audit=delegated,
            audit=audit,
        ) from exc
    _sync_r03_audit(audit, delegate_audit)
    _expect(
        getattr(r03_static, "config_sha256", None),
        predecessor.get("config_sha256"),
        "delegated R03 config SHA",
    )
    r00_snapshot = _snapshot_from_r03(
        getattr(r00, "report_snapshot"),
        "delegated R00 report",
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(r00_snapshot,),
        audit=audit,
        stage="r00_report",
    )

    contract_loader = getattr(r03_module, "_r02_contract_module", None)
    if not callable(contract_loader):
        raise R04ContractError(
            "pinned R03 contract lacks predecessor contract loader",
            stage="r01_completion",
            audit=audit,
        )
    try:
        r02_module = contract_loader(
            workspace_root=workspace_root,
            config=r03_config,
            audit=delegate_audit,
        )
        r01 = r03_module.deep_validate_r01_completion(
            workspace_root=workspace_root,
            config=r03_config,
            r02_module=r02_module,
            audit=delegate_audit,
        )
    except Exception as exc:
        raise _delegate_failure(
            exc,
            fallback_stage="r01_completion",
            delegate_audit=delegate_audit,
            audit=audit,
        ) from exc
    _sync_r03_audit(audit, delegate_audit)
    r01_snapshots = tuple(
        _snapshot_from_r03(value, f"delegated R01 snapshot {index}")
        for index, value in enumerate(getattr(r01, "snapshots"))
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=r01_snapshots,
        audit=audit,
        stage="r01_completion",
    )

    try:
        r02 = r03_module.deep_validate_r02_completion(
            workspace_root=workspace_root,
            config=r03_config,
            r02_module=r02_module,
            r01=r01,
            audit=delegate_audit,
        )
    except Exception as exc:
        raise _delegate_failure(
            exc,
            fallback_stage="r02_completion",
            delegate_audit=delegate_audit,
            audit=audit,
        ) from exc
    _sync_r03_audit(audit, delegate_audit)
    r02_snapshots = tuple(
        _snapshot_from_r03(value, f"delegated R02 snapshot {index}")
        for index, value in enumerate(getattr(r02, "snapshots"))
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=r02_snapshots,
        audit=audit,
        stage="r02_completion",
    )
    if audit.r02_failure_rescue_count:
        raise R04ContractError(
            "R02 hard-gate failures cannot be rescued by R04",
            stage="r02_completion",
            audit=audit,
        )
    return _mapping(r03_config, "R03 config"), r00, r01, r02


def _validate_r03_trace(payload: Mapping[str, Any]) -> None:
    forbidden = _collect_keys(payload).intersection(
        {
            "r04_label",
            "r04_score",
            "mos",
            "cmos",
            "learner_outcome",
            "learning_gain",
            "retention",
            "source_kind",
            "human_or_tts",
        }
    )
    if forbidden:
        raise R04ContractError(
            f"R03 selection trace contains prohibited labels: {sorted(forbidden)}"
        )
    for text in _collect_strings(payload):
        lowered = text.casefold()
        if "human_recording" in lowered or "cosyvoice_tts" in lowered:
            raise R04ContractError("R03 selection trace leaks source labels")


def deep_validate_r03_completion(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    r03_module: ModuleType,
    r01: object,
    r02: object,
    audit: AccessAudit,
) -> R03CompletionEvidence:
    """Validate a future R03 commit, bank, lineage, trace, aggregate, registry."""

    predecessor = _mapping(config.get("predecessor_r03"), "predecessor_r03")
    expected_commit_sha = _string(
        predecessor.get("completion_commit_sha256"),
        "R03 completion commit SHA",
    )
    if expected_commit_sha == "TBD_R03_COMPLETION":
        raise R04ContractError(
            "R03 completion commit is TBD_R03_COMPLETION",
            stage="r03_completion",
            audit=audit,
        )
    _sha256(expected_commit_sha, "R03 completion commit SHA")
    commit_schema = _string(
        predecessor.get("completion_commit_schema_version"),
        "R03 completion schema",
    )
    registry_schema = _string(
        predecessor.get("registry_schema_version"),
        "R03 registry schema",
    )
    if commit_schema.startswith("TBD_") or registry_schema.startswith("TBD_"):
        raise R04ContractError(
            "R03 completion/registry schema is TBD_R03_COMPLETION",
            stage="r03_completion",
            audit=audit,
        )
    audit.r03_completion_path_resolution_count += 1
    audit.r03_completion_read_attempt_count += 1
    try:
        commit, commit_snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                predecessor.get("completion_commit_path"),
                "R03 completion path",
            ),
            expected_sha256=expected_commit_sha,
            name="R03 completion commit",
        )
    except R04ContractError as exc:
        raise R04ContractError(
            f"R03 completion commit unavailable: {exc}",
            stage="r03_completion",
            audit=audit,
        ) from exc
    audit.r03_completion_read_count += 1
    _expect_keys(commit, R03_COMPLETION_KEYS, "R03 completion commit")
    _expect(commit.get("schema_version"), commit_schema, "R03 commit schema")
    _expect(commit.get("status"), "completed", "R03 commit status")
    _expect(
        commit.get("config_sha256"),
        predecessor.get("config_sha256"),
        "R03 config binding",
    )
    _expect(
        commit.get("r01_completion_commit_sha256"),
        getattr(r01, "commit_sha256"),
        "R03/R01 completion binding",
    )
    _expect(
        commit.get("r02_completion_commit_sha256"),
        getattr(r02, "commit_sha256"),
        "R03/R02 completion binding",
    )
    _expect(
        commit.get("r02_gate_report_sha256"),
        getattr(r02, "report_sha256"),
        "R03/R02 report binding",
    )
    _expect(commit.get("sentence_count"), 20, "R03 sentence count")
    selected_count = _integer(
        commit.get("selected_candidate_count"),
        "R03 selected candidate count",
        minimum=60,
        maximum=100,
    )
    _expect(commit.get("r04_status"), "not_run", "R03 R04 status")
    _expect(commit.get("publication_allowed"), False, "R03 publication")
    for field in (
        "preregistration_sha256",
        "bank_manifest_sha256",
        "selection_trace_sha256",
        "aggregate_report_sha256",
        "registry_sha256",
    ):
        _sha256(commit.get(field), f"R03 commit.{field}")

    bank, bank_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            predecessor.get("bank_manifest_path"),
            "R03 bank path",
        ),
        expected_sha256=_string(
            commit.get("bank_manifest_sha256"),
            "R03 bank SHA",
        ),
        name="R03 bank manifest",
    )
    audit.r03_bank_manifest_read_count += 1
    trace, trace_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            predecessor.get("selection_trace_path"),
            "R03 selection trace path",
        ),
        expected_sha256=_string(
            commit.get("selection_trace_sha256"),
            "R03 trace SHA",
        ),
        name="R03 selection trace",
    )
    audit.r03_selection_trace_read_count += 1
    aggregate, aggregate_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            predecessor.get("aggregate_report_path"),
            "R03 aggregate path",
        ),
        expected_sha256=_string(
            commit.get("aggregate_report_sha256"),
            "R03 aggregate SHA",
        ),
        name="R03 aggregate report",
    )
    audit.r03_aggregate_read_count += 1
    registry, registry_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            predecessor.get("registry_path"),
            "R03 registry path",
        ),
        expected_sha256=_string(
            commit.get("registry_sha256"),
            "R03 registry SHA",
        ),
        name="R03 registry",
    )
    audit.r03_registry_read_count += 1

    bank_validator = getattr(r03_module, "validate_bank_manifest_payload", None)
    lineage_validator = getattr(r03_module, "validate_bank_lineage_bindings", None)
    aggregate_validator = getattr(
        r03_module,
        "validate_a1_aggregate_payload",
        None,
    )
    if not callable(bank_validator):
        raise R04ContractError(
            "pinned R03 contract lacks bank validator",
            stage="r03_completion",
            audit=audit,
        )
    if not callable(lineage_validator):
        raise R04ContractError(
            "pinned R03 contract lacks lineage validator",
            stage="r03_completion",
            audit=audit,
        )
    if not callable(aggregate_validator):
        raise R04ContractError(
            "pinned R03 contract lacks A1 aggregate validator",
            stage="r03_completion",
            audit=audit,
        )
    try:
        bank_summary = bank_validator(bank)
        r02_report, r02_report_snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                _mapping(config.get("predecessor_r02"), "predecessor_r02").get(
                    "gate_report_path"
                ),
                "R02 gate report path",
            ),
            expected_sha256=_string(
                getattr(r02, "report_sha256"),
                "R02 report SHA",
            ),
            name="R02 gate report for R03 lineage",
        )
        audit.r02_gate_report_read_count += 1
        lineage_summary = lineage_validator(
            bank,
            r01_candidate_rows=getattr(
                getattr(r01, "delegated_r01_evidence"),
                "candidate_rows",
            ),
            r02_gate_report=r02_report,
        )
        audit.r03_bank_lineage_validation_count += 1
        aggregate_summary = aggregate_validator(aggregate)
    except Exception as exc:
        raise R04ContractError(
            f"R03 bank deep validation failed: {exc}",
            stage="r03_completion",
            audit=audit,
        ) from exc
    _expect(
        bank_summary.get("selected_candidate_count"),
        selected_count,
        "R03 bank/commit selected count",
    )
    _expect(
        lineage_summary.get("immutable_lineage_binding_count"),
        selected_count,
        "R03 bank lineage count",
    )
    _expect(
        aggregate_summary.get("selected_candidate_count"),
        selected_count,
        "R03 aggregate/commit selected count",
    )
    _expect(bank.get("r01_commit_sha256"), getattr(r01, "commit_sha256"), "bank R01")
    _expect(bank.get("r02_commit_sha256"), getattr(r02, "commit_sha256"), "bank R02")
    _expect(
        bank.get("r03_config_sha256"),
        predecessor.get("config_sha256"),
        "bank R03 config",
    )
    _expect(
        bank.get("r03_preregistration_sha256"),
        commit.get("preregistration_sha256"),
        "bank R03 preregistration",
    )
    _validate_r03_trace(trace)
    _expect_keys(registry, R03_REGISTRY_KEYS, "R03 registry")
    _expect(registry.get("schema_version"), registry_schema, "R03 registry schema")
    _expect(registry.get("status"), "completed", "R03 registry status")
    for field in (
        "run_id",
        "config_sha256",
        "r01_completion_commit_sha256",
        "r02_completion_commit_sha256",
        "r02_gate_report_sha256",
        "preregistration_sha256",
        "bank_manifest_sha256",
        "selection_trace_sha256",
        "aggregate_report_sha256",
        "sentence_count",
        "selected_candidate_count",
        "publication_allowed",
    ):
        _expect(registry.get(field), commit.get(field), f"R03 registry.{field}")
    _expect(registry.get("failure"), None, "R03 registry failure")
    snapshots = (
        commit_snapshot,
        bank_snapshot,
        trace_snapshot,
        aggregate_snapshot,
        registry_snapshot,
        r02_report_snapshot,
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=snapshots,
        audit=audit,
        stage="r03_completion",
    )
    return R03CompletionEvidence(
        commit_sha256=commit_snapshot.sha256,
        bank_manifest_sha256=bank_snapshot.sha256,
        selected_candidate_count=selected_count,
        snapshots=snapshots,
    )


def _read_approved_artifact(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    section_name: str,
    stage: str,
    path_counter: str,
    attempt_counter: str,
    read_counter: str,
    audit: AccessAudit,
) -> ApprovedEvidence:
    section = _mapping(config.get(section_name), section_name)
    expected_sha = _string(
        section.get("approved_artifact_sha256"),
        f"{section_name} approved SHA",
    )
    if expected_sha.startswith("TBD_"):
        raise R04ContractError(
            f"{section_name} is {expected_sha}",
            stage=stage,
            audit=audit,
        )
    _sha256(expected_sha, f"{section_name} approved SHA")
    setattr(audit, path_counter, getattr(audit, path_counter) + 1)
    setattr(audit, attempt_counter, getattr(audit, attempt_counter) + 1)
    try:
        payload, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("approved_artifact_path"),
                f"{section_name} approved path",
            ),
            expected_sha256=expected_sha,
            name=section_name,
        )
    except R04ContractError as exc:
        raise R04ContractError(
            f"{section_name} unavailable: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    setattr(audit, read_counter, getattr(audit, read_counter) + 1)
    return ApprovedEvidence(
        sha256=snapshot.sha256,
        snapshot=snapshot,
        payload=payload,
    )


def ensure_future_output_targets_absent(
    *,
    workspace_root: Path,
    outputs: Mapping[str, Any],
) -> None:
    """Validate future no-overwrite, path, symlink, and atomic-write policy."""

    _expect(outputs.get("no_overwrite"), True, "future no-overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "future atomic publish")
    _expect(
        outputs.get("write_enabled_in_this_package"),
        False,
        "static write boundary",
    )
    schema_fields = {"randomization_schema_path", "rating_schema_path"}
    targets: set[str] = set()
    for field, value in outputs.items():
        if not field.endswith("_path") or field in schema_fields:
            continue
        relative = _relative_string(value, f"outputs.{field}")
        if relative in targets:
            raise R04ContractError("duplicate future output target")
        targets.add(relative)
        target = _reject_symlink_chain(
            workspace_root,
            relative,
            f"outputs.{field}",
            require_leaf=False,
        )
        if target.exists() or target.is_symlink():
            raise R04ContractError(f"no-overwrite target already exists: {relative}")


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Run read-only predecessor and governance gates in immutable order."""

    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
        audit=audit,
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=static.source_snapshots,
        audit=audit,
        stage="static_source",
    )
    r03_module = _r03_contract_module(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    r03_config, r00, r01, r02 = deep_validate_r00_r01_r02(
        workspace_root=workspace_root,
        config=config,
        r03_module=r03_module,
        audit=audit,
    )
    del r03_config
    r03 = deep_validate_r03_completion(
        workspace_root=workspace_root,
        config=config,
        r03_module=r03_module,
        r01=r01,
        r02=r02,
        audit=audit,
    )

    human = _read_approved_artifact(
        workspace_root=workspace_root,
        config=config,
        section_name="human_research_approval",
        stage="human_research_approval",
        path_counter="human_approval_path_resolution_count",
        attempt_counter="human_approval_read_attempt_count",
        read_counter="human_approval_read_count",
        audit=audit,
    )
    try:
        validate_human_research_approval_payload(human.payload)
    except R04ContractError as exc:
        raise R04ContractError(
            str(exc),
            stage="human_research_approval",
            audit=audit,
        ) from exc
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(human.snapshot,),
        audit=audit,
        stage="human_research_approval",
    )

    prereg = _read_approved_artifact(
        workspace_root=workspace_root,
        config=config,
        section_name="preregistration",
        stage="preregistration",
        path_counter="preregistration_path_resolution_count",
        attempt_counter="preregistration_read_attempt_count",
        read_counter="preregistration_read_count",
        audit=audit,
    )
    try:
        prereg_summary = validate_preregistration_payload(
            prereg.payload,
            r03_completion_commit_sha256=r03.commit_sha256,
            r04_config_sha256=static.config_sha256,
        )
    except R04ContractError as exc:
        raise R04ContractError(
            str(exc),
            stage="preregistration",
            audit=audit,
        ) from exc
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(prereg.snapshot,),
        audit=audit,
        stage="preregistration",
    )

    pilot = _read_approved_artifact(
        workspace_root=workspace_root,
        config=config,
        section_name="pilot_nuisance_attestation",
        stage="pilot_nuisance_attestation",
        path_counter="pilot_attestation_path_resolution_count",
        attempt_counter="pilot_attestation_read_attempt_count",
        read_counter="pilot_attestation_read_count",
        audit=audit,
    )
    try:
        validate_pilot_nuisance_payload(
            pilot.payload,
            preregistration_sha256=prereg.sha256,
        )
    except R04ContractError as exc:
        raise R04ContractError(
            str(exc),
            stage="pilot_nuisance_attestation",
            audit=audit,
        ) from exc
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(pilot.snapshot,),
        audit=audit,
        stage="pilot_nuisance_attestation",
    )

    sap = _read_approved_artifact(
        workspace_root=workspace_root,
        config=config,
        section_name="statistical_analysis_plan",
        stage="statistical_analysis_plan",
        path_counter="sap_path_resolution_count",
        attempt_counter="sap_read_attempt_count",
        read_counter="sap_read_count",
        audit=audit,
    )
    try:
        sap_summary = validate_statistical_analysis_plan_payload(
            sap.payload,
            preregistration_sha256=prereg.sha256,
            pilot_attestation_sha256=pilot.sha256,
            preregistered_listener_count=_integer(
                prereg_summary.get("listener_count_n"),
                "preregistered listener count",
                minimum=1,
            ),
        )
    except R04ContractError as exc:
        raise R04ContractError(
            str(exc),
            stage="statistical_analysis_plan",
            audit=audit,
        ) from exc
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(sap.snapshot,),
        audit=audit,
        stage="statistical_analysis_plan",
    )

    listener = _read_approved_artifact(
        workspace_root=workspace_root,
        config=config,
        section_name="listener_attestations",
        stage="listener_attestations",
        path_counter="listener_attestation_path_resolution_count",
        attempt_counter="listener_attestation_read_attempt_count",
        read_counter="listener_attestation_read_count",
        audit=audit,
    )
    try:
        validate_listener_attestation_payload(
            listener.payload,
            human_approval_sha256=human.sha256,
            preregistration_sha256=prereg.sha256,
            sap_sha256=sap.sha256,
            required_listener_count=_integer(
                sap_summary.get("listener_count_n"),
                "SAP listener count",
                minimum=1,
            ),
        )
    except R04ContractError as exc:
        raise R04ContractError(
            str(exc),
            stage="listener_attestations",
            audit=audit,
        ) from exc
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(listener.snapshot,),
        audit=audit,
        stage="listener_attestations",
    )

    # Deliberately stop before resolving any D1 or output target.
    return PreflightResult(
        status="ready_for_separate_external_evaluation_executor",
        config_sha256=static.config_sha256,
        r00_report_sha256=_string(
            getattr(r00, "report_sha256"),
            "R00 report SHA",
        ),
        r01_commit_sha256=_string(
            getattr(r01, "commit_sha256"),
            "R01 commit SHA",
        ),
        r02_commit_sha256=_string(
            getattr(r02, "commit_sha256"),
            "R02 commit SHA",
        ),
        r03_commit_sha256=r03.commit_sha256,
        human_approval_sha256=human.sha256,
        preregistration_sha256=prereg.sha256,
        pilot_attestation_sha256=pilot.sha256,
        sap_sha256=sap.sha256,
        listener_attestation_sha256=listener.sha256,
        audit=audit.as_dict(),
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
        "actual_listener_or_audio_or_rating_execution": False,
        "actual_model_or_gpu_execution": False,
        "actual_mos_cmos_or_statistics_computation": False,
        "actual_superiority_or_completion_decision": False,
        "artifact_created": False,
        "r04_completion_claim_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: R04ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "actual_listener_or_audio_or_rating_execution": False,
        "actual_model_or_gpu_execution": False,
        "actual_mos_cmos_or_statistics_computation": False,
        "actual_superiority_or_completion_decision": False,
        "artifact_created": False,
        "r04_completion_claim_allowed": False,
        "publication_allowed": False,
    }
