"""Long-lived, network-isolated Seungun production v2 process."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID

from korean_phone_ctc.production_pipeline import SeungunProductionPipeline

from .seungun_runtime_server import _separate_protocol_output
from .seungun_runtime_worker import (
    _PROTOCOL_VERSION as _ONE_SHOT_PROTOCOL_VERSION,
    _WorkerError,
    _assert_network_isolated,
    _request,
)


_SERVER_PROTOCOL_VERSION = "voice_coach.seungun_runtime_server.v2"
_DEVICE_PATTERN = re.compile(r"^(?:cpu|cuda(?::[0-9]+)?)$")
_MAXIMUM_REQUEST_BYTES = 65_536


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    arguments = parser.parse_args()
    if _DEVICE_PATTERN.fullmatch(arguments.device) is None:
        raise _WorkerError("seungun_worker_protocol_invalid")
    return arguments


def _emit(value: dict[str, object]) -> None:
    from . import seungun_runtime_server as protocol_output

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    protocol_output._PROTOCOL_OUTPUT.write(payload + "\n")
    protocol_output._PROTOCOL_OUTPUT.flush()


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
        pipeline = SeungunProductionPipeline.from_repository(
            repository_root=repository_root,
            device=arguments.device,
        )
        pipeline.preload()
        _emit(
            {
                "protocol_version": _SERVER_PROTOCOL_VERSION,
                "status": "ready",
                "network_isolation": "seccomp_socket_denied",
                "capabilities": pipeline.capabilities(),
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
            analysis = pipeline.analyze(
                audio_path=audio_path,
                expected_phones=expected_phones,
            )
            _emit(
                {
                    "protocol_version": _SERVER_PROTOCOL_VERSION,
                    "request_id": request_id,
                    "status": "completed",
                    "network_isolation": "seccomp_socket_denied",
                    "analysis": analysis.to_json_object(),
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
