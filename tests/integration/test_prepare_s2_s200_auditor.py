from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv-scorer/bin/python"
SCRIPT = ROOT / "scripts/prepare_s2_s200_auditor.py"
OUTPUT_ROOT = ROOT / "artifacts/experiments/aihub71490_s2_s200_independent_auditor_v1"


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    return environment


def _snapshot_output() -> dict[str, str]:
    if not OUTPUT_ROOT.exists():
        return {}
    return {
        path.relative_to(OUTPUT_ROOT).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(OUTPUT_ROOT.rglob("*"))
        if path.is_file()
    }


def test_cli_config_only_has_zero_runtime_access_and_output() -> None:
    before = _snapshot_output()
    completed = subprocess.run(
        [str(PYTHON), str(SCRIPT), "--config-only"],
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_read_count"] >= 10
    for field_name in (
        "predecessor_metadata_read_attempt_count",
        "predecessor_metadata_read_count",
        "external_aggregate_read_attempt_count",
        "external_aggregate_read_count",
        "data_read_count",
        "model_read_count",
        "protected_data_read_count",
        "holdout_read_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
        "result_count",
        "output_count",
    ):
        assert result[field_name] == 0
    assert _snapshot_output() == before


def test_cli_full_preflight_fails_at_s108_before_external_or_output() -> None:
    before = _snapshot_output()
    completed = subprocess.run(
        [str(PYTHON), str(SCRIPT)],
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "predecessor_validation"
    assert result["predecessor_metadata_read_attempt_count"] == 1
    assert result["predecessor_metadata_read_count"] == 0
    assert result["external_aggregate_read_attempt_count"] == 0
    for field_name in (
        "data_read_count",
        "model_read_count",
        "protected_data_read_count",
        "holdout_read_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
        "result_count",
        "output_count",
    ):
        assert result[field_name] == 0
    assert _snapshot_output() == before
