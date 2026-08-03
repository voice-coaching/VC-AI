"""Fail-closed D09/H02/H03 longitudinal human-research readiness contract.

The module validates public/static policy and aggregate authorization receipts.
It never recruits, consents, randomizes, records audio, invokes coaching or
reference systems, delivers an intervention, reads participant/rating rows,
computes a metric, writes an artifact, or claims research completion.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
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
TBD_DELIVERY = "TBD_DELIVERY_PREDECESSORS"
TBD_FEASIBILITY = "TBD_H02_H03_FEASIBILITY_PILOT"
TBD_H01 = "TBD_H01_SAMPLE_SIZE_LOCK"
TBD_SAP = "TBD_H04_SAP"
TBD_CONFIRMATORY = "TBD_H02_H03_COMPLETION"
TBD_SEPARATION = "TBD_PHASE_SEPARATION"
TBD_FEASIBILITY_COMPARISON = "TBD_D09_FEASIBILITY_COMPARISON_STAGE_SEPARATION"
TBD_CONFIRMATORY_COMPARISON = "TBD_D09_CONFIRMATORY_COMPARISON_STAGE_SEPARATION"
ZERO_SHA256 = "0" * 64
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
UTC_RE = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})Z$"
)
PHASES = ("feasibility", "confirmatory")
COMPARISON_ARMS = {
    "c3_minus_c0": ("C0", "C3"),
    "c5_minus_c3": ("C3", "C5"),
}
TIMEPOINTS = ("pre", "post_at_2_weeks", "retention_4_weeks_after_post")

SOURCE_PATHS = (
    "configs/human_research/d09_h02_h03_comparison_stage_separation_receipt_v1.schema.json",
    "configs/human_research/d09_h02_h03_audio_qc_v1.schema.json",
    "configs/human_research/d09_h02_h03_audio_v1.schema.json",
    "configs/human_research/d09_h02_h03_blind_rating_v1.schema.json",
    "configs/human_research/d09_h02_h03_delivery_predecessor_receipt_v1.schema.json",
    "configs/human_research/d09_h02_h03_disposition_v1.schema.json",
    "configs/human_research/d09_h02_h03_execution_authorization_v1.template.json",
    "configs/human_research/d09_h02_h03_intervention_exposure_v1.schema.json",
    "configs/human_research/d09_h02_h03_intervention_manual_v1.template.json",
    "configs/human_research/d09_h02_h03_operational_preregistration_v1.template.json",
    "configs/human_research/d09_h02_h03_participant_v1.schema.json",
    "configs/human_research/d09_h02_h03_phase_separation_receipt_v1.schema.json",
    "configs/human_research/d09_h02_h03_randomization_v1.schema.json",
    "configs/human_research/d09_h02_h03_session_v1.schema.json",
    "configs/human_research/h04_feasibility_pilot_completion_v1.schema.json",
    "configs/human_research/h04_h01_final_sample_size_lock_v1.schema.json",
    "configs/human_research/h04_h02_h03_blind_bundle_completion_v1.schema.json",
    "configs/human_research/h04_statistical_analysis_plan_v1.template.json",
    "configs/experiments/h04_learning_effect_analysis_v1.yaml",
    "src/voice_coach/human_research/h04_learning_effect_contract.py",
    "configs/experiments/h00_human_research_approval_v1.yaml",
    "src/voice_coach/human_research/h00_approval_contract.py",
    "configs/experiments/wavlm_s108_no_locked_dry_run_v1.yaml",
    "src/voice_coach/training/wavlm_s108_dry_run.py",
    "configs/experiments/e01_metamorphic_robustness_v1.yaml",
    "src/voice_coach/evaluation/e01_metamorphic_contract.py",
    "configs/experiments/c02_deterministic_coaching_selection_v1.yaml",
    "src/voice_coach/coaching/c02_selection_contract.py",
    "configs/experiments/e02_confidence_guard_v1.yaml",
    "src/voice_coach/evaluation/e02_confidence_guard_contract.py",
    "configs/experiments/c03_expert_coaching_audit_v1.yaml",
    "src/voice_coach/coaching/c03_expert_audit_contract.py",
    "configs/experiments/e00_closed_loop_demo_v1.yaml",
    "src/voice_coach/evaluation/e00_closed_loop_contract.py",
    "configs/experiments/r03_reference_bank_selection_v1.yaml",
    "src/voice_coach/references/r03_selection_contract.py",
    "configs/experiments/r04_blind_multireference_evaluation_v1.yaml",
    "src/voice_coach/references/r04_blind_evaluation_contract.py",
    "configs/experiments/learning_effect_power_v1.yaml",
)

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_h00",
    "h04_handoff",
    "comparison_stage_separation",
    "delivery_predecessors",
    "data_contract",
    "operational_preregistration",
    "intervention_manual",
    "execution_authorization",
    "phase_contract",
    "governance",
    "non_execution",
    "execution_order",
    "outputs",
}
COMPARISON_SEPARATION_CONFIG_KEYS = {
    "schema_path",
    "schema_sha256",
    "feasibility_receipt_path",
    "feasibility_receipt_sha256",
    "confirmatory_receipt_path",
    "confirmatory_receipt_sha256",
    "participant_audio_rating_overlap_or_reuse_allowed",
    "shared_c3_participant_arm_allowed",
    "stage_2_unblinded_effect_access_allowed",
}
EXPERIMENT_KEYS = {
    "id",
    "stage",
    "implementation_status",
    "actual_portal_or_institution_contact_allowed",
    "actual_recruitment_or_consent_allowed",
    "actual_participant_session_or_audio_access_allowed",
    "actual_randomization_or_intervention_allowed",
    "actual_model_api_reference_or_coaching_allowed",
    "actual_blind_rating_or_metric_allowed",
    "actual_output_or_publication_allowed",
    "completion_claim_allowed",
}
H00_CONFIG_KEYS = {
    "actual_official_authorization_required",
    "config_path",
    "config_sha256",
    "contract_path",
    "contract_sha256",
    "approval_path",
    "approval_sha256",
    "attestation_path",
    "attestation_sha256",
    "required_status",
    "pilot_and_confirmatory_scope_required",
    "recruitment_consent_audio_randomization_intervention_rating_scope_required",
    "restricted_official_document_resolution_allowed",
}
H04_CONFIG_KEYS = {
    "config_path",
    "config_sha256",
    "contract_path",
    "contract_sha256",
    "feasibility_schema_path",
    "feasibility_schema_sha256",
    "feasibility_completion_path",
    "feasibility_completion_sha256",
    "h01_schema_path",
    "h01_schema_sha256",
    "h01_lock_path",
    "h01_lock_sha256",
    "sap_template_path",
    "sap_template_sha256",
    "frozen_sap_path",
    "frozen_sap_sha256",
    "confirmatory_schema_path",
    "confirmatory_schema_sha256",
    "confirmatory_completion_path",
    "confirmatory_completion_sha256",
    "phase_separation_schema_path",
    "phase_separation_schema_sha256",
    "phase_separation_path",
    "phase_separation_sha256",
}
DELIVERY_CONFIG_KEYS = {
    "receipt_schema_path",
    "receipt_schema_sha256",
    "receipt_path",
    "receipt_sha256",
    "s108",
    "e01",
    "c02",
    "e02",
    "c03",
    "e00",
    "r03",
    "r04",
    "static_package_may_resolve_or_read_predecessor_artifacts",
}
DELIVERY_SOURCE_KEYS = {
    "s108": {
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "completion_path",
        "completion_sha256",
    },
    "e01": {
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "completion_path",
        "completion_sha256",
    },
    "c02": {
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "completion_path",
        "completion_sha256",
    },
    "e02": {
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "completion_path",
        "completion_sha256",
    },
    "c03": {
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "report_path",
        "report_sha256",
    },
    "e00": {
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "completion_path",
        "completion_sha256",
        "d08_rights_lineage_sha256",
    },
    "r03": {
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "bank_path",
        "bank_sha256",
    },
    "r04": {
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "completion_path",
        "completion_sha256",
    },
}
DATA_CONFIG_KEYS = {
    "participant_schema_path",
    "participant_schema_sha256",
    "session_schema_path",
    "session_schema_sha256",
    "audio_schema_path",
    "audio_schema_sha256",
    "audio_qc_schema_path",
    "audio_qc_schema_sha256",
    "randomization_schema_path",
    "randomization_schema_sha256",
    "intervention_exposure_schema_path",
    "intervention_exposure_schema_sha256",
    "blind_rating_schema_path",
    "blind_rating_schema_sha256",
    "disposition_schema_path",
    "disposition_schema_sha256",
    "participant_session_audio_qc_randomization_exposure_rating_disposition_zone",
    "pseudonym_identity_key_zone",
    "restricted_locator_zone",
    "aggregate_handoff_zone",
}
PREREG_CONFIG_KEYS = {
    "template_path",
    "template_sha256",
    "feasibility_path",
    "feasibility_sha256",
    "confirmatory_path",
    "confirmatory_sha256",
    "exact_eligibility_recruitment_n_qc_exposure_rating_anchors_adjudication_support_required",
    "unresolved_fields_must_fail_closed",
}
MANUAL_CONFIG_KEYS = {
    "template_path",
    "template_sha256",
    "feasibility_path",
    "feasibility_sha256",
    "confirmatory_path",
    "confirmatory_sha256",
    "c0_c3_c5_exact_version_and_hash_required",
    "exposure_matching_required",
}
AUTH_CONFIG_KEYS = {
    "template_path",
    "template_sha256",
    "feasibility_path",
    "feasibility_sha256",
    "confirmatory_path",
    "confirmatory_sha256",
    "statistics_effect_or_publication_authorization_allowed",
}
NONEXECUTION_KEYS = {
    "portal_or_institution_contact_allowed",
    "recruitment_allowed",
    "consent_allowed",
    "participant_or_session_access_allowed",
    "microphone_or_audio_access_allowed",
    "qc_execution_allowed",
    "randomization_allowed",
    "model_or_gpu_access_allowed",
    "api_or_network_access_allowed",
    "intervention_or_reference_delivery_allowed",
    "blind_rating_allowed",
    "metric_statistics_or_effect_allowed",
    "output_registry_or_commit_write_allowed",
    "publication_or_completion_claim_allowed",
}

PARTICIPANT_KEYS = {
    "schema_version",
    "record_id_sha256",
    "phase",
    "comparison",
    "assigned_condition",
    "participant_key_sha256",
    "consent_receipt_sha256",
    "l1_stratum_id",
    "baseline_stratum_id",
    "randomized",
    "participant_blinding_claimed",
    "direct_identifiers_present",
    "privacy_zone",
}
SESSION_KEYS = {
    "schema_version",
    "session_id_sha256",
    "participant_key_sha256",
    "phase",
    "assigned_condition",
    "session_index",
    "session_kind",
    "scheduled_at_utc",
    "occurred",
    "protocol_deviation_present",
    "protocol_deviation_receipt_sha256",
    "privacy_zone",
}
AUDIO_KEYS = {
    "schema_version",
    "audio_object_id_sha256",
    "participant_key_sha256",
    "phase",
    "comparison",
    "timepoint",
    "item_set",
    "item_card_sha256",
    "target_text_sha256",
    "recording_content_sha256",
    "recording_receipt_sha256",
    "condition_time_identity_masked_for_rating",
    "qc_status",
    "audio_locator_present",
    "privacy_zone",
}
QC_KEYS = {
    "schema_version",
    "qc_record_id_sha256",
    "audio_object_id_sha256",
    "phase",
    "timepoint",
    "frozen_qc_policy_sha256",
    "decode_passed",
    "sample_rate_hz",
    "duration_ms",
    "clipping_fraction",
    "snr_db",
    "baseline_hard_qc_eligibility_only",
    "postrandomization_exclusion_allowed",
    "status",
    "coded_reason_id",
    "privacy_zone",
}
RANDOMIZATION_KEYS = {
    "schema_version",
    "assignment_receipt_id_sha256",
    "participant_key_sha256",
    "phase",
    "comparison",
    "randomization_stage",
    "assigned_condition",
    "allocation_unit",
    "allocation_ratio",
    "l1_stratum_id",
    "baseline_stratum_id",
    "stage_randomization_receipt_sha256",
    "stage_gate_receipt_sha256",
    "s108_completion_sha256",
    "e01_completion_sha256",
    "c02_completion_sha256",
    "e02_completion_sha256",
    "c03_completion_sha256",
    "e00_completion_sha256",
    "r03_bank_sha256",
    "r04_completion_sha256",
    "allocation_concealed_until_assignment",
    "stage_2_activation_basis",
    "unblinded_effect_used_for_stage_2_activation",
    "privacy_zone",
}
COMPARISON_STAGE_SEPARATION_KEYS = {
    "schema_version",
    "receipt_id",
    "phase",
    "status",
    "stage_1",
    "stage_2",
    "stage_1_randomization_closed_at_utc",
    "stage_2_activation_authorized_at_utc",
    "stage_2_randomization_opened_at_utc",
    "stage_2_activation_decision_receipt_sha256",
    "stage_1_unblinded_effect_access_log_sha256",
    "stage_1_unblinded_effect_access_count_before_stage_2_activation",
    "stage_1_outcome_or_effect_available_at_stage_2_activation",
    "stage_2_activation_basis",
    "activation_time_order_verified",
    "participant_intersection_count",
    "audio_intersection_count",
    "rating_intersection_count",
    "shared_c3_participant_intersection_count",
    "copied_or_cloned_row_count",
    "comparison_participant_audio_rating_reuse_allowed",
    "independent_participant_unit_parallel_comparisons",
    "privacy_zone",
    "binding_sha256",
}
COMPARISON_STAGE_KEYS = {
    "comparison",
    "allowed_conditions",
    "randomization_receipt_sha256",
    "participant_set_sha256",
    "audio_set_sha256",
    "rating_set_sha256",
    "gate_receipt_sha256",
    "s108_completion_sha256",
    "e01_completion_sha256",
    "c02_completion_sha256",
    "e02_completion_sha256",
    "c03_completion_sha256",
    "e00_completion_sha256",
    "r03_bank_sha256",
    "r04_completion_sha256",
}
EXPOSURE_KEYS = {
    "schema_version",
    "exposure_record_id_sha256",
    "participant_key_sha256",
    "session_id_sha256",
    "phase",
    "assigned_condition",
    "manual_version_id",
    "manual_sha256",
    "s108_completion_sha256",
    "e01_completion_sha256",
    "c02_completion_sha256",
    "e02_completion_sha256",
    "c03_completion_sha256",
    "e00_completion_sha256",
    "r03_bank_sha256",
    "r04_completion_sha256",
    "assigned_seconds",
    "received_seconds",
    "assigned_item_count",
    "received_item_count",
    "adherence_fraction",
    "coaching_delivered",
    "reference_delivered",
    "blocked_safe_action_preserved",
    "protocol_deviation_retained",
    "free_generation_used",
    "privacy_zone",
}
RATING_KEYS = {
    "schema_version",
    "rating_record_id_sha256",
    "blind_item_code_sha256",
    "audio_object_id_sha256",
    "rater_key_sha256",
    "phase",
    "outcome",
    "score_1_to_5",
    "rater_blinded_to_condition",
    "rater_blinded_to_timepoint",
    "rater_blinded_to_participant_identity",
    "condition_field_present_in_rating_view",
    "timepoint_field_present_in_rating_view",
    "participant_identity_present_in_rating_view",
    "adjudicated",
    "adjudication_receipt_sha256",
    "privacy_zone",
}
DISPOSITION_KEYS = {
    "schema_version",
    "disposition_record_id_sha256",
    "participant_key_sha256",
    "phase",
    "comparison",
    "assigned_condition",
    "randomized",
    "withdrawn",
    "withdrawal_coded_receipt_sha256",
    "followup_status",
    "missingness_coded_receipt_sha256",
    "protocol_deviation_present",
    "protocol_deviation_receipt_sha256",
    "adverse_feedback_present",
    "adverse_feedback_receipt_sha256",
    "retained_in_itt",
    "postrandomization_exclusion_used",
    "free_text_present",
    "privacy_zone",
}

DELIVERY_KEYS = {
    "schema_version",
    "receipt_id",
    "status",
    "validated_at_utc",
    "s108",
    "e01",
    "c02",
    "e02",
    "c03",
    "e00",
    "r03",
    "r04",
    "assurance",
    "privacy_zone",
    "binding_sha256",
}
DELIVERY_PAYLOAD_KEYS = {
    "s108": {
        "config_sha256",
        "contract_sha256",
        "completion_sha256",
        "model_identity_sha256",
        "checkpoint_sha256",
        "calibrator_sha256",
        "threshold_policy_sha256",
        "deep_validation_receipt_sha256",
        "decision",
    },
    "e01": {
        "config_sha256",
        "contract_sha256",
        "completion_sha256",
        "aggregate_report_sha256",
        "deep_validation_receipt_sha256",
        "status",
        "robustness_passed",
    },
    "c02": {
        "config_sha256",
        "contract_sha256",
        "completion_sha256",
        "selection_output_sha256",
        "preregistration_sha256",
        "deep_validation_receipt_sha256",
        "production_release_allowed",
    },
    "e02": {
        "config_sha256",
        "contract_sha256",
        "completion_sha256",
        "guard_policy_sha256",
        "api_response_contract_sha256",
        "deep_validation_receipt_sha256",
        "status",
        "confidence_and_api_guard_passed",
    },
    "c03": {
        "config_sha256",
        "contract_sha256",
        "report_sha256",
        "deep_validation_receipt_sha256",
        "decision",
        "production_release_allowed",
    },
    "e00": {
        "config_sha256",
        "contract_sha256",
        "completion_sha256",
        "reference_handoff_sha256",
        "d08_rights_lineage_sha256",
        "deep_validation_receipt_sha256",
        "status",
    },
    "r03": {
        "config_sha256",
        "contract_sha256",
        "bank_manifest_sha256",
        "deep_validation_receipt_sha256",
        "status",
        "publication_required",
    },
    "r04": {
        "config_sha256",
        "contract_sha256",
        "completion_sha256",
        "aggregate_analysis_sha256",
        "deep_validation_receipt_sha256",
        "status",
        "blind_multireference_evaluation_passed",
    },
}
DELIVERY_ASSURANCE_KEYS = {
    "c3_uses_only_validated_s108_e01_c02_e02_c03",
    "c5_uses_validated_s108_e01_c02_e02_c03_e00_r03_r04",
    "e00_d08_rights_lineage_bound",
    "locked_result_copy_or_replay_used",
    "free_generation_used",
    "human_effect_or_outcome_used_for_predecessor_selection",
    "participant_rows_present",
    "audio_or_rating_rows_present",
    "aggregate_only",
}
SEPARATION_KEYS = {
    "schema_version",
    "receipt_id",
    "status",
    "feasibility_completion_sha256",
    "h01_final_sample_size_lock_sha256",
    "frozen_sap_sha256",
    "confirmatory_randomization_receipt_sha256",
    "confirmatory_data_lock_receipt_sha256",
    "feasibility_participant_set_sha256",
    "confirmatory_participant_set_sha256",
    "feasibility_audio_set_sha256",
    "confirmatory_audio_set_sha256",
    "feasibility_rating_set_sha256",
    "confirmatory_rating_set_sha256",
    "participant_intersection_count",
    "audio_intersection_count",
    "rating_intersection_count",
    "copied_or_cloned_row_count",
    "independent_confirmatory_recruitment_completed",
    "participant_audio_rating_reuse_allowed",
    "privacy_zone",
    "binding_sha256",
}

PREREG_KEYS = {
    "schema_version",
    "preregistration_id",
    "phase",
    "status",
    "frozen_at_utc",
    "lineage",
    "eligibility_and_recruitment",
    "sample_size",
    "schedule_and_items",
    "randomization",
    "audio_qc",
    "exposure_and_adherence",
    "blind_rating",
    "governance",
    "privacy_and_claims",
    "approval",
    "binding_sha256",
}
PREREG_SECTION_KEYS = {
    "lineage": {
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "protocol_sha256",
        "delivery_predecessor_receipt_sha256",
        "feasibility_completion_sha256",
        "h01_final_sample_size_lock_sha256",
        "frozen_sap_sha256",
    },
    "eligibility_and_recruitment": {
        "exact_eligibility_rule_id",
        "exact_exclusion_rule_id",
        "exact_recruitment_channel_id",
        "exact_screening_workflow_id",
        "minor_policy_id",
        "compensation_schedule_id",
        "coercion_review_receipt_sha256",
    },
    "sample_size": {
        "stage_1_final_enrollment_per_arm",
        "stage_2_final_enrollment_per_arm",
        "feasibility_per_arm_minimum",
        "feasibility_per_arm_maximum",
        "confirmatory_n_must_equal_h01_lock",
        "automatic_final_n_lock_allowed",
    },
    "schedule_and_items": {
        "intervention_duration_days",
        "post_timepoint",
        "retention_days_after_post",
        "timepoints",
        "fixed_practice_item_card_sha256",
        "fixed_transfer_item_card_sha256",
        "same_assessment_items_across_timepoints",
        "transfer_items_excluded_from_training",
    },
    "randomization": {
        "allocation_unit",
        "allocation_ratio",
        "strata",
        "allocation_concealment_workflow_id",
        "stage_1_comparison_id",
        "stage_1_allowed_conditions",
        "stage_1_randomization_receipt_rule_id",
        "stage_1_delivery_gate_components",
        "stage_2_comparison_id",
        "stage_2_allowed_conditions",
        "stage_2_randomization_receipt_rule_id",
        "stage_2_delivery_gate_components",
        "stage_randomizations_separate",
        "comparison_participant_audio_rating_reuse_allowed",
        "stage_2_activation_basis",
        "stage_2_activation_time_order_rule_id",
        "stage_1_unblinded_effect_access_log_rule_id",
        "stage_1_unblinded_effect_access_count_before_activation",
        "stage_1_outcome_or_effect_available_at_activation",
        "unblinded_effect_used_for_stage_2_activation",
    },
    "audio_qc": {
        "exact_recording_protocol_id",
        "exact_sample_rate_hz",
        "exact_duration_rule_id",
        "exact_snr_rule_id",
        "exact_clipping_rule_id",
        "exact_decode_rule_id",
        "baseline_hard_qc_before_randomization_only",
        "postrandomization_qc_failure_preserved_as_missingness",
    },
    "exposure_and_adherence": {
        "exact_session_schedule_id",
        "exact_assigned_seconds_rule_id",
        "exact_assigned_item_count_rule_id",
        "exact_adherence_denominator_id",
        "exact_protocol_deviation_taxonomy_id",
        "nonadherence_retained_after_randomization",
    },
    "blind_rating": {
        "minimum_independent_raters",
        "rater_qualification_rule_id",
        "rating_anchor_version_id",
        "randomization_and_blocking_rule_id",
        "adjudication_rule_id",
        "minimum_support_rule_id",
        "raters_blinded_to_condition_timepoint_and_identity",
        "participant_blinding_claim_allowed",
    },
    "governance": {
        "consent_withdrawal_workflow_id",
        "retention_and_deletion_schedule_id",
        "pseudonym_key_separation_workflow_id",
        "incident_response_workflow_id",
        "pause_stop_workflow_id",
        "adverse_feedback_taxonomy_id",
        "withdrawal_missingness_deviation_and_adverse_preserved",
    },
    "privacy_and_claims": {
        "participant_audio_rating_rows_zone",
        "pseudonym_key_zone",
        "restricted_locator_zone",
        "aggregate_completion_zone",
        "feasibility_efficacy_claim_allowed",
        "participant_audio_rating_reuse_across_phases_allowed",
        "public_row_or_audio_release_allowed",
    },
    "approval": {
        "decision",
        "principal_investigator_receipt_sha256",
        "statistician_receipt_sha256",
        "data_custodian_receipt_sha256",
        "approved_and_frozen",
    },
}
MANUAL_KEYS = {
    "schema_version",
    "manual_bundle_id",
    "phase",
    "status",
    "frozen_at_utc",
    "lineage",
    "common_delivery",
    "c0",
    "c3",
    "c5",
    "assurance",
    "approval",
    "binding_sha256",
}
MANUAL_SECTION_KEYS = {
    "lineage": {
        "h00_approval_sha256",
        "operational_preregistration_sha256",
        "delivery_predecessor_receipt_sha256",
    },
    "common_delivery": {
        "two_week_duration_required",
        "equal_exposure_time_and_practice_count_required",
        "exact_session_schedule_id",
        "exact_exposure_matching_rule_id",
        "human_escalation_workflow_id",
        "blocked_safe_action_override_allowed",
    },
    "c0": {
        "version_id",
        "manual_sha256",
        "active_control_time_and_practice_matched",
        "automated_diagnostic_or_coaching_delivered",
        "multi_reference_or_coached_reattempt_delivered",
    },
    "c3": {
        "version_id",
        "manual_sha256",
        "structured_evidence_limited_diagnostic_and_coaching",
        "s108_completion_sha256",
        "e01_completion_sha256",
        "c02_completion_sha256",
        "e02_completion_sha256",
        "c03_report_sha256",
        "multi_reference_or_coached_reattempt_delivered",
    },
    "c5": {
        "version_id",
        "manual_sha256",
        "inherits_c3_exact_delivery",
        "e00_completion_sha256",
        "r03_bank_sha256",
        "r04_completion_sha256",
        "multi_reference_and_coached_reattempt_required",
    },
    "assurance": {
        "manuals_versioned_and_immutable",
        "condition_components_incremental_only",
        "free_generation_allowed",
        "unapproved_reference_or_coaching_allowed",
        "participant_blinding_claimed",
    },
    "approval": {
        "decision",
        "principal_investigator_receipt_sha256",
        "intervention_owner_receipt_sha256",
        "safety_reviewer_receipt_sha256",
        "approved_and_frozen",
    },
}
AUTH_KEYS = {
    "schema_version",
    "authorization_id",
    "phase",
    "status",
    "valid_from_utc",
    "valid_until_utc",
    "lineage",
    "authorized_operations",
    "operational_assurance",
    "approval",
    "privacy_zone",
    "binding_sha256",
}
AUTH_SECTION_KEYS = {
    "lineage": {
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "operational_preregistration_sha256",
        "intervention_manual_bundle_sha256",
        "delivery_predecessor_receipt_sha256",
        "feasibility_completion_sha256",
        "h01_final_sample_size_lock_sha256",
        "frozen_sap_sha256",
    },
    "authorized_operations": {
        "recruitment",
        "consent",
        "participant_randomization",
        "audio_recording",
        "intervention_delivery",
        "blind_rating",
        "restricted_d1_processing",
        "statistics_or_effect_analysis",
        "publication",
    },
    "operational_assurance": {
        "h00_scope_current",
        "consent_withdrawal_retention_deletion_ready",
        "compensation_and_minor_policy_ready",
        "pseudonym_key_separation_ready",
        "incident_and_pause_stop_ready",
        "manuals_and_preregistration_frozen",
        "delivery_predecessors_actual_and_validated",
        "stage_randomization_receipts_separate",
        "comparison_participant_audio_rating_sets_disjoint",
        "comparison_c3_participants_not_shared",
        "stage_2_effect_independent_activation",
        "feasibility_and_confirmatory_sets_must_be_disjoint",
        "participant_blinding_claimed",
    },
    "approval": {
        "decision",
        "principal_investigator_receipt_sha256",
        "data_custodian_receipt_sha256",
        "operations_lead_receipt_sha256",
        "active",
    },
}


class D09ContractError(ValueError):
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
            raise D09ContractError("YAML mapping keys must be strings")
        if key in result:
            raise D09ContractError(f"duplicate YAML key: {key}")
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
    h00_restricted_document_resolution_count: int = 0
    h00_restricted_document_read_count: int = 0
    h04_config_read_count: int = 0
    h04_contract_load_count: int = 0
    feasibility_completion_path_resolution_count: int = 0
    feasibility_completion_read_count: int = 0
    h01_lock_path_resolution_count: int = 0
    h01_lock_read_count: int = 0
    sap_path_resolution_count: int = 0
    sap_read_count: int = 0
    h04_deep_validation_count: int = 0
    delivery_receipt_path_resolution_count: int = 0
    delivery_receipt_read_count: int = 0
    delivery_deep_validation_count: int = 0
    predecessor_artifact_path_resolution_count: int = 0
    predecessor_artifact_read_count: int = 0
    preregistration_path_resolution_count: int = 0
    preregistration_read_count: int = 0
    preregistration_deep_validation_count: int = 0
    manual_path_resolution_count: int = 0
    manual_read_count: int = 0
    manual_deep_validation_count: int = 0
    authorization_path_resolution_count: int = 0
    authorization_read_count: int = 0
    authorization_deep_validation_count: int = 0
    portal_or_institution_contact_count: int = 0
    recruitment_count: int = 0
    consent_count: int = 0
    participant_read_count: int = 0
    session_read_count: int = 0
    microphone_access_count: int = 0
    audio_read_count: int = 0
    audio_write_count: int = 0
    qc_execution_count: int = 0
    randomization_count: int = 0
    model_access_count: int = 0
    gpu_access_count: int = 0
    api_invocation_count: int = 0
    network_access_count: int = 0
    coaching_delivery_count: int = 0
    reference_delivery_count: int = 0
    intervention_delivery_count: int = 0
    blind_rating_count: int = 0
    metric_computation_count: int = 0
    statistics_computation_count: int = 0
    effect_estimation_count: int = 0
    restricted_d1_data_read_count: int = 0
    pseudonym_key_read_count: int = 0
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
    approval_sha256: str
    attestation_sha256: str


@dataclass(frozen=True)
class H04PhaseEvidence:
    feasibility_sha256: str
    feasibility_payload: Mapping[str, Any]
    h01_sha256: str
    h01_payload: Mapping[str, Any]
    sap_sha256: str
    sap_payload: Mapping[str, Any]
    module: ModuleType


@dataclass(frozen=True)
class DeliveryEvidence:
    sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PreregEvidence:
    sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ManualEvidence:
    sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class AuthorizationEvidence:
    sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ComparisonStageEvidence:
    sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PreflightResult:
    status: str
    phase: str
    config_sha256: str
    h00_approval_sha256: str
    h00_attestation_sha256: str
    feasibility_completion_sha256: str
    h01_lock_sha256: str
    sap_sha256: str
    delivery_receipt_sha256: str
    preregistration_sha256: str
    manual_sha256: str
    authorization_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise D09ContractError(f"{name} must be a string-keyed mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise D09ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise D09ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise D09ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise D09ContractError(f"{name} must be >= {minimum}")
    return value


def _number(
    value: object,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise D09ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise D09ContractError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise D09ContractError(f"{name} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise D09ContractError(f"{name} must be <= {maximum}")
    return result


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise D09ContractError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise D09ContractError(f"{name} must be finite decimal") from exc
    if not result.is_finite():
        raise D09ContractError(f"{name} must be finite decimal")
    return result


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if not SHA256_RE.fullmatch(text):
        raise D09ContractError(f"{name} must be lowercase SHA-256")
    return text


def _safe_id(value: object, name: str) -> str:
    text = _string(value, name)
    if not SAFE_ID_RE.fullmatch(text):
        raise D09ContractError(f"{name} must be a deterministic safe ID")
    return text


def _utc(value: object, name: str) -> datetime:
    text = _string(value, name)
    match = UTC_RE.fullmatch(text)
    if match is None:
        raise D09ContractError(f"{name} must be UTC second precision")
    try:
        result = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            tzinfo=timezone.utc,
        )
    except ValueError as exc:
        raise D09ContractError(f"{name} is not a valid timestamp") from exc
    if result > datetime.now(timezone.utc):
        raise D09ContractError(f"{name} cannot be in the future")
    return result


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise D09ContractError(
            f"{name} mismatch: expected {expected!r}, got {actual!r}"
        )


def _expect_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise D09ContractError(
            f"{name} keys mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise D09ContractError(f"{name} contains non-finite number")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_nonfinite_tree(child, name)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite_tree(child, name)


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise D09ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise D09ContractError(f"non-finite JSON constant prohibited: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    _reject_nonfinite_tree(value, "canonical JSON")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def binding_sha256(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload["binding_sha256"] = ""
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _contains_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("TBD_")
    if isinstance(value, Mapping):
        return any(_contains_tbd(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_tbd(child) for child in value)
    return False


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or text != path.as_posix()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise D09ContractError(f"{name} must be a safe workspace-relative path")
    return text


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
        exists = current.exists() or current.is_symlink()
        if current.is_symlink():
            raise D09ContractError(f"{name} traverses symlink: {relative}")
        if exists and index < len(parts) - 1 and not current.is_dir():
            raise D09ContractError(f"{name} parent is not directory: {relative}")
    if require_leaf and not current.exists():
        raise D09ContractError(f"{name} missing: {relative}")
    try:
        current.relative_to(root)
    except ValueError as exc:
        raise D09ContractError(f"{name} escapes workspace") from exc
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
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise D09ContractError(f"{name} must be a regular file")
        data = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(data) != before.st_size:
        raise D09ContractError(f"{name} short/long read detected")
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise D09ContractError(f"{name} changed during read")
    return data, FileSnapshot(
        relative_path=relative,
        sha256=hashlib.sha256(data).hexdigest(),
        size=before.st_size,
        device=before.st_dev,
        inode=before.st_ino,
        mtime_ns=before.st_mtime_ns,
    )


def _verify_snapshot(
    workspace_root: Path,
    snapshot: FileSnapshot,
    name: str,
) -> None:
    _, current = _read_file(workspace_root, snapshot.relative_path, name)
    if current != snapshot:
        raise D09ContractError(f"{name} changed after validation")


def _load_json_bytes(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise D09ContractError(f"{name} is invalid UTF-8 JSON") from exc
    return _mapping(value, name)


def _load_config_bytes(data: bytes) -> Mapping[str, Any]:
    try:
        value = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise D09ContractError("D09 config is invalid UTF-8 YAML") from exc
    return _mapping(value, "D09 config")


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        return _load_config_bytes(path.read_bytes())
    except OSError as exc:
        raise D09ContractError(f"cannot read D09 config: {exc}") from exc


def _read_exact_json(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
    name: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    data, snapshot = _read_file(workspace_root, relative, name)
    if snapshot.sha256 != expected_sha256:
        raise D09ContractError(f"{name} SHA-256 mismatch")
    return _load_json_bytes(data, name), snapshot


def _load_module(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
    module_name: str,
) -> ModuleType:
    data, snapshot = _read_file(workspace_root, relative, module_name)
    if snapshot.sha256 != expected_sha256:
        raise D09ContractError(f"{module_name} SHA-256 mismatch")
    path = workspace_root / relative
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise D09ContractError(f"cannot load {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _verify_snapshots(
    *,
    workspace_root: Path,
    snapshots: tuple[FileSnapshot, ...],
    audit: AccessAudit,
    stage: str,
) -> None:
    for snapshot in snapshots:
        try:
            _verify_snapshot(workspace_root, snapshot, snapshot.relative_path)
        except D09ContractError as exc:
            raise D09ContractError(str(exc), stage=stage, audit=audit) from exc
        audit.source_toctou_verification_count += 1


def _validate_schema_root(
    payload: Mapping[str, Any],
    *,
    root_keys: set[str],
    name: str,
) -> None:
    _expect(
        payload.get("$schema"), "https://json-schema.org/draft/2020-12/schema", name
    )
    _expect(payload.get("type"), "object", f"{name} root type")
    _expect(payload.get("additionalProperties"), False, f"{name} strict root")
    required = _list(payload.get("required"), f"{name} required")
    if len(required) != len(set(required)) or set(required) != root_keys:
        raise D09ContractError(f"{name} required root key set drift")
    properties = _mapping(payload.get("properties"), f"{name} properties")
    if set(properties) != root_keys:
        raise D09ContractError(f"{name} property root key set drift")


def _validate_templates(payload_by_path: Mapping[str, bytes]) -> None:
    prereg = _load_json_bytes(
        payload_by_path[
            "configs/human_research/d09_h02_h03_operational_preregistration_v1.template.json"
        ],
        "D09 preregistration template",
    )
    _expect_keys(prereg, PREREG_KEYS, "D09 preregistration template")
    for section, keys in PREREG_SECTION_KEYS.items():
        _expect_keys(
            _mapping(prereg.get(section), f"prereg template {section}"),
            keys,
            f"prereg template {section}",
        )
    _expect(prereg.get("status"), "draft_unapproved", "prereg template status")
    if not _contains_tbd(prereg):
        raise D09ContractError("preregistration template must remain unresolved")

    manual = _load_json_bytes(
        payload_by_path[
            "configs/human_research/d09_h02_h03_intervention_manual_v1.template.json"
        ],
        "D09 manual template",
    )
    _expect_keys(manual, MANUAL_KEYS, "D09 manual template")
    for section, keys in MANUAL_SECTION_KEYS.items():
        _expect_keys(
            _mapping(manual.get(section), f"manual template {section}"),
            keys,
            f"manual template {section}",
        )
    _expect(manual.get("status"), "draft_unapproved", "manual template status")
    if not _contains_tbd(manual):
        raise D09ContractError("manual template must remain unresolved")

    authorization = _load_json_bytes(
        payload_by_path[
            "configs/human_research/d09_h02_h03_execution_authorization_v1.template.json"
        ],
        "D09 authorization template",
    )
    _expect_keys(authorization, AUTH_KEYS, "D09 authorization template")
    for section, keys in AUTH_SECTION_KEYS.items():
        _expect_keys(
            _mapping(authorization.get(section), f"authorization template {section}"),
            keys,
            f"authorization template {section}",
        )
    _expect(
        authorization.get("status"),
        "draft_inactive",
        "authorization template status",
    )
    if not _contains_tbd(authorization):
        raise D09ContractError("authorization template must remain unresolved")


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_KEYS, "D09 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "config schema")
    experiment = _mapping(config.get("experiment"), "experiment")
    _expect_keys(experiment, EXPERIMENT_KEYS, "experiment")
    _expect(
        experiment.get("id"),
        "d09_h02_h03_longitudinal_collection_v1",
        "experiment ID",
    )
    _expect(
        experiment.get("stage"),
        "wp1_d09_wp9_h02_h03_longitudinal_collection",
        "experiment stage",
    )
    _expect(experiment.get("implementation_status"), STATIC_STATUS, "status")
    for field in EXPERIMENT_KEYS - {"id", "stage", "implementation_status"}:
        _expect(experiment.get(field), False, f"experiment.{field}")

    canonical = _mapping(config.get("canonical_sources"), "canonical sources")
    _expect_keys(canonical, {"access_scope", "pins"}, "canonical sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_or_A1_aggregate_static_pins_only",
        "source scope",
    )

    h00 = _mapping(config.get("predecessor_h00"), "H00")
    _expect_keys(h00, H00_CONFIG_KEYS, "H00")
    for field in (
        "actual_official_authorization_required",
        "pilot_and_confirmatory_scope_required",
        "recruitment_consent_audio_randomization_intervention_rating_scope_required",
    ):
        _expect(h00.get(field), True, f"H00.{field}")
    _expect(
        h00.get("restricted_official_document_resolution_allowed"),
        False,
        "H00 restricted boundary",
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
        h00.get("required_status"),
        "ready_for_separately_authorized_h00_human_operations",
        "H00 status",
    )

    h04 = _mapping(config.get("h04_handoff"), "H04 handoff")
    _expect_keys(h04, H04_CONFIG_KEYS, "H04 handoff")
    for field in H04_CONFIG_KEYS:
        if field.endswith("_path"):
            _relative_string(h04.get(field), f"H04.{field}")
        elif field.endswith("_sha256"):
            value = _string(h04.get(field), f"H04.{field}")
            if value not in {
                TBD_FEASIBILITY,
                TBD_H01,
                TBD_SAP,
                TBD_CONFIRMATORY,
                TBD_SEPARATION,
            }:
                _sha256(value, f"H04.{field}")

    comparison_separation = _mapping(
        config.get("comparison_stage_separation"),
        "comparison-stage separation",
    )
    _expect_keys(
        comparison_separation,
        COMPARISON_SEPARATION_CONFIG_KEYS,
        "comparison-stage separation",
    )
    for field in (
        "schema_path",
        "feasibility_receipt_path",
        "confirmatory_receipt_path",
    ):
        _relative_string(
            comparison_separation.get(field),
            f"comparison-stage separation.{field}",
        )
    _sha256(
        comparison_separation.get("schema_sha256"),
        "comparison-stage separation schema SHA",
    )
    for field, placeholder in (
        ("feasibility_receipt_sha256", TBD_FEASIBILITY_COMPARISON),
        ("confirmatory_receipt_sha256", TBD_CONFIRMATORY_COMPARISON),
    ):
        value = _string(
            comparison_separation.get(field),
            f"comparison-stage separation.{field}",
        )
        if value != placeholder:
            _sha256(value, f"comparison-stage separation.{field}")
    for field in (
        "participant_audio_rating_overlap_or_reuse_allowed",
        "shared_c3_participant_arm_allowed",
        "stage_2_unblinded_effect_access_allowed",
    ):
        _expect(
            comparison_separation.get(field),
            False,
            f"comparison-stage separation.{field}",
        )

    delivery = _mapping(config.get("delivery_predecessors"), "delivery")
    _expect_keys(delivery, DELIVERY_CONFIG_KEYS, "delivery")
    for field in ("receipt_schema_path", "receipt_path"):
        _relative_string(delivery.get(field), f"delivery.{field}")
    _sha256(delivery.get("receipt_schema_sha256"), "delivery schema SHA")
    delivery_sha = _string(delivery.get("receipt_sha256"), "delivery receipt SHA")
    if delivery_sha != TBD_DELIVERY:
        _sha256(delivery_sha, "delivery receipt SHA")
    _expect(
        delivery.get("static_package_may_resolve_or_read_predecessor_artifacts"),
        False,
        "delivery protected artifact boundary",
    )
    allowed_placeholders = {
        "s108": "TBD_S108_COMPLETION",
        "e01": "TBD_E01_COMPLETION",
        "c02": "TBD_C02_COMPLETION",
        "e02": "TBD_E02_COMPLETION",
        "c03": "TBD_C03_COMPLETION",
        "e00": "TBD_E00_COMPLETION",
        "r03": "TBD_R03_COMPLETION",
        "r04": "TBD_R04_COMPLETION",
    }
    for name, keys in DELIVERY_SOURCE_KEYS.items():
        section = _mapping(delivery.get(name), f"delivery {name}")
        _expect_keys(section, keys, f"delivery {name}")
        for field in keys:
            if field.endswith("_path"):
                _relative_string(section.get(field), f"delivery {name}.{field}")
            elif field.endswith("_sha256"):
                value = _string(section.get(field), f"delivery {name}.{field}")
                placeholders = {allowed_placeholders[name]}
                if name == "e00" and field == "d08_rights_lineage_sha256":
                    placeholders.add("TBD_E00_D08_R03_LINEAGE")
                if value not in placeholders:
                    _sha256(value, f"delivery {name}.{field}")

    data = _mapping(config.get("data_contract"), "data contract")
    _expect_keys(data, DATA_CONFIG_KEYS, "data contract")
    for field in DATA_CONFIG_KEYS:
        if field.endswith("_path"):
            _relative_string(data.get(field), f"data.{field}")
        elif field.endswith("_sha256"):
            _sha256(data.get(field), f"data.{field}")
    _expect(
        data.get(
            "participant_session_audio_qc_randomization_exposure_rating_disposition_zone"
        ),
        "D1_pseudonymized_restricted_human_research",
        "D1 zone",
    )
    _expect(
        data.get("pseudonym_identity_key_zone"),
        "R0_direct_identifier_key_separate",
        "R0 zone",
    )
    _expect(
        data.get("restricted_locator_zone"),
        "R1_restricted_locator",
        "R1 zone",
    )
    _expect(data.get("aggregate_handoff_zone"), "A1_aggregate_only", "A1 zone")

    prereg = _mapping(config.get("operational_preregistration"), "prereg config")
    _expect_keys(prereg, PREREG_CONFIG_KEYS, "prereg config")
    manual = _mapping(config.get("intervention_manual"), "manual config")
    _expect_keys(manual, MANUAL_CONFIG_KEYS, "manual config")
    authorization = _mapping(config.get("execution_authorization"), "auth config")
    _expect_keys(authorization, AUTH_CONFIG_KEYS, "auth config")
    for section, prefix, artifact_placeholders in (
        (
            prereg,
            "prereg",
            {
                "feasibility_sha256": "TBD_D09_FEASIBILITY_PREREGISTRATION",
                "confirmatory_sha256": "TBD_D09_CONFIRMATORY_PREREGISTRATION",
            },
        ),
        (
            manual,
            "manual",
            {
                "feasibility_sha256": "TBD_D09_FEASIBILITY_MANUAL",
                "confirmatory_sha256": "TBD_D09_CONFIRMATORY_MANUAL",
            },
        ),
        (
            authorization,
            "authorization",
            {
                "feasibility_sha256": "TBD_D09_FEASIBILITY_AUTHORIZATION",
                "confirmatory_sha256": "TBD_D09_CONFIRMATORY_AUTHORIZATION",
            },
        ),
    ):
        for field in ("template_path", "feasibility_path", "confirmatory_path"):
            _relative_string(section.get(field), f"{prefix}.{field}")
        _sha256(section.get("template_sha256"), f"{prefix} template SHA")
        for field, placeholder in artifact_placeholders.items():
            value = _string(section.get(field), f"{prefix}.{field}")
            if value != placeholder:
                _sha256(value, f"{prefix}.{field}")
    for field in (
        "exact_eligibility_recruitment_n_qc_exposure_rating_anchors_adjudication_support_required",
        "unresolved_fields_must_fail_closed",
    ):
        _expect(prereg.get(field), True, f"prereg.{field}")
    for field in (
        "c0_c3_c5_exact_version_and_hash_required",
        "exposure_matching_required",
    ):
        _expect(manual.get(field), True, f"manual.{field}")
    _expect(
        authorization.get("statistics_effect_or_publication_authorization_allowed"),
        False,
        "authorization statistics boundary",
    )

    phase_contract = _mapping(config.get("phase_contract"), "phase contract")
    _expect_keys(
        phase_contract,
        {
            "phase_a",
            "phase_b",
            "comparisons",
            "allocation_unit",
            "stratification",
            "separate_stage_randomization_receipts_required",
            "stage_1_delivery_gate_components",
            "stage_2_delivery_gate_components",
            "comparison_participant_audio_rating_overlap_or_reuse_allowed",
            "shared_c3_participant_arm_allowed",
            "stage_2_activation_basis",
            "unblinded_effect_activation_allowed",
            "intervention_duration_days",
            "assessment_timepoints",
            "retention_days_after_post",
            "fixed_practice_and_transfer_items_required",
            "transfer_items_may_be_trained",
            "minimum_independent_blind_raters",
            "rater_blind_dimensions",
            "participant_blinding_claim_allowed",
        },
        "phase contract",
    )
    phase_a = _mapping(phase_contract.get("phase_a"), "phase A")
    _expect_keys(
        phase_a,
        {
            "id",
            "per_arm_minimum",
            "per_arm_maximum",
            "efficacy_claim_allowed",
            "h01_or_sap_required_before_phase",
            "output_schema",
        },
        "phase A",
    )
    for actual_value, expected_value, name in (
        (phase_a.get("id"), "feasibility", "phase A ID"),
        (phase_a.get("per_arm_minimum"), 15, "phase A minimum"),
        (phase_a.get("per_arm_maximum"), 20, "phase A maximum"),
        (phase_a.get("efficacy_claim_allowed"), False, "phase A efficacy"),
        (
            phase_a.get("h01_or_sap_required_before_phase"),
            False,
            "phase A circularity",
        ),
        (
            phase_a.get("output_schema"),
            "voice_coach.h04_feasibility_pilot_completion.v1",
            "phase A handoff",
        ),
    ):
        _expect(actual_value, expected_value, name)
    phase_b = _mapping(phase_contract.get("phase_b"), "phase B")
    _expect_keys(
        phase_b,
        {
            "id",
            "final_n_source",
            "feasibility_completion_h01_lock_public_prereg_and_sap_required_before_recruitment",
            "pilot_participant_audio_rating_reuse_or_copy_allowed",
            "output_schema",
        },
        "phase B",
    )
    for phase_b_actual, phase_b_expected, name in (
        (phase_b.get("id"), "confirmatory", "phase B ID"),
        (phase_b.get("final_n_source"), "actual_h01_manual_lock", "phase B N"),
        (
            phase_b.get(
                "feasibility_completion_h01_lock_public_prereg_and_sap_required_before_recruitment"
            ),
            True,
            "phase B predecessors",
        ),
        (
            phase_b.get("pilot_participant_audio_rating_reuse_or_copy_allowed"),
            False,
            "phase separation",
        ),
        (
            phase_b.get("output_schema"),
            "voice_coach.h04_h02_h03_blind_bundle_completion.v1",
            "phase B handoff",
        ),
    ):
        _expect(phase_b_actual, phase_b_expected, name)
    expected_comparisons = [
        {"id": "c3_minus_c0", "arms": ["C0", "C3"], "allocation_ratio": "1:1"},
        {"id": "c5_minus_c3", "arms": ["C3", "C5"], "allocation_ratio": "1:1"},
    ]
    _expect(phase_contract.get("comparisons"), expected_comparisons, "comparisons")
    for contract_actual, contract_expected, name in (
        (phase_contract.get("allocation_unit"), "participant", "allocation unit"),
        (
            phase_contract.get("stratification"),
            ["l1", "baseline_comprehensibility"],
            "strata",
        ),
        (
            phase_contract.get("separate_stage_randomization_receipts_required"),
            True,
            "separate randomization",
        ),
        (
            phase_contract.get("stage_1_delivery_gate_components"),
            ["S108", "E01", "C02", "E02", "C03"],
            "stage 1 delivery gate",
        ),
        (
            phase_contract.get("stage_2_delivery_gate_components"),
            ["S108", "E01", "C02", "E02", "C03", "E00", "R03", "R04"],
            "stage 2 delivery gate",
        ),
        (
            phase_contract.get(
                "comparison_participant_audio_rating_overlap_or_reuse_allowed"
            ),
            False,
            "comparison reuse",
        ),
        (
            phase_contract.get("shared_c3_participant_arm_allowed"),
            False,
            "shared C3 participant arm",
        ),
        (
            phase_contract.get("stage_2_activation_basis"),
            "approval_safety_privacy_and_delivery_readiness_only",
            "stage 2 basis",
        ),
        (
            phase_contract.get("unblinded_effect_activation_allowed"),
            False,
            "stage 2 effect",
        ),
        (phase_contract.get("intervention_duration_days"), 14, "duration"),
        (
            phase_contract.get("assessment_timepoints"),
            list(TIMEPOINTS),
            "timepoints",
        ),
        (phase_contract.get("retention_days_after_post"), 28, "retention"),
        (
            phase_contract.get("fixed_practice_and_transfer_items_required"),
            True,
            "fixed item sets",
        ),
        (
            phase_contract.get("transfer_items_may_be_trained"),
            False,
            "transfer training",
        ),
        (
            phase_contract.get("minimum_independent_blind_raters"),
            2,
            "raters",
        ),
        (
            phase_contract.get("rater_blind_dimensions"),
            ["condition", "timepoint", "participant_identity"],
            "rater blind dimensions",
        ),
        (
            phase_contract.get("participant_blinding_claim_allowed"),
            False,
            "participant blinding",
        ),
    ):
        _expect(contract_actual, contract_expected, name)

    governance = _mapping(config.get("governance"), "governance")
    _expect_keys(
        governance,
        {
            "consent_withdrawal_retention_deletion_required",
            "compensation_and_minor_policy_required",
            "incident_and_pause_stop_required",
            "pseudonym_key_separation_required",
            "adherence_missingness_protocol_deviation_preserved",
            "adverse_feedback_preserved_and_aggregated",
            "postrandomization_exclusion_allowed",
            "public_participant_audio_rating_or_locator_allowed",
        },
        "governance",
    )
    for field in {
        "consent_withdrawal_retention_deletion_required",
        "compensation_and_minor_policy_required",
        "incident_and_pause_stop_required",
        "pseudonym_key_separation_required",
        "adherence_missingness_protocol_deviation_preserved",
        "adverse_feedback_preserved_and_aggregated",
    }:
        _expect(governance.get(field), True, f"governance.{field}")
    for field in {
        "postrandomization_exclusion_allowed",
        "public_participant_audio_rating_or_locator_allowed",
    }:
        _expect(governance.get(field), False, f"governance.{field}")

    nonexecution = _mapping(config.get("non_execution"), "nonexecution")
    _expect_keys(nonexecution, NONEXECUTION_KEYS, "nonexecution")
    for field in NONEXECUTION_KEYS:
        _expect(nonexecution.get(field), False, f"non_execution.{field}")

    order = _mapping(config.get("execution_order"), "execution order")
    _expect_keys(order, {"feasibility", "confirmatory"}, "execution order")
    _expect(
        tuple(_list(order.get("feasibility"), "feasibility order")),
        (
            "validate_static_sources_schemas_and_fail_closed_templates",
            "require_exact_h00_approval_and_attestation_before_path_resolution",
            "deep_validate_actual_h00_scope",
            "validate_actual_delivery_predecessor_aggregate_receipt",
            "validate_frozen_feasibility_operational_preregistration",
            "validate_frozen_feasibility_c0_c3_c5_manual_bundle",
            "validate_active_feasibility_execution_authorization",
            "stop_before_recruitment_consent_randomization_audio_intervention_rating_metric_or_output",
        ),
        "feasibility order",
    )
    _expect(
        tuple(_list(order.get("confirmatory"), "confirmatory order")),
        (
            "validate_static_sources_schemas_and_fail_closed_templates",
            "require_exact_h00_approval_and_attestation_before_path_resolution",
            "deep_validate_actual_h00_scope",
            "validate_actual_feasibility_completion",
            "validate_actual_h01_manual_final_n_lock",
            "validate_frozen_h04_sap",
            "validate_actual_delivery_predecessor_aggregate_receipt",
            "validate_frozen_confirmatory_operational_preregistration",
            "validate_frozen_confirmatory_c0_c3_c5_manual_bundle",
            "validate_active_confirmatory_execution_authorization",
            "stop_before_new_confirmatory_recruitment_randomization_audio_intervention_rating_metric_or_output",
        ),
        "confirmatory order",
    )

    outputs = _mapping(config.get("outputs"), "outputs")
    _expect_keys(
        outputs,
        {
            "restricted_root",
            "feasibility_handoff_path",
            "confirmatory_handoff_path",
            "phase_separation_path",
            "feasibility_comparison_stage_separation_path",
            "confirmatory_comparison_stage_separation_path",
            "registry_path",
            "failure_path",
            "no_overwrite",
            "atomic_publish_required",
            "write_enabled_in_this_package",
        },
        "outputs",
    )
    for field in {
        "restricted_root",
        "feasibility_handoff_path",
        "confirmatory_handoff_path",
        "phase_separation_path",
        "feasibility_comparison_stage_separation_path",
        "confirmatory_comparison_stage_separation_path",
        "registry_path",
        "failure_path",
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
    """Validate exactly the pinned C1/A1 sources and no runtime evidence."""

    root = workspace_root.resolve(strict=True)
    access = audit if audit is not None else AccessAudit()
    _validate_config_values(config)
    absolute_config = config_path if config_path.is_absolute() else root / config_path
    try:
        relative_config = absolute_config.relative_to(root).as_posix()
    except ValueError as exc:
        raise D09ContractError("D09 config must be inside workspace") from exc
    config_data, config_snapshot = _read_file(root, relative_config, "D09 config")
    if _load_config_bytes(config_data) != config:
        raise D09ContractError("D09 config changed between load and validation")

    pins = _list(
        _mapping(config.get("canonical_sources"), "canonical sources").get("pins"),
        "canonical pins",
    )
    if len(pins) != len(SOURCE_PATHS):
        raise D09ContractError("canonical source pin count drift")
    seen: set[str] = set()
    snapshots: list[FileSnapshot] = []
    payload_by_path: dict[str, bytes] = {}
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        if relative in seen:
            raise D09ContractError(f"duplicate source pin: {relative}")
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_sha:
            raise D09ContractError(
                f"canonical source SHA-256 drift: {relative}",
                stage="source_pin",
                audit=access,
            )
        seen.add(relative)
        snapshots.append(snapshot)
        payload_by_path[relative] = data
    if set(seen) != set(SOURCE_PATHS):
        raise D09ContractError("canonical source path set drift")
    by_path = {snapshot.relative_path: snapshot for snapshot in snapshots}

    schema_roots = {
        "configs/human_research/d09_h02_h03_participant_v1.schema.json": PARTICIPANT_KEYS,
        "configs/human_research/d09_h02_h03_session_v1.schema.json": SESSION_KEYS,
        "configs/human_research/d09_h02_h03_audio_v1.schema.json": AUDIO_KEYS,
        "configs/human_research/d09_h02_h03_audio_qc_v1.schema.json": QC_KEYS,
        "configs/human_research/d09_h02_h03_randomization_v1.schema.json": RANDOMIZATION_KEYS,
        "configs/human_research/d09_h02_h03_intervention_exposure_v1.schema.json": EXPOSURE_KEYS,
        "configs/human_research/d09_h02_h03_blind_rating_v1.schema.json": RATING_KEYS,
        "configs/human_research/d09_h02_h03_disposition_v1.schema.json": DISPOSITION_KEYS,
        "configs/human_research/d09_h02_h03_delivery_predecessor_receipt_v1.schema.json": DELIVERY_KEYS,
        "configs/human_research/d09_h02_h03_phase_separation_receipt_v1.schema.json": SEPARATION_KEYS,
        "configs/human_research/d09_h02_h03_comparison_stage_separation_receipt_v1.schema.json": COMPARISON_STAGE_SEPARATION_KEYS,
    }
    for path, keys in schema_roots.items():
        _validate_schema_root(
            _load_json_bytes(payload_by_path[path], f"schema {path}"),
            root_keys=keys,
            name=f"schema {path}",
        )
    _validate_templates(payload_by_path)

    bindings: list[tuple[Mapping[str, Any], str, str, str]] = []
    h00 = _mapping(config.get("predecessor_h00"), "H00")
    h04 = _mapping(config.get("h04_handoff"), "H04")
    comparison_separation = _mapping(
        config.get("comparison_stage_separation"),
        "comparison-stage separation",
    )
    delivery = _mapping(config.get("delivery_predecessors"), "delivery")
    data_config = _mapping(config.get("data_contract"), "data")
    prereg = _mapping(config.get("operational_preregistration"), "prereg")
    manual = _mapping(config.get("intervention_manual"), "manual")
    auth = _mapping(config.get("execution_authorization"), "authorization")
    bindings.extend(
        [
            (
                h00,
                "config_path",
                "config_sha256",
                "configs/experiments/h00_human_research_approval_v1.yaml",
            ),
            (
                h00,
                "contract_path",
                "contract_sha256",
                "src/voice_coach/human_research/h00_approval_contract.py",
            ),
            (
                h04,
                "config_path",
                "config_sha256",
                "configs/experiments/h04_learning_effect_analysis_v1.yaml",
            ),
            (
                h04,
                "contract_path",
                "contract_sha256",
                "src/voice_coach/human_research/h04_learning_effect_contract.py",
            ),
            (
                h04,
                "feasibility_schema_path",
                "feasibility_schema_sha256",
                "configs/human_research/h04_feasibility_pilot_completion_v1.schema.json",
            ),
            (
                h04,
                "h01_schema_path",
                "h01_schema_sha256",
                "configs/human_research/h04_h01_final_sample_size_lock_v1.schema.json",
            ),
            (
                h04,
                "sap_template_path",
                "sap_template_sha256",
                "configs/human_research/h04_statistical_analysis_plan_v1.template.json",
            ),
            (
                h04,
                "confirmatory_schema_path",
                "confirmatory_schema_sha256",
                "configs/human_research/h04_h02_h03_blind_bundle_completion_v1.schema.json",
            ),
            (
                h04,
                "phase_separation_schema_path",
                "phase_separation_schema_sha256",
                "configs/human_research/d09_h02_h03_phase_separation_receipt_v1.schema.json",
            ),
            (
                comparison_separation,
                "schema_path",
                "schema_sha256",
                "configs/human_research/d09_h02_h03_comparison_stage_separation_receipt_v1.schema.json",
            ),
            (
                delivery,
                "receipt_schema_path",
                "receipt_schema_sha256",
                "configs/human_research/d09_h02_h03_delivery_predecessor_receipt_v1.schema.json",
            ),
            (
                prereg,
                "template_path",
                "template_sha256",
                "configs/human_research/d09_h02_h03_operational_preregistration_v1.template.json",
            ),
            (
                manual,
                "template_path",
                "template_sha256",
                "configs/human_research/d09_h02_h03_intervention_manual_v1.template.json",
            ),
            (
                auth,
                "template_path",
                "template_sha256",
                "configs/human_research/d09_h02_h03_execution_authorization_v1.template.json",
            ),
        ]
    )
    for name, path, field in (
        ("participant", "d09_h02_h03_participant_v1.schema.json", "participant"),
        ("session", "d09_h02_h03_session_v1.schema.json", "session"),
        ("audio", "d09_h02_h03_audio_v1.schema.json", "audio"),
        ("audio_qc", "d09_h02_h03_audio_qc_v1.schema.json", "audio_qc"),
        ("randomization", "d09_h02_h03_randomization_v1.schema.json", "randomization"),
        (
            "intervention_exposure",
            "d09_h02_h03_intervention_exposure_v1.schema.json",
            "intervention_exposure",
        ),
        ("blind_rating", "d09_h02_h03_blind_rating_v1.schema.json", "blind_rating"),
        ("disposition", "d09_h02_h03_disposition_v1.schema.json", "disposition"),
    ):
        del name
        bindings.append(
            (
                data_config,
                f"{field}_schema_path",
                f"{field}_schema_sha256",
                f"configs/human_research/{path}",
            )
        )
    for name in ("s108", "e01", "c02", "e02", "c03", "e00", "r03", "r04"):
        section = _mapping(delivery.get(name), f"delivery {name}")
        source_prefix = {
            "s108": (
                "configs/experiments/wavlm_s108_no_locked_dry_run_v1.yaml",
                "src/voice_coach/training/wavlm_s108_dry_run.py",
            ),
            "e01": (
                "configs/experiments/e01_metamorphic_robustness_v1.yaml",
                "src/voice_coach/evaluation/e01_metamorphic_contract.py",
            ),
            "c02": (
                "configs/experiments/c02_deterministic_coaching_selection_v1.yaml",
                "src/voice_coach/coaching/c02_selection_contract.py",
            ),
            "e02": (
                "configs/experiments/e02_confidence_guard_v1.yaml",
                "src/voice_coach/evaluation/e02_confidence_guard_contract.py",
            ),
            "c03": (
                "configs/experiments/c03_expert_coaching_audit_v1.yaml",
                "src/voice_coach/coaching/c03_expert_audit_contract.py",
            ),
            "e00": (
                "configs/experiments/e00_closed_loop_demo_v1.yaml",
                "src/voice_coach/evaluation/e00_closed_loop_contract.py",
            ),
            "r03": (
                "configs/experiments/r03_reference_bank_selection_v1.yaml",
                "src/voice_coach/references/r03_selection_contract.py",
            ),
            "r04": (
                "configs/experiments/r04_blind_multireference_evaluation_v1.yaml",
                "src/voice_coach/references/r04_blind_evaluation_contract.py",
            ),
        }[name]
        bindings.extend(
            [
                (section, "config_path", "config_sha256", source_prefix[0]),
                (section, "contract_path", "contract_sha256", source_prefix[1]),
            ]
        )
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
        if field.endswith("_path") or field == "restricted_root"
    ]
    if len(output_paths) != len(set(output_paths)):
        raise D09ContractError("D09 output paths must be unique")
    for output in output_paths:
        output_parts = PurePosixPath(output).parts
        for source in seen:
            source_parts = PurePosixPath(source).parts
            if (
                output_parts == source_parts
                or output_parts[: len(source_parts)] == source_parts
                or source_parts[: len(output_parts)] == output_parts
            ):
                raise D09ContractError(
                    f"D09 output/source overlap prohibited: {output} / {source}"
                )

    policy = {
        key: config[key]
        for key in (
            "predecessor_h00",
            "h04_handoff",
            "comparison_stage_separation",
            "delivery_predecessors",
            "data_contract",
            "operational_preregistration",
            "intervention_manual",
            "execution_authorization",
            "phase_contract",
            "governance",
            "non_execution",
            "execution_order",
        )
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        source_snapshots=tuple(snapshots),
    )


def _sync_delegated_audit(result: object, audit: AccessAudit) -> None:
    values = getattr(result, "audit", {})
    if not isinstance(values, Mapping):
        return
    audit.h00_approval_path_resolution_count += int(
        values.get("approval_path_resolution_count", 0)
    )
    audit.h00_approval_read_count += int(values.get("approval_read_count", 0))
    audit.h00_attestation_path_resolution_count += int(
        values.get("attestation_path_resolution_count", 0)
    )
    audit.h00_attestation_read_count += int(values.get("attestation_read_count", 0))
    audit.h00_restricted_document_resolution_count += int(
        values.get("restricted_official_document_resolution_count", 0)
    )
    audit.h00_restricted_document_read_count += int(
        values.get("restricted_official_document_read_count", 0)
    )


def deep_validate_h00(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> H00Evidence:
    """Require actual H00 evidence before resolving either evidence path."""

    section = _mapping(config.get("predecessor_h00"), "H00")
    approval_sha = _string(section.get("approval_sha256"), "H00 approval SHA")
    attestation_sha = _string(
        section.get("attestation_sha256"),
        "H00 attestation SHA",
    )
    if approval_sha == TBD_H00_APPROVAL or attestation_sha == TBD_H00_ATTESTATION:
        raise D09ContractError(
            "actual H00 approval and attestation hashes are required",
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
    approval_config = _mapping(
        h00_config.get("official_approval"),
        "H00 official approval",
    )
    attestation_config = _mapping(
        h00_config.get("official_document_attestation"),
        "H00 official document attestation",
    )
    for h00_actual, h00_expected, name in (
        (
            approval_config.get("approval_path"),
            section.get("approval_path"),
            "approval path",
        ),
        (
            approval_config.get("approval_sha256"),
            approval_sha,
            "approval SHA",
        ),
        (
            attestation_config.get("attestation_path"),
            section.get("attestation_path"),
            "attestation path",
        ),
        (
            attestation_config.get("attestation_sha256"),
            attestation_sha,
            "attestation SHA",
        ),
    ):
        _expect(h00_actual, h00_expected, f"H00 {name} binding")
    module = _load_module(
        workspace_root=workspace_root,
        relative=_relative_string(section.get("contract_path"), "H00 contract path"),
        expected_sha256=_sha256(section.get("contract_sha256"), "H00 contract SHA"),
        module_name="_voice_coach_d09_pinned_h00_contract",
    )
    audit.h00_contract_load_count += 1
    runner = getattr(module, "run_full_preflight", None)
    if not callable(runner):
        raise D09ContractError(
            "pinned H00 contract has no full preflight",
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
        delegated = getattr(exc, "audit", None)
        if delegated is not None:
            proxy = type(
                "_DelegatedAudit",
                (),
                {"audit": getattr(delegated, "as_dict", lambda: {})()},
            )()
            _sync_delegated_audit(proxy, audit)
        raise D09ContractError(
            f"H00 deep validation failed: {exc}",
            stage="h00_authorization",
            audit=audit,
        ) from exc
    _sync_delegated_audit(result, audit)
    audit.h00_deep_validation_count += 1
    _expect(result.status, section.get("required_status"), "H00 status")
    _expect(result.config_sha256, config_snapshot.sha256, "H00 config binding")
    _expect(result.approval_sha256, approval_sha, "H00 approval binding")
    _expect(result.attestation_sha256, attestation_sha, "H00 attestation binding")
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(config_snapshot,),
        audit=audit,
        stage="h00_config",
    )
    return H00Evidence(approval_sha, attestation_sha)


def _load_h04_contract(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> tuple[Mapping[str, Any], FileSnapshot, ModuleType]:
    section = _mapping(config.get("h04_handoff"), "H04 handoff")
    config_data, config_snapshot = _read_file(
        workspace_root,
        _relative_string(section.get("config_path"), "H04 config path"),
        "H04 config",
    )
    audit.h04_config_read_count += 1
    _expect(config_snapshot.sha256, section.get("config_sha256"), "H04 config SHA")
    h04_config = _load_config_bytes(config_data)
    module = _load_module(
        workspace_root=workspace_root,
        relative=_relative_string(section.get("contract_path"), "H04 contract path"),
        expected_sha256=_sha256(section.get("contract_sha256"), "H04 contract SHA"),
        module_name="_voice_coach_d09_pinned_h04_contract",
    )
    audit.h04_contract_load_count += 1
    return h04_config, config_snapshot, module


def deep_validate_h04_confirmatory_prerequisites(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    h00: H00Evidence,
    audit: AccessAudit,
) -> H04PhaseEvidence:
    """Validate feasibility -> H01 manual lock -> SAP, in that exact order."""

    section = _mapping(config.get("h04_handoff"), "H04 handoff")
    feasibility_sha = _string(
        section.get("feasibility_completion_sha256"),
        "feasibility completion SHA",
    )
    if feasibility_sha == TBD_FEASIBILITY:
        raise D09ContractError(
            "actual feasibility completion is required before H01",
            stage="feasibility_pilot_completion",
            audit=audit,
        )
    _sha256(feasibility_sha, "feasibility completion SHA")
    h04_config, config_snapshot, module = _load_h04_contract(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    h04_h00 = module.H00Evidence(
        config_sha256=_sha256(
            _mapping(h04_config.get("predecessor_h00"), "H04 H00").get("config_sha256"),
            "H04 H00 config SHA",
        ),
        approval_sha256=h00.approval_sha256,
        attestation_sha256=h00.attestation_sha256,
    )
    audit.feasibility_completion_path_resolution_count += 1
    feasibility_payload, feasibility_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            section.get("feasibility_completion_path"),
            "feasibility completion path",
        ),
        expected_sha256=feasibility_sha,
        name="H04 feasibility completion",
    )
    audit.feasibility_completion_read_count += 1
    try:
        module.validate_feasibility_pilot_payload(
            feasibility_payload,
            h00=h04_h00,
        )
    except Exception as exc:
        raise D09ContractError(
            f"H04 feasibility validation failed: {exc}",
            stage="feasibility_pilot_completion",
            audit=audit,
        ) from exc
    feasibility = module.FeasibilityEvidence(
        feasibility_snapshot.sha256,
        feasibility_payload,
        None,
    )

    h01_sha = _string(section.get("h01_lock_sha256"), "H01 lock SHA")
    if h01_sha == TBD_H01:
        raise D09ContractError(
            "actual manual H01 final-N lock is required after feasibility",
            stage="h01_final_n_lock",
            audit=audit,
        )
    _sha256(h01_sha, "H01 lock SHA")
    audit.h01_lock_path_resolution_count += 1
    h01_payload, h01_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(section.get("h01_lock_path"), "H01 lock path"),
        expected_sha256=h01_sha,
        name="H01 final sample-size lock",
    )
    audit.h01_lock_read_count += 1
    h04_h01_config = _mapping(h04_config.get("predecessor_h01"), "H04 H01")
    try:
        module.validate_h01_lock_payload(
            h01_payload,
            h00=h04_h00,
            feasibility=feasibility,
            source_config_sha256=_sha256(
                h04_h01_config.get("source_planning_config_sha256"),
                "H04 planning config SHA",
            ),
            source_artifact_sha256=_sha256(
                h04_h01_config.get("source_planning_artifact_sha256"),
                "H04 planning artifact SHA",
            ),
        )
    except Exception as exc:
        raise D09ContractError(
            f"H01 final-N lock validation failed: {exc}",
            stage="h01_final_n_lock",
            audit=audit,
        ) from exc
    h01 = module.H01Evidence(h01_snapshot.sha256, h01_payload, None)

    sap_sha = _string(section.get("frozen_sap_sha256"), "H04 SAP SHA")
    if sap_sha == TBD_SAP:
        raise D09ContractError(
            "actual frozen H04 SAP is required after H01",
            stage="h04_sap",
            audit=audit,
        )
    _sha256(sap_sha, "H04 SAP SHA")
    audit.sap_path_resolution_count += 1
    sap_payload, sap_snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(section.get("frozen_sap_path"), "H04 SAP path"),
        expected_sha256=sap_sha,
        name="frozen H04 SAP",
    )
    audit.sap_read_count += 1
    try:
        module.validate_sap_payload(sap_payload, h00=h04_h00, h01=h01)
    except Exception as exc:
        raise D09ContractError(
            f"H04 SAP validation failed: {exc}",
            stage="h04_sap",
            audit=audit,
        ) from exc
    audit.h04_deep_validation_count += 3
    for snapshot, name in (
        (config_snapshot, "H04 config"),
        (feasibility_snapshot, "H04 feasibility completion"),
        (h01_snapshot, "H01 final-N lock"),
        (sap_snapshot, "H04 SAP"),
    ):
        _verify_snapshot(workspace_root, snapshot, name)
    return H04PhaseEvidence(
        feasibility_sha256=feasibility_snapshot.sha256,
        feasibility_payload=feasibility_payload,
        h01_sha256=h01_snapshot.sha256,
        h01_payload=h01_payload,
        sap_sha256=sap_snapshot.sha256,
        sap_payload=sap_payload,
        module=module,
    )


def _delivery_completion_sha(
    section: Mapping[str, Any],
    name: str,
) -> str:
    field = {
        "s108": "completion_sha256",
        "e01": "completion_sha256",
        "c02": "completion_sha256",
        "e02": "completion_sha256",
        "c03": "report_sha256",
        "e00": "completion_sha256",
        "r03": "bank_sha256",
        "r04": "completion_sha256",
    }[name]
    value = _string(section.get(field), f"delivery {name} completion SHA")
    expected_placeholder = {
        "s108": "TBD_S108_COMPLETION",
        "e01": "TBD_E01_COMPLETION",
        "c02": "TBD_C02_COMPLETION",
        "e02": "TBD_E02_COMPLETION",
        "c03": "TBD_C03_COMPLETION",
        "e00": "TBD_E00_COMPLETION",
        "r03": "TBD_R03_COMPLETION",
        "r04": "TBD_R04_COMPLETION",
    }[name]
    if value == expected_placeholder:
        raise D09ContractError(
            f"actual {name} delivery completion is required",
            stage="delivery_predecessors",
        )
    return _sha256(value, f"delivery {name} completion SHA")


def validate_delivery_receipt_payload(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
) -> None:
    """Validate an A1 receipt without resolving any protected predecessor artifact."""

    _reject_nonfinite_tree(payload, "delivery receipt")
    if _contains_tbd(payload):
        raise D09ContractError("delivery receipt contains unresolved TBD value")
    _expect_keys(payload, DELIVERY_KEYS, "delivery receipt")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_delivery_predecessor_receipt.v1",
        "delivery schema",
    )
    if not re.fullmatch(
        r"d09delivery_[0-9a-f]{32}",
        _string(payload.get("receipt_id"), "delivery receipt ID"),
    ):
        raise D09ContractError("invalid delivery receipt ID")
    _expect(
        payload.get("status"),
        "all_c3_c5_delivery_predecessors_deep_validated",
        "delivery status",
    )
    _utc(payload.get("validated_at_utc"), "delivery validated at")
    delivery_config = _mapping(config.get("delivery_predecessors"), "delivery")
    completion_fields = {
        "s108": "completion_sha256",
        "e01": "completion_sha256",
        "c02": "completion_sha256",
        "e02": "completion_sha256",
        "c03": "report_sha256",
        "e00": "completion_sha256",
        "r03": "bank_manifest_sha256",
        "r04": "completion_sha256",
    }
    for name in ("s108", "e01", "c02", "e02", "c03", "e00", "r03", "r04"):
        value = _mapping(payload.get(name), f"delivery receipt {name}")
        _expect_keys(value, DELIVERY_PAYLOAD_KEYS[name], f"delivery receipt {name}")
        source = _mapping(delivery_config.get(name), f"delivery config {name}")
        _expect(
            value.get("config_sha256"),
            source.get("config_sha256"),
            f"delivery {name} config binding",
        )
        _expect(
            value.get("contract_sha256"),
            source.get("contract_sha256"),
            f"delivery {name} contract binding",
        )
        _expect(
            value.get(completion_fields[name]),
            _delivery_completion_sha(source, name),
            f"delivery {name} completion binding",
        )
        for field in DELIVERY_PAYLOAD_KEYS[name]:
            if field.endswith("_sha256"):
                _sha256(value.get(field), f"delivery {name}.{field}")
    e00_source = _mapping(delivery_config.get("e00"), "delivery config e00")
    d08_lineage_sha = _string(
        e00_source.get("d08_rights_lineage_sha256"),
        "E00 D08 rights lineage SHA",
    )
    if d08_lineage_sha == "TBD_E00_D08_R03_LINEAGE":
        raise D09ContractError("actual E00 D08 rights lineage hash is required")
    _sha256(d08_lineage_sha, "E00 D08 rights lineage SHA")
    _expect(
        _mapping(payload.get("e00"), "delivery receipt e00").get(
            "d08_rights_lineage_sha256"
        ),
        d08_lineage_sha,
        "E00 D08 rights lineage binding",
    )
    _expect(payload.get("privacy_zone"), "A1_aggregate_delivery_readiness", "zone")
    assurance = _mapping(payload.get("assurance"), "delivery assurance")
    _expect_keys(assurance, DELIVERY_ASSURANCE_KEYS, "delivery assurance")
    for field in (
        "c3_uses_only_validated_s108_e01_c02_e02_c03",
        "c5_uses_validated_s108_e01_c02_e02_c03_e00_r03_r04",
        "e00_d08_rights_lineage_bound",
        "aggregate_only",
    ):
        _expect(assurance.get(field), True, f"delivery assurance.{field}")
    for field in DELIVERY_ASSURANCE_KEYS - {
        "c3_uses_only_validated_s108_e01_c02_e02_c03",
        "c5_uses_validated_s108_e01_c02_e02_c03_e00_r03_r04",
        "e00_d08_rights_lineage_bound",
        "aggregate_only",
    }:
        _expect(assurance.get(field), False, f"delivery assurance.{field}")
    _expect(payload.get("binding_sha256"), binding_sha256(payload), "delivery binding")


def deep_validate_delivery_receipt(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> DeliveryEvidence:
    section = _mapping(config.get("delivery_predecessors"), "delivery")
    expected_sha = _string(section.get("receipt_sha256"), "delivery receipt SHA")
    if expected_sha == TBD_DELIVERY:
        raise D09ContractError(
            "actual aggregate delivery predecessor receipt is required",
            stage="delivery_predecessors",
            audit=audit,
        )
    _sha256(expected_sha, "delivery receipt SHA")
    # Check every completion pin before resolving the aggregate receipt path.
    for name in ("s108", "e01", "c02", "e02", "c03", "e00", "r03", "r04"):
        try:
            _delivery_completion_sha(
                _mapping(section.get(name), f"delivery {name}"),
                name,
            )
        except D09ContractError as exc:
            raise D09ContractError(
                str(exc),
                stage="delivery_predecessors",
                audit=audit,
            ) from exc
    d08_lineage_sha = _string(
        _mapping(section.get("e00"), "delivery e00").get("d08_rights_lineage_sha256"),
        "E00 D08 rights lineage SHA",
    )
    if d08_lineage_sha == "TBD_E00_D08_R03_LINEAGE":
        raise D09ContractError(
            "actual E00 D08 rights lineage hash is required",
            stage="delivery_predecessors",
            audit=audit,
        )
    _sha256(d08_lineage_sha, "E00 D08 rights lineage SHA")
    audit.delivery_receipt_path_resolution_count += 1
    payload, snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(section.get("receipt_path"), "delivery receipt path"),
        expected_sha256=expected_sha,
        name="D09 aggregate delivery predecessor receipt",
    )
    audit.delivery_receipt_read_count += 1
    validate_delivery_receipt_payload(payload, config=config)
    audit.delivery_deep_validation_count += 1
    _verify_snapshot(workspace_root, snapshot, "delivery receipt")
    return DeliveryEvidence(snapshot.sha256, payload)


def _phase_lineage(
    *,
    phase: str,
    h04: H04PhaseEvidence | None,
) -> tuple[str, str, str]:
    if phase == "feasibility":
        if h04 is not None:
            raise D09ContractError(
                "feasibility phase must not consume H01/SAP evidence"
            )
        return ZERO_SHA256, ZERO_SHA256, ZERO_SHA256
    if phase != "confirmatory" or h04 is None:
        raise D09ContractError("confirmatory phase requires feasibility, H01, and SAP")
    return h04.feasibility_sha256, h04.h01_sha256, h04.sap_sha256


def _validate_safe_id_fields(
    section: Mapping[str, Any],
    fields: set[str],
    name: str,
) -> None:
    for field in fields:
        _safe_id(section.get(field), f"{name}.{field}")


def validate_preregistration_payload(
    payload: Mapping[str, Any],
    *,
    phase: str,
    h00: H00Evidence,
    h04: H04PhaseEvidence | None,
    delivery: DeliveryEvidence,
) -> None:
    """Validate an exact, public, frozen operational preregistration."""

    if phase not in PHASES:
        raise D09ContractError("invalid phase")
    _reject_nonfinite_tree(payload, "operational preregistration")
    if _contains_tbd(payload):
        raise D09ContractError("operational preregistration contains TBD")
    _expect_keys(payload, PREREG_KEYS, "operational preregistration")
    for section_name, keys in PREREG_SECTION_KEYS.items():
        _expect_keys(
            _mapping(payload.get(section_name), f"prereg {section_name}"),
            keys,
            f"prereg {section_name}",
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_operational_preregistration.v1",
        "prereg schema",
    )
    if not re.fullmatch(
        r"d09prereg_[0-9a-f]{32}",
        _string(payload.get("preregistration_id"), "preregistration ID"),
    ):
        raise D09ContractError("invalid preregistration ID")
    _expect(payload.get("phase"), phase, "prereg phase")
    _expect(
        payload.get("status"),
        "approved_public_and_frozen_before_phase_recruitment",
        "prereg status",
    )
    _utc(payload.get("frozen_at_utc"), "prereg frozen at")
    feasibility_sha, h01_sha, sap_sha = _phase_lineage(phase=phase, h04=h04)
    lineage = _mapping(payload.get("lineage"), "prereg lineage")
    for field, expected in (
        ("h00_approval_sha256", h00.approval_sha256),
        ("h00_attestation_sha256", h00.attestation_sha256),
        ("delivery_predecessor_receipt_sha256", delivery.sha256),
        ("feasibility_completion_sha256", feasibility_sha),
        ("h01_final_sample_size_lock_sha256", h01_sha),
        ("frozen_sap_sha256", sap_sha),
    ):
        _expect(lineage.get(field), expected, f"prereg lineage.{field}")
    _sha256(lineage.get("protocol_sha256"), "prereg protocol SHA")

    eligibility = _mapping(
        payload.get("eligibility_and_recruitment"),
        "prereg eligibility",
    )
    _validate_safe_id_fields(
        eligibility,
        {
            "exact_eligibility_rule_id",
            "exact_exclusion_rule_id",
            "exact_recruitment_channel_id",
            "exact_screening_workflow_id",
            "minor_policy_id",
            "compensation_schedule_id",
        },
        "prereg eligibility",
    )
    _sha256(
        eligibility.get("coercion_review_receipt_sha256"),
        "prereg coercion review",
    )

    sample = _mapping(payload.get("sample_size"), "prereg sample size")
    stage_1_n = _integer(
        sample.get("stage_1_final_enrollment_per_arm"),
        "prereg stage 1 N",
        minimum=1,
    )
    stage_2_n = _integer(
        sample.get("stage_2_final_enrollment_per_arm"),
        "prereg stage 2 N",
        minimum=1,
    )
    _expect(sample.get("feasibility_per_arm_minimum"), 15, "feasibility minimum")
    _expect(sample.get("feasibility_per_arm_maximum"), 20, "feasibility maximum")
    _expect(sample.get("automatic_final_n_lock_allowed"), False, "automatic N lock")
    if phase == "feasibility":
        if not 15 <= stage_1_n <= 20 or not 15 <= stage_2_n <= 20:
            raise D09ContractError("feasibility target must be 15-20 per arm")
        _expect(
            sample.get("confirmatory_n_must_equal_h01_lock"),
            False,
            "feasibility H01 N binding",
        )
    else:
        assert h04 is not None
        comparisons = _mapping(h04.h01_payload.get("comparisons"), "H01 comparisons")
        expected_stage_1 = _integer(
            _mapping(
                comparisons.get("stage_1_c0_vs_c3"),
                "H01 stage 1",
            ).get("final_enrollment_per_arm"),
            "H01 stage 1 per-arm N",
            minimum=1,
        )
        expected_stage_2 = _integer(
            _mapping(
                comparisons.get("stage_2_c3_vs_c5"),
                "H01 stage 2",
            ).get("final_enrollment_per_arm"),
            "H01 stage 2 per-arm N",
            minimum=1,
        )
        _expect(stage_1_n, expected_stage_1, "confirmatory stage 1 H01 N")
        _expect(stage_2_n, expected_stage_2, "confirmatory stage 2 H01 N")
        _expect(
            sample.get("confirmatory_n_must_equal_h01_lock"),
            True,
            "confirmatory H01 N binding",
        )

    schedule = _mapping(payload.get("schedule_and_items"), "prereg schedule")
    _expect(schedule.get("intervention_duration_days"), 14, "duration")
    _expect(schedule.get("post_timepoint"), "end_of_two_week_intervention", "post")
    _expect(schedule.get("retention_days_after_post"), 28, "retention")
    _expect(schedule.get("timepoints"), list(TIMEPOINTS), "timepoints")
    for field in ("fixed_practice_item_card_sha256", "fixed_transfer_item_card_sha256"):
        _sha256(schedule.get(field), f"prereg schedule.{field}")
    _expect(
        schedule.get("same_assessment_items_across_timepoints"),
        True,
        "fixed assessment items",
    )
    _expect(
        schedule.get("transfer_items_excluded_from_training"),
        True,
        "transfer training exclusion",
    )

    randomization = _mapping(payload.get("randomization"), "prereg randomization")
    for randomization_actual, randomization_expected, name in (
        (randomization.get("allocation_unit"), "participant", "allocation unit"),
        (randomization.get("allocation_ratio"), "1:1", "allocation ratio"),
        (
            randomization.get("strata"),
            ["l1", "baseline_comprehensibility"],
            "strata",
        ),
        (
            randomization.get("stage_1_comparison_id"),
            "c3_minus_c0",
            "stage 1 comparison",
        ),
        (
            randomization.get("stage_1_allowed_conditions"),
            ["C0", "C3"],
            "stage 1 conditions",
        ),
        (
            randomization.get("stage_1_delivery_gate_components"),
            ["S108", "E01", "C02", "E02", "C03"],
            "stage 1 delivery gate",
        ),
        (
            randomization.get("stage_2_comparison_id"),
            "c5_minus_c3",
            "stage 2 comparison",
        ),
        (
            randomization.get("stage_2_allowed_conditions"),
            ["C3", "C5"],
            "stage 2 conditions",
        ),
        (
            randomization.get("stage_2_delivery_gate_components"),
            ["S108", "E01", "C02", "E02", "C03", "E00", "R03", "R04"],
            "stage 2 delivery gate",
        ),
        (
            randomization.get("stage_randomizations_separate"),
            True,
            "separate stage randomization",
        ),
        (
            randomization.get("comparison_participant_audio_rating_reuse_allowed"),
            False,
            "comparison reuse",
        ),
        (
            randomization.get("stage_2_activation_basis"),
            "approval_safety_privacy_and_delivery_readiness_only",
            "stage 2 basis",
        ),
        (
            randomization.get(
                "stage_1_unblinded_effect_access_count_before_activation"
            ),
            0,
            "pre-activation unblinded access count",
        ),
        (
            randomization.get("stage_1_outcome_or_effect_available_at_activation"),
            False,
            "pre-activation effect availability",
        ),
        (
            randomization.get("unblinded_effect_used_for_stage_2_activation"),
            False,
            "stage 2 effect use",
        ),
    ):
        _expect(
            randomization_actual,
            randomization_expected,
            f"prereg randomization {name}",
        )
    _validate_safe_id_fields(
        randomization,
        {
            "allocation_concealment_workflow_id",
            "stage_1_randomization_receipt_rule_id",
            "stage_2_randomization_receipt_rule_id",
            "stage_2_activation_time_order_rule_id",
            "stage_1_unblinded_effect_access_log_rule_id",
        },
        "prereg randomization",
    )

    audio_qc = _mapping(payload.get("audio_qc"), "prereg audio QC")
    _validate_safe_id_fields(
        audio_qc,
        {
            "exact_recording_protocol_id",
            "exact_duration_rule_id",
            "exact_snr_rule_id",
            "exact_clipping_rule_id",
            "exact_decode_rule_id",
        },
        "prereg audio QC",
    )
    _integer(audio_qc.get("exact_sample_rate_hz"), "exact sample rate", minimum=1)
    _expect(
        audio_qc.get("baseline_hard_qc_before_randomization_only"),
        True,
        "baseline QC",
    )
    _expect(
        audio_qc.get("postrandomization_qc_failure_preserved_as_missingness"),
        True,
        "postrandomization QC missingness",
    )
    exposure = _mapping(
        payload.get("exposure_and_adherence"),
        "prereg exposure",
    )
    _validate_safe_id_fields(
        exposure,
        {
            "exact_session_schedule_id",
            "exact_assigned_seconds_rule_id",
            "exact_assigned_item_count_rule_id",
            "exact_adherence_denominator_id",
            "exact_protocol_deviation_taxonomy_id",
        },
        "prereg exposure",
    )
    _expect(
        exposure.get("nonadherence_retained_after_randomization"),
        True,
        "nonadherence retention",
    )
    rating = _mapping(payload.get("blind_rating"), "prereg blind rating")
    _expect(rating.get("minimum_independent_raters"), 2, "minimum raters")
    _validate_safe_id_fields(
        rating,
        {
            "rater_qualification_rule_id",
            "rating_anchor_version_id",
            "randomization_and_blocking_rule_id",
            "adjudication_rule_id",
            "minimum_support_rule_id",
        },
        "prereg rating",
    )
    _expect(
        rating.get("raters_blinded_to_condition_timepoint_and_identity"),
        True,
        "rater blinding",
    )
    _expect(
        rating.get("participant_blinding_claim_allowed"),
        False,
        "participant blinding",
    )
    governance = _mapping(payload.get("governance"), "prereg governance")
    _validate_safe_id_fields(
        governance,
        {
            "consent_withdrawal_workflow_id",
            "retention_and_deletion_schedule_id",
            "pseudonym_key_separation_workflow_id",
            "incident_response_workflow_id",
            "pause_stop_workflow_id",
            "adverse_feedback_taxonomy_id",
        },
        "prereg governance",
    )
    _expect(
        governance.get("withdrawal_missingness_deviation_and_adverse_preserved"),
        True,
        "preserved governance outcomes",
    )
    privacy = _mapping(payload.get("privacy_and_claims"), "prereg privacy")
    for field, privacy_expected in (
        (
            "participant_audio_rating_rows_zone",
            "D1_pseudonymized_restricted_human_research",
        ),
        ("pseudonym_key_zone", "R0_direct_identifier_key_separate"),
        ("restricted_locator_zone", "R1_restricted_locator"),
        ("aggregate_completion_zone", "A1_aggregate_only"),
        ("feasibility_efficacy_claim_allowed", False),
        ("participant_audio_rating_reuse_across_phases_allowed", False),
        ("public_row_or_audio_release_allowed", False),
    ):
        _expect(
            privacy.get(field),
            privacy_expected,
            f"prereg privacy.{field}",
        )
    approval = _mapping(payload.get("approval"), "prereg approval")
    _expect(approval.get("decision"), "approved", "prereg approval")
    for field in (
        "principal_investigator_receipt_sha256",
        "statistician_receipt_sha256",
        "data_custodian_receipt_sha256",
    ):
        _sha256(approval.get(field), f"prereg approval.{field}")
    _expect(approval.get("approved_and_frozen"), True, "prereg frozen approval")
    _expect(payload.get("binding_sha256"), binding_sha256(payload), "prereg binding")


def deep_validate_preregistration(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    phase: str,
    h00: H00Evidence,
    h04: H04PhaseEvidence | None,
    delivery: DeliveryEvidence,
    audit: AccessAudit,
) -> PreregEvidence:
    section = _mapping(
        config.get("operational_preregistration"),
        "operational preregistration",
    )
    expected_sha = _string(
        section.get(f"{phase}_sha256"),
        f"{phase} preregistration SHA",
    )
    placeholder = {
        "feasibility": "TBD_D09_FEASIBILITY_PREREGISTRATION",
        "confirmatory": "TBD_D09_CONFIRMATORY_PREREGISTRATION",
    }[phase]
    if expected_sha == placeholder:
        raise D09ContractError(
            f"actual frozen {phase} operational preregistration is required",
            stage=f"{phase}_preregistration",
            audit=audit,
        )
    _sha256(expected_sha, f"{phase} preregistration SHA")
    audit.preregistration_path_resolution_count += 1
    payload, snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            section.get(f"{phase}_path"),
            f"{phase} preregistration path",
        ),
        expected_sha256=expected_sha,
        name=f"{phase} operational preregistration",
    )
    audit.preregistration_read_count += 1
    validate_preregistration_payload(
        payload,
        phase=phase,
        h00=h00,
        h04=h04,
        delivery=delivery,
    )
    audit.preregistration_deep_validation_count += 1
    _verify_snapshot(workspace_root, snapshot, f"{phase} preregistration")
    return PreregEvidence(snapshot.sha256, payload)


def _delivery_payload_completion(
    delivery: DeliveryEvidence,
    name: str,
) -> str:
    field = {
        "s108": "completion_sha256",
        "e01": "completion_sha256",
        "c02": "completion_sha256",
        "e02": "completion_sha256",
        "c03": "report_sha256",
        "e00": "completion_sha256",
        "r03": "bank_manifest_sha256",
        "r04": "completion_sha256",
    }[name]
    return _sha256(
        _mapping(delivery.payload.get(name), f"delivery {name}").get(field),
        f"delivery {name}.{field}",
    )


def validate_manual_payload(
    payload: Mapping[str, Any],
    *,
    phase: str,
    h00: H00Evidence,
    preregistration: PreregEvidence,
    delivery: DeliveryEvidence,
) -> None:
    _reject_nonfinite_tree(payload, "intervention manual")
    if _contains_tbd(payload):
        raise D09ContractError("intervention manual contains TBD")
    _expect_keys(payload, MANUAL_KEYS, "intervention manual")
    for section_name, keys in MANUAL_SECTION_KEYS.items():
        _expect_keys(
            _mapping(payload.get(section_name), f"manual {section_name}"),
            keys,
            f"manual {section_name}",
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_intervention_manual.v1",
        "manual schema",
    )
    if not re.fullmatch(
        r"d09manual_[0-9a-f]{32}",
        _string(payload.get("manual_bundle_id"), "manual bundle ID"),
    ):
        raise D09ContractError("invalid intervention manual ID")
    _expect(payload.get("phase"), phase, "manual phase")
    _expect(
        payload.get("status"),
        "approved_versioned_and_frozen",
        "manual status",
    )
    _utc(payload.get("frozen_at_utc"), "manual frozen at")
    lineage = _mapping(payload.get("lineage"), "manual lineage")
    for field, lineage_expected in (
        ("h00_approval_sha256", h00.approval_sha256),
        ("operational_preregistration_sha256", preregistration.sha256),
        ("delivery_predecessor_receipt_sha256", delivery.sha256),
    ):
        _expect(
            lineage.get(field),
            lineage_expected,
            f"manual lineage.{field}",
        )
    common = _mapping(payload.get("common_delivery"), "manual common delivery")
    _expect(common.get("two_week_duration_required"), True, "manual duration")
    _expect(
        common.get("equal_exposure_time_and_practice_count_required"),
        True,
        "manual exposure matching",
    )
    _validate_safe_id_fields(
        common,
        {
            "exact_session_schedule_id",
            "exact_exposure_matching_rule_id",
            "human_escalation_workflow_id",
        },
        "manual common delivery",
    )
    _expect(
        common.get("blocked_safe_action_override_allowed"),
        False,
        "manual safe block",
    )
    c0 = _mapping(payload.get("c0"), "manual C0")
    _safe_id(c0.get("version_id"), "manual C0 version")
    _sha256(c0.get("manual_sha256"), "manual C0 SHA")
    _expect(
        c0.get("active_control_time_and_practice_matched"),
        True,
        "C0 matching",
    )
    _expect(
        c0.get("automated_diagnostic_or_coaching_delivered"),
        False,
        "C0 coaching",
    )
    _expect(
        c0.get("multi_reference_or_coached_reattempt_delivered"),
        False,
        "C0 reference",
    )
    c3 = _mapping(payload.get("c3"), "manual C3")
    _safe_id(c3.get("version_id"), "manual C3 version")
    _sha256(c3.get("manual_sha256"), "manual C3 SHA")
    _expect(
        c3.get("structured_evidence_limited_diagnostic_and_coaching"),
        True,
        "C3 coaching",
    )
    for field, name in (
        ("s108_completion_sha256", "s108"),
        ("e01_completion_sha256", "e01"),
        ("c02_completion_sha256", "c02"),
        ("e02_completion_sha256", "e02"),
        ("c03_report_sha256", "c03"),
    ):
        _expect(
            c3.get(field),
            _delivery_payload_completion(delivery, name),
            f"C3 {name} binding",
        )
    _expect(
        c3.get("multi_reference_or_coached_reattempt_delivered"),
        False,
        "C3 reference",
    )
    c5 = _mapping(payload.get("c5"), "manual C5")
    _safe_id(c5.get("version_id"), "manual C5 version")
    _sha256(c5.get("manual_sha256"), "manual C5 SHA")
    _expect(c5.get("inherits_c3_exact_delivery"), True, "C5 inheritance")
    for field, name in (
        ("e00_completion_sha256", "e00"),
        ("r03_bank_sha256", "r03"),
        ("r04_completion_sha256", "r04"),
    ):
        _expect(
            c5.get(field),
            _delivery_payload_completion(delivery, name),
            f"C5 {name} binding",
        )
    _expect(
        c5.get("multi_reference_and_coached_reattempt_required"),
        True,
        "C5 reference",
    )
    manual_hashes = {
        _string(
            _mapping(payload.get(condition), condition).get("manual_sha256"), condition
        )
        for condition in ("c0", "c3", "c5")
    }
    if len(manual_hashes) != 3:
        raise D09ContractError("C0/C3/C5 manual hashes must be distinct")
    assurance = _mapping(payload.get("assurance"), "manual assurance")
    for field in (
        "manuals_versioned_and_immutable",
        "condition_components_incremental_only",
    ):
        _expect(assurance.get(field), True, f"manual assurance.{field}")
    for field in (
        "free_generation_allowed",
        "unapproved_reference_or_coaching_allowed",
        "participant_blinding_claimed",
    ):
        _expect(assurance.get(field), False, f"manual assurance.{field}")
    approval = _mapping(payload.get("approval"), "manual approval")
    _expect(approval.get("decision"), "approved", "manual approval")
    for field in (
        "principal_investigator_receipt_sha256",
        "intervention_owner_receipt_sha256",
        "safety_reviewer_receipt_sha256",
    ):
        _sha256(approval.get(field), f"manual approval.{field}")
    _expect(approval.get("approved_and_frozen"), True, "manual freeze")
    _expect(payload.get("binding_sha256"), binding_sha256(payload), "manual binding")


def deep_validate_manual(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    phase: str,
    h00: H00Evidence,
    preregistration: PreregEvidence,
    delivery: DeliveryEvidence,
    audit: AccessAudit,
) -> ManualEvidence:
    section = _mapping(config.get("intervention_manual"), "intervention manual")
    expected_sha = _string(
        section.get(f"{phase}_sha256"),
        f"{phase} manual SHA",
    )
    placeholder = {
        "feasibility": "TBD_D09_FEASIBILITY_MANUAL",
        "confirmatory": "TBD_D09_CONFIRMATORY_MANUAL",
    }[phase]
    if expected_sha == placeholder:
        raise D09ContractError(
            f"actual frozen {phase} intervention manual is required",
            stage=f"{phase}_manual",
            audit=audit,
        )
    _sha256(expected_sha, f"{phase} manual SHA")
    audit.manual_path_resolution_count += 1
    payload, snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            section.get(f"{phase}_path"),
            f"{phase} intervention manual path",
        ),
        expected_sha256=expected_sha,
        name=f"{phase} intervention manual",
    )
    audit.manual_read_count += 1
    validate_manual_payload(
        payload,
        phase=phase,
        h00=h00,
        preregistration=preregistration,
        delivery=delivery,
    )
    audit.manual_deep_validation_count += 1
    _verify_snapshot(workspace_root, snapshot, f"{phase} intervention manual")
    return ManualEvidence(snapshot.sha256, payload)


def _utc_without_now_check(value: object, name: str) -> datetime:
    text = _string(value, name)
    match = UTC_RE.fullmatch(text)
    if match is None:
        raise D09ContractError(f"{name} must be UTC second precision")
    try:
        return datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            tzinfo=timezone.utc,
        )
    except ValueError as exc:
        raise D09ContractError(f"{name} is not a valid timestamp") from exc


def validate_authorization_payload(
    payload: Mapping[str, Any],
    *,
    phase: str,
    h00: H00Evidence,
    h04: H04PhaseEvidence | None,
    delivery: DeliveryEvidence,
    preregistration: PreregEvidence,
    manual: ManualEvidence,
) -> None:
    _reject_nonfinite_tree(payload, "execution authorization")
    if _contains_tbd(payload):
        raise D09ContractError("execution authorization contains TBD")
    _expect_keys(payload, AUTH_KEYS, "execution authorization")
    for section_name, keys in AUTH_SECTION_KEYS.items():
        _expect_keys(
            _mapping(payload.get(section_name), f"authorization {section_name}"),
            keys,
            f"authorization {section_name}",
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_execution_authorization.v1",
        "authorization schema",
    )
    if not re.fullmatch(
        r"d09auth_[0-9a-f]{32}",
        _string(payload.get("authorization_id"), "authorization ID"),
    ):
        raise D09ContractError("invalid authorization ID")
    _expect(payload.get("phase"), phase, "authorization phase")
    _expect(payload.get("status"), "active_for_phase_operations", "authorization")
    valid_from = _utc_without_now_check(
        payload.get("valid_from_utc"),
        "authorization valid from",
    )
    valid_until = _utc_without_now_check(
        payload.get("valid_until_utc"),
        "authorization valid until",
    )
    now = datetime.now(timezone.utc)
    if not valid_from <= now < valid_until:
        raise D09ContractError("execution authorization is not currently active")
    feasibility_sha, h01_sha, sap_sha = _phase_lineage(phase=phase, h04=h04)
    lineage = _mapping(payload.get("lineage"), "authorization lineage")
    for field, authorization_expected in (
        ("h00_approval_sha256", h00.approval_sha256),
        ("h00_attestation_sha256", h00.attestation_sha256),
        ("operational_preregistration_sha256", preregistration.sha256),
        ("intervention_manual_bundle_sha256", manual.sha256),
        ("delivery_predecessor_receipt_sha256", delivery.sha256),
        ("feasibility_completion_sha256", feasibility_sha),
        ("h01_final_sample_size_lock_sha256", h01_sha),
        ("frozen_sap_sha256", sap_sha),
    ):
        _expect(
            lineage.get(field),
            authorization_expected,
            f"authorization lineage.{field}",
        )
    operations = _mapping(
        payload.get("authorized_operations"),
        "authorized operations",
    )
    for field in (
        "recruitment",
        "consent",
        "participant_randomization",
        "audio_recording",
        "intervention_delivery",
        "blind_rating",
        "restricted_d1_processing",
    ):
        _expect(operations.get(field), True, f"authorized operations.{field}")
    for field in ("statistics_or_effect_analysis", "publication"):
        _expect(operations.get(field), False, f"authorized operations.{field}")
    assurance = _mapping(
        payload.get("operational_assurance"),
        "authorization assurance",
    )
    for field in AUTH_SECTION_KEYS["operational_assurance"] - {
        "participant_blinding_claimed"
    }:
        _expect(assurance.get(field), True, f"authorization assurance.{field}")
    _expect(
        assurance.get("participant_blinding_claimed"),
        False,
        "authorization participant blinding",
    )
    approval = _mapping(payload.get("approval"), "authorization approval")
    _expect(approval.get("decision"), "approved", "authorization approval")
    for field in (
        "principal_investigator_receipt_sha256",
        "data_custodian_receipt_sha256",
        "operations_lead_receipt_sha256",
    ):
        _sha256(approval.get(field), f"authorization approval.{field}")
    _expect(approval.get("active"), True, "authorization active")
    _expect(
        payload.get("privacy_zone"),
        "A1_C1_public_authorization_no_identity",
        "authorization zone",
    )
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "authorization binding",
    )


def deep_validate_authorization(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    phase: str,
    h00: H00Evidence,
    h04: H04PhaseEvidence | None,
    delivery: DeliveryEvidence,
    preregistration: PreregEvidence,
    manual: ManualEvidence,
    audit: AccessAudit,
) -> AuthorizationEvidence:
    section = _mapping(
        config.get("execution_authorization"),
        "execution authorization",
    )
    expected_sha = _string(
        section.get(f"{phase}_sha256"),
        f"{phase} authorization SHA",
    )
    placeholder = {
        "feasibility": "TBD_D09_FEASIBILITY_AUTHORIZATION",
        "confirmatory": "TBD_D09_CONFIRMATORY_AUTHORIZATION",
    }[phase]
    if expected_sha == placeholder:
        raise D09ContractError(
            f"actual active {phase} execution authorization is required",
            stage=f"{phase}_authorization",
            audit=audit,
        )
    _sha256(expected_sha, f"{phase} authorization SHA")
    audit.authorization_path_resolution_count += 1
    payload, snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            section.get(f"{phase}_path"),
            f"{phase} authorization path",
        ),
        expected_sha256=expected_sha,
        name=f"{phase} execution authorization",
    )
    audit.authorization_read_count += 1
    validate_authorization_payload(
        payload,
        phase=phase,
        h00=h00,
        h04=h04,
        delivery=delivery,
        preregistration=preregistration,
        manual=manual,
    )
    audit.authorization_deep_validation_count += 1
    _verify_snapshot(workspace_root, snapshot, f"{phase} authorization")
    return AuthorizationEvidence(snapshot.sha256, payload)


def _validate_phase_and_condition(
    payload: Mapping[str, Any],
    *,
    phase: str | None,
    comparison_field: bool,
    name: str,
) -> tuple[str, str | None, str | None]:
    record_phase = _string(payload.get("phase"), f"{name} phase")
    if record_phase not in PHASES:
        raise D09ContractError(f"{name} phase is invalid")
    if phase is not None:
        _expect(record_phase, phase, f"{name} phase")
    comparison: str | None = None
    condition: str | None = None
    if comparison_field:
        comparison = _string(payload.get("comparison"), f"{name} comparison")
        if comparison not in COMPARISON_ARMS:
            raise D09ContractError(f"{name} comparison is invalid")
        condition = _string(
            payload.get("assigned_condition"),
            f"{name} assigned condition",
        )
        if condition not in COMPARISON_ARMS[comparison]:
            raise D09ContractError(
                f"{name} condition {condition} is not in {comparison}"
            )
    return record_phase, comparison, condition


def _validate_sha_fields(
    payload: Mapping[str, Any],
    fields: set[str],
    name: str,
) -> None:
    for field in fields:
        _sha256(payload.get(field), f"{name}.{field}")


def validate_participant_record(
    payload: Mapping[str, Any],
    *,
    phase: str | None = None,
) -> None:
    _expect_keys(payload, PARTICIPANT_KEYS, "participant")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_participant.v1",
        "participant schema",
    )
    _validate_phase_and_condition(
        payload,
        phase=phase,
        comparison_field=True,
        name="participant",
    )
    _validate_sha_fields(
        payload,
        {"record_id_sha256", "participant_key_sha256", "consent_receipt_sha256"},
        "participant",
    )
    _safe_id(payload.get("l1_stratum_id"), "participant L1 stratum")
    _safe_id(payload.get("baseline_stratum_id"), "participant baseline stratum")
    _expect(payload.get("randomized"), True, "participant randomized")
    _expect(
        payload.get("participant_blinding_claimed"),
        False,
        "participant blinding",
    )
    _expect(
        payload.get("direct_identifiers_present"),
        False,
        "participant identifiers",
    )
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_human_research",
        "participant zone",
    )


def validate_session_record(
    payload: Mapping[str, Any],
    *,
    phase: str | None = None,
) -> None:
    _expect_keys(payload, SESSION_KEYS, "session")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_session.v1",
        "session schema",
    )
    record_phase, _, _ = _validate_phase_and_condition(
        payload,
        phase=phase,
        comparison_field=False,
        name="session",
    )
    del record_phase
    _validate_sha_fields(
        payload,
        {
            "session_id_sha256",
            "participant_key_sha256",
            "protocol_deviation_receipt_sha256",
        },
        "session",
    )
    if payload.get("assigned_condition") not in {"C0", "C3", "C5"}:
        raise D09ContractError("session assigned condition is invalid")
    _integer(payload.get("session_index"), "session index", minimum=0)
    if payload.get("session_kind") not in {
        "pre_baseline",
        "intervention",
        "post_at_2_weeks",
        "retention_4_weeks_after_post",
    }:
        raise D09ContractError("session kind is invalid")
    _utc_without_now_check(payload.get("scheduled_at_utc"), "session scheduled at")
    if not isinstance(payload.get("occurred"), bool):
        raise D09ContractError("session occurred must be boolean")
    deviation = payload.get("protocol_deviation_present")
    if not isinstance(deviation, bool):
        raise D09ContractError("session deviation flag must be boolean")
    receipt = _string(
        payload.get("protocol_deviation_receipt_sha256"),
        "session deviation receipt",
    )
    if (receipt != ZERO_SHA256) != deviation:
        raise D09ContractError("session deviation flag/receipt mismatch")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_human_research",
        "session zone",
    )


def validate_audio_record(
    payload: Mapping[str, Any],
    *,
    phase: str | None = None,
) -> None:
    _expect_keys(payload, AUDIO_KEYS, "audio")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_audio.v1",
        "audio schema",
    )
    _validate_phase_and_condition(
        payload,
        phase=phase,
        comparison_field=False,
        name="audio",
    )
    comparison = _string(payload.get("comparison"), "audio comparison")
    if comparison not in COMPARISON_ARMS:
        raise D09ContractError("audio comparison is invalid")
    _validate_sha_fields(
        payload,
        {
            "audio_object_id_sha256",
            "participant_key_sha256",
            "item_card_sha256",
            "target_text_sha256",
            "recording_content_sha256",
            "recording_receipt_sha256",
        },
        "audio",
    )
    if payload.get("timepoint") not in TIMEPOINTS:
        raise D09ContractError("audio timepoint is invalid")
    if payload.get("item_set") not in {"fixed_practice", "fixed_transfer"}:
        raise D09ContractError("audio item set is invalid")
    _expect(
        payload.get("condition_time_identity_masked_for_rating"),
        True,
        "audio rating mask",
    )
    if payload.get("qc_status") not in {"passed", "failed_preserved_as_missingness"}:
        raise D09ContractError("audio QC status is invalid")
    _expect(payload.get("audio_locator_present"), False, "audio locator")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_human_research",
        "audio zone",
    )


def validate_audio_qc_record(
    payload: Mapping[str, Any],
    *,
    phase: str | None = None,
) -> None:
    _expect_keys(payload, QC_KEYS, "audio QC")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_audio_qc.v1",
        "audio QC schema",
    )
    _validate_phase_and_condition(
        payload,
        phase=phase,
        comparison_field=False,
        name="audio QC",
    )
    _validate_sha_fields(
        payload,
        {
            "qc_record_id_sha256",
            "audio_object_id_sha256",
            "frozen_qc_policy_sha256",
        },
        "audio QC",
    )
    timepoint = payload.get("timepoint")
    if timepoint not in TIMEPOINTS:
        raise D09ContractError("audio QC timepoint is invalid")
    if not isinstance(payload.get("decode_passed"), bool):
        raise D09ContractError("audio QC decode flag must be boolean")
    _integer(payload.get("sample_rate_hz"), "audio QC sample rate", minimum=1)
    _integer(payload.get("duration_ms"), "audio QC duration", minimum=0)
    _number(
        payload.get("clipping_fraction"),
        "audio QC clipping",
        minimum=0.0,
        maximum=1.0,
    )
    _number(payload.get("snr_db"), "audio QC SNR")
    _expect(
        payload.get("baseline_hard_qc_eligibility_only"),
        timepoint == "pre",
        "audio QC baseline eligibility boundary",
    )
    _expect(
        payload.get("postrandomization_exclusion_allowed"),
        False,
        "audio QC postrandomization exclusion",
    )
    status = payload.get("status")
    if status not in {"passed", "failed_preserved_as_missingness"}:
        raise D09ContractError("audio QC status is invalid")
    if status == "passed" and payload.get("decode_passed") is not True:
        raise D09ContractError("audio cannot pass QC when decoding failed")
    _safe_id(payload.get("coded_reason_id"), "audio QC coded reason")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_human_research",
        "audio QC zone",
    )


def validate_randomization_record(
    payload: Mapping[str, Any],
    *,
    delivery: DeliveryEvidence,
    phase: str | None = None,
) -> None:
    _expect_keys(payload, RANDOMIZATION_KEYS, "randomization")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_randomization.v1",
        "randomization schema",
    )
    _, comparison, _ = _validate_phase_and_condition(
        payload,
        phase=phase,
        comparison_field=True,
        name="randomization",
    )
    _validate_sha_fields(
        payload,
        {
            "assignment_receipt_id_sha256",
            "participant_key_sha256",
            "stage_randomization_receipt_sha256",
            "stage_gate_receipt_sha256",
            "s108_completion_sha256",
            "e01_completion_sha256",
            "c02_completion_sha256",
            "e02_completion_sha256",
            "c03_completion_sha256",
            "e00_completion_sha256",
            "r03_bank_sha256",
            "r04_completion_sha256",
        },
        "randomization",
    )
    _expect(payload.get("allocation_unit"), "participant", "randomization unit")
    _expect(payload.get("allocation_ratio"), "1:1", "randomization ratio")
    _safe_id(payload.get("l1_stratum_id"), "randomization L1 stratum")
    _safe_id(payload.get("baseline_stratum_id"), "randomization baseline stratum")
    _expect(
        payload.get("allocation_concealed_until_assignment"),
        True,
        "allocation concealment",
    )
    _expect(
        payload.get("unblinded_effect_used_for_stage_2_activation"),
        False,
        "randomization unblinded effect use",
    )
    common_fields = {
        "s108_completion_sha256": "s108",
        "e01_completion_sha256": "e01",
        "c02_completion_sha256": "c02",
        "e02_completion_sha256": "e02",
        "c03_completion_sha256": "c03",
    }
    for field, name in common_fields.items():
        _expect(
            payload.get(field),
            _delivery_payload_completion(delivery, name),
            f"randomization {name} gate",
        )
    if comparison == "c3_minus_c0":
        _expect(payload.get("randomization_stage"), "stage_1", "stage 1")
        _expect(
            payload.get("stage_2_activation_basis"),
            "not_applicable_stage_1",
            "stage 1 activation basis",
        )
        for field in (
            "e00_completion_sha256",
            "r03_bank_sha256",
            "r04_completion_sha256",
        ):
            _expect(payload.get(field), ZERO_SHA256, f"stage 1 {field}")
    else:
        _expect(payload.get("randomization_stage"), "stage_2", "stage 2")
        _expect(
            payload.get("stage_2_activation_basis"),
            "approval_safety_privacy_and_delivery_readiness_only",
            "stage 2 activation basis",
        )
        for field, name in (
            ("e00_completion_sha256", "e00"),
            ("r03_bank_sha256", "r03"),
            ("r04_completion_sha256", "r04"),
        ):
            _expect(
                payload.get(field),
                _delivery_payload_completion(delivery, name),
                f"stage 2 {name} gate",
            )
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_human_research",
        "randomization zone",
    )


def validate_intervention_exposure_record(
    payload: Mapping[str, Any],
    *,
    manual: ManualEvidence,
    delivery: DeliveryEvidence,
    phase: str | None = None,
) -> None:
    _expect_keys(payload, EXPOSURE_KEYS, "intervention exposure")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_intervention_exposure.v1",
        "exposure schema",
    )
    _validate_phase_and_condition(
        payload,
        phase=phase,
        comparison_field=False,
        name="exposure",
    )
    condition = _string(payload.get("assigned_condition"), "exposure condition")
    if condition not in {"C0", "C3", "C5"}:
        raise D09ContractError("exposure condition is invalid")
    _validate_sha_fields(
        payload,
        {
            "exposure_record_id_sha256",
            "participant_key_sha256",
            "session_id_sha256",
            "manual_sha256",
            "s108_completion_sha256",
            "e01_completion_sha256",
            "c02_completion_sha256",
            "e02_completion_sha256",
            "c03_completion_sha256",
            "e00_completion_sha256",
            "r03_bank_sha256",
            "r04_completion_sha256",
        },
        "exposure",
    )
    manual_section = _mapping(
        manual.payload.get(condition.lower()),
        f"manual {condition}",
    )
    _expect(
        payload.get("manual_version_id"),
        manual_section.get("version_id"),
        "exposure manual version",
    )
    _expect(
        payload.get("manual_sha256"),
        manual_section.get("manual_sha256"),
        "exposure manual SHA",
    )
    all_gate_fields = {
        "s108_completion_sha256": "s108",
        "e01_completion_sha256": "e01",
        "c02_completion_sha256": "c02",
        "e02_completion_sha256": "e02",
        "c03_completion_sha256": "c03",
        "e00_completion_sha256": "e00",
        "r03_bank_sha256": "r03",
        "r04_completion_sha256": "r04",
    }
    if condition == "C0":
        for field in all_gate_fields:
            _expect(payload.get(field), ZERO_SHA256, f"C0 exposure {field}")
        expected_coaching = False
        expected_reference = False
    elif condition == "C3":
        for field, name in all_gate_fields.items():
            expected = (
                _delivery_payload_completion(delivery, name)
                if name in {"s108", "e01", "c02", "e02", "c03"}
                else ZERO_SHA256
            )
            _expect(payload.get(field), expected, f"C3 exposure {field}")
        expected_coaching = True
        expected_reference = False
    else:
        for field, name in all_gate_fields.items():
            _expect(
                payload.get(field),
                _delivery_payload_completion(delivery, name),
                f"C5 exposure {field}",
            )
        expected_coaching = True
        expected_reference = True
    _expect(
        payload.get("coaching_delivered"),
        expected_coaching,
        "exposure coaching",
    )
    _expect(
        payload.get("reference_delivered"),
        expected_reference,
        "exposure reference",
    )
    assigned_seconds = _integer(
        payload.get("assigned_seconds"),
        "exposure assigned seconds",
        minimum=0,
    )
    received_seconds = _integer(
        payload.get("received_seconds"),
        "exposure received seconds",
        minimum=0,
    )
    assigned_items = _integer(
        payload.get("assigned_item_count"),
        "exposure assigned item count",
        minimum=0,
    )
    received_items = _integer(
        payload.get("received_item_count"),
        "exposure received item count",
        minimum=0,
    )
    if received_seconds > assigned_seconds or received_items > assigned_items:
        raise D09ContractError("received exposure cannot exceed assigned exposure")
    adherence = _decimal(payload.get("adherence_fraction"), "adherence")
    if adherence < 0 or adherence > 1:
        raise D09ContractError("adherence must be between zero and one")
    expected_adherence = (
        Decimal(0)
        if assigned_seconds == 0
        else Decimal(received_seconds) / Decimal(assigned_seconds)
    )
    if abs(adherence - expected_adherence) > Decimal("0.000000001"):
        raise D09ContractError("adherence fraction does not match seconds")
    if assigned_seconds == 0 and received_seconds != 0:
        raise D09ContractError("zero assigned seconds requires zero received seconds")
    if assigned_items == 0 and received_items != 0:
        raise D09ContractError("zero assigned items requires zero received items")
    _expect(
        payload.get("blocked_safe_action_preserved"),
        True,
        "exposure safe block",
    )
    _expect(
        payload.get("protocol_deviation_retained"),
        True,
        "exposure deviation retention",
    )
    _expect(payload.get("free_generation_used"), False, "exposure free generation")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_human_research",
        "exposure zone",
    )


def validate_blind_rating_record(
    payload: Mapping[str, Any],
    *,
    phase: str | None = None,
) -> None:
    _expect_keys(payload, RATING_KEYS, "blind rating")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_blind_rating.v1",
        "rating schema",
    )
    _validate_phase_and_condition(
        payload,
        phase=phase,
        comparison_field=False,
        name="rating",
    )
    _validate_sha_fields(
        payload,
        {
            "rating_record_id_sha256",
            "blind_item_code_sha256",
            "audio_object_id_sha256",
            "rater_key_sha256",
            "adjudication_receipt_sha256",
        },
        "rating",
    )
    if payload.get("outcome") not in {
        "comprehensibility",
        "phone_accuracy",
        "fluency",
    }:
        raise D09ContractError("rating outcome is invalid")
    score = _integer(payload.get("score_1_to_5"), "rating score", minimum=1)
    if score > 5:
        raise D09ContractError("rating score must be at most 5")
    for field in (
        "rater_blinded_to_condition",
        "rater_blinded_to_timepoint",
        "rater_blinded_to_participant_identity",
    ):
        _expect(payload.get(field), True, f"rating {field}")
    for field in (
        "condition_field_present_in_rating_view",
        "timepoint_field_present_in_rating_view",
        "participant_identity_present_in_rating_view",
    ):
        _expect(payload.get(field), False, f"rating {field}")
    adjudicated = payload.get("adjudicated")
    if not isinstance(adjudicated, bool):
        raise D09ContractError("rating adjudicated must be boolean")
    receipt = _string(
        payload.get("adjudication_receipt_sha256"),
        "rating adjudication receipt",
    )
    if (receipt != ZERO_SHA256) != adjudicated:
        raise D09ContractError("rating adjudication flag/receipt mismatch")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_human_research",
        "rating zone",
    )


def validate_disposition_record(
    payload: Mapping[str, Any],
    *,
    phase: str | None = None,
) -> None:
    _expect_keys(payload, DISPOSITION_KEYS, "disposition")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_disposition.v1",
        "disposition schema",
    )
    _validate_phase_and_condition(
        payload,
        phase=phase,
        comparison_field=True,
        name="disposition",
    )
    _validate_sha_fields(
        payload,
        {
            "disposition_record_id_sha256",
            "participant_key_sha256",
            "withdrawal_coded_receipt_sha256",
            "missingness_coded_receipt_sha256",
            "protocol_deviation_receipt_sha256",
            "adverse_feedback_receipt_sha256",
        },
        "disposition",
    )
    _expect(payload.get("randomized"), True, "disposition randomized")
    withdrawn = payload.get("withdrawn")
    deviation = payload.get("protocol_deviation_present")
    adverse = payload.get("adverse_feedback_present")
    if not all(isinstance(value, bool) for value in (withdrawn, deviation, adverse)):
        raise D09ContractError("disposition flags must be boolean")
    if payload.get("followup_status") not in {
        "complete",
        "post_missing",
        "retention_missing",
        "withdrawn",
    }:
        raise D09ContractError("disposition follow-up status is invalid")
    _expect(
        payload.get("followup_status") == "withdrawn",
        withdrawn,
        "withdrawal/follow-up consistency",
    )
    for flag, field in (
        (withdrawn, "withdrawal_coded_receipt_sha256"),
        (
            payload.get("followup_status") != "complete",
            "missingness_coded_receipt_sha256",
        ),
        (deviation, "protocol_deviation_receipt_sha256"),
        (adverse, "adverse_feedback_receipt_sha256"),
    ):
        if (_string(payload.get(field), field) != ZERO_SHA256) != flag:
            raise D09ContractError(f"disposition {field} flag/receipt mismatch")
    _expect(payload.get("retained_in_itt"), True, "disposition ITT")
    _expect(
        payload.get("postrandomization_exclusion_used"),
        False,
        "disposition exclusion",
    )
    _expect(payload.get("free_text_present"), False, "disposition free text")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_human_research",
        "disposition zone",
    )


def validate_comparison_stage_separation_payload(
    payload: Mapping[str, Any],
    *,
    phase: str,
    delivery: DeliveryEvidence,
) -> None:
    """Validate zero reuse across the two independently randomized comparisons."""

    _reject_nonfinite_tree(payload, "comparison-stage separation")
    if _contains_tbd(payload):
        raise D09ContractError("comparison-stage separation receipt contains TBD")
    _expect_keys(
        payload,
        COMPARISON_STAGE_SEPARATION_KEYS,
        "comparison-stage separation",
    )
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_comparison_stage_separation_receipt.v1",
        "comparison-stage schema",
    )
    if not re.fullmatch(
        r"d09comparison_[0-9a-f]{32}",
        _string(payload.get("receipt_id"), "comparison-stage receipt ID"),
    ):
        raise D09ContractError("invalid comparison-stage receipt ID")
    _expect(payload.get("phase"), phase, "comparison-stage phase")
    _expect(
        payload.get("status"),
        "zero_overlap_independent_parallel_comparisons",
        "comparison-stage status",
    )
    stage_1 = _mapping(payload.get("stage_1"), "comparison stage 1")
    stage_2 = _mapping(payload.get("stage_2"), "comparison stage 2")
    _expect_keys(stage_1, COMPARISON_STAGE_KEYS, "comparison stage 1")
    _expect_keys(stage_2, COMPARISON_STAGE_KEYS, "comparison stage 2")
    _expect(stage_1.get("comparison"), "c3_minus_c0", "stage 1 comparison")
    _expect(stage_1.get("allowed_conditions"), ["C0", "C3"], "stage 1 arms")
    _expect(stage_2.get("comparison"), "c5_minus_c3", "stage 2 comparison")
    _expect(stage_2.get("allowed_conditions"), ["C3", "C5"], "stage 2 arms")
    for stage_name, stage in (("stage 1", stage_1), ("stage 2", stage_2)):
        _validate_sha_fields(
            stage,
            COMPARISON_STAGE_KEYS - {"comparison", "allowed_conditions"},
            f"comparison {stage_name}",
        )
        for field in (
            "randomization_receipt_sha256",
            "participant_set_sha256",
            "audio_set_sha256",
            "rating_set_sha256",
            "gate_receipt_sha256",
        ):
            if stage.get(field) == ZERO_SHA256:
                raise D09ContractError(f"comparison {stage_name} {field} is zero")
    for field, name in (
        ("s108_completion_sha256", "s108"),
        ("e01_completion_sha256", "e01"),
        ("c02_completion_sha256", "c02"),
        ("e02_completion_sha256", "e02"),
        ("c03_completion_sha256", "c03"),
    ):
        expected = _delivery_payload_completion(delivery, name)
        _expect(stage_1.get(field), expected, f"stage 1 {name} gate")
        _expect(stage_2.get(field), expected, f"stage 2 {name} gate")
    for field in (
        "e00_completion_sha256",
        "r03_bank_sha256",
        "r04_completion_sha256",
    ):
        _expect(stage_1.get(field), ZERO_SHA256, f"stage 1 {field}")
    for field, name in (
        ("e00_completion_sha256", "e00"),
        ("r03_bank_sha256", "r03"),
        ("r04_completion_sha256", "r04"),
    ):
        _expect(
            stage_2.get(field),
            _delivery_payload_completion(delivery, name),
            f"stage 2 {name} gate",
        )
    stage_1_closed = _utc(
        payload.get("stage_1_randomization_closed_at_utc"),
        "stage 1 randomization closed at",
    )
    stage_2_authorized = _utc(
        payload.get("stage_2_activation_authorized_at_utc"),
        "stage 2 activation authorized at",
    )
    stage_2_opened = _utc(
        payload.get("stage_2_randomization_opened_at_utc"),
        "stage 2 randomization opened at",
    )
    if not stage_1_closed <= stage_2_authorized <= stage_2_opened:
        raise D09ContractError("comparison-stage activation time order is invalid")
    _validate_sha_fields(
        payload,
        {
            "stage_2_activation_decision_receipt_sha256",
            "stage_1_unblinded_effect_access_log_sha256",
        },
        "comparison-stage separation",
    )
    for field in (
        "stage_2_activation_decision_receipt_sha256",
        "stage_1_unblinded_effect_access_log_sha256",
    ):
        if payload.get(field) == ZERO_SHA256:
            raise D09ContractError(f"comparison-stage {field} cannot be zero")
    for field, separation_expected in (
        ("stage_1_unblinded_effect_access_count_before_stage_2_activation", 0),
        ("stage_1_outcome_or_effect_available_at_stage_2_activation", False),
        (
            "stage_2_activation_basis",
            "approval_safety_privacy_and_delivery_readiness_only",
        ),
        ("activation_time_order_verified", True),
        ("participant_intersection_count", 0),
        ("audio_intersection_count", 0),
        ("rating_intersection_count", 0),
        ("shared_c3_participant_intersection_count", 0),
        ("copied_or_cloned_row_count", 0),
        ("comparison_participant_audio_rating_reuse_allowed", False),
        ("independent_participant_unit_parallel_comparisons", True),
        ("privacy_zone", "A1_aggregate_comparison_stage_separation"),
    ):
        _expect(
            payload.get(field),
            separation_expected,
            f"comparison-stage {field}",
        )
    for field in ("participant_set_sha256", "audio_set_sha256", "rating_set_sha256"):
        if stage_1.get(field) == stage_2.get(field):
            raise D09ContractError(
                f"comparison-stage {field} cannot be shared between comparisons"
            )
    if stage_1.get("randomization_receipt_sha256") == stage_2.get(
        "randomization_receipt_sha256"
    ):
        raise D09ContractError("stage randomization receipts must be separate")
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "comparison-stage binding",
    )


def deep_validate_comparison_stage_separation(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    phase: str,
    delivery: DeliveryEvidence,
) -> ComparisonStageEvidence:
    section = _mapping(
        config.get("comparison_stage_separation"),
        "comparison-stage separation",
    )
    expected_sha = _string(
        section.get(f"{phase}_receipt_sha256"),
        f"{phase} comparison-stage receipt SHA",
    )
    placeholder = {
        "feasibility": TBD_FEASIBILITY_COMPARISON,
        "confirmatory": TBD_CONFIRMATORY_COMPARISON,
    }[phase]
    if expected_sha == placeholder:
        raise D09ContractError(
            f"actual {phase} comparison-stage separation receipt is required",
            stage=f"{phase}_comparison_stage_separation",
        )
    _sha256(expected_sha, f"{phase} comparison-stage receipt SHA")
    payload, snapshot = _read_exact_json(
        workspace_root=workspace_root,
        relative=_relative_string(
            section.get(f"{phase}_receipt_path"),
            f"{phase} comparison-stage receipt path",
        ),
        expected_sha256=expected_sha,
        name=f"{phase} comparison-stage separation receipt",
    )
    validate_comparison_stage_separation_payload(
        payload,
        phase=phase,
        delivery=delivery,
    )
    _verify_snapshot(workspace_root, snapshot, f"{phase} comparison-stage receipt")
    return ComparisonStageEvidence(snapshot.sha256, payload)


def validate_phase_separation_payload(
    payload: Mapping[str, Any],
    *,
    h04: H04PhaseEvidence,
    confirmatory_payload: Mapping[str, Any],
) -> None:
    _reject_nonfinite_tree(payload, "phase separation")
    if _contains_tbd(payload):
        raise D09ContractError("phase separation receipt contains TBD")
    _expect_keys(payload, SEPARATION_KEYS, "phase separation")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d09_h02_h03_phase_separation_receipt.v1",
        "phase separation schema",
    )
    if not re.fullmatch(
        r"d09separation_[0-9a-f]{32}",
        _string(payload.get("receipt_id"), "phase separation ID"),
    ):
        raise D09ContractError("invalid phase separation ID")
    _expect(
        payload.get("status"),
        "zero_overlap_independently_recruited_confirmatory",
        "phase separation status",
    )
    receipts = _mapping(
        confirmatory_payload.get("receipts"),
        "confirmatory receipts",
    )
    for field, expected in (
        ("feasibility_completion_sha256", h04.feasibility_sha256),
        ("h01_final_sample_size_lock_sha256", h04.h01_sha256),
        ("frozen_sap_sha256", h04.sap_sha256),
        (
            "confirmatory_randomization_receipt_sha256",
            receipts.get("randomization_receipt_sha256"),
        ),
        (
            "confirmatory_data_lock_receipt_sha256",
            receipts.get("data_lock_receipt_sha256"),
        ),
    ):
        _expect(payload.get(field), expected, f"phase separation {field}")
    _validate_sha_fields(
        payload,
        {
            "feasibility_participant_set_sha256",
            "confirmatory_participant_set_sha256",
            "feasibility_audio_set_sha256",
            "confirmatory_audio_set_sha256",
            "feasibility_rating_set_sha256",
            "confirmatory_rating_set_sha256",
        },
        "phase separation",
    )
    for prefix in ("participant", "audio", "rating"):
        feasibility_set = payload.get(f"feasibility_{prefix}_set_sha256")
        confirmatory_set = payload.get(f"confirmatory_{prefix}_set_sha256")
        if feasibility_set == confirmatory_set:
            raise D09ContractError(f"{prefix} set hash reused across phases")
    for field, expected in (
        ("participant_intersection_count", 0),
        ("audio_intersection_count", 0),
        ("rating_intersection_count", 0),
        ("copied_or_cloned_row_count", 0),
        ("independent_confirmatory_recruitment_completed", True),
        ("participant_audio_rating_reuse_allowed", False),
        ("privacy_zone", "A1_aggregate_phase_separation"),
    ):
        _expect(payload.get(field), expected, f"phase separation {field}")
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "phase separation binding",
    )


def _validate_d09_handoff_lineage(
    payload: Mapping[str, Any],
    *,
    phase: str,
    preregistration: PreregEvidence,
    manual: ManualEvidence,
    comparison_stage: ComparisonStageEvidence,
) -> None:
    lineage = _mapping(payload.get("lineage"), f"{phase} H04 lineage")
    prereg_field = (
        "feasibility_preregistration_sha256" if phase == "feasibility" else None
    )
    if prereg_field is not None:
        _expect(
            lineage.get(prereg_field),
            preregistration.sha256,
            f"{phase} preregistration handoff",
        )
    for condition in ("c0", "c3", "c5"):
        _expect(
            lineage.get(f"{condition}_manual_sha256"),
            _mapping(manual.payload.get(condition), f"manual {condition}").get(
                "manual_sha256"
            ),
            f"{phase} {condition} manual handoff",
        )
    receipts = _mapping(payload.get("receipts"), f"{phase} H04 receipts")
    _expect(
        receipts.get("randomization_receipt_sha256"),
        comparison_stage.sha256,
        f"{phase} comparison-stage randomization handoff",
    )


def validate_feasibility_handoff_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
    h04_module: ModuleType,
    preregistration: PreregEvidence,
    manual: ManualEvidence,
    comparison_stage: ComparisonStageEvidence,
) -> None:
    """Validate the no-efficacy feasibility handoff into the exact H04 schema."""

    h04_h00 = h04_module.H00Evidence(
        config_sha256=ZERO_SHA256,
        approval_sha256=h00.approval_sha256,
        attestation_sha256=h00.attestation_sha256,
    )
    try:
        h04_module.validate_feasibility_pilot_payload(payload, h00=h04_h00)
    except Exception as exc:
        raise D09ContractError(f"H04 feasibility handoff failed: {exc}") from exc
    _expect(
        comparison_stage.payload.get("phase"),
        "feasibility",
        "feasibility comparison-stage phase",
    )
    _validate_d09_handoff_lineage(
        payload,
        phase="feasibility",
        preregistration=preregistration,
        manual=manual,
        comparison_stage=comparison_stage,
    )


def validate_confirmatory_handoff_payload(
    payload: Mapping[str, Any],
    *,
    h00: H00Evidence,
    h04: H04PhaseEvidence,
    preregistration: PreregEvidence,
    manual: ManualEvidence,
    comparison_stage: ComparisonStageEvidence,
    phase_separation: Mapping[str, Any],
) -> None:
    """Validate a separately recruited confirmatory handoff into exact H04."""

    module = h04.module
    h04_h00 = module.H00Evidence(
        config_sha256=ZERO_SHA256,
        approval_sha256=h00.approval_sha256,
        attestation_sha256=h00.attestation_sha256,
    )
    feasibility = module.FeasibilityEvidence(
        h04.feasibility_sha256,
        h04.feasibility_payload,
        None,
    )
    h01 = module.H01Evidence(h04.h01_sha256, h04.h01_payload, None)
    sap = module.SapEvidence(h04.sap_sha256, h04.sap_payload, None)
    try:
        module.validate_h02_h03_payload(
            payload,
            h00=h04_h00,
            feasibility=feasibility,
            h01=h01,
            sap=sap,
        )
    except Exception as exc:
        raise D09ContractError(f"H04 confirmatory handoff failed: {exc}") from exc
    _expect(
        comparison_stage.payload.get("phase"),
        "confirmatory",
        "confirmatory comparison-stage phase",
    )
    _validate_d09_handoff_lineage(
        payload,
        phase="confirmatory",
        preregistration=preregistration,
        manual=manual,
        comparison_stage=comparison_stage,
    )
    validate_phase_separation_payload(
        phase_separation,
        h04=h04,
        confirmatory_payload=payload,
    )


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    phase: str = "feasibility",
) -> PreflightResult:
    """Validate authority and frozen inputs, then stop before human operations."""

    if phase not in PHASES:
        raise D09ContractError("phase must be feasibility or confirmatory")
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
    h04: H04PhaseEvidence | None = None
    if phase == "confirmatory":
        h04 = deep_validate_h04_confirmatory_prerequisites(
            workspace_root=workspace_root,
            config=config,
            h00=h00,
            audit=audit,
        )
    delivery = deep_validate_delivery_receipt(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    preregistration = deep_validate_preregistration(
        workspace_root=workspace_root,
        config=config,
        phase=phase,
        h00=h00,
        h04=h04,
        delivery=delivery,
        audit=audit,
    )
    manual = deep_validate_manual(
        workspace_root=workspace_root,
        config=config,
        phase=phase,
        h00=h00,
        preregistration=preregistration,
        delivery=delivery,
        audit=audit,
    )
    authorization = deep_validate_authorization(
        workspace_root=workspace_root,
        config=config,
        phase=phase,
        h00=h00,
        h04=h04,
        delivery=delivery,
        preregistration=preregistration,
        manual=manual,
        audit=audit,
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=static.source_snapshots,
        audit=audit,
        stage="static_source_final",
    )
    return PreflightResult(
        status=f"ready_for_separately_authorized_{phase}_human_operations",
        phase=phase,
        config_sha256=static.config_sha256,
        h00_approval_sha256=h00.approval_sha256,
        h00_attestation_sha256=h00.attestation_sha256,
        feasibility_completion_sha256=(
            h04.feasibility_sha256 if h04 is not None else ZERO_SHA256
        ),
        h01_lock_sha256=h04.h01_sha256 if h04 is not None else ZERO_SHA256,
        sap_sha256=h04.sap_sha256 if h04 is not None else ZERO_SHA256,
        delivery_receipt_sha256=delivery.sha256,
        preregistration_sha256=preregistration.sha256,
        manual_sha256=manual.sha256,
        authorization_sha256=authorization.sha256,
        audit=audit.as_dict(),
    )


def config_only_result(
    static: StaticValidation,
    audit: AccessAudit | None = None,
) -> Mapping[str, Any]:
    access = audit if audit is not None else AccessAudit()
    return {
        "status": "valid_config_only",
        "implementation_status": STATIC_STATUS,
        "phase": "not_selected",
        "config_sha256": static.config_sha256,
        "policy_sha256": static.policy_sha256,
        "source_pin_count": len(static.source_snapshots),
        "h00_approval_sha256": ZERO_SHA256,
        "h00_attestation_sha256": ZERO_SHA256,
        "feasibility_completion_sha256": ZERO_SHA256,
        "h01_lock_sha256": ZERO_SHA256,
        "sap_sha256": ZERO_SHA256,
        "delivery_receipt_sha256": ZERO_SHA256,
        "preregistration_sha256": ZERO_SHA256,
        "manual_sha256": ZERO_SHA256,
        "authorization_sha256": ZERO_SHA256,
        **access.as_dict(),
    }


def failure_result(error: D09ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
    }
