"""Missing visual runtimes remain explicit and never reach artifacts."""

from pathlib import Path

from voice_coach.visual_feedback.adapters.unavailable import (
    UnavailableContentAvSyncAdapter,
    UnavailableLandmarkVideoQualityAdapter,
    UnavailableLipLandmarkTrackingAdapter,
)
from voice_coach.visual_feedback.models.enums import (
    VisualCueState,
    VisualFeedbackStatus,
    VideoQualityStatus,
)
from voice_coach.visual_feedback.testing import build_internal_contract_fixture

ROOT = Path(__file__).resolve().parents[2]


def test_unavailable_runtime_never_invents_qc_landmarks_or_sync() -> None:
    fixture = build_internal_contract_fixture(repository_root=ROOT)

    quality = UnavailableLandmarkVideoQualityAdapter().observe(
        video_handle=fixture.request.video_handle
    )
    tracking = UnavailableLipLandmarkTrackingAdapter().track(
        video_handle=fixture.request.video_handle,
        video_start_ms=100,
        video_end_ms=200,
    )
    sync = UnavailableContentAvSyncAdapter().measure(
        video_handle=fixture.request.video_handle,
        audio_timebase=fixture.request.binding_receipt.audio_timebase,
        video_timebase=fixture.request.binding_receipt.video_timebase,
    )

    assert quality.status is VideoQualityStatus.FAILED_CLOSED
    assert quality.reason_codes == ("lip_landmark_backend_unavailable",)
    assert tracking.cues[0].state is VisualCueState.BACKEND_UNAVAILABLE
    assert tracking.cues[0].value is None
    assert sync.state is VisualCueState.BACKEND_UNAVAILABLE
    assert sync.offset_ms is None


def test_backend_unavailable_tracking_stops_before_artifact_lookup() -> None:
    fixture = build_internal_contract_fixture(repository_root=ROOT)

    class ExplodingArtifactPort:
        def load(self, **kwargs):  # noqa: ANN003, ANN201 - assertion double
            del kwargs
            raise AssertionError("artifact lookup must not run")

    fixture.coordinator._tracking_port = (  # noqa: SLF001 - white-box gate test
        UnavailableLipLandmarkTrackingAdapter()
    )
    fixture.coordinator._calibration_port = (  # noqa: SLF001
        ExplodingArtifactPort()
    )
    fixture.coordinator._claim_catalog_port = (  # noqa: SLF001
        ExplodingArtifactPort()
    )

    result = fixture.coordinator.create_visual_feedback(fixture.request)

    assert result.status is VisualFeedbackStatus.VISUAL_UNCERTAIN
    assert result.reason_codes == ("lip_landmark_backend_unavailable",)
    assert result.supplemental_action_candidate is None
    assert result.reference_calibration_receipts == ()
    assert result.media_deletion_receipt.status == "deleted"
