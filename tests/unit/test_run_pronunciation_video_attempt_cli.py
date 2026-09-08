"""Public-boundary tests for the internal local video CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_pronunciation_video_attempt.py"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_cli_denies_before_source_io_without_explicit_consent(tmp_path: Path) -> None:
    private_locator = tmp_path / "private-participant-video.mp4"

    completed = _run(
        "--video",
        str(private_locator),
        "--prompt-id",
        "R00-01",
    )

    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "status": "failed_closed",
        "reason_codes": ["explicit_local_processing_consent_required"],
    }
    assert not private_locator.exists()
    assert str(private_locator) not in completed.stdout
    assert str(private_locator) not in completed.stderr


def test_cli_missing_source_is_path_free_after_consent(tmp_path: Path) -> None:
    private_locator = tmp_path / "missing-private-participant-video.mp4"

    completed = _run(
        "--video",
        str(private_locator),
        "--prompt-id",
        "R00-01",
        "--consent-local-processing",
    )

    assert completed.returncode == 3
    response = json.loads(completed.stdout)
    assert response["status"] == "failed_closed"
    assert response["reason_codes"] == ["source_open_failed"]
    assert response["audio_result"] is None
    assert response["visual_supplement"] is None
    assert response["cleanup_status"] == "not_applicable"
    assert response["closed_beta_lip_trajectory"] == {
        "status": "not_configured",
        "attempt_status": "not_run",
        "geometry_artifact_sha256": None,
        "reason_codes": [],
        "contains_pronunciation_truth": False,
        "contains_action_truth": False,
    }
    assert str(private_locator) not in completed.stdout
    assert str(private_locator) not in completed.stderr


def test_cli_preopened_fd_still_requires_consent_before_duplication() -> None:
    completed = _run(
        "--video-fd",
        "987654",
        "--prompt-id",
        "R00-01",
    )

    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "status": "failed_closed",
        "reason_codes": ["explicit_local_processing_consent_required"],
    }


def test_cli_invalid_preopened_fd_is_path_free_after_consent() -> None:
    completed = _run(
        "--video-fd",
        "987654",
        "--prompt-id",
        "R00-01",
        "--consent-local-processing",
    )

    assert completed.returncode == 3
    response = json.loads(completed.stdout)
    assert response["status"] == "failed_closed"
    assert response["reason_codes"] == ["source_open_failed"]
    assert "987654" not in completed.stdout
    assert "987654" not in completed.stderr


def test_cli_rejects_unpacked_visual_runtime_options() -> None:
    completed = _run(
        "--video-fd",
        "987654",
        "--prompt-id",
        "R00-01",
        "--consent-local-processing",
        "--visual-runtime-bundle",
        "missing-runtime-bundle.json",
    )

    assert completed.returncode == 2
    assert "unrecognized arguments: --visual-runtime-bundle" in completed.stderr


def test_cli_rejects_partial_clova_configuration_before_source_io() -> None:
    completed = _run(
        "--video-fd",
        "987654",
        "--prompt-id",
        "R00-01",
        "--consent-local-processing",
        "--advice-endpoint",
        "http://127.0.0.1:8000/v1",
    )

    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "status": "failed_closed",
        "reason_codes": ["advice_generator_configuration_invalid"],
    }
    assert "987654" not in completed.stdout
    assert "987654" not in completed.stderr


def test_cli_help_exposes_only_user_authority_inputs() -> None:
    completed = _run("--help")

    assert completed.returncode == 0
    assert "--video" in completed.stdout
    assert "--video-fd" in completed.stdout
    assert "--prompt-id" in completed.stdout
    assert "--consent-local-processing" in completed.stdout
    assert "--production-visual-release" in completed.stdout
    assert "--closed-beta-lip-geometry-artifact" in completed.stdout
    assert "--advice-endpoint" in completed.stdout
    assert "--advice-revision" in completed.stdout
    assert "expected-phone" not in completed.stdout
    assert "binding-receipt" not in completed.stdout
    assert "reliability-receipt" not in completed.stdout
    assert "visual-runtime-bundle" not in completed.stdout
    assert "visual-runtime-eligibility" not in completed.stdout
    assert "--visual-release" not in completed.stdout
