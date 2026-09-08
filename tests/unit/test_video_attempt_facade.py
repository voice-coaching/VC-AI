from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest

from voice_coach.pronunciation_attempts.models import (
    AudioDerivationReceipt,
    AudioStreamProbe,
    AvContainerPolicy,
    DecodedRgbFrame,
    MediaProbe,
    RestrictedMediaDeletionReceipt,
    RestrictedPcmAudio,
    RestrictedVideoAsset,
    Rgb24Sample,
    VideoAdmissionPolicy,
    VideoConsentAuthorization,
    VideoConsentDecision,
    VideoStreamProbe,
)
from voice_coach.pronunciation_attempts.services.video_attempt_facade import (
    HostVisualHandoff,
    SeungunRuntimeResult,
    TrustedPrompt,
    VideoAttemptCommand,
    VideoAttemptFacade,
    VideoAttemptStatus,
    VisualDetection,
    VisualReliabilityDecision,
    VisualStageStatus,
    WorkspaceCleanupStatus,
)


_ATTEMPT_SHA256 = "a" * 64
_CONTAINER_SHA256 = "b" * 64
_CONSENT_SHA256 = "c" * 64
_ASSET_HANDLE_ID = "d" * 64
_PCM_SHA256 = "e" * 64
_AUDIO_ARTIFACT_ID = "f" * 64


def _authorization(*, granted: bool) -> VideoConsentAuthorization:
    issued_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return VideoConsentAuthorization(
        attempt_id_sha256=_ATTEMPT_SHA256,
        consent_receipt_sha256=_CONSENT_SHA256,
        decision=(
            VideoConsentDecision.GRANTED
            if granted
            else VideoConsentDecision.DENIED
        ),
        purpose="pronunciation_visual_feedback",
        data_category="user_audiovisual_attempt",
        retention_policy_id="delete_on_completion",
        delete_on_completion=True,
        remote_egress_allowed=False,
        issued_at_utc=issued_at,
        expires_at_utc=issued_at + timedelta(minutes=10),
    )


def _asset() -> RestrictedVideoAsset:
    return RestrictedVideoAsset(
        handle_id=_ASSET_HANDLE_ID,
        attempt_id_sha256=_ATTEMPT_SHA256,
        container_sha256=_CONTAINER_SHA256,
        byte_count=1024,
        consent_receipt_sha256=_CONSENT_SHA256,
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


def _audio() -> RestrictedPcmAudio:
    return RestrictedPcmAudio(
        artifact_id=_AUDIO_ARTIFACT_ID,
        asset_handle_id=_ASSET_HANDLE_ID,
        attempt_id_sha256=_ATTEMPT_SHA256,
        byte_count=320,
        sample_count=160,
        derivation_receipt=AudioDerivationReceipt.issue(
            container_sha256=_CONTAINER_SHA256,
            audio_stream_index=1,
            tool_revision="ffmpeg-test",
            pcm_sha256=_PCM_SHA256,
            source_time_base="1/48000",
        ),
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


class _Ports:
    def __init__(self) -> None:
        self.trace: list[str] = []
        self.fail_at: str | None = None
        self.consent_granted = True
        self.selected_phone_expected_index: int | None = 1
        self.reliability_supported = True
        self.handoff_index_offset = 0
        self.seungun_calls = 0
        self.audio_result: dict[str, object] = {
            "status": "audio_feedback_ready",
            "phone_feedback": ["p0", "p1"],
        }
        self.visual_result: dict[str, object] = {"kind": "visual_supplement"}

    def _call(self, name: str) -> None:
        self.trace.append(name)
        if self.fail_at == name:
            raise RuntimeError(f"fake {name} failure")

    def authorize(
        self, *, prompt_id: str, consent_reference: str
    ) -> VideoConsentAuthorization:
        self._call("consent")
        return _authorization(granted=self.consent_granted)

    def admit(
        self,
        source_path: str,
        *,
        attempt_id_sha256: str,
        authorization: VideoConsentAuthorization,
        policy: VideoAdmissionPolicy,
    ) -> RestrictedVideoAsset:
        self._call("admit")
        assert attempt_id_sha256 == authorization.attempt_id_sha256
        return _asset()

    def probe(
        self, asset: RestrictedVideoAsset, policy: AvContainerPolicy
    ) -> MediaProbe:
        self._call("probe")
        return _probe()

    def extract_embedded_audio(
        self, asset: RestrictedVideoAsset, policy: AvContainerPolicy
    ) -> RestrictedPcmAudio:
        self._call("extract")
        return _audio()

    def decode_rgb24_sample(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
        *,
        frame_count: int,
    ) -> Rgb24Sample:
        self._call("decode")
        assert frame_count == 2
        return _rgb_sample()

    def delete(
        self, asset: RestrictedVideoAsset
    ) -> RestrictedMediaDeletionReceipt:
        self._call("delete")
        return RestrictedMediaDeletionReceipt(
            asset_handle_id=asset.handle_id,
            status="deleted",
            deletion_receipt_sha256="1" * 64,
        )

    def resolve(self, *, prompt_id: str) -> TrustedPrompt:
        self._call("prompt")
        return TrustedPrompt(
            prompt_id=prompt_id,
            revision="prompt-revision-1",
            normalized_text="가 나 다",
            expected_phones=("k", "a", "n"),
        )

    def analyze(
        self, *, audio: RestrictedPcmAudio, prompt: TrustedPrompt
    ) -> SeungunRuntimeResult:
        self._call("seungun")
        self.seungun_calls += 1
        return SeungunRuntimeResult(
            audio_result=self.audio_result,
            analysis_evidence={"analysis_ref": "seungun-owned"},
            selected_phone_expected_index=self.selected_phone_expected_index,
        )

    def issue(
        self,
        *,
        authorization: VideoConsentAuthorization,
        asset: RestrictedVideoAsset,
        probe: MediaProbe,
        audio: RestrictedPcmAudio,
        prompt: TrustedPrompt,
        seungun_result: SeungunRuntimeResult,
    ) -> HostVisualHandoff:
        self._call("handoff")
        selected_index = seungun_result.selected_phone_expected_index
        assert selected_index is not None
        return HostVisualHandoff(
            selected_phone_expected_index=(
                selected_index + self.handoff_index_offset
            ),
            binding_receipt={"issuer": "host"},
            payload={"analysis_evidence": seungun_result.analysis_evidence},
        )

    def detect(
        self,
        *,
        sample: Rgb24Sample,
        probe: MediaProbe,
        handoff: HostVisualHandoff,
    ) -> VisualDetection:
        self._call("detector")
        return VisualDetection(evidence={"landmarks": "detector-owned"})

    def plan_phone_window(
        self,
        *,
        detection: VisualDetection,
        probe: MediaProbe,
        handoff: HostVisualHandoff,
    ) -> object:
        del detection, probe, handoff
        self._call("phone_window_plan")
        return object()

    def decode_rgb24_window(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
        *,
        probe: MediaProbe,
        request: object,
    ) -> object:
        del asset, policy, probe, request
        self._call("phone_window_decode")
        return object()

    def detect_phone_window(
        self,
        *,
        window: object,
        detection: VisualDetection,
        probe: MediaProbe,
        handoff: HostVisualHandoff,
    ) -> VisualDetection:
        del window, probe, handoff
        self._call("phone_window_detector")
        return detection

    def evaluate(
        self, *, detection: VisualDetection, handoff: HostVisualHandoff
    ) -> VisualReliabilityDecision:
        self._call("reliability")
        if not self.reliability_supported:
            return VisualReliabilityDecision(
                supported=False,
                receipt=None,
                reason_codes=("landmark_reliability_below_gate",),
            )
        return VisualReliabilityDecision(
            supported=True,
            receipt={"issuer": "reliability-backend"},
        )

    def create(
        self,
        *,
        detection: VisualDetection,
        handoff: HostVisualHandoff,
        reliability: VisualReliabilityDecision,
    ) -> dict[str, object]:
        self._call("visual")
        assert reliability.receipt is not None
        return self.visual_result


def _facade(
    ports: _Ports, *, with_phone_window: bool = False
) -> VideoAttemptFacade:
    return VideoAttemptFacade(
        consent_port=ports,
        media_store_port=ports,
        container_port=ports,
        admission_policy=VideoAdmissionPolicy(
            maximum_source_bytes=10_000_000,
            required_purpose="pronunciation_visual_feedback",
            required_data_category="user_audiovisual_attempt",
            allowed_retention_policy_ids=("delete_on_completion",),
        ),
        container_policy=AvContainerPolicy(
            minimum_duration_ms=100,
            maximum_duration_ms=10_000,
            minimum_width=1,
            maximum_width=1920,
            minimum_height=1,
            maximum_height=1080,
            minimum_frames_per_second=1.0,
            maximum_frames_per_second=120.0,
            minimum_frame_count=1,
            maximum_frame_count=1000,
            maximum_rgb_sample_frames=10,
            maximum_rgb_sample_bytes=1_000_000,
            maximum_pcm_bytes=10_000_000,
            subprocess_timeout_seconds=10.0,
            maximum_subprocess_output_bytes=1_000_000,
        ),
        visual_sample_frame_count=2,
        prompt_port=ports,
        seungun_port=ports,
        handoff_port=ports,
        detector_port=ports,
        reliability_port=ports,
        visual_port=ports,
        phone_window_decoder_port=ports if with_phone_window else None,
        phone_window_detector_port=ports if with_phone_window else None,
    )


def _command() -> VideoAttemptCommand:
    return VideoAttemptCommand(
        prompt_id="trusted-prompt-1",
        video_locator="opaque-cli-video-locator",
        consent_reference="consent-session-reference",
    )


def test_success_uses_host_authorities_in_order_and_runs_seungun_once() -> None:
    ports = _Ports()

    result = _facade(ports).run(_command())

    assert ports.trace == [
        "consent",
        "admit",
        "probe",
        "extract",
        "prompt",
        "seungun",
        "handoff",
        "decode",
        "detector",
        "reliability",
        "visual",
        "delete",
    ]
    assert ports.seungun_calls == 1
    assert result.status is VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_SUPPLEMENTED
    assert result.audio_result is ports.audio_result
    assert result.visual_supplement is ports.visual_result
    assert result.visual_stage_status is VisualStageStatus.COMPLETED
    assert result.cleanup_status is WorkspaceCleanupStatus.COMPLETED


def test_dense_phone_window_runs_after_global_detection_and_cleans_up_once() -> None:
    ports = _Ports()

    result = _facade(ports, with_phone_window=True).run(_command())

    assert ports.trace == [
        "consent",
        "admit",
        "probe",
        "extract",
        "prompt",
        "seungun",
        "handoff",
        "decode",
        "detector",
        "phone_window_plan",
        "phone_window_decode",
        "phone_window_detector",
        "reliability",
        "visual",
        "delete",
    ]
    assert ports.seungun_calls == 1
    assert ports.trace.count("delete") == 1
    assert result.status is VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_SUPPLEMENTED


def test_dense_phone_window_failure_keeps_audio_and_cleans_up_once() -> None:
    ports = _Ports()
    ports.fail_at = "phone_window_decode"

    result = _facade(ports, with_phone_window=True).run(_command())

    assert ports.seungun_calls == 1
    assert ports.trace[-2:] == ["phone_window_decode", "delete"]
    assert ports.trace.count("delete") == 1
    assert result.status is VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_UNAVAILABLE
    assert result.audio_result is ports.audio_result
    assert result.reason_codes == ("phone_window_decode_failed",)
    assert result.visual_stage_status is VisualStageStatus.PHONE_WINDOW_DECODE_FAILED


def test_entrypoint_cannot_supply_phone_index_or_authority_receipts() -> None:
    assert {field.name for field in fields(VideoAttemptCommand)} == {
        "prompt_id",
        "video_locator",
        "consent_reference",
    }


def test_entrypoint_repr_hides_locator_and_consent_reference() -> None:
    command = VideoAttemptCommand(
        prompt_id="R00-S001",
        video_locator="/private/participant/video.mp4",
        consent_reference="opaque-consent-secret",
    )

    shown = repr(command)
    assert "/private/participant/video.mp4" not in shown
    assert "opaque-consent-secret" not in shown


def test_consent_denial_stops_before_file_admission() -> None:
    ports = _Ports()
    ports.consent_granted = False

    result = _facade(ports).run(_command())

    assert ports.trace == ["consent"]
    assert result.status is VideoAttemptStatus.FAILED_CLOSED
    assert result.reason_codes == ("consent_not_granted",)
    assert result.audio_result is None
    assert result.cleanup_status is WorkspaceCleanupStatus.NOT_APPLICABLE


@pytest.mark.parametrize(
    ("failure_stage", "expected_reason"),
    [
        ("probe", "media_probe_failed"),
        ("extract", "embedded_audio_extraction_failed"),
        ("prompt", "trusted_prompt_unavailable"),
        ("seungun", "seungun_runtime_failed"),
    ],
)
def test_pre_audio_failures_delete_restricted_asset(
    failure_stage: str, expected_reason: str
) -> None:
    ports = _Ports()
    ports.fail_at = failure_stage

    result = _facade(ports).run(_command())

    assert ports.trace[-1] == "delete"
    assert ports.trace.count("delete") == 1
    assert ports.seungun_calls <= 1
    assert result.status is VideoAttemptStatus.FAILED_CLOSED
    assert result.reason_codes == (expected_reason,)
    assert result.audio_result is None
    assert result.cleanup_status is WorkspaceCleanupStatus.COMPLETED


def test_delete_is_attempted_even_when_runtime_interrupt_propagates() -> None:
    class _InterruptingPorts(_Ports):
        def analyze(
            self, *, audio: RestrictedPcmAudio, prompt: TrustedPrompt
        ) -> SeungunRuntimeResult:
            self.trace.append("seungun")
            raise KeyboardInterrupt

    ports = _InterruptingPorts()

    with pytest.raises(KeyboardInterrupt):
        _facade(ports).run(_command())

    assert ports.trace[-1] == "delete"
    assert ports.trace.count("delete") == 1


def test_detector_success_does_not_synthesize_reliability() -> None:
    ports = _Ports()
    ports.reliability_supported = False

    result = _facade(ports).run(_command())

    assert ports.trace[-4:] == ["decode", "detector", "reliability", "delete"]
    assert "visual" not in ports.trace
    assert result.audio_result is ports.audio_result
    assert result.visual_supplement is None
    assert result.visual_stage_status is VisualStageStatus.BLOCKED_RELIABILITY
    assert result.reason_codes == ("landmark_reliability_below_gate",)
    with pytest.raises(ValueError, match="issued receipt"):
        VisualReliabilityDecision(supported=True, receipt=None)


@pytest.mark.parametrize(
    ("failure_stage", "visual_stage"),
    [
        ("handoff", VisualStageStatus.HANDOFF_FAILED),
        ("decode", VisualStageStatus.VIDEO_DECODE_FAILED),
        ("detector", VisualStageStatus.DETECTOR_FAILED),
        ("reliability", VisualStageStatus.RELIABILITY_FAILED),
        ("visual", VisualStageStatus.BACKEND_FAILED),
    ],
)
def test_visual_path_failure_never_overwrites_audio_result(
    failure_stage: str, visual_stage: VisualStageStatus
) -> None:
    ports = _Ports()
    ports.fail_at = failure_stage

    result = _facade(ports).run(_command())

    assert ports.seungun_calls == 1
    assert ports.trace[-1] == "delete"
    assert ports.trace.count("delete") == 1
    assert result.status is VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_UNAVAILABLE
    assert result.audio_result is ports.audio_result
    assert result.visual_supplement is None
    assert result.visual_stage_status is visual_stage
    assert result.cleanup_status is WorkspaceCleanupStatus.COMPLETED


def test_no_selected_phone_skips_visual_work_and_keeps_audio() -> None:
    ports = _Ports()
    ports.selected_phone_expected_index = None

    result = _facade(ports).run(_command())

    assert ports.trace == [
        "consent",
        "admit",
        "probe",
        "extract",
        "prompt",
        "seungun",
        "delete",
    ]
    assert result.audio_result is ports.audio_result
    assert result.visual_stage_status is VisualStageStatus.SKIPPED_NO_SELECTED_PHONE
    assert result.cleanup_status is WorkspaceCleanupStatus.COMPLETED


def test_delete_failure_suppresses_all_results_and_fails_closed() -> None:
    ports = _Ports()
    ports.fail_at = "delete"

    result = _facade(ports).run(_command())

    assert ports.trace[-2:] == ["visual", "delete"]
    assert result.status is VideoAttemptStatus.FAILED_CLOSED
    assert result.audio_result is None
    assert result.visual_supplement is None
    assert result.visual_stage_status is VisualStageStatus.CLEANUP_FAILED
    assert result.cleanup_status is WorkspaceCleanupStatus.FAILED
    assert result.reason_codes == ("workspace_cleanup_failed",)


def test_handoff_cannot_change_seungun_selected_phone() -> None:
    ports = _Ports()
    ports.handoff_index_offset = 1

    result = _facade(ports).run(_command())

    assert "decode" not in ports.trace
    assert result.audio_result is ports.audio_result
    assert result.visual_stage_status is VisualStageStatus.HANDOFF_FAILED
    assert result.reason_codes == ("host_visual_handoff_failed",)
