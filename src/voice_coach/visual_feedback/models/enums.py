"""Exact public and internal state vocabularies for the visual sidecar."""

from enum import StrEnum


class VisualFeedbackStatus(StrEnum):
    """Terminal result of one visual sidecar invocation."""

    VISUAL_FEEDBACK_READY = "visual_feedback_ready"
    NO_SUPPORTED_LIP_CUE = "no_supported_lip_cue"
    VISUAL_RERECORD_REQUIRED = "visual_rerecord_required"
    VISUAL_UNCERTAIN = "visual_uncertain"
    VISUAL_UNSUPPORTED = "visual_unsupported"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    VISUAL_FAILED_CLOSED = "visual_failed_closed"


class VisualCueState(StrEnum):
    """Observability and quality of one visible lip cue."""

    OBSERVED = "observed"
    NOT_OBSERVABLE = "not_observable"
    MISSING = "missing"
    LOW_CONFIDENCE = "low_confidence"
    OUT_OF_DISTRIBUTION = "out_of_distribution"
    BACKEND_UNAVAILABLE = "backend_unavailable"


class VisualEvidenceRelation(StrEnum):
    """Relationship between visual evidence and immutable upstream evidence."""

    SUPPORTS_UPSTREAM = "supports_upstream"
    CONFLICTS_WITH_UPSTREAM = "conflicts_with_upstream"
    NOT_COMPARABLE = "not_comparable"


class UpstreamReliabilityStatus(StrEnum):
    """Whether the seungun result is eligible for supplemental visual use."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class ConsentDecision(StrEnum):
    """Recorded facial-video processing decision."""

    GRANTED = "granted"
    DENIED = "denied"


class VideoQualityStatus(StrEnum):
    """Internal quality outcome before cue extraction."""

    PASSED = "passed"
    RERECORD_REQUIRED = "rerecord_required"
    UNSUPPORTED = "unsupported"
    FAILED_CLOSED = "failed_closed"


class CalibrationScope(StrEnum):
    """Evidence scope carried by calibration and catalog artifacts."""

    CALIBRATED = "calibrated"
    SYNTHETIC_CONTRACT_ONLY = "synthetic_contract_only"


class ExecutionScope(StrEnum):
    """Separates internal contract smoke from user-facing execution."""

    USER_RUNTIME = "user_runtime"
    INTERNAL_CONTRACT_TEST = "internal_contract_test"


class DeviationDirection(StrEnum):
    """Direction of a calibrated visual deviation used by an allowlisted claim."""

    BELOW_REFERENCE = "below_reference"
    ABOVE_REFERENCE = "above_reference"


__all__ = [
    "CalibrationScope",
    "ConsentDecision",
    "DeviationDirection",
    "ExecutionScope",
    "UpstreamReliabilityStatus",
    "VideoQualityStatus",
    "VisualCueState",
    "VisualEvidenceRelation",
    "VisualFeedbackStatus",
]
