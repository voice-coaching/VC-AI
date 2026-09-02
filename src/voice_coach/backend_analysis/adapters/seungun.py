"""Production bridge from restricted encoded audio to Seungun-owned feedback."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Iterator, Mapping, Protocol, Sequence
import wave

from voice_coach.pronunciation_attempts.adapters.bounded_isolated_process import (
    IsolatedProcessError,
    run_bounded_isolated_process,
)
from voice_coach.pronunciation_feedback.coaching_payload import CoachingPayloadError
from voice_coach.pronunciation_feedback.domain import ExpectedPhoneSequence
from voice_coach.pronunciation_feedback.seungun_bridge import (
    SEUNGUN_SCHEMA_VERSION,
    generate_grounded_phone_feedback,
)

from ..domain import AnalysisOutcome, AnalysisRequest, PronunciationEvidence
from ..ports import BackendAnalysisError, MaterializedAudio, SeungunAnalysis


class ExpectedPhoneFrontend(Protocol):
    def expected_phones(self, normalized_text: str) -> ExpectedPhoneSequence: ...


class FrozenDetector(Protocol):
    def analyze(
        self, *, audio_path: str | Path, expected_phones: Sequence[str]
    ) -> Mapping[str, object] | object: ...


@dataclass(frozen=True, slots=True)
class DecodedWav:
    path: Path
    duration_ms: int


class AudioDecoder(Protocol):
    def decode(self, source: Path) -> AbstractContextManager[DecodedWav]: ...


class RestrictedFfmpegAudioDecoder:
    """Decode with network syscalls denied and bounded stdout/time/memory."""

    def __init__(
        self,
        *,
        ffmpeg_binary: str | Path | None = None,
        timeout_seconds: float = 60.0,
        maximum_wav_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        executable = Path(ffmpeg_binary or shutil.which("ffmpeg") or "")
        if (
            not executable.is_absolute()
            or not executable.is_file()
            or timeout_seconds <= 0
            or maximum_wav_bytes <= 0
        ):
            raise ValueError("audio_decoder_configuration_invalid")
        self._ffmpeg = executable
        self._timeout_seconds = timeout_seconds
        self._maximum_wav_bytes = maximum_wav_bytes

    @contextmanager
    def decode(self, source: Path) -> Iterator[DecodedWav]:
        destination = source.parent / "decoded.wav"
        destination_created = False
        try:
            completed = run_bounded_isolated_process(
                launcher_python=sys.executable,
                target=[
                    str(self._ffmpeg),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-map_metadata",
                    "-1",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "s16le",
                    "pipe:1",
                ],
                input_bytes=b"",
                cwd=str(source.parent),
                environment={
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                },
                timeout_seconds=self._timeout_seconds,
                maximum_output_bytes=self._maximum_wav_bytes,
                address_space_bytes=2 * 1024 * 1024 * 1024,
            )
            if (
                completed.return_code != 0
                or not completed.stdout
                or len(completed.stdout) % 2 != 0
            ):
                raise BackendAnalysisError(
                    "audio_decode_unsupported", "지원되지 않는 녹음 형식입니다."
                )
            self._write_restricted_wav(destination, completed.stdout)
            destination_created = True
            duration_ms = self._validate_wav(destination)
            yield DecodedWav(path=destination, duration_ms=duration_ms)
        except BackendAnalysisError:
            raise
        except (IsolatedProcessError, OSError, ValueError, wave.Error):
            raise BackendAnalysisError(
                "audio_decode_unsupported", "지원되지 않는 녹음 형식입니다."
            ) from None
        finally:
            if destination_created:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _write_restricted_wav(destination: Path, pcm_s16le: bytes) -> None:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                with wave.open(output, "wb") as wav_output:
                    wav_output.setnchannels(1)
                    wav_output.setsampwidth(2)
                    wav_output.setframerate(16_000)
                    wav_output.writeframes(pcm_s16le)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(destination, 0o400)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _validate_wav(path: Path) -> int:
        with wave.open(str(path), "rb") as stream:
            if (
                stream.getnchannels() != 1
                or stream.getframerate() != 16_000
                or stream.getsampwidth() != 2
                or stream.getnframes() <= 0
            ):
                raise BackendAnalysisError(
                    "audio_decode_unsupported", "지원되지 않는 녹음 형식입니다."
                )
            return round(stream.getnframes() / 16_000 * 1_000)


class SeungunBackendAnalysisAdapter:
    """Invoke the frozen detector once and preserve its selected-phone decision."""

    def __init__(
        self,
        *,
        decoder: AudioDecoder,
        expected_phone_frontend: ExpectedPhoneFrontend,
        detector: FrozenDetector,
    ) -> None:
        self._decoder = decoder
        self._expected_phone_frontend = expected_phone_frontend
        self._detector = detector

    def analyze(
        self, *, request: AnalysisRequest, audio: MaterializedAudio
    ) -> SeungunAnalysis:
        try:
            expected = self._expected_phone_frontend.expected_phones(request.script_text)
        except Exception:
            raise BackendAnalysisError(
                "seungun_backend_unavailable",
                "현재 분석 엔진을 사용할 수 없습니다. 다시 시도해 주세요.",
            ) from None
        try:
            with self._decoder.decode(audio.path) as decoded:
                raw = self._detector.analyze(
                    audio_path=decoded.path,
                    expected_phones=expected.tokens,
                )
                analysis = _analysis_snapshot(raw)
        except BackendAnalysisError:
            raise
        except Exception:
            raise BackendAnalysisError(
                "seungun_detector_failed",
                "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
            ) from None
        if tuple(analysis.get("expected_phones", ())) != expected.tokens:
            raise BackendAnalysisError(
                "seungun_detector_failed",
                "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
            )
        pipeline_revision = _pipeline_revision(expected, analysis)
        try:
            feedback = generate_grounded_phone_feedback(
                analysis=analysis,
                target_text=request.script_text,
                generator=None,
            )
        except CoachingPayloadError:
            if not _is_completed_no_issue(analysis, expected.tokens):
                raise BackendAnalysisError(
                    "seungun_detector_failed",
                    "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
                ) from None
            return SeungunAnalysis(
                outcome=AnalysisOutcome.COMPLETED_NO_ISSUE,
                summary_feedback=None,
                pronunciation_evidence=None,
                pipeline_revision=pipeline_revision,
            )
        location = feedback.payload.location
        if (
            location is None
            or location.unit_type != "phone"
            or feedback.payload.detector_score is None
            or feedback.payload.operating_threshold is None
            or feedback.payload.score_semantics is None
        ):
            raise BackendAnalysisError(
                "seungun_detector_failed",
                "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
            )
        return SeungunAnalysis(
            outcome=AnalysisOutcome.COACHING_READY,
            summary_feedback=feedback.message,
            pronunciation_evidence=PronunciationEvidence(
                selected_phone=location.target_unit,
                selected_expected_index=location.unit_index,
                selected_start_ms=location.start_ms,
                selected_end_ms=location.end_ms,
                detector_score=feedback.payload.detector_score,
                operating_threshold=feedback.payload.operating_threshold,
                score_semantics=feedback.payload.score_semantics,
                evidence_state=feedback.payload.evidence_state,
            ),
            pipeline_revision=pipeline_revision,
        )


def _analysis_snapshot(value: object) -> dict[str, object]:
    try:
        raw = value if isinstance(value, Mapping) else value.to_json_object()  # type: ignore[attr-defined]
        snapshot = json.loads(
            json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False)
        )
    except Exception:
        raise BackendAnalysisError(
            "seungun_detector_failed",
            "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
        ) from None
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SEUNGUN_SCHEMA_VERSION:
        raise BackendAnalysisError(
            "seungun_detector_failed",
            "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
        )
    return snapshot


def _is_completed_no_issue(
    analysis: Mapping[str, object], expected_phones: tuple[str, ...]
) -> bool:
    phones = analysis.get("phones")
    if not isinstance(phones, list) or len(phones) != len(expected_phones):
        return False
    return all(
        isinstance(phone, dict)
        and phone.get("expected_index") == index
        and phone.get("expected_phone") == expected_phone
        and phone.get("is_error") is False
        for index, (expected_phone, phone) in enumerate(
            zip(expected_phones, phones, strict=True)
        )
    )


def _pipeline_revision(
    expected: ExpectedPhoneSequence, analysis: Mapping[str, object]
) -> str:
    detector = analysis.get("detector_version")
    if not isinstance(detector, str) or not detector or len(detector) > 48:
        raise BackendAnalysisError(
            "seungun_detector_failed",
            "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
        )
    revision = f"{expected.backend_id}:{expected.revision}|seungun:{detector}"
    if len(revision) > 100:
        raise BackendAnalysisError(
            "seungun_detector_failed",
            "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
        )
    return revision


__all__ = [
    "DecodedWav",
    "RestrictedFfmpegAudioDecoder",
    "SeungunBackendAnalysisAdapter",
]
