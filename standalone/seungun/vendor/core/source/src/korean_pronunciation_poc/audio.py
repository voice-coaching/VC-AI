from __future__ import annotations

import json
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, JsonValue

from .config import PocConfig
from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .schemas import AudioMetadata


class AudioResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prepared_wav: Path
    metadata: AudioMetadata


def _failure(
    code: ErrorCode,
    stage: PipelineStage,
    message_ko: str,
    **details: JsonValue,
) -> PipelineFailure:
    return PipelineFailure(
        PipelineError(
            code=code,
            stage=stage,
            message_ko=message_ko,
            details=details,
        )
    )


def _resolve_command(command: str) -> str:
    executable = shutil.which(command)
    if executable is None:
        raise _failure(
            ErrorCode.DEPENDENCY_MISSING,
            PipelineStage.DEPENDENCY,
            "필수 오디오 처리 실행 파일을 찾을 수 없습니다.",
            command=command,
        )
    return executable


def _run(argv: list[str], timeout_s: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


def _parse_probe_payload(payload: str) -> tuple[float, int, int, str]:
    data: Any = json.loads(payload)
    stream = data["streams"][0]
    duration_s = float(data["format"]["duration"])
    sample_rate_hz = int(stream["sample_rate"])
    channels = int(stream["channels"])
    encoding = str(stream["codec_name"])
    if duration_s <= 0 or sample_rate_hz <= 0 or channels <= 0 or not encoding:
        raise ValueError("invalid audio metadata")
    return duration_s, sample_rate_hz, channels, encoding


def _probe(
    executable: str,
    path: Path,
    timeout_s: int,
    *,
    invalid_code: ErrorCode,
) -> tuple[float, int, int, str]:
    argv = [
        executable,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,sample_rate,channels:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = _run(argv, timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise _failure(
            ErrorCode.AUDIO_CONVERSION_FAILED,
            PipelineStage.AUDIO,
            "오디오 검사 시간이 제한을 초과했습니다.",
            tool="ffprobe",
        ) from exc
    if completed.returncode != 0:
        raise _failure(
            invalid_code,
            PipelineStage.AUDIO,
            "음성 파일을 읽거나 검증할 수 없습니다.",
            tool="ffprobe",
            returncode=completed.returncode,
        )
    try:
        return _parse_probe_payload(completed.stdout)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _failure(
            invalid_code,
            PipelineStage.AUDIO,
            "음성 파일 메타데이터가 올바르지 않습니다.",
            tool="ffprobe",
            exception_type=type(exc).__name__,
        ) from exc


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as audio_file:
        for chunk in iter(lambda: audio_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_partial(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def prepare_audio(source: Path, workdir: Path, config: PocConfig) -> AudioResult:
    ffmpeg = _resolve_command(config.audio.ffmpeg_command)
    ffprobe = _resolve_command(config.audio.ffprobe_command)

    source_path = source.expanduser().resolve(strict=False)
    if not source_path.is_file():
        raise _failure(
            ErrorCode.INVALID_AUDIO,
            PipelineStage.AUDIO,
            "입력 음성 파일이 존재하지 않습니다.",
        )

    project_root = config.paths.project_root.resolve(strict=True)
    resolved_workdir = workdir.expanduser().resolve(strict=False)
    if not resolved_workdir.is_relative_to(project_root):
        raise _failure(
            ErrorCode.INVALID_AUDIO,
            PipelineStage.AUDIO,
            "오디오 작업 경로는 프로젝트 내부여야 합니다.",
        )

    source_duration_s, _, _, _ = _probe(
        ffprobe,
        source_path,
        config.audio.timeout_s,
        invalid_code=ErrorCode.INVALID_AUDIO,
    )
    if source_duration_s > config.analysis.max_audio_s:
        raise _failure(
            ErrorCode.INVALID_AUDIO,
            PipelineStage.AUDIO,
            "입력 음성이 허용 길이를 초과했습니다.",
            duration_s=source_duration_s,
            max_audio_s=config.analysis.max_audio_s,
        )

    resolved_workdir.mkdir(parents=True, exist_ok=True)
    resolved_workdir = resolved_workdir.resolve(strict=True)
    if not resolved_workdir.is_relative_to(project_root):
        raise _failure(
            ErrorCode.INVALID_AUDIO,
            PipelineStage.AUDIO,
            "오디오 작업 경로는 프로젝트 내부여야 합니다.",
        )

    target = resolved_workdir / f"prepared-{uuid4().hex}.wav"
    argv = [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(target),
    ]
    try:
        try:
            completed = _run(argv, config.audio.timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise _failure(
                ErrorCode.AUDIO_CONVERSION_FAILED,
                PipelineStage.AUDIO,
                "오디오 변환 시간이 제한을 초과했습니다.",
                tool="ffmpeg",
            ) from exc
        if completed.returncode != 0:
            raise _failure(
                ErrorCode.AUDIO_CONVERSION_FAILED,
                PipelineStage.AUDIO,
                "오디오 변환에 실패했습니다.",
                tool="ffmpeg",
                returncode=completed.returncode,
            )
        if not target.is_file():
            raise _failure(
                ErrorCode.AUDIO_CONVERSION_FAILED,
                PipelineStage.AUDIO,
                "변환된 오디오 파일이 생성되지 않았습니다.",
            )

        duration_s, sample_rate_hz, channels, encoding = _probe(
            ffprobe,
            target,
            config.audio.timeout_s,
            invalid_code=ErrorCode.AUDIO_CONVERSION_FAILED,
        )
        if (sample_rate_hz, channels, encoding) != (16_000, 1, "pcm_s16le"):
            raise _failure(
                ErrorCode.AUDIO_CONVERSION_FAILED,
                PipelineStage.AUDIO,
                "변환된 오디오 규격이 요구 조건과 다릅니다.",
                sample_rate_hz=sample_rate_hz,
                channels=channels,
                encoding=encoding,
            )
    except PipelineFailure:
        _remove_partial(target)
        raise

    return AudioResult(
        prepared_wav=target,
        metadata=AudioMetadata(
            duration_s=duration_s,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            encoding=encoding,
            sha256=_file_sha256(target),
        ),
    )
