from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prepare_d07_independent_expert_gold.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_config_only_keeps_every_protected_runtime_counter_zero() -> None:
    completed = _run("--config-only")
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 20
    assert result["source_pin_read_count"] == 20
    assert result["g04_progress_aggregate_read_count"] == 1
    assert result["g04_t43_completed_count"] == 0
    assert result["g04_t43_expected_count"] == 43
    assert result["g04_i30_completed_count"] == 0
    assert result["g04_i30_expected_count"] == 30
    for field, value in result.items():
        if field.endswith("_count") and field not in (
            "source_pin_count",
            "source_pin_read_count",
            "g04_progress_aggregate_read_count",
            "g04_t43_expected_count",
            "g04_i30_expected_count",
        ):
            assert value == 0, field
    assert result["artifact_created"] is False
    assert result["d07_completion_claim_allowed"] is False


def test_full_fails_at_the_reviewer_attestation_before_protected_paths() -> None:
    """The institutional/H00 gate that used to stop this run was removed.

    See DEV-CLAUDE-009: no approval, consent or privacy attestation is checked
    anywhere in D07 any more, so the reviewer attestation is now the first stop.
    """

    completed = _run()
    assert completed.returncode == 1
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "reviewer_attestation"
    assert "reviewer" in result["error"]
    assert result["source_pin_read_count"] == 20
    assert result["source_toctou_verification_count"] == 20
    assert result["g04_progress_aggregate_read_count"] == 1
    for field, value in result.items():
        if field.endswith("_count") and field not in (
            "source_pin_read_count",
            "source_toctou_verification_count",
            "g04_progress_aggregate_read_count",
            "g04_t43_expected_count",
            "g04_i30_expected_count",
        ):
            assert value == 0, field
    assert result["actual_expert_count"] == 0
    assert result["actual_rating_count"] == 0
    assert result["actual_locked_access_count"] == 0
    assert result["actual_metric_count"] == 0
    assert result["actual_result_count"] == 0
