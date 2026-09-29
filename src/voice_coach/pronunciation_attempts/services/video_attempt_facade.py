"""Host-owned orchestration for one pronunciation attempt from user video.

The untrusted entrypoint deliberately accepts no phone index, expected-phone
sequence, binding receipt, or reliability receipt.  Trusted host ports produce
those values only after the admitted video's embedded audio has been analysed
by Seungun exactly once.

This module owns ordering and failure policy.  Source-file access, restricted
storage, decoder processes, Seungun, detector, reliability, and visual work all
remain behind injected ports.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Mapping, Protocol

from ..models import (
    AvContainerPolicy,
    MediaProbe,
    PronunciationAttemptMediaError,
    RestrictedMediaDeletionReceipt,
    RestrictedPcmAudio,
    RestrictedVideoAsset,
    Rgb24Sample,
    VideoAdmissionPolicy,
    VideoConsentAuthorization,
    VideoConsentDecision,
)
from ..phone_window import PhoneWindowDecodeRequest, Rgb24PhoneWindow
from ..ports import (
    AvContainerPort,
    PhoneWindowDecoderPort,
    RestrictedMediaStorePort,
)


class VideoAttemptStatus(StrEnum):
    """Terminal host status for a video-backed pronunciation attempt."""

    FAILED_CLOSED = "failed_closed"
    AUDIO_COMPLETED_VISUAL_UNAVAILABLE = "audio_completed_visual_unavailable"
    AUDIO_COMPLETED_VISUAL_SUPPLEMENTED = "audio_completed_visual_supplemented"


class VisualStageStatus(StrEnum):
    """Why the optional visual sidecar did or did not produce a supplement."""

    NOT_STARTED = "not_started"
    SKIPPED_NO_SELECTED_PHONE = "skipped_no_selected_phone"
    INVALID_SELECTED_PHONE = "invalid_selected_phone"
    HANDOFF_FAILED = "handoff_failed"
    VIDEO_DECODE_FAILED = "video_decode_failed"
    DETECTOR_FAILED = "detector_failed"
    PHONE_WINDOW_PLAN_FAILED = "phone_window_plan_failed"
    PHONE_WINDOW_DECODE_FAILED = "phone_window_decode_failed"
    PHONE_WINDOW_DETECTOR_FAILED = "phone_window_detector_failed"
    BLOCKED_RELIABILITY = "blocked_reliability"
    RELIABILITY_FAILED = "reliability_failed"
    BACKEND_FAILED = "backend_failed"
    COMPLETED = "completed"
    CLEANUP_FAILED = "cleanup_failed"


class WorkspaceCleanupStatus(StrEnum):
    """Restricted workspace cleanup status."""

    NOT_APPLICABLE = "not_applicable"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class VideoAttemptCommand:
    """Untrusted CLI/API input, intentionally free of authority outputs."""

    prompt_id: str
    video_locator: str = field(repr=False)
    consent_reference: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_non_empty("prompt_id", self.prompt_id)
        _require_non_empty("video_locator", self.video_locator)
        _require_non_empty("consent_reference", self.consent_reference)


@dataclass(frozen=True, slots=True)
class TrustedPrompt:
    """Prompt text and expected phones resolved from a trusted host source."""

    prompt_id: str
    revision: str
    normalized_text: str
    expected_phones: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty("prompt_id", self.prompt_id)
        _require_non_empty("revision", self.revision)
        _require_non_empty("normalized_text", self.normalized_text)
        if not self.expected_phones:
            raise ValueError("expected_phones must not be empty")
        if any(not phone.strip() for phone in self.expected_phones):
            raise ValueError("expected_phones must contain non-empty phones")


@dataclass(frozen=True, slots=True)
class SeungunRuntimeResult:
    """Authoritative audio result and immutable evidence from one runtime call."""

    audio_result: Mapping[str, object]
    analysis_evidence: object
    selected_phone_expected_index: int | None

    def __post_init__(self) -> None:
        selected_index = self.selected_phone_expected_index
        if selected_index is not None:
            if isinstance(selected_index, bool) or not isinstance(selected_index, int):
                raise ValueError("selected phone index must be an integer")
            if selected_index < 0:
                raise ValueError("selected phone index must be non-negative")


@dataclass(frozen=True, slots=True)
class HostVisualHandoff:
    """Host-issued sidecar handoff; never supplied by the entrypoint caller."""

    selected_phone_expected_index: int
    binding_receipt: object
    payload: object
    runtime_context: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        selected_index = self.selected_phone_expected_index
        if isinstance(selected_index, bool) or not isinstance(selected_index, int):
            raise ValueError("selected phone index must be an integer")
        if selected_index < 0:
            raise ValueError("selected phone index must be non-negative")


@dataclass(frozen=True, slots=True)
class VisualDetection:
    """Detector evidence; its existence does not imply reliability approval."""

    evidence: object


@dataclass(frozen=True, slots=True)
class VisualReliabilityDecision:
    """Independent reliability gate over detector evidence."""

    supported: bool
    receipt: object | None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", _unique(self.reason_codes))
        if self.supported and self.receipt is None:
            raise ValueError("supported reliability requires an issued receipt")


@dataclass(frozen=True, slots=True)
class VideoAttemptResult:
    """Privacy-safe host result; raw locators and exception text are omitted."""

    status: VideoAttemptStatus
    reason_codes: tuple[str, ...]
    audio_result: Mapping[str, object] | None
    visual_supplement: Mapping[str, object] | None
    visual_stage_status: VisualStageStatus
    cleanup_status: WorkspaceCleanupStatus
    cleanup_receipt: RestrictedMediaDeletionReceipt | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", _unique(self.reason_codes))


class ConsentAuthorizationPort(Protocol):
    """Resolve an opaque consent reference into a host authorization."""

    def authorize(
        self, *, prompt_id: str, consent_reference: str
    ) -> VideoConsentAuthorization: ...


class TrustedPromptPort(Protocol):
    """Resolve prompt content and expected phones from a trusted source."""

    def resolve(self, *, prompt_id: str) -> TrustedPrompt: ...


class SeungunRuntimePort(Protocol):
    """Run authoritative Seungun audio analysis exactly once per attempt."""

    def analyze(
        self, *, audio: RestrictedPcmAudio, prompt: TrustedPrompt
    ) -> SeungunRuntimeResult: ...


class HostVisualHandoffPort(Protocol):
    """Issue the selected-phone binding and visual-sidecar handoff."""

    def issue(
        self,
        *,
        authorization: VideoConsentAuthorization,
        asset: RestrictedVideoAsset,
        probe: MediaProbe,
        audio: RestrictedPcmAudio,
        prompt: TrustedPrompt,
        seungun_result: SeungunRuntimeResult,
    ) -> HostVisualHandoff: ...


class VisualDetectorPort(Protocol):
    """Produce visual evidence from a policy-bounded RGB sample."""

    def detect(
        self,
        *,
        sample: Rgb24Sample,
        probe: MediaProbe,
        handoff: HostVisualHandoff,
    ) -> VisualDetection: ...


class PhoneWindowVisualDetectorPort(Protocol):
    """Plan and track a dense window from already-bound global evidence."""

    def plan_phone_window(
        self,
        *,
        detection: VisualDetection,
        probe: MediaProbe,
        handoff: HostVisualHandoff,
    ) -> PhoneWindowDecodeRequest | None: ...

    def detect_phone_window(
        self,
        *,
        window: Rgb24PhoneWindow,
        detection: VisualDetection,
        probe: MediaProbe,
        handoff: HostVisualHandoff,
    ) -> VisualDetection: ...


class VisualReliabilityPort(Protocol):
    """Independently approve or reject detector evidence."""

    def evaluate(
        self, *, detection: VisualDetection, handoff: HostVisualHandoff
    ) -> VisualReliabilityDecision: ...


class VisualSupplementPort(Protocol):
    """Build an optional supplement from approved visual evidence."""

    def create(
        self,
        *,
        detection: VisualDetection,
        handoff: HostVisualHandoff,
        reliability: VisualReliabilityDecision,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class _Draft:
    status: VideoAttemptStatus
    reason_codes: tuple[str, ...]
    audio_result: Mapping[str, object] | None = None
    visual_supplement: Mapping[str, object] | None = None
    visual_stage_status: VisualStageStatus = VisualStageStatus.NOT_STARTED
    decoded_sample_times_ms: tuple[float, ...] = ()
    coaching_measurement_receipt: Mapping[str, object] | None = None


class VideoAttemptFacade:
    """Run the host-owned audio attempt and optional visual sidecar."""

    def __init__(
        self,
        *,
        consent_port: ConsentAuthorizationPort,
        media_store_port: RestrictedMediaStorePort,
        container_port: AvContainerPort,
        admission_policy: VideoAdmissionPolicy,
        container_policy: AvContainerPolicy,
        visual_sample_frame_count: int,
        prompt_port: TrustedPromptPort,
        seungun_port: SeungunRuntimePort,
        handoff_port: HostVisualHandoffPort,
        detector_port: VisualDetectorPort,
        reliability_port: VisualReliabilityPort,
        visual_port: VisualSupplementPort,
        phone_window_decoder_port: PhoneWindowDecoderPort | None = None,
        phone_window_detector_port: PhoneWindowVisualDetectorPort | None = None,
    ) -> None:
        if (
            isinstance(visual_sample_frame_count, bool)
            or not isinstance(visual_sample_frame_count, int)
            or visual_sample_frame_count <= 0
        ):
            raise ValueError("visual_sample_frame_count must be a positive integer")
        if visual_sample_frame_count > container_policy.maximum_rgb_sample_frames:
            raise ValueError("visual sample frame count exceeds container policy")
        if (phone_window_decoder_port is None) != (
            phone_window_detector_port is None
        ):
            raise ValueError(
                "phone-window decoder and detector must be configured together"
            )
        self._consent_port = consent_port
        self._media_store_port = media_store_port
        self._container_port = container_port
        self._admission_policy = admission_policy
        self._container_policy = container_policy
        self._visual_sample_frame_count = visual_sample_frame_count
        self._prompt_port = prompt_port
        self._seungun_port = seungun_port
        self._handoff_port = handoff_port
        self._detector_port = detector_port
        self._reliability_port = reliability_port
        self._visual_port = visual_port
        self._phone_window_decoder_port = phone_window_decoder_port
        self._phone_window_detector_port = phone_window_detector_port

    def run(self, command: VideoAttemptCommand) -> VideoAttemptResult:
        """Run one attempt, deleting every successfully admitted asset."""

        try:
            authorization = self._consent_port.authorize(
                prompt_id=command.prompt_id,
                consent_reference=command.consent_reference,
            )
        except Exception:
            return _result(
                _Draft(
                    status=VideoAttemptStatus.FAILED_CLOSED,
                    reason_codes=("consent_authorization_failed",),
                )
            )

        if authorization.decision is not VideoConsentDecision.GRANTED:
            return _result(
                _Draft(
                    status=VideoAttemptStatus.FAILED_CLOSED,
                    reason_codes=("consent_not_granted",),
                )
            )

        try:
            asset = self._media_store_port.admit(
                command.video_locator,
                attempt_id_sha256=authorization.attempt_id_sha256,
                authorization=authorization,
                policy=self._admission_policy,
            )
        except Exception as error:
            return _result(
                _Draft(
                    status=VideoAttemptStatus.FAILED_CLOSED,
                    reason_codes=(_media_reason(error, "media_admission_failed"),),
                )
            )

        cleanup_receipt: RestrictedMediaDeletionReceipt | None = None
        cleanup_error: Exception | None = None
        try:
            try:
                draft = self._run_in_asset(
                    command=command,
                    authorization=authorization,
                    asset=asset,
                )
            except Exception:  # pragma: no cover - last-resort service boundary
                draft = _Draft(
                    status=VideoAttemptStatus.FAILED_CLOSED,
                    reason_codes=("attempt_orchestration_failed",),
                )
        finally:
            # A BaseException still propagates, but only after deletion is tried.
            try:
                cleanup_receipt = self._media_store_port.delete(asset)
            except Exception as error:
                cleanup_error = error

        return self._finalize_cleanup(
            draft=draft,
            cleanup_receipt=cleanup_receipt,
            cleanup_error=cleanup_error,
        )

    def _run_in_asset(
        self,
        *,
        command: VideoAttemptCommand,
        authorization: VideoConsentAuthorization,
        asset: RestrictedVideoAsset,
    ) -> _Draft:
        try:
            probe = self._container_port.probe(asset, self._container_policy)
        except Exception as error:
            return _Draft(
                status=VideoAttemptStatus.FAILED_CLOSED,
                reason_codes=(_media_reason(error, "media_probe_failed"),),
            )

        try:
            audio = self._container_port.extract_embedded_audio(
                asset,
                self._container_policy,
            )
        except Exception as error:
            return _Draft(
                status=VideoAttemptStatus.FAILED_CLOSED,
                reason_codes=(
                    _media_reason(error, "embedded_audio_extraction_failed"),
                ),
            )

        try:
            prompt = self._prompt_port.resolve(prompt_id=command.prompt_id)
            if prompt.prompt_id != command.prompt_id:
                raise ValueError("trusted prompt identifier mismatch")
        except Exception:
            return _Draft(
                status=VideoAttemptStatus.FAILED_CLOSED,
                reason_codes=("trusted_prompt_unavailable",),
            )

        try:
            # This is the sole Seungun runtime invocation in the use case.
            seungun_result = self._seungun_port.analyze(audio=audio, prompt=prompt)
        except Exception:
            return _Draft(
                status=VideoAttemptStatus.FAILED_CLOSED,
                reason_codes=("seungun_runtime_failed",),
            )

        audio_result = seungun_result.audio_result
        selected_index = seungun_result.selected_phone_expected_index
        if selected_index is None:
            return _audio_only(
                audio_result=audio_result,
                reasons=("seungun_selected_phone_absent",),
                visual_stage=VisualStageStatus.SKIPPED_NO_SELECTED_PHONE,
            )
        if selected_index >= len(prompt.expected_phones):
            return _audio_only(
                audio_result=audio_result,
                reasons=("seungun_selected_phone_out_of_range",),
                visual_stage=VisualStageStatus.INVALID_SELECTED_PHONE,
            )

        try:
            handoff = self._handoff_port.issue(
                authorization=authorization,
                asset=asset,
                probe=probe,
                audio=audio,
                prompt=prompt,
                seungun_result=seungun_result,
            )
            if handoff.selected_phone_expected_index != selected_index:
                raise ValueError("host handoff changed Seungun's selected phone")
        except Exception:
            return _audio_only(
                audio_result=audio_result,
                reasons=("host_visual_handoff_failed",),
                visual_stage=VisualStageStatus.HANDOFF_FAILED,
            )

        return self._run_visual_stages(asset=asset, probe=probe, handoff=handoff, audio_result=audio_result)

    def _run_visual_stages(self, *, asset, probe, handoff, audio_result, decode_cache=None):
        decoded_sample_times_ms = ()
        try:
            sample = decode_cache.get("global_sample") if decode_cache is not None else None
            cache_key = (asset.container_sha256, self._container_policy, self._visual_sample_frame_count)
            if decode_cache is not None and decode_cache.get("source_key") != cache_key:
                sample = None
            if sample is None:
                sample = self._container_port.decode_rgb24_sample(
                    asset, self._container_policy, frame_count=self._visual_sample_frame_count)
                if decode_cache is not None:
                    decode_cache["global_sample"] = sample
                    decode_cache["source_key"] = cache_key
        except Exception as error:
            return _audio_only(
                audio_result=audio_result,
                reasons=(_media_reason(error, "video_decode_failed"),),
                visual_stage=VisualStageStatus.VIDEO_DECODE_FAILED,
            )

        try:
            detection = self._detector_port.detect(
                sample=sample,
                probe=probe,
                handoff=handoff,
            )
        except Exception:
            return _audio_only(
                audio_result=audio_result,
                reasons=("visual_detector_failed",),
                visual_stage=VisualStageStatus.DETECTOR_FAILED,
            )

        if (
            self._phone_window_decoder_port is not None
            and self._phone_window_detector_port is not None
        ):
            try:
                phone_window_request = (
                    self._phone_window_detector_port.plan_phone_window(
                        detection=detection,
                        probe=probe,
                        handoff=handoff,
                    )
                )
            except Exception:
                return _audio_only(
                    audio_result=audio_result,
                    reasons=("phone_window_planning_failed",),
                    visual_stage=VisualStageStatus.PHONE_WINDOW_PLAN_FAILED,
                )

            if phone_window_request is not None:
                try:
                    phone_window = (
                        self._phone_window_decoder_port.decode_rgb24_window(
                            asset,
                            self._container_policy,
                            probe=probe,
                            request=phone_window_request,
                        )
                    )
                    decoded_sample_times_ms = tuple(t / 1000 for t in phone_window.decode_receipt.decoded_timestamps_us)
                except Exception as error:
                    return _audio_only(
                        audio_result=audio_result,
                        reasons=(
                            _media_reason(error, "phone_window_decode_failed"),
                        ),
                        visual_stage=VisualStageStatus.PHONE_WINDOW_DECODE_FAILED,
                    )

                try:
                    detection = (
                        self._phone_window_detector_port.detect_phone_window(
                            window=phone_window,
                            detection=detection,
                            probe=probe,
                            handoff=handoff,
                        )
                    )
                except Exception:
                    return _audio_only(
                        audio_result=audio_result,
                        reasons=("phone_window_detector_failed",),
                        visual_stage=(
                            VisualStageStatus.PHONE_WINDOW_DETECTOR_FAILED
                        ),
                    )

        try:
            reliability = self._reliability_port.evaluate(
                detection=detection,
                handoff=handoff,
            )
        except Exception:
            return _audio_only(
                audio_result=audio_result,
                reasons=("visual_reliability_failed",),
                visual_stage=VisualStageStatus.RELIABILITY_FAILED,
            )

        if not reliability.supported:
            return _audio_only(
                audio_result=audio_result,
                reasons=reliability.reason_codes
                or ("visual_reliability_not_supported",),
                visual_stage=VisualStageStatus.BLOCKED_RELIABILITY,
            )

        try:
            receipt = None
            observer = getattr(self._visual_port, "create_with_observations", None)
            if observer is not None:
                visual_supplement, receipt = observer(
                    detection=detection, handoff=handoff, reliability=reliability)
            else:
                visual_supplement = self._visual_port.create(
                    detection=detection, handoff=handoff, reliability=reliability)
        except Exception:
            return _audio_only(
                audio_result=audio_result,
                reasons=("visual_backend_failed",),
                visual_stage=VisualStageStatus.BACKEND_FAILED,
            )

        return _Draft(
            status=VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_SUPPLEMENTED,
            reason_codes=(),
            audio_result=audio_result,
            visual_supplement=visual_supplement,
            visual_stage_status=VisualStageStatus.COMPLETED,
            decoded_sample_times_ms=decoded_sample_times_ms,
            coaching_measurement_receipt=receipt,
        )

    def run_http(self, *, video_locator, attempt_id_sha256, expected_sha256, prompt_id):
        """Called only after HTTP authentication, current claim and S3 integrity checks."""
        asset = self._media_store_port.admit_http(video_locator, attempt_id_sha256=attempt_id_sha256,
            expected_sha256=expected_sha256, policy=self._admission_policy)
        try:
            probe = self._container_port.probe(asset, self._container_policy)
            audio = self._container_port.extract_embedded_audio(asset, self._container_policy)
            prompt = self._prompt_port.resolve(prompt_id=prompt_id)
            seungun = self._seungun_port.analyze(audio=audio, prompt=prompt)
            if seungun.selected_phone_expected_index is None:
                return None
            handoff = self._handoff_port.issue_http(asset=asset, probe=probe, audio=audio,
                prompt=prompt, seungun_result=seungun)
            draft = self._run_visual_stages(asset=asset, probe=probe, handoff=handoff,
                                           audio_result=seungun.audio_result)
            return draft.visual_supplement
        finally:
            self._media_store_port.delete(asset)

    def run_http_observations(self, *, video_locator, attempt_id_sha256, expected_sha256,
                              prompt_id, expected_indices):
        """Bounded host-admitted coaching windows, sharing decode and Seungun.

        This is not a public endpoint. The backend coaching policy supplies the
        indices; each window still goes through binding, QC, sync and reliability.
        """
        if len(expected_indices) > 3 or len(set(expected_indices)) != len(expected_indices):
            raise ValueError("coaching_visual_candidate_limit")
        asset = self._media_store_port.admit_http(video_locator, attempt_id_sha256=attempt_id_sha256,
            expected_sha256=expected_sha256, policy=self._admission_policy)
        try:
            probe = self._container_port.probe(asset, self._container_policy)
            audio = self._container_port.extract_embedded_audio(asset, self._container_policy)
            prompt = self._prompt_port.resolve(prompt_id=prompt_id)
            seungun = self._seungun_port.analyze(audio=audio, prompt=prompt)
            rows = seungun.analysis_evidence["phones"]
            observations = []
            decode_cache = {}  # Request-local raw sample; never reuse candidate-bound receipts.
            for index in expected_indices:
                if type(index) is not int or not 0 <= index < len(rows):
                    raise ValueError("coaching_visual_index_invalid")
                row = rows[index]
                if not row.get("is_error") or row.get("alignment_operation") == "deletion" or row.get("start_ms") is None:
                    raise ValueError("coaching_visual_unadmitted_candidate")
                selected = replace(seungun, selected_phone_expected_index=index)
                handoff = self._handoff_port.issue_http(asset=asset, probe=probe, audio=audio,
                    prompt=prompt, seungun_result=selected)
                draft = self._run_visual_stages(asset=asset, probe=probe, handoff=handoff,
                                               audio_result=seungun.audio_result, decode_cache=decode_cache)
                observations.append(dict(expectedIndex=index, stage=draft.visual_stage_status.value,
                    reasonCodes=list(draft.reason_codes), supplement=draft.visual_supplement,
                    measurementReceipt=draft.coaching_measurement_receipt,
                    decodedSampleTimesMs=list(draft.decoded_sample_times_ms), videoDurationMs=probe.duration_ms))
            return observations
        finally:
            self._media_store_port.delete(asset)

    def _finalize_cleanup(
        self,
        *,
        draft: _Draft,
        cleanup_receipt: RestrictedMediaDeletionReceipt | None,
        cleanup_error: Exception | None,
    ) -> VideoAttemptResult:
        if cleanup_error is not None:
            failed_draft = replace(
                draft,
                status=VideoAttemptStatus.FAILED_CLOSED,
                reason_codes=_unique(
                    (
                        *draft.reason_codes,
                        _media_reason(cleanup_error, "workspace_cleanup_failed"),
                    )
                ),
                audio_result=None,
                visual_supplement=None,
                visual_stage_status=VisualStageStatus.CLEANUP_FAILED,
            )
            return _result(
                failed_draft,
                cleanup_status=WorkspaceCleanupStatus.FAILED,
            )

        if cleanup_receipt is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("cleanup completed without a deletion receipt")
        return _result(
            draft,
            cleanup_status=WorkspaceCleanupStatus.COMPLETED,
            cleanup_receipt=cleanup_receipt,
        )


def _audio_only(
    *,
    audio_result: Mapping[str, object],
    reasons: tuple[str, ...],
    visual_stage: VisualStageStatus,
) -> _Draft:
    return _Draft(
        status=VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_UNAVAILABLE,
        reason_codes=reasons,
        audio_result=audio_result,
        visual_stage_status=visual_stage,
    )


def _result(
    draft: _Draft,
    *,
    cleanup_status: WorkspaceCleanupStatus = WorkspaceCleanupStatus.NOT_APPLICABLE,
    cleanup_receipt: RestrictedMediaDeletionReceipt | None = None,
) -> VideoAttemptResult:
    return VideoAttemptResult(
        status=draft.status,
        reason_codes=draft.reason_codes,
        audio_result=draft.audio_result,
        visual_supplement=draft.visual_supplement,
        visual_stage_status=draft.visual_stage_status,
        cleanup_status=cleanup_status,
        cleanup_receipt=cleanup_receipt,
    )


def _media_reason(error: Exception, fallback: str) -> str:
    if isinstance(error, PronunciationAttemptMediaError):
        return error.code.value
    return fallback


def _require_non_empty(field_name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


__all__ = [
    "ConsentAuthorizationPort",
    "HostVisualHandoff",
    "HostVisualHandoffPort",
    "SeungunRuntimePort",
    "SeungunRuntimeResult",
    "TrustedPrompt",
    "TrustedPromptPort",
    "VideoAttemptCommand",
    "VideoAttemptFacade",
    "VideoAttemptResult",
    "VideoAttemptStatus",
    "VisualDetection",
    "VisualDetectorPort",
    "VisualReliabilityDecision",
    "VisualReliabilityPort",
    "VisualStageStatus",
    "VisualSupplementPort",
    "WorkspaceCleanupStatus",
]
