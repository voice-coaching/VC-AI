from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv-scorer/bin/python"
SCRIPT = ROOT / "scripts/prepare_wavlm_s108_no_locked_dry_run.py"
RECEIPT = ROOT / (
    "artifacts/experiments/aihub71490_wavlm_s108_no_locked_dry_run_v1/"
    "receipts/73f85af411619e341b19cd78966f39acaf84e4cf9346bb34195cb222af0aaa92.json"
)


def _snapshot_output() -> dict[str, str]:
    root = ROOT / "artifacts/experiments/aihub71490_wavlm_s108_no_locked_dry_run_v1"
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    return environment


def test_config_only_has_zero_artifact_access_and_zero_output() -> None:
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
    assert result["status"] == "valid"
    assert result["protected_data_access_count"] == 0
    assert result["locked_access_count"] == 0
    assert result["output_count"] == 0
    assert _snapshot_output() == before


def test_published_current_state_receipt_is_deep_valid() -> None:
    completed = subprocess.run(
        [str(PYTHON), str(SCRIPT), "--validate-receipt", str(RECEIPT)],
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid"
    assert result["historical_snapshot"] is True
    assert result["overall_state"] == "engineering_in_progress"
    assert result["ready_for_locked_batch"] is False
    assert result["s108_completion_claim_allowed"] is False
    assert result["s108_score_allowed"] is False
    assert result["row_level_artifact_read_count"] == 0
    assert result["locked_access_count"] == 0
