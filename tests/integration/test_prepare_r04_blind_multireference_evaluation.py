from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_r04_blind_multireference_evaluation.py"
CONFIG = WORKSPACE / "configs/experiments/r04_blind_multireference_evaluation_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE
    / "artifacts/experiments/r04_blind_multireference_evaluation_v1"
    / "randomization_manifest.json",
    WORKSPACE / "data/processed/evaluation/r04/r04_rating_manifest_v1.json",
    WORKSPACE
    / "artifacts/experiments/r04_blind_multireference_evaluation_v1"
    / "r04_aggregate_analysis_v1.json",
    WORKSPACE
    / "artifacts/experiments/r04_blind_multireference_evaluation_v1"
    / "commit.json",
    WORKSPACE
    / "artifacts/experiment_registry/r04-blind-multireference-evaluation-v1"
    / "metadata.json",
    WORKSPACE
    / "artifacts/experiments/r04_blind_multireference_evaluation_v1"
    / "failure.json",
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


def _assert_zero_evaluation_and_output(result: dict[str, object]) -> None:
    for field in (
        "r02_failure_rescue_count",
        "r02_quarantine_row_read_count",
        "r03_completion_path_resolution_count",
        "r03_completion_read_attempt_count",
        "r03_completion_read_count",
        "r03_registry_read_count",
        "r03_bank_manifest_read_count",
        "r03_selection_trace_read_count",
        "r03_aggregate_read_count",
        "r03_bank_lineage_validation_count",
        "human_approval_path_resolution_count",
        "human_approval_read_attempt_count",
        "human_approval_read_count",
        "preregistration_path_resolution_count",
        "preregistration_read_attempt_count",
        "preregistration_read_count",
        "pilot_attestation_path_resolution_count",
        "pilot_attestation_read_attempt_count",
        "pilot_attestation_read_count",
        "sap_path_resolution_count",
        "sap_read_attempt_count",
        "sap_read_count",
        "listener_attestation_path_resolution_count",
        "listener_attestation_read_attempt_count",
        "listener_attestation_read_count",
        "d1_audio_path_resolution_count",
        "d1_audio_read_count",
        "d1_rating_path_resolution_count",
        "d1_rating_read_count",
        "listener_recruitment_count",
        "audio_read_count",
        "audio_write_count",
        "model_access_count",
        "gpu_access_count",
        "randomization_execution_count",
        "rating_collection_count",
        "mos_computation_count",
        "cmos_computation_count",
        "statistics_computation_count",
        "superiority_decision_count",
        "result_generation_count",
        "output_path_resolution_count",
        "output_count",
        "registry_write_count",
        "commit_write_count",
        "publication_count",
    ):
        assert result[field] == 0
    assert result["actual_listener_or_audio_or_rating_execution"] is False
    assert result["actual_model_or_gpu_execution"] is False
    assert result["actual_mos_cmos_or_statistics_computation"] is False
    assert result["actual_superiority_or_completion_decision"] is False
    assert result["artifact_created"] is False
    assert result["r04_completion_claim_allowed"] is False
    assert result["publication_allowed"] is False


def test_config_only_has_zero_runtime_predecessor_d1_or_output_access() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 25
    assert result["source_pin_read_count"] == 25
    assert result["predecessor_static_source_read_count"] == 0
    assert result["r00_deep_validation_count"] == 0
    assert result["r01_completion_read_attempt_count"] == 0
    assert result["r02_completion_read_attempt_count"] == 0
    _assert_zero_evaluation_and_output(result)
    assert before == after


def test_full_preflight_deep_validates_r00_then_stops_at_missing_r01() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "r01_completion"
    assert result["error"] == "R01 completion commit is TBD_R01_COMPLETION"
    assert result["source_pin_read_count"] == 25
    assert result["predecessor_static_source_read_count"] == 17
    assert result["r00_deep_validation_count"] == 1
    assert result["r00_public_or_aggregate_evidence_read_count"] == 10
    assert result["r00_raw_audio_read_count"] == 0
    assert result["r00_row_level_read_count"] == 0
    assert result["r01_completion_path_resolution_count"] == 0
    assert result["r01_completion_read_attempt_count"] == 0
    assert result["r01_completion_read_count"] == 0
    assert result["r02_completion_read_attempt_count"] == 0
    _assert_zero_evaluation_and_output(result)
    assert before == after


def test_help_states_every_static_nonexecution_boundary() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "does not recruit listeners" in normalized
    assert "open or write audio/ratings" in normalized
    assert "run a model or GPU" in normalized
    assert "compute MOS/CMOS/statistics" in normalized
    assert "decide superiority/completion" in normalized
    assert "publish results" in normalized
