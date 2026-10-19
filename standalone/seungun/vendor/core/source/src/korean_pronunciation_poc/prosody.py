from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from math import isfinite
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import JsonValue

from .config import AcousticSettings
from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .schemas import AcousticFeature, FeaturePoint


PRAAT_SILENCE_FLOOR_DB = -300.0


@dataclass(frozen=True)
class ProsodyResult:
    features: list[AcousticFeature]


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


def _load_parselmouth() -> Any:
    return import_module("parselmouth")


def _summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "min": None, "max": None, "range": None}
    minimum = min(values)
    maximum = max(values)
    return {
        "median": float(np.median(values)),
        "min": minimum,
        "max": maximum,
        "range": maximum - minimum,
    }


def _contour_feature(
    *,
    name: str,
    unit: str,
    method: str,
    times: np.ndarray[Any, Any],
    values: np.ndarray[Any, Any],
    valid: np.ndarray[Any, Any],
) -> AcousticFeature:
    if times.size != values.size or values.size != valid.size:
        raise ValueError("acoustic contour arrays must have the same length")
    points = [
        FeaturePoint(
            time_s=float(time_s),
            value=float(value) if bool(is_valid) else None,
        )
        for time_s, value, is_valid in zip(times, values, valid, strict=True)
    ]
    valid_values = [
        float(value) for value, is_valid in zip(values, valid, strict=True) if is_valid
    ]
    return AcousticFeature(
        name=name,
        unit=unit,
        method=method,
        points=points,
        summary=_summary(valid_values),
    )


def _ratio_feature(name: str, valid: np.ndarray[Any, Any]) -> AcousticFeature:
    ratio = float(np.count_nonzero(valid) / valid.size) if valid.size else 0.0
    return AcousticFeature(
        name=name,
        unit="ratio",
        method="valid_frames_v1",
        summary={"value": ratio},
    )


def extract_prosody(path: Path, settings: AcousticSettings) -> ProsodyResult:
    try:
        parselmouth = _load_parselmouth()
    except (ImportError, ModuleNotFoundError) as exc:
        raise _failure(
            ErrorCode.DEPENDENCY_MISSING,
            PipelineStage.DEPENDENCY,
            "Parselmouth 의존성을 불러올 수 없습니다.",
            dependency="parselmouth",
        ) from exc

    prepared_wav = path.expanduser().resolve(strict=False)
    if not prepared_wav.is_file():
        raise _failure(
            ErrorCode.ACOUSTIC_ANALYSIS_FAILED,
            PipelineStage.ACOUSTIC,
            "음향 분석용 WAV 파일이 존재하지 않습니다.",
        )

    try:
        sound = parselmouth.Sound(str(prepared_wav))
        pitch = sound.to_pitch_ac(
            time_step=settings.time_step_s,
            pitch_floor=settings.pitch_floor_hz,
            pitch_ceiling=settings.pitch_ceiling_hz,
        )
        raw_f0 = np.asarray(
            pitch.selected_array["frequency"], dtype=np.float64
        ).reshape(-1)
        f0_times = np.asarray(pitch.xs(), dtype=np.float64).reshape(-1)
        valid_f0 = np.isfinite(raw_f0) & (raw_f0 > 0.0)

        intensity = sound.to_intensity(
            minimum_pitch=settings.pitch_floor_hz,
            time_step=settings.time_step_s,
        )
        raw_intensity = np.asarray(intensity.values[0], dtype=np.float64).reshape(-1)
        intensity_times = np.asarray(intensity.xs(), dtype=np.float64).reshape(-1)
        valid_intensity = np.isfinite(raw_intensity) & (
            raw_intensity > PRAAT_SILENCE_FLOOR_DB
        )
        relative_intensity = np.zeros_like(raw_intensity)
        if np.any(valid_intensity):
            center = float(np.median(raw_intensity[valid_intensity]))
            relative_intensity[valid_intensity] = (
                raw_intensity[valid_intensity] - center
            )

        features = [
            _contour_feature(
                name="f0_hz",
                unit="Hz",
                method="parselmouth_to_pitch_ac_v1",
                times=f0_times,
                values=raw_f0,
                valid=valid_f0,
            ),
            _ratio_feature("f0_valid_ratio", valid_f0),
            _contour_feature(
                name="relative_intensity_db",
                unit="dB",
                method="parselmouth_to_intensity_median_centered_v1",
                times=intensity_times,
                values=relative_intensity,
                valid=valid_intensity,
            ),
            _ratio_feature("intensity_valid_ratio", valid_intensity),
        ]
        if any(
            point.value is not None and not isfinite(point.value)
            for feature in features
            for point in feature.points
        ):
            raise ValueError("acoustic contour contains a non-finite value")
        return ProsodyResult(features=features)
    except PipelineFailure:
        raise
    except Exception as exc:
        raise _failure(
            ErrorCode.ACOUSTIC_ANALYSIS_FAILED,
            PipelineStage.ACOUSTIC,
            "Parselmouth 음향 분석에 실패했습니다.",
            exception_type=type(exc).__name__,
        ) from exc
