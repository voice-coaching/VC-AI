from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import inspect
import os
from pathlib import Path
from types import SimpleNamespace
import stat
import wave

import pytest

from voice_coach.pronunciation_attempts.adapters import (
    ffmpeg_av_container as ffmpeg_adapter_module,
)
from voice_coach.pronunciation_attempts.adapters import runtime as runtime_module
from voice_coach.pronunciation_attempts.adapters.runtime import (
    BlockedVisualReliabilityAdapter,
    FixedLocalTrustedPromptAdapter,
    FrozenSeungunRuntimeAdapter,
    LocalExplicitConsentAuthority,
    MediaPipeVisualDetectorBridge,
    PronunciationAttemptRuntimeError,
    R00TrustedPromptAdapter,
    SeungunVisualHandoffAdapter,
)
from voice_coach.pronunciation_attempts.adapters.calibrated_runtime_decisions import (
    CalibratedAvSyncDecisionAdapter,
    CalibratedAvSyncDecisionPolicy,
    CalibratedVideoQcDecisionAdapter,
    CalibratedVideoQcDecisionPolicy,
)
from voice_coach.pronunciation_attempts.adapters.lip_audio_av_sync import (
    MEASUREMENT_REVISION as AV_SYNC_MEASUREMENT_REVISION,
    AvSyncAnchorCandidate,
    AvSyncCandidateEvidence,
    av_sync_measurement_code_sha256,
)
from voice_coach.pronunciation_attempts.adapters.video_qc import (
    MEASUREMENT_REVISION as VIDEO_QC_MEASUREMENT_REVISION,
    VideoQcCandidateEvidence,
    VideoQcMetrics,
    video_qc_measurement_code_sha256,
)
from voice_coach.pronunciation_attempts.models import (
    AudioDerivationReceipt,
    AudioStreamProbe,
    DecodedRgbFrame,
    MediaProbe,
    RestrictedPcmAudio,
    RestrictedVideoAsset,
    Rgb24Sample,
    VideoConsentAuthorization,
    VideoConsentDecision,
    VideoStreamProbe,
)
from voice_coach.pronunciation_attempts.phone_window import (
    PhoneWindowDecodePolicy,
    PhoneWindowDecodeReceipt,
    PolicyValidatedAvSyncOffset,
    Rgb24PhoneWindow,
    decoded_rgb_sha256,
    phone_window_code_sha256,
)
from voice_coach.pronunciation_attempts.services.video_attempt_facade import (
    HostVisualHandoff,
    SeungunRuntimeResult,
    TrustedPrompt,
)
from voice_coach.pronunciation_feedback.domain import (
    ExpectedPhoneSequence,
    PracticePrompt,
    TargetLanguage,
)
from voice_coach.pronunciation_feedback.seungun_bridge import (
    SEUNGUN_SCHEMA_VERSION,
    SEUNGUN_SCORE_SEMANTICS,
)
from voice_coach.visual_feedback.models.binding import UpstreamReliabilityReceipt
from voice_coach.visual_feedback.models.enums import UpstreamReliabilityStatus
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.services.handoff import (
    SeungunAttemptBindingFacade,
    SeungunVisualHandoff,
)


_NOW = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
_CONTAINER_SHA256 = "b" * 64
_ASSET_HANDLE_ID = "d" * 64
_PCM = b"\x00\x00" * 1600
_PCM_SHA256 = hashlib.sha256(_PCM).hexdigest()


def test_fixed_local_prompt_is_exact_and_uses_expected_phone_frontend() -> None:
    class Frontend:
        def expected_phones(self, text: str) -> ExpectedPhoneSequence:
            assert text == "쌀을 깨끗이 씻어요"
            return ExpectedPhoneSequence(
                tokens=("ㅆ", "ㅏ", "ㄹ"),
                token_sha256="a" * 64,
                backend_id="g2pk-expected-phones",
                revision="0.9.4",
            )

    adapter = FixedLocalTrustedPromptAdapter(
        prompt_id="LOCAL-TEST-RICE-CLEAN-V1",
        normalized_text="쌀을 깨끗이 씻어요",
        expected_phone_frontend=Frontend(),  # type: ignore[arg-type]
    )

    prompt = adapter.resolve(prompt_id="LOCAL-TEST-RICE-CLEAN-V1")
    assert prompt.normalized_text == "쌀을 깨끗이 씻어요"
    assert prompt.expected_phones == ("ㅆ", "ㅏ", "ㄹ")
    with pytest.raises(PronunciationAttemptRuntimeError):
        adapter.resolve(prompt_id="R00-01")


def test_explicit_consent_requires_exact_token_and_issues_both_receipts() -> None:
    nonces = iter((b"g" * 32, b"d" * 32))
    authority = LocalExplicitConsentAuthority(
        consent_token="explicit-consent-token",
        clock=lambda: _NOW,
        attempt_nonce=lambda: next(nonces),
    )

    granted = authority.authorize(
        prompt_id="R00-S001",
        consent_reference="explicit-consent-token",
    )
    denied = authority.authorize(
        prompt_id="R00-S001",
        consent_reference="explicit-consent-token ",
    )

    assert granted.decision is VideoConsentDecision.GRANTED
    assert denied.decision is VideoConsentDecision.DENIED
    assert granted.remote_egress_allowed is False
    assert granted.delete_on_completion is True
    receipt = authority.receipt_for(granted)
    assert receipt.receipt_sha256 == granted.consent_receipt_sha256
    assert receipt.attempt_id_sha256 == granted.attempt_id_sha256
    assert receipt.remote_egress_allowed is False
    assert authority.receipt_for(denied).decision.value == "denied"


def test_r00_prompt_adapter_hashes_catalog_and_g2pk_lineage() -> None:
    prompt = PracticePrompt(
        prompt_id="R00-S001",
        display_text="가나다.",
        normalized_text="가나다.",
        normalized_text_sha256=sha256_document({"text": "가나다."}),
        difficulty="beginner",
        syllable_count=3,
        word_count=1,
        practice_targets=(),
        license_spdx="CC0-1.0",
        catalog_revision="r00-card-revision",
        target_language=TargetLanguage.KOREAN,
    )
    sequence = ExpectedPhoneSequence(
        tokens=("ㄱ", "ㅏ"),
        token_sha256="1" * 64,
        backend_id="g2pk-pinned",
        revision="g2pk-1.0.3",
    )

    class Catalog:
        def get_prompt(self, prompt_id: str) -> PracticePrompt:
            assert prompt_id == prompt.prompt_id
            return prompt

    class Frontend:
        def expected_phones(self, normalized_text: str) -> ExpectedPhoneSequence:
            assert normalized_text == prompt.normalized_text
            return sequence

    adapter = R00TrustedPromptAdapter(
        catalog=Catalog(),  # type: ignore[arg-type]
        expected_phone_frontend=Frontend(),  # type: ignore[arg-type]
    )

    trusted = adapter.resolve(prompt_id=prompt.prompt_id)

    assert trusted.expected_phones == sequence.tokens
    assert trusted.revision.startswith("r00_g2pk_")
    assert len(trusted.revision) == len("r00_g2pk_") + 64
    assert "가나다" not in trusted.revision


def test_runtime_has_no_concrete_detector_import() -> None:
    source = inspect.getsource(runtime_module)
    assert "import korean_phone_ctc" not in source
    assert "from korean_phone_ctc" not in source


def test_runtime_calls_detector_once_and_deletes_restricted_temp_wav(
    tmp_path: Path,
) -> None:
    audio = _audio()
    detector = _RecordingDetector(_analysis_document(has_error=True))
    temp_root = tmp_path / "seungun-runtime"
    runtime = FrozenSeungunRuntimeAdapter(
        media_store=_AudioStore(_PCM),  # type: ignore[arg-type]
        detector=detector,
        restricted_temp_root=temp_root,
    )

    result = runtime.analyze(audio=audio, prompt=_prompt())

    assert detector.call_count == 1
    assert result.selected_phone_expected_index == 0
    assert result.audio_result["status"] == "coaching_ready"
    assert result.audio_result["grounded_payload"] is not None
    assert detector.observed_path is not None
    assert not detector.observed_path.exists()
    assert list(temp_root.iterdir()) == []


def test_runtime_returns_completed_no_issue_without_selected_index(
    tmp_path: Path,
) -> None:
    detector = _RecordingDetector(_analysis_document(has_error=False))
    runtime = FrozenSeungunRuntimeAdapter(
        media_store=_AudioStore(_PCM),  # type: ignore[arg-type]
        detector=detector,
        restricted_temp_root=tmp_path / "seungun-runtime",
    )

    result = runtime.analyze(audio=_audio(), prompt=_prompt())

    assert detector.call_count == 1
    assert result.selected_phone_expected_index is None
    assert result.audio_result["status"] == "completed_no_issue"


def test_runtime_failure_is_path_free_and_still_deletes_temp_wav(
    tmp_path: Path,
) -> None:
    class FailingDetector(_RecordingDetector):
        def analyze(self, *, audio_path: str | Path, expected_phones):  # noqa: ANN001
            super()._inspect(audio_path=audio_path, expected_phones=expected_phones)
            raise RuntimeError(f"sensitive path: {audio_path}")

    detector = FailingDetector(_analysis_document(has_error=True))
    temp_root = tmp_path / "seungun-runtime"
    runtime = FrozenSeungunRuntimeAdapter(
        media_store=_AudioStore(_PCM),  # type: ignore[arg-type]
        detector=detector,
        restricted_temp_root=temp_root,
    )

    with pytest.raises(
        PronunciationAttemptRuntimeError,
        match="^seungun_detector_failed$",
    ):
        runtime.analyze(audio=_audio(), prompt=_prompt())

    assert detector.call_count == 1
    assert detector.observed_path is not None
    assert not detector.observed_path.exists()
    assert list(temp_root.iterdir()) == []


def test_visual_handoff_preserves_consent_audio_and_video_lineage() -> None:
    authority, authorization = _granted_authority()
    asset = _asset(authorization)
    audio = _audio(attempt_id_sha256=authorization.attempt_id_sha256)
    reliability = UpstreamReliabilityReceipt.issue(
        status=UpstreamReliabilityStatus.SUPPORTED,
        policy_revision="seungun_reliability_v1",
    )
    adapter = SeungunVisualHandoffAdapter(
        binding_facade=SeungunAttemptBindingFacade(),
        consent_receipts=authority,
        reliability_receipt=reliability,
        clock=lambda: _NOW,
        capture_contract_id="production_capture_v1",
        population_id_sha256="7" * 64,
        context_id="production_context_v1",
    )
    result = SeungunRuntimeResult(
        audio_result={"status": "coaching_ready"},
        analysis_evidence=_analysis_document(has_error=True),
        selected_phone_expected_index=0,
    )

    handoff = adapter.issue(
        authorization=authorization,
        asset=asset,
        probe=_probe(),
        audio=audio,
        prompt=_prompt(),
        seungun_result=result,
    )

    assert isinstance(handoff.payload, SeungunVisualHandoff)
    visual_handoff = handoff.payload
    assert (
        visual_handoff.request.consent_receipt.receipt_sha256
        == authorization.consent_receipt_sha256
    )
    assert (
        visual_handoff.source_record.audio_derivation_receipt_sha256
        == audio.derivation_receipt.receipt_sha256
    )
    binding = visual_handoff.request.binding_receipt
    assert binding.audio_source_sha256 == audio.derivation_receipt.pcm_sha256
    assert binding.video_source_sha256 == asset.container_sha256
    assert (
        binding.upstream_reliability_receipt_sha256
        == reliability.receipt_sha256
    )


def test_visual_handoff_rejects_container_digest_mismatch() -> None:
    authority, authorization = _granted_authority()
    asset = _asset(authorization)
    mismatched_audio = _audio(
        attempt_id_sha256=authorization.attempt_id_sha256,
        container_sha256="9" * 64,
    )
    adapter = SeungunVisualHandoffAdapter(
        binding_facade=SeungunAttemptBindingFacade(),
        consent_receipts=authority,
        reliability_receipt=UpstreamReliabilityReceipt.issue(
            status=UpstreamReliabilityStatus.SUPPORTED,
            policy_revision="seungun_reliability_v1",
        ),
        clock=lambda: _NOW,
        capture_contract_id="production_capture_v1",
        population_id_sha256="7" * 64,
        context_id="production_context_v1",
    )

    with pytest.raises(
        PronunciationAttemptRuntimeError,
        match="^media_lineage_mismatch$",
    ):
        adapter.issue(
            authorization=authorization,
            asset=asset,
            probe=_probe(),
            audio=mismatched_audio,
            prompt=_prompt(),
            seungun_result=SeungunRuntimeResult(
                audio_result={"status": "coaching_ready"},
                analysis_evidence=_analysis_document(has_error=True),
                selected_phone_expected_index=0,
            ),
        )


def test_visual_handoff_revalidates_exact_consent_digest() -> None:
    authority, authorization = _granted_authority()
    asset = RestrictedVideoAsset(
        handle_id=_ASSET_HANDLE_ID,
        attempt_id_sha256=authorization.attempt_id_sha256,
        container_sha256=_CONTAINER_SHA256,
        byte_count=4096,
        consent_receipt_sha256="8" * 64,
    )
    adapter = SeungunVisualHandoffAdapter(
        binding_facade=SeungunAttemptBindingFacade(),
        consent_receipts=authority,
        reliability_receipt=UpstreamReliabilityReceipt.issue(
            status=UpstreamReliabilityStatus.SUPPORTED,
            policy_revision="seungun_reliability_v1",
        ),
        clock=lambda: _NOW,
        capture_contract_id="production_capture_v1",
        population_id_sha256="7" * 64,
        context_id="production_context_v1",
    )

    with pytest.raises(
        PronunciationAttemptRuntimeError,
        match="^consent_lineage_mismatch$",
    ):
        adapter.issue(
            authorization=authorization,
            asset=asset,
            probe=_probe(),
            audio=_audio(attempt_id_sha256=authorization.attempt_id_sha256),
            prompt=_prompt(),
            seungun_result=SeungunRuntimeResult(
                audio_result={"status": "coaching_ready"},
                analysis_evidence=_analysis_document(has_error=True),
                selected_phone_expected_index=0,
            ),
        )


def test_mediapipe_success_is_evidence_only_and_p6_p8_reliability_is_blocked() -> None:
    class LandmarkBatch:
        asset_handle_id = _ASSET_HANDLE_ID
        container_sha256 = _CONTAINER_SHA256
        model_sha256 = "5" * 64
        evidence_sha256 = "6" * 64

    batch = LandmarkBatch()

    class LipDetector:
        def __init__(self) -> None:
            self.calls = 0

        def detect(self, sample: Rgb24Sample) -> object:
            self.calls += 1
            return batch

    detector = LipDetector()

    class QcDetector:
        def __init__(self) -> None:
            self.calls = 0

        def measure(
            self, *, sample: Rgb24Sample, landmarks: object
        ) -> VideoQcCandidateEvidence:
            self.calls += 1
            assert sample == _rgb_sample()
            assert landmarks is batch
            return _qc_candidate(
                sample=sample,
                landmark_evidence_sha256=batch.evidence_sha256,
                model_sha256=batch.model_sha256,
            )

    qc_detector = QcDetector()
    bridge = MediaPipeVisualDetectorBridge(
        detector,
        qc_detector=qc_detector,
    )
    handoff = _issued_visual_handoff(
        reliability_status=UpstreamReliabilityStatus.SUPPORTED
    )

    detection = bridge.detect(
        sample=_rgb_sample(),
        probe=_probe(),
        handoff=handoff,
    )
    decision = BlockedVisualReliabilityAdapter().evaluate(
        detection=detection,
        handoff=handoff,
    )

    assert detector.calls == 1
    assert qc_detector.calls == 1
    assert detection.evidence.landmark_evidence is batch
    assert isinstance(
        detection.evidence.video_qc_evidence, VideoQcCandidateEvidence
    )
    assert (
        detection.evidence.binding_receipt_sha256
        == handoff.binding_receipt.receipt_sha256
    )
    assert decision.supported is False
    assert decision.receipt is None
    assert decision.reason_codes == (
        "video_qc_candidate_not_calibrated",
        "content_av_sync_backend_not_configured",
        "p6_user_runtime_calibration_not_available",
        "p8_user_runtime_claim_catalog_not_available",
    )


def test_mediapipe_bridge_rejects_rgb_sample_from_another_container() -> None:
    class LipDetector:
        def detect(self, sample: Rgb24Sample) -> object:
            raise AssertionError("lineage must fail before detector invocation")

    original = _rgb_sample()
    mismatched = Rgb24Sample(
        asset_handle_id=original.asset_handle_id,
        container_sha256="9" * 64,
        video_stream_index=original.video_stream_index,
        frames=original.frames,
        ffmpeg_revision=original.ffmpeg_revision,
    )

    with pytest.raises(
        PronunciationAttemptRuntimeError,
        match="^visual_detection_lineage_mismatch$",
    ):
        MediaPipeVisualDetectorBridge(LipDetector()).detect(
            sample=mismatched,
            probe=_probe(),
            handoff=_issued_visual_handoff(
                reliability_status=UpstreamReliabilityStatus.SUPPORTED
            ),
        )


def test_dense_phone_window_uses_only_bound_anchor_and_validated_sync() -> None:
    handoff = _issued_visual_handoff(
        reliability_status=UpstreamReliabilityStatus.SUPPORTED
    )
    binding = handoff.binding_receipt
    sync_policy_sha256 = "4" * 64
    qc_measurement_policy_sha256 = "2" * 64

    class LandmarkBatch:
        def __init__(self, sample: Rgb24Sample, evidence_sha256: str) -> None:
            self.asset_handle_id = sample.asset_handle_id
            self.container_sha256 = sample.container_sha256
            self.model_sha256 = "5" * 64
            self.evidence_sha256 = evidence_sha256
            self.frames = tuple(
                SimpleNamespace(timestamp_ms=frame.timestamp_us // 1000)
                for frame in sample.frames
            )

    class LipDetector:
        def __init__(self) -> None:
            self.calls = 0

        def detect(self, sample: Rgb24Sample) -> object:
            self.calls += 1
            return LandmarkBatch(sample, f"{self.calls + 5:x}" * 64)

    class QcDetector:
        def measure(
            self, *, sample: Rgb24Sample, landmarks: object
        ) -> VideoQcCandidateEvidence:
            return _qc_candidate(
                sample=sample,
                landmark_evidence_sha256=getattr(
                    landmarks, "evidence_sha256"
                ),
                model_sha256=getattr(landmarks, "model_sha256"),
                measurement_policy_sha256=qc_measurement_policy_sha256,
            )

    class SyncDetector:
        def measure(
            self,
            *,
            audio: RestrictedPcmAudio,
            landmarks: object,
            probe: MediaProbe,
        ) -> AvSyncCandidateEvidence:
            return _sync_candidate(
                audio=audio,
                probe=probe,
                landmark_evidence_sha256=getattr(
                    landmarks, "evidence_sha256"
                ),
                model_sha256=getattr(landmarks, "model_sha256"),
                measurement_policy_sha256=sync_policy_sha256,
                offset_ms=30,
            )

    detector = LipDetector()
    phone_window_policy = PhoneWindowDecodePolicy(
        policy_revision="selected_phone_test_v1",
        expected_av_sync_policy_sha256=sync_policy_sha256,
        context_contract_sha256="3" * 64,
        maximum_absolute_offset_ms=100,
        padding_before_ms=20,
        padding_after_ms=20,
        minimum_frame_count=2,
        maximum_frame_count=8,
        maximum_window_duration_ms=500,
        maximum_rgb_bytes=1_000,
    )
    qc_decision_policy = _qc_decision_policy(
        measurement_policy_sha256=qc_measurement_policy_sha256
    )
    sync_decision_policy = CalibratedAvSyncDecisionPolicy(
        policy_revision="calibrated_sync_test_v1",
        expected_measurement_policy_sha256=sync_policy_sha256,
        maximum_absolute_offset_ms=100,
        minimum_correlation=0.8,
        minimum_anchor_count=1,
        minimum_anchor_strength=0.8,
    )
    bridge = MediaPipeVisualDetectorBridge(
        detector,
        qc_detector=QcDetector(),
        sync_detector=SyncDetector(),
        qc_decision_adapter=CalibratedVideoQcDecisionAdapter(
            policy=qc_decision_policy
        ),
        sync_decision_adapter=CalibratedAvSyncDecisionAdapter(
            policy=sync_decision_policy
        ),
        expected_video_qc_decision_policy_sha256=(
            qc_decision_policy.policy_sha256
        ),
        expected_av_sync_decision_policy_sha256=(
            sync_decision_policy.policy_sha256
        ),
        phone_window_policy=phone_window_policy,
    )
    global_detection = bridge.detect(
        sample=_rgb_sample(),
        probe=_probe(),
        handoff=handoff,
    )

    request = bridge.plan_phone_window(
        detection=global_detection,
        probe=_probe(),
        handoff=handoff,
    )

    assert request is not None
    assert global_detection.evidence.video_qc_decision is not None
    assert global_detection.evidence.video_qc_decision.status == "passed"
    assert (
        global_detection.evidence.video_qc_decision.source_candidate_receipt_sha256
        == global_detection.evidence.video_qc_evidence.receipt_sha256
    )
    assert (
        global_detection.evidence.video_qc_decision.observed_metrics
        == global_detection.evidence.video_qc_evidence.metrics
    )
    assert (
        global_detection.evidence.video_qc_decision.decision_adapter_code_sha256
        != global_detection.evidence.video_qc_decision.measurement_adapter_code_sha256
    )
    assert global_detection.evidence.av_sync_decision is not None
    assert global_detection.evidence.av_sync_decision.status == "synchronized"
    assert (
        global_detection.evidence.av_sync_decision.source_candidate_receipt_sha256
        == global_detection.evidence.av_sync_candidate_evidence.receipt_sha256
    )
    assert (
        global_detection.evidence.av_sync_decision.observed_offset_ms,
        global_detection.evidence.av_sync_decision.observed_correlation,
        global_detection.evidence.av_sync_decision.observed_anchors,
    ) == (
        global_detection.evidence.av_sync_candidate_evidence.candidate_offset_ms,
        global_detection.evidence.av_sync_candidate_evidence.correlation,
        global_detection.evidence.av_sync_candidate_evidence.anchors,
    )
    assert (
        global_detection.evidence.av_sync_decision.decision_adapter_code_sha256
        != global_detection.evidence.av_sync_decision.measurement_adapter_code_sha256
    )
    assert global_detection.evidence.av_sync_evidence is not None
    assert (
        global_detection.evidence.av_sync_evidence.av_sync_decision_receipt_sha256
        == global_detection.evidence.av_sync_decision.receipt_sha256
    )
    with pytest.raises(ValueError, match="decision digest differs"):
        replace(
            global_detection.evidence.av_sync_decision,
            observed_correlation=0.94,
        )
    # Exact anchor [100, 200] + validated 30 ms offset + fixed 20 ms padding.
    assert (request.video_start_ms, request.video_end_ms) == (110, 250)
    assert request.phone_anchor_sha256 == bridge._selected_phone_anchor(  # noqa: SLF001
        handoff
    ).anchor_ref

    dense_sample = Rgb24Sample(
        asset_handle_id=_ASSET_HANDLE_ID,
        container_sha256=_CONTAINER_SHA256,
        video_stream_index=0,
        frames=(
            DecodedRgbFrame(
                timestamp_us=120_000,
                width=1,
                height=1,
                rgb24=b"\x02" * 3,
            ),
            DecodedRgbFrame(
                timestamp_us=200_000,
                width=1,
                height=1,
                rgb24=b"\x03" * 3,
            ),
        ),
        ffmpeg_revision="ffmpeg-test",
    )
    decode_receipt = PhoneWindowDecodeReceipt.issue(
        request=request,
        video_stream_index=0,
        selected_frame_indices=(3, 5),
        decoded_timestamps_us=(120_000, 200_000),
        width=1,
        height=1,
        decoded_rgb_sha256=decoded_rgb_sha256(dense_sample),
        ffmpeg_revision="ffmpeg-test",
        decoder_code_sha256=_module_sha256(ffmpeg_adapter_module),
    )
    with pytest.raises(ValueError, match="receipt digest differs"):
        replace(
            decode_receipt,
            decoded_timestamps_us=(120_000, 210_000),
        )
    dense_detection = bridge.detect_phone_window(
        window=Rgb24PhoneWindow(
            request=request,
            sample=dense_sample,
            decode_receipt=decode_receipt,
        ),
        detection=global_detection,
        probe=_probe(),
        handoff=handoff,
    )

    phone_evidence = dense_detection.evidence.phone_window_evidence
    assert phone_evidence is not None
    assert detector.calls == 2
    assert (
        phone_evidence.runtime_receipt.phone_window_decode_receipt_sha256
        == decode_receipt.receipt_sha256
    )
    assert (
        phone_evidence.runtime_receipt.phone_window_tracking_receipt_sha256
        == phone_evidence.tracking_receipt.receipt_sha256
    )
    assert (
        dense_detection.evidence.detection_receipt_sha256
        == global_detection.evidence.detection_receipt_sha256
    )
    assert phone_evidence.decode_receipt.decoder_code_sha256 == _module_sha256(
        ffmpeg_adapter_module
    )
    assert phone_evidence.tracking_receipt.tracking_code_sha256 == _module_sha256(
        runtime_module
    )
    assert phone_evidence.runtime_receipt.phone_window_code_sha256 == (
        phone_window_code_sha256()
    )
    assert phone_evidence.runtime_receipt.phone_window_policy_revision == (
        phone_window_policy.policy_revision
    )
    assert phone_evidence.runtime_receipt.phone_window_policy_sha256 == (
        phone_window_policy.policy_sha256
    )


def test_raw_av_sync_candidate_cannot_create_phone_window() -> None:
    class LandmarkBatch:
        asset_handle_id = _ASSET_HANDLE_ID
        container_sha256 = _CONTAINER_SHA256
        model_sha256 = "5" * 64
        evidence_sha256 = "6" * 64

    class LipDetector:
        def detect(self, sample: Rgb24Sample) -> object:
            del sample
            return LandmarkBatch()

    class SyncDetector:
        def measure(
            self,
            *,
            audio: RestrictedPcmAudio,
            landmarks: object,
            probe: MediaProbe,
        ) -> AvSyncCandidateEvidence:
            return _sync_candidate(
                audio=audio,
                probe=probe,
                landmark_evidence_sha256=getattr(
                    landmarks, "evidence_sha256"
                ),
                model_sha256=getattr(landmarks, "model_sha256"),
                measurement_policy_sha256="4" * 64,
                offset_ms=0,
            )

    handoff = _issued_visual_handoff(
        reliability_status=UpstreamReliabilityStatus.SUPPORTED
    )
    bridge = MediaPipeVisualDetectorBridge(
        LipDetector(),
        sync_detector=SyncDetector(),
        phone_window_policy=PhoneWindowDecodePolicy(
            policy_revision="selected_phone_test_v1",
            expected_av_sync_policy_sha256="4" * 64,
            context_contract_sha256="3" * 64,
            maximum_absolute_offset_ms=100,
            padding_before_ms=20,
            padding_after_ms=20,
            minimum_frame_count=2,
            maximum_frame_count=8,
            maximum_window_duration_ms=500,
            maximum_rgb_bytes=1_000,
        ),
    )
    detection = bridge.detect(
        sample=_rgb_sample(),
        probe=_probe(),
        handoff=handoff,
    )

    assert bridge.plan_phone_window(
        detection=detection,
        probe=_probe(),
        handoff=handoff,
    ) is None


def test_failed_calibrated_sync_decision_cannot_issue_validated_offset() -> None:
    handoff = _issued_visual_handoff(
        reliability_status=UpstreamReliabilityStatus.SUPPORTED
    )
    candidate = _sync_candidate(
        audio=handoff.runtime_context,
        probe=_probe(),
        landmark_evidence_sha256="6" * 64,
        model_sha256="5" * 64,
        measurement_policy_sha256="4" * 64,
        offset_ms=0,
    )
    policy = CalibratedAvSyncDecisionPolicy(
        policy_revision="calibrated_sync_strict_test_v1",
        expected_measurement_policy_sha256="4" * 64,
        maximum_absolute_offset_ms=100,
        minimum_correlation=0.99,
        minimum_anchor_count=1,
        minimum_anchor_strength=0.8,
    )

    decision = CalibratedAvSyncDecisionAdapter(policy=policy).decide(
        candidate=candidate,
        binding_receipt=handoff.binding_receipt,
    )

    assert decision.status == "failed"
    assert decision.reason_codes == ("av_sync_correlation_below_policy",)
    assert decision.source_candidate_receipt_sha256 == candidate.receipt_sha256
    assert decision.observed_correlation == candidate.correlation
    with pytest.raises(ValueError, match="did not synchronize"):
        PolicyValidatedAvSyncOffset.issue_from_decision(decision=decision)


def test_bridge_rejects_untyped_synchronized_mapping() -> None:
    class LandmarkBatch:
        asset_handle_id = _ASSET_HANDLE_ID
        container_sha256 = _CONTAINER_SHA256
        model_sha256 = "5" * 64
        evidence_sha256 = "6" * 64

    class LipDetector:
        def detect(self, sample: Rgb24Sample) -> object:
            del sample
            return LandmarkBatch()

    class UntypedSyncBackend:
        def measure(self, **kwargs: object) -> object:
            del kwargs
            return {
                "status": "synchronized",
                "receipt_sha256": "9" * 64,
                "offset_ms": 0,
            }

    with pytest.raises(
        PronunciationAttemptRuntimeError,
        match="^av_sync_evidence_invalid$",
    ):
        MediaPipeVisualDetectorBridge(
            LipDetector(),
            sync_detector=UntypedSyncBackend(),  # type: ignore[arg-type]
        ).detect(
            sample=_rgb_sample(),
            probe=_probe(),
            handoff=_issued_visual_handoff(
                reliability_status=UpstreamReliabilityStatus.SUPPORTED
            ),
        )


def _qc_metrics(frame_count: int) -> VideoQcMetrics:
    return VideoQcMetrics(
        frame_count=frame_count,
        single_face_frame_count=frame_count,
        multiple_face_frame_count=0,
        single_face_coverage=1.0,
        multiple_face_frame_fraction=0.0,
        mean_luma_8bit=100.0,
        underexposed_pixel_fraction=0.0,
        overexposed_pixel_fraction=0.0,
        mean_neighbor_luma_delta_8bit=10.0,
        lip_roi_frame_count=frame_count,
        lip_roi_mean_luma_8bit=100.0,
        lip_roi_underexposed_pixel_fraction=0.0,
        lip_roi_overexposed_pixel_fraction=0.0,
        lip_roi_mean_neighbor_luma_delta_8bit=10.0,
        track_transition_coverage=1.0,
        longest_single_face_run_fraction=1.0,
        maximum_lip_center_step_normalized=0.01,
    )


def _qc_candidate(
    *,
    sample: Rgb24Sample,
    landmark_evidence_sha256: str,
    model_sha256: str,
    measurement_policy_sha256: str = "2" * 64,
) -> VideoQcCandidateEvidence:
    timestamps_us = tuple(frame.timestamp_us for frame in sample.frames)
    metrics = _qc_metrics(len(sample.frames))
    provenance = {
        "schema_version": "voice_coach.video_qc_provenance.v2",
        "asset_handle_id": sample.asset_handle_id,
        "container_sha256": sample.container_sha256,
        "video_stream_index": sample.video_stream_index,
        "sample_timestamps_us": list(timestamps_us),
        "landmark_timestamps_ms": [value // 1000 for value in timestamps_us],
        "decoded_rgb_evidence_sha256": "7" * 64,
        "landmark_evidence_sha256": landmark_evidence_sha256,
        "landmark_model_sha256": model_sha256,
        "policy_receipt_sha256": measurement_policy_sha256,
        "measurement_revision": VIDEO_QC_MEASUREMENT_REVISION,
        "measurement_adapter_code_sha256": (
            video_qc_measurement_code_sha256()
        ),
    }
    unsigned = {
        "schema_version": "voice_coach.video_qc_candidate.v2",
        "status": "candidate_unvalidated",
        **{key: value for key, value in provenance.items() if key != "schema_version"},
        "metrics": metrics.to_json_object(),
        "reason_codes": [],
        "provenance_receipt_sha256": sha256_document(provenance),
    }
    return VideoQcCandidateEvidence(
        status="candidate_unvalidated",
        asset_handle_id=sample.asset_handle_id,
        container_sha256=sample.container_sha256,
        video_stream_index=sample.video_stream_index,
        sample_timestamps_us=timestamps_us,
        landmark_timestamps_ms=tuple(value // 1000 for value in timestamps_us),
        decoded_rgb_evidence_sha256="7" * 64,
        landmark_evidence_sha256=landmark_evidence_sha256,
        landmark_model_sha256=model_sha256,
        policy_receipt_sha256=measurement_policy_sha256,
        measurement_revision=VIDEO_QC_MEASUREMENT_REVISION,
        measurement_adapter_code_sha256=video_qc_measurement_code_sha256(),
        metrics=metrics,
        reason_codes=(),
        provenance_receipt_sha256=sha256_document(provenance),
        receipt_sha256=sha256_document(unsigned),
    )


def _sync_candidate(
    *,
    audio: RestrictedPcmAudio,
    probe: MediaProbe,
    landmark_evidence_sha256: str,
    model_sha256: str,
    measurement_policy_sha256: str,
    offset_ms: int,
) -> AvSyncCandidateEvidence:
    anchors = (AvSyncAnchorCandidate(audio_ms=0, video_ms=30, strength=1.0),)
    lineage = {
        "schema_version": "voice_coach.lip_audio_av_sync_lineage.v2",
        "asset_handle_id": probe.asset_handle_id,
        "audio_derivation_receipt_sha256": (
            audio.derivation_receipt.receipt_sha256
        ),
        "audio_source_sha256": audio.derivation_receipt.pcm_sha256,
        "video_source_sha256": probe.container_sha256,
        "audio_stream_index": probe.audio_stream.stream_index,
        "audio_start_time_us": probe.audio_stream.start_time_us,
        "video_stream_index": probe.video_stream.stream_index,
        "video_start_time_us": probe.video_stream.start_time_us,
        "landmark_evidence_sha256": landmark_evidence_sha256,
        "landmark_model_sha256": model_sha256,
        "measurement_policy_sha256": measurement_policy_sha256,
        "measurement_revision": AV_SYNC_MEASUREMENT_REVISION,
        "measurement_adapter_code_sha256": av_sync_measurement_code_sha256(),
    }
    unsigned = {
        "schema_version": "voice_coach.lip_audio_av_sync_candidate.v2",
        **{key: value for key, value in lineage.items() if key != "schema_version"},
        "status": "candidate_unvalidated",
        "candidate_offset_ms": offset_ms,
        "correlation": 0.95,
        "anchors": [anchor.to_json_object() for anchor in anchors],
        "reason_codes": [],
        "lineage_receipt_sha256": sha256_document(lineage),
    }
    return AvSyncCandidateEvidence(
        asset_handle_id=probe.asset_handle_id,
        audio_derivation_receipt_sha256=(
            audio.derivation_receipt.receipt_sha256
        ),
        audio_source_sha256=audio.derivation_receipt.pcm_sha256,
        video_source_sha256=probe.container_sha256,
        audio_stream_index=probe.audio_stream.stream_index,
        audio_start_time_us=probe.audio_stream.start_time_us,
        video_stream_index=probe.video_stream.stream_index,
        video_start_time_us=probe.video_stream.start_time_us,
        landmark_evidence_sha256=landmark_evidence_sha256,
        landmark_model_sha256=model_sha256,
        measurement_policy_sha256=measurement_policy_sha256,
        measurement_revision=AV_SYNC_MEASUREMENT_REVISION,
        measurement_adapter_code_sha256=av_sync_measurement_code_sha256(),
        status="candidate_unvalidated",
        candidate_offset_ms=offset_ms,
        correlation=0.95,
        anchors=anchors,
        reason_codes=(),
        lineage_receipt_sha256=sha256_document(lineage),
        receipt_sha256=sha256_document(unsigned),
    )


def _qc_decision_policy(
    *, measurement_policy_sha256: str
) -> CalibratedVideoQcDecisionPolicy:
    return CalibratedVideoQcDecisionPolicy(
        policy_revision="calibrated_video_qc_test_v1",
        expected_measurement_policy_sha256=measurement_policy_sha256,
        minimum_single_face_coverage=0.9,
        maximum_multiple_face_frame_fraction=0.0,
        minimum_mean_luma_8bit=20.0,
        maximum_mean_luma_8bit=230.0,
        maximum_underexposed_pixel_fraction=0.1,
        maximum_overexposed_pixel_fraction=0.1,
        minimum_lip_roi_mean_neighbor_luma_delta_8bit=1.0,
        minimum_track_transition_coverage=0.5,
        minimum_longest_single_face_run_fraction=0.8,
        maximum_lip_center_step_normalized=0.2,
    )


class _AudioStore:
    def __init__(self, pcm: bytes) -> None:
        self._pcm = pcm

    @contextmanager
    def open_derived_audio(self, audio: RestrictedPcmAudio):  # noqa: ANN201
        from io import BytesIO

        yield BytesIO(self._pcm)


class _CompletedAnalysis:
    def __init__(self, document: dict[str, object]) -> None:
        self._document = document

    def to_json_object(self) -> dict[str, object]:
        return self._document


class _RecordingDetector:
    def __init__(self, document: dict[str, object]) -> None:
        self._document = document
        self.call_count = 0
        self.observed_path: Path | None = None

    def _inspect(self, *, audio_path: str | Path, expected_phones) -> None:  # noqa: ANN001
        self.call_count += 1
        path = Path(audio_path)
        self.observed_path = path
        assert path.is_file()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o400
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
        with wave.open(str(path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 16_000
            assert wav_file.readframes(wav_file.getnframes()) == _PCM
        assert tuple(expected_phones) == _prompt().expected_phones

    def analyze(
        self, *, audio_path: str | Path, expected_phones
    ) -> _CompletedAnalysis:  # noqa: ANN001
        self._inspect(audio_path=audio_path, expected_phones=expected_phones)
        return _CompletedAnalysis(self._document)


def _prompt() -> TrustedPrompt:
    return TrustedPrompt(
        prompt_id="R00-S001",
        revision="r00_g2pk_" + "6" * 64,
        normalized_text="가",
        expected_phones=("ㄱ", "ㅏ"),
    )


def _audio(
    *,
    attempt_id_sha256: str = "a" * 64,
    container_sha256: str = _CONTAINER_SHA256,
) -> RestrictedPcmAudio:
    return RestrictedPcmAudio(
        artifact_id="f" * 64,
        asset_handle_id=_ASSET_HANDLE_ID,
        attempt_id_sha256=attempt_id_sha256,
        byte_count=len(_PCM),
        sample_count=len(_PCM) // 2,
        derivation_receipt=AudioDerivationReceipt.issue(
            container_sha256=container_sha256,
            audio_stream_index=1,
            tool_revision="ffmpeg-test",
            pcm_sha256=_PCM_SHA256,
            source_time_base="1/48000",
        ),
    )


def _granted_authority() -> tuple[
    LocalExplicitConsentAuthority, VideoConsentAuthorization
]:
    authority = LocalExplicitConsentAuthority(
        consent_token="explicit-consent-token",
        clock=lambda: _NOW,
        attempt_nonce=lambda: b"a" * 32,
    )
    authorization = authority.authorize(
        prompt_id="R00-S001",
        consent_reference="explicit-consent-token",
    )
    return authority, authorization


def _issued_visual_handoff(
    *, reliability_status: UpstreamReliabilityStatus
) -> HostVisualHandoff:
    authority, authorization = _granted_authority()
    adapter = SeungunVisualHandoffAdapter(
        binding_facade=SeungunAttemptBindingFacade(),
        consent_receipts=authority,
        reliability_receipt=UpstreamReliabilityReceipt.issue(
            status=reliability_status,
            policy_revision="seungun_reliability_v1",
            reason_codes=(
                ()
                if reliability_status is UpstreamReliabilityStatus.SUPPORTED
                else ("upstream_reliability_unavailable",)
            ),
        ),
        clock=lambda: _NOW,
        capture_contract_id="production_capture_v1",
        population_id_sha256="7" * 64,
        context_id="production_context_v1",
    )
    return adapter.issue(
        authorization=authorization,
        asset=_asset(authorization),
        probe=_probe(),
        audio=_audio(attempt_id_sha256=authorization.attempt_id_sha256),
        prompt=_prompt(),
        seungun_result=SeungunRuntimeResult(
            audio_result={"status": "coaching_ready"},
            analysis_evidence=_analysis_document(has_error=True),
            selected_phone_expected_index=0,
        ),
    )


def _asset(authorization) -> RestrictedVideoAsset:  # noqa: ANN001
    return RestrictedVideoAsset(
        handle_id=_ASSET_HANDLE_ID,
        attempt_id_sha256=authorization.attempt_id_sha256,
        container_sha256=_CONTAINER_SHA256,
        byte_count=4096,
        consent_receipt_sha256=authorization.consent_receipt_sha256,
    )


def _probe() -> MediaProbe:
    return MediaProbe(
        asset_handle_id=_ASSET_HANDLE_ID,
        container_sha256=_CONTAINER_SHA256,
        format_name="mp4",
        duration_ms=1000,
        video_stream=VideoStreamProbe(
            stream_index=0,
            codec_name="h264",
            width=1,
            height=1,
            frames_per_second_numerator=25,
            frames_per_second_denominator=1,
            frame_count=2,
            time_base="1/1000000",
            start_time_us=0,
            duration_ms=1000,
        ),
        audio_stream=AudioStreamProbe(
            stream_index=1,
            codec_name="aac",
            sample_rate_hz=48_000,
            channels=1,
            time_base="1/48000",
            start_time_us=0,
            duration_ms=1000,
        ),
        video_frame_timestamps_us=(0, 40_000),
        ffprobe_revision="ffprobe-test",
    )


def _rgb_sample() -> Rgb24Sample:
    return Rgb24Sample(
        asset_handle_id=_ASSET_HANDLE_ID,
        container_sha256=_CONTAINER_SHA256,
        video_stream_index=0,
        frames=(
            DecodedRgbFrame(timestamp_us=0, width=1, height=1, rgb24=b"\x00" * 3),
            DecodedRgbFrame(
                timestamp_us=40_000,
                width=1,
                height=1,
                rgb24=b"\x01" * 3,
            ),
        ),
        ffmpeg_revision="ffmpeg-test",
    )


def _module_sha256(module: object) -> str:
    module_path = getattr(module, "__file__", None)
    assert isinstance(module_path, str)
    return hashlib.sha256(Path(module_path).read_bytes()).hexdigest()


def _analysis_document(*, has_error: bool) -> dict[str, object]:
    return {
        "schema_version": SEUNGUN_SCHEMA_VERSION,
        "detector_version": "frozen_detector_v1",
        "detector_config_sha256": "2" * 64,
        "backbone_name": "frozen_backbone",
        "backbone_tree_sha256": "3" * 64,
        "expected_phones": ["ㄱ", "ㅏ"],
        "ctc_hypothesis": ["ㅋ" if has_error else "ㄱ", "ㅏ"],
        "insertions": [],
        "phones": [
            {
                "expected_index": 0,
                "expected_phone": "ㄱ",
                "observed_candidate": "ㅋ" if has_error else "ㄱ",
                "alignment_operation": "substitution" if has_error else "correct",
                "start_ms": 100,
                "end_ms": 200,
                "error_score": 0.91 if has_error else 0.1,
                "operating_threshold": 0.8,
                "is_error": has_error,
                "score_semantics": SEUNGUN_SCORE_SEMANTICS,
                "soft_features": {},
            },
            {
                "expected_index": 1,
                "expected_phone": "ㅏ",
                "observed_candidate": "ㅏ",
                "alignment_operation": "correct",
                "start_ms": 200,
                "end_ms": 300,
                "error_score": 0.1,
                "operating_threshold": 0.8,
                "is_error": False,
                "score_semantics": SEUNGUN_SCORE_SEMANTICS,
                "soft_features": {},
            },
        ],
        "notices": ["completed analysis fixture"],
    }
