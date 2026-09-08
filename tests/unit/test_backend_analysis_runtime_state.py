from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path

import pytest

from voice_coach.backend_analysis.runtime_state import WorkerRuntimeState
from voice_coach.backend_analysis.secret_files import secret_text
from voice_coach.backend_analysis.healthcheck import main as healthcheck_main


def test_runtime_state_contains_only_bounded_operational_fields(tmp_path: Path) -> None:
    path = tmp_path / "worker-state.json"
    state = WorkerRuntimeState(
        path,
        clock=lambda: datetime(2026, 9, 3, tzinfo=UTC),
    )

    state.ready()
    state.polled(2)

    payload = json.loads(path.read_text(encoding="ascii"))
    assert set(payload) == {
        "schemaVersion",
        "pid",
        "status",
        "startedAtUtc",
        "lastPollAtUtc",
        "ackedCount",
        "consecutiveFailures",
        "lastFailureCode",
    }
    assert payload["status"] == "ready"
    assert payload["ackedCount"] == 2
    assert payload["consecutiveFailures"] == 0
    assert path.stat().st_mode & 0o777 == 0o600


def test_runtime_state_marks_failures_without_exception_details(tmp_path: Path) -> None:
    path = tmp_path / "worker-state.json"
    state = WorkerRuntimeState(path)

    state.failed("analysis_stream_poll_failed")

    payload = json.loads(path.read_text(encoding="ascii"))
    assert payload["status"] == "degraded"
    assert payload["lastFailureCode"] == "analysis_stream_poll_failed"
    assert payload["consecutiveFailures"] == 1


def test_healthcheck_accepts_a_recent_ready_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "worker-state.json"
    state = WorkerRuntimeState(path)
    state.ready()
    monkeypatch.setenv("AI_ANALYSIS_WORKER_STATE_PATH", str(path))
    monkeypatch.setenv("AI_ANALYSIS_WORKER_HEALTH_MAX_AGE_SECONDS", "30")

    assert healthcheck_main() == 0

    state.failed("analysis_stream_poll_failed")
    assert healthcheck_main() == 1


def test_secret_file_is_bounded_and_mutually_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "redis-password"
    path.write_text("synthetic-secret\n", encoding="utf-8")

    assert secret_text(
        {"PASSWORD_FILE": str(path)},
        value_name="PASSWORD",
        file_name="PASSWORD_FILE",
        maximum_bytes=100,
    ) == "synthetic-secret"

    with pytest.raises(ValueError, match="deployment_secret_source_invalid"):
        secret_text(
            {"PASSWORD": "direct", "PASSWORD_FILE": str(path)},
            value_name="PASSWORD",
            file_name="PASSWORD_FILE",
            maximum_bytes=100,
        )


def test_secret_file_refuses_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text("synthetic", encoding="utf-8")
    link = tmp_path / "link"
    os.symlink(source, link)

    with pytest.raises(ValueError, match="deployment_secret_source_invalid"):
        secret_text(
            {"PASSWORD_FILE": str(link)},
            value_name="PASSWORD",
            file_name="PASSWORD_FILE",
            maximum_bytes=100,
        )
