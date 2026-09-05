"""Closed-beta adapter combining released and 40-point MediaPipe evidence.

The released adapter remains the authority for the existing four-coordinate
cue.  A second isolated worker extracts P6 candidate landmarks, and this module
returns them only when both workers agree on face count and the four shared lip
coordinates.  Candidate arrays remain in memory and are represented in lineage
receipts only by a digest.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys

from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.p6_geometry_schema import (
    MEDIAPIPE_LIP_CANDIDATE_INDICES,
)
from voice_coach.visual_feedback.models.p6_lip_trajectory import (
    P6LipCandidateFrame,
)

from ..models import Rgb24Sample
from .bounded_isolated_process import (
    IsolatedProcessError,
    run_bounded_isolated_process,
)
from .closed_beta_lip_trajectory_worker import (
    BACKEND_REVISION as TRAJECTORY_BACKEND_REVISION,
    PROTOCOL_VERSION as TRAJECTORY_PROTOCOL_VERSION,
)
from .mediapipe_lip_landmarks import (
    LipLandmarkRuntimeError,
    MediaPipeLandmarkBatch,
    MediaPipeLandmarkPolicy,
    MediaPipeLipGeometry,
    MediaPipeLipLandmarkAdapter,
)


_SUBPROCESS_ADDRESS_SPACE_BYTES = 4 * 1024 * 1024 * 1024
_PARITY_TOLERANCE = 1e-6
_SHARED_INDICES = (61, 291, 13, 14)
_WORKER_SOURCE_PATH = Path(__file__).with_name(
    "closed_beta_lip_trajectory_worker.py"
)
_WORKER_FAILURE_REASON_CODES = frozenset(
    {
        "landmark_frame_timestamps_invalid",
        "landmark_geometry_degenerate",
        "landmark_geometry_out_of_bounds",
        "landmark_model_integrity_failed",
        "landmark_model_unavailable",
        "landmark_worker_protocol_invalid",
        "landmark_worker_unexpected_failure",
        "lip_landmark_backend_unavailable",
        "lip_landmark_inference_failed",
        "network_isolation_backend_unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class ClosedBetaMediaPipeLandmarkBatch(MediaPipeLandmarkBatch):
    """Released evidence plus an in-memory, digest-bound candidate sequence."""

    trajectory_backend_revision: str
    trajectory_worker_sha256: str
    candidate_indices: tuple[int, ...]
    candidate_frames: tuple[P6LipCandidateFrame, ...]

    def __post_init__(self) -> None:
        super(ClosedBetaMediaPipeLandmarkBatch, self).__post_init__()
        if self.trajectory_backend_revision != TRAJECTORY_BACKEND_REVISION:
            raise ValueError("closed-beta trajectory backend revision differs")
        if (
            len(self.trajectory_worker_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.trajectory_worker_sha256
            )
        ):
            raise ValueError("closed-beta worker digest is invalid")
        if self.candidate_indices != MEDIAPIPE_LIP_CANDIDATE_INDICES:
            raise ValueError("closed-beta candidate schema differs")
        if len(self.candidate_frames) != len(self.frames):
            raise ValueError("closed-beta frame count differs")
        for released, candidate in zip(
            self.frames, self.candidate_frames, strict=True
        ):
            if (
                released.timestamp_ms != candidate.timestamp_ms
                or released.face_count != candidate.face_count
            ):
                raise ValueError("closed-beta frame lineage differs")

    @property
    def trajectory_values_sha256(self) -> str:
        return sha256_document(
            [
                {
                    "timestamp_ms": frame.timestamp_ms,
                    "face_count": frame.face_count,
                    "candidate_points_xy": (
                        None
                        if frame.candidate_points_xy is None
                        else [list(point) for point in frame.candidate_points_xy]
                    ),
                }
                for frame in self.candidate_frames
            ]
        )

    def receipt_json_object(self) -> dict[str, object]:
        base = MediaPipeLandmarkBatch.receipt_json_object(self)
        return {
            **base,
            "schema_version": (
                "voice_coach.closed_beta_mediapipe_landmark_evidence.v1"
            ),
            "closed_beta_trajectory": {
                "backend_revision": self.trajectory_backend_revision,
                "worker_sha256": self.trajectory_worker_sha256,
                "candidate_indices": list(self.candidate_indices),
                "frame_count": len(self.candidate_frames),
                "trajectory_values_sha256": self.trajectory_values_sha256,
                "privacy_contract": (
                    "aggregate_only_no_paths_ids_frames_or_landmark_arrays"
                ),
                "contains_pronunciation_truth": False,
                "contains_action_truth": False,
            },
        }


class ClosedBetaMediaPipeLipLandmarkAdapter:
    """Run released detection and a separate 40-point worker with parity."""

    def __init__(
        self,
        *,
        model_path: str | Path,
        expected_model_sha256: str,
        policy: MediaPipeLandmarkPolicy,
        worker_python: str | Path | None = None,
        repository_root: str | Path | None = None,
    ) -> None:
        self._model_path = Path(model_path)
        self._expected_model_sha256 = expected_model_sha256
        self._policy = policy
        self._worker_python = Path(worker_python or sys.executable).absolute()
        self._repository_root = Path(
            repository_root
            if repository_root is not None
            else Path(__file__).resolve().parents[4]
        )
        try:
            worker_source = _WORKER_SOURCE_PATH.read_bytes()
        except OSError:
            raise LipLandmarkRuntimeError(
                "lip_landmark_backend_unavailable"
            ) from None
        self._trajectory_worker_sha256 = hashlib.sha256(
            worker_source
        ).hexdigest()
        self._released = MediaPipeLipLandmarkAdapter(
            model_path=model_path,
            expected_model_sha256=expected_model_sha256,
            policy=policy,
            worker_python=worker_python,
            repository_root=self._repository_root,
        )

    def detect(self, sample: Rgb24Sample) -> MediaPipeLandmarkBatch:
        released = self._released.detect(sample)
        try:
            candidates = self._detect_candidates(sample)
            self._validate_released_parity(released, candidates)
            return ClosedBetaMediaPipeLandmarkBatch(
                asset_handle_id=released.asset_handle_id,
                container_sha256=released.container_sha256,
                backend_revision=released.backend_revision,
                model_sha256=released.model_sha256,
                network_isolation=released.network_isolation,
                frames=released.frames,
                trajectory_backend_revision=TRAJECTORY_BACKEND_REVISION,
                trajectory_worker_sha256=self._trajectory_worker_sha256,
                candidate_indices=MEDIAPIPE_LIP_CANDIDATE_INDICES,
                candidate_frames=candidates,
            )
        except Exception:
            # Closed beta must not take down the already released four-point
            # visual path.  The sidecar observes the base batch type and emits
            # a stable per-attempt unavailable notice instead.
            return released

    def _detect_candidates(
        self, sample: Rgb24Sample
    ) -> tuple[P6LipCandidateFrame, ...]:
        if len(sample.frames) > self._policy.maximum_frame_count:
            raise LipLandmarkRuntimeError("landmark_frame_limit_exceeded")
        if sum(len(frame.rgb24) for frame in sample.frames) > (
            self._policy.maximum_total_rgb_bytes
        ):
            raise LipLandmarkRuntimeError("landmark_frame_bytes_exceeded")
        request = {
            "protocol_version": TRAJECTORY_PROTOCOL_VERSION,
            "model_path": str(self._model_path),
            "expected_model_sha256": self._expected_model_sha256,
            "options": {
                "maximum_frame_count": self._policy.maximum_frame_count,
                "maximum_total_rgb_bytes": self._policy.maximum_total_rgb_bytes,
                "minimum_face_detection_confidence": (
                    self._policy.minimum_face_detection_confidence
                ),
                "minimum_face_presence_confidence": (
                    self._policy.minimum_face_presence_confidence
                ),
                "minimum_tracking_confidence": (
                    self._policy.minimum_tracking_confidence
                ),
            },
            "frames": [
                {
                    "timestamp_us": frame.timestamp_us,
                    "width": frame.width,
                    "height": frame.height,
                    "rgb24_base64": base64.b64encode(frame.rgb24).decode("ascii"),
                }
                for frame in sample.frames
            ],
        }
        request_bytes = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        response = self._run_candidate_worker(request_bytes)
        return self._parse_candidate_response(sample=sample, response=response)

    def _run_candidate_worker(self, request_bytes: bytes) -> dict[str, object]:
        environment = {
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(self._repository_root / "src"),
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "TF_NUM_INTRAOP_THREADS": "1",
            "TF_NUM_INTEROP_THREADS": "1",
        }
        target = [
            str(self._worker_python),
            "-m",
            (
                "voice_coach.pronunciation_attempts.adapters."
                "closed_beta_lip_trajectory_worker"
            ),
        ]
        try:
            completed = run_bounded_isolated_process(
                launcher_python=sys.executable,
                target=target,
                input_bytes=request_bytes,
                cwd=str(self._repository_root),
                environment=environment,
                timeout_seconds=self._policy.subprocess_timeout_seconds,
                maximum_output_bytes=(
                    self._policy.maximum_subprocess_output_bytes
                ),
                address_space_bytes=_SUBPROCESS_ADDRESS_SPACE_BYTES,
            )
        except IsolatedProcessError as error:
            reason = {
                "isolated_process_timeout": "lip_landmark_worker_timeout",
                "isolated_process_output_exceeded": (
                    "lip_landmark_worker_output_exceeded"
                ),
            }.get(error.reason_code, "lip_landmark_inference_failed")
            raise LipLandmarkRuntimeError(reason) from None
        try:
            response = json.loads(
                completed.stdout.decode("utf-8", errors="strict")
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LipLandmarkRuntimeError(
                "lip_landmark_worker_invalid"
            ) from None
        if not isinstance(response, dict):
            raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
        if completed.return_code != 0 or response.get("status") != "completed":
            reason = response.get("reason_code")
            if reason not in _WORKER_FAILURE_REASON_CODES:
                reason = "lip_landmark_inference_failed"
            raise LipLandmarkRuntimeError(reason)
        return response

    def _parse_candidate_response(
        self,
        *,
        sample: Rgb24Sample,
        response: dict[str, object],
    ) -> tuple[P6LipCandidateFrame, ...]:
        if set(response) != {
            "protocol_version",
            "status",
            "backend_revision",
            "worker_sha256",
            "model_sha256",
            "network_isolation",
            "candidate_indices",
            "frames",
        } or any(
            (
                response["protocol_version"] != TRAJECTORY_PROTOCOL_VERSION,
                response["backend_revision"] != TRAJECTORY_BACKEND_REVISION,
                response["worker_sha256"] != self._trajectory_worker_sha256,
                response["model_sha256"] != self._expected_model_sha256,
                response["network_isolation"] != "seccomp_socket_denied",
                response["candidate_indices"]
                != list(MEDIAPIPE_LIP_CANDIDATE_INDICES),
            )
        ):
            raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
        raw_frames = response["frames"]
        if not isinstance(raw_frames, list) or len(raw_frames) != len(sample.frames):
            raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
        parsed_frames: list[P6LipCandidateFrame] = []
        for raw, decoded in zip(raw_frames, sample.frames, strict=True):
            if not isinstance(raw, dict) or set(raw) != {
                "timestamp_ms",
                "face_count",
                "candidate_points_xy",
            }:
                raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
            face_count = raw["face_count"]
            if (
                raw["timestamp_ms"] != decoded.timestamp_us // 1_000
                or isinstance(face_count, bool)
                or not isinstance(face_count, int)
            ):
                raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
            points_value = raw["candidate_points_xy"]
            points: tuple[tuple[float, float], ...] | None = None
            if points_value is not None:
                if not isinstance(points_value, list):
                    raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
                converted: list[tuple[float, float]] = []
                for point in points_value:
                    if (
                        not isinstance(point, list)
                        or len(point) != 2
                        or any(isinstance(value, bool) for value in point)
                    ):
                        raise LipLandmarkRuntimeError(
                            "lip_landmark_worker_invalid"
                        )
                    try:
                        converted.append((float(point[0]), float(point[1])))
                    except (TypeError, ValueError):
                        raise LipLandmarkRuntimeError(
                            "lip_landmark_worker_invalid"
                        ) from None
                points = tuple(converted)
            try:
                parsed_frames.append(
                    P6LipCandidateFrame(
                        timestamp_ms=int(raw["timestamp_ms"]),
                        face_count=face_count,
                        candidate_points_xy=points,
                    )
                )
            except (TypeError, ValueError) as error:
                raise LipLandmarkRuntimeError(
                    "lip_landmark_worker_invalid"
                ) from error
        return tuple(parsed_frames)

    @staticmethod
    def _validate_released_parity(
        released: MediaPipeLandmarkBatch,
        candidates: tuple[P6LipCandidateFrame, ...],
    ) -> None:
        point_offsets = {
            index: MEDIAPIPE_LIP_CANDIDATE_INDICES.index(index)
            for index in _SHARED_INDICES
        }
        for frame, candidate in zip(released.frames, candidates, strict=True):
            if (
                frame.timestamp_ms != candidate.timestamp_ms
                or frame.face_count != candidate.face_count
            ):
                raise LipLandmarkRuntimeError(
                    "closed_beta_landmark_parity_failed"
                )
            if frame.lip_geometry is None:
                continue
            points = candidate.candidate_points_xy
            if points is None:
                raise LipLandmarkRuntimeError(
                    "closed_beta_landmark_parity_failed"
                )
            left_x, right_x = sorted(
                (
                    points[point_offsets[61]][0],
                    points[point_offsets[291]][0],
                )
            )
            upper_y, lower_y = sorted(
                (
                    points[point_offsets[13]][1],
                    points[point_offsets[14]][1],
                )
            )
            expected = MediaPipeLipGeometry(
                upper_lip_y=upper_y,
                lower_lip_y=lower_y,
                left_corner_x=left_x,
                right_corner_x=right_x,
            )
            if any(
                not math.isclose(
                    getattr(frame.lip_geometry, name),
                    getattr(expected, name),
                    rel_tol=0.0,
                    abs_tol=_PARITY_TOLERANCE,
                )
                for name in (
                    "upper_lip_y",
                    "lower_lip_y",
                    "left_corner_x",
                    "right_corner_x",
                )
            ):
                raise LipLandmarkRuntimeError(
                    "closed_beta_landmark_parity_failed"
                )


__all__ = [
    "ClosedBetaMediaPipeLandmarkBatch",
    "ClosedBetaMediaPipeLipLandmarkAdapter",
]
