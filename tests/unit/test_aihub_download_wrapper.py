"""Regression tests for the credential-safe AI Hub shell wrapper."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "download_aihub_cli.sh"
AWAIT_SCRIPT = ROOT / "scripts" / "await_aihub_approval.sh"
POWERSHELL_L2_SCRIPT = ROOT / "scripts" / "download_aihub_l2_files.ps1"
POWERSHELL_FILE_SCRIPT = ROOT / "scripts" / "download_aihub_file_resumable.ps1"
POWERSHELL_GUARD_SCRIPT = ROOT / "scripts" / "aihub538_p8_guard.ps1"
_P8_RESERVED_FILE_KEYS = (
    "55080", "55091", "55102", "55106", "55107", "55108", "55109", "55110",
    "55111", "55081", "55082", "55083", "55084", "55085", "55086", "55087",
    "55088", "55089", "55090", "55092", "55093", "55094", "55095", "55096",
    "55097", "55098", "55099", "55100", "55101", "55103", "55104", "55105",
    "55112", "55123", "55134", "55138", "55139", "55140", "55141", "55142",
    "55143", "55113", "55114", "55115", "55116", "55117", "55118", "55119",
    "55120", "55121", "55122", "55124", "55125", "55126", "55127", "55128",
    "55129", "55130", "55131", "55132", "55133", "55135", "55136", "55137",
)


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


def test_generic_bash_wrapper_prevents_all_p8_keys_without_side_effects(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "must-not-exist"
    completed = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "download",
            "68",
            ",".join(_P8_RESERVED_FILE_KEYS),
            str(destination),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "AIHUB_API_KEY": "must-not-be-read-or-printed",
            "VOICE_COACH_PYTHON": "/bin/false",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 65
    assert "requires_typed_entrypoint" in completed.stderr
    assert "must-not-be-read-or-printed" not in completed.stderr
    assert not destination.exists()


def test_approval_watcher_guards_p8_before_env_file_or_log_access() -> None:
    destination = ROOT / "data/restricted/aihub/dataset-539"
    status_path = destination / "approval-watcher.log"
    before = status_path.stat() if status_path.exists() else None
    completed = subprocess.run(
        ["bash", str(AWAIT_SCRIPT)],
        cwd=ROOT,
        env={
            **os.environ,
            "DATASET_KEY": "539",
            "FILE_KEYS": ",".join(_P8_RESERVED_FILE_KEYS),
            "VOICE_COACH_PYTHON": "/bin/false",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 65
    assert "requires_typed_entrypoint" in completed.stderr
    after = status_path.stat() if status_path.exists() else None
    assert before == after


def test_powershell_generic_entrypoints_reserve_p8_keys_globally() -> None:
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is not installed")

    all_keys = ",".join(_P8_RESERVED_FILE_KEYS)
    l2 = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(POWERSHELL_L2_SCRIPT),
            "-DatasetKey",
            "68",
            "-FileKeys",
            all_keys,
        ],
        cwd=ROOT,
        env={key: value for key, value in os.environ.items() if key != "AIHUB_API_KEY"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    single = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(POWERSHELL_FILE_SCRIPT),
            "-DatasetKey",
            "539",
            "-FileKey",
            _P8_RESERVED_FILE_KEYS[0],
            "-ExpectedFileName",
            "must-not-be-created.wav",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    alias = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(POWERSHELL_L2_SCRIPT),
            "-DatasetKey",
            "68",
            "-FileKeys",
            "055105",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert l2.returncode != 0
    assert "typed one-shot" in l2.stderr and "entrypoint" in l2.stderr
    assert single.returncode != 0
    assert "typed one-shot" in single.stderr and "entrypoint" in single.stderr
    assert alias.returncode != 0
    assert "canonical positive-decimal" in alias.stderr


def test_powershell_guard_contains_independently_reviewed_64_key_set() -> None:
    source = POWERSHELL_GUARD_SCRIPT.read_text(encoding="utf-8")
    observed = tuple(re.findall(r"'([1-9][0-9]*)'", source))
    assert observed == _P8_RESERVED_FILE_KEYS
