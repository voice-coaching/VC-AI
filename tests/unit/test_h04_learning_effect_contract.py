from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from voice_coach.human_research.h04_learning_effect_contract import (
    AccessAudit,
    AnalystEvidence,
    FeasibilityEvidence,
    H00Evidence,
    H01Evidence,
    H02H03Evidence,
    H04ContractError,
    SapEvidence,
    binding_sha256,
    canonical_json_bytes,
    deep_validate_h02_h03,
    ensure_future_output_targets_absent,
    load_config,
    run_full_preflight,
    validate_aggregate_input_payload,
    validate_analyst_attestation_payload,
    validate_completion_payload,
    validate_feasibility_pilot_payload,
    validate_h01_lock_payload,
    validate_h02_h03_payload,
    validate_sap_payload,
    validate_static_config,
    validate_statistical_report_payload,
)


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG_PATH = WORKSPACE / "configs/experiments/h04_learning_effect_analysis_v1.yaml"
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


def _evidence() -> tuple[
    H00Evidence,
    FeasibilityEvidence,
    H01Evidence,
    SapEvidence,
    H02H03Evidence,
    AnalystEvidence,
]:
    h00 = H00Evidence(config_sha256=ZERO, approval_sha256=ONE, attestation_sha256=TWO)
    feasibility_payload = _feasibility_payload(h00)
    feasibility = FeasibilityEvidence(THREE, feasibility_payload, None)
    h01_payload = _h01_payload(h00, feasibility)
    h01 = H01Evidence(FOUR, h01_payload, None)
    sap_payload = _sap_payload(h00, h01)
    sap = SapEvidence(FIVE, sap_payload, None)
    h02_payload = _h02_payload(h00, feasibility, h01, sap)
    h02 = H02H03Evidence(SIX, h02_payload, None)
    analyst_payload = _analyst_payload(h00, h01, sap, h02)
    analyst = AnalystEvidence(SEVEN, analyst_payload, None)
    return h00, feasibility, h01, sap, h02, analyst


def _feasibility_payload(h00: H00Evidence) -> dict[str, object]:
    stage_1 = {
        "comparison_id": "c3_minus_c0",
        "arm_a": "C0",
        "arm_b": "C3",
        "arm_a_randomized": 15,
        "arm_b_randomized": 15,
        "randomized_total": 30,
        "pre_observed_participants": 30,
        "post_observed_participants": 28,
        "retention_observed_participants": 26,
        "blind_rating_count": 504,
    }
    stage_2 = {
        "comparison_id": "c5_minus_c3",
        "arm_a": "C3",
        "arm_b": "C5",
        "arm_a_randomized": 20,
        "arm_b_randomized": 20,
        "randomized_total": 40,
        "pre_observed_participants": 40,
        "post_observed_participants": 37,
        "retention_observed_participants": 34,
        "blind_rating_count": 666,
    }
    return _bind(
        {
            "schema_version": "voice_coach.h04_feasibility_pilot_completion.v1",
            "pilot_id": f"h04feaspilot_{'0' * 32}",
            "status": "completed_feasibility_pilot_before_h01_lock",
            "privacy_zone": "A1_aggregate_feasibility_pilot_receipt",
            "lineage": {
                "h00_approval_sha256": h00.approval_sha256,
                "h00_attestation_sha256": h00.attestation_sha256,
                "protocol_sha256": EIGHT,
                "feasibility_preregistration_sha256": NINE,
                "allocation_contract_sha256": A_SHA,
                "c0_manual_sha256": B_SHA,
                "c3_manual_sha256": C_SHA,
                "c5_manual_sha256": D_SHA,
            },
            "completion": {
                "actual_participants_enrolled_and_randomized": True,
                "approved_consent_and_audio_scope_confirmed": True,
                "two_week_feasibility_intervention_completed": True,
                "pre_post_retention_collection_completed": True,
                "blind_independent_ratings_completed": True,
                "minimum_independent_raters": 2,
                "raters_blinded_to_condition_timepoint_and_identity": True,
                "participant_blinding_claimed": False,
                "feasibility_only_not_confirmatory_efficacy": True,
                "observed_treatment_effect_used_for_power": False,
                "aggregate_nuisance_parameters_completed": True,
                "withdrawal_handled_under_approved_consent": True,
            },
            "counts": {
                "stage_1_c0_vs_c3": stage_1,
                "stage_2_c3_vs_c5": stage_2,
            },
            "receipts": {
                "aggregate_nuisance_parameter_report_sha256": E_SHA,
                "blind_rater_reliability_report_sha256": F_SHA,
                "randomization_receipt_sha256": ZERO,
                "delivery_fidelity_report_sha256": ONE,
                "missingness_attrition_report_sha256": TWO,
                "adverse_feedback_report_sha256": THREE,
                "data_lock_receipt_sha256": FOUR,
            },
            "privacy_safety": {
                "participant_rows_present": False,
                "participant_identifiers_present": False,
                "audio_or_transcript_present": False,
                "raw_locator_present": False,
                "free_text_present": False,
                "individual_scores_present": False,
                "observed_effect_estimates_present": False,
                "aggregate_only": True,
            },
            "binding_sha256": ZERO,
        }
    )


def _h01_payload(
    h00: H00Evidence,
    feasibility: FeasibilityEvidence,
) -> dict[str, object]:
    nuisance = feasibility.payload["receipts"][
        "aggregate_nuisance_parameter_report_sha256"
    ]
    return _bind(
        {
            "schema_version": "voice_coach.h04_h01_final_sample_size_lock.v1",
            "lock_id": f"h01lock_{'1' * 32}",
            "status": "completed_and_locked_before_confirmatory_recruitment",
            "privacy_zone": "A1_aggregate_sample_size_lock",
            "h00_approval_sha256": h00.approval_sha256,
            "h00_attestation_sha256": h00.attestation_sha256,
            "source_planning_config_sha256": A_SHA,
            "source_planning_artifact_sha256": B_SHA,
            "feasibility_pilot_completion_sha256": feasibility.sha256,
            "pilot_nuisance_report_sha256": nuisance,
            "full_simulation_report_sha256": C_SHA,
            "public_preregistration_sha256": D_SHA,
            "minimum_relevant_difference_receipt_sha256": E_SHA,
            "statistician_approval_receipt_sha256": F_SHA,
            "principal_investigator_approval_receipt_sha256": ZERO,
            "locked_at_utc": "2025-01-01T00:00:00Z",
            "locked_before_confirmatory_recruitment": True,
            "actual_empirical_pilot_used": True,
            "pilot_observed_treatment_effect_used": False,
            "full_mixed_or_cluster_simulation_completed": True,
            "final_sample_size_auto_locked": False,
            "final_sample_size_manually_locked": True,
            "comparisons": {
                "stage_1_c0_vs_c3": {
                    "comparison_id": "c3_minus_c0",
                    "arms": ["C0", "C3"],
                    "allocation_ratio": "1:1",
                    "final_enrollment_per_arm": 30,
                    "total_final_enrollment": 60,
                },
                "stage_2_c3_vs_c5": {
                    "comparison_id": "c5_minus_c3",
                    "arms": ["C3", "C5"],
                    "allocation_ratio": "1:1",
                    "final_enrollment_per_arm": 32,
                    "total_final_enrollment": 64,
                },
            },
            "binding_sha256": ZERO,
        }
    )


def _sap_payload(h00: H00Evidence, h01: H01Evidence) -> dict[str, object]:
    template = json.loads(
        (
            WORKSPACE
            / "configs/human_research/h04_statistical_analysis_plan_v1.template.json"
        ).read_text(encoding="utf-8")
    )
    template.update(
        {
            "sap_id": f"h04sap_{'2' * 32}",
            "status": "approved_and_frozen",
            "registered_at_utc": "2025-01-02T00:00:00Z",
            "registered_by_role": "independent_statistician",
            "frozen_before_h02_h03_data_lock_condition_unmask_or_analysis": True,
        }
    )
    template["lineage"] = {
        "h00_approval_sha256": h00.approval_sha256,
        "h00_attestation_sha256": h00.attestation_sha256,
        "h01_final_sample_size_lock_sha256": h01.sha256,
        "protocol_sha256": EIGHT,
    }
    template["primary_model"].update(
        {
            "software_and_version_id": "r_4_3_0_lme4_1_1_35",
            "software_environment_lock_sha256": NINE,
            "model_family_id": "linear_mixed_likelihood_v1",
            "ordinal_or_linear_choice_id": "linear_primary_ordinal_sensitivity_v1",
            "outcome_support_id": "bounded_1_to_5_v1",
            "optimizer_id": "bobyqa_v1",
            "convergence_rule_id": "gradient_hessian_v1",
            "ci_method_id": "profile_likelihood_v1",
            "bootstrap_or_resampling_support_id": "cluster_bootstrap_v1",
            "fallback_model_policy_id": "no_unregistered_fallback_v1",
        }
    )
    template["missing_data"].update(
        {
            "mi_imputation_count": 50,
            "mi_model_id": "multilevel_mar_mi_v1",
            "delta_grid": [-0.5, -0.25, 0.0, 0.25, 0.5],
            "pattern_mixture_model_id": "delta_pattern_mixture_v1",
            "complete_case_definition_id": "all_three_timepoints_v1",
        }
    )
    template["per_protocol_sensitivity"]["exact_exposure_denominator_and_window_id"] = (
        "assigned_sessions_two_week_window_v1"
    )
    template["approval"] = {
        "decision": "approved",
        "statistician_approval_receipt_sha256": A_SHA,
        "principal_investigator_approval_receipt_sha256": B_SHA,
        "approved_and_frozen": True,
    }
    return _bind(template)


def _h02_payload(
    h00: H00Evidence,
    feasibility: FeasibilityEvidence,
    h01: H01Evidence,
    sap: SapEvidence,
) -> dict[str, object]:
    def stage(arm_a: int, arm_b: int) -> dict[str, int]:
        return {
            "arm_a": arm_a,
            "arm_b": arm_b,
            "randomized_total": arm_a + arm_b,
            "pre_observed_participants": arm_a + arm_b,
            "post_observed_participants": arm_a + arm_b - 2,
            "retention_observed_participants": arm_a + arm_b - 4,
            "blind_rating_count": (arm_a + arm_b - 4) * 18,
        }

    return _bind(
        {
            "schema_version": "voice_coach.h04_h02_h03_blind_bundle_completion.v1",
            "bundle_id": f"h0203bundle_{'3' * 32}",
            "status": "completed_locked_blind_pre_post_retention",
            "privacy_zone": "A1_aggregate_completion_receipt",
            "lineage": {
                "h00_approval_sha256": h00.approval_sha256,
                "h00_attestation_sha256": h00.attestation_sha256,
                "feasibility_pilot_completion_sha256": feasibility.sha256,
                "h01_final_sample_size_lock_sha256": h01.sha256,
                "frozen_sap_sha256": sap.sha256,
                "protocol_sha256": EIGHT,
                "allocation_contract_sha256": NINE,
                "c0_manual_sha256": A_SHA,
                "c3_manual_sha256": B_SHA,
                "c5_manual_sha256": C_SHA,
            },
            "completion": {
                "actual_participants_enrolled_and_randomized": True,
                "approved_consent_and_audio_scope_confirmed": True,
                "h02_two_week_intervention_completed": True,
                "h03_pre_post_retention_completed": True,
                "blind_independent_ratings_completed": True,
                "minimum_independent_raters": 2,
                "raters_blinded_to_condition_timepoint_and_identity": True,
                "participant_blinding_claimed": False,
                "analyst_arm_labels_masked_until_data_and_sap_lock": True,
                "data_locked_after_sap_freeze": True,
                "withdrawal_handled_under_approved_consent": True,
            },
            "counts": {
                "stage_1_c0_vs_c3": stage(30, 30),
                "stage_2_c3_vs_c5": stage(32, 32),
            },
            "receipts": {
                "restricted_d1_analysis_dataset_sha256": D_SHA,
                "blind_rating_dataset_sha256": E_SHA,
                "randomization_receipt_sha256": F_SHA,
                "delivery_fidelity_report_sha256": ZERO,
                "missingness_report_sha256": ONE,
                "withdrawal_report_sha256": TWO,
                "adverse_feedback_report_sha256": THREE,
                "ceiling_floor_report_sha256": FOUR,
                "data_lock_receipt_sha256": FIVE,
            },
            "privacy_safety": {
                "participant_rows_present": False,
                "participant_identifiers_present": False,
                "audio_or_transcript_present": False,
                "raw_locator_present": False,
                "free_text_present": False,
                "individual_scores_present": False,
                "aggregate_only": True,
            },
            "binding_sha256": ZERO,
        }
    )


def _analyst_payload(
    h00: H00Evidence,
    h01: H01Evidence,
    sap: SapEvidence,
    h02: H02H03Evidence,
) -> dict[str, object]:
    template = json.loads(
        (
            WORKSPACE
            / "configs/human_research/h04_analyst_attestation_v1.template.json"
        ).read_text(encoding="utf-8")
    )
    template.update(
        {
            "attestation_id": f"h04analyst_{'4' * 32}",
            "status": "attested_for_locked_h04_analysis",
            "analyst_identity_sha256": EIGHT,
            "analyst_role_id": "independent_statistical_analyst",
            "attested_at_utc": "2025-01-03T00:00:00Z",
            "signed_attestation_receipt_sha256": NINE,
        }
    )
    template["lineage"] = {
        "h00_approval_sha256": h00.approval_sha256,
        "h00_attestation_sha256": h00.attestation_sha256,
        "h01_final_sample_size_lock_sha256": h01.sha256,
        "h02_h03_completion_sha256": h02.sha256,
        "frozen_sap_sha256": sap.sha256,
    }
    for field in template["blinding_and_order"]:
        template["blinding_and_order"][field] = True
    template["reproducibility"] = {
        "analysis_code_sha256": A_SHA,
        "software_environment_lock_sha256": NINE,
        "execution_receipt_sha256": B_SHA,
        "model_diagnostics_sha256": C_SHA,
    }
    return _bind(template)


def _aggregate_input(
    h00: H00Evidence,
    h01: H01Evidence,
    sap: SapEvidence,
    h02: H02H03Evidence,
    analyst: AnalystEvidence,
) -> dict[str, object]:
    source_counts = h02.payload["counts"]
    counts: dict[str, object] = {}
    for stage, values in source_counts.items():
        counts[stage] = {
            key: value for key, value in values.items() if key not in {"arm_a", "arm_b"}
        }
    receipts = dict(h02.payload["receipts"])
    receipts.update(
        {
            "analysis_code_sha256": A_SHA,
            "software_environment_lock_sha256": NINE,
        }
    )
    return _bind(
        {
            "schema_version": "voice_coach.h04_learning_effect_aggregate_input.v1",
            "input_id": f"h04input_{'5' * 32}",
            "status": "locked_aggregate_manifest_for_authorized_analysis",
            "privacy_zone": "A1_aggregate_manifest_no_participant_rows",
            "lineage": {
                "h00_approval_sha256": h00.approval_sha256,
                "h00_attestation_sha256": h00.attestation_sha256,
                "h01_final_sample_size_lock_sha256": h01.sha256,
                "h02_h03_completion_sha256": h02.sha256,
                "frozen_sap_sha256": sap.sha256,
                "analyst_attestation_sha256": analyst.sha256,
            },
            "design_contract": {
                "comparisons": ["c3_minus_c0", "c5_minus_c3"],
                "timepoints": [
                    "pre",
                    "post_at_2_weeks",
                    "retention_4_weeks_after_post",
                ],
                "primary_outcome": "blind_expert_comprehensibility_1_to_5",
                "primary_population": "intention_to_treat_as_randomized",
                "fixed_effect_formula": "score~condition*time+l1+baseline",
                "random_intercepts": ["participant", "sentence", "rater"],
            },
            "counts": counts,
            "receipt_hashes": receipts,
            "analysis_authorization": {
                "h00_scope_current": True,
                "h01_final_sample_size_locked": True,
                "h02_h03_bundle_completed": True,
                "sap_frozen_before_unmask_and_analysis": True,
                "analyst_attested": True,
                "restricted_d1_row_access_allowed_by_this_static_package": False,
                "statistics_execution_allowed_by_this_static_package": False,
            },
            "privacy_safety": {
                "participant_rows_present": False,
                "participant_identifiers_present": False,
                "individual_ratings_present": False,
                "audio_or_transcript_present": False,
                "raw_locator_present": False,
                "free_text_present": False,
                "effect_estimates_present": False,
                "aggregate_only": True,
            },
            "binding_sha256": ZERO,
        }
    )


def _hypothesis(raw: float, adjusted: float) -> dict[str, object]:
    return {
        "two_sided": True,
        "unadjusted_p_value": raw,
        "holm_adjusted_p_value": adjusted,
        "reject_at_familywise_0_05": adjusted <= 0.05,
    }


def _interval(estimate: float = 0.2) -> dict[str, float]:
    return {
        "estimate": estimate,
        "ci_lower": estimate - 0.1,
        "ci_upper": estimate + 0.1,
    }


def _report(
    aggregate_input: dict[str, object],
    h00: H00Evidence,
    h01: H01Evidence,
    sap: SapEvidence,
    h02: H02H03Evidence,
    analyst: AnalystEvidence,
) -> dict[str, object]:
    primary_meta = (
        (
            "c3_minus_c0_pre_to_post",
            "primary_post",
            "c3_minus_c0",
            "pre_to_post",
            0.01,
            0.02,
        ),
        (
            "c5_minus_c3_pre_to_post",
            "primary_post",
            "c5_minus_c3",
            "pre_to_post",
            0.04,
            0.04,
        ),
        (
            "c3_minus_c0_pre_to_retention",
            "key_retention",
            "c3_minus_c0",
            "pre_to_retention",
            0.02,
            0.04,
        ),
        (
            "c5_minus_c3_pre_to_retention",
            "key_retention",
            "c5_minus_c3",
            "pre_to_retention",
            0.03,
            0.04,
        ),
    )
    primary = [
        {
            "contrast_id": contrast,
            "family": family,
            "comparison": comparison,
            "time_comparison": time,
            "outcome": "blind_expert_comprehensibility_1_to_5",
            "raw_1_to_5_difference": _interval(),
            "standardized_effect": _interval(0.3),
            "hypothesis": _hypothesis(raw, adjusted),
        }
        for contrast, family, comparison, time, raw, adjusted in primary_meta
    ]
    outcome_time = {
        "phone_accuracy_post": ("blind_expert_phone_accuracy", "post"),
        "phone_accuracy_retention": ("blind_expert_phone_accuracy", "retention"),
        "fluency_post": ("blind_expert_fluency", "post"),
        "fluency_retention": ("blind_expert_fluency", "retention"),
    }
    secondary = []
    for test_id, (outcome, timepoint) in outcome_time.items():
        for comparison in ("c3_minus_c0", "c5_minus_c3"):
            secondary.append(
                {
                    "test_id": test_id,
                    "family": "secondary",
                    "comparison": comparison,
                    "outcome": outcome,
                    "timepoint": timepoint,
                    "effect": _interval(),
                    "hypothesis": _hypothesis(0.01, 0.08),
                }
            )
    contrasts = [row[0] for row in primary_meta]
    sensitivity = [
        {
            "method": method,
            "parameters_sha256": EIGHT,
            "analysis_population": (
                "per_protocol_sensitivity_only"
                if method == "per_protocol_at_least_80_percent_exposure"
                else "itt_sensitivity"
            ),
            "results": [
                {
                    "contrast_id": contrast,
                    "estimate": 0.2,
                    "ci_lower": 0.1,
                    "ci_upper": 0.3,
                }
                for contrast in contrasts
            ],
        }
        for method in (
            "mar_multilevel_multiple_imputation",
            "mnar_delta_adjusted_pattern_mixture",
            "complete_case",
            "per_protocol_at_least_80_percent_exposure",
        )
    ]
    arm_denominators = {
        ("c3_minus_c0", "C0"): 30,
        ("c3_minus_c0", "C3"): 30,
        ("c5_minus_c3", "C3"): 32,
        ("c5_minus_c3", "C5"): 32,
    }
    count_cells = [
        {
            "comparison": comparison,
            "condition": condition,
            "count": 0,
            "denominator": denominator,
            "fraction": 0.0,
        }
        for (comparison, condition), denominator in arm_denominators.items()
    ]
    missing_cells = [
        {
            "comparison": comparison,
            "condition": condition,
            "timepoint": timepoint,
            "denominator": denominator,
            "missing_count": 0,
            "missing_fraction": 0.0,
            "coded_reason_summary_sha256": D_SHA,
        }
        for (comparison, condition), denominator in arm_denominators.items()
        for timepoint in ("pre", "post", "retention")
    ]
    sap_model = sap.payload["primary_model"]
    return _bind(
        {
            "schema_version": "voice_coach.h04_learning_effect_statistical_report.v1",
            "report_id": f"h04report_{'6' * 32}",
            "status": "completed_aggregate_statistical_analysis",
            "privacy_zone": "A1_aggregate_statistical_report",
            "lineage": {
                "aggregate_input_sha256": __import__("hashlib")
                .sha256(canonical_json_bytes(aggregate_input))
                .hexdigest(),
                "h00_approval_sha256": h00.approval_sha256,
                "h00_attestation_sha256": h00.attestation_sha256,
                "h01_final_sample_size_lock_sha256": h01.sha256,
                "h02_h03_completion_sha256": h02.sha256,
                "frozen_sap_sha256": sap.sha256,
                "analyst_attestation_sha256": analyst.sha256,
                "analysis_code_sha256": A_SHA,
                "software_environment_lock_sha256": NINE,
            },
            "analysis_contract": {
                "primary_population": "intention_to_treat_as_randomized",
                "primary_estimator": "likelihood_based_mixed_model_all_observed_itt",
                "fixed_effect_formula": "score~condition*time+l1+baseline",
                "random_intercepts": ["participant", "sentence", "rater"],
                "model_family_id": sap_model["model_family_id"],
                "ordinal_or_linear_choice_id": sap_model["ordinal_or_linear_choice_id"],
                "outcome_support_id": sap_model["outcome_support_id"],
                "software_id": sap_model["software_and_version_id"],
                "optimizer_id": sap_model["optimizer_id"],
                "convergence_rule_id": sap_model["convergence_rule_id"],
                "ci_method_id": sap_model["ci_method_id"],
                "multiplicity_method": "holm_within_each_registered_family",
                "alpha_two_sided": 0.05,
            },
            "primary_and_retention_results": primary,
            "secondary_family_results": secondary,
            "sensitivity_analyses": sensitivity,
            "model_diagnostics": {
                "primary_model_converged": True,
                "optimizer_converged": True,
                "singular_or_boundary_fit": False,
                "support_violations": 0,
                "fallback_model_used": False,
                "convergence_diagnostics_sha256": B_SHA,
                "bootstrap_or_ci_diagnostics_sha256": C_SHA,
            },
            "missingness_and_safety": {
                "missingness_by_arm_and_time": missing_cells,
                "withdrawal_by_arm": deepcopy(count_cells),
                "adverse_feedback_by_arm": deepcopy(count_cells),
                "ceiling_by_arm": deepcopy(count_cells),
                "floor_by_arm": deepcopy(count_cells),
            },
            "reporting_assurance": {
                "actual_statistics_computed": True,
                "actual_effect_estimates_reported": True,
                "actual_report_completed": True,
                "itt_primary": True,
                "likelihood_mixed_model_primary": True,
                "holm_applied_within_primary_retention_secondary_families": True,
                "mar_mi_reported": True,
                "mnar_pattern_mixture_reported": True,
                "complete_case_reported": True,
                "per_protocol_is_sensitivity_only": True,
                "last_observation_carried_forward_used": False,
                "post_randomization_exclusion_used": False,
                "participant_rows_present": False,
                "individual_scores_present": False,
                "audio_or_transcript_present": False,
                "raw_locator_present": False,
                "free_text_present": False,
                "aggregate_only": True,
                "publication_allowed": False,
            },
            "binding_sha256": ZERO,
        }
    )


def test_static_config_reads_exactly_19_pins_and_no_runtime() -> None:
    config = load_config(CONFIG_PATH)
    audit = AccessAudit()
    result = validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CONFIG_PATH,
        config=config,
        audit=audit,
    )
    assert len(result.source_snapshots) == 19
    assert audit.source_pin_read_count == 19
    assert audit.source_toctou_verification_count == 0
    runtime = audit.as_dict()
    runtime.pop("source_pin_read_count")
    runtime.pop("source_toctou_verification_count")
    assert set(runtime.values()) == {0}


def test_full_preflight_stops_at_h00_before_any_runtime_path_resolution() -> None:
    config = load_config(CONFIG_PATH)
    with pytest.raises(H04ContractError, match="TBD_H00_APPROVAL") as raised:
        run_full_preflight(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )
    assert raised.value.stage == "h00_authorization"
    audit = raised.value.audit
    assert audit is not None
    assert audit.source_pin_read_count == 19
    assert audit.source_toctou_verification_count == 19
    protected = audit.as_dict()
    protected.pop("source_pin_read_count")
    protected.pop("source_toctou_verification_count")
    assert set(protected.values()) == {0}


def test_static_config_rejects_duplicate_missingness_method() -> None:
    config = deepcopy(load_config(CONFIG_PATH))
    config["analysis_contract"]["missingness_sensitivity"].append(
        "mar_multilevel_multiple_imputation"
    )
    with pytest.raises(H04ContractError, match="missing sensitivity"):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_feasibility_receipt_accepts_only_15_to_20_per_arm_and_no_effect() -> None:
    h00, feasibility, *_ = _evidence()
    validate_feasibility_pilot_payload(feasibility.payload, h00=h00)

    bad_n = deepcopy(feasibility.payload)
    bad_n["counts"]["stage_1_c0_vs_c3"]["arm_a_randomized"] = 21
    bad_n["counts"]["stage_1_c0_vs_c3"]["randomized_total"] = 36
    _bind(bad_n)
    with pytest.raises(H04ContractError, match="15-20"):
        validate_feasibility_pilot_payload(bad_n, h00=h00)

    bad_effect = deepcopy(feasibility.payload)
    bad_effect["completion"]["observed_treatment_effect_used_for_power"] = True
    _bind(bad_effect)
    with pytest.raises(H04ContractError, match="observed treatment effect"):
        validate_feasibility_pilot_payload(bad_effect, h00=h00)


def test_h01_lock_cross_binds_distinct_pilot_and_nuisance_receipt() -> None:
    h00, feasibility, h01, *_ = _evidence()
    validate_h01_lock_payload(
        h01.payload,
        h00=h00,
        feasibility=feasibility,
        source_config_sha256=A_SHA,
        source_artifact_sha256=B_SHA,
    )
    for field in (
        "feasibility_pilot_completion_sha256",
        "pilot_nuisance_report_sha256",
    ):
        bad = deepcopy(h01.payload)
        bad[field] = NINE
        _bind(bad)
        with pytest.raises(H04ContractError, match="H01"):
            validate_h01_lock_payload(
                bad,
                h00=h00,
                feasibility=feasibility,
                source_config_sha256=A_SHA,
                source_artifact_sha256=B_SHA,
            )


def test_sap_is_frozen_before_data_lock_and_forbids_locf_and_duplicate_mar() -> None:
    h00, _, h01, sap, *_ = _evidence()
    validate_sap_payload(sap.payload, h00=h00, h01=h01)

    bad_locf = deepcopy(sap.payload)
    bad_locf["missing_data"]["last_observation_carried_forward_allowed"] = True
    _bind(bad_locf)
    with pytest.raises(H04ContractError, match="LOCF"):
        validate_sap_payload(bad_locf, h00=h00, h01=h01)

    duplicate_mar = deepcopy(sap.payload)
    duplicate_mar["missing_data"]["sensitivity_methods"].append(
        "mar_multilevel_multiple_imputation"
    )
    _bind(duplicate_mar)
    with pytest.raises(H04ContractError, match="SAP sensitivity"):
        validate_sap_payload(duplicate_mar, h00=h00, h01=h01)


def test_confirmatory_bundle_cross_binds_pilot_and_h01_locked_sample_size() -> None:
    h00, feasibility, h01, sap, h02, _ = _evidence()
    validate_h02_h03_payload(
        h02.payload,
        h00=h00,
        feasibility=feasibility,
        h01=h01,
        sap=sap,
    )

    wrong_pilot = deepcopy(h02.payload)
    wrong_pilot["lineage"]["feasibility_pilot_completion_sha256"] = NINE
    _bind(wrong_pilot)
    with pytest.raises(H04ContractError, match="feasibility"):
        validate_h02_h03_payload(
            wrong_pilot,
            h00=h00,
            feasibility=feasibility,
            h01=h01,
            sap=sap,
        )

    wrong_n = deepcopy(h02.payload)
    wrong_n["counts"]["stage_1_c0_vs_c3"]["arm_a"] = 29
    wrong_n["counts"]["stage_1_c0_vs_c3"]["randomized_total"] = 59
    _bind(wrong_n)
    with pytest.raises(H04ContractError, match="H01-locked"):
        validate_h02_h03_payload(
            wrong_n,
            h00=h00,
            feasibility=feasibility,
            h01=h01,
            sap=sap,
        )


def test_deep_confirmatory_gate_rejects_reused_feasibility_receipt_before_path() -> (
    None
):
    h00, feasibility, h01, sap, *_ = _evidence()
    audit = AccessAudit()
    with pytest.raises(H04ContractError, match="cannot be reused"):
        deep_validate_h02_h03(
            workspace_root=WORKSPACE,
            config={
                "predecessor_h02_h03": {
                    "completion_sha256": feasibility.sha256,
                }
            },
            h00=h00,
            feasibility=feasibility,
            h01=h01,
            sap=sap,
            audit=audit,
        )
    assert audit.h02_h03_completion_path_resolution_count == 0
    assert audit.h02_h03_completion_read_attempt_count == 0


def test_analyst_attestation_enforces_blinding_itt_and_forbidden_methods() -> None:
    h00, _, h01, sap, h02, analyst = _evidence()
    validate_analyst_attestation_payload(
        analyst.payload,
        h00=h00,
        h01=h01,
        sap=sap,
        h02_h03=h02,
    )
    bad = deepcopy(analyst.payload)
    bad["analysis_commitments"]["last_observation_carried_forward_used"] = True
    _bind(bad)
    with pytest.raises(H04ContractError, match="last_observation"):
        validate_analyst_attestation_payload(
            bad,
            h00=h00,
            h01=h01,
            sap=sap,
            h02_h03=h02,
        )


def test_aggregate_input_is_score_and_effect_free_and_exactly_bound() -> None:
    h00, _, h01, sap, h02, analyst = _evidence()
    aggregate_input = _aggregate_input(h00, h01, sap, h02, analyst)
    validate_aggregate_input_payload(
        aggregate_input,
        h00=h00,
        h01=h01,
        sap=sap,
        h02_h03=h02,
        analyst=analyst,
    )
    bad = deepcopy(aggregate_input)
    bad["privacy_safety"]["participant_rows_present"] = True
    _bind(bad)
    with pytest.raises(H04ContractError, match="participant"):
        validate_aggregate_input_payload(
            bad,
            h00=h00,
            h01=h01,
            sap=sap,
            h02_h03=h02,
            analyst=analyst,
        )


def test_statistical_report_enforces_all_contrasts_effects_sensitivities_and_holm() -> (
    None
):
    h00, _, h01, sap, h02, analyst = _evidence()
    aggregate_input = _aggregate_input(h00, h01, sap, h02, analyst)
    report = _report(aggregate_input, h00, h01, sap, h02, analyst)
    validate_statistical_report_payload(
        report,
        aggregate_input=aggregate_input,
        h00=h00,
        h01=h01,
        sap=sap,
        h02_h03=h02,
        analyst=analyst,
    )

    bad_holm = deepcopy(report)
    bad_holm["primary_and_retention_results"][0]["hypothesis"][
        "holm_adjusted_p_value"
    ] = 0.03
    _bind(bad_holm)
    with pytest.raises(H04ContractError, match="Holm adjustment mismatch"):
        validate_statistical_report_payload(
            bad_holm,
            aggregate_input=aggregate_input,
            h00=h00,
            h01=h01,
            sap=sap,
            h02_h03=h02,
            analyst=analyst,
        )

    bad_pp = deepcopy(report)
    bad_pp["sensitivity_analyses"][3]["analysis_population"] = "primary"
    _bind(bad_pp)
    with pytest.raises(H04ContractError, match="population"):
        validate_statistical_report_payload(
            bad_pp,
            aggregate_input=aggregate_input,
            h00=h00,
            h01=h01,
            sap=sap,
            h02_h03=h02,
            analyst=analyst,
        )


def test_report_rejects_locf_postrandomization_exclusion_and_fraction_tamper() -> None:
    h00, _, h01, sap, h02, analyst = _evidence()
    aggregate_input = _aggregate_input(h00, h01, sap, h02, analyst)
    report = _report(aggregate_input, h00, h01, sap, h02, analyst)
    for field in (
        "last_observation_carried_forward_used",
        "post_randomization_exclusion_used",
    ):
        bad = deepcopy(report)
        bad["reporting_assurance"][field] = True
        _bind(bad)
        with pytest.raises(H04ContractError, match=field):
            validate_statistical_report_payload(
                bad,
                aggregate_input=aggregate_input,
                h00=h00,
                h01=h01,
                sap=sap,
                h02_h03=h02,
                analyst=analyst,
            )

    bad_fraction = deepcopy(report)
    bad_fraction["missingness_and_safety"]["missingness_by_arm_and_time"][0][
        "missing_fraction"
    ] = 0.25
    _bind(bad_fraction)
    with pytest.raises(H04ContractError, match="fraction arithmetic"):
        validate_statistical_report_payload(
            bad_fraction,
            aggregate_input=aggregate_input,
            h00=h00,
            h01=h01,
            sap=sap,
            h02_h03=h02,
            analyst=analyst,
        )


def test_completion_receipt_binds_exact_validated_aggregate_artifacts() -> None:
    h00, _, h01, sap, h02, analyst = _evidence()
    aggregate_input = _aggregate_input(h00, h01, sap, h02, analyst)
    report = _report(aggregate_input, h00, h01, sap, h02, analyst)
    aggregate_bytes = canonical_json_bytes(aggregate_input)
    report_bytes = canonical_json_bytes(report)
    import hashlib

    completion = _bind(
        {
            "schema_version": "voice_coach.h04_learning_effect_completion.v1",
            "commit_id": f"h04commit_{'7' * 32}",
            "status": "committed",
            "privacy_zone": "A1_aggregate_completion_receipt",
            "lineage": {
                "h00_approval_sha256": h00.approval_sha256,
                "h00_attestation_sha256": h00.attestation_sha256,
                "h01_final_sample_size_lock_sha256": h01.sha256,
                "h02_h03_completion_sha256": h02.sha256,
                "frozen_sap_sha256": sap.sha256,
                "analyst_attestation_sha256": analyst.sha256,
            },
            "artifacts": {
                "aggregate_input": {
                    "sha256": hashlib.sha256(aggregate_bytes).hexdigest(),
                    "size_bytes": len(aggregate_bytes),
                },
                "statistical_report": {
                    "sha256": hashlib.sha256(report_bytes).hexdigest(),
                    "size_bytes": len(report_bytes),
                },
            },
            "assurance": {
                "actual_h04_analysis_completed": True,
                "strict_report_validation_completed": True,
                "itt_primary": True,
                "likelihood_mixed_model_primary": True,
                "all_registered_contrasts_reported": True,
                "all_registered_sensitivities_reported": True,
                "familywise_holm_reported": True,
                "last_observation_carried_forward_used": False,
                "post_randomization_exclusion_used": False,
                "participant_or_rating_rows_present": False,
                "audio_or_transcript_present": False,
                "raw_locator_present": False,
                "free_text_present": False,
                "model_gpu_network_used_by_static_package": False,
                "aggregate_only": True,
                "publication_allowed": False,
            },
            "binding_sha256": ZERO,
        }
    )
    validate_completion_payload(
        completion,
        h00=h00,
        h01=h01,
        sap=sap,
        h02_h03=h02,
        analyst=analyst,
        aggregate_input_sha256=hashlib.sha256(aggregate_bytes).hexdigest(),
        aggregate_input_size=len(aggregate_bytes),
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
        report_size=len(report_bytes),
    )
    bad = deepcopy(completion)
    bad["artifacts"]["statistical_report"]["size_bytes"] += 1
    _bind(bad)
    with pytest.raises(H04ContractError, match="report size"):
        validate_completion_payload(
            bad,
            h00=h00,
            h01=h01,
            sap=sap,
            h02_h03=h02,
            analyst=analyst,
            aggregate_input_sha256=hashlib.sha256(aggregate_bytes).hexdigest(),
            aggregate_input_size=len(aggregate_bytes),
            report_sha256=hashlib.sha256(report_bytes).hexdigest(),
            report_size=len(report_bytes),
        )


def test_future_output_guard_rejects_existing_traversal_and_symlink(
    tmp_path: Path,
) -> None:
    outputs = {
        "aggregate_input_path": "future/input.json",
        "statistical_report_path": "future/report.json",
        "completion_path": "future/commit.json",
        "registry_path": "future/registry.json",
        "failure_report_path": "future/failure.json",
        "no_overwrite": True,
        "atomic_publish_required": True,
        "write_enabled_in_this_package": False,
    }
    ensure_future_output_targets_absent(workspace_root=tmp_path, outputs=outputs)

    existing = deepcopy(outputs)
    (tmp_path / "future").mkdir()
    (tmp_path / "future/input.json").write_text("occupied", encoding="utf-8")
    with pytest.raises(H04ContractError, match="already exists"):
        ensure_future_output_targets_absent(workspace_root=tmp_path, outputs=existing)

    traversal = deepcopy(outputs)
    traversal["aggregate_input_path"] = "../escape.json"
    with pytest.raises(H04ContractError, match="workspace-relative path"):
        ensure_future_output_targets_absent(workspace_root=tmp_path, outputs=traversal)

    symlink_root = tmp_path / "symlink_case"
    symlink_root.mkdir()
    (symlink_root / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    symlinked = deepcopy(outputs)
    symlinked["aggregate_input_path"] = "link/input.json"
    with pytest.raises(H04ContractError, match="symlink"):
        ensure_future_output_targets_absent(
            workspace_root=symlink_root,
            outputs=symlinked,
        )
