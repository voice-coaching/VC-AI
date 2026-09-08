import numpy as np

from voice_coach.models.explicit_baseline import ExplicitBaseline


def test_explicit_baseline_fits_toy_data() -> None:
    features = np.array(
        [[0.0, 0.0], [0.1, 0.2], [0.9, 0.8], [1.0, 1.0]], dtype=np.float64
    )
    phone_labels = np.array([0, 0, 1, 1])
    utterance_scores = np.array([0.1, 0.2, 0.8, 0.9])
    baseline = ExplicitBaseline.create().fit_phone(
        features, phone_labels
    ).fit_utterance(features, utterance_scores)

    assert baseline.phone_classifier.predict(features).tolist() == [0, 0, 1, 1]
    assert baseline.utterance_regressor.predict(features).shape == (4,)
