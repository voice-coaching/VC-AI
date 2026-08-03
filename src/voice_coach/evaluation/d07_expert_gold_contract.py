"""Fail-closed D07 independent expert gold static-readiness contract.

The package validates C1/A1 static policy and, in full-preflight mode, only
the authorization chain needed before a separate calibration executor may
resolve D1/R1/R0 data:

1. actual H00 public approval and restricted-document attestation;
2. D07-specific institutional approval;
3. exactly two independent primary experts plus one disagreement adjudicator;
4. a frozen preregistration with every design choice resolved.

G04 remains a distinct 240-item alignment-review stage.  Static validation
checks only its A1 0/240 progress aggregate and never resolves the restricted
UI state.  Pure payload validators define future D07 item/rating, calibration,
G04 completion, locked one-shot authorization, locked report, and completion
contracts without executing human review or opening protected data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from types import ModuleType
from typing import Any

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
STATIC_STATUS = "static_readiness_only"
TBD_H00_APPROVAL = "TBD_H00_APPROVAL"
TBD_H00_ATTESTATION = "TBD_H00_ATTESTATION"
TBD_EXTERNAL_ATTESTATION = "TBD_EXTERNAL_ATTESTATION"
TBD_PREREGISTRATION = "TBD_PREREGISTRATION"
TBD_ATTESTATION = "TBD_ATTESTATION"
TBD_G04_COMPLETION = "TBD_G04_COMPLETION"
TBD_EXTERNAL_AUTHORIZATION = "TBD_EXTERNAL_AUTHORIZATION"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
EMAIL_RE = re.compile(r"(?i)[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)")

ITEM_SCHEMA_VERSION = "voice_coach.d07_gold_item_manifest.v1"
RATING_SCHEMA_VERSION = "voice_coach.d07_expert_rating_manifest.v1"
CALIBRATION_SCHEMA_VERSION = "voice_coach.d07_calibration_agreement_report.v1"
LOCKED_REPORT_SCHEMA_VERSION = "voice_coach.d07_locked_evaluation_report.v1"
COMPLETION_SCHEMA_VERSION = "voice_coach.d07_gold_completion_attestation.v1"
INSTITUTIONAL_APPROVAL_SCHEMA_VERSION = (
    "voice_coach.d07_institutional_expert_approval.v1"
)
REVIEWER_ATTESTATION_SCHEMA_VERSION = "voice_coach.d07_reviewer_attestation_manifest.v1"
PREREGISTRATION_SCHEMA_VERSION = "voice_coach.d07_preregistration.v1"
LOCKED_AUTHORIZATION_SCHEMA_VERSION = "voice_coach.d07_locked_access_authorization.v1"
G04_PROGRESS_SCHEMA_VERSION = "voice_coach.alignment_review_report.v1"
G04_COMPLETION_SCHEMA_VERSION = "voice_coach.g04_alignment_completion_attestation.v1"

CALIBRATION_COUNT = 800
MINIMUM_TOTAL_COUNT = 1000
MAXIMUM_TOTAL_COUNT = 2000
MINIMUM_LOCKED_COUNT = 200
MAXIMUM_LOCKED_COUNT = 1200
PRIMARY_REVIEWER_COUNT = 2
ADJUDICATOR_COUNT = 1
SCALAR_AGREEMENT_THRESHOLD = Decimal("0.70")
TAXONOMY_AGREEMENT_THRESHOLD = Decimal("0.65")
STRATIFICATION_AXES = (
    "l1",
    "gender",
    "proficiency",
    "utterance_length",
    "target_phone",
    "existing_error",
)
SENSITIVITY_BASES = (
    "primary_reviewer_1_original",
    "primary_reviewer_2_original",
    "adjudicated_gold",
)
PRIMARY_ROLES = ("primary_reviewer_1", "primary_reviewer_2")
REVIEWER_ROLES = (*PRIMARY_ROLES, "adjudicator")
PHONE_REALIZATIONS = ("correct", "substitution", "deletion", "uncertain")
BOUNDARY_VALUES = ("yes", "no", "uncertain")
ERROR_TYPES = (
    "initial_consonant",
    "final_consonant",
    "vowel_error",
    "tensification",
    "other",
    "repetition",
    "nasalization",
    "aspiration",
    "liquidization",
    "liaison_rule",
    "boundary_intonation_error",
    "n_insertion",
    "palatalization",
    "stress_error",
    "interjection",
    "vowel_insertion",
    "phoneme_deletion",
    "uncertain",
)

EXPECTED_EXECUTION_ORDER = (
    "validate_d07_static_sources_schemas_templates_and_g04_aggregate_separation",
    "require_exact_h00_approval_and_attestation_sha_before_path_resolution",
    "deep_validate_actual_h00_approval_without_restricted_document_resolution",
    "validate_d07_specific_institutional_approval",
    "validate_two_primary_reviewers_and_one_adjudicator_attestations",
    "validate_frozen_d07_preregistration_without_tbd",
    "stop_before_d1_item_r1_locator_r0_audio_rating_or_output_resolution",
    "separate_executor_calibration_800_independent_ratings_only",
    "validate_calibration_agreement_gate_or_require_retraining_and_separate_resample",
    "validate_g04_completion_as_distinct_240_item_stage",
    "validate_one_shot_locked_expert_access_authorization",
    "separate_executor_locked_test_up_to_1200_without_rubric_or_primary_change",
    "validate_two_original_and_adjudicated_sensitivity_reports",
    "publish_aggregate_completion_attestation_only_after_independent_validation",
)

SOURCE_PATHS = (
    "configs/expert_evaluation/d07_calibration_agreement_report_v1.schema.json",
    "configs/expert_evaluation/d07_expert_rating_manifest_v1.schema.json",
    "configs/expert_evaluation/d07_gold_completion_attestation_v1.schema.json",
    "configs/expert_evaluation/d07_gold_item_manifest_v1.schema.json",
    "configs/expert_evaluation/d07_institutional_expert_approval_v1.template.json",
    "configs/expert_evaluation/d07_locked_access_authorization_v1.template.json",
    "configs/expert_evaluation/d07_locked_evaluation_report_v1.schema.json",
    "configs/expert_evaluation/d07_preregistration_v1.template.json",
    "configs/expert_evaluation/d07_reviewer_attestation_v1.template.json",
    "configs/coaching/korean_error_advice_ontology_v1.json",
    "configs/experiments/h00_human_research_approval_v1.yaml",
    "configs/experiments/wavlm_s108_no_locked_dry_run_v1.yaml",
    "data/processed/aihub71490/manifests/aihub71490_manual_alignment_review_progress.json",
    "docs/AIHUB71490_MANUAL_ALIGNMENT_AUDIT_KO.md",
    "docs/DATA_PRIVACY_BOUNDARY_KO.md",
    "docs/EXTERNAL_EVALUATION_EXECUTION_PACK_KO.md",
    "docs/LOCAL_ALIGNMENT_REVIEW_UI_KO.md",
    "docs/S2_S200_DATA_CARD_KO.md",
    "docs/S2_S201_WHISPER_CONTENT_DATA_CARD_KO.md",
    "docs/S2_S202_INDEPENDENT_AUDITOR_DATA_MODEL_CARD_KO.md",
    "docs/S2_S203_EXPERT_HOLDOUT_EVALUATION_CARD_KO.md",
    "scripts/prepare_d07_independent_expert_gold.py",
    "src/voice_coach/evaluation/d07_expert_gold_contract.py",
    "src/voice_coach/human_research/h00_approval_contract.py",
    "src/voice_coach/training/wavlm_s108_dry_run.py",
)

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "g04_separation",
    "predecessor_h00",
    "institutional_expert_approval",
    "reviewer_attestations",
    "preregistration",
    "sampling_contract",
    "rating_contract",
    "calibration_agreement",
    "locked_test_contract",
    "privacy_and_non_execution",
    "execution_order",
    "outputs",
}

FORBIDDEN_RECURSIVE_KEYS = {
    "audio_path",
    "audio_url",
    "audio_bytes",
    "raw_audio",
    "file_stem",
    "filename",
    "file_name",
    "raw_item_id",
    "raw_speaker_id",
    "reviewer_name",
    "speaker_name",
    "name",
    "email",
    "phone",
    "phone_number",
    "institution_name",
    "organization_name",
    "address",
    "model_output",
    "model_score",
    "model_prediction",
    "checkpoint_path",
    "source_path",
    "transcript",
    "prompt_text",
}

ITEM_KEYS = {
    "item_id_sha256",
    "speaker_id_sha256",
    "split",
    "audio_object_id_sha256",
    "l1_stratum_id",
    "gender_stratum_id",
    "proficiency_stratum_id",
    "utterance_length_bin_id",
    "target_phone_stratum_id",
    "existing_error_present",
    "target_phone_count",
    "review_order_commitment_sha256",
    "model_output_exposed",
    "system_condition_exposed",
    "g04_item_reused",
    "s203_item_reused",
}

RATING_KEYS = {
    "rating_id",
    "item_id_sha256",
    "reviewer_id_sha256",
    "reviewer_role",
    "target_phone_index",
    "phone_realization",
    "error_type",
    "boundary_acceptable",
    "pronunciation_score",
    "comprehensibility_score",
    "fluency_score",
    "confidence",
    "original_rating_ids_sha256",
    "disagreement_receipt_sha256",
    "adjudication_reason_code",
    "adjudication_note",
    "independent_original_rating",
    "peer_rating_seen",
    "model_output_seen",
    "system_condition_seen",
    "rating_receipt_sha256",
}


class D07ContractError(ValueError):
    """Raised before unauthorized D07 human or locked-test access."""

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
            raise D07ContractError(f"duplicate YAML key: {key!r}")
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
    g04_progress_aggregate_read_count: int = 0
    g04_ui_state_path_resolution_count: int = 0
    g04_ui_state_read_count: int = 0
    h00_module_load_count: int = 0
    h00_approval_path_resolution_count: int = 0
    h00_approval_read_attempt_count: int = 0
    h00_approval_read_count: int = 0
    h00_attestation_path_resolution_count: int = 0
    h00_attestation_read_attempt_count: int = 0
    h00_attestation_read_count: int = 0
    h00_restricted_document_path_resolution_count: int = 0
    h00_restricted_document_read_count: int = 0
    institutional_approval_path_resolution_count: int = 0
    institutional_approval_read_attempt_count: int = 0
    institutional_approval_read_count: int = 0
    reviewer_attestation_path_resolution_count: int = 0
    reviewer_attestation_read_attempt_count: int = 0
    reviewer_attestation_read_count: int = 0
    preregistration_path_resolution_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    d1_item_manifest_path_resolution_count: int = 0
    d1_item_manifest_read_count: int = 0
    d1_item_row_read_count: int = 0
    r1_locator_path_resolution_count: int = 0
    r1_locator_read_count: int = 0
    calibration_audio_path_resolution_count: int = 0
    calibration_audio_read_count: int = 0
    calibration_rating_read_count: int = 0
    calibration_metric_count: int = 0
    calibration_result_count: int = 0
    g04_completion_path_resolution_count: int = 0
    g04_completion_read_count: int = 0
    locked_authorization_path_resolution_count: int = 0
    locked_authorization_read_count: int = 0
    locked_item_path_resolution_count: int = 0
    locked_item_read_count: int = 0
    locked_audio_path_resolution_count: int = 0
    locked_audio_read_count: int = 0
    locked_rating_read_count: int = 0
    expert_locked_access_transaction_count: int = 0
    developer_locked_access_count: int = 0
    model_access_count: int = 0
    gpu_access_count: int = 0
    network_access_count: int = 0
    metric_count: int = 0
    result_count: int = 0
    output_path_resolution_count: int = 0
    output_write_count: int = 0
    registry_write_count: int = 0
    commit_write_count: int = 0
    publication_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return {field: int(getattr(self, field)) for field in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    relative_path: str
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


@dataclass(frozen=True, slots=True)
class H00Evidence:
    approval_sha256: str
    attestation_sha256: str


@dataclass(frozen=True, slots=True)
class InstitutionalApprovalEvidence:
    sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ReviewerEvidence:
    sha256: str
    qualification_policy_sha256: str
    reviewer_ids_by_role: Mapping[str, str]
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreregistrationEvidence:
    sha256: str
    note_max_characters: int
    rubric_sha256: str
    primary_definition_sha256: str
    total_item_count: int
    locked_item_count: int
    scalar_gate_statistic: str
    taxonomy_gate_statistic: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ItemManifestEvidence:
    sha256: str
    items: Mapping[str, Mapping[str, Any]]
    calibration_count: int
    locked_count: int


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: str
    config_sha256: str
    h00_approval_sha256: str
    h00_attestation_sha256: str
    institutional_approval_sha256: str
    reviewer_attestation_sha256: str
    preregistration_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise D07ContractError(f"{name} must be a string-keyed mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise D07ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise D07ContractError(f"{name} must be a non-empty string")
    return value


def _integer(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise D07ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise D07ContractError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise D07ContractError(f"{name} must be <= {maximum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise D07ContractError(f"{name} must be boolean")
    return value


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if not SHA256_RE.fullmatch(text):
        raise D07ContractError(f"{name} must be a lowercase SHA-256")
    return text


def _safe_id(value: object, name: str) -> str:
    text = _string(value, name)
    if not SAFE_ID_RE.fullmatch(text):
        raise D07ContractError(f"{name} must be a bounded safe ID")
    return text


def _decimal(
    value: object,
    name: str,
    *,
    minimum: Decimal,
    maximum: Decimal,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise D07ContractError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise D07ContractError(f"{name} must be numeric") from exc
    if not result.is_finite() or result < minimum or result > maximum:
        raise D07ContractError(f"{name} must be in [{minimum},{maximum}]")
    return result


def _unit(value: object, name: str) -> Decimal:
    return _decimal(value, name, minimum=Decimal(0), maximum=Decimal(1))


def _signed(value: object, name: str) -> Decimal:
    return _decimal(value, name, minimum=Decimal(-1), maximum=Decimal(1))


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise D07ContractError(
            f"{name} differs: expected {expected!r}, observed {actual!r}"
        )


def _expect_keys(
    payload: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(payload)
    if actual != expected:
        raise D07ContractError(
            f"{name} keys differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise D07ContractError(f"{name} must be a normalized relative path")
    if "\\" in text or "\x00" in text or text != path.as_posix():
        raise D07ContractError(f"{name} must be a normalized POSIX path")
    return text


def _reject_nonfinite_tree(value: object, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise D07ContractError(f"{name} contains non-finite number")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_nonfinite_tree(child, f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_nonfinite_tree(child, f"{name}[{index}]")


def _reject_sensitive_payload(value: object, name: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_RECURSIVE_KEYS:
                raise D07ContractError(f"{name} contains forbidden field {key!r}")
            _reject_sensitive_payload(child, name)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_payload(child, name)


def _contains_tbd(value: object) -> bool:
    if isinstance(value, str):
        return value.startswith("TBD_")
    if isinstance(value, Mapping):
        return any(_contains_tbd(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_tbd(child) for child in value)
    return False


def _validate_note(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise D07ContractError("adjudication note must be a string")
    if len(value) > maximum:
        raise D07ContractError("adjudication note exceeds preregistered limit")
    if "\r" in value or "\n" in value or "\x00" in value:
        raise D07ContractError("adjudication note contains control/newline")
    lowered = value.casefold()
    if EMAIL_RE.search(value) or PHONE_RE.search(value):
        raise D07ContractError("adjudication note contains contact-like PII")
    if any(
        token in lowered
        for token in (
            "이름",
            "성명",
            "연락처",
            "소속",
            "출신기관",
            "기관명",
            "학교명",
            "http://",
            "https://",
        )
    ):
        raise D07ContractError("adjudication note contains prohibited PII marker")
    return value


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    _reject_nonfinite_tree(payload, "canonical payload")
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise D07ContractError(f"cannot load D07 config: {exc}") from exc
    return _mapping(payload, "D07 config")


def _reject_symlink_chain(
    workspace_root: Path,
    relative: str,
    name: str,
    *,
    require_leaf: bool,
) -> Path:
    root = workspace_root.resolve(strict=True)
    current = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        is_leaf = index == len(parts) - 1
        try:
            status = current.lstat()
        except FileNotFoundError:
            if require_leaf or not is_leaf:
                raise D07ContractError(f"{name} missing: {relative}")
            return current
        if stat.S_ISLNK(status.st_mode):
            raise D07ContractError(f"{name} traverses symlink: {relative}")
        if not is_leaf and not stat.S_ISDIR(status.st_mode):
            raise D07ContractError(f"{name} parent is not a directory")
        if is_leaf and require_leaf and not stat.S_ISREG(status.st_mode):
            raise D07ContractError(f"{name} is not a regular file")
    return current


def _read_file(
    workspace_root: Path,
    relative: str,
    name: str,
) -> tuple[bytes, FileSnapshot]:
    normalized = _relative_string(relative, name)
    path = _reject_symlink_chain(
        workspace_root,
        normalized,
        name,
        require_leaf=True,
    )
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if before_identity != after_identity or len(data) != after.st_size:
        raise D07ContractError(f"{name} changed while being read")
    return data, FileSnapshot(
        relative_path=normalized,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        device=after.st_dev,
        inode=after.st_ino,
        mtime_ns=after.st_mtime_ns,
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
        raise D07ContractError(f"{name} SHA-256 differs")
    try:
        payload = json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise D07ContractError(f"{name} must be valid JSON") from exc
    return _mapping(payload, name), snapshot


def _verify_snapshot(workspace_root: Path, snapshot: FileSnapshot) -> None:
    _, current = _read_file(
        workspace_root,
        snapshot.relative_path,
        f"snapshot {snapshot.relative_path}",
    )
    if current != snapshot:
        raise D07ContractError(f"TOCTOU drift: {snapshot.relative_path}")


def _load_module(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
) -> ModuleType:
    data, snapshot = _read_file(workspace_root, relative, "pinned H00 contract")
    if snapshot.sha256 != expected_sha256:
        raise D07ContractError("pinned H00 contract SHA-256 differs")
    module_name = "_voice_coach_d07_pinned_h00_contract"
    module = ModuleType(module_name)
    module.__file__ = str(workspace_root / relative)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(
            compile(data, str(workspace_root / relative), "exec"),
            module.__dict__,
        )
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise D07ContractError(f"cannot load pinned H00 contract: {exc}") from exc
    return module


def _validate_schema(
    payload: Mapping[str, Any],
    *,
    schema_version: str,
    name: str,
) -> None:
    _expect(
        payload.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        f"{name} dialect",
    )
    _expect(payload.get("type"), "object", f"{name} root")
    _expect(payload.get("additionalProperties"), False, f"{name} strict root")
    properties = _mapping(payload.get("properties"), f"{name} properties")
    version = _mapping(properties.get("schema_version"), f"{name} version")
    _expect(version.get("const"), schema_version, f"{name} schema version")
    if set(_list(payload.get("required"), f"{name} required")) != set(properties):
        raise D07ContractError(f"{name} root required/properties differ")

    def walk(node: object, path: str) -> None:
        if isinstance(node, Mapping):
            if node.get("type") == "object" and "properties" in node:
                _expect(
                    node.get("additionalProperties"),
                    False,
                    f"{name} strict object {path}",
                )
            for key, child in node.items():
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(payload, "$")


def _validate_template(
    payload: Mapping[str, Any],
    *,
    schema_version: str,
    name: str,
) -> None:
    _expect(payload.get("schema_version"), schema_version, f"{name} schema")
    _expect(payload.get("privacy_zone"), "C1_public_template_only", f"{name} zone")
    if not _contains_tbd(payload):
        raise D07ContractError(f"{name} must retain TBD placeholders")


def _validate_g04_progress(payload: Mapping[str, Any]) -> None:
    _expect(
        payload.get("schema_version"),
        G04_PROGRESS_SCHEMA_VERSION,
        "G04 progress schema",
    )
    _expect(payload.get("status"), "incomplete", "G04 current status")
    _expect(payload.get("expected_count"), 240, "G04 expected count")
    _expect(payload.get("completed_count"), 0, "G04 completed count")
    _expect(payload.get("remaining_count"), 240, "G04 remaining count")
    _expect(payload.get("completion_fraction"), 0.0, "G04 fraction")
    _expect(payload.get("correct_count"), 0, "G04 correct count")
    _expect(payload.get("incorrect_count"), 0, "G04 incorrect count")
    _expect(payload.get("alignment_accuracy"), None, "G04 incomplete accuracy")
    _expect(payload.get("minimum_accuracy"), 0.95, "G04 gate")
    _expect(payload.get("reviewer_count"), 0, "G04 reviewer count")
    source = _mapping(payload.get("source"), "G04 source")
    _expect(source.get("review_state_sha256"), None, "G04 state unopened")
    export = _mapping(payload.get("export"), "G04 export")
    _expect(export.get("written"), False, "G04 export boundary")
    _expect(export.get("sha256"), None, "G04 export hash")
    privacy = _mapping(payload.get("privacy"), "G04 privacy")
    for field in (
        "item_ids_namespaced_sha256_only",
        "reviewer_ids_namespaced_sha256_only",
    ):
        _expect(privacy.get(field), True, f"G04 privacy {field}")
    _expect(
        privacy.get("raw_paths_or_utterance_ids_exported"),
        False,
        "G04 path export",
    )


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_KEYS, "D07 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "config schema")
    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(experiment.get("id"), "d07-independent-expert-gold-v1", "experiment ID")
    _expect(experiment.get("work_package"), "D07", "work package")
    _expect(experiment.get("status"), STATIC_STATUS, "experiment status")
    for field in (
        "actual_expert_count",
        "actual_rating_count",
        "actual_locked_access_count",
        "actual_metric_count",
        "actual_result_count",
        "completion_claim_allowed",
    ):
        _expect(experiment.get(field), 0 if field.endswith("_count") else False, field)

    canonical = _mapping(config.get("canonical_sources"), "canonical_sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_or_A1_aggregate_static_pins_only",
        "source access scope",
    )
    _expect(canonical.get("strict_sha256"), True, "strict source hashes")

    g04 = _mapping(config.get("g04_separation"), "g04_separation")
    _expect(g04.get("stage"), "G04_distinct_alignment_review", "G04 stage")
    _expect(g04.get("expected_count"), 240, "G04 count")
    _expect(g04.get("current_completed_count"), 0, "G04 current progress")
    _expect(g04.get("d07_item_or_rating_reuse_allowed"), False, "G04 reuse")
    _expect(g04.get("restricted_ui_state_access_allowed"), False, "G04 state access")
    _relative_string(g04.get("progress_aggregate_path"), "G04 progress path")
    g04_completion_sha = _string(
        g04.get("completion_attestation_sha256"),
        "G04 completion SHA",
    )
    if g04_completion_sha != TBD_G04_COMPLETION:
        _sha256(g04_completion_sha, "G04 completion SHA")

    h00 = _mapping(config.get("predecessor_h00"), "predecessor_h00")
    for field in ("config_path", "contract_path"):
        _relative_string(h00.get(field), f"H00 {field}")
    for field, marker in (
        ("approval_sha256", TBD_H00_APPROVAL),
        ("attestation_sha256", TBD_H00_ATTESTATION),
    ):
        value = _string(h00.get(field), f"H00 {field}")
        if value != marker:
            _sha256(value, f"H00 {field}")

    approval = _mapping(
        config.get("institutional_expert_approval"),
        "institutional_expert_approval",
    )
    _expect(
        approval.get("required_schema_version"),
        INSTITUTIONAL_APPROVAL_SCHEMA_VERSION,
        "D07 approval schema",
    )
    approval_sha = _string(approval.get("approved_artifact_sha256"), "D07 approval SHA")
    if approval_sha != TBD_EXTERNAL_ATTESTATION:
        _sha256(approval_sha, "D07 approval SHA")

    reviewer = _mapping(config.get("reviewer_attestations"), "reviewers")
    _expect(
        reviewer.get("required_schema_version"),
        REVIEWER_ATTESTATION_SCHEMA_VERSION,
        "reviewer schema",
    )
    _expect(reviewer.get("primary_reviewer_count"), 2, "primary reviewer count")
    _expect(reviewer.get("adjudicator_count"), 1, "adjudicator count")
    _expect(
        reviewer.get("qualification_evidence"),
        TBD_ATTESTATION,
        "qualification evidence boundary",
    )
    reviewer_sha = _string(reviewer.get("approved_artifact_sha256"), "reviewer SHA")
    if reviewer_sha != TBD_EXTERNAL_ATTESTATION:
        _sha256(reviewer_sha, "reviewer SHA")

    prereg = _mapping(config.get("preregistration"), "preregistration")
    _expect(
        prereg.get("required_schema_version"),
        PREREGISTRATION_SCHEMA_VERSION,
        "prereg schema",
    )
    prereg_sha = _string(prereg.get("approved_artifact_sha256"), "prereg SHA")
    if prereg_sha != TBD_PREREGISTRATION:
        _sha256(prereg_sha, "prereg SHA")

    sampling = _mapping(config.get("sampling_contract"), "sampling_contract")
    _expect(sampling.get("minimum_total_count"), MINIMUM_TOTAL_COUNT, "minimum total")
    _expect(sampling.get("maximum_total_count"), MAXIMUM_TOTAL_COUNT, "maximum total")
    _expect(sampling.get("calibration_count"), CALIBRATION_COUNT, "calibration count")
    _expect(
        sampling.get("minimum_locked_test_count"), MINIMUM_LOCKED_COUNT, "locked min"
    )
    _expect(
        sampling.get("maximum_locked_test_count"), MAXIMUM_LOCKED_COUNT, "locked max"
    )
    _expect(
        tuple(_list(sampling.get("stratification_axes"), "stratification axes")),
        STRATIFICATION_AXES,
        "stratification axes",
    )
    _expect(sampling.get("exact_allocation"), TBD_PREREGISTRATION, "allocation TBD")

    rating = _mapping(config.get("rating_contract"), "rating_contract")
    _expect(
        tuple(_list(rating.get("phone_realization"), "phone realization")),
        PHONE_REALIZATIONS,
        "phone realization",
    )
    _expect(
        tuple(_list(rating.get("boundary_acceptable"), "boundary")),
        BOUNDARY_VALUES,
        "boundary",
    )
    _expect(rating.get("score_range"), [1, 5], "score range")
    _expect(rating.get("confidence_range"), [1, 4], "confidence range")
    _expect(rating.get("primary_reviewers_mutually_and_model_blind"), True, "blind")
    _expect(rating.get("adjudicator_only_disagreements"), True, "adjudication scope")
    _expect(rating.get("original_ratings_preserved"), True, "original preservation")

    agreement = _mapping(config.get("calibration_agreement"), "agreement")
    _expect(agreement.get("scalar_threshold"), 0.70, "scalar threshold")
    _expect(agreement.get("taxonomy_threshold"), 0.65, "taxonomy threshold")
    _expect(
        agreement.get("combination"),
        "scalar_gte_0_70_or_taxonomy_gte_0_65",
        "agreement combination",
    )
    _expect(agreement.get("scalar_gate_statistic"), TBD_PREREGISTRATION, "scalar TBD")
    _expect(
        agreement.get("taxonomy_gate_statistic"), TBD_PREREGISTRATION, "taxonomy TBD"
    )
    _expect(
        agreement.get("failure_requires_retraining_and_separate_resample"),
        True,
        "failure",
    )

    locked = _mapping(config.get("locked_test_contract"), "locked")
    _expect(locked.get("g04_completion_required"), True, "G04 final gate")
    _expect(locked.get("expert_access_transaction_count"), 1, "expert one-shot")
    _expect(locked.get("developer_locked_access_count"), 0, "developer locked access")
    _expect(locked.get("rubric_change_after_start_allowed"), False, "rubric freeze")
    _expect(locked.get("primary_change_after_start_allowed"), False, "primary freeze")
    _expect(
        tuple(_list(locked.get("sensitivity_bases"), "sensitivity bases")),
        SENSITIVITY_BASES,
        "sensitivity bases",
    )

    privacy = _mapping(config.get("privacy_and_non_execution"), "privacy")
    for field in (
        "r0_audio_access_allowed_in_this_package",
        "r1_locator_access_allowed_in_this_package",
        "d1_item_or_rating_access_allowed_in_this_package",
        "g04_ui_state_access_allowed",
        "locked_audio_access_allowed_in_this_package",
        "reviewer_recruitment_or_rating_allowed",
        "model_gpu_network_access_allowed",
        "output_write_allowed",
    ):
        _expect(privacy.get(field), False, f"privacy {field}")
    _expect(
        tuple(_list(config.get("execution_order"), "execution order")),
        EXPECTED_EXECUTION_ORDER,
        "execution order",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    _expect(outputs.get("write_enabled_in_this_package"), False, "write boundary")
    _expect(outputs.get("no_overwrite"), True, "no overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "atomic publish")
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
    root = workspace_root.resolve(strict=True)
    access = audit if audit is not None else AccessAudit()
    _validate_config_values(config)
    absolute_config = config_path if config_path.is_absolute() else root / config_path
    try:
        relative_config = absolute_config.relative_to(root).as_posix()
    except ValueError as exc:
        raise D07ContractError("D07 config must be inside workspace") from exc
    config_data, config_snapshot = _read_file(root, relative_config, "D07 config")
    loaded = yaml.load(config_data.decode(), Loader=_UniqueKeyLoader)
    if loaded != config:
        raise D07ContractError("D07 config changed between load and validation")

    canonical = _mapping(config.get("canonical_sources"), "canonical_sources")
    pins = _list(canonical.get("pins"), "source pins")
    if len(pins) != len(SOURCE_PATHS):
        raise D07ContractError("source pin count differs")
    snapshots: list[FileSnapshot] = []
    payloads: dict[str, bytes] = {}
    seen: set[str] = set()
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        if relative in seen:
            raise D07ContractError(f"duplicate source pin: {relative}")
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_sha:
            raise D07ContractError(
                f"canonical source SHA-256 drift: {relative}",
                stage="source_pin",
                audit=access,
            )
        seen.add(relative)
        snapshots.append(snapshot)
        payloads[relative] = data
    if tuple(sorted(seen)) != tuple(sorted(SOURCE_PATHS)):
        raise D07ContractError("source pin path set differs")
    by_path = {snapshot.relative_path: snapshot for snapshot in snapshots}

    schema_specs = (
        (
            "configs/expert_evaluation/d07_gold_item_manifest_v1.schema.json",
            ITEM_SCHEMA_VERSION,
            "D07 item schema",
        ),
        (
            "configs/expert_evaluation/d07_expert_rating_manifest_v1.schema.json",
            RATING_SCHEMA_VERSION,
            "D07 rating schema",
        ),
        (
            "configs/expert_evaluation/d07_calibration_agreement_report_v1.schema.json",
            CALIBRATION_SCHEMA_VERSION,
            "D07 calibration schema",
        ),
        (
            "configs/expert_evaluation/d07_locked_evaluation_report_v1.schema.json",
            LOCKED_REPORT_SCHEMA_VERSION,
            "D07 locked report schema",
        ),
        (
            "configs/expert_evaluation/d07_gold_completion_attestation_v1.schema.json",
            COMPLETION_SCHEMA_VERSION,
            "D07 completion schema",
        ),
    )
    for path, version, name in schema_specs:
        _validate_schema(
            _mapping(json.loads(payloads[path]), name),
            schema_version=version,
            name=name,
        )
    template_specs = (
        (
            "configs/expert_evaluation/d07_institutional_expert_approval_v1.template.json",
            INSTITUTIONAL_APPROVAL_SCHEMA_VERSION,
            "D07 institutional template",
        ),
        (
            "configs/expert_evaluation/d07_reviewer_attestation_v1.template.json",
            REVIEWER_ATTESTATION_SCHEMA_VERSION,
            "D07 reviewer template",
        ),
        (
            "configs/expert_evaluation/d07_preregistration_v1.template.json",
            PREREGISTRATION_SCHEMA_VERSION,
            "D07 preregistration template",
        ),
        (
            "configs/expert_evaluation/d07_locked_access_authorization_v1.template.json",
            LOCKED_AUTHORIZATION_SCHEMA_VERSION,
            "D07 locked authorization template",
        ),
    )
    for path, version, name in template_specs:
        _validate_template(
            _mapping(json.loads(payloads[path]), name),
            schema_version=version,
            name=name,
        )

    progress_path = (
        "data/processed/aihub71490/manifests/"
        "aihub71490_manual_alignment_review_progress.json"
    )
    progress = _mapping(json.loads(payloads[progress_path]), "G04 progress aggregate")
    _validate_g04_progress(progress)
    access.g04_progress_aggregate_read_count += 1

    catalog_path = "configs/coaching/korean_error_advice_ontology_v1.json"
    catalog = _mapping(json.loads(payloads[catalog_path]), "C01 ontology")
    tags = _list(catalog.get("tags"), "C01 ontology tags")
    observed_tags = tuple(
        _string(_mapping(tag, "C01 tag").get("id"), "C01 tag ID") for tag in tags
    )
    _expect(observed_tags, ERROR_TYPES[:-1], "D07 17-tag ontology binding")

    bindings = (
        (
            "g04_separation",
            "progress_aggregate_path",
            "progress_aggregate_sha256",
            progress_path,
        ),
        (
            "predecessor_h00",
            "config_path",
            "config_sha256",
            "configs/experiments/h00_human_research_approval_v1.yaml",
        ),
        (
            "predecessor_h00",
            "contract_path",
            "contract_sha256",
            "src/voice_coach/human_research/h00_approval_contract.py",
        ),
        (
            "institutional_expert_approval",
            "template_path",
            "template_sha256",
            "configs/expert_evaluation/d07_institutional_expert_approval_v1.template.json",
        ),
        (
            "reviewer_attestations",
            "template_path",
            "template_sha256",
            "configs/expert_evaluation/d07_reviewer_attestation_v1.template.json",
        ),
        (
            "preregistration",
            "template_path",
            "template_sha256",
            "configs/expert_evaluation/d07_preregistration_v1.template.json",
        ),
        (
            "locked_test_contract",
            "authorization_template_path",
            "authorization_template_sha256",
            "configs/expert_evaluation/d07_locked_access_authorization_v1.template.json",
        ),
    )
    for section_name, path_field, sha_field, expected_path in bindings:
        section = _mapping(config.get(section_name), section_name)
        _expect(section.get(path_field), expected_path, f"{section_name}.{path_field}")
        _expect(
            section.get(sha_field),
            by_path[expected_path].sha256,
            f"{section_name}.{sha_field}",
        )

    outputs = _mapping(config.get("outputs"), "outputs")
    output_schema_bindings = {
        "item_schema_path": "configs/expert_evaluation/d07_gold_item_manifest_v1.schema.json",
        "rating_schema_path": "configs/expert_evaluation/d07_expert_rating_manifest_v1.schema.json",
        "calibration_report_schema_path": "configs/expert_evaluation/d07_calibration_agreement_report_v1.schema.json",
        "locked_report_schema_path": "configs/expert_evaluation/d07_locked_evaluation_report_v1.schema.json",
        "completion_schema_path": "configs/expert_evaluation/d07_gold_completion_attestation_v1.schema.json",
    }
    for field, expected_path in output_schema_bindings.items():
        _expect(outputs.get(field), expected_path, f"outputs.{field}")
        _expect(
            outputs.get(field.replace("_path", "_sha256")),
            by_path[expected_path].sha256,
            f"outputs.{field} hash",
        )

    policy = {
        "g04_separation": config["g04_separation"],
        "sampling_contract": config["sampling_contract"],
        "rating_contract": config["rating_contract"],
        "calibration_agreement": config["calibration_agreement"],
        "locked_test_contract": config["locked_test_contract"],
        "privacy_and_non_execution": config["privacy_and_non_execution"],
        "execution_order": config["execution_order"],
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        source_snapshots=tuple(snapshots),
    )


def _verify_snapshots(
    workspace_root: Path,
    snapshots: Sequence[FileSnapshot],
    audit: AccessAudit,
) -> None:
    for snapshot in snapshots:
        _verify_snapshot(workspace_root, snapshot)
        audit.source_toctou_verification_count += 1


def _deep_validate_h00(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> H00Evidence:
    section = _mapping(config.get("predecessor_h00"), "predecessor_h00")
    approval_sha = _string(section.get("approval_sha256"), "H00 approval SHA")
    attestation_sha = _string(section.get("attestation_sha256"), "H00 attestation SHA")
    if approval_sha == TBD_H00_APPROVAL or attestation_sha == TBD_H00_ATTESTATION:
        raise D07ContractError(
            "H00 actual approval/attestation is unresolved",
            stage="h00_approval",
            audit=audit,
        )
    _sha256(approval_sha, "H00 approval SHA")
    _sha256(attestation_sha, "H00 attestation SHA")
    audit.h00_module_load_count += 1
    try:
        module = _load_module(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("contract_path"), "H00 contract path"
            ),
            expected_sha256=_sha256(section.get("contract_sha256"), "H00 contract SHA"),
        )
        h00_config_path = workspace_root / _relative_string(
            section.get("config_path"),
            "H00 config path",
        )
        h00_config = module.load_config(h00_config_path)
        official = _mapping(h00_config.get("official_approval"), "pinned H00 approval")
        attestation = _mapping(
            h00_config.get("official_document_attestation"),
            "pinned H00 attestation",
        )
        _expect(official.get("approval_sha256"), approval_sha, "H00 approval binding")
        _expect(
            attestation.get("attestation_sha256"),
            attestation_sha,
            "H00 attestation binding",
        )
        result = module.run_full_preflight(
            workspace_root=workspace_root,
            config_path=h00_config_path,
            config=h00_config,
        )
    except Exception as exc:
        raise D07ContractError(
            f"H00 deep validation failed: {exc}",
            stage="h00_approval",
            audit=audit,
        ) from exc
    h00_audit = result.audit
    audit.h00_approval_path_resolution_count += int(
        h00_audit["approval_path_resolution_count"]
    )
    audit.h00_approval_read_attempt_count += int(
        h00_audit["approval_read_attempt_count"]
    )
    audit.h00_approval_read_count += int(h00_audit["approval_read_count"])
    audit.h00_attestation_path_resolution_count += int(
        h00_audit["attestation_path_resolution_count"]
    )
    audit.h00_attestation_read_attempt_count += int(
        h00_audit["attestation_read_attempt_count"]
    )
    audit.h00_attestation_read_count += int(h00_audit["attestation_read_count"])
    audit.h00_restricted_document_path_resolution_count += int(
        h00_audit["restricted_locator_path_resolution_count"]
    )
    audit.h00_restricted_document_read_count += int(
        h00_audit["restricted_document_read_count"]
    )
    _expect(result.approval_sha256, approval_sha, "H00 deep approval SHA")
    _expect(result.attestation_sha256, attestation_sha, "H00 deep attestation SHA")
    return H00Evidence(
        approval_sha256=approval_sha,
        attestation_sha256=attestation_sha,
    )


def validate_institutional_approval_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
) -> Mapping[str, Any]:
    _reject_nonfinite_tree(payload, "D07 institutional approval")
    _reject_sensitive_payload(payload, "D07 institutional approval")
    keys = {
        "schema_version",
        "approval_id",
        "status",
        "h00_approval_sha256",
        "h00_official_document_attestation_sha256",
        "d07_protocol_sha256",
        "institutional_scope_receipt_sha256",
        "existing_restricted_audio_expert_rating_approved",
        "calibration_800_approved",
        "locked_test_up_to_1200_expert_rating_approved",
        "two_independent_reviewers_approved",
        "disagreement_only_adjudicator_approved",
        "consent_or_lawful_basis_verified",
        "withdrawal_process_verified",
        "purpose_limitation_verified",
        "retention_and_deletion_verified",
        "access_logging_verified",
        "restricted_redistribution_only",
        "raw_audio_export_allowed",
        "privacy_zone",
    }
    _expect_keys(payload, keys, "D07 institutional approval")
    _expect(
        payload.get("schema_version"),
        INSTITUTIONAL_APPROVAL_SCHEMA_VERSION,
        "D07 approval schema",
    )
    _safe_id(payload.get("approval_id"), "D07 approval ID")
    _expect(payload.get("status"), "approved", "D07 approval status")
    _expect(payload.get("h00_approval_sha256"), h00.approval_sha256, "H00 approval")
    _expect(
        payload.get("h00_official_document_attestation_sha256"),
        h00.attestation_sha256,
        "H00 attestation",
    )
    for field in ("d07_protocol_sha256", "institutional_scope_receipt_sha256"):
        _sha256(payload.get(field), f"D07 approval {field}")
    for field in (
        "existing_restricted_audio_expert_rating_approved",
        "calibration_800_approved",
        "locked_test_up_to_1200_expert_rating_approved",
        "two_independent_reviewers_approved",
        "disagreement_only_adjudicator_approved",
        "consent_or_lawful_basis_verified",
        "withdrawal_process_verified",
        "purpose_limitation_verified",
        "retention_and_deletion_verified",
        "access_logging_verified",
        "restricted_redistribution_only",
    ):
        _expect(payload.get(field), True, f"D07 approval {field}")
    _expect(payload.get("raw_audio_export_allowed"), False, "raw audio export")
    _expect(payload.get("privacy_zone"), "C1_public_attestation", "approval zone")
    if _contains_tbd(payload):
        raise D07ContractError("D07 institutional approval contains TBD")
    return payload


def validate_reviewer_attestation_payload(
    payload: Mapping[str, Any],
    *,
    institutional_approval_sha256: str,
) -> ReviewerEvidence:
    _reject_nonfinite_tree(payload, "D07 reviewer attestation")
    _reject_sensitive_payload(payload, "D07 reviewer attestation")
    keys = {
        "schema_version",
        "status",
        "d07_institutional_approval_sha256",
        "reviewer_count",
        "primary_reviewer_count",
        "adjudicator_count",
        "qualification_evidence_policy",
        "rubric_training_policy_sha256",
        "reviewers",
        "every_reviewer_has_korean_phonetics_pronunciation_education_or_alignment_experience",
        "primary_reviewers_mutually_blind",
        "all_reviewers_model_blind",
        "adjudicator_restricted_to_disagreements",
        "reviewer_identity_mapping_stored_separately",
        "privacy_zone",
    }
    _expect_keys(payload, keys, "D07 reviewer attestation")
    _expect(
        payload.get("schema_version"),
        REVIEWER_ATTESTATION_SCHEMA_VERSION,
        "reviewer schema",
    )
    _expect(payload.get("status"), "approved", "reviewer status")
    _expect(
        payload.get("d07_institutional_approval_sha256"),
        institutional_approval_sha256,
        "reviewer approval binding",
    )
    _expect(payload.get("reviewer_count"), 3, "reviewer count")
    _expect(payload.get("primary_reviewer_count"), 2, "primary reviewer count")
    _expect(payload.get("adjudicator_count"), 1, "adjudicator count")
    qualification_policy = _sha256(
        payload.get("qualification_evidence_policy"),
        "qualification policy SHA",
    )
    _sha256(payload.get("rubric_training_policy_sha256"), "rubric training policy")
    reviewers = _list(payload.get("reviewers"), "reviewers")
    _expect(len(reviewers), 3, "reviewer list count")
    by_role: dict[str, str] = {}
    seen_ids: set[str] = set()
    for index, raw_reviewer in enumerate(reviewers):
        reviewer = _mapping(raw_reviewer, f"reviewer {index}")
        _expect_keys(
            reviewer,
            {
                "reviewer_id_sha256",
                "role",
                "qualification_family",
                "qualification_evidence_receipt_sha256",
                "rubric_training_receipt_sha256",
                "confidentiality_receipt_sha256",
                "conflict_of_interest_cleared",
                "mutual_blinding_attested",
                "model_blinding_attested",
                "disagreement_only_scope_attested",
            },
            f"reviewer {index}",
        )
        reviewer_id = _sha256(
            reviewer.get("reviewer_id_sha256"), f"reviewer {index} ID"
        )
        if reviewer_id in seen_ids:
            raise D07ContractError("duplicate reviewer ID")
        seen_ids.add(reviewer_id)
        role = _string(reviewer.get("role"), f"reviewer {index} role")
        if role not in REVIEWER_ROLES or role in by_role:
            raise D07ContractError("reviewer roles must be unique and complete")
        by_role[role] = reviewer_id
        if reviewer.get("qualification_family") not in (
            "korean_phonetics",
            "korean_pronunciation_education",
            "forced_alignment_review",
        ):
            raise D07ContractError("unsupported reviewer qualification family")
        for field in (
            "qualification_evidence_receipt_sha256",
            "rubric_training_receipt_sha256",
            "confidentiality_receipt_sha256",
        ):
            _sha256(reviewer.get(field), f"reviewer {index} {field}")
        _expect(
            reviewer.get("conflict_of_interest_cleared"),
            True,
            f"reviewer {index} conflict",
        )
        _expect(
            reviewer.get("model_blinding_attested"),
            True,
            f"reviewer {index} model blindness",
        )
        if role in PRIMARY_ROLES:
            _expect(
                reviewer.get("mutual_blinding_attested"),
                True,
                f"reviewer {index} mutual blindness",
            )
            _expect(
                reviewer.get("disagreement_only_scope_attested"),
                False,
                f"reviewer {index} adjudication scope",
            )
        else:
            _expect(
                reviewer.get("mutual_blinding_attested"),
                False,
                "adjudicator mutual-blind field",
            )
            _expect(
                reviewer.get("disagreement_only_scope_attested"),
                True,
                "adjudicator disagreement scope",
            )
    if set(by_role) != set(REVIEWER_ROLES):
        raise D07ContractError("reviewer role set is incomplete")
    for field in (
        "every_reviewer_has_korean_phonetics_pronunciation_education_or_alignment_experience",
        "primary_reviewers_mutually_blind",
        "all_reviewers_model_blind",
        "adjudicator_restricted_to_disagreements",
        "reviewer_identity_mapping_stored_separately",
    ):
        _expect(payload.get(field), True, f"reviewer attestation {field}")
    _expect(payload.get("privacy_zone"), "C1_public_attestation", "reviewer zone")
    if _contains_tbd(payload):
        raise D07ContractError("reviewer attestation contains TBD")
    return ReviewerEvidence(
        sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        qualification_policy_sha256=qualification_policy,
        reviewer_ids_by_role=by_role,
        payload=payload,
    )


def validate_preregistration_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
    institutional_approval_sha256: str,
    reviewer: ReviewerEvidence,
    g04_progress_sha256: str,
    ontology_sha256: str,
) -> PreregistrationEvidence:
    _reject_nonfinite_tree(payload, "D07 preregistration")
    _reject_sensitive_payload(payload, "D07 preregistration")
    keys = {
        "schema_version",
        "status",
        "frozen",
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "d07_institutional_approval_sha256",
        "reviewer_attestation_sha256",
        "g04_progress_aggregate_sha256",
        "sampling",
        "reviewer_design",
        "rubric_and_labels",
        "calibration_agreement",
        "statistics",
        "locked_test",
        "approval",
        "privacy_zone",
    }
    _expect_keys(payload, keys, "D07 preregistration")
    _expect(
        payload.get("schema_version"), PREREGISTRATION_SCHEMA_VERSION, "prereg schema"
    )
    _expect(payload.get("status"), "approved", "prereg status")
    _expect(payload.get("frozen"), True, "prereg frozen")
    _expect(
        payload.get("h00_approval_sha256"), h00.approval_sha256, "prereg H00 approval"
    )
    _expect(
        payload.get("h00_attestation_sha256"),
        h00.attestation_sha256,
        "prereg H00 attestation",
    )
    _expect(
        payload.get("d07_institutional_approval_sha256"),
        institutional_approval_sha256,
        "prereg institutional approval",
    )
    _expect(
        payload.get("reviewer_attestation_sha256"),
        reviewer.sha256,
        "prereg reviewer binding",
    )
    _expect(
        payload.get("g04_progress_aggregate_sha256"),
        g04_progress_sha256,
        "prereg G04 aggregate binding",
    )

    sampling = _mapping(payload.get("sampling"), "sampling")
    _expect_keys(
        sampling,
        {
            "total_item_count",
            "calibration_item_count",
            "locked_test_item_count",
            "stratification_axes",
            "exact_allocation",
            "small_group_minimum_allocation",
            "population_weighting",
            "unweighted_slice_reporting",
            "split_assignment_frozen_before_first_rating",
            "g04_item_reuse_allowed",
            "s203_item_reuse_allowed",
        },
        "sampling",
    )
    total = _integer(
        sampling.get("total_item_count"),
        "total item count",
        minimum=MINIMUM_TOTAL_COUNT,
        maximum=MAXIMUM_TOTAL_COUNT,
    )
    _expect(sampling.get("calibration_item_count"), CALIBRATION_COUNT, "calibration N")
    locked = _integer(
        sampling.get("locked_test_item_count"),
        "locked item count",
        minimum=MINIMUM_LOCKED_COUNT,
        maximum=MAXIMUM_LOCKED_COUNT,
    )
    _expect(total, CALIBRATION_COUNT + locked, "split count sum")
    _expect(
        tuple(_list(sampling.get("stratification_axes"), "sampling strata")),
        STRATIFICATION_AXES,
        "sampling strata",
    )
    allocation = sampling.get("exact_allocation")
    if not isinstance(allocation, (list, Mapping)) or not allocation:
        raise D07ContractError("exact sampling allocation must be resolved")
    for field in (
        "unweighted_slice_reporting",
        "split_assignment_frozen_before_first_rating",
    ):
        _expect(sampling.get(field), True, f"sampling {field}")
    for field in ("g04_item_reuse_allowed", "s203_item_reuse_allowed"):
        _expect(sampling.get(field), False, f"sampling {field}")

    design = _mapping(payload.get("reviewer_design"), "reviewer design")
    _expect(design.get("primary_reviewer_count"), 2, "reviewer design primary count")
    _expect(design.get("adjudicator_count"), 1, "reviewer design adjudicator count")
    _expect(
        design.get("qualification_evidence"),
        reviewer.qualification_policy_sha256,
        "qualification policy binding",
    )
    for field in (
        "all_items_independently_rated_by_both_primary_reviewers",
        "primary_reviewers_mutually_blind",
        "all_reviewers_model_blind",
        "original_ratings_preserved",
    ):
        _expect(design.get(field), True, f"reviewer design {field}")
    note_max = _integer(
        design.get("adjudication_note_max_characters"),
        "adjudication note maximum",
        minimum=1,
        maximum=10000,
    )

    rubric = _mapping(payload.get("rubric_and_labels"), "rubric")
    rubric_sha = _sha256(rubric.get("rubric_sha256"), "rubric SHA")
    primary_sha = _sha256(
        rubric.get("primary_definition_sha256"),
        "primary definition SHA",
    )
    _expect(
        tuple(_list(rubric.get("phone_realization_values"), "phone values")),
        PHONE_REALIZATIONS,
        "phone values",
    )
    _expect(
        rubric.get("error_type_ontology_sha256"),
        ontology_sha256,
        "ontology binding",
    )
    _expect(
        tuple(_list(rubric.get("boundary_values"), "boundary values")),
        BOUNDARY_VALUES,
        "boundary values",
    )
    _expect(rubric.get("boundary_reference_ms"), 50, "boundary reference")
    _expect(rubric.get("score_range"), [1, 5], "score range")
    _expect(rubric.get("confidence_range"), [1, 4], "confidence range")

    agreement = _mapping(payload.get("calibration_agreement"), "agreement")
    _expect(agreement.get("scalar_gate_threshold"), 0.7, "scalar threshold")
    _expect(agreement.get("taxonomy_gate_threshold"), 0.65, "taxonomy threshold")
    _expect(
        agreement.get("gate_combination"),
        "scalar_gte_0_70_or_taxonomy_gte_0_65",
        "gate combination",
    )
    scalar_stat = _safe_id(
        agreement.get("scalar_gate_statistic"),
        "scalar gate statistic",
    )
    taxonomy_stat = _safe_id(
        agreement.get("taxonomy_gate_statistic"),
        "taxonomy gate statistic",
    )
    for field in (
        "failed_gate_requires_retraining",
        "failed_gate_requires_separate_resample",
    ):
        _expect(agreement.get(field), True, f"agreement {field}")
    _expect(
        agreement.get("adjudication_may_mask_low_agreement"),
        False,
        "adjudication masking",
    )

    statistics = _mapping(payload.get("statistics"), "statistics")
    for field in (
        "confidence_interval_method",
        "confidence_level",
        "cluster_unit",
        "multiplicity_control",
        "minimum_support_rule",
        "small_support_policy",
        "weighted_and_unweighted_slice_estimands",
    ):
        if statistics.get(field) in (None, ""):
            raise D07ContractError(f"statistics {field} is unresolved")
    locked_policy = _mapping(payload.get("locked_test"), "locked policy")
    _expect(
        locked_policy.get("g04_completion_required_before_access"), True, "G04 gate"
    )
    _expect(locked_policy.get("expert_access_transaction_count"), 1, "expert access")
    _expect(
        locked_policy.get("developer_model_result_access_before_final_gate"),
        False,
        "developer access",
    )
    _expect(
        locked_policy.get("rubric_change_after_start_allowed"), False, "rubric change"
    )
    _expect(
        locked_policy.get("primary_change_after_start_allowed"), False, "primary change"
    )
    _expect(
        tuple(_list(locked_policy.get("sensitivity_bases"), "sensitivity bases")),
        SENSITIVITY_BASES,
        "sensitivity bases",
    )
    approval = _mapping(payload.get("approval"), "prereg approval")
    _expect(approval.get("decision"), "approved", "prereg approval decision")
    _sha256(approval.get("signed_receipt_sha256"), "prereg receipt")
    _expect(payload.get("privacy_zone"), "C1_public_preregistration", "prereg zone")
    if _contains_tbd(payload):
        raise D07ContractError("frozen D07 preregistration contains TBD")
    return PreregistrationEvidence(
        sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        note_max_characters=note_max,
        rubric_sha256=rubric_sha,
        primary_definition_sha256=primary_sha,
        total_item_count=total,
        locked_item_count=locked,
        scalar_gate_statistic=scalar_stat,
        taxonomy_gate_statistic=taxonomy_stat,
        payload=payload,
    )


def validate_item_manifest_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
    institutional_approval_sha256: str,
    reviewer_sha256: str,
    preregistration: PreregistrationEvidence,
    g04_progress_sha256: str,
) -> ItemManifestEvidence:
    _reject_nonfinite_tree(payload, "D07 item manifest")
    _reject_sensitive_payload(payload, "D07 item manifest")
    keys = {
        "schema_version",
        "manifest_id",
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "d07_institutional_approval_sha256",
        "reviewer_attestation_sha256",
        "preregistration_sha256",
        "g04_progress_aggregate_sha256",
        "privacy_zone",
        "split_assignment_frozen_before_first_rating",
        "total_item_count",
        "calibration_item_count",
        "locked_test_item_count",
        "items",
    }
    _expect_keys(payload, keys, "D07 item manifest")
    _expect(payload.get("schema_version"), ITEM_SCHEMA_VERSION, "item schema")
    manifest_id = _string(payload.get("manifest_id"), "item manifest ID")
    if not re.fullmatch(r"d07items_[0-9a-f]{32}", manifest_id):
        raise D07ContractError("invalid D07 item manifest ID")
    bindings = {
        "h00_approval_sha256": h00.approval_sha256,
        "h00_attestation_sha256": h00.attestation_sha256,
        "d07_institutional_approval_sha256": institutional_approval_sha256,
        "reviewer_attestation_sha256": reviewer_sha256,
        "preregistration_sha256": preregistration.sha256,
        "g04_progress_aggregate_sha256": g04_progress_sha256,
    }
    for field, expected in bindings.items():
        _expect(payload.get(field), expected, f"item manifest {field}")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "item zone",
    )
    _expect(
        payload.get("split_assignment_frozen_before_first_rating"),
        True,
        "split freeze",
    )
    _expect(
        payload.get("total_item_count"), preregistration.total_item_count, "total N"
    )
    _expect(payload.get("calibration_item_count"), CALIBRATION_COUNT, "calibration N")
    _expect(
        payload.get("locked_test_item_count"),
        preregistration.locked_item_count,
        "locked N",
    )
    items = _list(payload.get("items"), "D07 items")
    _expect(len(items), preregistration.total_item_count, "item row count")
    by_id: dict[str, Mapping[str, Any]] = {}
    calibration_count = 0
    locked_count = 0
    for index, raw_item in enumerate(items):
        item = _mapping(raw_item, f"D07 item {index}")
        _expect_keys(item, ITEM_KEYS, f"D07 item {index}")
        item_id = _sha256(item.get("item_id_sha256"), f"item {index} ID")
        if item_id in by_id:
            raise D07ContractError("duplicate D07 item ID")
        by_id[item_id] = item
        _sha256(item.get("speaker_id_sha256"), f"item {index} speaker")
        _sha256(item.get("audio_object_id_sha256"), f"item {index} audio object")
        split = _string(item.get("split"), f"item {index} split")
        if split == "calibration":
            calibration_count += 1
        elif split == "locked_test":
            locked_count += 1
        else:
            raise D07ContractError("invalid D07 split")
        for field in (
            "l1_stratum_id",
            "gender_stratum_id",
            "proficiency_stratum_id",
            "utterance_length_bin_id",
            "target_phone_stratum_id",
        ):
            _safe_id(item.get(field), f"item {index} {field}")
        _boolean(item.get("existing_error_present"), f"item {index} existing error")
        _integer(item.get("target_phone_count"), f"item {index} phone count", minimum=1)
        _sha256(
            item.get("review_order_commitment_sha256"),
            f"item {index} order commitment",
        )
        for field in (
            "model_output_exposed",
            "system_condition_exposed",
            "g04_item_reused",
            "s203_item_reused",
        ):
            _expect(item.get(field), False, f"item {index} {field}")
    _expect(calibration_count, CALIBRATION_COUNT, "calibration row count")
    _expect(locked_count, preregistration.locked_item_count, "locked row count")
    return ItemManifestEvidence(
        sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        items=by_id,
        calibration_count=calibration_count,
        locked_count=locked_count,
    )


def validate_rating_manifest_payload(
    payload: Mapping[str, Any],
    *,
    items: ItemManifestEvidence,
    reviewer: ReviewerEvidence,
    preregistration: PreregistrationEvidence,
) -> Mapping[str, Any]:
    _reject_nonfinite_tree(payload, "D07 rating manifest")
    _reject_sensitive_payload(payload, "D07 rating manifest")
    keys = {
        "schema_version",
        "manifest_id",
        "split",
        "item_manifest_sha256",
        "reviewer_attestation_sha256",
        "preregistration_sha256",
        "rubric_sha256",
        "primary_definition_sha256",
        "privacy_zone",
        "original_ratings_preserved",
        "rating_count",
        "ratings",
    }
    _expect_keys(payload, keys, "D07 rating manifest")
    _expect(payload.get("schema_version"), RATING_SCHEMA_VERSION, "rating schema")
    manifest_id = _string(payload.get("manifest_id"), "rating manifest ID")
    if not re.fullmatch(r"d07ratings_[0-9a-f]{32}", manifest_id):
        raise D07ContractError("invalid rating manifest ID")
    split = _string(payload.get("split"), "rating split")
    if split not in ("calibration", "locked_test"):
        raise D07ContractError("invalid rating split")
    _expect(payload.get("item_manifest_sha256"), items.sha256, "rating item binding")
    _expect(
        payload.get("reviewer_attestation_sha256"),
        reviewer.sha256,
        "rating reviewer binding",
    )
    _expect(
        payload.get("preregistration_sha256"),
        preregistration.sha256,
        "rating prereg binding",
    )
    _expect(
        payload.get("rubric_sha256"), preregistration.rubric_sha256, "rating rubric"
    )
    _expect(
        payload.get("primary_definition_sha256"),
        preregistration.primary_definition_sha256,
        "rating primary definition",
    )
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "rating zone",
    )
    _expect(payload.get("original_ratings_preserved"), True, "original preservation")
    ratings = _list(payload.get("ratings"), "ratings")
    _expect(payload.get("rating_count"), len(ratings), "rating count")
    seen_rating_ids: set[str] = set()
    roles_by_unit: dict[tuple[str, int], set[str]] = {}
    for index, raw_rating in enumerate(ratings):
        rating = _mapping(raw_rating, f"rating {index}")
        _expect_keys(rating, RATING_KEYS, f"rating {index}")
        rating_id = _string(rating.get("rating_id"), f"rating {index} ID")
        if not re.fullmatch(r"d07rating_[0-9a-f]{32}", rating_id):
            raise D07ContractError("invalid D07 rating ID")
        if rating_id in seen_rating_ids:
            raise D07ContractError("duplicate D07 rating ID")
        seen_rating_ids.add(rating_id)
        item_id = _sha256(rating.get("item_id_sha256"), f"rating {index} item")
        item = items.items.get(item_id)
        if item is None or item.get("split") != split:
            raise D07ContractError("rating references unknown or wrong-split item")
        phone_index = _integer(
            rating.get("target_phone_index"),
            f"rating {index} phone index",
            minimum=0,
        )
        if phone_index >= _integer(item.get("target_phone_count"), "item phone count"):
            raise D07ContractError("rating phone index is out of bounds")
        role = _string(rating.get("reviewer_role"), f"rating {index} role")
        if role not in REVIEWER_ROLES:
            raise D07ContractError("invalid reviewer role")
        _expect(
            rating.get("reviewer_id_sha256"),
            reviewer.reviewer_ids_by_role[role],
            "rating reviewer/role binding",
        )
        if rating.get("phone_realization") not in PHONE_REALIZATIONS:
            raise D07ContractError("invalid phone realization")
        if rating.get("error_type") not in ERROR_TYPES:
            raise D07ContractError("invalid error type")
        if rating.get("boundary_acceptable") not in BOUNDARY_VALUES:
            raise D07ContractError("invalid boundary judgment")
        for field in (
            "pronunciation_score",
            "comprehensibility_score",
            "fluency_score",
        ):
            _integer(rating.get(field), f"rating {index} {field}", minimum=1, maximum=5)
        _integer(
            rating.get("confidence"), f"rating {index} confidence", minimum=1, maximum=4
        )
        originals = _list(
            rating.get("original_rating_ids_sha256"),
            f"rating {index} original links",
        )
        unit = (item_id, phone_index)
        unit_roles = roles_by_unit.setdefault(unit, set())
        if role in unit_roles:
            raise D07ContractError("duplicate reviewer role for rating unit")
        unit_roles.add(role)
        if role in PRIMARY_ROLES:
            _expect(originals, [], "primary original links")
            _expect(
                rating.get("disagreement_receipt_sha256"), None, "primary disagreement"
            )
            _expect(rating.get("adjudication_reason_code"), None, "primary reason")
            _expect(rating.get("adjudication_note"), None, "primary note")
            _expect(
                rating.get("independent_original_rating"), True, "primary independence"
            )
            _expect(rating.get("peer_rating_seen"), False, "primary peer blindness")
        else:
            if len(originals) != 2 or len(set(originals)) != 2:
                raise D07ContractError("adjudication must retain two original links")
            for original in originals:
                _sha256(original, "adjudication original rating SHA")
            _sha256(
                rating.get("disagreement_receipt_sha256"),
                "adjudication disagreement receipt",
            )
            _safe_id(rating.get("adjudication_reason_code"), "adjudication reason")
            _validate_note(
                rating.get("adjudication_note"),
                maximum=preregistration.note_max_characters,
            )
            _expect(
                rating.get("independent_original_rating"), False, "adjudication role"
            )
            _expect(rating.get("peer_rating_seen"), True, "adjudication peer access")
        _expect(rating.get("model_output_seen"), False, "model blindness")
        _expect(rating.get("system_condition_seen"), False, "system blindness")
        _sha256(rating.get("rating_receipt_sha256"), "rating receipt")
    for unit, roles in roles_by_unit.items():
        if not set(PRIMARY_ROLES).issubset(roles):
            raise D07ContractError(f"rating unit lacks two primary originals: {unit}")
    return payload


def _validate_score_agreement(payload: Mapping[str, Any], name: str) -> None:
    _expect_keys(payload, {"qwk", "icc_2_1", "mae", "score_distribution"}, name)
    _signed(payload.get("qwk"), f"{name} QWK")
    _signed(payload.get("icc_2_1"), f"{name} ICC")
    _decimal(payload.get("mae"), f"{name} MAE", minimum=Decimal(0), maximum=Decimal(4))
    distribution = _list(payload.get("score_distribution"), f"{name} distribution")
    _expect(len(distribution), 5, f"{name} distribution length")
    for index, count in enumerate(distribution):
        _integer(count, f"{name} distribution {index}", minimum=0)


def validate_calibration_report_payload(
    payload: Mapping[str, Any],
    *,
    preregistration: PreregistrationEvidence,
) -> Mapping[str, Any]:
    _reject_nonfinite_tree(payload, "D07 calibration report")
    _reject_sensitive_payload(payload, "D07 calibration report")
    keys = {
        "schema_version",
        "report_id",
        "item_manifest_sha256",
        "rating_manifest_sha256",
        "reviewer_attestation_sha256",
        "preregistration_sha256",
        "privacy_zone",
        "calibration_item_count",
        "independent_rater_count",
        "adjudicator_count",
        "two_original_ratings_retained",
        "adjudication_excluded_from_agreement",
        "phone_agreement",
        "score_agreement",
        "ontology_agreement",
        "gate",
        "decision",
    }
    _expect_keys(payload, keys, "D07 calibration report")
    _expect(
        payload.get("schema_version"),
        CALIBRATION_SCHEMA_VERSION,
        "calibration schema",
    )
    report_id = _string(payload.get("report_id"), "calibration report ID")
    if not re.fullmatch(r"d07cal_[0-9a-f]{32}", report_id):
        raise D07ContractError("invalid calibration report ID")
    for field in (
        "item_manifest_sha256",
        "rating_manifest_sha256",
        "reviewer_attestation_sha256",
    ):
        _sha256(payload.get(field), f"calibration report {field}")
    _expect(
        payload.get("preregistration_sha256"),
        preregistration.sha256,
        "calibration prereg binding",
    )
    _expect(payload.get("privacy_zone"), "A1_aggregate_only", "calibration zone")
    _expect(payload.get("calibration_item_count"), 800, "calibration count")
    _expect(payload.get("independent_rater_count"), 2, "primary reviewer count")
    _expect(payload.get("adjudicator_count"), 1, "adjudicator count")
    _expect(payload.get("two_original_ratings_retained"), True, "original ratings")
    _expect(
        payload.get("adjudication_excluded_from_agreement"),
        True,
        "agreement originals only",
    )
    phone = _mapping(payload.get("phone_agreement"), "phone agreement")
    _expect_keys(phone, {"macro_f1", "cohens_kappa", "prevalence"}, "phone agreement")
    _unit(phone.get("macro_f1"), "phone macro F1")
    _signed(phone.get("cohens_kappa"), "phone kappa")
    _unit(phone.get("prevalence"), "phone prevalence")
    scores = _mapping(payload.get("score_agreement"), "score agreement")
    _expect_keys(
        scores,
        {"pronunciation_score", "comprehensibility_score", "fluency_score"},
        "score agreement",
    )
    for name, raw_score in scores.items():
        _validate_score_agreement(_mapping(raw_score, name), name)
    ontology = _mapping(payload.get("ontology_agreement"), "ontology agreement")
    _expect_keys(
        ontology,
        {"macro_agreement", "krippendorff_alpha"},
        "ontology agreement",
    )
    _unit(ontology.get("macro_agreement"), "ontology macro agreement")
    _signed(ontology.get("krippendorff_alpha"), "ontology alpha")

    gate = _mapping(payload.get("gate"), "calibration gate")
    _expect(
        gate.get("scalar_statistic_id"),
        preregistration.scalar_gate_statistic,
        "scalar gate statistic",
    )
    _expect(
        gate.get("taxonomy_statistic_id"),
        preregistration.taxonomy_gate_statistic,
        "taxonomy gate statistic",
    )
    scalar = _signed(gate.get("scalar_value"), "scalar gate value")
    taxonomy = _signed(gate.get("taxonomy_value"), "taxonomy gate value")
    _expect(gate.get("scalar_threshold"), 0.7, "scalar threshold")
    _expect(gate.get("taxonomy_threshold"), 0.65, "taxonomy threshold")
    _expect(
        gate.get("combination"),
        "scalar_gte_0_70_or_taxonomy_gte_0_65",
        "gate combination",
    )
    passed = (
        scalar >= SCALAR_AGREEMENT_THRESHOLD or taxonomy >= TAXONOMY_AGREEMENT_THRESHOLD
    )
    decision = _mapping(payload.get("decision"), "calibration decision")
    _expect(decision.get("agreement_gate_passed"), passed, "agreement decision")
    if passed:
        expected = {
            "agreement_gate_passed": True,
            "status": "passed_ready_for_locked_authorization",
            "rubric_retraining_required": False,
            "separate_resample_required": False,
            "failed_sample_may_be_reused": False,
            "rubric_frozen_for_locked_test": True,
            "primary_definition_frozen_for_locked_test": True,
            "eligible_for_locked_authorization": True,
            "locked_test_rating_allowed_before_g04_and_one_shot_authorization": False,
        }
    else:
        expected = {
            "agreement_gate_passed": False,
            "status": "failed_retrain_and_resample",
            "rubric_retraining_required": True,
            "separate_resample_required": True,
            "failed_sample_may_be_reused": False,
            "rubric_frozen_for_locked_test": False,
            "primary_definition_frozen_for_locked_test": False,
            "eligible_for_locked_authorization": False,
            "locked_test_rating_allowed_before_g04_and_one_shot_authorization": False,
        }
    _expect(dict(decision), expected, "calibration decision")
    return payload


def validate_g04_completion_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    _reject_nonfinite_tree(payload, "G04 completion")
    _reject_sensitive_payload(payload, "G04 completion")
    keys = {
        "schema_version",
        "status",
        "aggregate_only",
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
        "locked_access_count",
        "expected_count",
        "completed_count",
        "correct_count",
        "alignment_accuracy",
        "scorer_status",
        "privacy_safe_report_sha256",
        "reviewer_qualification_confirmed",
        "research_lead_approved",
    }
    _expect_keys(payload, keys, "G04 completion")
    _expect(payload.get("schema_version"), G04_COMPLETION_SCHEMA_VERSION, "G04 schema")
    _expect(payload.get("status"), "passed", "G04 status")
    _expect(payload.get("aggregate_only"), True, "G04 aggregate")
    for field in (
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
    ):
        _expect(payload.get(field), False, f"G04 {field}")
    _expect(payload.get("locked_access_count"), 0, "G04 locked access")
    _expect(payload.get("expected_count"), 240, "G04 expected count")
    _expect(payload.get("completed_count"), 240, "G04 completed count")
    correct = _integer(
        payload.get("correct_count"), "G04 correct", minimum=228, maximum=240
    )
    accuracy = _unit(payload.get("alignment_accuracy"), "G04 accuracy")
    _expect(accuracy, Decimal(correct) / Decimal(240), "G04 accuracy/count")
    if accuracy < Decimal("0.95"):
        raise D07ContractError("G04 accuracy gate failed")
    _expect(payload.get("scorer_status"), "passed", "G04 scorer")
    _sha256(payload.get("privacy_safe_report_sha256"), "G04 report SHA")
    _expect(payload.get("reviewer_qualification_confirmed"), True, "G04 reviewer")
    _expect(payload.get("research_lead_approved"), True, "G04 research lead")
    return payload


def validate_locked_authorization_payload(
    payload: Mapping[str, Any],
    *,
    preregistration: PreregistrationEvidence,
    item_manifest_sha256: str,
    calibration_report_sha256: str,
    g04_completion_sha256: str,
) -> Mapping[str, Any]:
    _reject_nonfinite_tree(payload, "D07 locked authorization")
    _reject_sensitive_payload(payload, "D07 locked authorization")
    keys = {
        "schema_version",
        "status",
        "preregistration_sha256",
        "item_manifest_sha256",
        "calibration_report_sha256",
        "g04_completion_attestation_sha256",
        "rubric_sha256",
        "primary_definition_sha256",
        "calibration_agreement_gate_passed",
        "g04_completed_as_separate_stage",
        "rubric_frozen",
        "primary_definition_frozen",
        "prior_expert_locked_access_transaction_count",
        "authorized_expert_locked_access_transaction_count",
        "developer_locked_access_authorized",
        "model_result_access_authorized",
        "one_shot_transaction_id_sha256",
        "authorization_receipt_sha256",
        "privacy_zone",
    }
    _expect_keys(payload, keys, "D07 locked authorization")
    _expect(
        payload.get("schema_version"),
        LOCKED_AUTHORIZATION_SCHEMA_VERSION,
        "locked authorization schema",
    )
    _expect(payload.get("status"), "authorized_one_shot", "locked authorization status")
    _expect(
        payload.get("preregistration_sha256"), preregistration.sha256, "locked prereg"
    )
    _expect(payload.get("item_manifest_sha256"), item_manifest_sha256, "locked items")
    _expect(
        payload.get("calibration_report_sha256"),
        calibration_report_sha256,
        "locked calibration",
    )
    _expect(
        payload.get("g04_completion_attestation_sha256"),
        g04_completion_sha256,
        "locked G04",
    )
    _expect(
        payload.get("rubric_sha256"), preregistration.rubric_sha256, "locked rubric"
    )
    _expect(
        payload.get("primary_definition_sha256"),
        preregistration.primary_definition_sha256,
        "locked primary",
    )
    for field in (
        "calibration_agreement_gate_passed",
        "g04_completed_as_separate_stage",
        "rubric_frozen",
        "primary_definition_frozen",
    ):
        _expect(payload.get(field), True, f"locked authorization {field}")
    _expect(
        payload.get("prior_expert_locked_access_transaction_count"),
        0,
        "prior expert locked access",
    )
    _expect(
        payload.get("authorized_expert_locked_access_transaction_count"),
        1,
        "authorized expert locked access",
    )
    _expect(
        payload.get("developer_locked_access_authorized"), False, "developer access"
    )
    _expect(payload.get("model_result_access_authorized"), False, "model access")
    _sha256(payload.get("one_shot_transaction_id_sha256"), "one-shot transaction ID")
    _sha256(payload.get("authorization_receipt_sha256"), "authorization receipt")
    _expect(
        payload.get("privacy_zone"), "C1_public_authorization", "authorization zone"
    )
    if _contains_tbd(payload):
        raise D07ContractError("locked authorization contains TBD")
    return payload


def validate_locked_report_payload(
    payload: Mapping[str, Any],
    *,
    preregistration: PreregistrationEvidence,
) -> Mapping[str, Any]:
    _reject_nonfinite_tree(payload, "D07 locked report")
    _reject_sensitive_payload(payload, "D07 locked report")
    keys = {
        "schema_version",
        "report_id",
        "item_manifest_sha256",
        "rating_manifest_sha256",
        "calibration_report_sha256",
        "g04_completion_attestation_sha256",
        "locked_access_authorization_sha256",
        "preregistration_sha256",
        "privacy_zone",
        "locked_test_item_count",
        "expert_locked_access_transaction_count",
        "developer_locked_access_count",
        "rubric_unchanged_after_locked_start",
        "primary_definition_unchanged_after_locked_start",
        "two_original_ratings_retained",
        "disagreements_only_adjudicated",
        "model_results_unseen",
        "sensitivity_analyses",
        "statistical_contract",
    }
    _expect_keys(payload, keys, "D07 locked report")
    _expect(
        payload.get("schema_version"), LOCKED_REPORT_SCHEMA_VERSION, "locked schema"
    )
    report_id = _string(payload.get("report_id"), "locked report ID")
    if not re.fullmatch(r"d07locked_[0-9a-f]{32}", report_id):
        raise D07ContractError("invalid locked report ID")
    for field in (
        "item_manifest_sha256",
        "rating_manifest_sha256",
        "calibration_report_sha256",
        "g04_completion_attestation_sha256",
        "locked_access_authorization_sha256",
    ):
        _sha256(payload.get(field), f"locked report {field}")
    _expect(
        payload.get("preregistration_sha256"),
        preregistration.sha256,
        "locked report prereg",
    )
    _expect(payload.get("privacy_zone"), "A1_aggregate_only", "locked report zone")
    _expect(
        payload.get("locked_test_item_count"),
        preregistration.locked_item_count,
        "locked report count",
    )
    _expect(payload.get("expert_locked_access_transaction_count"), 1, "expert access")
    _expect(payload.get("developer_locked_access_count"), 0, "developer access")
    for field in (
        "rubric_unchanged_after_locked_start",
        "primary_definition_unchanged_after_locked_start",
        "two_original_ratings_retained",
        "disagreements_only_adjudicated",
        "model_results_unseen",
    ):
        _expect(payload.get(field), True, f"locked report {field}")
    analyses = _list(payload.get("sensitivity_analyses"), "sensitivity analyses")
    _expect(len(analyses), 3, "sensitivity analysis count")
    observed_bases: list[str] = []
    for index, raw_analysis in enumerate(analyses):
        analysis = _mapping(raw_analysis, f"sensitivity {index}")
        _expect_keys(
            analysis,
            {"analysis_basis", "metric_definition_sha256", "aggregate_result_sha256"},
            f"sensitivity {index}",
        )
        basis = _string(analysis.get("analysis_basis"), f"sensitivity {index} basis")
        observed_bases.append(basis)
        _sha256(analysis.get("metric_definition_sha256"), "sensitivity metric")
        _sha256(analysis.get("aggregate_result_sha256"), "sensitivity result")
    _expect(tuple(observed_bases), SENSITIVITY_BASES, "sensitivity basis order")
    statistics = _mapping(payload.get("statistical_contract"), "locked statistics")
    _expect_keys(
        statistics,
        {
            "confidence_interval_method_sha256",
            "multiplicity_method_sha256",
            "minimum_support_rule_sha256",
            "cluster_unit_sha256",
            "slice_allocation_weighting_sha256",
        },
        "locked statistics",
    )
    for field in statistics:
        _sha256(statistics.get(field), f"locked statistics {field}")
    return payload


def validate_completion_attestation_payload(
    payload: Mapping[str, Any],
    *,
    preregistration: PreregistrationEvidence,
) -> Mapping[str, Any]:
    _reject_nonfinite_tree(payload, "D07 completion")
    _reject_sensitive_payload(payload, "D07 completion")
    keys = {
        "schema_version",
        "status",
        "aggregate_only",
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
        "locked_access_count",
        "calibration_count",
        "locked_gold_count",
        "independent_rater_count",
        "adjudicator_count",
        "two_original_ratings_retained",
        "adjudication_completed",
        "scalar_agreement",
        "taxonomy_agreement",
        "agreement_gate_passed",
        "label_definition_frozen",
        "primary_definition_frozen",
        "rubric_unchanged_after_locked_start",
        "model_results_unseen",
        "research_lead_approved",
        "calibration_gold_sha256",
        "sealed_locked_gold_sha256",
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "d07_institutional_approval_sha256",
        "reviewer_attestation_sha256",
        "preregistration_sha256",
        "calibration_report_sha256",
        "g04_completion_attestation_sha256",
        "locked_access_authorization_sha256",
        "locked_evaluation_report_sha256",
        "expert_locked_access_transaction_count",
        "sensitivity_analysis_completed",
    }
    _expect_keys(payload, keys, "D07 completion")
    _expect(
        payload.get("schema_version"), COMPLETION_SCHEMA_VERSION, "completion schema"
    )
    _expect(payload.get("status"), "completed", "completion status")
    _expect(payload.get("aggregate_only"), True, "completion aggregate")
    for field in (
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
    ):
        _expect(payload.get(field), False, f"completion {field}")
    _expect(payload.get("locked_access_count"), 0, "S108 developer locked access")
    _expect(payload.get("calibration_count"), 800, "completion calibration count")
    _expect(
        payload.get("locked_gold_count"),
        preregistration.locked_item_count,
        "completion locked count",
    )
    _expect(payload.get("independent_rater_count"), 2, "completion rater count")
    _expect(payload.get("adjudicator_count"), 1, "completion adjudicator count")
    for field in (
        "two_original_ratings_retained",
        "adjudication_completed",
        "agreement_gate_passed",
        "label_definition_frozen",
        "primary_definition_frozen",
        "rubric_unchanged_after_locked_start",
        "model_results_unseen",
        "research_lead_approved",
        "sensitivity_analysis_completed",
    ):
        _expect(payload.get(field), True, f"completion {field}")
    scalar = _signed(payload.get("scalar_agreement"), "completion scalar agreement")
    taxonomy = _signed(
        payload.get("taxonomy_agreement"), "completion taxonomy agreement"
    )
    if scalar < SCALAR_AGREEMENT_THRESHOLD and taxonomy < TAXONOMY_AGREEMENT_THRESHOLD:
        raise D07ContractError("completion agreement gate failed")
    _expect(
        payload.get("preregistration_sha256"),
        preregistration.sha256,
        "completion prereg",
    )
    for field in (
        "calibration_gold_sha256",
        "sealed_locked_gold_sha256",
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "d07_institutional_approval_sha256",
        "reviewer_attestation_sha256",
        "calibration_report_sha256",
        "g04_completion_attestation_sha256",
        "locked_access_authorization_sha256",
        "locked_evaluation_report_sha256",
    ):
        _sha256(payload.get(field), f"completion {field}")
    _expect(payload.get("expert_locked_access_transaction_count"), 1, "expert access")
    return payload


def _read_institutional_approval(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    h00: H00Evidence,
    audit: AccessAudit,
) -> InstitutionalApprovalEvidence:
    section = _mapping(
        config.get("institutional_expert_approval"),
        "institutional_expert_approval",
    )
    expected_sha = _string(section.get("approved_artifact_sha256"), "D07 approval SHA")
    if expected_sha == TBD_EXTERNAL_ATTESTATION:
        raise D07ContractError(
            "D07 institutional approval is TBD_EXTERNAL_ATTESTATION",
            stage="institutional_approval",
            audit=audit,
        )
    _sha256(expected_sha, "D07 approval SHA")
    audit.institutional_approval_path_resolution_count += 1
    audit.institutional_approval_read_attempt_count += 1
    payload, snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            section.get("approved_artifact_path"), "D07 approval path"
        ),
        expected_sha256=expected_sha,
        name="D07 institutional approval",
    )
    audit.institutional_approval_read_count += 1
    validate_institutional_approval_payload(payload, h00=h00)
    _verify_snapshot(workspace_root, snapshot)
    return InstitutionalApprovalEvidence(sha256=snapshot.sha256, payload=payload)


def _read_reviewers(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    approval: InstitutionalApprovalEvidence,
    audit: AccessAudit,
) -> ReviewerEvidence:
    section = _mapping(config.get("reviewer_attestations"), "reviewers")
    expected_sha = _string(section.get("approved_artifact_sha256"), "reviewer SHA")
    if expected_sha == TBD_EXTERNAL_ATTESTATION:
        raise D07ContractError(
            "D07 reviewer attestation is TBD_EXTERNAL_ATTESTATION",
            stage="reviewer_attestation",
            audit=audit,
        )
    _sha256(expected_sha, "reviewer SHA")
    audit.reviewer_attestation_path_resolution_count += 1
    audit.reviewer_attestation_read_attempt_count += 1
    payload, snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            section.get("approved_artifact_path"), "reviewer path"
        ),
        expected_sha256=expected_sha,
        name="D07 reviewer attestation",
    )
    audit.reviewer_attestation_read_count += 1
    evidence = validate_reviewer_attestation_payload(
        payload,
        institutional_approval_sha256=approval.sha256,
    )
    _expect(evidence.sha256, snapshot.sha256, "reviewer canonical/file SHA")
    _verify_snapshot(workspace_root, snapshot)
    return evidence


def _read_preregistration(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    h00: H00Evidence,
    approval: InstitutionalApprovalEvidence,
    reviewer: ReviewerEvidence,
    audit: AccessAudit,
) -> PreregistrationEvidence:
    section = _mapping(config.get("preregistration"), "preregistration")
    expected_sha = _string(section.get("approved_artifact_sha256"), "prereg SHA")
    if expected_sha == TBD_PREREGISTRATION:
        raise D07ContractError(
            "D07 preregistration is TBD_PREREGISTRATION",
            stage="preregistration",
            audit=audit,
        )
    _sha256(expected_sha, "prereg SHA")
    audit.preregistration_path_resolution_count += 1
    audit.preregistration_read_attempt_count += 1
    payload, snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(section.get("approved_artifact_path"), "prereg path"),
        expected_sha256=expected_sha,
        name="D07 preregistration",
    )
    audit.preregistration_read_count += 1
    g04 = _mapping(config.get("g04_separation"), "g04 separation")
    ontology_sha = _mapping(config.get("rating_contract"), "rating contract").get(
        "error_type_ontology_sha256"
    )
    evidence = validate_preregistration_payload(
        payload,
        h00=h00,
        institutional_approval_sha256=approval.sha256,
        reviewer=reviewer,
        g04_progress_sha256=_sha256(
            g04.get("progress_aggregate_sha256"),
            "G04 progress SHA",
        ),
        ontology_sha256=_sha256(ontology_sha, "ontology SHA"),
    )
    _expect(evidence.sha256, snapshot.sha256, "prereg canonical/file SHA")
    _verify_snapshot(workspace_root, snapshot)
    return evidence


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Validate external authorization and stop before D1/R1/R0 paths."""

    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
        audit=audit,
    )
    _verify_snapshots(workspace_root, static.source_snapshots, audit)
    h00 = _deep_validate_h00(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    approval = _read_institutional_approval(
        workspace_root=workspace_root,
        config=config,
        h00=h00,
        audit=audit,
    )
    reviewer = _read_reviewers(
        workspace_root=workspace_root,
        config=config,
        approval=approval,
        audit=audit,
    )
    prereg = _read_preregistration(
        workspace_root=workspace_root,
        config=config,
        h00=h00,
        approval=approval,
        reviewer=reviewer,
        audit=audit,
    )
    return PreflightResult(
        status="ready_for_separate_registered_d07_calibration_executor",
        config_sha256=static.config_sha256,
        h00_approval_sha256=h00.approval_sha256,
        h00_attestation_sha256=h00.attestation_sha256,
        institutional_approval_sha256=approval.sha256,
        reviewer_attestation_sha256=reviewer.sha256,
        preregistration_sha256=prereg.sha256,
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
        "g04_current_completed_count": 0,
        "g04_expected_count": 240,
        "actual_expert_count": 0,
        "actual_rating_count": 0,
        "actual_locked_access_count": 0,
        "actual_metric_count": 0,
        "actual_result_count": 0,
        "artifact_created": False,
        "d07_completion_claim_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: D07ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "g04_current_completed_count": 0,
        "g04_expected_count": 240,
        "actual_expert_count": 0,
        "actual_rating_count": 0,
        "actual_locked_access_count": 0,
        "actual_metric_count": 0,
        "actual_result_count": 0,
        "artifact_created": False,
        "d07_completion_claim_allowed": False,
        "publication_allowed": False,
    }
