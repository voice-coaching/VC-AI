import numpy as np

from voice_coach.evaluation import (
    apply_temperature,
    expected_calibration_error,
    fit_temperature,
    risk_coverage_curve,
)


def test_temperature_fitting_softens_overconfident_errors() -> None:
    logits = np.array([[8.0, 0.0], [8.0, 0.0], [0.0, 8.0], [0.0, 8.0]])
    labels = np.array([0, 1, 1, 0])
    temperature = fit_temperature(logits, labels)
    assert temperature > 1.0
    probabilities = apply_temperature(logits, temperature)
    assert np.allclose(probabilities.sum(axis=1), 1.0)


def test_ece_is_zero_for_exact_bin_accuracy() -> None:
    probabilities = np.array([[0.5, 0.5], [0.5, 0.5]])
    labels = np.array([0, 1])
    assert expected_calibration_error(probabilities, labels, bins=2) == 0.0


def test_risk_coverage_orders_by_confidence() -> None:
    probabilities = np.array([[0.9, 0.1], [0.4, 0.6], [0.55, 0.45]])
    labels = np.array([0, 1, 1])
    curve = risk_coverage_curve(probabilities, labels)
    assert curve[0]["risk"] == 0.0
    assert curve[-1]["coverage"] == 1.0
    assert curve[-1]["risk"] == 1 / 3
