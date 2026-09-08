"""Canonical nine-axis labels and missing-label masking for PDF Phase 0.

The module preserves the paper's complete assessment vector without choosing
raw-label normalization, loss weights, aggregation, thresholds, or a model.
Callers must explicitly attest that every observed score was already
normalized to the unit interval.  Missing labels are represented by a
separate boolean mask and are never silently interpreted as observed zeros.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import math
from numbers import Real


NINE_AXIS_LABEL_POLICY_ID = "voice_coach.nine_axis_labels.v1"


class NineAxisLabelError(ValueError):
    """Raised when a nine-axis label or prediction violates the contract."""


class AssessmentAxis(str, Enum):
    """The nine dimensions fixed by design principle P1 in the PDF."""

    CONTENT = "content"
    SEGMENTAL = "segmental"
    DURATION = "duration"
    PAUSE = "pause"
    INTONATION = "intonation"
    ENERGY = "energy"
    RHYTHM = "rhythm"
    NATURALNESS = "naturalness"
    COMPREHENSIBILITY = "comprehensibility"


class AxisScoreNormalization(str, Enum):
    """Explicit score-scale assertion accepted by this primitive."""

    CALLER_NORMALIZED_UNIT_INTERVAL = "caller_normalized_unit_interval"


CANONICAL_AXIS_ORDER: tuple[AssessmentAxis, ...] = tuple(AssessmentAxis)
_AXIS_SET = frozenset(CANONICAL_AXIS_ORDER)


def _unit_interval_score(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise NineAxisLabelError(f"{field_name} must be a finite real number in [0, 1]")
    try:
        score = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise NineAxisLabelError(
            f"{field_name} must be a finite real number in [0, 1]"
        ) from exc
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise NineAxisLabelError(f"{field_name} must be a finite real number in [0, 1]")
    return 0.0 if score == 0.0 else score


def _validate_axis_mapping(
    labels_by_axis: Mapping[AssessmentAxis, object],
) -> None:
    if not isinstance(labels_by_axis, Mapping):
        raise NineAxisLabelError("labels_by_axis must be a mapping")

    provided_axes: set[AssessmentAxis] = set()
    for axis in labels_by_axis:
        if type(axis) is not AssessmentAxis:
            raise NineAxisLabelError(
                "labels_by_axis keys must be explicit AssessmentAxis values"
            )
        provided_axes.add(axis)

    if provided_axes != _AXIS_SET or len(labels_by_axis) != len(CANONICAL_AXIS_ORDER):
        missing = sorted(axis.value for axis in _AXIS_SET - provided_axes)
        unexpected = sorted(axis.value for axis in provided_axes - _AXIS_SET)
        raise NineAxisLabelError(
            "labels_by_axis must contain every canonical axis exactly once; "
            f"missing={missing}, unexpected={unexpected}"
        )


@dataclass(frozen=True, slots=True)
class NineAxisLabelVector:
    """Dense normalized labels plus a separate observed-label mask.

    A zero in ``normalized_scores`` is only a label when the same position in
    ``observed_mask`` is true.  False positions use zero solely as an inert
    dense-tensor fill value.
    """

    normalized_scores: tuple[float, ...]
    observed_mask: tuple[bool, ...]
    normalization_policy: AxisScoreNormalization

    def __post_init__(self) -> None:
        if type(self.normalization_policy) is not AxisScoreNormalization:
            raise NineAxisLabelError(
                "normalization_policy must be an explicit AxisScoreNormalization"
            )
        if type(self.normalized_scores) is not tuple or len(
            self.normalized_scores
        ) != len(CANONICAL_AXIS_ORDER):
            raise NineAxisLabelError("normalized_scores must be an exact nine-tuple")
        if type(self.observed_mask) is not tuple or len(self.observed_mask) != len(
            CANONICAL_AXIS_ORDER
        ):
            raise NineAxisLabelError("observed_mask must be an exact nine-tuple")

        observed_count = 0
        for index, (score, is_observed) in enumerate(
            zip(self.normalized_scores, self.observed_mask, strict=True)
        ):
            if type(score) is not float or (
                score == 0.0 and math.copysign(1.0, score) < 0.0
            ):
                raise NineAxisLabelError(
                    "normalized_scores must contain canonical built-in floats"
                )
            validated_score = _unit_interval_score(
                score,
                field_name=f"normalized_scores[{index}]",
            )
            if type(is_observed) is not bool:
                raise NineAxisLabelError(
                    f"observed_mask[{index}] must be an exact boolean"
                )
            if is_observed:
                observed_count += 1
            elif validated_score != 0.0:
                raise NineAxisLabelError(
                    "unobserved axes must use the canonical zero fill value"
                )
        if observed_count == 0:
            raise NineAxisLabelError("at least one axis label must be observed")

    @property
    def observed_count(self) -> int:
        """Return the number of supervised axes."""

        return sum(self.observed_mask)

    @property
    def missing_axes(self) -> tuple[AssessmentAxis, ...]:
        """Return missing axes in canonical order."""

        return tuple(
            axis
            for axis, is_observed in zip(
                CANONICAL_AXIS_ORDER,
                self.observed_mask,
                strict=True,
            )
            if not is_observed
        )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-safe dense label contract."""

        return {
            "policy_id": NINE_AXIS_LABEL_POLICY_ID,
            "normalization_policy": self.normalization_policy.value,
            "axis_order": [axis.value for axis in CANONICAL_AXIS_ORDER],
            "normalized_scores": list(self.normalized_scores),
            "observed_mask": list(self.observed_mask),
            "observed_count": self.observed_count,
        }


@dataclass(frozen=True, slots=True)
class ObservedAxisPairs:
    """Prediction/target pairs for observed axes only, without aggregation."""

    axes: tuple[AssessmentAxis, ...]
    prediction_scores: tuple[float, ...]
    target_scores: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.axes or len(self.axes) > len(CANONICAL_AXIS_ORDER):
            raise NineAxisLabelError("axes must contain one to nine values")
        if type(self.axes) is not tuple or any(
            type(axis) is not AssessmentAxis for axis in self.axes
        ):
            raise NineAxisLabelError("axes must be an exact AssessmentAxis tuple")
        canonical_subset = tuple(
            axis for axis in CANONICAL_AXIS_ORDER if axis in self.axes
        )
        if self.axes != canonical_subset or len(set(self.axes)) != len(self.axes):
            raise NineAxisLabelError("axes must be a unique canonical-order subset")
        if (
            type(self.prediction_scores) is not tuple
            or type(self.target_scores) is not tuple
            or len(self.prediction_scores) != len(self.axes)
            or len(self.target_scores) != len(self.axes)
        ):
            raise NineAxisLabelError(
                "prediction_scores and target_scores must match axes"
            )
        for index, prediction in enumerate(self.prediction_scores):
            if type(prediction) is not float or (
                prediction == 0.0 and math.copysign(1.0, prediction) < 0.0
            ):
                raise NineAxisLabelError(
                    "prediction_scores must contain canonical built-in floats"
                )
            _unit_interval_score(
                prediction,
                field_name=f"prediction_scores[{index}]",
            )
        for index, target in enumerate(self.target_scores):
            if type(target) is not float or (
                target == 0.0 and math.copysign(1.0, target) < 0.0
            ):
                raise NineAxisLabelError(
                    "target_scores must contain canonical built-in floats"
                )
            _unit_interval_score(target, field_name=f"target_scores[{index}]")

    @property
    def observed_count(self) -> int:
        """Return the number of prediction/target pairs."""

        return len(self.axes)

    def to_dict(self) -> dict[str, object]:
        """Return observed pairs without identifiers or an implicit loss."""

        return {
            "policy_id": NINE_AXIS_LABEL_POLICY_ID,
            "axes": [axis.value for axis in self.axes],
            "prediction_scores": list(self.prediction_scores),
            "target_scores": list(self.target_scores),
            "observed_count": self.observed_count,
        }


def build_nine_axis_label_vector(
    labels_by_axis: Mapping[AssessmentAxis, object],
    *,
    normalization_policy: AxisScoreNormalization,
) -> NineAxisLabelVector:
    """Build the canonical dense vector and derive its missing-label mask.

    Every canonical key is required.  ``None`` means unavailable and becomes
    a false mask value plus a zero dense fill.  The caller must select the
    normalization enum explicitly; raw 1–5 labels or implicit conversion are
    not accepted.
    """

    if type(normalization_policy) is not AxisScoreNormalization:
        raise NineAxisLabelError(
            "normalization_policy must be an explicit AxisScoreNormalization"
        )
    _validate_axis_mapping(labels_by_axis)

    normalized_scores: list[float] = []
    observed_mask: list[bool] = []
    for axis in CANONICAL_AXIS_ORDER:
        value = labels_by_axis[axis]
        if value is None:
            normalized_scores.append(0.0)
            observed_mask.append(False)
        else:
            normalized_scores.append(
                _unit_interval_score(
                    value,
                    field_name=f"labels_by_axis[{axis.value}]",
                )
            )
            observed_mask.append(True)

    return NineAxisLabelVector(
        normalized_scores=tuple(normalized_scores),
        observed_mask=tuple(observed_mask),
        normalization_policy=normalization_policy,
    )


def select_observed_axis_pairs(
    prediction_scores: Sequence[object],
    labels: NineAxisLabelVector,
) -> ObservedAxisPairs:
    """Select canonical prediction/target pairs where labels are observed.

    The function intentionally does not compute a loss, axis weighting,
    aggregate score, threshold, or pass/fail decision.
    """

    if type(labels) is not NineAxisLabelVector:
        raise NineAxisLabelError("labels must be an exact NineAxisLabelVector")
    if isinstance(prediction_scores, (str, bytes, bytearray)) or not isinstance(
        prediction_scores,
        Sequence,
    ):
        raise NineAxisLabelError("prediction_scores must be a nine-value sequence")
    if len(prediction_scores) != len(CANONICAL_AXIS_ORDER):
        raise NineAxisLabelError("prediction_scores must contain exactly nine values")

    validated_predictions = tuple(
        _unit_interval_score(
            score,
            field_name=f"prediction_scores[{index}]",
        )
        for index, score in enumerate(prediction_scores)
    )
    observed_axes: list[AssessmentAxis] = []
    observed_predictions: list[float] = []
    observed_targets: list[float] = []
    for axis, prediction, target, is_observed in zip(
        CANONICAL_AXIS_ORDER,
        validated_predictions,
        labels.normalized_scores,
        labels.observed_mask,
        strict=True,
    ):
        if is_observed:
            observed_axes.append(axis)
            observed_predictions.append(prediction)
            observed_targets.append(target)

    return ObservedAxisPairs(
        axes=tuple(observed_axes),
        prediction_scores=tuple(observed_predictions),
        target_scores=tuple(observed_targets),
    )
