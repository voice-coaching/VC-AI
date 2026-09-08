from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from voice_coach.coaching.c02_selection_contract import (
    AccessAudit,
    C01Evidence,
    C02ContractError,
    FileSnapshot,
    PreregistrationPolicy,
    S107Evidence,
    derive_selection_payload,
    ensure_future_output_targets_absent,
    load_config,
    validate_coaching_output_payload,
    validate_diagnostic_input_payload,
    validate_preregistration_payload,
    validate_static_config,
)


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG = WORKSPACE / "configs/experiments/c02_deterministic_coaching_selection_v1.yaml"
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64
H5 = "5" * 64
H6 = "6" * 64


def _snapshot(relative: str, sha: str) -> FileSnapshot:
    return FileSnapshot(relative, sha, 1, 1, 1, 1)


def _catalog() -> dict[str, object]:
    return {
        "tags": [
            {
                "id": "initial_consonant",
                "localization_policy": "phone_overlap_required",
                "advice": {
                    "advice_id": "KO_INITIAL_CONSONANT_RETRY_001",
                    "action": "draft_coach",
                    "message_ko": "초성 조언",
                    "review_status": "engineering_draft",
                },
            },
            {
                "id": "repetition",
                "localization_policy": "no_target_phone_claim",
                "advice": {
                    "advice_id": "KO_REPETITION_RETRY_001",
                    "action": "draft_coach",
                    "message_ko": "반복 조언",
                    "review_status": "engineering_draft",
                },
            },
            {
                "id": "stress_error",
                "localization_policy": "prosodic_span_only",
                "advice": {
                    "advice_id": "KO_STRESS_RETRY_001",
                    "action": "draft_coach",
                    "message_ko": "강세 조언",
                    "review_status": "engineering_draft",
                },
            },
            {
                "id": "other",
                "localization_policy": "no_target_phone_claim",
                "advice": {
                    "advice_id": None,
                    "action": "abstain_expert_review",
                    "message_ko": None,
                    "review_status": "not_applicable",
                },
            },
        ]
    }


@pytest.fixture
def evidence() -> tuple[PreregistrationPolicy, C01Evidence, S107Evidence]:
    policy = PreregistrationPolicy(
        sha256=H4,
        confidence_threshold=0.75,
        confidence_semantics="calibrated_issue_error_probability",
        issue_confidence_aggregation="maximum_overlapping_phone_probability",
        minimum_supported_evidence_count=1,
        evidence_by_issue_type={
            "initial_consonant": frozenset({"gold_label", "gop_low"}),
            "repetition": frozenset({"content_mismatch"}),
            "stress_error": frozenset({"prosody_outlier"}),
            "other": frozenset(),
        },
        payload={},
        snapshot=None,
    )
    c01 = C01Evidence(
        report_sha256=H1,
        report={},
        catalog=_catalog(),
        snapshots=(),
    )
    s107 = S107Evidence(
        commit_sha256=H2,
        threshold_report_sha256=H3,
        issue_threshold=0.75,
        threshold_selection_status="selected_provisional_engineering",
        commit_snapshot=_snapshot("commit.json", H2),
        threshold_snapshot=_snapshot("threshold.json", H3),
    )
    return policy, c01, s107


def _issue(
    *,
    issue_id: str = H5,
    issue_type: str = "initial_consonant",
    start_ms: int = 10,
    confidence: float = 0.9,
    severity: float = 0.9,
    evidence_ids: list[str] | None = None,
    target: str | None = "ㄱ",
    overlap: str | None = H6,
) -> dict[str, object]:
    return {
        "issue_id_sha256": issue_id,
        "issue_type": issue_type,
        "start_ms": start_ms,
        "end_ms": start_ms + 20,
        "target": target,
        "observed": "ㅋ",
        "severity": severity,
        "calibrated_confidence": confidence,
        "comprehensibility_impact": 0.8,
        "trainability": 0.7,
        "evidence_ids": evidence_ids or ["gold_label"],
        "s107_calibration_row_sha256": H1,
        "claim_evidence_sha256": H2,
        "target_overlap_evidence_sha256": overlap,
    }


def _input(
    policy: PreregistrationPolicy,
    *,
    top_k: int = 1,
    issues: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "voice_coach.c02_diagnostic_issues_input.v1",
        "input_id": "c02in_" + "a" * 32,
        "request_id_sha256": H6,
        "c01_validation_report_sha256": H1,
        "s107_completion_commit_sha256": H2,
        "s107_threshold_policy_report_sha256": H3,
        "c02_preregistration_sha256": policy.sha256,
        "top_k": top_k,
        "confidence_threshold": policy.confidence_threshold,
        "confidence_semantics": policy.confidence_semantics,
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "issues": issues if issues is not None else [_issue()],
    }


def _derive(
    payload: dict[str, object],
    evidence: tuple[PreregistrationPolicy, C01Evidence, S107Evidence],
) -> dict[str, object]:
    policy, c01, s107 = evidence
    return dict(
        derive_selection_payload(
            payload,
            policy=policy,
            c01=c01,
            s107=s107,
            c02_config_sha256=H5,
        )
    )


def test_static_config_validates_exactly_16_sources_without_runtime_access() -> None:
    config = load_config(CONFIG)
    audit = AccessAudit()
    result = validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CONFIG,
        config=config,
        audit=audit,
    )

    assert len(result.source_snapshots) == 16
    assert audit.source_pin_read_count == 16
    assert audit.c01_deep_validation_count == 0
    assert audit.s107_completion_path_resolution_count == 0
    assert audit.diagnostic_input_read_count == 0
    assert audit.coaching_selection_count == 0
    assert audit.output_count == 0


def test_top1_exact_catalog_message_and_threshold_equality_are_supported(
    evidence: tuple[PreregistrationPolicy, C01Evidence, S107Evidence],
) -> None:
    policy, _, _ = evidence
    payload = _input(policy, issues=[_issue(confidence=0.75)])
    output = _derive(payload, evidence)
    decision = output["ranked_window"][0]  # type: ignore[index]

    assert output["status"] == "selected"
    assert output["action"] == "coach"
    assert decision["action"] == "coach"
    assert decision["message_ko"] == "초성 조언"
    assert decision["advice_id"] == "KO_INITIAL_CONSONANT_RETRY_001"
    assert decision["priority_score_decimal"] == "0.378000000000"
    assert output["generation_contract"]["free_generation_llm_used"] is False  # type: ignore[index]


def test_low_confidence_abstains_to_rerecord(
    evidence: tuple[PreregistrationPolicy, C01Evidence, S107Evidence],
) -> None:
    policy, _, _ = evidence
    output = _derive(_input(policy, issues=[_issue(confidence=0.749)]), evidence)
    decision = output["ranked_window"][0]  # type: ignore[index]

    assert output["status"] == "abstained"
    assert output["action"] == "rerecord"
    assert decision["abstention_reason"] == "low_confidence"
    assert decision["message_ko"] is None


@pytest.mark.parametrize(
    ("issue", "reason"),
    [
        (_issue(issue_type="unknown_issue"), "unknown"),
        (_issue(issue_type="other", target=None, overlap=None), "other"),
        (_issue(evidence_ids=["duration_short"]), "unsupported evidence"),
        (_issue(target=None, overlap=None), "missing positive overlap receipt"),
        (
            _issue(
                issue_type="repetition",
                evidence_ids=["content_mismatch"],
                target="ㄱ",
                overlap=H6,
            ),
            "forbidden target phone claim",
        ),
    ],
)
def test_unsupported_claims_abstain_to_expert_review(
    evidence: tuple[PreregistrationPolicy, C01Evidence, S107Evidence],
    issue: dict[str, object],
    reason: str,
) -> None:
    del reason
    policy, _, _ = evidence
    output = _derive(_input(policy, issues=[issue]), evidence)
    decision = output["ranked_window"][0]  # type: ignore[index]

    assert output["status"] == "abstained"
    assert output["action"] == "expert_review"
    assert decision["abstention_reason"] == "unsupported_claim"
    assert decision["advice_id"] is None
    assert decision["message_ko"] is None


def test_blocked_top1_is_not_backfilled_by_lower_ranked_supported_issue(
    evidence: tuple[PreregistrationPolicy, C01Evidence, S107Evidence],
) -> None:
    policy, _, _ = evidence
    blocked = _issue(
        issue_id=H5,
        issue_type="unknown_issue",
        severity=1.0,
        confidence=1.0,
    )
    supported = _issue(issue_id=H6, severity=0.5, confidence=0.9)
    output = _derive(_input(policy, issues=[supported, blocked]), evidence)

    assert output["ranked_window_count"] == 1
    assert output["selected_count"] == 0
    assert output["blocked_count"] == 1
    assert output["ranked_window"][0]["issue_id_sha256"] == H5  # type: ignore[index]


def test_top3_tie_order_is_input_order_independent(
    evidence: tuple[PreregistrationPolicy, C01Evidence, S107Evidence],
) -> None:
    policy, _, _ = evidence
    first = _issue(issue_id=H5, start_ms=20)
    second = _issue(issue_id=H6, start_ms=10)
    left = _derive(_input(policy, top_k=3, issues=[first, second]), evidence)
    right = _derive(_input(policy, top_k=3, issues=[second, first]), evidence)

    assert (
        [
            row["issue_id_sha256"]
            for row in left["ranked_window"]  # type: ignore[union-attr]
        ]
        == [
            row["issue_id_sha256"]
            for row in right["ranked_window"]  # type: ignore[union-attr]
        ]
        == [H6, H5]
    )


def test_no_issues_returns_none_without_text(
    evidence: tuple[PreregistrationPolicy, C01Evidence, S107Evidence],
) -> None:
    policy, _, _ = evidence
    output = _derive(_input(policy, top_k=3, issues=[]), evidence)

    assert output["status"] == "no_issues"
    assert output["action"] == "none"
    assert output["ranked_window"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"top_k": 2}),
        lambda payload: payload.update({"prompt": "write advice"}),
        lambda payload: payload["issues"][0].update(  # type: ignore[index,union-attr]
            {"severity": float("nan")}
        ),
        lambda payload: payload["issues"].append(  # type: ignore[union-attr]
            deepcopy(payload["issues"][0])  # type: ignore[index]
        ),
    ],
)
def test_invalid_or_generative_diagnostic_payload_fails_closed(
    evidence: tuple[PreregistrationPolicy, C01Evidence, S107Evidence],
    mutation: object,
) -> None:
    policy, c01, s107 = evidence
    payload = _input(policy)
    mutation(payload)  # type: ignore[operator]

    with pytest.raises(C02ContractError):
        validate_diagnostic_input_payload(
            payload,
            policy=policy,
            c01=c01,
            s107=s107,
        )


def test_tampered_message_or_llm_flag_fails_output_recomputation(
    evidence: tuple[PreregistrationPolicy, C01Evidence, S107Evidence],
) -> None:
    policy, c01, s107 = evidence
    input_payload = _input(policy)
    output = _derive(input_payload, evidence)
    output["ranked_window"][0]["message_ko"] = "임의 생성 문장"  # type: ignore[index]

    with pytest.raises(C02ContractError):
        validate_coaching_output_payload(
            output,
            input_payload=input_payload,
            policy=policy,
            c01=c01,
            s107=s107,
            c02_config_sha256=H5,
        )
    output = _derive(input_payload, evidence)
    output["generation_contract"]["free_generation_llm_used"] = True  # type: ignore[index]
    with pytest.raises(C02ContractError):
        validate_coaching_output_payload(
            output,
            input_payload=input_payload,
            policy=policy,
            c01=c01,
            s107=s107,
            c02_config_sha256=H5,
        )


def _approved_preregistration() -> dict[str, object]:
    return {
        "schema_version": "voice_coach.c02_coaching_selection_preregistration.v1",
        "preregistration_id": "c02prereg_" + "a" * 32,
        "status": "approved_and_frozen",
        "registered_at_utc": "2026-07-29T12:00:00Z",
        "registered_by_role": "independent_protocol_owner",
        "frozen_before_diagnostic_issue_or_coaching_output_access": True,
        "c01_validation_report_sha256": H1,
        "s107_completion_commit_sha256": H2,
        "s107_threshold_policy_report_sha256": H3,
        "selection_policy": {
            "priority_formula": (
                "severity*calibrated_confidence*comprehensibility_impact*trainability"
            ),
            "allowed_top_k": [1, 3],
            "default_top_k": 1,
            "ranking_tie_breakers": [
                "start_ms",
                "end_ms",
                "issue_type",
                "target",
                "observed",
                "evidence_ids",
                "issue_id_sha256",
            ],
            "blocked_top_k_backfill_allowed": False,
            "priority_arithmetic": "base10_decimal_from_canonical_json_numbers",
            "priority_output_scale": 12,
            "priority_rounding": "decimal_round_half_even",
        },
        "calibration_policy": {
            "confidence_semantics": "calibrated_issue_error_probability",
            "issue_confidence_aggregation_from_s107_phone_probabilities": (
                "maximum_overlapping_phone_probability"
            ),
            "exact_confidence_threshold": 0.75,
            "threshold_comparison": "greater_than_or_equal",
            "threshold_authorization_rule": ("exactly_s107_selected_issue_threshold"),
            "low_confidence_action": "rerecord",
            "s107_provisional_threshold_accepted_without_preregistration": False,
        },
        "evidence_policy": {
            "allowed_evidence_ids": [
                "gold_label",
                "ctc_competing_phone",
                "gop_low",
                "duration_short",
                "duration_long",
                "alignment_low",
                "content_mismatch",
                "prosody_outlier",
            ],
            "issue_type_to_supported_evidence_ids": {
                "initial_consonant": ["gold_label"],
                "repetition": ["content_mismatch"],
                "stress_error": ["prosody_outlier"],
                "other": [],
            },
            "minimum_supported_evidence_count": 1,
            "positive_target_overlap_receipt_required_for_phone_localized_claim": True,
            "unsupported_claim_action": "expert_review",
            "unknown_issue_type_action": "expert_review",
            "other_issue_type_action": "expert_review",
        },
        "generation_safety": {
            "message_source": "exact_c01_catalog_entry_only",
            "free_generation_llm_allowed": False,
            "prompt_or_completion_field_allowed": False,
            "network_access_allowed": False,
            "runtime_model_access_allowed": False,
            "catalog_message_rewrite_allowed": False,
            "catalog_advice_id_rewrite_allowed": False,
            "production_release_allowed_before_c03": False,
        },
        "approval": {
            "decision": "approved",
            "protocol_sha256": H5,
            "signed_receipt_sha256": H6,
        },
    }


def test_approved_preregistration_binds_exact_s107_threshold(
    evidence: tuple[PreregistrationPolicy, C01Evidence, S107Evidence],
) -> None:
    _, c01, s107 = evidence
    policy = validate_preregistration_payload(
        _approved_preregistration(),
        c01_report_sha256=H1,
        s107=s107,
        catalog=c01.catalog,
    )
    assert policy.confidence_threshold == 0.75
    assert policy.evidence_by_issue_type["other"] == frozenset()

    drift = _approved_preregistration()
    drift["calibration_policy"]["exact_confidence_threshold"] = 0.74  # type: ignore[index]
    with pytest.raises(C02ContractError):
        validate_preregistration_payload(
            drift,
            c01_report_sha256=H1,
            s107=s107,
            catalog=c01.catalog,
        )
    with pytest.raises(C02ContractError):
        validate_preregistration_payload(
            _approved_preregistration(),
            c01_report_sha256=H1,
            s107=replace(
                s107,
                threshold_selection_status="abstain_all_no_provisional_candidate",
            ),
            catalog=c01.catalog,
        )


def test_future_output_target_guards_traversal_existing_and_symlink(
    tmp_path: Path,
) -> None:
    base = {
        "no_overwrite": True,
        "atomic_publish_required": True,
        "write_enabled_in_this_package": False,
        "selection_manifest_path": "artifacts/c02/new.json",
    }
    ensure_future_output_targets_absent(workspace_root=tmp_path, outputs=base)
    traversal = {**base, "selection_manifest_path": "../escape.json"}
    with pytest.raises(C02ContractError):
        ensure_future_output_targets_absent(
            workspace_root=tmp_path,
            outputs=traversal,
        )
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "c02").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(C02ContractError):
        ensure_future_output_targets_absent(workspace_root=tmp_path, outputs=base)
