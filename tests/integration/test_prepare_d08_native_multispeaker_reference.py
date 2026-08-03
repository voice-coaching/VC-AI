from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_d08_native_multispeaker_reference.py"
CONFIG = WORKSPACE / "configs/experiments/d08_native_multispeaker_reference_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE / "data/restricted/references/d08/d08_recording_metadata_v1.json",
    WORKSPACE / "data/processed/references/d08/d08_qc_report_v1.json",
    WORKSPACE / "artifacts/external_evaluation/d08/d08_completion_v1.json",
    WORKSPACE / "artifacts/external_evaluation/d08/d08_completion_v1.commit.json",
    WORKSPACE / "artifacts/external_evaluation/d08/failure.json",
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


def _assert_runtime_zero(result: dict[str, object]) -> None:
    for field in (
        "h00_path_resolution_count",
        "h00_read_attempt_count",
        "h00_read_count",
        "individual_consent_path_resolution_count",
        "individual_consent_read_attempt_count",
        "individual_consent_read_count",
        "recording_rights_path_resolution_count",
        "recording_rights_read_attempt_count",
        "recording_rights_read_count",
        "qc_preregistration_path_resolution_count",
        "qc_preregistration_read_attempt_count",
        "qc_preregistration_read_count",
        "recruitment_count",
        "consent_collection_count",
        "recording_data_path_resolution_count",
        "metadata_manifest_read_count",
        "metadata_manifest_write_count",
        "microphone_access_count",
        "audio_read_count",
        "audio_write_count",
        "recording_session_count",
        "recording_take_count",
        "model_access_count",
        "network_access_count",
        "gpu_access_count",
        "qc_report_read_count",
        "qc_report_write_count",
        "qc_metric_computation_count",
        "qc_human_listening_count",
        "qc_adjudication_count",
        "completion_read_count",
        "completion_write_count",
        "publication_count",
        "commit_count",
        "output_path_resolution_count",
        "output_count",
    ):
        assert result[field] == 0, field


def test_config_only_reads_only_static_sources() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 19
    assert result["source_pin_read_count"] == 19
    assert result["source_pin_verify_count"] == 0
    assert result["r00_deep_validation_count"] == 0
    assert result["planning_target_speakers_per_sentence"] == 4
    assert result["binding_speaker_floor"] == "TBD_PREREGISTRATION"
    _assert_runtime_zero(result)
    assert before == after


def test_full_deep_validates_r00_then_stops_before_h00_path() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "h00_institutional_approval"
    assert result["error"] == ("H00 institutional approval is TBD_EXTERNAL_ATTESTATION")
    assert result["source_pin_read_count"] == 19
    assert result["source_pin_verify_count"] == 19
    assert result["r00_validator_load_count"] == 1
    assert result["r00_deep_validation_count"] == 1
    assert result["r00_public_or_aggregate_evidence_read_count"] == 33
    assert result["r00_raw_audio_read_count"] == 0
    assert result["r00_row_level_read_count"] == 0
    _assert_runtime_zero(result)
    assert result["actual_recruitment_completed"] is False
    assert result["actual_consent_completed"] is False
    assert result["actual_recording_completed"] is False
    assert result["actual_qc_completed"] is False
    assert result["d08_completion_claim_allowed"] is False
    assert before == after


def test_help_discloses_zero_runtime_and_output_scope() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "does not recruit speakers" in normalized
    assert "access a microphone" in normalized
    assert "network/GPU" in normalized
    assert "publish anything" in normalized
