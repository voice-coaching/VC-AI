from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log2

import numpy as np

from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .prosody import ProsodyResult
from .schemas import AcousticFeature, FeaturePoint, TimeInterval
from .timing import PausePosition, TimingResult


NON_SPEECH_LABELS = {"", "sil", "sp", "noise"}
PAUSE_METHOD = "timing_internal_pauses_v1"
SPEECH_F0_RATIO_METHOD = "speech_f0_valid_frames_v1"
SEMITONE_METHOD = "speech_f0_semitone_relative_median_v1"
SEMITONE_PERCENTILE_METHOD = "speech_f0_semitone_p90_p10_v1"
SPEECH_INTENSITY_METHOD = "speech_relative_intensity_median_centered_v1"
INTENSITY_PERCENTILE_METHOD = "speech_intensity_p90_p10_v1"


@dataclass(frozen=True)
class RobustMetricsResult:
    features: tuple[AcousticFeature, ...]


def _failure(exc: Exception) -> PipelineFailure:
    return PipelineFailure(
        PipelineError(
            code=ErrorCode.ACOUSTIC_ANALYSIS_FAILED,
            stage=PipelineStage.ACOUSTIC,
            message_ko="강건한 음향 비교 지표 계산에 실패했습니다.",
            details={"exception_type": type(exc).__name__},
        )
    )


def _value_feature(
    name: str,
    unit: str,
    value: float,
    *,
    method: str,
) -> AcousticFeature:
    return AcousticFeature(
        name=name,
        unit=unit,
        method=method,
        summary={"value": value},
    )


def _summary(values: list[float]) -> dict[str, float | None]:
    minimum = min(values)
    maximum = max(values)
    return {
        "median": float(np.median(values)),
        "min": minimum,
        "max": maximum,
        "range": maximum - minimum,
    }


def _find_feature(
    prosody: ProsodyResult,
    name: str,
) -> AcousticFeature | None:
    return next(
        (feature for feature in prosody.features if feature.name == name),
        None,
    )


def _speech_union(
    word_intervals: list[TimeInterval],
) -> tuple[tuple[float, float], ...]:
    speech = sorted(
        (
            (interval.start_s, interval.end_s)
            for interval in word_intervals
            if interval.label not in NON_SPEECH_LABELS
        ),
        key=lambda interval: (interval[0], interval[1]),
    )
    merged: list[tuple[float, float]] = []
    for start_s, end_s in speech:
        if not merged or start_s > merged[-1][1]:
            merged.append((start_s, end_s))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end_s))
    return tuple(merged)


def _in_speech(
    time_s: float,
    speech_intervals: tuple[tuple[float, float], ...],
) -> bool:
    return any(start_s <= time_s < end_s for start_s, end_s in speech_intervals)


def _internal_pause_features(timing: TimingResult) -> list[AcousticFeature]:
    internal = [
        pause for pause in timing.pauses if pause.position is PausePosition.INTERNAL
    ]
    duration_s = sum(
        pause.interval.end_s - pause.interval.start_s for pause in internal
    )
    return [
        _value_feature(
            "internal_pause_count",
            "count",
            float(len(internal)),
            method=PAUSE_METHOD,
        ),
        _value_feature(
            "internal_pause_total_duration",
            "s",
            duration_s,
            method=PAUSE_METHOD,
        ),
    ]


def _f0_features(
    feature: AcousticFeature,
    speech_intervals: tuple[tuple[float, float], ...],
) -> list[AcousticFeature]:
    speech_points = [
        point for point in feature.points if _in_speech(point.time_s, speech_intervals)
    ]
    if not speech_points:
        return []
    valid_points = [
        point
        for point in speech_points
        if point.value is not None and isfinite(point.value) and point.value > 0.0
    ]
    ratio = len(valid_points) / len(speech_points)
    features = [
        _value_feature(
            "speech_f0_valid_ratio",
            "ratio",
            ratio,
            method=SPEECH_F0_RATIO_METHOD,
        )
    ]
    if not valid_points:
        return features

    values = [point.value for point in valid_points if point.value is not None]
    median = float(np.median(values))
    semitone_values = [12.0 * log2(value / median) for value in values]
    semitone_points = [
        FeaturePoint(time_s=point.time_s, value=value)
        for point, value in zip(valid_points, semitone_values, strict=True)
    ]
    features.extend(
        [
            AcousticFeature(
                name="f0_semitone_relative_median",
                unit="semitone",
                method=SEMITONE_METHOD,
                points=semitone_points,
                summary=_summary(semitone_values),
            ),
            _value_feature(
                "f0_semitone_percentile_range",
                "semitone",
                float(np.percentile(semitone_values, 90))
                - float(np.percentile(semitone_values, 10)),
                method=SEMITONE_PERCENTILE_METHOD,
            ),
        ]
    )
    return features


def _intensity_features(
    feature: AcousticFeature,
    speech_intervals: tuple[tuple[float, float], ...],
) -> list[AcousticFeature]:
    speech_points = [
        point
        for point in feature.points
        if _in_speech(point.time_s, speech_intervals)
        and point.value is not None
        and isfinite(point.value)
    ]
    if not speech_points:
        return []
    values = [point.value for point in speech_points if point.value is not None]
    center = float(np.median(values))
    relative_values = [value - center for value in values]
    relative_points = [
        FeaturePoint(time_s=point.time_s, value=value)
        for point, value in zip(speech_points, relative_values, strict=True)
    ]
    return [
        AcousticFeature(
            name="speech_relative_intensity_db",
            unit="dB",
            method=SPEECH_INTENSITY_METHOD,
            points=relative_points,
            summary=_summary(relative_values),
        ),
        _value_feature(
            "speech_intensity_percentile_range",
            "dB",
            float(np.percentile(relative_values, 90))
            - float(np.percentile(relative_values, 10)),
            method=INTENSITY_PERCENTILE_METHOD,
        ),
    ]


def calculate_robust_metrics(
    timing: TimingResult,
    *,
    word_intervals: list[TimeInterval],
    prosody: ProsodyResult | None,
) -> RobustMetricsResult:
    try:
        features = _internal_pause_features(timing)
        speech_intervals = _speech_union(word_intervals)
        if not speech_intervals or prosody is None:
            return RobustMetricsResult(features=tuple(features))

        f0 = _find_feature(prosody, "f0_hz")
        if f0 is not None:
            features.extend(_f0_features(f0, speech_intervals))
        intensity = _find_feature(prosody, "relative_intensity_db")
        if intensity is not None:
            features.extend(_intensity_features(intensity, speech_intervals))
        return RobustMetricsResult(features=tuple(features))
    except PipelineFailure:
        raise
    except Exception as exc:
        raise _failure(exc) from exc
