from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import re
from typing import Any, Protocol, cast

from praatio.utilities import textgrid_io  # type: ignore[import-untyped]
from pydantic import JsonValue, ValidationError

from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .schemas import TimeInterval


REQUIRED_TIER_NAMES = ("words", "phones")


@dataclass(frozen=True)
class TextGridParseResult:
    word_intervals: list[TimeInterval]
    phone_intervals: list[TimeInterval]
    tier_names: tuple[str, ...]


class _PraatioInterval(Protocol):
    start: str | float
    end: str | float
    label: str


def _failure(message_ko: str, **details: JsonValue) -> PipelineFailure:
    return PipelineFailure(
        PipelineError(
            code=ErrorCode.TEXTGRID_PARSE_FAILED,
            stage=PipelineStage.TEXTGRID,
            message_ko=message_ko,
            details=details,
        )
    )


def _validate_time(value: float, *, field: str) -> None:
    if not isfinite(value) or value < 0:
        raise _failure(
            "TextGrid 시간값이 유효하지 않습니다.",
            field=field,
        )


def _read_and_validate_raw_textgrid(path: Path) -> str:
    try:
        try:
            content = path.read_text(encoding="utf-16")
        except UnicodeError:
            content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _failure(
            "TextGrid 파일을 읽을 수 없습니다.",
            exception_type=type(exc).__name__,
        ) from exc

    for match in re.finditer(
        r"^\s*(?:xmin|xmax)\s*=\s*([^\s]+)\s*$",
        content,
        flags=re.MULTILINE,
    ):
        try:
            value = float(match.group(1))
        except ValueError as exc:
            raise _failure("TextGrid 시간값이 숫자가 아닙니다.") from exc
        _validate_time(value, field="raw_textgrid_time")
    return content


def _convert_entries(
    entries: list[_PraatioInterval],
    *,
    audio_duration_s: float,
) -> list[TimeInterval]:
    intervals: list[TimeInterval] = []
    previous_end_s: float | None = None
    for entry in sorted(entries, key=lambda value: float(value.start)):
        start_s = float(entry.start)
        end_s = float(entry.end)
        _validate_time(start_s, field="start_s")
        _validate_time(end_s, field="end_s")
        if start_s > end_s:
            raise _failure("TextGrid 구간의 시작 시간이 종료 시간보다 늦습니다.")
        if previous_end_s is not None and previous_end_s > start_s:
            raise _failure("TextGrid의 같은 tier 구간이 서로 겹칩니다.")
        if end_s > audio_duration_s:
            raise _failure(
                "TextGrid 구간이 오디오 길이를 초과합니다.",
                audio_duration_s=audio_duration_s,
            )
        try:
            intervals.append(
                TimeInterval(label=entry.label, start_s=start_s, end_s=end_s)
            )
        except ValidationError as exc:
            raise _failure(
                "TextGrid 구간을 공통 시간 계약으로 변환할 수 없습니다.",
                exception_type=type(exc).__name__,
            ) from exc
        previous_end_s = end_s
    return intervals


def parse_textgrid(path: Path, *, audio_duration_s: float) -> TextGridParseResult:
    textgrid_path = path.expanduser().resolve(strict=False)
    if not textgrid_path.is_file():
        raise _failure("TextGrid 파일이 존재하지 않습니다.")
    _validate_time(audio_duration_s, field="audio_duration_s")
    content = _read_and_validate_raw_textgrid(textgrid_path)

    try:
        parsed = cast(
            dict[str, Any],
            textgrid_io.parseTextgridStr(content, includeEmptyIntervals=True),
        )
        _validate_time(float(parsed["xmin"]), field="textgrid_min_s")
        _validate_time(float(parsed["xmax"]), field="textgrid_max_s")
        tiers = cast(list[dict[str, Any]], parsed["tiers"])
        tier_names = tuple(str(tier["name"]) for tier in tiers)
        if len(set(tier_names)) != len(tier_names):
            raise _failure("TextGrid tier 이름이 중복되었습니다.")
        missing_tiers = [
            tier_name
            for tier_name in REQUIRED_TIER_NAMES
            if tier_name not in tier_names
        ]
        if missing_tiers:
            raise _failure(
                "TextGrid에 필수 MFA tier가 없습니다.",
                missing_tiers=cast(JsonValue, missing_tiers),
            )

        tiers_by_name = {str(tier["name"]): tier for tier in tiers}
        words_tier = tiers_by_name["words"]
        phones_tier = tiers_by_name["phones"]
        if (
            words_tier.get("class") != "IntervalTier"
            or phones_tier.get("class") != "IntervalTier"
        ):
            raise _failure("MFA words와 phones tier는 IntervalTier여야 합니다.")
        for tier_name, tier in (("words", words_tier), ("phones", phones_tier)):
            _validate_time(float(tier["xmin"]), field=f"{tier_name}_min_s")
            _validate_time(float(tier["xmax"]), field=f"{tier_name}_max_s")

        return TextGridParseResult(
            word_intervals=_convert_entries(
                cast(list[_PraatioInterval], words_tier["entries"]),
                audio_duration_s=audio_duration_s,
            ),
            phone_intervals=_convert_entries(
                cast(list[_PraatioInterval], phones_tier["entries"]),
                audio_duration_s=audio_duration_s,
            ),
            tier_names=tier_names,
        )
    except PipelineFailure:
        raise
    except Exception as exc:
        raise _failure(
            "TextGrid 파일을 파싱할 수 없습니다.",
            exception_type=type(exc).__name__,
        ) from exc
