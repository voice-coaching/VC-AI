"""Pure common-time-grid agreement statistics for two F0 contours.

This module does not read audio, call a pitch backend, select corpus rows,
access a split, or choose scientific thresholds. The caller supplies two
in-memory contours, an exact common time grid, all interpolation policies, and
the octave-match tolerance.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from numbers import Integral, Real
from statistics import median
from typing import TypeVar, cast


PITCH_CONTOUR_AGREEMENT_POLICY_ID = "voice_coach.pitch_contour_agreement.v1"


class PitchContourAgreementError(ValueError):
    """Raised when an input or derived statistic violates the strict contract."""


class PitchInterpolationPolicy(str, Enum):
    """Caller-selected interpolation and nearest-neighbour tie behavior."""

    LINEAR = "linear"
    NEAREST_EARLIER_ON_TIE = "nearest_earlier_on_tie"
    NEAREST_LATER_ON_TIE = "nearest_later_on_tie"


class PitchEndpointPolicy(str, Enum):
    """Caller-selected handling of common-grid points outside shared support."""

    REQUIRE_SHARED_SUPPORT = "require_shared_support"
    TRIM_TO_SHARED_SUPPORT = "trim_to_shared_support"


class PitchUnvoicedPolicy(str, Enum):
    """Caller-selected treatment of zero-valued, unvoiced source frames."""

    PRESERVE_GAPS = "preserve_gaps"
    BRIDGE_INTERNAL_GAPS = "bridge_internal_gaps"


@dataclass(frozen=True, slots=True)
class PitchContourSeries:
    """Transient zero-is-unvoiced contour input without source identifiers."""

    time_seconds: tuple[float, ...]
    f0_hz: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PitchContourAgreementResult:
    """Privacy-minimal technical statistics without the input contours or grid."""

    evaluation_policy_id: str
    interpolation_policy: PitchInterpolationPolicy
    endpoint_policy: PitchEndpointPolicy
    unvoiced_policy: PitchUnvoicedPolicy
    octave_tolerance_cents: float
    reference_source_frame_count: int
    candidate_source_frame_count: int
    requested_common_frame_count: int
    evaluated_common_frame_count: int
    trimmed_common_frame_count: int
    resampled_reference_voiced_frame_count: int
    resampled_candidate_voiced_frame_count: int
    jointly_voiced_frame_count: int
    reference_only_voiced_frame_count: int
    candidate_only_voiced_frame_count: int
    jointly_unvoiced_frame_count: int
    voicing_agreement_rate: float
    jointly_voiced_rate: float
    mean_candidate_minus_reference_hz: float
    mean_absolute_hz_error: float
    median_absolute_hz_error: float
    root_mean_squared_hz_error: float
    maximum_absolute_hz_error: float
    mean_candidate_minus_reference_cents: float
    mean_absolute_cents_error: float
    median_absolute_cents_error: float
    root_mean_squared_cents_error: float
    maximum_absolute_cents_error: float
    octave_error_frame_count: int
    octave_error_rate: float
    result_digest_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-safe public result fields."""

        return {
            "evaluation_policy_id": self.evaluation_policy_id,
            "interpolation_policy": self.interpolation_policy.value,
            "endpoint_policy": self.endpoint_policy.value,
            "unvoiced_policy": self.unvoiced_policy.value,
            "octave_tolerance_cents": self.octave_tolerance_cents,
            "reference_source_frame_count": self.reference_source_frame_count,
            "candidate_source_frame_count": self.candidate_source_frame_count,
            "requested_common_frame_count": self.requested_common_frame_count,
            "evaluated_common_frame_count": self.evaluated_common_frame_count,
            "trimmed_common_frame_count": self.trimmed_common_frame_count,
            "resampled_reference_voiced_frame_count": (
                self.resampled_reference_voiced_frame_count
            ),
            "resampled_candidate_voiced_frame_count": (
                self.resampled_candidate_voiced_frame_count
            ),
            "jointly_voiced_frame_count": self.jointly_voiced_frame_count,
            "reference_only_voiced_frame_count": (
                self.reference_only_voiced_frame_count
            ),
            "candidate_only_voiced_frame_count": (
                self.candidate_only_voiced_frame_count
            ),
            "jointly_unvoiced_frame_count": self.jointly_unvoiced_frame_count,
            "voicing_agreement_rate": self.voicing_agreement_rate,
            "jointly_voiced_rate": self.jointly_voiced_rate,
            "mean_candidate_minus_reference_hz": (
                self.mean_candidate_minus_reference_hz
            ),
            "mean_absolute_hz_error": self.mean_absolute_hz_error,
            "median_absolute_hz_error": self.median_absolute_hz_error,
            "root_mean_squared_hz_error": self.root_mean_squared_hz_error,
            "maximum_absolute_hz_error": self.maximum_absolute_hz_error,
            "mean_candidate_minus_reference_cents": (
                self.mean_candidate_minus_reference_cents
            ),
            "mean_absolute_cents_error": self.mean_absolute_cents_error,
            "median_absolute_cents_error": self.median_absolute_cents_error,
            "root_mean_squared_cents_error": self.root_mean_squared_cents_error,
            "maximum_absolute_cents_error": self.maximum_absolute_cents_error,
            "octave_error_frame_count": self.octave_error_frame_count,
            "octave_error_rate": self.octave_error_rate,
            "result_digest_sha256": self.result_digest_sha256,
        }


_EnumType = TypeVar("_EnumType", bound=Enum)


def _explicit_enum(
    value: object,
    enum_type: type[_EnumType],
    *,
    field_name: str,
) -> _EnumType:
    if type(value) is not enum_type:
        raise PitchContourAgreementError(
            f"{field_name} must be an explicit {enum_type.__name__} value"
        )
    return cast(_EnumType, value)


def _positive_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise PitchContourAgreementError(f"{field_name} must be a positive integer")
    normalized = int(value)
    if normalized <= 0:
        raise PitchContourAgreementError(f"{field_name} must be a positive integer")
    return normalized


def _finite_real(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise PitchContourAgreementError(f"{field_name} must be a finite real number")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        raise PitchContourAgreementError(
            f"{field_name} must be a finite real number"
        ) from None
    if not math.isfinite(normalized):
        raise PitchContourAgreementError(f"{field_name} must be a finite real number")
    return 0.0 if normalized == 0.0 else normalized


def _finite_tuple(
    values: object,
    *,
    field_name: str,
    max_frame_count: int,
) -> tuple[float, ...]:
    if type(values) is not tuple:
        raise PitchContourAgreementError(f"{field_name} must be an exact tuple")
    if not values:
        raise PitchContourAgreementError(f"{field_name} must not be empty")
    if len(values) > max_frame_count:
        raise PitchContourAgreementError(f"{field_name} exceeds max_frame_count")
    return tuple(
        _finite_real(value, field_name=field_name)
        for value in cast(tuple[object, ...], values)
    )


def _validate_time_grid(
    values: object,
    *,
    field_name: str,
    max_frame_count: int,
) -> tuple[float, ...]:
    normalized = _finite_tuple(
        values,
        field_name=field_name,
        max_frame_count=max_frame_count,
    )
    if any(value < 0.0 for value in normalized):
        raise PitchContourAgreementError(f"{field_name} must be non-negative")
    if any(
        current <= previous for previous, current in zip(normalized, normalized[1:])
    ):
        raise PitchContourAgreementError(f"{field_name} must be strictly increasing")
    return normalized


def _validate_contour(
    contour: object,
    *,
    field_name: str,
    max_frame_count: int,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if type(contour) is not PitchContourSeries:
        raise PitchContourAgreementError(
            f"{field_name} must be an exact PitchContourSeries value"
        )
    typed_contour = cast(PitchContourSeries, contour)
    times = _validate_time_grid(
        typed_contour.time_seconds,
        field_name=f"{field_name}.time_seconds",
        max_frame_count=max_frame_count,
    )
    frequencies = _finite_tuple(
        typed_contour.f0_hz,
        field_name=f"{field_name}.f0_hz",
        max_frame_count=max_frame_count,
    )
    if len(times) != len(frequencies):
        raise PitchContourAgreementError(
            f"{field_name} time and F0 frame counts differ"
        )
    if any(frequency < 0.0 for frequency in frequencies):
        raise PitchContourAgreementError(
            f"{field_name}.f0_hz must use zero for unvoiced frames"
        )
    return times, frequencies


def _effective_common_grid(
    common_times: tuple[float, ...],
    *,
    reference_times: tuple[float, ...],
    candidate_times: tuple[float, ...],
    endpoint_policy: PitchEndpointPolicy,
) -> tuple[tuple[float, ...], int]:
    shared_start = max(reference_times[0], candidate_times[0])
    shared_end = min(reference_times[-1], candidate_times[-1])
    if shared_start > shared_end:
        raise PitchContourAgreementError(
            "reference and candidate contours have no shared time support"
        )

    if endpoint_policy is PitchEndpointPolicy.REQUIRE_SHARED_SUPPORT:
        if common_times[0] < shared_start or common_times[-1] > shared_end:
            raise PitchContourAgreementError(
                "common_time_seconds falls outside shared contour support"
            )
        return common_times, 0

    trimmed_times = tuple(
        value for value in common_times if shared_start <= value <= shared_end
    )
    if not trimmed_times:
        raise PitchContourAgreementError(
            "endpoint trimming leaves no common time points"
        )
    return trimmed_times, len(common_times) - len(trimmed_times)


def _linear_value(
    left_time: float,
    left_f0: float,
    right_time: float,
    right_f0: float,
    target_time: float,
) -> float:
    fraction = (target_time - left_time) / (right_time - left_time)
    try:
        interpolated = math.fsum(((1.0 - fraction) * left_f0, fraction * right_f0))
    except (OverflowError, ValueError):
        raise PitchContourAgreementError(
            "pitch interpolation produced a non-finite value"
        ) from None
    if not math.isfinite(interpolated) or interpolated < 0.0:
        raise PitchContourAgreementError(
            "pitch interpolation produced a non-finite value"
        )
    return 0.0 if interpolated == 0.0 else interpolated


def _interpolate_at(
    target_time: float,
    *,
    source_times: tuple[float, ...],
    source_f0: tuple[float, ...],
    interpolation_policy: PitchInterpolationPolicy,
    preserve_gaps: bool,
) -> float:
    if target_time < source_times[0] or target_time > source_times[-1]:
        return 0.0

    right_index = bisect_left(source_times, target_time)
    if right_index < len(source_times) and source_times[right_index] == target_time:
        return source_f0[right_index]
    if right_index == 0 or right_index == len(source_times):
        return 0.0

    left_index = right_index - 1
    left_f0 = source_f0[left_index]
    right_f0 = source_f0[right_index]
    if (
        interpolation_policy is PitchInterpolationPolicy.LINEAR
        and preserve_gaps
        and (left_f0 == 0.0 or right_f0 == 0.0)
    ):
        return 0.0

    if interpolation_policy is PitchInterpolationPolicy.LINEAR:
        return _linear_value(
            source_times[left_index],
            left_f0,
            source_times[right_index],
            right_f0,
            target_time,
        )

    left_distance = target_time - source_times[left_index]
    right_distance = source_times[right_index] - target_time
    if left_distance < right_distance:
        return left_f0
    if right_distance < left_distance:
        return right_f0
    if interpolation_policy is PitchInterpolationPolicy.NEAREST_EARLIER_ON_TIE:
        return left_f0
    return right_f0


def _resample_contour(
    source_times: tuple[float, ...],
    source_f0: tuple[float, ...],
    common_times: tuple[float, ...],
    *,
    interpolation_policy: PitchInterpolationPolicy,
    unvoiced_policy: PitchUnvoicedPolicy,
) -> tuple[float, ...]:
    preserve_gaps = unvoiced_policy is PitchUnvoicedPolicy.PRESERVE_GAPS
    if preserve_gaps:
        interpolation_times = source_times
        interpolation_f0 = source_f0
    else:
        voiced_pairs = tuple(
            (time, frequency)
            for time, frequency in zip(source_times, source_f0, strict=True)
            if frequency > 0.0
        )
        if not voiced_pairs:
            return (0.0,) * len(common_times)
        interpolation_times = tuple(pair[0] for pair in voiced_pairs)
        interpolation_f0 = tuple(pair[1] for pair in voiced_pairs)

    return tuple(
        _interpolate_at(
            target_time,
            source_times=interpolation_times,
            source_f0=interpolation_f0,
            interpolation_policy=interpolation_policy,
            preserve_gaps=preserve_gaps,
        )
        for target_time in common_times
    )


def _scaled_mean(values: tuple[float, ...], *, field_name: str) -> float:
    scale = max(abs(value) for value in values)
    if scale == 0.0:
        return 0.0
    try:
        normalized_mean = math.fsum(value / scale for value in values) / len(values)
        result = scale * normalized_mean
    except (OverflowError, ValueError):
        raise PitchContourAgreementError(
            f"{field_name} could not be represented as a finite statistic"
        ) from None
    if not math.isfinite(result):
        raise PitchContourAgreementError(
            f"{field_name} could not be represented as a finite statistic"
        )
    return 0.0 if result == 0.0 else result


def _root_mean_square(values: tuple[float, ...], *, field_name: str) -> float:
    scale = max(abs(value) for value in values)
    if scale == 0.0:
        return 0.0
    try:
        normalized_square_mean = math.fsum(
            (value / scale) ** 2 for value in values
        ) / len(values)
        result = scale * math.sqrt(normalized_square_mean)
    except (OverflowError, ValueError):
        raise PitchContourAgreementError(
            f"{field_name} could not be represented as a finite statistic"
        ) from None
    if not math.isfinite(result):
        raise PitchContourAgreementError(
            f"{field_name} could not be represented as a finite statistic"
        )
    return result


def _median(values: tuple[float, ...], *, field_name: str) -> float:
    try:
        result = float(median(values))
    except (OverflowError, TypeError, ValueError):
        raise PitchContourAgreementError(
            f"{field_name} could not be represented as a finite statistic"
        ) from None
    if not math.isfinite(result):
        raise PitchContourAgreementError(
            f"{field_name} could not be represented as a finite statistic"
        )
    return 0.0 if result == 0.0 else result


def _result_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def evaluate_pitch_contour_agreement(
    reference: PitchContourSeries,
    candidate: PitchContourSeries,
    common_time_seconds: tuple[float, ...],
    *,
    interpolation_policy: PitchInterpolationPolicy,
    endpoint_policy: PitchEndpointPolicy,
    unvoiced_policy: PitchUnvoicedPolicy,
    octave_tolerance_cents: float,
    max_frame_count: int,
) -> PitchContourAgreementResult:
    """Compare two zero-is-unvoiced contours on a caller-supplied time grid.

    ``reference`` defines only the sign of candidate-minus-reference errors. It
    is not asserted to be human gold. Octave matches use an inclusive,
    caller-supplied tolerance around the nearest non-zero multiple of 1200
    cents; the tolerance must remain below the 600-cent midpoint.
    """

    frame_limit = _positive_integer(max_frame_count, field_name="max_frame_count")
    selected_interpolation = _explicit_enum(
        interpolation_policy,
        PitchInterpolationPolicy,
        field_name="interpolation_policy",
    )
    selected_endpoint = _explicit_enum(
        endpoint_policy,
        PitchEndpointPolicy,
        field_name="endpoint_policy",
    )
    selected_unvoiced = _explicit_enum(
        unvoiced_policy,
        PitchUnvoicedPolicy,
        field_name="unvoiced_policy",
    )
    octave_tolerance = _finite_real(
        octave_tolerance_cents,
        field_name="octave_tolerance_cents",
    )
    if not 0.0 <= octave_tolerance < 600.0:
        raise PitchContourAgreementError(
            "octave_tolerance_cents must be at least zero and below 600"
        )

    reference_times, reference_f0 = _validate_contour(
        reference,
        field_name="reference",
        max_frame_count=frame_limit,
    )
    candidate_times, candidate_f0 = _validate_contour(
        candidate,
        field_name="candidate",
        max_frame_count=frame_limit,
    )
    requested_common_times = _validate_time_grid(
        common_time_seconds,
        field_name="common_time_seconds",
        max_frame_count=frame_limit,
    )
    common_times, trimmed_count = _effective_common_grid(
        requested_common_times,
        reference_times=reference_times,
        candidate_times=candidate_times,
        endpoint_policy=selected_endpoint,
    )

    resampled_reference = _resample_contour(
        reference_times,
        reference_f0,
        common_times,
        interpolation_policy=selected_interpolation,
        unvoiced_policy=selected_unvoiced,
    )
    resampled_candidate = _resample_contour(
        candidate_times,
        candidate_f0,
        common_times,
        interpolation_policy=selected_interpolation,
        unvoiced_policy=selected_unvoiced,
    )

    jointly_voiced_count = 0
    reference_only_count = 0
    candidate_only_count = 0
    jointly_unvoiced_count = 0
    signed_hz_errors: list[float] = []
    signed_cents_errors: list[float] = []
    for reference_f0_hz, candidate_f0_hz in zip(
        resampled_reference,
        resampled_candidate,
        strict=True,
    ):
        reference_is_voiced = reference_f0_hz > 0.0
        candidate_is_voiced = candidate_f0_hz > 0.0
        if reference_is_voiced and candidate_is_voiced:
            jointly_voiced_count += 1
            signed_hz_error = candidate_f0_hz - reference_f0_hz
            signed_cents_error = 1_200.0 * (
                math.log2(candidate_f0_hz) - math.log2(reference_f0_hz)
            )
            if not math.isfinite(signed_hz_error) or not math.isfinite(
                signed_cents_error
            ):
                raise PitchContourAgreementError(
                    "jointly voiced error produced a non-finite value"
                )
            signed_hz_errors.append(0.0 if signed_hz_error == 0.0 else signed_hz_error)
            signed_cents_errors.append(
                0.0 if signed_cents_error == 0.0 else signed_cents_error
            )
        elif reference_is_voiced:
            reference_only_count += 1
        elif candidate_is_voiced:
            candidate_only_count += 1
        else:
            jointly_unvoiced_count += 1

    if jointly_voiced_count == 0:
        raise PitchContourAgreementError(
            "common grid contains no jointly voiced frames"
        )

    signed_hz = tuple(signed_hz_errors)
    absolute_hz = tuple(abs(value) for value in signed_hz)
    signed_cents = tuple(signed_cents_errors)
    absolute_cents = tuple(abs(value) for value in signed_cents)
    octave_error_count = sum(
        1
        for value in absolute_cents
        if (
            (nearest_octave := math.floor(value / 1_200.0 + 0.5)) >= 1
            and abs(value - nearest_octave * 1_200.0) <= octave_tolerance
        )
    )

    evaluated_frame_count = len(common_times)
    resampled_reference_voiced_count = jointly_voiced_count + reference_only_count
    resampled_candidate_voiced_count = jointly_voiced_count + candidate_only_count
    payload: dict[str, object] = {
        "evaluation_policy_id": PITCH_CONTOUR_AGREEMENT_POLICY_ID,
        "interpolation_policy": selected_interpolation.value,
        "endpoint_policy": selected_endpoint.value,
        "unvoiced_policy": selected_unvoiced.value,
        "octave_tolerance_cents": octave_tolerance,
        "reference_source_frame_count": len(reference_times),
        "candidate_source_frame_count": len(candidate_times),
        "requested_common_frame_count": len(requested_common_times),
        "evaluated_common_frame_count": evaluated_frame_count,
        "trimmed_common_frame_count": trimmed_count,
        "resampled_reference_voiced_frame_count": (resampled_reference_voiced_count),
        "resampled_candidate_voiced_frame_count": (resampled_candidate_voiced_count),
        "jointly_voiced_frame_count": jointly_voiced_count,
        "reference_only_voiced_frame_count": reference_only_count,
        "candidate_only_voiced_frame_count": candidate_only_count,
        "jointly_unvoiced_frame_count": jointly_unvoiced_count,
        "voicing_agreement_rate": (jointly_voiced_count + jointly_unvoiced_count)
        / evaluated_frame_count,
        "jointly_voiced_rate": jointly_voiced_count / evaluated_frame_count,
        "mean_candidate_minus_reference_hz": _scaled_mean(
            signed_hz,
            field_name="mean_candidate_minus_reference_hz",
        ),
        "mean_absolute_hz_error": _scaled_mean(
            absolute_hz,
            field_name="mean_absolute_hz_error",
        ),
        "median_absolute_hz_error": _median(
            absolute_hz,
            field_name="median_absolute_hz_error",
        ),
        "root_mean_squared_hz_error": _root_mean_square(
            signed_hz,
            field_name="root_mean_squared_hz_error",
        ),
        "maximum_absolute_hz_error": max(absolute_hz),
        "mean_candidate_minus_reference_cents": _scaled_mean(
            signed_cents,
            field_name="mean_candidate_minus_reference_cents",
        ),
        "mean_absolute_cents_error": _scaled_mean(
            absolute_cents,
            field_name="mean_absolute_cents_error",
        ),
        "median_absolute_cents_error": _median(
            absolute_cents,
            field_name="median_absolute_cents_error",
        ),
        "root_mean_squared_cents_error": _root_mean_square(
            signed_cents,
            field_name="root_mean_squared_cents_error",
        ),
        "maximum_absolute_cents_error": max(absolute_cents),
        "octave_error_frame_count": octave_error_count,
        "octave_error_rate": octave_error_count / jointly_voiced_count,
    }
    digest = _result_digest(payload)
    return PitchContourAgreementResult(
        evaluation_policy_id=PITCH_CONTOUR_AGREEMENT_POLICY_ID,
        interpolation_policy=selected_interpolation,
        endpoint_policy=selected_endpoint,
        unvoiced_policy=selected_unvoiced,
        octave_tolerance_cents=octave_tolerance,
        reference_source_frame_count=len(reference_times),
        candidate_source_frame_count=len(candidate_times),
        requested_common_frame_count=len(requested_common_times),
        evaluated_common_frame_count=evaluated_frame_count,
        trimmed_common_frame_count=trimmed_count,
        resampled_reference_voiced_frame_count=resampled_reference_voiced_count,
        resampled_candidate_voiced_frame_count=resampled_candidate_voiced_count,
        jointly_voiced_frame_count=jointly_voiced_count,
        reference_only_voiced_frame_count=reference_only_count,
        candidate_only_voiced_frame_count=candidate_only_count,
        jointly_unvoiced_frame_count=jointly_unvoiced_count,
        voicing_agreement_rate=cast(float, payload["voicing_agreement_rate"]),
        jointly_voiced_rate=cast(float, payload["jointly_voiced_rate"]),
        mean_candidate_minus_reference_hz=cast(
            float,
            payload["mean_candidate_minus_reference_hz"],
        ),
        mean_absolute_hz_error=cast(float, payload["mean_absolute_hz_error"]),
        median_absolute_hz_error=cast(float, payload["median_absolute_hz_error"]),
        root_mean_squared_hz_error=cast(
            float,
            payload["root_mean_squared_hz_error"],
        ),
        maximum_absolute_hz_error=cast(float, payload["maximum_absolute_hz_error"]),
        mean_candidate_minus_reference_cents=cast(
            float,
            payload["mean_candidate_minus_reference_cents"],
        ),
        mean_absolute_cents_error=cast(
            float,
            payload["mean_absolute_cents_error"],
        ),
        median_absolute_cents_error=cast(
            float,
            payload["median_absolute_cents_error"],
        ),
        root_mean_squared_cents_error=cast(
            float,
            payload["root_mean_squared_cents_error"],
        ),
        maximum_absolute_cents_error=cast(
            float,
            payload["maximum_absolute_cents_error"],
        ),
        octave_error_frame_count=octave_error_count,
        octave_error_rate=cast(float, payload["octave_error_rate"]),
        result_digest_sha256=digest,
    )


__all__ = [
    "PITCH_CONTOUR_AGREEMENT_POLICY_ID",
    "PitchContourAgreementError",
    "PitchContourAgreementResult",
    "PitchContourSeries",
    "PitchEndpointPolicy",
    "PitchInterpolationPolicy",
    "PitchUnvoicedPolicy",
    "evaluate_pitch_contour_agreement",
]
