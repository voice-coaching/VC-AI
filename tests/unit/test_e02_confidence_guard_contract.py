from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml

from voice_coach.evaluation.e02_confidence_guard_contract import (
    AccessAudit,
    C02Evidence,
    E02ContractError,
    S107Evidence,
    binding_sha256,
    canonical_json_bytes,
    derive_api_guard_response,
    deep_validate_c02_bundle,
    ensure_future_output_targets_absent,
    load_config,
    validate_api_guard_response,
    validate_c02_bundle_commit_payload,
    validate_guard_input_payload,
    validate_preregistration_payload,
    validate_static_config,
)


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG = WORKSPACE / "configs/experiments/e02_confidence_guard_v1.yaml"
PREREG_TEMPLATE = (
    WORKSPACE / "configs/coaching/e02_confidence_guard_preregistration_v1.template.json"
)


def _h(index: int) -> str:
    return hashlib.sha256(f"e02-test-{index}".encode()).hexdigest()


def _evidence(
    *,
    status: str = "selected",
    selected_count: int = 1,
    blocked_count: int = 0,
    ranked_window_count: int = 1,
) -> tuple[S107Evidence, C02Evidence]:
    s107 = S107Evidence(
        commit_sha256=_h(1),
        threshold_report_sha256=_h(2),
        threshold_selection_status="selected_provisional_engineering",
        c02_contract_module=ModuleType("_e02_test_c02"),
        c02_s107_evidence=object(),
    )
    c02 = C02Evidence(
        commit_sha256=_h(3),
        config_sha256=_h(4),
        preregistration_sha256=_h(5),
        diagnostic_input_sha256=_h(6),
        selection_output_sha256=_h(7),
        registry_sha256=_h(8),
        selection_output={
            "status": status,
            "selected_count": selected_count,
            "blocked_count": blocked_count,
            "ranked_window_count": ranked_window_count,
        },
        snapshots=(),
    )
    return s107, c02


def _approved_preregistration(
    s107: S107Evidence,
    c02: C02Evidence,
) -> dict[str, Any]:
    payload = json.loads(PREREG_TEMPLATE.read_text())
    payload.update(
        {
            "preregistration_id": "e02prereg_" + "a" * 32,
            "status": "approved_and_frozen",
            "registered_at_utc": "2026-07-28T12:00:00Z",
            "registered_by_role": "independent_protocol_owner",
            "frozen_before_guard_input_or_api_access": True,
        }
    )
    payload["lineage"] = {
        "s107_completion_commit_sha256": s107.commit_sha256,
        "s107_threshold_policy_report_sha256": s107.threshold_report_sha256,
        "c02_completion_commit_sha256": c02.commit_sha256,
        "c02_preregistration_sha256": c02.preregistration_sha256,
    }
    payload["domain_policy"] = {
        "ood_definition_id": "registered_population_and_acoustic_scope_v1",
        "ood_definition_policy_sha256": _h(10),
        "ood_threshold_or_rule_policy_sha256": _h(11),
        "domain_assessment_receipt_required": True,
        "out_of_domain_blocks_all_automatic_correction": True,
    }
    payload["confidence_policy"] = {
        "threshold_source": "deep_validated_actual_s107_threshold_policy_only",
        "numeric_threshold_copied_into_e02": False,
        "threshold_policy_report_sha256": s107.threshold_report_sha256,
        "confidence_assessment_contract_sha256": _h(12),
        "below_threshold_blocks_all_automatic_correction": True,
    }
    payload["support_and_abstention_policy"] = {
        "unsupported_definition_id": "c02_preregistered_support_contract_v1",
        "unsupported_policy_sha256": _h(13),
        "abstain_all_definition_id": "c02_ranked_window_selected_zero_v1",
        "abstain_all_policy_sha256": _h(14),
        "unsupported_blocks_all_automatic_correction": True,
        "abstain_all_blocks_all_automatic_correction": True,
        "c02_blocked_top_k_backfill_allowed": False,
        "c02_free_generation_allowed": False,
    }
    payload["combination_policy"] = {
        "blocked_reason_precedence": [
            "unsupported",
            "out_of_domain",
            "low_confidence",
            "abstain_all",
        ],
        "safe_next_action_by_reason": {
            "out_of_domain": "expert_review",
            "low_confidence": "rerecord",
            "unsupported": "expert_review",
            "abstain_all": "rerecord",
        },
        "mixed_reason_resolution_id": "first_registered_reason_wins_v1",
        "allowed_blocked_safe_next_actions": [
            "rerecord",
            "expert_review",
        ],
    }
    payload["approval"] = {
        "decision": "approved",
        "protocol_sha256": _h(15),
        "signed_receipt_sha256": _h(16),
    }
    payload["binding_sha256"] = binding_sha256(payload)
    return payload


def _guard_input(
    *,
    policy_sha256: str,
    s107: S107Evidence,
    c02: C02Evidence,
    domain_status: str = "within_registered_domain",
    confidence_status: str = "at_or_above_s107_policy",
    support_status: str = "supported",
    c02_selection_status: str = "selected",
    c02_abstain_all: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "voice_coach.e02_confidence_guard_input.v1",
        "guard_input_id": "e02in_" + "b" * 32,
        "privacy_zone": "D1_pseudonymized_restricted_api_guard",
        "request_binding_sha256": _h(20),
        "lineage": {
            "s107_completion_commit_sha256": s107.commit_sha256,
            "s107_threshold_policy_report_sha256": s107.threshold_report_sha256,
            "c02_completion_commit_sha256": c02.commit_sha256,
            "c02_preregistration_sha256": c02.preregistration_sha256,
            "c02_diagnostic_input_sha256": c02.diagnostic_input_sha256,
            "c02_selection_output_sha256": c02.selection_output_sha256,
            "e02_preregistration_sha256": policy_sha256,
        },
        "assessments": {
            "domain_status": domain_status,
            "confidence_status": confidence_status,
            "support_status": support_status,
            "c02_selection_status": c02_selection_status,
            "c02_abstain_all": c02_abstain_all,
        },
        "evidence_provenance": {
            "domain_assessment_receipt_sha256": _h(21),
            "confidence_assessment_receipt_sha256": _h(22),
            "support_assessment_receipt_sha256": _h(23),
            "c02_selection_receipt_sha256": c02.selection_output_sha256,
        },
        "inherited_safety": {
            "c02_blocked_top_k_no_backfill": True,
            "c02_free_generation_used": False,
            "c02_network_used": False,
            "c02_runtime_model_used": False,
            "automatic_advice_payload_present": False,
            "automatic_reference_payload_present": False,
            "automatic_corrective_action_payload_present": False,
        },
        "binding_sha256": "",
    }
    payload["binding_sha256"] = binding_sha256(payload)
    return payload


def _policy_and_input(
    **input_overrides: Any,
) -> tuple[S107Evidence, C02Evidence, Any, dict[str, Any]]:
    status = input_overrides.get("c02_selection_status", "selected")
    if status == "abstained":
        s107, c02 = _evidence(
            status="abstained",
            selected_count=0,
            blocked_count=1,
            ranked_window_count=1,
        )
        input_overrides.setdefault("c02_abstain_all", True)
    elif status == "no_issues":
        s107, c02 = _evidence(
            status="no_issues",
            selected_count=0,
            blocked_count=0,
            ranked_window_count=0,
        )
    else:
        s107, c02 = _evidence()
    prereg = _approved_preregistration(s107, c02)
    policy = validate_preregistration_payload(prereg, s107=s107, c02=c02)
    payload = _guard_input(
        policy_sha256=policy.sha256,
        s107=s107,
        c02=c02,
        **input_overrides,
    )
    return s107, c02, policy, payload


def _c02_bundle_commit(s107: S107Evidence) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "voice_coach.e02_c02_coaching_bundle_commit.v1",
        "bundle_id": "e02c02bundle_" + "c" * 32,
        "status": "committed",
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "c02_config": {
            "path": "configs/experiments/c02.yaml",
            "sha256": _h(30),
            "size_bytes": 1,
        },
        "s107": {
            "completion_commit_sha256": s107.commit_sha256,
            "threshold_policy_report_sha256": s107.threshold_report_sha256,
        },
        "preregistration": {
            "path": "artifacts/c02/prereg.json",
            "sha256": _h(31),
            "size_bytes": 1,
        },
        "diagnostic_input": {
            "path": "data/processed/c02/input.json",
            "sha256": _h(32),
            "size_bytes": 1,
        },
        "selection_output": {
            "path": "data/processed/c02/output.json",
            "sha256": _h(33),
            "size_bytes": 1,
        },
        "registry": {
            "path": "artifacts/registry/c02.json",
            "sha256": _h(34),
            "size_bytes": 1,
            "status": "completed",
        },
        "assurance": {
            "actual_c02_selection_completed": True,
            "deterministic_recomputation_required": True,
            "blocked_top_k_backfill_allowed": False,
            "free_generation_llm_used": False,
            "prompt_or_completion_present": False,
            "network_accessed": False,
            "runtime_model_accessed": False,
            "gpu_accessed": False,
            "reference_generation_executed": False,
            "production_release_allowed": False,
        },
        "binding_sha256": "",
    }
    payload["binding_sha256"] = binding_sha256(payload)
    return payload


def test_static_config_reads_25_sources_and_nothing_runtime() -> None:
    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CONFIG,
        config=load_config(CONFIG),
        audit=audit,
    )

    assert len(static.source_snapshots) == 25
    assert audit.source_pin_read_count == 25
    assert audit.s107_completion_path_resolution_count == 0
    assert audit.c02_completion_path_resolution_count == 0
    assert audit.request_or_row_access_count == 0
    assert audit.api_invocation_count == 0
    assert audit.model_access_count == 0
    assert audit.output_count == 0


def test_unresolved_c02_commit_stops_before_its_path_resolution() -> None:
    s107, _ = _evidence()
    audit = AccessAudit()

    with pytest.raises(E02ContractError, match="TBD_C02_COMPLETION"):
        deep_validate_c02_bundle(
            workspace_root=WORKSPACE,
            config=load_config(CONFIG),
            s107=s107,
            audit=audit,
        )

    assert audit.c02_completion_path_resolution_count == 0
    assert audit.c02_completion_read_attempt_count == 0
    assert audit.c02_completion_read_count == 0


def test_deep_c02_bundle_reads_effective_config_as_yaml(tmp_path: Path) -> None:
    def write_bytes(relative: str, data: bytes) -> dict[str, Any]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {
            "path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }

    def write_json(relative: str, payload: dict[str, Any]) -> dict[str, Any]:
        return write_bytes(relative, canonical_json_bytes(payload))

    module = ModuleType("_e02_fake_c02_contract")
    s107 = S107Evidence(
        commit_sha256=_h(70),
        threshold_report_sha256=_h(71),
        threshold_selection_status="selected_provisional_engineering",
        c02_contract_module=module,
        c02_s107_evidence=object(),
    )
    prereg_record = write_json("artifacts/c02/prereg.json", {"fixture": True})
    input_record = write_json(
        "data/processed/c02/input.json",
        {"schema_version": "voice_coach.c02_diagnostic_issues_input.v1"},
    )
    output_payload = {
        "schema_version": "voice_coach.c02_coaching_selection_output.v1",
        "status": "selected",
        "ranked_window_count": 1,
        "selected_count": 1,
        "blocked_count": 0,
    }
    output_record = write_json("data/processed/c02/output.json", output_payload)
    config_payload = {
        "predecessor_s107": {
            "completion_commit_sha256": s107.commit_sha256,
            "threshold_artifact_sha256": s107.threshold_report_sha256,
        },
        "preregistration": {
            "approved_artifact_path": prereg_record["path"],
            "approved_artifact_sha256": prereg_record["sha256"],
        },
        "outputs": {
            "selection_manifest_path": output_record["path"],
            "registry_path": "artifacts/registry/c02.json",
            "commit_path": "artifacts/c02/commit.json",
        },
    }
    config_record = write_bytes(
        "configs/c02.yaml",
        yaml.safe_dump(config_payload, sort_keys=True).encode(),
    )
    registry_payload: dict[str, Any] = {
        "schema_version": "voice_coach.e02_c02_coaching_bundle_registry.v1",
        "status": "completed",
        "c02_config_sha256": config_record["sha256"],
        "c02_preregistration_sha256": prereg_record["sha256"],
        "diagnostic_input_sha256": input_record["sha256"],
        "selection_output_sha256": output_record["sha256"],
        "s107_completion_commit_sha256": s107.commit_sha256,
        "s107_threshold_policy_report_sha256": s107.threshold_report_sha256,
        "deterministic_catalog_only": True,
        "blocked_top_k_backfill_allowed": False,
        "free_generation_llm_used": False,
        "prompt_or_completion_present": False,
        "network_accessed": False,
        "runtime_model_accessed": False,
        "gpu_accessed": False,
        "reference_generation_executed": False,
        "production_release_allowed": False,
        "binding_sha256": "",
    }
    registry_payload["binding_sha256"] = binding_sha256(registry_payload)
    registry_record = write_json(
        config_payload["outputs"]["registry_path"],
        registry_payload,
    )
    registry_record["status"] = "completed"
    commit = _c02_bundle_commit(s107)
    commit.update(
        {
            "c02_config": config_record,
            "preregistration": prereg_record,
            "diagnostic_input": input_record,
            "selection_output": output_record,
            "registry": registry_record,
        }
    )
    commit["binding_sha256"] = binding_sha256(commit)
    commit_record = write_json(config_payload["outputs"]["commit_path"], commit)

    class FakeAudit:
        source_pin_read_count = 0

    prereg_path = tmp_path / prereg_record["path"]
    prereg_stat = prereg_path.stat()
    prereg_snapshot = SimpleNamespace(
        relative_path=prereg_record["path"],
        sha256=prereg_record["sha256"],
        size=prereg_record["size_bytes"],
        device=prereg_stat.st_dev,
        inode=prereg_stat.st_ino,
        mtime_ns=prereg_stat.st_mtime_ns,
    )
    module.AccessAudit = FakeAudit
    module.load_config = lambda path: yaml.safe_load(path.read_text())
    module.validate_static_config = lambda **kwargs: SimpleNamespace(
        config_sha256=config_record["sha256"],
        source_snapshots=(),
    )
    module.deep_validate_c01 = lambda **kwargs: object()
    module._read_approved_preregistration = lambda **kwargs: SimpleNamespace(
        sha256=prereg_record["sha256"],
        snapshot=prereg_snapshot,
    )
    module.validate_diagnostic_input_payload = lambda *args, **kwargs: None
    module.validate_coaching_output_payload = lambda *args, **kwargs: None

    config = {
        "predecessor_c02": {
            "config_path": config_record["path"],
            "config_sha256": config_record["sha256"],
            "completion_commit_path": commit_record["path"],
            "completion_commit_sha256": commit_record["sha256"],
            "required_diagnostic_input_schema_version": (
                "voice_coach.c02_diagnostic_issues_input.v1"
            ),
            "required_selection_output_schema_version": (
                "voice_coach.c02_coaching_selection_output.v1"
            ),
        }
    }
    audit = AccessAudit()
    evidence = deep_validate_c02_bundle(
        workspace_root=tmp_path,
        config=config,
        s107=s107,
        audit=audit,
    )

    assert evidence.config_sha256 == config_record["sha256"]
    assert evidence.selection_output == output_payload
    assert audit.c02_deep_recomputation_count == 1


@pytest.mark.parametrize(
    ("overrides", "reason", "safe_action"),
    [
        (
            {"domain_status": "out_of_registered_domain"},
            "out_of_domain",
            "expert_review",
        ),
        (
            {"confidence_status": "below_s107_policy"},
            "low_confidence",
            "rerecord",
        ),
        (
            {"support_status": "unsupported"},
            "unsupported",
            "expert_review",
        ),
        (
            {"c02_selection_status": "abstained"},
            "abstain_all",
            "rerecord",
        ),
    ],
)
def test_every_registered_outside_confidence_condition_blocks_all_automatic_output(
    overrides: dict[str, Any],
    reason: str,
    safe_action: str,
) -> None:
    s107, c02, policy, input_payload = _policy_and_input(**overrides)
    response = derive_api_guard_response(
        input_payload,
        policy=policy,
        s107=s107,
        c02=c02,
    )

    assert response["status"] == "blocked"
    assert response["blocked"] is True
    assert response["blocked_reasons"] == [reason]
    assert response["safe_next_action"] == safe_action
    assert not any(response["capability_decision"].values())
    assert response["safety_receipt"]["advice_payload_present"] is False
    assert response["safety_receipt"]["reference_payload_present"] is False
    assert response["safety_receipt"]["corrective_action_payload_present"] is False


def test_mixed_reason_order_and_safe_action_come_only_from_frozen_policy() -> None:
    s107, c02, policy, input_payload = _policy_and_input(
        domain_status="out_of_registered_domain",
        confidence_status="below_s107_policy",
        support_status="unsupported",
    )
    response = derive_api_guard_response(
        input_payload,
        policy=policy,
        s107=s107,
        c02=c02,
    )

    assert response["blocked_reasons"] == [
        "unsupported",
        "out_of_domain",
        "low_confidence",
    ]
    assert response["safe_next_action"] == "expert_review"


def test_in_domain_supported_selected_request_only_allows_separate_renderer() -> None:
    s107, c02, policy, input_payload = _policy_and_input()
    response = derive_api_guard_response(
        input_payload,
        policy=policy,
        s107=s107,
        c02=c02,
    )

    assert response["status"] == "allowed_for_separate_renderer"
    assert response["blocked"] is False
    assert response["blocked_reasons"] == []
    assert response["safe_next_action"] is None
    assert response["capability_decision"] == {
        "automatic_coaching_advice_allowed": True,
        "automatic_reference_allowed": True,
        "automatic_corrective_action_allowed": True,
        "separate_renderer_required": True,
    }
    assert response["safety_receipt"]["api_executed_by_static_package"] is False


def test_no_issue_request_returns_no_correction_without_capabilities() -> None:
    s107, c02, policy, input_payload = _policy_and_input(
        c02_selection_status="no_issues"
    )
    response = derive_api_guard_response(
        input_payload,
        policy=policy,
        s107=s107,
        c02=c02,
    )

    assert response["status"] == "no_correction"
    assert response["blocked"] is False
    assert response["blocked_reasons"] == []
    assert response["safe_next_action"] is None
    assert not any(response["capability_decision"].values())


def test_response_is_exactly_recomputed_and_tamper_fails() -> None:
    s107, c02, policy, input_payload = _policy_and_input(
        confidence_status="below_s107_policy"
    )
    response = derive_api_guard_response(
        input_payload,
        policy=policy,
        s107=s107,
        c02=c02,
    )
    validate_api_guard_response(
        response,
        input_payload=input_payload,
        policy=policy,
        s107=s107,
        c02=c02,
    )

    response["capability_decision"]["automatic_reference_allowed"] = True
    response["binding_sha256"] = binding_sha256(response)
    with pytest.raises(E02ContractError):
        validate_api_guard_response(
            response,
            input_payload=input_payload,
            policy=policy,
            s107=s107,
            c02=c02,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_score", 0.99),
        ("name", "person"),
        ("audio_path", "/raw/audio.wav"),
        ("free_text", "generated explanation"),
        ("advice", {"message": "unsafe"}),
    ],
)
def test_api_score_pii_path_free_text_or_payload_leakage_is_rejected(
    field: str,
    value: object,
) -> None:
    s107, c02, policy, input_payload = _policy_and_input()
    input_payload[field] = value
    input_payload["binding_sha256"] = binding_sha256(input_payload)
    with pytest.raises(E02ContractError):
        validate_guard_input_payload(
            input_payload,
            policy=policy,
            s107=s107,
            c02=c02,
        )


def test_input_lineage_no_backfill_and_free_generation_are_enforced() -> None:
    s107, c02, policy, input_payload = _policy_and_input()
    input_payload["lineage"]["s107_threshold_policy_report_sha256"] = _h(50)
    input_payload["binding_sha256"] = binding_sha256(input_payload)
    with pytest.raises(E02ContractError):
        validate_guard_input_payload(
            input_payload,
            policy=policy,
            s107=s107,
            c02=c02,
        )

    _, _, _, input_payload = _policy_and_input()
    input_payload["inherited_safety"]["c02_free_generation_used"] = True
    input_payload["binding_sha256"] = binding_sha256(input_payload)
    with pytest.raises(E02ContractError):
        validate_guard_input_payload(
            input_payload,
            policy=policy,
            s107=s107,
            c02=c02,
        )


def test_unresolved_or_numeric_threshold_preregistration_fails_closed() -> None:
    s107, c02 = _evidence()
    template = json.loads(PREREG_TEMPLATE.read_text())
    with pytest.raises(E02ContractError):
        validate_preregistration_payload(template, s107=s107, c02=c02)

    approved = _approved_preregistration(s107, c02)
    approved["confidence_policy"]["exact_threshold"] = 0.75
    approved["binding_sha256"] = binding_sha256(approved)
    with pytest.raises(E02ContractError):
        validate_preregistration_payload(approved, s107=s107, c02=c02)


def test_preregistration_must_inherit_c02_reason_specific_safe_actions() -> None:
    s107, c02 = _evidence()
    approved = _approved_preregistration(s107, c02)
    approved["combination_policy"]["safe_next_action_by_reason"]["low_confidence"] = (
        "expert_review"
    )
    approved["binding_sha256"] = binding_sha256(approved)

    with pytest.raises(E02ContractError, match="low-confidence"):
        validate_preregistration_payload(approved, s107=s107, c02=c02)


def test_c02_bundle_commit_requires_s107_binding_and_all_safety_assurances() -> None:
    s107, _ = _evidence()
    payload = _c02_bundle_commit(s107)
    validate_c02_bundle_commit_payload(payload, s107=s107)

    payload["assurance"]["blocked_top_k_backfill_allowed"] = True
    payload["binding_sha256"] = binding_sha256(payload)
    with pytest.raises(E02ContractError):
        validate_c02_bundle_commit_payload(payload, s107=s107)

    payload = _c02_bundle_commit(s107)
    payload["s107"]["threshold_policy_report_sha256"] = _h(60)
    payload["binding_sha256"] = binding_sha256(payload)
    with pytest.raises(E02ContractError):
        validate_c02_bundle_commit_payload(payload, s107=s107)


def test_config_drift_cannot_add_or_enable_numeric_threshold() -> None:
    config = deepcopy(load_config(CONFIG))
    config["guard_contract"]["exact_confidence_threshold"] = 0.75
    with pytest.raises(E02ContractError):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG,
            config=config,
        )

    config = deepcopy(load_config(CONFIG))
    config["guard_contract"]["numeric_threshold_in_config_allowed"] = True
    with pytest.raises(E02ContractError):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG,
            config=config,
        )

    config = deepcopy(load_config(CONFIG))
    config["predecessor_s107"]["completion_validator_symbol"] = "other_validator"
    with pytest.raises(E02ContractError):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG,
            config=config,
        )

    config = deepcopy(load_config(CONFIG))
    config["predecessor_c02"]["required_registry_schema_version"] = "drifted"
    with pytest.raises(E02ContractError):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG,
            config=config,
        )


def test_future_output_guards_traversal_existing_and_symlink(tmp_path: Path) -> None:
    outputs = {
        "guard_decision_path": "artifacts/e02/decision.json",
        "aggregate_report_path": "artifacts/e02/report.json",
        "registry_path": "artifacts/e02/registry.json",
        "commit_path": "artifacts/e02/commit.json",
        "failure_report_path": "artifacts/e02/failure.json",
        "no_overwrite": True,
        "atomic_publish_required": True,
        "write_enabled_in_this_package": False,
    }
    ensure_future_output_targets_absent(workspace_root=tmp_path, outputs=outputs)
    traversal = {**outputs, "guard_decision_path": "../escape.json"}
    with pytest.raises(E02ContractError):
        ensure_future_output_targets_absent(
            workspace_root=tmp_path,
            outputs=traversal,
        )
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "e02").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(E02ContractError):
        ensure_future_output_targets_absent(
            workspace_root=tmp_path,
            outputs=outputs,
        )
