from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from voice_coach.evaluation import b05_locked_contract as contract

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/experiments/b05_explicit_locked_evaluation_v1.yaml"
H = "a" * 64


def _bound(payload: dict[str, Any]) -> dict[str, Any]:
    payload["binding_sha256"] = contract.binding_sha256(payload)
    return payload


def _freeze() -> dict[str, Any]:
    return _bound(
        {
            "schema_version": contract.S108_FREEZE_SCHEMA_VERSION,
            "freeze_id": "b05s108freeze_" + "a" * 32,
            "status": "immutable_ready_for_joint_locked_batch",
            "g04_completion_sha256": H,
            "d07_completion_sha256": H,
            "s107_config_sha256": contract.S107_CONFIG_SHA256,
            "s107_commit_sha256": H,
            "s108_no_locked_config_sha256": contract.S108_CONFIG_SHA256,
            "s108_no_locked_receipt_sha256": H,
            "s108_model_identity_sha256": H,
            "s108_checkpoint_sha256": H,
            "s108_calibrator_sha256": H,
            "s108_threshold_policy_sha256": H,
            "s108_source_chain_sha256": H,
            "s108_execution_spec_sha256": H,
            "choices_frozen_before_locked_access": True,
            "ready_for_joint_locked_batch": True,
            "locked_results_unseen": True,
            "locked_access_count": 0,
            "contains_identifiers": False,
            "contains_rows": False,
            "contains_audio": False,
            "contains_labels": False,
            "contains_predictions": False,
            "privacy_zone": "A1_aggregate_batch_readiness",
            "binding_sha256": H,
        }
    )


def _batch() -> dict[str, Any]:
    freeze = _freeze()
    return _bound(
        {
            "schema_version": contract.BATCH_SCHEMA_VERSION,
            "batch_id": "b05batch_" + "a" * 32,
            "status": "frozen_authorized_not_opened",
            "preregistration_sha256": H,
            "g04_completion_sha256": H,
            "d07_completion_sha256": H,
            "locked_split_manifest_sha256": H,
            "locked_set_identity_sha256": H,
            "locked_utterance_count": 2,
            "b03_config_sha256": contract.B03_CONFIG_SHA256,
            "b03_report_sha256": contract.B03_REPORT_SHA256,
            "b03_registry_sha256": contract.B03_REGISTRY_SHA256,
            "b03_checkpoint_set_sha256": contract.B03_CHECKPOINT_SET_SHA256,
            "b03_feature_allowlist_sha256": contract.B03_FEATURE_ALLOWLIST_SHA256,
            "b03_phone_decision_threshold": 0.5,
            "b03_seed_ensemble_definition": (
                "no_prediction_ensemble_independent_seed_metrics_then_arithmetic_mean"
            ),
            "s107_commit_sha256": H,
            "s108_arm_freeze_sha256": H,
            "arm_execution_specs": [
                {
                    "arm_id": "b05_explicit_baseline",
                    "immutable_spec_sha256": contract.B03_CONFIG_SHA256,
                    "checkpoint_set_or_model_sha256": (
                        contract.B03_CHECKPOINT_SET_SHA256
                    ),
                    "decision_policy_sha256": contract.B03_FEATURE_ALLOWLIST_SHA256,
                    "result_based_mutation_allowed": False,
                },
                {
                    "arm_id": "s108_final_s1",
                    "immutable_spec_sha256": freeze["s108_execution_spec_sha256"],
                    "checkpoint_set_or_model_sha256": freeze[
                        "s108_model_identity_sha256"
                    ],
                    "decision_policy_sha256": freeze["s108_threshold_policy_sha256"],
                    "result_based_mutation_allowed": False,
                },
            ],
            "execution_order": list(contract.ARMS),
            "both_arms_immutable_before_access": True,
            "result_visibility_embargo_until_both_sealed": True,
            "prior_joint_locked_access_count": 0,
            "maximum_joint_locked_access_count": 1,
            "rerun_after_success_allowed": False,
            "rerun_after_failure_allowed": False,
            "new_independent_test_set_required_for_any_new_run": True,
            "contains_locked_rows": False,
            "contains_audio_or_locator": False,
            "contains_labels": False,
            "contains_predictions": False,
            "privacy_zone": "D1_access_controlled_batch_control_no_locator",
            "binding_sha256": H,
        }
    )


def _prediction_manifest() -> dict[str, Any]:
    shards: list[dict[str, Any]] = []
    for seed in contract.SEEDS:
        shards.append(
            {
                "task": "phone_binary_error",
                "outcome": None,
                "seed": seed,
                "shard_index": 0,
                "relative_path": f"phone/seed_{seed}/part-00000.parquet",
                "sha256": H,
                "row_count": 2,
            }
        )
        for outcome in contract.OUTCOMES:
            shards.append(
                {
                    "task": "utterance_ordinal_score",
                    "outcome": outcome,
                    "seed": seed,
                    "shard_index": 0,
                    "relative_path": (
                        f"utterance/{outcome}/seed_{seed}/part-00000.parquet"
                    ),
                    "sha256": H,
                    "row_count": 2,
                }
            )
    return _bound(
        {
            "schema_version": contract.PREDICTION_SCHEMA_VERSION,
            "prediction_manifest_id": "b05pred_" + "a" * 32,
            "status": "sealed_before_joint_result_release",
            "preregistration_sha256": H,
            "batch_manifest_sha256": H,
            "authorization_sha256": H,
            "one_shot_transaction_id_sha256": H,
            "b03_config_sha256": contract.B03_CONFIG_SHA256,
            "b03_report_sha256": contract.B03_REPORT_SHA256,
            "b03_registry_sha256": contract.B03_REGISTRY_SHA256,
            "b03_checkpoint_set_sha256": contract.B03_CHECKPOINT_SET_SHA256,
            "b03_feature_allowlist_sha256": contract.B03_FEATURE_ALLOWLIST_SHA256,
            "phone_decision_threshold": 0.5,
            "seed_ensemble_definition": (
                "no_prediction_ensemble_independent_seed_metrics_then_arithmetic_mean"
            ),
            "split": "locked_test",
            "alignment_role": "oracle_target_boundaries_locked_research_only",
            "seeds": list(contract.SEEDS),
            "storage_format": "parquet_shards",
            "phone_row_schema": list(contract.PHONE_ROW_SCHEMA),
            "utterance_row_schema": list(contract.UTTERANCE_ROW_SCHEMA),
            "shards": shards,
            "phone_row_count": 6,
            "utterance_row_count": 18,
            "gold_labels_embedded": False,
            "raw_audio_or_locator_embedded": False,
            "direct_identifiers_embedded": False,
            "free_text_embedded": False,
            "publication_allowed": False,
            "privacy_zone": "D1_pseudonymized_restricted_locked_predictions",
            "binding_sha256": H,
        }
    )


def _analysis() -> dict[str, Any]:
    return {
        "locked_population_definition_sha256": H,
        "phone_confusion_definition_sha256": H,
        "utterance_ordinal_confusion_definition_sha256": H,
        "slice_family_definition_sha256": H,
        "slice_axes": list(contract.SLICE_AXES),
        "speaker_cluster_bootstrap_method": "speaker_percentile",
        "bootstrap_repetitions": 5000,
        "confidence_level": 0.95,
        "multiple_comparison_correction": "holm",
        "holm_family_definition_sha256": H,
        "missing_data_policy_sha256": H,
        "undefined_metric_policy_sha256": H,
        "minimum_support_policy_sha256": H,
        "minimum_support_thresholds": {
            "phone_positive_count": 1,
            "phone_negative_count": 1,
            "utterance_row_count": 1,
            "slice_speaker_count": 1,
            "slice_row_count": 1,
        },
        "primary_estimands_sha256": H,
        "slice_result_keys": [
            {
                "axis": "l1",
                "group_token_sha256": H,
                "unit": "phone",
                "metric_name": "macro_f1",
                "missing_allowed": False,
            }
        ],
        "bootstrap_result_keys": [
            {
                "estimand_id_sha256": H,
                "metric_name": "macro_f1",
            }
        ],
        "holm_hypotheses": [
            {
                "family_id_sha256": H,
                "hypothesis_id_sha256": H,
                "family_size": 1,
                "alpha": 0.05,
            }
        ],
        "cluster_unit": "speaker_id_sha256",
    }


def _preregistration() -> dict[str, Any]:
    freeze = _freeze()
    return _bound(
        {
            "schema_version": contract.PREREG_SCHEMA_VERSION,
            "preregistration_id": "b05prereg_" + "a" * 32,
            "status": "approved_and_frozen",
            "frozen_before_any_locked_access": True,
            "predecessor_bindings": {
                "b03_config_sha256": contract.B03_CONFIG_SHA256,
                "b03_report_sha256": contract.B03_REPORT_SHA256,
                "b03_registry_sha256": contract.B03_REGISTRY_SHA256,
                "b03_checkpoint_set_sha256": contract.B03_CHECKPOINT_SET_SHA256,
                "g04_completion_sha256": H,
                "d07_completion_sha256": H,
                "s107_commit_sha256": H,
                "s108_arm_freeze_sha256": H,
            },
            "b03_canonical_baseline": {
                "seeds": list(contract.SEEDS),
                "checkpoint_set_sha256": contract.B03_CHECKPOINT_SET_SHA256,
                "feature_allowlist_sha256": contract.B03_FEATURE_ALLOWLIST_SHA256,
                "phone_decision_threshold": 0.5,
                "threshold_origin": "b03_sklearn_predict_default_not_b06_posthoc",
                "seed_ensemble_definition": (
                    "no_prediction_ensemble_independent_seed_metrics_then_"
                    "arithmetic_mean"
                ),
                "posthoc_b06_substitution_used": False,
                "result_based_change_allowed": False,
            },
            "s108_arm": {
                "arm_freeze_sha256": H,
                "model_identity_sha256": freeze["s108_model_identity_sha256"],
                "checkpoint_sha256": freeze["s108_checkpoint_sha256"],
                "calibrator_sha256": freeze["s108_calibrator_sha256"],
                "threshold_policy_sha256": freeze["s108_threshold_policy_sha256"],
                "execution_spec_sha256": freeze["s108_execution_spec_sha256"],
                "result_based_change_allowed": False,
            },
            "joint_batch": {
                "included_arms": list(contract.ARMS),
                "same_locked_batch_manifest_required": True,
                "both_arms_immutable_before_access": True,
                "first_arm_result_hidden_until_both_arms_sealed": True,
                "authorized_locked_access_count": 1,
                "rerun_after_success_allowed": False,
                "rerun_after_failure_allowed": False,
                "new_independent_test_set_required_for_any_new_run": True,
                "joint_atomic_publication_required": True,
            },
            "analysis_plan": _analysis(),
            "approvals": {
                "protocol_owner_approved": True,
                "statistics_owner_approved": True,
                "privacy_owner_approved": True,
                "signed_protocol_receipt_sha256": H,
                "signed_statistics_receipt_sha256": H,
                "signed_privacy_receipt_sha256": H,
            },
            "privacy_and_claims": {
                "contains_identifiers": False,
                "contains_rows": False,
                "contains_audio": False,
                "contains_labels": False,
                "contains_predictions": False,
                "contains_locked_results": False,
                "b05_completion_claim_allowed_before_joint_receipt": False,
                "s108_completion_claim_allowed_before_joint_receipt": False,
                "deployment_claim_allowed": False,
            },
            "binding_sha256": H,
        }
    )


def _report() -> dict[str, Any]:
    phone = []
    for seed in contract.SEEDS:
        phone.append(
            {
                "seed": seed,
                "true_negative_count": 1,
                "false_positive_count": 0,
                "false_negative_count": 0,
                "true_positive_count": 1,
                "negative_support": 1,
                "positive_support": 1,
                "metric_status": "estimable",
                "macro_f1": 1.0,
                "auprc": 1.0,
            }
        )
    utterance = []
    for seed in contract.SEEDS:
        for outcome in contract.OUTCOMES:
            utterance.append(
                {
                    "seed": seed,
                    "outcome": outcome,
                    "support": 2,
                    "metric_status": "estimable",
                    "ordinal_confusion_matrix": [
                        [1, 0, 0, 0, 0],
                        [0, 1, 0, 0, 0],
                        [0, 0, 0, 0, 0],
                        [0, 0, 0, 0, 0],
                        [0, 0, 0, 0, 0],
                    ],
                    "metrics": {
                        "qwk": 1.0,
                        "spearman": 1.0,
                        "mae": 0.0,
                        "icc_2_1": 1.0,
                    },
                }
            )
    analysis = _analysis()
    policy = {field: analysis[field] for field in contract.ANALYSIS_SHA_FIELDS}
    return _bound(
        {
            "schema_version": contract.REPORT_SCHEMA_VERSION,
            "report_id": "b05report_" + "a" * 32,
            "status": "completed_joint_one_shot_locked_evaluation",
            "config_sha256": H,
            "preregistration_sha256": H,
            "batch_manifest_sha256": H,
            "authorization_sha256": H,
            "one_shot_transaction_id_sha256": H,
            "b03_config_sha256": contract.B03_CONFIG_SHA256,
            "b03_report_sha256": contract.B03_REPORT_SHA256,
            "b03_registry_sha256": contract.B03_REGISTRY_SHA256,
            "b03_checkpoint_set_sha256": contract.B03_CHECKPOINT_SET_SHA256,
            "b03_feature_allowlist_sha256": contract.B03_FEATURE_ALLOWLIST_SHA256,
            "s108_arm_freeze_sha256": H,
            "locked_predictions_sha256": H,
            "locked_utterance_count": 2,
            "locked_phone_count": 2,
            "seeds": list(contract.SEEDS),
            "phone_seed_results": phone,
            "utterance_seed_results": utterance,
            "slice_results": [
                {
                    "axis": "l1",
                    "group_token_sha256": H,
                    "unit": "phone",
                    "metric_name": "macro_f1",
                    "speaker_support": 1,
                    "row_support": 2,
                    "positive_support": 1,
                    "negative_support": 1,
                    "status": "estimable",
                    "estimate": 1.0,
                }
            ],
            "speaker_cluster_bootstrap_results": [
                {
                    "estimand_id_sha256": H,
                    "metric_name": "macro_f1",
                    "cluster_unit": "speaker_id_sha256",
                    "method": "speaker_percentile",
                    "repetitions": 5000,
                    "confidence_level": 0.95,
                    "status": "estimable",
                    "point_estimate": 1.0,
                    "confidence_interval_lower": 0.9,
                    "confidence_interval_upper": 1.0,
                }
            ],
            "holm_results": [
                {
                    "family_id_sha256": H,
                    "hypothesis_id_sha256": H,
                    "family_size": 1,
                    "status": "estimable",
                    "raw_p_value": 0.01,
                    "adjusted_p_value": 0.01,
                    "alpha": 0.05,
                    "rejected": True,
                }
            ],
            "analysis_policy_bindings": policy,
            "one_shot_assurance": {
                "joint_locked_access_count": 1,
                "b05_execution_count": 1,
                "s108_execution_count": 1,
                "both_arms_sealed_before_result_release": True,
                "first_arm_result_visible_before_second_arm_sealed": False,
                "arm_mutation_after_access": False,
                "rerun_count": 0,
                "rerun_allowed": False,
                "new_independent_test_set_required_for_new_run": True,
            },
            "privacy_and_claims": {
                "privacy_zone": "A1_aggregate_locked_evaluation",
                "contains_identifiers": False,
                "contains_rows": False,
                "contains_audio_or_locator": False,
                "contains_labels": False,
                "contains_predictions": False,
                "contains_free_text": False,
                "oracle_alignment_research_only": True,
                "deployment_claim_allowed": False,
                "b05_completion_claim_allowed": False,
            },
            "binding_sha256": H,
        }
    )


def _receipt(status: str) -> dict[str, Any]:
    completed = status == "completed_both_arms_sealed"
    return _bound(
        {
            "schema_version": contract.RECEIPT_SCHEMA_VERSION,
            "receipt_id": "b05receipt_" + "a" * 32,
            "status": status,
            "preregistration_sha256": H,
            "batch_manifest_sha256": H,
            "authorization_sha256": H,
            "one_shot_transaction_id_sha256": H,
            "joint_locked_access_count": 1,
            "b05_execution_count": 1,
            "s108_execution_count": 1,
            "b05_predictions_sha256": H if completed else None,
            "s108_predictions_sha256": H if completed else None,
            "b05_report_sha256": H if completed else None,
            "s108_promotion_report_sha256": H if completed else None,
            "failure_receipt_sha256": None if completed else H,
            "both_arms_sealed_before_result_release": completed,
            "first_arm_result_visible_before_second_arm_sealed": False,
            "b05_arm_mutated_after_access": False,
            "s108_arm_mutated_after_access": False,
            "rerun_count": 0,
            "authorization_consumed": True,
            "rerun_allowed": False,
            "new_independent_test_set_required_for_any_new_run": True,
            "contains_rows": False,
            "contains_audio_or_locator": False,
            "contains_labels": False,
            "contains_predictions": False,
            "privacy_zone": "A1_aggregate_one_shot_receipt",
            "binding_sha256": H,
        }
    )


def test_static_config_validates_without_protected_access() -> None:
    config = contract.load_config(CONFIG)
    audit = contract.AccessAudit()
    result = contract.validate_static_config(
        workspace_root=ROOT,
        config_path=CONFIG,
        config=config,
        audit=audit,
    )
    assert len(result.source_snapshots) == 35
    assert audit.source_pin_read_count == 34
    assert audit.source_toctou_verification_count == 34
    assert audit.b03_static_validation_count == 1
    assert audit.b06_static_validation_count == 1
    assert audit.locked_row_read_count == 0
    assert audit.b03_checkpoint_hash_read_count == 0
    assert audit.model_deserialize_count == 0
    assert audit.metric_count == 0
    assert audit.output_write_count == 0


def test_full_preflight_stops_before_g04_path_or_any_protected_access() -> None:
    config = contract.load_config(CONFIG)
    with pytest.raises(contract.B05ContractError) as raised:
        contract.run_full_preflight(
            workspace_root=ROOT,
            config_path=CONFIG,
            config=config,
        )
    error = raised.value
    assert error.stage == "g04_completion"
    assert str(error) == "G04 completion SHA is TBD_G04_COMPLETION"
    assert error.audit is not None
    audit = error.audit
    assert audit.g04_path_resolution_count == 0
    assert audit.d07_path_resolution_count == 0
    assert audit.b03_checkpoint_hash_read_count == 0
    assert audit.locked_locator_resolution_count == 0
    assert audit.model_access_count == 0
    assert audit.gpu_access_count == 0
    assert audit.metric_count == 0
    assert audit.output_path_resolution_count == 0


def test_batch_manifest_requires_exact_unique_arm_set() -> None:
    batch = _batch()
    contract.validate_batch_manifest_payload(
        batch,
        preregistration_sha256=H,
        g04_sha256=H,
        d07_sha256=H,
        s107_sha256=H,
        s108_freeze_sha256=H,
        s108_freeze=_freeze(),
    )
    duplicate = copy.deepcopy(batch)
    duplicate["arm_execution_specs"][1] = copy.deepcopy(
        duplicate["arm_execution_specs"][0]
    )
    duplicate["binding_sha256"] = contract.binding_sha256(duplicate)
    with pytest.raises(contract.B05ContractError, match="batch arm 1"):
        contract.validate_batch_manifest_payload(
            duplicate,
            preregistration_sha256=H,
            g04_sha256=H,
            d07_sha256=H,
            s107_sha256=H,
            s108_freeze_sha256=H,
            s108_freeze=_freeze(),
        )


def test_preregistration_carries_forward_fixed_b03_statistics() -> None:
    preregistration = _preregistration()
    contract.validate_preregistration_payload(
        preregistration,
        g04_sha256=H,
        d07_sha256=H,
        s107_sha256=H,
        s108_freeze_sha256=H,
        s108_freeze=_freeze(),
    )
    conflict = copy.deepcopy(preregistration)
    conflict["analysis_plan"]["bootstrap_repetitions"] = 4999
    conflict["binding_sha256"] = contract.binding_sha256(conflict)
    with pytest.raises(contract.B05ContractError, match="bootstrap reps"):
        contract.validate_preregistration_payload(
            conflict,
            g04_sha256=H,
            d07_sha256=H,
            s107_sha256=H,
            s108_freeze_sha256=H,
            s108_freeze=_freeze(),
        )


def test_parquet_manifest_requires_exact_seed_outcome_combinations() -> None:
    manifest = _prediction_manifest()
    contract.validate_prediction_manifest_payload(
        manifest,
        preregistration_sha256=H,
        batch_manifest_sha256=H,
        authorization_sha256=H,
        one_shot_transaction_id_sha256=H,
    )
    duplicate = copy.deepcopy(manifest)
    duplicate["shards"][-1]["outcome"] = contract.OUTCOMES[0]
    duplicate["shards"][-1]["relative_path"] = "duplicate/part-00000.parquet"
    duplicate["binding_sha256"] = contract.binding_sha256(duplicate)
    with pytest.raises(contract.B05ContractError, match="combinations differ"):
        contract.validate_prediction_manifest_payload(
            duplicate,
            preregistration_sha256=H,
            batch_manifest_sha256=H,
            authorization_sha256=H,
            one_shot_transaction_id_sha256=H,
        )


def test_report_enforces_exact_3x3_and_confusion_arithmetic() -> None:
    report = _report()
    prereg = {"analysis_plan": _analysis()}
    contract.validate_aggregate_report_payload(
        report,
        config_sha256=H,
        preregistration_sha256=H,
        batch_manifest_sha256=H,
        authorization_sha256=H,
        one_shot_transaction_id_sha256=H,
        s108_freeze_sha256=H,
        locked_predictions_sha256=H,
        preregistration=prereg,
        prediction_manifest=_prediction_manifest(),
    )
    bad_support = copy.deepcopy(report)
    bad_support["phone_seed_results"][0]["negative_support"] = 2
    bad_support["binding_sha256"] = contract.binding_sha256(bad_support)
    with pytest.raises(contract.B05ContractError, match="negative support"):
        contract.validate_aggregate_report_payload(
            bad_support,
            config_sha256=H,
            preregistration_sha256=H,
            batch_manifest_sha256=H,
            authorization_sha256=H,
            one_shot_transaction_id_sha256=H,
            s108_freeze_sha256=H,
            locked_predictions_sha256=H,
            preregistration=prereg,
        )
    duplicate = copy.deepcopy(report)
    duplicate["utterance_seed_results"][-1] = copy.deepcopy(
        duplicate["utterance_seed_results"][0]
    )
    duplicate["binding_sha256"] = contract.binding_sha256(duplicate)
    with pytest.raises(contract.B05ContractError, match="result order"):
        contract.validate_aggregate_report_payload(
            duplicate,
            config_sha256=H,
            preregistration_sha256=H,
            batch_manifest_sha256=H,
            authorization_sha256=H,
            one_shot_transaction_id_sha256=H,
            s108_freeze_sha256=H,
            locked_predictions_sha256=H,
            preregistration=prereg,
        )


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("slice_results", "exact preregistered slice result keys"),
        (
            "speaker_cluster_bootstrap_results",
            "exact preregistered bootstrap result keys",
        ),
        ("holm_results", "exact preregistered Holm hypotheses"),
    ],
)
def test_completed_report_rejects_omitted_preregistered_family(
    field: str,
    message: str,
) -> None:
    report = _report()
    report[field] = []
    report["binding_sha256"] = contract.binding_sha256(report)
    with pytest.raises(contract.B05ContractError, match=message):
        contract.validate_aggregate_report_payload(
            report,
            config_sha256=H,
            preregistration_sha256=H,
            batch_manifest_sha256=H,
            authorization_sha256=H,
            one_shot_transaction_id_sha256=H,
            s108_freeze_sha256=H,
            locked_predictions_sha256=H,
            preregistration={"analysis_plan": _analysis()},
        )


@pytest.mark.parametrize(
    "status",
    ["completed_both_arms_sealed", "failed_irrecoverably_no_rerun"],
)
def test_receipt_accepts_only_atomic_completed_or_non_disclosing_failure(
    status: str,
) -> None:
    receipt = _receipt(status)
    contract.validate_batch_receipt_payload(
        receipt,
        preregistration_sha256=H,
        batch_manifest_sha256=H,
        authorization_sha256=H,
        one_shot_transaction_id_sha256=H,
    )


def test_failed_receipt_cannot_disclose_partial_result_hash() -> None:
    receipt = _receipt("failed_irrecoverably_no_rerun")
    receipt["b05_predictions_sha256"] = H
    receipt["binding_sha256"] = contract.binding_sha256(receipt)
    with pytest.raises(contract.B05ContractError, match="failed receipt"):
        contract.validate_batch_receipt_payload(
            receipt,
            preregistration_sha256=H,
            batch_manifest_sha256=H,
            authorization_sha256=H,
            one_shot_transaction_id_sha256=H,
        )


def test_hash_dag_rejects_output_receipt_and_child_root_back_edges() -> None:
    predictions = _prediction_manifest()
    report = _report()
    registry = {"schema_version": "child"}
    joint = {"schema_version": "root"}
    contract.validate_hash_dag_documents(
        b05_predictions=predictions,
        b05_report=report,
        b05_registry=registry,
        joint_commit=joint,
    )
    cyclic_prediction = dict(predictions)
    cyclic_prediction["batch_receipt_sha256"] = H
    with pytest.raises(contract.B05ContractError, match="hash back-edge"):
        contract.validate_hash_dag_documents(
            b05_predictions=cyclic_prediction,
            b05_report=report,
            b05_registry=registry,
            joint_commit=joint,
        )
    with pytest.raises(contract.B05ContractError, match="joint root"):
        contract.validate_hash_dag_documents(
            b05_predictions=predictions,
            b05_report=report,
            b05_registry=registry,
            joint_commit={"s108_commit_sha256": H},
        )


def test_registry_and_single_joint_root_have_no_mutual_commit_edge() -> None:
    registry = _bound(
        {
            "schema_version": contract.REGISTRY_SCHEMA_VERSION,
            "run_id": "b05-explicit-locked-evaluation-v1",
            "status": "completed",
            "failure": None,
            "config_sha256": H,
            "preregistration_sha256": H,
            "batch_manifest_sha256": H,
            "authorization_sha256": H,
            "batch_receipt_sha256": H,
            "b03_config_sha256": contract.B03_CONFIG_SHA256,
            "b03_report_sha256": contract.B03_REPORT_SHA256,
            "b03_registry_sha256": contract.B03_REGISTRY_SHA256,
            "b03_checkpoint_set_sha256": contract.B03_CHECKPOINT_SET_SHA256,
            "s108_arm_freeze_sha256": H,
            "locked_predictions_sha256": H,
            "aggregate_report_sha256": H,
            "joint_locked_access_count": 1,
            "b05_execution_count": 1,
            "s108_execution_count": 1,
            "rerun_count": 0,
            "artifact_count": 2,
            "result_based_mutation_detected": False,
            "posthoc_b06_substitution_used": False,
            "privacy_zone": "A1_aggregate_registry",
            "binding_sha256": H,
        }
    )
    contract.validate_registry_payload(
        registry,
        config_sha256=H,
        preregistration_sha256=H,
        batch_manifest_sha256=H,
        authorization_sha256=H,
        batch_receipt_sha256=H,
        s108_freeze_sha256=H,
        locked_predictions_sha256=H,
        aggregate_report_sha256=H,
    )
    commit = _bound(
        {
            "schema_version": contract.COMMIT_SCHEMA_VERSION,
            "status": "committed_joint_one_shot",
            "config_sha256": H,
            "preregistration_sha256": H,
            "batch_manifest_sha256": H,
            "authorization_sha256": H,
            "batch_receipt_sha256": H,
            "b03_checkpoint_set_sha256": contract.B03_CHECKPOINT_SET_SHA256,
            "s108_arm_freeze_sha256": H,
            "b05_locked_predictions_sha256": H,
            "b05_aggregate_report_sha256": H,
            "b05_registry_sha256": H,
            "s108_locked_predictions_sha256": H,
            "s108_promotion_report_sha256": H,
            "s108_registry_sha256": H,
            "joint_locked_transaction_id_sha256": H,
            "joint_locked_access_count": 1,
            "both_arms_published_together": True,
            "first_result_used_to_change_other_arm": False,
            "rerun_count": 0,
            "rerun_allowed": False,
            "new_independent_test_set_required_for_any_new_run": True,
            "b05_completion_claim_allowed": True,
            "s108_completion_claim_bound_to_joint_root": True,
            "child_commit_back_reference_allowed": False,
            "deployment_claim_allowed": False,
            "binding_sha256": H,
        }
    )
    contract.validate_commit_payload(
        commit,
        config_sha256=H,
        preregistration_sha256=H,
        batch_manifest_sha256=H,
        authorization_sha256=H,
        batch_receipt_sha256=H,
        s108_freeze_sha256=H,
        b05_predictions_sha256=H,
        b05_report_sha256=H,
        b05_registry_sha256=H,
        s108_predictions_sha256=H,
        s108_report_sha256=H,
        s108_registry_sha256=H,
        one_shot_transaction_id_sha256=H,
    )
    assert "s108_commit_sha256" not in commit
    assert "joint_root_commit_sha256" not in registry


def test_output_schemas_encode_acyclic_hash_dag() -> None:
    prediction_schema = json.loads(
        (
            ROOT / "configs/evaluation/b05_locked_row_predictions_v1.schema.json"
        ).read_text()
    )
    report_schema = json.loads(
        (
            ROOT / "configs/evaluation/b05_locked_aggregate_report_v1.schema.json"
        ).read_text()
    )
    registry_schema = json.loads(
        (ROOT / "configs/evaluation/b05_locked_registry_v1.schema.json").read_text()
    )
    commit_schema = json.loads(
        (ROOT / "configs/evaluation/b05_locked_commit_v1.schema.json").read_text()
    )
    assert "batch_receipt_sha256" not in prediction_schema["properties"]
    assert "batch_receipt_sha256" not in report_schema["properties"]
    assert "s108_promotion_report_sha256" not in report_schema["properties"]
    assert "s108_promotion_report_sha256" not in registry_schema["properties"]
    assert "s108_commit_sha256" not in commit_schema["properties"]
    assert "child_commit_back_reference_allowed" in commit_schema["properties"]


def test_checkpoint_hash_verifier_reads_bytes_without_deserialization(
    tmp_path: Path,
) -> None:
    checkpoints = []
    for seed in contract.SEEDS:
        path = tmp_path / f"checkpoint-{seed}.bin"
        path.write_bytes(f"seed-{seed}".encode())
        checkpoints.append(
            {
                "seed": seed,
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    audit = contract.AccessAudit()
    snapshots = contract._verify_b03_checkpoint_files(
        workspace_root=tmp_path,
        config={"b03_canonical_baseline": {"checkpoints": checkpoints}},
        audit=audit,
    )
    assert len(snapshots) == 3
    assert audit.b03_checkpoint_path_resolution_count == 3
    assert audit.b03_checkpoint_hash_read_attempt_count == 3
    assert audit.b03_checkpoint_hash_read_count == 3
    assert audit.b03_checkpoint_deserialize_count == 0
    assert audit.model_deserialize_count == 0


def test_checkpoint_hash_verifier_rejects_symlink(
    tmp_path: Path,
) -> None:
    target = tmp_path / "real.bin"
    target.write_bytes(b"x")
    link = tmp_path / "checkpoint.bin"
    link.symlink_to(target.name)
    config = {
        "b03_canonical_baseline": {
            "checkpoints": [
                {
                    "seed": 42,
                    "path": link.name,
                    "sha256": hashlib.sha256(b"x").hexdigest(),
                }
            ]
        }
    }
    audit = contract.AccessAudit()
    with pytest.raises(contract.B05ContractError, match="symlink"):
        contract._verify_b03_checkpoint_files(
            workspace_root=tmp_path,
            config=config,
            audit=audit,
        )
    assert audit.b03_checkpoint_hash_read_attempt_count == 1
    assert audit.b03_checkpoint_hash_read_count == 0
    assert audit.b03_checkpoint_deserialize_count == 0


def test_g04_exact_count_accuracy_relation() -> None:
    payload = {
        "schema_version": contract.G04_SCHEMA_VERSION,
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
        "privacy_safe_report_sha256": H,
        "reviewer_qualification_confirmed": True,
        "research_lead_approved": True,
    }
    contract.validate_g04_completion_payload(payload)
    payload["alignment_accuracy"] = 0.96
    with pytest.raises(contract.B05ContractError, match="accuracy/count"):
        contract.validate_g04_completion_payload(payload)
