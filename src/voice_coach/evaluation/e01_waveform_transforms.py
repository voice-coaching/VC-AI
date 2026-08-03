"""Pure in-memory waveform primitives for a future E01 executor.

Only leading/trailing silence and global gain are implemented.  The module
does not choose experimental magnitudes, tolerances, thresholds, or expected
relations; read files; load a model; use a GPU; or calculate an E01 pass rate.
Every execution-relevant value must be supplied by the caller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import hashlib
import math
from numbers import Integral, Real
from typing import Literal

import numpy as np
from numpy.typing import NDArray


FloatPCM = NDArray[np.floating]
SampleCountRounding = Literal["floor", "ceil", "half_up"]
ClippingPolicy = Literal["error", "clip"]

_ROUNDING_MODES = {
    "floor": ROUND_FLOOR,
    "ceil": ROUND_CEILING,
    "half_up": ROUND_HALF_UP,
}
_CLIPPING_POLICIES = frozenset({"error", "clip"})
_SUPPORTED_DTYPES = frozenset({np.dtype(np.float32), np.dtype(np.float64)})


class E01WaveformTransformError(ValueError):
    """Raised when a waveform or explicit transform parameter is unsafe."""


@dataclass(frozen=True, slots=True)
class SilenceTransformMetadata:
    """Privacy-minimal evidence for one in-memory silence transform."""

    transform: Literal["silence"]
    sample_rate_hz: int
    leading_silence_seconds: float
    trailing_silence_seconds: float
    sample_count_rounding: SampleCountRounding
    leading_silence_samples: int
    trailing_silence_samples: int
    peak_limit: float
    max_output_samples: int
    input_sample_count: int
    output_sample_count: int
    input_peak: float
    output_peak: float
    output_digest_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return metadata only; waveform samples are never serialized."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class GainTransformMetadata:
    """Privacy-minimal evidence for one in-memory global-gain transform."""

    transform: Literal["gain"]
    sample_rate_hz: int
    gain_db: float
    linear_gain: float
    clipping_policy: ClippingPolicy
    peak_limit: float
    max_output_samples: int
    input_sample_count: int
    output_sample_count: int
    input_peak: float
    output_peak: float
    output_digest_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return metadata only; waveform samples are never serialized."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class SilenceTransformResult:
    """A newly allocated waveform and its silence-transform metadata."""

    waveform: FloatPCM
    metadata: SilenceTransformMetadata


@dataclass(frozen=True, slots=True)
class GainTransformResult:
    """A newly allocated waveform and its global-gain metadata."""

    waveform: FloatPCM
    metadata: GainTransformMetadata


def _explicit_positive_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise E01WaveformTransformError(f"{field_name} must be a positive integer")
    integer_value = int(value)
    if integer_value <= 0:
        raise E01WaveformTransformError(f"{field_name} must be a positive integer")
    if integer_value > int(np.iinfo(np.intp).max):
        raise E01WaveformTransformError(
            f"{field_name} exceeds the platform sample-count limit"
        )
    return integer_value


def _finite_real(
    value: object,
    *,
    field_name: str,
    minimum: float | None,
    strictly_greater: bool,
) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise E01WaveformTransformError(f"{field_name} must be a finite real number")
    try:
        float_value = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise E01WaveformTransformError(
            f"{field_name} must be a finite real number"
        ) from exc
    if not math.isfinite(float_value):
        raise E01WaveformTransformError(f"{field_name} must be a finite real number")
    if minimum is not None:
        is_invalid = (
            float_value <= minimum if strictly_greater else float_value < minimum
        )
        if is_invalid:
            comparison = "greater than" if strictly_greater else "at least"
            raise E01WaveformTransformError(
                f"{field_name} must be {comparison} {minimum}"
            )
    return float_value


def _validated_waveform(
    waveform: object,
    *,
    peak_limit: object,
    max_output_samples: object,
) -> tuple[FloatPCM, float, float, int]:
    if type(waveform) is not np.ndarray:
        raise E01WaveformTransformError(
            "waveform must be an in-memory base NumPy array"
        )
    if waveform.ndim != 1:
        raise E01WaveformTransformError("waveform must be one-dimensional mono PCM")
    if waveform.size == 0:
        raise E01WaveformTransformError("waveform must not be empty")
    if waveform.dtype not in _SUPPORTED_DTYPES:
        raise E01WaveformTransformError(
            "waveform dtype must be float32 or float64 normalized PCM"
        )
    if not bool(np.isfinite(waveform).all()):
        raise E01WaveformTransformError("waveform contains NaN or Inf")

    validated_peak_limit = _finite_real(
        peak_limit,
        field_name="peak_limit",
        minimum=0.0,
        strictly_greater=True,
    )
    validated_max_output_samples = _explicit_positive_integer(
        max_output_samples,
        field_name="max_output_samples",
    )
    if waveform.size > validated_max_output_samples:
        raise E01WaveformTransformError("input sample count exceeds max_output_samples")

    input_peak = float(np.max(np.abs(waveform)))
    if input_peak > validated_peak_limit:
        raise E01WaveformTransformError(
            "input waveform peak exceeds the explicit peak_limit"
        )
    return (
        waveform,
        input_peak,
        validated_peak_limit,
        validated_max_output_samples,
    )


def _rounded_sample_count(
    seconds: object,
    *,
    field_name: str,
    sample_rate_hz: int,
    rounding_policy: SampleCountRounding,
    max_output_samples: int,
) -> int:
    validated_seconds = _finite_real(
        seconds,
        field_name=field_name,
        minimum=0.0,
        strictly_greater=False,
    )
    if not isinstance(rounding_policy, str):
        raise E01WaveformTransformError(
            "sample_count_rounding must be one of: floor, ceil, half_up"
        )
    rounding_mode = _ROUNDING_MODES.get(rounding_policy)
    if rounding_mode is None:
        raise E01WaveformTransformError(
            "sample_count_rounding must be one of: floor, ceil, half_up"
        )

    exact_count = Decimal(str(validated_seconds)) * Decimal(sample_rate_hz)
    rounded_count = exact_count.to_integral_value(rounding=rounding_mode)
    if rounded_count > max_output_samples:
        raise E01WaveformTransformError(
            f"{field_name} rounds beyond max_output_samples"
        )
    return int(rounded_count)


def _waveform_digest(waveform: FloatPCM) -> str:
    contiguous = np.ascontiguousarray(waveform)
    digest = hashlib.sha256()
    digest.update(b"voice_coach.e01.float_pcm.v1\0")
    digest.update(contiguous.dtype.str.encode("ascii"))
    digest.update(b"\0")
    digest.update(str(contiguous.size).encode("ascii"))
    digest.update(b"\0")
    digest.update(contiguous.tobytes(order="C"))
    return digest.hexdigest()


def _safe_dtype_peak_limit(dtype: np.dtype[np.floating], peak_limit: float) -> float:
    """Return the greatest target-dtype value that does not exceed the limit."""

    dtype_type = dtype.type
    candidate = dtype_type(peak_limit)
    if not bool(np.isfinite(candidate)):
        raise E01WaveformTransformError(
            "peak_limit cannot be represented by the source dtype"
        )
    if float(candidate) > peak_limit:
        candidate = np.nextafter(candidate, dtype_type(0.0))
    safe_limit = float(candidate)
    if safe_limit < 0.0 or safe_limit > peak_limit:
        raise E01WaveformTransformError(
            "peak_limit has no safe source-dtype representation"
        )
    return safe_limit


def add_leading_and_trailing_silence(
    waveform: FloatPCM,
    *,
    sample_rate_hz: int,
    leading_silence_seconds: float,
    trailing_silence_seconds: float,
    sample_count_rounding: SampleCountRounding,
    peak_limit: float,
    max_output_samples: int,
) -> SilenceTransformResult:
    """Return a copy with exact zero-valued leading and trailing PCM samples."""

    validated_sample_rate = _explicit_positive_integer(
        sample_rate_hz,
        field_name="sample_rate_hz",
    )
    (
        validated_waveform,
        input_peak,
        validated_peak_limit,
        validated_max_output_samples,
    ) = _validated_waveform(
        waveform,
        peak_limit=peak_limit,
        max_output_samples=max_output_samples,
    )
    leading_samples = _rounded_sample_count(
        leading_silence_seconds,
        field_name="leading_silence_seconds",
        sample_rate_hz=validated_sample_rate,
        rounding_policy=sample_count_rounding,
        max_output_samples=validated_max_output_samples,
    )
    trailing_samples = _rounded_sample_count(
        trailing_silence_seconds,
        field_name="trailing_silence_seconds",
        sample_rate_hz=validated_sample_rate,
        rounding_policy=sample_count_rounding,
        max_output_samples=validated_max_output_samples,
    )
    output_sample_count = (
        int(validated_waveform.size) + leading_samples + trailing_samples
    )
    if output_sample_count > validated_max_output_samples:
        raise E01WaveformTransformError(
            "silence transform output exceeds max_output_samples"
        )

    try:
        transformed = np.concatenate(
            (
                np.zeros(leading_samples, dtype=validated_waveform.dtype),
                validated_waveform,
                np.zeros(trailing_samples, dtype=validated_waveform.dtype),
            )
        )
    except (MemoryError, ValueError) as exc:
        raise E01WaveformTransformError(
            "silence transform could not allocate the validated output"
        ) from exc

    output_peak = float(np.max(np.abs(transformed)))
    return SilenceTransformResult(
        waveform=transformed,
        metadata=SilenceTransformMetadata(
            transform="silence",
            sample_rate_hz=validated_sample_rate,
            leading_silence_seconds=float(leading_silence_seconds),
            trailing_silence_seconds=float(trailing_silence_seconds),
            sample_count_rounding=sample_count_rounding,
            leading_silence_samples=leading_samples,
            trailing_silence_samples=trailing_samples,
            peak_limit=validated_peak_limit,
            max_output_samples=validated_max_output_samples,
            input_sample_count=int(validated_waveform.size),
            output_sample_count=int(transformed.size),
            input_peak=input_peak,
            output_peak=output_peak,
            output_digest_sha256=_waveform_digest(transformed),
        ),
    )


def change_global_gain_db(
    waveform: FloatPCM,
    *,
    sample_rate_hz: int,
    gain_db: float,
    clipping_policy: ClippingPolicy,
    peak_limit: float,
    max_output_samples: int,
) -> GainTransformResult:
    """Apply an explicit dB gain with fail-closed or explicit clipping."""

    validated_sample_rate = _explicit_positive_integer(
        sample_rate_hz,
        field_name="sample_rate_hz",
    )
    (
        validated_waveform,
        input_peak,
        validated_peak_limit,
        validated_max_output_samples,
    ) = _validated_waveform(
        waveform,
        peak_limit=peak_limit,
        max_output_samples=max_output_samples,
    )
    validated_gain_db = _finite_real(
        gain_db,
        field_name="gain_db",
        minimum=None,
        strictly_greater=False,
    )
    if (
        not isinstance(clipping_policy, str)
        or clipping_policy not in _CLIPPING_POLICIES
    ):
        raise E01WaveformTransformError("clipping_policy must be one of: error, clip")
    try:
        linear_gain = math.pow(10.0, validated_gain_db / 20.0)
    except OverflowError as exc:
        raise E01WaveformTransformError(
            "gain_db produces an overflowing linear gain"
        ) from exc
    if not math.isfinite(linear_gain) or linear_gain <= 0.0:
        raise E01WaveformTransformError(
            "gain_db produces a non-finite or underflowed linear gain"
        )

    try:
        with np.errstate(over="raise", invalid="raise"):
            transformed_float64 = (
                np.asarray(
                    validated_waveform,
                    dtype=np.float64,
                )
                * linear_gain
            )
    except FloatingPointError as exc:
        raise E01WaveformTransformError(
            "gain multiplication overflowed or became invalid"
        ) from exc
    if not bool(np.isfinite(transformed_float64).all()):
        raise E01WaveformTransformError("gain multiplication produced NaN or Inf")

    unbounded_peak = float(np.max(np.abs(transformed_float64)))
    safe_dtype_peak_limit = _safe_dtype_peak_limit(
        validated_waveform.dtype,
        validated_peak_limit,
    )
    if unbounded_peak > validated_peak_limit:
        if clipping_policy == "error":
            raise E01WaveformTransformError(
                "gain output exceeds peak_limit under clipping_policy=error"
            )
        transformed_float64 = np.clip(
            transformed_float64,
            -safe_dtype_peak_limit,
            safe_dtype_peak_limit,
        )
    elif clipping_policy == "clip":
        transformed_float64 = np.clip(
            transformed_float64,
            -safe_dtype_peak_limit,
            safe_dtype_peak_limit,
        )

    dtype_limit = float(np.finfo(validated_waveform.dtype).max)
    if float(np.max(np.abs(transformed_float64))) > dtype_limit:
        raise E01WaveformTransformError(
            "gain output exceeds the source dtype finite range"
        )
    with np.errstate(over="raise", invalid="raise"):
        transformed = transformed_float64.astype(
            validated_waveform.dtype,
            copy=True,
        )
    if not bool(np.isfinite(transformed).all()):
        raise E01WaveformTransformError("gain output contains NaN or Inf")
    output_peak = float(np.max(np.abs(transformed)))
    if output_peak > validated_peak_limit:
        raise E01WaveformTransformError(
            "gain output exceeds peak_limit after dtype conversion"
        )

    return GainTransformResult(
        waveform=transformed,
        metadata=GainTransformMetadata(
            transform="gain",
            sample_rate_hz=validated_sample_rate,
            gain_db=validated_gain_db,
            linear_gain=linear_gain,
            clipping_policy=clipping_policy,
            peak_limit=validated_peak_limit,
            max_output_samples=validated_max_output_samples,
            input_sample_count=int(validated_waveform.size),
            output_sample_count=int(transformed.size),
            input_peak=input_peak,
            output_peak=output_peak,
            output_digest_sha256=_waveform_digest(transformed),
        ),
    )


__all__ = [
    "ClippingPolicy",
    "E01WaveformTransformError",
    "FloatPCM",
    "GainTransformMetadata",
    "GainTransformResult",
    "SampleCountRounding",
    "SilenceTransformMetadata",
    "SilenceTransformResult",
    "add_leading_and_trailing_silence",
    "change_global_gain_db",
]
