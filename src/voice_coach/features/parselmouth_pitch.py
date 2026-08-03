"""Strict in-memory Parselmouth pitch-contour extraction.

This adapter fixes only the Phase 1 pitch extraction boundary. It does not
resolve audio paths, resample waveforms, select corpus rows, write caches, or
choose scientific pitch parameters for the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
from importlib import metadata
import json
from math import isfinite
from numbers import Integral, Real
from typing import Literal, Protocol, cast

import numpy as np
from numpy.typing import NDArray


EXPECTED_PARSELMOUTH_VERSION = "0.4.7"
EXPECTED_SAMPLE_RATE_HZ = 16_000

FloatArray = NDArray[np.float64]
UnvoicedPolicy = Literal["keep_zero", "drop"]


class ParselmouthPitchExtractionError(ValueError):
    """Raised when the pinned in-memory pitch contract is violated."""


class _SelectedArrayLike(Protocol):
    def __getitem__(self, key: str) -> object: ...


class _PitchLike(Protocol):
    @property
    def selected_array(self) -> _SelectedArrayLike: ...

    def xs(self) -> object: ...


class _SoundLike(Protocol):
    def to_pitch(
        self,
        *,
        time_step: float,
        pitch_floor: float,
        pitch_ceiling: float,
    ) -> _PitchLike: ...


class _ParselmouthLike(Protocol):
    def Sound(
        self,
        values: FloatArray,
        *,
        sampling_frequency: float,
    ) -> _SoundLike: ...


@dataclass(frozen=True, slots=True)
class ParselmouthPitchContour:
    """A replayable contour and voiced summary without source identifiers."""

    parselmouth_version: str
    sample_rate_hz: int
    time_step_seconds: float
    pitch_floor_hz: float
    pitch_ceiling_hz: float
    unvoiced_policy: UnvoicedPolicy
    time_seconds: tuple[float, ...]
    f0_hz: tuple[float, ...]
    backend_frame_count: int
    returned_frame_count: int
    voiced_frame_count: int
    unvoiced_frame_count: int
    voiced_median_hz: float
    voiced_range_hz: float
    contour_sha256: str


def _validate_audio(samples: object, sample_rate_hz: int) -> FloatArray:
    if (
        isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, Integral)
        or int(sample_rate_hz) != EXPECTED_SAMPLE_RATE_HZ
    ):
        raise ParselmouthPitchExtractionError(
            f"sample_rate_hz must be the integer {EXPECTED_SAMPLE_RATE_HZ}"
        )
    try:
        untyped_audio = np.asarray(samples)
    except (TypeError, ValueError) as exc:
        raise ParselmouthPitchExtractionError(
            "samples must be a real numeric PCM array"
        ) from exc
    if untyped_audio.ndim != 1:
        raise ParselmouthPitchExtractionError("samples must be mono")
    if untyped_audio.size == 0:
        raise ParselmouthPitchExtractionError("samples must not be empty")
    if untyped_audio.dtype.kind not in {"f", "i", "u"}:
        raise ParselmouthPitchExtractionError("samples must be real numeric PCM")
    audio = np.asarray(untyped_audio, dtype=np.float64)
    if not np.isfinite(audio).all():
        raise ParselmouthPitchExtractionError("samples contain NaN or Inf")
    return audio


def _positive_finite_scalar(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ParselmouthPitchExtractionError(
            f"{field_name} must be a finite positive real number"
        )
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0.0:
        raise ParselmouthPitchExtractionError(
            f"{field_name} must be a finite positive real number"
        )
    return normalized


def _validate_parameters(
    *,
    time_step_seconds: object,
    pitch_floor_hz: object,
    pitch_ceiling_hz: object,
    unvoiced_policy: object,
) -> tuple[float, float, float, UnvoicedPolicy]:
    time_step = _positive_finite_scalar(time_step_seconds, "time_step_seconds")
    pitch_floor = _positive_finite_scalar(pitch_floor_hz, "pitch_floor_hz")
    pitch_ceiling = _positive_finite_scalar(pitch_ceiling_hz, "pitch_ceiling_hz")
    if pitch_ceiling <= pitch_floor:
        raise ParselmouthPitchExtractionError(
            "pitch_ceiling_hz must be greater than pitch_floor_hz"
        )
    if not isinstance(unvoiced_policy, str) or unvoiced_policy not in {
        "keep_zero",
        "drop",
    }:
        raise ParselmouthPitchExtractionError(
            "unvoiced_policy must be exactly 'keep_zero' or 'drop'"
        )
    return (
        time_step,
        pitch_floor,
        pitch_ceiling,
        cast(UnvoicedPolicy, unvoiced_policy),
    )


def _load_pinned_backend() -> tuple[_ParselmouthLike, str]:
    try:
        distribution_version = metadata.version("praat-parselmouth")
        parselmouth = importlib.import_module("parselmouth")
    except (metadata.PackageNotFoundError, ImportError) as exc:
        raise ParselmouthPitchExtractionError(
            "praat-parselmouth is unavailable in this environment"
        ) from exc
    module_version = getattr(parselmouth, "__version__", None)
    if (
        distribution_version != EXPECTED_PARSELMOUTH_VERSION
        or module_version != EXPECTED_PARSELMOUTH_VERSION
    ):
        raise ParselmouthPitchExtractionError(
            "Parselmouth version differs from the pinned extraction contract"
        )
    return parselmouth, distribution_version


def _read_backend_contour(
    pitch: _PitchLike,
) -> tuple[FloatArray, FloatArray]:
    try:
        times = np.asarray(pitch.xs(), dtype=np.float64)
        frequencies = np.asarray(
            pitch.selected_array["frequency"],
            dtype=np.float64,
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise ParselmouthPitchExtractionError(
            "Parselmouth returned an invalid pitch result"
        ) from exc
    if times.ndim != 1 or frequencies.ndim != 1:
        raise ParselmouthPitchExtractionError(
            "Parselmouth time and frequency contours must be one-dimensional"
        )
    if times.size == 0 or frequencies.size == 0:
        raise ParselmouthPitchExtractionError(
            "Parselmouth returned an empty pitch contour"
        )
    if times.shape != frequencies.shape:
        raise ParselmouthPitchExtractionError(
            "Parselmouth time and frequency contour shapes differ"
        )
    if not np.isfinite(times).all() or not np.isfinite(frequencies).all():
        raise ParselmouthPitchExtractionError(
            "Parselmouth pitch contour contains NaN or Inf"
        )
    if np.any(times < 0.0) or (times.size > 1 and not np.all(np.diff(times) > 0.0)):
        raise ParselmouthPitchExtractionError(
            "Parselmouth time grid must be non-negative and strictly increasing"
        )
    if np.any(frequencies < 0.0):
        raise ParselmouthPitchExtractionError(
            "Parselmouth frequencies must use zero for unvoiced frames"
        )
    return times, frequencies


def _validate_voiced_frequencies(
    frequencies: FloatArray,
    *,
    pitch_floor_hz: float,
    pitch_ceiling_hz: float,
) -> NDArray[np.bool_]:
    voiced_mask = frequencies > 0.0
    if not np.any(voiced_mask):
        raise ParselmouthPitchExtractionError(
            "Parselmouth pitch contour contains no voiced frames"
        )
    voiced_frequencies = frequencies[voiced_mask]
    if np.any(voiced_frequencies < pitch_floor_hz) or np.any(
        voiced_frequencies > pitch_ceiling_hz
    ):
        raise ParselmouthPitchExtractionError(
            "voiced frequencies fall outside the caller-provided pitch bounds"
        )
    return voiced_mask


def _contour_digest(
    *,
    parselmouth_version: str,
    sample_rate_hz: int,
    time_step_seconds: float,
    pitch_floor_hz: float,
    pitch_ceiling_hz: float,
    unvoiced_policy: UnvoicedPolicy,
    time_seconds: FloatArray,
    f0_hz: FloatArray,
) -> str:
    contract = {
        "parselmouth_version": parselmouth_version,
        "pitch_ceiling_hz": pitch_ceiling_hz,
        "pitch_floor_hz": pitch_floor_hz,
        "sample_rate_hz": sample_rate_hz,
        "time_step_seconds": time_step_seconds,
        "unvoiced_policy": unvoiced_policy,
    }
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            contract,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    digest.update(b"\x00time_seconds\x00")
    digest.update(np.asarray(time_seconds, dtype="<f8").tobytes(order="C"))
    digest.update(b"\x00f0_hz\x00")
    digest.update(np.asarray(f0_hz, dtype="<f8").tobytes(order="C"))
    return digest.hexdigest()


def extract_parselmouth_pitch_contour(
    samples: object,
    sample_rate_hz: int,
    *,
    time_step_seconds: float,
    pitch_floor_hz: float,
    pitch_ceiling_hz: float,
    unvoiced_policy: UnvoicedPolicy,
    backend: _ParselmouthLike | None = None,
    backend_version: str | None = None,
) -> ParselmouthPitchContour:
    """Extract a caller-parameterized Parselmouth F0 contour from mono PCM.

    ``backend`` and ``backend_version`` are jointly injectable for deterministic
    contract tests. Production calls omit both and lazily load
    ``praat-parselmouth==0.4.7``.
    """

    audio = _validate_audio(samples, sample_rate_hz)
    time_step, pitch_floor, pitch_ceiling, policy = _validate_parameters(
        time_step_seconds=time_step_seconds,
        pitch_floor_hz=pitch_floor_hz,
        pitch_ceiling_hz=pitch_ceiling_hz,
        unvoiced_policy=unvoiced_policy,
    )
    if (backend is None) != (backend_version is None):
        raise ParselmouthPitchExtractionError(
            "backend and backend_version must be provided together"
        )
    if backend is None:
        backend, version = _load_pinned_backend()
    else:
        version = str(backend_version)
        if version != EXPECTED_PARSELMOUTH_VERSION:
            raise ParselmouthPitchExtractionError(
                "backend version differs from the pinned extraction contract"
            )

    try:
        sound = backend.Sound(
            audio,
            sampling_frequency=float(EXPECTED_SAMPLE_RATE_HZ),
        )
        pitch = sound.to_pitch(
            time_step=time_step,
            pitch_floor=pitch_floor,
            pitch_ceiling=pitch_ceiling,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ParselmouthPitchExtractionError(
            "Parselmouth pitch extraction failed"
        ) from exc

    backend_times, backend_frequencies = _read_backend_contour(pitch)
    voiced_mask = _validate_voiced_frequencies(
        backend_frequencies,
        pitch_floor_hz=pitch_floor,
        pitch_ceiling_hz=pitch_ceiling,
    )
    canonical_frequencies = np.where(
        voiced_mask,
        backend_frequencies,
        0.0,
    ).astype(np.float64, copy=False)
    if policy == "drop":
        returned_times = backend_times[voiced_mask]
        returned_frequencies = canonical_frequencies[voiced_mask]
    else:
        returned_times = backend_times
        returned_frequencies = canonical_frequencies

    voiced_frequencies = canonical_frequencies[voiced_mask]
    backend_frame_count = int(backend_times.size)
    voiced_frame_count = int(voiced_frequencies.size)
    returned_frame_count = int(returned_times.size)
    unvoiced_frame_count = backend_frame_count - voiced_frame_count
    voiced_median_hz = float(np.median(voiced_frequencies))
    voiced_range_hz = float(np.max(voiced_frequencies) - np.min(voiced_frequencies))
    contour_sha256 = _contour_digest(
        parselmouth_version=version,
        sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ,
        time_step_seconds=time_step,
        pitch_floor_hz=pitch_floor,
        pitch_ceiling_hz=pitch_ceiling,
        unvoiced_policy=policy,
        time_seconds=returned_times,
        f0_hz=returned_frequencies,
    )
    return ParselmouthPitchContour(
        parselmouth_version=version,
        sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ,
        time_step_seconds=time_step,
        pitch_floor_hz=pitch_floor,
        pitch_ceiling_hz=pitch_ceiling,
        unvoiced_policy=policy,
        time_seconds=tuple(float(value) for value in returned_times),
        f0_hz=tuple(float(value) for value in returned_frequencies),
        backend_frame_count=backend_frame_count,
        returned_frame_count=returned_frame_count,
        voiced_frame_count=voiced_frame_count,
        unvoiced_frame_count=unvoiced_frame_count,
        voiced_median_hz=voiced_median_hz,
        voiced_range_hz=voiced_range_hz,
        contour_sha256=contour_sha256,
    )


__all__ = [
    "EXPECTED_PARSELMOUTH_VERSION",
    "EXPECTED_SAMPLE_RATE_HZ",
    "ParselmouthPitchContour",
    "ParselmouthPitchExtractionError",
    "UnvoicedPolicy",
    "extract_parselmouth_pitch_contour",
]
