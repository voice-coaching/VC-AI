from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_r03_reference_bank_selection.py"
CONFIG = WORKSPACE / "configs/experiments/r03_reference_bank_selection_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE / "data/processed/references/r03/r03_reference_bank_manifest_v1.json",
    WORKSPACE
    / "artifacts/experiments/r03_reference_bank_selection_v1"
    / "r03_selection_trace_v1.json",
    WORKSPACE
    / "artifacts/experiments/r03_reference_bank_selection_v1"
    / "r03_reference_bank_aggregate_v1.json",
    WORKSPACE / "artifacts/experiments/r03_reference_bank_selection_v1/commit.json",
    WORKSPACE
    / "artifacts/experiment_registry/r03-reference-bank-selection-v1/metadata.json",
    WORKSPACE / "artifacts/experiments/r03_reference_bank_selection_v1/failure.json",
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


def _assert_zero_runtime(result: dict[str, object]) -> None:
    for field in (
        "r01_audio_read_count",
        "r02_quarantine_partition_validation_count",
        "selection_quarantine_row_read_count",
        "preregistration_path_resolution_count",
        "preregistration_read_attempt_count",
        "preregistration_read_count",
        "eligible_candidate_data_path_resolution_count",
        "eligible_candidate_data_read_count",
        "objective_metric_computation_count",
        "pareto_computation_count",
        "diversity_computation_count",
        "selection_count",
        "audio_read_count",
        "audio_write_count",
        "model_access_count",
        "gpu_access_count",
        "r04_label_read_count",
        "bank_manifest_read_count",
        "bank_manifest_write_count",
        "aggregate_write_count",
        "publication_count",
        "output_path_resolution_count",
        "output_count",
    ):
        assert result[field] == 0
    assert result["actual_audio_or_model_or_gpu_execution"] is False
    assert result["actual_metric_or_selection_execution"] is False
    assert result["bank_manifest_created"] is False
    assert result["r03_completion_claim_allowed"] is False
    assert result["publication_allowed"] is False


def test_config_only_has_zero_predecessor_runtime_data_or_output() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 15
    assert result["source_pin_read_count"] == 15
    assert result["r00_deep_validation_count"] == 0
    assert result["r01_completion_read_attempt_count"] == 0
    assert result["r02_completion_read_attempt_count"] == 0
    _assert_zero_runtime(result)
    assert before == after


def test_full_preflight_validates_r00_then_stops_at_missing_r01() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "r01_completion"
    assert result["error"] == "R01 completion commit is TBD_R01_COMPLETION"
    assert result["source_pin_read_count"] == 15
    assert result["r00_validator_load_count"] == 1
    assert result["r00_deep_validation_count"] == 1
    assert result["r00_public_or_aggregate_evidence_read_count"] == 10
    assert result["r00_raw_audio_read_count"] == 0
    assert result["r00_row_level_read_count"] == 0
    assert result["r01_completion_path_resolution_count"] == 0
    assert result["r01_completion_read_attempt_count"] == 0
    assert result["r01_completion_read_count"] == 0
    assert result["r02_contract_load_count"] == 1
    assert result["r02_completion_read_attempt_count"] == 0
    _assert_zero_runtime(result)
    assert before == after


def test_help_exposes_static_nonexecution_boundary() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "does not open candidate audio" in normalized
    assert "run a model or GPU" in normalized
    assert "read R04 labels" in normalized
    assert "publish anything" in normalized
