from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from voice_coach.evaluation import d07_expert_gold_contract as contract


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs/experiments/d07_independent_expert_gold_v1.yaml"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def _h00() -> contract.H00Evidence:
    return contract.H00Evidence(
        approval_sha256=SHA_A,
        attestation_sha256=SHA_B,
    )


def _institutional_approval() -> dict[str, object]:
    return {
        "schema_version": contract.INSTITUTIONAL_APPROVAL_SCHEMA_VERSION,
        "approval_id": "d07-approval-2026",
        "status": "approved",
        "h00_approval_sha256": SHA_A,
        "h00_official_document_attestation_sha256": SHA_B,
        "d07_protocol_sha256": SHA_C,
        "institutional_scope_receipt_sha256": SHA_D,
        "existing_restricted_audio_expert_rating_approved": True,
        "calibration_800_approved": True,
        "locked_test_up_to_1200_expert_rating_approved": True,
        "two_independent_reviewers_approved": True,
        "disagreement_only_adjudicator_approved": True,
        "consent_or_lawful_basis_verified": True,
        "withdrawal_process_verified": True,
        "purpose_limitation_verified": True,
        "retention_and_deletion_verified": True,
        "access_logging_verified": True,
        "restricted_redistribution_only": True,
        "raw_audio_export_allowed": False,
        "privacy_zone": "C1_public_attestation",
    }


def _reviewer_row(role: str, index: int) -> dict[str, object]:
    is_adjudicator = role == "adjudicator"
    return {
        "reviewer_id_sha256": f"{index + 1:064x}",
        "role": role,
        "qualification_family": (
            "forced_alignment_review" if is_adjudicator else "korean_phonetics"
        ),
        "qualification_evidence_receipt_sha256": SHA_A,
        "rubric_training_receipt_sha256": SHA_B,
        "confidentiality_receipt_sha256": SHA_C,
        "conflict_of_interest_cleared": True,
        "mutual_blinding_attested": not is_adjudicator,
        "model_blinding_attested": True,
        "disagreement_only_scope_attested": is_adjudicator,
    }


def _reviewer_payload() -> dict[str, object]:
    return {
        "schema_version": contract.REVIEWER_ATTESTATION_SCHEMA_VERSION,
        "status": "approved",
        "d07_institutional_approval_sha256": SHA_C,
        "reviewer_count": 3,
        "primary_reviewer_count": 2,
        "adjudicator_count": 1,
        "qualification_evidence_policy": SHA_D,
        "rubric_training_policy_sha256": SHA_E,
        "reviewers": [
            _reviewer_row("primary_reviewer_1", 0),
            _reviewer_row("primary_reviewer_2", 1),
            _reviewer_row("adjudicator", 2),
        ],
        "every_reviewer_has_korean_phonetics_pronunciation_education_or_alignment_experience": True,
        "primary_reviewers_mutually_blind": True,
        "all_reviewers_model_blind": True,
        "adjudicator_restricted_to_disagreements": True,
        "reviewer_identity_mapping_stored_separately": True,
        "privacy_zone": "C1_public_attestation",
    }


def _reviewer_evidence() -> contract.ReviewerEvidence:
    return contract.validate_reviewer_attestation_payload(
        _reviewer_payload(),
        institutional_approval_sha256=SHA_C,
    )


def _resolved_prereg(
    reviewer: contract.ReviewerEvidence,
) -> contract.PreregistrationEvidence:
    path = ROOT / "configs/expert_evaluation/d07_preregistration_v1.template.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(
        {
            "status": "approved",
            "frozen": True,
            "h00_approval_sha256": SHA_A,
            "h00_attestation_sha256": SHA_B,
            "d07_institutional_approval_sha256": SHA_C,
            "reviewer_attestation_sha256": reviewer.sha256,
            "g04_progress_aggregate_sha256": SHA_D,
            "privacy_zone": "C1_public_preregistration",
        }
    )
    payload["sampling"].update(
        {
            "total_item_count": 1000,
            "locked_test_item_count": 200,
            "exact_allocation": [{"stratum_id": "all", "count": 1000}],
            "small_group_minimum_allocation": "approved_rule_v1",
            "population_weighting": "approved_weighting_v1",
        }
    )
    payload["reviewer_design"].update(
        {
            "qualification_evidence": reviewer.qualification_policy_sha256,
            "adjudication_trigger": "approved_disagreement_rule_v1",
            "adjudication_protocol": "approved_protocol_v1",
            "adjudication_note_max_characters": 256,
            "adjudication_reason_taxonomy": "approved_reason_taxonomy_v1",
        }
    )
    payload["rubric_and_labels"].update(
        {
            "rubric_version": "v1",
            "rubric_sha256": SHA_E,
            "primary_definition_sha256": SHA_F,
            "error_type_ontology_sha256": SHA_A,
        }
    )
    payload["calibration_agreement"].update(
        {
            "scalar_gate_statistic": "pronunciation_icc_2_1",
            "taxonomy_gate_statistic": "ontology_macro_agreement",
        }
    )
    payload["statistics"].update(
        {
            "confidence_interval_method": "approved_ci_v1",
            "confidence_level": 0.95,
            "cluster_unit": "speaker_id_sha256",
            "multiplicity_control": "approved_multiplicity_v1",
            "minimum_support_rule": "approved_support_v1",
            "small_support_policy": "report_not_gate",
            "weighted_and_unweighted_slice_estimands": "approved_estimands_v1",
        }
    )
    payload["locked_test"].update(
        {
            "one_shot_transaction_protocol": "approved_one_shot_v1",
            "recovery_and_failure_policy": "approved_failure_policy_v1",
        }
    )
    payload["approval"].update({"decision": "approved", "signed_receipt_sha256": SHA_C})
    return contract.validate_preregistration_payload(
        payload,
        h00=_h00(),
        institutional_approval_sha256=SHA_C,
        reviewer=reviewer,
        g04_progress_sha256=SHA_D,
        ontology_sha256=SHA_A,
    )


def _item(index: int, split: str) -> dict[str, object]:
    return {
        "item_id_sha256": f"{index + 1:064x}",
        "speaker_id_sha256": f"{(index % 100) + 3000:064x}",
        "split": split,
        "audio_object_id_sha256": SHA_A,
        "l1_stratum_id": "l1_zh",
        "gender_stratum_id": "gender_f",
        "proficiency_stratum_id": "level_mid",
        "utterance_length_bin_id": "length_mid",
        "target_phone_stratum_id": "phone_k",
        "existing_error_present": bool(index % 2),
        "target_phone_count": 1,
        "review_order_commitment_sha256": SHA_B,
        "model_output_exposed": False,
        "system_condition_exposed": False,
        "g04_item_reused": False,
        "s203_item_reused": False,
    }


def _item_manifest(
    prereg: contract.PreregistrationEvidence,
    reviewer: contract.ReviewerEvidence,
) -> dict[str, object]:
    items = [_item(index, "calibration") for index in range(800)]
    items.extend(_item(index, "locked_test") for index in range(800, 1000))
    return {
        "schema_version": contract.ITEM_SCHEMA_VERSION,
        "manifest_id": "d07items_" + "1" * 32,
        "h00_approval_sha256": SHA_A,
        "h00_attestation_sha256": SHA_B,
        "d07_institutional_approval_sha256": SHA_C,
        "reviewer_attestation_sha256": reviewer.sha256,
        "preregistration_sha256": prereg.sha256,
        "g04_progress_aggregate_sha256": SHA_D,
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "split_assignment_frozen_before_first_rating": True,
        "total_item_count": 1000,
        "calibration_item_count": 800,
        "locked_test_item_count": 200,
        "items": items,
    }


def _small_item_evidence() -> contract.ItemManifestEvidence:
    row = _item(0, "calibration")
    return contract.ItemManifestEvidence(
        sha256=SHA_A,
        items={row["item_id_sha256"]: row},
        calibration_count=1,
        locked_count=0,
    )


def _rating(
    role: str,
    reviewer: contract.ReviewerEvidence,
    index: int,
) -> dict[str, object]:
    is_adjudicator = role == "adjudicator"
    return {
        "rating_id": f"d07rating_{index + 1:032x}",
        "item_id_sha256": f"{1:064x}",
        "reviewer_id_sha256": reviewer.reviewer_ids_by_role[role],
        "reviewer_role": role,
        "target_phone_index": 0,
        "phone_realization": "correct",
        "error_type": "initial_consonant",
        "boundary_acceptable": "yes",
        "pronunciation_score": 4,
        "comprehensibility_score": 4,
        "fluency_score": 4,
        "confidence": 3,
        "original_rating_ids_sha256": [SHA_A, SHA_B] if is_adjudicator else [],
        "disagreement_receipt_sha256": SHA_C if is_adjudicator else None,
        "adjudication_reason_code": "phone_disagreement" if is_adjudicator else None,
        "adjudication_note": "두 원평정의 실현 유형이 다름" if is_adjudicator else None,
        "independent_original_rating": not is_adjudicator,
        "peer_rating_seen": is_adjudicator,
        "model_output_seen": False,
        "system_condition_seen": False,
        "rating_receipt_sha256": SHA_D,
    }


def _rating_manifest(
    reviewer: contract.ReviewerEvidence,
    prereg: contract.PreregistrationEvidence,
) -> dict[str, object]:
    ratings = [
        _rating("primary_reviewer_1", reviewer, 0),
        _rating("primary_reviewer_2", reviewer, 1),
        _rating("adjudicator", reviewer, 2),
    ]
    return {
        "schema_version": contract.RATING_SCHEMA_VERSION,
        "manifest_id": "d07ratings_" + "2" * 32,
        "split": "calibration",
        "item_manifest_sha256": SHA_A,
        "reviewer_attestation_sha256": reviewer.sha256,
        "preregistration_sha256": prereg.sha256,
        "rubric_sha256": prereg.rubric_sha256,
        "primary_definition_sha256": prereg.primary_definition_sha256,
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "original_ratings_preserved": True,
        "rating_count": 3,
        "ratings": ratings,
    }


def _score_agreement(value: float) -> dict[str, object]:
    return {
        "qwk": value,
        "icc_2_1": value,
        "mae": 0.5,
        "score_distribution": [10, 20, 30, 40, 50],
    }


def _calibration_report(
    prereg: contract.PreregistrationEvidence,
    *,
    scalar: float = 0.70,
    taxonomy: float = 0.40,
) -> dict[str, object]:
    passed = scalar >= 0.70 or taxonomy >= 0.65
    return {
        "schema_version": contract.CALIBRATION_SCHEMA_VERSION,
        "report_id": "d07cal_" + "3" * 32,
        "item_manifest_sha256": SHA_A,
        "rating_manifest_sha256": SHA_B,
        "reviewer_attestation_sha256": SHA_C,
        "preregistration_sha256": prereg.sha256,
        "privacy_zone": "A1_aggregate_only",
        "calibration_item_count": 800,
        "independent_rater_count": 2,
        "adjudicator_count": 1,
        "two_original_ratings_retained": True,
        "adjudication_excluded_from_agreement": True,
        "phone_agreement": {
            "macro_f1": 0.8,
            "cohens_kappa": 0.7,
            "prevalence": 0.3,
        },
        "score_agreement": {
            "pronunciation_score": _score_agreement(scalar),
            "comprehensibility_score": _score_agreement(scalar),
            "fluency_score": _score_agreement(scalar),
        },
        "ontology_agreement": {
            "macro_agreement": taxonomy,
            "krippendorff_alpha": taxonomy,
        },
        "gate": {
            "scalar_statistic_id": prereg.scalar_gate_statistic,
            "scalar_value": scalar,
            "scalar_threshold": 0.7,
            "taxonomy_statistic_id": prereg.taxonomy_gate_statistic,
            "taxonomy_value": taxonomy,
            "taxonomy_threshold": 0.65,
            "combination": "scalar_gte_0_70_or_taxonomy_gte_0_65",
        },
        "decision": {
            "agreement_gate_passed": passed,
            "status": (
                "passed_ready_for_locked_authorization"
                if passed
                else "failed_retrain_and_resample"
            ),
            "rubric_retraining_required": not passed,
            "separate_resample_required": not passed,
            "failed_sample_may_be_reused": False,
            "rubric_frozen_for_locked_test": passed,
            "primary_definition_frozen_for_locked_test": passed,
            "eligible_for_locked_authorization": passed,
            "locked_test_rating_allowed_before_g04_and_one_shot_authorization": False,
        },
    }


def _g04_completion() -> dict[str, object]:
    return {
        "schema_version": contract.G04_COMPLETION_SCHEMA_VERSION,
        "status": "passed",
        "aggregate_only": True,
        "contains_identifiers": False,
        "contains_rows": False,
        "contains_audio": False,
        "contains_labels": False,
        "contains_predictions": False,
        "locked_access_count": 0,
        "expected_count": 240,
        "completed_count": 240,
        "correct_count": 228,
        "alignment_accuracy": 0.95,
        "scorer_status": "passed",
        "privacy_safe_report_sha256": SHA_A,
        "reviewer_qualification_confirmed": True,
        "research_lead_approved": True,
    }


def _locked_report(
    prereg: contract.PreregistrationEvidence,
) -> dict[str, object]:
    return {
        "schema_version": contract.LOCKED_REPORT_SCHEMA_VERSION,
        "report_id": "d07locked_" + "4" * 32,
        "item_manifest_sha256": SHA_A,
        "rating_manifest_sha256": SHA_B,
        "calibration_report_sha256": SHA_C,
        "g04_completion_attestation_sha256": SHA_D,
        "locked_access_authorization_sha256": SHA_E,
        "preregistration_sha256": prereg.sha256,
        "privacy_zone": "A1_aggregate_only",
        "locked_test_item_count": 200,
        "expert_locked_access_transaction_count": 1,
        "developer_locked_access_count": 0,
        "rubric_unchanged_after_locked_start": True,
        "primary_definition_unchanged_after_locked_start": True,
        "two_original_ratings_retained": True,
        "disagreements_only_adjudicated": True,
        "model_results_unseen": True,
        "sensitivity_analyses": [
            {
                "analysis_basis": basis,
                "metric_definition_sha256": SHA_A,
                "aggregate_result_sha256": SHA_B,
            }
            for basis in contract.SENSITIVITY_BASES
        ],
        "statistical_contract": {
            "confidence_interval_method_sha256": SHA_A,
            "multiplicity_method_sha256": SHA_B,
            "minimum_support_rule_sha256": SHA_C,
            "cluster_unit_sha256": SHA_D,
            "slice_allocation_weighting_sha256": SHA_E,
        },
    }


def _completion(
    prereg: contract.PreregistrationEvidence,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": contract.COMPLETION_SCHEMA_VERSION,
        "status": "completed",
        "aggregate_only": True,
        "contains_identifiers": False,
        "contains_rows": False,
        "contains_audio": False,
        "contains_labels": False,
        "contains_predictions": False,
        "locked_access_count": 0,
        "calibration_count": 800,
        "locked_gold_count": 200,
        "independent_rater_count": 2,
        "adjudicator_count": 1,
        "two_original_ratings_retained": True,
        "adjudication_completed": True,
        "scalar_agreement": 0.70,
        "taxonomy_agreement": 0.40,
        "agreement_gate_passed": True,
        "label_definition_frozen": True,
        "primary_definition_frozen": True,
        "rubric_unchanged_after_locked_start": True,
        "model_results_unseen": True,
        "research_lead_approved": True,
        "preregistration_sha256": prereg.sha256,
        "expert_locked_access_transaction_count": 1,
        "sensitivity_analysis_completed": True,
    }
    for field, value in (
        ("calibration_gold_sha256", SHA_A),
        ("sealed_locked_gold_sha256", SHA_B),
        ("h00_approval_sha256", SHA_C),
        ("h00_attestation_sha256", SHA_D),
        ("d07_institutional_approval_sha256", SHA_E),
        ("reviewer_attestation_sha256", SHA_F),
        ("calibration_report_sha256", SHA_A),
        ("g04_completion_attestation_sha256", SHA_B),
        ("locked_access_authorization_sha256", SHA_C),
        ("locked_evaluation_report_sha256", SHA_D),
    ):
        payload[field] = value
    return payload


def test_repository_config_only_reads_only_c1_and_g04_a1_aggregate() -> None:
    config = contract.load_config(CONFIG_PATH)
    audit = contract.AccessAudit()
    static = contract.validate_static_config(
        workspace_root=ROOT,
        config_path=CONFIG_PATH,
        config=config,
        audit=audit,
    )
    result = contract.config_only_result(static, audit)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 25
    assert result["source_pin_read_count"] == 25
    assert result["g04_progress_aggregate_read_count"] == 1
    assert result["g04_current_completed_count"] == 0
    for field, value in result.items():
        if field.endswith("_count") and field not in (
            "source_pin_count",
            "source_pin_read_count",
            "g04_progress_aggregate_read_count",
            "g04_expected_count",
        ):
            assert value == 0, field


def test_full_stops_at_h00_tbd_before_module_or_external_path() -> None:
    config = contract.load_config(CONFIG_PATH)
    with pytest.raises(contract.D07ContractError, match="H00") as caught:
        contract.run_full_preflight(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=config,
        )
    assert caught.value.stage == "h00_approval"
    assert caught.value.audit is not None
    audit = caught.value.audit
    assert audit.source_pin_read_count == 25
    assert audit.source_toctou_verification_count == 25
    assert audit.g04_progress_aggregate_read_count == 1
    for field in (
        "h00_module_load_count",
        "h00_approval_path_resolution_count",
        "h00_approval_read_attempt_count",
        "h00_approval_read_count",
        "h00_attestation_path_resolution_count",
        "h00_attestation_read_attempt_count",
        "h00_attestation_read_count",
        "institutional_approval_path_resolution_count",
        "reviewer_attestation_path_resolution_count",
        "preregistration_path_resolution_count",
        "d1_item_manifest_path_resolution_count",
        "r1_locator_path_resolution_count",
        "calibration_audio_path_resolution_count",
        "g04_ui_state_path_resolution_count",
        "locked_item_path_resolution_count",
        "locked_audio_path_resolution_count",
        "expert_locked_access_transaction_count",
        "model_access_count",
        "metric_count",
        "output_path_resolution_count",
        "commit_write_count",
    ):
        assert getattr(audit, field) == 0, field


def test_g04_aggregate_is_separate_zero_of_240_and_never_d07_reuse() -> None:
    progress = json.loads(
        (
            ROOT / "data/processed/aihub71490/manifests/"
            "aihub71490_manual_alignment_review_progress.json"
        ).read_text(encoding="utf-8")
    )
    contract._validate_g04_progress(progress)
    assert progress["completed_count"] == 0
    assert progress["source"]["review_state_sha256"] is None
    assert progress["export"]["written"] is False


def test_institutional_and_three_role_reviewer_attestations() -> None:
    approval = _institutional_approval()
    assert (
        contract.validate_institutional_approval_payload(
            approval,
            h00=_h00(),
        )
        == approval
    )
    reviewer = _reviewer_evidence()
    assert set(reviewer.reviewer_ids_by_role) == set(contract.REVIEWER_ROLES)

    duplicate = _reviewer_payload()
    duplicate["reviewers"][2]["role"] = "primary_reviewer_2"
    with pytest.raises(contract.D07ContractError, match="roles"):
        contract.validate_reviewer_attestation_payload(
            duplicate,
            institutional_approval_sha256=SHA_C,
        )


def test_preregistration_resolves_design_without_inventing_static_values() -> None:
    reviewer = _reviewer_evidence()
    prereg = _resolved_prereg(reviewer)
    assert prereg.total_item_count == 1000
    assert prereg.locked_item_count == 200
    assert prereg.note_max_characters == 256

    unresolved_payload = deepcopy(prereg.payload)
    unresolved_payload["statistics"]["multiplicity_control"] = (
        contract.TBD_PREREGISTRATION
    )
    with pytest.raises(contract.D07ContractError, match="contains TBD"):
        contract.validate_preregistration_payload(
            unresolved_payload,
            h00=_h00(),
            institutional_approval_sha256=SHA_C,
            reviewer=reviewer,
            g04_progress_sha256=SHA_D,
            ontology_sha256=SHA_A,
        )


def test_item_manifest_enforces_800_plus_prefixed_locked_and_no_g04_reuse() -> None:
    reviewer = _reviewer_evidence()
    prereg = _resolved_prereg(reviewer)
    payload = _item_manifest(prereg, reviewer)
    evidence = contract.validate_item_manifest_payload(
        payload,
        h00=_h00(),
        institutional_approval_sha256=SHA_C,
        reviewer_sha256=reviewer.sha256,
        preregistration=prereg,
        g04_progress_sha256=SHA_D,
    )
    assert evidence.calibration_count == 800
    assert evidence.locked_count == 200

    reused = deepcopy(payload)
    reused["items"][0]["g04_item_reused"] = True
    with pytest.raises(contract.D07ContractError, match="g04_item_reused"):
        contract.validate_item_manifest_payload(
            reused,
            h00=_h00(),
            institutional_approval_sha256=SHA_C,
            reviewer_sha256=reviewer.sha256,
            preregistration=prereg,
            g04_progress_sha256=SHA_D,
        )


def test_rating_manifest_requires_two_blind_originals_and_disagreement_adjudication() -> (
    None
):
    reviewer = _reviewer_evidence()
    prereg = _resolved_prereg(reviewer)
    payload = _rating_manifest(reviewer, prereg)
    assert (
        contract.validate_rating_manifest_payload(
            payload,
            items=_small_item_evidence(),
            reviewer=reviewer,
            preregistration=prereg,
        )
        == payload
    )

    missing_primary = deepcopy(payload)
    missing_primary["ratings"].pop(1)
    missing_primary["rating_count"] = 2
    with pytest.raises(contract.D07ContractError, match="two primary"):
        contract.validate_rating_manifest_payload(
            missing_primary,
            items=_small_item_evidence(),
            reviewer=reviewer,
            preregistration=prereg,
        )


def test_adjudication_note_rejects_pii_and_preserves_original_links() -> None:
    reviewer = _reviewer_evidence()
    prereg = _resolved_prereg(reviewer)
    payload = _rating_manifest(reviewer, prereg)
    payload["ratings"][2]["adjudication_note"] = "연락처 010-1234-5678"
    with pytest.raises(contract.D07ContractError, match="PII"):
        contract.validate_rating_manifest_payload(
            payload,
            items=_small_item_evidence(),
            reviewer=reviewer,
            preregistration=prereg,
        )


def test_calibration_gate_is_scalar_or_taxonomy_and_failure_requires_resample() -> None:
    prereg = _resolved_prereg(_reviewer_evidence())
    scalar_pass = _calibration_report(prereg, scalar=0.70, taxonomy=0.20)
    assert (
        contract.validate_calibration_report_payload(
            scalar_pass,
            preregistration=prereg,
        )
        == scalar_pass
    )

    taxonomy_pass = _calibration_report(prereg, scalar=0.20, taxonomy=0.65)
    assert (
        contract.validate_calibration_report_payload(
            taxonomy_pass,
            preregistration=prereg,
        )
        == taxonomy_pass
    )

    failed = _calibration_report(prereg, scalar=0.69, taxonomy=0.64)
    assert (
        contract.validate_calibration_report_payload(
            failed,
            preregistration=prereg,
        )
        == failed
    )
    assert failed["decision"]["rubric_retraining_required"] is True
    assert failed["decision"]["separate_resample_required"] is True
    assert failed["decision"]["eligible_for_locked_authorization"] is False
    assert (
        failed["decision"][
            "locked_test_rating_allowed_before_g04_and_one_shot_authorization"
        ]
        is False
    )


def test_g04_completion_and_one_shot_locked_authorization_are_distinct() -> None:
    g04 = _g04_completion()
    assert contract.validate_g04_completion_payload(g04) == g04
    prereg = _resolved_prereg(_reviewer_evidence())
    authorization = {
        "schema_version": contract.LOCKED_AUTHORIZATION_SCHEMA_VERSION,
        "status": "authorized_one_shot",
        "preregistration_sha256": prereg.sha256,
        "item_manifest_sha256": SHA_A,
        "calibration_report_sha256": SHA_B,
        "g04_completion_attestation_sha256": SHA_C,
        "rubric_sha256": prereg.rubric_sha256,
        "primary_definition_sha256": prereg.primary_definition_sha256,
        "calibration_agreement_gate_passed": True,
        "g04_completed_as_separate_stage": True,
        "rubric_frozen": True,
        "primary_definition_frozen": True,
        "prior_expert_locked_access_transaction_count": 0,
        "authorized_expert_locked_access_transaction_count": 1,
        "developer_locked_access_authorized": False,
        "model_result_access_authorized": False,
        "one_shot_transaction_id_sha256": SHA_D,
        "authorization_receipt_sha256": SHA_E,
        "privacy_zone": "C1_public_authorization",
    }
    assert (
        contract.validate_locked_authorization_payload(
            authorization,
            preregistration=prereg,
            item_manifest_sha256=SHA_A,
            calibration_report_sha256=SHA_B,
            g04_completion_sha256=SHA_C,
        )
        == authorization
    )

    replay = deepcopy(authorization)
    replay["prior_expert_locked_access_transaction_count"] = 1
    with pytest.raises(contract.D07ContractError, match="prior expert"):
        contract.validate_locked_authorization_payload(
            replay,
            preregistration=prereg,
            item_manifest_sha256=SHA_A,
            calibration_report_sha256=SHA_B,
            g04_completion_sha256=SHA_C,
        )


def test_locked_report_preserves_rubric_primary_and_three_sensitivities() -> None:
    prereg = _resolved_prereg(_reviewer_evidence())
    payload = _locked_report(prereg)
    assert (
        contract.validate_locked_report_payload(
            payload,
            preregistration=prereg,
        )
        == payload
    )

    changed = deepcopy(payload)
    changed["rubric_unchanged_after_locked_start"] = False
    with pytest.raises(contract.D07ContractError, match="rubric"):
        contract.validate_locked_report_payload(
            changed,
            preregistration=prereg,
        )


def test_completion_is_s108_compatible_and_rejects_failed_agreement() -> None:
    prereg = _resolved_prereg(_reviewer_evidence())
    payload = _completion(prereg)
    assert (
        contract.validate_completion_attestation_payload(
            payload,
            preregistration=prereg,
        )
        == payload
    )

    failed = deepcopy(payload)
    failed["scalar_agreement"] = 0.69
    failed["taxonomy_agreement"] = 0.64
    with pytest.raises(contract.D07ContractError, match="agreement gate"):
        contract.validate_completion_attestation_payload(
            failed,
            preregistration=prereg,
        )


def test_public_schemas_are_strict_and_templates_keep_evidence_tbd() -> None:
    schema_files = (
        "d07_gold_item_manifest_v1.schema.json",
        "d07_expert_rating_manifest_v1.schema.json",
        "d07_calibration_agreement_report_v1.schema.json",
        "d07_locked_evaluation_report_v1.schema.json",
        "d07_gold_completion_attestation_v1.schema.json",
    )
    for filename in schema_files:
        payload = json.loads(
            (ROOT / "configs/expert_evaluation" / filename).read_text(encoding="utf-8")
        )
        assert payload["additionalProperties"] is False
        assert set(payload["required"]) == set(payload["properties"])

    prereg = json.loads(
        (
            ROOT / "configs/expert_evaluation/d07_preregistration_v1.template.json"
        ).read_text(encoding="utf-8")
    )
    assert contract._contains_tbd(prereg)
    assert prereg["sampling"]["exact_allocation"] == "TBD_PREREGISTRATION"
    assert prereg["reviewer_design"]["qualification_evidence"] == "TBD_ATTESTATION"


def test_static_source_does_not_include_or_open_g04_ui_state() -> None:
    assert not any("alignment_review_state" in path for path in contract.SOURCE_PATHS)
    state_path = (
        ROOT / "data/restricted/aihub/dataset-71490/review/alignment_review_state.json"
    )
    before = state_path.stat() if state_path.exists() else None
    config = contract.load_config(CONFIG_PATH)
    contract.validate_static_config(
        workspace_root=ROOT,
        config_path=CONFIG_PATH,
        config=config,
    )
    after = state_path.stat() if state_path.exists() else None
    assert before == after


def test_hash_helpers_are_canonical_for_external_artifacts() -> None:
    payload = _institutional_approval()
    digest = hashlib.sha256(contract.canonical_json_bytes(payload)).hexdigest()
    assert len(digest) == 64
