"""Long-lived, network-isolated Seungun process with a line-framed protocol."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID

from .seungun_runtime_worker import (
    _PROTOCOL_VERSION as _ONE_SHOT_PROTOCOL_VERSION,
    _WorkerError,
    _analyze_detector,
    _assert_network_isolated,
    _load_detector,
    _request,
)


_SERVER_PROTOCOL_VERSION = "voice_coach.seungun_runtime_server.v1"
_DEVICE_PATTERN = re.compile(r"^(?:cpu|cuda(?::[0-9]+)?)$")
_MAXIMUM_REQUEST_BYTES = 65_536
_PROTOCOL_OUTPUT = sys.stdout


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    arguments = parser.parse_args()
    if _DEVICE_PATTERN.fullmatch(arguments.device) is None:
        raise _WorkerError("seungun_worker_protocol_invalid")
    return arguments


def _emit(value: dict[str, object]) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    _PROTOCOL_OUTPUT.write(payload + "\n")
    _PROTOCOL_OUTPUT.flush()


def _separate_protocol_output() -> None:
    """Keep fd 1 noise from native model libraries out of the JSON protocol."""

    global _PROTOCOL_OUTPUT
    sys.stdout.flush()
    protocol_descriptor = os.dup(sys.stdout.fileno())
    _PROTOCOL_OUTPUT = os.fdopen(
        protocol_descriptor,
        "w",
        encoding="utf-8",
        buffering=1,
        closefd=True,
    )
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())


def _server_request(value: object, *, device: str) -> tuple[str, Path, tuple[str, ...]]:
    if not isinstance(value, dict) or set(value) != {
        "protocol_version",
        "request_id",
        "audio_path",
        "expected_phones",
    }:
        raise _WorkerError("seungun_worker_protocol_invalid")
    if value["protocol_version"] != _SERVER_PROTOCOL_VERSION:
        raise _WorkerError("seungun_worker_protocol_invalid")
    request_id = value["request_id"]
    if not isinstance(request_id, str):
        raise _WorkerError("seungun_worker_protocol_invalid")
    try:
        UUID(request_id)
    except ValueError:
        raise _WorkerError("seungun_worker_protocol_invalid") from None
    audio_path, expected_phones, parsed_device = _request(
        {
            "protocol_version": _ONE_SHOT_PROTOCOL_VERSION,
            "audio_path": value["audio_path"],
            "expected_phones": value["expected_phones"],
            "device": device,
        }
    )
    if parsed_device != device:
        raise _WorkerError("seungun_worker_protocol_invalid")
    return request_id, audio_path, expected_phones


def main() -> int:
    try:
        _separate_protocol_output()
        arguments = _arguments()
        _assert_network_isolated()
        repository_root = Path(__file__).resolve().parents[4]
        detector = _load_detector(
            repository_root=repository_root,
            device=arguments.device,
        )
        _emit(
            {
                "protocol_version": _SERVER_PROTOCOL_VERSION,
                "status": "ready",
                "network_isolation": "seccomp_socket_denied",
            }
        )
    except _WorkerError as error:
        _emit(
            {
                "protocol_version": _SERVER_PROTOCOL_VERSION,
                "status": "failed",
                "reason_code": error.reason_code,
            }
        )
        return 2
    except Exception:
        _emit(
            {
                "protocol_version": _SERVER_PROTOCOL_VERSION,
                "status": "failed",
                "reason_code": "seungun_worker_unexpected_failure",
            }
        )
        return 2

    while True:
        raw = sys.stdin.buffer.readline(_MAXIMUM_REQUEST_BYTES + 1)
        if not raw:
            return 0
        if len(raw) > _MAXIMUM_REQUEST_BYTES or not raw.endswith(b"\n"):
            return 2
        request_id: str | None = None
        try:
            value = json.loads(raw.decode("utf-8", errors="strict"))
            request_id, audio_path, expected_phones = _server_request(
                value,
                device=arguments.device,
            )
            document = _analyze_detector(
                detector,
                audio_path=audio_path,
                expected_phones=expected_phones,
            )
            _emit(
                {
                    "protocol_version": _SERVER_PROTOCOL_VERSION,
                    "request_id": request_id,
                    "status": "completed",
                    "network_isolation": "seccomp_socket_denied",
                    "analysis": document,
                }
            )
        except _WorkerError as error:
            if request_id is None:
                return 2
            _emit(
                {
                    "protocol_version": _SERVER_PROTOCOL_VERSION,
                    "request_id": request_id,
                    "status": "failed",
                    "reason_code": error.reason_code,
                }
            )
        except Exception:
            if request_id is None:
                return 2
            _emit(
                {
                    "protocol_version": _SERVER_PROTOCOL_VERSION,
                    "request_id": request_id,
                    "status": "failed",
                    "reason_code": "seungun_worker_unexpected_failure",
                }
            )


if __name__ == "__main__":
    raise SystemExit(main())
