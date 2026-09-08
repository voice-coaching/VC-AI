"""Network-isolated closed-beta worker for full lip candidate landmarks.

The released four-coordinate worker remains byte-for-byte unchanged because
existing P6 evidence pins that source file.  This separate protocol emits the
40 candidate points used by the frozen P6 mapping and is accepted only after a
host-side parity check against the released worker output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from voice_coach.visual_feedback.models.p6_geometry_schema import (
    MEDIAPIPE_LIP_CANDIDATE_INDICES,
)

from .mediapipe_landmark_worker import (
    _WorkerError,
    _coordinate,
    _decode_frames,
    _deny_network_syscalls,
    _read_model_bytes,
    _require_options,
)


PROTOCOL_VERSION = "voice_coach.closed_beta_lip_trajectory_worker.v1"
BACKEND_REVISION = "mediapipe_1.0.1_closed_beta_40point_video_v1"


def _worker_source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


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
                record: dict[str, object] = {
                    "timestamp_ms": timestamp_ms,
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
                output_frames.append(record)
    except _WorkerError:
        raise
    except Exception as error:
        raise _WorkerError("lip_landmark_inference_failed") from error

    return {
        "protocol_version": PROTOCOL_VERSION,
        "status": "completed",
        "backend_revision": BACKEND_REVISION,
        "worker_sha256": _worker_source_sha256(),
        "model_sha256": request["expected_model_sha256"],
        "network_isolation": "seccomp_socket_denied",
        "candidate_indices": list(MEDIAPIPE_LIP_CANDIDATE_INDICES),
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
        response = _run(json.load(sys.stdin))
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


__all__ = ["BACKEND_REVISION", "PROTOCOL_VERSION"]
