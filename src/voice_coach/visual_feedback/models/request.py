"""Application request and restricted internal video observation record."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import VisualFeedbackContractError
from .binding import (
    ConsentReceipt,
    MediaTimebaseReceipt,
    RestrictedVideoHandle,
    SameAttemptBindingReceipt,
    SeungunAnalysisReference,
)
from .evidence import LipLandmarkFrame, SyncAnchor, VideoProbe
from .integrity import (
    require_finite_number,
    require_integer,
    require_safe_identifier,
    require_sha256,
)


@dataclass(frozen=True, slots=True)
class VisualFeedbackRequest:
    """Typed command for one optional visual supplement invocation."""

    analysis_ref: SeungunAnalysisReference
    upstream_phone_expected_index: int
    video_handle: RestrictedVideoHandle
    binding_receipt: SameAttemptBindingReceipt
    consent_receipt: ConsentReceipt
    capture_contract_id: str
    population_id_sha256: str
    context_id: str

    def __post_init__(self) -> None:
        require_integer(
            self.upstream_phone_expected_index,
            "upstream_phone_expected_index",
            minimum=0,
        )
        require_safe_identifier(self.capture_contract_id, "capture_contract_id")
        require_sha256(self.population_id_sha256, "population_id_sha256")
        require_safe_identifier(self.context_id, "context_id")


@dataclass(frozen=True, slots=True)
class RestrictedVideoRecord:
    """Internal test-backend record; contains no media bytes or filesystem path."""

    handle: RestrictedVideoHandle
    audio_timebase: MediaTimebaseReceipt
    video_timebase: MediaTimebaseReceipt
    probe: VideoProbe
    lip_frames: tuple[LipLandmarkFrame, ...]
    sync_anchors: tuple[SyncAnchor, ...]

    def __post_init__(self) -> None:
        if self.handle.timebase_sha256 != self.video_timebase.timebase_sha256:
            raise VisualFeedbackContractError(
                "restricted video handle timebase digest differs"
            )
        if self.video_timebase.duration_ms != self.probe.duration_ms:
            raise VisualFeedbackContractError(
                "restricted video probe duration differs from its timebase"
            )
        if self.lip_frames:
            timestamps = [frame.timestamp_ms for frame in self.lip_frames]
            if timestamps != sorted(timestamps) or len(set(timestamps)) != len(
                timestamps
            ):
                raise VisualFeedbackContractError(
                    "lip frame timestamps must be strictly increasing"
                )
            if timestamps[-1] > self.video_timebase.duration_ms:
                raise VisualFeedbackContractError(
                    "lip frame exceeds the video timebase"
                )
        if self.sync_anchors:
            audio_times = [anchor.audio_ms for anchor in self.sync_anchors]
            video_times = [anchor.video_ms for anchor in self.sync_anchors]
            if audio_times != sorted(audio_times) or len(set(audio_times)) != len(
                audio_times
            ):
                raise VisualFeedbackContractError(
                    "sync audio timestamps must be strictly increasing"
                )
            if video_times != sorted(video_times):
                raise VisualFeedbackContractError(
                    "sync video timestamps must be monotonic"
                )
            if audio_times[-1] > self.audio_timebase.duration_ms:
                raise VisualFeedbackContractError(
                    "sync anchor exceeds the audio timebase"
                )
            if video_times[-1] > self.video_timebase.duration_ms:
                raise VisualFeedbackContractError(
                    "sync anchor exceeds the video timebase"
                )


@dataclass(frozen=True, slots=True)
class VideoQualityPolicy:
    """Injected QC limits; no undocumented production defaults are provided."""

    maximum_absolute_pose_degrees: float
    minimum_blur_score: float
    maximum_occlusion_fraction: float
    minimum_lighting_score: float
    minimum_lip_roi_fraction: float

    def __post_init__(self) -> None:
        pose = require_finite_number(
            self.maximum_absolute_pose_degrees,
            "maximum_absolute_pose_degrees",
        )
        blur = require_finite_number(self.minimum_blur_score, "minimum_blur_score")
        occlusion = require_finite_number(
            self.maximum_occlusion_fraction,
            "maximum_occlusion_fraction",
        )
        lighting = require_finite_number(
            self.minimum_lighting_score,
            "minimum_lighting_score",
        )
        lip_roi = require_finite_number(
            self.minimum_lip_roi_fraction,
            "minimum_lip_roi_fraction",
        )
        if not 0.0 <= pose <= 180.0 or not 0.0 <= blur <= 1.0:
            raise VisualFeedbackContractError(
                "pose must be in [0, 180] and blur must be in [0, 1]"
            )
        if not all(0.0 <= value <= 1.0 for value in (occlusion, lighting, lip_roi)):
            raise VisualFeedbackContractError(
                "occlusion, lighting, and lip ROI limits must be in [0, 1]"
            )


@dataclass(frozen=True, slots=True)
class LipTrackingPolicy:
    """Injected visibility and closure limits for aggregate 2D lip cues."""

    minimum_frame_visibility: float
    minimum_visible_frame_fraction: float
    closure_aperture_ratio: float

    def __post_init__(self) -> None:
        visibility = require_finite_number(
            self.minimum_frame_visibility,
            "minimum_frame_visibility",
        )
        visible_fraction = require_finite_number(
            self.minimum_visible_frame_fraction,
            "minimum_visible_frame_fraction",
        )
        closure_ratio = require_finite_number(
            self.closure_aperture_ratio,
            "closure_aperture_ratio",
        )
        if not 0.0 <= visibility <= 1.0 or not 0.0 <= visible_fraction <= 1.0:
            raise VisualFeedbackContractError(
                "visibility limits must be in [0, 1]"
            )
        if closure_ratio < 0.0:
            raise VisualFeedbackContractError(
                "closure_aperture_ratio must be non-negative"
            )


@dataclass(frozen=True, slots=True)
class AvSyncPolicy:
    """Injected acceptance bounds for an affine A/V clock fit."""

    maximum_absolute_offset_ms: float
    maximum_absolute_drift_parts_per_million: float
    maximum_root_mean_square_error_ms: float
    minimum_anchor_count: int = 3

    def __post_init__(self) -> None:
        limits = (
            require_finite_number(
                self.maximum_absolute_offset_ms,
                "maximum_absolute_offset_ms",
            ),
            require_finite_number(
                self.maximum_absolute_drift_parts_per_million,
                "maximum_absolute_drift_parts_per_million",
            ),
            require_finite_number(
                self.maximum_root_mean_square_error_ms,
                "maximum_root_mean_square_error_ms",
            ),
        )
        if any(limit < 0.0 for limit in limits):
            raise VisualFeedbackContractError(
                "A/V sync acceptance limits must be non-negative"
            )
        require_integer(self.minimum_anchor_count, "minimum_anchor_count", minimum=2)


__all__ = [
    "AvSyncPolicy",
    "LipTrackingPolicy",
    "RestrictedVideoRecord",
    "VideoQualityPolicy",
    "VisualFeedbackRequest",
]
