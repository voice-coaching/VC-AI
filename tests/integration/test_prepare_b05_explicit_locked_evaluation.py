from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prepare_b05_explicit_locked_evaluation.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workspace-root", str(ROOT), *args],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_config_only_is_static_and_zero_protected_access() -> None:
    result = _run("--config-only")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "static_contract_validated_no_locked_execution"
    assert payload["source_pin_read_count"] == 35
    assert payload["b03_static_validation_count"] == 1
    assert payload["b06_static_validation_count"] == 1
    for field in (
        "g04_path_resolution_count",
        "d07_path_resolution_count",
        "s107_path_resolution_count",
        "s108_freeze_path_resolution_count",
        "preregistration_path_resolution_count",
        "batch_manifest_path_resolution_count",
        "b03_checkpoint_hash_read_attempt_count",
        "authorization_path_resolution_count",
        "locked_locator_resolution_count",
        "locked_row_read_count",
        "locked_audio_read_count",
        "locked_label_read_count",
        "locked_prediction_read_count",
        "model_access_count",
        "model_deserialize_count",
        "model_inference_count",
        "gpu_access_count",
        "metric_count",
        "bootstrap_count",
        "holm_count",
        "output_path_resolution_count",
        "output_write_count",
    ):
        assert payload[field] == 0
    assert payload["actual_locked_access_performed"] is False
    assert payload["b05_score_awarded"] == 0
    assert payload["b05_completion_claim_allowed"] is False


def test_full_mode_reports_first_blocker_before_g04_path() -> None:
    result = _run()
    assert result.returncode == 1
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["status"] == "blocked_fail_closed"
    assert payload["stage"] == "g04_completion"
    assert payload["error"] == "G04 completion SHA is TBD_G04_COMPLETION"
    assert payload["g04_path_resolution_count"] == 0
    assert payload["d07_path_resolution_count"] == 0
    assert payload["b03_checkpoint_hash_read_attempt_count"] == 0
    assert payload["authorization_path_resolution_count"] == 0
    assert payload["locked_locator_resolution_count"] == 0
    assert payload["model_access_count"] == 0
    assert payload["gpu_access_count"] == 0
    assert payload["metric_count"] == 0
    assert payload["output_path_resolution_count"] == 0
    assert payload["output_write_count"] == 0
    assert payload["b05_score_awarded"] == 0
