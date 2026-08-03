"""Strict in-memory aligned-segment duration scaling for E01.

The primitive uses a six-point Praat ``DurationTier`` and overlap-add
resynthesis.  Its trapezoidal control curve has unit scale outside the
caller-provided segment and uses a derived plateau so the average scale over
the aligned segment equals the requested factor despite caller-provided
symmetric transition widths.  The theoretical area under that curve changes
duration by exactly
``segment_duration * (duration_scale_factor - 1)``.

Praat 6.1.38, as bundled by ``praat-parselmouth==0.4.7`` here, draws random
durations for every voiceless interval during duration resynthesis.  A
caller-explicit Praat seed is therefore set immediately before overlap-add
and the global Praat RNG is safely reinitialized in a ``finally`` block.
Praat exposes no API for snapshotting and restoring its prior RNG state, so a
real call must run as a single job in a dedicated subprocess.  The caller has
to attest that isolation policy explicitly.

Praat's overlap-add implementation may differ from that continuous-time
target by a small number of discrete samples.  The caller must therefore
provide both a sample-count rounding policy and a maximum allowed sample
error.  The implementation reports, and validates, the actual error.

This module does not choose scientific magnitudes or tolerances; read or
write audio; use a network or GPU; or evaluate an E01 score relation.  Its
only RNG operation is the caller-pinned Praat seed and mandatory safe reset
inside the attested isolated process.  PitchTier control points are audited
before and after the DurationTier replacement, but PSOLA may still introduce
spectral, energy, boundary, or naturalness changes.  Those collateral effects
require separate measurement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from importlib import import_module, metadata
import hashlib
import json
import math
from numbers import Integral, Real
from typing import Literal, Protocol, cast

import numpy as np
from numpy.typing import NDArray


EXPECTED_PARSELMOUTH_VERSION = "0.4.7"
EXPECTED_PRAAT_VERSION = "6.1.38"
EXPECTED_SAMPLE_RATE_HZ = 16_000
DURATION_SCALING_ALGORITHM_ID = "praat_duration_tier_seeded_psola_overlap_add_v1"
DURATION_TIER_POINT_COUNT = 6
PRAAT_PROCESS_ISOLATION_POLICY = "dedicated_single_job_subprocess"
PRAAT_RNG_RESET_POLICY = "safe_unpredictable_finally_then_process_exit"

FloatPCM = NDArray[np.floating]
Float64Array = NDArray[np.float64]
SampleCountRounding = Literal["half_up"]

_ROUNDING_MODES = {
    "half_up": ROUND_HALF_UP,
}
_SUPPORTED_DTYPES = frozenset({np.dtype(np.float32), np.dtype(np.float64)})


class E01DurationTransformError(ValueError):
    """Raised when an aligned-duration transform violates its contract."""


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


class _PraatRun(Protocol):
    def __call__(self, script: str) -> object: ...


@dataclass(frozen=True, slots=True)
class _BackendDurationScalingOutput:
    waveform: object
    sample_rate_hz: object
    output_xmin_seconds: object
    output_xmax_seconds: object
    source_pitch_times_seconds: object
    source_pitch_values_hz: object
    final_pitch_times_seconds: object
    final_pitch_values_hz: object
    duration_tier_times_seconds: object
    duration_tier_scale_values: object
    duration_tier_point_count_before: object
    duration_tier_point_count_after: object


class _DurationScalingBackend(Protocol):
    def scale_aligned_segment_duration(
        self,
        samples: Float64Array,
        *,
        sample_rate_hz: int,
        time_step_seconds: float,
        analysis_pitch_floor_hz: float,
        analysis_pitch_ceiling_hz: float,
        duration_tier_times_seconds: Float64Array,
        duration_tier_scale_values: Float64Array,
        praat_random_seed: int,
    ) -> _BackendDurationScalingOutput: ...


@dataclass(frozen=True, slots=True)
class E01DurationTransformMetadata:
    """Privacy-minimal evidence for one in-memory duration transform."""

    transform: Literal["duration"]
    operation: Literal["scale_aligned_segment_duration"]
    algorithm_id: str
    parselmouth_version: str
    praat_version: str
    backend_provenance: str
    paper_evidence_eligibility: str
    sample_rate_hz: int
    time_step_seconds: float
    analysis_pitch_floor_hz: float
    analysis_pitch_ceiling_hz: float
    segment_start_seconds: float
    segment_end_seconds: float
    transition_seconds: float
    segment_start_sample: int
    segment_end_sample: int
    transition_samples: int
    duration_scale_factor: float
    duration_tier_plateau_scale_factor: float
    praat_random_seed: int
    praat_process_isolation_policy: str
    praat_rng_reset_policy: str
    expected_sample_count_rounding: SampleCountRounding
    maximum_duration_error_samples: int
    minimum_pitch_tier_points: int
    peak_limit: float
    max_output_samples: int
    input_sample_count: int
    theoretical_output_sample_count: int
    output_sample_count: int
    duration_error_samples: int
    output_duration_seconds: float
    theoretical_continuous_output_duration_seconds: float
    duration_tier_area_seconds: float
    duration_tier_area_error_seconds: float
    duration_tier_point_count: int
    pitch_tier_point_count: int
    maximum_pitch_tier_time_change_seconds: float
    maximum_pitch_tier_value_change_hz: float
    input_peak: float
    output_peak: float
    output_digest_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return aggregate transform metadata without source PCM or IDs."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class E01DurationTransformResult:
    """A newly allocated waveform and controlled-duration metadata."""

    waveform: FloatPCM
    metadata: E01DurationTransformMetadata


class _ParselmouthPsolaDurationBackend:
    """Narrow adapter over the exact Praat duration commands used here."""

    def __init__(
        self,
        *,
        sound_factory: _SoundFactory,
        praat_call: _PraatCall,
        praat_run: _PraatRun,
    ) -> None:
        self._sound_factory = sound_factory
        self._praat_call = praat_call
        self._praat_run = praat_run

    @staticmethod
    def _integer_query(value: object, *, field_name: str) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, (Integral, Real))
            or not math.isfinite(float(value))
            or not float(value).is_integer()
        ):
            raise E01DurationTransformError(
                f"Parselmouth returned an invalid {field_name}"
            )
        return int(float(value))

    @staticmethod
    def _float_query(value: object, *, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise E01DurationTransformError(
                f"Parselmouth returned an invalid {field_name}"
            )
        normalized = float(value)
        if not math.isfinite(normalized):
            raise E01DurationTransformError(
                f"Parselmouth returned an invalid {field_name}"
            )
        return normalized

    def _tier_values(
        self,
        tier: object,
        *,
        point_count: int,
        value_field_name: str,
    ) -> tuple[Float64Array, Float64Array]:
        times = np.empty(point_count, dtype=np.float64)
        values = np.empty(point_count, dtype=np.float64)
        for point_index in range(1, point_count + 1):
            times[point_index - 1] = self._float_query(
                self._praat_call(
                    tier,
                    "Get time from index",
                    point_index,
                ),
                field_name=f"{value_field_name} time",
            )
            values[point_index - 1] = self._float_query(
                self._praat_call(
                    tier,
                    "Get value at index",
                    point_index,
                ),
                field_name=value_field_name,
            )
        return times, values

    def scale_aligned_segment_duration(
        self,
        samples: Float64Array,
        *,
        sample_rate_hz: int,
        time_step_seconds: float,
        analysis_pitch_floor_hz: float,
        analysis_pitch_ceiling_hz: float,
        duration_tier_times_seconds: Float64Array,
        duration_tier_scale_values: Float64Array,
        praat_random_seed: int,
    ) -> _BackendDurationScalingOutput:
        """Replace an empty DurationTier and run overlap-add resynthesis."""

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
        source_pitch_tier = self._praat_call(
            manipulation,
            "Extract pitch tier",
        )
        pitch_point_count = self._integer_query(
            self._praat_call(
                source_pitch_tier,
                "Get number of points",
            ),
            field_name="pitch-tier point count",
        )
        source_pitch_times, source_pitch_values = self._tier_values(
            source_pitch_tier,
            point_count=pitch_point_count,
            value_field_name="pitch-tier frequency",
        )

        duration_tier = self._praat_call(
            manipulation,
            "Extract duration tier",
        )
        duration_count_before = self._integer_query(
            self._praat_call(
                duration_tier,
                "Get number of points",
            ),
            field_name="duration-tier point count",
        )
        for point_time, scale_value in zip(
            duration_tier_times_seconds,
            duration_tier_scale_values,
            strict=True,
        ):
            self._praat_call(
                duration_tier,
                "Add point",
                float(point_time),
                float(scale_value),
            )
        duration_count_after = self._integer_query(
            self._praat_call(
                duration_tier,
                "Get number of points",
            ),
            field_name="duration-tier point count",
        )
        actual_duration_times, actual_duration_values = self._tier_values(
            duration_tier,
            point_count=duration_count_after,
            value_field_name="duration-tier scale",
        )
        self._praat_call(
            [duration_tier, manipulation],
            "Replace duration tier",
        )

        final_pitch_tier = self._praat_call(
            manipulation,
            "Extract pitch tier",
        )
        final_pitch_count = self._integer_query(
            self._praat_call(
                final_pitch_tier,
                "Get number of points",
            ),
            field_name="final pitch-tier point count",
        )
        if final_pitch_count != pitch_point_count:
            raise E01DurationTransformError(
                "Parselmouth changed the PitchTier point count"
            )
        final_pitch_times, final_pitch_values = self._tier_values(
            final_pitch_tier,
            point_count=final_pitch_count,
            value_field_name="final pitch-tier frequency",
        )

        seed_script = (
            f"random_initializeWithSeedUnsafelyButPredictably ({praat_random_seed})"
        )
        reset_script = "random_initializeSafelyAndUnpredictably ()"
        try:
            self._praat_run(seed_script)
        except Exception as exc:
            raise E01DurationTransformError(
                "Praat deterministic RNG initialization failed"
            ) from exc
        try:
            output_sound = cast(
                _SoundLike,
                self._praat_call(
                    manipulation,
                    "Get resynthesis (overlap-add)",
                ),
            )
        finally:
            try:
                self._praat_run(reset_script)
            except Exception as exc:
                raise E01DurationTransformError(
                    "Praat safe RNG reinitialization failed"
                ) from exc
        output_values = np.asarray(output_sound.values)
        if output_values.ndim != 2 or output_values.shape[0] != 1:
            raise E01DurationTransformError(
                "Parselmouth returned invalid mono resynthesis samples"
            )
        output_waveform = np.asarray(
            output_values[0],
            dtype=np.float64,
        ).copy()
        return _BackendDurationScalingOutput(
            waveform=output_waveform,
            sample_rate_hz=output_sound.sampling_frequency,
            output_xmin_seconds=output_sound.xmin,
            output_xmax_seconds=output_sound.xmax,
            source_pitch_times_seconds=source_pitch_times,
            source_pitch_values_hz=source_pitch_values,
            final_pitch_times_seconds=final_pitch_times,
            final_pitch_values_hz=final_pitch_values,
            duration_tier_times_seconds=actual_duration_times,
            duration_tier_scale_values=actual_duration_values,
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
        raise E01DurationTransformError(f"{field_name} must be a finite real number")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise E01DurationTransformError(
            f"{field_name} must be a finite real number"
        ) from exc
    if not math.isfinite(normalized):
        raise E01DurationTransformError(f"{field_name} must be a finite real number")
    violates_minimum = (
        normalized <= minimum if strictly_greater else normalized < minimum
    )
    if violates_minimum or (maximum is not None and normalized > maximum):
        qualifier = "greater than" if strictly_greater else "at least"
        message = f"{field_name} must be {qualifier} {minimum}"
        if maximum is not None:
            message += f" and at most {maximum}"
        raise E01DurationTransformError(message)
    return normalized


def _positive_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise E01DurationTransformError(f"{field_name} must be a positive integer")
    normalized = int(value)
    if normalized <= 0:
        raise E01DurationTransformError(f"{field_name} must be a positive integer")
    if normalized > int(np.iinfo(np.intp).max):
        raise E01DurationTransformError(
            f"{field_name} exceeds the platform sample-count limit"
        )
    return normalized


def _nonnegative_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise E01DurationTransformError(f"{field_name} must be a non-negative integer")
    normalized = int(value)
    if normalized < 0:
        raise E01DurationTransformError(f"{field_name} must be a non-negative integer")
    if normalized > int(np.iinfo(np.intp).max):
        raise E01DurationTransformError(
            f"{field_name} exceeds the platform sample-count limit"
        )
    return normalized


def _praat_random_seed(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise E01DurationTransformError(
            "praat_random_seed must be an integer from 1 through 2147483647"
        )
    normalized = int(value)
    if normalized < 1 or normalized > 2_147_483_647:
        raise E01DurationTransformError(
            "praat_random_seed must be an integer from 1 through 2147483647"
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
        raise E01DurationTransformError(
            "waveform must be an in-memory base NumPy array"
        )
    if waveform.ndim != 1:
        raise E01DurationTransformError("waveform must be one-dimensional mono PCM")
    if waveform.size == 0:
        raise E01DurationTransformError("waveform must not be empty")
    if waveform.dtype not in _SUPPORTED_DTYPES:
        raise E01DurationTransformError(
            "waveform dtype must be float32 or float64 normalized PCM"
        )
    if not bool(np.isfinite(waveform).all()):
        raise E01DurationTransformError("waveform contains NaN or Inf")
    if (
        isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, Integral)
        or int(sample_rate_hz) != EXPECTED_SAMPLE_RATE_HZ
    ):
        raise E01DurationTransformError(
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
        raise E01DurationTransformError("input sample count exceeds max_output_samples")
    input_peak = float(np.max(np.abs(waveform)))
    if input_peak > validated_peak_limit:
        raise E01DurationTransformError(
            "input waveform peak exceeds the explicit peak_limit"
        )
    return (
        waveform,
        EXPECTED_SAMPLE_RATE_HZ,
        validated_peak_limit,
        validated_max_output_samples,
        input_peak,
    )


def _exact_sample_index(
    seconds: float,
    *,
    field_name: str,
    sample_rate_hz: int,
) -> int:
    try:
        exact_samples = Decimal(str(seconds)) * Decimal(sample_rate_hz)
    except InvalidOperation as exc:
        raise E01DurationTransformError(
            f"{field_name} cannot be represented on the sample grid"
        ) from exc
    integral_samples = exact_samples.to_integral_value()
    if exact_samples != integral_samples:
        raise E01DurationTransformError(
            f"{field_name} must lie exactly on the {sample_rate_hz} Hz sample grid"
        )
    return int(integral_samples)


def _validate_transform_parameters(
    *,
    input_sample_count: int,
    sample_rate_hz: int,
    time_step_seconds: object,
    analysis_pitch_floor_hz: object,
    analysis_pitch_ceiling_hz: object,
    segment_start_seconds: object,
    segment_end_seconds: object,
    transition_seconds: object,
    duration_scale_factor: object,
    praat_random_seed: object,
    praat_process_isolation_policy: object,
    expected_sample_count_rounding: object,
    maximum_duration_error_samples: object,
    minimum_pitch_tier_points: object,
    max_output_samples: int,
) -> tuple[
    float,
    float,
    float,
    float,
    float,
    float,
    int,
    int,
    int,
    float,
    float,
    int,
    SampleCountRounding,
    int,
    int,
    int,
    Float64Array,
    Float64Array,
]:
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
        raise E01DurationTransformError(
            "analysis_pitch_ceiling_hz must exceed analysis_pitch_floor_hz"
        )
    segment_start = _finite_real(
        segment_start_seconds,
        field_name="segment_start_seconds",
        minimum=0.0,
        strictly_greater=True,
    )
    segment_end = _finite_real(
        segment_end_seconds,
        field_name="segment_end_seconds",
        minimum=0.0,
        strictly_greater=True,
    )
    transition = _finite_real(
        transition_seconds,
        field_name="transition_seconds",
        minimum=0.0,
        strictly_greater=True,
    )
    scale_factor = _finite_real(
        duration_scale_factor,
        field_name="duration_scale_factor",
        minimum=0.0,
        strictly_greater=True,
    )
    if scale_factor == 1.0:
        raise E01DurationTransformError(
            "duration_scale_factor must differ from 1.0 for a perturbation"
        )
    random_seed = _praat_random_seed(praat_random_seed)
    if (
        type(praat_process_isolation_policy) is not str
        or praat_process_isolation_policy != PRAAT_PROCESS_ISOLATION_POLICY
    ):
        raise E01DurationTransformError(
            "praat_process_isolation_policy must attest the exact dedicated "
            "single-job subprocess policy"
        )
    if segment_end <= segment_start:
        raise E01DurationTransformError(
            "segment_end_seconds must exceed segment_start_seconds"
        )
    if segment_end - segment_start <= 2.0 * transition:
        raise E01DurationTransformError(
            "segment duration must exceed twice transition_seconds"
        )

    input_duration_seconds = input_sample_count / sample_rate_hz
    if segment_start - transition <= 0.0:
        raise E01DurationTransformError(
            "segment_start_seconds must leave a transition margin after input start"
        )
    if segment_end + transition >= input_duration_seconds:
        raise E01DurationTransformError(
            "segment_end_seconds must leave a transition margin before input end"
        )

    start_sample = _exact_sample_index(
        segment_start,
        field_name="segment_start_seconds",
        sample_rate_hz=sample_rate_hz,
    )
    end_sample = _exact_sample_index(
        segment_end,
        field_name="segment_end_seconds",
        sample_rate_hz=sample_rate_hz,
    )
    transition_samples = _exact_sample_index(
        transition,
        field_name="transition_seconds",
        sample_rate_hz=sample_rate_hz,
    )
    if transition_samples <= 0:
        raise E01DurationTransformError(
            "transition_seconds must span at least one sample"
        )
    segment_sample_count = end_sample - start_sample
    try:
        plateau_scale_decimal = (
            Decimal(str(scale_factor)) * Decimal(segment_sample_count)
            - Decimal(transition_samples)
        ) / Decimal(segment_sample_count - transition_samples)
    except (InvalidOperation, OverflowError, ZeroDivisionError) as exc:
        raise E01DurationTransformError(
            "duration_scale_factor cannot produce a valid in-segment "
            "DurationTier plateau"
        ) from exc
    if plateau_scale_decimal <= 0:
        raise E01DurationTransformError(
            "duration_scale_factor is too small for the transition width"
        )
    plateau_scale_factor = float(plateau_scale_decimal)
    if not math.isfinite(plateau_scale_factor) or plateau_scale_factor <= 0.0:
        raise E01DurationTransformError(
            "duration_scale_factor cannot produce a finite DurationTier plateau"
        )

    if (
        type(expected_sample_count_rounding) is not str
        or expected_sample_count_rounding != "half_up"
    ):
        raise E01DurationTransformError(
            "expected_sample_count_rounding must be the pinned half_up policy"
        )
    rounding = cast(SampleCountRounding, expected_sample_count_rounding)
    maximum_error = _nonnegative_integer(
        maximum_duration_error_samples,
        field_name="maximum_duration_error_samples",
    )
    maximum_supported_error = max(
        1,
        int(math.ceil(time_step * sample_rate_hz)),
    )
    if maximum_error > maximum_supported_error:
        raise E01DurationTransformError(
            "maximum_duration_error_samples exceeds one analysis time step"
        )
    minimum_pitch_points = _positive_integer(
        minimum_pitch_tier_points,
        field_name="minimum_pitch_tier_points",
    )

    try:
        theoretical_count_decimal = Decimal(input_sample_count) + (
            Decimal(end_sample - start_sample)
            * (Decimal(str(scale_factor)) - Decimal(1))
        )
        theoretical_count_decimal = theoretical_count_decimal.to_integral_value(
            rounding=_ROUNDING_MODES[rounding]
        )
    except (InvalidOperation, OverflowError) as exc:
        raise E01DurationTransformError(
            "duration_scale_factor produces an invalid theoretical sample count"
        ) from exc
    theoretical_count = int(theoretical_count_decimal)
    if theoretical_count <= 0:
        raise E01DurationTransformError(
            "duration transform theoretical output is empty"
        )
    if theoretical_count > max_output_samples:
        raise E01DurationTransformError(
            "duration transform theoretical output exceeds max_output_samples"
        )
    if theoretical_count > 3 * input_sample_count:
        raise E01DurationTransformError(
            "duration transform theoretical output exceeds the pinned Praat "
            "three-times-input safety cap"
        )
    if theoretical_count + maximum_error > max_output_samples:
        raise E01DurationTransformError(
            "duration error allowance exceeds max_output_samples"
        )

    duration_times = np.array(
        [
            0.0,
            segment_start,
            segment_start + transition,
            segment_end - transition,
            segment_end,
            input_duration_seconds,
        ],
        dtype=np.float64,
    )
    duration_values = np.array(
        [
            1.0,
            1.0,
            plateau_scale_factor,
            plateau_scale_factor,
            1.0,
            1.0,
        ],
        dtype=np.float64,
    )
    if not bool(np.all(np.diff(duration_times) > 0.0)):
        raise E01DurationTransformError(
            "duration-tier times are not strictly increasing"
        )
    theoretical_continuous_duration = input_duration_seconds + (
        (segment_end - segment_start) * (scale_factor - 1.0)
    )
    duration_tier_area = float(np.trapezoid(duration_values, duration_times))
    if not math.isclose(
        duration_tier_area,
        theoretical_continuous_duration,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise E01DurationTransformError(
            "duration-tier area differs from the requested aligned-segment "
            "duration factor"
        )
    return (
        time_step,
        analysis_floor,
        analysis_ceiling,
        segment_start,
        segment_end,
        transition,
        start_sample,
        end_sample,
        transition_samples,
        scale_factor,
        plateau_scale_factor,
        random_seed,
        rounding,
        maximum_error,
        minimum_pitch_points,
        theoretical_count,
        duration_times,
        duration_values,
    )


def _load_pinned_backend() -> tuple[_DurationScalingBackend, str, str]:
    try:
        distribution_version = metadata.version("praat-parselmouth")
        parselmouth = import_module("parselmouth")
    except (metadata.PackageNotFoundError, ImportError) as exc:
        raise E01DurationTransformError(
            "praat-parselmouth is unavailable in this environment"
        ) from exc
    module_version = getattr(parselmouth, "__version__", None)
    praat_version = getattr(parselmouth, "PRAAT_VERSION", None)
    if (
        distribution_version != EXPECTED_PARSELMOUTH_VERSION
        or module_version != EXPECTED_PARSELMOUTH_VERSION
        or praat_version != EXPECTED_PRAAT_VERSION
    ):
        raise E01DurationTransformError(
            "Parselmouth or Praat version differs from the pinned duration contract"
        )
    sound_factory = cast(_SoundFactory, getattr(parselmouth, "Sound", None))
    praat_module = getattr(parselmouth, "praat", None)
    praat_call = cast(_PraatCall, getattr(praat_module, "call", None))
    praat_run = cast(_PraatRun, getattr(praat_module, "run", None))
    if (
        not callable(sound_factory)
        or not callable(praat_call)
        or not callable(praat_run)
    ):
        raise E01DurationTransformError(
            "Parselmouth lacks the required in-memory Praat command boundary"
        )
    return (
        _ParselmouthPsolaDurationBackend(
            sound_factory=sound_factory,
            praat_call=praat_call,
            praat_run=praat_run,
        ),
        distribution_version,
        praat_version,
    )


def _base_float64_vector(value: object, *, field_name: str) -> Float64Array:
    if type(value) is not np.ndarray:
        raise E01DurationTransformError(
            f"backend {field_name} must be a base NumPy array"
        )
    if value.ndim != 1:
        raise E01DurationTransformError(f"backend {field_name} must be one-dimensional")
    if value.dtype != np.dtype(np.float64):
        raise E01DurationTransformError(f"backend {field_name} must use float64")
    if not bool(np.isfinite(value).all()):
        raise E01DurationTransformError(f"backend {field_name} contains NaN or Inf")
    return value


def _exact_nonnegative_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise E01DurationTransformError(
            f"backend {field_name} must be a non-negative integer"
        )
    normalized = int(value)
    if normalized < 0:
        raise E01DurationTransformError(
            f"backend {field_name} must be a non-negative integer"
        )
    return normalized


def _validate_backend_output(
    backend_output: object,
    *,
    source_dtype: np.dtype[np.floating],
    input_sample_count: int,
    sample_rate_hz: int,
    peak_limit: float,
    max_output_samples: int,
    theoretical_output_sample_count: int,
    theoretical_continuous_output_duration_seconds: float,
    maximum_duration_error_samples: int,
    analysis_pitch_floor_hz: float,
    analysis_pitch_ceiling_hz: float,
    minimum_pitch_tier_points: int,
    expected_duration_times: Float64Array,
    expected_duration_values: Float64Array,
) -> tuple[FloatPCM, int, int, float, float, float, float]:
    if type(backend_output) is not _BackendDurationScalingOutput:
        raise E01DurationTransformError(
            "duration backend returned an invalid result contract"
        )
    output_sample_rate = backend_output.sample_rate_hz
    if (
        isinstance(output_sample_rate, bool)
        or not isinstance(output_sample_rate, Real)
        or not math.isfinite(float(output_sample_rate))
        or float(output_sample_rate) != float(sample_rate_hz)
    ):
        raise E01DurationTransformError("duration backend changed the sample rate")
    output_xmin = backend_output.output_xmin_seconds
    output_xmax = backend_output.output_xmax_seconds
    if (
        isinstance(output_xmin, bool)
        or not isinstance(output_xmin, Real)
        or not math.isfinite(float(output_xmin))
        or isinstance(output_xmax, bool)
        or not isinstance(output_xmax, Real)
        or not math.isfinite(float(output_xmax))
        or float(output_xmin) != 0.0
        or float(output_xmax) <= float(output_xmin)
    ):
        raise E01DurationTransformError(
            "duration backend returned an invalid output time domain"
        )

    output_float64 = _base_float64_vector(
        backend_output.waveform,
        field_name="waveform",
    )
    if output_float64.size == 0:
        raise E01DurationTransformError("duration backend returned empty PCM")
    if output_float64.size > max_output_samples:
        raise E01DurationTransformError("duration backend exceeded max_output_samples")
    output_duration_seconds = int(output_float64.size) / sample_rate_hz
    if not math.isclose(
        float(output_xmax) - float(output_xmin),
        theoretical_continuous_output_duration_seconds,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise E01DurationTransformError(
            "duration backend output time domain differs from the continuous "
            "DurationTier target"
        )
    duration_error = abs(int(output_float64.size) - theoretical_output_sample_count)
    if duration_error > maximum_duration_error_samples:
        raise E01DurationTransformError(
            "duration backend exceeded maximum_duration_error_samples"
        )

    duration_count_before = _exact_nonnegative_integer(
        backend_output.duration_tier_point_count_before,
        field_name="duration-tier point count before",
    )
    duration_count_after = _exact_nonnegative_integer(
        backend_output.duration_tier_point_count_after,
        field_name="duration-tier point count after",
    )
    if duration_count_before != 0:
        raise E01DurationTransformError(
            "Praat DurationTier must be empty before controlled insertion"
        )
    if duration_count_after != DURATION_TIER_POINT_COUNT:
        raise E01DurationTransformError(
            "Praat DurationTier point count differs from the six-point contract"
        )
    actual_duration_times = _base_float64_vector(
        backend_output.duration_tier_times_seconds,
        field_name="duration-tier times",
    )
    actual_duration_values = _base_float64_vector(
        backend_output.duration_tier_scale_values,
        field_name="duration-tier values",
    )
    if (
        actual_duration_times.shape != expected_duration_times.shape
        or actual_duration_values.shape != expected_duration_values.shape
        or not bool(np.array_equal(actual_duration_times, expected_duration_times))
        or not bool(np.array_equal(actual_duration_values, expected_duration_values))
    ):
        raise E01DurationTransformError(
            "duration backend changed the exact six-point DurationTier"
        )

    source_pitch_times = _base_float64_vector(
        backend_output.source_pitch_times_seconds,
        field_name="source pitch-tier times",
    )
    source_pitch_values = _base_float64_vector(
        backend_output.source_pitch_values_hz,
        field_name="source pitch-tier values",
    )
    final_pitch_times = _base_float64_vector(
        backend_output.final_pitch_times_seconds,
        field_name="final pitch-tier times",
    )
    final_pitch_values = _base_float64_vector(
        backend_output.final_pitch_values_hz,
        field_name="final pitch-tier values",
    )
    if (
        source_pitch_times.shape != source_pitch_values.shape
        or final_pitch_times.shape != final_pitch_values.shape
        or source_pitch_times.shape != final_pitch_times.shape
    ):
        raise E01DurationTransformError("duration backend pitch-tier shapes differ")
    pitch_point_count = int(source_pitch_times.size)
    if pitch_point_count < minimum_pitch_tier_points:
        raise E01DurationTransformError(
            "duration backend returned fewer than minimum_pitch_tier_points"
        )
    if (
        np.any(source_pitch_times < 0.0)
        or (
            pitch_point_count > 1
            and not bool(np.all(np.diff(source_pitch_times) > 0.0))
        )
        or np.any(source_pitch_times > (input_sample_count / sample_rate_hz))
    ):
        raise E01DurationTransformError("source pitch-tier time grid is invalid")
    if np.any(source_pitch_values < analysis_pitch_floor_hz) or np.any(
        source_pitch_values > analysis_pitch_ceiling_hz
    ):
        raise E01DurationTransformError(
            "source pitch-tier values exceed analysis bounds"
        )
    if not bool(np.array_equal(source_pitch_times, final_pitch_times)):
        raise E01DurationTransformError(
            "DurationTier replacement changed PitchTier times"
        )
    if not bool(np.array_equal(source_pitch_values, final_pitch_values)):
        raise E01DurationTransformError(
            "DurationTier replacement changed PitchTier values"
        )

    output = np.asarray(output_float64, dtype=source_dtype).copy()
    if not bool(np.isfinite(output).all()):
        raise E01DurationTransformError(
            "duration backend output is not representable by the source dtype"
        )

    output_peak = float(np.max(np.abs(output)))
    if output_peak > peak_limit:
        raise E01DurationTransformError(
            "duration resynthesis exceeds peak_limit; normalization and clipping "
            "are forbidden"
        )
    maximum_time_change = float(np.max(np.abs(final_pitch_times - source_pitch_times)))
    maximum_value_change = float(
        np.max(np.abs(final_pitch_values - source_pitch_values))
    )
    return (
        output,
        duration_error,
        pitch_point_count,
        maximum_time_change,
        maximum_value_change,
        output_peak,
        output_duration_seconds,
    )


def _waveform_digest(
    waveform: FloatPCM,
    *,
    contract: dict[str, object],
) -> str:
    contiguous = np.ascontiguousarray(waveform)
    digest = hashlib.sha256()
    digest.update(b"voice_coach.e01.scale_aligned_segment_duration.v1\0")
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


def scale_aligned_segment_duration(
    waveform: FloatPCM,
    *,
    sample_rate_hz: int,
    time_step_seconds: float,
    analysis_pitch_floor_hz: float,
    analysis_pitch_ceiling_hz: float,
    segment_start_seconds: float,
    segment_end_seconds: float,
    transition_seconds: float,
    duration_scale_factor: float,
    praat_random_seed: int,
    praat_process_isolation_policy: str,
    expected_sample_count_rounding: SampleCountRounding,
    maximum_duration_error_samples: int,
    minimum_pitch_tier_points: int,
    peak_limit: float,
    max_output_samples: int,
    backend: _DurationScalingBackend | None = None,
    backend_version: str | None = None,
) -> E01DurationTransformResult:
    """Scale one caller-aligned segment using a pinned Praat DurationTier.

    Production calls omit ``backend`` and ``backend_version`` and lazily load
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
        validated_segment_start,
        validated_segment_end,
        validated_transition,
        segment_start_sample,
        segment_end_sample,
        transition_samples,
        validated_scale_factor,
        duration_tier_plateau_scale_factor,
        validated_praat_random_seed,
        validated_rounding,
        validated_maximum_error,
        validated_minimum_pitch_points,
        theoretical_output_count,
        duration_times,
        duration_values,
    ) = _validate_transform_parameters(
        input_sample_count=int(validated_waveform.size),
        sample_rate_hz=validated_sample_rate,
        time_step_seconds=time_step_seconds,
        analysis_pitch_floor_hz=analysis_pitch_floor_hz,
        analysis_pitch_ceiling_hz=analysis_pitch_ceiling_hz,
        segment_start_seconds=segment_start_seconds,
        segment_end_seconds=segment_end_seconds,
        transition_seconds=transition_seconds,
        duration_scale_factor=duration_scale_factor,
        praat_random_seed=praat_random_seed,
        praat_process_isolation_policy=praat_process_isolation_policy,
        expected_sample_count_rounding=expected_sample_count_rounding,
        maximum_duration_error_samples=maximum_duration_error_samples,
        minimum_pitch_tier_points=minimum_pitch_tier_points,
        max_output_samples=validated_max_samples,
    )
    theoretical_continuous_output_duration = (
        int(validated_waveform.size) / validated_sample_rate
    ) + (
        (validated_segment_end - validated_segment_start)
        * (validated_scale_factor - 1.0)
    )
    duration_tier_area = float(np.trapezoid(duration_values, duration_times))
    duration_tier_area_error = abs(
        duration_tier_area - theoretical_continuous_output_duration
    )
    if (backend is None) != (backend_version is None):
        raise E01DurationTransformError(
            "backend and backend_version must be provided together"
        )
    if backend is None:
        selected_backend, selected_version, selected_praat_version = (
            _load_pinned_backend()
        )
        backend_provenance = "pinned_parselmouth_runtime"
        paper_evidence_eligibility = "requires_external_worker_receipt"
    else:
        selected_backend = backend
        if (
            type(backend_version) is not str
            or backend_version != EXPECTED_PARSELMOUTH_VERSION
        ):
            raise E01DurationTransformError(
                "backend version differs from the pinned duration contract"
            )
        selected_version = backend_version
        selected_praat_version = EXPECTED_PRAAT_VERSION
        backend_provenance = "test_injected_backend"
        paper_evidence_eligibility = "forbidden_test_injection"

    backend_input = np.asarray(validated_waveform, dtype=np.float64).copy()
    try:
        backend_output = selected_backend.scale_aligned_segment_duration(
            backend_input,
            sample_rate_hz=validated_sample_rate,
            time_step_seconds=validated_time_step,
            analysis_pitch_floor_hz=validated_analysis_floor,
            analysis_pitch_ceiling_hz=validated_analysis_ceiling,
            duration_tier_times_seconds=duration_times.copy(),
            duration_tier_scale_values=duration_values.copy(),
            praat_random_seed=validated_praat_random_seed,
        )
    except E01DurationTransformError:
        raise
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise E01DurationTransformError(
            "Parselmouth PSOLA duration scaling failed"
        ) from exc

    (
        output,
        duration_error,
        pitch_point_count,
        maximum_pitch_time_change,
        maximum_pitch_value_change,
        output_peak,
        output_duration_seconds,
    ) = _validate_backend_output(
        backend_output,
        source_dtype=validated_waveform.dtype,
        input_sample_count=int(validated_waveform.size),
        sample_rate_hz=validated_sample_rate,
        peak_limit=validated_peak_limit,
        max_output_samples=validated_max_samples,
        theoretical_output_sample_count=theoretical_output_count,
        theoretical_continuous_output_duration_seconds=(
            theoretical_continuous_output_duration
        ),
        maximum_duration_error_samples=validated_maximum_error,
        analysis_pitch_floor_hz=validated_analysis_floor,
        analysis_pitch_ceiling_hz=validated_analysis_ceiling,
        minimum_pitch_tier_points=validated_minimum_pitch_points,
        expected_duration_times=duration_times,
        expected_duration_values=duration_values,
    )
    digest_contract: dict[str, object] = {
        "algorithm_id": DURATION_SCALING_ALGORITHM_ID,
        "analysis_pitch_ceiling_hz": validated_analysis_ceiling,
        "analysis_pitch_floor_hz": validated_analysis_floor,
        "backend_provenance": backend_provenance,
        "duration_scale_factor": validated_scale_factor,
        "duration_tier_plateau_scale_factor": (duration_tier_plateau_scale_factor),
        "expected_sample_count_rounding": validated_rounding,
        "max_output_samples": validated_max_samples,
        "maximum_duration_error_samples": validated_maximum_error,
        "minimum_pitch_tier_points": validated_minimum_pitch_points,
        "parselmouth_version": selected_version,
        "praat_version": selected_praat_version,
        "paper_evidence_eligibility": paper_evidence_eligibility,
        "peak_limit": validated_peak_limit,
        "praat_process_isolation_policy": PRAAT_PROCESS_ISOLATION_POLICY,
        "praat_random_seed": validated_praat_random_seed,
        "praat_rng_reset_policy": PRAAT_RNG_RESET_POLICY,
        "sample_rate_hz": validated_sample_rate,
        "segment_end_seconds": validated_segment_end,
        "segment_start_seconds": validated_segment_start,
        "time_step_seconds": validated_time_step,
        "transition_seconds": validated_transition,
    }
    output_digest = _waveform_digest(
        output,
        contract=digest_contract,
    )
    return E01DurationTransformResult(
        waveform=output,
        metadata=E01DurationTransformMetadata(
            transform="duration",
            operation="scale_aligned_segment_duration",
            algorithm_id=DURATION_SCALING_ALGORITHM_ID,
            parselmouth_version=selected_version,
            praat_version=selected_praat_version,
            backend_provenance=backend_provenance,
            paper_evidence_eligibility=paper_evidence_eligibility,
            sample_rate_hz=validated_sample_rate,
            time_step_seconds=validated_time_step,
            analysis_pitch_floor_hz=validated_analysis_floor,
            analysis_pitch_ceiling_hz=validated_analysis_ceiling,
            segment_start_seconds=validated_segment_start,
            segment_end_seconds=validated_segment_end,
            transition_seconds=validated_transition,
            segment_start_sample=segment_start_sample,
            segment_end_sample=segment_end_sample,
            transition_samples=transition_samples,
            duration_scale_factor=validated_scale_factor,
            duration_tier_plateau_scale_factor=(duration_tier_plateau_scale_factor),
            praat_random_seed=validated_praat_random_seed,
            praat_process_isolation_policy=PRAAT_PROCESS_ISOLATION_POLICY,
            praat_rng_reset_policy=PRAAT_RNG_RESET_POLICY,
            expected_sample_count_rounding=validated_rounding,
            maximum_duration_error_samples=validated_maximum_error,
            minimum_pitch_tier_points=validated_minimum_pitch_points,
            peak_limit=validated_peak_limit,
            max_output_samples=validated_max_samples,
            input_sample_count=int(validated_waveform.size),
            theoretical_output_sample_count=theoretical_output_count,
            output_sample_count=int(output.size),
            duration_error_samples=duration_error,
            output_duration_seconds=output_duration_seconds,
            theoretical_continuous_output_duration_seconds=(
                theoretical_continuous_output_duration
            ),
            duration_tier_area_seconds=duration_tier_area,
            duration_tier_area_error_seconds=duration_tier_area_error,
            duration_tier_point_count=DURATION_TIER_POINT_COUNT,
            pitch_tier_point_count=pitch_point_count,
            maximum_pitch_tier_time_change_seconds=maximum_pitch_time_change,
            maximum_pitch_tier_value_change_hz=maximum_pitch_value_change,
            input_peak=input_peak,
            output_peak=output_peak,
            output_digest_sha256=output_digest,
        ),
    )


__all__ = [
    "DURATION_SCALING_ALGORITHM_ID",
    "DURATION_TIER_POINT_COUNT",
    "E01DurationTransformError",
    "E01DurationTransformMetadata",
    "E01DurationTransformResult",
    "EXPECTED_PARSELMOUTH_VERSION",
    "EXPECTED_PRAAT_VERSION",
    "EXPECTED_SAMPLE_RATE_HZ",
    "PRAAT_PROCESS_ISOLATION_POLICY",
    "PRAAT_RNG_RESET_POLICY",
    "SampleCountRounding",
    "scale_aligned_segment_duration",
]
