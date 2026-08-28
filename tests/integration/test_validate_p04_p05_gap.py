from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_p04_p05_gap.py"
CONFIG_RELATIVE = Path("configs/experiments/p04_p05_gap_validation_v1.yaml")


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    config_path = workspace / CONFIG_RELATIVE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes((ROOT / CONFIG_RELATIVE).read_bytes())
    return workspace


def _command(workspace: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace-root",
            str(workspace),
            "--config",
            CONFIG_RELATIVE.as_posix(),
            *extra,
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_cli_config_only_then_execution_remains_blocked_without_output(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)

    config_only = _command(workspace, "--validate-config-only")
    assert config_only.returncode == 0, config_only.stderr
    config_payload = json.loads(config_only.stdout)
    assert config_payload["status"] == "blocked_retired_canonical_sources"
    assert config_payload["blocker"] == "retired_canonical_sources"
    assert config_payload["declared_source_count"] == 4
    assert config_payload["declared_implementation_source_count"] == 0
    assert config_payload["access"]["aggregate_sources_read"] == 0
    assert config_payload["access"]["outputs_written"] == 0
    assert not (workspace / "artifacts/reproducibility/p04_p05_gap_validation").exists()

    run = _command(workspace)
    assert run.returncode == 1
    assert run.stdout == ""
    assert run.stderr == (
        "P04/P05 gap validation blocked: retired_canonical_sources\n"
    )
    assert not (workspace / "artifacts/reproducibility/p04_p05_gap_validation").exists()
