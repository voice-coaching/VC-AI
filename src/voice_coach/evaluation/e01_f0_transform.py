"""Strict in-memory F0 scaling for a future E01 executor.

The only manipulated Praat control is a global PitchTier multiplier.  Praat's
DurationTier must remain empty, the pitch-point time grid must remain exact,
and the output must preserve the input sample rate and sample count.  The
module does not choose scientific magnitudes or tolerances; read or write
audio; use a network, GPU, or random number generator; or calculate an E01
pass rate.

PSOLA resynthesis can still introduce spectral, energy, or naturalness
changes.  Consequently, this primitive establishes a controlled nominal F0
factor and exact discrete-duration preservation, not that F0 is the only
acoustic property that changed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import import_module, metadata
import hashlib
import json
import math
from numbers import Integral, Real
from typing import Literal, Protocol, cast

import numpy as np
from numpy.typing import NDArray


EXPECTED_PARSELMOUTH_VERSION = "0.4.7"
EXPECTED_SAMPLE_RATE_HZ = 16_000
F0_SCALING_ALGORITHM_ID = "praat_manipulation_psola_overlap_add_v1"

FloatPCM = NDArray[np.floating]
Float64Array = NDArray[np.float64]
_SUPPORTED_DTYPES = frozenset({np.dtype(np.float32), np.dtype(np.float64)})


class E01F0TransformError(ValueError):
    """Raised when the controlled F0-transform contract is violated."""


class _SoundLike(Protocol):
    @property
    def values(self) -> object: ...

    @property
    def sampling_frequency(self) -> object: ...

    @property
    def xmin(self) -> object: ...

    @property
    def xmax(self) -> object: ...


class _SoundFactory(Protocol):
    def __call__(
        self,
        values: Float64Array,
        *,
        sampling_frequency: float,
    ) -> _SoundLike: ...


class _PraatCall(Protocol):
    def __call__(
        self,
        selected_objects: object,
        command: str,
        *arguments: object,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class _BackendF0ScalingOutput:
    waveform: object
    sample_rate_hz: object
    source_pitch_times_seconds: object
    source_pitch_values_hz: object
    scaled_pitch_times_seconds: object
    scaled_pitch_values_hz: object
    duration_tier_point_count_before: object
    duration_tier_point_count_after: object


class _F0ScalingBackend(Protocol):
    def scale_global_f0(
        self,
        samples: Float64Array,
        *,
        sample_rate_hz: int,
        time_step_seconds: float,
        analysis_pitch_floor_hz: float,
        analysis_pitch_ceiling_hz: float,
        f0_scale_factor: float,
    ) -> _BackendF0ScalingOutput: ...


@dataclass(frozen=True, slots=True)
class E01F0TransformMetadata:
    """Privacy-minimal evidence for one controlled in-memory F0 transform."""

    transform: Literal["f0"]
    operation: Literal["scale_global_f0"]
    algorithm_id: str
    parselmouth_version: str
    sample_rate_hz: int
    time_step_seconds: float
    analysis_pitch_floor_hz: float
    analysis_pitch_ceiling_hz: float
    f0_scale_factor: float
    allowed_scaled_pitch_floor_hz: float
    allowed_scaled_pitch_ceiling_hz: float
    minimum_pitch_tier_points: int
    peak_limit: float
    max_output_samples: int
    input_sample_count: int
    output_sample_count: int
    pitch_tier_point_count: int
    source_pitch_tier_min_hz: float
    source_pitch_tier_max_hz: float
    scaled_pitch_tier_min_hz: float
    scaled_pitch_tier_max_hz: float
    maximum_pitch_tier_scale_error_hz: float
    input_peak: float
    output_peak: float
    output_digest_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return aggregate transform metadata without retaining source PCM."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class E01F0TransformResult:
    """A newly allocated waveform and its controlled-F0 metadata."""

    waveform: FloatPCM
    metadata: E01F0TransformMetadata


class _ParselmouthPsolaBackend:
    """Narrow adapter over the exact Praat commands used by this primitive."""

    def __init__(
        self,
        *,
        sound_factory: _SoundFactory,
        praat_call: _PraatCall,
    ) -> None:
        self._sound_factory = sound_factory
        self._praat_call = praat_call

    @staticmethod
    def _integer_query(value: object, *, field_name: str) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, (Integral, Real))
            or not math.isfinite(float(value))
            or not float(value).is_integer()
        ):
            raise E01F0TransformError(f"Parselmouth returned an invalid {field_name}")
        return int(float(value))

    @staticmethod
    def _float_query(value: object, *, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise E01F0TransformError(f"Parselmouth returned an invalid {field_name}")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise E01F0TransformError(f"Parselmouth returned an invalid {field_name}")
        return normalized

    def _tier_values(
        self,
        pitch_tier: object,
        *,
        point_count: int,
    ) -> tuple[Float64Array, Float64Array]:
        times = np.empty(point_count, dtype=np.float64)
        frequencies = np.empty(point_count, dtype=np.float64)
        for point_index in range(1, point_count + 1):
            times[point_index - 1] = self._float_query(
                self._praat_call(
                    pitch_tier,
                    "Get time from index",
                    point_index,
                ),
                field_name="pitch-tier time",
            )
            frequencies[point_index - 1] = self._float_query(
                self._praat_call(
                    pitch_tier,
                    "Get value at index",
                    point_index,
                ),
                field_name="pitch-tier frequency",
            )
        return times, frequencies

    def scale_global_f0(
        self,
        samples: Float64Array,
        *,
        sample_rate_hz: int,
        time_step_seconds: float,
        analysis_pitch_floor_hz: float,
        analysis_pitch_ceiling_hz: float,
        f0_scale_factor: float,
    ) -> _BackendF0ScalingOutput:
        """Apply one PitchTier multiplier and overlap-add resynthesis."""

        sound = self._sound_factory(
            samples,
            sampling_frequency=float(sample_rate_hz),
        )
        manipulation = self._praat_call(
            sound,
            "To Manipulation",
            time_step_seconds,
            analysis_pitch_floor_hz,
            analysis_pitch_ceiling_hz,
        )
        duration_tier_before = self._praat_call(
            manipulation,
            "Extract duration tier",
        )
        duration_count_before = self._integer_query(
            self._praat_call(
                duration_tier_before,
                "Get number of points",
            ),
            field_name="duration-tier point count",
        )
        pitch_tier = self._praat_call(
            manipulation,
            "Extract pitch tier",
        )
        pitch_point_count = self._integer_query(
            self._praat_call(
                pitch_tier,
                "Get number of points",
            ),
            field_name="pitch-tier point count",
        )
        source_times, source_values = self._tier_values(
            pitch_tier,
            point_count=pitch_point_count,
        )
        sound_start_seconds = self._float_query(
            sound.xmin,
            field_name="sound start time",
        )
        sound_end_seconds = self._float_query(
            sound.xmax,
            field_name="sound end time",
        )
        self._praat_call(
            pitch_tier,
            "Multiply frequencies",
            sound_start_seconds,
            sound_end_seconds,
            f0_scale_factor,
        )
        scaled_times, scaled_values = self._tier_values(
            pitch_tier,
            point_count=pitch_point_count,
        )
        self._praat_call(
            [pitch_tier, manipulation],
            "Replace pitch tier",
        )
        duration_tier_after = self._praat_call(
            manipulation,
            "Extract duration tier",
        )
        duration_count_after = self._integer_query(
            self._praat_call(
                duration_tier_after,
                "Get number of points",
            ),
            field_name="duration-tier point count",
        )
        output_sound = cast(
            _SoundLike,
            self._praat_call(
                manipulation,
                "Get resynthesis (overlap-add)",
            ),
        )
        output_values = np.asarray(output_sound.values)
        if output_values.ndim != 2 or output_values.shape[0] != 1:
            raise E01F0TransformError(
                "Parselmouth returned invalid mono resynthesis samples"
            )
        output_waveform = np.asarray(
            output_values[0],
            dtype=np.float64,
        ).copy()
        return _BackendF0ScalingOutput(
            waveform=output_waveform,
            sample_rate_hz=output_sound.sampling_frequency,
            source_pitch_times_seconds=source_times,
            source_pitch_values_hz=source_values,
            scaled_pitch_times_seconds=scaled_times,
            scaled_pitch_values_hz=scaled_values,
            duration_tier_point_count_before=duration_count_before,
            duration_tier_point_count_after=duration_count_after,
        )


def _finite_real(
    value: object,
    *,
    field_name: str,
    minimum: float,
    maximum: float | None = None,
    strictly_greater: bool,
) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise E01F0TransformError(f"{field_name} must be a finite real number")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise E01F0TransformError(f"{field_name} must be a finite real number") from exc
    if not math.isfinite(normalized):
        raise E01F0TransformError(f"{field_name} must be a finite real number")
    violates_minimum = (
        normalized <= minimum if strictly_greater else normalized < minimum
    )
    if violates_minimum or (maximum is not None and normalized > maximum):
        qualifier = "greater than" if strictly_greater else "at least"
        message = f"{field_name} must be {qualifier} {minimum}"
        if maximum is not None:
            message += f" and at most {maximum}"
        raise E01F0TransformError(message)
    return normalized


def _positive_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise E01F0TransformError(f"{field_name} must be a positive integer")
    normalized = int(value)
    if normalized <= 0:
        raise E01F0TransformError(f"{field_name} must be a positive integer")
    if normalized > int(np.iinfo(np.intp).max):
        raise E01F0TransformError(
            f"{field_name} exceeds the platform sample-count limit"
        )
    return normalized


def _validate_input_waveform(
    waveform: object,
    *,
    sample_rate_hz: object,
    peak_limit: object,
    max_output_samples: object,
) -> tuple[FloatPCM, int, float, int, float]:
    if type(waveform) is not np.ndarray:
        raise E01F0TransformError("waveform must be an in-memory base NumPy array")
    if waveform.ndim != 1:
        raise E01F0TransformError("waveform must be one-dimensional mono PCM")
    if waveform.size == 0:
        raise E01F0TransformError("waveform must not be empty")
    if waveform.dtype not in _SUPPORTED_DTYPES:
        raise E01F0TransformError(
            "waveform dtype must be float32 or float64 normalized PCM"
        )
    if not bool(np.isfinite(waveform).all()):
        raise E01F0TransformError("waveform contains NaN or Inf")
    if (
        isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, Integral)
        or int(sample_rate_hz) != EXPECTED_SAMPLE_RATE_HZ
    ):
        raise E01F0TransformError(
            f"sample_rate_hz must be the integer {EXPECTED_SAMPLE_RATE_HZ}"
        )
    validated_peak_limit = _finite_real(
        peak_limit,
        field_name="peak_limit",
        minimum=0.0,
        maximum=1.0,
        strictly_greater=True,
    )
    validated_max_output_samples = _positive_integer(
        max_output_samples,
        field_name="max_output_samples",
    )
    if waveform.size > validated_max_output_samples:
        raise E01F0TransformError("input sample count exceeds max_output_samples")
    input_peak = float(np.max(np.abs(waveform)))
    if input_peak > validated_peak_limit:
        raise E01F0TransformError("input waveform peak exceeds the explicit peak_limit")
    return (
        waveform,
        EXPECTED_SAMPLE_RATE_HZ,
        validated_peak_limit,
        validated_max_output_samples,
        input_peak,
    )


def _validate_transform_parameters(
    *,
    time_step_seconds: object,
    analysis_pitch_floor_hz: object,
    analysis_pitch_ceiling_hz: object,
    f0_scale_factor: object,
    allowed_scaled_pitch_floor_hz: object,
    allowed_scaled_pitch_ceiling_hz: object,
    minimum_pitch_tier_points: object,
) -> tuple[float, float, float, float, float, float, int]:
    time_step = _finite_real(
        time_step_seconds,
        field_name="time_step_seconds",
        minimum=0.0,
        strictly_greater=True,
    )
    analysis_floor = _finite_real(
        analysis_pitch_floor_hz,
        field_name="analysis_pitch_floor_hz",
        minimum=0.0,
        strictly_greater=True,
    )
    analysis_ceiling = _finite_real(
        analysis_pitch_ceiling_hz,
        field_name="analysis_pitch_ceiling_hz",
        minimum=0.0,
        strictly_greater=True,
    )
    if analysis_ceiling <= analysis_floor:
        raise E01F0TransformError(
            "analysis_pitch_ceiling_hz must exceed analysis_pitch_floor_hz"
        )
    scale_factor = _finite_real(
        f0_scale_factor,
        field_name="f0_scale_factor",
        minimum=0.0,
        strictly_greater=True,
    )
    if scale_factor == 1.0:
        raise E01F0TransformError(
            "f0_scale_factor must differ from 1.0 for a perturbation"
        )
    scaled_floor = _finite_real(
        allowed_scaled_pitch_floor_hz,
        field_name="allowed_scaled_pitch_floor_hz",
        minimum=0.0,
        strictly_greater=True,
    )
    scaled_ceiling = _finite_real(
        allowed_scaled_pitch_ceiling_hz,
        field_name="allowed_scaled_pitch_ceiling_hz",
        minimum=0.0,
        strictly_greater=True,
    )
    if scaled_ceiling <= scaled_floor:
        raise E01F0TransformError(
            "allowed_scaled_pitch_ceiling_hz must exceed allowed_scaled_pitch_floor_hz"
        )
    minimum_points = _positive_integer(
        minimum_pitch_tier_points,
        field_name="minimum_pitch_tier_points",
    )
    return (
        time_step,
        analysis_floor,
        analysis_ceiling,
        scale_factor,
        scaled_floor,
        scaled_ceiling,
        minimum_points,
    )


def _load_pinned_backend() -> tuple[_F0ScalingBackend, str]:
    try:
        distribution_version = metadata.version("praat-parselmouth")
        parselmouth = import_module("parselmouth")
    except (metadata.PackageNotFoundError, ImportError) as exc:
        raise E01F0TransformError(
            "praat-parselmouth is unavailable in this environment"
        ) from exc
    module_version = getattr(parselmouth, "__version__", None)
    if (
        distribution_version != EXPECTED_PARSELMOUTH_VERSION
        or module_version != EXPECTED_PARSELMOUTH_VERSION
    ):
        raise E01F0TransformError(
            "Parselmouth version differs from the pinned F0-transform contract"
        )
    sound_factory = cast(_SoundFactory, getattr(parselmouth, "Sound", None))
    praat_module = getattr(parselmouth, "praat", None)
    praat_call = cast(_PraatCall, getattr(praat_module, "call", None))
    if not callable(sound_factory) or not callable(praat_call):
        raise E01F0TransformError(
            "Parselmouth lacks the required in-memory Praat command boundary"
        )
    return (
        _ParselmouthPsolaBackend(
            sound_factory=sound_factory,
            praat_call=praat_call,
        ),
        distribution_version,
    )


def _base_float64_vector(value: object, *, field_name: str) -> Float64Array:
    if type(value) is not np.ndarray:
        raise E01F0TransformError(f"backend {field_name} must be a base NumPy array")
    if value.ndim != 1:
        raise E01F0TransformError(f"backend {field_name} must be one-dimensional")
    if value.dtype != np.dtype(np.float64):
        raise E01F0TransformError(f"backend {field_name} must use float64")
    if not bool(np.isfinite(value).all()):
        raise E01F0TransformError(f"backend {field_name} contains NaN or Inf")
    return value


def _validate_backend_output(
    backend_output: object,
    *,
    input_sample_count: int,
    source_dtype: np.dtype[np.floating],
    sample_rate_hz: int,
    peak_limit: float,
    max_output_samples: int,
    analysis_pitch_floor_hz: float,
    analysis_pitch_ceiling_hz: float,
    f0_scale_factor: float,
    allowed_scaled_pitch_floor_hz: float,
    allowed_scaled_pitch_ceiling_hz: float,
    minimum_pitch_tier_points: int,
) -> tuple[
    FloatPCM,
    int,
    float,
    float,
    float,
    float,
    float,
    float,
]:
    if type(backend_output) is not _BackendF0ScalingOutput:
        raise E01F0TransformError("F0 backend returned an invalid result contract")
    output_sample_rate = backend_output.sample_rate_hz
    if (
        isinstance(output_sample_rate, bool)
        or not isinstance(output_sample_rate, Real)
        or not math.isfinite(float(output_sample_rate))
        or float(output_sample_rate) != float(sample_rate_hz)
    ):
        raise E01F0TransformError("F0 backend changed the sample rate")

    output_float64 = _base_float64_vector(
        backend_output.waveform,
        field_name="waveform",
    )
    if output_float64.size != input_sample_count:
        raise E01F0TransformError("F0 backend changed the discrete waveform duration")
    if output_float64.size > max_output_samples:
        raise E01F0TransformError("F0 backend exceeded max_output_samples")

    source_times = _base_float64_vector(
        backend_output.source_pitch_times_seconds,
        field_name="source pitch times",
    )
    source_values = _base_float64_vector(
        backend_output.source_pitch_values_hz,
        field_name="source pitch values",
    )
    scaled_times = _base_float64_vector(
        backend_output.scaled_pitch_times_seconds,
        field_name="scaled pitch times",
    )
    scaled_values = _base_float64_vector(
        backend_output.scaled_pitch_values_hz,
        field_name="scaled pitch values",
    )
    if (
        source_times.shape != source_values.shape
        or scaled_times.shape != scaled_values.shape
        or source_times.shape != scaled_times.shape
    ):
        raise E01F0TransformError("F0 backend pitch-tier shapes differ")
    point_count = int(source_times.size)
    if point_count < minimum_pitch_tier_points:
        raise E01F0TransformError(
            "F0 backend returned fewer than minimum_pitch_tier_points"
        )
    if np.any(source_times < 0.0) or (
        point_count > 1 and not bool(np.all(np.diff(source_times) > 0.0))
    ):
        raise E01F0TransformError(
            "source pitch-tier times must be non-negative and increasing"
        )
    input_duration_seconds = input_sample_count / sample_rate_hz
    if np.any(source_times > input_duration_seconds):
        raise E01F0TransformError(
            "source pitch-tier times exceed the input waveform duration"
        )
    if not bool(np.array_equal(source_times, scaled_times)):
        raise E01F0TransformError("F0 backend changed the pitch-tier time grid")
    if np.any(source_values <= 0.0):
        raise E01F0TransformError("source pitch-tier frequencies must be positive")
    if np.any(source_values < analysis_pitch_floor_hz) or np.any(
        source_values > analysis_pitch_ceiling_hz
    ):
        raise E01F0TransformError(
            "source pitch-tier frequencies exceed analysis bounds"
        )
    if np.any(scaled_values <= 0.0):
        raise E01F0TransformError("scaled pitch-tier frequencies must be positive")
    expected_scaled_values = source_values * f0_scale_factor
    if not bool(
        np.allclose(
            scaled_values,
            expected_scaled_values,
            rtol=1e-12,
            atol=0.0,
        )
    ):
        raise E01F0TransformError(
            "F0 backend did not apply the exact global pitch-tier multiplier"
        )
    if np.any(scaled_values < allowed_scaled_pitch_floor_hz) or np.any(
        scaled_values > allowed_scaled_pitch_ceiling_hz
    ):
        raise E01F0TransformError(
            "scaled pitch-tier frequencies exceed caller-provided bounds"
        )

    for duration_count in (
        backend_output.duration_tier_point_count_before,
        backend_output.duration_tier_point_count_after,
    ):
        if (
            isinstance(duration_count, bool)
            or not isinstance(duration_count, Integral)
            or int(duration_count) != 0
        ):
            raise E01F0TransformError(
                "Praat DurationTier must remain empty during F0 scaling"
            )

    output = np.asarray(output_float64, dtype=source_dtype).copy()
    if not bool(np.isfinite(output).all()):
        raise E01F0TransformError(
            "F0 backend output is not representable by the source dtype"
        )
    output_peak = float(np.max(np.abs(output)))
    if output_peak > peak_limit:
        raise E01F0TransformError(
            "F0 resynthesis exceeds peak_limit; normalization and clipping "
            "are forbidden"
        )
    maximum_scale_error = float(np.max(np.abs(scaled_values - expected_scaled_values)))
    return (
        output,
        point_count,
        float(np.min(source_values)),
        float(np.max(source_values)),
        float(np.min(scaled_values)),
        float(np.max(scaled_values)),
        maximum_scale_error,
        output_peak,
    )


def _waveform_digest(
    waveform: FloatPCM,
    *,
    contract: dict[str, object],
) -> str:
    contiguous = np.ascontiguousarray(waveform)
    digest = hashlib.sha256()
    digest.update(b"voice_coach.e01.scale_global_f0.v1\0")
    digest.update(
        json.dumps(
            contract,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    digest.update(b"\0")
    digest.update(contiguous.dtype.str.encode("ascii"))
    digest.update(b"\0")
    digest.update(str(contiguous.size).encode("ascii"))
    digest.update(b"\0")
    digest.update(contiguous.tobytes(order="C"))
    return digest.hexdigest()


def scale_global_f0(
    waveform: FloatPCM,
    *,
    sample_rate_hz: int,
    time_step_seconds: float,
    analysis_pitch_floor_hz: float,
    analysis_pitch_ceiling_hz: float,
    f0_scale_factor: float,
    allowed_scaled_pitch_floor_hz: float,
    allowed_scaled_pitch_ceiling_hz: float,
    minimum_pitch_tier_points: int,
    peak_limit: float,
    max_output_samples: int,
    backend: _F0ScalingBackend | None = None,
    backend_version: str | None = None,
) -> E01F0TransformResult:
    """Scale the whole detected PitchTier with pinned Praat PSOLA.

    All scientific parameters are caller-explicit.  Production calls omit
    ``backend`` and ``backend_version`` and lazily load
    ``praat-parselmouth==0.4.7``.  The paired injection arguments exist only
    for deterministic contract tests.
    """

    (
        validated_waveform,
        validated_sample_rate,
        validated_peak_limit,
        validated_max_samples,
        input_peak,
    ) = _validate_input_waveform(
        waveform,
        sample_rate_hz=sample_rate_hz,
        peak_limit=peak_limit,
        max_output_samples=max_output_samples,
    )
    (
        validated_time_step,
        validated_analysis_floor,
        validated_analysis_ceiling,
        validated_scale_factor,
        validated_scaled_floor,
        validated_scaled_ceiling,
        validated_minimum_points,
    ) = _validate_transform_parameters(
        time_step_seconds=time_step_seconds,
        analysis_pitch_floor_hz=analysis_pitch_floor_hz,
        analysis_pitch_ceiling_hz=analysis_pitch_ceiling_hz,
        f0_scale_factor=f0_scale_factor,
        allowed_scaled_pitch_floor_hz=allowed_scaled_pitch_floor_hz,
        allowed_scaled_pitch_ceiling_hz=allowed_scaled_pitch_ceiling_hz,
        minimum_pitch_tier_points=minimum_pitch_tier_points,
    )
    if (backend is None) != (backend_version is None):
        raise E01F0TransformError(
            "backend and backend_version must be provided together"
        )
    if backend is None:
        selected_backend, selected_version = _load_pinned_backend()
    else:
        selected_backend = backend
        if (
            type(backend_version) is not str
            or backend_version != EXPECTED_PARSELMOUTH_VERSION
        ):
            raise E01F0TransformError(
                "backend version differs from the pinned F0-transform contract"
            )
        selected_version = backend_version

    backend_input = np.asarray(validated_waveform, dtype=np.float64).copy()
    try:
        backend_output = selected_backend.scale_global_f0(
            backend_input,
            sample_rate_hz=validated_sample_rate,
            time_step_seconds=validated_time_step,
            analysis_pitch_floor_hz=validated_analysis_floor,
            analysis_pitch_ceiling_hz=validated_analysis_ceiling,
            f0_scale_factor=validated_scale_factor,
        )
    except E01F0TransformError:
        raise
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise E01F0TransformError("Parselmouth PSOLA F0 scaling failed") from exc

    (
        output,
        pitch_point_count,
        source_pitch_min,
        source_pitch_max,
        scaled_pitch_min,
        scaled_pitch_max,
        maximum_scale_error,
        output_peak,
    ) = _validate_backend_output(
        backend_output,
        input_sample_count=int(validated_waveform.size),
        source_dtype=validated_waveform.dtype,
        sample_rate_hz=validated_sample_rate,
        peak_limit=validated_peak_limit,
        max_output_samples=validated_max_samples,
        analysis_pitch_floor_hz=validated_analysis_floor,
        analysis_pitch_ceiling_hz=validated_analysis_ceiling,
        f0_scale_factor=validated_scale_factor,
        allowed_scaled_pitch_floor_hz=validated_scaled_floor,
        allowed_scaled_pitch_ceiling_hz=validated_scaled_ceiling,
        minimum_pitch_tier_points=validated_minimum_points,
    )
    digest_contract: dict[str, object] = {
        "algorithm_id": F0_SCALING_ALGORITHM_ID,
        "allowed_scaled_pitch_ceiling_hz": validated_scaled_ceiling,
        "allowed_scaled_pitch_floor_hz": validated_scaled_floor,
        "analysis_pitch_ceiling_hz": validated_analysis_ceiling,
        "analysis_pitch_floor_hz": validated_analysis_floor,
        "f0_scale_factor": validated_scale_factor,
        "max_output_samples": validated_max_samples,
        "minimum_pitch_tier_points": validated_minimum_points,
        "parselmouth_version": selected_version,
        "peak_limit": validated_peak_limit,
        "sample_rate_hz": validated_sample_rate,
        "time_step_seconds": validated_time_step,
    }
    output_digest = _waveform_digest(
        output,
        contract=digest_contract,
    )
    metadata_result = E01F0TransformMetadata(
        transform="f0",
        operation="scale_global_f0",
        algorithm_id=F0_SCALING_ALGORITHM_ID,
        parselmouth_version=selected_version,
        sample_rate_hz=validated_sample_rate,
        time_step_seconds=validated_time_step,
        analysis_pitch_floor_hz=validated_analysis_floor,
        analysis_pitch_ceiling_hz=validated_analysis_ceiling,
        f0_scale_factor=validated_scale_factor,
        allowed_scaled_pitch_floor_hz=validated_scaled_floor,
        allowed_scaled_pitch_ceiling_hz=validated_scaled_ceiling,
        minimum_pitch_tier_points=validated_minimum_points,
        peak_limit=validated_peak_limit,
        max_output_samples=validated_max_samples,
        input_sample_count=int(validated_waveform.size),
        output_sample_count=int(output.size),
        pitch_tier_point_count=pitch_point_count,
        source_pitch_tier_min_hz=source_pitch_min,
        source_pitch_tier_max_hz=source_pitch_max,
        scaled_pitch_tier_min_hz=scaled_pitch_min,
        scaled_pitch_tier_max_hz=scaled_pitch_max,
        maximum_pitch_tier_scale_error_hz=maximum_scale_error,
        input_peak=input_peak,
        output_peak=output_peak,
        output_digest_sha256=output_digest,
    )
    return E01F0TransformResult(
        waveform=output,
        metadata=metadata_result,
    )


__all__ = [
    "E01F0TransformError",
    "E01F0TransformMetadata",
    "E01F0TransformResult",
    "EXPECTED_PARSELMOUTH_VERSION",
    "EXPECTED_SAMPLE_RATE_HZ",
    "F0_SCALING_ALGORITHM_ID",
    "scale_global_f0",
]
