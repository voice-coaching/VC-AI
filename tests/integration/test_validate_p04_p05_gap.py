from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import yaml  # type: ignore[import-untyped]


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_p04_p05_gap.py"
CONFIG_RELATIVE = Path("configs/experiments/p04_p05_gap_validation_v1.yaml")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    config = yaml.safe_load((ROOT / CONFIG_RELATIVE).read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    for group in ("sources", "implementation_sources"):
        records = config[group]
        assert isinstance(records, dict)
        for record in records.values():
            assert isinstance(record, dict)
            relative = Path(str(record["path"]))
            destination = workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
            content = destination.read_bytes()
            record["sha256"] = _sha(content)
            record["size_bytes"] = len(content)
    config_path = workspace / CONFIG_RELATIVE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(
            config,
            allow_unicode=True,
            sort_keys=False,
            width=1000,
        ),
        encoding="utf-8",
    )
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


def test_cli_config_only_then_publish_and_commit_only_validation(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)

    config_only = _command(workspace, "--validate-config-only")
    assert config_only.returncode == 0, config_only.stderr
    config_payload = json.loads(config_only.stdout)
    assert config_payload["status"] == "valid_config_only"
    assert config_payload["access"]["aggregate_sources_read"] == 0
    assert config_payload["access"]["outputs_written"] == 0
    assert not (workspace / "artifacts/reproducibility/p04_p05_gap_validation").exists()

    run = _command(workspace)
    assert run.returncode == 0, run.stderr
    payload = json.loads(run.stdout)
    assert payload["status"] == "integrity_pass_semantic_update_pending"
    assert payload["deep_validation"] == "valid"
    assert payload["p04_complete"] is False
    assert payload["p05_complete"] is False

    validate = _command(workspace, "--validate-commit-only")
    assert validate.returncode == 0, validate.stderr
    validation = json.loads(validate.stdout)
    assert validation["status"] == "valid"
    assert validation["generation_id"] == payload["generation_id"]
    assert validation["semantic_finding_codes"] == [
        "B07_COMPLETED_EVIDENCE_NOT_PROMOTED",
    ]
    assert validation["semantic_resolution_codes"] == [
        "CLAIM_GAP_P04_C_COUNT_RESOLVED",
        "DATA_LICENSE_LEDGER_EVIDENCE_MAPPING_RESOLVED",
    ]
