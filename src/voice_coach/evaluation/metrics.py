"""Reproducible model metrics and speaker-cluster uncertainty estimates.

The functions in this module intentionally make undefined cases visible:

* AUPRC is ``NaN`` when a requested class has no positive examples, unless
  ``undefined="zero"`` is selected explicitly.
* QWK, Spearman correlation, and ICC(2,1) are ``NaN`` when their denominator
  is undefined (for example, constant ratings).
* Empty inputs, non-finite numeric inputs, incompatible shapes, and invalid
  probability matrices raise ``ValueError`` instead of producing a plausible
  but meaningless number.

No function in this module reads a dataset or a split.  In particular, locked
test access remains the responsibility of the experiment runner.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.stats import spearmanr
from sklearn.metrics import average_precision_score, f1_score, mean_absolute_error


UndefinedAUPRC = Literal["nan", "zero"]


@dataclass(frozen=True)
class SliceMetricResult:
    """One metric value for one metadata slice."""

    slice_value: Hashable
    n_samples: int
    value: float
    meets_min_samples: bool


@dataclass(frozen=True)
class ClusterBootstrapResult:
    """Percentile confidence interval from a speaker-cluster bootstrap."""

    estimate: float
    confidence_lower: float
    confidence_upper: float
    standard_error: float
    confidence_level: float
    n_resamples: int
    n_valid_resamples: int
    n_speakers: int
    seed: int


@dataclass(frozen=True)
class HolmCorrectionResult:
    """Holm step-down family-wise error-rate correction result."""

    adjusted_p_values: NDArray[np.float64]
    rejected: NDArray[np.bool_]
    alpha: float


def _one_dimensional(name: str, values: ArrayLike) -> NDArray:
    array = np.asarray(values)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if array.size == 0:
        raise ValueError(f"{name} must not be empty")
    return array


def _same_length(*named_arrays: tuple[str, NDArray]) -> None:
    lengths = {len(array) for _, array in named_arrays}
    if len(lengths) != 1:
        details = ", ".join(f"{name}={len(array)}" for name, array in named_arrays)
        raise ValueError(f"input lengths differ: {details}")


def _finite_numeric(name: str, values: ArrayLike) -> NDArray[np.float64]:
    array = _one_dimensional(name, values).astype(np.float64)
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain only finite values")
    return array


def _ordered_unique(values: Sequence[object]) -> list[Hashable]:
    result: list[Hashable] = []
    observed: set[Hashable] = set()
    for value in values:
        scalar = value.item() if isinstance(value, np.generic) else value
        if scalar is None or (
            isinstance(scalar, (float, np.floating)) and np.isnan(scalar)
        ):
            raise ValueError("labels and group identifiers must not be missing")
        if not isinstance(scalar, Hashable):
            raise ValueError("labels and group identifiers must be hashable")
        if scalar not in observed:
            observed.add(scalar)
            result.append(scalar)
    return result


def _resolved_labels(
    targets: NDArray,
    predictions: NDArray | None,
    labels: Sequence[Hashable] | None,
) -> list[Hashable]:
    if labels is None:
        combined = targets.tolist()
        if predictions is not None:
            combined.extend(predictions.tolist())
        resolved = _ordered_unique(combined)
    else:
        resolved = _ordered_unique(list(labels))
        if len(resolved) != len(labels):
            raise ValueError("labels must not contain duplicates")
    if not resolved:
        raise ValueError("labels must not be empty")
    allowed = set(resolved)
    if any(value not in allowed for value in _ordered_unique(targets.tolist())):
        raise ValueError("y_true contains a value outside labels")
    if predictions is not None and any(
        value not in allowed for value in _ordered_unique(predictions.tolist())
    ):
        raise ValueError("y_pred contains a value outside labels")
    return resolved


def macro_f1(
    y_true: ArrayLike,
    y_pred: ArrayLike,
    *,
    labels: Sequence[Hashable] | None = None,
) -> float:
    """Return binary or multiclass macro F1.

    A requested class with no true or predicted examples contributes zero via
    scikit-learn's explicit ``zero_division=0`` policy.
    """

    targets = _one_dimensional("y_true", y_true)
    predictions = _one_dimensional("y_pred", y_pred)
    _same_length(("y_true", targets), ("y_pred", predictions))
    resolved = _resolved_labels(targets, predictions, labels)
    return float(
        f1_score(
            targets,
            predictions,
            labels=resolved,
            average="macro",
            zero_division=0,
        )
    )


def auprc(
    y_true: ArrayLike,
    y_score: ArrayLike,
    *,
    labels: Sequence[Hashable] | None = None,
    positive_label: Hashable | None = None,
    undefined: UndefinedAUPRC = "nan",
) -> float:
    """Return binary AUPRC or one-vs-rest macro multiclass AUPRC.

    A one-dimensional score vector is interpreted as the score for
    ``positive_label``.  If it is omitted, exactly two labels must be
    available and the second label is positive.  A two-dimensional score
    matrix is interpreted column-wise according to ``labels``; when labels
    are omitted, target labels must be integer class indices ``0..K-1``.

    AUPRC has no positive-class recall when a requested class is absent.
    ``undefined="nan"`` (the default) returns ``NaN`` if any requested class
    is absent.  ``undefined="zero"`` assigns zero to such a class before
    macro averaging.  This makes the reporting policy deliberate and avoids
    warning-dependent behavior.
    """

    if undefined not in {"nan", "zero"}:
        raise ValueError("undefined must be 'nan' or 'zero'")
    targets = _one_dimensional("y_true", y_true)
    scores = np.asarray(y_score, dtype=np.float64)
    if scores.ndim not in {1, 2}:
        raise ValueError("y_score must have shape [samples] or [samples, classes]")
    if scores.shape[0] != len(targets):
        raise ValueError("y_true and y_score lengths differ")
    if not np.all(np.isfinite(scores)):
        raise ValueError("y_score must contain only finite values")

    if scores.ndim == 1:
        resolved = _resolved_labels(targets, None, labels)
        if labels is None:
            try:
                resolved = sorted(resolved)
            except TypeError as error:
                raise ValueError(
                    "labels or positive_label are required when binary labels "
                    "do not have a natural order"
                ) from error
        if positive_label is None:
            if len(resolved) != 2:
                raise ValueError(
                    "binary AUPRC needs exactly two labels or positive_label"
                )
            positive_label = resolved[1]
        if positive_label not in set(resolved):
            raise ValueError("positive_label is outside labels")
        binary_targets = np.asarray(targets == positive_label, dtype=np.int64)
        if binary_targets.sum() == 0:
            return float("nan") if undefined == "nan" else 0.0
        return float(average_precision_score(binary_targets, scores))

    n_classes = scores.shape[1]
    if n_classes < 2:
        raise ValueError("multiclass y_score must have at least two columns")
    if positive_label is not None:
        raise ValueError("positive_label is only valid for binary y_score")
    if labels is None:
        if not np.issubdtype(targets.dtype, np.integer):
            raise ValueError("labels are required for non-integer multiclass targets")
        resolved = list(range(n_classes))
        _resolved_labels(targets, None, resolved)
    else:
        resolved = _resolved_labels(targets, None, labels)
        if len(resolved) != n_classes:
            raise ValueError("labels length must match y_score columns")

    per_class: list[float] = []
    for column, label in enumerate(resolved):
        binary_targets = np.asarray(targets == label, dtype=np.int64)
        if binary_targets.sum() == 0:
            if undefined == "nan":
                return float("nan")
            per_class.append(0.0)
        else:
            per_class.append(
                float(average_precision_score(binary_targets, scores[:, column]))
            )
    return float(np.mean(per_class))


def classification_metrics(
    y_true: ArrayLike,
    y_pred: ArrayLike,
    y_score: ArrayLike,
    *,
    labels: Sequence[Hashable] | None = None,
    positive_label: Hashable | None = None,
    undefined_auprc: UndefinedAUPRC = "nan",
) -> dict[str, float]:
    """Return the project's primary classification metrics in one wrapper."""

    return {
        "macro_f1": macro_f1(y_true, y_pred, labels=labels),
        "auprc": auprc(
            y_true,
            y_score,
            labels=labels,
            positive_label=positive_label,
            undefined=undefined_auprc,
        ),
    }


def quadratic_weighted_kappa(
    y_true: ArrayLike,
    y_pred: ArrayLike,
    *,
    labels: Sequence[Hashable] | None = None,
) -> float:
    """Return quadratic weighted kappa (QWK) for ordered categories.

    The explicit label order defines ordinal distance.  If omitted, labels are
    sorted; incomparable label types require an explicit order.  QWK is
    returned as ``NaN`` when expected
    weighted disagreement is zero, including a single constant category.
    """

    targets = _one_dimensional("y_true", y_true)
    predictions = _one_dimensional("y_pred", y_pred)
    _same_length(("y_true", targets), ("y_pred", predictions))
    resolved = _resolved_labels(targets, predictions, labels)
    if labels is None:
        try:
            resolved = sorted(resolved)
        except TypeError as error:
            raise ValueError(
                "labels are required when ordinal categories do not have a "
                "natural order"
            ) from error
    if len(resolved) < 2:
        return float("nan")

    positions = {label: index for index, label in enumerate(resolved)}
    true_indices = np.asarray([positions[value] for value in targets], dtype=np.int64)
    pred_indices = np.asarray(
        [positions[value] for value in predictions], dtype=np.int64
    )
    n_classes = len(resolved)
    observed = np.zeros((n_classes, n_classes), dtype=np.float64)
    np.add.at(observed, (true_indices, pred_indices), 1.0)
    observed /= len(targets)
    expected = np.outer(
        np.bincount(true_indices, minlength=n_classes) / len(targets),
        np.bincount(pred_indices, minlength=n_classes) / len(targets),
    )
    grid = np.arange(n_classes, dtype=np.float64)
    weights = ((grid[:, None] - grid[None, :]) / (n_classes - 1)) ** 2
    numerator = float(np.sum(weights * observed))
    denominator = float(np.sum(weights * expected))
    if np.isclose(denominator, 0.0):
        return float("nan")
    return float(1.0 - numerator / denominator)


def spearman_correlation(y_true: ArrayLike, y_pred: ArrayLike) -> float:
    """Return Spearman's rho, or ``NaN`` for fewer than two/constant values."""

    targets = _finite_numeric("y_true", y_true)
    predictions = _finite_numeric("y_pred", y_pred)
    _same_length(("y_true", targets), ("y_pred", predictions))
    if len(targets) < 2 or np.ptp(targets) == 0 or np.ptp(predictions) == 0:
        return float("nan")
    return float(spearmanr(targets, predictions).statistic)


def mae(y_true: ArrayLike, y_pred: ArrayLike) -> float:
    """Return mean absolute error for finite, non-empty vectors."""

    targets = _finite_numeric("y_true", y_true)
    predictions = _finite_numeric("y_pred", y_pred)
    _same_length(("y_true", targets), ("y_pred", predictions))
    return float(mean_absolute_error(targets, predictions))


def icc_2_1(ratings: ArrayLike) -> float:
    """Return ICC(2,1): two-way random, absolute-agreement, single measure.

    ``ratings`` has shape ``[targets, raters]``.  A complete finite matrix with
    at least two targets and two raters is required.  The implementation uses
    the Shrout-Fleiss/McGraw-Wong ANOVA form:

    ``(MSR-MSE) / (MSR+(k-1)MSE+k(MSC-MSE)/n)``.

    ICC may legitimately be negative.  A zero denominator (for example an
    entirely constant rating matrix) is reported as ``NaN``.
    """

    values = np.asarray(ratings, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError("ratings must have shape [targets, raters]")
    n_targets, n_raters = values.shape
    if n_targets < 2 or n_raters < 2:
        raise ValueError("ICC(2,1) needs at least two targets and two raters")
    if not np.all(np.isfinite(values)):
        raise ValueError("ratings must contain only finite values")

    target_means = values.mean(axis=1)
    rater_means = values.mean(axis=0)
    grand_mean = float(values.mean())
    ms_targets = float(
        n_raters * np.sum((target_means - grand_mean) ** 2) / (n_targets - 1)
    )
    ms_raters = float(
        n_targets * np.sum((rater_means - grand_mean) ** 2) / (n_raters - 1)
    )
    residuals = (
        values
        - target_means[:, None]
        - rater_means[None, :]
        + grand_mean
    )
    ms_error = float(
        np.sum(residuals**2) / ((n_targets - 1) * (n_raters - 1))
    )
    denominator = (
        ms_targets
        + (n_raters - 1) * ms_error
        + n_raters * (ms_raters - ms_error) / n_targets
    )
    scale = max(abs(ms_targets), abs(ms_raters), abs(ms_error), 1.0)
    if np.isclose(denominator, 0.0, rtol=0.0, atol=np.finfo(float).eps * scale):
        return float("nan")
    return float((ms_targets - ms_error) / denominator)


def multiclass_brier_score(
    y_true: ArrayLike,
    probabilities: ArrayLike,
    *,
    labels: Sequence[Hashable] | None = None,
) -> float:
    """Return the unscaled multiclass Brier score.

    The score is ``mean(sum_k((p_k - one_hot(y)_k)**2))`` and therefore lies
    in ``[0, 2]`` for valid probability vectors.  It is not divided by two.
    """

    targets = _one_dimensional("y_true", y_true)
    values = np.asarray(probabilities, dtype=np.float64)
    if values.ndim != 2 or values.shape[0] != len(targets):
        raise ValueError("probabilities must have shape [samples, classes]")
    if values.shape[1] < 2:
        raise ValueError("probabilities must have at least two columns")
    if not np.all(np.isfinite(values)):
        raise ValueError("probabilities must contain only finite values")
    if np.any(values < 0.0) or np.any(values > 1.0):
        raise ValueError("probabilities must lie in [0, 1]")
    if not np.allclose(values.sum(axis=1), 1.0, rtol=1e-6, atol=1e-6):
        raise ValueError("each probability row must sum to one")

    if labels is None:
        if not np.issubdtype(targets.dtype, np.integer):
            raise ValueError("labels are required for non-integer targets")
        resolved: list[Hashable] = list(range(values.shape[1]))
        _resolved_labels(targets, None, resolved)
    else:
        resolved = _resolved_labels(targets, None, labels)
        if len(resolved) != values.shape[1]:
            raise ValueError("labels length must match probability columns")
    positions = {label: index for index, label in enumerate(resolved)}
    one_hot = np.zeros_like(values)
    one_hot[
        np.arange(len(targets)),
        np.asarray([positions[value] for value in targets], dtype=np.int64),
    ] = 1.0
    return float(np.mean(np.sum((values - one_hot) ** 2, axis=1)))


def slice_metric(
    statistic: Callable[..., float],
    slice_values: ArrayLike,
    *sample_arrays: ArrayLike,
    min_samples: int = 1,
) -> list[SliceMetricResult]:
    """Evaluate a statistic independently for every observed metadata slice.

    Slice order follows first appearance.  Slices below ``min_samples`` are
    retained with ``value=NaN`` so that small or missing groups cannot silently
    disappear from an evaluation report.
    """

    if min_samples < 1:
        raise ValueError("min_samples must be positive")
    groups = _one_dimensional("slice_values", slice_values)
    if not sample_arrays:
        raise ValueError("at least one sample array is required")
    arrays = [
        _one_dimensional(f"sample_arrays[{index}]", values)
        for index, values in enumerate(sample_arrays)
    ]
    _same_length(
        ("slice_values", groups),
        *((f"sample_arrays[{index}]", array) for index, array in enumerate(arrays)),
    )
    unique_groups = _ordered_unique(groups.tolist())
    results: list[SliceMetricResult] = []
    for group in unique_groups:
        selected = np.asarray(groups == group, dtype=np.bool_)
        count = int(selected.sum())
        eligible = count >= min_samples
        value = (
            float(statistic(*(array[selected] for array in arrays)))
            if eligible
            else float("nan")
        )
        results.append(
            SliceMetricResult(
                slice_value=group,
                n_samples=count,
                value=value,
                meets_min_samples=eligible,
            )
        )
    return results


def speaker_cluster_bootstrap(
    statistic: Callable[..., float],
    speaker_ids: ArrayLike,
    *sample_arrays: ArrayLike,
    n_resamples: int = 5_000,
    confidence_level: float = 0.95,
    seed: int = 42,
) -> ClusterBootstrapResult:
    """Bootstrap whole speakers and return a deterministic percentile CI.

    Each resample draws the observed number of unique speakers with
    replacement and includes every utterance belonging to each selected
    speaker.  A speaker drawn twice contributes its utterances twice.
    Non-finite resampled statistics are omitted and disclosed through
    ``n_valid_resamples``; a non-finite full-sample estimate or zero valid
    resamples raises ``ValueError``.
    """

    if n_resamples < 1:
        raise ValueError("n_resamples must be positive")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must lie strictly between 0 and 1")
    if not isinstance(seed, (int, np.integer)):
        raise ValueError("seed must be an integer")
    speakers = _one_dimensional("speaker_ids", speaker_ids)
    if not sample_arrays:
        raise ValueError("at least one sample array is required")
    arrays = [
        _one_dimensional(f"sample_arrays[{index}]", values)
        for index, values in enumerate(sample_arrays)
    ]
    _same_length(
        ("speaker_ids", speakers),
        *((f"sample_arrays[{index}]", array) for index, array in enumerate(arrays)),
    )
    unique_speakers = _ordered_unique(speakers.tolist())
    if len(unique_speakers) < 2:
        raise ValueError("speaker bootstrap needs at least two unique speakers")
    speaker_rows = [
        np.flatnonzero(np.asarray(speakers == speaker, dtype=np.bool_))
        for speaker in unique_speakers
    ]
    estimate = float(statistic(*arrays))
    if not np.isfinite(estimate):
        raise ValueError("full-sample statistic must be finite")

    generator = np.random.default_rng(int(seed))
    bootstrap_values: list[float] = []
    for _ in range(n_resamples):
        selected_speakers = generator.integers(
            0, len(unique_speakers), size=len(unique_speakers)
        )
        selected_rows = np.concatenate(
            [speaker_rows[index] for index in selected_speakers]
        )
        value = float(statistic(*(array[selected_rows] for array in arrays)))
        if np.isfinite(value):
            bootstrap_values.append(value)
    if not bootstrap_values:
        raise ValueError("all bootstrap statistics were non-finite")

    distribution = np.asarray(bootstrap_values, dtype=np.float64)
    tail = (1.0 - confidence_level) / 2.0
    lower, upper = np.quantile(distribution, [tail, 1.0 - tail])
    standard_error = (
        float(distribution.std(ddof=1))
        if distribution.size > 1
        else float("nan")
    )
    return ClusterBootstrapResult(
        estimate=estimate,
        confidence_lower=float(lower),
        confidence_upper=float(upper),
        standard_error=standard_error,
        confidence_level=float(confidence_level),
        n_resamples=int(n_resamples),
        n_valid_resamples=int(distribution.size),
        n_speakers=len(unique_speakers),
        seed=int(seed),
    )


def holm_correction(
    p_values: ArrayLike,
    *,
    alpha: float = 0.05,
) -> HolmCorrectionResult:
    """Apply Holm's step-down correction while preserving input order."""

    values = _finite_numeric("p_values", p_values)
    if np.any((values < 0.0) | (values > 1.0)):
        raise ValueError("p_values must lie in [0, 1]")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must lie strictly between 0 and 1")

    order = np.argsort(values, kind="stable")
    ordered = values[order]
    multipliers = np.arange(len(values), 0, -1, dtype=np.float64)
    adjusted_ordered = np.minimum(
        1.0, np.maximum.accumulate(ordered * multipliers)
    )
    adjusted = np.empty_like(adjusted_ordered)
    adjusted[order] = adjusted_ordered
    return HolmCorrectionResult(
        adjusted_p_values=adjusted,
        rejected=np.asarray(adjusted <= alpha, dtype=np.bool_),
        alpha=float(alpha),
    )


__all__ = [
    "ClusterBootstrapResult",
    "HolmCorrectionResult",
    "SliceMetricResult",
    "auprc",
    "classification_metrics",
    "holm_correction",
    "icc_2_1",
    "macro_f1",
    "mae",
    "multiclass_brier_score",
    "quadratic_weighted_kappa",
    "slice_metric",
    "speaker_cluster_bootstrap",
    "spearman_correlation",
]
