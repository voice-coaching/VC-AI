from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from voice_coach.coaching import c03_expert_audit_contract as contract


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs/experiments/c03_expert_coaching_audit_v1.yaml"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def _item(index: int, *, role: str = "primary") -> dict[str, object]:
    is_primary = role == "primary"
    return {
        "audit_item_id": f"c03item_{index:032x}",
        "sample_role": role,
        "c02_input_manifest_sha256": SHA_A,
        "c02_output_manifest_sha256": SHA_B,
        "audio_object_id_sha256": SHA_C,
        "target_sentence_object_id_sha256": SHA_D,
        "top1_advice_object_id_sha256": SHA_E if is_primary else None,
        "l1_stratum_id": "l1_ko",
        "error_type_stratum_id": "segmental_substitution",
        "confidence_bin_id": "bin_mid",
        "coaching_action": "coach" if is_primary else "rerecord",
        "review_packet_sha256": SHA_F,
        "reviewer_visible_fields": ["audio", "target_sentence", "top1_advice"],
        "model_score_exposed_to_reviewer": False,
        "source_condition_exposed_to_reviewer": False,
    }


def _item_manifest() -> dict[str, object]:
    items = [_item(index) for index in range(200)]
    items.append(_item(200, role="abstention_referral"))
    return {
        "schema_version": contract.ITEM_SCHEMA_VERSION,
        "manifest_id": "c03items_" + "1" * 32,
        "c02_completion_commit_sha256": SHA_A,
        "c02_d1_bundle_manifest_sha256": SHA_B,
        "c03_preregistration_sha256": SHA_C,
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "primary_item_count": 200,
        "abstention_referral_item_count": 1,
        "items": items,
    }


def _rating(*, role: str = "original") -> dict[str, object]:
    return {
        "rating_id": "c03rating_" + "2" * 32,
        "audit_item_id": "c03item_" + "0" * 32,
        "reviewer_id_sha256": SHA_A,
        "rating_role": role,
        "original_rating_id_sha256": SHA_B if role == "adjudication" else None,
        "independent_review": role == "original",
        "reviewer_visible_fields": ["audio", "target_sentence", "top1_advice"],
        "model_score_seen": False,
        "source_condition_seen": False,
        "location_accuracy": "correct",
        "error_type_accuracy": "correct",
        "advice_actionability": "actionable",
        "unsupported_claim": "absent",
        "dangerous_expression": False,
        "discriminatory_expression": False,
        "speaker_identity_inference": False,
        "medical_judgment": False,
        "risk_taxonomy_sha256": SHA_C,
        "risk_codes": [],
        "rating_receipt_sha256": SHA_D,
    }


def _rating_manifest(*, role: str = "original") -> dict[str, object]:
    return {
        "schema_version": contract.RATING_SCHEMA_VERSION,
        "manifest_id": "c03ratings_" + "3" * 32,
        "item_manifest_sha256": SHA_A,
        "reviewer_attestation_manifest_sha256": SHA_B,
        "preregistration_sha256": SHA_C,
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "ratings": [_rating(role=role)],
    }


def _metric(numerator: int, denominator: int) -> dict[str, object]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator,
    }


def _report() -> dict[str, object]:
    return {
        "schema_version": contract.REPORT_SCHEMA_VERSION,
        "report_id": "c03report_" + "4" * 32,
        "item_manifest_sha256": SHA_A,
        "rating_manifest_sha256": SHA_B,
        "preregistration_sha256": SHA_C,
        "reviewer_attestation_manifest_sha256": SHA_D,
        "privacy_zone": "A1_aggregate_only",
        "primary_sample_count": 200,
        "reviewer_count": 2,
        "original_rating_count": 400,
        "adjudication_rating_count": 0,
        "primary_metrics": {
            "location_accuracy": _metric(180, 200),
            "error_type_accuracy": _metric(180, 200),
            "unsupported_claim_rate": _metric(3, 200),
        },
        "actionability": _metric(170, 200),
        "abstention_referral_audit": {
            "sample_count": 20,
            "metric_definition_sha256": SHA_E,
            "numerator": 2,
            "denominator": 20,
            "rate": 0.1,
            "gate_applied": False,
        },
        "statistical_contract": {
            "confidence_interval_method_sha256": SHA_A,
            "multiplicity_method_sha256": SHA_B,
            "support_rule_sha256": SHA_C,
            "adjudication_rule_sha256": SHA_D,
            "risk_taxonomy_sha256": SHA_E,
            "confidence_intervals": [
                {
                    "metric_id": "location_accuracy",
                    "lower": 0.84,
                    "upper": 0.94,
                },
                {
                    "metric_id": "error_type_accuracy",
                    "lower": 0.84,
                    "upper": 0.94,
                },
                {
                    "metric_id": "unsupported_claim_rate",
                    "lower": 0.0,
                    "upper": 0.04,
                },
            ],
            "multiplicity_results": [],
            "support_results": [],
        },
        "zero_tolerance_safety": {
            "dangerous_expression_count": 0,
            "discriminatory_expression_count": 0,
            "speaker_identity_inference_count": 0,
            "medical_judgment_count": 0,
            "any_event": False,
            "pass_allowed": True,
        },
        "decision": {
            "location_gate_passed": True,
            "error_type_gate_passed": True,
            "unsupported_claim_gate_passed": True,
            "zero_tolerance_gate_passed": True,
            "overall_passed": True,
            "status": "passed",
            "revision_required": False,
            "reaudit_required": False,
            "production_release_allowed": True,
        },
    }


def _approval() -> dict[str, object]:
    return {
        "schema_version": contract.APPROVAL_SCHEMA_VERSION,
        "approval_id": "c03-approval-2026",
        "status": "approved",
        "protocol_sha256": SHA_A,
        "institutional_authority_receipt_sha256": SHA_B,
        "expert_evaluation_authority_receipt_sha256": SHA_C,
        "audio_use_approved": True,
        "target_sentence_display_approved": True,
        "top1_advice_display_approved": True,
        "consent_or_lawful_basis_verified": True,
        "withdrawal_process_verified": True,
        "purpose_limitation_verified": True,
        "retention_schedule_verified": True,
        "access_control_verified": True,
        "reviewer_confidentiality_required": True,
        "reviewer_mapping_stored_separately": True,
        "raw_audio_export_allowed": False,
        "privacy_zone": "C1_public_attestation",
    }


def _reviewers(approval_sha: str = SHA_A) -> dict[str, object]:
    return {
        "schema_version": contract.REVIEWER_SCHEMA_VERSION,
        "status": "approved",
        "institutional_expert_approval_sha256": approval_sha,
        "reviewer_count": 1,
        "qualification_policy_sha256": SHA_B,
        "rubric_sha256": SHA_C,
        "risk_taxonomy_sha256": SHA_D,
        "reviewers": [
            {
                "reviewer_id_sha256": SHA_E,
                "qualification_receipt_sha256": SHA_A,
                "confidentiality_receipt_sha256": SHA_B,
                "rubric_training_receipt_sha256": SHA_C,
                "model_score_blinding_attested": True,
                "source_condition_blinding_attested": True,
                "conflict_of_interest_cleared": True,
            }
        ],
        "every_reviewer_qualified": True,
        "every_reviewer_confidentiality_attested": True,
        "every_reviewer_blind_to_model_score": True,
        "every_reviewer_blind_to_source_condition": True,
        "reviewer_identity_mapping_stored_separately": True,
        "privacy_zone": "C1_public_attestation",
    }


def _resolved_prereg(
    reviewer: contract.ReviewerEvidence,
) -> dict[str, object]:
    template_path = (
        ROOT / "configs/coaching/c03_expert_audit_preregistration_v1.template.json"
    )
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "status": "approved",
            "frozen": True,
            "institutional_expert_approval_sha256": SHA_A,
            "reviewer_attestation_manifest_sha256": reviewer.sha256,
            "s107_completion_commit_sha256": SHA_B,
            "c02_completion_commit_sha256": SHA_C,
            "exact_primary_sampling_allocation": [
                {
                    "l1": "l1_ko",
                    "error_type": "segmental_substitution",
                    "confidence_bin": "mid",
                    "count": 200,
                }
            ],
            "privacy_zone": "C1_public_preregistration",
        }
    )
    payload["abstention_referral_sampling"].update(
        {
            "relation_to_primary_sample": "additional_disjoint_sample",
            "exact_count_or_allocation": {"count": 20},
            "metric_definition": "over_referral_rate",
            "gate": "reported_without_gate",
        }
    )
    payload["reviewer_design"].update(
        {
            "reviewer_count_n": 1,
            "qualification_requirements": "policy_bound_by_attestation",
            "ratings_per_item": 1,
            "assignment_policy": "independent",
            "adjudication_trigger": "any_disagreement",
            "adjudication_protocol": "preserve_original_then_adjudicate",
            "agreement_metric_and_support": "preregistered_policy_v1",
        }
    )
    for metric in payload["primary_metrics"].values():
        metric["exact_numerator_denominator"] = "adjudicated_item_level"
    payload["primary_metrics"]["advice_actionability"]["gate"] = "reported_without_gate"
    payload["zero_tolerance_safety"]["detailed_risk_taxonomy"] = "taxonomy_v1"
    payload["statistics"].update(
        {
            "confidence_interval_method": "method_v1",
            "confidence_level": 0.95,
            "cluster_or_resampling_unit": "audit_item",
            "multiplicity_control": "method_v1",
            "minimum_support_rule": "rule_v1",
            "small_support_policy": "report_only",
        }
    )
    payload["approval"].update({"decision": "approved", "signed_receipt_sha256": SHA_D})
    return payload


def test_repository_config_only_has_zero_runtime_access() -> None:
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
    assert result["source_pin_count"] == 20
    assert result["source_pin_read_count"] == 20
    for field, value in result.items():
        if field.endswith("_count") and field != "source_pin_count":
            if field != "source_pin_read_count":
                assert value == 0, field


def test_full_preflight_stops_at_first_s107_marker_before_any_runtime_path() -> None:
    config = contract.load_config(CONFIG_PATH)
    with pytest.raises(
        contract.C03ContractError, match="TBD_S107_COMPLETION"
    ) as caught:
        contract.run_full_preflight(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=config,
        )
    error = caught.value
    assert error.stage == "s107_completion"
    assert error.audit is not None
    audit = error.audit.as_dict()
    assert audit["source_pin_read_count"] == 20
    assert audit["source_toctou_verification_count"] == 20
    for field, value in audit.items():
        if field not in ("source_pin_read_count", "source_toctou_verification_count"):
            assert value == 0, field


def test_pinned_c02_contract_loader_supports_dataclass_modules() -> None:
    config = contract.load_config(CONFIG_PATH)
    c02 = config["predecessor_c02"]
    module = contract._load_module(
        workspace_root=ROOT,
        relative=c02["contract_path"],
        expected_sha256=c02["contract_sha256"],
    )
    assert callable(module.deep_validate_s107)
    assert callable(module.validate_coaching_output_payload)


def test_unresolved_c02_marker_stops_before_commit_or_bundle_path() -> None:
    config = contract.load_config(CONFIG_PATH)
    audit = contract.AccessAudit()
    with pytest.raises(contract.C03ContractError, match="TBD_C02_COMPLETION") as caught:
        contract._deep_validate_c02(
            workspace_root=ROOT,
            config=config,
            s107=object(),  # type: ignore[arg-type]
            audit=audit,
        )
    assert caught.value.stage == "c02_completion"
    for field in (
        "c02_completion_path_resolution_count",
        "c02_completion_read_attempt_count",
        "c02_completion_read_count",
        "c02_bundle_path_resolution_count",
        "c02_bundle_read_attempt_count",
        "c02_bundle_read_count",
        "c02_bundle_row_read_count",
        "c02_deterministic_row_validation_count",
        "approval_path_resolution_count",
        "reviewer_attestation_path_resolution_count",
        "preregistration_path_resolution_count",
        "audit_item_path_resolution_count",
        "audio_path_resolution_count",
        "rating_path_resolution_count",
        "metric_count",
        "output_path_resolution_count",
        "commit_write_count",
    ):
        assert getattr(audit, field) == 0, field


def test_static_config_preserves_only_evidence_backed_fixed_values() -> None:
    config = contract.load_config(CONFIG_PATH)
    assert config["sampling_and_blinding"]["primary_sample_count"] == 200
    assert config["sampling_and_blinding"]["stratification_axes"] == [
        "l1",
        "error_type",
        "confidence_bin",
    ]
    assert (
        config["sampling_and_blinding"]["exact_sampling_allocation"]
        == contract.TBD_PREREGISTRATION
    )
    assert (
        config["reviewer_attestations"]["reviewer_count_n"] == contract.TBD_ATTESTATION
    )
    assert config["gates"]["unsupported_claim_rate"] == {
        "operator": "less_than",
        "threshold": 0.02,
    }


def test_item_manifest_enforces_200_primary_three_strata_and_separate_abstention() -> (
    None
):
    payload = _item_manifest()
    assert contract.validate_item_manifest_payload(payload) == payload

    wrong_count = deepcopy(payload)
    wrong_count["items"].pop()
    wrong_count["abstention_referral_item_count"] = 1
    with pytest.raises(contract.C03ContractError, match="abstention row count"):
        contract.validate_item_manifest_payload(wrong_count)

    exposed = deepcopy(payload)
    exposed["items"][0]["model_score_exposed_to_reviewer"] = True
    with pytest.raises(contract.C03ContractError, match="model score"):
        contract.validate_item_manifest_payload(exposed)


def test_item_manifest_rejects_raw_path_or_plain_target_text() -> None:
    payload = _item_manifest()
    payload["items"][0]["audio_path"] = "data/raw/person.wav"
    with pytest.raises(contract.C03ContractError, match="forbidden field"):
        contract.validate_item_manifest_payload(payload)


def test_rating_manifest_preserves_blinding_and_adjudication_linkage() -> None:
    payload = _rating_manifest()
    assert (
        contract.validate_rating_manifest_payload(
            payload,
            allowed_item_ids={"c03item_" + "0" * 32},
        )
        == payload
    )

    adjudication = _rating_manifest(role="adjudication")
    assert contract.validate_rating_manifest_payload(adjudication) == adjudication

    exposed = deepcopy(payload)
    exposed["ratings"][0]["model_score_seen"] = True
    with pytest.raises(contract.C03ContractError, match="model score"):
        contract.validate_rating_manifest_payload(exposed)


def test_report_exact_gate_boundaries_and_strict_unsupported_operator() -> None:
    passing = _report()
    assert contract.validate_report_payload(passing) == passing

    exactly_two_percent = deepcopy(passing)
    exactly_two_percent["primary_metrics"]["unsupported_claim_rate"] = _metric(4, 200)
    exactly_two_percent["decision"].update(
        {
            "unsupported_claim_gate_passed": False,
            "overall_passed": False,
            "status": "failed_metric_gate",
            "reaudit_required": True,
            "production_release_allowed": False,
        }
    )
    assert contract.validate_report_payload(exactly_two_percent) == exactly_two_percent


def test_any_zero_tolerance_event_forces_revision_reaudit_and_no_pass() -> None:
    payload = _report()
    payload["zero_tolerance_safety"].update(
        {
            "dangerous_expression_count": 1,
            "any_event": True,
            "pass_allowed": False,
        }
    )
    payload["decision"].update(
        {
            "zero_tolerance_gate_passed": False,
            "overall_passed": False,
            "status": "failed_safety_revise_and_reaudit",
            "revision_required": True,
            "reaudit_required": True,
            "production_release_allowed": False,
        }
    )
    assert contract.validate_report_payload(payload) == payload

    false_pass = deepcopy(payload)
    false_pass["decision"]["overall_passed"] = True
    with pytest.raises(contract.C03ContractError, match="overall decision"):
        contract.validate_report_payload(false_pass)


def test_approval_reviewer_and_frozen_preregistration_bind_in_order() -> None:
    approval = _approval()
    assert contract.validate_approval_payload(approval) == approval
    reviewer = contract.validate_reviewer_attestation_payload(
        _reviewers(),
        approval_sha256=SHA_A,
    )
    prereg = _resolved_prereg(reviewer)
    assert (
        contract.validate_preregistration_payload(
            prereg,
            approval_sha256=SHA_A,
            reviewer=reviewer,
            s107_commit_sha256=SHA_B,
            c02_commit_sha256=SHA_C,
        )
        == prereg
    )


def test_preregistration_rejects_unresolved_design_and_reviewer_n_drift() -> None:
    reviewer = contract.validate_reviewer_attestation_payload(
        _reviewers(),
        approval_sha256=SHA_A,
    )
    unresolved = _resolved_prereg(reviewer)
    unresolved["statistics"]["multiplicity_control"] = "TBD_PREREGISTRATION"
    with pytest.raises(contract.C03ContractError, match="cannot contain TBD"):
        contract.validate_preregistration_payload(
            unresolved,
            approval_sha256=SHA_A,
            reviewer=reviewer,
            s107_commit_sha256=SHA_B,
            c02_commit_sha256=SHA_C,
        )

    drift = _resolved_prereg(reviewer)
    drift["reviewer_design"]["reviewer_count_n"] = 2
    with pytest.raises(contract.C03ContractError, match="reviewer N"):
        contract.validate_preregistration_payload(
            drift,
            approval_sha256=SHA_A,
            reviewer=reviewer,
            s107_commit_sha256=SHA_B,
            c02_commit_sha256=SHA_C,
        )


def test_public_schemas_are_strict_and_templates_remain_unresolved() -> None:
    schema_paths = (
        "c03_expert_audit_item_manifest_v1.schema.json",
        "c03_expert_rating_manifest_v1.schema.json",
        "c03_expert_audit_report_v1.schema.json",
    )
    for filename in schema_paths:
        payload = json.loads(
            (ROOT / "configs/coaching" / filename).read_text(encoding="utf-8")
        )
        assert payload["additionalProperties"] is False
        assert set(payload["required"]) == set(payload["properties"])

    prereg = json.loads(
        (
            ROOT / "configs/coaching/c03_expert_audit_preregistration_v1.template.json"
        ).read_text(encoding="utf-8")
    )
    assert contract._contains_tbd(prereg)
    assert prereg["reviewer_design"]["reviewer_count_n"] == "TBD_ATTESTATION"
    assert prereg["statistics"]["confidence_interval_method"] == ("TBD_PREREGISTRATION")
