from __future__ import annotations

import platform
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from .config import PocConfig
from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .mfa import MfaRunResult
from .schemas import PackageVersion, Provenance, RuntimeEnvironment


PlatformRole = Literal["mac_smoke", "dlpc_formal", "test", "unknown"]
Runner = Callable[..., subprocess.CompletedProcess[str]]
PackageVersionLookup = Callable[[str], str]
Clock = Callable[[], datetime]

TRACKED_PACKAGES = (
    "g2pK",
    "numpy",
    "praat-parselmouth",
    "praatio",
    "pydantic",
)


def _failure(message_ko: str, **details: JsonValue) -> PipelineFailure:
    return PipelineFailure(
        PipelineError(
            code=ErrorCode.INTERNAL_ERROR,
            stage=PipelineStage.INTERNAL,
            message_ko=message_ko,
            details=details,
        )
    )


def _file_sha256(path: Path) -> str:
    try:
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise _failure(
            "재현성 파일의 SHA-256을 계산할 수 없습니다.",
            path=str(path),
            exception_type=type(exc).__name__,
        ) from exc


def _run(
    runner: Runner,
    argv: Sequence[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _failure(
            "재현성 명령을 실행할 수 없습니다.",
            command=argv[0],
            exception_type=type(exc).__name__,
        ) from exc


def _git_state(config: PocConfig, runner: Runner) -> tuple[str, bool]:
    """Legacy output fields populated from the pinned deployment snapshot."""
    try:
        path = config.paths.project_root / "config/release_provenance.json"
        doc = json.loads(path.read_bytes())
        if (set(doc) != {"schema", "source_git_commit", "source_modified"}
                or doc["schema"] != "canonical-release-provenance-v1"
                or not re.fullmatch("[0-9a-f]{40}", doc["source_git_commit"])
                or type(doc["source_modified"]) is not bool):
            raise ValueError("RELEASE_PROVENANCE_INVALID")
        return doc["source_git_commit"], doc["source_modified"]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise _failure("배포 출처 정보를 확인할 수 없습니다.",
                       exception_type=type(exc).__name__) from exc


def _tool_version(
    command: str,
    *,
    cwd: Path,
    runner: Runner,
) -> str | None:
    try:
        completed = _run(runner, [command, "-version"], cwd=cwd)
    except PipelineFailure:
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    first_line = completed.stdout.splitlines()[0]
    tokens = first_line.split()
    if len(tokens) >= 3 and tokens[1].lower() == "version":
        return tokens[2]
    return tokens[1] if len(tokens) >= 2 else first_line


def _package_versions(
    config: PocConfig,
    *,
    mfa_result: MfaRunResult | None,
    runner: Runner,
    package_version: PackageVersionLookup,
) -> list[PackageVersion]:
    found: list[PackageVersion] = []
    for name in TRACKED_PACKAGES:
        try:
            found.append(PackageVersion(name=name, version=package_version(name)))
        except PackageNotFoundError:
            continue
    for name, command in (
        ("ffmpeg", config.audio.ffmpeg_command),
        ("ffprobe", config.audio.ffprobe_command),
    ):
        tool_version = _tool_version(
            command,
            cwd=config.paths.project_root,
            runner=runner,
        )
        if tool_version is not None:
            found.append(PackageVersion(name=name, version=tool_version))
    if mfa_result is not None:
        found.append(PackageVersion(name="mfa", version=mfa_result.mfa_version))
    return sorted(found, key=lambda item: item.name)


def collect_provenance(
    config: PocConfig,
    platform_role: PlatformRole,
    *,
    mfa_result: MfaRunResult | None,
    runner: Runner = subprocess.run,
    package_version: PackageVersionLookup = version,
    now: Clock = lambda: datetime.now(timezone.utc),
) -> Provenance:
    root = config.paths.project_root.resolve(strict=True)
    config_path = root / "config/poc.toml"
    lock_path = root / "uv.lock"
    git_commit, dirty_worktree = _git_state(config, runner)
    packages = _package_versions(
        config,
        mfa_result=mfa_result,
        runner=runner,
        package_version=package_version,
    )
    models = []
    if mfa_result is not None:
        models.append(
            mfa_result.model.model_copy(update={"sha256": mfa_result.manifest_sha256})
        )
    return Provenance(
        created_at_utc=now(),
        git_commit=git_commit,
        dirty_worktree=dirty_worktree,
        packages=packages,
        models=models,
        runtime=RuntimeEnvironment(
            platform_role=platform_role,
            operating_system=f"{platform.system()} {platform.release()}",
            architecture=platform.machine(),
            python_version=platform.python_version() or sys.version.split()[0],
        ),
        config_sha256=_file_sha256(config_path),
        dependency_lock_sha256=_file_sha256(lock_path),
    )
