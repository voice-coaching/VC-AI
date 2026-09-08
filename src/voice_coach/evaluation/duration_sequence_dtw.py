"""Pure, provenance-explicit DTW for positive duration sequences.

This module consumes only two in-memory tuples of duration values. It does not
read audio, run an aligner, access a dataset split, or choose scientific
thresholds. The caller must explicitly select every DTW and output policy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
import math
from numbers import Integral, Real
from typing import TypeVar, cast


DURATION_SEQUENCE_DTW_POLICY_ID = "voice_coach.duration_sequence_dtw.v1"
DURATION_SEQUENCE_DTW_HARD_MAX_CELLS = 1_000_000


class DurationSequenceDtwError(ValueError):
    """Raised when input, policy, or derived DTW state violates the contract."""


class DurationSequenceProvenance(str, Enum):
    """Caller-attested origin and comparison role of a duration sequence."""

    PREDICTED_AUTOMATIC = "predicted_automatic"
    REFERENCE_AUTOMATIC = "reference_automatic"
    REFERENCE_HUMAN_REVIEWED = "reference_human_reviewed"
    REFERENCE_CALLER_SUPPLIED_UNVERIFIED = "reference_caller_supplied_unverified"


class DurationLocalCost(str, Enum):
    """Caller-selected pointwise duration discrepancy."""

    ABSOLUTE_SECONDS = "absolute_seconds"
    ABSOLUTE_LOG_RATIO = "absolute_log_ratio"


class DurationBandPolicy(str, Enum):
    """Caller-selected restriction on admissible DTW cells."""

    UNBOUNDED = "unbounded"
    SAKOE_CHIBA_ABSOLUTE_INDEX = "sakoe_chiba_absolute_index"


class DurationStepPolicy(str, Enum):
    """Caller-selected predecessor steps."""

    SYMMETRIC_UNIT = "symmetric_unit"
    DIAGONAL_ONLY = "diagonal_only"


class DurationTieBreakPolicy(str, Enum):
    """Exact-cost tie order for predecessor selection."""

    DIAGONAL_REFERENCE_PREDICTED = "diagonal_reference_predicted"
    REFERENCE_DIAGONAL_PREDICTED = "reference_diagonal_predicted"
    PREDICTED_DIAGONAL_REFERENCE = "predicted_diagonal_reference"


class DurationNormalizationPolicy(str, Enum):
    """Caller-selected denominator for the accumulated local cost."""

    NONE = "none"
    PATH_CELL_COUNT = "path_cell_count"
    REFERENCE_COUNT = "reference_count"
    PREDICTED_COUNT = "predicted_count"


class DurationPathPolicy(str, Enum):
    """Caller-selected privacy-minimal path disclosure."""

    SUMMARY_ONLY = "summary_only"
    RETURN_INDEX_PAIRS = "return_index_pairs"


@dataclass(frozen=True, slots=True)
class DurationSequence:
    """Transient duration values with caller-attested provenance."""

    provenance: DurationSequenceProvenance
    durations_seconds: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class DurationDtwPolicy:
    """All caller-explicit DTW, resource, and path-output choices."""

    local_cost: DurationLocalCost
    band: DurationBandPolicy
    band_radius: int | None
    step: DurationStepPolicy
    tie_break: DurationTieBreakPolicy
    normalization: DurationNormalizationPolicy
    max_cells: int
    path: DurationPathPolicy


@dataclass(frozen=True, slots=True)
class DurationDtwResult:
    """Privacy-minimal DTW summary without durations or source identifiers."""

    evaluation_policy_id: str
    reference_provenance: DurationSequenceProvenance
    predicted_provenance: DurationSequenceProvenance
    local_cost_policy: DurationLocalCost
    band_policy: DurationBandPolicy
    band_radius: int | None
    step_policy: DurationStepPolicy
    tie_break_policy: DurationTieBreakPolicy
    normalization_policy: DurationNormalizationPolicy
    max_cells: int
    path_policy: DurationPathPolicy
    reference_count: int
    predicted_count: int
    full_lattice_cell_count: int
    admissible_cell_count: int
    reachable_cell_count: int
    path_cell_count: int
    diagonal_step_count: int
    reference_advance_step_count: int
    predicted_advance_step_count: int
    total_cost: float
    normalization_denominator: int
    normalized_cost: float
    index_path: tuple[tuple[int, int], ...] | None
    result_digest_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-safe public result fields."""

        return {
            "evaluation_policy_id": self.evaluation_policy_id,
            "reference_provenance": self.reference_provenance.value,
            "predicted_provenance": self.predicted_provenance.value,
            "local_cost_policy": self.local_cost_policy.value,
            "band_policy": self.band_policy.value,
            "band_radius": self.band_radius,
            "step_policy": self.step_policy.value,
            "tie_break_policy": self.tie_break_policy.value,
            "normalization_policy": self.normalization_policy.value,
            "max_cells": self.max_cells,
            "path_policy": self.path_policy.value,
            "reference_count": self.reference_count,
            "predicted_count": self.predicted_count,
            "full_lattice_cell_count": self.full_lattice_cell_count,
            "admissible_cell_count": self.admissible_cell_count,
            "reachable_cell_count": self.reachable_cell_count,
            "path_cell_count": self.path_cell_count,
            "diagonal_step_count": self.diagonal_step_count,
            "reference_advance_step_count": (self.reference_advance_step_count),
            "predicted_advance_step_count": (self.predicted_advance_step_count),
            "total_cost": self.total_cost,
            "normalization_denominator": self.normalization_denominator,
            "normalized_cost": self.normalized_cost,
            "index_path": (
                [list(pair) for pair in self.index_path]
                if self.index_path is not None
                else None
            ),
            "result_digest_sha256": self.result_digest_sha256,
        }


class _Step(str, Enum):
    DIAGONAL = "diagonal"
    REFERENCE_ADVANCE = "reference_advance"
    PREDICTED_ADVANCE = "predicted_advance"


_EnumType = TypeVar("_EnumType", bound=Enum)


def _explicit_enum(
    value: object,
    enum_type: type[_EnumType],
    *,
    field_name: str,
) -> _EnumType:
    if type(value) is not enum_type:
        raise DurationSequenceDtwError(
            f"{field_name} must be an explicit {enum_type.__name__} value"
        )
    return cast(_EnumType, value)


def _finite_positive_real(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise DurationSequenceDtwError(
            f"{field_name} must contain finite positive real numbers"
        )
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        raise DurationSequenceDtwError(
            f"{field_name} must contain finite positive real numbers"
        ) from None
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise DurationSequenceDtwError(
            f"{field_name} must contain finite positive real numbers"
        )
    return normalized


def _positive_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise DurationSequenceDtwError(f"{field_name} must be a positive integer")
    normalized = int(value)
    if normalized <= 0:
        raise DurationSequenceDtwError(f"{field_name} must be a positive integer")
    return normalized


def _validate_sequence(
    value: object,
    *,
    field_name: str,
) -> tuple[DurationSequenceProvenance, tuple[float, ...]]:
    if type(value) is not DurationSequence:
        raise DurationSequenceDtwError(
            f"{field_name} must be an exact DurationSequence value"
        )
    sequence = cast(DurationSequence, value)
    provenance = _explicit_enum(
        sequence.provenance,
        DurationSequenceProvenance,
        field_name=f"{field_name}.provenance",
    )
    if type(sequence.durations_seconds) is not tuple:
        raise DurationSequenceDtwError(
            f"{field_name}.durations_seconds must be an exact tuple"
        )
    if not sequence.durations_seconds:
        raise DurationSequenceDtwError(
            f"{field_name}.durations_seconds must not be empty"
        )
    durations = tuple(
        _finite_positive_real(
            duration,
            field_name=f"{field_name}.durations_seconds",
        )
        for duration in sequence.durations_seconds
    )
    return provenance, durations


def _validate_provenance_roles(
    reference: DurationSequenceProvenance,
    predicted: DurationSequenceProvenance,
) -> None:
    reference_values = {
        DurationSequenceProvenance.REFERENCE_AUTOMATIC,
        DurationSequenceProvenance.REFERENCE_HUMAN_REVIEWED,
        DurationSequenceProvenance.REFERENCE_CALLER_SUPPLIED_UNVERIFIED,
    }
    if reference not in reference_values:
        raise DurationSequenceDtwError(
            "reference provenance must have a reference role"
        )
    if predicted is not DurationSequenceProvenance.PREDICTED_AUTOMATIC:
        raise DurationSequenceDtwError(
            "predicted provenance must have the predicted role"
        )


def _validate_policy(
    value: object,
) -> tuple[
    DurationLocalCost,
    DurationBandPolicy,
    int | None,
    DurationStepPolicy,
    DurationTieBreakPolicy,
    DurationNormalizationPolicy,
    int,
    DurationPathPolicy,
]:
    if type(value) is not DurationDtwPolicy:
        raise DurationSequenceDtwError(
            "policy must be an exact DurationDtwPolicy value"
        )
    policy = cast(DurationDtwPolicy, value)
    local_cost = _explicit_enum(
        policy.local_cost,
        DurationLocalCost,
        field_name="policy.local_cost",
    )
    band = _explicit_enum(
        policy.band,
        DurationBandPolicy,
        field_name="policy.band",
    )
    step = _explicit_enum(
        policy.step,
        DurationStepPolicy,
        field_name="policy.step",
    )
    tie_break = _explicit_enum(
        policy.tie_break,
        DurationTieBreakPolicy,
        field_name="policy.tie_break",
    )
    normalization = _explicit_enum(
        policy.normalization,
        DurationNormalizationPolicy,
        field_name="policy.normalization",
    )
    path = _explicit_enum(
        policy.path,
        DurationPathPolicy,
        field_name="policy.path",
    )
    max_cells = _positive_integer(
        policy.max_cells,
        field_name="policy.max_cells",
    )
    if max_cells > DURATION_SEQUENCE_DTW_HARD_MAX_CELLS:
        raise DurationSequenceDtwError(
            "policy.max_cells exceeds the implementation hard limit"
        )

    if band is DurationBandPolicy.UNBOUNDED:
        if policy.band_radius is not None:
            raise DurationSequenceDtwError(
                "policy.band_radius must be None for the unbounded policy"
            )
        band_radius = None
    else:
        if isinstance(policy.band_radius, bool) or not isinstance(
            policy.band_radius, Integral
        ):
            raise DurationSequenceDtwError(
                "policy.band_radius must be a non-negative integer"
            )
        band_radius = int(policy.band_radius)
        if band_radius < 0:
            raise DurationSequenceDtwError(
                "policy.band_radius must be a non-negative integer"
            )

    return (
        local_cost,
        band,
        band_radius,
        step,
        tie_break,
        normalization,
        max_cells,
        path,
    )


def _row_bounds(
    reference_index: int,
    *,
    predicted_count: int,
    band: DurationBandPolicy,
    band_radius: int | None,
) -> tuple[int, int]:
    if band is DurationBandPolicy.UNBOUNDED:
        return 0, predicted_count
    if band_radius is None:  # pragma: no cover - guarded by policy validation
        raise AssertionError("band radius unexpectedly absent")
    return (
        max(0, reference_index - band_radius),
        min(predicted_count, reference_index + band_radius + 1),
    )


def _admissible_cell_count(
    *,
    reference_count: int,
    predicted_count: int,
    band: DurationBandPolicy,
    band_radius: int | None,
) -> int:
    return sum(
        stop - start
        for reference_index in range(reference_count)
        for start, stop in (
            _row_bounds(
                reference_index,
                predicted_count=predicted_count,
                band=band,
                band_radius=band_radius,
            ),
        )
    )


def _local_cost(
    reference_duration: float,
    predicted_duration: float,
    policy: DurationLocalCost,
) -> float:
    if policy is DurationLocalCost.ABSOLUTE_SECONDS:
        cost = abs(predicted_duration - reference_duration)
    else:
        cost = abs(math.log(predicted_duration) - math.log(reference_duration))
    if not math.isfinite(cost):
        raise DurationSequenceDtwError("a derived local duration cost is not finite")
    return cost


def _tie_order(policy: DurationTieBreakPolicy) -> tuple[_Step, ...]:
    if policy is DurationTieBreakPolicy.DIAGONAL_REFERENCE_PREDICTED:
        return (
            _Step.DIAGONAL,
            _Step.REFERENCE_ADVANCE,
            _Step.PREDICTED_ADVANCE,
        )
    if policy is DurationTieBreakPolicy.REFERENCE_DIAGONAL_PREDICTED:
        return (
            _Step.REFERENCE_ADVANCE,
            _Step.DIAGONAL,
            _Step.PREDICTED_ADVANCE,
        )
    return (
        _Step.PREDICTED_ADVANCE,
        _Step.DIAGONAL,
        _Step.REFERENCE_ADVANCE,
    )


def _normalization_denominator(
    policy: DurationNormalizationPolicy,
    *,
    path_cell_count: int,
    reference_count: int,
    predicted_count: int,
) -> int:
    if policy is DurationNormalizationPolicy.NONE:
        return 1
    if policy is DurationNormalizationPolicy.PATH_CELL_COUNT:
        return path_cell_count
    if policy is DurationNormalizationPolicy.REFERENCE_COUNT:
        return reference_count
    return predicted_count


def _result_digest(result: DurationDtwResult) -> str:
    payload = result.to_dict()
    payload.pop("result_digest_sha256")
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate_duration_sequence_dtw(
    *,
    reference: DurationSequence,
    predicted: DurationSequence,
    policy: DurationDtwPolicy,
) -> DurationDtwResult:
    """Evaluate caller-labeled reference and predicted duration sequences.

    No argument has a scientific default. ``reference`` sets the comparison
    direction only; its exact boundary provenance remains caller-attested and
    is copied into the privacy-minimal summary.
    """

    reference_provenance, reference_durations = _validate_sequence(
        reference,
        field_name="reference",
    )
    predicted_provenance, predicted_durations = _validate_sequence(
        predicted,
        field_name="predicted",
    )
    _validate_provenance_roles(reference_provenance, predicted_provenance)
    (
        local_cost_policy,
        band_policy,
        band_radius,
        step_policy,
        tie_break_policy,
        normalization_policy,
        max_cells,
        path_policy,
    ) = _validate_policy(policy)

    reference_count = len(reference_durations)
    predicted_count = len(predicted_durations)
    if (
        step_policy is DurationStepPolicy.DIAGONAL_ONLY
        and reference_count != predicted_count
    ):
        raise DurationSequenceDtwError(
            "diagonal-only policy requires equal sequence counts"
        )
    if (
        band_policy is DurationBandPolicy.SAKOE_CHIBA_ABSOLUTE_INDEX
        and band_radius is not None
        and abs(reference_count - predicted_count) > band_radius
    ):
        raise DurationSequenceDtwError(
            "the caller-selected band cannot connect both endpoints"
        )

    admissible_cell_count = _admissible_cell_count(
        reference_count=reference_count,
        predicted_count=predicted_count,
        band=band_policy,
        band_radius=band_radius,
    )
    if admissible_cell_count > max_cells:
        raise DurationSequenceDtwError(
            "the admissible DTW lattice exceeds policy.max_cells"
        )

    tie_order = _tie_order(tie_break_policy)
    tie_priority = {step_value: index for index, step_value in enumerate(tie_order)}
    allowed_steps = (
        (_Step.DIAGONAL,)
        if step_policy is DurationStepPolicy.DIAGONAL_ONLY
        else (
            _Step.DIAGONAL,
            _Step.REFERENCE_ADVANCE,
            _Step.PREDICTED_ADVANCE,
        )
    )

    previous_costs: dict[int, float] = {}
    backpointers: dict[int, _Step] = {}
    reachable_cell_count = 0

    for reference_index, reference_duration in enumerate(reference_durations):
        current_costs: dict[int, float] = {}
        start, stop = _row_bounds(
            reference_index,
            predicted_count=predicted_count,
            band=band_policy,
            band_radius=band_radius,
        )
        for predicted_index in range(start, stop):
            cell_cost = _local_cost(
                reference_duration,
                predicted_durations[predicted_index],
                local_cost_policy,
            )
            if reference_index == 0 and predicted_index == 0:
                current_costs[predicted_index] = cell_cost
                reachable_cell_count += 1
                continue

            candidates: list[tuple[float, int, _Step]] = []
            if (
                _Step.DIAGONAL in allowed_steps
                and reference_index > 0
                and predicted_index > 0
                and predicted_index - 1 in previous_costs
            ):
                candidates.append(
                    (
                        previous_costs[predicted_index - 1],
                        tie_priority[_Step.DIAGONAL],
                        _Step.DIAGONAL,
                    )
                )
            if (
                _Step.REFERENCE_ADVANCE in allowed_steps
                and reference_index > 0
                and predicted_index in previous_costs
            ):
                candidates.append(
                    (
                        previous_costs[predicted_index],
                        tie_priority[_Step.REFERENCE_ADVANCE],
                        _Step.REFERENCE_ADVANCE,
                    )
                )
            if (
                _Step.PREDICTED_ADVANCE in allowed_steps
                and predicted_index > 0
                and predicted_index - 1 in current_costs
            ):
                candidates.append(
                    (
                        current_costs[predicted_index - 1],
                        tie_priority[_Step.PREDICTED_ADVANCE],
                        _Step.PREDICTED_ADVANCE,
                    )
                )
            if not candidates:
                continue

            predecessor_cost, _, selected_step = min(candidates)
            cumulative_cost = predecessor_cost + cell_cost
            if not math.isfinite(cumulative_cost):
                raise DurationSequenceDtwError(
                    "the accumulated duration cost is not finite"
                )
            current_costs[predicted_index] = cumulative_cost
            cell_key = reference_index * predicted_count + predicted_index
            backpointers[cell_key] = selected_step
            reachable_cell_count += 1
        previous_costs = current_costs

    total_cost = previous_costs.get(predicted_count - 1)
    if total_cost is None:
        raise DurationSequenceDtwError(
            "no path connects both endpoints under the caller-selected policy"
        )

    reversed_path: list[tuple[int, int]] = []
    reference_index = reference_count - 1
    predicted_index = predicted_count - 1
    diagonal_step_count = 0
    reference_advance_step_count = 0
    predicted_advance_step_count = 0
    while True:
        reversed_path.append((reference_index, predicted_index))
        if reference_index == 0 and predicted_index == 0:
            break
        cell_key = reference_index * predicted_count + predicted_index
        path_step = backpointers.get(cell_key)
        if path_step is None:  # pragma: no cover - internal invariant
            raise DurationSequenceDtwError(
                "the selected DTW path has an invalid predecessor"
            )
        if path_step is _Step.DIAGONAL:
            reference_index -= 1
            predicted_index -= 1
            diagonal_step_count += 1
        elif path_step is _Step.REFERENCE_ADVANCE:
            reference_index -= 1
            reference_advance_step_count += 1
        else:
            predicted_index -= 1
            predicted_advance_step_count += 1

    index_path = tuple(reversed(reversed_path))
    path_cell_count = len(index_path)
    denominator = _normalization_denominator(
        normalization_policy,
        path_cell_count=path_cell_count,
        reference_count=reference_count,
        predicted_count=predicted_count,
    )
    normalized_cost = total_cost / denominator
    if not math.isfinite(normalized_cost):
        raise DurationSequenceDtwError("the normalized duration cost is not finite")

    result = DurationDtwResult(
        evaluation_policy_id=DURATION_SEQUENCE_DTW_POLICY_ID,
        reference_provenance=reference_provenance,
        predicted_provenance=predicted_provenance,
        local_cost_policy=local_cost_policy,
        band_policy=band_policy,
        band_radius=band_radius,
        step_policy=step_policy,
        tie_break_policy=tie_break_policy,
        normalization_policy=normalization_policy,
        max_cells=max_cells,
        path_policy=path_policy,
        reference_count=reference_count,
        predicted_count=predicted_count,
        full_lattice_cell_count=reference_count * predicted_count,
        admissible_cell_count=admissible_cell_count,
        reachable_cell_count=reachable_cell_count,
        path_cell_count=path_cell_count,
        diagonal_step_count=diagonal_step_count,
        reference_advance_step_count=reference_advance_step_count,
        predicted_advance_step_count=predicted_advance_step_count,
        total_cost=total_cost,
        normalization_denominator=denominator,
        normalized_cost=normalized_cost,
        index_path=(
            index_path if path_policy is DurationPathPolicy.RETURN_INDEX_PAIRS else None
        ),
        result_digest_sha256="",
    )
    return replace(result, result_digest_sha256=_result_digest(result))


__all__ = [
    "DURATION_SEQUENCE_DTW_HARD_MAX_CELLS",
    "DURATION_SEQUENCE_DTW_POLICY_ID",
    "DurationBandPolicy",
    "DurationDtwPolicy",
    "DurationDtwResult",
    "DurationLocalCost",
    "DurationNormalizationPolicy",
    "DurationPathPolicy",
    "DurationSequence",
    "DurationSequenceDtwError",
    "DurationSequenceProvenance",
    "DurationStepPolicy",
    "DurationTieBreakPolicy",
    "evaluate_duration_sequence_dtw",
]
