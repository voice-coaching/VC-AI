from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from voice_coach.references.r05_grpo_decision_contract import (
    ASSURANCE_KEYS,
    EVIDENCE_BINDING_KEYS,
    RUNTIME_COUNTER_FIELDS,
    SOURCE_PATHS,
    AccessAudit,
    R05ContractError,
    canonical_json_bytes,
    config_only_result,
    ensure_output_targets_absent,
    load_config,
    run_full_preflight,
    validate_completed_decision_bundle,
    validate_decision_report_payload,
    validate_static_config,
)


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG_PATH = WORKSPACE / "configs/experiments/r05_grpo_go_no_go_v1.yaml"


def _config() -> dict[str, object]:
    return copy.deepcopy(dict(load_config(CONFIG_PATH)))


def _sha(character: str) -> str:
    return character * 64


def _gate(
    gate_id: str,
    *,
    estimate: float | None,
    lower: float | None,
    upper: float | None,
    decision: str = "passed",
) -> dict[str, object]:
    operators: dict[str, tuple[str, float | None]] = {
        "s1_phone_f1": ("greater_than_or_equal", 0.8),
        "s1_utterance_icc": ("greater_than_or_equal", 0.7),
        "s2_severe_error_recall": ("greater_than_or_equal", 0.8),
        "multi_reference_blind_superiority": (
            "preregistered_superiority",
            None,
        ),
        "actual_pilot_learning_effect_signal": ("preregistered_signal", None),
    }
    operator, threshold = operators[gate_id]
    missing = decision == "missing"
    return {
        "gate_id": gate_id,
        "evidence_sha256": None if missing else _sha("a"),
        "metric_or_estimand_id": None if missing else f"metric_{gate_id}",
        "population_id": None if missing else "population_synthetic_fixture",
        "alignment_id": None if missing else "alignment_synthetic_fixture",
        "confidence_interval_method": None if missing else "ci_synthetic_fixture",
        "decision_rule_id": None if missing else "rule_synthetic_fixture",
        "operator": operator,
        "threshold": threshold,
        "point_estimate": None if missing else estimate,
        "confidence_interval_lower": None if missing else lower,
        "confidence_interval_upper": None if missing else upper,
        "superiority_margin": (
            0.0
            if gate_id == "multi_reference_blind_superiority" and not missing
            else None
        ),
        "effect_size": (
            0.2
            if gate_id == "actual_pilot_learning_effect_signal" and not missing
            else None
        ),
        "decision": decision,
    }


def _report() -> dict[str, object]:
    gates = [
        _gate("s1_phone_f1", estimate=0.82, lower=0.80, upper=0.84),
        _gate("s1_utterance_icc", estimate=0.73, lower=0.70, upper=0.76),
        _gate("s2_severe_error_recall", estimate=0.83, lower=0.80, upper=0.86),
        _gate(
            "multi_reference_blind_superiority",
            estimate=0.15,
            lower=0.02,
            upper=0.28,
        ),
        _gate(
            "actual_pilot_learning_effect_signal",
            estimate=0.12,
            lower=0.01,
            upper=0.23,
        ),
    ]
    return {
        "schema_version": "voice_coach.r05_grpo_decision_report.v1",
        "report_id": "r05_" + "1" * 32,
        "status": "completed",
        "privacy_zone": "A1_aggregate_only",
        "contains_row_level_data": False,
        "contains_direct_identifiers": False,
        "contains_free_text": False,
        "training_or_download_executed": False,
        "config_sha256": _sha("1"),
        "preregistration_sha256": _sha("2"),
        "human_authorization_sha256": _sha("3"),
        "evidence_bindings": {
            key: _sha(str(index + 4))
            for index, key in enumerate(sorted(EVIDENCE_BINDING_KEYS))
        },
        "gate_results": gates,
        "assurance_results": {key: True for key in ASSURANCE_KEYS},
        "overall_decision": "GO",
        "grpo_entry_authorized": True,
        "publication_allowed": False,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _with_binding(value: dict[str, object]) -> dict[str, object]:
    result = dict(value)
    result["binding_sha256"] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
    return result


def _copy_static_workspace(destination: Path) -> tuple[Path, dict[str, object], str]:
    for relative in SOURCE_PATHS:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(WORKSPACE / relative, target)
    config_target = destination / "configs/experiments/r05_grpo_go_no_go_v1.yaml"
    config_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CONFIG_PATH, config_target)
    config = copy.deepcopy(dict(load_config(config_target)))
    static = validate_static_config(
        workspace_root=destination,
        config_path=config_target,
        config=config,
    )
    return config_target, config, static.config_sha256


def _write_completed_bundle(
    root: Path,
    config: dict[str, object],
    config_sha256: str,
) -> None:
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    report_path = root / str(outputs["decision_report_path"])
    registry_path = root / str(outputs["registry_path"])
    commit_path = root / str(outputs["commit_path"])
    report = _report()
    report["config_sha256"] = config_sha256
    _write_json(report_path, report)
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    registry = _with_binding(
        {
            "schema_version": "voice_coach.r05_grpo_decision_registry.v1",
            "run_id": "r05-grpo-go-no-go-v1",
            "status": "completed",
            "config_sha256": config_sha256,
            "artifacts": [
                {
                    "name": "decision_report",
                    "path": str(outputs["decision_report_path"]),
                    "sha256": report_sha,
                    "schema_version": "voice_coach.r05_grpo_decision_report.v1",
                }
            ],
        }
    )
    _write_json(registry_path, registry)
    registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    commit = _with_binding(
        {
            "schema_version": "voice_coach.r05_grpo_decision_commit.v1",
            "status": "committed",
            "config_sha256": config_sha256,
            "preregistration_sha256": report["preregistration_sha256"],
            "human_authorization_sha256": report["human_authorization_sha256"],
            "overall_decision": "GO",
            "decision_report": {
                "path": str(outputs["decision_report_path"]),
                "sha256": report_sha,
            },
            "registry": {
                "path": str(outputs["registry_path"]),
                "sha256": registry_sha,
            },
        }
    )
    _write_json(commit_path, commit)


def test_canonical_static_config_has_zero_runtime() -> None:
    config = _config()
    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CONFIG_PATH,
        config=config,
        audit=audit,
    )
    result = config_only_result(static, audit)

    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == len(SOURCE_PATHS)
    assert result["source_pin_read_count"] == len(SOURCE_PATHS)
    for field in RUNTIME_COUNTER_FIELDS:
        assert result[field] == 0
    assert result["actual_training_download_model_gpu_reward_execution"] is False
    assert result["actual_metric_or_effect_estimation"] is False
    assert result["decision_report_created"] is False


@pytest.mark.parametrize(
    ("gate_id", "field", "value"),
    [
        ("s1_phone_f1", "threshold", 0.79),
        ("s1_utterance_icc", "threshold", 0.69),
        ("s2_severe_error_recall", "threshold", 0.79),
        (
            "multi_reference_blind_superiority",
            "superiority_margin",
            0.0,
        ),
        (
            "actual_pilot_learning_effect_signal",
            "effect_estimand",
            "posthoc_effect",
        ),
    ],
)
def test_static_config_rejects_gate_or_unresolved_policy_drift(
    gate_id: str,
    field: str,
    value: object,
) -> None:
    config = _config()
    gate_contract = config["gate_contract"]
    assert isinstance(gate_contract, dict)
    gate = gate_contract[gate_id]
    assert isinstance(gate, dict)
    gate[field] = value

    with pytest.raises(R05ContractError):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_full_preflight_stops_before_resolving_missing_s108() -> None:
    config = _config()
    with pytest.raises(R05ContractError) as raised:
        run_full_preflight(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )

    error = raised.value
    assert error.stage == "s108_final"
    assert str(error) == "S108 completion commit SHA is TBD_S108_COMPLETION"
    assert error.audit is not None
    assert error.audit.source_pin_read_count == len(SOURCE_PATHS)
    for field in RUNTIME_COUNTER_FIELDS:
        assert getattr(error.audit, field) == 0


def test_synthetic_all_pass_report_is_go() -> None:
    assert validate_decision_report_payload(_report()) == "GO"


def test_synthetic_missing_gate_is_blocked() -> None:
    report = _report()
    gates = report["gate_results"]
    assert isinstance(gates, list)
    gates[-1] = _gate(
        "actual_pilot_learning_effect_signal",
        estimate=None,
        lower=None,
        upper=None,
        decision="missing",
    )
    report["overall_decision"] = "BLOCKED"
    report["grpo_entry_authorized"] = False

    assert validate_decision_report_payload(report) == "BLOCKED"


def test_synthetic_failed_gate_is_no_go() -> None:
    report = _report()
    gates = report["gate_results"]
    assert isinstance(gates, list)
    gates[0] = _gate(
        "s1_phone_f1",
        estimate=0.70,
        lower=0.65,
        upper=0.75,
        decision="failed",
    )
    report["overall_decision"] = "NO_GO"
    report["grpo_entry_authorized"] = False

    assert validate_decision_report_payload(report) == "NO_GO"


def test_go_never_infers_missing_gate_as_pass() -> None:
    report = _report()
    gates = report["gate_results"]
    assert isinstance(gates, list)
    gates[-1] = _gate(
        "actual_pilot_learning_effect_signal",
        estimate=None,
        lower=None,
        upper=None,
        decision="missing",
    )

    with pytest.raises(R05ContractError, match="all five gates"):
        validate_decision_report_payload(report)


@pytest.mark.parametrize("assurance", sorted(ASSURANCE_KEYS))
def test_go_requires_every_assurance(assurance: str) -> None:
    report = _report()
    assurances = report["assurance_results"]
    assert isinstance(assurances, dict)
    assurances[assurance] = False

    with pytest.raises(R05ContractError, match="every assurance"):
        validate_decision_report_payload(report)


@pytest.mark.parametrize(
    "forbidden",
    [
        "participant_id",
        "listener_id",
        "speaker_id",
        "item_id_sha256",
        "free_text",
        "audio_path",
    ],
)
def test_a1_report_rejects_nested_rows_identifiers_and_free_text(
    forbidden: str,
) -> None:
    report = _report()
    gates = report["gate_results"]
    assert isinstance(gates, list)
    gate = gates[0]
    assert isinstance(gate, dict)
    gate["unexpected"] = {forbidden: "synthetic"}

    with pytest.raises(R05ContractError, match="forbidden A1"):
        validate_decision_report_payload(report)


def test_report_rejects_nonfinite_and_reversed_ci() -> None:
    nonfinite = _report()
    gates = nonfinite["gate_results"]
    assert isinstance(gates, list)
    gate = gates[0]
    assert isinstance(gate, dict)
    gate["point_estimate"] = float("nan")
    with pytest.raises(R05ContractError, match="non-finite"):
        validate_decision_report_payload(nonfinite)

    reversed_ci = _report()
    gates = reversed_ci["gate_results"]
    assert isinstance(gates, list)
    gate = gates[0]
    assert isinstance(gate, dict)
    gate["confidence_interval_lower"] = 0.9
    gate["confidence_interval_upper"] = 0.8
    with pytest.raises(R05ContractError, match="reversed"):
        validate_decision_report_payload(reversed_ci)


def test_fixed_gate_cannot_pass_below_threshold() -> None:
    report = _report()
    gates = report["gate_results"]
    assert isinstance(gates, list)
    gates[0] = _gate(
        "s1_phone_f1",
        estimate=0.79,
        lower=0.78,
        upper=0.80,
        decision="passed",
    )

    with pytest.raises(R05ContractError, match="below its fixed threshold"):
        validate_decision_report_payload(report)


def test_output_path_traversal_is_rejected(tmp_path: Path) -> None:
    config = _config()
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    outputs["decision_report_path"] = "../outside.json"

    with pytest.raises(R05ContractError, match="workspace-relative"):
        ensure_output_targets_absent(workspace_root=tmp_path, config=config)


def test_static_source_hash_drift_is_rejected() -> None:
    config = _config()
    canonical = config["canonical_sources"]
    assert isinstance(canonical, dict)
    pins = canonical["pins"]
    assert isinstance(pins, list)
    first = pins[0]
    assert isinstance(first, dict)
    first["sha256"] = _sha("f")

    with pytest.raises(R05ContractError, match="source pin"):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_no_overwrite_rejects_existing_output(tmp_path: Path) -> None:
    config = _config()
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    existing = tmp_path / str(outputs["decision_report_path"])
    existing.parent.mkdir(parents=True)
    existing.write_text("synthetic", encoding="utf-8")

    with pytest.raises(R05ContractError, match="already exists"):
        ensure_output_targets_absent(workspace_root=tmp_path, config=config)


def test_no_overwrite_rejects_symlink_chain(tmp_path: Path) -> None:
    config = _config()
    (tmp_path / "artifacts").symlink_to(tmp_path / "outside", target_is_directory=True)

    with pytest.raises(R05ContractError, match="symlink"):
        ensure_output_targets_absent(workspace_root=tmp_path, config=config)


def test_synthetic_completed_bundle_validates_atomically(tmp_path: Path) -> None:
    config_path, config, config_sha = _copy_static_workspace(tmp_path)
    _write_completed_bundle(tmp_path, config, config_sha)

    assert (
        validate_completed_decision_bundle(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
        == "GO"
    )


def test_completed_bundle_rejects_report_symlink(tmp_path: Path) -> None:
    config_path, config, config_sha = _copy_static_workspace(tmp_path)
    _write_completed_bundle(tmp_path, config, config_sha)
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    report_path = tmp_path / str(outputs["decision_report_path"])
    outside = tmp_path / "synthetic_outside.json"
    shutil.copyfile(report_path, outside)
    report_path.unlink()
    report_path.symlink_to(outside)

    with pytest.raises(R05ContractError, match="symlink"):
        validate_completed_decision_bundle(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_completed_bundle_detects_toctou(tmp_path: Path) -> None:
    config_path, config, config_sha = _copy_static_workspace(tmp_path)
    _write_completed_bundle(tmp_path, config, config_sha)
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    report_path = tmp_path / str(outputs["decision_report_path"])

    def mutate_after_initial_validation() -> None:
        report_path.write_bytes(report_path.read_bytes() + b" ")

    with pytest.raises(R05ContractError, match="changed after validation"):
        validate_completed_decision_bundle(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
            before_final_verify_hook=mutate_after_initial_validation,
        )


def test_completed_bundle_rejects_hash_tamper(tmp_path: Path) -> None:
    config_path, config, config_sha = _copy_static_workspace(tmp_path)
    _write_completed_bundle(tmp_path, config, config_sha)
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    report_path = tmp_path / str(outputs["decision_report_path"])
    report_path.write_bytes(report_path.read_bytes() + b" ")

    with pytest.raises(R05ContractError, match="decision report SHA"):
        validate_completed_decision_bundle(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
