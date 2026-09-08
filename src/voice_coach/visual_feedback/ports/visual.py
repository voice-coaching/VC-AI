"""Ports for consent, restricted media, visual observation, and artifacts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models.artifacts import VisualCalibrationArtifact, VisualClaimCatalog
from ..models.binding import (
    ConsentReceipt,
    MediaTimebaseReceipt,
    RestrictedVideoHandle,
)
from ..models.evidence import (
    AvSyncObservation,
    LipTrackObservation,
    MediaDeletionReceipt,
    VideoQualityObservation,
)


@runtime_checkable
class ConsentRightsPort(Protocol):
    """Validate purpose-limited facial-video consent before media access."""

    def validate(
        self,
        *,
        receipt: ConsentReceipt,
        video_handle: RestrictedVideoHandle,
    ) -> tuple[bool, tuple[str, ...]]: ...


@runtime_checkable
class RestrictedVideoPort(Protocol):
    """Own restricted handle lifecycle without exposing paths to services."""

    def assert_available(self, video_handle: RestrictedVideoHandle) -> None: ...

    def delete(self, video_handle: RestrictedVideoHandle) -> MediaDeletionReceipt: ...


@runtime_checkable
class VideoQualityPort(Protocol):
    """Observe video quality and frontal-pose support."""

    def observe(
        self, *, video_handle: RestrictedVideoHandle
    ) -> VideoQualityObservation: ...


@runtime_checkable
class LipTrackingPort(Protocol):
    """Return aggregate visible-lip cues for a requested video window."""

    def track(
        self,
        *,
        video_handle: RestrictedVideoHandle,
        video_start_ms: int,
        video_end_ms: int,
    ) -> LipTrackObservation: ...


@runtime_checkable
class AvSyncPort(Protocol):
    """Measure the audio-to-video clock transform from matched events."""

    def measure(
        self,
        *,
        video_handle: RestrictedVideoHandle,
        audio_timebase: MediaTimebaseReceipt,
        video_timebase: MediaTimebaseReceipt,
    ) -> AvSyncObservation: ...


@runtime_checkable
class VisualCalibrationPort(Protocol):
    """Load the frozen visual calibration artifact for a capture contract."""

    def load(self, *, capture_contract_id: str) -> VisualCalibrationArtifact: ...


@runtime_checkable
class VisualClaimCatalogPort(Protocol):
    """Load the frozen allowlist of visual action and claim identifiers."""

    def load(self) -> VisualClaimCatalog: ...


__all__ = [
    "AvSyncPort",
    "ConsentRightsPort",
    "LipTrackingPort",
    "RestrictedVideoPort",
    "VideoQualityPort",
    "VisualCalibrationPort",
    "VisualClaimCatalogPort",
]
