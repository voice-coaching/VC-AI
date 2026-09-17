"""Concrete composition bridges for the host-owned video-attempt service.

All public failures are stable path-free codes.  The Seungun runtime depends
only on a structural detector protocol; importing a concrete detector here
would let application wiring silently replace the frozen runtime boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
import threading
from typing import Protocol
import wave

from voice_coach.pronunciation_feedback.adapters.catalog_r00 import R00PromptCatalog
from voice_coach.pronunciation_feedback.adapters.expected_phones_subprocess import (
    SubprocessG2PKExpectedPhoneFrontend,
)
from voice_coach.pronunciation_feedback.coaching_payload import CoachingPayloadError
from voice_coach.pronunciation_feedback.ports import GroundedAdviceGeneratorPort
from voice_coach.pronunciation_feedback.seungun_bridge import (
    SEUNGUN_SCHEMA_VERSION,
    generate_grounded_phone_feedback,
)
from voice_coach.visual_feedback.models.binding import (
    FACIAL_VIDEO_DATA_CATEGORY,
    VISUAL_PROCESSING_PURPOSE,
    ConsentReceipt,
    MediaTimebaseReceipt,
    RestrictedVideoHandle,
    SameAttemptBindingReceipt,
    HttpSameAttemptBindingReceipt,
    UpstreamReliabilityReceipt,
)
from voice_coach.visual_feedback.models.enums import (
    ConsentDecision,
    UpstreamReliabilityStatus,
)
from voice_coach.visual_feedback.models.integrity import (
    canonical_json_bytes,
    sha256_document,
)
from voice_coach.visual_feedback.adapters.seungun import parse_seungun_analysis
from voice_coach.visual_feedback.models.evidence import UpstreamPhoneAnchor
from voice_coach.visual_feedback.services.handoff import (
    SeungunAttemptBindingFacade,
    SeungunVisualHandoff,
)

from ..models import (
    MediaProbe,
    RestrictedPcmAudio,
    RestrictedVideoAsset,
    Rgb24Sample,
    VideoConsentAuthorization,
    VideoConsentDecision,
)
from ..ports import RestrictedMediaStorePort
from ..phone_window import (
    DensePhoneWindowEvidence,
    PhoneWindowDecodePolicy,
    PhoneWindowDecodeRequest,
    PhoneWindowTrackingReceipt,
    PolicyValidatedAvSyncOffset,
    Rgb24PhoneWindow,
    RuntimePhoneWindowReceipt,
)
from ..services.video_attempt_facade import (
    HostVisualHandoff,
    SeungunRuntimeResult,
    TrustedPrompt,
    VisualDetection,
    VisualReliabilityDecision,
)
from .bounded_isolated_process import (
    IsolatedProcessError,
    run_bounded_isolated_process,
)
from .calibrated_runtime_decisions import (
    RuntimeAvSyncDecision,
    RuntimeVideoQcDecision,
)
from .lip_audio_av_sync import AvSyncCandidateEvidence
from .video_qc import VideoQcCandidateEvidence


_SEUNGUN_WORKER_PROTOCOL_VERSION = "voice_coach.seungun_runtime_worker.v1"


class PronunciationAttemptRuntimeError(RuntimeError):
    """Stable path-free failure from a concrete runtime bridge."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AttemptNoncePort(Protocol):
    """Random in production and deterministic in tests."""

    def __call__(self) -> bytes: ...


class CompletedPronunciationAnalysis(Protocol):
    """Structural output returned by the injected frozen detector."""

    def to_json_object(self) -> Mapping[str, object]: ...


class FrozenPronunciationDetectorPort(Protocol):
    """Detector contract without importing its concrete implementation."""

    def analyze(
        self,
        *,
        audio_path: str | Path,
        expected_phones: Sequence[str],
    ) -> Mapping[str, object] | CompletedPronunciationAnalysis: ...


class IsolatedFrozenSeungunDetectorAdapter:
    """Run the concrete frozen detector only inside the isolated worker."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        device: str,
        worker_python: str | Path | None = None,
        timeout_seconds: float = 180.0,
        maximum_output_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        root = Path(repository_root)
        if not root.is_absolute():
            raise ValueError("repository_root must be absolute")
        if timeout_seconds <= 0 or maximum_output_bytes <= 0:
            raise ValueError("worker bounds must be positive")
        self._root = root
        self._device = device
        self._worker_python = Path(worker_python or sys.executable).absolute()
        self._timeout_seconds = timeout_seconds
        self._maximum_output_bytes = maximum_output_bytes

    def analyze(
        self,
        *,
        audio_path: str | Path,
        expected_phones: Sequence[str],
    ) -> Mapping[str, object]:
        if not self._worker_python.is_file():
            raise PronunciationAttemptRuntimeError(
                "seungun_worker_unavailable"
            )
        request = {
            "protocol_version": _SEUNGUN_WORKER_PROTOCOL_VERSION,
            "audio_path": str(Path(audio_path).absolute()),
            "expected_phones": list(expected_phones),
            "device": self._device,
        }
        input_bytes = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        environment = {
            "DO_NOT_TRACK": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_OFFLINE": "1",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join(
                (str(self._root / "src"), str(self._root / "seungun/src"))
            ),
            "TOKENIZERS_PARALLELISM": "false",
            "TRANSFORMERS_OFFLINE": "1",
        }
        target = [
            str(self._worker_python),
            "-m",
            (
                "voice_coach.pronunciation_attempts.adapters."
                "seungun_runtime_worker"
            ),
        ]
        try:
            completed = run_bounded_isolated_process(
                launcher_python=sys.executable,
                target=target,
                input_bytes=input_bytes,
                cwd=str(self._root),
                environment=environment,
                timeout_seconds=self._timeout_seconds,
                maximum_output_bytes=self._maximum_output_bytes,
                address_space_bytes=64 * 1024 * 1024 * 1024,
                allow_local_sockets=True,
            )
        except IsolatedProcessError:
            raise PronunciationAttemptRuntimeError(
                "seungun_worker_failed"
            ) from None
        try:
            response = json.loads(completed.stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PronunciationAttemptRuntimeError(
                "seungun_worker_invalid"
            ) from None
        if (
            completed.return_code != 0
            or not isinstance(response, dict)
            or set(response)
            != {
                "protocol_version",
                "status",
                "network_isolation",
                "analysis",
            }
            or response["protocol_version"] != _SEUNGUN_WORKER_PROTOCOL_VERSION
            or response["status"] != "completed"
            or response["network_isolation"] != "seccomp_socket_denied"
            or not isinstance(response["analysis"], Mapping)
        ):
            raise PronunciationAttemptRuntimeError("seungun_worker_failed")
        return dict(response["analysis"])


class ConsentReceiptResolverPort(Protocol):
    """Resolve the receipt issued with an exact authorization."""

    def receipt_for(
        self, authorization: VideoConsentAuthorization
    ) -> ConsentReceipt: ...


class LipLandmarkDetectorPort(Protocol):
    """Structural MediaPipe boundary consumed by the visual detector bridge."""

    def detect(self, sample: Rgb24Sample) -> object: ...


class AvSyncCandidatePort(Protocol):
    """Extract uncalibrated content-sync candidates from restricted evidence."""

    def measure(
        self,
        *,
        audio: RestrictedPcmAudio,
        landmarks: object,
        probe: MediaProbe,
    ) -> AvSyncCandidateEvidence: ...


class VideoQcCandidatePort(Protocol):
    """Measure calibration-pending QC evidence over decoded RGB/landmarks."""

    def measure(
        self, *, sample: Rgb24Sample, landmarks: object
    ) -> VideoQcCandidateEvidence: ...


class VideoQcDecisionPort(Protocol):
    """Apply a separately calibrated policy to one exact QC candidate."""

    def decide(
        self,
        *,
        candidate: VideoQcCandidateEvidence,
        binding_receipt: SameAttemptBindingReceipt,
    ) -> RuntimeVideoQcDecision: ...


class AvSyncDecisionPort(Protocol):
    """Apply a separately calibrated policy to one exact sync candidate."""

    def decide(
        self,
        *,
        candidate: AvSyncCandidateEvidence,
        binding_receipt: SameAttemptBindingReceipt,
    ) -> RuntimeAvSyncDecision: ...


@dataclass(frozen=True, slots=True)
class RuntimeVisualDetectionEvidence:
    """Cryptographically bind one landmark result to its sample and handoff."""

    binding_receipt_sha256: str
    container_sha256: str
    sample_timestamps_sha256: str
    landmark_evidence_sha256: str
    model_sha256: str
    video_qc_candidate_receipt_sha256: str | None
    video_qc_receipt_sha256: str | None
    av_sync_candidate_receipt_sha256: str | None
    av_sync_receipt_sha256: str | None
    av_sync_offset_receipt_sha256: str | None
    detection_receipt_sha256: str
    landmark_evidence: object = field(repr=False)
    video_qc_evidence: VideoQcCandidateEvidence | None = field(
        default=None, repr=False
    )
    video_qc_decision: RuntimeVideoQcDecision | None = field(
        default=None, repr=False
    )
    av_sync_candidate_evidence: AvSyncCandidateEvidence | None = field(
        default=None, repr=False
    )
    av_sync_decision: RuntimeAvSyncDecision | None = field(
        default=None, repr=False
    )
    av_sync_evidence: PolicyValidatedAvSyncOffset | None = field(
        default=None, repr=False
    )
    phone_window_evidence: DensePhoneWindowEvidence | None = field(
        default=None,
        repr=False,
    )
    schema_version: str = "voice_coach.runtime_visual_detection.v3"

    def __post_init__(self) -> None:
        if self.schema_version != "voice_coach.runtime_visual_detection.v3":
            raise ValueError("visual detection schema differs")
        for name in (
            "binding_receipt_sha256",
            "container_sha256",
            "sample_timestamps_sha256",
            "landmark_evidence_sha256",
            "model_sha256",
            "detection_receipt_sha256",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{name} must be lowercase SHA-256")
        if self.detection_receipt_sha256 != sha256_document(
            self.unsigned_json_object()
        ):
            raise ValueError("visual detection receipt digest differs")
        for name in (
            "video_qc_candidate_receipt_sha256",
            "video_qc_receipt_sha256",
            "av_sync_candidate_receipt_sha256",
            "av_sync_receipt_sha256",
            "av_sync_offset_receipt_sha256",
        ):
            value = getattr(self, name)
            if value is not None and not _is_sha256(value):
                raise ValueError(f"{name} must be lowercase SHA-256")
        if (self.video_qc_candidate_receipt_sha256 is None) != (
            self.video_qc_evidence is None
        ):
            raise ValueError("video QC candidate and receipt must appear together")
        if self.video_qc_evidence is not None and (
            self.video_qc_candidate_receipt_sha256
            != self.video_qc_evidence.receipt_sha256
        ):
            raise ValueError("video QC candidate receipt differs")
        if (self.video_qc_receipt_sha256 is None) != (
            self.video_qc_decision is None
        ):
            raise ValueError("video QC decision and receipt must appear together")
        if self.video_qc_decision is not None and (
            self.video_qc_evidence is None
            or self.video_qc_receipt_sha256
            != self.video_qc_decision.receipt_sha256
            or self.video_qc_decision.source_candidate_receipt_sha256
            != self.video_qc_evidence.receipt_sha256
        ):
            raise ValueError("video QC decision lineage differs")
        if (self.av_sync_candidate_receipt_sha256 is None) != (
            self.av_sync_candidate_evidence is None
        ):
            raise ValueError("A/V sync candidate and receipt must appear together")
        if self.av_sync_candidate_evidence is not None and (
            self.av_sync_candidate_receipt_sha256
            != self.av_sync_candidate_evidence.receipt_sha256
        ):
            raise ValueError("A/V sync candidate receipt differs")
        if (self.av_sync_receipt_sha256 is None) != (
            self.av_sync_decision is None
        ):
            raise ValueError("A/V sync decision and receipt must appear together")
        if self.av_sync_decision is not None and (
            self.av_sync_candidate_evidence is None
            or self.av_sync_receipt_sha256
            != self.av_sync_decision.receipt_sha256
            or self.av_sync_decision.source_candidate_receipt_sha256
            != self.av_sync_candidate_evidence.receipt_sha256
        ):
            raise ValueError("A/V sync decision lineage differs")
        if (self.av_sync_offset_receipt_sha256 is None) != (
            self.av_sync_evidence is None
        ):
            raise ValueError("A/V sync offset and receipt must appear together")
        if self.av_sync_evidence is not None and (
            self.av_sync_decision is None
            or self.av_sync_decision.status != "synchronized"
            or self.av_sync_offset_receipt_sha256
            != self.av_sync_evidence.receipt_sha256
            or self.av_sync_evidence.av_sync_decision_receipt_sha256
            != self.av_sync_decision.receipt_sha256
        ):
            raise ValueError("A/V sync offset lineage differs")
        if self.phone_window_evidence is not None and (
            self.phone_window_evidence.runtime_receipt.detection_receipt_sha256
            != self.detection_receipt_sha256
            or self.phone_window_evidence.runtime_receipt.binding_receipt_sha256
            != self.binding_receipt_sha256
        ):
            raise ValueError("phone-window evidence differs from global detection")

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "binding_receipt_sha256": self.binding_receipt_sha256,
            "container_sha256": self.container_sha256,
            "sample_timestamps_sha256": self.sample_timestamps_sha256,
            "landmark_evidence_sha256": self.landmark_evidence_sha256,
            "model_sha256": self.model_sha256,
            "video_qc_candidate_receipt_sha256": (
                self.video_qc_candidate_receipt_sha256
            ),
            "video_qc_receipt_sha256": self.video_qc_receipt_sha256,
            "av_sync_candidate_receipt_sha256": (
                self.av_sync_candidate_receipt_sha256
            ),
            "av_sync_receipt_sha256": self.av_sync_receipt_sha256,
            "av_sync_offset_receipt_sha256": (
                self.av_sync_offset_receipt_sha256
            ),
        }


class LocalExplicitConsentAuthority:
    """Issue one attempt authorization after exact local-token comparison."""

    def __init__(
        self,
        *,
        consent_token: str,
        clock: Callable[[], datetime],
        attempt_nonce: AttemptNoncePort,
        validity: timedelta = timedelta(minutes=15),
        retention_policy_id: str = "delete_on_completion_v1",
        purpose: str = VISUAL_PROCESSING_PURPOSE,
        data_category: str = FACIAL_VIDEO_DATA_CATEGORY,
    ) -> None:
        if not isinstance(consent_token, str) or not consent_token:
            raise ValueError("consent_token must not be empty")
        if validity <= timedelta(0):
            raise ValueError("validity must be positive")
        if not retention_policy_id:
            raise ValueError("retention_policy_id must not be empty")
        self._consent_token = consent_token.encode("utf-8")
        self._clock = clock
        self._attempt_nonce = attempt_nonce
        self._validity = validity
        self._retention_policy_id = retention_policy_id
        self._purpose = purpose
        self._data_category = data_category
        self._receipts: dict[str, tuple[VideoConsentAuthorization, ConsentReceipt]] = {}
        self._lock = threading.RLock()

    def authorize(
        self, *, prompt_id: str, consent_reference: str
    ) -> VideoConsentAuthorization:
        if not isinstance(prompt_id, str) or not prompt_id:
            raise PronunciationAttemptRuntimeError("consent_request_invalid")
        now = _require_utc_clock(self._clock)
        try:
            nonce = self._attempt_nonce()
        except Exception:
            raise PronunciationAttemptRuntimeError(
                "attempt_nonce_unavailable"
            ) from None
        if not isinstance(nonce, bytes) or len(nonce) < 16:
            raise PronunciationAttemptRuntimeError("attempt_nonce_invalid")
        attempt_id_sha256 = sha256_document(
            {
                "schema_version": "voice_coach.local_video_attempt_id.v1",
                "prompt_id_sha256": sha256_document({"prompt_id": prompt_id}),
                "attempt_nonce_sha256": hashlib.sha256(nonce).hexdigest(),
                "issued_at_utc": _utc_text(now),
            }
        )
        supplied = (
            consent_reference.encode("utf-8")
            if isinstance(consent_reference, str)
            else b""
        )
        granted = secrets.compare_digest(supplied, self._consent_token)
        visual_decision = (
            ConsentDecision.GRANTED if granted else ConsentDecision.DENIED
        )
        media_decision = (
            VideoConsentDecision.GRANTED
            if granted
            else VideoConsentDecision.DENIED
        )
        expires = now + self._validity
        receipt = ConsentReceipt.issue(
            attempt_id_sha256=attempt_id_sha256,
            decision=visual_decision,
            purpose=self._purpose,
            data_category=self._data_category,
            retention_policy_id=self._retention_policy_id,
            delete_on_completion=True,
            remote_egress_allowed=False,
            issued_at_utc=_utc_text(now),
            expires_at_utc=_utc_text(expires),
        )
        authorization = VideoConsentAuthorization(
            attempt_id_sha256=attempt_id_sha256,
            consent_receipt_sha256=receipt.receipt_sha256,
            decision=media_decision,
            purpose=receipt.purpose,
            data_category=receipt.data_category,
            retention_policy_id=receipt.retention_policy_id,
            delete_on_completion=receipt.delete_on_completion,
            remote_egress_allowed=receipt.remote_egress_allowed,
            issued_at_utc=now,
            expires_at_utc=expires,
        )
        with self._lock:
            if attempt_id_sha256 in self._receipts:
                raise PronunciationAttemptRuntimeError("attempt_nonce_reused")
            self._receipts[attempt_id_sha256] = (authorization, receipt)
        return authorization

    def receipt_for(
        self, authorization: VideoConsentAuthorization
    ) -> ConsentReceipt:
        with self._lock:
            record = self._receipts.get(authorization.attempt_id_sha256)
        if record is None or record[0] != authorization:
            raise PronunciationAttemptRuntimeError("consent_receipt_unavailable")
        return record[1]


class R00TrustedPromptAdapter:
    """Resolve an R00 prompt and its server-side g2pK expected phones."""

    def __init__(
        self,
        *,
        catalog: R00PromptCatalog,
        expected_phone_frontend: SubprocessG2PKExpectedPhoneFrontend,
    ) -> None:
        self._catalog = catalog
        self._expected_phone_frontend = expected_phone_frontend

    def resolve(self, *, prompt_id: str) -> TrustedPrompt:
        try:
            prompt = self._catalog.get_prompt(prompt_id)
            sequence = self._expected_phone_frontend.expected_phones(
                prompt.normalized_text
            )
            revision_digest = sha256_document(
                {
                    "schema_version": "voice_coach.r00_trusted_prompt.v1",
                    "catalog_revision": prompt.catalog_revision,
                    "prompt_id_sha256": sha256_document(
                        {"prompt_id": prompt.prompt_id}
                    ),
                    "normalized_text_sha256": prompt.normalized_text_sha256,
                    "expected_phone_backend_id": sequence.backend_id,
                    "expected_phone_revision": sequence.revision,
                    "expected_phone_token_sha256": sequence.token_sha256,
                }
            )
            return TrustedPrompt(
                prompt_id=prompt.prompt_id,
                revision=f"r00_g2pk_{revision_digest}",
                normalized_text=prompt.normalized_text,
                expected_phones=sequence.tokens,
            )
        except Exception as error:
            if isinstance(error, PronunciationAttemptRuntimeError):
                raise
            raise PronunciationAttemptRuntimeError(
                "trusted_prompt_unavailable"
            ) from None


class FixedLocalTrustedPromptAdapter:
    """Resolve one host-fixed local smoke prompt through the pinned g2pK.

    This adapter does not accept caller-provided text and does not pretend that
    a short local smoke sentence belongs to the frozen 20-sentence R00 card.
    """

    def __init__(
        self,
        *,
        prompt_id: str,
        normalized_text: str,
        expected_phone_frontend: SubprocessG2PKExpectedPhoneFrontend,
    ) -> None:
        if not prompt_id or not normalized_text:
            raise ValueError("fixed local prompt fields must not be empty")
        self._prompt_id = prompt_id
        self._normalized_text = normalized_text
        self._expected_phone_frontend = expected_phone_frontend

    def resolve(self, *, prompt_id: str) -> TrustedPrompt:
        if prompt_id != self._prompt_id:
            raise PronunciationAttemptRuntimeError("trusted_prompt_unavailable")
        try:
            sequence = self._expected_phone_frontend.expected_phones(
                self._normalized_text
            )
        except Exception:
            raise PronunciationAttemptRuntimeError(
                "trusted_prompt_unavailable"
            ) from None
        revision_digest = sha256_document(
            {
                "schema_version": "voice_coach.fixed_local_trusted_prompt.v1",
                "prompt_id": self._prompt_id,
                "normalized_text": self._normalized_text,
                "expected_phone_backend_id": sequence.backend_id,
                "expected_phone_revision": sequence.revision,
                "expected_phone_token_sha256": sequence.token_sha256,
            }
        )
        return TrustedPrompt(
            prompt_id=self._prompt_id,
            revision=f"fixed_local_g2pk_{revision_digest}",
            normalized_text=self._normalized_text,
            expected_phones=sequence.tokens,
        )


class FrozenSeungunRuntimeAdapter:
    """Materialize restricted PCM as a transient WAV and run one detector call."""

    def __init__(
        self,
        *,
        media_store: RestrictedMediaStorePort,
        detector: FrozenPronunciationDetectorPort,
        restricted_temp_root: str | os.PathLike[str],
        advice_generator: GroundedAdviceGeneratorPort | None = None,
    ) -> None:
        self._media_store = media_store
        self._detector = detector
        self._restricted_temp_root = Path(restricted_temp_root).absolute()
        self._advice_generator = advice_generator

    def analyze(
        self, *, audio: RestrictedPcmAudio, prompt: TrustedPrompt
    ) -> SeungunRuntimeResult:
        workspace, wav_path = self._create_restricted_wav_workspace()
        try:
            self._write_wav(audio=audio, wav_path=wav_path)
            try:
                completed = self._detector.analyze(
                    audio_path=wav_path,
                    expected_phones=prompt.expected_phones,
                )
            except Exception:
                raise PronunciationAttemptRuntimeError(
                    "seungun_detector_failed"
                ) from None
            analysis = _analysis_snapshot(completed)
            if tuple(analysis.get("expected_phones", ())) != prompt.expected_phones:
                raise PronunciationAttemptRuntimeError(
                    "seungun_expected_phone_mismatch"
                )
            analysis = _recover_expected_phone_spans(
                analysis,
                audio_duration_ms=round(audio.sample_count / 16_000 * 1_000),
            )
            return self._build_result(analysis=analysis, prompt=prompt)
        finally:
            _remove_restricted_wav_workspace(workspace=workspace, wav_path=wav_path)

    def _build_result(
        self, *, analysis: dict[str, object], prompt: TrustedPrompt
    ) -> SeungunRuntimeResult:
        try:
            feedback = generate_grounded_phone_feedback(
                analysis=analysis,
                target_text=prompt.normalized_text,
                generator=self._advice_generator,
            )
        except CoachingPayloadError:
            if not _is_completed_no_issue(analysis, prompt.expected_phones):
                raise PronunciationAttemptRuntimeError(
                    "seungun_analysis_invalid"
                ) from None
            return SeungunRuntimeResult(
                audio_result={
                    "status": "completed_no_issue",
                    "prompt_id": prompt.prompt_id,
                    "message": None,
                    "grounded_payload": None,
                },
                analysis_evidence=analysis,
                selected_phone_expected_index=None,
            )

        selected_index = _selected_index_from_grounded_payload(
            feedback.payload.issue_id
        )
        return SeungunRuntimeResult(
            audio_result={
                "status": "coaching_ready",
                "prompt_id": prompt.prompt_id,
                "message": feedback.message,
                "grounded_payload": feedback.payload.to_json_object(),
                "generator_status": feedback.generator_status,
                "generator_id": feedback.generator_id,
                "generator_revision": feedback.generator_revision,
                "fallback_reason": feedback.fallback_reason,
            },
            analysis_evidence=analysis,
            selected_phone_expected_index=selected_index,
        )

    def _create_restricted_wav_workspace(self) -> tuple[Path, Path]:
        root = self._restricted_temp_root
        try:
            os.mkdir(root, mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise PronunciationAttemptRuntimeError(
                "temporary_audio_workspace_unavailable"
            ) from None
        try:
            root_stat = os.lstat(root)
        except OSError:
            raise PronunciationAttemptRuntimeError(
                "temporary_audio_workspace_unavailable"
            ) from None
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or stat.S_ISLNK(root_stat.st_mode)
            or stat.S_IMODE(root_stat.st_mode) != 0o700
        ):
            raise PronunciationAttemptRuntimeError(
                "temporary_audio_workspace_unavailable"
            )
        workspace: Path | None = None
        try:
            workspace = Path(tempfile.mkdtemp(prefix="attempt-", dir=root))
            os.chmod(workspace, 0o700)
        except OSError:
            if workspace is not None:
                try:
                    os.rmdir(workspace)
                except OSError:
                    pass
            raise PronunciationAttemptRuntimeError(
                "temporary_audio_workspace_unavailable"
            ) from None
        assert workspace is not None
        return workspace, workspace / "audio.wav"

    def _write_wav(self, *, audio: RestrictedPcmAudio, wav_path: Path) -> None:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise PronunciationAttemptRuntimeError(
                "temporary_audio_workspace_unavailable"
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | nofollow
        descriptor = -1
        try:
            descriptor = os.open(wav_path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
        except OSError:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise PronunciationAttemptRuntimeError(
                "temporary_audio_write_failed"
            ) from None
        byte_count = 0
        pcm_sha256 = hashlib.sha256()
        try:
            with self._media_store.open_derived_audio(audio) as source:
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = -1
                    with wave.open(output, "wb") as wav_file:
                        wav_file.setnchannels(1)
                        wav_file.setsampwidth(2)
                        wav_file.setframerate(16_000)
                        while True:
                            payload = source.read(1024 * 1024)
                            if not payload:
                                break
                            if not isinstance(payload, bytes):
                                raise PronunciationAttemptRuntimeError(
                                    "restricted_audio_read_failed"
                                )
                            byte_count += len(payload)
                            pcm_sha256.update(payload)
                            wav_file.writeframesraw(payload)
                    output.flush()
                    os.fsync(output.fileno())
                    os.fchmod(output.fileno(), 0o400)
        except PronunciationAttemptRuntimeError:
            raise
        except Exception:
            raise PronunciationAttemptRuntimeError(
                "temporary_audio_write_failed"
            ) from None
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if (
            byte_count != audio.byte_count
            or byte_count % 2 != 0
            or pcm_sha256.hexdigest() != audio.derivation_receipt.pcm_sha256
        ):
            raise PronunciationAttemptRuntimeError(
                "restricted_audio_integrity_mismatch"
            )


class SeungunVisualHandoffAdapter:
    """Bind one completed runtime result into the existing visual sidecar."""

    def __init__(
        self,
        *,
        binding_facade: SeungunAttemptBindingFacade,
        consent_receipts: ConsentReceiptResolverPort,
        reliability_receipt: UpstreamReliabilityReceipt,
        clock: Callable[[], datetime],
        capture_contract_id: str,
        population_id_sha256: str,
        context_id: str,
    ) -> None:
        self._binding_facade = binding_facade
        self._consent_receipts = consent_receipts
        self._reliability_receipt = reliability_receipt
        self._clock = clock
        self._capture_contract_id = capture_contract_id
        self._population_id_sha256 = population_id_sha256
        self._context_id = context_id

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
        selected_index = seungun_result.selected_phone_expected_index
        if selected_index is None:
            raise PronunciationAttemptRuntimeError("selected_phone_unavailable")
        now = _require_utc_clock(self._clock)
        try:
            consent_receipt = self._consent_receipts.receipt_for(authorization)
        except PronunciationAttemptRuntimeError:
            raise
        except Exception:
            raise PronunciationAttemptRuntimeError(
                "consent_receipt_unavailable"
            ) from None
        _validate_consent_lineage(
            authorization=authorization,
            consent_receipt=consent_receipt,
            asset=asset,
            now=now,
        )
        _validate_media_lineage(asset=asset, probe=probe, audio=audio)
        audio_timebase = _audio_timebase(audio=audio, probe=probe)
        video_timebase = _video_timebase(asset=asset, probe=probe)
        video_handle = RestrictedVideoHandle(
            handle_id=asset.handle_id,
            attempt_id_sha256=asset.attempt_id_sha256,
            source_sha256=asset.container_sha256,
            timebase_sha256=video_timebase.timebase_sha256,
        )
        analysis = seungun_result.analysis_evidence
        if not isinstance(analysis, Mapping) and not callable(
            getattr(analysis, "to_json_object", None)
        ):
            raise PronunciationAttemptRuntimeError("seungun_analysis_invalid")
        try:
            visual_handoff = self._binding_facade.bind_completed_analysis(
                analysis=analysis,
                attempt_id_sha256=asset.attempt_id_sha256,
                prompt_id_sha256=sha256_document({"prompt_id": prompt.prompt_id}),
                prompt_revision=prompt.revision,
                expected_phones=prompt.expected_phones,
                audio_source_sha256=audio.derivation_receipt.pcm_sha256,
                audio_timebase=audio_timebase,
                video_handle=video_handle,
                video_timebase=video_timebase,
                reliability_receipt=self._reliability_receipt,
                consent_receipt=consent_receipt,
                issued_at_utc=_utc_text(now),
                upstream_phone_expected_index=selected_index,
                capture_contract_id=self._capture_contract_id,
                population_id_sha256=self._population_id_sha256,
                context_id=self._context_id,
                audio_derivation_receipt_sha256=(
                    audio.derivation_receipt.receipt_sha256
                ),
            )
        except PronunciationAttemptRuntimeError:
            raise
        except Exception:
            raise PronunciationAttemptRuntimeError("visual_handoff_failed") from None
        return HostVisualHandoff(
            selected_phone_expected_index=selected_index,
            binding_receipt=visual_handoff.request.binding_receipt,
            payload=visual_handoff,
            runtime_context=audio,
        )


class HttpSeungunVisualHandoffAdapter(SeungunVisualHandoffAdapter):
    """Server-admitted execution binding, without consent resolution."""

    def issue_http(
        self,
        *,
        asset: RestrictedVideoAsset,
        probe: MediaProbe,
        audio: RestrictedPcmAudio,
        prompt: TrustedPrompt,
        seungun_result: SeungunRuntimeResult,
    ) -> HostVisualHandoff:
        selected_index = seungun_result.selected_phone_expected_index
        if selected_index is None:
            raise PronunciationAttemptRuntimeError("selected_phone_unavailable")
        now = _require_utc_clock(self._clock)
        _validate_media_lineage(asset=asset, probe=probe, audio=audio)
        audio_timebase = _audio_timebase(audio=audio, probe=probe)
        video_timebase = _video_timebase(asset=asset, probe=probe)
        video_handle = RestrictedVideoHandle(
            handle_id=asset.handle_id,
            attempt_id_sha256=asset.attempt_id_sha256,
            source_sha256=asset.container_sha256,
            timebase_sha256=video_timebase.timebase_sha256,
        )
        analysis = seungun_result.analysis_evidence
        if not isinstance(analysis, Mapping) and not callable(
            getattr(analysis, "to_json_object", None)
        ):
            raise PronunciationAttemptRuntimeError("seungun_analysis_invalid")
        try:
            visual_handoff = self._binding_facade.bind_http_completed_analysis(
                analysis=analysis,
                attempt_id_sha256=asset.attempt_id_sha256,
                prompt_id_sha256=sha256_document({"prompt_id": prompt.prompt_id}),
                prompt_revision=prompt.revision,
                expected_phones=prompt.expected_phones,
                audio_source_sha256=audio.derivation_receipt.pcm_sha256,
                audio_timebase=audio_timebase,
                video_handle=video_handle,
                video_timebase=video_timebase,
                reliability_receipt=self._reliability_receipt,
                execution_binding_sha256=asset.attempt_id_sha256,
                issued_at_utc=_utc_text(now),
                upstream_phone_expected_index=selected_index,
                capture_contract_id=self._capture_contract_id,
                population_id_sha256=self._population_id_sha256,
                context_id=self._context_id,
                audio_derivation_receipt_sha256=(
                    audio.derivation_receipt.receipt_sha256
                ),
            )
        except PronunciationAttemptRuntimeError:
            raise
        except Exception:
            raise PronunciationAttemptRuntimeError("visual_handoff_failed") from None
        return HostVisualHandoff(
            selected_phone_expected_index=selected_index,
            binding_receipt=visual_handoff.request.binding_receipt,
            payload=visual_handoff,
            runtime_context=audio,
        )


class MediaPipeVisualDetectorBridge:
    """Wrap MediaPipe evidence without making a reliability statement."""

    def __init__(
        self,
        detector: LipLandmarkDetectorPort,
        *,
        qc_detector: VideoQcCandidatePort | None = None,
        sync_detector: AvSyncCandidatePort | None = None,
        qc_decision_adapter: VideoQcDecisionPort | None = None,
        sync_decision_adapter: AvSyncDecisionPort | None = None,
        expected_video_qc_decision_policy_sha256: str | None = None,
        expected_av_sync_decision_policy_sha256: str | None = None,
        phone_window_policy: PhoneWindowDecodePolicy | None = None,
    ) -> None:
        if (qc_decision_adapter is None) != (
            expected_video_qc_decision_policy_sha256 is None
        ) or (sync_decision_adapter is None) != (
            expected_av_sync_decision_policy_sha256 is None
        ):
            raise ValueError(
                "decision adapters require exact expected policy digests"
            )
        if qc_decision_adapter is not None and qc_detector is None:
            raise ValueError("video QC decision requires a candidate adapter")
        if sync_decision_adapter is not None and sync_detector is None:
            raise ValueError("A/V sync decision requires a candidate adapter")
        for value in (
            expected_video_qc_decision_policy_sha256,
            expected_av_sync_decision_policy_sha256,
        ):
            if value is not None and not _is_sha256(value):
                raise ValueError("expected decision policy must be SHA-256")
        self._detector = detector
        self._qc_detector = qc_detector
        self._sync_detector = sync_detector
        self._qc_decision_adapter = qc_decision_adapter
        self._sync_decision_adapter = sync_decision_adapter
        self._expected_video_qc_decision_policy_sha256 = (
            expected_video_qc_decision_policy_sha256
        )
        self._expected_av_sync_decision_policy_sha256 = (
            expected_av_sync_decision_policy_sha256
        )
        self._phone_window_policy = phone_window_policy

    def detect(
        self,
        *,
        sample: Rgb24Sample,
        probe: MediaProbe,
        handoff: HostVisualHandoff,
    ) -> VisualDetection:
        binding = handoff.binding_receipt
        if not isinstance(binding, (SameAttemptBindingReceipt, HttpSameAttemptBindingReceipt)):
            raise PronunciationAttemptRuntimeError(
                "visual_detection_binding_invalid"
            )
        expected_indices = _uniform_frame_indices(
            total_frame_count=probe.video_stream.frame_count,
            sample_frame_count=len(sample.frames),
        )
        expected_timestamps = tuple(
            probe.video_frame_timestamps_us[index] for index in expected_indices
        )
        actual_timestamps = tuple(frame.timestamp_us for frame in sample.frames)
        payload_binding = getattr(
            getattr(getattr(handoff.payload, "request", None), "binding_receipt", None),
            "receipt_sha256",
            None,
        )
        if (
            sample.asset_handle_id != probe.asset_handle_id
            or sample.container_sha256 != probe.container_sha256
            or sample.video_stream_index != probe.video_stream.stream_index
            or actual_timestamps != expected_timestamps
            or binding.video_source_sha256 != sample.container_sha256
            or binding.receipt_sha256 != payload_binding
        ):
            raise PronunciationAttemptRuntimeError(
                "visual_detection_lineage_mismatch"
            )
        try:
            evidence = self._detector.detect(sample)
        except Exception:
            raise PronunciationAttemptRuntimeError("visual_detector_failed") from None
        landmark_evidence_sha256 = getattr(evidence, "evidence_sha256", None)
        model_sha256 = getattr(evidence, "model_sha256", None)
        if (
            getattr(evidence, "asset_handle_id", None) != sample.asset_handle_id
            or getattr(evidence, "container_sha256", None)
            != sample.container_sha256
            or not _is_sha256(landmark_evidence_sha256)
            or not _is_sha256(model_sha256)
        ):
            raise PronunciationAttemptRuntimeError(
                "visual_detection_lineage_mismatch"
            )
        qc_evidence: VideoQcCandidateEvidence | None = None
        qc_candidate_receipt_sha256: str | None = None
        qc_decision: RuntimeVideoQcDecision | None = None
        qc_receipt_sha256: str | None = None
        if self._qc_detector is not None:
            try:
                qc_evidence = self._qc_detector.measure(
                    sample=sample,
                    landmarks=evidence,
                )
            except Exception:
                raise PronunciationAttemptRuntimeError(
                    "video_qc_backend_failed"
                ) from None
            if not isinstance(qc_evidence, VideoQcCandidateEvidence):
                raise PronunciationAttemptRuntimeError(
                    "video_qc_evidence_invalid"
                )
            qc_candidate_receipt_sha256 = qc_evidence.receipt_sha256
            if (
                qc_evidence.asset_handle_id != sample.asset_handle_id
                or qc_evidence.container_sha256 != sample.container_sha256
                or qc_evidence.landmark_evidence_sha256
                != landmark_evidence_sha256
                or qc_evidence.landmark_model_sha256 != model_sha256
                or qc_evidence.sample_timestamps_us != actual_timestamps
            ):
                raise PronunciationAttemptRuntimeError(
                    "video_qc_evidence_invalid"
                )
            if self._qc_decision_adapter is not None:
                try:
                    qc_decision = self._qc_decision_adapter.decide(
                        candidate=qc_evidence,
                        binding_receipt=binding,
                    )
                except Exception:
                    raise PronunciationAttemptRuntimeError(
                        "video_qc_decision_failed"
                    ) from None
                if (
                    not isinstance(qc_decision, RuntimeVideoQcDecision)
                    or qc_decision.source_candidate_receipt_sha256
                    != qc_evidence.receipt_sha256
                    or qc_decision.binding_receipt_sha256
                    != binding.receipt_sha256
                    or qc_decision.landmark_evidence_sha256
                    != landmark_evidence_sha256
                    or qc_decision.landmark_model_sha256 != model_sha256
                    or qc_decision.observed_metrics != qc_evidence.metrics
                    or qc_decision.decision_policy_sha256
                    != self._expected_video_qc_decision_policy_sha256
                ):
                    raise PronunciationAttemptRuntimeError(
                        "video_qc_decision_invalid"
                    )
                qc_receipt_sha256 = qc_decision.receipt_sha256
        sync_candidate: AvSyncCandidateEvidence | None = None
        sync_candidate_receipt_sha256: str | None = None
        sync_decision: RuntimeAvSyncDecision | None = None
        sync_receipt_sha256: str | None = None
        sync_evidence: PolicyValidatedAvSyncOffset | None = None
        sync_offset_receipt_sha256: str | None = None
        if self._sync_detector is not None:
            if not isinstance(handoff.runtime_context, RestrictedPcmAudio):
                raise PronunciationAttemptRuntimeError(
                    "av_sync_audio_context_unavailable"
                )
            try:
                sync_candidate = self._sync_detector.measure(
                    audio=handoff.runtime_context,
                    landmarks=evidence,
                    probe=probe,
                )
            except Exception:
                raise PronunciationAttemptRuntimeError(
                    "av_sync_backend_failed"
                ) from None
            if not isinstance(sync_candidate, AvSyncCandidateEvidence):
                raise PronunciationAttemptRuntimeError(
                    "av_sync_evidence_invalid"
                )
            sync_candidate_receipt_sha256 = sync_candidate.receipt_sha256
            audio = handoff.runtime_context
            if (
                sync_candidate.asset_handle_id != sample.asset_handle_id
                or sync_candidate.audio_derivation_receipt_sha256
                != audio.derivation_receipt.receipt_sha256
                or sync_candidate.audio_source_sha256
                != binding.audio_source_sha256
                or sync_candidate.video_source_sha256
                != binding.video_source_sha256
                or sync_candidate.landmark_evidence_sha256
                != landmark_evidence_sha256
                or sync_candidate.landmark_model_sha256 != model_sha256
            ):
                raise PronunciationAttemptRuntimeError(
                    "av_sync_evidence_invalid"
                )
            if self._sync_decision_adapter is not None:
                try:
                    sync_decision = self._sync_decision_adapter.decide(
                        candidate=sync_candidate,
                        binding_receipt=binding,
                    )
                except Exception:
                    raise PronunciationAttemptRuntimeError(
                        "av_sync_decision_failed"
                    ) from None
                if (
                    not isinstance(sync_decision, RuntimeAvSyncDecision)
                    or sync_decision.source_candidate_receipt_sha256
                    != sync_candidate.receipt_sha256
                    or sync_decision.binding_receipt_sha256
                    != binding.receipt_sha256
                    or sync_decision.landmark_evidence_sha256
                    != landmark_evidence_sha256
                    or sync_decision.landmark_model_sha256 != model_sha256
                    or sync_decision.observed_offset_ms
                    != sync_candidate.candidate_offset_ms
                    or sync_decision.observed_correlation
                    != sync_candidate.correlation
                    or sync_decision.observed_anchors != sync_candidate.anchors
                    or sync_decision.decision_policy_sha256
                    != self._expected_av_sync_decision_policy_sha256
                ):
                    raise PronunciationAttemptRuntimeError(
                        "av_sync_decision_invalid"
                    )
                sync_receipt_sha256 = sync_decision.receipt_sha256
                if sync_decision.status == "synchronized":
                    try:
                        sync_evidence = (
                            PolicyValidatedAvSyncOffset.issue_from_decision(
                                decision=sync_decision
                            )
                        )
                    except ValueError:
                        raise PronunciationAttemptRuntimeError(
                            "av_sync_offset_invalid"
                        ) from None
                    sync_offset_receipt_sha256 = sync_evidence.receipt_sha256
        sample_timestamps_sha256 = sha256_document(
            {
                "video_stream_index": sample.video_stream_index,
                "timestamps_us": list(actual_timestamps),
                "ffmpeg_revision": sample.ffmpeg_revision,
            }
        )
        unsigned = {
            "schema_version": "voice_coach.runtime_visual_detection.v3",
            "binding_receipt_sha256": binding.receipt_sha256,
            "container_sha256": sample.container_sha256,
            "sample_timestamps_sha256": sample_timestamps_sha256,
            "landmark_evidence_sha256": landmark_evidence_sha256,
            "model_sha256": model_sha256,
            "video_qc_candidate_receipt_sha256": (
                qc_candidate_receipt_sha256
            ),
            "video_qc_receipt_sha256": qc_receipt_sha256,
            "av_sync_candidate_receipt_sha256": (
                sync_candidate_receipt_sha256
            ),
            "av_sync_receipt_sha256": sync_receipt_sha256,
            "av_sync_offset_receipt_sha256": sync_offset_receipt_sha256,
        }
        wrapped = RuntimeVisualDetectionEvidence(
            binding_receipt_sha256=binding.receipt_sha256,
            container_sha256=sample.container_sha256,
            sample_timestamps_sha256=sample_timestamps_sha256,
            landmark_evidence_sha256=landmark_evidence_sha256,
            model_sha256=model_sha256,
            video_qc_candidate_receipt_sha256=(
                qc_candidate_receipt_sha256
            ),
            video_qc_receipt_sha256=qc_receipt_sha256,
            av_sync_candidate_receipt_sha256=(
                sync_candidate_receipt_sha256
            ),
            av_sync_receipt_sha256=sync_receipt_sha256,
            av_sync_offset_receipt_sha256=sync_offset_receipt_sha256,
            detection_receipt_sha256=sha256_document(unsigned),
            landmark_evidence=evidence,
            video_qc_evidence=qc_evidence,
            video_qc_decision=qc_decision,
            av_sync_candidate_evidence=sync_candidate,
            av_sync_decision=sync_decision,
            av_sync_evidence=sync_evidence,
        )
        return VisualDetection(evidence=wrapped)

    def plan_phone_window(
        self,
        *,
        detection: VisualDetection,
        probe: MediaProbe,
        handoff: HostVisualHandoff,
    ) -> PhoneWindowDecodeRequest | None:
        """Map only the exact bound Seungun anchor through an approved offset."""

        policy = self._phone_window_policy
        evidence = detection.evidence
        binding = handoff.binding_receipt
        if policy is None:
            return None
        if not isinstance(evidence, RuntimeVisualDetectionEvidence) or not isinstance(
            binding, (SameAttemptBindingReceipt, HttpSameAttemptBindingReceipt)
        ):
            raise PronunciationAttemptRuntimeError(
                "phone_window_detection_lineage_invalid"
            )
        if evidence.phone_window_evidence is not None:
            raise PronunciationAttemptRuntimeError(
                "phone_window_already_observed"
            )
        sync_offset = evidence.av_sync_evidence
        if not isinstance(sync_offset, PolicyValidatedAvSyncOffset):
            # Raw sync candidates are intentionally insufficient to map a phone.
            return None
        qc_decision = evidence.video_qc_decision
        sync_decision = evidence.av_sync_decision
        if (
            not isinstance(qc_decision, RuntimeVideoQcDecision)
            or qc_decision.status != "passed"
            or not isinstance(sync_decision, RuntimeAvSyncDecision)
            or sync_decision.status != "synchronized"
        ):
            return None
        anchor = self._selected_phone_anchor(handoff)
        if anchor.start_ms is None or anchor.end_ms is None:
            return None
        if (
            sync_offset.attempt_id_sha256 != binding.attempt_id_sha256
            or sync_offset.binding_receipt_sha256 != binding.receipt_sha256
            or sync_offset.audio_source_sha256 != binding.audio_source_sha256
            or sync_offset.video_source_sha256 != binding.video_source_sha256
            or sync_offset.landmark_evidence_sha256
            != evidence.landmark_evidence_sha256
            or sync_offset.policy_sha256
            != policy.expected_av_sync_policy_sha256
            or sync_offset.av_sync_receipt_sha256
            != evidence.av_sync_receipt_sha256
            or sync_offset.receipt_sha256
            != evidence.av_sync_offset_receipt_sha256
            or sync_offset.source_candidate_receipt_sha256
            != evidence.av_sync_candidate_receipt_sha256
            or sync_offset.decision_policy_sha256
            != self._expected_av_sync_decision_policy_sha256
            or sync_offset.av_sync_decision_receipt_sha256
            != sync_decision.receipt_sha256
            or qc_decision.receipt_sha256
            != evidence.video_qc_receipt_sha256
            or abs(sync_offset.offset_ms) > policy.maximum_absolute_offset_ms
            or probe.container_sha256 != binding.video_source_sha256
        ):
            raise PronunciationAttemptRuntimeError(
                "phone_window_sync_lineage_invalid"
            )

        # Seungun spans are relative to decoded PCM.  The validated offset maps
        # absolute audio PTS to absolute video PTS, so both bound origins are
        # accounted for before returning a video-local window.
        core_start_ms = (
            binding.audio_timebase.origin_offset_ms
            + anchor.start_ms
            + sync_offset.offset_ms
            - binding.video_timebase.origin_offset_ms
        )
        core_end_ms = (
            binding.audio_timebase.origin_offset_ms
            + anchor.end_ms
            + sync_offset.offset_ms
            - binding.video_timebase.origin_offset_ms
        )
        video_duration_ms = binding.video_timebase.duration_ms
        if (
            core_start_ms < 0
            or core_end_ms > video_duration_ms
            or core_end_ms <= core_start_ms
        ):
            return None
        video_start_ms = max(0, core_start_ms - policy.padding_before_ms)
        video_end_ms = min(
            video_duration_ms,
            core_end_ms + policy.padding_after_ms,
        )
        if video_end_ms - video_start_ms > policy.maximum_window_duration_ms:
            return None
        try:
            return PhoneWindowDecodeRequest.issue(
                attempt_id_sha256=binding.attempt_id_sha256,
                binding_receipt_sha256=binding.receipt_sha256,
                container_sha256=binding.video_source_sha256,
                detection_receipt_sha256=evidence.detection_receipt_sha256,
                phone_anchor_sha256=anchor.anchor_ref,
                av_sync_receipt_sha256=sync_offset.av_sync_receipt_sha256,
                av_sync_offset_receipt_sha256=sync_offset.receipt_sha256,
                policy=policy,
                video_start_ms=video_start_ms,
                video_end_ms=video_end_ms,
            )
        except ValueError:
            raise PronunciationAttemptRuntimeError(
                "phone_window_request_invalid"
            ) from None

    def detect_phone_window(
        self,
        *,
        window: Rgb24PhoneWindow,
        detection: VisualDetection,
        probe: MediaProbe,
        handoff: HostVisualHandoff,
    ) -> VisualDetection:
        """Track a dense selected-phone sample without changing global QC/sync."""

        evidence = detection.evidence
        binding = handoff.binding_receipt
        policy = self._phone_window_policy
        if not isinstance(evidence, RuntimeVisualDetectionEvidence) or not isinstance(
            binding, (SameAttemptBindingReceipt, HttpSameAttemptBindingReceipt)
        ):
            raise PronunciationAttemptRuntimeError(
                "phone_window_detection_lineage_invalid"
            )
        request = window.request
        anchor = self._selected_phone_anchor(handoff)
        if (
            policy is None
            or evidence.phone_window_evidence is not None
            or request.binding_receipt_sha256 != binding.receipt_sha256
            or request.detection_receipt_sha256
            != evidence.detection_receipt_sha256
            or request.phone_anchor_sha256 != anchor.anchor_ref
            or request.container_sha256 != probe.container_sha256
            or not isinstance(
                evidence.video_qc_decision, RuntimeVideoQcDecision
            )
            or evidence.video_qc_decision.status != "passed"
            or evidence.av_sync_receipt_sha256
            != request.av_sync_receipt_sha256
            or window.sample.asset_handle_id != probe.asset_handle_id
            or window.sample.container_sha256 != probe.container_sha256
            or window.sample.video_stream_index
            != probe.video_stream.stream_index
        ):
            raise PronunciationAttemptRuntimeError(
                "phone_window_detection_lineage_invalid"
            )
        try:
            landmark_evidence = self._detector.detect(window.sample)
        except Exception:
            raise PronunciationAttemptRuntimeError(
                "phone_window_landmark_detector_failed"
            ) from None
        landmark_evidence_sha256 = getattr(
            landmark_evidence, "evidence_sha256", None
        )
        model_sha256 = getattr(landmark_evidence, "model_sha256", None)
        landmark_timestamps_ms = tuple(
            getattr(frame, "timestamp_ms", None)
            for frame in getattr(landmark_evidence, "frames", ())
        )
        expected_timestamps_ms = tuple(
            frame.timestamp_us // 1000 for frame in window.sample.frames
        )
        if (
            getattr(landmark_evidence, "asset_handle_id", None)
            != window.sample.asset_handle_id
            or getattr(landmark_evidence, "container_sha256", None)
            != window.sample.container_sha256
            or landmark_timestamps_ms != expected_timestamps_ms
            or not _is_sha256(landmark_evidence_sha256)
            or model_sha256 != evidence.model_sha256
        ):
            raise PronunciationAttemptRuntimeError(
                "phone_window_landmark_lineage_invalid"
            )
        try:
            tracking_receipt = PhoneWindowTrackingReceipt.issue(
                window=window,
                landmark_evidence_sha256=landmark_evidence_sha256,
                model_sha256=model_sha256,
                tracking_code_sha256=_adapter_code_sha256(),
            )
            runtime_receipt = RuntimePhoneWindowReceipt.issue(
                request=request,
                video_qc_receipt_sha256=evidence.video_qc_receipt_sha256,
                tracking_receipt=tracking_receipt,
                policy=policy,
            )
            phone_window_evidence = DensePhoneWindowEvidence(
                decode_receipt=window.decode_receipt,
                tracking_receipt=tracking_receipt,
                runtime_receipt=runtime_receipt,
                landmark_evidence=landmark_evidence,
            )
            return VisualDetection(
                evidence=replace(
                    evidence,
                    phone_window_evidence=phone_window_evidence,
                )
            )
        except ValueError:
            raise PronunciationAttemptRuntimeError(
                "phone_window_receipt_invalid"
            ) from None

    @staticmethod
    def _selected_phone_anchor(
        handoff: HostVisualHandoff,
    ) -> UpstreamPhoneAnchor:
        payload = handoff.payload
        if not isinstance(payload, SeungunVisualHandoff):
            raise PronunciationAttemptRuntimeError(
                "phone_window_handoff_invalid"
            )
        selected_index = handoff.selected_phone_expected_index
        if (
            payload.request.upstream_phone_expected_index != selected_index
            or payload.request.binding_receipt.receipt_sha256
            != handoff.binding_receipt.receipt_sha256
        ):
            raise PronunciationAttemptRuntimeError(
                "phone_window_handoff_invalid"
            )
        try:
            anchors = parse_seungun_analysis(
                payload.source_record.analysis,
                analysis_ref=payload.source_record.reference,
            )
        except Exception:
            raise PronunciationAttemptRuntimeError(
                "phone_window_anchor_invalid"
            ) from None
        if selected_index >= len(anchors):
            raise PronunciationAttemptRuntimeError(
                "phone_window_anchor_invalid"
            )
        anchor = anchors[selected_index]
        if anchor.expected_index != selected_index:
            raise PronunciationAttemptRuntimeError(
                "phone_window_anchor_invalid"
            )
        return anchor


class BlockedVisualReliabilityAdapter:
    """Fail closed until user-runtime P6/P8 visual evidence is available."""

    def evaluate(
        self, *, detection: VisualDetection, handoff: HostVisualHandoff
    ) -> VisualReliabilityDecision:
        evidence = detection.evidence
        binding = handoff.binding_receipt
        if (
            not isinstance(evidence, RuntimeVisualDetectionEvidence)
            or not isinstance(binding, (SameAttemptBindingReceipt, HttpSameAttemptBindingReceipt))
            or evidence.binding_receipt_sha256 != binding.receipt_sha256
            or evidence.container_sha256 != binding.video_source_sha256
        ):
            raise PronunciationAttemptRuntimeError(
                "visual_reliability_lineage_mismatch"
            )
        sync_candidate = evidence.av_sync_candidate_evidence
        sync_decision = evidence.av_sync_decision
        if sync_candidate is None:
            sync_reasons = ("content_av_sync_backend_not_configured",)
        elif sync_decision is None and sync_candidate.status == "candidate_unvalidated":
            sync_reasons = ("content_av_sync_candidate_not_calibrated",)
        elif sync_candidate.status == "unavailable":
            sync_reasons = sync_candidate.reason_codes or (
                "content_av_sync_not_observed",
            )
        elif sync_decision is not None and sync_decision.status == "failed":
            sync_reasons = ("content_av_sync_decision_failed",)
        else:
            sync_reasons = ("content_av_sync_not_observed",)
        qc_evidence = evidence.video_qc_evidence
        qc_decision = evidence.video_qc_decision
        if qc_evidence is None:
            qc_reasons = ("video_qc_backend_not_configured",)
        elif qc_decision is None and qc_evidence.status == "candidate_unvalidated":
            qc_reasons = ("video_qc_candidate_not_calibrated",)
        elif qc_evidence.status == "unavailable":
            qc_reasons = qc_evidence.reason_codes or ("video_qc_not_observed",)
        elif qc_decision is not None and qc_decision.status == "failed":
            qc_reasons = ("video_qc_decision_failed",)
        else:
            qc_reasons = ("video_qc_not_observed",)
        reasons = [
            *qc_reasons,
            *sync_reasons,
            "p6_user_runtime_calibration_not_available",
            "p8_user_runtime_claim_catalog_not_available",
        ]
        payload = handoff.payload
        if isinstance(payload, SeungunVisualHandoff) and (
            payload.source_record.reliability_receipt.status
            is not UpstreamReliabilityStatus.SUPPORTED
        ):
            reasons.insert(0, "upstream_reliability_not_supported")
        return VisualReliabilityDecision(
            supported=False,
            receipt=None,
            reason_codes=tuple(reasons),
        )


def _require_utc_clock(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        raise PronunciationAttemptRuntimeError("clock_unavailable") from None
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset().total_seconds() != 0
    ):
        raise PronunciationAttemptRuntimeError("clock_not_utc")
    return value


def _utc_text(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _analysis_snapshot(
    value: Mapping[str, object] | CompletedPronunciationAnalysis,
) -> dict[str, object]:
    try:
        raw: object = value if isinstance(value, Mapping) else value.to_json_object()
        if not isinstance(raw, Mapping):
            raise TypeError
        snapshot = json.loads(canonical_json_bytes(dict(raw)))
    except Exception:
        raise PronunciationAttemptRuntimeError("seungun_analysis_invalid") from None
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SEUNGUN_SCHEMA_VERSION:
        raise PronunciationAttemptRuntimeError("seungun_analysis_invalid")
    return snapshot


def _recover_expected_phone_spans(
    analysis: dict[str, object], *, audio_duration_ms: int
) -> dict[str, object]:
    """Add bounded host-estimated timing only where CTC reported a deletion.

    The frozen detector deliberately has no observed span for a deletion.  A
    visual retry still needs a bounded window, so the host interpolates the
    expected phone slot from neighbouring observed spans.  The deletion
    operation and absent observed candidate remain unchanged, and a notice
    prevents the estimate from being confused with observed CTC evidence.
    """

    phones = analysis.get("phones")
    if not isinstance(phones, list) or audio_duration_ms <= 0:
        return analysis
    timed_durations = [
        phone["end_ms"] - phone["start_ms"]
        for phone in phones
        if isinstance(phone, dict)
        and isinstance(phone.get("start_ms"), int)
        and isinstance(phone.get("end_ms"), int)
        and phone["end_ms"] > phone["start_ms"]
    ]
    estimate_ms = 100
    if timed_durations:
        ordered = sorted(timed_durations)
        estimate_ms = ordered[len(ordered) // 2]
    estimate_ms = max(40, min(240, estimate_ms))
    recovered = False
    for index, phone in enumerate(phones):
        if (
            not isinstance(phone, dict)
            or phone.get("alignment_operation") != "deletion"
            or phone.get("start_ms") is not None
            or phone.get("end_ms") is not None
        ):
            continue
        previous_end = next(
            (
                candidate.get("end_ms")
                for candidate in reversed(phones[:index])
                if isinstance(candidate, dict)
                and isinstance(candidate.get("end_ms"), int)
            ),
            None,
        )
        next_start = next(
            (
                candidate.get("start_ms")
                for candidate in phones[index + 1 :]
                if isinstance(candidate, dict)
                and isinstance(candidate.get("start_ms"), int)
            ),
            None,
        )
        if previous_end is not None and next_start is not None and next_start > previous_end:
            start_ms, end_ms = previous_end, next_start
        elif previous_end is not None:
            start_ms = min(previous_end, max(0, audio_duration_ms - 1))
            end_ms = min(audio_duration_ms, start_ms + estimate_ms)
            if end_ms <= start_ms:
                start_ms = max(0, end_ms - estimate_ms)
        elif next_start is not None:
            end_ms = max(1, min(next_start, audio_duration_ms))
            start_ms = max(0, end_ms - estimate_ms)
        else:
            slot_count = max(1, len(phones))
            start_ms = min(audio_duration_ms - 1, audio_duration_ms * index // slot_count)
            end_ms = max(start_ms + 1, audio_duration_ms * (index + 1) // slot_count)
        if end_ms > start_ms:
            phone["start_ms"] = start_ms
            phone["end_ms"] = end_ms
            recovered = True
    if recovered:
        notices = analysis.get("notices")
        if not isinstance(notices, list):
            notices = []
            analysis["notices"] = notices
        notice = "deletion spans use host estimated expected-sequence timing, not observed CTC timing"
        if notice not in notices:
            notices.append(notice)
    return analysis


def _is_completed_no_issue(
    analysis: Mapping[str, object], expected_phones: tuple[str, ...]
) -> bool:
    phones = analysis.get("phones")
    if not isinstance(phones, list) or len(phones) != len(expected_phones):
        return False
    for expected_index, (expected_phone, phone) in enumerate(
        zip(expected_phones, phones, strict=True)
    ):
        if not isinstance(phone, dict):
            return False
        if (
            phone.get("expected_index") != expected_index
            or phone.get("expected_phone") != expected_phone
            or phone.get("is_error") is not False
        ):
            return False
    return True


def _selected_index_from_grounded_payload(issue_id: str) -> int:
    prefix = "phone-"
    if not issue_id.startswith(prefix):
        raise PronunciationAttemptRuntimeError("grounded_feedback_index_invalid")
    try:
        selected_index = int(issue_id[len(prefix) :])
    except ValueError:
        raise PronunciationAttemptRuntimeError(
            "grounded_feedback_index_invalid"
        ) from None
    if selected_index < 0:
        raise PronunciationAttemptRuntimeError("grounded_feedback_index_invalid")
    return selected_index


def _remove_restricted_wav_workspace(*, workspace: Path, wav_path: Path) -> None:
    failed = False
    try:
        os.unlink(wav_path)
    except FileNotFoundError:
        pass
    except OSError:
        failed = True
    try:
        os.rmdir(workspace)
    except FileNotFoundError:
        pass
    except OSError:
        failed = True
    if failed:
        raise PronunciationAttemptRuntimeError("temporary_audio_cleanup_failed")


def _validate_consent_lineage(
    *,
    authorization: VideoConsentAuthorization,
    consent_receipt: ConsentReceipt,
    asset: RestrictedVideoAsset,
    now: datetime,
) -> None:
    decision_matches = (
        authorization.decision is VideoConsentDecision.GRANTED
        and consent_receipt.decision is ConsentDecision.GRANTED
    )
    fields_match = (
        authorization.attempt_id_sha256 == consent_receipt.attempt_id_sha256
        == asset.attempt_id_sha256
        and authorization.consent_receipt_sha256
        == consent_receipt.receipt_sha256
        == asset.consent_receipt_sha256
        and authorization.purpose == consent_receipt.purpose
        and authorization.data_category == consent_receipt.data_category
        and authorization.retention_policy_id
        == consent_receipt.retention_policy_id
        and authorization.delete_on_completion
        == consent_receipt.delete_on_completion
        and authorization.remote_egress_allowed
        == consent_receipt.remote_egress_allowed
        and _utc_text(authorization.issued_at_utc) == consent_receipt.issued_at_utc
        and _utc_text(authorization.expires_at_utc) == consent_receipt.expires_at_utc
    )
    if (
        not decision_matches
        or not fields_match
        or authorization.remote_egress_allowed
        or not authorization.delete_on_completion
        or now < authorization.issued_at_utc
        or now >= authorization.expires_at_utc
    ):
        raise PronunciationAttemptRuntimeError("consent_lineage_mismatch")


def _validate_media_lineage(
    *, asset: RestrictedVideoAsset, probe: MediaProbe, audio: RestrictedPcmAudio
) -> None:
    receipt = audio.derivation_receipt
    if (
        probe.asset_handle_id != asset.handle_id
        or probe.container_sha256 != asset.container_sha256
        or audio.asset_handle_id != asset.handle_id
        or audio.attempt_id_sha256 != asset.attempt_id_sha256
        or receipt.container_sha256 != asset.container_sha256
        or receipt.audio_stream_index != probe.audio_stream.stream_index
        or receipt.source_time_base != probe.audio_stream.time_base
    ):
        raise PronunciationAttemptRuntimeError("media_lineage_mismatch")


def _audio_timebase(
    *, audio: RestrictedPcmAudio, probe: MediaProbe
) -> MediaTimebaseReceipt:
    return MediaTimebaseReceipt(
        clock_id_sha256=sha256_document(
            {
                "pcm_sha256": audio.derivation_receipt.pcm_sha256,
                "sample_count": audio.sample_count,
                "pcm_time_base": audio.derivation_receipt.pcm_time_base,
            }
        ),
        duration_ms=max(1, (audio.sample_count * 1000 + 15_999) // 16_000),
        origin_offset_ms=_milliseconds(probe.audio_stream.start_time_us),
        transform_revision="canonical_mono_16khz_s16le_v1",
    )


def _video_timebase(
    *, asset: RestrictedVideoAsset, probe: MediaProbe
) -> MediaTimebaseReceipt:
    return MediaTimebaseReceipt(
        clock_id_sha256=sha256_document(
            {
                "container_sha256": asset.container_sha256,
                "video_stream_index": probe.video_stream.stream_index,
                "source_time_base": probe.video_stream.time_base,
                "frame_timestamps_us": list(probe.video_frame_timestamps_us),
            }
        ),
        duration_ms=probe.video_stream.duration_ms,
        origin_offset_ms=_milliseconds(probe.video_stream.start_time_us),
        transform_revision="restricted_video_probe_v1",
    )


def _milliseconds(microseconds: int) -> int:
    magnitude = (abs(microseconds) + 500) // 1000
    return magnitude if microseconds >= 0 else -magnitude


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _uniform_frame_indices(
    *, total_frame_count: int, sample_frame_count: int
) -> tuple[int, ...]:
    if sample_frame_count <= 0 or sample_frame_count > total_frame_count:
        raise PronunciationAttemptRuntimeError("visual_sample_geometry_invalid")
    if sample_frame_count == 1:
        return (0,)
    denominator = sample_frame_count - 1
    maximum_index = total_frame_count - 1
    return tuple(
        (position * maximum_index + denominator // 2) // denominator
        for position in range(sample_frame_count)
    )


def _adapter_code_sha256() -> str:
    """Return the loaded runtime adapter source pin without caller input."""

    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        raise ValueError("runtime adapter source code is unavailable") from None


__all__ = [
    "AttemptNoncePort",
    "AvSyncCandidatePort",
    "AvSyncDecisionPort",
    "BlockedVisualReliabilityAdapter",
    "CompletedPronunciationAnalysis",
    "ConsentReceiptResolverPort",
    "FixedLocalTrustedPromptAdapter",
    "FrozenPronunciationDetectorPort",
    "FrozenSeungunRuntimeAdapter",
    "IsolatedFrozenSeungunDetectorAdapter",
    "LipLandmarkDetectorPort",
    "LocalExplicitConsentAuthority",
    "MediaPipeVisualDetectorBridge",
    "PronunciationAttemptRuntimeError",
    "R00TrustedPromptAdapter",
    "RuntimeVisualDetectionEvidence",
    "SeungunVisualHandoffAdapter",
    "VideoQcCandidatePort",
    "VideoQcDecisionPort",
]
