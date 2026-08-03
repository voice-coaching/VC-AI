from __future__ import annotations

import json
import math
import re

import pytest

from voice_coach.evaluation.speaker_similarity import (
    L2_NORMALIZATION_POLICY_ID,
    SimilarityAggregation,
    SpeakerSimilarityError,
    score_speaker_similarity,
)


def test_all_explicit_aggregation_policies_have_expected_scores() -> None:
    candidate = [3.0, 4.0]
    references = [[1.0, 0.0], [0.0, 1.0]]

    expected = {
        SimilarityAggregation.PAIRWISE_MEAN: 0.7,
        SimilarityAggregation.PAIRWISE_MEDIAN: 0.7,
        SimilarityAggregation.PAIRWISE_MINIMUM: 0.6,
        SimilarityAggregation.PAIRWISE_MAXIMUM: 0.8,
        SimilarityAggregation.NORMALIZED_CENTROID: 7.0 / math.sqrt(50.0),
    }
    for aggregation, expected_score in expected.items():
        result = score_speaker_similarity(
            candidate,
            references,
            aggregation=aggregation,
        )

        assert result.score == pytest.approx(expected_score)
        assert result.reference_count == 2
        assert result.embedding_dimension == 2
        assert result.normalization_policy == L2_NORMALIZATION_POLICY_ID
        assert result.aggregation_policy is aggregation


def test_l2_normalization_and_reference_order_are_digest_invariant() -> None:
    left = score_speaker_similarity(
        [3.0, 4.0],
        [[1.0, 0.0], [0.0, 2.0]],
        aggregation=SimilarityAggregation.PAIRWISE_MEAN,
    )
    right = score_speaker_similarity(
        [6.0, 8.0],
        [[0.0, 8.0], [5.0, 0.0]],
        aggregation=SimilarityAggregation.PAIRWISE_MEAN,
    )

    assert left == right
    assert re.fullmatch(r"[0-9a-f]{64}", left.input_digest_sha256)
    assert re.fullmatch(r"[0-9a-f]{64}", left.result_digest_sha256)


def test_result_exposes_only_score_counts_dimension_policy_and_digests() -> None:
    result = score_speaker_similarity(
        [1.0, 0.0],
        [[1.0, 0.0], [0.5, 0.5]],
        aggregation=SimilarityAggregation.PAIRWISE_MAXIMUM,
    )
    payload = result.to_dict()

    assert set(payload) == {
        "score",
        "reference_count",
        "embedding_dimension",
        "normalization_policy",
        "aggregation_policy",
        "input_digest_sha256",
        "result_digest_sha256",
    }
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "candidate_embedding",
        "reference_embeddings",
        "raw_embedding",
        "normalized_embedding",
        "speaker_id",
        "candidate_id",
        "audio_path",
        "reference_id",
        "pairwise_scores",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "candidate",
    [
        [],
        [0.0, 0.0],
        [math.nan, 1.0],
        [math.inf, 1.0],
        [-math.inf, 1.0],
    ],
)
def test_invalid_candidate_embeddings_fail_closed(candidate: list[float]) -> None:
    with pytest.raises(SpeakerSimilarityError):
        score_speaker_similarity(
            candidate,
            [[1.0, 0.0]],
            aggregation=SimilarityAggregation.PAIRWISE_MEAN,
        )


@pytest.mark.parametrize(
    "references",
    [
        [],
        [[]],
        [[0.0, 0.0]],
        [[math.nan, 1.0]],
        [[math.inf, 1.0]],
    ],
)
def test_invalid_reference_embeddings_fail_closed(
    references: list[list[float]],
) -> None:
    with pytest.raises(SpeakerSimilarityError):
        score_speaker_similarity(
            [1.0, 0.0],
            references,
            aggregation=SimilarityAggregation.PAIRWISE_MEAN,
        )


def test_dimension_mismatch_fails_closed() -> None:
    with pytest.raises(SpeakerSimilarityError, match="dimension"):
        score_speaker_similarity(
            [1.0, 0.0],
            [[1.0, 0.0, 0.0]],
            aggregation=SimilarityAggregation.PAIRWISE_MEAN,
        )


def test_boolean_values_fail_closed() -> None:
    with pytest.raises(SpeakerSimilarityError, match="boolean"):
        score_speaker_similarity(
            [True, 0.0],  # type: ignore[list-item]
            [[1.0, 0.0]],
            aggregation=SimilarityAggregation.PAIRWISE_MEAN,
        )
    with pytest.raises(SpeakerSimilarityError, match="boolean"):
        score_speaker_similarity(
            [1.0, 0.0],
            [[False, 1.0]],  # type: ignore[list-item]
            aggregation=SimilarityAggregation.PAIRWISE_MEAN,
        )


@pytest.mark.parametrize(
    "aggregation",
    ["pairwise_mean", True, None],
)
def test_aggregation_requires_exact_enum(aggregation: object) -> None:
    with pytest.raises(SpeakerSimilarityError, match="explicit"):
        score_speaker_similarity(
            [1.0, 0.0],
            [[1.0, 0.0]],
            aggregation=aggregation,  # type: ignore[arg-type]
        )


def test_aggregation_has_no_implicit_default() -> None:
    with pytest.raises(TypeError):
        score_speaker_similarity(  # type: ignore[call-arg]
            [1.0, 0.0],
            [[1.0, 0.0]],
        )


def test_antipodal_reference_centroid_fails_closed() -> None:
    with pytest.raises(SpeakerSimilarityError, match="centroid"):
        score_speaker_similarity(
            [1.0, 0.0],
            [[1.0, 0.0], [-1.0, 0.0]],
            aggregation=SimilarityAggregation.NORMALIZED_CENTROID,
        )


def test_duplicate_references_remain_explicit_observations() -> None:
    result = score_speaker_similarity(
        [1.0, 0.0],
        [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
        aggregation=SimilarityAggregation.PAIRWISE_MEAN,
    )

    assert result.reference_count == 3
    assert result.score == pytest.approx(2.0 / 3.0)
