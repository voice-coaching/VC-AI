from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .schemas import Sha256


class ModelManifestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_path: Path
    manifest_sha256: Sha256
    file_count: int
    total_bytes: int


def _failure(message_ko: str, **details: JsonValue) -> PipelineFailure:
    return PipelineFailure(
        PipelineError(
            code=ErrorCode.MODEL_MISSING,
            stage=PipelineStage.ALIGNMENT,
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


def create_model_manifest(
    snapshot_path: Path,
    manifest_path: Path,
    *,
    repository: str,
    revision: str,
    license_id: str,
    expected_lfs_sha256: Mapping[str, str],
) -> ModelManifestResult:
    snapshot = snapshot_path.expanduser().resolve(strict=False)
    if not snapshot.is_dir():
        raise _failure("MFA 모델 snapshot 디렉터리가 없습니다.")

    manifest = manifest_path.expanduser().resolve(strict=False)
    if manifest.is_relative_to(snapshot):
        raise _failure("MFA 모델 manifest는 snapshot 외부에 저장해야 합니다.")

    files = sorted(
        (path for path in snapshot.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(snapshot).as_posix(),
    )
    if not files:
        raise _failure("MFA 모델 snapshot에 파일이 없습니다.")

    entries: list[dict[str, JsonValue]] = []
    total_bytes = 0
    seen_paths: set[str] = set()
    for path in files:
        relative_path = path.relative_to(snapshot).as_posix()
        actual_sha256 = _file_sha256(path)
        expected_sha256 = expected_lfs_sha256.get(relative_path)
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise _failure(
                "MFA 모델 파일의 SHA-256이 공식 값과 일치하지 않습니다.",
                model_file=relative_path,
            )
        size_bytes = path.stat().st_size
        total_bytes += size_bytes
        seen_paths.add(relative_path)
        entries.append(
            {
                "path": relative_path,
                "size_bytes": size_bytes,
                "sha256": actual_sha256,
                "official_sha256_verified": expected_sha256 is not None,
            }
        )

    missing_official_files = sorted(set(expected_lfs_sha256) - seen_paths)
    if missing_official_files:
        raise _failure(
            "공식 SHA-256 검증 대상 MFA 모델 파일이 없습니다.",
            missing_files=cast(JsonValue, missing_official_files),
        )

    payload = {
        "schema_version": "1.0.0",
        "repository": repository,
        "revision": revision,
        "license": license_id,
        "files": entries,
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = manifest.with_suffix(manifest.suffix + ".tmp")
    temporary_manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest)
    return ModelManifestResult(
        manifest_path=manifest,
        manifest_sha256=_file_sha256(manifest),
        file_count=len(entries),
        total_bytes=total_bytes,
    )
