"""Explicit fail-closed adapters for optional visual runtimes.

The local real-media loop can validate and demux a video without pretending a
face/landmark or content-sync model exists.  These adapters preserve that
distinction in the same typed observations used by the sidecar.
"""

from __future__ import annotations

from ..errors import VisualFeedbackContractError
from ..models.binding import MediaTimebaseReceipt, RestrictedVideoHandle
from ..models.enums import VisualCueState, VideoQualityStatus
from ..models.evidence import (
    AdapterReceipt,
    AvSyncObservation,
    LipCueMeasurement,
    LipTrackObservation,
    VideoQualityObservation,
)


class UnavailableLandmarkVideoQualityAdapter:
    """Reject visual QC when face/pose/lip landmark inference is unavailable."""

    def __init__(
        self, *, backend_revision: str = "unconfigured_landmark_runtime_v1"
    ) -> None:
        self._backend_revision = backend_revision

    def observe(
        self, *, video_handle: RestrictedVideoHandle
    ) -> VideoQualityObservation:
        del video_handle
        reasons = ("lip_landmark_backend_unavailable",)
        return VideoQualityObservation(
            status=VideoQualityStatus.FAILED_CLOSED,
            reason_codes=reasons,
            receipt=AdapterReceipt(
                backend_id="landmark_video_quality",
                backend_revision=self._backend_revision,
                status="backend_unavailable",
                reason_codes=reasons,
            ),
        )


class UnavailableLipLandmarkTrackingAdapter:
    """Return a typed unavailable cue without inventing landmark coordinates."""

    def __init__(
        self, *, backend_revision: str = "unconfigured_landmark_runtime_v1"
    ) -> None:
        self._backend_revision = backend_revision

    def track(
        self,
        *,
        video_handle: RestrictedVideoHandle,
        video_start_ms: int,
        video_end_ms: int,
    ) -> LipTrackObservation:
        del video_handle
        if video_start_ms < 0 or video_end_ms <= video_start_ms:
            raise VisualFeedbackContractError("tracking window must move forward")
        reasons = ("lip_landmark_backend_unavailable",)
        receipt = AdapterReceipt(
            backend_id="lip_landmark_tracking",
            backend_revision=self._backend_revision,
            status=VisualCueState.BACKEND_UNAVAILABLE.value,
            reason_codes=reasons,
        )
        return LipTrackObservation(
            cues=(
                LipCueMeasurement(
                    cue_id="lip_aperture_ratio",
                    state=VisualCueState.BACKEND_UNAVAILABLE,
                    value=None,
                    unit="ratio",
                    video_start_ms=video_start_ms,
                    video_end_ms=video_end_ms,
                    reason_codes=reasons,
                    receipt=receipt,
                ),
            ),
            receipt=receipt,
        )


class UnavailableContentAvSyncAdapter:
    """Refuse to treat a shared container clock as observed content sync."""

    def __init__(
        self, *, backend_revision: str = "unconfigured_content_sync_v1"
    ) -> None:
        self._backend_revision = backend_revision

    def measure(
        self,
        *,
        video_handle: RestrictedVideoHandle,
        audio_timebase: MediaTimebaseReceipt,
        video_timebase: MediaTimebaseReceipt,
    ) -> AvSyncObservation:
        del video_handle, audio_timebase, video_timebase
        reasons = ("av_content_sync_backend_unavailable",)
        return AvSyncObservation(
            state=VisualCueState.BACKEND_UNAVAILABLE,
            offset_ms=None,
            drift_parts_per_million=None,
            root_mean_square_error_ms=None,
            reason_codes=reasons,
            receipt=AdapterReceipt(
                backend_id="content_av_sync",
                backend_revision=self._backend_revision,
                status=VisualCueState.BACKEND_UNAVAILABLE.value,
                reason_codes=reasons,
            ),
        )


__all__ = [
    "UnavailableContentAvSyncAdapter",
    "UnavailableLandmarkVideoQualityAdapter",
    "UnavailableLipLandmarkTrackingAdapter",
]
