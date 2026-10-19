from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import JsonValue

from .config import PocConfig
from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .schemas import AnalysisResult


def _failure(message_ko: str, **details: JsonValue) -> PipelineFailure:
    return PipelineFailure(
        PipelineError(
            code=ErrorCode.INTERNAL_ERROR,
            stage=PipelineStage.STORAGE,
            message_ko=message_ko,
            details=details,
        )
    )


def _deterministic_json(payload: JsonValue) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _remove_temp(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def save_result(result: AnalysisResult, config: PocConfig) -> Path:
    temp_path: Path | None = None
    try:
        project_root = config.paths.project_root.resolve(strict=True)
        output_root = config.paths.output_dir.resolve(strict=False)
        if not output_root.is_relative_to(project_root):
            raise ValueError("output_dir must remain inside project_root")

        requested_dir = output_root / str(result.analysis_id)
        requested_dir.mkdir(parents=True, exist_ok=True)
        target_dir = requested_dir.resolve(strict=True)
        if not target_dir.is_relative_to(project_root) or not target_dir.is_relative_to(
            output_root
        ):
            raise ValueError("analysis output directory escaped project_root")
        target = target_dir / "result.json"

        serialized = _deterministic_json(result.model_dump(mode="json"))
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".result-",
            suffix=".tmp",
            dir=target_dir,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        temp_path = None
        return target
    except Exception as exc:
        _remove_temp(temp_path)
        if isinstance(exc, PipelineFailure):
            raise
        raise _failure(
            "분석 결과를 안전하게 저장할 수 없습니다.",
            exception_type=type(exc).__name__,
        ) from exc


def write_analysis_schema(path: Path) -> Path:
    schema_path = path.expanduser().resolve(strict=False)
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    payload = AnalysisResult.model_json_schema()
    schema_path.write_text(
        _deterministic_json(payload),
        encoding="utf-8",
        newline="\n",
    )
    return schema_path
