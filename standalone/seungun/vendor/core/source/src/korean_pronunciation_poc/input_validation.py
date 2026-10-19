from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from .audio import AudioResult
from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .pipeline import PipelineRunResult

ValidationStatus = Literal["ACCEPT", "REJECT", "INCONCLUSIVE", "SYSTEM_FAILURE"]
ReasonCode = Literal[
    "AUDIO_DECODE_OR_CORRUPTION",
    "SILENCE_OR_NO_SPEECH",
    "ALIGNMENT_FAILURE",
    "SYSTEM_ANALYSIS_FAILURE",
    "INSUFFICIENT_CONTENT",
    "TRANSCRIPT_MISMATCH",
]


class InputValidationResultV1(BaseModel):
    """Development-only sidecar; never part of ai_core_result_v1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["input_validation_result_v1"] = "input_validation_result_v1"
    gate_version: Literal["phase2_gate_spec_v1", "demo_content_gate_calibration_v2"] = "phase2_gate_spec_v1"
    status: ValidationStatus
    reason_code: ReasonCode | None
    stage: PipelineStage | None
    evidence: dict[str, JsonValue]
    source_provenance: str | None
    original_error: PipelineError | None
    subject: Literal["RECORDING", "ANALYSIS_SYSTEM", "UNKNOWN"]
    decision_basis: str | None


def reject_if_canonical_pcm_all_zero(audio: AudioResult) -> None:
    """Inspect the prepared PCM, not compressed input or normalized model tensor.

    An inspection failure is allowed to propagate as a system error. In particular,
    an empty or malformed prepared WAV is never reclassified as digital silence.
    """
    with wave.open(str(audio.prepared_wav), "rb") as wav:
        if (
            wav.getframerate(), wav.getnchannels(), wav.getsampwidth(),
            wav.getcomptype(),
        ) != (16_000, 1, 2, "NONE"):
            raise ValueError("unexpected canonical PCM contract")
        sample_count = wav.getnframes()
        if sample_count <= 0:
            raise ValueError("empty canonical PCM")
        read_count = 0
        while read_count < sample_count:
            chunk = wav.readframes(min(65536, sample_count - read_count))
            if not chunk or len(chunk) % 2:
                raise ValueError("truncated canonical PCM")
            read_count += len(chunk) // 2
            if chunk.strip(b"\x00"):
                return
        if read_count != sample_count:
            raise ValueError("canonical PCM frame count mismatch")

    raise PipelineFailure(PipelineError(
        code=ErrorCode.INVALID_AUDIO,
        stage=PipelineStage.AUDIO,
        message_ko="canonical PCM의 모든 샘플이 정확히 0입니다.",
        details={
            "condition": "canonical_pcm_all_samples_zero",
            "sample_count": sample_count,
            "nonzero_sample_count": 0,
            "sample_rate_hz": 16000,
            "channels": 1,
            "sample_width_bytes": 2,
            "canonical_sha256": audio.metadata.sha256,
        },
    ))


def _probe_failure_evidence(
    path: Path, *, command: str, timeout_s: int
) -> tuple[str, dict[str, JsonValue]]:
    """Recheck only a failed source probe; do not infer cause from returncode alone."""
    try:
        with path.open("rb") as source:
            source.read(1)
    except OSError as exc:
        return "system", {"diagnostic": "source_unreadable", "exception_type": type(exc).__name__}
    executable = shutil.which(command)
    if executable is None:
        return "system", {"diagnostic": "ffprobe_executable_missing", "command": command}
    argv = [
        executable, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels:format=duration",
        "-of", "json", str(path),
    ]
    try:
        probe = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "system", {"diagnostic": "ffprobe_diagnostic_failed", "exception_type": type(exc).__name__}
    stderr = probe.stderr.strip()
    evidence: dict[str, JsonValue] = {
        "diagnostic": "ffprobe_recheck",
        "returncode": probe.returncode,
        "stderr": stderr,
        "executable": executable,
    }
    if "Permission denied" in stderr or "Operation not permitted" in stderr:
        return "system", evidence
    # This is ffmpeg's explicit input-content diagnostic, not a return-code heuristic.
    if probe.returncode != 0 and "Invalid data found when processing input" in stderr:
        return "recording", evidence
    return "unknown", evidence


def classify_pipeline_run(
    run: PipelineRunResult, *, audio_path: Path,
    ffprobe_command: str = "ffprobe", ffprobe_timeout_s: int = 60,
) -> InputValidationResultV1:
    errors = run.result.errors
    if not errors:
        return InputValidationResultV1(
            status="ACCEPT", reason_code=None, stage=None, evidence={},
            source_provenance=None, original_error=None, subject="RECORDING",
            decision_basis="no_approved_invalidity_evidence",
        )

    first = errors[0]
    evidence: dict[str, JsonValue] = {
        "all_errors": [error.model_dump(mode="json") for error in errors],
    }
    status: ValidationStatus = "INCONCLUSIVE"
    reason: ReasonCode | None = None
    subject: Literal["RECORDING", "ANALYSIS_SYSTEM", "UNKNOWN"] = "UNKNOWN"
    provenance = "pipeline.AnalysisPipeline.analyze"
    basis = "unattributed_pipeline_failure"

    if (
        first.code is ErrorCode.INVALID_AUDIO
        and first.details.get("condition") == "demo_content_reject"
        and first.details.get("reason_code") in {"INSUFFICIENT_CONTENT", "TRANSCRIPT_MISMATCH"}
    ):
        return InputValidationResultV1(
            gate_version="demo_content_gate_calibration_v2",
            status="REJECT", reason_code=first.details["reason_code"],
            stage=first.stage, evidence={**evidence, "content_measure": first.details["measurement"],
                                         "content_config_sha256": first.details["config_sha256"]},
            source_provenance="demo_content_evidence.measure/route",
            original_error=first, subject="RECORDING",
            decision_basis=first.details["basis"],
        )

    if (
        first.code is ErrorCode.INVALID_AUDIO
        and first.stage is PipelineStage.AUDIO
        and first.details.get("condition") == "canonical_pcm_all_samples_zero"
    ):
        return InputValidationResultV1(
            status="REJECT",
            reason_code="SILENCE_OR_NO_SPEECH",
            stage=PipelineStage.AUDIO,
            evidence={
                **evidence,
                "exact_digital_silence": dict(first.details),
            },
            source_provenance="input_validation.reject_if_canonical_pcm_all_zero",
            original_error=first,
            subject="RECORDING",
            decision_basis="canonical_pcm_all_samples_zero",
        )

    if (
        first.code is ErrorCode.INVALID_AUDIO
        and first.stage is PipelineStage.AUDIO
        and first.details.get("tool") == "ffprobe"
    ):
        provenance = "audio._probe(source)"
        reason = "AUDIO_DECODE_OR_CORRUPTION"
        if "returncode" in first.details:
            cause, diagnostic = _probe_failure_evidence(
                audio_path, command=ffprobe_command, timeout_s=ffprobe_timeout_s
            )
            evidence["ffprobe_failure_recheck"] = diagnostic
            if cause == "recording":
                status, subject = "REJECT", "RECORDING"
                basis = "explicit_ffprobe_input_content_diagnostic"
            elif cause == "system":
                status, reason, subject = (
                    "SYSTEM_FAILURE", "SYSTEM_ANALYSIS_FAILURE", "ANALYSIS_SYSTEM"
                )
                basis = "diagnostic_tool_or_source_access_failure"
            else:
                basis = "ffprobe_nonzero_without_input_attribution"
        else:
            basis = "probe_metadata_failure_without_input_attribution"
    elif first.code in {ErrorCode.ALIGNMENT_FAILED, ErrorCode.TEXTGRID_PARSE_FAILED}:
        provenance = (
            "mfa.run_mfa"
            if first.code is ErrorCode.ALIGNMENT_FAILED
            else "textgrid.parse_textgrid"
        )
        if first.details.get("exception_type") in {"BrokenPipeError", "TimeoutError", "EOFError", "ConnectionError"}:
            status, reason, subject = "SYSTEM_FAILURE", "SYSTEM_ANALYSIS_FAILURE", "ANALYSIS_SYSTEM"
            basis = "explicit_alignment_transport_or_timeout_failure"
        else:
            status, reason, subject = "INCONCLUSIVE", "ALIGNMENT_FAILURE", "UNKNOWN"
            basis = "explicit_unusable_alignment_recording_cause_unproven"
    elif first.code in {
        ErrorCode.DEPENDENCY_MISSING,
        ErrorCode.MODEL_MISSING,
        ErrorCode.INTERNAL_ERROR,
        ErrorCode.G2P_FAILED,
        ErrorCode.ACOUSTIC_ANALYSIS_FAILED,
    } or (
        first.code is ErrorCode.AUDIO_CONVERSION_FAILED
        and first.details.get("tool") in {"ffprobe", "ffmpeg"}
        and first.message_ko in {
            "오디오 검사 시간이 제한을 초과했습니다.",
            "오디오 변환 시간이 제한을 초과했습니다.",
        }
    ):
        status, reason, subject = (
            "SYSTEM_FAILURE", "SYSTEM_ANALYSIS_FAILURE", "ANALYSIS_SYSTEM"
        )
        basis = "dependency_model_internal_or_command_timeout"
    elif first.code is ErrorCode.AUDIO_CONVERSION_FAILED:
        status, reason, subject = (
            "INCONCLUSIVE", "AUDIO_DECODE_OR_CORRUPTION", "UNKNOWN"
        )
        provenance = "audio.prepare_audio"
        basis = "canonicalization_failed_input_causality_unproven"

    return InputValidationResultV1(
        status=status, reason_code=reason, stage=first.stage, evidence=evidence,
        source_provenance=provenance, original_error=first, subject=subject,
        decision_basis=basis,
    )
