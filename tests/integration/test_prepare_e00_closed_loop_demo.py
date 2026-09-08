from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_e00_closed_loop_demo.py"
CONFIG = WORKSPACE / "configs/experiments/e00_closed_loop_demo_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE / "artifacts/demo/e00/e00_closed_loop_trace_v1.json",
    WORKSPACE / "artifacts/demo/e00/e00_closed_loop_completion_v1.json",
    WORKSPACE / "artifacts/experiment_registry/e00-closed-loop-demo-v1/metadata.json",
    WORKSPACE / "artifacts/demo/e00/commit.json",
    WORKSPACE / "artifacts/demo/e00/failure.json",
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


def _assert_zero_protected_runtime_and_output(
    result: dict[str, object],
) -> None:
    for field, value in result.items():
        if field.endswith("_count") and field not in {
            "source_pin_count",
            "source_pin_read_count",
            "source_toctou_verification_count",
        }:
            assert value == 0, field
    assert (
        result["actual_input_audio_model_gpu_api_coaching_reference_rerecord_metric"]
        is False
    )
    assert result["artifact_created"] is False
    assert result["completion_claim_allowed"] is False
    assert result["production_deployment_claim_allowed"] is False


def test_config_only_reads_42_scoped_sources_and_nothing_protected() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 42
    assert result["source_pin_read_count"] == 42
    assert result["source_toctou_verification_count"] == 0
    _assert_zero_protected_runtime_and_output(result)
    assert before == after


def test_full_preflight_stops_before_unresolved_s108_or_any_demo_path() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "s108_final"
    assert result["error"] == "S108 completion commit SHA is TBD_S108_COMPLETION"
    assert result["source_pin_read_count"] == 42
    assert result["source_toctou_verification_count"] == 42
    _assert_zero_protected_runtime_and_output(result)
    assert before == after


def test_help_exposes_closed_loop_lineage_and_scope_boundaries() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "never resolves a session" in normalized
    assert "runs S108" in normalized
    assert "invokes E02/API" in normalized
    assert "delivers C02 coaching or R03/D08 reference" in normalized
    assert "records attempt 2" in normalized
    assert "computes a comparison" in normalized
    assert "writes output" in normalized
    assert "only to learner attempt input" in normalized
    assert "separately licensed D08 human reference" in normalized
    assert "exact D08 metadata-row, audio, and rights lineage receipt" in normalized
