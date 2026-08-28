from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_e02_confidence_guard.py"
CONFIG = WORKSPACE / "configs/experiments/e02_confidence_guard_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE / "data/processed/evaluation/e02/e02_confidence_guard_decision_v1.json",
    WORKSPACE / "artifacts/evaluation/e02/e02_confidence_guard_report_v1.json",
    WORKSPACE / "artifacts/experiment_registry/e02-confidence-guard-v1/metadata.json",
    WORKSPACE / "artifacts/evaluation/e02/commit.json",
    WORKSPACE / "artifacts/evaluation/e02/failure.json",
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


def _assert_zero_predecessor_runtime_api_and_output(
    result: dict[str, object],
) -> None:
    for field in (
        "s107_completion_path_resolution_count",
        "s107_completion_read_attempt_count",
        "s107_completion_read_count",
        "s107_validator_load_count",
        "s107_deep_validation_count",
        "s107_calibration_evidence_read_count",
        "s107_threshold_report_read_count",
        "s107_registry_read_count",
        "c02_completion_path_resolution_count",
        "c02_completion_read_attempt_count",
        "c02_completion_read_count",
        "c02_contract_load_count",
        "c02_static_source_read_count",
        "c02_c01_deep_validation_count",
        "c02_preregistration_read_count",
        "c02_diagnostic_input_read_count",
        "c02_selection_output_read_count",
        "c02_registry_read_count",
        "c02_deep_recomputation_count",
        "e02_preregistration_path_resolution_count",
        "e02_preregistration_read_attempt_count",
        "e02_preregistration_read_count",
        "guard_input_path_resolution_count",
        "guard_input_read_count",
        "request_or_row_access_count",
        "api_invocation_count",
        "coaching_advice_delivery_count",
        "reference_delivery_count",
        "corrective_action_delivery_count",
        "model_access_count",
        "gpu_access_count",
        "metric_computation_count",
        "network_access_count",
        "llm_access_count",
        "free_generation_count",
        "output_path_resolution_count",
        "output_count",
        "registry_write_count",
        "commit_write_count",
        "publication_count",
    ):
        assert result[field] == 0
    assert result["actual_request_coaching_reference_or_api_execution"] is False
    assert result["actual_model_gpu_metric_network_llm_execution"] is False
    assert result["artifact_created"] is False
    assert result["e02_completion_claim_allowed"] is False
    assert result["production_release_allowed"] is False
    assert result["publication_allowed"] is False


def test_config_only_reads_27_scoped_sources_and_nothing_runtime() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 27
    assert result["source_pin_read_count"] == 27
    assert result["source_toctou_verification_count"] == 0
    _assert_zero_predecessor_runtime_api_and_output(result)
    assert before == after


def test_full_preflight_stops_before_unresolved_s107_path_or_c02_access() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "s107_completion"
    assert result["error"] == "S107 completion commit is TBD_S107_COMPLETION"
    assert result["source_pin_read_count"] == 27
    assert result["source_toctou_verification_count"] == 27
    _assert_zero_predecessor_runtime_api_and_output(result)
    assert before == after


def test_help_states_threshold_and_every_nonexecution_boundary() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "does not read a request or guard input" in normalized
    assert "invoke an API" in normalized
    assert "deliver coaching advice/reference/corrective actions" in normalized
    assert "copy a numeric S107 threshold into E02" in normalized
    assert "call a model/GPU/metric/network/LLM" in normalized
    assert "freely generate text" in normalized
    assert "write an artifact" in normalized
    assert "release to production" in normalized
    assert "claim E02 completion" in normalized
