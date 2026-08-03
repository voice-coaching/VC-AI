"""Fail-closed C03 expert coaching audit readiness contract.

This module is deliberately not an audit executor.  Config-only mode reads C1
source pins and validates policy/templates/schemas.  Full preflight enforces
this predecessor order:

1. actual S107 completion and threshold evidence;
2. actual C02 D1 deterministic-coaching bundle;
3. institutional and expert authorization;
4. reviewer qualification/blinding attestations;
5. a frozen preregistration with every design TBD resolved.

Only after all five stages may a separate registered executor resolve audit
items, R0 audio, R1 display packets, ratings, metrics, or output paths.
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
TBD_S107_COMPLETION = "TBD_S107_COMPLETION"
TBD_C02_COMPLETION = "TBD_C02_COMPLETION"
TBD_EXTERNAL_ATTESTATION = "TBD_EXTERNAL_ATTESTATION"
TBD_PREREGISTRATION = "TBD_PREREGISTRATION"
TBD_ATTESTATION = "TBD_ATTESTATION"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

ITEM_SCHEMA_VERSION = "voice_coach.c03_expert_audit_item_manifest.v1"
RATING_SCHEMA_VERSION = "voice_coach.c03_expert_rating_manifest.v1"
REPORT_SCHEMA_VERSION = "voice_coach.c03_expert_audit_report.v1"
APPROVAL_SCHEMA_VERSION = "voice_coach.c03_institutional_expert_approval.v1"
REVIEWER_SCHEMA_VERSION = "voice_coach.c03_reviewer_attestation_manifest.v1"
PREREG_SCHEMA_VERSION = "voice_coach.c03_expert_audit_preregistration.v1"
C02_COMMIT_SCHEMA_VERSION = "voice_coach.c02_coaching_bundle_commit.v1"
C02_BUNDLE_SCHEMA_VERSION = "voice_coach.c02_actual_d1_coaching_bundle.v1"

PRIMARY_SAMPLE_COUNT = 200
LOCATION_THRESHOLD = Decimal("0.90")
TYPE_THRESHOLD = Decimal("0.90")
UNSUPPORTED_THRESHOLD = Decimal("0.02")
STRATIFICATION_AXES = ("l1", "error_type", "confidence_bin")
REVIEWER_VISIBLE_FIELDS = ("audio", "target_sentence", "top1_advice")
ZERO_TOLERANCE_FIELDS = (
    "dangerous_expression_count",
    "discriminatory_expression_count",
    "speaker_identity_inference_count",
    "medical_judgment_count",
)

EXPECTED_EXECUTION_ORDER = (
    "validate_c03_static_config_sources_templates_and_schemas",
    "require_exact_s107_completed_commit_pins_before_path_resolution",
    "deep_validate_actual_s107_completion_and_threshold_report",
    "require_exact_c02_completed_commit_and_d1_bundle_pins_before_path_resolution",
    "deep_validate_actual_c02_d1_deterministic_coaching_bundle",
    "validate_institutional_and_expert_approval",
    "validate_reviewer_attestations_and_blinding",
    "validate_frozen_c03_preregistration_without_tbd",
    "stop_before_audit_item_audio_rating_metric_or_output_resolution",
)

SOURCE_PATHS = (
    "configs/coaching/c03_expert_audit_item_manifest_v1.schema.json",
    "configs/coaching/c03_expert_audit_preregistration_v1.template.json",
    "configs/coaching/c03_expert_audit_report_v1.schema.json",
    "configs/coaching/c03_expert_rating_manifest_v1.schema.json",
    "configs/coaching/c03_institutional_expert_approval_v1.template.json",
    "configs/coaching/c03_reviewer_attestation_v1.template.json",
    "configs/coaching/c02_coaching_selection_output_v1.schema.json",
    "configs/coaching/c02_coaching_selection_preregistration_v1.template.json",
    "configs/coaching/c02_diagnostic_issues_input_v1.schema.json",
    "configs/experiments/c02_deterministic_coaching_selection_v1.yaml",
    "configs/experiments/wavlm_s107_calibration_v1.yaml",
    "docs/C01_KOREAN_ERROR_ADVICE_ONTOLOGY_DATA_CARD_KO.md",
    "docs/C02_COACHING_SELECTION_DATA_MODEL_SAFETY_CARD_KO.md",
    "docs/C02_DETERMINISTIC_COACHING_SELECTION_READINESS_KO.md",
    "docs/DATA_PRIVACY_BOUNDARY_KO.md",
    "docs/EXTERNAL_EVALUATION_EXECUTION_PACK_KO.md",
    "docs/WAVLM_S107_CALIBRATION_READINESS_KO.md",
    "scripts/prepare_c03_expert_coaching_audit.py",
    "src/voice_coach/coaching/c02_selection_contract.py",
    "src/voice_coach/coaching/c03_expert_audit_contract.py",
    "src/voice_coach/training/wavlm_s107_calibration_training.py",
)

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_s107",
    "predecessor_c02",
    "institutional_expert_approval",
    "reviewer_attestations",
    "preregistration",
    "sampling_and_blinding",
    "gates",
    "privacy_and_safety",
    "execution_order",
    "outputs",
}

ITEM_KEYS = {
    "audit_item_id",
    "sample_role",
    "c02_input_manifest_sha256",
    "c02_output_manifest_sha256",
    "audio_object_id_sha256",
    "target_sentence_object_id_sha256",
    "top1_advice_object_id_sha256",
    "l1_stratum_id",
    "error_type_stratum_id",
    "confidence_bin_id",
    "coaching_action",
    "review_packet_sha256",
    "reviewer_visible_fields",
    "model_score_exposed_to_reviewer",
    "source_condition_exposed_to_reviewer",
}

RATING_KEYS = {
    "rating_id",
    "audit_item_id",
    "reviewer_id_sha256",
    "rating_role",
    "original_rating_id_sha256",
    "independent_review",
    "reviewer_visible_fields",
    "model_score_seen",
    "source_condition_seen",
    "location_accuracy",
    "error_type_accuracy",
    "advice_actionability",
    "unsupported_claim",
    "dangerous_expression",
    "discriminatory_expression",
    "speaker_identity_inference",
    "medical_judgment",
    "risk_taxonomy_sha256",
    "risk_codes",
    "rating_receipt_sha256",
}

FORBIDDEN_RECURSIVE_KEYS = {
    "audio_path",
    "audio_url",
    "audio_bytes",
    "target_sentence",
    "target_text",
    "transcript",
    "speaker_name",
    "reviewer_name",
    "email",
    "phone_number",
    "direct_identifier",
    "model_score",
    "confidence_score",
    "source_path",
    "model_path",
    "checkpoint_path",
    "prompt",
    "completion",
    "free_text",
}


class C03ContractError(ValueError):
    """Raised before an unauthorized or unfrozen C03 action."""

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
            raise C03ContractError(f"duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(slots=True)
class AccessAudit:
    """Counters proving ordering and zero protected/runtime access."""

    source_pin_read_count: int = 0
    source_toctou_verification_count: int = 0
    s107_completion_path_resolution_count: int = 0
    s107_completion_read_attempt_count: int = 0
    s107_completion_read_count: int = 0
    s107_validator_load_count: int = 0
    s107_deep_validation_count: int = 0
    s107_threshold_report_read_count: int = 0
    c02_completion_path_resolution_count: int = 0
    c02_completion_read_attempt_count: int = 0
    c02_completion_read_count: int = 0
    c02_bundle_path_resolution_count: int = 0
    c02_bundle_read_attempt_count: int = 0
    c02_bundle_read_count: int = 0
    c02_bundle_row_read_count: int = 0
    c02_deterministic_row_validation_count: int = 0
    c02_preregistration_read_count: int = 0
    approval_path_resolution_count: int = 0
    approval_read_attempt_count: int = 0
    approval_read_count: int = 0
    reviewer_attestation_path_resolution_count: int = 0
    reviewer_attestation_read_attempt_count: int = 0
    reviewer_attestation_read_count: int = 0
    preregistration_path_resolution_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    audit_item_path_resolution_count: int = 0
    audit_item_manifest_read_count: int = 0
    audit_item_row_read_count: int = 0
    audio_path_resolution_count: int = 0
    audio_read_count: int = 0
    target_sentence_read_count: int = 0
    top1_advice_read_count: int = 0
    rating_path_resolution_count: int = 0
    rating_read_count: int = 0
    adjudication_count: int = 0
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
class S107Evidence:
    c02_module: ModuleType
    c02_config: Mapping[str, Any]
    c01: Any
    s107: Any


@dataclass(frozen=True, slots=True)
class C02Evidence:
    commit_sha256: str
    bundle_sha256: str
    item_count: int


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ReviewerEvidence:
    sha256: str
    reviewer_count: int
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreregistrationEvidence:
    sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: str
    config_sha256: str
    s107_commit_sha256: str
    c02_commit_sha256: str
    approval_sha256: str
    reviewer_attestation_sha256: str
    preregistration_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise C03ContractError(f"{name} must be a string-keyed mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise C03ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise C03ContractError(f"{name} must be a non-empty string")
    return value


def _integer(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise C03ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise C03ContractError(f"{name} must be >= {minimum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise C03ContractError(f"{name} must be boolean")
    return value


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if not SHA256_RE.fullmatch(text):
        raise C03ContractError(f"{name} must be a lowercase SHA-256")
    return text


def _safe_id(value: object, name: str) -> str:
    text = _string(value, name)
    if not SAFE_ID_RE.fullmatch(text):
        raise C03ContractError(f"{name} must be a bounded safe ID")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise C03ContractError(
            f"{name} differs: expected {expected!r}, observed {actual!r}"
        )


def _expect_keys(
    payload: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(payload)
    if actual != expected:
        raise C03ContractError(
            f"{name} keys differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise C03ContractError(f"{name} must be a normalized relative path")
    if "\\" in text or "\x00" in text or text != path.as_posix():
        raise C03ContractError(f"{name} must be a normalized POSIX path")
    return text


def _rate(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise C03ContractError(f"{name} must be a finite rate")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise C03ContractError(f"{name} must be a finite rate") from exc
    if not result.is_finite() or result < 0 or result > 1:
        raise C03ContractError(f"{name} must be in [0,1]")
    return result


def _reject_nonfinite_tree(value: object, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise C03ContractError(f"{name} contains non-finite numeric value")
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
                raise C03ContractError(f"{name} contains forbidden field {key!r}")
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
        raise C03ContractError(f"cannot load C03 config: {exc}") from exc
    return _mapping(payload, "C03 config")


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
                raise C03ContractError(f"{name} missing: {relative}")
            return current
        if stat.S_ISLNK(status.st_mode):
            raise C03ContractError(f"{name} traverses symlink: {relative}")
        if not is_leaf and not stat.S_ISDIR(status.st_mode):
            raise C03ContractError(f"{name} parent is not a directory: {relative}")
        if is_leaf and require_leaf and not stat.S_ISREG(status.st_mode):
            raise C03ContractError(f"{name} is not a regular file: {relative}")
    return current


def _read_file(
    workspace_root: Path,
    relative: str,
    name: str,
) -> tuple[bytes, FileSnapshot]:
    path = _reject_symlink_chain(
        workspace_root,
        _relative_string(relative, name),
        name,
        require_leaf=True,
    )
    before = path.stat()
    data = path.read_bytes()
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
    if identity_before != identity_after or len(data) != after.st_size:
        raise C03ContractError(f"{name} changed while being read")
    return data, FileSnapshot(
        relative_path=relative,
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
        raise C03ContractError(f"{name} SHA-256 differs")
    try:
        payload = json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise C03ContractError(f"{name} must be valid JSON") from exc
    return _mapping(payload, name), snapshot


def _verify_snapshot(workspace_root: Path, snapshot: FileSnapshot) -> None:
    _, current = _read_file(
        workspace_root,
        snapshot.relative_path,
        f"snapshot {snapshot.relative_path}",
    )
    if current != snapshot:
        raise C03ContractError(f"TOCTOU drift: {snapshot.relative_path}")


def _load_module(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
) -> ModuleType:
    data, snapshot = _read_file(workspace_root, relative, "pinned C02 contract")
    if snapshot.sha256 != expected_sha256:
        raise C03ContractError("pinned C02 contract SHA-256 differs")
    module_name = "_voice_coach_c03_pinned_c02_contract"
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
        raise C03ContractError(f"cannot load pinned C02 contract: {exc}") from exc
    return module


def _validate_schema(
    payload: Mapping[str, Any],
    *,
    schema_version: str,
    name: str,
) -> None:
    _expect(
        payload.get("$schema"), "https://json-schema.org/draft/2020-12/schema", name
    )
    _expect(payload.get("type"), "object", name)
    _expect(payload.get("additionalProperties"), False, name)
    properties = _mapping(payload.get("properties"), f"{name} properties")
    version = _mapping(properties.get("schema_version"), f"{name} schema_version")
    _expect(version.get("const"), schema_version, f"{name} version")
    required = _list(payload.get("required"), f"{name} required")
    if set(required) != set(properties):
        raise C03ContractError(f"{name} root required/properties differ")


def _validate_template_placeholders(
    payload: Mapping[str, Any],
    *,
    schema_version: str,
    name: str,
) -> None:
    _expect(payload.get("schema_version"), schema_version, f"{name} schema")
    _expect(payload.get("privacy_zone"), "C1_public_template_only", f"{name} zone")
    if not _contains_tbd(payload):
        raise C03ContractError(f"{name} must retain unresolved placeholders")


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_KEYS, "C03 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "config schema")
    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(
        set(experiment),
        {
            "id",
            "work_package",
            "status",
            "implementation_scope",
            "actual_expert_audit_completed",
            "actual_audio_accessed",
            "actual_metrics_computed",
            "production_release_allowed",
        },
        "experiment keys",
    )
    _expect(experiment.get("id"), "c03-expert-coaching-audit-v1", "experiment ID")
    _expect(experiment.get("work_package"), "C03", "work package")
    _expect(experiment.get("status"), STATIC_STATUS, "experiment status")
    _expect(
        experiment.get("implementation_scope"),
        "read_only_fail_closed_static_readiness",
        "implementation scope",
    )
    for field in (
        "actual_expert_audit_completed",
        "actual_audio_accessed",
        "actual_metrics_computed",
        "production_release_allowed",
    ):
        _expect(experiment.get(field), False, f"experiment.{field}")

    canonical = _mapping(config.get("canonical_sources"), "canonical_sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_or_aggregate_static_pins_only",
        "canonical source scope",
    )
    _expect(canonical.get("strict_sha256"), True, "strict source hashes")

    s107 = _mapping(config.get("predecessor_s107"), "predecessor_s107")
    _expect(s107.get("required_actual_completion"), True, "S107 actual completion")
    _expect(
        s107.get("required_commit_schema_version"),
        "voice_coach.wavlm_s107_calibration_commit.v1",
        "S107 commit schema",
    )
    for field in ("completion_commit_path", "config_path"):
        _relative_string(s107.get(field), f"S107 {field}")
    for field in ("completion_commit_sha256", "threshold_report_sha256"):
        value = _string(s107.get(field), f"S107 {field}")
        if value != TBD_S107_COMPLETION:
            _sha256(value, f"S107 {field}")

    c02 = _mapping(config.get("predecessor_c02"), "predecessor_c02")
    _expect(c02.get("required_actual_d1_bundle"), True, "C02 actual bundle")
    _expect(
        c02.get("required_commit_schema_version"),
        C02_COMMIT_SCHEMA_VERSION,
        "C02 commit schema",
    )
    _expect(
        c02.get("required_bundle_schema_version"),
        C02_BUNDLE_SCHEMA_VERSION,
        "C02 bundle schema",
    )
    for field in ("completion_commit_path", "d1_bundle_manifest_path"):
        _relative_string(c02.get(field), f"C02 {field}")
    for field in ("completion_commit_sha256", "d1_bundle_manifest_sha256"):
        value = _string(c02.get(field), f"C02 {field}")
        if value != TBD_C02_COMPLETION:
            _sha256(value, f"C02 {field}")

    approval = _mapping(
        config.get("institutional_expert_approval"),
        "institutional_expert_approval",
    )
    _expect(
        approval.get("required_schema_version"), APPROVAL_SCHEMA_VERSION, "approval"
    )
    _relative_string(approval.get("approved_artifact_path"), "approval path")
    approval_sha = _string(approval.get("approved_artifact_sha256"), "approval SHA")
    if approval_sha != TBD_EXTERNAL_ATTESTATION:
        _sha256(approval_sha, "approval SHA")

    reviewers = _mapping(config.get("reviewer_attestations"), "reviewer_attestations")
    _expect(
        reviewers.get("required_schema_version"), REVIEWER_SCHEMA_VERSION, "reviewer"
    )
    _relative_string(reviewers.get("approved_artifact_path"), "reviewer path")
    reviewer_sha = _string(reviewers.get("approved_artifact_sha256"), "reviewer SHA")
    if reviewer_sha != TBD_EXTERNAL_ATTESTATION:
        _sha256(reviewer_sha, "reviewer SHA")
    _expect(reviewers.get("reviewer_count_n"), TBD_ATTESTATION, "reviewer N boundary")
    _expect(
        reviewers.get("qualification_requirements"),
        TBD_ATTESTATION,
        "reviewer qualification boundary",
    )

    prereg = _mapping(config.get("preregistration"), "preregistration")
    _expect(prereg.get("required_schema_version"), PREREG_SCHEMA_VERSION, "prereg")
    _relative_string(prereg.get("approved_artifact_path"), "prereg path")
    prereg_sha = _string(prereg.get("approved_artifact_sha256"), "prereg SHA")
    if prereg_sha != TBD_PREREGISTRATION:
        _sha256(prereg_sha, "prereg SHA")

    sampling = _mapping(config.get("sampling_and_blinding"), "sampling_and_blinding")
    _expect(sampling.get("primary_sample_count"), PRIMARY_SAMPLE_COUNT, "sample count")
    _expect(
        tuple(_list(sampling.get("stratification_axes"), "stratification axes")),
        STRATIFICATION_AXES,
        "stratification axes",
    )
    _expect(
        sampling.get("exact_sampling_allocation"),
        TBD_PREREGISTRATION,
        "sampling allocation boundary",
    )
    _expect(
        sampling.get("abstention_referral_separate_sample_required"), True, "abstention"
    )
    _expect(
        sampling.get("abstention_referral_exact_count_and_relation"),
        TBD_PREREGISTRATION,
        "abstention sampling boundary",
    )
    _expect(
        tuple(_list(sampling.get("reviewer_visible_fields"), "visible fields")),
        REVIEWER_VISIBLE_FIELDS,
        "visible fields",
    )
    _expect(sampling.get("model_score_visible"), False, "model score blindness")
    _expect(sampling.get("source_condition_visible"), False, "source blindness")

    gates = _mapping(config.get("gates"), "gates")
    location = _mapping(gates.get("location_accuracy"), "location gate")
    error_type = _mapping(gates.get("error_type_accuracy"), "type gate")
    unsupported = _mapping(gates.get("unsupported_claim_rate"), "unsupported gate")
    _expect(
        location, {"operator": "greater_than_or_equal", "threshold": 0.9}, "location"
    )
    _expect(error_type, {"operator": "greater_than_or_equal", "threshold": 0.9}, "type")
    _expect(unsupported, {"operator": "less_than", "threshold": 0.02}, "unsupported")
    zero = _mapping(gates.get("zero_tolerance_safety"), "zero tolerance")
    _expect(
        zero,
        {
            "dangerous_expression_max_count": 0,
            "discriminatory_expression_max_count": 0,
            "speaker_identity_inference_max_count": 0,
            "medical_judgment_max_count": 0,
            "any_event_prohibits_pass": True,
            "any_event_requires_revision_and_reaudit": True,
        },
        "zero tolerance gates",
    )

    privacy = _mapping(config.get("privacy_and_safety"), "privacy_and_safety")
    _expect(
        privacy.get("audit_item_zone"),
        "D1_pseudonymized_restricted_experiment",
        "item zone",
    )
    _expect(privacy.get("audio_zone"), "R0_raw_restricted", "audio zone")
    _expect(
        privacy.get("display_locator_zone"), "R1_restricted_locator", "locator zone"
    )
    _expect(privacy.get("aggregate_report_zone"), "A1_aggregate_only", "report zone")
    for field in (
        "direct_identifier_allowed",
        "raw_audio_export_allowed",
        "free_text_rating_allowed",
        "identity_inference_allowed",
        "medical_judgment_allowed",
        "model_gpu_network_access_in_this_package",
        "audit_execution_in_this_package",
    ):
        _expect(privacy.get(field), False, f"privacy.{field}")

    _expect(
        tuple(_list(config.get("execution_order"), "execution_order")),
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
    """Validate immutable C1 pins without resolving any predecessor/path."""

    root = workspace_root.resolve(strict=True)
    access = audit if audit is not None else AccessAudit()
    _validate_config_values(config)
    absolute_config = config_path if config_path.is_absolute() else root / config_path
    try:
        relative_config = absolute_config.relative_to(root).as_posix()
    except ValueError as exc:
        raise C03ContractError("C03 config must be inside workspace") from exc
    config_data, config_snapshot = _read_file(root, relative_config, "C03 config")
    loaded = yaml.load(config_data.decode(), Loader=_UniqueKeyLoader)
    if loaded != config:
        raise C03ContractError("C03 config changed between load and validation")

    canonical = _mapping(config.get("canonical_sources"), "canonical_sources")
    pins = _list(canonical.get("pins"), "canonical source pins")
    if len(pins) != len(SOURCE_PATHS):
        raise C03ContractError("canonical source pin count differs")
    snapshots: list[FileSnapshot] = []
    payloads: dict[str, bytes] = {}
    seen: set[str] = set()
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        if relative in seen:
            raise C03ContractError(f"duplicate source pin: {relative}")
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_sha:
            raise C03ContractError(
                f"canonical source SHA-256 drift: {relative}",
                stage="source_pin",
                audit=access,
            )
        seen.add(relative)
        snapshots.append(snapshot)
        payloads[relative] = data
    if tuple(sorted(seen)) != tuple(sorted(SOURCE_PATHS)):
        raise C03ContractError("canonical source path set differs")
    by_path = {snapshot.relative_path: snapshot for snapshot in snapshots}

    schema_specs = (
        (
            "configs/coaching/c03_expert_audit_item_manifest_v1.schema.json",
            ITEM_SCHEMA_VERSION,
            "C03 item schema",
        ),
        (
            "configs/coaching/c03_expert_rating_manifest_v1.schema.json",
            RATING_SCHEMA_VERSION,
            "C03 rating schema",
        ),
        (
            "configs/coaching/c03_expert_audit_report_v1.schema.json",
            REPORT_SCHEMA_VERSION,
            "C03 report schema",
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
            "configs/coaching/c03_institutional_expert_approval_v1.template.json",
            APPROVAL_SCHEMA_VERSION,
            "C03 approval template",
        ),
        (
            "configs/coaching/c03_reviewer_attestation_v1.template.json",
            REVIEWER_SCHEMA_VERSION,
            "C03 reviewer template",
        ),
        (
            "configs/coaching/c03_expert_audit_preregistration_v1.template.json",
            PREREG_SCHEMA_VERSION,
            "C03 preregistration template",
        ),
    )
    for path, version, name in template_specs:
        _validate_template_placeholders(
            _mapping(json.loads(payloads[path]), name),
            schema_version=version,
            name=name,
        )

    bindings = (
        (
            "predecessor_s107",
            "config_path",
            "config_sha256",
            "configs/experiments/wavlm_s107_calibration_v1.yaml",
        ),
        (
            "predecessor_c02",
            "config_path",
            "config_sha256",
            "configs/experiments/c02_deterministic_coaching_selection_v1.yaml",
        ),
        (
            "predecessor_c02",
            "contract_path",
            "contract_sha256",
            "src/voice_coach/coaching/c02_selection_contract.py",
        ),
        (
            "institutional_expert_approval",
            "template_path",
            "template_sha256",
            "configs/coaching/c03_institutional_expert_approval_v1.template.json",
        ),
        (
            "reviewer_attestations",
            "template_path",
            "template_sha256",
            "configs/coaching/c03_reviewer_attestation_v1.template.json",
        ),
        (
            "preregistration",
            "template_path",
            "template_sha256",
            "configs/coaching/c03_expert_audit_preregistration_v1.template.json",
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
    schema_bindings = {
        "item_schema_path": "configs/coaching/c03_expert_audit_item_manifest_v1.schema.json",
        "rating_schema_path": "configs/coaching/c03_expert_rating_manifest_v1.schema.json",
        "report_schema_path": "configs/coaching/c03_expert_audit_report_v1.schema.json",
    }
    for field, expected_path in schema_bindings.items():
        _expect(outputs.get(field), expected_path, f"outputs.{field}")
        _expect(
            outputs.get(field.replace("_path", "_sha256")),
            by_path[expected_path].sha256,
            f"outputs.{field} hash",
        )

    policy = {
        "sampling_and_blinding": config["sampling_and_blinding"],
        "gates": config["gates"],
        "privacy_and_safety": config["privacy_and_safety"],
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


def _deep_validate_s107(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> S107Evidence:
    section = _mapping(config.get("predecessor_s107"), "predecessor_s107")
    commit_sha = _string(section.get("completion_commit_sha256"), "S107 commit SHA")
    threshold_sha = _string(
        section.get("threshold_report_sha256"), "S107 threshold SHA"
    )
    if commit_sha == TBD_S107_COMPLETION or threshold_sha == TBD_S107_COMPLETION:
        raise C03ContractError(
            "S107 actual completion is TBD_S107_COMPLETION",
            stage="s107_completion",
            audit=audit,
        )
    _sha256(commit_sha, "S107 commit SHA")
    _sha256(threshold_sha, "S107 threshold SHA")

    c02_section = _mapping(config.get("predecessor_c02"), "predecessor_c02")
    audit.s107_validator_load_count += 1
    try:
        module = _load_module(
            workspace_root=workspace_root,
            relative=_relative_string(
                c02_section.get("contract_path"), "C02 contract path"
            ),
            expected_sha256=_sha256(
                c02_section.get("contract_sha256"), "C02 contract SHA"
            ),
        )
        c02_config_path = workspace_root / _relative_string(
            c02_section.get("config_path"),
            "C02 config path",
        )
        c02_config = module.load_config(c02_config_path)
        c02_static_audit = module.AccessAudit()
        c02_static = module.validate_static_config(
            workspace_root=workspace_root,
            config_path=c02_config_path,
            config=c02_config,
            audit=c02_static_audit,
        )
        module._verify_snapshots(
            workspace_root=workspace_root,
            snapshots=c02_static.source_snapshots,
            audit=c02_static_audit,
            stage="c03_c02_static",
        )
        c02_s107 = _mapping(c02_config.get("predecessor_s107"), "pinned C02 S107")
        _expect(
            c02_s107.get("completion_commit_sha256"),
            commit_sha,
            "C03/C02 S107 commit binding",
        )
        _expect(
            c02_s107.get("threshold_artifact_sha256"),
            threshold_sha,
            "C03/C02 S107 threshold binding",
        )
        c01 = module.deep_validate_c01(
            workspace_root=workspace_root,
            config=c02_config,
            audit=c02_static_audit,
        )
        s107 = module.deep_validate_s107(
            workspace_root=workspace_root,
            config=c02_config,
            audit=c02_static_audit,
        )
    except Exception as exc:
        raise C03ContractError(
            f"S107 deep validation through pinned C02 failed: {exc}",
            stage="s107_completion",
            audit=audit,
        ) from exc
    audit.s107_completion_path_resolution_count += (
        c02_static_audit.s107_completion_path_resolution_count
    )
    audit.s107_completion_read_attempt_count += (
        c02_static_audit.s107_completion_read_attempt_count
    )
    audit.s107_completion_read_count += c02_static_audit.s107_completion_read_count
    audit.s107_deep_validation_count += c02_static_audit.s107_deep_validation_count
    audit.s107_threshold_report_read_count += (
        c02_static_audit.s107_threshold_report_read_count
    )
    _expect(s107.commit_sha256, commit_sha, "S107 deep commit hash")
    _expect(s107.threshold_report_sha256, threshold_sha, "S107 deep threshold hash")
    return S107Evidence(
        c02_module=module,
        c02_config=c02_config,
        c01=c01,
        s107=s107,
    )


def _validate_c02_commit(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    s107: S107Evidence,
) -> None:
    keys = {
        "schema_version",
        "status",
        "c02_config_sha256",
        "c02_preregistration_sha256",
        "c01_validation_report_sha256",
        "s107_completion_commit_sha256",
        "s107_threshold_policy_report_sha256",
        "d1_bundle_manifest_path",
        "d1_bundle_manifest_sha256",
        "item_count",
        "coach_item_count",
        "abstention_referral_item_count",
        "actual_d1_bundle_completed",
        "deterministic_catalog_only_validated",
        "free_generation_llm_used",
        "model_gpu_network_accessed",
        "production_release_allowed",
    }
    _expect_keys(payload, keys, "C02 completion commit")
    _expect(
        payload.get("schema_version"), C02_COMMIT_SCHEMA_VERSION, "C02 commit schema"
    )
    _expect(payload.get("status"), "committed", "C02 commit status")
    _expect(
        payload.get("c02_config_sha256"),
        _mapping(config.get("predecessor_c02"), "predecessor_c02").get("config_sha256"),
        "C02 config binding",
    )
    _sha256(payload.get("c02_preregistration_sha256"), "C02 preregistration SHA")
    _expect(
        payload.get("c01_validation_report_sha256"),
        s107.c01.report_sha256,
        "C02 C01 binding",
    )
    _expect(
        payload.get("s107_completion_commit_sha256"),
        s107.s107.commit_sha256,
        "C02 S107 commit binding",
    )
    _expect(
        payload.get("s107_threshold_policy_report_sha256"),
        s107.s107.threshold_report_sha256,
        "C02 S107 threshold binding",
    )
    c02 = _mapping(config.get("predecessor_c02"), "predecessor_c02")
    _expect(
        payload.get("d1_bundle_manifest_path"),
        c02.get("d1_bundle_manifest_path"),
        "C02 bundle path binding",
    )
    _expect(
        payload.get("d1_bundle_manifest_sha256"),
        c02.get("d1_bundle_manifest_sha256"),
        "C02 bundle hash binding",
    )
    item_count = _integer(payload.get("item_count"), "C02 item count", minimum=1)
    coach_count = _integer(
        payload.get("coach_item_count"), "C02 coach count", minimum=0
    )
    abstention_count = _integer(
        payload.get("abstention_referral_item_count"),
        "C02 abstention/referral count",
        minimum=0,
    )
    if coach_count + abstention_count != item_count:
        raise C03ContractError("C02 action counts must sum to item count")
    for field in (
        "actual_d1_bundle_completed",
        "deterministic_catalog_only_validated",
    ):
        _expect(payload.get(field), True, f"C02 commit {field}")
    for field in (
        "free_generation_llm_used",
        "model_gpu_network_accessed",
        "production_release_allowed",
    ):
        _expect(payload.get(field), False, f"C02 commit {field}")


def _validate_c02_bundle(
    payload: Mapping[str, Any],
    *,
    commit: Mapping[str, Any],
    s107: S107Evidence,
    c02_policy: Any,
    c02_config_sha256: str,
    audit: AccessAudit,
) -> None:
    keys = {
        "schema_version",
        "bundle_id",
        "c02_config_sha256",
        "c02_preregistration_sha256",
        "c01_validation_report_sha256",
        "s107_completion_commit_sha256",
        "s107_threshold_policy_report_sha256",
        "privacy_zone",
        "item_count",
        "rows",
    }
    _expect_keys(payload, keys, "C02 D1 bundle")
    _expect(
        payload.get("schema_version"), C02_BUNDLE_SCHEMA_VERSION, "C02 bundle schema"
    )
    bundle_id = _string(payload.get("bundle_id"), "C02 bundle ID")
    if not re.fullmatch(r"c02bundle_[0-9a-f]{32}", bundle_id):
        raise C03ContractError("invalid C02 bundle ID")
    for field in (
        "c02_config_sha256",
        "c02_preregistration_sha256",
        "c01_validation_report_sha256",
        "s107_completion_commit_sha256",
        "s107_threshold_policy_report_sha256",
    ):
        _expect(payload.get(field), commit.get(field), f"C02 bundle {field}")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "C02 bundle zone",
    )
    rows = _list(payload.get("rows"), "C02 rows")
    _expect(len(rows), commit.get("item_count"), "C02 bundle item count")
    _expect(payload.get("item_count"), len(rows), "C02 declared item count")
    seen: set[str] = set()
    for index, raw_row in enumerate(rows):
        row = _mapping(raw_row, f"C02 row {index}")
        _expect_keys(
            row,
            {
                "row_id_sha256",
                "l1_stratum_id",
                "audio_object_id_sha256",
                "target_sentence_object_id_sha256",
                "input_payload",
                "output_payload",
            },
            f"C02 row {index}",
        )
        row_id = _sha256(row.get("row_id_sha256"), f"C02 row {index} ID")
        if row_id in seen:
            raise C03ContractError("duplicate C02 row ID")
        seen.add(row_id)
        _safe_id(row.get("l1_stratum_id"), f"C02 row {index} L1 stratum")
        _sha256(row.get("audio_object_id_sha256"), f"C02 row {index} audio object")
        _sha256(
            row.get("target_sentence_object_id_sha256"),
            f"C02 row {index} target object",
        )
        input_payload = _mapping(row.get("input_payload"), f"C02 row {index} input")
        output_payload = _mapping(row.get("output_payload"), f"C02 row {index} output")
        s107.c02_module.validate_diagnostic_input_payload(
            input_payload,
            policy=c02_policy,
            c01=s107.c01,
            s107=s107.s107,
        )
        s107.c02_module.validate_coaching_output_payload(
            output_payload,
            input_payload=input_payload,
            policy=c02_policy,
            c01=s107.c01,
            s107=s107.s107,
            c02_config_sha256=c02_config_sha256,
        )
        audit.c02_bundle_row_read_count += 1
        audit.c02_deterministic_row_validation_count += 1


def _deep_validate_c02(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    s107: S107Evidence,
    audit: AccessAudit,
) -> C02Evidence:
    section = _mapping(config.get("predecessor_c02"), "predecessor_c02")
    commit_sha = _string(section.get("completion_commit_sha256"), "C02 commit SHA")
    bundle_sha = _string(section.get("d1_bundle_manifest_sha256"), "C02 bundle SHA")
    if commit_sha == TBD_C02_COMPLETION or bundle_sha == TBD_C02_COMPLETION:
        raise C03ContractError(
            "C02 actual D1 bundle is TBD_C02_COMPLETION",
            stage="c02_completion",
            audit=audit,
        )
    _sha256(commit_sha, "C02 commit SHA")
    _sha256(bundle_sha, "C02 bundle SHA")
    audit.c02_completion_path_resolution_count += 1
    audit.c02_completion_read_attempt_count += 1
    try:
        commit, commit_snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("completion_commit_path"), "C02 commit path"
            ),
            expected_sha256=commit_sha,
            name="C02 completion commit",
        )
    except C03ContractError as exc:
        raise C03ContractError(
            f"C02 completion unavailable: {exc}",
            stage="c02_completion",
            audit=audit,
        ) from exc
    audit.c02_completion_read_count += 1
    _validate_c02_commit(commit, config=config, s107=s107)

    c02_audit = s107.c02_module.AccessAudit()
    try:
        c02_policy = s107.c02_module._read_approved_preregistration(
            workspace_root=workspace_root,
            config=s107.c02_config,
            c01=s107.c01,
            s107=s107.s107,
            audit=c02_audit,
        )
    except Exception as exc:
        raise C03ContractError(
            f"C02 approved preregistration unavailable: {exc}",
            stage="c02_completion",
            audit=audit,
        ) from exc
    audit.c02_preregistration_read_count += c02_audit.preregistration_read_count
    _expect(
        c02_policy.sha256,
        commit.get("c02_preregistration_sha256"),
        "C02 preregistration binding",
    )

    audit.c02_bundle_path_resolution_count += 1
    audit.c02_bundle_read_attempt_count += 1
    try:
        bundle, bundle_snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("d1_bundle_manifest_path"), "C02 bundle path"
            ),
            expected_sha256=bundle_sha,
            name="C02 D1 bundle",
        )
    except C03ContractError as exc:
        raise C03ContractError(
            f"C02 D1 bundle unavailable: {exc}",
            stage="c02_completion",
            audit=audit,
        ) from exc
    audit.c02_bundle_read_count += 1
    _validate_c02_bundle(
        bundle,
        commit=commit,
        s107=s107,
        c02_policy=c02_policy,
        c02_config_sha256=_sha256(commit.get("c02_config_sha256"), "C02 config SHA"),
        audit=audit,
    )
    _verify_snapshot(workspace_root, commit_snapshot)
    _verify_snapshot(workspace_root, bundle_snapshot)
    return C02Evidence(
        commit_sha256=commit_snapshot.sha256,
        bundle_sha256=bundle_snapshot.sha256,
        item_count=_integer(commit.get("item_count"), "C02 item count", minimum=1),
    )


def validate_approval_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a resolved institutional and expert approval without I/O."""

    _reject_nonfinite_tree(payload, "C03 approval")
    _reject_sensitive_payload(payload, "C03 approval")
    keys = {
        "schema_version",
        "approval_id",
        "status",
        "protocol_sha256",
        "institutional_authority_receipt_sha256",
        "expert_evaluation_authority_receipt_sha256",
        "audio_use_approved",
        "target_sentence_display_approved",
        "top1_advice_display_approved",
        "consent_or_lawful_basis_verified",
        "withdrawal_process_verified",
        "purpose_limitation_verified",
        "retention_schedule_verified",
        "access_control_verified",
        "reviewer_confidentiality_required",
        "reviewer_mapping_stored_separately",
        "raw_audio_export_allowed",
        "privacy_zone",
    }
    _expect_keys(payload, keys, "C03 approval")
    _expect(payload.get("schema_version"), APPROVAL_SCHEMA_VERSION, "approval schema")
    _safe_id(payload.get("approval_id"), "approval ID")
    _expect(payload.get("status"), "approved", "approval status")
    for field in (
        "protocol_sha256",
        "institutional_authority_receipt_sha256",
        "expert_evaluation_authority_receipt_sha256",
    ):
        _sha256(payload.get(field), f"approval {field}")
    for field in (
        "audio_use_approved",
        "target_sentence_display_approved",
        "top1_advice_display_approved",
        "consent_or_lawful_basis_verified",
        "withdrawal_process_verified",
        "purpose_limitation_verified",
        "retention_schedule_verified",
        "access_control_verified",
        "reviewer_confidentiality_required",
        "reviewer_mapping_stored_separately",
    ):
        _expect(payload.get(field), True, f"approval {field}")
    _expect(payload.get("raw_audio_export_allowed"), False, "raw audio export")
    _expect(payload.get("privacy_zone"), "C1_public_attestation", "approval zone")
    if _contains_tbd(payload):
        raise C03ContractError("resolved approval cannot contain TBD")
    return payload


def validate_reviewer_attestation_payload(
    payload: Mapping[str, Any],
    *,
    approval_sha256: str,
) -> ReviewerEvidence:
    """Validate pseudonymous reviewer qualifications and blindness."""

    _reject_nonfinite_tree(payload, "C03 reviewer attestation")
    _reject_sensitive_payload(payload, "C03 reviewer attestation")
    keys = {
        "schema_version",
        "status",
        "institutional_expert_approval_sha256",
        "reviewer_count",
        "qualification_policy_sha256",
        "rubric_sha256",
        "risk_taxonomy_sha256",
        "reviewers",
        "every_reviewer_qualified",
        "every_reviewer_confidentiality_attested",
        "every_reviewer_blind_to_model_score",
        "every_reviewer_blind_to_source_condition",
        "reviewer_identity_mapping_stored_separately",
        "privacy_zone",
    }
    _expect_keys(payload, keys, "reviewer attestation")
    _expect(payload.get("schema_version"), REVIEWER_SCHEMA_VERSION, "reviewer schema")
    _expect(payload.get("status"), "approved", "reviewer status")
    _expect(
        payload.get("institutional_expert_approval_sha256"),
        _sha256(approval_sha256, "approval SHA"),
        "reviewer approval binding",
    )
    for field in (
        "qualification_policy_sha256",
        "rubric_sha256",
        "risk_taxonomy_sha256",
    ):
        _sha256(payload.get(field), f"reviewer {field}")
    reviewers = _list(payload.get("reviewers"), "reviewers")
    count = _integer(payload.get("reviewer_count"), "reviewer count", minimum=1)
    _expect(len(reviewers), count, "reviewer count/list binding")
    seen: set[str] = set()
    for index, raw_reviewer in enumerate(reviewers):
        reviewer = _mapping(raw_reviewer, f"reviewer {index}")
        _expect_keys(
            reviewer,
            {
                "reviewer_id_sha256",
                "qualification_receipt_sha256",
                "confidentiality_receipt_sha256",
                "rubric_training_receipt_sha256",
                "model_score_blinding_attested",
                "source_condition_blinding_attested",
                "conflict_of_interest_cleared",
            },
            f"reviewer {index}",
        )
        reviewer_id = _sha256(
            reviewer.get("reviewer_id_sha256"), f"reviewer {index} ID"
        )
        if reviewer_id in seen:
            raise C03ContractError("duplicate reviewer ID")
        seen.add(reviewer_id)
        for field in (
            "qualification_receipt_sha256",
            "confidentiality_receipt_sha256",
            "rubric_training_receipt_sha256",
        ):
            _sha256(reviewer.get(field), f"reviewer {index} {field}")
        for field in (
            "model_score_blinding_attested",
            "source_condition_blinding_attested",
            "conflict_of_interest_cleared",
        ):
            _expect(reviewer.get(field), True, f"reviewer {index} {field}")
    for field in (
        "every_reviewer_qualified",
        "every_reviewer_confidentiality_attested",
        "every_reviewer_blind_to_model_score",
        "every_reviewer_blind_to_source_condition",
        "reviewer_identity_mapping_stored_separately",
    ):
        _expect(payload.get(field), True, f"reviewer {field}")
    _expect(payload.get("privacy_zone"), "C1_public_attestation", "reviewer zone")
    if _contains_tbd(payload):
        raise C03ContractError("resolved reviewer attestation cannot contain TBD")
    canonical_sha = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return ReviewerEvidence(
        sha256=canonical_sha,
        reviewer_count=count,
        payload=payload,
    )


def validate_preregistration_payload(
    payload: Mapping[str, Any],
    *,
    approval_sha256: str,
    reviewer: ReviewerEvidence,
    s107_commit_sha256: str,
    c02_commit_sha256: str,
) -> Mapping[str, Any]:
    """Validate fixed evidence-backed choices and all resolved design choices."""

    _reject_nonfinite_tree(payload, "C03 preregistration")
    _reject_sensitive_payload(payload, "C03 preregistration")
    keys = {
        "schema_version",
        "status",
        "frozen",
        "institutional_expert_approval_sha256",
        "reviewer_attestation_manifest_sha256",
        "s107_completion_commit_sha256",
        "c02_completion_commit_sha256",
        "primary_sample_count",
        "primary_stratification_axes",
        "exact_primary_sampling_allocation",
        "abstention_referral_sampling",
        "reviewer_design",
        "reviewer_view",
        "primary_metrics",
        "zero_tolerance_safety",
        "statistics",
        "approval",
        "privacy_zone",
    }
    _expect_keys(payload, keys, "C03 preregistration")
    _expect(payload.get("schema_version"), PREREG_SCHEMA_VERSION, "prereg schema")
    _expect(payload.get("status"), "approved", "prereg status")
    _expect(payload.get("frozen"), True, "prereg frozen")
    _expect(
        payload.get("institutional_expert_approval_sha256"),
        approval_sha256,
        "prereg approval binding",
    )
    _expect(
        payload.get("reviewer_attestation_manifest_sha256"),
        reviewer.sha256,
        "prereg reviewer binding",
    )
    _expect(
        payload.get("s107_completion_commit_sha256"),
        s107_commit_sha256,
        "prereg S107 binding",
    )
    _expect(
        payload.get("c02_completion_commit_sha256"),
        c02_commit_sha256,
        "prereg C02 binding",
    )
    _expect(payload.get("primary_sample_count"), PRIMARY_SAMPLE_COUNT, "prereg N")
    _expect(
        tuple(_list(payload.get("primary_stratification_axes"), "prereg strata")),
        STRATIFICATION_AXES,
        "prereg strata",
    )
    allocation = payload.get("exact_primary_sampling_allocation")
    if not isinstance(allocation, (list, Mapping)) or not allocation:
        raise C03ContractError("resolved preregistration needs exact sample allocation")

    abstention = _mapping(
        payload.get("abstention_referral_sampling"),
        "abstention/referral sampling",
    )
    _expect_keys(
        abstention,
        {
            "required",
            "relation_to_primary_sample",
            "exact_count_or_allocation",
            "metric_definition",
            "gate",
        },
        "abstention/referral sampling",
    )
    _expect(abstention.get("required"), True, "abstention sample required")

    reviewer_design = _mapping(payload.get("reviewer_design"), "reviewer design")
    _expect_keys(
        reviewer_design,
        {
            "reviewer_count_n",
            "qualification_requirements",
            "ratings_per_item",
            "assignment_policy",
            "adjudication_trigger",
            "adjudication_protocol",
            "agreement_metric_and_support",
        },
        "reviewer design",
    )
    _expect(
        reviewer_design.get("reviewer_count_n"),
        reviewer.reviewer_count,
        "prereg/reviewer N binding",
    )
    view = _mapping(payload.get("reviewer_view"), "reviewer view")
    _expect_keys(
        view,
        {"visible_fields", "model_score_visible", "source_condition_visible"},
        "reviewer view",
    )
    _expect(
        tuple(_list(view.get("visible_fields"), "reviewer visible fields")),
        REVIEWER_VISIBLE_FIELDS,
        "reviewer visible fields",
    )
    _expect(view.get("model_score_visible"), False, "model score blindness")
    _expect(view.get("source_condition_visible"), False, "source blindness")

    metrics = _mapping(payload.get("primary_metrics"), "primary metrics")
    _expect_keys(
        metrics,
        {
            "location_accuracy",
            "error_type_accuracy",
            "unsupported_claim_rate",
            "advice_actionability",
        },
        "primary metrics",
    )
    fixed_metrics = (
        ("location_accuracy", "greater_than_or_equal", Decimal("0.9")),
        ("error_type_accuracy", "greater_than_or_equal", Decimal("0.9")),
        ("unsupported_claim_rate", "less_than", Decimal("0.02")),
    )
    for name, operator, threshold in fixed_metrics:
        metric = _mapping(metrics.get(name), name)
        _expect_keys(
            metric,
            {"threshold_operator", "threshold", "exact_numerator_denominator"},
            name,
        )
        _expect(metric.get("threshold_operator"), operator, f"{name} operator")
        _expect(_rate(metric.get("threshold"), f"{name} threshold"), threshold, name)
    actionability = _mapping(metrics.get("advice_actionability"), "actionability")
    _expect_keys(
        actionability,
        {"reported", "gate", "exact_numerator_denominator"},
        "actionability",
    )
    _expect(actionability.get("reported"), True, "actionability reporting")

    zero = _mapping(payload.get("zero_tolerance_safety"), "zero tolerance")
    _expect(
        zero.get("dangerous_expression_max_count"),
        0,
        "dangerous expression maximum",
    )
    _expect(
        zero.get("discriminatory_expression_max_count"),
        0,
        "discriminatory expression maximum",
    )
    _expect(
        zero.get("speaker_identity_inference_max_count"),
        0,
        "identity inference maximum",
    )
    _expect(zero.get("medical_judgment_max_count"), 0, "medical maximum")
    for field in (
        "any_event_requires_revision",
        "any_event_requires_reaudit",
        "any_event_prohibits_pass",
    ):
        _expect(zero.get(field), True, f"zero tolerance {field}")

    statistics = _mapping(payload.get("statistics"), "statistics")
    _expect_keys(
        statistics,
        {
            "confidence_interval_method",
            "confidence_level",
            "cluster_or_resampling_unit",
            "multiplicity_control",
            "minimum_support_rule",
            "small_support_policy",
        },
        "statistics",
    )
    approval = _mapping(payload.get("approval"), "prereg approval")
    _expect_keys(approval, {"decision", "signed_receipt_sha256"}, "prereg approval")
    _expect(approval.get("decision"), "approved", "prereg approval decision")
    _sha256(approval.get("signed_receipt_sha256"), "prereg signed receipt")
    _expect(payload.get("privacy_zone"), "C1_public_preregistration", "prereg zone")
    if _contains_tbd(payload):
        raise C03ContractError("frozen preregistration cannot contain TBD")
    return payload


def validate_item_manifest_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a D1 sampled-item manifest without resolving audio or text."""

    _reject_nonfinite_tree(payload, "C03 item manifest")
    _reject_sensitive_payload(payload, "C03 item manifest")
    keys = {
        "schema_version",
        "manifest_id",
        "c02_completion_commit_sha256",
        "c02_d1_bundle_manifest_sha256",
        "c03_preregistration_sha256",
        "privacy_zone",
        "primary_item_count",
        "abstention_referral_item_count",
        "items",
    }
    _expect_keys(payload, keys, "C03 item manifest")
    _expect(payload.get("schema_version"), ITEM_SCHEMA_VERSION, "item schema")
    manifest_id = _string(payload.get("manifest_id"), "item manifest ID")
    if not re.fullmatch(r"c03items_[0-9a-f]{32}", manifest_id):
        raise C03ContractError("invalid item manifest ID")
    for field in (
        "c02_completion_commit_sha256",
        "c02_d1_bundle_manifest_sha256",
        "c03_preregistration_sha256",
    ):
        _sha256(payload.get(field), f"item manifest {field}")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "item manifest zone",
    )
    primary_declared = _integer(
        payload.get("primary_item_count"),
        "primary item count",
        minimum=0,
    )
    _expect(primary_declared, PRIMARY_SAMPLE_COUNT, "primary item count")
    abstention_declared = _integer(
        payload.get("abstention_referral_item_count"),
        "abstention/referral item count",
        minimum=0,
    )
    items = _list(payload.get("items"), "audit items")
    seen: set[str] = set()
    primary_count = 0
    abstention_count = 0
    for index, raw_item in enumerate(items):
        item = _mapping(raw_item, f"audit item {index}")
        _expect_keys(item, ITEM_KEYS, f"audit item {index}")
        item_id = _string(item.get("audit_item_id"), f"audit item {index} ID")
        if not re.fullmatch(r"c03item_[0-9a-f]{32}", item_id):
            raise C03ContractError(f"invalid audit item ID at {index}")
        if item_id in seen:
            raise C03ContractError("duplicate audit item ID")
        seen.add(item_id)
        role = _string(item.get("sample_role"), f"audit item {index} role")
        if role == "primary":
            primary_count += 1
            _expect(item.get("coaching_action"), "coach", "primary coaching action")
            _sha256(item.get("top1_advice_object_id_sha256"), "primary top-1 advice")
        elif role == "abstention_referral":
            abstention_count += 1
            if item.get("coaching_action") not in ("rerecord", "expert_review", "none"):
                raise C03ContractError("abstention/referral action differs")
            _expect(item.get("top1_advice_object_id_sha256"), None, "abstention advice")
        else:
            raise C03ContractError("invalid sample role")
        for field in (
            "c02_input_manifest_sha256",
            "c02_output_manifest_sha256",
            "audio_object_id_sha256",
            "target_sentence_object_id_sha256",
            "review_packet_sha256",
        ):
            _sha256(item.get(field), f"audit item {index} {field}")
        for field in (
            "l1_stratum_id",
            "error_type_stratum_id",
            "confidence_bin_id",
        ):
            _safe_id(item.get(field), f"audit item {index} {field}")
        _expect(
            tuple(_list(item.get("reviewer_visible_fields"), "item visible fields")),
            REVIEWER_VISIBLE_FIELDS,
            "item visible fields",
        )
        _expect(item.get("model_score_exposed_to_reviewer"), False, "model score")
        _expect(
            item.get("source_condition_exposed_to_reviewer"),
            False,
            "source condition",
        )
    _expect(primary_count, primary_declared, "primary row count")
    _expect(abstention_count, abstention_declared, "abstention row count")
    _expect(len(items), primary_count + abstention_count, "total item count")
    return payload


def validate_rating_manifest_payload(
    payload: Mapping[str, Any],
    *,
    allowed_item_ids: set[str] | None = None,
) -> Mapping[str, Any]:
    """Validate D1 ratings while preserving originals and reviewer blindness."""

    _reject_nonfinite_tree(payload, "C03 rating manifest")
    _reject_sensitive_payload(payload, "C03 rating manifest")
    keys = {
        "schema_version",
        "manifest_id",
        "item_manifest_sha256",
        "reviewer_attestation_manifest_sha256",
        "preregistration_sha256",
        "privacy_zone",
        "ratings",
    }
    _expect_keys(payload, keys, "C03 rating manifest")
    _expect(payload.get("schema_version"), RATING_SCHEMA_VERSION, "rating schema")
    manifest_id = _string(payload.get("manifest_id"), "rating manifest ID")
    if not re.fullmatch(r"c03ratings_[0-9a-f]{32}", manifest_id):
        raise C03ContractError("invalid rating manifest ID")
    for field in (
        "item_manifest_sha256",
        "reviewer_attestation_manifest_sha256",
        "preregistration_sha256",
    ):
        _sha256(payload.get(field), f"rating manifest {field}")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "rating zone",
    )
    seen: set[str] = set()
    for index, raw_rating in enumerate(_list(payload.get("ratings"), "ratings")):
        rating = _mapping(raw_rating, f"rating {index}")
        _expect_keys(rating, RATING_KEYS, f"rating {index}")
        rating_id = _string(rating.get("rating_id"), f"rating {index} ID")
        if not re.fullmatch(r"c03rating_[0-9a-f]{32}", rating_id):
            raise C03ContractError("invalid rating ID")
        if rating_id in seen:
            raise C03ContractError("duplicate rating ID")
        seen.add(rating_id)
        item_id = _string(rating.get("audit_item_id"), f"rating {index} item")
        if not re.fullmatch(r"c03item_[0-9a-f]{32}", item_id):
            raise C03ContractError("invalid rating item ID")
        if allowed_item_ids is not None and item_id not in allowed_item_ids:
            raise C03ContractError("rating references unknown audit item")
        for field in (
            "reviewer_id_sha256",
            "risk_taxonomy_sha256",
            "rating_receipt_sha256",
        ):
            _sha256(rating.get(field), f"rating {index} {field}")
        role = _string(rating.get("rating_role"), f"rating {index} role")
        if role == "original":
            _expect(
                rating.get("original_rating_id_sha256"), None, "original rating link"
            )
            _expect(rating.get("independent_review"), True, "independent original")
        elif role == "adjudication":
            _sha256(
                rating.get("original_rating_id_sha256"), "adjudication original link"
            )
            _expect(
                rating.get("independent_review"), False, "adjudication independence"
            )
        else:
            raise C03ContractError("invalid rating role")
        _expect(
            tuple(
                _list(rating.get("reviewer_visible_fields"), "rating visible fields")
            ),
            REVIEWER_VISIBLE_FIELDS,
            "rating visible fields",
        )
        _expect(rating.get("model_score_seen"), False, "rating model score")
        _expect(rating.get("source_condition_seen"), False, "rating source condition")
        if rating.get("location_accuracy") not in (
            "correct",
            "incorrect",
            "uncertain",
            "not_applicable",
        ):
            raise C03ContractError("invalid location judgment")
        if rating.get("error_type_accuracy") not in (
            "correct",
            "incorrect",
            "uncertain",
            "not_applicable",
        ):
            raise C03ContractError("invalid type judgment")
        if rating.get("advice_actionability") not in (
            "actionable",
            "not_actionable",
            "uncertain",
            "not_applicable",
        ):
            raise C03ContractError("invalid actionability judgment")
        if rating.get("unsupported_claim") not in ("absent", "present", "uncertain"):
            raise C03ContractError("invalid unsupported-claim judgment")
        for field in (
            "dangerous_expression",
            "discriminatory_expression",
            "speaker_identity_inference",
            "medical_judgment",
        ):
            _boolean(rating.get(field), f"rating {index} {field}")
        risk_codes = _list(rating.get("risk_codes"), f"rating {index} risk codes")
        if len(risk_codes) != len(set(risk_codes)):
            raise C03ContractError("risk codes must be unique")
        for code in risk_codes:
            _safe_id(code, f"rating {index} risk code")
    return payload


def _validate_metric(metric: Mapping[str, Any], name: str) -> Decimal:
    _expect_keys(metric, {"numerator", "denominator", "rate"}, name)
    numerator = _integer(metric.get("numerator"), f"{name} numerator", minimum=0)
    denominator = _integer(metric.get("denominator"), f"{name} denominator", minimum=1)
    if numerator > denominator:
        raise C03ContractError(f"{name} numerator exceeds denominator")
    observed = _rate(metric.get("rate"), f"{name} rate")
    expected = Decimal(numerator) / Decimal(denominator)
    if observed != expected:
        raise C03ContractError(f"{name} rate does not equal numerator/denominator")
    return observed


def validate_report_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Recompute fixed numerical and zero-tolerance decisions for an A1 report."""

    _reject_nonfinite_tree(payload, "C03 report")
    _reject_sensitive_payload(payload, "C03 report")
    keys = {
        "schema_version",
        "report_id",
        "item_manifest_sha256",
        "rating_manifest_sha256",
        "preregistration_sha256",
        "reviewer_attestation_manifest_sha256",
        "privacy_zone",
        "primary_sample_count",
        "reviewer_count",
        "original_rating_count",
        "adjudication_rating_count",
        "primary_metrics",
        "actionability",
        "abstention_referral_audit",
        "statistical_contract",
        "zero_tolerance_safety",
        "decision",
    }
    _expect_keys(payload, keys, "C03 report")
    _expect(payload.get("schema_version"), REPORT_SCHEMA_VERSION, "report schema")
    report_id = _string(payload.get("report_id"), "report ID")
    if not re.fullmatch(r"c03report_[0-9a-f]{32}", report_id):
        raise C03ContractError("invalid report ID")
    for field in (
        "item_manifest_sha256",
        "rating_manifest_sha256",
        "preregistration_sha256",
        "reviewer_attestation_manifest_sha256",
    ):
        _sha256(payload.get(field), f"report {field}")
    _expect(payload.get("privacy_zone"), "A1_aggregate_only", "report zone")
    _expect(payload.get("primary_sample_count"), PRIMARY_SAMPLE_COUNT, "report N")
    _integer(payload.get("reviewer_count"), "report reviewer count", minimum=1)
    _integer(payload.get("original_rating_count"), "original rating count", minimum=1)
    _integer(payload.get("adjudication_rating_count"), "adjudication count", minimum=0)

    metrics = _mapping(payload.get("primary_metrics"), "report metrics")
    _expect_keys(
        metrics,
        {"location_accuracy", "error_type_accuracy", "unsupported_claim_rate"},
        "report metrics",
    )
    location = _validate_metric(
        _mapping(metrics.get("location_accuracy"), "location accuracy"),
        "location accuracy",
    )
    error_type = _validate_metric(
        _mapping(metrics.get("error_type_accuracy"), "type accuracy"),
        "type accuracy",
    )
    unsupported = _validate_metric(
        _mapping(metrics.get("unsupported_claim_rate"), "unsupported claim rate"),
        "unsupported claim rate",
    )
    _validate_metric(
        _mapping(payload.get("actionability"), "actionability"), "actionability"
    )

    abstention = _mapping(payload.get("abstention_referral_audit"), "abstention audit")
    _expect_keys(
        abstention,
        {
            "sample_count",
            "metric_definition_sha256",
            "numerator",
            "denominator",
            "rate",
            "gate_applied",
        },
        "abstention audit",
    )
    sample_count = _integer(
        abstention.get("sample_count"), "abstention sample", minimum=0
    )
    _sha256(abstention.get("metric_definition_sha256"), "abstention metric definition")
    numerator = _integer(abstention.get("numerator"), "abstention numerator", minimum=0)
    denominator = _integer(
        abstention.get("denominator"), "abstention denominator", minimum=0
    )
    if numerator > denominator or denominator > sample_count:
        raise C03ContractError("abstention aggregate counts are inconsistent")
    abstention_rate = _rate(abstention.get("rate"), "abstention rate")
    expected_abstention_rate = (
        Decimal(0) if denominator == 0 else Decimal(numerator) / Decimal(denominator)
    )
    _expect(abstention_rate, expected_abstention_rate, "abstention rate")
    _expect(abstention.get("gate_applied"), False, "abstention evidence-only boundary")

    statistics = _mapping(payload.get("statistical_contract"), "statistical contract")
    _expect_keys(
        statistics,
        {
            "confidence_interval_method_sha256",
            "multiplicity_method_sha256",
            "support_rule_sha256",
            "adjudication_rule_sha256",
            "risk_taxonomy_sha256",
            "confidence_intervals",
            "multiplicity_results",
            "support_results",
        },
        "statistical contract",
    )
    for field in (
        "confidence_interval_method_sha256",
        "multiplicity_method_sha256",
        "support_rule_sha256",
        "adjudication_rule_sha256",
        "risk_taxonomy_sha256",
    ):
        _sha256(statistics.get(field), f"statistics {field}")
    cis = _list(statistics.get("confidence_intervals"), "confidence intervals")
    ci_ids: set[str] = set()
    for index, raw_ci in enumerate(cis):
        ci = _mapping(raw_ci, f"confidence interval {index}")
        _expect_keys(
            ci, {"metric_id", "lower", "upper"}, f"confidence interval {index}"
        )
        metric_id = _string(ci.get("metric_id"), f"confidence interval {index} metric")
        if metric_id in ci_ids:
            raise C03ContractError("duplicate confidence interval metric")
        ci_ids.add(metric_id)
        lower = _rate(ci.get("lower"), f"confidence interval {index} lower")
        upper = _rate(ci.get("upper"), f"confidence interval {index} upper")
        if lower > upper:
            raise C03ContractError("confidence interval lower exceeds upper")
    if not {
        "location_accuracy",
        "error_type_accuracy",
        "unsupported_claim_rate",
    }.issubset(ci_ids):
        raise C03ContractError("primary metric confidence intervals are incomplete")
    for field in ("multiplicity_results", "support_results"):
        for index, raw_result in enumerate(_list(statistics.get(field), field)):
            result = _mapping(raw_result, f"{field} {index}")
            _expect_keys(result, {"result_id", "value"}, f"{field} {index}")
            _safe_id(result.get("result_id"), f"{field} {index} ID")

    safety = _mapping(payload.get("zero_tolerance_safety"), "zero tolerance report")
    _expect_keys(
        safety,
        {
            "dangerous_expression_count",
            "discriminatory_expression_count",
            "speaker_identity_inference_count",
            "medical_judgment_count",
            "any_event",
            "pass_allowed",
        },
        "zero tolerance report",
    )
    safety_counts = [
        _integer(safety.get(field), field, minimum=0) for field in ZERO_TOLERANCE_FIELDS
    ]
    any_safety = any(safety_counts)
    _expect(safety.get("any_event"), any_safety, "zero tolerance any-event")
    _expect(safety.get("pass_allowed"), not any_safety, "zero tolerance pass")

    location_pass = location >= LOCATION_THRESHOLD
    type_pass = error_type >= TYPE_THRESHOLD
    unsupported_pass = unsupported < UNSUPPORTED_THRESHOLD
    safety_pass = not any_safety
    overall = location_pass and type_pass and unsupported_pass and safety_pass
    decision = _mapping(payload.get("decision"), "decision")
    _expect_keys(
        decision,
        {
            "location_gate_passed",
            "error_type_gate_passed",
            "unsupported_claim_gate_passed",
            "zero_tolerance_gate_passed",
            "overall_passed",
            "status",
            "revision_required",
            "reaudit_required",
            "production_release_allowed",
        },
        "decision",
    )
    _expect(decision.get("location_gate_passed"), location_pass, "location decision")
    _expect(decision.get("error_type_gate_passed"), type_pass, "type decision")
    _expect(
        decision.get("unsupported_claim_gate_passed"),
        unsupported_pass,
        "unsupported decision",
    )
    _expect(decision.get("zero_tolerance_gate_passed"), safety_pass, "safety decision")
    _expect(decision.get("overall_passed"), overall, "overall decision")
    if any_safety:
        expected_status = "failed_safety_revise_and_reaudit"
        expected_revision = True
        expected_reaudit = True
    elif not overall:
        expected_status = "failed_metric_gate"
        expected_revision = False
        expected_reaudit = True
    else:
        expected_status = "passed"
        expected_revision = False
        expected_reaudit = False
    _expect(decision.get("status"), expected_status, "decision status")
    _expect(decision.get("revision_required"), expected_revision, "revision decision")
    _expect(decision.get("reaudit_required"), expected_reaudit, "reaudit decision")
    _expect(
        decision.get("production_release_allowed"),
        overall,
        "production release decision",
    )
    return payload


def _read_approval(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> ApprovalEvidence:
    section = _mapping(
        config.get("institutional_expert_approval"),
        "institutional_expert_approval",
    )
    expected_sha = _string(section.get("approved_artifact_sha256"), "approval SHA")
    if expected_sha == TBD_EXTERNAL_ATTESTATION:
        raise C03ContractError(
            "institutional/expert approval is TBD_EXTERNAL_ATTESTATION",
            stage="institutional_expert_approval",
            audit=audit,
        )
    _sha256(expected_sha, "approval SHA")
    audit.approval_path_resolution_count += 1
    audit.approval_read_attempt_count += 1
    payload, snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            section.get("approved_artifact_path"), "approval path"
        ),
        expected_sha256=expected_sha,
        name="institutional/expert approval",
    )
    audit.approval_read_count += 1
    validate_approval_payload(payload)
    _verify_snapshot(workspace_root, snapshot)
    return ApprovalEvidence(sha256=snapshot.sha256, payload=payload)


def _read_reviewers(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    approval: ApprovalEvidence,
    audit: AccessAudit,
) -> ReviewerEvidence:
    section = _mapping(config.get("reviewer_attestations"), "reviewer_attestations")
    expected_sha = _string(section.get("approved_artifact_sha256"), "reviewer SHA")
    if expected_sha == TBD_EXTERNAL_ATTESTATION:
        raise C03ContractError(
            "reviewer attestations are TBD_EXTERNAL_ATTESTATION",
            stage="reviewer_attestations",
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
        name="reviewer attestations",
    )
    audit.reviewer_attestation_read_count += 1
    evidence = validate_reviewer_attestation_payload(
        payload,
        approval_sha256=approval.sha256,
    )
    _expect(evidence.sha256, snapshot.sha256, "reviewer canonical/file hash")
    _verify_snapshot(workspace_root, snapshot)
    return evidence


def _read_preregistration(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    s107: S107Evidence,
    c02: C02Evidence,
    approval: ApprovalEvidence,
    reviewer: ReviewerEvidence,
    audit: AccessAudit,
) -> PreregistrationEvidence:
    section = _mapping(config.get("preregistration"), "preregistration")
    expected_sha = _string(section.get("approved_artifact_sha256"), "prereg SHA")
    if expected_sha == TBD_PREREGISTRATION:
        raise C03ContractError(
            "C03 preregistration is TBD_PREREGISTRATION",
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
        name="C03 preregistration",
    )
    audit.preregistration_read_count += 1
    validate_preregistration_payload(
        payload,
        approval_sha256=approval.sha256,
        reviewer=reviewer,
        s107_commit_sha256=s107.s107.commit_sha256,
        c02_commit_sha256=c02.commit_sha256,
    )
    _verify_snapshot(workspace_root, snapshot)
    return PreregistrationEvidence(sha256=snapshot.sha256, payload=payload)


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Validate predecessors/authorization and stop before protected audit data."""

    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
        audit=audit,
    )
    _verify_snapshots(workspace_root, static.source_snapshots, audit)
    s107 = _deep_validate_s107(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    c02 = _deep_validate_c02(
        workspace_root=workspace_root,
        config=config,
        s107=s107,
        audit=audit,
    )
    approval = _read_approval(
        workspace_root=workspace_root,
        config=config,
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
        s107=s107,
        c02=c02,
        approval=approval,
        reviewer=reviewer,
        audit=audit,
    )
    return PreflightResult(
        status="ready_for_separate_registered_c03_executor",
        config_sha256=static.config_sha256,
        s107_commit_sha256=s107.s107.commit_sha256,
        c02_commit_sha256=c02.commit_sha256,
        approval_sha256=approval.sha256,
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
        "actual_expert_or_rating_count": 0,
        "actual_audio_or_protected_row_count": 0,
        "actual_metric_or_result_count": 0,
        "actual_model_gpu_network_count": 0,
        "artifact_created": False,
        "c03_completion_claim_allowed": False,
        "production_release_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: C03ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "actual_expert_or_rating_count": 0,
        "actual_audio_or_protected_row_count": 0,
        "actual_metric_or_result_count": 0,
        "actual_model_gpu_network_count": 0,
        "artifact_created": False,
        "c03_completion_claim_allowed": False,
        "production_release_allowed": False,
        "publication_allowed": False,
    }
