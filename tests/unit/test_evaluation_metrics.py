import math

import numpy as np
import pytest
from sklearn.metrics import cohen_kappa_score

from voice_coach.evaluation import (
    auprc,
    classification_metrics,
    holm_correction,
    icc_2_1,
    macro_f1,
    mae,
    multiclass_brier_score,
    quadratic_weighted_kappa,
    slice_metric,
    speaker_cluster_bootstrap,
    spearman_correlation,
)


def test_binary_and_multiclass_classification_metrics() -> None:
    binary = classification_metrics(
        np.array([0, 0, 1, 1]),
        np.array([0, 1, 1, 1]),
        np.array([0.1, 0.7, 0.8, 0.9]),
        labels=[0, 1],
    )
    assert binary["macro_f1"] == pytest.approx((2 / 3 + 0.8) / 2)
    assert binary["auprc"] == 1.0

    targets = np.array([0, 1, 2, 0, 1, 2])
    scores = np.eye(3)[targets] * 0.8 + 0.2 / 3
    predicted = scores.argmax(axis=1)
    assert macro_f1(targets, predicted, labels=[0, 1, 2]) == 1.0
    assert auprc(targets, scores, labels=[0, 1, 2]) == 1.0


def test_f1_fixed_label_without_support_contributes_zero() -> None:
    assert macro_f1([0, 0], [0, 0], labels=[0, 1]) == 0.5


def test_auprc_absent_positive_policy_is_explicit() -> None:
    assert math.isnan(auprc([0, 0], [0.2, 0.1], labels=[0, 1]))
    assert (
        auprc([0, 0], [0.2, 0.1], labels=[0, 1], undefined="zero") == 0.0
    )

    multiclass_scores = np.array([[0.8, 0.2, 0.0], [0.7, 0.3, 0.0]])
    assert math.isnan(auprc([0, 1], multiclass_scores, labels=[0, 1, 2]))
    assert np.isfinite(
        auprc(
            [0, 1],
            multiclass_scores,
            labels=[0, 1, 2],
            undefined="zero",
        )
    )


def test_binary_auprc_default_positive_label_uses_sorted_order() -> None:
    # The result must not depend on which class happens to appear first.
    assert auprc([1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2]) == 1.0


def test_qwk_matches_sklearn_and_constant_case_is_nan() -> None:
    targets = np.array([0, 0, 1, 1, 2, 2])
    predictions = np.array([0, 1, 1, 2, 2, 2])
    expected = cohen_kappa_score(targets, predictions, weights="quadratic")
    assert quadratic_weighted_kappa(
        targets, predictions, labels=[0, 1, 2]
    ) == pytest.approx(expected)
    assert math.isnan(quadratic_weighted_kappa([1, 1], [1, 1]))


def test_spearman_and_mae_with_degenerate_handling() -> None:
    assert spearman_correlation([1, 2, 3], [10, 20, 30]) == 1.0
    assert mae([1, 2, 3], [2, 2, 1]) == 1.0
    assert math.isnan(spearman_correlation([1, 1, 1], [1, 2, 3]))
    with pytest.raises(ValueError, match="finite"):
        mae([1, np.nan], [1, 2])


def test_icc_2_1_perfect_agreement_and_constant_case() -> None:
    ratings = np.array(
        [
            [1.0, 1.0, 1.0],
            [2.0, 2.0, 2.0],
            [4.0, 4.0, 4.0],
            [5.0, 5.0, 5.0],
        ]
    )
    assert icc_2_1(ratings) == 1.0
    assert math.isnan(icc_2_1(np.ones((3, 2))))
    with pytest.raises(ValueError, match="at least two"):
        icc_2_1(np.ones((1, 2)))


def test_icc_2_1_known_reference_value() -> None:
    # Hand-calculated from the ICC(A,1) ANOVA formula.
    ratings = np.array(
        [
            [9, 2, 5, 8],
            [6, 1, 3, 2],
            [8, 4, 6, 8],
            [7, 1, 2, 6],
            [10, 5, 6, 9],
            [6, 2, 4, 7],
        ],
        dtype=float,
    )
    assert icc_2_1(ratings) == pytest.approx(0.2897637795)


def test_multiclass_brier_score_and_probability_validation() -> None:
    targets = np.array([0, 2])
    perfect = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    assert multiclass_brier_score(targets, perfect) == 0.0

    uniform = np.full((2, 3), 1 / 3)
    assert multiclass_brier_score(targets, uniform) == pytest.approx(2 / 3)
    with pytest.raises(ValueError, match="sum to one"):
        multiclass_brier_score(targets, np.full((2, 3), 0.2))


def test_slice_metric_keeps_small_slices_visible() -> None:
    result = slice_metric(
        mae,
        np.array(["zh", "zh", "ja"]),
        np.array([1.0, 3.0, 2.0]),
        np.array([2.0, 3.0, 4.0]),
        min_samples=2,
    )
    assert [item.slice_value for item in result] == ["zh", "ja"]
    assert result[0].value == 0.5
    assert result[0].meets_min_samples
    assert result[1].n_samples == 1
    assert math.isnan(result[1].value)
    assert not result[1].meets_min_samples


def test_speaker_cluster_bootstrap_is_deterministic_and_clustered() -> None:
    speakers = np.array(["s1", "s1", "s2", "s2", "s3", "s3"])
    targets = np.array([0, 0, 1, 1, 0, 0])
    predictions = np.array([0, 1, 1, 1, 0, 0])

    first = speaker_cluster_bootstrap(
        lambda y, pred: macro_f1(y, pred, labels=[0, 1]),
        speakers,
        targets,
        predictions,
        n_resamples=200,
        seed=17,
    )
    second = speaker_cluster_bootstrap(
        lambda y, pred: macro_f1(y, pred, labels=[0, 1]),
        speakers,
        targets,
        predictions,
        n_resamples=200,
        seed=17,
    )
    assert first == second
    assert first.n_speakers == 3
    assert first.n_resamples == first.n_valid_resamples == 200
    assert first.confidence_lower <= first.estimate <= first.confidence_upper


def test_speaker_cluster_bootstrap_rejects_one_speaker() -> None:
    with pytest.raises(ValueError, match="at least two"):
        speaker_cluster_bootstrap(
            mae,
            ["only", "only"],
            [1.0, 2.0],
            [1.0, 3.0],
            n_resamples=10,
        )


def test_holm_correction_preserves_order_and_controls_family() -> None:
    result = holm_correction([0.03, 0.001, 0.04, 0.20], alpha=0.05)
    assert result.adjusted_p_values == pytest.approx([0.09, 0.004, 0.09, 0.20])
    assert result.rejected.tolist() == [False, True, False, False]
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        holm_correction([0.1, 1.1])


def test_metric_shape_validation_is_explicit() -> None:
    with pytest.raises(ValueError, match="lengths differ"):
        macro_f1([0, 1], [0])
    with pytest.raises(ValueError, match="one-dimensional"):
        spearman_correlation([[1, 2]], [1, 2])
    with pytest.raises(ValueError, match="non-integer"):
        multiclass_brier_score(["low", "high"], np.full((2, 2), 0.5))
