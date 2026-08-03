from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts/prepare_d09_h02_h03_longitudinal_collection.py"
CONFIG = WORKSPACE / "configs/experiments/d09_h02_h03_longitudinal_collection_v1.yaml"


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


def _assert_runtime_zero(payload: dict[str, object]) -> None:
    for field in (
        "portal_or_institution_contact_count",
        "recruitment_count",
        "consent_count",
        "participant_read_count",
        "session_read_count",
        "microphone_access_count",
        "audio_read_count",
        "audio_write_count",
        "qc_execution_count",
        "randomization_count",
        "model_access_count",
        "gpu_access_count",
        "api_invocation_count",
        "network_access_count",
        "coaching_delivery_count",
        "reference_delivery_count",
        "intervention_delivery_count",
        "blind_rating_count",
        "metric_computation_count",
        "statistics_computation_count",
        "effect_estimation_count",
        "restricted_d1_data_read_count",
        "pseudonym_key_read_count",
        "output_path_resolution_count",
        "output_count",
        "registry_write_count",
        "commit_write_count",
        "publication_count",
    ):
        assert payload[field] == 0
    for field in (
        "actual_portal_or_institution_contact",
        "actual_recruitment_or_consent",
        "actual_participant_session_audio_or_d1_access",
        "actual_randomization_or_intervention",
        "actual_model_gpu_api_or_network_access",
        "actual_coaching_reference_or_blind_rating",
        "actual_metric_statistics_or_effect",
        "artifact_created",
        "completion_or_publication_claim_allowed",
    ):
        assert payload[field] is False


def test_config_only_validates_46_sources_and_no_runtime() -> None:
    completed = _run("--config-only", "--phase", "confirmatory")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "valid_config_only"
    assert payload["source_pin_count"] == 46
    assert payload["source_pin_read_count"] == 46
    assert payload["requested_phase"] == "confirmatory"
    _assert_runtime_zero(payload)


def test_feasibility_stops_at_h00_before_any_evidence_path() -> None:
    completed = _run("--phase", "feasibility")
    assert completed.returncode == 1
    assert completed.stdout == ""
    payload = json.loads(completed.stderr)
    assert payload["status"] == "failed_closed"
    assert payload["stage"] == "h00_authorization"
    assert "actual H00 approval" in payload["error"]
    assert payload["source_pin_read_count"] == 46
    assert payload["source_toctou_verification_count"] == 46
    assert payload["h00_approval_path_resolution_count"] == 0
    assert payload["delivery_receipt_path_resolution_count"] == 0
    _assert_runtime_zero(payload)


def test_confirmatory_also_stops_at_h00_before_h04_or_delivery() -> None:
    completed = _run("--phase", "confirmatory")
    assert completed.returncode == 1
    payload = json.loads(completed.stderr)
    assert payload["stage"] == "h00_authorization"
    assert payload["h04_config_read_count"] == 0
    assert payload["feasibility_completion_path_resolution_count"] == 0
    assert payload["h01_lock_path_resolution_count"] == 0
    assert payload["sap_path_resolution_count"] == 0
    assert payload["predecessor_artifact_path_resolution_count"] == 0
    _assert_runtime_zero(payload)


def test_help_states_phase_separation_and_nonexecution_boundaries() -> None:
    completed = _run("--help")
    normalized = " ".join(completed.stdout.split())
    assert completed.returncode == 0
    assert "15-20-per-arm" in normalized
    assert "efficacy pilot" in normalized
    assert "manual H01 final-N lock" in normalized
    assert "frozen SAP" in normalized
    assert "recruitment, consent" in normalized
    assert "participant/session/audio access" in normalized
    assert "randomization" in normalized
    assert "model/API/GPU access" in normalized
    assert "coaching/reference delivery" in normalized
    assert "metric/statistics/effect analysis" in normalized
    assert "output, or publication" in normalized
