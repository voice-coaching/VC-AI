from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_h04_learning_effect_analysis.py"
CONFIG = WORKSPACE / "configs/experiments/h04_learning_effect_analysis_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE
    / "data/processed/human_research/h04/h04_learning_effect_aggregate_input_v1.json",
    WORKSPACE
    / "artifacts/human_research/h04/h04_learning_effect_statistical_report_v1.json",
    WORKSPACE / "artifacts/human_research/h04/h04_learning_effect_completion_v1.json",
    WORKSPACE
    / "artifacts/experiment_registry/h04-learning-effect-analysis-v1/metadata.json",
    WORKSPACE / "artifacts/human_research/h04/failure.json",
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


def _assert_zero_protected_runtime_and_output(result: dict[str, object]) -> None:
    for field in (
        "h00_config_read_count",
        "h00_contract_load_count",
        "h00_deep_validation_count",
        "h00_approval_path_resolution_count",
        "h00_approval_read_count",
        "h00_attestation_path_resolution_count",
        "h00_attestation_read_count",
        "h00_restricted_official_document_resolution_count",
        "h00_restricted_official_document_read_count",
        "feasibility_pilot_path_resolution_count",
        "feasibility_pilot_read_attempt_count",
        "feasibility_pilot_read_count",
        "feasibility_pilot_deep_validation_count",
        "h01_completion_path_resolution_count",
        "h01_completion_read_attempt_count",
        "h01_completion_read_count",
        "h01_deep_validation_count",
        "sap_path_resolution_count",
        "sap_read_attempt_count",
        "sap_read_count",
        "sap_deep_validation_count",
        "h02_h03_completion_path_resolution_count",
        "h02_h03_completion_read_attempt_count",
        "h02_h03_completion_read_count",
        "h02_h03_deep_validation_count",
        "analyst_attestation_path_resolution_count",
        "analyst_attestation_read_attempt_count",
        "analyst_attestation_read_count",
        "analyst_attestation_deep_validation_count",
        "aggregate_input_path_resolution_count",
        "aggregate_input_read_count",
        "participant_read_count",
        "rating_row_read_count",
        "audio_read_count",
        "transcript_read_count",
        "restricted_d1_data_read_count",
        "personal_or_sensitive_data_read_count",
        "model_access_count",
        "gpu_access_count",
        "network_access_count",
        "statistics_computation_count",
        "effect_estimation_count",
        "report_generation_count",
        "output_path_resolution_count",
        "output_count",
        "registry_write_count",
        "commit_write_count",
        "publication_count",
    ):
        assert result[field] == 0
    assert result["actual_participant_rating_audio_or_restricted_data_access"] is False
    assert result["actual_model_gpu_network_or_statistics_execution"] is False
    assert result["actual_effect_estimate_or_report"] is False
    assert result["artifact_created"] is False
    assert result["h04_completion_claim_allowed"] is False
    assert result["publication_allowed"] is False


def test_config_only_reads_19_static_sources_and_nothing_runtime() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 15
    assert result["source_pin_read_count"] == 15
    assert result["source_toctou_verification_count"] == 0
    _assert_zero_protected_runtime_and_output(result)
    assert before == after


def test_full_preflight_stops_at_h00_before_every_protected_path() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "h00_authorization"
    assert result["error"] == "H00 approval is TBD_H00_APPROVAL"
    assert result["source_pin_read_count"] == 15
    assert result["source_toctou_verification_count"] == 15
    _assert_zero_protected_runtime_and_output(result)
    assert before == after


def test_help_states_canonical_gate_order_and_every_nonexecution_boundary() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "distinct completed feasibility-pilot aggregate" in normalized
    assert "H01 manual sample-size lock" in normalized
    assert "frozen SAP" in normalized
    assert "confirmatory H02/H03 blind bundle" in normalized
    assert "participant/rating rows" in normalized
    assert "audio, transcripts, restricted D1 data, or raw locators" in normalized
    assert "statistical model" in normalized
    assert "effect estimate" in normalized
    assert "model, GPU, or network operation" in normalized
    assert "generate a statistical report" in normalized
    assert "write an artifact" in normalized
    assert "publish results" in normalized
    assert "claim H04 completion" in normalized
