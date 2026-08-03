from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from voice_coach.evaluation.e01_metamorphic_contract import (
    PERTURBATION_IDS,
    RUNTIME_FIELDS,
    SOURCE_PATHS,
    AccessAudit,
    E01ContractError,
    canonical_json_bytes,
    config_only_result,
    ensure_output_targets_absent,
    load_config,
    run_full_preflight,
    validate_completed_bundle,
    validate_input_manifest_payload,
    validate_report_payload,
    validate_static_config,
)


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG_PATH = WORKSPACE / "configs/experiments/e01_metamorphic_robustness_v1.yaml"


def _config() -> dict[str, object]:
    return copy.deepcopy(dict(load_config(CONFIG_PATH)))


def _sha(character: str) -> str:
    return character * 64


def _manifest() -> dict[str, object]:
    cases: list[dict[str, object]] = []
    for index, perturbation_id in enumerate(PERTURBATION_IDS, start=1):
        target = _sha("e") if perturbation_id in {"duration", "phone"} else None
        cases.append(
            {
                "case_id": f"e01c_{index:032x}",
                "source_item_id_sha256": _sha(str(index)),
                "source_audio_object_id_sha256": _sha("a"),
                "source_audio_sha256": _sha("b"),
                "perturbation_id": perturbation_id,
                "parameter_set_sha256": _sha("c"),
                "expected_relation_id_sha256": _sha("d"),
                "target_region_sha256": target,
            }
        )
    return {
        "schema_version": "voice_coach.e01_metamorphic_input_manifest.v1",
        "manifest_id": "e01m_" + "1" * 32,
        "status": "frozen",
        "privacy_zone": "D1_pseudonymized_restricted_non_locked",
        "split_role": "phase3_non_holdout_e01_only",
        "contains_locked_test": False,
        "contains_s203_expert_holdout": False,
        "contains_raw_audio": False,
        "contains_audio_or_model_paths": False,
        "s108_commit_sha256": _sha("1"),
        "selected_model_identity_sha256": _sha("2"),
        "preregistration_sha256": _sha("3"),
        "deterministic_seed": 20260729,
        "case_count": len(cases),
        "cases": cases,
    }


def _result(
    perturbation_id: str,
    *,
    passed: int = 19,
    failed: int = 1,
    invalid: int = 0,
    decision: str = "passed",
) -> dict[str, object]:
    evaluable = passed + failed
    case_count = evaluable + invalid
    relation = {
        "silence": "invariant",
        "gain": "invariant",
        "duration": "monotonic_nonincreasing",
        "f0": "monotonic_nonincreasing",
        "phone": "targeted_change",
    }[perturbation_id]
    return {
        "perturbation_id": perturbation_id,
        "expectation_type": relation,
        "case_count": case_count,
        "evaluable_count": evaluable,
        "passed_count": passed,
        "failed_count": failed,
        "invalid_count": invalid,
        "pass_rate": None if evaluable == 0 else passed / evaluable,
        "confidence_interval_lower": None if evaluable == 0 else 0.90,
        "confidence_interval_upper": None if evaluable == 0 else 1.0,
        "decision": decision,
    }


def _report() -> dict[str, object]:
    results = [_result(perturbation_id) for perturbation_id in PERTURBATION_IDS]
    return {
        "schema_version": "voice_coach.e01_metamorphic_report.v1",
        "report_id": "e01r_" + "1" * 32,
        "status": "completed",
        "privacy_zone": "A1_aggregate_only",
        "contains_row_level_data": False,
        "contains_direct_or_pseudonymous_ids": False,
        "contains_free_text": False,
        "locked_test_access_count": 0,
        "s203_expert_holdout_access_count": 0,
        "config_sha256": _sha("1"),
        "s108_commit_sha256": _sha("2"),
        "selected_model_identity_sha256": _sha("3"),
        "selected_checkpoint_sha256": _sha("4"),
        "preregistration_sha256": _sha("5"),
        "input_manifest_sha256": _sha("6"),
        "metric_computation_external_to_static_package": True,
        "perturbation_results": results,
        "overall": {
            "case_count": 100,
            "evaluable_count": 100,
            "passed_count": 95,
            "failed_count": 5,
            "invalid_count": 0,
            "metric": "metamorphic_expectation_pass_rate",
            "operator": "greater_than_or_equal",
            "threshold": 0.95,
            "pass_rate": 0.95,
            "gate_statistic_value": 0.95,
            "decision": "passed",
        },
        "publication_allowed": False,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
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
    config_path = destination / "configs/experiments/e01_metamorphic_robustness_v1.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CONFIG_PATH, config_path)
    config = copy.deepcopy(dict(load_config(config_path)))
    static = validate_static_config(
        workspace_root=destination,
        config_path=config_path,
        config=config,
    )
    return config_path, config, static.config_sha256


def _write_completed_bundle(
    root: Path,
    config: dict[str, object],
    config_sha256: str,
) -> None:
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    report_path = root / str(outputs["aggregate_report_path"])
    registry_path = root / str(outputs["registry_path"])
    commit_path = root / str(outputs["commit_path"])
    report = _report()
    report["config_sha256"] = config_sha256
    _write_json(report_path, report)
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    registry = _with_binding(
        {
            "schema_version": "voice_coach.e01_metamorphic_registry.v1",
            "run_id": "e01-metamorphic-robustness-v1",
            "status": "completed",
            "config_sha256": config_sha256,
            "artifacts": [
                {
                    "name": "aggregate_report",
                    "path": str(outputs["aggregate_report_path"]),
                    "sha256": report_sha,
                    "schema_version": "voice_coach.e01_metamorphic_report.v1",
                }
            ],
        }
    )
    _write_json(registry_path, registry)
    registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    commit = _with_binding(
        {
            "schema_version": "voice_coach.e01_metamorphic_commit.v1",
            "status": "committed",
            "config_sha256": config_sha256,
            "s108_commit_sha256": report["s108_commit_sha256"],
            "selected_model_identity_sha256": report["selected_model_identity_sha256"],
            "selected_checkpoint_sha256": report["selected_checkpoint_sha256"],
            "preregistration_sha256": report["preregistration_sha256"],
            "input_manifest_sha256": report["input_manifest_sha256"],
            "decision": "passed",
            "report": {
                "path": str(outputs["aggregate_report_path"]),
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
    for field in RUNTIME_FIELDS:
        assert result[field] == 0
    assert result["actual_audio_perturbation_or_model_execution"] is False
    assert result["actual_metric_or_decision"] is False
    assert result["locked_test_accessed"] is False
    assert result["report_created"] is False


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("pass_gate", "threshold", 0.94),
        ("pass_gate", "operator", "greater_than"),
        ("perturbation_contract", "background_noise_in_scope", True),
        (
            "evaluation_scope",
            "source_population_pointer",
            "posthoc_population.json",
        ),
    ],
)
def test_static_config_rejects_gate_scope_or_tbd_drift(
    section: str,
    field: str,
    value: object,
) -> None:
    config = _config()
    mapping = config[section]
    assert isinstance(mapping, dict)
    mapping[field] = value

    with pytest.raises(E01ContractError):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_static_config_rejects_invented_magnitude() -> None:
    config = _config()
    contract = config["perturbation_contract"]
    assert isinstance(contract, dict)
    silence = contract["silence"]
    assert isinstance(silence, dict)
    magnitude = silence["exact_magnitude"]
    assert isinstance(magnitude, dict)
    magnitude["leading_levels"] = [0.1]

    with pytest.raises(E01ContractError, match="leading_levels"):
        validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_full_preflight_stops_before_resolving_s108() -> None:
    with pytest.raises(E01ContractError) as raised:
        run_full_preflight(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=_config(),
        )

    error = raised.value
    assert error.stage == "s108_final"
    assert str(error) == "S108 completion commit SHA is TBD_S108_COMPLETION"
    assert error.audit is not None
    assert error.audit.source_pin_read_count == len(SOURCE_PATHS)
    for field in RUNTIME_FIELDS:
        assert getattr(error.audit, field) == 0


def test_synthetic_input_manifest_is_valid() -> None:
    assert validate_input_manifest_payload(_manifest()) == 5


@pytest.mark.parametrize(
    "field",
    [
        "contains_locked_test",
        "contains_s203_expert_holdout",
        "contains_raw_audio",
        "contains_audio_or_model_paths",
    ],
)
def test_input_manifest_rejects_protected_or_path_markers(field: str) -> None:
    manifest = _manifest()
    manifest[field] = True
    with pytest.raises(E01ContractError, match=field):
        validate_input_manifest_payload(manifest)


def test_input_manifest_requires_all_five_families() -> None:
    manifest = _manifest()
    cases = manifest["cases"]
    assert isinstance(cases, list)
    cases.pop()
    manifest["case_count"] = len(cases)

    with pytest.raises(E01ContractError, match="coverage"):
        validate_input_manifest_payload(manifest)


def test_input_manifest_rejects_duplicate_and_target_scope() -> None:
    duplicate = _manifest()
    cases = duplicate["cases"]
    assert isinstance(cases, list)
    cases.append(copy.deepcopy(cases[0]))
    copied = cases[-1]
    assert isinstance(copied, dict)
    copied["case_id"] = "e01c_" + "f" * 32
    duplicate["case_count"] = len(cases)
    with pytest.raises(E01ContractError, match="semantic"):
        validate_input_manifest_payload(duplicate)

    wrong_target = _manifest()
    cases = wrong_target["cases"]
    assert isinstance(cases, list)
    silence = cases[0]
    assert isinstance(silence, dict)
    silence["target_region_sha256"] = _sha("e")
    with pytest.raises(E01ContractError, match="cannot expose"):
        validate_input_manifest_payload(wrong_target)


def test_synthetic_report_passes_at_fixed_inclusive_gate() -> None:
    assert validate_report_payload(_report()) == "passed"


def test_synthetic_report_failed_and_blocked_contracts() -> None:
    failed = _report()
    results = failed["perturbation_results"]
    assert isinstance(results, list)
    results[0] = _result("silence", passed=18, failed=2, decision="failed")
    overall = failed["overall"]
    assert isinstance(overall, dict)
    overall["passed_count"] = 94
    overall["failed_count"] = 6
    overall["pass_rate"] = 0.94
    overall["gate_statistic_value"] = 0.94
    overall["decision"] = "failed"
    assert validate_report_payload(failed) == "failed"

    blocked = _report()
    blocked_results = [
        _result(
            perturbation_id,
            passed=0,
            failed=0,
            invalid=0,
            decision="blocked",
        )
        for perturbation_id in PERTURBATION_IDS
    ]
    blocked["perturbation_results"] = blocked_results
    blocked["overall"] = {
        "case_count": 0,
        "evaluable_count": 0,
        "passed_count": 0,
        "failed_count": 0,
        "invalid_count": 0,
        "metric": "metamorphic_expectation_pass_rate",
        "operator": "greater_than_or_equal",
        "threshold": 0.95,
        "pass_rate": None,
        "gate_statistic_value": None,
        "decision": "blocked",
    }
    assert validate_report_payload(blocked) == "blocked"


def test_report_rejects_pass_below_fixed_gate() -> None:
    report = _report()
    overall = report["overall"]
    assert isinstance(overall, dict)
    overall["gate_statistic_value"] = 0.94

    with pytest.raises(E01ContractError, match="fixed threshold"):
        validate_report_payload(report)


@pytest.mark.parametrize(
    "forbidden",
    ["item_id_sha256", "case_id", "speaker_id", "audio_path", "free_text"],
)
def test_report_rejects_nested_ids_paths_and_free_text(forbidden: str) -> None:
    report = _report()
    overall = report["overall"]
    assert isinstance(overall, dict)
    overall["unexpected"] = {forbidden: "synthetic"}

    with pytest.raises(E01ContractError, match="forbidden A1"):
        validate_report_payload(report)


def test_report_rejects_count_rate_ci_and_nonfinite_drift() -> None:
    bad_count = _report()
    overall = bad_count["overall"]
    assert isinstance(overall, dict)
    overall["passed_count"] = 94
    with pytest.raises(E01ContractError, match="evaluable count"):
        validate_report_payload(bad_count)

    bad_ci = _report()
    results = bad_ci["perturbation_results"]
    assert isinstance(results, list)
    first = results[0]
    assert isinstance(first, dict)
    first["confidence_interval_lower"] = 0.99
    first["confidence_interval_upper"] = 0.90
    with pytest.raises(E01ContractError, match="reversed"):
        validate_report_payload(bad_ci)

    nonfinite = _report()
    overall = nonfinite["overall"]
    assert isinstance(overall, dict)
    overall["pass_rate"] = float("nan")
    with pytest.raises(E01ContractError, match="non-finite"):
        validate_report_payload(nonfinite)


def test_report_relation_must_match_preregistration() -> None:
    expected = {perturbation_id: "invariant" for perturbation_id in PERTURBATION_IDS}
    with pytest.raises(E01ContractError, match="preregistered relation"):
        validate_report_payload(_report(), expected_relations=expected)


def test_no_overwrite_and_path_traversal(tmp_path: Path) -> None:
    config = _config()
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    existing = tmp_path / str(outputs["aggregate_report_path"])
    existing.parent.mkdir(parents=True)
    existing.write_text("synthetic", encoding="utf-8")
    with pytest.raises(E01ContractError, match="already exists"):
        ensure_output_targets_absent(workspace_root=tmp_path, config=config)

    traversal = _config()
    outputs = traversal["outputs"]
    assert isinstance(outputs, dict)
    outputs["aggregate_report_path"] = "../outside.json"
    with pytest.raises(E01ContractError, match="workspace-relative"):
        ensure_output_targets_absent(workspace_root=tmp_path, config=traversal)


def test_no_overwrite_rejects_symlink_chain(tmp_path: Path) -> None:
    config = _config()
    (tmp_path / "artifacts").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(E01ContractError, match="symlink"):
        ensure_output_targets_absent(workspace_root=tmp_path, config=config)


def test_synthetic_completed_bundle_validates(tmp_path: Path) -> None:
    config_path, config, config_sha = _copy_static_workspace(tmp_path)
    _write_completed_bundle(tmp_path, config, config_sha)
    assert (
        validate_completed_bundle(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
        == "passed"
    )


def test_completed_bundle_rejects_symlink_hash_and_toctou(tmp_path: Path) -> None:
    config_path, config, config_sha = _copy_static_workspace(tmp_path)
    _write_completed_bundle(tmp_path, config, config_sha)
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    report_path = tmp_path / str(outputs["aggregate_report_path"])
    outside = tmp_path / "outside.json"
    shutil.copyfile(report_path, outside)
    report_path.unlink()
    report_path.symlink_to(outside)
    with pytest.raises(E01ContractError, match="symlink"):
        validate_completed_bundle(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )

    report_path.unlink()
    shutil.copyfile(outside, report_path)
    report_path.write_bytes(report_path.read_bytes() + b" ")
    with pytest.raises(E01ContractError, match="report SHA"):
        validate_completed_bundle(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )

    shutil.copyfile(outside, report_path)

    def mutate() -> None:
        report_path.write_bytes(report_path.read_bytes() + b" ")

    with pytest.raises(E01ContractError, match="changed after validation"):
        validate_completed_bundle(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
            before_final_verify_hook=mutate,
        )
