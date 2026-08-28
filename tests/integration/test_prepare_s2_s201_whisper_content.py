from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prepare_s2_s201_whisper_content.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_repository_config_only_cli_reports_zero_runtime_access() -> None:
    completed = _run("--config-only")
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_read_count"] == 8
    for field in (
        "predecessor_metadata_read_attempt_count",
        "external_attestation_read_attempt_count",
        "data_path_resolution_count",
        "data_manifest_read_count",
        "data_row_read_count",
        "audio_read_count",
        "transcript_row_read_count",
        "model_path_resolution_count",
        "model_metadata_read_count",
        "model_weight_read_count",
        "model_download_count",
        "protected_data_read_count",
        "s1_prediction_read_count",
        "oracle_alignment_read_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
        "prediction_count",
        "result_count",
        "output_path_resolution_count",
        "output_count",
    ):
        assert result[field] == 0


def test_repository_full_cli_fails_at_s108_before_data_model_or_output() -> None:
    completed = _run()
    assert completed.returncode == 1
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "predecessor_validation"
    assert "S108" in result["error"]
    assert result["predecessor_metadata_read_attempt_count"] == 1
    for field in (
        "threshold_attestation_read_attempt_count",
        "external_attestation_read_attempt_count",
        "data_path_resolution_count",
        "data_manifest_read_count",
        "data_row_read_count",
        "model_path_resolution_count",
        "model_metadata_read_count",
        "model_weight_read_count",
        "model_download_count",
        "protected_data_read_count",
        "s1_prediction_read_count",
        "oracle_alignment_read_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
        "prediction_count",
        "result_count",
        "output_path_resolution_count",
        "output_count",
    ):
        assert result[field] == 0
