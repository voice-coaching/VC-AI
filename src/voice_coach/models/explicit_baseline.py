"""Scikit-learn baselines for phone errors and utterance scores."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass
class ExplicitBaseline:
    phone_classifier: Pipeline
    utterance_regressor: Pipeline

    @classmethod
    def create(cls, seed: int = 42) -> ExplicitBaseline:
        return cls(
            phone_classifier=Pipeline(
                [
                    ("scale", StandardScaler()),
                    (
                        "model",
                        LogisticRegression(
                            class_weight="balanced",
                            max_iter=1_000,
                            random_state=seed,
                        ),
                    ),
                ]
            ),
            utterance_regressor=Pipeline(
                [("scale", StandardScaler()), ("model", Ridge(alpha=1.0))]
            ),
        )

    def fit_phone(
        self, features: NDArray[np.floating], labels: NDArray[np.integer]
    ) -> ExplicitBaseline:
        self.phone_classifier.fit(features, labels)
        return self

    def fit_utterance(
        self, features: NDArray[np.floating], scores: NDArray[np.floating]
    ) -> ExplicitBaseline:
        self.utterance_regressor.fit(features, scores)
        return self
