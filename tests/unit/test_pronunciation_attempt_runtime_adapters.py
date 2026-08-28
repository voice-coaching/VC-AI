from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import inspect
import os
from pathlib import Path
import stat
import wave

import pytest

from voice_coach.pronunciation_attempts.adapters import runtime as runtime_module
from voice_coach.pronunciation_attempts.adapters.runtime import (
    BlockedVisualReliabilityAdapter,
    FrozenSeungunRuntimeAdapter,
    LocalExplicitConsentAuthority,
    MediaPipeVisualDetectorBridge,
    PronunciationAttemptRuntimeError,
    R00TrustedPromptAdapter,
    SeungunVisualHandoffAdapter,
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

    class QcEvidence:
        status = "candidate_unvalidated"
        asset_handle_id = _ASSET_HANDLE_ID
        container_sha256 = _CONTAINER_SHA256
        landmark_evidence_sha256 = batch.evidence_sha256
        landmark_model_sha256 = batch.model_sha256
        sample_timestamps_us = (0, 40_000)
        receipt_sha256 = "8" * 64

    class QcDetector:
        def __init__(self) -> None:
            self.calls = 0

        def measure(self, *, sample: Rgb24Sample, landmarks: object) -> object:
            self.calls += 1
            assert sample == _rgb_sample()
            assert landmarks is batch
            return QcEvidence()

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
    assert isinstance(detection.evidence.video_qc_evidence, QcEvidence)
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
