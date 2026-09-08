from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_h00_human_research_approval.py"
CONFIG = WORKSPACE / "configs/experiments/h00_human_research_approval_v1.yaml"
DECLARED_OUTPUTS = (
    WORKSPACE / "artifacts/human_research/h00/h00_approval_validation_v1.json",
    WORKSPACE
    / "artifacts/experiment_registry/h00-human-research-approval-v1/metadata.json",
    WORKSPACE / "artifacts/human_research/h00/failure.json",
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


def _assert_zero_external_human_runtime_and_output(
    result: dict[str, object],
) -> None:
    for field in (
        "approval_path_resolution_count",
        "approval_read_attempt_count",
        "approval_read_count",
        "approval_deep_validation_count",
        "attestation_path_resolution_count",
        "attestation_read_attempt_count",
        "attestation_read_count",
        "attestation_deep_validation_count",
        "restricted_official_document_locator_resolution_count",
        "restricted_official_document_read_count",
        "environment_read_count",
        "external_access_count",
        "network_access_count",
        "browser_access_count",
        "portal_submission_count",
        "institution_contact_count",
        "official_decision_generation_count",
        "participant_recruitment_count",
        "participant_consent_collection_count",
        "participant_randomization_count",
        "participant_row_read_count",
        "audio_recording_count",
        "audio_read_count",
        "audio_write_count",
        "personal_data_read_count",
        "sensitive_data_read_count",
        "intervention_delivery_count",
        "model_access_count",
        "gpu_access_count",
        "external_or_tts_transfer_count",
        "output_path_resolution_count",
        "output_count",
        "registry_write_count",
        "commit_write_count",
        "publication_count",
    ):
        assert result[field] == 0
    assert result["actual_external_browser_submission_or_contact"] is False
    assert result["actual_official_determination_or_approval"] is False
    assert result["actual_participant_recruitment_consent_or_randomization"] is False
    assert result["actual_audio_personal_or_sensitive_data_access"] is False
    assert result["actual_intervention_model_gpu_or_tts_execution"] is False
    assert result["artifact_created"] is False
    assert result["h00_completion_claim_allowed"] is False
    assert result["publication_allowed"] is False


def test_config_only_reads_7_static_sources_and_nothing_external() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run("--config-only")
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 7
    assert result["source_pin_read_count"] == 7
    assert result["source_toctou_verification_count"] == 0
    _assert_zero_external_human_runtime_and_output(result)
    assert before == after


def test_full_preflight_stops_before_tbd_approval_path_resolution() -> None:
    before = tuple(path.exists() for path in DECLARED_OUTPUTS)
    completed = _run()
    after = tuple(path.exists() for path in DECLARED_OUTPUTS)

    assert completed.returncode == 1
    assert completed.stdout == ""
    result = json.loads(completed.stderr)
    assert result["status"] == "failed_closed"
    assert result["stage"] == "h00_approval"
    assert result["error"] == "H00 approval is TBD_H00_APPROVAL"
    assert result["source_pin_read_count"] == 7
    assert result["source_toctou_verification_count"] == 7
    _assert_zero_external_human_runtime_and_output(result)
    assert before == after


def test_help_states_every_nonexecution_and_no_inference_boundary() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())

    assert completed.returncode == 0
    assert "does not infer approval or exemption" in normalized
    assert "browse or submit an institution portal" in normalized
    assert "contact an institution" in normalized
    assert "recruit or consent participants" in normalized
    assert "record/read/write audio" in normalized
    assert "access personal or sensitive data" in normalized
    assert "call a model/GPU/TTS service" in normalized
    assert "write artifacts" in normalized
    assert "claim H00 completion" in normalized
