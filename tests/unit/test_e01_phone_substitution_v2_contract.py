from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from voice_coach.evaluation import e01_phone_substitution_v2_contract as contract


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATHS = (
    ROOT / "configs/reference/e01_phone_case_sidecar_v2.schema.json",
    ROOT / "configs/reference/e01_phone_worker_receipt_v2.schema.json",
    ROOT / "configs/reference/e01_phone_paired_aggregate_report_v2.schema.json",
)


def _sha(character: str) -> str:
    return character * 64


def _case(*, suffix: str = "1") -> dict[str, object]:
    policy_bindings = {
        "preregistration_sha256": _sha("1"),
        "phone_case_policy_sha256": _sha("2"),
        "tolerance_policy_sha256": _sha("3"),
        "minimum_support_policy_sha256": _sha("4"),
        "invalid_case_policy_sha256": _sha("5"),
        "u_to_i_relation_policy_sha256": _sha("6"),
        "u_to_t_relation_policy_sha256": _sha("7"),
        "i_to_t_relation_policy_sha256": _sha("8"),
    }
    return {
        "schema_version": contract.CASE_SCHEMA_VERSION,
        "status": "frozen_before_execution",
        "privacy_zone": "D1_pseudonymized_restricted_non_locked",
        "access_scope": "phone_executor_and_independent_verifier_only",
        "case_id": "e01pc_" + suffix * 32,
        "contains_locked_test": False,
        "contains_s203_expert_holdout": False,
        "contains_raw_audio": False,
        "contains_paths": False,
        "role_graph": ["U_to_I", "U_to_T", "I_to_T"],
        "policy_bindings": policy_bindings,
        "human_observation": {
            "boundary_source": "human_observed_adjudicated_not_expected_g2p",
            "source_boundary_annotation_sha256": _sha("9"),
            "donor_boundary_annotation_sha256": _sha("a"),
            "source_phone_label_sha256": _sha("b"),
            "donor_phone_label_sha256": _sha("c"),
            "source_and_donor_phone_differ": True,
            "observers_independent_from_s1": True,
        },
        "consent": {
            "consent_scope_sha256": _sha("d"),
            "source_derivative_processing_allowed": True,
            "donor_derivative_processing_allowed": True,
            "phone_splice_or_resynthesis_allowed": True,
            "public_audio_release_allowed": False,
        },
        "lineage": {
            "same_speaker_required": True,
            "same_session_required": True,
            "same_prompt_context_required": True,
            "source_speaker_lineage_sha256": _sha("1"),
            "donor_speaker_lineage_sha256": _sha("1"),
            "source_session_lineage_sha256": _sha("2"),
            "donor_session_lineage_sha256": _sha("2"),
            "source_prompt_context_lineage_sha256": _sha("3"),
            "donor_prompt_context_lineage_sha256": _sha("3"),
        },
        "restricted_material": {
            "source_audio_pcm_sha256": _sha("4"),
            "donor_audio_pcm_sha256": _sha("5"),
            "source_region_sha256": _sha("6"),
            "donor_region_sha256": _sha("7"),
            "edit_mask_sha256": _sha("8"),
            "source_sample_count": 100,
            "edit_mask_sample_count": 20,
        },
    }


def _receipt(
    case_payload: dict[str, object],
    *,
    statuses: tuple[str, str, str] = ("passed", "passed", "passed"),
) -> dict[str, object]:
    validated_case = contract.validate_phone_case_sidecar(case_payload)
    policies = copy.deepcopy(case_payload["policy_bindings"])
    assert isinstance(policies, dict)
    source_phone = case_payload["human_observation"]
    assert isinstance(source_phone, dict)
    material = case_payload["restricted_material"]
    assert isinstance(material, dict)

    def relation(
        source_role: str,
        comparison_role: str,
        expectation: str,
        policy_sha256: object,
        status: str,
    ) -> dict[str, object]:
        return {
            "source_role": source_role,
            "comparison_role": comparison_role,
            "expectation": expectation,
            "relation_policy_sha256": policy_sha256,
            "status": status,
            "invalid_reason_code": (
                "metric_undefined" if status == "invalid" else None
            ),
        }

    return {
        "schema_version": contract.WORKER_SCHEMA_VERSION,
        "status": "completed_restricted_receipt",
        "privacy_zone": "D1_pseudonymized_restricted_non_locked",
        "access_scope": "phone_executor_and_independent_verifier_only",
        "case_id": validated_case.case_id,
        "case_sidecar_sha256": validated_case.sidecar_sha256,
        "worker_implementation_sha256": _sha("a"),
        "runtime_identity_sha256": _sha("b"),
        "policy_bindings": policies,
        "role_outputs": {
            "U": {
                "role": "U",
                "origin": "unmodified_source",
                "pcm_sha256": material["source_audio_pcm_sha256"],
                "outside_edit_mask_pcm_sha256": _sha("9"),
                "sample_count": 100,
            },
            "I": {
                "role": "I",
                "origin": "identity_control_resynthesis",
                "pcm_sha256": _sha("d"),
                "outside_edit_mask_pcm_sha256": _sha("9"),
                "sample_count": 100,
            },
            "T": {
                "role": "T",
                "origin": "target_phone_treatment_resynthesis",
                "pcm_sha256": _sha("e"),
                "outside_edit_mask_pcm_sha256": _sha("9"),
                "sample_count": 100,
            },
        },
        "edit_mask": {
            "edit_mask_sha256": material["edit_mask_sha256"],
            "masked_sample_count": 20,
            "outside_mask_sample_count": 80,
        },
        "invariants": {
            "same_sample_count_u_i_t": True,
            "u_i_outside_edit_mask_exact_identity": True,
            "u_t_outside_edit_mask_exact_identity": True,
            "i_t_outside_edit_mask_exact_identity": True,
            "automatic_clipping_or_normalization_applied": False,
        },
        "observed_phone_bindings": {
            "u_source_phone_label_sha256": source_phone["source_phone_label_sha256"],
            "i_identity_phone_label_sha256": source_phone["source_phone_label_sha256"],
            "t_target_phone_label_sha256": source_phone["donor_phone_label_sha256"],
            "i_keeps_u_phone": True,
            "t_uses_donor_phone": True,
        },
        "independent_phone_verifier": {
            "verification_receipt_sha256": _sha("1"),
            "s1_model_identity_sha256": _sha("2"),
            "phone_verifier_model_identity_sha256": _sha("3"),
            "s1_training_lineage_sha256": _sha("4"),
            "phone_verifier_training_lineage_sha256": _sha("5"),
            "lineage_policy": (
                "disjoint_from_s1_models_features_predictions_thresholds"
            ),
            "s1_weights_accessed": False,
            "s1_features_accessed": False,
            "s1_predictions_accessed": False,
            "s1_thresholds_accessed": False,
            "trained_on_s1_outputs": False,
            "human_observed_boundaries_used": True,
        },
        "stage_separation": {
            "s201_role": "content_consistency_routing_only",
            "s201_phone_gold_feature_or_target_used": False,
            "s201_score_used_for_phone_decision": False,
            "s202_role": "independent_utterance_quality_auditor_not_phone_gold",
            "s202_used_as_phone_gold": False,
            "s202_phone_label_or_boundary_used": False,
            "s202_score_used_for_phone_decision": False,
        },
        "relations": {
            "U_to_I": relation(
                "U",
                "I",
                "identity_control_invariance",
                policies["u_to_i_relation_policy_sha256"],
                statuses[0],
            ),
            "U_to_T": relation(
                "U",
                "T",
                "targeted_phone_change",
                policies["u_to_t_relation_policy_sha256"],
                statuses[1],
            ),
            "I_to_T": relation(
                "I",
                "T",
                "targeted_phone_change",
                policies["i_to_t_relation_policy_sha256"],
                statuses[2],
            ),
        },
        "publication_allowed": False,
    }


def _report(
    receipts: list[dict[str, object]],
    *,
    minimum_support: int = 1,
) -> dict[str, object]:
    sorted_inputs = [(str(receipt["case_id"]), receipt) for receipt in receipts]
    bundle_sha256 = contract._private_receipt_bundle_sha256(sorted_inputs)
    relation_results: list[dict[str, object]] = []
    statuses_by_relation: list[list[str]] = [[], [], []]
    for receipt in receipts:
        relations = receipt["relations"]
        assert isinstance(relations, dict)
        for index, role in enumerate(contract.RELATION_ROLES):
            result = relations[role]
            assert isinstance(result, dict)
            statuses_by_relation[index].append(str(result["status"]))
    decisions: list[str] = []
    passed_total = 0
    failed_total = 0
    invalid_total = 0
    for role, statuses in zip(
        contract.RELATION_ROLES,
        statuses_by_relation,
        strict=True,
    ):
        passed = statuses.count("passed")
        failed = statuses.count("failed")
        invalid = statuses.count("invalid")
        evaluable = passed + failed
        passed_total += passed
        failed_total += failed
        invalid_total += invalid
        required_pass = 0 if evaluable == 0 else evaluable
        decision = (
            "blocked"
            if evaluable < minimum_support
            else ("passed" if passed >= required_pass else "failed")
        )
        decisions.append(decision)
        relation_results.append(
            {
                "relation_role": role,
                "case_count": len(receipts),
                "evaluable_count": evaluable,
                "passed_count": passed,
                "failed_count": failed,
                "invalid_count": invalid,
                "minimum_support_required_count": minimum_support,
                "required_pass_count": required_pass,
                "pass_rate": None if evaluable == 0 else passed / evaluable,
                "decision": decision,
            }
        )
    evaluable_total = passed_total + failed_total
    all_three_passed = sum(
        all(status == "passed" for status in statuses)
        for statuses in zip(*statuses_by_relation, strict=True)
    )
    any_invalid = sum(
        "invalid" in statuses for statuses in zip(*statuses_by_relation, strict=True)
    )
    overall_decision = (
        "blocked"
        if "blocked" in decisions
        else ("failed" if "failed" in decisions else "passed")
    )
    return {
        "schema_version": contract.REPORT_SCHEMA_VERSION,
        "status": "aggregate_only_completed",
        "privacy_zone": "A1_aggregate_only",
        "leakage_guard": {
            "contains_row_or_case_data": False,
            "contains_audio_or_path_data": False,
            "contains_speaker_or_session_data": False,
            "contains_phone_or_transcript_data": False,
            "contains_boundary_or_mask_data": False,
            "contains_restricted_hashes_other_than_bundle_digest": False,
            "contains_free_text": False,
        },
        "private_receipt_bundle_sha256": bundle_sha256,
        "case_count": len(receipts),
        "relation_results": relation_results,
        "overall": {
            "case_count": len(receipts),
            "relation_count": 3 * len(receipts),
            "evaluable_relation_count": evaluable_total,
            "passed_relation_count": passed_total,
            "failed_relation_count": failed_total,
            "invalid_relation_count": invalid_total,
            "all_three_relations_passed_case_count": all_three_passed,
            "any_invalid_relation_case_count": any_invalid,
            "pass_rate": (
                None if evaluable_total == 0 else passed_total / evaluable_total
            ),
            "decision": overall_decision,
        },
        "publication_allowed": False,
        "paper_complete": False,
    }


def _valid_bundle() -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, object],
]:
    first_case = _case(suffix="1")
    second_case = _case(suffix="2")
    first_receipt = _receipt(first_case)
    second_receipt = _receipt(
        second_case,
        statuses=("passed", "failed", "invalid"),
    )
    receipts = [first_receipt, second_receipt]
    return [first_case, second_case], receipts, _report(receipts)


def _assert_all_object_schemas_are_closed(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
        for item in value.values():
            _assert_all_object_schemas_are_closed(item)
    elif isinstance(value, list):
        for item in value:
            _assert_all_object_schemas_are_closed(item)


def test_all_three_json_schemas_parse_and_close_every_object() -> None:
    observed_ids: list[str] = []
    for path in SCHEMA_PATHS:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(schema, dict)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False
        observed_ids.append(str(schema["$id"]))
        _assert_all_object_schemas_are_closed(schema)

    assert observed_ids == [
        contract.CASE_SCHEMA_VERSION,
        contract.WORKER_SCHEMA_VERSION,
        contract.REPORT_SCHEMA_VERSION,
    ]


def test_valid_case_enforces_observed_same_lineage_and_restricted_hashes() -> None:
    result = contract.validate_phone_case_sidecar(_case())

    assert result.case_id == "e01pc_" + "1" * 32
    assert result.source_phone_label_sha256 == _sha("b")
    assert result.donor_phone_label_sha256 == _sha("c")
    assert result.source_sample_count == 100
    assert result.edit_mask_sample_count == 20


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("root", "contains_locked_test", True, "contains_locked_test"),
        (
            "human_observation",
            "boundary_source",
            "expected_g2p_phone",
            "boundary_source",
        ),
        (
            "human_observation",
            "source_and_donor_phone_differ",
            False,
            "source_and_donor_phone_differ",
        ),
        (
            "human_observation",
            "observers_independent_from_s1",
            False,
            "observers_independent_from_s1",
        ),
        (
            "consent",
            "source_derivative_processing_allowed",
            False,
            "source_derivative_processing_allowed",
        ),
        (
            "consent",
            "donor_derivative_processing_allowed",
            False,
            "donor_derivative_processing_allowed",
        ),
        (
            "consent",
            "phone_splice_or_resynthesis_allowed",
            False,
            "phone_splice_or_resynthesis_allowed",
        ),
        (
            "consent",
            "public_audio_release_allowed",
            True,
            "public_audio_release_allowed",
        ),
    ],
)
def test_case_rejects_protected_observation_or_consent_drift(
    section: str,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _case()
    target = payload if section == "root" else payload[section]
    assert isinstance(target, dict)
    target[field] = value

    with pytest.raises(contract.E01PhoneSubstitutionV2ContractError, match=message):
        contract.validate_phone_case_sidecar(payload)


@pytest.mark.parametrize(
    ("source_field", "donor_field"),
    [
        ("source_speaker_lineage_sha256", "donor_speaker_lineage_sha256"),
        ("source_session_lineage_sha256", "donor_session_lineage_sha256"),
        (
            "source_prompt_context_lineage_sha256",
            "donor_prompt_context_lineage_sha256",
        ),
    ],
)
def test_case_rejects_same_lineage_claim_without_equal_digest(
    source_field: str,
    donor_field: str,
) -> None:
    payload = _case()
    lineage = payload["lineage"]
    assert isinstance(lineage, dict)
    assert lineage[source_field] == lineage[donor_field]
    lineage[donor_field] = _sha("f")

    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="lineage mismatch",
    ):
        contract.validate_phone_case_sidecar(payload)


def test_case_rejects_equal_source_and_donor_phone_and_full_mask() -> None:
    same_phone = _case()
    observation = same_phone["human_observation"]
    assert isinstance(observation, dict)
    observation["donor_phone_label_sha256"] = observation["source_phone_label_sha256"]
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="must differ",
    ):
        contract.validate_phone_case_sidecar(same_phone)

    full_mask = _case()
    material = full_mask["restricted_material"]
    assert isinstance(material, dict)
    material["edit_mask_sample_count"] = material["source_sample_count"]
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="outside-mask",
    ):
        contract.validate_phone_case_sidecar(full_mask)


def test_case_rejects_extra_field_wrong_role_order_and_zero_policy_hash() -> None:
    extra = _case()
    extra["speaker_id"] = "forbidden"
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="extra",
    ):
        contract.validate_phone_case_sidecar(extra)

    wrong_order = _case()
    wrong_order["role_graph"] = ["U_to_T", "U_to_I", "I_to_T"]
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="role_graph",
    ):
        contract.validate_phone_case_sidecar(wrong_order)

    zero_policy = _case()
    policies = zero_policy["policy_bindings"]
    assert isinstance(policies, dict)
    policies["invalid_case_policy_sha256"] = "0" * 64
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="nonzero",
    ):
        contract.validate_phone_case_sidecar(zero_policy)


def test_valid_receipt_enforces_three_roles_and_stage_separation() -> None:
    case_payload = _case()
    validated_case = contract.validate_phone_case_sidecar(case_payload)
    result = contract.validate_phone_worker_receipt(
        _receipt(case_payload),
        case=validated_case,
    )

    assert result.case_id == validated_case.case_id
    assert result.relation_statuses == ("passed", "passed", "passed")


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        (
            "invariants",
            "same_sample_count_u_i_t",
            False,
            "same_sample_count",
        ),
        (
            "invariants",
            "u_t_outside_edit_mask_exact_identity",
            False,
            "u_t_outside",
        ),
        (
            "invariants",
            "automatic_clipping_or_normalization_applied",
            True,
            "automatic_clipping",
        ),
        (
            "stage_separation",
            "s201_phone_gold_feature_or_target_used",
            True,
            "s201_phone_gold",
        ),
        (
            "stage_separation",
            "s201_score_used_for_phone_decision",
            True,
            "s201_score",
        ),
        (
            "stage_separation",
            "s202_used_as_phone_gold",
            True,
            "s202_used_as_phone_gold",
        ),
        (
            "stage_separation",
            "s202_phone_label_or_boundary_used",
            True,
            "s202_phone_label",
        ),
        (
            "stage_separation",
            "s202_score_used_for_phone_decision",
            True,
            "s202_score",
        ),
        (
            "independent_phone_verifier",
            "s1_features_accessed",
            True,
            "s1_features",
        ),
        (
            "independent_phone_verifier",
            "trained_on_s1_outputs",
            True,
            "trained_on_s1",
        ),
    ],
)
def test_receipt_rejects_invariant_independence_or_stage_drift(
    section: str,
    field: str,
    value: object,
    message: str,
) -> None:
    case_payload = _case()
    receipt = _receipt(case_payload)
    target = receipt[section]
    assert isinstance(target, dict)
    target[field] = value

    with pytest.raises(contract.E01PhoneSubstitutionV2ContractError, match=message):
        contract.validate_phone_worker_receipt(
            receipt,
            case=contract.validate_phone_case_sidecar(case_payload),
        )


def test_receipt_rejects_sample_count_and_outside_mask_mismatch() -> None:
    case_payload = _case()
    sample_mismatch = _receipt(case_payload)
    outputs = sample_mismatch["role_outputs"]
    assert isinstance(outputs, dict)
    identity = outputs["I"]
    assert isinstance(identity, dict)
    identity["sample_count"] = 99
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="sample count",
    ):
        contract.validate_phone_worker_receipt(
            sample_mismatch,
            case=contract.validate_phone_case_sidecar(case_payload),
        )

    outside_mismatch = _receipt(case_payload)
    outputs = outside_mismatch["role_outputs"]
    assert isinstance(outputs, dict)
    treatment = outputs["T"]
    assert isinstance(treatment, dict)
    treatment["outside_edit_mask_pcm_sha256"] = _sha("f")
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="outside the edit mask",
    ):
        contract.validate_phone_worker_receipt(
            outside_mismatch,
            case=contract.validate_phone_case_sidecar(case_payload),
        )


def test_receipt_rejects_mask_partition_and_unchanged_target_pcm() -> None:
    case_payload = _case()
    wrong_partition = _receipt(case_payload)
    mask = wrong_partition["edit_mask"]
    assert isinstance(mask, dict)
    mask["outside_mask_sample_count"] = 79
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="partition",
    ):
        contract.validate_phone_worker_receipt(
            wrong_partition,
            case=contract.validate_phone_case_sidecar(case_payload),
        )

    unchanged_treatment = _receipt(case_payload)
    outputs = unchanged_treatment["role_outputs"]
    assert isinstance(outputs, dict)
    source = outputs["U"]
    treatment = outputs["T"]
    assert isinstance(source, dict)
    assert isinstance(treatment, dict)
    treatment["pcm_sha256"] = source["pcm_sha256"]
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="targeted T PCM",
    ):
        contract.validate_phone_worker_receipt(
            unchanged_treatment,
            case=contract.validate_phone_case_sidecar(case_payload),
        )


def test_receipt_rejects_wrong_phone_role_or_relation_policy() -> None:
    case_payload = _case()
    wrong_phone = _receipt(case_payload)
    phone_bindings = wrong_phone["observed_phone_bindings"]
    assert isinstance(phone_bindings, dict)
    phone_bindings["i_identity_phone_label_sha256"] = phone_bindings[
        "t_target_phone_label_sha256"
    ]
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="phone role",
    ):
        contract.validate_phone_worker_receipt(
            wrong_phone,
            case=contract.validate_phone_case_sidecar(case_payload),
        )

    wrong_policy = _receipt(case_payload)
    relations = wrong_policy["relations"]
    assert isinstance(relations, dict)
    relation = relations["I_to_T"]
    assert isinstance(relation, dict)
    relation["relation_policy_sha256"] = _sha("f")
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="policy binding mismatch",
    ):
        contract.validate_phone_worker_receipt(
            wrong_policy,
            case=contract.validate_phone_case_sidecar(case_payload),
        )


def test_receipt_rejects_verifier_model_or_training_lineage_shared_with_s1() -> None:
    case_payload = _case()
    for verifier_field, s1_field in (
        ("phone_verifier_model_identity_sha256", "s1_model_identity_sha256"),
        ("phone_verifier_training_lineage_sha256", "s1_training_lineage_sha256"),
    ):
        receipt = _receipt(case_payload)
        verifier = receipt["independent_phone_verifier"]
        assert isinstance(verifier, dict)
        verifier[verifier_field] = verifier[s1_field]
        with pytest.raises(
            contract.E01PhoneSubstitutionV2ContractError,
            match="independent from S1",
        ):
            contract.validate_phone_worker_receipt(
                receipt,
                case=contract.validate_phone_case_sidecar(case_payload),
            )


def test_receipt_invalid_reason_is_status_bound() -> None:
    case_payload = _case()
    invalid = _receipt(
        case_payload,
        statuses=("passed", "invalid", "passed"),
    )
    result = contract.validate_phone_worker_receipt(
        invalid,
        case=contract.validate_phone_case_sidecar(case_payload),
    )
    assert result.relation_statuses[1] == "invalid"

    missing_reason = copy.deepcopy(invalid)
    relations = missing_reason["relations"]
    assert isinstance(relations, dict)
    relation = relations["U_to_T"]
    assert isinstance(relation, dict)
    relation["invalid_reason_code"] = None
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="needs a preregistered reason",
    ):
        contract.validate_phone_worker_receipt(
            missing_reason,
            case=contract.validate_phone_case_sidecar(case_payload),
        )

    reason_on_pass = _receipt(case_payload)
    relations = reason_on_pass["relations"]
    assert isinstance(relations, dict)
    relation = relations["U_to_I"]
    assert isinstance(relation, dict)
    relation["invalid_reason_code"] = "metric_undefined"
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="needs null reason",
    ):
        contract.validate_phone_worker_receipt(
            reason_on_pass,
            case=contract.validate_phone_case_sidecar(case_payload),
        )


def test_valid_a1_report_has_one_digest_and_count_only_relations() -> None:
    case_payload = _case()
    receipt = _receipt(case_payload)
    report = _report([receipt])

    result = contract.validate_phone_aggregate_report(report)

    assert result.case_count == 1
    assert result.relation_status_counts == ((1, 0, 0),) * 3
    assert result.decision == "passed"
    assert contract._count_sha256_values(report) == [
        report["private_receipt_bundle_sha256"]
    ]


@pytest.mark.parametrize(
    "leak_field",
    [
        "row_id",
        "case_id",
        "speaker_id",
        "session_id",
        "phone_label",
        "boundary_samples",
        "audio_sha256",
        "edit_mask_sha256",
        "source_path",
        "transcript",
        "free_text_note",
    ],
)
def test_a1_report_rejects_row_audio_speaker_phone_boundary_or_hash_leak(
    leak_field: str,
) -> None:
    case_payload = _case()
    receipt = _receipt(case_payload)
    report = _report([receipt])
    report[leak_field] = _sha("f")

    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="extra",
    ):
        contract.validate_phone_aggregate_report(report)


def test_a1_report_rejects_nested_leak_and_second_digest() -> None:
    case_payload = _case()
    receipt = _receipt(case_payload)
    report = _report([receipt])
    overall = report["overall"]
    assert isinstance(overall, dict)
    overall["source_audio_sha256"] = _sha("f")

    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="private receipt-bundle digest",
    ):
        contract.validate_phone_aggregate_report(report)


def test_a1_report_rejects_arithmetic_rate_support_and_decision_drift() -> None:
    case_payload = _case()
    receipt = _receipt(case_payload)

    bad_counts = _report([receipt])
    relation_results = bad_counts["relation_results"]
    assert isinstance(relation_results, list)
    first = relation_results[0]
    assert isinstance(first, dict)
    first["failed_count"] = 1
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="counts are inconsistent",
    ):
        contract.validate_phone_aggregate_report(bad_counts)

    bad_rate = _report([receipt])
    relation_results = bad_rate["relation_results"]
    assert isinstance(relation_results, list)
    first = relation_results[0]
    assert isinstance(first, dict)
    first["pass_rate"] = 0.5
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="aggregate counts",
    ):
        contract.validate_phone_aggregate_report(bad_rate)

    bad_support_decision = _report([receipt], minimum_support=2)
    relation_results = bad_support_decision["relation_results"]
    assert isinstance(relation_results, list)
    first = relation_results[0]
    assert isinstance(first, dict)
    first["decision"] = "passed"
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="support",
    ):
        contract.validate_phone_aggregate_report(bad_support_decision)


def test_a1_report_rejects_relation_order_and_paper_completion_claim() -> None:
    case_payload = _case()
    receipt = _receipt(case_payload)
    wrong_order = _report([receipt])
    relation_results = wrong_order["relation_results"]
    assert isinstance(relation_results, list)
    relation_results[0], relation_results[1] = (
        relation_results[1],
        relation_results[0],
    )
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="relation_role",
    ):
        contract.validate_phone_aggregate_report(wrong_order)

    completion_claim = _report([receipt])
    completion_claim["paper_complete"] = True
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="paper_complete",
    ):
        contract.validate_phone_aggregate_report(completion_claim)


def test_full_bundle_cross_checks_digest_counts_and_case_intersections() -> None:
    cases, receipts, report = _valid_bundle()

    result = contract.validate_phone_bundle(
        case_payloads=cases,
        receipt_payloads=list(reversed(receipts)),
        aggregate_report_payload=report,
    )

    assert result.case_count == 2
    assert result.decision == "failed"
    assert result.actual_execution_performed_by_validator is False
    assert result.paper_complete is False


def test_full_bundle_rejects_digest_and_private_count_drift() -> None:
    cases, receipts, report = _valid_bundle()
    bad_digest = copy.deepcopy(report)
    bad_digest["private_receipt_bundle_sha256"] = _sha("f")
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="bundle digest mismatch",
    ):
        contract.validate_phone_bundle(
            case_payloads=cases,
            receipt_payloads=receipts,
            aggregate_report_payload=bad_digest,
        )

    bad_count = copy.deepcopy(report)
    relation_results = bad_count["relation_results"]
    assert isinstance(relation_results, list)
    second = relation_results[1]
    assert isinstance(second, dict)
    second["passed_count"] = 2
    second["failed_count"] = 0
    second["pass_rate"] = 1.0
    second["decision"] = "passed"
    overall = bad_count["overall"]
    assert isinstance(overall, dict)
    overall["passed_relation_count"] = 5
    overall["failed_relation_count"] = 0
    overall["all_three_relations_passed_case_count"] = 1
    overall["pass_rate"] = 1.0
    overall["decision"] = "passed"
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="private receipts",
    ):
        contract.validate_phone_bundle(
            case_payloads=cases,
            receipt_payloads=receipts,
            aggregate_report_payload=bad_count,
        )


def test_full_bundle_rejects_duplicate_case_or_unmatched_receipt() -> None:
    cases, receipts, report = _valid_bundle()
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="duplicate private case_id",
    ):
        contract.validate_phone_bundle(
            case_payloads=[cases[0], copy.deepcopy(cases[0])],
            receipt_payloads=receipts,
            aggregate_report_payload=report,
        )

    unmatched = copy.deepcopy(receipts)
    unmatched[1]["case_id"] = "e01pc_" + "f" * 32
    with pytest.raises(
        contract.E01PhoneSubstitutionV2ContractError,
        match="no matching private case",
    ):
        contract.validate_phone_bundle(
            case_payloads=cases,
            receipt_payloads=unmatched,
            aggregate_report_payload=report,
        )
