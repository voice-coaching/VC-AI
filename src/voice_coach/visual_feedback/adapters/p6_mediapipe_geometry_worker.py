"""Calibration-only MediaPipe worker for AI Hub 68 lip geometry.

The process runs behind ``network_isolated_exec`` and installs its own socket
filter before importing OpenCV, NumPy, or MediaPipe.  It returns only the fixed
40-point MediaPipe lip candidate set needed to learn the frozen 24-point map.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any

from ..models.p6_geometry_schema import MEDIAPIPE_LIP_CANDIDATE_INDICES


PROTOCOL_VERSION = "voice_coach.p6_mediapipe_geometry_worker.v1"
BACKEND_REVISION = "mediapipe_1.0.1_face_landmarker_p6_geometry_v1"
_HARD_MAXIMUM_MODEL_BYTES = 64 * 1024 * 1024
_HARD_MAXIMUM_FRAME_COUNT = 128
_HARD_MAXIMUM_JPEG_BYTES = 32 * 1024 * 1024
_SCMP_ACT_ALLOW = 0x7FFF0000
_SCMP_ACT_ERRNO = 0x00050000


class _WorkerError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _deny_network_syscalls() -> None:
    """Install a fail-closed socket filter before loading ML dependencies."""

    try:
        library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    except OSError:
        raise _WorkerError("p6_network_isolation_unavailable") from None
    library.seccomp_init.argtypes = (ctypes.c_uint32,)
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = (ctypes.c_char_p,)
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    )
    library.seccomp_rule_add.restype = ctypes.c_int
    library.seccomp_load.argtypes = (ctypes.c_void_p,)
    library.seccomp_load.restype = ctypes.c_int
    library.seccomp_release.argtypes = (ctypes.c_void_p,)
    library.seccomp_release.restype = None
    context = library.seccomp_init(_SCMP_ACT_ALLOW)
    if not context:
        raise _WorkerError("p6_network_isolation_unavailable")
    try:
        deny_action = _SCMP_ACT_ERRNO | errno.EPERM
        for syscall_name in (
            b"socket",
            b"socketpair",
            b"connect",
            b"accept",
            b"accept4",
            b"bind",
            b"listen",
            b"sendto",
            b"sendmsg",
            b"sendmmsg",
        ):
            syscall_number = library.seccomp_syscall_resolve_name(syscall_name)
            if syscall_number < 0 or library.seccomp_rule_add(
                context, deny_action, syscall_number, 0
            ) != 0:
                raise _WorkerError("p6_network_isolation_unavailable")
        if library.seccomp_load(context) != 0:
            raise _WorkerError("p6_network_isolation_unavailable")
    finally:
        library.seccomp_release(context)


def _read_model(path_value: object, expected_sha256: object) -> bytes:
    if not isinstance(path_value, str) or not path_value:
        raise _WorkerError("p6_landmark_model_unavailable")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise _WorkerError("p6_landmark_model_integrity_failed")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if not hasattr(os, "O_NOFOLLOW"):
        raise _WorkerError("p6_landmark_model_unavailable")
    flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(Path(path_value), flags)
    except OSError:
        raise _WorkerError("p6_landmark_model_unavailable") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 1 <= metadata.st_size <= _HARD_MAXIMUM_MODEL_BYTES
        ):
            raise _WorkerError("p6_landmark_model_unavailable")
        payload = bytearray()
        digest = hashlib.sha256()
        while len(payload) < metadata.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, metadata.st_size - len(payload)))
            if not chunk:
                raise _WorkerError("p6_landmark_model_integrity_failed")
            payload.extend(chunk)
            digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise _WorkerError("p6_landmark_model_integrity_failed")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _require_request(value: object) -> tuple[dict[str, object], list[dict[str, object]]]:
    if not isinstance(value, dict) or set(value) != {
        "protocol_version",
        "model_path",
        "expected_model_sha256",
        "minimum_face_detection_confidence",
        "minimum_face_presence_confidence",
        "maximum_image_dimension",
        "frames",
    }:
        raise _WorkerError("p6_worker_protocol_invalid")
    if value["protocol_version"] != PROTOCOL_VERSION:
        raise _WorkerError("p6_worker_protocol_invalid")
    for key in (
        "minimum_face_detection_confidence",
        "minimum_face_presence_confidence",
    ):
        number = value[key]
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(float(number))
            or not 0 <= float(number) <= 1
        ):
            raise _WorkerError("p6_worker_protocol_invalid")
    maximum_dimension = value["maximum_image_dimension"]
    if (
        isinstance(maximum_dimension, bool)
        or not isinstance(maximum_dimension, int)
        or not 256 <= maximum_dimension <= 1024
    ):
        raise _WorkerError("p6_worker_protocol_invalid")
    frames = value["frames"]
    if (
        not isinstance(frames, list)
        or not 1 <= len(frames) <= _HARD_MAXIMUM_FRAME_COUNT
    ):
        raise _WorkerError("p6_worker_protocol_invalid")
    total_jpeg_bytes = 0
    validated: list[dict[str, object]] = []
    seen: set[str] = set()
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != {
            "sample_sha256",
            "expected_width",
            "expected_height",
            "jpeg_base64",
        }:
            raise _WorkerError("p6_worker_protocol_invalid")
        sample_sha256 = frame["sample_sha256"]
        if (
            not isinstance(sample_sha256, str)
            or len(sample_sha256) != 64
            or any(character not in "0123456789abcdef" for character in sample_sha256)
            or sample_sha256 in seen
        ):
            raise _WorkerError("p6_worker_protocol_invalid")
        seen.add(sample_sha256)
        width = frame["expected_width"]
        height = frame["expected_height"]
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item <= 0
            for item in (width, height)
        ):
            raise _WorkerError("p6_worker_protocol_invalid")
        encoded = frame["jpeg_base64"]
        if not isinstance(encoded, str):
            raise _WorkerError("p6_worker_protocol_invalid")
        try:
            jpeg_bytes = base64.b64decode(encoded, validate=True)
        except (TypeError, ValueError):
            raise _WorkerError("p6_worker_protocol_invalid") from None
        total_jpeg_bytes += len(jpeg_bytes)
        if total_jpeg_bytes > _HARD_MAXIMUM_JPEG_BYTES:
            raise _WorkerError("p6_worker_input_exceeded")
        validated.append(
            {
                "sample_sha256": sample_sha256,
                "expected_width": width,
                "expected_height": height,
                "jpeg_bytes": jpeg_bytes,
            }
        )
    return value, validated


def _coordinate(landmark: Any, axis: str) -> float:
    number = float(getattr(landmark, axis))
    if not math.isfinite(number) or not -0.05 <= number <= 1.05:
        raise _WorkerError("p6_landmark_geometry_invalid")
    return min(1.0, max(0.0, number))


def _run(request_value: object) -> dict[str, object]:
    request, frames = _require_request(request_value)
    model_bytes = _read_model(
        request["model_path"], request["expected_model_sha256"]
    )
    _deny_network_syscalls()
    try:
        import cv2
        import mediapipe as mp
        import numpy as np
    except ImportError:
        raise _WorkerError("p6_landmark_backend_unavailable") from None

    options = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_buffer=model_bytes),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_faces=2,
        min_face_detection_confidence=float(
            request["minimum_face_detection_confidence"]
        ),
        min_face_presence_confidence=float(
            request["minimum_face_presence_confidence"]
        ),
        min_tracking_confidence=0.5,
        output_face_blendshapes=False,
        output_facial_transformation_matrixes=False,
    )
    results: list[dict[str, object]] = []
    maximum_dimension = int(request["maximum_image_dimension"])
    try:
        with mp.tasks.vision.FaceLandmarker.create_from_options(options) as landmarker:
            for frame in frames:
                jpeg_array = np.frombuffer(frame["jpeg_bytes"], dtype=np.uint8)
                bgr = cv2.imdecode(jpeg_array, cv2.IMREAD_COLOR)
                if bgr is None or bgr.ndim != 3 or bgr.shape[2] != 3:
                    raise _WorkerError("p6_jpeg_decode_failed")
                height, width = bgr.shape[:2]
                if (
                    width != frame["expected_width"]
                    or height != frame["expected_height"]
                ):
                    raise _WorkerError("p6_jpeg_dimension_mismatch")
                scale = min(1.0, maximum_dimension / max(width, height))
                if scale < 1.0:
                    bgr = cv2.resize(
                        bgr,
                        (round(width * scale), round(height * scale)),
                        interpolation=cv2.INTER_AREA,
                    )
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                detection = landmarker.detect(image)
                faces = detection.face_landmarks
                record: dict[str, object] = {
                    "sample_sha256": frame["sample_sha256"],
                    "face_count": len(faces),
                    "candidate_points_xy": None,
                }
                if len(faces) == 1:
                    landmarks = faces[0]
                    record["candidate_points_xy"] = [
                        [
                            _coordinate(landmarks[index], "x"),
                            _coordinate(landmarks[index], "y"),
                        ]
                        for index in MEDIAPIPE_LIP_CANDIDATE_INDICES
                    ]
                results.append(record)
    except _WorkerError:
        raise
    except Exception:
        raise _WorkerError("p6_landmark_inference_failed") from None
    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "completed",
        "backend_revision": BACKEND_REVISION,
        "model_sha256": request["expected_model_sha256"],
        "network_isolation": "seccomp_socket_denied",
        "candidate_indices": list(MEDIAPIPE_LIP_CANDIDATE_INDICES),
        "frames": results,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--network-isolation-self-test", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.network_isolation_self_test:
        try:
            _deny_network_syscalls()
            import socket

            socket.socket()
        except PermissionError:
            _emit(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "status": "passed",
                    "network_isolation": "seccomp_socket_denied",
                }
            )
            return 0
        except _WorkerError as error:
            _emit({"status": "failed_closed", "reason_code": error.reason_code})
            return 2
        _emit({"status": "failed_closed", "reason_code": "p6_network_not_denied"})
        return 2
    try:
        request = json.load(sys.stdin)
        response = _run(request)
    except _WorkerError as error:
        _emit({"status": "failed_closed", "reason_code": error.reason_code})
        return 2
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        _emit({"status": "failed_closed", "reason_code": "p6_worker_protocol_invalid"})
        return 2
    _emit(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BACKEND_REVISION",
    "MEDIAPIPE_LIP_CANDIDATE_INDICES",
    "PROTOCOL_VERSION",
]
