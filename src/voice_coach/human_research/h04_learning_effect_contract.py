"""Fail-closed H04 learning-effect statistical-analysis readiness contract.

This package validates only public/static or aggregate predecessor receipts. It
does not read participant/rating rows, audio, transcripts, or restricted D1
analysis data; it does not run a statistical model, estimate an effect, create a
report, write an artifact, or claim H04 completion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
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
TBD_H00_APPROVAL = "TBD_H00_APPROVAL"
TBD_H00_ATTESTATION = "TBD_H00_ATTESTATION"
TBD_FEASIBILITY = "TBD_H02_H03_FEASIBILITY_PILOT"
TBD_H01_LOCK = "TBD_H01_SAMPLE_SIZE_LOCK"
TBD_H02_H03 = "TBD_H02_H03_COMPLETION"
TBD_SAP = "TBD_H04_SAP"
TBD_ANALYST = "TBD_H04_ANALYST_ATTESTATION"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
LOWER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,127}$")
UTC_RE = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})Z$"
)

SOURCE_PATHS = (
    "configs/human_research/h04_feasibility_pilot_completion_v1.schema.json",
    "configs/human_research/h04_h01_final_sample_size_lock_v1.schema.json",
    "configs/human_research/h04_h02_h03_blind_bundle_completion_v1.schema.json",
    "configs/human_research/h04_learning_effect_aggregate_input_v1.schema.json",
    "configs/human_research/h04_learning_effect_statistical_report_v1.schema.json",
    "configs/human_research/h04_learning_effect_completion_v1.schema.json",
    "configs/human_research/h04_statistical_analysis_plan_v1.template.json",
    "configs/human_research/h04_analyst_attestation_v1.template.json",
    "configs/experiments/h00_human_research_approval_v1.yaml",
    "src/voice_coach/human_research/h00_approval_contract.py",
    "configs/experiments/learning_effect_power_v1.yaml",
    "artifacts/planning/learning_effect_power_sensitivity_v1.json",
    "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md",
    "docs/paper/CLAIM_GAP_MAP_KO.md",
    "docs/paper/METHODS_ETHICS_REPRODUCIBILITY_GAP_KO.md",
)

EXPECTED_EXECUTION_ORDER = (
    "validate_h04_static_config_sources_schemas_and_fail_closed_templates",
    "require_exact_h00_approval_and_attestation_pins_before_path_resolution",
    "deep_validate_actual_h00_official_authorization",
    "require_and_validate_completed_h00_authorized_feasibility_pilot_aggregate",
    "require_and_validate_actual_h01_pilot_informed_final_sample_size_lock",
    "require_and_validate_frozen_h04_statistical_analysis_plan",
    "require_and_validate_actual_h02_h03_completed_confirmatory_blind_longitudinal_bundle",
    "require_and_validate_independent_blind_analyst_attestation",
    "stop_before_participant_rating_audio_restricted_data_statistics_effect_report_or_output",
)

COMPARISONS = ("c3_minus_c0", "c5_minus_c3")
TIMEPOINTS = ("pre", "post_at_2_weeks", "retention_4_weeks_after_post")
RANDOM_INTERCEPTS = ("participant", "sentence", "rater")
PRIMARY_POST = (
    "c3_minus_c0_pre_to_post",
    "c5_minus_c3_pre_to_post",
)
KEY_RETENTION = (
    "c3_minus_c0_pre_to_retention",
    "c5_minus_c3_pre_to_retention",
)
ALL_CONFIRMATORY = PRIMARY_POST + KEY_RETENTION
SECONDARY_MEMBERS = (
    "phone_accuracy_post",
    "phone_accuracy_retention",
    "fluency_post",
    "fluency_retention",
)
SENSITIVITY_METHODS = (
    "mar_multilevel_multiple_imputation",
    "mnar_delta_adjusted_pattern_mixture",
    "complete_case",
    "per_protocol_at_least_80_percent_exposure",
)

TOP_LEVEL_CONFIG_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_h00",
    "predecessor_feasibility_pilot",
    "predecessor_h01",
    "predecessor_h02_h03",
    "statistical_analysis_plan",
    "analyst_attestation",
    "schemas",
    "analysis_contract",
    "unresolved_preregistration",
    "privacy_and_access",
    "non_execution",
    "execution_order",
    "outputs",
}

CONFIG_SECTION_KEYS = {
    "experiment": {
        "id",
        "stage",
        "implementation_status",
        "actual_participant_or_rating_access_allowed",
        "actual_audio_or_transcript_access_allowed",
        "actual_statistics_or_effect_computation_allowed",
        "actual_model_or_gpu_access_allowed",
        "actual_network_access_allowed",
        "actual_report_or_output_write_allowed",
        "publication_allowed",
        "completion_claim_allowed",
    },
    "canonical_sources": {"access_scope", "pins"},
    "predecessor_h00": {
        "deep_validation_required",
        "actual_official_authorization_required",
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "approval_path",
        "approval_sha256",
        "attestation_path",
        "attestation_sha256",
        "required_preflight_status",
        "required_actual_pilot_scope",
        "required_analysis_scope",
        "restricted_official_document_resolution_allowed",
    },
    "predecessor_h01": {
        "actual_pilot_informed_final_sample_size_lock_required",
        "completion_path",
        "completion_sha256",
        "schema_path",
        "schema_sha256",
        "source_planning_config_path",
        "source_planning_config_sha256",
        "source_planning_artifact_path",
        "source_planning_artifact_sha256",
        "required_schema_version",
        "required_status",
        "actual_empirical_pilot_required",
        "observed_pilot_treatment_effect_for_power_allowed",
        "full_mixed_or_cluster_simulation_required",
        "automatic_final_sample_size_lock_allowed",
        "statistician_and_principal_investigator_manual_lock_required",
        "public_preregistration_before_confirmatory_recruitment_required",
    },
    "predecessor_feasibility_pilot": {
        "actual_h00_authorized_feasibility_pilot_required",
        "completion_path",
        "completion_sha256",
        "schema_path",
        "schema_sha256",
        "required_schema_version",
        "required_status",
        "minimum_randomized_per_arm",
        "maximum_randomized_per_arm",
        "blind_rating_reliability_required",
        "aggregate_nuisance_parameters_required",
        "observed_treatment_effect_for_power_allowed",
        "confirmatory_efficacy_claim_allowed",
        "restricted_d1_data_resolution_allowed_by_static_package",
    },
    "predecessor_h02_h03": {
        "actual_completed_confirmatory_blind_longitudinal_bundle_required",
        "completion_path",
        "completion_sha256",
        "schema_path",
        "schema_sha256",
        "required_schema_version",
        "required_status",
        "actual_h02_two_week_intervention_required",
        "actual_h03_pre_post_retention_required",
        "minimum_independent_raters",
        "blind_condition_timepoint_identity_required",
        "participant_blinding_claim_allowed",
        "powered_final_sample_sizes_must_match_h01_lock",
        "feasibility_pilot_receipt_must_not_be_reused_as_confirmatory_bundle",
        "restricted_d1_data_resolution_allowed_by_static_package",
    },
    "statistical_analysis_plan": {
        "template_path",
        "template_sha256",
        "approved_path",
        "approved_sha256",
        "required_schema_version",
        "required_status",
        "must_be_frozen_before_h02_h03_data_lock_condition_unmask_or_analysis",
        "unresolved_fields_must_fail_closed",
    },
    "analyst_attestation": {
        "template_path",
        "template_sha256",
        "approved_path",
        "approved_sha256",
        "required_schema_version",
        "required_status",
        "independent_analyst_required",
        "blind_until_data_and_sap_lock_required",
        "unresolved_fields_must_fail_closed",
    },
    "schemas": {
        "aggregate_input_path",
        "aggregate_input_sha256",
        "statistical_report_path",
        "statistical_report_sha256",
        "completion_path",
        "completion_sha256",
    },
    "analysis_contract": {
        "primary_population",
        "primary_estimator",
        "fixed_effect_formula",
        "random_intercepts",
        "primary_post_contrasts",
        "key_retention_contrasts",
        "required_effect_reporting",
        "missingness_primary",
        "missingness_sensitivity",
        "forbidden_missingness_or_population_methods",
        "per_protocol_minimum_assigned_exposure_fraction",
        "per_protocol_primary_claim_allowed",
        "multiplicity_method",
        "alpha_two_sided",
        "multiplicity_families",
        "required_disposition_and_safety_reporting",
    },
    "unresolved_preregistration": {
        "exact_software_and_version_id",
        "exact_model_family_id",
        "exact_ordinal_or_linear_choice_id",
        "exact_outcome_support_id",
        "exact_mnar_delta_grid",
        "exact_mi_imputation_count",
        "exact_optimizer_id",
        "exact_convergence_rule_id",
        "exact_ci_method_id",
        "exact_bootstrap_or_resampling_support_id",
    },
    "privacy_and_access": {
        "aggregate_input_zone",
        "statistical_report_zone",
        "restricted_analysis_data_zone",
        "participant_or_rating_row_access_allowed_by_static_package",
        "audio_or_transcript_access_allowed_by_static_package",
        "restricted_locator_resolution_allowed_by_static_package",
        "aggregate_publication_allowed_by_static_package",
        "forbidden_recursive_fields",
    },
    "non_execution": {
        "participant_access_allowed",
        "rating_row_access_allowed",
        "audio_or_transcript_access_allowed",
        "restricted_d1_data_access_allowed",
        "model_access_allowed",
        "gpu_access_allowed",
        "network_access_allowed",
        "statistics_computation_allowed",
        "effect_estimation_allowed",
        "report_generation_allowed",
        "output_write_allowed",
        "registry_or_commit_write_allowed",
        "publication_or_completion_claim_allowed",
    },
    "outputs": {
        "aggregate_input_path",
        "statistical_report_path",
        "completion_path",
        "registry_path",
        "failure_report_path",
        "no_overwrite",
        "atomic_publish_required",
        "write_enabled_in_this_package",
    },
}

H01_KEYS = {
    "schema_version",
    "lock_id",
    "status",
    "privacy_zone",
    "h00_approval_sha256",
    "h00_attestation_sha256",
    "source_planning_config_sha256",
    "source_planning_artifact_sha256",
    "feasibility_pilot_completion_sha256",
    "pilot_nuisance_report_sha256",
    "full_simulation_report_sha256",
    "public_preregistration_sha256",
    "minimum_relevant_difference_receipt_sha256",
    "statistician_approval_receipt_sha256",
    "principal_investigator_approval_receipt_sha256",
    "locked_at_utc",
    "locked_before_confirmatory_recruitment",
    "actual_empirical_pilot_used",
    "pilot_observed_treatment_effect_used",
    "full_mixed_or_cluster_simulation_completed",
    "final_sample_size_auto_locked",
    "final_sample_size_manually_locked",
    "comparisons",
    "binding_sha256",
}
FEASIBILITY_KEYS = {
    "schema_version",
    "pilot_id",
    "status",
    "privacy_zone",
    "lineage",
    "completion",
    "counts",
    "receipts",
    "privacy_safety",
    "binding_sha256",
}
FEASIBILITY_LINEAGE_KEYS = {
    "h00_approval_sha256",
    "h00_attestation_sha256",
    "protocol_sha256",
    "feasibility_preregistration_sha256",
    "allocation_contract_sha256",
    "c0_manual_sha256",
    "c3_manual_sha256",
    "c5_manual_sha256",
}
FEASIBILITY_COMPLETION_KEYS = {
    "actual_participants_enrolled_and_randomized",
    "approved_consent_and_audio_scope_confirmed",
    "two_week_feasibility_intervention_completed",
    "pre_post_retention_collection_completed",
    "blind_independent_ratings_completed",
    "minimum_independent_raters",
    "raters_blinded_to_condition_timepoint_and_identity",
    "participant_blinding_claimed",
    "feasibility_only_not_confirmatory_efficacy",
    "observed_treatment_effect_used_for_power",
    "aggregate_nuisance_parameters_completed",
    "withdrawal_handled_under_approved_consent",
}
FEASIBILITY_STAGE_COUNT_KEYS = {
    "comparison_id",
    "arm_a",
    "arm_b",
    "arm_a_randomized",
    "arm_b_randomized",
    "randomized_total",
    "pre_observed_participants",
    "post_observed_participants",
    "retention_observed_participants",
    "blind_rating_count",
}
FEASIBILITY_RECEIPT_KEYS = {
    "aggregate_nuisance_parameter_report_sha256",
    "blind_rater_reliability_report_sha256",
    "randomization_receipt_sha256",
    "delivery_fidelity_report_sha256",
    "missingness_attrition_report_sha256",
    "adverse_feedback_report_sha256",
    "data_lock_receipt_sha256",
}
FEASIBILITY_PRIVACY_KEYS = {
    "participant_rows_present",
    "participant_identifiers_present",
    "audio_or_transcript_present",
    "raw_locator_present",
    "free_text_present",
    "individual_scores_present",
    "observed_effect_estimates_present",
    "aggregate_only",
}
H01_COMPARISON_KEYS = {
    "comparison_id",
    "arms",
    "allocation_ratio",
    "final_enrollment_per_arm",
    "total_final_enrollment",
}
H02_H03_KEYS = {
    "schema_version",
    "bundle_id",
    "status",
    "privacy_zone",
    "lineage",
    "completion",
    "counts",
    "receipts",
    "privacy_safety",
    "binding_sha256",
}
H02_LINEAGE_KEYS = {
    "h00_approval_sha256",
    "h00_attestation_sha256",
    "feasibility_pilot_completion_sha256",
    "h01_final_sample_size_lock_sha256",
    "frozen_sap_sha256",
    "protocol_sha256",
    "allocation_contract_sha256",
    "c0_manual_sha256",
    "c3_manual_sha256",
    "c5_manual_sha256",
}
H02_COMPLETION_KEYS = {
    "actual_participants_enrolled_and_randomized",
    "approved_consent_and_audio_scope_confirmed",
    "h02_two_week_intervention_completed",
    "h03_pre_post_retention_completed",
    "blind_independent_ratings_completed",
    "minimum_independent_raters",
    "raters_blinded_to_condition_timepoint_and_identity",
    "participant_blinding_claimed",
    "analyst_arm_labels_masked_until_data_and_sap_lock",
    "data_locked_after_sap_freeze",
    "withdrawal_handled_under_approved_consent",
}
STAGE_COUNT_KEYS = {
    "arm_a",
    "arm_b",
    "randomized_total",
    "pre_observed_participants",
    "post_observed_participants",
    "retention_observed_participants",
    "blind_rating_count",
}
H02_RECEIPT_KEYS = {
    "restricted_d1_analysis_dataset_sha256",
    "blind_rating_dataset_sha256",
    "randomization_receipt_sha256",
    "delivery_fidelity_report_sha256",
    "missingness_report_sha256",
    "withdrawal_report_sha256",
    "adverse_feedback_report_sha256",
    "ceiling_floor_report_sha256",
    "data_lock_receipt_sha256",
}
H02_PRIVACY_KEYS = {
    "participant_rows_present",
    "participant_identifiers_present",
    "audio_or_transcript_present",
    "raw_locator_present",
    "free_text_present",
    "individual_scores_present",
    "aggregate_only",
}

SAP_KEYS = {
    "schema_version",
    "sap_id",
    "status",
    "registered_at_utc",
    "registered_by_role",
    "frozen_before_h02_h03_data_lock_condition_unmask_or_analysis",
    "lineage",
    "estimand_and_population",
    "primary_model",
    "confirmatory_contrasts",
    "missing_data",
    "per_protocol_sensitivity",
    "multiplicity",
    "aggregate_reporting",
    "approval",
    "binding_sha256",
}
SAP_SECTION_KEYS = {
    "lineage": {
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "h01_final_sample_size_lock_sha256",
        "protocol_sha256",
    },
    "estimand_and_population": {
        "primary_population",
        "intercurrent_event_policy",
        "primary_outcome",
        "primary_time_comparison",
        "key_time_comparison",
        "comparisons",
    },
    "primary_model": {
        "primary_estimator",
        "fixed_effect_formula",
        "random_intercepts",
        "software_and_version_id",
        "software_environment_lock_sha256",
        "model_family_id",
        "ordinal_or_linear_choice_id",
        "outcome_support_id",
        "optimizer_id",
        "convergence_rule_id",
        "ci_method_id",
        "bootstrap_or_resampling_support_id",
        "fallback_model_policy_id",
        "model_selection_using_unblinded_effects_allowed",
    },
    "confirmatory_contrasts": {
        "primary_post",
        "key_retention",
        "required_effect_reporting",
    },
    "missing_data": {
        "primary",
        "sensitivity_methods",
        "mi_imputation_count",
        "mi_model_id",
        "delta_grid",
        "pattern_mixture_model_id",
        "complete_case_definition_id",
        "last_observation_carried_forward_allowed",
        "post_randomization_exclusion_allowed",
    },
    "per_protocol_sensitivity": {
        "minimum_assigned_exposure_fraction",
        "sensitivity_only",
        "primary_claim_allowed",
        "exact_exposure_denominator_and_window_id",
    },
    "multiplicity": {
        "method",
        "alpha_two_sided",
        "families",
        "primary_post_members",
        "key_retention_members",
        "secondary_family_member_ids",
    },
    "aggregate_reporting": {
        "missingness_by_arm_time_and_coded_reason_required",
        "withdrawal_by_arm_required",
        "adverse_feedback_by_arm_required",
        "ceiling_and_floor_by_arm_required",
        "model_convergence_and_support_required",
        "participant_rows_or_individual_scores_allowed",
        "audio_transcript_raw_locator_or_free_text_allowed",
    },
    "approval": {
        "decision",
        "statistician_approval_receipt_sha256",
        "principal_investigator_approval_receipt_sha256",
        "approved_and_frozen",
    },
}

ANALYST_KEYS = {
    "schema_version",
    "attestation_id",
    "status",
    "privacy_zone",
    "analyst_identity_sha256",
    "analyst_role_id",
    "attested_at_utc",
    "lineage",
    "blinding_and_order",
    "analysis_commitments",
    "reproducibility",
    "privacy_assurance",
    "signed_attestation_receipt_sha256",
    "binding_sha256",
}
ANALYST_SECTION_KEYS = {
    "lineage": {
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "h01_final_sample_size_lock_sha256",
        "h02_h03_completion_sha256",
        "frozen_sap_sha256",
    },
    "blinding_and_order": {
        "independent_statistical_analyst",
        "arm_labels_masked_until_data_and_sap_lock",
        "sap_seen_and_accepted_before_authorized_unmask",
        "no_unregistered_effect_driven_model_selection",
    },
    "analysis_commitments": {
        "statistics_execution_before_full_authorization_allowed",
        "itt_primary_required",
        "likelihood_mixed_model_primary_required",
        "participant_sentence_rater_random_intercepts_required",
        "all_four_comprehensibility_contrasts_required",
        "raw_standardized_and_95_ci_required",
        "mar_multilevel_mi_required",
        "mnar_delta_pattern_mixture_required",
        "complete_case_required",
        "per_protocol_at_least_80_percent_is_sensitivity_only",
        "holm_primary_retention_secondary_families_required",
        "last_observation_carried_forward_used",
        "post_randomization_exclusion_used",
    },
    "reproducibility": {
        "analysis_code_sha256",
        "software_environment_lock_sha256",
        "execution_receipt_sha256",
        "model_diagnostics_sha256",
    },
    "privacy_assurance": {
        "public_artifact_contains_participant_rows",
        "public_artifact_contains_identifiers",
        "public_artifact_contains_individual_scores",
        "public_artifact_contains_audio_or_transcript",
        "public_artifact_contains_raw_locator",
        "public_artifact_contains_free_text",
        "aggregate_only",
    },
}

INPUT_KEYS = {
    "schema_version",
    "input_id",
    "status",
    "privacy_zone",
    "lineage",
    "design_contract",
    "counts",
    "receipt_hashes",
    "analysis_authorization",
    "privacy_safety",
    "binding_sha256",
}
INPUT_LINEAGE_KEYS = {
    "h00_approval_sha256",
    "h00_attestation_sha256",
    "h01_final_sample_size_lock_sha256",
    "h02_h03_completion_sha256",
    "frozen_sap_sha256",
    "analyst_attestation_sha256",
}
INPUT_DESIGN_KEYS = {
    "comparisons",
    "timepoints",
    "primary_outcome",
    "primary_population",
    "fixed_effect_formula",
    "random_intercepts",
}
INPUT_STAGE_COUNT_KEYS = STAGE_COUNT_KEYS - {"arm_a", "arm_b"}
INPUT_RECEIPT_KEYS = H02_RECEIPT_KEYS | {
    "analysis_code_sha256",
    "software_environment_lock_sha256",
}
INPUT_AUTHORIZATION_KEYS = {
    "h00_scope_current",
    "h01_final_sample_size_locked",
    "h02_h03_bundle_completed",
    "sap_frozen_before_unmask_and_analysis",
    "analyst_attested",
    "restricted_d1_row_access_allowed_by_this_static_package",
    "statistics_execution_allowed_by_this_static_package",
}
INPUT_PRIVACY_KEYS = {
    "participant_rows_present",
    "participant_identifiers_present",
    "individual_ratings_present",
    "audio_or_transcript_present",
    "raw_locator_present",
    "free_text_present",
    "effect_estimates_present",
    "aggregate_only",
}

REPORT_KEYS = {
    "schema_version",
    "report_id",
    "status",
    "privacy_zone",
    "lineage",
    "analysis_contract",
    "primary_and_retention_results",
    "secondary_family_results",
    "sensitivity_analyses",
    "model_diagnostics",
    "missingness_and_safety",
    "reporting_assurance",
    "binding_sha256",
}
REPORT_LINEAGE_KEYS = {
    "aggregate_input_sha256",
    "h00_approval_sha256",
    "h00_attestation_sha256",
    "h01_final_sample_size_lock_sha256",
    "h02_h03_completion_sha256",
    "frozen_sap_sha256",
    "analyst_attestation_sha256",
    "analysis_code_sha256",
    "software_environment_lock_sha256",
}
REPORT_ANALYSIS_KEYS = {
    "primary_population",
    "primary_estimator",
    "fixed_effect_formula",
    "random_intercepts",
    "model_family_id",
    "ordinal_or_linear_choice_id",
    "outcome_support_id",
    "software_id",
    "optimizer_id",
    "convergence_rule_id",
    "ci_method_id",
    "multiplicity_method",
    "alpha_two_sided",
}
CONFIRMATORY_RESULT_KEYS = {
    "contrast_id",
    "family",
    "comparison",
    "time_comparison",
    "outcome",
    "raw_1_to_5_difference",
    "standardized_effect",
    "hypothesis",
}
SECONDARY_RESULT_KEYS = {
    "test_id",
    "family",
    "comparison",
    "outcome",
    "timepoint",
    "effect",
    "hypothesis",
}
INTERVAL_KEYS = {"estimate", "ci_lower", "ci_upper"}
HYPOTHESIS_KEYS = {
    "two_sided",
    "unadjusted_p_value",
    "holm_adjusted_p_value",
    "reject_at_familywise_0_05",
}
SENSITIVITY_KEYS = {
    "method",
    "parameters_sha256",
    "analysis_population",
    "results",
}
SENSITIVITY_ESTIMATE_KEYS = {
    "contrast_id",
    "estimate",
    "ci_lower",
    "ci_upper",
}
MODEL_DIAGNOSTIC_KEYS = {
    "primary_model_converged",
    "optimizer_converged",
    "singular_or_boundary_fit",
    "support_violations",
    "fallback_model_used",
    "convergence_diagnostics_sha256",
    "bootstrap_or_ci_diagnostics_sha256",
}
MISSINGNESS_SAFETY_KEYS = {
    "missingness_by_arm_and_time",
    "withdrawal_by_arm",
    "adverse_feedback_by_arm",
    "ceiling_by_arm",
    "floor_by_arm",
}
MISSINGNESS_CELL_KEYS = {
    "comparison",
    "condition",
    "timepoint",
    "denominator",
    "missing_count",
    "missing_fraction",
    "coded_reason_summary_sha256",
}
COUNT_CELL_KEYS = {
    "comparison",
    "condition",
    "count",
    "denominator",
    "fraction",
}
REPORTING_ASSURANCE_KEYS = {
    "actual_statistics_computed",
    "actual_effect_estimates_reported",
    "actual_report_completed",
    "itt_primary",
    "likelihood_mixed_model_primary",
    "holm_applied_within_primary_retention_secondary_families",
    "mar_mi_reported",
    "mnar_pattern_mixture_reported",
    "complete_case_reported",
    "per_protocol_is_sensitivity_only",
    "last_observation_carried_forward_used",
    "post_randomization_exclusion_used",
    "participant_rows_present",
    "individual_scores_present",
    "audio_or_transcript_present",
    "raw_locator_present",
    "free_text_present",
    "aggregate_only",
    "publication_allowed",
}

COMPLETION_KEYS = {
    "schema_version",
    "commit_id",
    "status",
    "privacy_zone",
    "lineage",
    "artifacts",
    "assurance",
    "binding_sha256",
}
COMPLETION_LINEAGE_KEYS = INPUT_LINEAGE_KEYS - {"analyst_attestation_sha256"} | {
    "analyst_attestation_sha256"
}
COMPLETION_ARTIFACT_KEYS = {"aggregate_input", "statistical_report"}
ARTIFACT_KEYS = {"sha256", "size_bytes"}
COMPLETION_ASSURANCE_KEYS = {
    "actual_h04_analysis_completed",
    "strict_report_validation_completed",
    "itt_primary",
    "likelihood_mixed_model_primary",
    "all_registered_contrasts_reported",
    "all_registered_sensitivities_reported",
    "familywise_holm_reported",
    "last_observation_carried_forward_used",
    "post_randomization_exclusion_used",
    "participant_or_rating_rows_present",
    "audio_or_transcript_present",
    "raw_locator_present",
    "free_text_present",
    "model_gpu_network_used_by_static_package",
    "aggregate_only",
    "publication_allowed",
}

FORBIDDEN_PUBLIC_FIELDS = {
    "name",
    "email",
    "phone",
    "address",
    "legal_name",
    "participant_id",
    "participant_rows",
    "rater_id",
    "reviewer_id",
    "individual_score",
    "individual_rating",
    "audio_path",
    "recording_path",
    "transcript",
    "source_path",
    "raw_path",
    "restricted_path",
    "locator",
    "url",
    "filename",
    "file_name",
    "free_text",
    "comment",
    "note",
}


class H04ContractError(ValueError):
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
            raise H04ContractError("YAML mapping keys must be strings")
        if key in result:
            raise H04ContractError(f"duplicate YAML key: {key}")
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
    h00_config_read_count: int = 0
    h00_contract_load_count: int = 0
    h00_deep_validation_count: int = 0
    h00_approval_path_resolution_count: int = 0
    h00_approval_read_count: int = 0
    h00_attestation_path_resolution_count: int = 0
    h00_attestation_read_count: int = 0
    h00_restricted_official_document_resolution_count: int = 0
    h00_restricted_official_document_read_count: int = 0
    feasibility_pilot_path_resolution_count: int = 0
    feasibility_pilot_read_attempt_count: int = 0
    feasibility_pilot_read_count: int = 0
    feasibility_pilot_deep_validation_count: int = 0
    h01_completion_path_resolution_count: int = 0
    h01_completion_read_attempt_count: int = 0
    h01_completion_read_count: int = 0
    h01_deep_validation_count: int = 0
    sap_path_resolution_count: int = 0
    sap_read_attempt_count: int = 0
    sap_read_count: int = 0
    sap_deep_validation_count: int = 0
    h02_h03_completion_path_resolution_count: int = 0
    h02_h03_completion_read_attempt_count: int = 0
    h02_h03_completion_read_count: int = 0
    h02_h03_deep_validation_count: int = 0
    analyst_attestation_path_resolution_count: int = 0
    analyst_attestation_read_attempt_count: int = 0
    analyst_attestation_read_count: int = 0
    analyst_attestation_deep_validation_count: int = 0
    aggregate_input_path_resolution_count: int = 0
    aggregate_input_read_count: int = 0
    participant_read_count: int = 0
    rating_row_read_count: int = 0
    audio_read_count: int = 0
    transcript_read_count: int = 0
    restricted_d1_data_read_count: int = 0
    personal_or_sensitive_data_read_count: int = 0
    model_access_count: int = 0
    gpu_access_count: int = 0
    network_access_count: int = 0
    statistics_computation_count: int = 0
    effect_estimation_count: int = 0
    report_generation_count: int = 0
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
class H00Evidence:
    config_sha256: str
    approval_sha256: str
    attestation_sha256: str


@dataclass(frozen=True)
class FeasibilityEvidence:
    sha256: str
    payload: Mapping[str, Any]
    snapshot: FileSnapshot | None


@dataclass(frozen=True)
class H01Evidence:
    sha256: str
    payload: Mapping[str, Any]
    snapshot: FileSnapshot | None


@dataclass(frozen=True)
class SapEvidence:
    sha256: str
    payload: Mapping[str, Any]
    snapshot: FileSnapshot | None


@dataclass(frozen=True)
class H02H03Evidence:
    sha256: str
    payload: Mapping[str, Any]
    snapshot: FileSnapshot | None


@dataclass(frozen=True)
class AnalystEvidence:
    sha256: str
    payload: Mapping[str, Any]
    snapshot: FileSnapshot | None


@dataclass(frozen=True)
class PreflightResult:
    status: str
    config_sha256: str
    h00_approval_sha256: str
    h00_attestation_sha256: str
    feasibility_pilot_completion_sha256: str
    h01_completion_sha256: str
    sap_sha256: str
    h02_h03_completion_sha256: str
    analyst_attestation_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise H04ContractError(f"{name} must be a string-keyed mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise H04ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise H04ContractError(f"{name} must be a non-empty string")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise H04ContractError(f"{name} must be boolean")
    return value


def _integer(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise H04ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise H04ContractError(f"{name} must be >= {minimum}")
    return value


def _number(
    value: object,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise H04ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise H04ContractError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise H04ContractError(f"{name} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise H04ContractError(f"{name} must be <= {maximum}")
    return result


def _decimal_probability(value: object, name: str) -> Decimal:
    result = _number(value, name, minimum=0.0, maximum=1.0)
    try:
        return Decimal(str(result))
    except InvalidOperation as exc:
        raise H04ContractError(f"{name} invalid decimal") from exc


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if not SHA256_RE.fullmatch(text):
        raise H04ContractError(f"{name} must be lowercase SHA-256")
    return text


def _safe_id(value: object, name: str) -> str:
    text = _string(value, name)
    if not SAFE_ID_RE.fullmatch(text):
        raise H04ContractError(f"{name} must be a bounded safe ID")
    return text


def _lower_id(value: object, name: str) -> str:
    text = _string(value, name)
    if not LOWER_ID_RE.fullmatch(text):
        raise H04ContractError(f"{name} must be a lower-case deterministic ID")
    return text


def _utc(value: object, name: str) -> str:
    text = _string(value, name)
    match = UTC_RE.fullmatch(text)
    if match is None:
        raise H04ContractError(f"{name} must be UTC second precision")
    from datetime import datetime, timezone

    try:
        parsed = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            tzinfo=timezone.utc,
        )
    except ValueError as exc:
        raise H04ContractError(f"{name} invalid UTC timestamp") from exc
    if parsed > datetime.now(timezone.utc):
        raise H04ContractError(f"{name} cannot be in the future")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise H04ContractError(
            f"{name} mismatch: expected {expected!r}, got {actual!r}"
        )


def _expect_keys(
    payload: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise H04ContractError(f"{name} key drift; missing={missing}, extra={extra}")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise H04ContractError(f"{name} contains non-finite number")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_nonfinite_tree(item, name)
    elif isinstance(value, list):
        for item in value:
            _reject_nonfinite_tree(item, name)


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise H04ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise H04ContractError(f"non-finite JSON constant prohibited: {value}")


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
    without_binding = {
        key: item for key, item in value.items() if key != "binding_sha256"
    }
    return hashlib.sha256(canonical_json_bytes(without_binding)).hexdigest()


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
        raise H04ContractError(
            f"{name} contains participant/row/audio/path/free-text fields: "
            f"{sorted(forbidden)}"
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
        raise H04ContractError(f"{name} must be a safe workspace-relative path")
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
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        leaf = index == len(parts) - 1
        try:
            info = current.lstat()
        except FileNotFoundError:
            if require_leaf:
                raise H04ContractError(f"{name} missing: {relative}") from None
            return root / relative
        if stat.S_ISLNK(info.st_mode):
            raise H04ContractError(f"{name} symlink prohibited: {relative}")
        if not leaf and not stat.S_ISDIR(info.st_mode):
            raise H04ContractError(f"{name} parent is not directory: {relative}")
        if leaf and require_leaf and not stat.S_ISREG(info.st_mode):
            raise H04ContractError(f"{name} must be a regular file: {relative}")
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
            raise H04ContractError(f"{name} is not a regular file")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise H04ContractError(f"{name} changed before open")
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
        raise H04ContractError(f"{name} changed while reading")
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
        raise H04ContractError(
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
        raise H04ContractError(f"{name} invalid JSON: {exc}") from exc
    payload = _mapping(value, name)
    _reject_nonfinite_tree(payload, name)
    return payload


def _load_config_bytes(data: bytes) -> Mapping[str, Any]:
    try:
        value = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise H04ContractError(f"invalid YAML config: {exc}") from exc
    payload = _mapping(value, "config")
    _reject_nonfinite_tree(payload, "config")
    return payload


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        return _load_config_bytes(path.read_bytes())
    except OSError as exc:
        raise H04ContractError(f"cannot read config {path}: {exc}") from exc


def _read_exact_json(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
    name: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    data, snapshot = _read_file(workspace_root, relative, name)
    _expect(snapshot.sha256, _sha256(expected_sha256, f"{name} expected SHA"), name)
    return _load_json_bytes(data, name), snapshot


def _load_module(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
    module_name: str,
) -> ModuleType:
    path = _reject_symlink_chain(
        workspace_root,
        relative,
        module_name,
        require_leaf=True,
    )
    data, snapshot = _read_file(workspace_root, relative, module_name)
    _expect(
        snapshot.sha256,
        _sha256(expected_sha256, f"{module_name} expected SHA"),
        f"{module_name} SHA",
    )
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(data, str(path), "exec"), module.__dict__)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise H04ContractError(
            f"cannot import pinned module {relative}: {exc}"
        ) from exc
    return module


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
        except H04ContractError as exc:
            raise H04ContractError(
                str(exc),
                stage=f"{stage}_toctou",
                audit=audit,
            ) from exc
        audit.source_toctou_verification_count += 1


def _validate_schema_root(
    schema: Mapping[str, Any],
    *,
    root_keys: set[str],
    name: str,
) -> None:
    _expect(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema", name)
    _expect(schema.get("type"), "object", f"{name} root type")
    _expect(schema.get("additionalProperties"), False, f"{name} root strictness")
    _expect(set(_list(schema.get("required"), f"{name} required")), root_keys, name)
    _expect(
        set(_mapping(schema.get("properties"), f"{name} properties")), root_keys, name
    )


def _validate_templates(payload_by_path: Mapping[str, bytes]) -> None:
    sap_path = "configs/human_research/h04_statistical_analysis_plan_v1.template.json"
    analyst_path = "configs/human_research/h04_analyst_attestation_v1.template.json"
    sap = _load_json_bytes(payload_by_path[sap_path], "H04 SAP template")
    _expect_keys(sap, SAP_KEYS, "H04 SAP template")
    for section, keys in SAP_SECTION_KEYS.items():
        _expect_keys(
            _mapping(sap.get(section), f"SAP template {section}"), keys, section
        )
    _expect(
        sap.get("schema_version"),
        "voice_coach.h04_statistical_analysis_plan.v1",
        "SAP template schema",
    )
    _expect(sap.get("sap_id"), TBD_SAP, "SAP template ID")
    _expect(sap.get("status"), "draft_unapproved", "SAP template status")
    _expect(
        sap.get("frozen_before_h02_h03_data_lock_condition_unmask_or_analysis"),
        False,
        "SAP template freeze",
    )
    if not _contains_tbd(sap):
        raise H04ContractError("H04 SAP template must retain unresolved values")
    analyst = _load_json_bytes(
        payload_by_path[analyst_path],
        "H04 analyst template",
    )
    _expect_keys(analyst, ANALYST_KEYS, "H04 analyst template")
    for section, keys in ANALYST_SECTION_KEYS.items():
        _expect_keys(
            _mapping(analyst.get(section), f"analyst template {section}"),
            keys,
            section,
        )
    _expect(
        analyst.get("schema_version"),
        "voice_coach.h04_analyst_attestation.v1",
        "analyst template schema",
    )
    _expect(analyst.get("attestation_id"), TBD_ANALYST, "analyst template ID")
    _expect(analyst.get("status"), "draft_unapproved", "analyst template status")
    if not _contains_tbd(analyst):
        raise H04ContractError("H04 analyst template must retain unresolved values")


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_CONFIG_KEYS, "H04 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "config schema")
    for section, keys in CONFIG_SECTION_KEYS.items():
        _expect_keys(_mapping(config.get(section), section), keys, section)

    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(experiment.get("id"), "h04_learning_effect_analysis_v1", "experiment ID")
    _expect(
        experiment.get("stage"),
        "wp9_h04_learning_effect_statistical_analysis",
        "experiment stage",
    )
    _expect(experiment.get("implementation_status"), STATIC_STATUS, "status")
    for field in CONFIG_SECTION_KEYS["experiment"] - {
        "id",
        "stage",
        "implementation_status",
    }:
        _expect(experiment.get(field), False, f"experiment.{field}")

    canonical = _mapping(config.get("canonical_sources"), "canonical sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_or_A1_aggregate_static_pins_only",
        "canonical source scope",
    )

    h00 = _mapping(config.get("predecessor_h00"), "H00 predecessor")
    for field in (
        "deep_validation_required",
        "actual_official_authorization_required",
        "required_actual_pilot_scope",
        "required_analysis_scope",
    ):
        _expect(h00.get(field), True, f"H00.{field}")
    _expect(
        h00.get("restricted_official_document_resolution_allowed"),
        False,
        "H00 restricted document boundary",
    )
    for field in ("config_path", "contract_path", "approval_path", "attestation_path"):
        _relative_string(h00.get(field), f"H00.{field}")
    for field in ("config_sha256", "contract_sha256"):
        _sha256(h00.get(field), f"H00.{field}")
    for field, placeholder in (
        ("approval_sha256", TBD_H00_APPROVAL),
        ("attestation_sha256", TBD_H00_ATTESTATION),
    ):
        value = _string(h00.get(field), f"H00.{field}")
        if value != placeholder:
            _sha256(value, f"H00.{field}")
    _expect(
        h00.get("required_preflight_status"),
        "ready_for_separately_authorized_h00_human_operations",
        "H00 preflight status",
    )

    feasibility = _mapping(
        config.get("predecessor_feasibility_pilot"),
        "feasibility pilot predecessor",
    )
    for field in (
        "actual_h00_authorized_feasibility_pilot_required",
        "blind_rating_reliability_required",
        "aggregate_nuisance_parameters_required",
    ):
        _expect(feasibility.get(field), True, f"feasibility pilot.{field}")
    for field in (
        "observed_treatment_effect_for_power_allowed",
        "confirmatory_efficacy_claim_allowed",
        "restricted_d1_data_resolution_allowed_by_static_package",
    ):
        _expect(feasibility.get(field), False, f"feasibility pilot.{field}")
    for field in ("completion_path", "schema_path"):
        _relative_string(feasibility.get(field), f"feasibility pilot.{field}")
    _sha256(feasibility.get("schema_sha256"), "feasibility pilot schema SHA")
    feasibility_sha = _string(
        feasibility.get("completion_sha256"),
        "feasibility pilot completion SHA",
    )
    if feasibility_sha != TBD_FEASIBILITY:
        _sha256(feasibility_sha, "feasibility pilot completion SHA")
    _expect(
        feasibility.get("required_schema_version"),
        "voice_coach.h04_feasibility_pilot_completion.v1",
        "feasibility pilot schema",
    )
    _expect(
        feasibility.get("required_status"),
        "completed_feasibility_pilot_before_h01_lock",
        "feasibility pilot status",
    )
    _expect(
        _integer(
            feasibility.get("minimum_randomized_per_arm"),
            "feasibility minimum per arm",
        ),
        15,
        "feasibility minimum per arm",
    )
    _expect(
        _integer(
            feasibility.get("maximum_randomized_per_arm"),
            "feasibility maximum per arm",
        ),
        20,
        "feasibility maximum per arm",
    )

    h01 = _mapping(config.get("predecessor_h01"), "H01 predecessor")
    for field in (
        "actual_pilot_informed_final_sample_size_lock_required",
        "actual_empirical_pilot_required",
        "full_mixed_or_cluster_simulation_required",
        "statistician_and_principal_investigator_manual_lock_required",
        "public_preregistration_before_confirmatory_recruitment_required",
    ):
        _expect(h01.get(field), True, f"H01.{field}")
    for field in (
        "observed_pilot_treatment_effect_for_power_allowed",
        "automatic_final_sample_size_lock_allowed",
    ):
        _expect(h01.get(field), False, f"H01.{field}")
    for field in (
        "completion_path",
        "schema_path",
        "source_planning_config_path",
        "source_planning_artifact_path",
    ):
        _relative_string(h01.get(field), f"H01.{field}")
    for field in (
        "schema_sha256",
        "source_planning_config_sha256",
        "source_planning_artifact_sha256",
    ):
        _sha256(h01.get(field), f"H01.{field}")
    h01_sha = _string(h01.get("completion_sha256"), "H01 completion SHA")
    if h01_sha != TBD_H01_LOCK:
        _sha256(h01_sha, "H01 completion SHA")
    _expect(
        h01.get("required_schema_version"),
        "voice_coach.h04_h01_final_sample_size_lock.v1",
        "H01 schema",
    )
    _expect(
        h01.get("required_status"),
        "completed_and_locked_before_confirmatory_recruitment",
        "H01 status",
    )

    h02 = _mapping(config.get("predecessor_h02_h03"), "H02/H03 predecessor")
    for field in (
        "actual_completed_confirmatory_blind_longitudinal_bundle_required",
        "actual_h02_two_week_intervention_required",
        "actual_h03_pre_post_retention_required",
        "blind_condition_timepoint_identity_required",
        "powered_final_sample_sizes_must_match_h01_lock",
        "feasibility_pilot_receipt_must_not_be_reused_as_confirmatory_bundle",
    ):
        _expect(h02.get(field), True, f"H02/H03.{field}")
    for field in (
        "participant_blinding_claim_allowed",
        "restricted_d1_data_resolution_allowed_by_static_package",
    ):
        _expect(h02.get(field), False, f"H02/H03.{field}")
    _expect(h02.get("minimum_independent_raters"), 2, "H02/H03 raters")
    for field in ("completion_path", "schema_path"):
        _relative_string(h02.get(field), f"H02/H03.{field}")
    _sha256(h02.get("schema_sha256"), "H02/H03 schema SHA")
    h02_sha = _string(h02.get("completion_sha256"), "H02/H03 completion SHA")
    if h02_sha != TBD_H02_H03:
        _sha256(h02_sha, "H02/H03 completion SHA")
    _expect(
        h02.get("required_schema_version"),
        "voice_coach.h04_h02_h03_blind_bundle_completion.v1",
        "H02/H03 schema",
    )
    _expect(
        h02.get("required_status"),
        "completed_locked_blind_pre_post_retention",
        "H02/H03 status",
    )

    sap = _mapping(config.get("statistical_analysis_plan"), "SAP")
    analyst = _mapping(config.get("analyst_attestation"), "analyst attestation")
    for evidence_section, placeholder in ((sap, TBD_SAP), (analyst, TBD_ANALYST)):
        for field in ("template_path", "approved_path"):
            _relative_string(evidence_section.get(field), f"evidence.{field}")
        _sha256(evidence_section.get("template_sha256"), "template SHA")
        approved = _string(evidence_section.get("approved_sha256"), "approved SHA")
        if approved != placeholder:
            _sha256(approved, "approved SHA")
    _expect(
        sap.get("required_schema_version"),
        "voice_coach.h04_statistical_analysis_plan.v1",
        "SAP schema",
    )
    _expect(sap.get("required_status"), "approved_and_frozen", "SAP status")
    for field in (
        "must_be_frozen_before_h02_h03_data_lock_condition_unmask_or_analysis",
        "unresolved_fields_must_fail_closed",
    ):
        _expect(sap.get(field), True, f"SAP.{field}")
    _expect(
        analyst.get("required_schema_version"),
        "voice_coach.h04_analyst_attestation.v1",
        "analyst schema",
    )
    _expect(
        analyst.get("required_status"),
        "attested_for_locked_h04_analysis",
        "analyst status",
    )
    for field in (
        "independent_analyst_required",
        "blind_until_data_and_sap_lock_required",
        "unresolved_fields_must_fail_closed",
    ):
        _expect(analyst.get(field), True, f"analyst.{field}")

    schemas = _mapping(config.get("schemas"), "schemas")
    for field in (
        "aggregate_input_path",
        "statistical_report_path",
        "completion_path",
    ):
        _relative_string(schemas.get(field), f"schemas.{field}")
    for field in (
        "aggregate_input_sha256",
        "statistical_report_sha256",
        "completion_sha256",
    ):
        _sha256(schemas.get(field), f"schemas.{field}")

    analysis = _mapping(config.get("analysis_contract"), "analysis contract")
    _expect(
        analysis.get("primary_population"),
        "intention_to_treat_as_randomized",
        "primary population",
    )
    _expect(
        analysis.get("primary_estimator"),
        "likelihood_based_mixed_model_all_observed_itt",
        "primary estimator",
    )
    _expect(
        analysis.get("fixed_effect_formula"),
        "score~condition*time+l1+baseline",
        "fixed formula",
    )
    _expect(
        tuple(_list(analysis.get("random_intercepts"), "random")),
        RANDOM_INTERCEPTS,
        "random",
    )
    _expect(
        tuple(_list(analysis.get("primary_post_contrasts"), "primary")),
        PRIMARY_POST,
        "primary",
    )
    _expect(
        tuple(_list(analysis.get("key_retention_contrasts"), "retention")),
        KEY_RETENTION,
        "retention",
    )
    _expect(
        tuple(_list(analysis.get("required_effect_reporting"), "effects")),
        (
            "raw_1_to_5_difference",
            "standardized_effect",
            "95_percent_confidence_interval",
        ),
        "effect reporting",
    )
    _expect(
        analysis.get("missingness_primary"),
        "likelihood_based_mixed_model_using_all_observed_itt_data",
        "missingness primary",
    )
    _expect(
        tuple(_list(analysis.get("missingness_sensitivity"), "missing sensitivity")),
        SENSITIVITY_METHODS[:3],
        "missing sensitivity",
    )
    _expect(
        tuple(
            _list(
                analysis.get("forbidden_missingness_or_population_methods"), "forbidden"
            )
        ),
        ("last_observation_carried_forward", "post_randomization_exclusion"),
        "forbidden methods",
    )
    _expect(
        _number(
            analysis.get("per_protocol_minimum_assigned_exposure_fraction"),
            "PP threshold",
        ),
        0.8,
        "PP threshold",
    )
    _expect(analysis.get("per_protocol_primary_claim_allowed"), False, "PP claim")
    _expect(analysis.get("multiplicity_method"), "holm", "multiplicity")
    _expect(_number(analysis.get("alpha_two_sided"), "alpha"), 0.05, "alpha")
    _expect(
        tuple(_list(analysis.get("multiplicity_families"), "families")),
        ("primary_post", "key_retention", "secondary"),
        "families",
    )
    _expect(
        tuple(
            _list(
                analysis.get("required_disposition_and_safety_reporting"), "reporting"
            )
        ),
        (
            "missingness_by_arm_time_and_coded_reason",
            "withdrawal_by_arm",
            "adverse_feedback_by_arm",
            "ceiling_by_arm",
            "floor_by_arm",
        ),
        "reporting",
    )

    unresolved = _mapping(config.get("unresolved_preregistration"), "unresolved")
    for field in CONFIG_SECTION_KEYS["unresolved_preregistration"]:
        _expect(unresolved.get(field), "TBD_PREREGISTRATION", f"unresolved.{field}")

    privacy = _mapping(config.get("privacy_and_access"), "privacy")
    _expect(
        privacy.get("aggregate_input_zone"),
        "A1_aggregate_manifest_no_participant_rows",
        "input zone",
    )
    _expect(
        privacy.get("statistical_report_zone"),
        "A1_aggregate_statistical_report",
        "report zone",
    )
    _expect(
        privacy.get("restricted_analysis_data_zone"),
        "D1_pseudonymized_restricted_human_research",
        "D1 zone",
    )
    for field in (
        "participant_or_rating_row_access_allowed_by_static_package",
        "audio_or_transcript_access_allowed_by_static_package",
        "restricted_locator_resolution_allowed_by_static_package",
        "aggregate_publication_allowed_by_static_package",
    ):
        _expect(privacy.get(field), False, f"privacy.{field}")
    _expect(
        set(_list(privacy.get("forbidden_recursive_fields"), "forbidden fields")),
        FORBIDDEN_PUBLIC_FIELDS,
        "forbidden fields",
    )
    for field in CONFIG_SECTION_KEYS["non_execution"]:
        _expect(
            _mapping(config.get("non_execution"), "non execution").get(field),
            False,
            f"non_execution.{field}",
        )
    _expect(
        tuple(_list(config.get("execution_order"), "execution order")),
        EXPECTED_EXECUTION_ORDER,
        "execution order",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    for field in CONFIG_SECTION_KEYS["outputs"] - {
        "no_overwrite",
        "atomic_publish_required",
        "write_enabled_in_this_package",
    }:
        _relative_string(outputs.get(field), f"outputs.{field}")
    _expect(outputs.get("no_overwrite"), True, "no overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "atomic publish")
    _expect(outputs.get("write_enabled_in_this_package"), False, "write boundary")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    """Validate exactly 19 C1/A1 static sources and no runtime evidence."""

    root = workspace_root.resolve(strict=True)
    access = audit if audit is not None else AccessAudit()
    _validate_config_values(config)
    absolute_config = config_path if config_path.is_absolute() else root / config_path
    try:
        relative_config = absolute_config.relative_to(root).as_posix()
    except ValueError as exc:
        raise H04ContractError("H04 config must be inside workspace") from exc
    config_data, config_snapshot = _read_file(root, relative_config, "H04 config")
    if _load_config_bytes(config_data) != config:
        raise H04ContractError("H04 config changed between load and validation")

    pins = _list(
        _mapping(config.get("canonical_sources"), "canonical sources").get("pins"),
        "canonical pins",
    )
    if len(pins) != len(SOURCE_PATHS):
        raise H04ContractError("canonical source pin count drift")
    seen: set[str] = set()
    snapshots: list[FileSnapshot] = []
    payload_by_path: dict[str, bytes] = {}
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        if relative in seen:
            raise H04ContractError(f"duplicate source pin: {relative}")
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_sha:
            raise H04ContractError(
                f"canonical source SHA-256 drift: {relative}",
                stage="source_pin",
                audit=access,
            )
        seen.add(relative)
        snapshots.append(snapshot)
        payload_by_path[relative] = data
    if tuple(sorted(seen)) != tuple(sorted(SOURCE_PATHS)):
        raise H04ContractError("canonical source path set drift")
    by_path = {snapshot.relative_path: snapshot for snapshot in snapshots}

    schema_roots = {
        "configs/human_research/h04_feasibility_pilot_completion_v1.schema.json": FEASIBILITY_KEYS,
        "configs/human_research/h04_h01_final_sample_size_lock_v1.schema.json": H01_KEYS,
        "configs/human_research/h04_h02_h03_blind_bundle_completion_v1.schema.json": H02_H03_KEYS,
        "configs/human_research/h04_learning_effect_aggregate_input_v1.schema.json": INPUT_KEYS,
        "configs/human_research/h04_learning_effect_statistical_report_v1.schema.json": REPORT_KEYS,
        "configs/human_research/h04_learning_effect_completion_v1.schema.json": COMPLETION_KEYS,
    }
    for path, keys in schema_roots.items():
        _validate_schema_root(
            _load_json_bytes(payload_by_path[path], f"schema {path}"),
            root_keys=keys,
            name=f"schema {path}",
        )
    _validate_templates(payload_by_path)

    bindings: list[tuple[Mapping[str, Any], str, str, str]] = [
        (
            _mapping(config.get("predecessor_h00"), "H00"),
            "config_path",
            "config_sha256",
            "configs/experiments/h00_human_research_approval_v1.yaml",
        ),
        (
            _mapping(config.get("predecessor_h00"), "H00"),
            "contract_path",
            "contract_sha256",
            "src/voice_coach/human_research/h00_approval_contract.py",
        ),
        (
            _mapping(
                config.get("predecessor_feasibility_pilot"),
                "feasibility pilot",
            ),
            "schema_path",
            "schema_sha256",
            "configs/human_research/h04_feasibility_pilot_completion_v1.schema.json",
        ),
        (
            _mapping(config.get("predecessor_h01"), "H01"),
            "schema_path",
            "schema_sha256",
            "configs/human_research/h04_h01_final_sample_size_lock_v1.schema.json",
        ),
        (
            _mapping(config.get("predecessor_h01"), "H01"),
            "source_planning_config_path",
            "source_planning_config_sha256",
            "configs/experiments/learning_effect_power_v1.yaml",
        ),
        (
            _mapping(config.get("predecessor_h01"), "H01"),
            "source_planning_artifact_path",
            "source_planning_artifact_sha256",
            "artifacts/planning/learning_effect_power_sensitivity_v1.json",
        ),
        (
            _mapping(config.get("predecessor_h02_h03"), "H02/H03"),
            "schema_path",
            "schema_sha256",
            "configs/human_research/h04_h02_h03_blind_bundle_completion_v1.schema.json",
        ),
        (
            _mapping(config.get("statistical_analysis_plan"), "SAP"),
            "template_path",
            "template_sha256",
            "configs/human_research/h04_statistical_analysis_plan_v1.template.json",
        ),
        (
            _mapping(config.get("analyst_attestation"), "analyst"),
            "template_path",
            "template_sha256",
            "configs/human_research/h04_analyst_attestation_v1.template.json",
        ),
        (
            _mapping(config.get("schemas"), "schemas"),
            "aggregate_input_path",
            "aggregate_input_sha256",
            "configs/human_research/h04_learning_effect_aggregate_input_v1.schema.json",
        ),
        (
            _mapping(config.get("schemas"), "schemas"),
            "statistical_report_path",
            "statistical_report_sha256",
            "configs/human_research/h04_learning_effect_statistical_report_v1.schema.json",
        ),
        (
            _mapping(config.get("schemas"), "schemas"),
            "completion_path",
            "completion_sha256",
            "configs/human_research/h04_learning_effect_completion_v1.schema.json",
        ),
    ]
    for section, path_field, sha_field, expected_path in bindings:
        _expect(section.get(path_field), expected_path, f"{path_field} binding")
        _expect(
            section.get(sha_field),
            by_path[expected_path].sha256,
            f"{sha_field} binding",
        )

    outputs = _mapping(config.get("outputs"), "outputs")
    output_paths = [
        _relative_string(value, f"outputs.{field}")
        for field, value in outputs.items()
        if field.endswith("_path")
    ]
    if len(output_paths) != len(set(output_paths)):
        raise H04ContractError("H04 output paths must be unique")
    for output in output_paths:
        output_parts = PurePosixPath(output).parts
        for source in seen:
            source_parts = PurePosixPath(source).parts
            if (
                output_parts == source_parts
                or output_parts[: len(source_parts)] == source_parts
                or source_parts[: len(output_parts)] == output_parts
            ):
                raise H04ContractError(
                    f"H04 output/source overlap prohibited: {output} / {source}"
                )

    policy = {
        key: config[key]
        for key in (
            "predecessor_h00",
            "predecessor_feasibility_pilot",
            "predecessor_h01",
            "predecessor_h02_h03",
            "statistical_analysis_plan",
            "analyst_attestation",
            "analysis_contract",
            "unresolved_preregistration",
            "privacy_and_access",
            "non_execution",
            "execution_order",
        )
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        source_snapshots=tuple(snapshots),
    )


def _sync_h00_audit(result: Any, audit: AccessAudit) -> None:
    values = _mapping(getattr(result, "audit", {}), "H00 audit")
    audit.h00_approval_path_resolution_count = int(
        values.get("approval_path_resolution_count", 0)
    )
    audit.h00_approval_read_count = int(values.get("approval_read_count", 0))
    audit.h00_attestation_path_resolution_count = int(
        values.get("attestation_path_resolution_count", 0)
    )
    audit.h00_attestation_read_count = int(values.get("attestation_read_count", 0))
    audit.h00_restricted_official_document_resolution_count = int(
        values.get("restricted_official_document_locator_resolution_count", 0)
    )
    audit.h00_restricted_official_document_read_count = int(
        values.get("restricted_official_document_read_count", 0)
    )


def deep_validate_h00(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> H00Evidence:
    """Deep-validate H00 only after exact approval and attestation pins exist."""

    section = _mapping(config.get("predecessor_h00"), "H00 predecessor")
    approval_sha = _string(section.get("approval_sha256"), "H00 approval SHA")
    attestation_sha = _string(
        section.get("attestation_sha256"),
        "H00 attestation SHA",
    )
    if approval_sha == TBD_H00_APPROVAL or attestation_sha == TBD_H00_ATTESTATION:
        raise H04ContractError(
            "H00 approval is TBD_H00_APPROVAL",
            stage="h00_authorization",
            audit=audit,
        )
    _sha256(approval_sha, "H00 approval SHA")
    _sha256(attestation_sha, "H00 attestation SHA")

    config_data, config_snapshot = _read_file(
        workspace_root,
        _relative_string(section.get("config_path"), "H00 config path"),
        "H00 config",
    )
    audit.h00_config_read_count += 1
    _expect(config_snapshot.sha256, section.get("config_sha256"), "H00 config SHA")
    h00_config = _load_config_bytes(config_data)
    h00_approval = _mapping(h00_config.get("official_approval"), "H00 approval")
    h00_attestation = _mapping(
        h00_config.get("official_document_attestation"),
        "H00 attestation",
    )
    for actual, expected, name in (
        (
            h00_approval.get("approval_path"),
            section.get("approval_path"),
            "approval path",
        ),
        (h00_approval.get("approval_sha256"), approval_sha, "approval SHA"),
        (
            h00_attestation.get("attestation_path"),
            section.get("attestation_path"),
            "attestation path",
        ),
        (
            h00_attestation.get("attestation_sha256"),
            attestation_sha,
            "attestation SHA",
        ),
    ):
        _expect(actual, expected, f"H00 {name} binding")
    module = _load_module(
        workspace_root=workspace_root,
        relative=_relative_string(section.get("contract_path"), "H00 contract path"),
        expected_sha256=_sha256(section.get("contract_sha256"), "H00 contract SHA"),
        module_name="_voice_coach_h04_pinned_h00_contract",
    )
    audit.h00_contract_load_count += 1
    runner = getattr(module, "run_full_preflight", None)
    if not callable(runner):
        raise H04ContractError(
            "pinned H00 full preflight interface missing",
            stage="h00_authorization",
            audit=audit,
        )
    try:
        result = runner(
            workspace_root=workspace_root,
            config_path=workspace_root
            / _relative_string(section.get("config_path"), "H00 config path"),
            config=h00_config,
        )
    except Exception as exc:
        delegated_audit = getattr(exc, "audit", None)
        if delegated_audit is not None:
            _sync_h00_audit(
                type("_Result", (), {"audit": delegated_audit.as_dict()})(),
                audit,
            )
        raise H04ContractError(
            f"H00 deep validation failed: {exc}",
            stage="h00_authorization",
            audit=audit,
        ) from exc
    _sync_h00_audit(result, audit)
    audit.h00_deep_validation_count += 1
    _expect(result.status, section.get("required_preflight_status"), "H00 status")
    _expect(result.config_sha256, config_snapshot.sha256, "H00 config deep binding")
    _expect(result.approval_sha256, approval_sha, "H00 approval deep binding")
    _expect(
        result.attestation_sha256,
        attestation_sha,
        "H00 attestation deep binding",
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(config_snapshot,),
        audit=audit,
        stage="h00_config",
    )
    return H00Evidence(
        config_sha256=config_snapshot.sha256,
        approval_sha256=approval_sha,
        attestation_sha256=attestation_sha,
    )


def _validate_feasibility_stage_counts(
    value: Mapping[str, Any],
    *,
    name: str,
    comparison_id: str,
    arms: tuple[str, str],
) -> None:
    _expect_keys(value, FEASIBILITY_STAGE_COUNT_KEYS, name)
    _expect(value.get("comparison_id"), comparison_id, f"{name} comparison")
    _expect(value.get("arm_a"), arms[0], f"{name} arm A")
    _expect(value.get("arm_b"), arms[1], f"{name} arm B")
    arm_a = _integer(value.get("arm_a_randomized"), f"{name} arm A N", minimum=15)
    arm_b = _integer(value.get("arm_b_randomized"), f"{name} arm B N", minimum=15)
    if arm_a > 20 or arm_b > 20:
        raise H04ContractError(f"{name} feasibility N must be 15-20 per arm")
    randomized = _integer(value.get("randomized_total"), f"{name} total", minimum=30)
    _expect(randomized, arm_a + arm_b, f"{name} randomized arithmetic")
    for field in (
        "pre_observed_participants",
        "post_observed_participants",
        "retention_observed_participants",
    ):
        observed = _integer(value.get(field), f"{name}.{field}", minimum=0)
        if observed > randomized:
            raise H04ContractError(f"{name}.{field} exceeds randomized total")
    _integer(value.get("blind_rating_count"), f"{name} rating count", minimum=1)


def validate_feasibility_pilot_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
) -> None:
    """Validate the distinct H00-authorized feasibility-pilot aggregate receipt."""

    _reject_nonfinite_tree(payload, "feasibility pilot")
    _reject_public_leakage(payload, "feasibility pilot")
    _expect_keys(payload, FEASIBILITY_KEYS, "feasibility pilot")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h04_feasibility_pilot_completion.v1",
        "feasibility pilot schema",
    )
    if not re.fullmatch(
        r"h04feaspilot_[0-9a-f]{32}",
        _string(payload.get("pilot_id"), "feasibility pilot ID"),
    ):
        raise H04ContractError("invalid feasibility pilot ID")
    _expect(
        payload.get("status"),
        "completed_feasibility_pilot_before_h01_lock",
        "feasibility pilot status",
    )
    _expect(
        payload.get("privacy_zone"),
        "A1_aggregate_feasibility_pilot_receipt",
        "feasibility pilot zone",
    )
    lineage = _mapping(payload.get("lineage"), "feasibility pilot lineage")
    _expect_keys(lineage, FEASIBILITY_LINEAGE_KEYS, "feasibility pilot lineage")
    _expect(
        lineage.get("h00_approval_sha256"),
        h00.approval_sha256,
        "feasibility H00 approval",
    )
    _expect(
        lineage.get("h00_attestation_sha256"),
        h00.attestation_sha256,
        "feasibility H00 attestation",
    )
    for field in FEASIBILITY_LINEAGE_KEYS - {
        "h00_approval_sha256",
        "h00_attestation_sha256",
    }:
        _sha256(lineage.get(field), f"feasibility lineage.{field}")
    completion = _mapping(payload.get("completion"), "feasibility completion")
    _expect_keys(
        completion,
        FEASIBILITY_COMPLETION_KEYS,
        "feasibility completion",
    )
    for field in FEASIBILITY_COMPLETION_KEYS - {
        "minimum_independent_raters",
        "participant_blinding_claimed",
        "observed_treatment_effect_used_for_power",
    }:
        _expect(completion.get(field), True, f"feasibility completion.{field}")
    _integer(
        completion.get("minimum_independent_raters"),
        "feasibility minimum raters",
        minimum=2,
    )
    _expect(
        completion.get("participant_blinding_claimed"),
        False,
        "feasibility participant blinding",
    )
    _expect(
        completion.get("observed_treatment_effect_used_for_power"),
        False,
        "feasibility observed treatment effect",
    )
    counts = _mapping(payload.get("counts"), "feasibility counts")
    _expect_keys(
        counts,
        {"stage_1_c0_vs_c3", "stage_2_c3_vs_c5"},
        "feasibility counts",
    )
    _validate_feasibility_stage_counts(
        _mapping(counts.get("stage_1_c0_vs_c3"), "feasibility stage 1"),
        name="feasibility stage 1",
        comparison_id="c3_minus_c0",
        arms=("C0", "C3"),
    )
    _validate_feasibility_stage_counts(
        _mapping(counts.get("stage_2_c3_vs_c5"), "feasibility stage 2"),
        name="feasibility stage 2",
        comparison_id="c5_minus_c3",
        arms=("C3", "C5"),
    )
    receipts = _mapping(payload.get("receipts"), "feasibility receipts")
    _expect_keys(receipts, FEASIBILITY_RECEIPT_KEYS, "feasibility receipts")
    for field in FEASIBILITY_RECEIPT_KEYS:
        _sha256(receipts.get(field), f"feasibility receipt.{field}")
    privacy = _mapping(payload.get("privacy_safety"), "feasibility privacy")
    _expect_keys(privacy, FEASIBILITY_PRIVACY_KEYS, "feasibility privacy")
    _expect(privacy.get("aggregate_only"), True, "feasibility aggregate")
    for field in FEASIBILITY_PRIVACY_KEYS - {"aggregate_only"}:
        _expect(privacy.get(field), False, f"feasibility privacy.{field}")
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "feasibility binding",
    )


def deep_validate_feasibility_pilot(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    h00: H00Evidence,
    audit: AccessAudit,
) -> FeasibilityEvidence:
    section = _mapping(
        config.get("predecessor_feasibility_pilot"),
        "feasibility pilot predecessor",
    )
    expected_sha = _string(
        section.get("completion_sha256"),
        "feasibility pilot SHA",
    )
    if expected_sha == TBD_FEASIBILITY:
        raise H04ContractError(
            "feasibility pilot completion is TBD_H02_H03_FEASIBILITY_PILOT",
            stage="feasibility_pilot_completion",
            audit=audit,
        )
    _sha256(expected_sha, "feasibility pilot SHA")
    audit.feasibility_pilot_path_resolution_count += 1
    audit.feasibility_pilot_read_attempt_count += 1
    try:
        payload, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("completion_path"),
                "feasibility pilot completion path",
            ),
            expected_sha256=expected_sha,
            name="completed feasibility pilot",
        )
    except H04ContractError as exc:
        raise H04ContractError(
            f"feasibility pilot completion unavailable: {exc}",
            stage="feasibility_pilot_completion",
            audit=audit,
        ) from exc
    audit.feasibility_pilot_read_count += 1
    validate_feasibility_pilot_payload(payload, h00=h00)
    audit.feasibility_pilot_deep_validation_count += 1
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(snapshot,),
        audit=audit,
        stage="feasibility_pilot_completion",
    )
    return FeasibilityEvidence(snapshot.sha256, payload, snapshot)


def validate_h01_lock_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
    feasibility: FeasibilityEvidence,
    source_config_sha256: str,
    source_artifact_sha256: str,
) -> None:
    """Validate one actual pilot-informed H01 manual sample-size lock."""

    _reject_nonfinite_tree(payload, "H01 lock")
    _reject_public_leakage(payload, "H01 lock")
    _expect_keys(payload, H01_KEYS, "H01 lock")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h04_h01_final_sample_size_lock.v1",
        "H01 schema",
    )
    if not re.fullmatch(
        r"h01lock_[0-9a-f]{32}", _string(payload.get("lock_id"), "lock ID")
    ):
        raise H04ContractError("invalid H01 lock ID")
    _expect(
        payload.get("status"),
        "completed_and_locked_before_confirmatory_recruitment",
        "H01 status",
    )
    _expect(payload.get("privacy_zone"), "A1_aggregate_sample_size_lock", "H01 zone")
    _expect(payload.get("h00_approval_sha256"), h00.approval_sha256, "H01 H00 approval")
    _expect(
        payload.get("h00_attestation_sha256"),
        h00.attestation_sha256,
        "H01 H00 attestation",
    )
    _expect(
        payload.get("source_planning_config_sha256"),
        source_config_sha256,
        "H01 planning config",
    )
    _expect(
        payload.get("source_planning_artifact_sha256"),
        source_artifact_sha256,
        "H01 planning artifact",
    )
    _expect(
        payload.get("feasibility_pilot_completion_sha256"),
        feasibility.sha256,
        "H01 feasibility pilot completion",
    )
    feasibility_receipts = _mapping(
        feasibility.payload.get("receipts"),
        "feasibility receipts",
    )
    _expect(
        payload.get("pilot_nuisance_report_sha256"),
        feasibility_receipts.get("aggregate_nuisance_parameter_report_sha256"),
        "H01 feasibility nuisance report",
    )
    for field in (
        "full_simulation_report_sha256",
        "public_preregistration_sha256",
        "minimum_relevant_difference_receipt_sha256",
        "statistician_approval_receipt_sha256",
        "principal_investigator_approval_receipt_sha256",
    ):
        _sha256(payload.get(field), f"H01.{field}")
    _utc(payload.get("locked_at_utc"), "H01 locked at")
    for field in (
        "locked_before_confirmatory_recruitment",
        "actual_empirical_pilot_used",
        "full_mixed_or_cluster_simulation_completed",
        "final_sample_size_manually_locked",
    ):
        _expect(payload.get(field), True, f"H01.{field}")
    for field in (
        "pilot_observed_treatment_effect_used",
        "final_sample_size_auto_locked",
    ):
        _expect(payload.get(field), False, f"H01.{field}")
    comparisons = _mapping(payload.get("comparisons"), "H01 comparisons")
    _expect_keys(
        comparisons,
        {"stage_1_c0_vs_c3", "stage_2_c3_vs_c5"},
        "H01 comparisons",
    )
    expected = {
        "stage_1_c0_vs_c3": ("c3_minus_c0", ["C0", "C3"]),
        "stage_2_c3_vs_c5": ("c5_minus_c3", ["C3", "C5"]),
    }
    for stage, (comparison_id, arms) in expected.items():
        value = _mapping(comparisons.get(stage), f"H01 {stage}")
        _expect_keys(value, H01_COMPARISON_KEYS, f"H01 {stage}")
        _expect(value.get("comparison_id"), comparison_id, f"H01 {stage} ID")
        _expect(value.get("arms"), arms, f"H01 {stage} arms")
        _expect(value.get("allocation_ratio"), "1:1", f"H01 {stage} ratio")
        per_arm = _integer(
            value.get("final_enrollment_per_arm"),
            f"H01 {stage} per arm",
            minimum=1,
        )
        _expect(
            _integer(
                value.get("total_final_enrollment"), f"H01 {stage} total", minimum=2
            ),
            per_arm * 2,
            f"H01 {stage} total arithmetic",
        )
    _expect(payload.get("binding_sha256"), binding_sha256(payload), "H01 binding")


def deep_validate_h01(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    h00: H00Evidence,
    feasibility: FeasibilityEvidence,
    audit: AccessAudit,
) -> H01Evidence:
    section = _mapping(config.get("predecessor_h01"), "H01 predecessor")
    expected_sha = _string(section.get("completion_sha256"), "H01 completion SHA")
    if expected_sha == TBD_H01_LOCK:
        raise H04ContractError(
            "H01 final sample-size lock is TBD_H01_SAMPLE_SIZE_LOCK",
            stage="h01_completion",
            audit=audit,
        )
    _sha256(expected_sha, "H01 completion SHA")
    audit.h01_completion_path_resolution_count += 1
    audit.h01_completion_read_attempt_count += 1
    try:
        payload, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("completion_path"), "H01 completion path"
            ),
            expected_sha256=expected_sha,
            name="H01 final sample-size lock",
        )
    except H04ContractError as exc:
        raise H04ContractError(
            f"H01 completion unavailable: {exc}",
            stage="h01_completion",
            audit=audit,
        ) from exc
    audit.h01_completion_read_count += 1
    validate_h01_lock_payload(
        payload,
        h00=h00,
        feasibility=feasibility,
        source_config_sha256=_sha256(
            section.get("source_planning_config_sha256"),
            "H01 planning config SHA",
        ),
        source_artifact_sha256=_sha256(
            section.get("source_planning_artifact_sha256"),
            "H01 planning artifact SHA",
        ),
    )
    audit.h01_deep_validation_count += 1
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(snapshot,),
        audit=audit,
        stage="h01_completion",
    )
    return H01Evidence(snapshot.sha256, payload, snapshot)


def _validate_sap_structure(payload: Mapping[str, Any]) -> None:
    _reject_nonfinite_tree(payload, "H04 SAP")
    _reject_public_leakage(payload, "H04 SAP")
    _expect_keys(payload, SAP_KEYS, "H04 SAP")
    for section, keys in SAP_SECTION_KEYS.items():
        _expect_keys(_mapping(payload.get(section), f"SAP {section}"), keys, section)


def validate_sap_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
    h01: H01Evidence,
) -> None:
    """Validate the actual frozen, fully resolved H04 SAP."""

    _validate_sap_structure(payload)
    if _contains_tbd(payload):
        raise H04ContractError("approved H04 SAP contains unresolved TBD value")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h04_statistical_analysis_plan.v1",
        "SAP schema",
    )
    if not re.fullmatch(
        r"h04sap_[0-9a-f]{32}", _string(payload.get("sap_id"), "SAP ID")
    ):
        raise H04ContractError("invalid H04 SAP ID")
    _expect(payload.get("status"), "approved_and_frozen", "SAP status")
    _utc(payload.get("registered_at_utc"), "SAP registered at")
    _safe_id(payload.get("registered_by_role"), "SAP registered role")
    _expect(
        payload.get("frozen_before_h02_h03_data_lock_condition_unmask_or_analysis"),
        True,
        "SAP freeze order",
    )
    lineage = _mapping(payload.get("lineage"), "SAP lineage")
    _expect(lineage.get("h00_approval_sha256"), h00.approval_sha256, "SAP H00 approval")
    _expect(
        lineage.get("h00_attestation_sha256"),
        h00.attestation_sha256,
        "SAP H00 attestation",
    )
    _expect(
        lineage.get("h01_final_sample_size_lock_sha256"),
        h01.sha256,
        "SAP H01 lock",
    )
    _sha256(lineage.get("protocol_sha256"), "SAP protocol SHA")

    estimand = _mapping(payload.get("estimand_and_population"), "SAP estimand")
    expected_estimand: dict[str, object] = {
        "primary_population": "intention_to_treat_as_randomized",
        "intercurrent_event_policy": (
            "retain_randomized_participants_with_nonadherence_"
            "discontinuation_deviation_or_missing_followup"
        ),
        "primary_outcome": "blind_expert_comprehensibility_1_to_5",
        "primary_time_comparison": "pre_to_post",
        "key_time_comparison": "pre_to_retention",
        "comparisons": list(COMPARISONS),
    }
    for field, expected in expected_estimand.items():
        _expect(estimand.get(field), expected, f"SAP estimand.{field}")

    model = _mapping(payload.get("primary_model"), "SAP primary model")
    _expect(
        model.get("primary_estimator"),
        "likelihood_based_mixed_model_all_observed_itt",
        "SAP model.primary_estimator",
    )
    _expect(
        model.get("fixed_effect_formula"),
        "score~condition*time+l1+baseline",
        "SAP model.fixed_effect_formula",
    )
    _expect(
        model.get("random_intercepts"),
        list(RANDOM_INTERCEPTS),
        "SAP model.random_intercepts",
    )
    _expect(
        model.get("model_selection_using_unblinded_effects_allowed"),
        False,
        "SAP model.model_selection_using_unblinded_effects_allowed",
    )
    _safe_id(model.get("software_and_version_id"), "SAP software")
    _sha256(model.get("software_environment_lock_sha256"), "SAP software lock")
    for field in (
        "model_family_id",
        "ordinal_or_linear_choice_id",
        "outcome_support_id",
        "optimizer_id",
        "convergence_rule_id",
        "ci_method_id",
        "bootstrap_or_resampling_support_id",
        "fallback_model_policy_id",
    ):
        _safe_id(model.get(field), f"SAP model.{field}")

    contrasts = _mapping(payload.get("confirmatory_contrasts"), "SAP contrasts")
    _expect(
        tuple(_list(contrasts.get("primary_post"), "SAP primary")),
        PRIMARY_POST,
        "SAP primary",
    )
    _expect(
        tuple(_list(contrasts.get("key_retention"), "SAP retention")),
        KEY_RETENTION,
        "SAP retention",
    )
    _expect(
        tuple(_list(contrasts.get("required_effect_reporting"), "SAP effects")),
        (
            "raw_1_to_5_difference",
            "standardized_effect",
            "95_percent_confidence_interval",
        ),
        "SAP effect reporting",
    )

    missing = _mapping(payload.get("missing_data"), "SAP missing data")
    _expect(
        missing.get("primary"),
        "likelihood_based_mixed_model_using_all_observed_itt_data",
        "SAP missing primary",
    )
    _expect(
        tuple(_list(missing.get("sensitivity_methods"), "SAP sensitivity")),
        SENSITIVITY_METHODS[:3],
        "SAP sensitivity",
    )
    _integer(missing.get("mi_imputation_count"), "SAP MI m", minimum=2)
    _safe_id(missing.get("mi_model_id"), "SAP MI model")
    deltas = [
        _number(value, "SAP MNAR delta")
        for value in _list(missing.get("delta_grid"), "SAP delta grid")
    ]
    if not deltas or len(deltas) != len(set(deltas)) or deltas != sorted(deltas):
        raise H04ContractError("SAP MNAR delta grid must be nonempty, unique, sorted")
    _safe_id(missing.get("pattern_mixture_model_id"), "SAP pattern mixture")
    _safe_id(missing.get("complete_case_definition_id"), "SAP complete case")
    _expect(missing.get("last_observation_carried_forward_allowed"), False, "SAP LOCF")
    _expect(
        missing.get("post_randomization_exclusion_allowed"),
        False,
        "SAP post-randomization exclusion",
    )

    pp = _mapping(payload.get("per_protocol_sensitivity"), "SAP per protocol")
    _expect(
        _number(pp.get("minimum_assigned_exposure_fraction"), "SAP PP threshold"),
        0.8,
        "SAP PP threshold",
    )
    _expect(pp.get("sensitivity_only"), True, "SAP PP sensitivity")
    _expect(pp.get("primary_claim_allowed"), False, "SAP PP claim")
    _safe_id(
        pp.get("exact_exposure_denominator_and_window_id"),
        "SAP PP exposure definition",
    )

    multiplicity = _mapping(payload.get("multiplicity"), "SAP multiplicity")
    _expect(multiplicity.get("method"), "holm", "SAP Holm")
    _expect(
        _number(multiplicity.get("alpha_two_sided"), "SAP alpha"), 0.05, "SAP alpha"
    )
    _expect(
        tuple(_list(multiplicity.get("families"), "SAP families")),
        ("primary_post", "key_retention", "secondary"),
        "SAP families",
    )
    _expect(
        tuple(_list(multiplicity.get("primary_post_members"), "SAP primary members")),
        PRIMARY_POST,
        "SAP primary members",
    )
    _expect(
        tuple(
            _list(multiplicity.get("key_retention_members"), "SAP retention members")
        ),
        KEY_RETENTION,
        "SAP retention members",
    )
    _expect(
        tuple(_list(multiplicity.get("secondary_family_member_ids"), "SAP secondary")),
        SECONDARY_MEMBERS,
        "SAP secondary members",
    )

    reporting = _mapping(payload.get("aggregate_reporting"), "SAP reporting")
    for field in (
        "missingness_by_arm_time_and_coded_reason_required",
        "withdrawal_by_arm_required",
        "adverse_feedback_by_arm_required",
        "ceiling_and_floor_by_arm_required",
        "model_convergence_and_support_required",
    ):
        _expect(reporting.get(field), True, f"SAP reporting.{field}")
    for field in (
        "participant_rows_or_individual_scores_allowed",
        "audio_transcript_raw_locator_or_free_text_allowed",
    ):
        _expect(reporting.get(field), False, f"SAP reporting.{field}")
    approval = _mapping(payload.get("approval"), "SAP approval")
    _expect(approval.get("decision"), "approved", "SAP approval")
    _sha256(approval.get("statistician_approval_receipt_sha256"), "SAP statistician")
    _sha256(
        approval.get("principal_investigator_approval_receipt_sha256"),
        "SAP PI",
    )
    _expect(approval.get("approved_and_frozen"), True, "SAP approval freeze")
    _expect(payload.get("binding_sha256"), binding_sha256(payload), "SAP binding")


def deep_validate_sap(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    h00: H00Evidence,
    h01: H01Evidence,
    audit: AccessAudit,
) -> SapEvidence:
    section = _mapping(config.get("statistical_analysis_plan"), "SAP")
    expected_sha = _string(section.get("approved_sha256"), "SAP SHA")
    if expected_sha == TBD_SAP:
        raise H04ContractError(
            "H04 SAP is TBD_H04_SAP",
            stage="h04_sap",
            audit=audit,
        )
    _sha256(expected_sha, "SAP SHA")
    audit.sap_path_resolution_count += 1
    audit.sap_read_attempt_count += 1
    try:
        payload, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(section.get("approved_path"), "SAP path"),
            expected_sha256=expected_sha,
            name="H04 frozen SAP",
        )
    except H04ContractError as exc:
        raise H04ContractError(
            f"H04 SAP unavailable: {exc}",
            stage="h04_sap",
            audit=audit,
        ) from exc
    audit.sap_read_count += 1
    validate_sap_payload(payload, h00=h00, h01=h01)
    audit.sap_deep_validation_count += 1
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(snapshot,),
        audit=audit,
        stage="h04_sap",
    )
    return SapEvidence(snapshot.sha256, payload, snapshot)


def _validate_stage_counts(
    value: Mapping[str, Any],
    name: str,
    *,
    expected_per_arm: int,
) -> None:
    _expect_keys(value, STAGE_COUNT_KEYS, name)
    arm_a = _integer(value.get("arm_a"), f"{name} arm A", minimum=1)
    arm_b = _integer(value.get("arm_b"), f"{name} arm B", minimum=1)
    _expect(arm_a, expected_per_arm, f"{name} H01-locked arm A N")
    _expect(arm_b, expected_per_arm, f"{name} H01-locked arm B N")
    randomized = _integer(value.get("randomized_total"), f"{name} total", minimum=2)
    _expect(randomized, arm_a + arm_b, f"{name} randomized arithmetic")
    for field in (
        "pre_observed_participants",
        "post_observed_participants",
        "retention_observed_participants",
    ):
        observed = _integer(value.get(field), f"{name}.{field}", minimum=0)
        if observed > randomized:
            raise H04ContractError(f"{name}.{field} exceeds randomized total")
    _integer(value.get("blind_rating_count"), f"{name} rating count", minimum=1)


def validate_h02_h03_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
    feasibility: FeasibilityEvidence,
    h01: H01Evidence,
    sap: SapEvidence,
) -> None:
    """Validate the A1 completion receipt without resolving its D1 hashes."""

    _reject_nonfinite_tree(payload, "H02/H03 completion")
    _reject_public_leakage(payload, "H02/H03 completion")
    _expect_keys(payload, H02_H03_KEYS, "H02/H03 completion")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h04_h02_h03_blind_bundle_completion.v1",
        "H02/H03 schema",
    )
    if not re.fullmatch(
        r"h0203bundle_[0-9a-f]{32}",
        _string(payload.get("bundle_id"), "H02/H03 bundle ID"),
    ):
        raise H04ContractError("invalid H02/H03 bundle ID")
    _expect(
        payload.get("status"),
        "completed_locked_blind_pre_post_retention",
        "H02/H03 status",
    )
    _expect(
        payload.get("privacy_zone"),
        "A1_aggregate_completion_receipt",
        "H02/H03 zone",
    )
    lineage = _mapping(payload.get("lineage"), "H02/H03 lineage")
    _expect_keys(lineage, H02_LINEAGE_KEYS, "H02/H03 lineage")
    for field, expected in (
        ("h00_approval_sha256", h00.approval_sha256),
        ("h00_attestation_sha256", h00.attestation_sha256),
        ("feasibility_pilot_completion_sha256", feasibility.sha256),
        ("h01_final_sample_size_lock_sha256", h01.sha256),
        ("frozen_sap_sha256", sap.sha256),
    ):
        _expect(lineage.get(field), expected, f"H02/H03 lineage.{field}")
    for field in H02_LINEAGE_KEYS - {
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "feasibility_pilot_completion_sha256",
        "h01_final_sample_size_lock_sha256",
        "frozen_sap_sha256",
    }:
        _sha256(lineage.get(field), f"H02/H03 lineage.{field}")
    completion = _mapping(payload.get("completion"), "H02/H03 completion flags")
    _expect_keys(completion, H02_COMPLETION_KEYS, "H02/H03 completion flags")
    for field in H02_COMPLETION_KEYS - {
        "minimum_independent_raters",
        "participant_blinding_claimed",
    }:
        _expect(completion.get(field), True, f"H02/H03 completion.{field}")
    _expect(
        _integer(
            completion.get("minimum_independent_raters"),
            "H02/H03 minimum raters",
            minimum=2,
        )
        >= 2,
        True,
        "H02/H03 minimum raters",
    )
    _expect(
        completion.get("participant_blinding_claimed"),
        False,
        "H02/H03 participant blinding",
    )
    counts = _mapping(payload.get("counts"), "H02/H03 counts")
    _expect_keys(counts, {"stage_1_c0_vs_c3", "stage_2_c3_vs_c5"}, "H02/H03 counts")
    h01_comparisons = _mapping(h01.payload.get("comparisons"), "H01 comparisons")
    for stage in ("stage_1_c0_vs_c3", "stage_2_c3_vs_c5"):
        h01_stage = _mapping(h01_comparisons.get(stage), f"H01 {stage}")
        _validate_stage_counts(
            _mapping(counts.get(stage), stage),
            stage,
            expected_per_arm=_integer(
                h01_stage.get("final_enrollment_per_arm"),
                f"H01 {stage} per arm",
                minimum=1,
            ),
        )
    receipts = _mapping(payload.get("receipts"), "H02/H03 receipts")
    _expect_keys(receipts, H02_RECEIPT_KEYS, "H02/H03 receipts")
    for field in H02_RECEIPT_KEYS:
        _sha256(receipts.get(field), f"H02/H03 receipt.{field}")
    privacy = _mapping(payload.get("privacy_safety"), "H02/H03 privacy")
    _expect_keys(privacy, H02_PRIVACY_KEYS, "H02/H03 privacy")
    _expect(privacy.get("aggregate_only"), True, "H02/H03 aggregate")
    for field in H02_PRIVACY_KEYS - {"aggregate_only"}:
        _expect(privacy.get(field), False, f"H02/H03 privacy.{field}")
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "H02/H03 binding",
    )


def deep_validate_h02_h03(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    h00: H00Evidence,
    feasibility: FeasibilityEvidence,
    h01: H01Evidence,
    sap: SapEvidence,
    audit: AccessAudit,
) -> H02H03Evidence:
    section = _mapping(config.get("predecessor_h02_h03"), "H02/H03 predecessor")
    expected_sha = _string(section.get("completion_sha256"), "H02/H03 SHA")
    if expected_sha == TBD_H02_H03:
        raise H04ContractError(
            "H02/H03 completion is TBD_H02_H03_COMPLETION",
            stage="h02_h03_completion",
            audit=audit,
        )
    _sha256(expected_sha, "H02/H03 SHA")
    if expected_sha == feasibility.sha256:
        raise H04ContractError(
            "feasibility pilot receipt cannot be reused as confirmatory H02/H03 bundle",
            stage="h02_h03_completion",
            audit=audit,
        )
    audit.h02_h03_completion_path_resolution_count += 1
    audit.h02_h03_completion_read_attempt_count += 1
    try:
        payload, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("completion_path"),
                "H02/H03 completion path",
            ),
            expected_sha256=expected_sha,
            name="H02/H03 completed blind bundle",
        )
    except H04ContractError as exc:
        raise H04ContractError(
            f"H02/H03 completion unavailable: {exc}",
            stage="h02_h03_completion",
            audit=audit,
        ) from exc
    audit.h02_h03_completion_read_count += 1
    validate_h02_h03_payload(
        payload,
        h00=h00,
        feasibility=feasibility,
        h01=h01,
        sap=sap,
    )
    audit.h02_h03_deep_validation_count += 1
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(snapshot,),
        audit=audit,
        stage="h02_h03_completion",
    )
    return H02H03Evidence(snapshot.sha256, payload, snapshot)


def _validate_analyst_structure(payload: Mapping[str, Any]) -> None:
    _reject_nonfinite_tree(payload, "H04 analyst attestation")
    _reject_public_leakage(payload, "H04 analyst attestation")
    _expect_keys(payload, ANALYST_KEYS, "H04 analyst attestation")
    for section, keys in ANALYST_SECTION_KEYS.items():
        _expect_keys(
            _mapping(payload.get(section), f"analyst {section}"),
            keys,
            section,
        )


def validate_analyst_attestation_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
    h01: H01Evidence,
    sap: SapEvidence,
    h02_h03: H02H03Evidence,
) -> None:
    _validate_analyst_structure(payload)
    if _contains_tbd(payload):
        raise H04ContractError("approved analyst attestation contains TBD value")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h04_analyst_attestation.v1",
        "analyst schema",
    )
    if not re.fullmatch(
        r"h04analyst_[0-9a-f]{32}",
        _string(payload.get("attestation_id"), "analyst attestation ID"),
    ):
        raise H04ContractError("invalid H04 analyst attestation ID")
    _expect(
        payload.get("status"),
        "attested_for_locked_h04_analysis",
        "analyst status",
    )
    _expect(
        payload.get("privacy_zone"),
        "A1_C1_public_attestation_no_identity",
        "analyst zone",
    )
    _sha256(payload.get("analyst_identity_sha256"), "analyst identity hash")
    _safe_id(payload.get("analyst_role_id"), "analyst role")
    _utc(payload.get("attested_at_utc"), "analyst attested at")
    lineage = _mapping(payload.get("lineage"), "analyst lineage")
    for field, expected in (
        ("h00_approval_sha256", h00.approval_sha256),
        ("h00_attestation_sha256", h00.attestation_sha256),
        ("h01_final_sample_size_lock_sha256", h01.sha256),
        ("h02_h03_completion_sha256", h02_h03.sha256),
        ("frozen_sap_sha256", sap.sha256),
    ):
        _expect(lineage.get(field), expected, f"analyst lineage.{field}")
    blinding = _mapping(payload.get("blinding_and_order"), "analyst blinding")
    for field in ANALYST_SECTION_KEYS["blinding_and_order"]:
        _expect(blinding.get(field), True, f"analyst blinding.{field}")
    commitments = _mapping(payload.get("analysis_commitments"), "analyst commitments")
    for field in ANALYST_SECTION_KEYS["analysis_commitments"] - {
        "statistics_execution_before_full_authorization_allowed",
        "last_observation_carried_forward_used",
        "post_randomization_exclusion_used",
    }:
        _expect(commitments.get(field), True, f"analyst commitment.{field}")
    for field in (
        "statistics_execution_before_full_authorization_allowed",
        "last_observation_carried_forward_used",
        "post_randomization_exclusion_used",
    ):
        _expect(commitments.get(field), False, f"analyst commitment.{field}")
    reproducibility = _mapping(
        payload.get("reproducibility"), "analyst reproducibility"
    )
    for field in ANALYST_SECTION_KEYS["reproducibility"]:
        _sha256(reproducibility.get(field), f"analyst reproducibility.{field}")
    privacy = _mapping(payload.get("privacy_assurance"), "analyst privacy")
    _expect(privacy.get("aggregate_only"), True, "analyst aggregate")
    for field in ANALYST_SECTION_KEYS["privacy_assurance"] - {"aggregate_only"}:
        _expect(privacy.get(field), False, f"analyst privacy.{field}")
    _sha256(
        payload.get("signed_attestation_receipt_sha256"),
        "analyst signed receipt",
    )
    _expect(payload.get("binding_sha256"), binding_sha256(payload), "analyst binding")


def deep_validate_analyst(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    h00: H00Evidence,
    h01: H01Evidence,
    sap: SapEvidence,
    h02_h03: H02H03Evidence,
    audit: AccessAudit,
) -> AnalystEvidence:
    section = _mapping(config.get("analyst_attestation"), "analyst attestation")
    expected_sha = _string(section.get("approved_sha256"), "analyst SHA")
    if expected_sha == TBD_ANALYST:
        raise H04ContractError(
            "H04 analyst attestation is TBD_H04_ANALYST_ATTESTATION",
            stage="h04_analyst_attestation",
            audit=audit,
        )
    _sha256(expected_sha, "analyst SHA")
    audit.analyst_attestation_path_resolution_count += 1
    audit.analyst_attestation_read_attempt_count += 1
    try:
        payload, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(section.get("approved_path"), "analyst path"),
            expected_sha256=expected_sha,
            name="H04 analyst attestation",
        )
    except H04ContractError as exc:
        raise H04ContractError(
            f"H04 analyst attestation unavailable: {exc}",
            stage="h04_analyst_attestation",
            audit=audit,
        ) from exc
    audit.analyst_attestation_read_count += 1
    validate_analyst_attestation_payload(
        payload,
        h00=h00,
        h01=h01,
        sap=sap,
        h02_h03=h02_h03,
    )
    audit.analyst_attestation_deep_validation_count += 1
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(snapshot,),
        audit=audit,
        stage="h04_analyst_attestation",
    )
    return AnalystEvidence(snapshot.sha256, payload, snapshot)


def _stage_counts_without_arms(
    value: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    name: str,
) -> None:
    _expect_keys(value, INPUT_STAGE_COUNT_KEYS, name)
    for field in INPUT_STAGE_COUNT_KEYS:
        actual = _integer(value.get(field), f"{name}.{field}", minimum=0)
        _expect(actual, source.get(field), f"{name}.{field} binding")


def validate_aggregate_input_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
    h01: H01Evidence,
    sap: SapEvidence,
    h02_h03: H02H03Evidence,
    analyst: AnalystEvidence,
) -> None:
    """Validate a score/effect-free A1 manifest without reading D1 data."""

    _reject_nonfinite_tree(payload, "H04 aggregate input")
    _reject_public_leakage(payload, "H04 aggregate input")
    _expect_keys(payload, INPUT_KEYS, "H04 aggregate input")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h04_learning_effect_aggregate_input.v1",
        "H04 input schema",
    )
    if not re.fullmatch(
        r"h04input_[0-9a-f]{32}",
        _string(payload.get("input_id"), "H04 input ID"),
    ):
        raise H04ContractError("invalid H04 aggregate input ID")
    _expect(
        payload.get("status"),
        "locked_aggregate_manifest_for_authorized_analysis",
        "H04 input status",
    )
    _expect(
        payload.get("privacy_zone"),
        "A1_aggregate_manifest_no_participant_rows",
        "H04 input zone",
    )
    lineage = _mapping(payload.get("lineage"), "H04 input lineage")
    _expect_keys(lineage, INPUT_LINEAGE_KEYS, "H04 input lineage")
    for field, expected in (
        ("h00_approval_sha256", h00.approval_sha256),
        ("h00_attestation_sha256", h00.attestation_sha256),
        ("h01_final_sample_size_lock_sha256", h01.sha256),
        ("h02_h03_completion_sha256", h02_h03.sha256),
        ("frozen_sap_sha256", sap.sha256),
        ("analyst_attestation_sha256", analyst.sha256),
    ):
        _expect(lineage.get(field), expected, f"H04 input lineage.{field}")
    design = _mapping(payload.get("design_contract"), "H04 input design")
    _expect_keys(design, INPUT_DESIGN_KEYS, "H04 input design")
    expected_design: dict[str, object] = {
        "comparisons": list(COMPARISONS),
        "timepoints": list(TIMEPOINTS),
        "primary_outcome": "blind_expert_comprehensibility_1_to_5",
        "primary_population": "intention_to_treat_as_randomized",
        "fixed_effect_formula": "score~condition*time+l1+baseline",
        "random_intercepts": list(RANDOM_INTERCEPTS),
    }
    for field, expected_value in expected_design.items():
        _expect(design.get(field), expected_value, f"H04 input design.{field}")
    counts = _mapping(payload.get("counts"), "H04 input counts")
    _expect_keys(counts, {"stage_1_c0_vs_c3", "stage_2_c3_vs_c5"}, "input counts")
    source_counts = _mapping(h02_h03.payload.get("counts"), "H02/H03 counts")
    for stage in ("stage_1_c0_vs_c3", "stage_2_c3_vs_c5"):
        _stage_counts_without_arms(
            _mapping(counts.get(stage), f"input {stage}"),
            source=_mapping(source_counts.get(stage), f"source {stage}"),
            name=f"input {stage}",
        )
    receipts = _mapping(payload.get("receipt_hashes"), "H04 input receipts")
    _expect_keys(receipts, INPUT_RECEIPT_KEYS, "H04 input receipts")
    source_receipts = _mapping(h02_h03.payload.get("receipts"), "H02/H03 receipts")
    for field in H02_RECEIPT_KEYS:
        _expect(
            receipts.get(field), source_receipts.get(field), f"input receipt.{field}"
        )
    analyst_repro = _mapping(
        analyst.payload.get("reproducibility"),
        "analyst reproducibility",
    )
    for field in ("analysis_code_sha256", "software_environment_lock_sha256"):
        _expect(receipts.get(field), analyst_repro.get(field), f"input receipt.{field}")
    authorization = _mapping(
        payload.get("analysis_authorization"), "input authorization"
    )
    _expect_keys(authorization, INPUT_AUTHORIZATION_KEYS, "input authorization")
    for field in INPUT_AUTHORIZATION_KEYS - {
        "restricted_d1_row_access_allowed_by_this_static_package",
        "statistics_execution_allowed_by_this_static_package",
    }:
        _expect(authorization.get(field), True, f"input authorization.{field}")
    for field in (
        "restricted_d1_row_access_allowed_by_this_static_package",
        "statistics_execution_allowed_by_this_static_package",
    ):
        _expect(authorization.get(field), False, f"input authorization.{field}")
    privacy = _mapping(payload.get("privacy_safety"), "input privacy")
    _expect_keys(privacy, INPUT_PRIVACY_KEYS, "input privacy")
    _expect(privacy.get("aggregate_only"), True, "input aggregate")
    for field in INPUT_PRIVACY_KEYS - {"aggregate_only"}:
        _expect(privacy.get(field), False, f"input privacy.{field}")
    _expect(payload.get("binding_sha256"), binding_sha256(payload), "input binding")


def _validate_interval(value: Mapping[str, Any], name: str) -> None:
    _expect_keys(value, INTERVAL_KEYS, name)
    estimate = _number(value.get("estimate"), f"{name} estimate")
    lower = _number(value.get("ci_lower"), f"{name} CI lower")
    upper = _number(value.get("ci_upper"), f"{name} CI upper")
    if lower > estimate or estimate > upper:
        raise H04ContractError(f"{name} estimate must lie inside CI")


def _validate_hypothesis(
    value: Mapping[str, Any],
    name: str,
) -> tuple[Decimal, Decimal]:
    _expect_keys(value, HYPOTHESIS_KEYS, name)
    _expect(value.get("two_sided"), True, f"{name} sidedness")
    raw = _decimal_probability(value.get("unadjusted_p_value"), f"{name} raw p")
    adjusted = _decimal_probability(
        value.get("holm_adjusted_p_value"),
        f"{name} Holm p",
    )
    if adjusted < raw:
        raise H04ContractError(f"{name} Holm p cannot be smaller than raw p")
    _expect(
        value.get("reject_at_familywise_0_05"),
        adjusted <= Decimal("0.05"),
        f"{name} rejection",
    )
    return raw, adjusted


def _holm_adjusted(raw: Mapping[str, Decimal]) -> dict[str, Decimal]:
    ordered = sorted(raw.items(), key=lambda item: (item[1], item[0]))
    total = len(ordered)
    running = Decimal("0")
    result: dict[str, Decimal] = {}
    for index, (test_id, p_value) in enumerate(ordered):
        candidate = min(Decimal("1"), Decimal(total - index) * p_value)
        running = max(running, candidate)
        result[test_id] = running
    return result


def _validate_holm(
    observed: Mapping[str, tuple[Decimal, Decimal]],
    name: str,
) -> None:
    expected = _holm_adjusted({key: pair[0] for key, pair in observed.items()})
    for test_id, (_, adjusted) in observed.items():
        if adjusted != expected[test_id]:
            raise H04ContractError(
                f"{name} Holm adjustment mismatch for {test_id}: "
                f"expected {expected[test_id]}, got {adjusted}"
            )


def validate_statistical_report_payload(
    payload: Mapping[str, Any],
    *,
    aggregate_input: Mapping[str, Any],
    h00: H00Evidence,
    h01: H01Evidence,
    sap: SapEvidence,
    h02_h03: H02H03Evidence,
    analyst: AnalystEvidence,
) -> None:
    """Validate an actual A1 report, including exact Holm recomputation."""

    _reject_nonfinite_tree(payload, "H04 report")
    _reject_public_leakage(payload, "H04 report")
    _expect_keys(payload, REPORT_KEYS, "H04 report")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h04_learning_effect_statistical_report.v1",
        "H04 report schema",
    )
    if not re.fullmatch(
        r"h04report_[0-9a-f]{32}",
        _string(payload.get("report_id"), "H04 report ID"),
    ):
        raise H04ContractError("invalid H04 report ID")
    _expect(
        payload.get("status"),
        "completed_aggregate_statistical_analysis",
        "H04 report status",
    )
    _expect(
        payload.get("privacy_zone"),
        "A1_aggregate_statistical_report",
        "H04 report zone",
    )
    lineage = _mapping(payload.get("lineage"), "H04 report lineage")
    _expect_keys(lineage, REPORT_LINEAGE_KEYS, "H04 report lineage")
    input_sha = hashlib.sha256(canonical_json_bytes(aggregate_input)).hexdigest()
    input_lineage = _mapping(aggregate_input.get("lineage"), "input lineage")
    input_receipts = _mapping(aggregate_input.get("receipt_hashes"), "input receipts")
    expected_lineage = {
        "aggregate_input_sha256": input_sha,
        "h00_approval_sha256": h00.approval_sha256,
        "h00_attestation_sha256": h00.attestation_sha256,
        "h01_final_sample_size_lock_sha256": h01.sha256,
        "h02_h03_completion_sha256": h02_h03.sha256,
        "frozen_sap_sha256": sap.sha256,
        "analyst_attestation_sha256": analyst.sha256,
        "analysis_code_sha256": input_receipts["analysis_code_sha256"],
        "software_environment_lock_sha256": input_receipts[
            "software_environment_lock_sha256"
        ],
    }
    del input_lineage
    for field, expected in expected_lineage.items():
        _expect(lineage.get(field), expected, f"report lineage.{field}")

    analysis = _mapping(payload.get("analysis_contract"), "report analysis")
    _expect_keys(analysis, REPORT_ANALYSIS_KEYS, "report analysis")
    sap_model = _mapping(sap.payload.get("primary_model"), "SAP model")
    expected_analysis = {
        "primary_population": "intention_to_treat_as_randomized",
        "primary_estimator": "likelihood_based_mixed_model_all_observed_itt",
        "fixed_effect_formula": "score~condition*time+l1+baseline",
        "random_intercepts": list(RANDOM_INTERCEPTS),
        "model_family_id": sap_model["model_family_id"],
        "ordinal_or_linear_choice_id": sap_model["ordinal_or_linear_choice_id"],
        "outcome_support_id": sap_model["outcome_support_id"],
        "software_id": sap_model["software_and_version_id"],
        "optimizer_id": sap_model["optimizer_id"],
        "convergence_rule_id": sap_model["convergence_rule_id"],
        "ci_method_id": sap_model["ci_method_id"],
        "multiplicity_method": "holm_within_each_registered_family",
        "alpha_two_sided": 0.05,
    }
    for field, expected in expected_analysis.items():
        _expect(analysis.get(field), expected, f"report analysis.{field}")

    primary_rows = [
        _mapping(item, f"confirmatory result {index}")
        for index, item in enumerate(
            _list(
                payload.get("primary_and_retention_results"),
                "confirmatory results",
            )
        )
    ]
    if len(primary_rows) != 4:
        raise H04ContractError("exactly four confirmatory results required")
    observed_ids: set[str] = set()
    family_p: dict[str, dict[str, tuple[Decimal, Decimal]]] = {
        "primary_post": {},
        "key_retention": {},
    }
    expected_meta = {
        "c3_minus_c0_pre_to_post": ("primary_post", "c3_minus_c0", "pre_to_post"),
        "c5_minus_c3_pre_to_post": ("primary_post", "c5_minus_c3", "pre_to_post"),
        "c3_minus_c0_pre_to_retention": (
            "key_retention",
            "c3_minus_c0",
            "pre_to_retention",
        ),
        "c5_minus_c3_pre_to_retention": (
            "key_retention",
            "c5_minus_c3",
            "pre_to_retention",
        ),
    }
    for row in primary_rows:
        _expect_keys(row, CONFIRMATORY_RESULT_KEYS, "confirmatory result")
        contrast_id = _string(row.get("contrast_id"), "confirmatory contrast ID")
        if contrast_id in observed_ids or contrast_id not in expected_meta:
            raise H04ContractError(
                f"duplicate/unknown confirmatory result: {contrast_id}"
            )
        observed_ids.add(contrast_id)
        family, comparison, time_comparison = expected_meta[contrast_id]
        _expect(row.get("family"), family, f"{contrast_id} family")
        _expect(row.get("comparison"), comparison, f"{contrast_id} comparison")
        _expect(row.get("time_comparison"), time_comparison, f"{contrast_id} time")
        _expect(
            row.get("outcome"),
            "blind_expert_comprehensibility_1_to_5",
            f"{contrast_id} outcome",
        )
        _validate_interval(
            _mapping(row.get("raw_1_to_5_difference"), f"{contrast_id} raw"),
            f"{contrast_id} raw",
        )
        _validate_interval(
            _mapping(row.get("standardized_effect"), f"{contrast_id} standardized"),
            f"{contrast_id} standardized",
        )
        family_p[family][contrast_id] = _validate_hypothesis(
            _mapping(row.get("hypothesis"), f"{contrast_id} hypothesis"),
            f"{contrast_id} hypothesis",
        )
    _expect(observed_ids, set(ALL_CONFIRMATORY), "confirmatory result set")
    for family, values in family_p.items():
        _validate_holm(values, family)

    secondary_rows = [
        _mapping(item, f"secondary result {index}")
        for index, item in enumerate(
            _list(payload.get("secondary_family_results"), "secondary results")
        )
    ]
    expected_secondary = {
        (test_id, comparison)
        for test_id in SECONDARY_MEMBERS
        for comparison in COMPARISONS
    }
    observed_secondary: set[tuple[str, str]] = set()
    secondary_p: dict[str, tuple[Decimal, Decimal]] = {}
    outcome_time = {
        "phone_accuracy_post": ("blind_expert_phone_accuracy", "post"),
        "phone_accuracy_retention": (
            "blind_expert_phone_accuracy",
            "retention",
        ),
        "fluency_post": ("blind_expert_fluency", "post"),
        "fluency_retention": ("blind_expert_fluency", "retention"),
    }
    for row in secondary_rows:
        _expect_keys(row, SECONDARY_RESULT_KEYS, "secondary result")
        test_id = _string(row.get("test_id"), "secondary test ID")
        comparison = _string(row.get("comparison"), "secondary comparison")
        key = (test_id, comparison)
        if key in observed_secondary or key not in expected_secondary:
            raise H04ContractError(f"duplicate/unknown secondary result: {key}")
        observed_secondary.add(key)
        _expect(row.get("family"), "secondary", f"{key} family")
        expected_outcome, expected_time = outcome_time[test_id]
        _expect(row.get("outcome"), expected_outcome, f"{key} outcome")
        _expect(row.get("timepoint"), expected_time, f"{key} time")
        _validate_interval(
            _mapping(row.get("effect"), f"{key} effect"), f"{key} effect"
        )
        secondary_p[f"{test_id}:{comparison}"] = _validate_hypothesis(
            _mapping(row.get("hypothesis"), f"{key} hypothesis"),
            f"{key} hypothesis",
        )
    _expect(observed_secondary, expected_secondary, "secondary result set")
    _validate_holm(secondary_p, "secondary")

    sensitivities = [
        _mapping(item, f"sensitivity {index}")
        for index, item in enumerate(
            _list(payload.get("sensitivity_analyses"), "sensitivities")
        )
    ]
    observed_methods: set[str] = set()
    for sensitivity in sensitivities:
        _expect_keys(sensitivity, SENSITIVITY_KEYS, "sensitivity")
        method = _string(sensitivity.get("method"), "sensitivity method")
        if method in observed_methods or method not in SENSITIVITY_METHODS:
            raise H04ContractError(f"duplicate/unknown sensitivity: {method}")
        observed_methods.add(method)
        _sha256(sensitivity.get("parameters_sha256"), f"{method} parameters")
        expected_population = (
            "per_protocol_sensitivity_only"
            if method == "per_protocol_at_least_80_percent_exposure"
            else "itt_sensitivity"
        )
        _expect(
            sensitivity.get("analysis_population"),
            expected_population,
            f"{method} population",
        )
        rows = [
            _mapping(item, f"{method} result {index}")
            for index, item in enumerate(
                _list(sensitivity.get("results"), f"{method} results")
            )
        ]
        result_ids: set[str] = set()
        for row in rows:
            _expect_keys(row, SENSITIVITY_ESTIMATE_KEYS, f"{method} result")
            contrast_id = _string(row.get("contrast_id"), f"{method} contrast")
            if contrast_id in result_ids or contrast_id not in ALL_CONFIRMATORY:
                raise H04ContractError(f"{method} duplicate/unknown contrast")
            result_ids.add(contrast_id)
            estimate = _number(row.get("estimate"), f"{method} estimate")
            lower = _number(row.get("ci_lower"), f"{method} lower")
            upper = _number(row.get("ci_upper"), f"{method} upper")
            if lower > estimate or estimate > upper:
                raise H04ContractError(f"{method} estimate outside CI")
        _expect(result_ids, set(ALL_CONFIRMATORY), f"{method} result set")
    _expect(observed_methods, set(SENSITIVITY_METHODS), "sensitivity set")

    diagnostics = _mapping(payload.get("model_diagnostics"), "model diagnostics")
    _expect_keys(diagnostics, MODEL_DIAGNOSTIC_KEYS, "model diagnostics")
    _expect(diagnostics.get("primary_model_converged"), True, "model convergence")
    _expect(diagnostics.get("optimizer_converged"), True, "optimizer convergence")
    _expect(diagnostics.get("singular_or_boundary_fit"), False, "singular fit")
    _expect(
        _integer(
            diagnostics.get("support_violations"), "support violations", minimum=0
        ),
        0,
        "support violations",
    )
    _expect(diagnostics.get("fallback_model_used"), False, "fallback model")
    _sha256(diagnostics.get("convergence_diagnostics_sha256"), "convergence receipt")
    _sha256(
        diagnostics.get("bootstrap_or_ci_diagnostics_sha256"),
        "CI receipt",
    )

    disposition = _mapping(
        payload.get("missingness_and_safety"),
        "missingness and safety",
    )
    _expect_keys(disposition, MISSINGNESS_SAFETY_KEYS, "missingness and safety")
    confirmatory_counts = _mapping(h02_h03.payload.get("counts"), "H02/H03 counts")
    expected_arm_denominators: dict[tuple[str, str], int] = {}
    for stage, comparison, conditions in (
        ("stage_1_c0_vs_c3", "c3_minus_c0", ("C0", "C3")),
        ("stage_2_c3_vs_c5", "c5_minus_c3", ("C3", "C5")),
    ):
        stage_counts = _mapping(confirmatory_counts.get(stage), f"H02/H03 {stage}")
        expected_arm_denominators[(comparison, conditions[0])] = _integer(
            stage_counts.get("arm_a"),
            f"H02/H03 {stage} arm A",
            minimum=1,
        )
        expected_arm_denominators[(comparison, conditions[1])] = _integer(
            stage_counts.get("arm_b"),
            f"H02/H03 {stage} arm B",
            minimum=1,
        )
    expected_missing_cells = {
        (comparison, condition, timepoint)
        for comparison, condition in expected_arm_denominators
        for timepoint in ("pre", "post", "retention")
    }
    observed_missing_cells: set[tuple[str, str, str]] = set()
    for raw in _list(
        disposition.get("missingness_by_arm_and_time"), "missingness cells"
    ):
        cell = _mapping(raw, "missingness cell")
        _expect_keys(cell, MISSINGNESS_CELL_KEYS, "missingness cell")
        comparison = _string(cell.get("comparison"), "missing comparison")
        condition = _string(cell.get("condition"), "missing condition")
        timepoint = _string(cell.get("timepoint"), "missing timepoint")
        missing_cell_key = (comparison, condition, timepoint)
        if (
            missing_cell_key not in expected_missing_cells
            or missing_cell_key in observed_missing_cells
        ):
            raise H04ContractError(
                f"duplicate/unknown missingness cell: {missing_cell_key}"
            )
        observed_missing_cells.add(missing_cell_key)
        denominator = _integer(
            cell.get("denominator"), "missing denominator", minimum=1
        )
        _expect(
            denominator,
            expected_arm_denominators[(comparison, condition)],
            "missing denominator H02/H03 binding",
        )
        missing = _integer(cell.get("missing_count"), "missing count", minimum=0)
        if missing > denominator:
            raise H04ContractError("missing count exceeds denominator")
        fraction = _decimal_probability(
            cell.get("missing_fraction"), "missing fraction"
        )
        if fraction != Decimal(missing) / Decimal(denominator):
            raise H04ContractError("missing fraction arithmetic mismatch")
        _sha256(cell.get("coded_reason_summary_sha256"), "missing reason receipt")
    _expect(
        observed_missing_cells,
        expected_missing_cells,
        "missingness arm/time coverage",
    )
    for field in (
        "withdrawal_by_arm",
        "adverse_feedback_by_arm",
        "ceiling_by_arm",
        "floor_by_arm",
    ):
        observed_count_cells: set[tuple[str, str]] = set()
        for raw in _list(disposition.get(field), field):
            cell = _mapping(raw, field)
            _expect_keys(cell, COUNT_CELL_KEYS, field)
            comparison = _string(cell.get("comparison"), f"{field} comparison")
            condition = _string(cell.get("condition"), f"{field} condition")
            arm_cell_key = (comparison, condition)
            if (
                arm_cell_key not in expected_arm_denominators
                or arm_cell_key in observed_count_cells
            ):
                raise H04ContractError(
                    f"{field} duplicate/unknown arm cell: {arm_cell_key}"
                )
            observed_count_cells.add(arm_cell_key)
            denominator = _integer(
                cell.get("denominator"), f"{field} denominator", minimum=1
            )
            _expect(
                denominator,
                expected_arm_denominators[arm_cell_key],
                f"{field} denominator H02/H03 binding",
            )
            count = _integer(cell.get("count"), f"{field} count", minimum=0)
            if count > denominator:
                raise H04ContractError(f"{field} count exceeds denominator")
            fraction = _decimal_probability(cell.get("fraction"), f"{field} fraction")
            if fraction != Decimal(count) / Decimal(denominator):
                raise H04ContractError(f"{field} fraction arithmetic mismatch")
        _expect(
            observed_count_cells,
            set(expected_arm_denominators),
            f"{field} arm coverage",
        )
    assurance = _mapping(payload.get("reporting_assurance"), "report assurance")
    _expect_keys(assurance, REPORTING_ASSURANCE_KEYS, "report assurance")
    for field in REPORTING_ASSURANCE_KEYS - {
        "last_observation_carried_forward_used",
        "post_randomization_exclusion_used",
        "participant_rows_present",
        "individual_scores_present",
        "audio_or_transcript_present",
        "raw_locator_present",
        "free_text_present",
        "publication_allowed",
    }:
        _expect(assurance.get(field), True, f"report assurance.{field}")
    for field in (
        "last_observation_carried_forward_used",
        "post_randomization_exclusion_used",
        "participant_rows_present",
        "individual_scores_present",
        "audio_or_transcript_present",
        "raw_locator_present",
        "free_text_present",
        "publication_allowed",
    ):
        _expect(assurance.get(field), False, f"report assurance.{field}")
    _expect(payload.get("binding_sha256"), binding_sha256(payload), "report binding")


def validate_completion_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
    h01: H01Evidence,
    sap: SapEvidence,
    h02_h03: H02H03Evidence,
    analyst: AnalystEvidence,
    aggregate_input_sha256: str,
    aggregate_input_size: int,
    report_sha256: str,
    report_size: int,
) -> None:
    _reject_nonfinite_tree(payload, "H04 completion")
    _reject_public_leakage(payload, "H04 completion")
    _expect_keys(payload, COMPLETION_KEYS, "H04 completion")
    _expect(
        payload.get("schema_version"),
        "voice_coach.h04_learning_effect_completion.v1",
        "completion schema",
    )
    if not re.fullmatch(
        r"h04commit_[0-9a-f]{32}",
        _string(payload.get("commit_id"), "completion ID"),
    ):
        raise H04ContractError("invalid H04 completion ID")
    _expect(payload.get("status"), "committed", "completion status")
    _expect(
        payload.get("privacy_zone"),
        "A1_aggregate_completion_receipt",
        "completion zone",
    )
    lineage = _mapping(payload.get("lineage"), "completion lineage")
    _expect_keys(lineage, COMPLETION_LINEAGE_KEYS, "completion lineage")
    for field, expected in (
        ("h00_approval_sha256", h00.approval_sha256),
        ("h00_attestation_sha256", h00.attestation_sha256),
        ("h01_final_sample_size_lock_sha256", h01.sha256),
        ("h02_h03_completion_sha256", h02_h03.sha256),
        ("frozen_sap_sha256", sap.sha256),
        ("analyst_attestation_sha256", analyst.sha256),
    ):
        _expect(lineage.get(field), expected, f"completion lineage.{field}")
    artifacts = _mapping(payload.get("artifacts"), "completion artifacts")
    _expect_keys(artifacts, COMPLETION_ARTIFACT_KEYS, "completion artifacts")
    expected_artifacts = {
        "aggregate_input": (
            _sha256(aggregate_input_sha256, "aggregate input SHA"),
            _integer(aggregate_input_size, "aggregate input size", minimum=1),
        ),
        "statistical_report": (
            _sha256(report_sha256, "report SHA"),
            _integer(report_size, "report size", minimum=1),
        ),
    }
    for name, (sha, size) in expected_artifacts.items():
        artifact = _mapping(artifacts.get(name), f"completion {name}")
        _expect_keys(artifact, ARTIFACT_KEYS, f"completion {name}")
        _expect(artifact.get("sha256"), sha, f"completion {name} SHA")
        _expect(artifact.get("size_bytes"), size, f"completion {name} size")
    assurance = _mapping(payload.get("assurance"), "completion assurance")
    _expect_keys(assurance, COMPLETION_ASSURANCE_KEYS, "completion assurance")
    for field in COMPLETION_ASSURANCE_KEYS - {
        "last_observation_carried_forward_used",
        "post_randomization_exclusion_used",
        "participant_or_rating_rows_present",
        "audio_or_transcript_present",
        "raw_locator_present",
        "free_text_present",
        "model_gpu_network_used_by_static_package",
        "publication_allowed",
    }:
        _expect(assurance.get(field), True, f"completion assurance.{field}")
    for field in (
        "last_observation_carried_forward_used",
        "post_randomization_exclusion_used",
        "participant_or_rating_rows_present",
        "audio_or_transcript_present",
        "raw_locator_present",
        "free_text_present",
        "model_gpu_network_used_by_static_package",
        "publication_allowed",
    ):
        _expect(assurance.get(field), False, f"completion assurance.{field}")
    _expect(
        payload.get("binding_sha256"), binding_sha256(payload), "completion binding"
    )


def ensure_future_output_targets_absent(
    *,
    workspace_root: Path,
    outputs: Mapping[str, Any],
) -> None:
    """Enforce no-overwrite without resolving runtime D1 inputs."""

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
            raise H04ContractError("duplicate future H04 output target")
        seen.add(relative)
        target = _reject_symlink_chain(
            workspace_root,
            relative,
            f"outputs.{field}",
            require_leaf=False,
        )
        if target.exists() or target.is_symlink():
            raise H04ContractError(f"no-overwrite target already exists: {relative}")


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Validate predecessors in order and stop before participant/statistics work."""

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
    h00 = deep_validate_h00(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    feasibility = deep_validate_feasibility_pilot(
        workspace_root=workspace_root,
        config=config,
        h00=h00,
        audit=audit,
    )
    h01 = deep_validate_h01(
        workspace_root=workspace_root,
        config=config,
        h00=h00,
        feasibility=feasibility,
        audit=audit,
    )
    sap = deep_validate_sap(
        workspace_root=workspace_root,
        config=config,
        h00=h00,
        h01=h01,
        audit=audit,
    )
    h02_h03 = deep_validate_h02_h03(
        workspace_root=workspace_root,
        config=config,
        h00=h00,
        feasibility=feasibility,
        h01=h01,
        sap=sap,
        audit=audit,
    )
    analyst = deep_validate_analyst(
        workspace_root=workspace_root,
        config=config,
        h00=h00,
        h01=h01,
        sap=sap,
        h02_h03=h02_h03,
        audit=audit,
    )
    return PreflightResult(
        status="ready_for_separately_authorized_h04_restricted_statistical_executor",
        config_sha256=static.config_sha256,
        h00_approval_sha256=h00.approval_sha256,
        h00_attestation_sha256=h00.attestation_sha256,
        feasibility_pilot_completion_sha256=feasibility.sha256,
        h01_completion_sha256=h01.sha256,
        sap_sha256=sap.sha256,
        h02_h03_completion_sha256=h02_h03.sha256,
        analyst_attestation_sha256=analyst.sha256,
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
        "actual_participant_rating_audio_or_restricted_data_access": False,
        "actual_model_gpu_network_or_statistics_execution": False,
        "actual_effect_estimate_or_report": False,
        "artifact_created": False,
        "h04_completion_claim_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: H04ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "actual_participant_rating_audio_or_restricted_data_access": False,
        "actual_model_gpu_network_or_statistics_execution": False,
        "actual_effect_estimate_or_report": False,
        "artifact_created": False,
        "h04_completion_claim_allowed": False,
        "publication_allowed": False,
    }
