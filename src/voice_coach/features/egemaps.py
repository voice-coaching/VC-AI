"""Strict in-memory openSMILE eGeMAPSv02 functionals adapter.

The paper baseline calls for eGeMAPS energy/loudness features. This module
only defines and validates the extraction primitive. It does not resolve
audio paths, resample waveforms, build a corpus cache, or authorize any split.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import importlib
from importlib import metadata
from math import isfinite
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray


EXPECTED_OPENSMILE_VERSION = "2.6.0"
EXPECTED_FEATURE_COUNT = 88
EXPECTED_SAMPLE_RATE_HZ = 16_000

FloatArray = NDArray[np.floating[Any]]


class EGeMAPSExtractionError(ValueError):
    """Raised when the pinned eGeMAPSv02 extraction contract is violated."""


class _FrameLike(Protocol):
    columns: Iterable[object]

    def to_numpy(self, *, dtype: object) -> object: ...


class _SmileLike(Protocol):
    def process_signal(self, signal: FloatArray, sampling_rate: int) -> _FrameLike: ...


@dataclass(frozen=True, slots=True)
class EGeMAPSFunctionals:
    """One privacy-safe utterance vector with a replayable column contract."""

    open_smile_version: str
    feature_set: str
    feature_level: str
    sample_rate_hz: int
    names: tuple[str, ...]
    values: tuple[float, ...]
    column_order_sha256: str

    def as_mapping(self) -> Mapping[str, float]:
        return dict(zip(self.names, self.values, strict=True))


def _column_order_sha256(names: tuple[str, ...]) -> str:
    payload = "\n".join(names).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_audio(samples: object, sample_rate_hz: int) -> FloatArray:
    if isinstance(sample_rate_hz, bool) or sample_rate_hz != EXPECTED_SAMPLE_RATE_HZ:
        raise EGeMAPSExtractionError(
            f"sample_rate_hz must be exactly {EXPECTED_SAMPLE_RATE_HZ}"
        )
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim != 1:
        raise EGeMAPSExtractionError("samples must be mono")
    if audio.size == 0:
        raise EGeMAPSExtractionError("samples must not be empty")
    if not np.isfinite(audio).all():
        raise EGeMAPSExtractionError("samples contain NaN or Inf")
    return audio


def _load_pinned_backend() -> tuple[_SmileLike, str]:
    try:
        version = metadata.version("opensmile")
        opensmile = importlib.import_module("opensmile")
    except (metadata.PackageNotFoundError, ImportError) as exc:
        raise EGeMAPSExtractionError(
            "opensmile is unavailable in this environment"
        ) from exc
    if version != EXPECTED_OPENSMILE_VERSION:
        raise EGeMAPSExtractionError(
            "opensmile version differs from the pinned extraction contract"
        )
    try:
        smile = opensmile.Smile(
            feature_set=opensmile.FeatureSet.eGeMAPSv02,
            feature_level=opensmile.FeatureLevel.Functionals,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise EGeMAPSExtractionError(
            "pinned eGeMAPSv02 functionals backend is unavailable"
        ) from exc
    return smile, version


def _parse_frame(frame: _FrameLike) -> tuple[tuple[str, ...], tuple[float, ...]]:
    try:
        names = tuple(str(name) for name in frame.columns)
        values = np.asarray(frame.to_numpy(dtype=np.float64), dtype=np.float64)
    except (AttributeError, TypeError, ValueError) as exc:
        raise EGeMAPSExtractionError(
            "openSMILE returned an invalid result frame"
        ) from exc
    if values.shape != (1, EXPECTED_FEATURE_COUNT):
        raise EGeMAPSExtractionError(
            "eGeMAPSv02 functionals must contain exactly one row and 88 columns"
        )
    if len(names) != EXPECTED_FEATURE_COUNT:
        raise EGeMAPSExtractionError(
            "eGeMAPSv02 column count differs from the pinned contract"
        )
    if any(not name or "\n" in name or "\r" in name for name in names):
        raise EGeMAPSExtractionError("eGeMAPSv02 column names are invalid")
    if len(set(names)) != len(names):
        raise EGeMAPSExtractionError("eGeMAPSv02 column names are not unique")
    flattened = tuple(float(value) for value in values[0])
    if not all(isfinite(value) for value in flattened):
        raise EGeMAPSExtractionError("eGeMAPSv02 values contain NaN or Inf")
    return names, flattened


def extract_egemaps_functionals(
    samples: object,
    sample_rate_hz: int,
    *,
    backend: _SmileLike | None = None,
    backend_version: str | None = None,
) -> EGeMAPSFunctionals:
    """Extract one exact eGeMAPSv02 Functionals vector from in-memory mono PCM.

    ``backend`` and ``backend_version`` are jointly injectable for deterministic
    contract tests. Production calls omit both and load openSMILE 2.6.0.
    """

    audio = _validate_audio(samples, sample_rate_hz)
    if (backend is None) != (backend_version is None):
        raise EGeMAPSExtractionError(
            "backend and backend_version must be provided together"
        )
    if backend is None:
        backend, version = _load_pinned_backend()
    else:
        version = str(backend_version)
        if version != EXPECTED_OPENSMILE_VERSION:
            raise EGeMAPSExtractionError(
                "backend version differs from the pinned extraction contract"
            )
    try:
        frame = backend.process_signal(audio, sample_rate_hz)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise EGeMAPSExtractionError("openSMILE extraction failed") from exc
    names, values = _parse_frame(frame)
    return EGeMAPSFunctionals(
        open_smile_version=version,
        feature_set="eGeMAPSv02",
        feature_level="Functionals",
        sample_rate_hz=sample_rate_hz,
        names=names,
        values=values,
        column_order_sha256=_column_order_sha256(names),
    )


__all__ = [
    "EGeMAPSExtractionError",
    "EGeMAPSFunctionals",
    "EXPECTED_FEATURE_COUNT",
    "EXPECTED_OPENSMILE_VERSION",
    "EXPECTED_SAMPLE_RATE_HZ",
    "extract_egemaps_functionals",
]
