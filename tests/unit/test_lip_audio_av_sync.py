from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import hashlib
import math
import struct

import pytest

from voice_coach.pronunciation_attempts.adapters.lip_audio_av_sync import (
    AvSyncRuntimeError,
    LipAudioAvSyncAdapter,
    LipAudioAvSyncPolicy,
)
from voice_coach.pronunciation_attempts.adapters.mediapipe_lip_landmarks import (
    MediaPipeFaceFrame,
    MediaPipeLandmarkBatch,
    MediaPipeLipGeometry,
)
from voice_coach.pronunciation_attempts.models import (
    AudioDerivationReceipt,
    AudioStreamProbe,
    MediaProbe,
    RestrictedPcmAudio,
    VideoStreamProbe,
)


_HANDLE = "a" * 64
_CONTAINER = "b" * 64
_MODEL = "c" * 64
_APERTURES = (0.10, 0.22, 0.75, 0.35, 0.90, 0.28, 0.62, 0.15)
_TIMESTAMPS_MS = (100, 200, 300, 400, 500, 600, 700, 800)


class _Store:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    @contextmanager
    def open_derived_audio(self, audio: RestrictedPcmAudio):  # noqa: ANN201
        del audio
        yield BytesIO(self.payload)


def _pcm() -> bytes:
    samples: list[int] = []
    for sample_index in range(16_000):
        time_ms = sample_index / 16
        nearest = min(
            range(len(_TIMESTAMPS_MS)),
            key=lambda index: abs(_TIMESTAMPS_MS[index] - time_ms),
        )
        amplitude = round(_APERTURES[nearest] * 20_000)
        samples.append(amplitude if sample_index % 2 == 0 else -amplitude)
    return struct.pack(f"<{len(samples)}h", *samples)


def _audio(payload: bytes) -> RestrictedPcmAudio:
    return RestrictedPcmAudio(
        artifact_id="d" * 64,
        asset_handle_id=_HANDLE,
        attempt_id_sha256="e" * 64,
        byte_count=len(payload),
        sample_count=len(payload) // 2,
        derivation_receipt=AudioDerivationReceipt.issue(
            container_sha256=_CONTAINER,
            audio_stream_index=1,
            tool_revision="ffmpeg_test_v1",
            pcm_sha256=hashlib.sha256(payload).hexdigest(),
            source_time_base="1/48000",
        ),
    )


def _probe() -> MediaProbe:
    return MediaProbe(
        asset_handle_id=_HANDLE,
        container_sha256=_CONTAINER,
        format_name="mp4",
        duration_ms=1_000,
        video_stream=VideoStreamProbe(
            stream_index=0,
            codec_name="h264",
            width=320,
            height=240,
            frames_per_second_numerator=10,
            frames_per_second_denominator=1,
            frame_count=8,
            time_base="1/1000",
            start_time_us=0,
            duration_ms=1_000,
        ),
        audio_stream=AudioStreamProbe(
            stream_index=1,
            codec_name="aac",
            sample_rate_hz=48_000,
            channels=1,
            time_base="1/48000",
            start_time_us=0,
            duration_ms=1_000,
        ),
        video_frame_timestamps_us=tuple(value * 1000 for value in _TIMESTAMPS_MS),
        ffprobe_revision="ffprobe_test_v1",
    )


def _landmarks(*, available: bool = True) -> MediaPipeLandmarkBatch:
    frames = tuple(
        MediaPipeFaceFrame(
            timestamp_ms=timestamp_ms,
            face_count=1 if available else 0,
            lip_geometry=(
                MediaPipeLipGeometry(
                    upper_lip_y=0.4,
                    lower_lip_y=0.4 + aperture * 0.2,
                    left_corner_x=0.4,
                    right_corner_x=0.6,
                )
                if available
                else None
            ),
        )
        for timestamp_ms, aperture in zip(
            _TIMESTAMPS_MS, _APERTURES, strict=True
        )
    )
    return MediaPipeLandmarkBatch(
        asset_handle_id=_HANDLE,
        container_sha256=_CONTAINER,
        backend_revision="mediapipe_1.0.1_face_landmarker_float16_v1",
        model_sha256=_MODEL,
        network_isolation="seccomp_socket_denied",
        frames=frames,
    )


def _adapter(payload: bytes) -> LipAudioAvSyncAdapter:
    return LipAudioAvSyncAdapter(
        media_store=_Store(payload),
        policy=LipAudioAvSyncPolicy(
            maximum_absolute_offset_ms=200,
            offset_step_ms=20,
            energy_window_ms=40,
            minimum_landmark_frames=6,
            maximum_anchor_count=4,
        ),
    )


def test_extracts_unvalidated_sync_anchor_candidates_with_bound_receipt() -> None:
    payload = _pcm()

    result = _adapter(payload).measure(
        audio=_audio(payload),
        landmarks=_landmarks(),
        probe=_probe(),
    )

    assert result.status == "candidate_unvalidated"
    assert abs(result.candidate_offset_ms or 0) <= 20
    assert result.correlation is not None and result.correlation > 0.95
    assert 1 <= len(result.anchors) <= 4
    assert all(math.isfinite(anchor.strength) for anchor in result.anchors)
    assert len(result.receipt_sha256) == 64
    assert len(result.lineage_receipt_sha256) == 64


def test_missing_landmarks_returns_value_free_unavailable_evidence() -> None:
    payload = _pcm()

    result = _adapter(payload).measure(
        audio=_audio(payload),
        landmarks=_landmarks(available=False),
        probe=_probe(),
    )

    assert result.status == "unavailable"
    assert result.candidate_offset_ms is None
    assert result.correlation is None
    assert result.anchors == ()
    assert result.reason_codes == ("av_sync_landmark_coverage_insufficient",)


def test_cross_container_audio_is_rejected_before_read() -> None:
    payload = _pcm()
    audio = _audio(payload)
    wrong_receipt = AudioDerivationReceipt.issue(
        container_sha256="9" * 64,
        audio_stream_index=1,
        tool_revision="ffmpeg_test_v1",
        pcm_sha256=audio.derivation_receipt.pcm_sha256,
        source_time_base="1/48000",
    )
    mismatched = RestrictedPcmAudio(
        artifact_id=audio.artifact_id,
        asset_handle_id=audio.asset_handle_id,
        attempt_id_sha256=audio.attempt_id_sha256,
        byte_count=audio.byte_count,
        sample_count=audio.sample_count,
        derivation_receipt=wrong_receipt,
    )

    with pytest.raises(AvSyncRuntimeError, match="^av_sync_lineage_mismatch$"):
        _adapter(payload).measure(
            audio=mismatched,
            landmarks=_landmarks(),
            probe=_probe(),
        )
