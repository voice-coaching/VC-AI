from __future__ import annotations

import hashlib

import numpy as np
import pytest

from voice_coach.features.gop import (
    GOPError,
    PHONE_INVENTORY,
    assign_speaker_fold,
    compute_gop_features,
    cross_fit_gop_features,
    fit_match_prototypes,
    posterior_log_probabilities,
)


def _speaker_for_fold(seed: int, fold: int, start: int) -> tuple[str, int]:
    counter = start
    while True:
        speaker = hashlib.sha256(f"speaker-{counter}".encode()).hexdigest()
        if assign_speaker_fold(speaker, seed=seed) == fold:
            return speaker, counter + 1
        counter += 1


def _five_fold_rows() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    speakers: list[str] = []
    labels: list[str] = []
    embeddings: list[np.ndarray] = []
    matches: list[bool] = []
    counter = 0
    for fold in range(5):
        speaker, counter = _speaker_for_fold(42, fold, counter)
        for class_index, label in enumerate(PHONE_INVENTORY):
            vector = np.zeros(len(PHONE_INVENTORY), dtype=np.float32)
            vector[class_index] = 1.0
            vector[(class_index + fold + 1) % len(PHONE_INVENTORY)] = 0.01
            speakers.append(speaker)
            labels.append(label)
            embeddings.append(vector)
            matches.append(True)
    return (
        np.asarray(embeddings, dtype=np.float32),
        np.asarray(labels, dtype=np.str_),
        np.asarray(matches, dtype=np.bool_),
        np.asarray(speakers, dtype=np.str_),
    )


def test_prototype_posterior_and_gop_formula() -> None:
    embeddings = np.eye(len(PHONE_INVENTORY), dtype=np.float32)
    prototypes = fit_match_prototypes(
        embeddings,
        np.asarray(PHONE_INVENTORY),
        np.ones(len(PHONE_INVENTORY), dtype=np.bool_),
    )
    log_posterior = posterior_log_probabilities(
        embeddings,
        prototypes,
        temperature=0.1,
    )
    features = compute_gop_features(
        embeddings,
        np.asarray(PHONE_INVENTORY),
        prototypes,
        temperature=0.1,
    )

    assert prototypes.shape == (38, 38)
    assert np.allclose(np.linalg.norm(prototypes, axis=1), 1.0)
    assert np.allclose(np.exp(log_posterior).sum(axis=1), 1.0)
    assert np.all(features.target_is_top1 == 1.0)
    assert np.all(features.gop_margin > 0.0)
    assert np.isfinite(features.as_matrix()).all()


def test_cross_fit_uses_only_fold_zero_for_temperature_selection() -> None:
    embeddings, labels, matches, speakers = _five_fold_rows()
    result = cross_fit_gop_features(
        embeddings,
        labels,
        matches,
        speakers,
        seed=42,
        temperatures=(0.05, 0.1, 0.2),
        temperature_validation_folds=(0,),
    )

    expected_match_rows = int(np.count_nonzero(result.folds == 0))
    assert result.features.as_matrix().shape == (embeddings.shape[0], 4)
    assert {item.match_row_count for item in result.temperature_candidates} == {
        expected_match_rows
    }
    assert result.selected_temperature in {0.05, 0.1, 0.2}
    assert np.isfinite(result.features.as_matrix()).all()


@pytest.mark.parametrize(
    ("embeddings", "labels", "matches", "message"),
    [
        (
            np.zeros((38, 4), dtype=np.float32),
            np.asarray(PHONE_INVENTORY),
            np.ones(38, dtype=np.bool_),
            "zero-norm",
        ),
        (
            np.eye(38, dtype=np.float32),
            np.asarray([*PHONE_INVENTORY[:-1], "UNKNOWN"]),
            np.ones(38, dtype=np.bool_),
            "out-of-inventory",
        ),
        (
            np.eye(38, dtype=np.float32),
            np.asarray(PHONE_INVENTORY),
            np.asarray([*([True] * 37), False], dtype=np.bool_),
            "class is empty",
        ),
    ],
)
def test_prototype_contract_fails_closed(
    embeddings: np.ndarray,
    labels: np.ndarray,
    matches: np.ndarray,
    message: str,
) -> None:
    with pytest.raises(GOPError, match=message):
        fit_match_prototypes(embeddings, labels, matches)


def test_speaker_fold_is_namespaced_seed_bound_and_strict() -> None:
    speaker = hashlib.sha256(b"speaker").hexdigest()
    assert assign_speaker_fold(speaker, seed=42) == assign_speaker_fold(
        speaker,
        seed=42,
    )
    assignments = {assign_speaker_fold(speaker, seed=seed) for seed in range(40, 60)}
    assert len(assignments) > 1
    with pytest.raises(GOPError, match="lowercase SHA-256"):
        assign_speaker_fold("speaker", seed=42)
