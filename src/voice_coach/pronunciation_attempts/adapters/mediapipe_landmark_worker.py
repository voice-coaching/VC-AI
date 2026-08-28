"""Network-isolated MediaPipe face-landmark subprocess worker.

This module is an implementation detail of ``mediapipe_lip_landmarks``.  It
accepts bounded RGB24 frames over stdin and emits only minimal lip geometry.
Linux seccomp denies creation of every network socket before MediaPipe is
imported, because current MediaPipe Tasks wheels document utilization-metric
egress even though image processing itself is on-device.
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

PROTOCOL_VERSION = "voice_coach.mediapipe_lip_worker.v1"
BACKEND_REVISION = "mediapipe_1.0.1_face_landmarker_float16_v1"
_HARD_MAXIMUM_MODEL_BYTES = 64 * 1024 * 1024
_HARD_MAXIMUM_FRAME_COUNT = 256
_HARD_MAXIMUM_TOTAL_RGB_BYTES = 256 * 1024 * 1024
_LIP_LEFT_CORNER_INDEX = 61
_LIP_RIGHT_CORNER_INDEX = 291
_LIP_UPPER_INNER_INDEX = 13
_LIP_LOWER_INNER_INDEX = 14

# libseccomp action constants from <seccomp.h>.
_SCMP_ACT_ALLOW = 0x7FFF0000
_SCMP_ACT_ERRNO = 0x00050000


class _WorkerError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _deny_network_syscalls() -> None:
    """Install a fail-closed seccomp filter before importing the ML runtime."""

    try:
        # The outer launcher already denies process creation.  Avoid
        # ctypes.util.find_library(), which may spawn ldconfig/gcc helpers.
        library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    except OSError:
        raise _WorkerError("network_isolation_backend_unavailable")
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
        raise _WorkerError("network_isolation_backend_unavailable")
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
            if syscall_number < 0:
                raise _WorkerError("network_isolation_backend_unavailable")
            if library.seccomp_rule_add(
                context,
                deny_action,
                syscall_number,
                0,
            ) != 0:
                raise _WorkerError("network_isolation_backend_unavailable")
        if library.seccomp_load(context) != 0:
            raise _WorkerError("network_isolation_backend_unavailable")
    finally:
        library.seccomp_release(context)


def _read_model_bytes(path_value: object, expected_sha256: object) -> bytes:
    if not isinstance(path_value, str) or not path_value:
        raise _WorkerError("landmark_model_unavailable")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise _WorkerError("landmark_model_integrity_failed")
    path = Path(path_value)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise _WorkerError("landmark_model_unavailable")
    flags |= nofollow
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise _WorkerError("landmark_model_unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise _WorkerError("landmark_model_unavailable")
        if metadata.st_size <= 0 or metadata.st_size > _HARD_MAXIMUM_MODEL_BYTES:
            raise _WorkerError("landmark_model_unavailable")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise _WorkerError("landmark_model_integrity_failed")
            chunks.append(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.hexdigest() != expected_sha256:
            raise _WorkerError("landmark_model_integrity_failed")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _require_options(value: object) -> dict[str, float | int]:
    if not isinstance(value, dict) or set(value) != {
        "maximum_frame_count",
        "maximum_total_rgb_bytes",
        "minimum_face_detection_confidence",
        "minimum_face_presence_confidence",
        "minimum_tracking_confidence",
    }:
        raise _WorkerError("landmark_worker_protocol_invalid")
    maximum_frames = value["maximum_frame_count"]
    maximum_bytes = value["maximum_total_rgb_bytes"]
    if (
        isinstance(maximum_frames, bool)
        or not isinstance(maximum_frames, int)
        or not 1 <= maximum_frames <= _HARD_MAXIMUM_FRAME_COUNT
    ):
        raise _WorkerError("landmark_worker_protocol_invalid")
    if (
        isinstance(maximum_bytes, bool)
        or not isinstance(maximum_bytes, int)
        or not 1 <= maximum_bytes <= _HARD_MAXIMUM_TOTAL_RGB_BYTES
    ):
        raise _WorkerError("landmark_worker_protocol_invalid")
    result: dict[str, float | int] = {
        "maximum_frame_count": maximum_frames,
        "maximum_total_rgb_bytes": maximum_bytes,
    }
    for name in (
        "minimum_face_detection_confidence",
        "minimum_face_presence_confidence",
        "minimum_tracking_confidence",
    ):
        raw = value[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _WorkerError("landmark_worker_protocol_invalid")
        number = float(raw)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise _WorkerError("landmark_worker_protocol_invalid")
        result[name] = number
    return result


def _decode_frames(
    value: object,
    *,
    maximum_frame_count: int,
    maximum_total_rgb_bytes: int,
) -> list[tuple[int, int, int, bytes]]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum_frame_count:
        raise _WorkerError("landmark_worker_protocol_invalid")
    decoded: list[tuple[int, int, int, bytes]] = []
    previous_timestamp_ms: int | None = None
    total_bytes = 0
    for frame in value:
        if not isinstance(frame, dict) or set(frame) != {
            "timestamp_us",
            "width",
            "height",
            "rgb24_base64",
        }:
            raise _WorkerError("landmark_worker_protocol_invalid")
        timestamp_us = frame["timestamp_us"]
        width = frame["width"]
        height = frame["height"]
        if any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in (timestamp_us, width, height)
        ):
            raise _WorkerError("landmark_worker_protocol_invalid")
        if timestamp_us < 0 or width <= 0 or height <= 0:
            raise _WorkerError("landmark_worker_protocol_invalid")
        timestamp_ms = timestamp_us // 1000
        if previous_timestamp_ms is not None and timestamp_ms <= previous_timestamp_ms:
            raise _WorkerError("landmark_frame_timestamps_invalid")
        previous_timestamp_ms = timestamp_ms
        encoded = frame["rgb24_base64"]
        if not isinstance(encoded, str):
            raise _WorkerError("landmark_worker_protocol_invalid")
        try:
            rgb24 = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise _WorkerError("landmark_worker_protocol_invalid") from error
        if len(rgb24) != width * height * 3:
            raise _WorkerError("landmark_worker_protocol_invalid")
        total_bytes += len(rgb24)
        if total_bytes > maximum_total_rgb_bytes:
            raise _WorkerError("landmark_worker_protocol_invalid")
        decoded.append((timestamp_ms, width, height, rgb24))
    return decoded


def _coordinate(landmark: Any, axis: str) -> float:
    value = float(getattr(landmark, axis))
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise _WorkerError("landmark_geometry_out_of_bounds")
    return value


def _run(request: object) -> dict[str, object]:
    if not isinstance(request, dict) or set(request) != {
        "protocol_version",
        "model_path",
        "expected_model_sha256",
        "options",
        "frames",
    }:
        raise _WorkerError("landmark_worker_protocol_invalid")
    if request["protocol_version"] != PROTOCOL_VERSION:
        raise _WorkerError("landmark_worker_protocol_invalid")
    options = _require_options(request["options"])
    frames = _decode_frames(
        request["frames"],
        maximum_frame_count=int(options["maximum_frame_count"]),
        maximum_total_rgb_bytes=int(options["maximum_total_rgb_bytes"]),
    )
    model_bytes = _read_model_bytes(
        request["model_path"], request["expected_model_sha256"]
    )

    _deny_network_syscalls()
    try:
        import mediapipe as mp
        import numpy as np
    except ImportError as error:
        raise _WorkerError("lip_landmark_backend_unavailable") from error

    try:
        landmarker_options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_buffer=model_bytes),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=2,
            min_face_detection_confidence=float(
                options["minimum_face_detection_confidence"]
            ),
            min_face_presence_confidence=float(
                options["minimum_face_presence_confidence"]
            ),
            min_tracking_confidence=float(options["minimum_tracking_confidence"]),
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        output_frames: list[dict[str, object]] = []
        with mp.tasks.vision.FaceLandmarker.create_from_options(
            landmarker_options
        ) as landmarker:
            for timestamp_ms, width, height, rgb24 in frames:
                image_array = np.frombuffer(rgb24, dtype=np.uint8).reshape(
                    (height, width, 3)
                )
                image = mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=image_array,
                )
                detection = landmarker.detect_for_video(image, timestamp_ms)
                faces = detection.face_landmarks
                face_count = len(faces)
                record: dict[str, object] = {
                    "timestamp_ms": timestamp_ms,
                    "face_count": face_count,
                    "lip_geometry": None,
                }
                if face_count == 1:
                    landmarks = faces[0]
                    left_x = _coordinate(landmarks[_LIP_LEFT_CORNER_INDEX], "x")
                    right_x = _coordinate(landmarks[_LIP_RIGHT_CORNER_INDEX], "x")
                    upper_y = _coordinate(landmarks[_LIP_UPPER_INNER_INDEX], "y")
                    lower_y = _coordinate(landmarks[_LIP_LOWER_INNER_INDEX], "y")
                    lip_left_x, lip_right_x = sorted((left_x, right_x))
                    lip_upper_y, lip_lower_y = sorted((upper_y, lower_y))
                    if lip_right_x <= lip_left_x:
                        raise _WorkerError("landmark_geometry_degenerate")
                    record["lip_geometry"] = {
                        "upper_lip_y": lip_upper_y,
                        "lower_lip_y": lip_lower_y,
                        "left_corner_x": lip_left_x,
                        "right_corner_x": lip_right_x,
                    }
                output_frames.append(record)
    except _WorkerError:
        raise
    except Exception as error:
        raise _WorkerError("lip_landmark_inference_failed") from error

    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "completed",
        "backend_revision": BACKEND_REVISION,
        "model_sha256": request["expected_model_sha256"],
        "network_isolation": "seccomp_socket_denied",
        "frames": output_frames,
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
    args = parser.parse_args(argv)
    if args.network_isolation_self_test:
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
        except Exception:
            _emit(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "status": "failed",
                    "reason_code": "network_isolation_self_test_failed",
                }
            )
            return 2
        _emit(
            {
                "protocol_version": PROTOCOL_VERSION,
                "status": "failed",
                "reason_code": "network_isolation_not_enforced",
            }
        )
        return 2

    try:
        request = json.load(sys.stdin)
        response = _run(request)
    except _WorkerError as error:
        _emit(
            {
                "protocol_version": PROTOCOL_VERSION,
                "status": "failed",
                "reason_code": error.reason_code,
            }
        )
        return 2
    except Exception:
        _emit(
            {
                "protocol_version": PROTOCOL_VERSION,
                "status": "failed",
                "reason_code": "landmark_worker_unexpected_failure",
            }
        )
        return 2
    _emit(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
