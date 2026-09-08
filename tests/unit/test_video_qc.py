"""Contract tests for calibration-pending decoded-video QC candidates."""

from __future__ import annotations

from dataclasses import replace
import json
import math

import pytest

from voice_coach.pronunciation_attempts.adapters.mediapipe_landmark_worker import (
    BACKEND_REVISION,
)
from voice_coach.pronunciation_attempts.adapters.mediapipe_lip_landmarks import (
    MediaPipeFaceFrame,
    MediaPipeLandmarkBatch,
    MediaPipeLipGeometry,
)
from voice_coach.pronunciation_attempts.adapters.video_qc import (
    VideoQcCandidateAdapter,
    VideoQcPolicy,
    VideoQcRuntimeError,
)
from voice_coach.pronunciation_attempts.models import DecodedRgbFrame, Rgb24Sample


_ASSET = "a" * 64
_CONTAINER = "b" * 64
_MODEL = "c" * 64


def _policy(*, maximum_total_rgb_bytes: int = 1_000_000) -> VideoQcPolicy:
    return VideoQcPolicy(
        underexposed_luma_max_8bit=10,
        overexposed_luma_min_8bit=245,
        lip_roi_padding_fraction=0.05,
        minimum_single_face_frames=2,
        maximum_total_rgb_bytes=maximum_total_rgb_bytes,
    )


def _rgb24(grays: tuple[int, ...]) -> bytes:
    return bytes(channel for gray in grays for channel in (gray, gray, gray))


def _sample() -> Rgb24Sample:
    checker = tuple(
        255 if (x + y) % 2 else 0 for y in range(4) for x in range(4)
    )
    gray_frames = (
        (0,) * 16,
        checker,
        (255,) * 16,
        (100,) * 16,
    )
    return Rgb24Sample(
        asset_handle_id=_ASSET,
        container_sha256=_CONTAINER,
        video_stream_index=0,
        frames=tuple(
            DecodedRgbFrame(
                timestamp_us=index * 40_000,
                width=4,
                height=4,
                rgb24=_rgb24(grays),
            )
            for index, grays in enumerate(gray_frames)
        ),
        ffmpeg_revision="ffmpeg_test_v1",
    )


def _geometry(*, shift: float = 0.0) -> MediaPipeLipGeometry:
    return MediaPipeLipGeometry(
        upper_lip_y=0.35,
        lower_lip_y=0.65,
        left_corner_x=0.20 + shift,
        right_corner_x=0.80 + shift,
    )


def _landmarks() -> MediaPipeLandmarkBatch:
    return MediaPipeLandmarkBatch(
        asset_handle_id=_ASSET,
        container_sha256=_CONTAINER,
        backend_revision=BACKEND_REVISION,
        model_sha256=_MODEL,
        network_isolation="seccomp_socket_denied",
        frames=(
            MediaPipeFaceFrame(
                timestamp_ms=0,
                face_count=1,
                lip_geometry=_geometry(),
            ),
            MediaPipeFaceFrame(
                timestamp_ms=40,
                face_count=1,
                lip_geometry=_geometry(shift=0.05),
            ),
            MediaPipeFaceFrame(
                timestamp_ms=80,
                face_count=2,
                lip_geometry=None,
            ),
            MediaPipeFaceFrame(
                timestamp_ms=120,
                face_count=1,
                lip_geometry=_geometry(shift=0.02),
            ),
        ),
    )


def test_synthetic_video_emits_deterministic_uncalibrated_metrics_only() -> None:
    adapter = VideoQcCandidateAdapter(policy=_policy())

    first = adapter.measure(sample=_sample(), landmarks=_landmarks())
    second = adapter.measure(sample=_sample(), landmarks=_landmarks())

    assert first == second
    assert first.status == "candidate_unvalidated"
    assert first.reason_codes == ()
    assert first.sample_timestamps_us == (0, 40_000, 80_000, 120_000)
    assert first.landmark_timestamps_ms == (0, 40, 80, 120)
    assert len(first.decoded_rgb_evidence_sha256) == 64
    assert len(first.landmark_evidence_sha256) == 64
    assert len(first.policy_receipt_sha256) == 64
    assert len(first.provenance_receipt_sha256) == 64
    assert len(first.receipt_sha256) == 64

    metrics = first.metrics
    assert metrics.frame_count == 4
    assert metrics.single_face_frame_count == 3
    assert metrics.multiple_face_frame_count == 1
    assert metrics.single_face_coverage == 0.75
    assert metrics.multiple_face_frame_fraction == 0.25
    assert metrics.mean_luma_8bit == 120.625
    assert metrics.underexposed_pixel_fraction == 0.375
    assert metrics.overexposed_pixel_fraction == 0.375
    assert metrics.mean_neighbor_luma_delta_8bit > 0.0
    assert metrics.lip_roi_frame_count == 3
    assert metrics.lip_roi_mean_luma_8bit is not None
    assert metrics.lip_roi_mean_neighbor_luma_delta_8bit is not None
    assert metrics.track_transition_coverage == pytest.approx(1.0 / 3.0)
    assert metrics.longest_single_face_run_fraction == 0.5
    assert metrics.maximum_lip_center_step_normalized == pytest.approx(0.05)

    serialized = json.dumps(first.to_json_object(), sort_keys=True)
    represented = repr(first)
    assert "rgb24_base64" not in serialized
    assert "rgb24=b" not in represented
    assert '"frames"' not in serialized
    assert bytes(range(16)).hex() not in serialized
    assert first.status not in {"passed", "reliable"}


@pytest.mark.parametrize("mismatch", ["asset", "container", "timestamp"])
def test_exact_asset_container_and_timestamp_lineage_is_required(
    mismatch: str,
) -> None:
    landmarks = _landmarks()
    if mismatch == "asset":
        landmarks = replace(landmarks, asset_handle_id="d" * 64)
    elif mismatch == "container":
        landmarks = replace(landmarks, container_sha256="d" * 64)
    else:
        frames = list(landmarks.frames)
        frames[1] = replace(frames[1], timestamp_ms=41)
        landmarks = replace(landmarks, frames=tuple(frames))

    with pytest.raises(VideoQcRuntimeError) as caught:
        VideoQcCandidateAdapter(policy=_policy()).measure(
            sample=_sample(), landmarks=landmarks
        )

    assert caught.value.reason_code == "video_qc_lineage_mismatch"
    assert "/" not in str(caught.value)


def test_no_face_remains_unavailable_but_preserves_raw_frame_measurements() -> None:
    sample = _sample()
    landmarks = replace(
        _landmarks(),
        frames=tuple(
            MediaPipeFaceFrame(
                timestamp_ms=frame.timestamp_us // 1000,
                face_count=0,
                lip_geometry=None,
            )
            for frame in sample.frames
        ),
    )

    result = VideoQcCandidateAdapter(policy=_policy()).measure(
        sample=sample, landmarks=landmarks
    )

    assert result.status == "unavailable"
    assert result.reason_codes == ("video_qc_single_face_geometry_unavailable",)
    assert result.metrics.single_face_coverage == 0.0
    assert result.metrics.multiple_face_frame_fraction == 0.0
    assert result.metrics.mean_luma_8bit == 120.625
    assert result.metrics.lip_roi_frame_count == 0
    assert result.metrics.lip_roi_mean_luma_8bit is None
    assert result.metrics.maximum_lip_center_step_normalized is None
    assert result.status not in {"passed", "reliable"}


def test_resource_limit_and_nonfinite_policy_fail_closed() -> None:
    with pytest.raises(ValueError):
        replace(_policy(), lip_roi_padding_fraction=math.nan)

    adapter = VideoQcCandidateAdapter(policy=_policy(maximum_total_rgb_bytes=10))
    with pytest.raises(VideoQcRuntimeError) as caught:
        adapter.measure(sample=_sample(), landmarks=_landmarks())
    assert caught.value.reason_code == "video_qc_resource_limit_exceeded"


def test_metric_float_tampering_is_rejected() -> None:
    result = VideoQcCandidateAdapter(policy=_policy()).measure(
        sample=_sample(), landmarks=_landmarks()
    )

    with pytest.raises(ValueError):
        replace(result.metrics, mean_luma_8bit=math.inf)
    with pytest.raises(ValueError):
        replace(result.metrics, single_face_coverage=-0.1)
    with pytest.raises(ValueError):
        replace(result, status="passed")
