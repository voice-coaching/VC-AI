from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_e01_metamorphic_robustness.py"
CONFIG = WORKSPACE / "configs/experiments/e01_metamorphic_robustness_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE / "artifacts/experiments/e01_metamorphic_robustness_v1/report.json",
    WORKSPACE / "artifacts/experiments/e01_metamorphic_robustness_v1/commit.json",
    WORKSPACE
    / "artifacts/experiment_registry/e01-metamorphic-robustness-v1/metadata.json",
    WORKSPACE / "artifacts/experiments/e01_metamorphic_robustness_v1/failure.json",
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


def _assert_execution_zero(result: dict[str, object]) -> None:
    for field in (
        "input_path_resolution_count",
        "input_manifest_read_count",
        "input_case_read_count",
        "raw_audio_read_count",
        "perturbed_audio_write_count",
        "model_path_resolution_count",
        "model_weight_read_count",
        "model_inference_count",
        "network_access_count",
        "gpu_access_count",
        "metric_computation_count",
        "decision_count",
        "locked_test_read_count",
        "s203_expert_holdout_read_count",
        "output_path_resolution_count",
        "output_count",
        "publication_count",
    ):
        assert result[field] == 0
    assert result["actual_audio_perturbation_or_model_execution"] is False
    assert result["actual_metric_or_decision"] is False
    assert result["locked_test_accessed"] is False
    assert result["s203_expert_holdout_accessed"] is False
    assert result["report_created"] is False
    assert result["e01_completion_claim_allowed"] is False
    assert result["publication_allowed"] is False


def test_config_only_reads_only_static_sources() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 14
    assert result["source_pin_read_count"] == 14
    assert result["source_pin_verify_count"] == 0
    assert result["s108_deep_validation_count"] == 0
    assert result["predecessor_path_resolution_count"] == 0
    assert result["predecessor_read_attempt_count"] == 0
    assert result["predecessor_read_count"] == 0
    assert result["preregistration_path_resolution_count"] == 0
    assert result["preregistration_read_attempt_count"] == 0
    assert result["preregistration_read_count"] == 0
    _assert_execution_zero(result)
    assert before == after


def test_full_preflight_fails_before_s108_path_resolution() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "s108_final"
    assert result["error"] == "S108 completion commit SHA is TBD_S108_COMPLETION"
    assert result["source_pin_read_count"] == 14
    assert result["predecessor_path_resolution_count"] == 0
    assert result["predecessor_read_attempt_count"] == 0
    assert result["predecessor_read_count"] == 0
    assert result["preregistration_path_resolution_count"] == 0
    assert result["s108_deep_validation_count"] == 0
    _assert_execution_zero(result)
    assert before == after


def test_help_exposes_nonexecution_and_nonlocked_boundary() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "does not read audio" in normalized
    assert "locked/S203 holdout row" in normalized
    assert "silence/gain/duration/F0/phone perturbation" in normalized
    assert "open/run a model" in normalized
    assert "use network/GPU" in normalized
    assert "compute a metric/decision" in normalized
    assert "write/publish an output" in normalized
