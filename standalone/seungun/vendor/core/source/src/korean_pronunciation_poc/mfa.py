from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from .audio import AudioResult
from .config import PocConfig
from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .schemas import ModelVersion, Sha256
from .script import ScriptResult


Runner = Callable[..., subprocess.CompletedProcess[str]]


class MfaRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    textgrid_path: Path
    mfa_version: str
    model: ModelVersion
    manifest_sha256: Sha256
    argv_redacted: list[str]
    exit_code: int


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


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(config: PocConfig) -> Path:
    return config.paths.mfa_root_dir / "manifests/korean_mfa.json"


def _snapshot_path(config: PocConfig) -> Path:
    repository_cache_name = "models--" + config.alignment.model_repository.replace(
        "/", "--"
    )
    return (
        config.paths.mfa_root_dir
        / "huggingface"
        / repository_cache_name
        / "snapshots"
        / config.alignment.model_revision
    )


def _load_manifest(path: Path) -> Mapping[str, Any]:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _failure(
            ErrorCode.MODEL_MISSING,
            PipelineStage.ALIGNMENT,
            "MFA 모델 manifest를 읽을 수 없습니다.",
            exception_type=type(exc).__name__,
        ) from exc
    if not isinstance(payload, dict):
        raise _failure(
            ErrorCode.MODEL_MISSING,
            PipelineStage.ALIGNMENT,
            "MFA 모델 manifest 형식이 올바르지 않습니다.",
        )
    return payload


def _verify_model_manifest(config: PocConfig) -> str:
    manifest_path = _manifest_path(config)
    if not manifest_path.is_file():
        raise _failure(
            ErrorCode.MODEL_MISSING,
            PipelineStage.ALIGNMENT,
            "승인된 MFA 모델 manifest가 없습니다.",
        )

    manifest = _load_manifest(manifest_path)
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("repository") != config.alignment.model_repository
        or manifest.get("revision") != config.alignment.model_revision
        or manifest.get("license") != config.alignment.model_license
    ):
        raise _failure(
            ErrorCode.MODEL_MISSING,
            PipelineStage.ALIGNMENT,
            "MFA 모델 manifest의 식별자가 설정과 일치하지 않습니다.",
        )

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise _failure(
            ErrorCode.MODEL_MISSING,
            PipelineStage.ALIGNMENT,
            "MFA 모델 manifest에 검증할 파일이 없습니다.",
        )

    hf_cache = (config.paths.mfa_root_dir / "huggingface").resolve(strict=False)
    snapshot = _snapshot_path(config).resolve(strict=False)
    if not snapshot.is_relative_to(hf_cache):
        raise _failure(
            ErrorCode.MODEL_MISSING,
            PipelineStage.ALIGNMENT,
            "MFA 모델 snapshot이 프로젝트 캐시 밖에 있습니다.",
        )
    for entry in files:
        if not isinstance(entry, dict):
            raise _failure(
                ErrorCode.MODEL_MISSING,
                PipelineStage.ALIGNMENT,
                "MFA 모델 manifest 파일 항목이 올바르지 않습니다.",
            )
        relative_path = entry.get("path")
        expected_sha256 = entry.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(expected_sha256, str):
            raise _failure(
                ErrorCode.MODEL_MISSING,
                PipelineStage.ALIGNMENT,
                "MFA 모델 manifest 파일 항목이 올바르지 않습니다.",
            )
        manifest_relative_path = PurePosixPath(relative_path)
        if manifest_relative_path.is_absolute() or ".." in manifest_relative_path.parts:
            raise _failure(
                ErrorCode.MODEL_MISSING,
                PipelineStage.ALIGNMENT,
                "MFA 모델 manifest 파일 경로가 올바르지 않습니다.",
                model_file=relative_path,
            )
        candidate = (snapshot / Path(*manifest_relative_path.parts)).resolve(
            strict=False
        )
        if (
            not candidate.is_relative_to(hf_cache)
            or not candidate.is_file()
            or len(expected_sha256) != 64
            or _file_sha256(candidate) != expected_sha256
        ):
            raise _failure(
                ErrorCode.MODEL_MISSING,
                PipelineStage.ALIGNMENT,
                "MFA 모델 snapshot이 manifest와 일치하지 않습니다.",
                model_file=relative_path,
            )
    return _file_sha256(manifest_path)


def _runtime_environment(config: PocConfig) -> dict[str, str]:
    environment = dict(os.environ)
    hf_cache = config.paths.mfa_root_dir / "huggingface"
    environment.update(
        {
            "PATH": os.pathsep.join(
                (
                    str(config.paths.mfa_executable.parent),
                    environment.get("PATH", ""),
                )
            ),
            "MFA_ROOT_DIR": str(config.paths.mfa_root_dir),
            "HF_HOME": str(hf_cache),
            "HF_HUB_CACHE": str(hf_cache),
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        }
    )
    return environment


def _invoke(
    runner: Runner,
    argv: Sequence[str],
    timeout_s: float,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return runner(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env=environment,
    )


def _mfa_version(
    executable: Path,
    config: PocConfig,
    runner: Runner,
    environment: Mapping[str, str],
) -> str:
    argv = [str(executable), "version"]
    try:
        completed = _invoke(runner, argv, config.alignment.mfa_timeout_s, environment)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _failure(
            ErrorCode.DEPENDENCY_MISSING,
            PipelineStage.DEPENDENCY,
            "MFA 실행 파일의 버전을 확인할 수 없습니다.",
            exception_type=type(exc).__name__,
        ) from exc
    version = completed.stdout.strip()
    if completed.returncode != 0 or not version:
        raise _failure(
            ErrorCode.DEPENDENCY_MISSING,
            PipelineStage.DEPENDENCY,
            "MFA 실행 파일의 버전을 확인할 수 없습니다.",
            returncode=completed.returncode,
        )
    return version


def run_mfa(
    audio: AudioResult,
    script: ScriptResult,
    session_id: UUID,
    config: PocConfig,
    *,
    runner: Runner = subprocess.run,
) -> MfaRunResult:
    manifest_sha256 = _verify_model_manifest(config)

    executable = config.paths.mfa_executable
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise _failure(
            ErrorCode.DEPENDENCY_MISSING,
            PipelineStage.DEPENDENCY,
            "설정된 MFA 실행 파일을 사용할 수 없습니다.",
        )

    session_root = config.paths.work_dir / "mfa" / str(session_id)
    corpus_dir = session_root / "corpus"
    output_dir = session_root / "output"
    runtime_dir = session_root / "runtime"
    for directory in (corpus_dir, output_dir, runtime_dir):
        directory.mkdir(parents=True, exist_ok=True)

    corpus_wav = corpus_dir / audio.prepared_wav.name
    corpus_lab = corpus_wav.with_suffix(".lab")
    shutil.copy2(audio.prepared_wav, corpus_wav)
    corpus_lab.write_text(script.normalized + "\n", encoding="utf-8")

    environment = _runtime_environment(config)
    version = _mfa_version(executable, config, runner, environment)
    model_ref = f"{config.alignment.model_repository}@{config.alignment.model_revision}"
    argv = [
        str(executable),
        "align_hf",
        "--temporary_directory",
        str(runtime_dir),
        "--single_speaker",
        "--no_tokenization",
        "--use_g2p",
        "--num_jobs",
        "1",
        str(corpus_dir),
        model_ref,
        str(output_dir),
    ]
    try:
        completed = _invoke(runner, argv, config.alignment.mfa_timeout_s, environment)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _failure(
            ErrorCode.ALIGNMENT_FAILED,
            PipelineStage.ALIGNMENT,
            "MFA 정렬 실행이 제한 시간 안에 완료되지 않았습니다.",
            exception_type=type(exc).__name__,
        ) from exc
    if completed.returncode != 0:
        raise _failure(
            ErrorCode.ALIGNMENT_FAILED,
            PipelineStage.ALIGNMENT,
            "MFA 정렬 실행에 실패했습니다.",
            returncode=completed.returncode,
        )

    textgrids = sorted(output_dir.rglob("*.TextGrid"))
    if len(textgrids) != 1 or not textgrids[0].is_file():
        raise _failure(
            ErrorCode.ALIGNMENT_FAILED,
            PipelineStage.ALIGNMENT,
            "MFA 정렬 결과 TextGrid가 생성되지 않았습니다.",
            textgrid_count=len(textgrids),
        )

    redacted_argv = [
        "<mfa_executable>",
        "align_hf",
        "--temporary_directory",
        "<runtime_dir>",
        "--single_speaker",
        "--no_tokenization",
        "--use_g2p",
        "--num_jobs",
        "1",
        "<corpus_dir>",
        model_ref,
        "<output_dir>",
    ]
    return MfaRunResult(
        textgrid_path=textgrids[0],
        mfa_version=version,
        model=ModelVersion(
            role="forced_alignment",
            name=config.alignment.model_repository,
            revision=config.alignment.model_revision,
        ),
        manifest_sha256=manifest_sha256,
        argv_redacted=redacted_argv,
        exit_code=completed.returncode,
    )
