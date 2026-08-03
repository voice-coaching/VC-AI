from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_r01_human_cosyvoice_candidates.py"
CONFIG = WORKSPACE / "configs/experiments/r01_human_cosyvoice_candidates_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE / "data/processed/references/r01/r01_candidate_manifest_v1.json",
    WORKSPACE / "data/processed/references/r01/audio",
    WORKSPACE / "artifacts/experiments/r01_human_cosyvoice_candidates_v1/failure.json",
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


def test_config_only_reads_sources_and_has_zero_runtime_or_output() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 13
    assert result["source_pin_read_count"] == 13
    assert result["r00_deep_validation_count"] == 0
    assert result["human_attestation_read_attempt_count"] == 0
    assert result["cosyvoice_attestation_read_attempt_count"] == 0
    for field in (
        "recording_data_path_resolution_count",
        "candidate_data_path_resolution_count",
        "audio_read_count",
        "audio_write_count",
        "model_path_resolution_count",
        "model_metadata_read_count",
        "model_weight_read_count",
        "model_download_count",
        "runtime_environment_path_resolution_count",
        "runtime_environment_read_count",
        "gpu_access_count",
        "candidate_id_generation_count",
        "candidate_manifest_read_count",
        "candidate_manifest_write_count",
        "metric_count",
        "r02_gate_read_count",
        "publication_count",
        "output_path_resolution_count",
        "output_count",
    ):
        assert result[field] == 0
    assert result["audio_created"] is False
    assert result["candidate_manifest_created"] is False
    assert result["r01_completion_claim_allowed"] is False
    assert before == after


def test_full_preflight_deep_validates_r00_then_stops_before_human_path() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "human_attestation"
    assert result["error"] == ("human attestation is TBD_EXTERNAL_ATTESTATION")
    assert result["source_pin_read_count"] == 13
    assert result["r00_validator_load_count"] == 1
    assert result["r00_deep_validation_count"] == 1
    assert result["r00_public_or_aggregate_evidence_read_count"] > 0
    assert result["r00_raw_audio_read_count"] == 0
    assert result["r00_row_level_read_count"] == 0
    assert result["human_attestation_read_attempt_count"] == 0
    assert result["human_attestation_read_count"] == 0
    assert result["cosyvoice_attestation_read_attempt_count"] == 0
    assert result["cosyvoice_attestation_read_count"] == 0
    for field in (
        "recording_data_path_resolution_count",
        "candidate_data_path_resolution_count",
        "audio_read_count",
        "audio_write_count",
        "model_path_resolution_count",
        "model_metadata_read_count",
        "model_weight_read_count",
        "model_download_count",
        "runtime_environment_path_resolution_count",
        "runtime_environment_read_count",
        "gpu_access_count",
        "candidate_id_generation_count",
        "candidate_manifest_read_count",
        "candidate_manifest_write_count",
        "metric_count",
        "r02_gate_read_count",
        "publication_count",
        "output_path_resolution_count",
        "output_count",
    ):
        assert result[field] == 0
    assert result["audio_created"] is False
    assert result["candidate_manifest_created"] is False
    assert result["r01_completion_claim_allowed"] is False
    assert result["publication_allowed"] is False
    assert before == after


def test_help_states_that_no_runtime_generation_is_performed() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "does not record or read audio" in normalized
    assert "query a GPU" in normalized
    assert "publish anything" in normalized
