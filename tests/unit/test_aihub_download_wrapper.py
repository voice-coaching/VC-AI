"""Regression tests for the credential-safe AI Hub shell wrapper."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "download_aihub_cli.sh"


def _run_with_fake_python(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir(parents=True)
    fake = binary_directory / "python"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "printf 'secure-delegate-argv=%s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    environment = {
        **os.environ,
        "AIHUB_API_KEY": "test-secret-must-not-be-printed",
        "VOICE_COACH_PYTHON": str(fake),
        "PATH": "/usr/bin:/bin",
    }
    return subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "download",
            "68",
            "42813",
            str(tmp_path / "restricted"),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_wrapper_delegates_download_without_forwarding_the_secret(tmp_path: Path) -> None:
    completed = _run_with_fake_python(tmp_path)

    assert completed.returncode == 0
    assert "download_aihub_secure.py 68 42813" in completed.stdout
    assert "test-secret-must-not-be-printed" not in completed.stdout
    assert "test-secret-must-not-be-printed" not in completed.stderr


def test_wrapper_rejects_invalid_file_keys_before_delegation(tmp_path: Path) -> None:
    completed = subprocess.run(
        ["bash", str(SCRIPT), "download", "68", "42813,bad", str(tmp_path)],
        cwd=ROOT,
        env={**os.environ, "AIHUB_API_KEY": "not-printed"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "FILE_KEYS" in completed.stderr
    assert "not-printed" not in completed.stderr
