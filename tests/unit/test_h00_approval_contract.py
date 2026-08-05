from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from voice_coach.human_research.h00_approval_contract import (
    AccessAudit,
    H00ContractError,
    SOURCE_PATHS,
    binding_sha256,
    canonical_json_bytes,
    ensure_future_output_targets_absent,
    load_config,
    run_full_preflight,
    validate_approval_payload,
    validate_attestation_payload,
    validate_static_config,
)


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG = WORKSPACE / "configs/experiments/h00_human_research_approval_v1.yaml"
APPROVAL_TEMPLATE = (
    WORKSPACE
    / "configs/human_research/h00_human_research_approval_public_v1.template.json"
)
ATTESTATION_TEMPLATE = (
    WORKSPACE
    / "configs/human_research/h00_official_document_attestation_v1.template.json"
)


def _h(index: int) -> str:
    return hashlib.sha256(f"h00-test-{index}".encode()).hexdigest()


def _attestation_payload() -> dict[str, object]:
    payload = json.loads(ATTESTATION_TEMPLATE.read_text())
    payload.update(
        {
            "attestation_id": "h00attest_" + "b" * 32,
            "status": "attested_and_active",
            "institution_id_sha256": _h(1),
            "protocol_version": "v1.0",
            "protocol_sha256": _h(4),
            "official_determination_id_sha256": _h(2),
            "official_determination_document_sha256": _h(3),
            "restricted_official_document_locator_sha256": _h(40),
            "restricted_archive_receipt_sha256": _h(41),
            "custodian_role_id": "authorized_records_custodian",
        }
    )
    verification = payload["verification"]
    for field in (
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
    ):
        verification[field] = True
    payload["lifecycle"] = {
        "attested_at_utc": "2026-07-28T12:00:00Z",
        "official_status_verified_at_utc": "2026-07-28T13:00:00Z",
        "expires_at_utc_or_status_code": "2099-12-31T23:59:59Z",
        "amendment_status": "no_amendment",
        "current_amendment_sha256": None,
        "attestation_current": True,
    }
    payload["binding_sha256"] = binding_sha256(payload)
    return payload


def _approval_payload(attestation_sha256: str) -> dict[str, object]:
    payload = json.loads(APPROVAL_TEMPLATE.read_text())
    payload.update(
        {
            "approval_artifact_id": "h00approval_" + "a" * 32,
            "status": "active_official_determination",
            "actual_pilot_covered": True,
            "consent_approved": True,
            "privacy_approved": True,
            "retention_approved": True,
            "deletion_approved": True,
        }
    )
    payload["institution_evidence"] = {
        "institution_id_sha256": _h(1),
        "approving_body_role": "institutional_review_board",
        "official_determination_category": "approved",
        "official_determination_id_sha256": _h(2),
        "institution_form_sha256": _h(42),
        "official_determination_document_sha256": _h(3),
        "restricted_document_attestation_sha256": attestation_sha256,
        "official_source_verified": True,
    }
    payload["protocol"] = {
        "protocol_id_sha256": _h(43),
        "protocol_version": "v1.0",
        "protocol_sha256": _h(4),
        "application_bundle_sha256": _h(5),
        "consent_form_version": "v1.0",
        "consent_form_sha256": _h(6),
        "privacy_impact_assessment_sha256": _h(7),
        "data_management_plan_sha256": _h(8),
    }
    scope = payload["study_scope"]
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
        scope[field] = True
    scope["confirmatory_study_covered"] = False
    for index, condition in enumerate(("c0", "c3", "c5"), start=9):
        payload["intervention_manuals"][condition] = {
            "condition": condition,
            "version": "v1.0",
            "manual_sha256": _h(index),
            "frozen_before_recruitment": True,
        }
    randomization = payload["randomization_and_concealment"]
    randomization.update(
        {
            "allocation_method_id": "independent_stratified_block_randomization",
            "allocation_implementation_receipt_sha256": _h(12),
            "randomization_seed_custody_receipt_sha256": _h(13),
            "independent_allocation_role_id": "independent_allocation_manager",
        }
    )
    governance = payload["data_governance"]
    governance.update(
        {
            "sensitive_or_special_category_review_completed": True,
            "personal_data_categories_sha256": _h(14),
            "sensitive_data_categories_sha256": _h(15),
            "retention_duration_or_trigger_code": "institution_approved_rule_v1",
            "retention_policy_sha256": _h(16),
            "authorized_role_matrix_sha256": _h(17),
            "deletion_method_id": "approved_secure_deletion_v1",
            "deletion_protocol_sha256": _h(18),
            "backup_deletion_covered": True,
        }
    )
    rights = payload["consent_withdrawal_and_participant_rights"]
    rights.update(
        {
            "ai_incorrect_feedback_risk_disclosed": True,
            "withdrawal_protocol_sha256": _h(19),
            "withdrawal_existing_data_handling_code": "approved_consent_choice_v1",
            "deletion_request_route_sha256": _h(20),
            "human_contact_and_escalation_route_sha256": _h(21),
            "consent_language_accessibility_reviewed": True,
        }
    )
    payload["recruitment_compensation_and_minors"] = {
        "recruitment_materials_sha256": _h(22),
        "eligibility_protocol_sha256": _h(23),
        "undue_influence_review_completed": True,
        "compensation_terms_code": "institution_approved_terms_v1",
        "compensation_protocol_sha256": _h(24),
        "compensation_withdrawal_treatment_code": "approved_prorated_rule_v1",
        "minors_policy": "excluded",
        "guardian_consent_protocol_sha256": None,
        "minor_assent_protocol_sha256": None,
    }
    safety = payload["ai_feedback_and_safety"]
    safety.update(
        {
            "incorrect_feedback_risk_documented": True,
            "human_escalation_available": True,
            "feedback_safety_protocol_sha256": _h(25),
            "human_escalation_protocol_sha256": _h(26),
            "adverse_feedback_monitoring_protocol_sha256": _h(27),
        }
    )
    transfer = payload["pseudonymization_and_transfer"]
    transfer.update(
        {
            "key_custodian_role_receipt_sha256": _h(28),
            "external_service_transfer_allowed": False,
            "tts_service_transfer_allowed": False,
            "cross_border_transfer_allowed": False,
            "transfer_policy_sha256": _h(29),
            "approved_provider_scope_sha256": _h(30),
            "separate_transfer_consent_required": False,
            "separate_transfer_consent_approved": False,
        }
    )
    incident = payload["incident_pause_stop"]
    incident.update(
        {
            "incident_response_protocol_sha256": _h(31),
            "breach_notification_protocol_sha256": _h(32),
            "stop_protocol_sha256": _h(33),
            "resume_protocol_sha256": _h(34),
        }
    )
    payload["lifecycle"] = {
        "approved_at_utc": "2026-07-28T09:00:00Z",
        "effective_at_utc": "2026-07-28T10:00:00Z",
        "expires_at_utc_or_status_code": "2099-12-31T23:59:59Z",
        "status_verified_at_utc": "2026-07-28T11:00:00Z",
        "amendment_status": "no_amendment",
        "current_amendment_version": None,
        "current_amendment_sha256": None,
        "scope_change_requires_prior_amendment": True,
        "expiration_action": "stop_recruitment_intervention_and_recording",
        "suspended": False,
        "withdrawn": False,
        "current_and_active": True,
    }
    payload["authorization"] = {
        "decision": "officially_authorized",
        "official_determination_verified": True,
        "signed_receipt_sha256": _h(39),
        "immutable_archive_receipt_sha256": _h(41),
        "independent_verification_receipt_sha256": _h(44),
        "recruitment_execution_authorized": True,
        "consent_execution_authorized": True,
        "recording_execution_authorized": True,
    }
    payload["binding_sha256"] = binding_sha256(payload)
    return payload


def _valid_pair() -> tuple[dict[str, object], dict[str, object]]:
    attestation = _attestation_payload()
    attestation_sha = hashlib.sha256(canonical_json_bytes(attestation)).hexdigest()
    return _approval_payload(attestation_sha), attestation


def test_static_config_reads_12_sources_without_external_or_participant_access() -> (
    None
):
    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CONFIG,
        config=load_config(CONFIG),
        audit=audit,
    )

    assert len(static.source_snapshots) == 8
    assert audit.source_pin_read_count == 8
    assert audit.approval_path_resolution_count == 0
    assert audit.external_access_count == 0
    assert audit.network_access_count == 0
    assert audit.portal_submission_count == 0
    assert audit.participant_row_read_count == 0
    assert audit.audio_read_count == 0
    assert audit.output_count == 0


def test_valid_official_approval_and_attestation_hash_chain() -> None:
    approval_payload, attestation_payload = _valid_pair()
    approval = validate_approval_payload(approval_payload)
    attestation_sha = hashlib.sha256(
        canonical_json_bytes(attestation_payload)
    ).hexdigest()
    attestation = validate_attestation_payload(
        attestation_payload,
        approval=approval,
        attestation_sha256=attestation_sha,
    )

    assert approval.document["status"] == "active_official_determination"
    assert attestation.sha256 == attestation_sha


def test_synthetic_future_full_preflight_validates_both_public_artifacts(
    tmp_path: Path,
) -> None:
    for relative in SOURCE_PATHS:
        source = WORKSPACE / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    approval, attestation = _valid_pair()
    attestation_bytes = canonical_json_bytes(attestation)
    approval_bytes = canonical_json_bytes(approval)
    attestation_path = (
        tmp_path / "artifacts/external_attestations/h00/"
        "h00_official_document_attestation_v1.json"
    )
    approval_path = (
        tmp_path / "artifacts/external_attestations/h00/"
        "h00_human_research_approval_public_v1.json"
    )
    attestation_path.parent.mkdir(parents=True)
    attestation_path.write_bytes(attestation_bytes)
    approval_path.write_bytes(approval_bytes)

    config = deepcopy(load_config(CONFIG))
    config["official_approval"]["approval_sha256"] = hashlib.sha256(
        approval_bytes
    ).hexdigest()
    config["official_document_attestation"]["attestation_sha256"] = hashlib.sha256(
        attestation_bytes
    ).hexdigest()
    config_path = tmp_path / "configs/experiments/h00.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(canonical_json_bytes(config))

    result = run_full_preflight(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )

    assert result.status == "ready_for_separately_authorized_h00_human_operations"
    assert result.audit["source_pin_read_count"] == 8
    assert result.audit["source_toctou_verification_count"] == 10
    assert result.audit["approval_path_resolution_count"] == 1
    assert result.audit["approval_read_count"] == 1
    assert result.audit["approval_deep_validation_count"] == 1
    assert result.audit["attestation_path_resolution_count"] == 1
    assert result.audit["attestation_read_count"] == 1
    assert result.audit["attestation_deep_validation_count"] == 1
    assert result.audit["restricted_official_document_locator_resolution_count"] == 0
    assert result.audit["restricted_official_document_read_count"] == 0
    assert result.audit["external_access_count"] == 0
    assert result.audit["participant_recruitment_count"] == 0
    assert result.audit["audio_read_count"] == 0
    assert result.audit["model_access_count"] == 0
    assert result.audit["output_count"] == 0


@pytest.mark.parametrize(
    "category",
    ["modifications_required", "denied", "suspended", "expired", "withdrawn"],
)
def test_non_authorizing_official_determination_fails_closed(category: str) -> None:
    approval, _ = _valid_pair()
    approval["institution_evidence"]["official_determination_category"] = category
    approval["binding_sha256"] = binding_sha256(approval)

    with pytest.raises(H00ContractError):
        validate_approval_payload(approval)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["institution_evidence"].update({"official_source_verified": False}),
        lambda p: p["study_scope"].update({"c5_covered": False}),
        lambda p: p["intervention_manuals"]["c3"].update(
            {"frozen_before_recruitment": False}
        ),
        lambda p: p["randomization_and_concealment"].update(
            {"allocation_concealment_required": False}
        ),
        lambda p: p["data_governance"].update(
            {"retention_duration_or_trigger_code": "TBD_EXTERNAL"}
        ),
        lambda p: p["ai_feedback_and_safety"].update(
            {"human_escalation_available": False}
        ),
        lambda p: p["lifecycle"].update({"suspended": True}),
    ],
)
def test_missing_official_scope_or_safety_requirement_fails_closed(
    mutation: object,
) -> None:
    approval, _ = _valid_pair()
    mutation(approval)  # type: ignore[operator]
    approval["binding_sha256"] = binding_sha256(approval)

    with pytest.raises(H00ContractError):
        validate_approval_payload(approval)


def test_minors_inclusion_requires_guardian_and_assent_protocols() -> None:
    approval, _ = _valid_pair()
    recruitment = approval["recruitment_compensation_and_minors"]
    recruitment["minors_policy"] = "included_only_under_separately_approved_protections"
    approval["binding_sha256"] = binding_sha256(approval)

    with pytest.raises(H00ContractError):
        validate_approval_payload(approval)

    recruitment["guardian_consent_protocol_sha256"] = _h(45)
    recruitment["minor_assent_protocol_sha256"] = _h(46)
    approval["binding_sha256"] = binding_sha256(approval)
    validate_approval_payload(approval)


def test_external_or_tts_transfer_requires_separate_approved_consent() -> None:
    approval, _ = _valid_pair()
    transfer = approval["pseudonymization_and_transfer"]
    transfer["tts_service_transfer_allowed"] = True
    approval["binding_sha256"] = binding_sha256(approval)
    with pytest.raises(H00ContractError):
        validate_approval_payload(approval)

    transfer["separate_transfer_consent_required"] = True
    transfer["separate_transfer_consent_approved"] = True
    approval["binding_sha256"] = binding_sha256(approval)
    validate_approval_payload(approval)


def test_public_pii_participant_row_or_raw_locator_field_is_rejected() -> None:
    approval, _ = _valid_pair()
    approval["authorization"]["name"] = "not allowed"
    approval["binding_sha256"] = binding_sha256(approval)
    with pytest.raises(H00ContractError):
        validate_approval_payload(approval)

    approval, _ = _valid_pair()
    approval["official_document_locator"] = "/restricted/document.pdf"
    approval["binding_sha256"] = binding_sha256(approval)
    with pytest.raises(H00ContractError):
        validate_approval_payload(approval)


def test_tampered_binding_is_rejected() -> None:
    approval, _ = _valid_pair()
    approval["binding_sha256"] = _h(60)
    with pytest.raises(H00ContractError):
        validate_approval_payload(approval)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status_verified_at_utc", "2098-01-01T00:00:00Z"),
        ("status_verified_at_utc", "2026-02-31T00:00:00Z"),
        ("effective_at_utc", "2098-01-01T00:00:00Z"),
    ],
)
def test_invalid_or_future_approval_lifecycle_fails_closed(
    field: str,
    value: str,
) -> None:
    approval, _ = _valid_pair()
    approval["lifecycle"][field] = value
    approval["binding_sha256"] = binding_sha256(approval)

    with pytest.raises(H00ContractError):
        validate_approval_payload(approval)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["verification"].update({"signature_or_seal_verified": False}),
        lambda p: p["verification"].update(
            {"raw_locator_path_or_url_copied_to_attestation": True}
        ),
        lambda p: p.update({"official_determination_document_sha256": _h(61)}),
        lambda p: p["lifecycle"].update({"attestation_current": False}),
    ],
)
def test_invalid_official_document_attestation_fails_closed(mutation: object) -> None:
    approval_payload, attestation_payload = _valid_pair()
    approval = validate_approval_payload(approval_payload)
    mutation(attestation_payload)  # type: ignore[operator]
    attestation_payload["binding_sha256"] = binding_sha256(attestation_payload)
    attestation_sha = hashlib.sha256(
        canonical_json_bytes(attestation_payload)
    ).hexdigest()

    with pytest.raises(H00ContractError):
        validate_attestation_payload(
            attestation_payload,
            approval=approval,
            attestation_sha256=attestation_sha,
        )


@pytest.mark.parametrize(
    ("attested_at", "status_at"),
    [
        ("2026-07-28T12:00:00Z", "2098-01-01T00:00:00Z"),
        ("2026-02-31T00:00:00Z", "2026-07-28T13:00:00Z"),
        ("2026-07-28T10:00:00Z", "2026-07-28T10:30:00Z"),
    ],
)
def test_invalid_future_or_stale_attestation_lifecycle_fails_closed(
    attested_at: str,
    status_at: str,
) -> None:
    approval_payload, attestation_payload = _valid_pair()
    approval = validate_approval_payload(approval_payload)
    attestation_payload["lifecycle"]["attested_at_utc"] = attested_at
    attestation_payload["lifecycle"]["official_status_verified_at_utc"] = status_at
    attestation_payload["binding_sha256"] = binding_sha256(attestation_payload)
    attestation_sha = hashlib.sha256(
        canonical_json_bytes(attestation_payload)
    ).hexdigest()

    with pytest.raises(H00ContractError):
        validate_attestation_payload(
            attestation_payload,
            approval=approval,
            attestation_sha256=attestation_sha,
        )


def test_fail_closed_templates_cannot_be_used_as_approval() -> None:
    template = json.loads(APPROVAL_TEMPLATE.read_text())
    with pytest.raises(H00ContractError):
        validate_approval_payload(template)


def test_future_output_guards_traversal_existing_and_symlink(tmp_path: Path) -> None:
    outputs = {
        "validation_report_path": "artifacts/h00/report.json",
        "registry_path": "artifacts/h00/registry.json",
        "failure_report_path": "artifacts/h00/failure.json",
        "no_overwrite": True,
        "atomic_publish_required": True,
        "write_enabled_in_this_package": False,
    }
    ensure_future_output_targets_absent(workspace_root=tmp_path, outputs=outputs)
    traversal = {**outputs, "validation_report_path": "../escape.json"}
    with pytest.raises(H00ContractError):
        ensure_future_output_targets_absent(
            workspace_root=tmp_path,
            outputs=traversal,
        )
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "h00").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(H00ContractError):
        ensure_future_output_targets_absent(workspace_root=tmp_path, outputs=outputs)


def test_config_nonexecution_or_extra_key_drift_is_rejected() -> None:
    config = deepcopy(load_config(CONFIG))
    config["non_execution"]["portal_submission_allowed"] = True
    with pytest.raises(H00ContractError):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG,
            config=config,
        )
    config = deepcopy(load_config(CONFIG))
    config["experiment"]["approval_number"] = "invented"
    with pytest.raises(H00ContractError):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG,
            config=config,
        )
