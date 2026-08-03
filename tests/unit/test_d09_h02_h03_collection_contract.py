from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from voice_coach.human_research.d09_h02_h03_collection_contract import (
    AccessAudit,
    D09ContractError,
    DeliveryEvidence,
    H00Evidence,
    ManualEvidence,
    PreregEvidence,
    binding_sha256,
    load_config,
    validate_audio_qc_record,
    validate_authorization_payload,
    validate_blind_rating_record,
    validate_comparison_stage_separation_payload,
    validate_delivery_receipt_payload,
    validate_disposition_record,
    validate_intervention_exposure_record,
    validate_manual_payload,
    validate_participant_record,
    validate_preregistration_payload,
    validate_randomization_record,
    validate_session_record,
    validate_static_config,
)


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG_PATH = (
    WORKSPACE / "configs/experiments/d09_h02_h03_longitudinal_collection_v1.yaml"
)
ZERO = "0" * 64
ONE = "1" * 64
TWO = "2" * 64
THREE = "3" * 64
FOUR = "4" * 64
FIVE = "5" * 64
SIX = "6" * 64
SEVEN = "7" * 64
EIGHT = "8" * 64
NINE = "9" * 64
A_SHA = "a" * 64
B_SHA = "b" * 64
C_SHA = "c" * 64
D_SHA = "d" * 64
E_SHA = "e" * 64
F_SHA = "f" * 64


def _bind(payload: dict[str, object]) -> dict[str, object]:
    payload["binding_sha256"] = binding_sha256(payload)
    return payload


def _actual_config() -> dict[str, object]:
    config = deepcopy(load_config(CONFIG_PATH))
    delivery = config["delivery_predecessors"]
    assert isinstance(delivery, dict)
    delivery["s108"]["completion_sha256"] = ONE
    delivery["e01"]["completion_sha256"] = TWO
    delivery["c02"]["completion_sha256"] = THREE
    delivery["e02"]["completion_sha256"] = FOUR
    delivery["c03"]["report_sha256"] = FIVE
    delivery["e00"]["completion_sha256"] = SIX
    delivery["e00"]["d08_rights_lineage_sha256"] = SEVEN
    delivery["r03"]["bank_sha256"] = EIGHT
    delivery["r04"]["completion_sha256"] = NINE
    return config


def _delivery_payload(config: dict[str, object]) -> dict[str, object]:
    source = config["delivery_predecessors"]
    assert isinstance(source, dict)
    payload: dict[str, object] = {
        "schema_version": ("voice_coach.d09_h02_h03_delivery_predecessor_receipt.v1"),
        "receipt_id": f"d09delivery_{'a' * 32}",
        "status": "all_c3_c5_delivery_predecessors_deep_validated",
        "validated_at_utc": "2025-01-01T00:00:00Z",
        "s108": {
            "config_sha256": source["s108"]["config_sha256"],
            "contract_sha256": source["s108"]["contract_sha256"],
            "completion_sha256": ONE,
            "model_identity_sha256": A_SHA,
            "checkpoint_sha256": B_SHA,
            "calibrator_sha256": C_SHA,
            "threshold_policy_sha256": D_SHA,
            "deep_validation_receipt_sha256": E_SHA,
            "decision": "research_candidate_passed",
        },
        "e01": {
            "config_sha256": source["e01"]["config_sha256"],
            "contract_sha256": source["e01"]["contract_sha256"],
            "completion_sha256": TWO,
            "aggregate_report_sha256": A_SHA,
            "deep_validation_receipt_sha256": B_SHA,
            "status": "completed",
            "robustness_passed": True,
        },
        "c02": {
            "config_sha256": source["c02"]["config_sha256"],
            "contract_sha256": source["c02"]["contract_sha256"],
            "completion_sha256": THREE,
            "selection_output_sha256": A_SHA,
            "preregistration_sha256": B_SHA,
            "deep_validation_receipt_sha256": C_SHA,
            "production_release_allowed": True,
        },
        "e02": {
            "config_sha256": source["e02"]["config_sha256"],
            "contract_sha256": source["e02"]["contract_sha256"],
            "completion_sha256": FOUR,
            "guard_policy_sha256": A_SHA,
            "api_response_contract_sha256": B_SHA,
            "deep_validation_receipt_sha256": C_SHA,
            "status": "completed",
            "confidence_and_api_guard_passed": True,
        },
        "c03": {
            "config_sha256": source["c03"]["config_sha256"],
            "contract_sha256": source["c03"]["contract_sha256"],
            "report_sha256": FIVE,
            "deep_validation_receipt_sha256": A_SHA,
            "decision": "passed",
            "production_release_allowed": True,
        },
        "e00": {
            "config_sha256": source["e00"]["config_sha256"],
            "contract_sha256": source["e00"]["contract_sha256"],
            "completion_sha256": SIX,
            "reference_handoff_sha256": A_SHA,
            "d08_rights_lineage_sha256": SEVEN,
            "deep_validation_receipt_sha256": B_SHA,
            "status": "completed",
        },
        "r03": {
            "config_sha256": source["r03"]["config_sha256"],
            "contract_sha256": source["r03"]["contract_sha256"],
            "bank_manifest_sha256": EIGHT,
            "deep_validation_receipt_sha256": A_SHA,
            "status": "r03_selected_pending_r04",
            "publication_required": False,
        },
        "r04": {
            "config_sha256": source["r04"]["config_sha256"],
            "contract_sha256": source["r04"]["contract_sha256"],
            "completion_sha256": NINE,
            "aggregate_analysis_sha256": A_SHA,
            "deep_validation_receipt_sha256": B_SHA,
            "status": "completed",
            "blind_multireference_evaluation_passed": True,
        },
        "assurance": {
            "c3_uses_only_validated_s108_e01_c02_e02_c03": True,
            "c5_uses_validated_s108_e01_c02_e02_c03_e00_r03_r04": True,
            "e00_d08_rights_lineage_bound": True,
            "locked_result_copy_or_replay_used": False,
            "free_generation_used": False,
            "human_effect_or_outcome_used_for_predecessor_selection": False,
            "participant_rows_present": False,
            "audio_or_rating_rows_present": False,
            "aggregate_only": True,
        },
        "privacy_zone": "A1_aggregate_delivery_readiness",
        "binding_sha256": ZERO,
    }
    return _bind(payload)


def _delivery_evidence() -> DeliveryEvidence:
    config = _actual_config()
    return DeliveryEvidence(F_SHA, _delivery_payload(config))


def _manual_evidence() -> ManualEvidence:
    payload = {
        "c0": {"version_id": "c0_v1", "manual_sha256": A_SHA},
        "c3": {"version_id": "c3_v1", "manual_sha256": B_SHA},
        "c5": {"version_id": "c5_v1", "manual_sha256": C_SHA},
    }
    return ManualEvidence(D_SHA, payload)


def _replace_tbd(value: object, key: str = "") -> object:
    if isinstance(value, dict):
        return {
            child_key: _replace_tbd(child_value, child_key)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_replace_tbd(child, key) for child in value]
    if isinstance(value, str) and value.startswith("TBD_"):
        if key.endswith("_sha256"):
            return A_SHA
        if key == "phase":
            return "feasibility"
        if key.endswith("_at_utc"):
            return "2025-01-01T00:00:00Z"
        return "resolved_v1"
    return value


def _prereg_payload(
    h00: H00Evidence,
    delivery: DeliveryEvidence,
) -> dict[str, object]:
    template = json.loads(
        (
            WORKSPACE / "configs/human_research/"
            "d09_h02_h03_operational_preregistration_v1.template.json"
        ).read_text(encoding="utf-8")
    )
    payload = _replace_tbd(template)
    assert isinstance(payload, dict)
    payload.update(
        {
            "preregistration_id": f"d09prereg_{'c' * 32}",
            "phase": "feasibility",
            "status": "approved_public_and_frozen_before_phase_recruitment",
            "frozen_at_utc": "2025-01-01T00:00:00Z",
        }
    )
    payload["lineage"] = {
        "h00_approval_sha256": h00.approval_sha256,
        "h00_attestation_sha256": h00.attestation_sha256,
        "protocol_sha256": B_SHA,
        "delivery_predecessor_receipt_sha256": delivery.sha256,
        "feasibility_completion_sha256": ZERO,
        "h01_final_sample_size_lock_sha256": ZERO,
        "frozen_sap_sha256": ZERO,
    }
    payload["sample_size"].update(
        {
            "stage_1_final_enrollment_per_arm": 15,
            "stage_2_final_enrollment_per_arm": 20,
            "confirmatory_n_must_equal_h01_lock": False,
        }
    )
    payload["audio_qc"]["exact_sample_rate_hz"] = 16000
    payload["approval"].update(
        {
            "decision": "approved",
            "approved_and_frozen": True,
        }
    )
    payload["binding_sha256"] = ZERO
    return _bind(payload)


def _manual_payload(
    h00: H00Evidence,
    preregistration: PreregEvidence,
    delivery: DeliveryEvidence,
) -> dict[str, object]:
    template = json.loads(
        (
            WORKSPACE / "configs/human_research/"
            "d09_h02_h03_intervention_manual_v1.template.json"
        ).read_text(encoding="utf-8")
    )
    payload = _replace_tbd(template)
    assert isinstance(payload, dict)
    payload.update(
        {
            "manual_bundle_id": f"d09manual_{'d' * 32}",
            "phase": "feasibility",
            "status": "approved_versioned_and_frozen",
            "frozen_at_utc": "2025-01-01T00:00:00Z",
        }
    )
    payload["lineage"] = {
        "h00_approval_sha256": h00.approval_sha256,
        "operational_preregistration_sha256": preregistration.sha256,
        "delivery_predecessor_receipt_sha256": delivery.sha256,
    }
    payload["c0"].update({"version_id": "c0_v1", "manual_sha256": A_SHA})
    payload["c3"].update(
        {
            "version_id": "c3_v1",
            "manual_sha256": B_SHA,
            "s108_completion_sha256": ONE,
            "e01_completion_sha256": TWO,
            "c02_completion_sha256": THREE,
            "e02_completion_sha256": FOUR,
            "c03_report_sha256": FIVE,
        }
    )
    payload["c5"].update(
        {
            "version_id": "c5_v1",
            "manual_sha256": C_SHA,
            "e00_completion_sha256": SIX,
            "r03_bank_sha256": EIGHT,
            "r04_completion_sha256": NINE,
        }
    )
    payload["assurance"]["manuals_versioned_and_immutable"] = True
    payload["approval"].update(
        {
            "decision": "approved",
            "approved_and_frozen": True,
        }
    )
    payload["binding_sha256"] = ZERO
    return _bind(payload)


def _comparison_payload(delivery: DeliveryEvidence) -> dict[str, object]:
    common = {
        "s108_completion_sha256": ONE,
        "e01_completion_sha256": TWO,
        "c02_completion_sha256": THREE,
        "e02_completion_sha256": FOUR,
        "c03_completion_sha256": FIVE,
    }
    return _bind(
        {
            "schema_version": (
                "voice_coach.d09_h02_h03_comparison_stage_separation_receipt.v1"
            ),
            "receipt_id": f"d09comparison_{'b' * 32}",
            "phase": "feasibility",
            "status": "zero_overlap_independent_parallel_comparisons",
            "stage_1": {
                "comparison": "c3_minus_c0",
                "allowed_conditions": ["C0", "C3"],
                "randomization_receipt_sha256": A_SHA,
                "participant_set_sha256": B_SHA,
                "audio_set_sha256": C_SHA,
                "rating_set_sha256": D_SHA,
                "gate_receipt_sha256": E_SHA,
                **common,
                "e00_completion_sha256": ZERO,
                "r03_bank_sha256": ZERO,
                "r04_completion_sha256": ZERO,
            },
            "stage_2": {
                "comparison": "c5_minus_c3",
                "allowed_conditions": ["C3", "C5"],
                "randomization_receipt_sha256": F_SHA,
                "participant_set_sha256": E_SHA,
                "audio_set_sha256": F_SHA,
                "rating_set_sha256": A_SHA,
                "gate_receipt_sha256": B_SHA,
                **common,
                "e00_completion_sha256": SIX,
                "r03_bank_sha256": EIGHT,
                "r04_completion_sha256": NINE,
            },
            "stage_1_randomization_closed_at_utc": "2025-01-01T00:00:00Z",
            "stage_2_activation_authorized_at_utc": "2025-01-02T00:00:00Z",
            "stage_2_randomization_opened_at_utc": "2025-01-03T00:00:00Z",
            "stage_2_activation_decision_receipt_sha256": C_SHA,
            "stage_1_unblinded_effect_access_log_sha256": D_SHA,
            "stage_1_unblinded_effect_access_count_before_stage_2_activation": 0,
            "stage_1_outcome_or_effect_available_at_stage_2_activation": False,
            "stage_2_activation_basis": (
                "approval_safety_privacy_and_delivery_readiness_only"
            ),
            "activation_time_order_verified": True,
            "participant_intersection_count": 0,
            "audio_intersection_count": 0,
            "rating_intersection_count": 0,
            "shared_c3_participant_intersection_count": 0,
            "copied_or_cloned_row_count": 0,
            "comparison_participant_audio_rating_reuse_allowed": False,
            "independent_participant_unit_parallel_comparisons": True,
            "privacy_zone": "A1_aggregate_comparison_stage_separation",
            "binding_sha256": ZERO,
        }
    )


def test_static_contract_reads_exactly_46_sources() -> None:
    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CONFIG_PATH,
        config=load_config(CONFIG_PATH),
        audit=audit,
    )
    assert len(static.source_snapshots) == 46
    assert audit.source_pin_read_count == 46
    assert audit.recruitment_count == 0
    assert audit.audio_read_count == 0
    assert audit.output_count == 0


def test_all_d09_schemas_are_valid_draft_2020_12() -> None:
    for path in sorted(
        (WORKSPACE / "configs/human_research").glob("d09_h02_h03_*schema.json")
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert payload["type"] == "object"
        assert payload["additionalProperties"] is False
        assert set(payload["required"]) == set(payload["properties"])


def test_delivery_receipt_requires_e01_e02_r04_and_d08_lineage() -> None:
    config = _actual_config()
    payload = _delivery_payload(config)
    validate_delivery_receipt_payload(payload, config=config)
    tampered = deepcopy(payload)
    tampered["e00"]["d08_rights_lineage_sha256"] = A_SHA
    tampered["binding_sha256"] = binding_sha256(tampered)
    with pytest.raises(D09ContractError, match="D08 rights lineage"):
        validate_delivery_receipt_payload(tampered, config=config)


def test_comparison_receipt_rejects_shared_c3_participants() -> None:
    delivery = _delivery_evidence()
    payload = _comparison_payload(delivery)
    validate_comparison_stage_separation_payload(
        payload,
        phase="feasibility",
        delivery=delivery,
    )
    tampered = deepcopy(payload)
    tampered["shared_c3_participant_intersection_count"] = 1
    tampered["binding_sha256"] = binding_sha256(tampered)
    with pytest.raises(D09ContractError, match="shared_c3"):
        validate_comparison_stage_separation_payload(
            tampered,
            phase="feasibility",
            delivery=delivery,
        )


def test_comparison_receipt_rejects_unblinded_effect_access() -> None:
    delivery = _delivery_evidence()
    payload = _comparison_payload(delivery)
    payload["stage_1_unblinded_effect_access_count_before_stage_2_activation"] = 1
    payload["binding_sha256"] = binding_sha256(payload)
    with pytest.raises(D09ContractError, match="access_count"):
        validate_comparison_stage_separation_payload(
            payload,
            phase="feasibility",
            delivery=delivery,
        )


def test_participant_comparison_condition_pair_is_fail_closed() -> None:
    payload = {
        "schema_version": "voice_coach.d09_h02_h03_participant.v1",
        "record_id_sha256": ONE,
        "phase": "feasibility",
        "comparison": "c3_minus_c0",
        "assigned_condition": "C3",
        "participant_key_sha256": TWO,
        "consent_receipt_sha256": THREE,
        "l1_stratum_id": "korean_l1",
        "baseline_stratum_id": "baseline_mid",
        "randomized": True,
        "participant_blinding_claimed": False,
        "direct_identifiers_present": False,
        "privacy_zone": "D1_pseudonymized_restricted_human_research",
    }
    validate_participant_record(payload, phase="feasibility")
    payload["assigned_condition"] = "C5"
    with pytest.raises(D09ContractError, match="not in"):
        validate_participant_record(payload, phase="feasibility")


def test_stage_randomization_row_binds_exact_gate_and_allowed_arm() -> None:
    delivery = _delivery_evidence()
    payload = {
        "schema_version": "voice_coach.d09_h02_h03_randomization.v1",
        "assignment_receipt_id_sha256": ONE,
        "participant_key_sha256": TWO,
        "phase": "feasibility",
        "comparison": "c3_minus_c0",
        "randomization_stage": "stage_1",
        "assigned_condition": "C0",
        "allocation_unit": "participant",
        "allocation_ratio": "1:1",
        "l1_stratum_id": "korean_l1",
        "baseline_stratum_id": "baseline_mid",
        "stage_randomization_receipt_sha256": THREE,
        "stage_gate_receipt_sha256": FOUR,
        "s108_completion_sha256": ONE,
        "e01_completion_sha256": TWO,
        "c02_completion_sha256": THREE,
        "e02_completion_sha256": FOUR,
        "c03_completion_sha256": FIVE,
        "e00_completion_sha256": ZERO,
        "r03_bank_sha256": ZERO,
        "r04_completion_sha256": ZERO,
        "allocation_concealed_until_assignment": True,
        "stage_2_activation_basis": "not_applicable_stage_1",
        "unblinded_effect_used_for_stage_2_activation": False,
        "privacy_zone": "D1_pseudonymized_restricted_human_research",
    }
    validate_randomization_record(payload, delivery=delivery)
    payload["assigned_condition"] = "C5"
    with pytest.raises(D09ContractError, match="not in"):
        validate_randomization_record(payload, delivery=delivery)


def test_exposure_c3_requires_e01_e02_and_forbids_r04() -> None:
    payload = {
        "schema_version": "voice_coach.d09_h02_h03_intervention_exposure.v1",
        "exposure_record_id_sha256": ONE,
        "participant_key_sha256": TWO,
        "session_id_sha256": THREE,
        "phase": "feasibility",
        "assigned_condition": "C3",
        "manual_version_id": "c3_v1",
        "manual_sha256": B_SHA,
        "s108_completion_sha256": ONE,
        "e01_completion_sha256": TWO,
        "c02_completion_sha256": THREE,
        "e02_completion_sha256": FOUR,
        "c03_completion_sha256": FIVE,
        "e00_completion_sha256": ZERO,
        "r03_bank_sha256": ZERO,
        "r04_completion_sha256": ZERO,
        "assigned_seconds": 100,
        "received_seconds": 80,
        "assigned_item_count": 10,
        "received_item_count": 8,
        "adherence_fraction": 0.8,
        "coaching_delivered": True,
        "reference_delivered": False,
        "blocked_safe_action_preserved": True,
        "protocol_deviation_retained": True,
        "free_generation_used": False,
        "privacy_zone": "D1_pseudonymized_restricted_human_research",
    }
    validate_intervention_exposure_record(
        payload,
        manual=_manual_evidence(),
        delivery=_delivery_evidence(),
    )
    payload["r04_completion_sha256"] = NINE
    with pytest.raises(D09ContractError, match="C3 exposure"):
        validate_intervention_exposure_record(
            payload,
            manual=_manual_evidence(),
            delivery=_delivery_evidence(),
        )


def test_session_qc_rating_and_disposition_preserve_receipt_semantics() -> None:
    session = {
        "schema_version": "voice_coach.d09_h02_h03_session.v1",
        "session_id_sha256": ONE,
        "participant_key_sha256": TWO,
        "phase": "feasibility",
        "assigned_condition": "C0",
        "session_index": 0,
        "session_kind": "pre_baseline",
        "scheduled_at_utc": "2025-01-01T00:00:00Z",
        "occurred": True,
        "protocol_deviation_present": False,
        "protocol_deviation_receipt_sha256": ZERO,
        "privacy_zone": "D1_pseudonymized_restricted_human_research",
    }
    validate_session_record(session)
    qc = {
        "schema_version": "voice_coach.d09_h02_h03_audio_qc.v1",
        "qc_record_id_sha256": ONE,
        "audio_object_id_sha256": TWO,
        "phase": "feasibility",
        "timepoint": "pre",
        "frozen_qc_policy_sha256": THREE,
        "decode_passed": True,
        "sample_rate_hz": 16000,
        "duration_ms": 1000,
        "clipping_fraction": 0.0,
        "snr_db": 20.0,
        "baseline_hard_qc_eligibility_only": True,
        "postrandomization_exclusion_allowed": False,
        "status": "passed",
        "coded_reason_id": "passed",
        "privacy_zone": "D1_pseudonymized_restricted_human_research",
    }
    validate_audio_qc_record(qc)
    rating = {
        "schema_version": "voice_coach.d09_h02_h03_blind_rating.v1",
        "rating_record_id_sha256": ONE,
        "blind_item_code_sha256": TWO,
        "audio_object_id_sha256": THREE,
        "rater_key_sha256": FOUR,
        "phase": "feasibility",
        "outcome": "comprehensibility",
        "score_1_to_5": 4,
        "rater_blinded_to_condition": True,
        "rater_blinded_to_timepoint": True,
        "rater_blinded_to_participant_identity": True,
        "condition_field_present_in_rating_view": False,
        "timepoint_field_present_in_rating_view": False,
        "participant_identity_present_in_rating_view": False,
        "adjudicated": False,
        "adjudication_receipt_sha256": ZERO,
        "privacy_zone": "D1_pseudonymized_restricted_human_research",
    }
    validate_blind_rating_record(rating)
    disposition = {
        "schema_version": "voice_coach.d09_h02_h03_disposition.v1",
        "disposition_record_id_sha256": ONE,
        "participant_key_sha256": TWO,
        "phase": "feasibility",
        "comparison": "c3_minus_c0",
        "assigned_condition": "C0",
        "randomized": True,
        "withdrawn": False,
        "withdrawal_coded_receipt_sha256": ZERO,
        "followup_status": "complete",
        "missingness_coded_receipt_sha256": ZERO,
        "protocol_deviation_present": False,
        "protocol_deviation_receipt_sha256": ZERO,
        "adverse_feedback_present": False,
        "adverse_feedback_receipt_sha256": ZERO,
        "retained_in_itt": True,
        "postrandomization_exclusion_used": False,
        "free_text_present": False,
        "privacy_zone": "D1_pseudonymized_restricted_human_research",
    }
    validate_disposition_record(disposition)


def test_actual_feasibility_prereg_manual_and_authorization_chain() -> None:
    h00 = H00Evidence(ONE, TWO)
    delivery = _delivery_evidence()
    prereg_payload = _prereg_payload(h00, delivery)
    validate_preregistration_payload(
        prereg_payload,
        phase="feasibility",
        h00=h00,
        h04=None,
        delivery=delivery,
    )
    preregistration = PreregEvidence(D_SHA, prereg_payload)
    manual_payload = _manual_payload(h00, preregistration, delivery)
    validate_manual_payload(
        manual_payload,
        phase="feasibility",
        h00=h00,
        preregistration=preregistration,
        delivery=delivery,
    )
    manual = ManualEvidence(E_SHA, manual_payload)
    template = json.loads(
        (
            WORKSPACE / "configs/human_research/"
            "d09_h02_h03_execution_authorization_v1.template.json"
        ).read_text(encoding="utf-8")
    )
    authorization = _replace_tbd(template)
    assert isinstance(authorization, dict)
    authorization.update(
        {
            "authorization_id": f"d09auth_{'e' * 32}",
            "phase": "feasibility",
            "status": "active_for_phase_operations",
            "valid_from_utc": "2020-01-01T00:00:00Z",
            "valid_until_utc": "2099-01-01T00:00:00Z",
        }
    )
    authorization["lineage"] = {
        "h00_approval_sha256": h00.approval_sha256,
        "h00_attestation_sha256": h00.attestation_sha256,
        "operational_preregistration_sha256": preregistration.sha256,
        "intervention_manual_bundle_sha256": manual.sha256,
        "delivery_predecessor_receipt_sha256": delivery.sha256,
        "feasibility_completion_sha256": ZERO,
        "h01_final_sample_size_lock_sha256": ZERO,
        "frozen_sap_sha256": ZERO,
    }
    for field in (
        "recruitment",
        "consent",
        "participant_randomization",
        "audio_recording",
        "intervention_delivery",
        "blind_rating",
        "restricted_d1_processing",
    ):
        authorization["authorized_operations"][field] = True
    for field in authorization["operational_assurance"]:
        if field != "participant_blinding_claimed":
            authorization["operational_assurance"][field] = True
    authorization["approval"].update(
        {
            "decision": "approved",
            "active": True,
        }
    )
    authorization["binding_sha256"] = ZERO
    authorization = _bind(authorization)
    validate_authorization_payload(
        authorization,
        phase="feasibility",
        h00=h00,
        h04=None,
        delivery=delivery,
        preregistration=preregistration,
        manual=manual,
    )
    authorization["authorized_operations"]["statistics_or_effect_analysis"] = True
    authorization["binding_sha256"] = binding_sha256(authorization)
    with pytest.raises(D09ContractError, match="statistics_or_effect_analysis"):
        validate_authorization_payload(
            authorization,
            phase="feasibility",
            h00=h00,
            h04=None,
            delivery=delivery,
            preregistration=preregistration,
            manual=manual,
        )
