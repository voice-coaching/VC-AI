"""Synthetic-only fixture wiring for the visual sidecar contract smoke."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .adapters.artifacts import (
    JsonVisualCalibrationAdapter,
    JsonVisualClaimCatalogAdapter,
)
from .adapters.seungun import (
    FrozenSeungunPronunciationEvidenceAdapter,
    InMemorySeungunAnalysisSource,
    SeungunAnalysisSourceRecord,
)
from .adapters.video import (
    DeterministicVideoQualityAdapter,
    InMemoryRestrictedVideoAdapter,
    PrecomputedLipLandmarkTrackingAdapter,
    StaticConsentRightsAdapter,
    TimestampAvSyncAdapter,
)
from .models.binding import (
    ConsentReceipt,
    MediaTimebaseReceipt,
    RestrictedVideoHandle,
    SameAttemptBindingReceipt,
    SeungunAnalysisReference,
    UpstreamReliabilityReceipt,
    expected_phone_sequence_sha256,
)
from .models.enums import (
    ConsentDecision,
    ExecutionScope,
    UpstreamReliabilityStatus,
)
from .models.evidence import (
    SEUNGUN_ANALYSIS_SCHEMA_VERSION,
    SEUNGUN_SCORE_SEMANTICS,
    LipLandmarkFrame,
    SyncAnchor,
    VideoProbe,
)
from .models.integrity import sha256_document
from .models.request import (
    AvSyncPolicy,
    LipTrackingPolicy,
    RestrictedVideoRecord,
    VideoQualityPolicy,
    VisualFeedbackRequest,
)
from .services.coordinator import VisualFeedbackCoordinator

_CALIBRATION_SHA256 = (
    "ef9a9a55507e0fb0d84418df91b6f5aecfe7cce5e6df007b4444df59b800cb77"
)
_CATALOG_SHA256 = (
    "12319572e1b897222a24af377b93b43248311a3b684418dbaa033fabb71f14cc"
)
INTERNAL_POPULATION_SHA256 = (
    "9c639f0f277af48370d45937fe57398be30e0ba12b24a8111f77b5f6c7c06e49"
)
INTERNAL_CAPTURE_CONTRACT_ID = "internal_precomputed_landmarks_v1"


@dataclass(frozen=True, slots=True)
class InternalContractFixture:
    """Inspectable wiring and counters for one single-use synthetic attempt."""

    request: VisualFeedbackRequest
    coordinator: VisualFeedbackCoordinator
    video_store: InMemoryRestrictedVideoAdapter
    evidence_adapter: FrozenSeungunPronunciationEvidenceAdapter
    consent_adapter: StaticConsentRightsAdapter
    quality_adapter: DeterministicVideoQualityAdapter
    tracking_adapter: PrecomputedLipLandmarkTrackingAdapter
    sync_adapter: TimestampAvSyncAdapter


def build_internal_contract_fixture(
    *,
    repository_root: Path,
    execution_scope: ExecutionScope = ExecutionScope.INTERNAL_CONTRACT_TEST,
    consent_decision: ConsentDecision = ConsentDecision.GRANTED,
    blur_score: float = 0.9,
    sync_anchors: tuple[SyncAnchor, ...] | None = None,
    upstream_is_error: bool = True,
    aperture_ratio: float = 0.3,
    consent_issued_at_utc: str = "2026-08-26T00:00:00Z",
    consent_expires_at_utc: str = "2026-08-28T00:00:00Z",
    binding_issued_at_utc: str = "2026-08-26T00:00:01Z",
) -> InternalContractFixture:
    """Wire one deterministic fixture; it never opens media or invokes a model."""

    attempt_id = _fixture_sha256("attempt")
    prompt_id = _fixture_sha256("prompt")
    audio_source = _fixture_sha256("audio_source")
    audio_derivation_receipt = _fixture_sha256("audio_derivation_receipt")
    video_source = _fixture_sha256("video_source")
    expected_phones = ("ㅏ",)
    expected_phone_sha = expected_phone_sequence_sha256(expected_phones)
    audio_timebase = MediaTimebaseReceipt(
        clock_id_sha256=_fixture_sha256("audio_clock"),
        duration_ms=1_000,
        origin_offset_ms=0,
        transform_revision="internal_clock_v1",
    )
    video_timebase = MediaTimebaseReceipt(
        clock_id_sha256=_fixture_sha256("video_clock"),
        duration_ms=1_000,
        origin_offset_ms=0,
        transform_revision="internal_clock_v1",
    )
    reliability = UpstreamReliabilityReceipt.issue(
        status=UpstreamReliabilityStatus.SUPPORTED,
        policy_revision="internal_reliability_v1",
    )
    consent = ConsentReceipt.issue(
        attempt_id_sha256=attempt_id,
        decision=consent_decision,
        retention_policy_id="internal_delete_on_completion_v1",
        issued_at_utc=consent_issued_at_utc,
        expires_at_utc=consent_expires_at_utc,
    )
    analysis = _analysis_document(upstream_is_error=upstream_is_error)
    analysis_sha = sha256_document(analysis)
    analysis_ref = SeungunAnalysisReference(
        reference_id=_fixture_sha256("analysis_reference"),
        attempt_id_sha256=attempt_id,
        analysis_sha256=analysis_sha,
    )
    video_handle = RestrictedVideoHandle(
        handle_id=_fixture_sha256("video_handle"),
        attempt_id_sha256=attempt_id,
        source_sha256=video_source,
        timebase_sha256=video_timebase.timebase_sha256,
    )
    binding = SameAttemptBindingReceipt.issue(
        attempt_id_sha256=attempt_id,
        prompt_id_sha256=prompt_id,
        prompt_revision="internal_prompt_v1",
        expected_phone_sha256=expected_phone_sha,
        analysis_sha256=analysis_sha,
        audio_source_sha256=audio_source,
        audio_derivation_receipt_sha256=audio_derivation_receipt,
        video_source_sha256=video_source,
        audio_timebase=audio_timebase,
        video_timebase=video_timebase,
        upstream_reliability_receipt_sha256=reliability.receipt_sha256,
        consent_receipt_sha256=consent.receipt_sha256,
        issued_at_utc=binding_issued_at_utc,
    )
    source_record = SeungunAnalysisSourceRecord(
        reference=analysis_ref,
        analysis=analysis,
        prompt_id_sha256=prompt_id,
        prompt_revision="internal_prompt_v1",
        expected_phone_sha256=expected_phone_sha,
        audio_source_sha256=audio_source,
        audio_timebase=audio_timebase,
        reliability_receipt=reliability,
        binding_receipt_sha256=binding.receipt_sha256,
        audio_derivation_receipt_sha256=audio_derivation_receipt,
    )
    evidence_adapter = FrozenSeungunPronunciationEvidenceAdapter(
        InMemorySeungunAnalysisSource(
            {analysis_ref.reference_id: source_record}
        )
    )

    frames = _lip_frames(aperture_ratio=aperture_ratio)
    record = RestrictedVideoRecord(
        handle=video_handle,
        audio_timebase=audio_timebase,
        video_timebase=video_timebase,
        probe=VideoProbe(
            duration_ms=1_000,
            frame_count=30,
            frames_per_second=30.0,
            tracked_face_count=1,
            maximum_absolute_yaw_degrees=3.0,
            maximum_absolute_pitch_degrees=3.0,
            maximum_absolute_roll_degrees=2.0,
            blur_score=blur_score,
            occlusion_fraction=0.05,
            lighting_score=0.9,
            lip_roi_fraction=0.08,
            timestamps_monotonic=True,
        ),
        lip_frames=frames,
        sync_anchors=(
            sync_anchors
            if sync_anchors is not None
            else (
                SyncAnchor(audio_ms=0, video_ms=0),
                SyncAnchor(audio_ms=500, video_ms=500),
                SyncAnchor(audio_ms=1_000, video_ms=1_000),
            )
        ),
    )
    video_store = InMemoryRestrictedVideoAdapter(
        {video_handle.handle_id: record}
    )
    consent_adapter = StaticConsentRightsAdapter(
        now_utc=datetime(2026, 8, 27, tzinfo=UTC),
        allowed_retention_policy_ids=("internal_delete_on_completion_v1",),
    )
    quality_adapter = DeterministicVideoQualityAdapter(
        video_store,
        VideoQualityPolicy(
            maximum_absolute_pose_degrees=20.0,
            minimum_blur_score=0.5,
            maximum_occlusion_fraction=0.3,
            minimum_lighting_score=0.4,
            minimum_lip_roi_fraction=0.02,
        ),
    )
    tracking_adapter = PrecomputedLipLandmarkTrackingAdapter(
        video_store,
        LipTrackingPolicy(
            minimum_frame_visibility=0.8,
            minimum_visible_frame_fraction=0.6,
            closure_aperture_ratio=0.05,
        ),
    )
    sync_adapter = TimestampAvSyncAdapter(
        video_store,
        AvSyncPolicy(
            maximum_absolute_offset_ms=40.0,
            maximum_absolute_drift_parts_per_million=2_000.0,
            maximum_root_mean_square_error_ms=10.0,
            minimum_anchor_count=3,
        ),
    )
    calibration_path = (
        repository_root
        / "configs/visual_feedback/internal_visual_calibration_v1.json"
    )
    catalog_path = (
        repository_root
        / "configs/visual_feedback/internal_visual_claim_catalog_v1.json"
    )
    coordinator = VisualFeedbackCoordinator(
        evidence_port=evidence_adapter,
        consent_port=consent_adapter,
        video_port=video_store,
        quality_port=quality_adapter,
        tracking_port=tracking_adapter,
        sync_port=sync_adapter,
        calibration_port=JsonVisualCalibrationAdapter(
            {INTERNAL_CAPTURE_CONTRACT_ID: calibration_path},
            expected_artifact_sha256={
                INTERNAL_CAPTURE_CONTRACT_ID: _CALIBRATION_SHA256
            },
        ),
        claim_catalog_port=JsonVisualClaimCatalogAdapter(
            catalog_path,
            expected_catalog_sha256=_CATALOG_SHA256,
        ),
        execution_scope=execution_scope,
    )
    request = VisualFeedbackRequest(
        analysis_ref=analysis_ref,
        upstream_phone_expected_index=0,
        video_handle=video_handle,
        binding_receipt=binding,
        consent_receipt=consent,
        capture_contract_id=INTERNAL_CAPTURE_CONTRACT_ID,
        population_id_sha256=INTERNAL_POPULATION_SHA256,
        context_id="phone_center",
    )
    return InternalContractFixture(
        request=request,
        coordinator=coordinator,
        video_store=video_store,
        evidence_adapter=evidence_adapter,
        consent_adapter=consent_adapter,
        quality_adapter=quality_adapter,
        tracking_adapter=tracking_adapter,
        sync_adapter=sync_adapter,
    )


def _analysis_document(*, upstream_is_error: bool) -> dict[str, object]:
    return {
        "schema_version": SEUNGUN_ANALYSIS_SCHEMA_VERSION,
        "detector_version": "internal_fixture_detector_v1",
        "detector_config_sha256": _fixture_sha256("detector_config"),
        "backbone_name": "internal_fixture_backbone",
        "backbone_tree_sha256": _fixture_sha256("backbone_tree"),
        "expected_phones": ["ㅏ"],
        "ctc_hypothesis": ["ㅓ"],
        "insertions": [],
        "phones": [
            {
                "expected_index": 0,
                "expected_phone": "ㅏ",
                "observed_candidate": "ㅓ",
                "alignment_operation": "substitution",
                "start_ms": 200,
                "end_ms": 400,
                "error_score": 0.91 if upstream_is_error else 0.2,
                "operating_threshold": 0.8,
                "is_error": upstream_is_error,
                "score_semantics": SEUNGUN_SCORE_SEMANTICS,
                "soft_features": {},
            }
        ],
        "notices": ["synthetic contract fixture only"],
    }


def _lip_frames(*, aperture_ratio: float) -> tuple[LipLandmarkFrame, ...]:
    width = 0.4
    upper = 0.4
    lower = upper + width * aperture_ratio
    return tuple(
        LipLandmarkFrame(
            timestamp_ms=timestamp,
            upper_lip_y=upper,
            lower_lip_y=lower,
            left_corner_x=0.3,
            right_corner_x=0.7,
            visibility=0.99,
        )
        for timestamp in (200, 250, 300, 350, 400)
    )


def _fixture_sha256(label: str) -> str:
    return sha256_document({"internal_visual_fixture": label})


__all__ = [
    "INTERNAL_CAPTURE_CONTRACT_ID",
    "INTERNAL_POPULATION_SHA256",
    "InternalContractFixture",
    "build_internal_contract_fixture",
]
