"""Post-hoc confidence calibration and selective-risk metrics."""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp, softmax


def apply_temperature(logits: np.ndarray, temperature: float) -> np.ndarray:
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    values = np.asarray(logits, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError("logits must have shape [samples, classes]")
    return softmax(values / temperature, axis=1)


def fit_temperature(logits: np.ndarray, labels: np.ndarray) -> float:
    """Fit one positive temperature by calibration-set negative log likelihood."""

    values = np.asarray(logits, dtype=np.float64)
    targets = np.asarray(labels, dtype=np.int64)
    if values.ndim != 2 or targets.shape != (values.shape[0],):
        raise ValueError("logits/labels shapes are incompatible")
    if values.shape[0] == 0:
        raise ValueError("calibration set must not be empty")
    if np.any((targets < 0) | (targets >= values.shape[1])):
        raise ValueError("label is outside the logits class range")

    def negative_log_likelihood(log_temperature: float) -> float:
        scaled = values / np.exp(log_temperature)
        log_probabilities = scaled - logsumexp(scaled, axis=1, keepdims=True)
        return float(-log_probabilities[np.arange(len(targets)), targets].mean())

    result = minimize_scalar(
        negative_log_likelihood,
        bounds=(np.log(0.05), np.log(20.0)),
        method="bounded",
    )
    if not result.success:
        raise RuntimeError(f"temperature fitting failed: {result.message}")
    return float(np.exp(result.x))


def expected_calibration_error(
    probabilities: np.ndarray,
    labels: np.ndarray,
    *,
    bins: int = 15,
) -> float:
    values = np.asarray(probabilities, dtype=np.float64)
    targets = np.asarray(labels, dtype=np.int64)
    if values.ndim != 2 or targets.shape != (values.shape[0],):
        raise ValueError("probabilities/labels shapes are incompatible")
    if bins <= 0:
        raise ValueError("bins must be positive")
    confidence = values.max(axis=1)
    correct = values.argmax(axis=1) == targets
    boundaries = np.linspace(0.0, 1.0, bins + 1)
    error = 0.0
    for index in range(bins):
        if index == bins - 1:
            selected = (confidence >= boundaries[index]) & (
                confidence <= boundaries[index + 1]
            )
        else:
            selected = (confidence >= boundaries[index]) & (
                confidence < boundaries[index + 1]
            )
        if selected.any():
            error += selected.mean() * abs(
                correct[selected].mean() - confidence[selected].mean()
            )
    return float(error)


def risk_coverage_curve(
    probabilities: np.ndarray, labels: np.ndarray
) -> list[dict[str, float]]:
    """Return empirical error risk as lower-confidence predictions are withheld."""

    values = np.asarray(probabilities, dtype=np.float64)
    targets = np.asarray(labels, dtype=np.int64)
    if values.ndim != 2 or targets.shape != (values.shape[0],):
        raise ValueError("probabilities/labels shapes are incompatible")
    confidence = values.max(axis=1)
    errors = values.argmax(axis=1) != targets
    order = np.argsort(-confidence, kind="stable")
    ordered_errors = errors[order]
    cumulative_errors = np.cumsum(ordered_errors)
    total = len(targets)
    return [
        {
            "coverage": retained / total,
            "risk": float(cumulative_errors[retained - 1] / retained),
            "minimum_confidence": float(confidence[order[retained - 1]]),
        }
        for retained in range(1, total + 1)
    ]
