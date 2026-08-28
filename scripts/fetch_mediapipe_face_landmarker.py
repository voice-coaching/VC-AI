#!/usr/bin/env python
"""Fetch the pinned official MediaPipe face-landmarker model bundle.

The runtime never downloads models implicitly.  This explicit setup command
uses the versioned Google model URL, bounds the response, verifies SHA-256, and
atomically installs a mode-0600 file at a caller-selected local path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
MAXIMUM_MODEL_BYTES = 8 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_model(*, output: Path, replace: bool, timeout_seconds: float) -> dict[str, object]:
    """Download and verify the pinned artifact without exposing local paths."""

    if not output.is_absolute():
        raise ValueError("output must be an absolute path")
    if output.is_symlink():
        raise ValueError("output must not be a symlink")
    if not output.parent.is_dir():
        raise ValueError("output parent directory does not exist")
    if output.exists():
        if not output.is_file():
            raise ValueError("output exists and is not a regular file")
        if _sha256(output) == MODEL_SHA256:
            return {
                "status": "already_present",
                "model_sha256": MODEL_SHA256,
                "byte_count": output.stat().st_size,
            }
        if not replace:
            raise ValueError("output exists with a different digest; pass --replace")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".face-landmarker-",
            suffix=".partial",
            dir=output.parent,
            delete=False,
        ) as destination:
            temporary_path = Path(destination.name)
            digest = hashlib.sha256()
            byte_count = 0
            request = urllib.request.Request(  # noqa: S310 - fixed HTTPS origin
                MODEL_URL,
                headers={"User-Agent": "voice-coach-model-fetch/1"},
            )
            with urllib.request.urlopen(  # noqa: S310 - fixed HTTPS origin
                request,
                timeout=timeout_seconds,
            ) as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    byte_count += len(chunk)
                    if byte_count > MAXIMUM_MODEL_BYTES:
                        raise ValueError("model response exceeded the byte limit")
                    digest.update(chunk)
                    destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if byte_count == 0 or digest.hexdigest() != MODEL_SHA256:
            raise ValueError("downloaded model digest differs from the pin")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, output)
        temporary_path = None
        return {
            "status": "installed",
            "model_sha256": MODEL_SHA256,
            "byte_count": byte_count,
        }
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = fetch_model(
            output=args.output,
            replace=args.replace,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(
            json.dumps(
                {"status": "failed", "reason": type(error).__name__},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
