"""Tests for the credential-safe resumable AI Hub downloader."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "download_aihub_secure.py"
SPEC = importlib.util.spec_from_file_location("download_aihub_secure", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_response_range_requires_exact_resume_offset() -> None:
    headers = {
        "Content-Length": "10",
        "Content-Range": "bytes 5-14/15",
    }
    assert MODULE._expected_total(status=206, offset=5, headers=headers) == (
        15,
        True,
    )

    with pytest.raises(MODULE.DownloadError, match="content_range"):
        MODULE._expected_total(status=206, offset=4, headers=headers)


def test_full_response_restarts_instead_of_appending() -> None:
    assert MODULE._expected_total(
        status=200,
        offset=99,
        headers={"Content-Length": "123"},
    ) == (123, False)

    assert MODULE._expected_total(
        status=200,
        offset=99,
        headers={},
    ) == (None, False)


def test_cli_source_does_not_forward_secret_as_argument() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "import subprocess" not in source
    assert 'os.environ.get("AIHUB_API_KEY"' in source
    assert "headers = {\"apikey\": api_key}" in source


def test_completed_receipt_is_read_only_and_contains_no_credential(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "download.receipt.json"

    MODULE._write_receipt(
        receipt_path=receipt_path,
        dataset_key="538",
        file_keys="54725,54944",
        archive_size_bytes=123,
        archive_sha256="a" * 64,
    )

    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o400
    document = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert document["dataset_key"] == "538"
    assert document["file_keys"] == ["54725", "54944"]
    assert document["credential_persisted"] is False
    assert "apikey" not in receipt_path.read_text(encoding="utf-8").lower()
