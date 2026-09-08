from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "plan_learning_effect_power.py"
SOURCE_CONFIG = (
    REPOSITORY_ROOT / "configs" / "experiments" / "learning_effect_power_v1.yaml"
)
SPEC = importlib.util.spec_from_file_location("plan_learning_effect_power", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_config(root: Path) -> Path:
    destination = root / "configs" / SOURCE_CONFIG.name
    destination.parent.mkdir(parents=True)
    shutil.copyfile(SOURCE_CONFIG, destination)
    return destination


def _read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _pilot_payload() -> dict[str, Any]:
    return {
        "schema_version": MODULE.PILOT_SCHEMA,
        "status": "completed",
        "evidence_type": "empirical_pilot_nuisance_estimates",
        "outcome": "blind_expert_comprehensibility",
        "participant_count": 60,
        "participant_level_outcome_sd": 0.85,
        "pre_followup_correlation": 0.55,
        "delivery_cluster_icc": 0.04,
        "attrition_fraction": 0.18,
        "mean_delivery_cluster_size": 8.0,
        "delivery_cluster_size_cv": 0.20,
        "rater_icc_2_1": 0.76,
        "source_report_sha256": "a" * 64,
        "irb_or_equivalent_approval_confirmed": True,
        "consent_confirmed": True,
        "aggregate_only": True,
        "identifiers_present": False,
        "observed_treatment_effect_used_for_power": False,
    }


def _mark_empirical_pilot_available(config_path: Path) -> None:
    config = _read_yaml(config_path)
    status = config["current_external_status"]
    status["irb_or_equivalent_approval_obtained"] = True
    status["participants_recruited"] = True
    status["learner_recordings_collected"] = True
    status["empirical_pilot_nuisance_estimates_available"] = True
    _write_yaml(config_path, config)


def _plan(
    root: Path,
    config: Path,
    *,
    pilot: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    output = root / "artifacts" / "power-plan.json"
    result = MODULE.plan_learning_effect_power(
        config_path=config,
        output_path=output,
        workspace_root=root,
        pilot_estimates_path=pilot,
    )
    return result, output


def test_repeated_change_reference_case() -> None:
    unrounded, rounded, standardized = MODULE.repeated_change_sample_size(
        outcome_sd=1.0,
        raw_mean_difference=0.5,
        correlation=0.5,
        alpha=0.05,
        power=0.80,
    )

    assert math.isclose(unrounded, 62.791037874792686)
    assert rounded == 63
    assert standardized == 0.5


def test_cluster_design_effect_reference_cases() -> None:
    equal = MODULE.cluster_design_effect(
        mean_cluster_size=10.0,
        cluster_icc=0.05,
        cluster_size_cv=0.0,
    )
    unequal = MODULE.cluster_design_effect(
        mean_cluster_size=10.0,
        cluster_icc=0.05,
        cluster_size_cv=0.25,
    )

    assert math.isclose(equal, 1.45)
    assert math.isclose(unequal, 1.48125)
    assert unequal > equal


def test_current_config_emits_assumption_grid_without_final_sample_size(
    tmp_path: Path,
) -> None:
    config = _copy_config(tmp_path)

    result, output = _plan(tmp_path, config)

    assert result["status"] == "assumed_sensitivity_only"
    assert len(result["assumed_sensitivity_scenarios"]) == 243
    assert result["pilot_informed_scenarios"] == []
    assert result["final_sample_size"] is None
    assert result["final_sample_size_locked"] is False
    assert result["h01_completion_claim_allowed"] is False
    assert result["external_status"] == {
        "irb_or_equivalent_approval_obtained": False,
        "participants_recruited": False,
        "learner_recordings_collected": False,
        "empirical_pilot_input_supplied": False,
        "confirmatory_effect_observed": False,
    }
    assert result["provenance"]["config_sha256"] == _sha(config)
    serialized = output.read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert "participant_id" not in serialized
    assert 'final_sample_size": null' in serialized
    assert not list(output.parent.glob(".*.tmp"))


def _find_scenario(
    scenarios: list[dict[str, Any]],
    **inputs: float,
) -> dict[str, Any]:
    for scenario in scenarios:
        if all(
            math.isclose(float(scenario["inputs"][name]), value)
            for name, value in inputs.items()
        ):
            return scenario
    raise AssertionError(f"scenario not found: {inputs}")


def test_sensitivity_grid_has_expected_monotonic_directions(
    tmp_path: Path,
) -> None:
    config = _copy_config(tmp_path)
    result, _ = _plan(tmp_path, config)
    scenarios = result["assumed_sensitivity_scenarios"]
    common = {
        "participant_level_outcome_sd": 0.9,
        "pre_followup_correlation": 0.5,
        "delivery_cluster_icc": 0.05,
        "attrition_fraction": 0.2,
    }
    small_effect = _find_scenario(
        scenarios,
        raw_mean_difference_points=0.15,
        **common,
    )
    large_effect = _find_scenario(
        scenarios,
        raw_mean_difference_points=0.45,
        **common,
    )
    assert (
        small_effect["individual_randomized_repeated_change"][
            "enroll_per_arm_after_attrition"
        ]
        > large_effect["individual_randomized_repeated_change"][
            "enroll_per_arm_after_attrition"
        ]
    )

    low_attrition = _find_scenario(
        scenarios,
        raw_mean_difference_points=0.3,
        participant_level_outcome_sd=0.9,
        pre_followup_correlation=0.5,
        delivery_cluster_icc=0.05,
        attrition_fraction=0.1,
    )
    high_attrition = _find_scenario(
        scenarios,
        raw_mean_difference_points=0.3,
        participant_level_outcome_sd=0.9,
        pre_followup_correlation=0.5,
        delivery_cluster_icc=0.05,
        attrition_fraction=0.3,
    )
    assert (
        low_attrition["cluster_randomized_mixed_model_approximation"][
            "enroll_per_arm_after_attrition"
        ]
        < high_attrition["cluster_randomized_mixed_model_approximation"][
            "enroll_per_arm_after_attrition"
        ]
    )


def test_empirical_pilot_updates_nuisance_only_and_does_not_lock_sample_size(
    tmp_path: Path,
) -> None:
    config = _copy_config(tmp_path)
    _mark_empirical_pilot_available(config)
    pilot = tmp_path / "pilot" / "aggregate-estimates.json"
    _write_json(pilot, _pilot_payload())

    result, _ = _plan(tmp_path, config, pilot=pilot)

    assert result["status"] == "pilot_informed_sensitivity"
    assert len(result["pilot_informed_scenarios"]) == 3
    assert all(
        scenario["evidence_basis"]
        == "empirical_pilot_nuisance_plus_assumed_target_effect"
        for scenario in result["pilot_informed_scenarios"]
    )
    assert result["pilot_nuisance_summary"] == {
        "participant_count": 60,
        "rater_icc_2_1": 0.76,
        "rater_icc_used_in_sample_size_formula": False,
        "nuisance_estimates_are_empirical": True,
        "target_effects_remain_assumed": True,
    }
    assert result["provenance"]["pilot_input_sha256"] == _sha(pilot)
    assert result["provenance"]["observed_treatment_effect_used_for_power"] is False
    assert result["final_sample_size"] is None
    assert result["final_sample_size_locked"] is False


def test_pilot_input_requires_updated_external_status(tmp_path: Path) -> None:
    config = _copy_config(tmp_path)
    pilot = tmp_path / "pilot.json"
    _write_json(pilot, _pilot_payload())

    with pytest.raises(
        MODULE.PowerPlanError,
        match="pilot_input_without_updated_external_status",
    ):
        _plan(tmp_path, config, pilot=pilot)


def test_pilot_record_cannot_include_observed_effect_estimate(
    tmp_path: Path,
) -> None:
    config = _copy_config(tmp_path)
    _mark_empirical_pilot_available(config)
    pilot = tmp_path / "pilot.json"
    payload = _pilot_payload()
    payload["observed_treatment_effect"] = 0.4
    _write_json(pilot, payload)

    with pytest.raises(MODULE.PowerPlanError, match="pilot_key_set"):
        _plan(tmp_path, config, pilot=pilot)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("root", "status", "completed"),
        ("external", "participants_recruited", True),
        ("feasibility", "powered_efficacy_claim_allowed", True),
        ("stopping", "effect_based_early_stop_allowed", True),
        ("output", "identifiers_allowed", True),
    ],
)
def test_config_cannot_overstate_completion_or_relax_preregistered_guards(
    tmp_path: Path,
    section: str,
    key: str,
    value: object,
) -> None:
    config_path = _copy_config(tmp_path)
    config = _read_yaml(config_path)
    target = {
        "root": config,
        "external": config["current_external_status"],
        "feasibility": config["feasibility_pilot"],
        "stopping": config["stopping"],
        "output": config["output"],
    }[section]
    target[key] = value
    _write_yaml(config_path, config)

    with pytest.raises(MODULE.PowerPlanError):
        _plan(tmp_path, config_path)


@pytest.mark.parametrize("forbidden_part", ["raw", "restricted", ".env.local"])
def test_forbidden_inputs_are_rejected_without_reading(
    tmp_path: Path,
    forbidden_part: str,
) -> None:
    config = _copy_config(tmp_path)
    forbidden = tmp_path / forbidden_part / "pilot.json"

    with pytest.raises(MODULE.PowerPlanError, match="forbidden_path"):
        _plan(tmp_path, config, pilot=forbidden)


def test_cli_fails_closed_without_placeholder_output(tmp_path: Path) -> None:
    config = _copy_config(tmp_path)
    payload = _read_yaml(config)
    payload["analysis"]["primary_population"] = "per_protocol"
    _write_yaml(config, payload)
    output = tmp_path / "power.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace-root",
            str(tmp_path),
            "--config",
            str(config),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "analysis_population" in completed.stderr
    assert not output.exists()


def test_cli_success_remains_planning_only(tmp_path: Path) -> None:
    config = _copy_config(tmp_path)
    output = tmp_path / "power.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace-root",
            str(tmp_path),
            "--config",
            str(config),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    summary = json.loads(completed.stdout)
    assert summary == {
        "assumed_scenarios": 243,
        "final_sample_size_locked": False,
        "h01_completion_claim_allowed": False,
        "pilot_informed_scenarios": 0,
        "status": "assumed_sensitivity_only",
    }
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["final_sample_size"] is None
    assert report["h01_completion_claim_allowed"] is False
