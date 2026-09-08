"""Dependency-light acoustic features for the interpretable baseline."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.floating]


@dataclass(frozen=True)
class ExplicitFeatures:
    duration_sec: float
    rms: float
    peak: float
    clipping_ratio: float
    zero_crossing_rate: float
    pause_count: int
    pause_ratio: float
    voiced_ratio: float
    f0_median_hz: float | None
    f0_range_hz: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return asdict(self)


def _validate_audio(samples: FloatArray, sample_rate: int) -> FloatArray:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    audio = np.asarray(samples, dtype=np.float64)
    if audio.ndim != 1:
        raise ValueError("samples must be mono")
    if audio.size == 0:
        raise ValueError("samples must not be empty")
    if not np.isfinite(audio).all():
        raise ValueError("samples contain NaN or Inf")
    return audio


def _frames(
    audio: FloatArray,
    sample_rate: int,
    frame_ms: float = 25.0,
    hop_ms: float = 10.0,
) -> FloatArray:
    frame_size = max(1, round(sample_rate * frame_ms / 1000))
    hop_size = max(1, round(sample_rate * hop_ms / 1000))
    if audio.size < frame_size:
        audio = np.pad(audio, (0, frame_size - audio.size))
    count = 1 + (audio.size - frame_size) // hop_size
    shape = (count, frame_size)
    strides = (audio.strides[0] * hop_size, audio.strides[0])
    return np.lib.stride_tricks.as_strided(audio, shape=shape, strides=strides).copy()


def _run_count(mask: NDArray[np.bool_], minimum_frames: int) -> int:
    padded = np.pad(mask.astype(np.int8), (1, 1))
    changes = np.diff(padded)
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    return int(np.sum((ends - starts) >= minimum_frames))


def _estimate_f0(
    frames: FloatArray,
    sample_rate: int,
    voiced_mask: NDArray[np.bool_],
    f0_min_hz: float = 60.0,
    f0_max_hz: float = 500.0,
) -> FloatArray:
    min_lag = max(1, int(sample_rate / f0_max_hz))
    max_lag = min(frames.shape[1] - 1, int(sample_rate / f0_min_hz))
    estimates: list[float] = []
    window = np.hanning(frames.shape[1])

    for frame in frames[voiced_mask]:
        centered = (frame - np.mean(frame)) * window
        correlation = np.correlate(centered, centered, mode="full")[len(centered) - 1 :]
        if correlation[0] <= 0:
            continue
        search = correlation[min_lag : max_lag + 1]
        lag = min_lag + int(np.argmax(search))
        if correlation[lag] / correlation[0] < 0.3:
            continue
        estimates.append(sample_rate / lag)
    return np.asarray(estimates, dtype=np.float64)


def extract_explicit_features(
    samples: FloatArray,
    sample_rate: int,
    clipping_threshold: float = 0.999,
    silence_db_below_peak: float = 40.0,
    minimum_pause_ms: float = 100.0,
    *,
    compute_f0: bool = True,
) -> ExplicitFeatures:
    audio = _validate_audio(samples, sample_rate)
    frames = _frames(audio, sample_rate)
    frame_rms = np.sqrt(np.mean(np.square(frames), axis=1))
    peak_frame_rms = float(np.max(frame_rms))
    silence_threshold = peak_frame_rms * 10 ** (-silence_db_below_peak / 20)
    silence_mask = frame_rms <= max(silence_threshold, np.finfo(float).eps)
    minimum_pause_frames = max(1, round(minimum_pause_ms / 10.0))

    # A relative energy gate makes voiced/F0 estimates invariant to global gain.
    voiced_threshold = peak_frame_rms * 0.1
    voiced_mask = frame_rms > max(voiced_threshold, np.finfo(float).eps)
    f0 = (
        _estimate_f0(frames, sample_rate, voiced_mask)
        if compute_f0
        else np.asarray([], dtype=np.float64)
    )
    signs = np.signbit(audio)

    return ExplicitFeatures(
        duration_sec=audio.size / sample_rate,
        rms=float(np.sqrt(np.mean(np.square(audio)))),
        peak=float(np.max(np.abs(audio))),
        clipping_ratio=float(np.mean(np.abs(audio) >= clipping_threshold)),
        zero_crossing_rate=float(np.mean(signs[1:] != signs[:-1])) if audio.size > 1 else 0.0,
        pause_count=_run_count(silence_mask, minimum_pause_frames),
        pause_ratio=float(np.mean(silence_mask)),
        voiced_ratio=float(np.mean(voiced_mask)),
        f0_median_hz=float(np.median(f0)) if f0.size else None,
        f0_range_hz=float(np.percentile(f0, 95) - np.percentile(f0, 5))
        if f0.size
        else None,
    )


def npvi(durations: FloatArray) -> float | None:
    values = np.asarray(durations, dtype=np.float64)
    if values.ndim != 1 or values.size < 2:
        return None
    if not np.isfinite(values).all() or np.any(values <= 0):
        raise ValueError("durations must contain finite positive values")
    left = values[:-1]
    right = values[1:]
    return float(100 * np.mean(np.abs(right - left) / ((right + left) / 2)))
