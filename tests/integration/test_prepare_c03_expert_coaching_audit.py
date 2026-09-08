from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prepare_c03_expert_coaching_audit.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_repository_config_only_reads_static_sources_and_nothing_runtime() -> None:
    completed = _run("--config-only")
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 16
    assert result["source_pin_read_count"] == 16
    for field, value in result.items():
        if field.endswith("_count") and field not in (
            "source_pin_count",
            "source_pin_read_count",
        ):
            assert value == 0, field
    assert result["artifact_created"] is False
    assert result["production_release_allowed"] is False
    assert result["publication_allowed"] is False


def test_repository_full_fails_at_s107_before_path_read_or_runtime_access() -> None:
    completed = _run()
    assert completed.returncode == 1
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "s107_completion"
    assert "TBD_S107_COMPLETION" in result["error"]
    assert result["source_pin_read_count"] == 16
    assert result["source_toctou_verification_count"] == 16
    for field, value in result.items():
        if field.endswith("_count") and field not in (
            "source_pin_read_count",
            "source_toctou_verification_count",
        ):
            assert value == 0, field
    assert result["artifact_created"] is False
    assert result["c03_completion_claim_allowed"] is False
    assert result["production_release_allowed"] is False
