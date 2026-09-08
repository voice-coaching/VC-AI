"""Closed-beta landmark extraction preserves released detector authority."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from voice_coach.pronunciation_attempts.adapters.closed_beta_lip_trajectory import (
    ClosedBetaMediaPipeLipLandmarkAdapter,
    TRAJECTORY_BACKEND_REVISION,
    TRAJECTORY_PROTOCOL_VERSION,
)
from voice_coach.pronunciation_attempts.adapters.mediapipe_landmark_worker import (
    BACKEND_REVISION,
)
from voice_coach.pronunciation_attempts.adapters.mediapipe_lip_landmarks import (
    MediaPipeFaceFrame,
    MediaPipeLandmarkBatch,
    MediaPipeLandmarkPolicy,
    MediaPipeLipGeometry,
)
from voice_coach.pronunciation_attempts.models import DecodedRgbFrame, Rgb24Sample
from voice_coach.visual_feedback.models.p6_geometry_schema import (
    MEDIAPIPE_LIP_CANDIDATE_INDICES,
)


ROOT = Path(__file__).resolve().parents[2]


class _ReleasedDetector:
    def __init__(self, batch: MediaPipeLandmarkBatch) -> None:
        self.batch = batch

    def detect(self, sample: Rgb24Sample) -> MediaPipeLandmarkBatch:
        del sample
        return self.batch


def _policy() -> MediaPipeLandmarkPolicy:
    return MediaPipeLandmarkPolicy(
        minimum_face_detection_confidence=0.5,
        minimum_face_presence_confidence=0.5,
        minimum_tracking_confidence=0.5,
        maximum_frame_count=2,
        maximum_total_rgb_bytes=1024,
        subprocess_timeout_seconds=5.0,
        maximum_subprocess_output_bytes=128 * 1024,
    )


def _sample() -> Rgb24Sample:
    return Rgb24Sample(
        asset_handle_id="a" * 64,
        container_sha256="b" * 64,
        video_stream_index=0,
        frames=(DecodedRgbFrame(100_000, 2, 2, bytes(12)),),
        ffmpeg_revision="ffmpeg_test_v1",
    )


def _candidate_points() -> list[list[float]]:
    points = [[0.5, 0.5] for _ in MEDIAPIPE_LIP_CANDIDATE_INDICES]
    offsets = {
        index: MEDIAPIPE_LIP_CANDIDATE_INDICES.index(index)
        for index in (61, 291, 13, 14)
    }
    points[offsets[61]] = [0.25, 0.50]
    points[offsets[291]] = [0.75, 0.50]
    points[offsets[13]] = [0.50, 0.45]
    points[offsets[14]] = [0.50, 0.55]
    return points


def _released_batch() -> MediaPipeLandmarkBatch:
    return MediaPipeLandmarkBatch(
        asset_handle_id="a" * 64,
        container_sha256="b" * 64,
        backend_revision=BACKEND_REVISION,
        model_sha256="c" * 64,
        network_isolation="seccomp_socket_denied",
        frames=(
            MediaPipeFaceFrame(
                timestamp_ms=100,
                face_count=1,
                lip_geometry=MediaPipeLipGeometry(
                    upper_lip_y=0.45,
                    lower_lip_y=0.55,
                    left_corner_x=0.25,
                    right_corner_x=0.75,
                ),
            ),
        ),
    )


def _worker_response(
    points: list[list[float]],
    *,
    worker_sha256: str,
) -> dict[str, object]:
    return {
        "protocol_version": TRAJECTORY_PROTOCOL_VERSION,
        "status": "completed",
        "backend_revision": TRAJECTORY_BACKEND_REVISION,
        "worker_sha256": worker_sha256,
        "model_sha256": "c" * 64,
        "network_isolation": "seccomp_socket_denied",
        "candidate_indices": list(MEDIAPIPE_LIP_CANDIDATE_INDICES),
        "frames": [
            {
                "timestamp_ms": 100,
                "face_count": 1,
                "candidate_points_xy": points,
            }
        ],
    }


def _adapter() -> ClosedBetaMediaPipeLipLandmarkAdapter:
    adapter = ClosedBetaMediaPipeLipLandmarkAdapter(
        model_path=ROOT / "models/mediapipe/face_landmarker.task",
        expected_model_sha256="c" * 64,
        policy=_policy(),
        repository_root=ROOT,
    )
    adapter._released = _ReleasedDetector(_released_batch())  # noqa: SLF001
    return adapter


def test_accepts_40_points_only_after_released_four_point_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    monkeypatch.setattr(
        adapter,
        "_run_candidate_worker",
        lambda request: _worker_response(
            _candidate_points(),
            worker_sha256=adapter._trajectory_worker_sha256,  # noqa: SLF001
        ),
    )

    batch = adapter.detect(_sample())

    assert len(batch.candidate_frames[0].candidate_points_xy or ()) == 40
    receipt = batch.receipt_json_object()
    beta = receipt["closed_beta_trajectory"]
    assert beta["contains_pronunciation_truth"] is False
    assert beta["contains_action_truth"] is False
    assert "candidate_points_xy" not in json.dumps(beta, sort_keys=True)


def test_candidate_parity_failure_preserves_released_four_point_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    points = _candidate_points()
    points[MEDIAPIPE_LIP_CANDIDATE_INDICES.index(13)][1] = 0.40
    monkeypatch.setattr(
        adapter,
        "_run_candidate_worker",
        lambda request: _worker_response(
            points,
            worker_sha256=adapter._trajectory_worker_sha256,  # noqa: SLF001
        ),
    )

    batch = adapter.detect(_sample())

    assert batch is adapter._released.batch  # noqa: SLF001
    assert batch.__class__ is MediaPipeLandmarkBatch


def test_closed_beta_worker_denies_network_socket() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            (
                "voice_coach.pronunciation_attempts.adapters."
                "closed_beta_lip_trajectory_worker"
            ),
            "--network-isolation-self-test",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "protocol_version": TRAJECTORY_PROTOCOL_VERSION,
        "status": "passed",
        "network_isolation": "seccomp_socket_denied",
    }
