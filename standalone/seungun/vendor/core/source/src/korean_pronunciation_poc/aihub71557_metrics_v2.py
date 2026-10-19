from __future__ import annotations

import math
import re
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


METRIC_VERSION: Literal["aihub71557_compatible_metrics_v2"] = (
    "aihub71557_compatible_metrics_v2"
)
_HANGUL = re.compile(r"[가-힣]")
_FRAME_S = 0.025
_HOP_S = 0.010
_INTERNAL_PAUSE_FRAMES = 10


class Aihub71557MetricsV2Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_version: Literal["aihub71557_compatible_metrics_v2"] = METRIC_VERSION
    speech_rate: float | None = Field(default=None, allow_inf_nan=False)
    articulation_rate: float | None = Field(default=None, allow_inf_nan=False)
    pause_count_per_10_syllables: float | None = Field(
        default=None,
        allow_inf_nan=False,
    )
    pause_duration_per_10_syllables: float | None = Field(
        default=None,
        allow_inf_nan=False,
    )
    f0_median_hz: float | None = Field(default=None, allow_inf_nan=False)
    f0_range_st: float | None = Field(default=None, allow_inf_nan=False)
    f0_coverage: float | None = Field(default=None, allow_inf_nan=False)
    intensity_range_db: float | None = Field(default=None, allow_inf_nan=False)
    status: str = Field(min_length=1)


def _load_parselmouth() -> Any:
    return import_module("parselmouth")


def frame_audio(audio: np.ndarray, sample_rate_hz: int) -> np.ndarray:
    frame_size = max(1, round(sample_rate_hz * _FRAME_S))
    hop_size = max(1, round(sample_rate_hz * _HOP_S))
    if len(audio) < frame_size:
        audio = np.pad(audio, (0, frame_size - len(audio)))
    count = 1 + (len(audio) - frame_size) // hop_size
    return np.lib.stride_tricks.as_strided(
        audio,
        shape=(count, frame_size),
        strides=(audio.strides[0] * hop_size, audio.strides[0]),
    ).copy()


def internal_pause_stats(silence_mask: np.ndarray) -> tuple[int, float]:
    padded = np.pad(silence_mask.astype(np.int8), (1, 1))
    changes = np.diff(padded)
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    count = 0
    frames = 0
    for start, end in zip(starts, ends, strict=True):
        length = end - start
        if length < _INTERNAL_PAUSE_FRAMES:
            continue
        if start == 0 or end == len(silence_mask):
            continue
        count += 1
        frames += int(length)
    return count, frames * _HOP_S


def _error_result(exc: Exception) -> Aihub71557MetricsV2Result:
    return Aihub71557MetricsV2Result(
        status=f"error:{type(exc).__name__}:{exc}",
    )


def calculate_aihub71557_metrics_v2(
    wav_path: Path,
    *,
    analysis_start_s: float,
    analysis_end_s: float,
    text: str,
) -> Aihub71557MetricsV2Result:
    """Reproduce the 71557 reference extractor for an explicit audio window."""
    try:
        syllables = len(_HANGUL.findall(text))
        if syllables < 5:
            raise ValueError("too_few_syllables")

        parselmouth = _load_parselmouth()
        sound = parselmouth.Sound(str(wav_path))
        start_s = max(0.0, analysis_start_s)
        end_s = min(float(sound.duration), analysis_end_s)
        if end_s <= start_s:
            raise ValueError("invalid_interval")
        window = sound.extract_part(
            from_time=start_s,
            to_time=end_s,
            preserve_times=False,
        )
        duration_s = float(window.duration)

        audio = np.asarray(window.values, dtype=np.float64)
        if audio.ndim == 2:
            audio = np.mean(audio, axis=0)
        sample_rate_hz = int(round(window.sampling_frequency))
        frames = frame_audio(audio, sample_rate_hz)
        frame_rms = np.sqrt(np.mean(np.square(frames), axis=1))
        peak_rms = float(np.max(frame_rms))
        silence_threshold = peak_rms * 10 ** (-40.0 / 20.0)
        silence = frame_rms <= max(silence_threshold, np.finfo(float).eps)
        pause_count, pause_duration_s = internal_pause_stats(silence)
        speech_time_s = duration_s - pause_duration_s

        pitch = window.to_pitch_ac(
            time_step=0.01,
            pitch_floor=60.0,
            pitch_ceiling=600.0,
        )
        f0_all = np.asarray(pitch.selected_array["frequency"], dtype=float)
        voiced = f0_all[np.isfinite(f0_all) & (f0_all > 0.0)]
        f0_median_hz: float | None = None
        f0_range_st: float | None = None
        if len(voiced) >= 5:
            p10, p50, p90 = np.percentile(voiced, [10, 50, 90])
            f0_median_hz = float(p50)
            if p10 > 0.0:
                f0_range_st = float(12.0 * math.log2(p90 / p10))
        f0_coverage = float(len(voiced) / len(f0_all)) if len(f0_all) else None

        active_rms = frame_rms[(~silence) & np.isfinite(frame_rms) & (frame_rms > 0)]
        intensity_range_db: float | None = None
        if len(active_rms) >= 5:
            active_db = 20.0 * np.log10(active_rms)
            p10, p90 = np.percentile(active_db, [10, 90])
            intensity_range_db = float(p90 - p10)

        return Aihub71557MetricsV2Result(
            speech_rate=syllables / duration_s,
            articulation_rate=(
                syllables / speech_time_s if speech_time_s > 0.0 else None
            ),
            pause_count_per_10_syllables=pause_count * 10.0 / syllables,
            pause_duration_per_10_syllables=pause_duration_s * 10.0 / syllables,
            f0_median_hz=f0_median_hz,
            f0_range_st=f0_range_st,
            f0_coverage=f0_coverage,
            intensity_range_db=intensity_range_db,
            status="ok",
        )
    except Exception as exc:
        return _error_result(exc)
