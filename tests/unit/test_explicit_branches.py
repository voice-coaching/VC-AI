"""m3/m6/m8 branches must keep an undefined measurement undefined.

Two traps are specific to these modules. nPVI is undefined below two intervals, and
reporting 0.0 there would say "perfectly even rhythm" about an utterance too short
to have rhythm. Speaker similarity is a cosine over [-1, 1], and rescaling it into
[0, 1] would spend half the branch's range on degrees of "not this speaker".
"""

from __future__ import annotations

import numpy as np
import pytest

from voice_coach.evaluation.speaker_similarity import (
    SimilarityAggregation,
    SpeakerSimilarityResult,
)
from voice_coach.features.explicit import extract_explicit_features
from voice_coach.pronunciation_feedback.adapters.explicit_branches import (
    DURATION_BRANCH,
    MINIMUM_RHYTHM_INTERVALS,
    PAUSE_BRANCH,
    RHYTHM_BRANCH,
    SPEAKER_SIMILARITY_BRANCH,
    ExplicitBranchError,
    ExplicitBranchProvenance,
    build_duration_pause_branches,
    build_rhythm_branch,
    build_speaker_similarity_branch,
    normalized_npvi,
)
from voice_coach.pronunciation_feedback.domain import MetricState


def _features(*, seconds: float = 1.5, sample_rate: int = 16_000):
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    samples = (0.25 * np.sin(2 * np.pi * 180 * t)).astype(np.float64)
    # Insert a silent stretch so the pause detector has something to find.
    samples[int(0.5 * sample_rate) : int(0.8 * sample_rate)] = 0.0
    return extract_explicit_features(samples, sample_rate)


def _similarity(
    score: float = 0.82,
    *,
    reference_count: int = 3,
) -> SpeakerSimilarityResult:
    return SpeakerSimilarityResult(
        score=score,
        reference_count=reference_count,
        embedding_dimension=512,
        normalization_policy="l2",
        aggregation_policy=SimilarityAggregation("mean")
        if "mean" in [item.value for item in SimilarityAggregation]
        else list(SimilarityAggregation)[0],
        input_digest_sha256="a" * 64,
        result_digest_sha256="b" * 64,
    )


# -- m3 duration and pause ----------------------------------------------------


def test_real_features_are_extractable_from_synthetic_audio() -> None:
    features = _features()

    assert features.duration_sec == pytest.approx(1.5, abs=0.01)
    assert features.pause_count >= 1
    assert 0.0 <= features.pause_ratio <= 1.0


def test_duration_and_pause_carry_no_score_without_a_reference() -> None:
    """Whether this pausing is acceptable is a comparison, not a measurement."""

    branches = build_duration_pause_branches(_features())

    assert {branch.name for branch in branches} == {DURATION_BRANCH, PAUSE_BRANCH}
    for branch in branches:
        assert branch.state is MetricState.MISSING
        assert branch.score is None
        assert branch.config_hash == "no_reference_bank_measurement_only"


def test_a_reference_score_makes_duration_and_pause_observed() -> None:
    branches = build_duration_pause_branches(
        _features(), duration_reference_score=0.7, pause_reference_score=0.4
    )

    by_name = {branch.name: branch for branch in branches}
    assert by_name[DURATION_BRANCH].score == pytest.approx(0.7)
    assert by_name[PAUSE_BRANCH].score == pytest.approx(0.4)
    assert all(b.state is MetricState.OBSERVED for b in branches)


def test_one_reference_present_and_one_absent_are_reported_separately() -> None:
    branches = build_duration_pause_branches(
        _features(), duration_reference_score=0.7
    )

    by_name = {branch.name: branch for branch in branches}
    assert by_name[DURATION_BRANCH].state is MetricState.OBSERVED
    assert by_name[PAUSE_BRANCH].state is MetricState.MISSING


@pytest.mark.parametrize("value", [-0.1, 1.5])
def test_out_of_range_reference_fails_closed(value: float) -> None:
    with pytest.raises(ExplicitBranchError, match="within"):
        build_duration_pause_branches(_features(), duration_reference_score=value)


# -- m6 rhythm / nPVI --------------------------------------------------------


def test_rhythm_is_missing_below_two_intervals() -> None:
    """An utterance too short to have rhythm must not report even rhythm."""

    for durations in ([], [0.2]):
        branch = build_rhythm_branch(durations, reference_score=0.9)

        assert branch.state is MetricState.MISSING
        assert branch.score is None
        assert branch.config_hash == "fewer_than_two_intervals"


def test_rhythm_needs_a_reference_to_become_a_score() -> None:
    branch = build_rhythm_branch([0.20, 0.30, 0.22, 0.28])

    assert branch.state is MetricState.MISSING
    assert branch.config_hash == "no_reference_bank_measurement_only"


def test_rhythm_with_a_reference_is_observed() -> None:
    branch = build_rhythm_branch([0.20, 0.30, 0.22, 0.28], reference_score=0.65)

    assert branch.name == RHYTHM_BRANCH
    assert branch.state is MetricState.OBSERVED
    assert branch.score == pytest.approx(0.65)


def test_non_positive_durations_fail_closed() -> None:
    with pytest.raises(ExplicitBranchError, match="unusable"):
        build_rhythm_branch([0.2, 0.0, 0.3], reference_score=0.5)


def test_minimum_interval_threshold_is_exactly_two() -> None:
    assert MINIMUM_RHYTHM_INTERVALS == 2

    branch = build_rhythm_branch([0.2, 0.3], reference_score=0.5)

    assert branch.state is MetricState.OBSERVED


def test_normalized_npvi_is_none_when_undefined() -> None:
    assert normalized_npvi([]) is None
    assert normalized_npvi([0.2]) is None


def test_normalized_npvi_is_zero_for_perfectly_even_intervals() -> None:
    value = normalized_npvi([0.25, 0.25, 0.25, 0.25])

    assert value == pytest.approx(0.0)


def test_normalized_npvi_rises_with_alternating_intervals() -> None:
    even = normalized_npvi([0.25, 0.25, 0.25, 0.25])
    uneven = normalized_npvi([0.1, 0.4, 0.1, 0.4])

    assert even is not None and uneven is not None
    assert uneven > even
    assert 0.0 <= uneven <= 1.0


# -- m8 speaker similarity ----------------------------------------------------


def test_cosine_similarity_is_a_score_without_a_reference_bank() -> None:
    """A cosine is already a comparison, so this branch needs no bank."""

    branch = build_speaker_similarity_branch(_similarity(0.82))

    assert branch.name == SPEAKER_SIMILARITY_BRANCH
    assert branch.state is MetricState.OBSERVED
    assert branch.score == pytest.approx(0.82)


def test_zero_reference_speakers_means_no_comparison_happened() -> None:
    branch = build_speaker_similarity_branch(_similarity(0.9, reference_count=0))

    assert branch.state is MetricState.MISSING
    assert branch.score is None
    assert branch.config_hash == "no_reference_speaker"


def test_negative_similarity_clamps_rather_than_rescales() -> None:
    """Rescaling would spend half the range on degrees of "not this speaker"."""

    branch = build_speaker_similarity_branch(_similarity(-0.4))

    assert branch.score == pytest.approx(0.0)
    assert branch.state is MetricState.OBSERVED


def test_similarity_outside_the_cosine_range_fails_closed() -> None:
    with pytest.raises(ExplicitBranchError, match="cosine"):
        build_speaker_similarity_branch(_similarity(1.4))


def test_similarity_branch_records_the_result_digest() -> None:
    branch = build_speaker_similarity_branch(_similarity())

    assert branch.config_hash == "b" * 64


# -- provenance ---------------------------------------------------------------


def test_every_branch_names_its_backend_and_revision() -> None:
    branches = [
        *build_duration_pause_branches(_features()),
        build_rhythm_branch([0.2, 0.3]),
        build_speaker_similarity_branch(_similarity()),
    ]

    for branch in branches:
        assert branch.backend_id
        assert branch.revision


def test_custom_provenance_is_carried_through() -> None:
    marks = ExplicitBranchProvenance(backend_id="custom", revision="rev-9")

    branch = build_rhythm_branch([0.2, 0.3], provenance=marks)

    assert branch.backend_id == "custom"
    assert branch.revision == "rev-9"


def test_provenance_must_be_named() -> None:
    with pytest.raises(ExplicitBranchError, match="must name"):
        ExplicitBranchProvenance(backend_id="", revision="rev")
