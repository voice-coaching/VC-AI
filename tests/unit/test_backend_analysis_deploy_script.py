from __future__ import annotations

import os
from pathlib import Path
import subprocess


_RELEASE = "a" * 40


def test_worker_deploy_activates_a_revision_labeled_healthy_image(
    tmp_path: Path,
) -> None:
    completed, log = _run_deploy(tmp_path, current=False, fail_candidate=False)

    assert completed.returncode == 0
    assert f"Worker release {_RELEASE} is healthy." in completed.stdout
    assert "|build " in log
    assert f"intelligentai/backend-analysis-worker:{_RELEASE}|compose " in log
    assert " up " in log


def test_worker_deploy_restores_the_previous_image_when_health_wait_fails(
    tmp_path: Path,
) -> None:
    completed, log = _run_deploy(tmp_path, current=True, fail_candidate=True)

    assert completed.returncode != 0
    assert "restoring the previous image" in completed.stderr
    assert "Previous worker image is healthy after rollback." in completed.stderr
    assert f"intelligentai/backend-analysis-worker:{_RELEASE}|compose " in log
    assert (
        f"intelligentai/backend-analysis-worker:rollback-before-{_RELEASE}|compose "
        in log
    )


def test_worker_deploy_refuses_a_mismatched_checked_out_revision(
    tmp_path: Path,
) -> None:
    completed, log = _run_deploy(
        tmp_path,
        current=False,
        fail_candidate=False,
        checked_out_release="b" * 40,
    )

    assert completed.returncode == 2
    assert "does not match the requested release" in completed.stderr
    assert log == ""


def test_worker_rollback_activates_a_revision_labeled_prior_image(
    tmp_path: Path,
) -> None:
    completed, log = _run_deploy(
        tmp_path,
        current=False,
        fail_candidate=False,
        action="rollback",
    )

    assert completed.returncode == 0
    assert f"Worker rollback release {_RELEASE} is healthy." in completed.stdout
    assert "|build " not in log
    assert "|image inspect --format " in log
    assert f" intelligentai/backend-analysis-worker:{_RELEASE}\n" in log
    assert f"intelligentai/backend-analysis-worker:{_RELEASE}|compose " in log


def _run_deploy(
    tmp_path: Path,
    *,
    current: bool,
    fail_candidate: bool,
    checked_out_release: str = _RELEASE,
    action: str = "deploy",
) -> tuple[subprocess.CompletedProcess[str], str]:
    binary_root = tmp_path / "bin"
    binary_root.mkdir()
    log_path = tmp_path / "docker.log"
    docker = binary_root / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s|%s\\n' "${AI_ANALYSIS_WORKER_IMAGE:-}" "$*" >> "${FAKE_DOCKER_LOG}"
if [[ "$1" == "compose" ]]; then
  for argument in "$@"; do
    if [[ "$argument" == "ps" ]]; then
      if [[ "${FAKE_CURRENT:-0}" == "1" ]]; then printf '%s\\n' fake-container; fi
      exit 0
    fi
    if [[ "$argument" == "up" ]]; then
      if [[ "${FAKE_FAIL_CANDIDATE:-0}" == "1" \
        && "${AI_ANALYSIS_WORKER_IMAGE:-}" == *":${FAKE_RELEASE}" ]]; then
        exit 1
      fi
      exit 0
    fi
  done
  exit 0
fi
if [[ "$1" == "inspect" ]]; then
  printf '%s\\n' sha256:previous
  exit 0
fi
if [[ "$1" == "image" && "$2" == "inspect" && "$*" == *"--format"* ]]; then
  printf '%s\\n' "${FAKE_RELEASE}"
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o700)
    git = binary_root / "git"
    git.write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "$*" == *"rev-parse --verify HEAD"* ]]; then
  printf '%s\\n' "${FAKE_CHECKED_OUT_RELEASE}"
  exit 0
fi
if [[ "$*" == *"status --porcelain --untracked-files=normal"* ]]; then
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    git.chmod(0o700)
    environment = {
        **os.environ,
        "PATH": f"{binary_root}:/usr/bin:/bin",
        "FAKE_DOCKER_LOG": str(log_path),
        "FAKE_CURRENT": "1" if current else "0",
        "FAKE_FAIL_CANDIDATE": "1" if fail_candidate else "0",
        "FAKE_RELEASE": _RELEASE,
        "FAKE_CHECKED_OUT_RELEASE": checked_out_release,
        "AI_ANALYSIS_ROLLOUT_GATE": "quiesced-request-v4-result-v3",
        "AI_ANALYSIS_DEPLOY_WAIT_TIMEOUT_SECONDS": "60",
    }
    script = Path("scripts/deploy_backend_analysis_worker.sh").resolve()
    completed = subprocess.run(
        [str(script), action, _RELEASE],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return completed, log
