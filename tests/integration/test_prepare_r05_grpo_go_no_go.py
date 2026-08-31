from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_r05_grpo_go_no_go.py"
CONFIG = WORKSPACE / "configs/experiments/r05_grpo_go_no_go_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE / "artifacts/experiments/r05_grpo_go_no_go_v1/decision_report.json",
    WORKSPACE / "artifacts/experiments/r05_grpo_go_no_go_v1/commit.json",
    WORKSPACE / "artifacts/experiment_registry/r05-grpo-go-no-go-v1/metadata.json",
    WORKSPACE / "artifacts/experiments/r05_grpo_go_no_go_v1/failure.json",
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


def _assert_forbidden_runtime_zero(result: dict[str, object]) -> None:
    for field in (
        "row_level_data_read_count",
        "protected_data_read_count",
        "audio_read_count",
        "rating_read_count",
        "participant_record_read_count",
        "model_path_resolution_count",
        "model_weight_read_count",
        "model_download_count",
        "network_access_count",
        "gpu_access_count",
        "reward_execution_count",
        "grpo_training_count",
        "f5_or_styletts_execution_count",
        "metric_computation_count",
        "effect_estimation_count",
        "decision_evaluation_count",
        "output_path_resolution_count",
        "output_count",
        "publication_count",
    ):
        assert result[field] == 0
    assert result["actual_training_download_model_gpu_reward_execution"] is False
    assert result["actual_metric_or_effect_estimation"] is False
    assert result["decision_report_created"] is False
    assert result["r05_completion_claim_allowed"] is False
    assert result["publication_allowed"] is False


def test_config_only_reads_static_pins_and_nothing_runtime() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 11
    assert result["source_pin_read_count"] == 11
    assert result["source_pin_verify_count"] == 0
    assert result["s108_deep_validation_count"] == 0
    assert result["s203_deep_validation_count"] == 0
    assert result["r04_deep_validation_count"] == 0
    assert result["h00_deep_validation_count"] == 0
    assert result["h04_deep_validation_count"] == 0
    for field in (
        "predecessor_path_resolution_count",
        "predecessor_read_attempt_count",
        "predecessor_read_count",
        "external_evidence_path_resolution_count",
        "external_evidence_read_attempt_count",
        "external_evidence_read_count",
        "preregistration_path_resolution_count",
        "preregistration_read_attempt_count",
        "preregistration_read_count",
        "authorization_path_resolution_count",
        "authorization_read_attempt_count",
        "authorization_read_count",
        "aggregate_evidence_read_count",
    ):
        assert result[field] == 0
    _assert_forbidden_runtime_zero(result)
    assert before == after


def test_full_preflight_fails_first_at_unresolved_s108() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "s108_final"
    assert result["error"] == "S108 completion commit SHA is TBD_S108_COMPLETION"
    assert result["source_pin_read_count"] == 11
    assert result["predecessor_path_resolution_count"] == 0
    assert result["predecessor_read_attempt_count"] == 0
    assert result["predecessor_read_count"] == 0
    assert result["s108_deep_validation_count"] == 0
    assert result["s203_deep_validation_count"] == 0
    assert result["r04_deep_validation_count"] == 0
    assert result["h00_deep_validation_count"] == 0
    assert result["h04_deep_validation_count"] == 0
    _assert_forbidden_runtime_zero(result)
    assert before == after


def test_help_states_the_nonexecution_boundary() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "does not download or open a model" in normalized
    assert "run F5/StyleTTS/GRPO or a reward" in normalized
    assert "compute a metric/effect" in normalized
    assert "write a report" in normalized
    assert "authorize training" in normalized
