from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isclose, isfinite

from pydantic import JsonValue

from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .schemas import AcousticFeature, TimeInterval
from .script import ScriptResult


NON_SPEECH_LABELS = {"", "sil", "sp", "noise"}
NOISE_LABEL = "noise"
TIMING_METHOD = "mfa_word_intervals_v1"
SYLLABLE_METHOD = "normalized_hangul_syllable_blocks_v1"


class PausePosition(str, Enum):
    LEADING = "leading"
    INTERNAL = "internal"
    TRAILING = "trailing"


@dataclass(frozen=True)
class PauseSegment:
    interval: TimeInterval
    position: PausePosition


@dataclass(frozen=True)
class TimingResult:
    features: list[AcousticFeature]
    pauses: list[PauseSegment]


def _alignment_failure(message_ko: str, **details: JsonValue) -> PipelineFailure:
    return PipelineFailure(
        PipelineError(
            code=ErrorCode.ALIGNMENT_FAILED,
            stage=PipelineStage.ALIGNMENT,
            message_ko=message_ko,
            details=details,
        )
    )


def _count_hangul_syllables(text: str) -> int:
    return sum("\uac00" <= character <= "\ud7a3" for character in text)


def _feature(
    name: str,
    unit: str,
    value: float | None,
    *,
    method: str,
) -> AcousticFeature:
    return AcousticFeature(
        name=name,
        unit=unit,
        method=method,
        summary={"value": value},
    )


def _build_features(
    *,
    syllable_count: int,
    utterance_duration_s: float | None,
    speech_duration_s: float | None,
) -> list[AcousticFeature]:
    speaking_rate = (
        syllable_count / utterance_duration_s
        if utterance_duration_s is not None and utterance_duration_s > 0
        else None
    )
    articulation_rate = (
        syllable_count / speech_duration_s
        if speech_duration_s is not None and speech_duration_s > 0
        else None
    )
    return [
        _feature(
            "syllable_count",
            "syllables",
            float(syllable_count),
            method=SYLLABLE_METHOD,
        ),
        _feature(
            "utterance_duration",
            "s",
            utterance_duration_s,
            method=TIMING_METHOD,
        ),
        _feature(
            "speaking_rate",
            "syllables/s",
            speaking_rate,
            method=TIMING_METHOD,
        ),
        _feature(
            "articulation_rate",
            "syllables/s",
            articulation_rate,
            method=TIMING_METHOD,
        ),
    ]


def _validate_intervals(
    intervals: list[TimeInterval],
    *,
    audio_duration_s: float,
) -> None:
    previous: TimeInterval | None = None
    for interval in intervals:
        if interval.end_s > audio_duration_s:
            raise _alignment_failure(
                "단어 구간이 오디오 길이를 초과합니다.",
                audio_duration_s=audio_duration_s,
            )
        if previous is not None:
            if interval.start_s < previous.start_s:
                raise _alignment_failure("단어 구간의 시간 순서가 잘못되었습니다.")
            if interval.start_s < previous.end_s:
                raise _alignment_failure("단어 구간이 서로 겹칩니다.")
        previous = interval


def _silence_complement(
    intervals: list[TimeInterval],
    *,
    audio_duration_s: float,
) -> list[tuple[float, float]]:
    occupied = [
        interval
        for interval in intervals
        if interval.label not in NON_SPEECH_LABELS or interval.label == NOISE_LABEL
    ]
    candidates: list[tuple[float, float]] = []
    cursor = 0.0
    for interval in occupied:
        if interval.start_s > cursor:
            candidates.append((cursor, interval.start_s))
        cursor = max(cursor, interval.end_s)
    if cursor < audio_duration_s:
        candidates.append((cursor, audio_duration_s))
    return candidates


def _pause_position(
    *,
    start_s: float,
    end_s: float,
    first_speech_start_s: float,
    last_speech_end_s: float,
) -> PausePosition:
    if end_s <= first_speech_start_s:
        return PausePosition.LEADING
    if start_s >= last_speech_end_s:
        return PausePosition.TRAILING
    return PausePosition.INTERNAL


def calculate_timing(
    script: ScriptResult,
    *,
    word_intervals: list[TimeInterval] | None,
    audio_duration_s: float,
    min_silence_s: float,
) -> TimingResult:
    syllable_count = _count_hangul_syllables(script.normalized)
    if word_intervals is None:
        raise _alignment_failure("말속도 계산에 필요한 단어 정렬이 없습니다.")
    if not isfinite(audio_duration_s) or audio_duration_s < 0:
        raise _alignment_failure("오디오 길이가 유효하지 않습니다.")
    if not isfinite(min_silence_s) or min_silence_s < 0:
        raise _alignment_failure("최소 무음 길이가 유효하지 않습니다.")

    _validate_intervals(word_intervals, audio_duration_s=audio_duration_s)
    speech_intervals = [
        interval
        for interval in word_intervals
        if interval.label not in NON_SPEECH_LABELS
    ]
    if not speech_intervals:
        return TimingResult(
            features=_build_features(
                syllable_count=syllable_count,
                utterance_duration_s=None,
                speech_duration_s=None,
            ),
            pauses=[],
        )

    first_speech = speech_intervals[0]
    last_speech = speech_intervals[-1]
    utterance_duration_s = last_speech.end_s - first_speech.start_s
    speech_duration_s = sum(
        interval.end_s - interval.start_s for interval in speech_intervals
    )
    pauses = [
        PauseSegment(
            interval=TimeInterval(label="pause", start_s=start_s, end_s=end_s),
            position=_pause_position(
                start_s=start_s,
                end_s=end_s,
                first_speech_start_s=first_speech.start_s,
                last_speech_end_s=last_speech.end_s,
            ),
        )
        for start_s, end_s in _silence_complement(
            word_intervals,
            audio_duration_s=audio_duration_s,
        )
        if end_s - start_s > min_silence_s
        or isclose(end_s - start_s, min_silence_s, rel_tol=0.0, abs_tol=1e-12)
    ]
    return TimingResult(
        features=_build_features(
            syllable_count=syllable_count,
            utterance_duration_s=utterance_duration_s,
            speech_duration_s=speech_duration_s,
        ),
        pauses=pauses,
    )
