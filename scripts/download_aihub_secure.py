#!/usr/bin/env python3
"""Resume an approved AI Hub download without placing the API key in argv.

The official shell client forwards ``-aihubapikey`` to a child process.  That
makes the credential observable in ordinary process listings.  This small
wrapper implements the documented HTTP request directly, reads the key only
from ``AIHUB_API_KEY``, preserves interrupted partial files, and emits a
credential-free integrity receipt after the response is complete.

It intentionally downloads but does not extract the returned TAR.  Archive
admission/extraction belongs to the dataset-specific, path-safe adapter.
"""

from __future__ import annotations

import argparse
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_DIGITS = re.compile(r"^[0-9]+$")
_FILE_KEYS = re.compile(r"^[0-9]+(?:,[0-9]+)*$")
_CONTENT_RANGE = re.compile(r"^bytes ([0-9]+)-([0-9]+)/([0-9]+)$")
_CHUNK_BYTES = 8 * 1024 * 1024
_PROGRESS_BYTES = 256 * 1024 * 1024
_SCHEMA_VERSION = "voice_coach.aihub_secure_download_receipt.v1"


class DownloadError(RuntimeError):
    """A public, credential-free download failure."""


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_key")
    parser.add_argument("file_keys")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args()


def _validate_inputs(arguments: argparse.Namespace) -> None:
    if not _DIGITS.fullmatch(arguments.dataset_key):
        raise DownloadError("dataset_key_invalid")
    if not _FILE_KEYS.fullmatch(arguments.file_keys):
        raise DownloadError("file_keys_invalid")
    if arguments.timeout_seconds <= 0:
        raise DownloadError("timeout_invalid")


def _expected_total(
    *, status: int, offset: int, headers: object
) -> tuple[int | None, bool]:
    """Return expected total bytes and whether the response can be appended."""

    content_length_text = headers.get("Content-Length")  # type: ignore[attr-defined]
    try:
        content_length = int(content_length_text) if content_length_text else None
    except ValueError:
        raise DownloadError("response_content_length_invalid") from None

    if status == 206:
        content_range = headers.get("Content-Range")  # type: ignore[attr-defined]
        match = _CONTENT_RANGE.fullmatch(content_range or "")
        if match is None or int(match.group(1)) != offset:
            raise DownloadError("response_content_range_invalid")
        end = int(match.group(2))
        total = int(match.group(3))
        if end < offset or total != end + 1:
            raise DownloadError("response_content_range_invalid")
        if content_length is not None and content_length != total - offset:
            raise DownloadError("response_content_length_mismatch")
        return total, True

    if status == 200:
        # AI Hub currently streams some multi-file TAR responses without a
        # Content-Length.  urllib still validates HTTP chunk framing and raises
        # if that framing is truncated; archive admission performs the second,
        # format-level completeness check before any member is trusted.
        if content_length is None:
            return None, False
        if content_length <= 0:
            raise DownloadError("response_content_length_invalid")
        return content_length, False

    raise DownloadError(f"http_status_{status}")


def _stream_response(
    response: BinaryIO,
    *,
    partial_path: Path,
    append: bool,
    expected_total: int | None,
) -> int:
    mode = "ab" if append else "wb"
    completed = partial_path.stat().st_size if append else 0
    next_progress = ((completed // _PROGRESS_BYTES) + 1) * _PROGRESS_BYTES
    with partial_path.open(mode) as destination:
        os.fchmod(destination.fileno(), 0o600)
        while True:
            chunk = response.read(_CHUNK_BYTES)
            if not chunk:
                break
            destination.write(chunk)
            completed += len(chunk)
            if completed >= next_progress:
                print(f"downloaded_bytes={completed}", flush=True)
                next_progress = (
                    (completed // _PROGRESS_BYTES) + 1
                ) * _PROGRESS_BYTES
        destination.flush()
        os.fsync(destination.fileno())
    if expected_total is not None and completed != expected_total:
        raise DownloadError("response_truncated_partial_preserved")
    return completed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _write_receipt(
    *,
    receipt_path: Path,
    dataset_key: str,
    file_keys: str,
    archive_size_bytes: int,
    archive_sha256: str,
) -> None:
    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "dataset_key": dataset_key,
        "file_keys": file_keys.split(","),
        "archive_size_bytes": archive_size_bytes,
        "archive_sha256": archive_sha256,
        "download_completed_unix_seconds": int(time.time()),
        "credential_persisted": False,
        "archive_extracted": False,
    }
    temporary = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
    payload = json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fchmod(output.fileno(), 0o400)
            os.fsync(output.fileno())
        os.replace(temporary, receipt_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def download(arguments: argparse.Namespace) -> Path:
    _validate_inputs(arguments)
    api_key = os.environ.get("AIHUB_API_KEY", "")
    if not api_key:
        raise DownloadError("aihub_api_key_missing")

    destination = arguments.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)
    archive_path = destination / "download.tar"
    partial_path = destination / "download.tar.partial"
    receipt_path = destination / "download.receipt.json"
    if archive_path.exists() or receipt_path.exists():
        raise DownloadError("completed_download_already_exists")

    offset = partial_path.stat().st_size if partial_path.exists() else 0
    url = (
        "https://api.aihub.or.kr/down/0.6/"
        f"{arguments.dataset_key}.do?fileSn={arguments.file_keys}"
    )
    headers = {"apikey": api_key}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=arguments.timeout_seconds) as response:
            status = int(response.status)
            expected_total, append = _expected_total(
                status=status,
                offset=offset,
                headers=response.headers,
            )
            if offset and not append:
                print("range_not_supported_restarting_from_zero", flush=True)
            completed = _stream_response(
                response,
                partial_path=partial_path,
                append=append,
                expected_total=expected_total,
            )
    except HTTPError as error:
        raise DownloadError(f"http_status_{error.code}") from None
    except (TimeoutError, URLError, HTTPException, OSError):
        raise DownloadError("network_or_storage_failure_partial_preserved") from None
    finally:
        api_key = ""

    os.replace(partial_path, archive_path)
    os.chmod(archive_path, 0o600)
    archive_sha256 = _sha256(archive_path)
    _write_receipt(
        receipt_path=receipt_path,
        dataset_key=arguments.dataset_key,
        file_keys=arguments.file_keys,
        archive_size_bytes=completed,
        archive_sha256=archive_sha256,
    )
    print(f"download_complete bytes={completed} sha256={archive_sha256}")
    return archive_path


def main() -> int:
    try:
        download(_arguments())
    except DownloadError as error:
        print(f"AI Hub download failed: {error}", file=sys.stderr)
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
