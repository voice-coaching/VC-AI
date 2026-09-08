"""Boundary adapters for Seungun, restricted video, and frozen artifacts."""

from .artifacts import (
    InMemoryVisualCalibrationAdapter,
    InMemoryVisualClaimCatalogAdapter,
    JsonVisualCalibrationAdapter,
    JsonVisualClaimCatalogAdapter,
)
from .seungun import (
    FrozenSeungunPronunciationEvidenceAdapter,
    InMemorySeungunAnalysisSource,
    SeungunAnalysisSource,
    SeungunAnalysisSourceRecord,
)
from .video import (
    DeterministicVideoQualityAdapter,
    InMemoryRestrictedVideoAdapter,
    PrecomputedLipLandmarkTrackingAdapter,
    StaticConsentRightsAdapter,
    TimestampAvSyncAdapter,
)
from .unavailable import (
    UnavailableContentAvSyncAdapter,
    UnavailableLandmarkVideoQualityAdapter,
    UnavailableLipLandmarkTrackingAdapter,
)

__all__ = [
    "DeterministicVideoQualityAdapter",
    "FrozenSeungunPronunciationEvidenceAdapter",
    "InMemoryRestrictedVideoAdapter",
    "InMemorySeungunAnalysisSource",
    "InMemoryVisualCalibrationAdapter",
    "InMemoryVisualClaimCatalogAdapter",
    "JsonVisualCalibrationAdapter",
    "JsonVisualClaimCatalogAdapter",
    "PrecomputedLipLandmarkTrackingAdapter",
    "SeungunAnalysisSource",
    "SeungunAnalysisSourceRecord",
    "StaticConsentRightsAdapter",
    "TimestampAvSyncAdapter",
    "UnavailableContentAvSyncAdapter",
    "UnavailableLandmarkVideoQualityAdapter",
    "UnavailableLipLandmarkTrackingAdapter",
]
