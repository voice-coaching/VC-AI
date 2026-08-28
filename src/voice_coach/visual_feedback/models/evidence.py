"""Typed immutable seungun and visual evidence models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

from ..errors import VisualFeedbackContractError
from .binding import (
    MediaTimebaseReceipt,
    SeungunAnalysisReference,
    UpstreamReliabilityReceipt,
)
from .enums import (
    UpstreamReliabilityStatus,
    VisualCueState,
    VideoQualityStatus,
)
from .integrity import (
    require_finite_number,
    require_integer,
    require_safe_identifier,
    require_sha256,
    require_text_token,
    sha256_document,
)

SEUNGUN_ANALYSIS_SCHEMA_VERSION = "korean_phone_ctc.pronunciation_analysis.v1"
SEUNGUN_SCORE_SEMANTICS = (
    "detector_ranking_score_not_calibrated_correctness_confidence"
)


@dataclass(frozen=True, slots=True)
class SeungunAnalysisSourceRecord:
    """Immutable analysis snapshot and its facade-issued binding metadata."""

    reference: SeungunAnalysisReference
    analysis: Mapping[str, object]
    prompt_id_sha256: str
    prompt_revision: str
    expected_phone_sha256: str
    audio_source_sha256: str
    audio_timebase: MediaTimebaseReceipt
    reliability_receipt: UpstreamReliabilityReceipt
    binding_receipt_sha256: str
    audio_derivation_receipt_sha256: str

    def __post_init__(self) -> None:
        require_sha256(self.prompt_id_sha256, "prompt_id_sha256")
        require_safe_identifier(self.prompt_revision, "prompt_revision")
        require_sha256(self.expected_phone_sha256, "expected_phone_sha256")
        require_sha256(self.audio_source_sha256, "audio_source_sha256")
        require_sha256(self.binding_receipt_sha256, "binding_receipt_sha256")
        require_sha256(
            self.audio_derivation_receipt_sha256,
            "audio_derivation_receipt_sha256",
        )
        if sha256_document(self.analysis) != self.reference.analysis_sha256:
            raise VisualFeedbackContractError(
                "source analysis differs from its immutable reference"
            )


@dataclass(frozen=True, slots=True)
class AdapterReceipt:
    """Privacy-safe provenance for one adapter invocation."""

    backend_id: str
    backend_revision: str
    status: str
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_safe_identifier(self.backend_id, "backend_id")
        require_safe_identifier(self.backend_revision, "backend_revision")
        require_safe_identifier(self.status, "status")
        for reason in self.reason_codes:
            require_safe_identifier(reason, "reason_codes[]")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise VisualFeedbackContractError("adapter reason_codes must be unique")

    def to_json_object(self) -> dict[str, object]:
        return {
            "backend_id": self.backend_id,
            "backend_revision": self.backend_revision,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class UpstreamPhoneAnchor:
    """Read-only phone evidence copied from seungun without reinterpretation."""

    anchor_ref: str
    expected_index: int
    expected_phone: str
    alignment_operation: str
    start_ms: int | None
    end_ms: int | None
    observed_candidate: str | None
    error_score: float
    operating_threshold: float
    is_error: bool
    score_semantics: str

    def __post_init__(self) -> None:
        require_sha256(self.anchor_ref, "anchor_ref")
        require_integer(self.expected_index, "expected_index", minimum=0)
        require_text_token(self.expected_phone, "expected_phone")
        if self.alignment_operation not in ("correct", "substitution", "deletion"):
            raise VisualFeedbackContractError("alignment_operation is unknown")
        if (self.start_ms is None) != (self.end_ms is None):
            raise VisualFeedbackContractError(
                "phone span must be fully present or absent"
            )
        if self.start_ms is not None and self.end_ms is not None:
            require_integer(self.start_ms, "start_ms", minimum=0)
            require_integer(self.end_ms, "end_ms", minimum=1)
            if self.end_ms <= self.start_ms:
                raise VisualFeedbackContractError("phone span must move forward")
        if self.observed_candidate is not None:
            require_text_token(self.observed_candidate, "observed_candidate")
        if self.alignment_operation == "deletion" and (
            self.start_ms is not None or self.observed_candidate is not None
        ):
            raise VisualFeedbackContractError(
                "deletion evidence must not contain a span or candidate"
            )
        if self.alignment_operation != "deletion" and (
            self.start_ms is None or self.observed_candidate is None
        ):
            raise VisualFeedbackContractError(
                "non-deletion evidence requires a span and candidate"
            )
        if (
            self.alignment_operation == "correct"
            and self.observed_candidate != self.expected_phone
        ):
            raise VisualFeedbackContractError(
                "correct alignment must preserve the expected phone"
            )
        if (
            self.alignment_operation == "substitution"
            and self.observed_candidate == self.expected_phone
        ):
            raise VisualFeedbackContractError(
                "substitution candidate must differ from the expected phone"
            )
        score = require_finite_number(self.error_score, "error_score")
        threshold = require_finite_number(
            self.operating_threshold, "operating_threshold"
        )
        if not 0.0 <= score <= 1.0 or not 0.0 <= threshold <= 1.0:
            raise VisualFeedbackContractError(
                "detector score and threshold must be in [0, 1]"
            )
        if not isinstance(self.is_error, bool):
            raise VisualFeedbackContractError("is_error must be a boolean")
        if self.is_error != (score >= threshold):
            raise VisualFeedbackContractError(
                "is_error must preserve the frozen operating threshold decision"
            )
        if self.score_semantics != SEUNGUN_SCORE_SEMANTICS:
            raise VisualFeedbackContractError("seungun score semantics differ")


@dataclass(frozen=True, slots=True)
class SeungunEvidenceSnapshot:
    """Validated internal snapshot resolved through the seungun evidence port."""

    analysis_reference_id: str
    analysis_sha256: str
    analysis_schema_version: str
    attempt_id_sha256: str
    prompt_id_sha256: str
    prompt_revision: str
    expected_phone_sha256: str
    audio_derivation_receipt_sha256: str
    binding_receipt_sha256: str
    reliability_status: UpstreamReliabilityStatus
    reliability_receipt_sha256: str
    phone_anchors: tuple[UpstreamPhoneAnchor, ...]
    adapter_receipt: AdapterReceipt

    def __post_init__(self) -> None:
        for name in (
            "analysis_reference_id",
            "analysis_sha256",
            "attempt_id_sha256",
            "prompt_id_sha256",
            "expected_phone_sha256",
            "audio_derivation_receipt_sha256",
            "binding_receipt_sha256",
            "reliability_receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.analysis_schema_version != SEUNGUN_ANALYSIS_SCHEMA_VERSION:
            raise VisualFeedbackContractError("seungun analysis schema differs")
        require_safe_identifier(self.prompt_revision, "prompt_revision")
        if not self.phone_anchors:
            raise VisualFeedbackContractError(
                "seungun snapshot must contain phone anchors"
            )
        indices = [anchor.expected_index for anchor in self.phone_anchors]
        if indices != list(range(len(indices))):
            raise VisualFeedbackContractError(
                "phone anchors must preserve contiguous indices"
            )
        if len({anchor.anchor_ref for anchor in self.phone_anchors}) != len(
            self.phone_anchors
        ):
            raise VisualFeedbackContractError("phone anchor references must be unique")

    def anchor(self, expected_index: int) -> UpstreamPhoneAnchor | None:
        """Return the exact upstream anchor without selecting a replacement."""

        if expected_index < 0 or expected_index >= len(self.phone_anchors):
            return None
        return self.phone_anchors[expected_index]


@dataclass(frozen=True, slots=True)
class VideoProbe:
    """Precomputed video metadata consumed by deterministic internal QC."""

    duration_ms: int
    frame_count: int
    frames_per_second: float
    tracked_face_count: int
    maximum_absolute_yaw_degrees: float
    maximum_absolute_pitch_degrees: float
    maximum_absolute_roll_degrees: float
    blur_score: float
    occlusion_fraction: float
    lighting_score: float
    lip_roi_fraction: float
    timestamps_monotonic: bool

    def __post_init__(self) -> None:
        require_integer(self.duration_ms, "duration_ms", minimum=1)
        require_integer(self.frame_count, "frame_count", minimum=1)
        if require_finite_number(self.frames_per_second, "frames_per_second") <= 0:
            raise VisualFeedbackContractError("frames_per_second must be positive")
        require_integer(self.tracked_face_count, "tracked_face_count", minimum=0)
        for name in (
            "maximum_absolute_yaw_degrees",
            "maximum_absolute_pitch_degrees",
            "maximum_absolute_roll_degrees",
        ):
            value = require_finite_number(getattr(self, name), name)
            if value < 0:
                raise VisualFeedbackContractError(f"{name} must be non-negative")
        for name in (
            "blur_score",
            "occlusion_fraction",
            "lighting_score",
            "lip_roi_fraction",
        ):
            value = require_finite_number(getattr(self, name), name)
            if not 0.0 <= value <= 1.0:
                raise VisualFeedbackContractError(f"{name} must be in [0, 1]")
        if not isinstance(self.timestamps_monotonic, bool):
            raise VisualFeedbackContractError("timestamps_monotonic must be boolean")


@dataclass(frozen=True, slots=True)
class VideoQualityObservation:
    """QC result with no frame, face crop, or path."""

    status: VideoQualityStatus
    reason_codes: tuple[str, ...]
    receipt: AdapterReceipt

    def __post_init__(self) -> None:
        for reason in self.reason_codes:
            require_safe_identifier(reason, "reason_codes[]")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise VisualFeedbackContractError("quality reason_codes must be unique")
        if self.status is VideoQualityStatus.PASSED and self.reason_codes:
            raise VisualFeedbackContractError(
                "passed video quality cannot have reasons"
            )
        if self.status is not VideoQualityStatus.PASSED and not self.reason_codes:
            raise VisualFeedbackContractError("failed video quality requires a reason")


@dataclass(frozen=True, slots=True)
class LipLandmarkFrame:
    """Minimal normalized outer-lip geometry for internal contract smoke."""

    timestamp_ms: int
    upper_lip_y: float
    lower_lip_y: float
    left_corner_x: float
    right_corner_x: float
    visibility: float

    def __post_init__(self) -> None:
        require_integer(self.timestamp_ms, "timestamp_ms", minimum=0)
        for name in (
            "upper_lip_y",
            "lower_lip_y",
            "left_corner_x",
            "right_corner_x",
            "visibility",
        ):
            value = require_finite_number(getattr(self, name), name)
            if not 0.0 <= value <= 1.0:
                raise VisualFeedbackContractError(
                    f"normalized landmark {name} must be in [0, 1]"
                )
        if self.right_corner_x <= self.left_corner_x:
            raise VisualFeedbackContractError("lip width must be positive")


@dataclass(frozen=True, slots=True)
class LipCueMeasurement:
    """One aggregate visible-lip cue, never a phone diagnosis."""

    cue_id: str
    state: VisualCueState
    value: float | None
    unit: str
    video_start_ms: int | None
    video_end_ms: int | None
    reason_codes: tuple[str, ...]
    receipt: AdapterReceipt

    def __post_init__(self) -> None:
        require_safe_identifier(self.cue_id, "cue_id")
        require_safe_identifier(self.unit, "unit")
        if self.state is VisualCueState.OBSERVED:
            if self.value is None:
                raise VisualFeedbackContractError("observed cue must have a value")
            require_finite_number(self.value, "value")
            if self.video_start_ms is None or self.video_end_ms is None:
                raise VisualFeedbackContractError("observed cue must have a video span")
        elif self.value is not None:
            raise VisualFeedbackContractError("unobserved cue must not carry a value")
        if (self.video_start_ms is None) != (self.video_end_ms is None):
            raise VisualFeedbackContractError(
                "cue span must be fully present or absent"
            )
        if self.video_start_ms is not None and self.video_end_ms is not None:
            require_integer(self.video_start_ms, "video_start_ms", minimum=0)
            require_integer(self.video_end_ms, "video_end_ms", minimum=1)
            if self.video_end_ms <= self.video_start_ms:
                raise VisualFeedbackContractError("cue span must move forward")
        for reason in self.reason_codes:
            require_safe_identifier(reason, "reason_codes[]")


@dataclass(frozen=True, slots=True)
class LipTrackObservation:
    """Aggregate tracker output used by calibration and policy."""

    cues: tuple[LipCueMeasurement, ...]
    receipt: AdapterReceipt

    def __post_init__(self) -> None:
        cue_ids = [cue.cue_id for cue in self.cues]
        if len(set(cue_ids)) != len(cue_ids):
            raise VisualFeedbackContractError("tracked cue ids must be unique")


@dataclass(frozen=True, slots=True)
class SyncAnchor:
    """One matched audio/video timing event for deterministic sync fitting."""

    audio_ms: int
    video_ms: int

    def __post_init__(self) -> None:
        require_integer(self.audio_ms, "audio_ms", minimum=0)
        require_integer(self.video_ms, "video_ms", minimum=0)


@dataclass(frozen=True, slots=True)
class AvSyncObservation:
    """Affine audio-to-video clock transform and its measurement state."""

    state: VisualCueState
    offset_ms: float | None
    drift_parts_per_million: float | None
    root_mean_square_error_ms: float | None
    reason_codes: tuple[str, ...]
    receipt: AdapterReceipt

    def __post_init__(self) -> None:
        for reason in self.reason_codes:
            require_safe_identifier(reason, "reason_codes[]")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise VisualFeedbackContractError("sync reason_codes must be unique")
        values = (
            self.offset_ms,
            self.drift_parts_per_million,
            self.root_mean_square_error_ms,
        )
        if self.state is VisualCueState.OBSERVED:
            if self.reason_codes:
                raise VisualFeedbackContractError(
                    "observed sync cannot have failure reasons"
                )
            if any(value is None for value in values):
                raise VisualFeedbackContractError(
                    "observed sync must contain all values"
                )
            for name, value in zip(
                ("offset_ms", "drift_parts_per_million", "root_mean_square_error_ms"),
                values,
                strict=True,
            ):
                require_finite_number(value, name)
            assert self.root_mean_square_error_ms is not None
            if self.root_mean_square_error_ms < 0:
                raise VisualFeedbackContractError("sync RMSE must be non-negative")
        elif any(value is not None for value in values):
            raise VisualFeedbackContractError(
                "unobserved sync must not carry numeric values"
            )
        elif not self.reason_codes:
            raise VisualFeedbackContractError("unobserved sync requires a reason")

    def video_time_ms(self, audio_time_ms: int) -> float:
        """Map an audio timestamp after the observation has passed validation."""

        if self.state is not VisualCueState.OBSERVED:
            raise VisualFeedbackContractError("cannot map with unobserved sync")
        assert self.offset_ms is not None
        assert self.drift_parts_per_million is not None
        slope = 1.0 + self.drift_parts_per_million / 1_000_000.0
        result = self.offset_ms + slope * audio_time_ms
        if not math.isfinite(result):
            raise VisualFeedbackContractError("mapped video timestamp is not finite")
        return result


@dataclass(frozen=True, slots=True)
class MediaDeletionReceipt:
    """Restricted media cleanup result exposed without a handle or path."""

    status: str
    deletion_receipt_sha256: str
    backend_receipt: AdapterReceipt

    def __post_init__(self) -> None:
        if self.status not in ("deleted", "not_applicable_no_raw_media", "failed"):
            raise VisualFeedbackContractError("deletion status is unknown")
        require_sha256(self.deletion_receipt_sha256, "deletion_receipt_sha256")

    def to_json_object(self) -> dict[str, object]:
        return {
            "status": self.status,
            "deletion_receipt_sha256": self.deletion_receipt_sha256,
            "backend_receipt": self.backend_receipt.to_json_object(),
        }


__all__ = [
    "SEUNGUN_ANALYSIS_SCHEMA_VERSION",
    "SEUNGUN_SCORE_SEMANTICS",
    "AdapterReceipt",
    "AvSyncObservation",
    "LipCueMeasurement",
    "LipLandmarkFrame",
    "LipTrackObservation",
    "MediaDeletionReceipt",
    "SeungunAnalysisSourceRecord",
    "SeungunEvidenceSnapshot",
    "SyncAnchor",
    "UpstreamPhoneAnchor",
    "VideoProbe",
    "VideoQualityObservation",
]
