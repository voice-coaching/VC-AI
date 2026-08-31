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
from voice_coach.visual_feedback.models.runtime_eligibility import (
    AutomatedVisualRuntimeEligibilityReceipt,
    VisualRuntimeBundle,
)
from voice_coach.visual_feedback.services.handoff import SeungunAttemptBindingFacade

from .adapters.ffmpeg_av_container import FfmpegAvContainerAdapter
from .adapters.calibrated_runtime_decisions import (
    CalibratedAvSyncDecisionAdapter,
    CalibratedAvSyncDecisionPolicy,
    CalibratedVideoQcDecisionAdapter,
    CalibratedVideoQcDecisionPolicy,
)
from .adapters.lip_audio_av_sync import (
    LipAudioAvSyncAdapter,
    LipAudioAvSyncPolicy,
)
from .adapters.mediapipe_lip_landmarks import (
    LipLandmarkRuntimeError,
    MediaPipeLandmarkPolicy,
    MediaPipeLipLandmarkAdapter,
)
from .adapters.production_visual_sidecar import ProductionVisualSidecarAdapter
from .adapters.restricted_local_media import LocalRestrictedVideoAdmissionAdapter
from .adapters.video_qc import VideoQcCandidateAdapter, VideoQcPolicy
from .adapters.runtime import (
    BlockedVisualReliabilityAdapter,
    FrozenPronunciationDetectorPort,
    FrozenSeungunRuntimeAdapter,
    FixedLocalTrustedPromptAdapter,
    LocalExplicitConsentAuthority,
    MediaPipeVisualDetectorBridge,
    R00TrustedPromptAdapter,
    SeungunVisualHandoffAdapter,
)
from .models import AvContainerPolicy, VideoAdmissionPolicy
from .phone_window import PhoneWindowDecodePolicy
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
_CAPTURE_CONTRACT_ID = "local_allowlisted_video_aac_mp4_v2"
_CONTEXT_ID = "internal_video_cli"


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
    fixed_local_prompt: tuple[str, str] | None = None,
    clock: Callable[[], datetime] = _utc_now,
    attempt_nonce: Callable[[], bytes] = _attempt_nonce,
    video_qc_decision_policy: CalibratedVideoQcDecisionPolicy | None = None,
    av_sync_decision_policy: CalibratedAvSyncDecisionPolicy | None = None,
    visual_runtime_bundle: VisualRuntimeBundle | None = None,
    visual_runtime_eligibility: AutomatedVisualRuntimeEligibilityReceipt | None = None,
) -> VideoAttemptFacade:
    """Build the local loop, enabling visual publication only from promoted state.

    The returned facade can complete Seungun audio feedback from a supported
    MP4.  The hash-pinned Seungun v1 result is eligible as an upstream sidecar
    anchor, without any correctness-confidence claim.  The facade may execute
    the pinned MediaPipe landmark backend, but its independent visual
    reliability adapter remains fail-closed unless the caller injects a strictly
    reloadable P6/P8 runtime bundle, its exact eligibility receipt, and the
    calibrated QC/A/V decision policies pinned by that bundle.
    """

    root = Path(repository_root)
    if not root.is_absolute():
        raise ValueError("repository_root must be absolute")
    if (video_qc_decision_policy is None) != (av_sync_decision_policy is None):
        raise ValueError(
            "calibrated QC and A/V sync decision policies must be paired"
        )
    if (visual_runtime_bundle is None) != (visual_runtime_eligibility is None):
        raise ValueError("visual runtime bundle and eligibility must be paired")
    if visual_runtime_bundle is not None and video_qc_decision_policy is None:
        raise ValueError(
            "visual runtime requires calibrated QC and A/V sync decision policies"
        )
    if visual_runtime_bundle is not None:
        identity = visual_runtime_bundle.pipeline_identity
        if (
            identity.media_capture_contract_id != _CAPTURE_CONTRACT_ID
            or identity.video_qc_decision_policy_sha256
            != video_qc_decision_policy.policy_sha256
            or av_sync_decision_policy is None
            or identity.av_sync_decision_policy_sha256
            != av_sync_decision_policy.policy_sha256
        ):
            raise ValueError("visual runtime policy identity differs")

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
    if fixed_local_prompt is None:
        prompt = R00TrustedPromptAdapter(
            catalog=R00PromptCatalog(card_path or root / DEFAULT_CARD_PATH),
            expected_phone_frontend=expected_phone_frontend,
        )
    else:
        if card_path is not None:
            raise ValueError("fixed_local_prompt and card_path are mutually exclusive")
        prompt = FixedLocalTrustedPromptAdapter(
            prompt_id=fixed_local_prompt[0],
            normalized_text=fixed_local_prompt[1],
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
        capture_contract_id=_CAPTURE_CONTRACT_ID,
        population_id_sha256=(
            visual_runtime_bundle.pipeline_identity.population_id_sha256
            if visual_runtime_bundle is not None
            else sha256_document({"population": "unscoped_internal_video_attempt"})
        ),
        context_id=_CONTEXT_ID,
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
                maximum_frame_count=12,
                maximum_total_rgb_bytes=80 * 1024 * 1024,
                subprocess_timeout_seconds=120.0,
                maximum_subprocess_output_bytes=2 * 1024 * 1024,
            ),
            repository_root=root,
        )

    sync_policy = LipAudioAvSyncPolicy(
        maximum_absolute_offset_ms=300,
        offset_step_ms=20,
        energy_window_ms=40,
        minimum_landmark_frames=6,
        maximum_anchor_count=4,
    )
    sync_policy_sha256 = sha256_document(
        {
            "schema_version": "voice_coach.lip_audio_av_sync_policy.v1",
            **sync_policy.to_json_object(),
        }
    )
    qc_policy = VideoQcPolicy(
        # These define measurement bins and minimum evidence only;
        # they are not P6 pass/reliability thresholds.
        underexposed_luma_max_8bit=16,
        overexposed_luma_min_8bit=239,
        lip_roi_padding_fraction=0.05,
        minimum_single_face_frames=6,
        maximum_total_rgb_bytes=64 * 1024 * 1024,
    )
    if video_qc_decision_policy is not None and (
        video_qc_decision_policy.expected_measurement_policy_sha256
        != qc_policy.receipt_sha256
        or av_sync_decision_policy is None
        or av_sync_decision_policy.expected_measurement_policy_sha256
        != sync_policy_sha256
    ):
        raise ValueError(
            "calibrated decisions do not bind composition measurement policies"
        )
    visual_detector = MediaPipeVisualDetectorBridge(
        landmark_detector,
        qc_detector=VideoQcCandidateAdapter(policy=qc_policy),
        sync_detector=LipAudioAvSyncAdapter(
            media_store=media_store,
            policy=sync_policy,
        ),
        qc_decision_adapter=(
            CalibratedVideoQcDecisionAdapter(
                policy=video_qc_decision_policy
            )
            if video_qc_decision_policy is not None
            else None
        ),
        sync_decision_adapter=(
            CalibratedAvSyncDecisionAdapter(policy=av_sync_decision_policy)
            if av_sync_decision_policy is not None
            else None
        ),
        expected_video_qc_decision_policy_sha256=(
            video_qc_decision_policy.policy_sha256
            if video_qc_decision_policy is not None
            else None
        ),
        expected_av_sync_decision_policy_sha256=(
            av_sync_decision_policy.policy_sha256
            if av_sync_decision_policy is not None
            else None
        ),
        phone_window_policy=PhoneWindowDecodePolicy(
            policy_revision="selected_phone_rgb24_v1",
            expected_av_sync_policy_sha256=sync_policy_sha256,
            context_contract_sha256=sha256_document(
                {
                    "capture_contract_id": _CAPTURE_CONTRACT_ID,
                    "context_id": _CONTEXT_ID,
                    "video_codecs": ["h264", "hevc"],
                    "audio_codec": "aac",
                }
            ),
            maximum_absolute_offset_ms=sync_policy.maximum_absolute_offset_ms,
            padding_before_ms=100,
            padding_after_ms=100,
            minimum_frame_count=2,
            maximum_frame_count=12,
            maximum_window_duration_ms=1_000,
            maximum_rgb_bytes=80 * 1024 * 1024,
        ),
    )

    visual_sidecar = (
        ProductionVisualSidecarAdapter(
            runtime_bundle=visual_runtime_bundle,
            eligibility_receipt=visual_runtime_eligibility,
        )
        if visual_runtime_bundle is not None
        and visual_runtime_eligibility is not None
        else None
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
            maximum_width=1920,
            minimum_height=120,
            maximum_height=1920,
            minimum_frames_per_second=15.0,
            maximum_frames_per_second=60.0,
            minimum_frame_count=8,
            maximum_frame_count=1_800,
            maximum_rgb_sample_frames=8,
            maximum_rgb_sample_bytes=64 * 1024 * 1024,
            maximum_pcm_bytes=2 * 1024 * 1024,
            subprocess_timeout_seconds=120.0,
            maximum_subprocess_output_bytes=4 * 1024 * 1024,
        ),
        visual_sample_frame_count=8,
        prompt_port=prompt,
        seungun_port=seungun,
        handoff_port=handoff,
        detector_port=visual_detector,
        reliability_port=(
            visual_sidecar
            if visual_sidecar is not None
            else BlockedVisualReliabilityAdapter()
        ),
        visual_port=(
            visual_sidecar
            if visual_sidecar is not None
            else _NeverPublishVisualSupplement()
        ),
        phone_window_decoder_port=container,
        phone_window_detector_port=visual_detector,
    )


__all__ = [
    "LOCAL_CONSENT_REFERENCE",
    "OFFICIAL_FACE_LANDMARKER_SHA256",
    "build_local_video_attempt_facade",
]
