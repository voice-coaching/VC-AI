"""Composition root for the local, video-backed pronunciation attempt.

The application service owns orchestration while this module is the only place
that chooses concrete local adapters.  A concrete Seungun detector is injected
by the executable composition root so this package never imports or replaces
the frozen detector implicitly.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import secrets

from voice_coach.pronunciation_feedback.adapters.catalog_r00 import (
    DEFAULT_CARD_PATH,
    R00PromptCatalog,
)
from voice_coach.pronunciation_feedback.adapters.expected_phones_subprocess import (
    SubprocessG2PKExpectedPhoneFrontend,
)
from voice_coach.visual_feedback.models.binding import UpstreamReliabilityReceipt
from voice_coach.visual_feedback.models.enums import UpstreamReliabilityStatus
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.services.handoff import SeungunAttemptBindingFacade

from .adapters.ffmpeg_av_container import FfmpegAvContainerAdapter
from .adapters.lip_audio_av_sync import (
    LipAudioAvSyncAdapter,
    LipAudioAvSyncPolicy,
)
from .adapters.mediapipe_lip_landmarks import (
    LipLandmarkRuntimeError,
    MediaPipeLandmarkPolicy,
    MediaPipeLipLandmarkAdapter,
)
from .adapters.restricted_local_media import LocalRestrictedVideoAdmissionAdapter
from .adapters.video_qc import VideoQcCandidateAdapter, VideoQcPolicy
from .adapters.runtime import (
    BlockedVisualReliabilityAdapter,
    FrozenPronunciationDetectorPort,
    FrozenSeungunRuntimeAdapter,
    LocalExplicitConsentAuthority,
    MediaPipeVisualDetectorBridge,
    R00TrustedPromptAdapter,
    SeungunVisualHandoffAdapter,
)
from .models import AvContainerPolicy, VideoAdmissionPolicy
from .services.video_attempt_facade import (
    VideoAttemptFacade,
    VisualDetection,
    VisualReliabilityDecision,
)


LOCAL_CONSENT_REFERENCE = "local_explicit_lip_processing_v1"
OFFICIAL_FACE_LANDMARKER_SHA256 = (
    "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
)
_RETENTION_POLICY_ID = "delete_on_completion_v1"
_SEUNGUN_UPSTREAM_POLICY_REVISION = (
    "seungun_final_pronunciation_detector_v1_locked_test_v1"
)


class _UnavailableLipLandmarkDetector:
    """Explicit stand-in used when the optional pinned model is not configured."""

    def detect(self, sample: object) -> object:
        del sample
        raise LipLandmarkRuntimeError("landmark_model_not_configured")


class _NeverPublishVisualSupplement:
    """Invariant adapter: P6/P8-blocked composition must never publish a claim."""

    def create(
        self,
        *,
        detection: VisualDetection,
        handoff: object,
        reliability: VisualReliabilityDecision,
    ) -> dict[str, object]:
        del detection, handoff, reliability
        raise RuntimeError("visual publication is disabled by composition")


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _attempt_nonce() -> bytes:
    return secrets.token_bytes(32)


def build_local_video_attempt_facade(
    *,
    repository_root: str | Path,
    restricted_media_root: str | Path,
    restricted_audio_root: str | Path,
    nltk_data_root: str | Path,
    seungun_detector: FrozenPronunciationDetectorPort,
    landmark_model_path: str | Path | None = None,
    card_path: str | Path | None = None,
    clock: Callable[[], datetime] = _utc_now,
    attempt_nonce: Callable[[], bytes] = _attempt_nonce,
) -> VideoAttemptFacade:
    """Build the current internal loop with visual publication locked off.

    The returned facade can complete Seungun audio feedback from a supported
    MP4.  The hash-pinned Seungun v1 result is eligible as an upstream sidecar
    anchor, without any correctness-confidence claim.  The facade may execute
    the pinned MediaPipe landmark backend, but its independent visual
    reliability adapter remains fail-closed until user-runtime P6 calibration,
    P8 locked evaluation, and claim-policy conformance artifacts exist.
    """

    root = Path(repository_root)
    if not root.is_absolute():
        raise ValueError("repository_root must be absolute")

    media_store = LocalRestrictedVideoAdmissionAdapter(
        restricted_root=restricted_media_root,
        clock=clock,
    )
    container = FfmpegAvContainerAdapter(media_store)
    consent = LocalExplicitConsentAuthority(
        consent_token=LOCAL_CONSENT_REFERENCE,
        clock=clock,
        attempt_nonce=attempt_nonce,
        retention_policy_id=_RETENTION_POLICY_ID,
    )
    expected_phone_frontend = SubprocessG2PKExpectedPhoneFrontend(
        nltk_data_root=nltk_data_root,
        repository_root=root,
        # Seungun's frozen CTC vocabulary contains Korean phone tokens only;
        # the frontend's optional ``|`` display boundary is not a model token.
        word_boundary_policy="drop",
    )
    prompt = R00TrustedPromptAdapter(
        catalog=R00PromptCatalog(card_path or root / DEFAULT_CARD_PATH),
        expected_phone_frontend=expected_phone_frontend,
    )
    seungun = FrozenSeungunRuntimeAdapter(
        media_store=media_store,
        detector=seungun_detector,
        restricted_temp_root=restricted_audio_root,
    )
    # ``supported`` is deliberately narrow: the current hash-pinned Seungun v1
    # analysis is eligible to anchor the sidecar.  It does not turn the detector
    # ranking score into calibrated correctness confidence and does not authorize
    # a visual claim without the independent visual calibration/evaluation gates.
    upstream_reliability = UpstreamReliabilityReceipt.issue(
        status=UpstreamReliabilityStatus.SUPPORTED,
        policy_revision=_SEUNGUN_UPSTREAM_POLICY_REVISION,
    )
    handoff = SeungunVisualHandoffAdapter(
        binding_facade=SeungunAttemptBindingFacade(),
        consent_receipts=consent,
        reliability_receipt=upstream_reliability,
        clock=clock,
        capture_contract_id="local_h264_aac_mp4_v1",
        population_id_sha256=sha256_document(
            {"population": "unscoped_internal_video_attempt"}
        ),
        context_id="internal_video_cli",
    )

    if landmark_model_path is None:
        landmark_detector: object = _UnavailableLipLandmarkDetector()
    else:
        landmark_detector = MediaPipeLipLandmarkAdapter(
            model_path=landmark_model_path,
            expected_model_sha256=OFFICIAL_FACE_LANDMARKER_SHA256,
            policy=MediaPipeLandmarkPolicy(
                minimum_face_detection_confidence=0.5,
                minimum_face_presence_confidence=0.5,
                minimum_tracking_confidence=0.5,
                maximum_frame_count=8,
                maximum_total_rgb_bytes=24 * 1024 * 1024,
                subprocess_timeout_seconds=120.0,
                maximum_subprocess_output_bytes=2 * 1024 * 1024,
            ),
            repository_root=root,
        )

    return VideoAttemptFacade(
        consent_port=consent,
        media_store_port=media_store,
        container_port=container,
        admission_policy=VideoAdmissionPolicy(
            maximum_source_bytes=128 * 1024 * 1024,
            required_purpose="lip_shape_feedback",
            required_data_category="facial_video",
            allowed_retention_policy_ids=(_RETENTION_POLICY_ID,),
        ),
        container_policy=AvContainerPolicy(
            minimum_duration_ms=500,
            maximum_duration_ms=30_000,
            minimum_width=160,
            maximum_width=1280,
            minimum_height=120,
            maximum_height=720,
            minimum_frames_per_second=15.0,
            maximum_frames_per_second=60.0,
            minimum_frame_count=8,
            maximum_frame_count=1_800,
            maximum_rgb_sample_frames=8,
            maximum_rgb_sample_bytes=24 * 1024 * 1024,
            maximum_pcm_bytes=2 * 1024 * 1024,
            subprocess_timeout_seconds=120.0,
            maximum_subprocess_output_bytes=4 * 1024 * 1024,
        ),
        visual_sample_frame_count=8,
        prompt_port=prompt,
        seungun_port=seungun,
        handoff_port=handoff,
        detector_port=MediaPipeVisualDetectorBridge(
            landmark_detector,
            qc_detector=VideoQcCandidateAdapter(
                policy=VideoQcPolicy(
                    # These define measurement bins and minimum evidence only;
                    # they are not P6 pass/reliability thresholds.
                    underexposed_luma_max_8bit=16,
                    overexposed_luma_min_8bit=239,
                    lip_roi_padding_fraction=0.05,
                    minimum_single_face_frames=6,
                    maximum_total_rgb_bytes=24 * 1024 * 1024,
                )
            ),
            sync_detector=LipAudioAvSyncAdapter(
                media_store=media_store,
                policy=LipAudioAvSyncPolicy(
                    maximum_absolute_offset_ms=300,
                    offset_step_ms=20,
                    energy_window_ms=40,
                    minimum_landmark_frames=6,
                    maximum_anchor_count=4,
                ),
            ),
        ),
        reliability_port=BlockedVisualReliabilityAdapter(),
        visual_port=_NeverPublishVisualSupplement(),
    )


__all__ = [
    "LOCAL_CONSENT_REFERENCE",
    "OFFICIAL_FACE_LANDMARKER_SHA256",
    "build_local_video_attempt_facade",
]
