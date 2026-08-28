from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_c02_coaching_selection.py"
CONFIG = WORKSPACE / "configs/experiments/c02_deterministic_coaching_selection_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE / "data/processed/coaching/c02/c02_coaching_selection_manifest_v1.json",
    WORKSPACE / "artifacts/coaching/c02/c02_coaching_selection_report_v1.json",
    WORKSPACE / "artifacts/coaching/c02/commit.json",
    WORKSPACE
    / "artifacts/experiment_registry/c02-deterministic-coaching-selection-v1"
    / "metadata.json",
    WORKSPACE / "artifacts/coaching/c02/failure.json",
)


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace-root",
            str(WORKSPACE),
            "--config",
            str(CONFIG),
            *extra,
        ],
        cwd=WORKSPACE,
        env={"PYTHONPATH": str(WORKSPACE / "src")},
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_zero_runtime_and_output(result: dict[str, object]) -> None:
    for field in (
        "c01_row_level_read_count",
        "c01_raw_read_count",
        "c01_locator_read_count",
        "c01_environment_read_count",
        "c01_identifier_read_count",
        "s107_completion_path_resolution_count",
        "s107_completion_read_attempt_count",
        "s107_completion_read_count",
        "s107_validator_load_count",
        "s107_deep_validation_count",
        "s107_calibration_evidence_read_count",
        "s107_threshold_report_read_count",
        "s107_registry_read_count",
        "preregistration_path_resolution_count",
        "preregistration_read_attempt_count",
        "preregistration_read_count",
        "diagnostic_input_path_resolution_count",
        "diagnostic_input_read_count",
        "coaching_selection_count",
        "coaching_output_write_count",
        "effect_estimation_count",
        "model_access_count",
        "gpu_access_count",
        "network_access_count",
        "llm_access_count",
        "free_generation_count",
        "output_path_resolution_count",
        "output_count",
        "registry_write_count",
        "commit_write_count",
        "publication_count",
    ):
        assert result[field] == 0
    assert result["actual_diagnostic_or_coaching_execution"] is False
    assert result["actual_effect_estimation"] is False
    assert result["actual_model_gpu_network_or_llm_execution"] is False
    assert result["artifact_created"] is False
    assert result["c02_completion_claim_allowed"] is False
    assert result["production_release_allowed"] is False
    assert result["publication_allowed"] is False


def test_config_only_reads_19_static_sources_and_nothing_runtime() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 16
    assert result["source_pin_read_count"] == 16
    assert result["source_toctou_verification_count"] == 0
    assert result["c01_validator_load_count"] == 0
    assert result["c01_deep_validation_count"] == 0
    assert result["c01_validation_report_read_count"] == 0
    assert result["c01_privacy_safe_aggregate_read_count"] == 0
    _assert_zero_runtime_and_output(result)
    assert before == after


def test_full_preflight_deep_validates_c01_then_stops_before_s107_path() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "s107_completion"
    assert result["error"] == "S107 completion commit is TBD_S107_COMPLETION"
    assert result["source_pin_read_count"] == 16
    assert result["source_toctou_verification_count"] == 18
    assert result["c01_validator_load_count"] == 1
    assert result["c01_deep_validation_count"] == 1
    assert result["c01_validation_report_read_count"] == 1
    assert result["c01_privacy_safe_aggregate_read_count"] == 1
    _assert_zero_runtime_and_output(result)
    assert before == after


def test_help_states_all_nonexecution_boundaries() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "does not read diagnostic issue rows" in normalized
    assert "select or generate actual coaching" in normalized
    assert "estimate effects" in normalized
    assert "runtime model/GPU/network/LLM" in normalized
    assert "write artifacts" in normalized
    assert "release to production" in normalized
