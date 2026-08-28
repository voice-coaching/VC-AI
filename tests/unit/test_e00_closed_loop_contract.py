from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from voice_coach.evaluation.e00_closed_loop_contract import (
    AccessAudit,
    E00ContractError,
    binding_sha256,
    canonical_json_bytes,
    load_config,
    validate_completion_payload,
    validate_d08_r03_lineage_receipt_payload,
    validate_d08_aggregate_completion_payload,
    validate_execution_authorization_payload,
    validate_preregistration_payload,
    validate_reference_handoff_payload,
    validate_s108_diagnostic_handoff_payload,
    validate_session_manifest_payload,
    validate_static_config,
    validate_trace_payload,
)

WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG_PATH = WORKSPACE / "configs/experiments/e00_closed_loop_demo_v1.yaml"
SCHEMA_PATHS = (
    WORKSPACE / "configs/demo/e00_closed_loop_completion_v1.schema.json",
    WORKSPACE / "configs/demo/e00_closed_loop_session_v1.schema.json",
    WORKSPACE / "configs/demo/e00_closed_loop_trace_v1.schema.json",
    WORKSPACE / "configs/demo/e00_execution_authorization_v1.schema.json",
    WORKSPACE / "configs/demo/e00_d08_r03_lineage_receipt_v1.schema.json",
    WORKSPACE / "configs/demo/e00_reference_handoff_v1.schema.json",
    WORKSPACE / "configs/demo/e00_s108_diagnostic_handoff_v1.schema.json",
)


def _h(index: int) -> str:
    return hashlib.sha256(f"e00-test-{index}".encode()).hexdigest()


def _bind(payload: dict[str, Any]) -> dict[str, Any]:
    payload["binding_sha256"] = binding_sha256(payload)
    return payload


def _resolved_config() -> dict[str, Any]:
    config = deepcopy(dict(load_config(CONFIG_PATH)))
    predecessors = config["predecessors"]
    predecessors["s108"].update(
        {
            "completion_commit_sha256": _h(1),
            "model_identity_sha256": _h(1),
            "checkpoint_sha256": _h(2),
            "calibrator_sha256": _h(3),
            "threshold_policy_sha256": _h(4),
        }
    )
    predecessors["e01"]["completion_commit_sha256"] = _h(5)
    predecessors["c02_e02"].update(
        {
            "c02_bundle_commit_sha256": _h(6),
            "e02_preregistration_sha256": _h(7),
        }
    )
    predecessors["c03"]["report_sha256"] = _h(8)
    predecessors["r03"]["bank_manifest_sha256"] = _h(9)
    predecessors["d08"]["completion_sha256"] = _h(10)
    return config


def _preregistration(
    config: dict[str, Any],
    *,
    mode: str = "nonhuman_test_fixture",
    trace_count: int = 3,
) -> dict[str, Any]:
    predecessors = config["predecessors"]
    payload: dict[str, Any] = {
        "schema_version": "voice_coach.e00_closed_loop_preregistration.v1",
        "preregistration_id": "e00prereg_" + "a" * 32,
        "status": "approved_and_frozen",
        "execution_mode": mode,
        "frozen_before_session_or_audio_access": True,
        "predecessor_bindings": {
            "s108_completion_commit_sha256": predecessors["s108"][
                "completion_commit_sha256"
            ],
            "e01_completion_commit_sha256": predecessors["e01"][
                "completion_commit_sha256"
            ],
            "c02_bundle_commit_sha256": predecessors["c02_e02"][
                "c02_bundle_commit_sha256"
            ],
            "c03_report_sha256": predecessors["c03"]["report_sha256"],
            "e02_preregistration_sha256": predecessors["c02_e02"][
                "e02_preregistration_sha256"
            ],
            "r03_bank_manifest_sha256": predecessors["r03"]["bank_manifest_sha256"],
            "d08_completion_sha256": predecessors["d08"]["completion_sha256"],
        },
        "execution_population": {
            "demo_trace_count": trace_count,
            "target_sentence_selection_policy_sha256": _h(20),
            "human_eligibility_policy_sha256": _h(21),
            "fixture_provenance_policy_sha256": _h(22),
            "human_and_fixture_results_mixed": False,
        },
        "closed_loop_contract": {
            "attempt_count_per_eligible_loop": 2,
            "s108_actual_production_handoff_required": True,
            "locked_result_copy_or_replay_allowed": False,
            "e02_guard_before_any_delivery_required": True,
            "c02_selected_count": 1,
            "catalog_only": True,
            "free_generation_allowed": False,
            "r03_d08_licensed_reference_required": True,
            "same_scorer_pre_post_required": True,
            "result_based_scorer_change_allowed": False,
        },
        "blocked_policy": {
            "blocked_reasons": [
                "out_of_domain",
                "low_confidence",
                "unsupported",
                "abstain_all",
            ],
            "automatic_coaching_allowed": False,
            "automatic_reference_allowed": False,
            "automatic_corrective_action_allowed": False,
            "safe_next_actions": ["rerecord", "expert_review"],
            "reason_precedence_sha256": _h(23),
            "reason_to_action_policy_sha256": _h(24),
        },
        "comparison": {
            "paired_metric_family": "same_s108_diagnostic_profile",
            "paired_metric_definition_sha256": _h(25),
            "attempt_qc_policy_sha256": _h(26),
            "missing_or_failed_attempt_policy_sha256": _h(27),
            "exact_effect_estimand": "not_applicable_noninferential_demo",
            "confidence_interval_method": "not_applicable_noninferential_demo",
            "confidence_level": "not_applicable_noninferential_demo",
            "multiplicity_method": "not_applicable_noninferential_demo",
            "minimum_support": "not_applicable_noninferential_demo",
            "inferential_analysis_enabled_for_e00_demo": False,
        },
        "claims": {
            "noninferential_demo_only": True,
            "pedagogical_effect_claim_allowed": False,
            "clinical_claim_allowed": False,
            "production_deployment_claim_allowed": False,
        },
        "privacy_and_safety": {
            "user_or_participant_data_fabrication_allowed": False,
            "raw_audio_or_locator_in_public_trace_allowed": False,
            "numeric_score_or_threshold_in_public_trace_allowed": False,
            "source_identity_or_condition_in_public_trace_allowed": False,
            "free_text_in_public_trace_allowed": False,
            "human_mode_requires_h00_and_individual_consent": True,
            "fixture_mode_requires_no_human_voice_or_user_data": True,
        },
        "approved_by_protocol_owner": True,
        "approved_by_privacy_owner": True,
        "approved_by_statistics_owner": True,
        "binding_sha256": "",
    }
    return _bind(payload)


def _authorization(
    *,
    mode: str,
    preregistration_sha256: str,
) -> dict[str, Any]:
    human: dict[str, Any] | None = None
    fixture: dict[str, Any] | None = None
    if mode == "human_authorized_demo":
        human = {
            "h00_approval_sha256": _h(30),
            "h00_official_document_attestation_sha256": _h(31),
            "e00_scope_or_amendment_sha256": _h(32),
            "individual_consent_manifest_sha256": _h(33),
            "consent_active_for_recording_diagnostic_coaching_reference_rerecord": True,
            "withdrawal_and_deletion_policy_active": True,
            "human_escalation_available": True,
            "authorization_current": True,
        }
    else:
        fixture = {
            "fixture_provenance_sha256": _h(34),
            "fixture_license_sha256": _h(35),
            "scope": "learner_attempt_input_fixture_only",
            "contains_human_voice": False,
            "contains_participant_or_user_data": False,
            "represents_a_real_learner": False,
            "human_user_data_fabricated": False,
            "synthetic_or_nonhuman_label_explicit": True,
            "licensed_d08_human_reference_outside_fixture_scope": True,
            "pedagogical_or_clinical_claim_allowed": False,
        }
    return _bind(
        {
            "schema_version": "voice_coach.e00_execution_authorization.v1",
            "authorization_id": "e00auth_" + "b" * 32,
            "status": "active_authorization",
            "execution_mode": mode,
            "preregistration_sha256": preregistration_sha256,
            "human_authorization": human,
            "fixture_authorization": fixture,
            "privacy_zone": "A1_C1_public_summary_only",
            "contains_direct_identifiers": False,
            "contains_audio_or_locators": False,
            "contains_free_text": False,
            "binding_sha256": "",
        }
    )


def _attempt(index: int) -> dict[str, Any]:
    return {
        "attempt_id_sha256": _h(100 + index),
        "audio_object_id_sha256": _h(110 + index),
        "audio_content_sha256": _h(120 + index),
        "diagnostic_handoff_sha256": _h(130 + index),
        "scorer_identity_sha256": _h(140),
        "checkpoint_sha256": _h(141),
        "calibrator_sha256": _h(142),
        "threshold_policy_sha256": _h(143),
        "model_score_embedded": False,
        "raw_audio_locator_embedded": False,
    }


def _session(expected: dict[str, str]) -> dict[str, Any]:
    return _bind(
        {
            "schema_version": "voice_coach.e00_closed_loop_session.v1",
            "session_id": "e00session_" + "c" * 32,
            "execution_mode": "nonhuman_test_fixture",
            "privacy_zone": "D1_pseudonymized_restricted_demo",
            "target_text_sha256": _h(150),
            **expected,
            "attempt_1": _attempt(1),
            "attempt_2": _attempt(2),
            "same_scorer_contract": {
                "same_model_identity": True,
                "same_checkpoint": True,
                "same_calibrator": True,
                "same_threshold_policy": True,
                "result_based_model_or_threshold_change_allowed": False,
            },
            "contains_direct_identifiers": False,
            "contains_plain_target_or_transcript": False,
            "contains_audio_bytes_or_paths": False,
            "contains_numeric_model_scores": False,
            "contains_locked_result_copy": False,
            "contains_free_text": False,
            "user_or_participant_data_fabricated": False,
            "binding_sha256": "",
        }
    )


def _trace(
    *,
    status: str = "completed_eligible_loop",
    blocked_reasons: list[str] | None = None,
    safe_next_action: str | None = None,
) -> dict[str, Any]:
    eligible = status == "completed_eligible_loop"
    blocked = status == "blocked_safe_exit"
    decision = (
        "allowed_for_separate_renderer"
        if eligible
        else "blocked"
        if blocked
        else "no_correction"
    )
    return _bind(
        {
            "schema_version": "voice_coach.e00_closed_loop_trace.v1",
            "trace_id": "e00trace_" + "d" * 32,
            "status": status,
            "execution_mode": "nonhuman_test_fixture",
            "privacy_zone": "A1_aggregate_score_free_demo_trace",
            "session_manifest_sha256": _h(200),
            "event_order": [
                "attempt_1",
                "s108_diagnostic_1",
                "e02_guard",
                "c02_top1_coaching",
                "r03_d08_reference",
                "attempt_2",
                "same_scorer_comparison",
            ],
            "attempt_1": {
                "completed": True,
                "attempt_receipt_sha256": _h(201),
            },
            "diagnostic": {
                "s108_actual_production_handoff_used": True,
                "s108_handoff_sha256": _h(202),
                "locked_result_copied_or_replayed": False,
                "diagnostic_receipt_sha256": _h(203),
            },
            "guard": {
                "decision": decision,
                "blocked_reasons": blocked_reasons or [],
                "safe_next_action": safe_next_action,
                "e02_response_sha256": _h(204),
            },
            "coaching": {
                "delivered": eligible,
                "selected_count": 1 if eligible else 0,
                "top1_catalog_advice_id_sha256": _h(205) if eligible else None,
                "catalog_only": True,
                "free_generation_used": False,
                "llm_used": False,
                "blocked_top_k_backfill_used": False,
            },
            "reference": {
                "delivered": eligible,
                "handoff_sha256": _h(206) if eligible else None,
                "licensed_r03_d08_handoff": eligible,
                "source_or_locator_exposed": False,
                "score_exposed": False,
            },
            "attempt_2": {
                "completed": eligible,
                "attempt_receipt_sha256": _h(207) if eligible else None,
            },
            "comparison": {
                "completed": eligible,
                "same_scorer_verified": eligible,
                "paired_metric_definition_sha256": _h(208) if eligible else None,
                "comparison_receipt_sha256": _h(209) if eligible else None,
                "numeric_scores_embedded": False,
                "direction_or_effect_embedded": False,
            },
            "safety": {
                "user_or_participant_data_fabricated": False,
                "model_score_or_threshold_leaked": False,
                "source_identity_or_condition_leaked": False,
                "raw_audio_or_locator_leaked": False,
                "free_text_or_generated_advice_present": False,
                "blocked_capability_override_used": False,
            },
            "claims": {
                "noninferential_demo_only": True,
                "pedagogical_effect_claim_allowed": False,
                "clinical_claim_allowed": False,
                "deployment_claim_allowed": False,
            },
            "binding_sha256": "",
        }
    )


def _d08_completion() -> dict[str, Any]:
    sentences = []
    for index in range(1, 21):
        sentences.append(
            {
                "sentence_id": f"R00-{index:02d}",
                "distinct_speaker_count": 4,
                "accepted_natural_take_count": 2,
                "accepted_clear_educational_take_count": 1,
                "accepted_take_count": 3,
                "speaker_floor_pass": True,
                "planning_target_met": True,
                "exact_take_pattern_pass": True,
                "all_qc_pass": True,
            }
        )
    return {
        "schema_version": "voice_coach.d08_completion.v1",
        "completion_id": "d08c_" + "e" * 32,
        "status": "completed",
        "config_sha256": _h(300),
        "r00_card_sha256": _h(301),
        "h00_approval_sha256": _h(302),
        "individual_consent_attestation_sha256": _h(303),
        "recording_rights_attestation_sha256": _h(304),
        "qc_preregistration_sha256": _h(305),
        "metadata_manifest_sha256": _h(306),
        "qc_report_sha256": _h(307),
        "privacy_zone": "A1_aggregate_review_required",
        "contains_row_level_data": False,
        "contains_direct_or_pseudonymous_ids": False,
        "contains_audio_or_locator_hashes": False,
        "contains_free_text": False,
        "sentence_count": 20,
        "binding_minimum_distinct_speakers_per_sentence": 4,
        "planning_target_minimum_distinct_speakers_per_sentence": 4,
        "planning_target_met_all_sentences": True,
        "same_equipment_and_space_verified": True,
        "format_and_distance_verified": True,
        "source_license_honesty_verified": True,
        "qc_adjudication_contract_verified": True,
        "sentences": sentences,
        "totals": {
            "distinct_speaker_sentence_pairs": 80,
            "accepted_natural_take_count": 40,
            "accepted_clear_educational_take_count": 20,
            "accepted_take_count": 60,
            "failed_take_count": 0,
            "decoder_error_count": 0,
            "clipping_failure_count": 0,
            "dc_offset_failure_count": 0,
            "content_mismatch_failure_count": 0,
            "noise_failure_count": 0,
            "reverb_failure_count": 0,
            "interruption_failure_count": 0,
            "human_review_failure_count": 0,
            "adjudication_failure_count": 0,
        },
        "actual_recruitment_completed": True,
        "actual_consent_completed": True,
        "actual_recording_completed": True,
        "actual_qc_completed": True,
        "completion_claim_allowed": True,
        "publication_allowed": False,
        "implementation_git_commit": "1" * 40,
    }


def test_static_config_reads_49_scoped_sources_and_no_runtime() -> None:
    config = load_config(CONFIG_PATH)
    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CONFIG_PATH,
        config=config,
        audit=audit,
    )
    assert len(static.source_snapshots) == 49
    assert audit.source_pin_read_count == 49
    assert audit.source_toctou_verification_count == 0
    assert audit.s108_path_resolution_count == 0
    assert audit.raw_audio_read_count == 0
    assert audit.model_inference_count == 0
    assert audit.output_write_count == 0


def test_static_config_rejects_visual_sidecar_as_audio_authority() -> None:
    config = deepcopy(load_config(CONFIG_PATH))
    visual_pin = next(
        pin
        for pin in config["canonical_sources"]["pins"]
        if pin["path"] == "docs/architecture/README_KO.md"
    )
    visual_pin["scope"] = "seungun_audio_canonical"

    with pytest.raises(E00ContractError, match="source pin .* scope"):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_all_public_schemas_are_strict_at_root() -> None:
    for path in SCHEMA_PATHS:
        schema = json.loads(path.read_text())
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_frozen_preregistration_separates_noninferential_demo_from_effect() -> None:
    config = _resolved_config()
    prereg = _preregistration(config)
    summary = validate_preregistration_payload(prereg, config=config)
    assert summary["execution_mode"] == "nonhuman_test_fixture"
    assert summary["demo_trace_count"] == 3

    tampered = deepcopy(prereg)
    tampered["comparison"]["exact_effect_estimand"] = "mean_improvement"
    tampered["binding_sha256"] = binding_sha256(tampered)
    with pytest.raises(E00ContractError, match="exact_effect_estimand"):
        validate_preregistration_payload(tampered, config=config)


def test_unresolved_preregistration_and_predecessor_drift_fail() -> None:
    config = _resolved_config()
    prereg = _preregistration(config)
    prereg["comparison"]["minimum_support"] = "TBD_PREREGISTRATION"
    prereg["binding_sha256"] = binding_sha256(prereg)
    with pytest.raises(E00ContractError, match="unresolved TBD"):
        validate_preregistration_payload(prereg, config=config)

    drift = _preregistration(config)
    drift["predecessor_bindings"]["c03_report_sha256"] = _h(999)
    drift["binding_sha256"] = binding_sha256(drift)
    with pytest.raises(E00ContractError, match="c03_report_sha256"):
        validate_preregistration_payload(drift, config=config)


def test_fixture_authorization_scopes_no_human_voice_to_learner_attempt() -> None:
    prereg_sha = _h(400)
    payload = _authorization(
        mode="nonhuman_test_fixture",
        preregistration_sha256=prereg_sha,
    )
    result = validate_execution_authorization_payload(
        payload,
        expected_preregistration_sha256=prereg_sha,
        expected_mode="nonhuman_test_fixture",
    )
    assert result["execution_mode"] == "nonhuman_test_fixture"
    assert (
        payload["fixture_authorization"]["scope"]
        == "learner_attempt_input_fixture_only"
    )
    assert (
        payload["fixture_authorization"][
            "licensed_d08_human_reference_outside_fixture_scope"
        ]
        is True
    )

    tampered = deepcopy(payload)
    tampered["fixture_authorization"]["contains_human_voice"] = True
    tampered["binding_sha256"] = binding_sha256(tampered)
    with pytest.raises(E00ContractError, match="contains_human_voice"):
        validate_execution_authorization_payload(
            tampered,
            expected_preregistration_sha256=prereg_sha,
            expected_mode="nonhuman_test_fixture",
        )

    scope_drift = deepcopy(payload)
    scope_drift["fixture_authorization"]["scope"] = "whole_session"
    scope_drift["binding_sha256"] = binding_sha256(scope_drift)
    with pytest.raises(E00ContractError, match="scope"):
        validate_execution_authorization_payload(
            scope_drift,
            expected_preregistration_sha256=prereg_sha,
            expected_mode="nonhuman_test_fixture",
        )


def test_human_authorization_requires_h00_and_individual_consent() -> None:
    prereg_sha = _h(401)
    payload = _authorization(
        mode="human_authorized_demo",
        preregistration_sha256=prereg_sha,
    )
    with pytest.raises(E00ContractError, match="validated H00"):
        validate_execution_authorization_payload(
            payload,
            expected_preregistration_sha256=prereg_sha,
            expected_mode="human_authorized_demo",
        )
    validate_execution_authorization_payload(
        payload,
        expected_preregistration_sha256=prereg_sha,
        expected_mode="human_authorized_demo",
        h00_approval_sha256=_h(30),
        h00_attestation_sha256=_h(31),
    )

    tampered = deepcopy(payload)
    tampered["human_authorization"][
        "consent_active_for_recording_diagnostic_coaching_reference_rerecord"
    ] = False
    tampered["binding_sha256"] = binding_sha256(tampered)
    with pytest.raises(E00ContractError, match="consent_active"):
        validate_execution_authorization_payload(
            tampered,
            expected_preregistration_sha256=prereg_sha,
            expected_mode="human_authorized_demo",
            h00_approval_sha256=_h(30),
            h00_attestation_sha256=_h(31),
        )


def test_s108_handoff_requires_actual_inference_and_rejects_locked_replay() -> None:
    payload = _bind(
        {
            "schema_version": "voice_coach.e00_s108_diagnostic_handoff.v1",
            "handoff_id": "e00s108_" + "f" * 32,
            "status": "actual_production_diagnostic_handoff",
            "attempt_id_sha256": _h(500),
            "audio_content_sha256": _h(501),
            "s108_completion_commit_sha256": _h(502),
            "model_identity_sha256": _h(502),
            "checkpoint_sha256": _h(503),
            "calibrator_sha256": _h(504),
            "threshold_policy_sha256": _h(505),
            "production_inference_receipt_sha256": _h(506),
            "diagnostic_issue_manifest_sha256": _h(507),
            "input_role": "nonhuman_test_fixture_attempt",
            "actual_production_inference_executed": True,
            "locked_test_row_audio_prediction_or_result_copied": False,
            "locked_result_replay_used": False,
            "score_or_threshold_embedded": False,
            "source_split_or_condition_embedded": False,
            "raw_audio_or_locator_embedded": False,
            "eligible_for_e02_guard": True,
            "privacy_zone": "D1_pseudonymized_restricted_production_handoff",
            "binding_sha256": "",
        }
    )
    kwargs = {
        "expected_attempt_id_sha256": _h(500),
        "expected_audio_content_sha256": _h(501),
        "expected_s108_commit_sha256": _h(502),
        "expected_model_identity_sha256": _h(502),
        "expected_checkpoint_sha256": _h(503),
        "expected_calibrator_sha256": _h(504),
        "expected_threshold_policy_sha256": _h(505),
        "execution_mode": "nonhuman_test_fixture",
    }
    validate_s108_diagnostic_handoff_payload(payload, **kwargs)

    tampered = deepcopy(payload)
    tampered["locked_result_replay_used"] = True
    tampered["binding_sha256"] = binding_sha256(tampered)
    with pytest.raises(E00ContractError, match="locked_result_replay_used"):
        validate_s108_diagnostic_handoff_payload(tampered, **kwargs)


def test_session_requires_two_distinct_attempts_with_same_scorer() -> None:
    expected = {
        "authorization_sha256": _h(600),
        "preregistration_sha256": _h(601),
        "s108_completion_commit_sha256": _h(602),
        "s108_production_handoff_sha256": _h(603),
        "e01_completion_commit_sha256": _h(604),
        "c02_bundle_commit_sha256": _h(605),
        "c03_report_sha256": _h(606),
        "e02_preregistration_sha256": _h(607),
        "r03_bank_manifest_sha256": _h(608),
        "d08_completion_sha256": _h(609),
        "reference_handoff_sha256": _h(610),
    }
    payload = _session(expected)
    validate_session_manifest_payload(
        payload,
        expected_mode="nonhuman_test_fixture",
        expected_hashes=expected,
    )

    tampered = deepcopy(payload)
    tampered["attempt_2"]["checkpoint_sha256"] = _h(999)
    tampered["binding_sha256"] = binding_sha256(tampered)
    with pytest.raises(E00ContractError, match="same scorer checkpoint"):
        validate_session_manifest_payload(
            tampered,
            expected_mode="nonhuman_test_fixture",
            expected_hashes=expected,
        )


def test_eligible_trace_enforces_guard_then_top1_reference_rerecord_comparison() -> (
    None
):
    payload = _trace()
    status = validate_trace_payload(
        payload,
        expected_mode="nonhuman_test_fixture",
        expected_session_manifest_sha256=_h(200),
        expected_s108_handoff_sha256=_h(202),
        expected_reference_handoff_sha256=_h(206),
        expected_paired_metric_definition_sha256=_h(208),
    )
    assert status == "completed_eligible_loop"

    tampered = deepcopy(payload)
    tampered["coaching"]["selected_count"] = 2
    tampered["binding_sha256"] = binding_sha256(tampered)
    with pytest.raises(E00ContractError, match="at most top-1"):
        validate_trace_payload(
            tampered,
            expected_mode="nonhuman_test_fixture",
            expected_session_manifest_sha256=_h(200),
            expected_s108_handoff_sha256=_h(202),
            expected_reference_handoff_sha256=_h(206),
            expected_paired_metric_definition_sha256=_h(208),
        )


@pytest.mark.parametrize(
    ("reason", "action"),
    [
        ("out_of_domain", "expert_review"),
        ("low_confidence", "rerecord"),
        ("unsupported", "expert_review"),
        ("abstain_all", "rerecord"),
    ],
)
def test_every_guard_block_reason_forces_safe_exit(
    reason: str,
    action: str,
) -> None:
    payload = _trace(
        status="blocked_safe_exit",
        blocked_reasons=[reason],
        safe_next_action=action,
    )
    assert (
        validate_trace_payload(
            payload,
            expected_mode="nonhuman_test_fixture",
            expected_session_manifest_sha256=_h(200),
            expected_s108_handoff_sha256=_h(202),
            expected_reference_handoff_sha256=_h(206),
            expected_paired_metric_definition_sha256=_h(208),
        )
        == "blocked_safe_exit"
    )
    tampered = deepcopy(payload)
    tampered["reference"]["delivered"] = True
    tampered["reference"]["licensed_r03_d08_handoff"] = True
    tampered["reference"]["handoff_sha256"] = _h(206)
    tampered["binding_sha256"] = binding_sha256(tampered)
    with pytest.raises(E00ContractError, match="blocked reference"):
        validate_trace_payload(
            tampered,
            expected_mode="nonhuman_test_fixture",
            expected_session_manifest_sha256=_h(200),
            expected_s108_handoff_sha256=_h(202),
            expected_reference_handoff_sha256=_h(206),
            expected_paired_metric_definition_sha256=_h(208),
        )


def test_no_correction_is_terminal_without_delivery_or_claim() -> None:
    payload = _trace(status="no_correction_safe_exit")
    assert (
        validate_trace_payload(
            payload,
            expected_mode="nonhuman_test_fixture",
            expected_session_manifest_sha256=_h(200),
            expected_s108_handoff_sha256=_h(202),
            expected_reference_handoff_sha256=_h(206),
            expected_paired_metric_definition_sha256=_h(208),
        )
        == "no_correction_safe_exit"
    )


def test_reference_handoff_requires_exact_d08_candidate_audio_rights_receipt() -> None:
    d08 = _d08_completion()
    candidate = {
        "candidate_id": "r01c_" + "1" * 32,
        "target_text_sha256": _h(700),
        "audio_object_id_sha256": _h(701),
        "audio_sha256": _h(702),
        "source_kind": "human_recording",
        "source_attestation_sha256": _h(304),
        "speaker_id_sha256": _h(703),
        "speed_bucket": "neutral",
        "pitch_bucket": "neutral",
        "r02_eligible_row_sha256": _h(704),
        "objective_vector_sha256": _h(705),
        "pareto_member": True,
        "immutable_lineage_verified": True,
    }
    bank = {"sentences": [{"selected_candidates": [candidate]}]}
    row_sha = hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()
    lineage_receipt = _bind(
        {
            "schema_version": "voice_coach.e00_d08_r03_lineage_receipt.v1",
            "receipt_id": "e00lineage_" + "2" * 32,
            "status": "exact_cross_lineage_verified",
            "r03_bank_manifest_sha256": _h(706),
            "selected_candidate_row_sha256": row_sha,
            "target_text_sha256": _h(700),
            "selected_audio_object_id_sha256": _h(701),
            "selected_audio_sha256": _h(702),
            "source_audio_sha256": _h(702),
            "d08_completion_sha256": _h(707),
            "d08_metadata_manifest_sha256": _h(306),
            "d08_recording_metadata_row_sha256": _h(708),
            "d08_recording_audio_sha256": _h(702),
            "d08_recording_rights_attestation_sha256": _h(304),
            "source_rights_attestation_sha256": _h(304),
            "d08_recording_license_grant_sha256": _h(709),
            "source_recording_license_grant_sha256": _h(709),
            "candidate_audio_matches_d08_recording": True,
            "candidate_rights_match_d08_rights": True,
            "independent_r01_human_assumed_to_be_d08": False,
            "audio_locator_embedded": False,
            "speaker_identity_embedded": False,
            "privacy_zone": "D1_pseudonymized_restricted_lineage",
            "binding_sha256": "",
        }
    )
    lineage_sha = _h(710)
    lineage = validate_d08_r03_lineage_receipt_payload(
        lineage_receipt,
        r03_bank=bank,
        r03_bank_sha256=_h(706),
        d08_completion=d08,
        d08_completion_sha256=_h(707),
    )
    assert lineage["status"] == "valid_exact_d08_r03_lineage_receipt"
    payload = _bind(
        {
            "schema_version": "voice_coach.e00_reference_handoff.v1",
            "handoff_id": "e00ref_" + "2" * 32,
            "status": "authorized_for_single_demo_handoff",
            "target_text_sha256": _h(700),
            "selected_candidate_row_sha256": row_sha,
            "r03_bank_manifest_sha256": _h(706),
            "d08_completion_sha256": _h(707),
            "d08_recording_rights_attestation_sha256": _h(304),
            "source_rights_attestation_sha256": _h(304),
            "selected_audio_object_id_sha256": _h(701),
            "selected_audio_sha256": _h(702),
            "d08_r03_lineage_receipt_sha256": lineage_sha,
            "source_kind": "human_recording",
            "licensed_for_closed_loop_demo": True,
            "r03_row_lineage_verified": True,
            "d08_rights_binding_verified": True,
            "exact_candidate_audio_rights_cross_match_verified": True,
            "audio_locator_embedded": False,
            "speaker_identity_embedded": False,
            "source_condition_exposed_to_learner": False,
            "score_payload_embedded": False,
            "privacy_zone": "D1_pseudonymized_restricted_handoff",
            "binding_sha256": "",
        }
    )
    result = validate_reference_handoff_payload(
        payload,
        r03_bank=bank,
        r03_bank_sha256=_h(706),
        d08_completion=d08,
        d08_completion_sha256=_h(707),
        lineage_receipt=lineage_receipt,
        lineage_receipt_sha256=lineage_sha,
    )
    assert result["status"] == "valid_licensed_reference_handoff"

    assumed = deepcopy(lineage_receipt)
    assumed["independent_r01_human_assumed_to_be_d08"] = True
    assumed["binding_sha256"] = binding_sha256(assumed)
    with pytest.raises(E00ContractError, match="assumption"):
        validate_d08_r03_lineage_receipt_payload(
            assumed,
            r03_bank=bank,
            r03_bank_sha256=_h(706),
            d08_completion=d08,
            d08_completion_sha256=_h(707),
        )

    audio_mismatch = deepcopy(lineage_receipt)
    audio_mismatch["d08_recording_audio_sha256"] = _h(999)
    audio_mismatch["binding_sha256"] = binding_sha256(audio_mismatch)
    with pytest.raises(E00ContractError, match="audio cross-match"):
        validate_d08_r03_lineage_receipt_payload(
            audio_mismatch,
            r03_bank=bank,
            r03_bank_sha256=_h(706),
            d08_completion=d08,
            d08_completion_sha256=_h(707),
        )

    rights_mismatch = deepcopy(lineage_receipt)
    rights_mismatch["source_recording_license_grant_sha256"] = _h(999)
    rights_mismatch["binding_sha256"] = binding_sha256(rights_mismatch)
    with pytest.raises(E00ContractError, match="rights receipt"):
        validate_d08_r03_lineage_receipt_payload(
            rights_mismatch,
            r03_bank=bank,
            r03_bank_sha256=_h(706),
            d08_completion=d08,
            d08_completion_sha256=_h(707),
        )


def test_d08_aggregate_handoff_rejects_protected_row_key() -> None:
    payload = _d08_completion()
    result = validate_d08_aggregate_completion_payload(
        payload,
        expected_config_sha256=_h(300),
    )
    assert result["status"] == "valid_d08_aggregate_completion_handoff"
    payload["speaker_id_sha256"] = _h(999)
    with pytest.raises(E00ContractError, match="leaks protected"):
        validate_d08_aggregate_completion_payload(
            payload,
            expected_config_sha256=_h(300),
        )


def test_completion_counts_exact_flow_and_forbids_effect_claim() -> None:
    payload = _bind(
        {
            "schema_version": "voice_coach.e00_closed_loop_completion.v1",
            "completion_id": "e00completion_" + "3" * 32,
            "status": "completed_noninferential_demo",
            "config_sha256": _h(800),
            "preregistration_sha256": _h(801),
            "authorization_sha256": _h(802),
            "predecessor_chain_sha256": _h(803),
            "execution_mode": "nonhuman_test_fixture",
            "privacy_zone": "A1_aggregate_only",
            "trace_count": 3,
            "eligible_completed_loop_count": 1,
            "blocked_safe_exit_count": 1,
            "no_correction_safe_exit_count": 1,
            "same_scorer_comparison_count": 1,
            "traces_sha256": _h(804),
            "safety_counts": {
                "user_data_fabrication_count": 0,
                "free_generation_count": 0,
                "blocked_override_count": 0,
                "locked_result_copy_count": 0,
                "score_or_threshold_leak_count": 0,
                "source_or_locator_leak_count": 0,
            },
            "actual_execution": {
                "attempt1_recording_count": 3,
                "s108_diagnostic_handoff_count": 4,
                "e02_guard_count": 3,
                "c02_top1_coaching_delivery_count": 1,
                "licensed_reference_delivery_count": 1,
                "attempt2_recording_count": 1,
                "same_scorer_comparison_count": 1,
            },
            "claims": {
                "noninferential_demo_only": True,
                "effect_estimated": False,
                "confidence_interval_computed": False,
                "pedagogical_effect_claimed": False,
                "clinical_claimed": False,
                "production_deployment_claimed": False,
            },
            "contains_rows_or_pseudonymous_ids": False,
            "contains_audio_or_locator_hashes": False,
            "contains_numeric_scores_thresholds_or_effects": False,
            "contains_source_identity_or_condition": False,
            "contains_free_text": False,
            "completion_claim_allowed": True,
            "publication_allowed": False,
            "implementation_git_commit": "4" * 40,
            "binding_sha256": "",
        }
    )
    result = validate_completion_payload(
        payload,
        expected_config_sha256=_h(800),
        expected_preregistration_sha256=_h(801),
        expected_authorization_sha256=_h(802),
        expected_predecessor_chain_sha256=_h(803),
        expected_execution_mode="nonhuman_test_fixture",
        expected_trace_count=3,
    )
    assert result["eligible_completed_loop_count"] == 1

    tampered = deepcopy(payload)
    tampered["claims"]["pedagogical_effect_claimed"] = True
    tampered["binding_sha256"] = binding_sha256(tampered)
    with pytest.raises(E00ContractError, match="pedagogical_effect_claimed"):
        validate_completion_payload(
            tampered,
            expected_config_sha256=_h(800),
            expected_preregistration_sha256=_h(801),
            expected_authorization_sha256=_h(802),
            expected_predecessor_chain_sha256=_h(803),
            expected_execution_mode="nonhuman_test_fixture",
            expected_trace_count=3,
        )
