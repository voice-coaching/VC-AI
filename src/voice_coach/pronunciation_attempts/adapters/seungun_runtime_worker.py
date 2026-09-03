"""Network-isolated worker for the frozen Seungun detector."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import stat
import sys


_PROTOCOL_VERSION = "voice_coach.seungun_runtime_worker.v1"
_DEVICE_PATTERN = re.compile(r"^(?:cpu|cuda(?::[0-9]+)?)$")
_MAXIMUM_EXPECTED_PHONES = 4096


class _WorkerError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _assert_network_isolated() -> None:
    try:
        socket.socket()
    except PermissionError:
        return
    except OSError as error:
        if error.errno == 1:
            return
        raise _WorkerError("network_isolation_not_verified") from error
    raise _WorkerError("network_isolation_not_verified")


def _request(value: object) -> tuple[Path, tuple[str, ...], str]:
    if not isinstance(value, dict) or set(value) != {
        "protocol_version",
        "audio_path",
        "expected_phones",
        "device",
    }:
        raise _WorkerError("seungun_worker_protocol_invalid")
    if value["protocol_version"] != _PROTOCOL_VERSION:
        raise _WorkerError("seungun_worker_protocol_invalid")
    audio_value = value["audio_path"]
    expected_value = value["expected_phones"]
    device = value["device"]
    if not isinstance(audio_value, str) or not os.path.isabs(audio_value):
        raise _WorkerError("seungun_worker_protocol_invalid")
    if (
        not isinstance(expected_value, list)
        or not expected_value
        or len(expected_value) > _MAXIMUM_EXPECTED_PHONES
        or any(
            not isinstance(phone, str) or not phone or len(phone) > 16
            for phone in expected_value
        )
    ):
        raise _WorkerError("seungun_worker_protocol_invalid")
    if not isinstance(device, str) or _DEVICE_PATTERN.fullmatch(device) is None:
        raise _WorkerError("seungun_worker_protocol_invalid")
    audio_path = Path(audio_value)
    try:
        metadata = audio_path.lstat()
    except OSError:
        raise _WorkerError("restricted_audio_unavailable") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or audio_path.is_symlink()
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o400
    ):
        raise _WorkerError("restricted_audio_unavailable")
    return audio_path, tuple(expected_value), device


def _load_detector(*, repository_root: Path, device: str) -> object:
    try:
        from korean_phone_ctc.frozen_detector import FrozenPronunciationDetector
    except ImportError as error:
        raise _WorkerError("seungun_backend_unavailable") from error
    try:
        detector = FrozenPronunciationDetector(
            repository_root=repository_root,
            device=device,
        )
        detector.preload()
        return detector
    except Exception as error:
        raise _WorkerError("seungun_backend_unavailable") from error


def _analyze_detector(
    detector: object, *, audio_path: Path, expected_phones: tuple[str, ...]
) -> dict[str, object]:
    try:
        analysis = detector.analyze(  # type: ignore[attr-defined]
            audio_path=audio_path,
            expected_phones=expected_phones,
        )
        document = analysis.to_json_object()
    except Exception as error:
        raise _WorkerError("seungun_detector_failed") from error
    if not isinstance(document, dict):
        raise _WorkerError("seungun_detector_failed")
    return document


def _run(value: object) -> dict[str, object]:
    _assert_network_isolated()
    audio_path, expected_phones, device = _request(value)
    repository_root = Path(__file__).resolve().parents[4]
    detector = _load_detector(repository_root=repository_root, device=device)
    document = _analyze_detector(
        detector,
        audio_path=audio_path,
        expected_phones=expected_phones,
    )
    return {
        "protocol_version": _PROTOCOL_VERSION,
        "status": "completed",
        "network_isolation": "seccomp_socket_denied",
        "analysis": document,
    }


def _emit(value: dict[str, object]) -> None:
    sys.stdout.write(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def main() -> int:
    try:
        response = _run(json.load(sys.stdin))
    except _WorkerError as error:
        _emit(
            {
                "protocol_version": _PROTOCOL_VERSION,
                "status": "failed",
                "reason_code": error.reason_code,
            }
        )
        return 2
    except Exception:
        _emit(
            {
                "protocol_version": _PROTOCOL_VERSION,
                "status": "failed",
                "reason_code": "seungun_worker_unexpected_failure",
            }
        )
        return 2
    _emit(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
