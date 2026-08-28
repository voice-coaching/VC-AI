from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_r02_candidate_hard_gate.py"
CONFIG = WORKSPACE / "configs/experiments/r02_candidate_hard_gate_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE
    / "artifacts/experiments/r02_candidate_hard_gate_v1/blinded_execution_manifest.json",
    WORKSPACE
    / "artifacts/experiments/r02_candidate_hard_gate_v1/source_lineage_sidecar.json",
    WORKSPACE / "artifacts/experiments/r02_candidate_hard_gate_v1/report.json",
    WORKSPACE
    / "artifacts/experiments/r02_candidate_hard_gate_v1/eligible_for_r03.json",
    WORKSPACE / "artifacts/experiments/r02_candidate_hard_gate_v1/quarantined.json",
    WORKSPACE
    / "artifacts/experiment_registry/r02-candidate-hard-gate-v1/metadata.json",
    WORKSPACE / "artifacts/experiments/r02_candidate_hard_gate_v1/commit.json",
    WORKSPACE / "artifacts/experiments/r02_candidate_hard_gate_v1/failure.json",
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


def test_config_only_has_zero_runtime_model_audio_metric_or_output() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 15
    assert result["source_pin_read_count"] == 15
    for field in (
        "r00_deep_validation_count",
        "r01_completion_commit_read_attempt_count",
        "r01_candidate_manifest_read_count",
        "preregistration_read_attempt_count",
        "asr_attestation_read_attempt_count",
        "artifact_attestation_read_attempt_count",
        "d1_audio_root_path_resolution_count",
        "d1_audio_read_count",
        "model_path_resolution_count",
        "model_weight_read_count",
        "model_download_count",
        "gpu_access_count",
        "metric_execution_count",
        "s1_artifact_read_count",
        "s2_artifact_read_count",
        "gold_or_locked_test_read_count",
        "output_path_resolution_count",
        "output_write_count",
    ):
        assert result[field] == 0
    assert before == after


def test_full_preflight_deep_validates_r00_then_stops_before_r01_or_d1() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "r01_completion_commit"
    assert result["error"] == "R01 completion commit is TBD_R01_COMPLETION"
    assert result["source_pin_read_count"] == 15
    assert result["r00_validator_load_count"] == 1
    assert result["r00_deep_validation_count"] == 1
    assert result["r00_public_or_aggregate_evidence_read_count"] == 33
    assert result["r00_target_rule_read_count"] == 20
    assert result["r00_raw_audio_read_count"] == 0
    assert result["r00_row_level_read_count"] == 0
    assert result["r01_completion_commit_read_attempt_count"] == 0
    for field in (
        "r01_registry_read_count",
        "r01_candidate_manifest_read_count",
        "r01_human_attestation_read_count",
        "r01_cosyvoice_attestation_read_count",
        "preregistration_read_attempt_count",
        "asr_attestation_read_attempt_count",
        "artifact_attestation_read_attempt_count",
        "d1_candidate_manifest_path_resolution_count",
        "d1_audio_root_path_resolution_count",
        "d1_audio_read_count",
        "model_path_resolution_count",
        "model_weight_read_count",
        "model_download_count",
        "gpu_access_count",
        "metric_execution_count",
        "s1_artifact_read_count",
        "s2_artifact_read_count",
        "gold_or_locked_test_read_count",
        "source_label_gate_input_read_count",
        "output_path_resolution_count",
        "output_write_count",
        "publication_count",
    ):
        assert result[field] == 0
    assert before == after


def test_help_states_protected_and_runtime_boundaries() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "does not resolve D1 audio" in normalized
    assert "read audio or models" in normalized
    assert "query a GPU" in normalized
    assert "open S1/S2 or gold/locked data" in normalized
    assert "publish candidates" in normalized
