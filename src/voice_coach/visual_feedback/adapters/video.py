"""Deterministic internal adapters over precomputed, non-media observations.

These adapters deliberately do not decode video or run a landmark model.  They
exercise consent, lifecycle, QC, tracking, and synchronization contracts using
records injected by an internal test harness.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import math

from ..errors import VisualFeedbackContractError
from ..models.binding import (
    FACIAL_VIDEO_DATA_CATEGORY,
    VISUAL_PROCESSING_PURPOSE,
    ConsentReceipt,
    MediaTimebaseReceipt,
    RestrictedVideoHandle,
)
from ..models.enums import ConsentDecision, VisualCueState, VideoQualityStatus
from ..models.evidence import (
    AdapterReceipt,
    AvSyncObservation,
    LipCueMeasurement,
    LipTrackObservation,
    MediaDeletionReceipt,
    VideoQualityObservation,
)
from ..models.integrity import require_safe_identifier, sha256_document
from ..models.request import (
    AvSyncPolicy,
    LipTrackingPolicy,
    RestrictedVideoRecord,
    VideoQualityPolicy,
)


class InMemoryRestrictedVideoAdapter:
    """Own injected records behind opaque handles and delete them idempotently."""

    def __init__(
        self,
        records: Mapping[str, RestrictedVideoRecord],
        *,
        backend_revision: str = "internal_precomputed_v1",
    ) -> None:
        self._records = dict(records)
        self._deleted: set[str] = set()
        self._backend_revision = backend_revision
        self.access_count = 0
        self.delete_count = 0
        for handle_id, record in self._records.items():
            if handle_id != record.handle.handle_id:
                raise VisualFeedbackContractError(
                    "restricted video record key differs from its opaque handle"
                )

    def assert_available(self, video_handle: RestrictedVideoHandle) -> None:
        self.record(video_handle)

    def record(self, video_handle: RestrictedVideoHandle) -> RestrictedVideoRecord:
        """Resolve an injected record without returning a path or media bytes."""

        self.access_count += 1
        if video_handle.handle_id in self._deleted:
            raise VisualFeedbackContractError("restricted video was already deleted")
        record = self._records.get(video_handle.handle_id)
        if record is None or record.handle != video_handle:
            raise VisualFeedbackContractError(
                "restricted video handle is unavailable or mismatched"
            )
        return record

    def delete(self, video_handle: RestrictedVideoHandle) -> MediaDeletionReceipt:
        self.delete_count += 1
        record = self._records.get(video_handle.handle_id)
        if record is not None and record.handle != video_handle:
            raise VisualFeedbackContractError(
                "restricted video deletion handle is mismatched"
            )
        if record is None and video_handle.handle_id not in self._deleted:
            raise VisualFeedbackContractError(
                "restricted video deletion handle is unavailable"
            )
        if record is not None:
            del self._records[video_handle.handle_id]
        self._deleted.add(video_handle.handle_id)
        backend_receipt = AdapterReceipt(
            backend_id="restricted_video_lifecycle",
            backend_revision=self._backend_revision,
            status="deleted",
        )
        return MediaDeletionReceipt(
            status="deleted",
            deletion_receipt_sha256=sha256_document(
                {
                    "handle_id_sha256": video_handle.handle_id,
                    "attempt_id_sha256": video_handle.attempt_id_sha256,
                    "source_sha256": video_handle.source_sha256,
                    "timebase_sha256": video_handle.timebase_sha256,
                    "status": "deleted",
                    "backend_revision": self._backend_revision,
                }
            ),
            backend_receipt=backend_receipt,
        )


class StaticConsentRightsAdapter:
    """Validate an immutable purpose/retention receipt against a fixed clock."""

    def __init__(
        self,
        *,
        now_utc: datetime,
        allowed_retention_policy_ids: tuple[str, ...],
    ) -> None:
        if now_utc.tzinfo is None or now_utc.utcoffset() is None:
            raise VisualFeedbackContractError("consent validation clock needs timezone")
        if now_utc.utcoffset().total_seconds() != 0:
            raise VisualFeedbackContractError("consent validation clock must be UTC")
        if not allowed_retention_policy_ids:
            raise VisualFeedbackContractError(
                "at least one retention policy must be allowed"
            )
        self._now_utc = now_utc
        for policy_id in allowed_retention_policy_ids:
            require_safe_identifier(policy_id, "allowed_retention_policy_ids[]")
        self._allowed_retention_policy_ids = frozenset(
            allowed_retention_policy_ids
        )
        self.validation_count = 0

    def validate(
        self,
        *,
        receipt: ConsentReceipt,
        video_handle: RestrictedVideoHandle,
    ) -> tuple[bool, tuple[str, ...]]:
        self.validation_count += 1
        reasons: list[str] = []
        if receipt.attempt_id_sha256 != video_handle.attempt_id_sha256:
            reasons.append("consent_attempt_mismatch")
        if receipt.decision is not ConsentDecision.GRANTED:
            reasons.append("visual_consent_denied")
        if receipt.purpose != VISUAL_PROCESSING_PURPOSE:
            reasons.append("consent_purpose_mismatch")
        if receipt.data_category != FACIAL_VIDEO_DATA_CATEGORY:
            reasons.append("consent_data_category_mismatch")
        if receipt.retention_policy_id not in self._allowed_retention_policy_ids:
            reasons.append("retention_policy_not_allowed")
        if not receipt.delete_on_completion:
            reasons.append("delete_on_completion_required")
        if receipt.remote_egress_allowed:
            reasons.append("remote_egress_not_allowed")
        issued = datetime.fromisoformat(receipt.issued_at_utc.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(
            receipt.expires_at_utc.replace("Z", "+00:00")
        )
        if self._now_utc < issued:
            reasons.append("consent_not_yet_effective")
        if self._now_utc >= expires:
            reasons.append("consent_expired")
        return not reasons, tuple(reasons)


class DeterministicVideoQualityAdapter:
    """Apply injected QC limits to a precomputed video probe."""

    def __init__(
        self,
        store: InMemoryRestrictedVideoAdapter,
        policy: VideoQualityPolicy,
        *,
        backend_revision: str = "internal_probe_qc_v1",
    ) -> None:
        self._store = store
        self._policy = policy
        self._backend_revision = backend_revision
        self.observation_count = 0

    def observe(
        self, *, video_handle: RestrictedVideoHandle
    ) -> VideoQualityObservation:
        self.observation_count += 1
        probe = self._store.record(video_handle).probe
        if not probe.timestamps_monotonic:
            return self._result(
                VideoQualityStatus.FAILED_CLOSED,
                ("video_timestamps_not_monotonic",),
            )
        if probe.tracked_face_count > 1:
            return self._result(
                VideoQualityStatus.UNSUPPORTED,
                ("multiple_faces_unsupported",),
            )
        pose = max(
            probe.maximum_absolute_yaw_degrees,
            probe.maximum_absolute_pitch_degrees,
            probe.maximum_absolute_roll_degrees,
        )
        if pose > self._policy.maximum_absolute_pose_degrees:
            return self._result(
                VideoQualityStatus.UNSUPPORTED,
                ("frontal_pose_support_exceeded",),
            )
        reasons: list[str] = []
        if probe.tracked_face_count == 0:
            reasons.append("face_not_detected")
        if probe.blur_score < self._policy.minimum_blur_score:
            reasons.append("video_too_blurry")
        if probe.occlusion_fraction > self._policy.maximum_occlusion_fraction:
            reasons.append("lip_region_occluded")
        if probe.lighting_score < self._policy.minimum_lighting_score:
            reasons.append("lighting_insufficient")
        if probe.lip_roi_fraction < self._policy.minimum_lip_roi_fraction:
            reasons.append("lip_region_too_small")
        if reasons:
            return self._result(
                VideoQualityStatus.RERECORD_REQUIRED,
                tuple(reasons),
            )
        return self._result(VideoQualityStatus.PASSED, ())

    def _result(
        self,
        status: VideoQualityStatus,
        reasons: tuple[str, ...],
    ) -> VideoQualityObservation:
        return VideoQualityObservation(
            status=status,
            reason_codes=reasons,
            receipt=AdapterReceipt(
                backend_id="deterministic_video_quality",
                backend_revision=self._backend_revision,
                status=status.value,
                reason_codes=reasons,
            ),
        )


class PrecomputedLipLandmarkTrackingAdapter:
    """Aggregate lip aperture from injected minimal 2D landmark frames."""

    def __init__(
        self,
        store: InMemoryRestrictedVideoAdapter,
        policy: LipTrackingPolicy,
        *,
        backend_revision: str = "internal_landmark_aggregate_v1",
    ) -> None:
        self._store = store
        self._policy = policy
        self._backend_revision = backend_revision
        self.tracking_count = 0

    def track(
        self,
        *,
        video_handle: RestrictedVideoHandle,
        video_start_ms: int,
        video_end_ms: int,
    ) -> LipTrackObservation:
        self.tracking_count += 1
        if video_end_ms <= video_start_ms:
            raise VisualFeedbackContractError("tracking window must move forward")
        frames = tuple(
            frame
            for frame in self._store.record(video_handle).lip_frames
            if video_start_ms <= frame.timestamp_ms <= video_end_ms
        )
        receipt_base = {
            "backend_id": "precomputed_lip_landmark_tracking",
            "backend_revision": self._backend_revision,
        }
        if not frames:
            reasons = ("lip_frames_missing_for_window",)
            receipt = AdapterReceipt(
                **receipt_base,
                status="missing",
                reason_codes=reasons,
            )
            cue = _unobserved_aperture_cue(
                state=VisualCueState.MISSING,
                video_start_ms=video_start_ms,
                video_end_ms=video_end_ms,
                reasons=reasons,
                receipt=receipt,
            )
            return LipTrackObservation(cues=(cue,), receipt=receipt)

        visible = tuple(
            frame
            for frame in frames
            if frame.visibility >= self._policy.minimum_frame_visibility
        )
        visible_fraction = len(visible) / len(frames)
        if not visible or (
            visible_fraction < self._policy.minimum_visible_frame_fraction
        ):
            reasons = ("lip_visibility_below_policy",)
            receipt = AdapterReceipt(
                **receipt_base,
                status="low_confidence",
                reason_codes=reasons,
            )
            cue = _unobserved_aperture_cue(
                state=VisualCueState.LOW_CONFIDENCE,
                video_start_ms=video_start_ms,
                video_end_ms=video_end_ms,
                reasons=reasons,
                receipt=receipt,
            )
            return LipTrackObservation(cues=(cue,), receipt=receipt)

        ratios = tuple(
            abs(frame.lower_lip_y - frame.upper_lip_y)
            / (frame.right_corner_x - frame.left_corner_x)
            for frame in visible
        )
        aperture = sum(ratios) / len(ratios)
        closure_fraction = sum(
            ratio <= self._policy.closure_aperture_ratio for ratio in ratios
        ) / len(ratios)
        receipt = AdapterReceipt(
            **receipt_base,
            status="observed",
        )
        return LipTrackObservation(
            cues=(
                LipCueMeasurement(
                    cue_id="lip_aperture_ratio",
                    state=VisualCueState.OBSERVED,
                    value=aperture,
                    unit="ratio",
                    video_start_ms=video_start_ms,
                    video_end_ms=video_end_ms,
                    reason_codes=(),
                    receipt=receipt,
                ),
                LipCueMeasurement(
                    cue_id="lip_closure_fraction",
                    state=VisualCueState.OBSERVED,
                    value=closure_fraction,
                    unit="fraction",
                    video_start_ms=video_start_ms,
                    video_end_ms=video_end_ms,
                    reason_codes=(),
                    receipt=receipt,
                ),
            ),
            receipt=receipt,
        )


class TimestampAvSyncAdapter:
    """Fit an affine audio-to-video time transform from injected anchors."""

    def __init__(
        self,
        store: InMemoryRestrictedVideoAdapter,
        policy: AvSyncPolicy,
        *,
        backend_revision: str = "internal_timestamp_fit_v1",
    ) -> None:
        self._store = store
        self._policy = policy
        self._backend_revision = backend_revision
        self.measurement_count = 0

    def measure(
        self,
        *,
        video_handle: RestrictedVideoHandle,
        audio_timebase: MediaTimebaseReceipt,
        video_timebase: MediaTimebaseReceipt,
    ) -> AvSyncObservation:
        self.measurement_count += 1
        record = self._store.record(video_handle)
        if (
            record.audio_timebase != audio_timebase
            or record.video_timebase != video_timebase
        ):
            raise VisualFeedbackContractError(
                "A/V sync timebase differs from the restricted record"
            )
        anchors = record.sync_anchors
        if len(anchors) < self._policy.minimum_anchor_count:
            return self._unobserved(
                VisualCueState.MISSING,
                ("av_sync_anchor_count_insufficient",),
            )
        audio_mean = sum(anchor.audio_ms for anchor in anchors) / len(anchors)
        video_mean = sum(anchor.video_ms for anchor in anchors) / len(anchors)
        denominator = sum((anchor.audio_ms - audio_mean) ** 2 for anchor in anchors)
        if denominator <= 0.0:
            return self._unobserved(
                VisualCueState.MISSING,
                ("av_sync_audio_span_missing",),
            )
        slope = sum(
            (anchor.audio_ms - audio_mean) * (anchor.video_ms - video_mean)
            for anchor in anchors
        ) / denominator
        offset = video_mean - slope * audio_mean
        drift = (slope - 1.0) * 1_000_000.0
        residuals = tuple(
            anchor.video_ms - (offset + slope * anchor.audio_ms)
            for anchor in anchors
        )
        rmse = math.sqrt(sum(value * value for value in residuals) / len(residuals))
        reasons: list[str] = []
        if abs(offset) > self._policy.maximum_absolute_offset_ms:
            reasons.append("av_sync_offset_exceeds_policy")
        if abs(drift) > self._policy.maximum_absolute_drift_parts_per_million:
            reasons.append("av_sync_drift_exceeds_policy")
        if rmse > self._policy.maximum_root_mean_square_error_ms:
            reasons.append("av_sync_fit_error_exceeds_policy")
        if reasons:
            return self._unobserved(
                VisualCueState.LOW_CONFIDENCE,
                tuple(reasons),
            )
        return AvSyncObservation(
            state=VisualCueState.OBSERVED,
            offset_ms=offset,
            drift_parts_per_million=drift,
            root_mean_square_error_ms=rmse,
            reason_codes=(),
            receipt=AdapterReceipt(
                backend_id="timestamp_av_sync",
                backend_revision=self._backend_revision,
                status="observed",
            ),
        )

    def _unobserved(
        self,
        state: VisualCueState,
        reasons: tuple[str, ...],
    ) -> AvSyncObservation:
        return AvSyncObservation(
            state=state,
            offset_ms=None,
            drift_parts_per_million=None,
            root_mean_square_error_ms=None,
            reason_codes=reasons,
            receipt=AdapterReceipt(
                backend_id="timestamp_av_sync",
                backend_revision=self._backend_revision,
                status=state.value,
                reason_codes=reasons,
            ),
        )


def _unobserved_aperture_cue(
    *,
    state: VisualCueState,
    video_start_ms: int,
    video_end_ms: int,
    reasons: tuple[str, ...],
    receipt: AdapterReceipt,
) -> LipCueMeasurement:
    return LipCueMeasurement(
        cue_id="lip_aperture_ratio",
        state=state,
        value=None,
        unit="ratio",
        video_start_ms=video_start_ms,
        video_end_ms=video_end_ms,
        reason_codes=reasons,
        receipt=receipt,
    )


__all__ = [
    "DeterministicVideoQualityAdapter",
    "InMemoryRestrictedVideoAdapter",
    "PrecomputedLipLandmarkTrackingAdapter",
    "StaticConsentRightsAdapter",
    "TimestampAvSyncAdapter",
]
