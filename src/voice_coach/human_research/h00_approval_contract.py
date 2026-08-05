"""Fail-closed H00 human-research official-determination readiness contract.

This package validates only immutable public schemas/templates and, in a future
run, exact institution/IRB or equivalent official evidence supplied by an
authorized human custodian.  It cannot infer approval or exemption, resolve the
restricted document locator, browse or submit a portal form, contact an
institution, recruit or consent a participant, record audio, access personal or
sensitive data, randomize or deliver an intervention, call a model/GPU/TTS
service, write an artifact, or claim H00 completion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
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
TBD_H00_APPROVAL = "TBD_H00_APPROVAL"
TBD_H00_ATTESTATION = "TBD_H00_ATTESTATION"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
UTC_RE = re.compile(
    r"^20[0-9]{2}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])"
    r"T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)

SOURCE_PATHS = (
    "artifacts/planning/learning_effect_power_sensitivity_v1.json",
    "configs/experiments/learning_effect_power_v1.yaml",
    "configs/human_research/h00_human_research_approval_public_v1.schema.json",
    "configs/human_research/h00_human_research_approval_public_v1.template.json",
    "configs/human_research/h00_official_document_attestation_v1.schema.json",
    "configs/human_research/h00_official_document_attestation_v1.template.json",
    "docs/constraints/DATA_LICENSE_AND_ROLE_MATRIX_KO.md",
    "docs/constraints/DATA_PRIVACY_BOUNDARY_KO.md",
)

EXPECTED_EXECUTION_ORDER = (
    "validate_h00_static_config_sources_schemas_and_fail_closed_templates",
    "require_exact_h00_public_approval_sha_before_path_resolution",
    "validate_actual_public_approval_and_official_scope",
    "require_exact_official_document_attestation_sha_before_path_resolution",
    "validate_attestation_hash_chain_without_resolving_restricted_locator",
    "stop_before_external_browser_submission_contact_participant_audio_model_or_output",
)

APPROVAL_KEYS = {
    "schema_version",
    "approval_artifact_id",
    "status",
    "privacy_zone",
    "public_summary_only",
    "actual_pilot_covered",
    "consent_approved",
    "privacy_approved",
    "retention_approved",
    "deletion_approved",
    "institution_evidence",
    "protocol",
    "study_scope",
    "intervention_manuals",
    "randomization_and_concealment",
    "data_governance",
    "consent_withdrawal_and_participant_rights",
    "recruitment_compensation_and_minors",
    "ai_feedback_and_safety",
    "pseudonymization_and_transfer",
    "incident_pause_stop",
    "lifecycle",
    "authorization",
    "binding_sha256",
}
APPROVAL_NESTED_KEYS = {
    "institution_evidence": {
        "institution_id_sha256",
        "approving_body_role",
        "official_determination_category",
        "official_determination_id_sha256",
        "institution_form_sha256",
        "official_determination_document_sha256",
        "restricted_document_attestation_sha256",
        "official_source_verified",
    },
    "protocol": {
        "protocol_id_sha256",
        "protocol_version",
        "protocol_sha256",
        "application_bundle_sha256",
        "consent_form_version",
        "consent_form_sha256",
        "privacy_impact_assessment_sha256",
        "data_management_plan_sha256",
    },
    "study_scope": {
        "comparisons",
        "design",
        "intervention_duration",
        "timepoints",
        "c0_covered",
        "c3_covered",
        "c5_covered",
        "participant_recruitment_authorized",
        "informed_consent_authorized",
        "audio_recording_authorized",
        "randomized_intervention_authorized",
        "blind_rating_authorized",
        "d1_pseudonymized_processing_authorized",
        "a1_aggregate_analysis_authorized",
        "confirmatory_study_covered",
    },
    "intervention_manuals": {"c0", "c3", "c5"},
    "randomization_and_concealment": {
        "unit",
        "allocation_ratio",
        "parallel_design",
        "stratification_factors",
        "allocation_concealment_required",
        "allocation_method_id",
        "allocation_implementation_receipt_sha256",
        "randomization_seed_custody_receipt_sha256",
        "independent_allocation_role_id",
        "blind_outcome_raters",
        "participant_blinding_claimed",
    },
    "data_governance": {
        "audio_recording_in_scope",
        "personal_data_in_scope",
        "sensitive_or_special_category_review_completed",
        "approved_purpose_ids",
        "purpose_limitation_required",
        "personal_data_categories_sha256",
        "sensitive_data_categories_sha256",
        "retention_duration_or_trigger_code",
        "retention_policy_sha256",
        "authorized_role_matrix_sha256",
        "access_logging_required",
        "deletion_method_id",
        "deletion_protocol_sha256",
        "backup_deletion_covered",
        "data_zones",
        "raw_or_identifiable_data_publication_allowed",
    },
    "consent_withdrawal_and_participant_rights": {
        "informed_consent_required",
        "explicit_audio_recording_consent_required",
        "ai_incorrect_feedback_risk_disclosed",
        "withdrawal_without_penalty_allowed",
        "withdrawal_protocol_sha256",
        "withdrawal_existing_data_handling_code",
        "deletion_request_route_sha256",
        "human_contact_and_escalation_route_sha256",
        "consent_language_accessibility_reviewed",
    },
    "recruitment_compensation_and_minors": {
        "recruitment_materials_sha256",
        "eligibility_protocol_sha256",
        "undue_influence_review_completed",
        "compensation_terms_code",
        "compensation_protocol_sha256",
        "compensation_withdrawal_treatment_code",
        "minors_policy",
        "guardian_consent_protocol_sha256",
        "minor_assent_protocol_sha256",
    },
    "ai_feedback_and_safety": {
        "incorrect_feedback_risk_documented",
        "human_escalation_available",
        "unsupported_claim_abstention_required",
        "identity_inference_prohibited",
        "medical_judgment_prohibited",
        "feedback_safety_protocol_sha256",
        "human_escalation_protocol_sha256",
        "adverse_feedback_monitoring_protocol_sha256",
    },
    "pseudonymization_and_transfer": {
        "study_namespaced_pseudonymous_ids_required",
        "pseudonym_key_mapping_separated",
        "pseudonym_key_mapping_zone",
        "key_custodian_role_receipt_sha256",
        "direct_identifiers_in_public_artifact_allowed",
        "external_service_transfer_allowed",
        "tts_service_transfer_allowed",
        "cross_border_transfer_allowed",
        "transfer_policy_sha256",
        "approved_provider_scope_sha256",
        "minimum_necessary_transfer_required",
        "separate_transfer_consent_required",
        "separate_transfer_consent_approved",
    },
    "incident_pause_stop": {
        "incident_response_protocol_sha256",
        "breach_notification_protocol_sha256",
        "pause_stop_triggers",
        "pause_on_trigger_required",
        "stop_protocol_sha256",
        "resume_requires_official_body_and_principal_investigator",
        "resume_protocol_sha256",
    },
    "lifecycle": {
        "approved_at_utc",
        "effective_at_utc",
        "expires_at_utc_or_status_code",
        "status_verified_at_utc",
        "amendment_status",
        "current_amendment_version",
        "current_amendment_sha256",
        "scope_change_requires_prior_amendment",
        "expiration_action",
        "suspended",
        "withdrawn",
        "current_and_active",
    },
    "authorization": {
        "decision",
        "official_determination_verified",
        "signed_receipt_sha256",
        "immutable_archive_receipt_sha256",
        "independent_verification_receipt_sha256",
        "recruitment_execution_authorized",
        "consent_execution_authorized",
        "recording_execution_authorized",
    },
}
MANUAL_KEYS = {
    "condition",
    "version",
    "manual_sha256",
    "frozen_before_recruitment",
}
ATTESTATION_KEYS = {
    "schema_version",
    "attestation_id",
    "status",
    "privacy_zone",
    "public_summary_only",
    "institution_id_sha256",
    "protocol_version",
    "protocol_sha256",
    "official_determination_id_sha256",
    "official_determination_document_sha256",
    "restricted_official_document_locator_sha256",
    "restricted_archive_receipt_sha256",
    "custodian_role_id",
    "verification",
    "lifecycle",
    "binding_sha256",
}
ATTESTATION_VERIFICATION_KEYS = {
    "institution_official_source_verified",
    "official_document_opened_by_authorized_custodian",
    "document_hash_matches",
    "protocol_version_matches",
    "determination_id_matches_hashed_public_summary",
    "signature_or_seal_verified",
    "current_status_verified",
    "scope_covers_participant_recruitment",
    "scope_covers_informed_consent",
    "scope_covers_audio_recording",
    "scope_covers_randomized_intervention",
    "scope_covers_pre_post_retention",
    "pii_copied_to_attestation",
    "participant_rows_copied_to_attestation",
    "raw_locator_path_or_url_copied_to_attestation",
}
ATTESTATION_LIFECYCLE_KEYS = {
    "attested_at_utc",
    "official_status_verified_at_utc",
    "expires_at_utc_or_status_code",
    "amendment_status",
    "current_amendment_sha256",
    "attestation_current",
}
PAUSE_TRIGGERS = (
    "official_body_stop_or_pause_order",
    "consent_or_privacy_breach",
    "unexpected_serious_intervention_related_harm",
    "randomization_or_data_integrity_failure",
    "registered_intervention_cannot_be_delivered",
)
POSITIVE_DETERMINATIONS = (
    "approved",
    "exempt_or_waived_with_explicit_authorization",
    "equivalent_official_authorization",
)
FORBIDDEN_PUBLIC_FIELDS = {
    "name",
    "email",
    "phone",
    "address",
    "legal_name",
    "participant_id",
    "participant_rows",
    "raw_participants",
    "consent_records",
    "audio_path",
    "recording_path",
    "official_document_path",
    "official_document_locator",
    "portal_url",
    "url",
    "filename",
    "file_name",
    "approval_number",
    "form_contents",
    "document_contents",
    "free_text",
    "comment",
    "signature_image",
}
TOP_LEVEL_CONFIG_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "official_approval",
    "official_document_attestation",
    "study_contract",
    "ethics_privacy_requirements",
    "unresolved_external_fields",
    "privacy_and_publication",
    "non_execution",
    "execution_order",
    "outputs",
}
CONFIG_SECTION_KEYS = {
    "experiment": {
        "id",
        "stage",
        "implementation_status",
        "official_determination_created_by_this_package",
        "institution_or_irb_decision_inferred",
        "portal_submission_allowed",
        "institution_contact_allowed",
        "participant_recruitment_allowed",
        "consent_collection_allowed",
        "audio_recording_allowed",
        "intervention_or_randomization_allowed",
        "production_or_completion_claim_allowed",
    },
    "canonical_sources": {"access_scope", "pins"},
    "official_approval": {
        "approval_path",
        "approval_sha256",
        "schema_path",
        "schema_sha256",
        "template_path",
        "template_sha256",
        "required_schema_version",
        "required_status",
        "accepted_positive_determination_categories",
        "official_document_and_institution_source_required",
        "actual_pilot_scope_required",
        "recruitment_consent_recording_authorization_required",
        "no_tbd_allowed_in_approved_artifact",
    },
    "official_document_attestation": {
        "attestation_path",
        "attestation_sha256",
        "schema_path",
        "schema_sha256",
        "template_path",
        "template_sha256",
        "required_schema_version",
        "required_status",
        "restricted_locator_sha256_only",
        "actual_locator_path_or_url_in_public_artifact_allowed",
        "authorized_custodian_verification_required",
        "no_tbd_allowed_in_approved_attestation",
    },
    "study_contract": {
        "comparisons",
        "design",
        "allocation_ratio",
        "stratification_factors",
        "allocation_concealment_required",
        "intervention_duration",
        "timepoints",
        "c0_c3_c5_manual_version_and_sha_required",
        "manual_freeze_before_recruitment_required",
        "participant_blinding_claim_allowed",
        "blind_outcome_raters_required",
    },
    "ethics_privacy_requirements": {
        "audio_personal_and_sensitive_data_scope_required",
        "purpose_limitation_required",
        "retention_access_deletion_withdrawal_required",
        "explicit_audio_recording_consent_required",
        "ai_incorrect_feedback_risk_disclosure_required",
        "human_escalation_required",
        "recruitment_compensation_minors_policy_required",
        "incident_response_required",
        "pseudonym_key_separation_required",
        "external_and_tts_transfer_policy_required",
        "pause_stop_resume_policy_required",
        "amendment_expiry_and_current_status_required",
        "c1_a1_public_summary_must_be_pii_free",
        "participant_row_or_raw_path_in_public_summary_allowed",
        "raw_or_identifiable_data_publication_allowed",
    },
    "unresolved_external_fields": {
        "institution_form_and_body",
        "official_determination_category",
        "official_determination_id_sha256",
        "protocol_version_and_sha256",
        "c0_c3_c5_manual_versions_and_sha256",
        "allocation_method_and_receipts",
        "retention_duration_or_trigger",
        "compensation_terms_and_withdrawal_treatment",
        "minors_policy_and_protections",
        "external_tts_cross_border_transfer_scope",
        "amendment_expiry_and_current_status",
        "official_signed_and_archive_receipts",
    },
    "privacy_and_publication": {
        "approval_artifact_zone",
        "attestation_artifact_zone",
        "full_official_document_zone",
        "full_official_document_read_allowed_by_this_package",
        "restricted_locator_resolution_allowed_by_this_package",
        "public_artifact_may_contain_only_hashed_institution_and_determination_ids",
        "forbidden_recursive_fields",
    },
    "non_execution": {
        "external_access_allowed",
        "network_access_allowed",
        "browser_access_allowed",
        "portal_submission_allowed",
        "institution_contact_allowed",
        "official_decision_generation_allowed",
        "participant_recruitment_allowed",
        "participant_consent_collection_allowed",
        "participant_randomization_allowed",
        "participant_row_access_allowed",
        "audio_recording_allowed",
        "audio_read_or_write_allowed",
        "personal_or_sensitive_data_access_allowed",
        "intervention_delivery_allowed",
        "model_access_allowed",
        "gpu_access_allowed",
        "external_or_tts_transfer_allowed",
        "output_write_allowed",
        "registry_or_commit_write_allowed",
        "completion_or_publication_claim_allowed",
    },
    "outputs": {
        "validation_report_path",
        "registry_path",
        "failure_report_path",
        "no_overwrite",
        "atomic_publish_required",
        "write_enabled_in_this_package",
    },
}


class H00ContractError(ValueError):
    """Raised before unsupported ethics, external, participant, or I/O action."""

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
            raise H00ContractError("YAML mapping keys must be strings")
        if key in result:
            raise H00ContractError(f"duplicate YAML key: {key}")
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
    approval_path_resolution_count: int = 0
    approval_read_attempt_count: int = 0
    approval_read_count: int = 0
    approval_deep_validation_count: int = 0
    attestation_path_resolution_count: int = 0
    attestation_read_attempt_count: int = 0
    attestation_read_count: int = 0
    attestation_deep_validation_count: int = 0
    restricted_official_document_locator_resolution_count: int = 0
    restricted_official_document_read_count: int = 0
    environment_read_count: int = 0
    external_access_count: int = 0
    network_access_count: int = 0
    browser_access_count: int = 0
    portal_submission_count: int = 0
    institution_contact_count: int = 0
    official_decision_generation_count: int = 0
    participant_recruitment_count: int = 0
    participant_consent_collection_count: int = 0
    participant_randomization_count: int = 0
    participant_row_read_count: int = 0
    audio_recording_count: int = 0
    audio_read_count: int = 0
    audio_write_count: int = 0
    personal_data_read_count: int = 0
    sensitive_data_read_count: int = 0
    intervention_delivery_count: int = 0
    model_access_count: int = 0
    gpu_access_count: int = 0
    external_or_tts_transfer_count: int = 0
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
class ApprovalEvidence:
    sha256: str
    document: Mapping[str, Any]
    snapshot: FileSnapshot | None


@dataclass(frozen=True)
class AttestationEvidence:
    sha256: str
    document: Mapping[str, Any]
    snapshot: FileSnapshot | None


@dataclass(frozen=True)
class PreflightResult:
    status: str
    config_sha256: str
    approval_sha256: str
    attestation_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise H00ContractError(f"{name} must be a string-keyed mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise H00ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise H00ContractError(f"{name} must be a non-empty string")
    return value


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if not SHA256_RE.fullmatch(text):
        raise H00ContractError(f"{name} must be a lowercase SHA-256")
    return text


def _bounded_id(value: object, name: str) -> str:
    text = _string(value, name)
    if not ID_RE.fullmatch(text):
        raise H00ContractError(f"{name} must be a bounded identifier")
    return text


def _version(value: object, name: str) -> str:
    text = _string(value, name)
    if not VERSION_RE.fullmatch(text):
        raise H00ContractError(f"{name} must be a bounded version")
    return text


def _utc(value: object, name: str) -> str:
    text = _string(value, name)
    if not UTC_RE.fullmatch(text):
        raise H00ContractError(f"{name} must be second-precision UTC")
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise H00ContractError(f"{name} must be a valid UTC timestamp") from exc
    return text


def _current_utc(value: object, name: str) -> str:
    text = _utc(value, name)
    parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if parsed > datetime.now(timezone.utc):
        raise H00ContractError(f"{name} cannot be in the future")
    return text


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise H00ContractError(f"{name} must be boolean")
    return value


def _nullable_sha256(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _sha256(value, name)


def _nullable_version(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _version(value, name)


def _validate_expiry(
    value: object,
    *,
    status_verified_at_utc: str,
    name: str,
) -> str:
    expiry = _string(value, name)
    if expiry == "no_expiry_stated_by_official_body":
        return expiry
    _utc(expiry, name)
    if expiry <= status_verified_at_utc:
        raise H00ContractError(f"{name} must be after status verification")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if expiry <= now:
        raise H00ContractError(f"{name} is not current")
    return expiry


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise H00ContractError(f"{name} drift: expected {expected!r}, got {actual!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise H00ContractError(
            f"{name} keys drift; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise H00ContractError(f"{name} contains non-finite number")
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
            raise H00ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise H00ContractError(f"non-finite JSON constant prohibited: {value}")


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


def binding_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {key: item for key, item in value.items() if key != "binding_sha256"}
        )
    ).hexdigest()


def _contains_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("TBD_")
    if isinstance(value, Mapping):
        return any(_contains_tbd(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tbd(item) for item in value)
    return False


def _collect_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_collect_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_collect_keys(item))
    return keys


def _reject_public_leakage(value: Mapping[str, Any], name: str) -> None:
    forbidden = _collect_keys(value).intersection(FORBIDDEN_PUBLIC_FIELDS)
    if forbidden:
        raise H00ContractError(
            f"{name} contains PII/participant/raw-locator fields: {sorted(forbidden)}"
        )


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in ("", ".", "..") for part in pure.parts)
        or "\\" in text
    ):
        raise H00ContractError(f"{name} must be a safe workspace-relative path")
    return text


def _reject_symlink_chain(
    root: Path,
    relative: str,
    name: str,
    *,
    require_leaf: bool,
) -> Path:
    relative = _relative_string(relative, name)
    current = root
    for index, part in enumerate(PurePosixPath(relative).parts):
        current = current / part
        leaf = index == len(PurePosixPath(relative).parts) - 1
        try:
            info = current.lstat()
        except FileNotFoundError:
            if require_leaf:
                raise H00ContractError(f"{name} missing: {relative}") from None
            return root / relative
        if stat.S_ISLNK(info.st_mode):
            raise H00ContractError(f"{name} symlink prohibited: {relative}")
        if not leaf and not stat.S_ISDIR(info.st_mode):
            raise H00ContractError(f"{name} parent is not a directory: {relative}")
        if leaf and require_leaf and not stat.S_ISREG(info.st_mode):
            raise H00ContractError(f"{name} must be a regular file: {relative}")
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
            raise H00ContractError(f"{name} is not a regular file")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise H00ContractError(f"{name} changed before open")
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
        raise H00ContractError(f"{name} changed while reading")
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
        raise H00ContractError(
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
        raise H00ContractError(f"{name} invalid JSON: {exc}") from exc
    payload = _mapping(value, name)
    _reject_nonfinite_tree(payload, name)
    return payload


def _load_config_bytes(data: bytes) -> Mapping[str, Any]:
    try:
        value = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise H00ContractError(f"invalid YAML config: {exc}") from exc
    payload = _mapping(value, "config")
    _reject_nonfinite_tree(payload, "config")
    return payload


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        return _load_config_bytes(path.read_bytes())
    except OSError as exc:
        raise H00ContractError(f"cannot read config {path}: {exc}") from exc


def _read_exact_json(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
    name: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    data, snapshot = _read_file(workspace_root, relative, name)
    if snapshot.sha256 != _sha256(expected_sha256, f"{name} expected SHA"):
        raise H00ContractError(f"{name} SHA-256 drift")
    return _load_json_bytes(data, name), snapshot


def _validate_approval_structure(
    payload: Mapping[str, Any],
    *,
    name: str,
) -> None:
    _reject_nonfinite_tree(payload, name)
    _reject_public_leakage(payload, name)
    _expect_keys(payload, APPROVAL_KEYS, name)
    for section_name, keys in APPROVAL_NESTED_KEYS.items():
        section = _mapping(payload.get(section_name), f"{name}.{section_name}")
        _expect_keys(section, keys, f"{name}.{section_name}")
    manuals = _mapping(payload.get("intervention_manuals"), f"{name}.manuals")
    for condition in ("c0", "c3", "c5"):
        manual = _mapping(manuals.get(condition), f"{name}.manuals.{condition}")
        _expect_keys(manual, MANUAL_KEYS, f"{name}.manuals.{condition}")


def _validate_attestation_structure(
    payload: Mapping[str, Any],
    *,
    name: str,
) -> None:
    _reject_nonfinite_tree(payload, name)
    _reject_public_leakage(payload, name)
    _expect_keys(payload, ATTESTATION_KEYS, name)
    verification = _mapping(payload.get("verification"), f"{name}.verification")
    lifecycle = _mapping(payload.get("lifecycle"), f"{name}.lifecycle")
    _expect_keys(
        verification,
        ATTESTATION_VERIFICATION_KEYS,
        f"{name}.verification",
    )
    _expect_keys(
        lifecycle,
        ATTESTATION_LIFECYCLE_KEYS,
        f"{name}.lifecycle",
    )


def _validate_approval_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        "approval schema dialect",
    )
    _expect(schema.get("type"), "object", "approval schema root type")
    _expect(
        schema.get("additionalProperties"),
        False,
        "approval schema root strictness",
    )
    _expect(
        set(_list(schema.get("required"), "approval schema required")),
        APPROVAL_KEYS,
        "approval schema required",
    )
    _expect(
        set(_mapping(schema.get("properties"), "approval schema properties")),
        APPROVAL_KEYS,
        "approval schema properties",
    )
    definitions = _mapping(schema.get("$defs"), "approval schema definitions")
    definition_map = {
        "institutionEvidence": APPROVAL_NESTED_KEYS["institution_evidence"],
        "protocol": APPROVAL_NESTED_KEYS["protocol"],
        "studyScope": APPROVAL_NESTED_KEYS["study_scope"],
        "interventionManuals": APPROVAL_NESTED_KEYS["intervention_manuals"],
        "randomization": APPROVAL_NESTED_KEYS["randomization_and_concealment"],
        "dataGovernance": APPROVAL_NESTED_KEYS["data_governance"],
        "participantRights": APPROVAL_NESTED_KEYS[
            "consent_withdrawal_and_participant_rights"
        ],
        "recruitment": APPROVAL_NESTED_KEYS["recruitment_compensation_and_minors"],
        "aiSafety": APPROVAL_NESTED_KEYS["ai_feedback_and_safety"],
        "pseudonymizationTransfer": APPROVAL_NESTED_KEYS[
            "pseudonymization_and_transfer"
        ],
        "incidentPauseStop": APPROVAL_NESTED_KEYS["incident_pause_stop"],
        "lifecycle": APPROVAL_NESTED_KEYS["lifecycle"],
        "authorization": APPROVAL_NESTED_KEYS["authorization"],
        "manual": MANUAL_KEYS,
    }
    for definition_name, keys in definition_map.items():
        definition = _mapping(
            definitions.get(definition_name),
            f"approval schema {definition_name}",
        )
        _expect(
            definition.get("additionalProperties"),
            False,
            f"approval schema {definition_name} strictness",
        )
        _expect(
            set(_list(definition.get("required"), f"{definition_name} required")),
            keys,
            f"approval schema {definition_name} required",
        )
        _expect(
            set(
                _mapping(
                    definition.get("properties"),
                    f"{definition_name} properties",
                )
            ),
            keys,
            f"approval schema {definition_name} properties",
        )
    scope = _mapping(definitions.get("studyScope"), "approval study scope schema")
    scope_properties = _mapping(scope.get("properties"), "study scope properties")
    _expect(
        _mapping(scope_properties.get("comparisons"), "comparisons schema").get(
            "const"
        ),
        ["c3_minus_c0", "c5_minus_c3"],
        "approval schema comparisons",
    )
    _expect(
        _mapping(scope_properties.get("design"), "design schema").get("const"),
        "participant_level_parallel_1_to_1_randomized",
        "approval schema design",
    )
    pause = _mapping(
        definitions.get("incidentPauseStop"),
        "approval pause schema",
    )
    pause_properties = _mapping(pause.get("properties"), "pause properties")
    _expect(
        tuple(
            _list(
                _mapping(
                    pause_properties.get("pause_stop_triggers"),
                    "pause triggers schema",
                ).get("const"),
                "pause trigger constants",
            )
        ),
        PAUSE_TRIGGERS,
        "approval schema pause triggers",
    )


def _validate_attestation_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        "attestation schema dialect",
    )
    _expect(schema.get("type"), "object", "attestation schema root type")
    _expect(
        schema.get("additionalProperties"),
        False,
        "attestation schema root strictness",
    )
    _expect(
        set(_list(schema.get("required"), "attestation schema required")),
        ATTESTATION_KEYS,
        "attestation schema required",
    )
    _expect(
        set(_mapping(schema.get("properties"), "attestation schema properties")),
        ATTESTATION_KEYS,
        "attestation schema properties",
    )
    definitions = _mapping(schema.get("$defs"), "attestation schema definitions")
    for definition_name, keys in (
        ("verification", ATTESTATION_VERIFICATION_KEYS),
        ("lifecycle", ATTESTATION_LIFECYCLE_KEYS),
    ):
        definition = _mapping(
            definitions.get(definition_name),
            f"attestation schema {definition_name}",
        )
        _expect(
            definition.get("additionalProperties"),
            False,
            f"attestation schema {definition_name} strictness",
        )
        _expect(
            set(_list(definition.get("required"), f"{definition_name} required")),
            keys,
            f"attestation schema {definition_name} required",
        )
        _expect(
            set(
                _mapping(
                    definition.get("properties"),
                    f"{definition_name} properties",
                )
            ),
            keys,
            f"attestation schema {definition_name} properties",
        )


def _validate_approval_template(payload: Mapping[str, Any]) -> None:
    _validate_approval_structure(payload, name="H00 approval template")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h00_human_research_approval_public.v1",
        "approval template schema",
    )
    _expect(
        payload.get("approval_artifact_id"),
        TBD_H00_APPROVAL,
        "approval template ID",
    )
    _expect(
        payload.get("privacy_zone"),
        "A1_C1_public_summary_only",
        "approval template privacy zone",
    )
    _expect(payload.get("public_summary_only"), True, "approval public summary")
    for field in (
        "actual_pilot_covered",
        "consent_approved",
        "privacy_approved",
        "retention_approved",
        "deletion_approved",
    ):
        _expect(payload.get(field), False, f"approval template {field}")
    institution = _mapping(
        payload.get("institution_evidence"),
        "approval template institution",
    )
    _expect(
        institution.get("restricted_document_attestation_sha256"),
        TBD_H00_ATTESTATION,
        "approval template attestation binding",
    )
    _expect(
        institution.get("official_source_verified"),
        False,
        "approval template official source",
    )
    scope = _mapping(payload.get("study_scope"), "approval template scope")
    _expect(
        tuple(_list(scope.get("comparisons"), "approval template comparisons")),
        ("c3_minus_c0", "c5_minus_c3"),
        "approval template comparisons",
    )
    _expect(
        scope.get("design"),
        "participant_level_parallel_1_to_1_randomized",
        "approval template design",
    )
    for field in (
        "c0_covered",
        "c3_covered",
        "c5_covered",
        "participant_recruitment_authorized",
        "informed_consent_authorized",
        "audio_recording_authorized",
        "randomized_intervention_authorized",
        "blind_rating_authorized",
        "d1_pseudonymized_processing_authorized",
        "a1_aggregate_analysis_authorized",
        "confirmatory_study_covered",
    ):
        _expect(scope.get(field), False, f"approval template scope.{field}")
    randomization = _mapping(
        payload.get("randomization_and_concealment"),
        "approval template randomization",
    )
    _expect(randomization.get("unit"), "participant", "randomization unit")
    _expect(randomization.get("allocation_ratio"), "1:1", "allocation ratio")
    _expect(
        tuple(
            _list(
                randomization.get("stratification_factors"),
                "stratification factors",
            )
        ),
        ("l1", "baseline_comprehensibility_band"),
        "stratification factors",
    )
    _expect(
        randomization.get("participant_blinding_claimed"),
        False,
        "participant blinding boundary",
    )
    pause = _mapping(payload.get("incident_pause_stop"), "approval template pause")
    _expect(
        tuple(_list(pause.get("pause_stop_triggers"), "pause triggers")),
        PAUSE_TRIGGERS,
        "pause triggers",
    )
    authorization = _mapping(
        payload.get("authorization"),
        "approval template authorization",
    )
    for field in (
        "official_determination_verified",
        "recruitment_execution_authorized",
        "consent_execution_authorized",
        "recording_execution_authorized",
    ):
        _expect(
            authorization.get(field),
            False,
            f"approval template authorization.{field}",
        )
    if not _contains_tbd(payload):
        raise H00ContractError("approval template must retain unresolved TBD values")


def _validate_attestation_template(payload: Mapping[str, Any]) -> None:
    _validate_attestation_structure(payload, name="H00 attestation template")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h00_official_document_attestation.v1",
        "attestation template schema",
    )
    _expect(
        payload.get("attestation_id"),
        TBD_H00_ATTESTATION,
        "attestation template ID",
    )
    _expect(
        payload.get("privacy_zone"),
        "A1_C1_public_summary_only",
        "attestation template privacy zone",
    )
    _expect(payload.get("public_summary_only"), True, "attestation public summary")
    verification = _mapping(
        payload.get("verification"),
        "attestation template verification",
    )
    for field in ATTESTATION_VERIFICATION_KEYS:
        _expect(verification.get(field), False, f"attestation template {field}")
    lifecycle = _mapping(
        payload.get("lifecycle"),
        "attestation template lifecycle",
    )
    _expect(
        lifecycle.get("attestation_current"),
        False,
        "attestation template current status",
    )
    if not _contains_tbd(payload):
        raise H00ContractError("attestation template must retain unresolved TBD")


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_CONFIG_KEYS, "H00 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "config schema")
    for section_name, expected_keys in CONFIG_SECTION_KEYS.items():
        _expect_keys(
            _mapping(config.get(section_name), section_name),
            expected_keys,
            section_name,
        )
    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(experiment.get("id"), "h00_human_research_approval_v1", "experiment ID")
    _expect(
        experiment.get("stage"),
        "wp9_h00_official_human_research_determination",
        "experiment stage",
    )
    _expect(
        experiment.get("implementation_status"),
        STATIC_STATUS,
        "implementation status",
    )
    for field in (
        "official_determination_created_by_this_package",
        "institution_or_irb_decision_inferred",
        "portal_submission_allowed",
        "institution_contact_allowed",
        "participant_recruitment_allowed",
        "consent_collection_allowed",
        "audio_recording_allowed",
        "intervention_or_randomization_allowed",
        "production_or_completion_claim_allowed",
    ):
        _expect(experiment.get(field), False, f"experiment.{field}")

    approval = _mapping(config.get("official_approval"), "official approval")
    approval_sha = _string(approval.get("approval_sha256"), "approval SHA")
    if approval_sha != TBD_H00_APPROVAL:
        _sha256(approval_sha, "approval SHA")
    _expect(
        approval.get("required_schema_version"),
        "voice_coach.h00_human_research_approval_public.v1",
        "approval schema version",
    )
    _expect(
        approval.get("required_status"),
        "active_official_determination",
        "approval required status",
    )
    _expect(
        tuple(
            _list(
                approval.get("accepted_positive_determination_categories"),
                "positive determination categories",
            )
        ),
        POSITIVE_DETERMINATIONS,
        "positive determination categories",
    )
    for field in (
        "official_document_and_institution_source_required",
        "actual_pilot_scope_required",
        "recruitment_consent_recording_authorization_required",
        "no_tbd_allowed_in_approved_artifact",
    ):
        _expect(approval.get(field), True, f"official approval.{field}")
    for field in (
        "approval_path",
        "schema_path",
        "template_path",
    ):
        _relative_string(approval.get(field), f"official approval.{field}")
    for field in ("schema_sha256", "template_sha256"):
        _sha256(approval.get(field), f"official approval.{field}")

    attestation = _mapping(
        config.get("official_document_attestation"),
        "official document attestation",
    )
    attestation_sha = _string(
        attestation.get("attestation_sha256"),
        "attestation SHA",
    )
    if attestation_sha != TBD_H00_ATTESTATION:
        _sha256(attestation_sha, "attestation SHA")
    _expect(
        attestation.get("required_schema_version"),
        "voice_coach.h00_official_document_attestation.v1",
        "attestation schema version",
    )
    _expect(
        attestation.get("required_status"),
        "attested_and_active",
        "attestation required status",
    )
    for field in (
        "restricted_locator_sha256_only",
        "authorized_custodian_verification_required",
        "no_tbd_allowed_in_approved_attestation",
    ):
        _expect(attestation.get(field), True, f"attestation.{field}")
    _expect(
        attestation.get("actual_locator_path_or_url_in_public_artifact_allowed"),
        False,
        "attestation raw locator boundary",
    )
    for field in ("attestation_path", "schema_path", "template_path"):
        _relative_string(attestation.get(field), f"attestation.{field}")
    for field in ("schema_sha256", "template_sha256"):
        _sha256(attestation.get(field), f"attestation.{field}")

    study = _mapping(config.get("study_contract"), "study contract")
    _expect(
        tuple(_list(study.get("comparisons"), "study comparisons")),
        ("c3_minus_c0", "c5_minus_c3"),
        "study comparisons",
    )
    _expect(
        study.get("design"),
        "participant_level_parallel_1_to_1_randomized",
        "study design",
    )
    _expect(study.get("allocation_ratio"), "1:1", "allocation ratio")
    _expect(
        tuple(_list(study.get("stratification_factors"), "stratification")),
        ("l1", "baseline_comprehensibility_band"),
        "stratification",
    )
    _expect(study.get("intervention_duration"), "P2W", "intervention duration")
    _expect(
        tuple(_list(study.get("timepoints"), "study timepoints")),
        ("pre", "post_at_2_weeks", "retention_4_weeks_after_post"),
        "study timepoints",
    )
    for field in (
        "allocation_concealment_required",
        "c0_c3_c5_manual_version_and_sha_required",
        "manual_freeze_before_recruitment_required",
        "blind_outcome_raters_required",
    ):
        _expect(study.get(field), True, f"study contract.{field}")
    _expect(
        study.get("participant_blinding_claim_allowed"),
        False,
        "participant blinding boundary",
    )

    ethics = _mapping(
        config.get("ethics_privacy_requirements"),
        "ethics privacy requirements",
    )
    for field in CONFIG_SECTION_KEYS["ethics_privacy_requirements"] - {
        "participant_row_or_raw_path_in_public_summary_allowed",
        "raw_or_identifiable_data_publication_allowed",
    }:
        _expect(ethics.get(field), True, f"ethics.{field}")
    for field in (
        "participant_row_or_raw_path_in_public_summary_allowed",
        "raw_or_identifiable_data_publication_allowed",
    ):
        _expect(ethics.get(field), False, f"ethics.{field}")

    unresolved = _mapping(
        config.get("unresolved_external_fields"),
        "unresolved external fields",
    )
    for field, value in unresolved.items():
        expected = (
            "TBD_PREREGISTRATION"
            if field
            in {
                "c0_c3_c5_manual_versions_and_sha256",
                "allocation_method_and_receipts",
            }
            else "TBD_EXTERNAL"
        )
        _expect(value, expected, f"unresolved external fields.{field}")

    privacy = _mapping(
        config.get("privacy_and_publication"),
        "privacy and publication",
    )
    _expect(
        privacy.get("approval_artifact_zone"),
        "A1_C1_public_summary_only",
        "approval zone",
    )
    _expect(
        privacy.get("attestation_artifact_zone"),
        "A1_C1_public_summary_only",
        "attestation zone",
    )
    _expect(
        privacy.get("full_official_document_zone"),
        "R1_restricted_official_archive",
        "official document zone",
    )
    for field in (
        "full_official_document_read_allowed_by_this_package",
        "restricted_locator_resolution_allowed_by_this_package",
    ):
        _expect(privacy.get(field), False, f"privacy.{field}")
    _expect(
        privacy.get(
            "public_artifact_may_contain_only_hashed_institution_and_determination_ids"
        ),
        True,
        "public institution/determination ID boundary",
    )
    _expect(
        set(
            _list(
                privacy.get("forbidden_recursive_fields"),
                "forbidden public fields",
            )
        ),
        FORBIDDEN_PUBLIC_FIELDS,
        "forbidden public fields",
    )

    non_execution = _mapping(config.get("non_execution"), "non execution")
    for field in CONFIG_SECTION_KEYS["non_execution"]:
        _expect(non_execution.get(field), False, f"non_execution.{field}")
    _expect(
        tuple(_list(config.get("execution_order"), "execution order")),
        EXPECTED_EXECUTION_ORDER,
        "execution order",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    for field in ("validation_report_path", "registry_path", "failure_report_path"):
        _relative_string(outputs.get(field), f"outputs.{field}")
    _expect(outputs.get("no_overwrite"), True, "output no-overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "output atomic publish")
    _expect(
        outputs.get("write_enabled_in_this_package"),
        False,
        "output write boundary",
    )


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    """Validate only pinned C1/A1 sources; never resolve external evidence."""

    root = workspace_root.resolve(strict=True)
    access = audit if audit is not None else AccessAudit()
    _validate_config_values(config)
    absolute_config = config_path if config_path.is_absolute() else root / config_path
    try:
        config_relative = absolute_config.relative_to(root).as_posix()
    except ValueError as exc:
        raise H00ContractError("H00 config must be inside workspace") from exc
    config_data, config_snapshot = _read_file(root, config_relative, "H00 config")
    if _load_config_bytes(config_data) != config:
        raise H00ContractError("H00 config changed between load and validation")

    canonical = _mapping(config.get("canonical_sources"), "canonical sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_or_A1_aggregate_static_pins_only",
        "canonical source scope",
    )
    pins = _list(canonical.get("pins"), "canonical source pins")
    if len(pins) != len(SOURCE_PATHS):
        raise H00ContractError("canonical source pin count drift")
    seen: set[str] = set()
    snapshots: list[FileSnapshot] = []
    payload_by_path: dict[str, bytes] = {}
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        if relative in seen:
            raise H00ContractError(f"duplicate source pin: {relative}")
        seen.add(relative)
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_sha:
            raise H00ContractError(
                f"canonical source SHA-256 drift: {relative}",
                stage="source_pin",
                audit=access,
            )
        snapshots.append(snapshot)
        payload_by_path[relative] = data
    if tuple(sorted(seen)) != tuple(sorted(SOURCE_PATHS)):
        raise H00ContractError("canonical source path set drift")
    by_path = {snapshot.relative_path: snapshot for snapshot in snapshots}

    approval_schema_path = (
        "configs/human_research/h00_human_research_approval_public_v1.schema.json"
    )
    approval_template_path = (
        "configs/human_research/h00_human_research_approval_public_v1.template.json"
    )
    attestation_schema_path = (
        "configs/human_research/h00_official_document_attestation_v1.schema.json"
    )
    attestation_template_path = (
        "configs/human_research/h00_official_document_attestation_v1.template.json"
    )
    _validate_approval_schema(
        _load_json_bytes(
            payload_by_path[approval_schema_path],
            "H00 approval schema",
        )
    )
    _validate_attestation_schema(
        _load_json_bytes(
            payload_by_path[attestation_schema_path],
            "H00 attestation schema",
        )
    )
    _validate_approval_template(
        _load_json_bytes(
            payload_by_path[approval_template_path],
            "H00 approval template",
        )
    )
    _validate_attestation_template(
        _load_json_bytes(
            payload_by_path[attestation_template_path],
            "H00 attestation template",
        )
    )

    approval = _mapping(config.get("official_approval"), "official approval")
    for config_field, expected_path in (
        ("schema_path", approval_schema_path),
        ("template_path", approval_template_path),
    ):
        _expect(
            approval.get(config_field),
            expected_path,
            f"official approval.{config_field}",
        )
        sha_field = config_field.replace("_path", "_sha256")
        _expect(
            approval.get(sha_field),
            by_path[expected_path].sha256,
            f"official approval {sha_field}/source binding",
        )
    attestation = _mapping(
        config.get("official_document_attestation"),
        "official document attestation",
    )
    for config_field, expected_path in (
        ("schema_path", attestation_schema_path),
        ("template_path", attestation_template_path),
    ):
        _expect(
            attestation.get(config_field),
            expected_path,
            f"attestation.{config_field}",
        )
        sha_field = config_field.replace("_path", "_sha256")
        _expect(
            attestation.get(sha_field),
            by_path[expected_path].sha256,
            f"attestation {sha_field}/source binding",
        )

    planning = _load_json_bytes(
        payload_by_path["artifacts/planning/learning_effect_power_sensitivity_v1.json"],
        "H01 planning artifact",
    )
    _expect(
        planning.get("schema_version"),
        "voice_coach.learning_effect_power_plan.v1",
        "H01 planning schema",
    )
    _expect(
        planning.get("status"),
        "assumed_sensitivity_only",
        "H01 planning status",
    )
    _expect(planning.get("final_sample_size"), None, "H01 final sample size")
    _expect(
        planning.get("final_sample_size_locked"),
        False,
        "H01 sample-size lock",
    )
    _expect(
        planning.get("h01_completion_claim_allowed"),
        False,
        "H01 completion boundary",
    )
    _expect(
        len(_list(planning.get("pilot_informed_scenarios"), "pilot scenarios")),
        0,
        "H01 empirical pilot scenario count",
    )
    external = _mapping(planning.get("external_status"), "H01 external status")
    for field in (
        "confirmatory_effect_observed",
        "empirical_pilot_input_supplied",
        "irb_or_equivalent_approval_obtained",
        "learner_recordings_collected",
        "participants_recruited",
    ):
        _expect(external.get(field), False, f"H01 external status.{field}")
    privacy = _mapping(planning.get("privacy"), "H01 planning privacy")
    _expect(privacy.get("aggregate_only_output"), True, "H01 aggregate boundary")
    for field in (
        "environment_files_read",
        "identifiers_written",
        "participant_rows_read",
        "raw_or_restricted_data_read",
    ):
        _expect(privacy.get(field), False, f"H01 privacy.{field}")

    outputs = _mapping(config.get("outputs"), "outputs")
    output_paths = [
        _relative_string(value, f"outputs.{field}")
        for field, value in outputs.items()
        if field.endswith("_path")
    ]
    if len(output_paths) != len(set(output_paths)):
        raise H00ContractError("H00 output paths must be unique")
    for output in output_paths:
        output_parts = PurePosixPath(output).parts
        for source in seen:
            source_parts = PurePosixPath(source).parts
            if (
                output_parts == source_parts
                or output_parts[: len(source_parts)] == source_parts
                or source_parts[: len(output_parts)] == output_parts
            ):
                raise H00ContractError(
                    f"H00 output/source overlap prohibited: {output} / {source}"
                )
    policy = {
        "official_approval": config["official_approval"],
        "official_document_attestation": config["official_document_attestation"],
        "study_contract": config["study_contract"],
        "ethics_privacy_requirements": config["ethics_privacy_requirements"],
        "privacy_and_publication": config["privacy_and_publication"],
        "non_execution": config["non_execution"],
        "execution_order": config["execution_order"],
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
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
        except H00ContractError as exc:
            raise H00ContractError(
                str(exc),
                stage=f"{stage}_toctou",
                audit=audit,
            ) from exc
        audit.source_toctou_verification_count += 1


def validate_approval_payload(
    payload: Mapping[str, Any],
    *,
    accepted_positive_determinations: Sequence[str] = POSITIVE_DETERMINATIONS,
) -> ApprovalEvidence:
    """Validate an active official determination without inferring its category."""

    _validate_approval_structure(payload, name="H00 public approval")
    if _contains_tbd(payload):
        raise H00ContractError("H00 approved artifact contains unresolved TBD")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h00_human_research_approval_public.v1",
        "approval schema",
    )
    approval_id = _string(payload.get("approval_artifact_id"), "approval ID")
    if not re.fullmatch(r"h00approval_[0-9a-f]{32}", approval_id):
        raise H00ContractError("invalid H00 approval artifact ID")
    _expect(
        payload.get("status"),
        "active_official_determination",
        "approval status",
    )
    _expect(
        payload.get("privacy_zone"),
        "A1_C1_public_summary_only",
        "approval privacy zone",
    )
    _expect(payload.get("public_summary_only"), True, "approval public summary")
    for field in (
        "actual_pilot_covered",
        "consent_approved",
        "privacy_approved",
        "retention_approved",
        "deletion_approved",
    ):
        _expect(payload.get(field), True, f"approval.{field}")

    institution = _mapping(payload.get("institution_evidence"), "institution")
    for field in (
        "institution_id_sha256",
        "official_determination_id_sha256",
        "institution_form_sha256",
        "official_determination_document_sha256",
        "restricted_document_attestation_sha256",
    ):
        _sha256(institution.get(field), f"institution.{field}")
    role = _string(institution.get("approving_body_role"), "approving body role")
    if role not in {
        "institutional_review_board",
        "research_ethics_committee",
        "equivalent_official_research_governance_body",
    }:
        raise H00ContractError("unsupported approving body role")
    category = _string(
        institution.get("official_determination_category"),
        "official determination category",
    )
    accepted = tuple(accepted_positive_determinations)
    if accepted != POSITIVE_DETERMINATIONS:
        raise H00ContractError("positive determination policy drift")
    if category not in accepted:
        raise H00ContractError(
            "official determination does not explicitly authorize H00 scope"
        )
    _expect(
        institution.get("official_source_verified"),
        True,
        "institution official source verification",
    )

    protocol = _mapping(payload.get("protocol"), "protocol")
    for field in (
        "protocol_id_sha256",
        "protocol_sha256",
        "application_bundle_sha256",
        "consent_form_sha256",
        "privacy_impact_assessment_sha256",
        "data_management_plan_sha256",
    ):
        _sha256(protocol.get(field), f"protocol.{field}")
    _version(protocol.get("protocol_version"), "protocol version")
    _version(protocol.get("consent_form_version"), "consent form version")

    scope = _mapping(payload.get("study_scope"), "study scope")
    _expect(
        tuple(_list(scope.get("comparisons"), "study comparisons")),
        ("c3_minus_c0", "c5_minus_c3"),
        "study comparisons",
    )
    _expect(
        scope.get("design"),
        "participant_level_parallel_1_to_1_randomized",
        "study design",
    )
    _expect(scope.get("intervention_duration"), "P2W", "intervention duration")
    _expect(
        tuple(_list(scope.get("timepoints"), "study timepoints")),
        ("pre", "post_at_2_weeks", "retention_4_weeks_after_post"),
        "study timepoints",
    )
    for field in (
        "c0_covered",
        "c3_covered",
        "c5_covered",
        "participant_recruitment_authorized",
        "informed_consent_authorized",
        "audio_recording_authorized",
        "randomized_intervention_authorized",
        "blind_rating_authorized",
        "d1_pseudonymized_processing_authorized",
        "a1_aggregate_analysis_authorized",
    ):
        _expect(scope.get(field), True, f"study scope.{field}")
    _boolean(scope.get("confirmatory_study_covered"), "confirmatory study scope")

    manuals = _mapping(payload.get("intervention_manuals"), "manuals")
    for condition in ("c0", "c3", "c5"):
        manual = _mapping(manuals.get(condition), f"manual {condition}")
        _expect(manual.get("condition"), condition, f"manual {condition} identity")
        _version(manual.get("version"), f"manual {condition} version")
        _sha256(manual.get("manual_sha256"), f"manual {condition} SHA")
        _expect(
            manual.get("frozen_before_recruitment"),
            True,
            f"manual {condition} freeze",
        )

    randomization = _mapping(
        payload.get("randomization_and_concealment"),
        "randomization",
    )
    _expect(randomization.get("unit"), "participant", "randomization unit")
    _expect(randomization.get("allocation_ratio"), "1:1", "allocation ratio")
    _expect(randomization.get("parallel_design"), True, "parallel design")
    _expect(
        tuple(_list(randomization.get("stratification_factors"), "strata")),
        ("l1", "baseline_comprehensibility_band"),
        "randomization strata",
    )
    _expect(
        randomization.get("allocation_concealment_required"),
        True,
        "allocation concealment",
    )
    _bounded_id(randomization.get("allocation_method_id"), "allocation method")
    for field in (
        "allocation_implementation_receipt_sha256",
        "randomization_seed_custody_receipt_sha256",
    ):
        _sha256(randomization.get(field), f"randomization.{field}")
    _bounded_id(
        randomization.get("independent_allocation_role_id"),
        "allocation role",
    )
    _expect(
        randomization.get("blind_outcome_raters"),
        True,
        "blind outcome raters",
    )
    _expect(
        randomization.get("participant_blinding_claimed"),
        False,
        "participant blinding claim",
    )

    governance = _mapping(payload.get("data_governance"), "data governance")
    for field in (
        "audio_recording_in_scope",
        "personal_data_in_scope",
        "sensitive_or_special_category_review_completed",
        "purpose_limitation_required",
        "access_logging_required",
        "backup_deletion_covered",
    ):
        _expect(governance.get(field), True, f"data governance.{field}")
    _expect(
        tuple(_list(governance.get("approved_purpose_ids"), "approved purposes")),
        (
            "learning_effect_evaluation",
            "blind_pronunciation_rating",
            "retention_analysis",
        ),
        "approved purposes",
    )
    for field in (
        "personal_data_categories_sha256",
        "sensitive_data_categories_sha256",
        "retention_policy_sha256",
        "authorized_role_matrix_sha256",
        "deletion_protocol_sha256",
    ):
        _sha256(governance.get(field), f"data governance.{field}")
    _bounded_id(
        governance.get("retention_duration_or_trigger_code"),
        "retention duration or trigger",
    )
    _bounded_id(governance.get("deletion_method_id"), "deletion method")
    _expect(
        tuple(_list(governance.get("data_zones"), "data zones")),
        (
            "R0_restricted_raw",
            "R1_restricted_operations",
            "D1_pseudonymized_experiment",
            "A1_aggregate",
        ),
        "data zones",
    )
    _expect(
        governance.get("raw_or_identifiable_data_publication_allowed"),
        False,
        "raw data publication boundary",
    )

    rights = _mapping(
        payload.get("consent_withdrawal_and_participant_rights"),
        "participant rights",
    )
    for field in (
        "informed_consent_required",
        "explicit_audio_recording_consent_required",
        "ai_incorrect_feedback_risk_disclosed",
        "withdrawal_without_penalty_allowed",
        "consent_language_accessibility_reviewed",
    ):
        _expect(rights.get(field), True, f"participant rights.{field}")
    for field in (
        "withdrawal_protocol_sha256",
        "deletion_request_route_sha256",
        "human_contact_and_escalation_route_sha256",
    ):
        _sha256(rights.get(field), f"participant rights.{field}")
    _bounded_id(
        rights.get("withdrawal_existing_data_handling_code"),
        "withdrawal existing-data handling",
    )

    recruitment = _mapping(
        payload.get("recruitment_compensation_and_minors"),
        "recruitment",
    )
    for field in (
        "recruitment_materials_sha256",
        "eligibility_protocol_sha256",
        "compensation_protocol_sha256",
    ):
        _sha256(recruitment.get(field), f"recruitment.{field}")
    _expect(
        recruitment.get("undue_influence_review_completed"),
        True,
        "undue influence review",
    )
    _bounded_id(recruitment.get("compensation_terms_code"), "compensation terms")
    _bounded_id(
        recruitment.get("compensation_withdrawal_treatment_code"),
        "compensation withdrawal treatment",
    )
    minors_policy = _string(recruitment.get("minors_policy"), "minors policy")
    guardian = recruitment.get("guardian_consent_protocol_sha256")
    assent = recruitment.get("minor_assent_protocol_sha256")
    if minors_policy == "excluded":
        _expect(guardian, None, "excluded-minor guardian consent")
        _expect(assent, None, "excluded-minor assent")
    elif minors_policy == "included_only_under_separately_approved_protections":
        _sha256(guardian, "guardian consent protocol")
        _sha256(assent, "minor assent protocol")
    else:
        raise H00ContractError("invalid minors policy")

    safety = _mapping(payload.get("ai_feedback_and_safety"), "AI safety")
    for field in (
        "incorrect_feedback_risk_documented",
        "human_escalation_available",
        "unsupported_claim_abstention_required",
        "identity_inference_prohibited",
        "medical_judgment_prohibited",
    ):
        _expect(safety.get(field), True, f"AI safety.{field}")
    for field in (
        "feedback_safety_protocol_sha256",
        "human_escalation_protocol_sha256",
        "adverse_feedback_monitoring_protocol_sha256",
    ):
        _sha256(safety.get(field), f"AI safety.{field}")

    transfer = _mapping(
        payload.get("pseudonymization_and_transfer"),
        "pseudonymization and transfer",
    )
    for field in (
        "study_namespaced_pseudonymous_ids_required",
        "pseudonym_key_mapping_separated",
        "minimum_necessary_transfer_required",
    ):
        _expect(transfer.get(field), True, f"transfer.{field}")
    _expect(
        transfer.get("pseudonym_key_mapping_zone"),
        "R1_restricted_separate_key_store",
        "pseudonym key zone",
    )
    _sha256(
        transfer.get("key_custodian_role_receipt_sha256"),
        "key custodian receipt",
    )
    _expect(
        transfer.get("direct_identifiers_in_public_artifact_allowed"),
        False,
        "public direct identifier boundary",
    )
    transfer_flags = [
        _boolean(transfer.get(field), f"transfer.{field}")
        for field in (
            "external_service_transfer_allowed",
            "tts_service_transfer_allowed",
            "cross_border_transfer_allowed",
        )
    ]
    _sha256(transfer.get("transfer_policy_sha256"), "transfer policy")
    _sha256(
        transfer.get("approved_provider_scope_sha256"),
        "approved provider scope",
    )
    consent_required = _boolean(
        transfer.get("separate_transfer_consent_required"),
        "separate transfer consent required",
    )
    consent_approved = _boolean(
        transfer.get("separate_transfer_consent_approved"),
        "separate transfer consent approved",
    )
    if any(transfer_flags) and (not consent_required or not consent_approved):
        raise H00ContractError(
            "approved external/TTS transfer requires separate approved consent"
        )

    incident = _mapping(payload.get("incident_pause_stop"), "incident pause/stop")
    for field in (
        "incident_response_protocol_sha256",
        "breach_notification_protocol_sha256",
        "stop_protocol_sha256",
        "resume_protocol_sha256",
    ):
        _sha256(incident.get(field), f"incident.{field}")
    _expect(
        tuple(_list(incident.get("pause_stop_triggers"), "pause/stop triggers")),
        PAUSE_TRIGGERS,
        "pause/stop triggers",
    )
    _expect(incident.get("pause_on_trigger_required"), True, "pause trigger action")
    _expect(
        incident.get("resume_requires_official_body_and_principal_investigator"),
        True,
        "resume authorization",
    )

    lifecycle = _mapping(payload.get("lifecycle"), "approval lifecycle")
    approved_at = _utc(lifecycle.get("approved_at_utc"), "approved at")
    effective_at = _current_utc(lifecycle.get("effective_at_utc"), "effective at")
    status_at = _current_utc(
        lifecycle.get("status_verified_at_utc"),
        "status verified at",
    )
    if approved_at > effective_at or effective_at > status_at:
        raise H00ContractError("approval lifecycle timestamps are out of order")
    _validate_expiry(
        lifecycle.get("expires_at_utc_or_status_code"),
        status_verified_at_utc=status_at,
        name="approval expiry",
    )
    amendment_status = _string(
        lifecycle.get("amendment_status"),
        "amendment status",
    )
    amendment_version = _nullable_version(
        lifecycle.get("current_amendment_version"),
        "amendment version",
    )
    amendment_sha = _nullable_sha256(
        lifecycle.get("current_amendment_sha256"),
        "amendment SHA",
    )
    if amendment_status == "no_amendment":
        if amendment_version is not None or amendment_sha is not None:
            raise H00ContractError("no-amendment lifecycle must use null fields")
    elif amendment_status == "approved_amendment_active":
        if amendment_version is None or amendment_sha is None:
            raise H00ContractError("approved amendment requires version and SHA")
    else:
        raise H00ContractError("invalid amendment status")
    _expect(
        lifecycle.get("scope_change_requires_prior_amendment"),
        True,
        "scope-change amendment",
    )
    _expect(
        lifecycle.get("expiration_action"),
        "stop_recruitment_intervention_and_recording",
        "expiration action",
    )
    for field in ("suspended", "withdrawn"):
        _expect(lifecycle.get(field), False, f"lifecycle.{field}")
    _expect(lifecycle.get("current_and_active"), True, "current approval status")

    authorization = _mapping(payload.get("authorization"), "authorization")
    _expect(
        authorization.get("decision"),
        "officially_authorized",
        "authorization decision",
    )
    for field in (
        "official_determination_verified",
        "recruitment_execution_authorized",
        "consent_execution_authorized",
        "recording_execution_authorized",
    ):
        _expect(authorization.get(field), True, f"authorization.{field}")
    for field in (
        "signed_receipt_sha256",
        "immutable_archive_receipt_sha256",
        "independent_verification_receipt_sha256",
    ):
        _sha256(authorization.get(field), f"authorization.{field}")
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "approval binding",
    )
    canonical_sha = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return ApprovalEvidence(
        sha256=canonical_sha,
        document=payload,
        snapshot=None,
    )


def validate_attestation_payload(
    payload: Mapping[str, Any],
    *,
    approval: ApprovalEvidence,
    attestation_sha256: str | None = None,
) -> AttestationEvidence:
    """Validate the public hash attestation without resolving its locator."""

    _validate_attestation_structure(payload, name="H00 document attestation")
    if _contains_tbd(payload):
        raise H00ContractError("H00 attestation contains unresolved TBD")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h00_official_document_attestation.v1",
        "attestation schema",
    )
    attestation_id = _string(payload.get("attestation_id"), "attestation ID")
    if not re.fullmatch(r"h00attest_[0-9a-f]{32}", attestation_id):
        raise H00ContractError("invalid H00 attestation ID")
    _expect(payload.get("status"), "attested_and_active", "attestation status")
    _expect(
        payload.get("privacy_zone"),
        "A1_C1_public_summary_only",
        "attestation privacy zone",
    )
    _expect(payload.get("public_summary_only"), True, "attestation public summary")
    institution = _mapping(
        approval.document.get("institution_evidence"),
        "approval institution",
    )
    protocol = _mapping(approval.document.get("protocol"), "approval protocol")
    bindings = (
        ("institution_id_sha256", institution, "institution_id_sha256"),
        ("protocol_version", protocol, "protocol_version"),
        ("protocol_sha256", protocol, "protocol_sha256"),
        (
            "official_determination_id_sha256",
            institution,
            "official_determination_id_sha256",
        ),
        (
            "official_determination_document_sha256",
            institution,
            "official_determination_document_sha256",
        ),
    )
    for attestation_field, source, source_field in bindings:
        _expect(
            payload.get(attestation_field),
            source.get(source_field),
            f"attestation {attestation_field} binding",
        )
    for field in (
        "institution_id_sha256",
        "protocol_sha256",
        "official_determination_id_sha256",
        "official_determination_document_sha256",
        "restricted_official_document_locator_sha256",
        "restricted_archive_receipt_sha256",
    ):
        _sha256(payload.get(field), f"attestation.{field}")
    _version(payload.get("protocol_version"), "attestation protocol version")
    _bounded_id(payload.get("custodian_role_id"), "attestation custodian role")
    authorization = _mapping(
        approval.document.get("authorization"),
        "approval authorization",
    )
    _expect(
        payload.get("restricted_archive_receipt_sha256"),
        authorization.get("immutable_archive_receipt_sha256"),
        "attestation archive receipt binding",
    )
    actual_attestation_sha = (
        _sha256(attestation_sha256, "attestation artifact SHA")
        if attestation_sha256 is not None
        else hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    )
    _expect(
        institution.get("restricted_document_attestation_sha256"),
        actual_attestation_sha,
        "approval/attestation artifact binding",
    )
    verification = _mapping(payload.get("verification"), "attestation verification")
    for field in ATTESTATION_VERIFICATION_KEYS - {
        "pii_copied_to_attestation",
        "participant_rows_copied_to_attestation",
        "raw_locator_path_or_url_copied_to_attestation",
    }:
        _expect(verification.get(field), True, f"attestation verification.{field}")
    for field in (
        "pii_copied_to_attestation",
        "participant_rows_copied_to_attestation",
        "raw_locator_path_or_url_copied_to_attestation",
    ):
        _expect(verification.get(field), False, f"attestation verification.{field}")

    lifecycle = _mapping(payload.get("lifecycle"), "attestation lifecycle")
    attested_at = _current_utc(lifecycle.get("attested_at_utc"), "attested at")
    status_at = _current_utc(
        lifecycle.get("official_status_verified_at_utc"),
        "attestation status verified at",
    )
    if attested_at > status_at:
        raise H00ContractError("attestation lifecycle timestamps are out of order")
    approval_lifecycle = _mapping(
        approval.document.get("lifecycle"),
        "approval lifecycle",
    )
    approval_status_at = _current_utc(
        approval_lifecycle.get("status_verified_at_utc"),
        "approval status verified at",
    )
    if status_at < approval_status_at:
        raise H00ContractError(
            "attestation status verification predates approval status verification"
        )
    _expect(
        lifecycle.get("expires_at_utc_or_status_code"),
        approval_lifecycle.get("expires_at_utc_or_status_code"),
        "attestation/approval expiry binding",
    )
    _validate_expiry(
        lifecycle.get("expires_at_utc_or_status_code"),
        status_verified_at_utc=status_at,
        name="attestation expiry",
    )
    _expect(
        lifecycle.get("amendment_status"),
        approval_lifecycle.get("amendment_status"),
        "attestation amendment status binding",
    )
    _expect(
        lifecycle.get("current_amendment_sha256"),
        approval_lifecycle.get("current_amendment_sha256"),
        "attestation amendment SHA binding",
    )
    _nullable_sha256(
        lifecycle.get("current_amendment_sha256"),
        "attestation amendment SHA",
    )
    _expect(
        lifecycle.get("attestation_current"),
        True,
        "attestation current status",
    )
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "attestation binding",
    )
    return AttestationEvidence(
        sha256=actual_attestation_sha,
        document=payload,
        snapshot=None,
    )


def _read_approved_evidence(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> ApprovalEvidence:
    section = _mapping(config.get("official_approval"), "official approval")
    expected_sha = _string(section.get("approval_sha256"), "H00 approval SHA")
    if expected_sha == TBD_H00_APPROVAL:
        raise H00ContractError(
            "H00 approval is TBD_H00_APPROVAL",
            stage="h00_approval",
            audit=audit,
        )
    _sha256(expected_sha, "H00 approval SHA")
    audit.approval_path_resolution_count += 1
    audit.approval_read_attempt_count += 1
    try:
        document, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("approval_path"),
                "H00 approval path",
            ),
            expected_sha256=expected_sha,
            name="H00 public approval",
        )
    except H00ContractError as exc:
        raise H00ContractError(
            f"H00 approval unavailable: {exc}",
            stage="h00_approval",
            audit=audit,
        ) from exc
    audit.approval_read_count += 1
    try:
        validated = validate_approval_payload(
            document,
            accepted_positive_determinations=tuple(
                _list(
                    section.get("accepted_positive_determination_categories"),
                    "positive determination categories",
                )
            ),
        )
    except H00ContractError as exc:
        raise H00ContractError(
            str(exc),
            stage="h00_approval",
            audit=audit,
        ) from exc
    del validated
    audit.approval_deep_validation_count += 1
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(snapshot,),
        audit=audit,
        stage="h00_approval",
    )
    return ApprovalEvidence(
        sha256=snapshot.sha256,
        document=document,
        snapshot=snapshot,
    )


def _read_attestation_evidence(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    approval: ApprovalEvidence,
    audit: AccessAudit,
) -> AttestationEvidence:
    section = _mapping(
        config.get("official_document_attestation"),
        "official document attestation",
    )
    expected_sha = _string(
        section.get("attestation_sha256"),
        "H00 attestation SHA",
    )
    if expected_sha == TBD_H00_ATTESTATION:
        raise H00ContractError(
            "H00 official document attestation is TBD_H00_ATTESTATION",
            stage="h00_attestation",
            audit=audit,
        )
    _sha256(expected_sha, "H00 attestation SHA")
    audit.attestation_path_resolution_count += 1
    audit.attestation_read_attempt_count += 1
    try:
        document, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("attestation_path"),
                "H00 attestation path",
            ),
            expected_sha256=expected_sha,
            name="H00 official document attestation",
        )
    except H00ContractError as exc:
        raise H00ContractError(
            f"H00 attestation unavailable: {exc}",
            stage="h00_attestation",
            audit=audit,
        ) from exc
    audit.attestation_read_count += 1
    try:
        validate_attestation_payload(
            document,
            approval=approval,
            attestation_sha256=snapshot.sha256,
        )
    except H00ContractError as exc:
        raise H00ContractError(
            str(exc),
            stage="h00_attestation",
            audit=audit,
        ) from exc
    audit.attestation_deep_validation_count += 1
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(snapshot,),
        audit=audit,
        stage="h00_attestation",
    )
    return AttestationEvidence(
        sha256=snapshot.sha256,
        document=document,
        snapshot=snapshot,
    )


def ensure_future_output_targets_absent(
    *,
    workspace_root: Path,
    outputs: Mapping[str, Any],
) -> None:
    """Enforce future no-overwrite policy without creating any target."""

    _expect(outputs.get("no_overwrite"), True, "future no-overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "future atomic publish")
    _expect(
        outputs.get("write_enabled_in_this_package"),
        False,
        "static package write boundary",
    )
    seen: set[str] = set()
    for field, value in outputs.items():
        if not field.endswith("_path"):
            continue
        relative = _relative_string(value, f"outputs.{field}")
        if relative in seen:
            raise H00ContractError("duplicate future H00 output target")
        seen.add(relative)
        target = _reject_symlink_chain(
            workspace_root,
            relative,
            f"outputs.{field}",
            require_leaf=False,
        )
        if target.exists() or target.is_symlink():
            raise H00ContractError(f"no-overwrite target already exists: {relative}")


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Validate official public evidence and stop before every external action."""

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
    approval = _read_approved_evidence(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    attestation = _read_attestation_evidence(
        workspace_root=workspace_root,
        config=config,
        approval=approval,
        audit=audit,
    )
    # The locator hash is validated as a hash only. Never resolve the locator,
    # official document, portal, institution, participant, audio, or output.
    return PreflightResult(
        status="ready_for_separately_authorized_h00_human_operations",
        config_sha256=static.config_sha256,
        approval_sha256=approval.sha256,
        attestation_sha256=attestation.sha256,
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
        "actual_external_browser_submission_or_contact": False,
        "actual_official_determination_or_approval": False,
        "actual_participant_recruitment_consent_or_randomization": False,
        "actual_audio_personal_or_sensitive_data_access": False,
        "actual_intervention_model_gpu_or_tts_execution": False,
        "artifact_created": False,
        "h00_completion_claim_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: H00ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "actual_external_browser_submission_or_contact": False,
        "actual_official_determination_or_approval": False,
        "actual_participant_recruitment_consent_or_randomization": False,
        "actual_audio_personal_or_sensitive_data_access": False,
        "actual_intervention_model_gpu_or_tts_execution": False,
        "artifact_created": False,
        "h00_completion_claim_allowed": False,
        "publication_allowed": False,
    }
