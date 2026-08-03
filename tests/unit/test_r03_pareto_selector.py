from __future__ import annotations

import copy
import hashlib
import math
import random

import pytest

from voice_coach.references.r03_pareto_selector import (
    DiversityPolicy,
    NormalizationPolicy,
    ParetoPolicy,
    R03SelectionError,
    SelectorPolicy,
    replay_source_blind_selection,
    select_source_blind_references,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate(
    index: int,
    *,
    sentence_id: str = "R00-01",
    quality: float | None = None,
    error: float | None = None,
    speaker: int | None = None,
    speed: str | None = None,
    pitch: str | None = None,
) -> dict[str, object]:
    speeds = ("slow", "neutral", "fast")
    pitches = ("low", "neutral", "high")
    return {
        "candidate_id": f"r01c_{index:032x}",
        "sentence_id": sentence_id,
        "target_text_sha256": _sha(f"text-{sentence_id}"),
        "r02_eligible_row_sha256": _sha(f"eligible-{sentence_id}-{index}"),
        "objective_vector": {
            "quality": float(index if quality is None else quality),
            "error": float((10 - index) if error is None else error),
        },
        "speed_bucket": speed or speeds[index % len(speeds)],
        "pitch_bucket": pitch or pitches[index % len(pitches)],
        "blinded_speaker_cluster_id_sha256": _sha(
            f"speaker-{index if speaker is None else speaker}"
        ),
    }


def _policy(
    *,
    selected_count: int = 3,
    epsilon: float = 0.0,
    tie_policy: str = "keep_all",
    shortfall: str = "fill_by_front_rank",
    overflow: str = "diversity_select",
    minimum_speakers: int = 2,
    minimum_speeds: int = 2,
    minimum_pitches: int = 2,
    minimum_separation: float = 0.0,
    seed: int = 17,
) -> SelectorPolicy:
    return SelectorPolicy(
        objective_ids=("quality", "error"),
        selected_count=selected_count,
        candidate_order_policy="canonical_sentence_then_candidate_id",
        normalization=NormalizationPolicy(
            method="identity",
            scope="per_sentence",
            clipping_bounds=None,
            constant_objective_policy="error",
        ),
        pareto=ParetoPolicy(
            direction_by_objective={
                "quality": "maximize",
                "error": "minimize",
            },
            epsilon_by_objective={
                "quality": epsilon,
                "error": epsilon,
            },
            tie_policy=tie_policy,  # type: ignore[arg-type]
            frontier_shortfall_policy=shortfall,  # type: ignore[arg-type]
            frontier_overflow_policy=overflow,  # type: ignore[arg-type]
        ),
        diversity=DiversityPolicy(
            distance_metric="weighted_euclidean",
            distance_objective_ids=("quality", "error"),
            objective_weights={"quality": 1.0, "error": 1.0},
            speaker_mismatch_weight=1.0,
            speed_mismatch_weight=1.0,
            pitch_mismatch_weight=1.0,
            minimum_separation=minimum_separation,
            minimum_distinct_speakers=minimum_speakers,
            minimum_distinct_speed_buckets=minimum_speeds,
            minimum_distinct_pitch_buckets=minimum_pitches,
            selection_algorithm="exhaustive_maximin",
            seed=seed,
            combination_limit=10_000,
        ),
    )


def test_mixed_objective_directions_produce_per_sentence_fronts() -> None:
    rows = [
        _candidate(1, quality=9.0, error=1.0),
        _candidate(2, quality=8.0, error=2.0),
        _candidate(3, quality=7.0, error=3.0),
        _candidate(4, quality=6.0, error=4.0),
    ]

    result = select_source_blind_references(rows, policy=_policy())

    sentence = result.sentences[0]
    assert sentence.non_dominated_fronts == (
        ("r01c_" + f"{1:032x}",),
        ("r01c_" + f"{2:032x}",),
        ("r01c_" + f"{3:032x}",),
        ("r01c_" + f"{4:032x}",),
    )
    assert set(sentence.selected_candidate_ids) == {
        "r01c_" + f"{1:032x}",
        "r01c_" + f"{2:032x}",
        "r01c_" + f"{3:032x}",
    }


def test_epsilon_tie_policy_is_explicit_and_canonical() -> None:
    rows = [
        _candidate(1, quality=10.00, error=1.00),
        _candidate(2, quality=10.05, error=0.96),
        _candidate(3, quality=9.00, error=2.00),
        _candidate(4, quality=8.00, error=3.00),
    ]
    keep = select_source_blind_references(
        rows,
        policy=_policy(epsilon=0.1, tie_policy="keep_all"),
    )
    canonical = select_source_blind_references(
        rows,
        policy=_policy(
            epsilon=0.1,
            tie_policy="canonical_candidate_id",
        ),
    )

    assert keep.sentences[0].non_dominated_fronts[0] == (
        "r01c_" + f"{1:032x}",
        "r01c_" + f"{2:032x}",
    )
    assert canonical.sentences[0].tie_suppressed_candidate_ids == (
        "r01c_" + f"{2:032x}",
    )
    assert canonical.sentences[0].non_dominated_fronts[0] == ("r01c_" + f"{1:032x}",)


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_nonfinite_objective_fails_closed(bad_value: float) -> None:
    rows = [_candidate(index) for index in range(1, 5)]
    rows[0]["objective_vector"]["quality"] = bad_value  # type: ignore[index]

    with pytest.raises(R03SelectionError, match="finite"):
        select_source_blind_references(rows, policy=_policy())


def test_missing_objective_fails_closed() -> None:
    rows = [_candidate(index) for index in range(1, 5)]
    objective_vector = rows[0]["objective_vector"]
    assert isinstance(objective_vector, dict)
    del objective_vector["error"]

    with pytest.raises(R03SelectionError, match="objective keys mismatch"):
        select_source_blind_references(rows, policy=_policy())


def test_non_denylisted_extra_selector_field_still_fails_exact_allowlist() -> None:
    rows = [_candidate(index) for index in range(1, 5)]
    rows[0]["debug_metadata"] = "must not enter selector"

    with pytest.raises(R03SelectionError, match="exactly match"):
        select_source_blind_references(rows, policy=_policy())


def test_duplicate_candidate_id_fails_closed() -> None:
    rows = [_candidate(index) for index in range(1, 5)]
    rows[1]["candidate_id"] = rows[0]["candidate_id"]

    with pytest.raises(R03SelectionError, match="duplicate candidate_id"):
        select_source_blind_references(rows, policy=_policy())


def test_frontier_shortfall_can_fail_or_fill_by_rank() -> None:
    rows = [
        _candidate(1, quality=9.0, error=1.0),
        _candidate(2, quality=8.0, error=2.0),
        _candidate(3, quality=7.0, error=3.0),
        _candidate(4, quality=6.0, error=4.0),
    ]

    with pytest.raises(R03SelectionError, match="frontier shortfall"):
        select_source_blind_references(
            rows,
            policy=_policy(shortfall="fail"),
        )
    filled = select_source_blind_references(rows, policy=_policy())
    assert len(filled.sentences[0].selected_candidate_ids) == 3


def test_frontier_overflow_can_fail_or_use_diversity_selection() -> None:
    rows = [
        _candidate(1, quality=9.0, error=4.0),
        _candidate(2, quality=8.0, error=3.0),
        _candidate(3, quality=7.0, error=2.0),
        _candidate(4, quality=6.0, error=1.0),
    ]

    with pytest.raises(R03SelectionError, match="frontier overflow"):
        select_source_blind_references(
            rows,
            policy=_policy(overflow="fail"),
        )
    selected = select_source_blind_references(rows, policy=_policy())
    assert len(selected.sentences[0].non_dominated_fronts[0]) == 4
    assert len(selected.sentences[0].selected_candidate_ids) == 3
    assert selected.sentences[0].combinations_evaluated == 4


def test_speaker_speed_pitch_constraints_are_enforced() -> None:
    rows = [
        _candidate(
            index,
            quality=10.0 - index,
            error=float(index),
            speaker=1,
            speed="neutral",
            pitch="neutral",
        )
        for index in range(1, 5)
    ]

    with pytest.raises(R03SelectionError, match="diversity constraints"):
        select_source_blind_references(rows, policy=_policy())


def test_minimum_separation_infeasibility_fails_closed() -> None:
    rows = [
        _candidate(1, quality=1.0, error=1.0),
        _candidate(2, quality=1.1, error=1.1),
        _candidate(3, quality=1.2, error=1.2),
        _candidate(4, quality=1.3, error=1.3),
    ]

    with pytest.raises(R03SelectionError, match="diversity constraints"):
        select_source_blind_references(
            rows,
            policy=_policy(minimum_separation=100.0),
        )


def test_combination_limit_fails_before_unbounded_exhaustive_search() -> None:
    rows = [
        _candidate(index, quality=10.0 - index, error=6.0 - index)
        for index in range(1, 6)
    ]
    base = _policy()
    limited = SelectorPolicy(
        objective_ids=base.objective_ids,
        selected_count=base.selected_count,
        candidate_order_policy=base.candidate_order_policy,
        normalization=base.normalization,
        pareto=base.pareto,
        diversity=DiversityPolicy(
            distance_metric=base.diversity.distance_metric,
            distance_objective_ids=base.diversity.distance_objective_ids,
            objective_weights=base.diversity.objective_weights,
            speaker_mismatch_weight=base.diversity.speaker_mismatch_weight,
            speed_mismatch_weight=base.diversity.speed_mismatch_weight,
            pitch_mismatch_weight=base.diversity.pitch_mismatch_weight,
            minimum_separation=base.diversity.minimum_separation,
            minimum_distinct_speakers=base.diversity.minimum_distinct_speakers,
            minimum_distinct_speed_buckets=(
                base.diversity.minimum_distinct_speed_buckets
            ),
            minimum_distinct_pitch_buckets=(
                base.diversity.minimum_distinct_pitch_buckets
            ),
            selection_algorithm=base.diversity.selection_algorithm,
            seed=base.diversity.seed,
            combination_limit=2,
        ),
    )

    with pytest.raises(R03SelectionError, match="combination_limit exceeded"):
        select_source_blind_references(rows, policy=limited)


@pytest.mark.parametrize(
    "field",
    ["source_kind", "model_name", "r04_score", "learner_outcome"],
)
def test_denylisted_source_or_outcome_field_never_enters_selector(
    field: str,
) -> None:
    rows = [_candidate(index) for index in range(1, 5)]
    rows[0][field] = "leak"

    with pytest.raises(R03SelectionError, match="denylisted"):
        select_source_blind_references(rows, policy=_policy())


def test_input_order_does_not_change_fronts_selection_or_digests() -> None:
    rows = [
        _candidate(1, quality=9.0, error=4.0),
        _candidate(2, quality=8.0, error=3.0),
        _candidate(3, quality=7.0, error=2.0),
        _candidate(4, quality=6.0, error=1.0),
        _candidate(5, quality=5.0, error=0.0),
    ]
    shuffled = copy.deepcopy(rows)
    random.Random(123).shuffle(shuffled)

    left = select_source_blind_references(rows, policy=_policy())
    right = select_source_blind_references(shuffled, policy=_policy())

    assert left == right
    assert left.trace_payload() == right.trace_payload()


def test_multiple_sentences_have_independent_fronts_and_three_to_five_outputs() -> None:
    rows = [
        _candidate(sentence_offset + index, sentence_id=sentence_id)
        for sentence_offset, sentence_id in (
            (0, "R00-01"),
            (100, "R00-02"),
        )
        for index in range(1, 6)
    ]

    result = select_source_blind_references(
        rows,
        policy=_policy(selected_count=5),
    )

    assert [item.sentence_id for item in result.sentences] == [
        "R00-01",
        "R00-02",
    ]
    assert all(len(item.selected_candidate_ids) == 5 for item in result.sentences)


def test_replay_digest_is_deterministic_and_mismatch_fails_closed() -> None:
    rows = [
        _candidate(1, quality=9.0, error=4.0),
        _candidate(2, quality=8.0, error=3.0),
        _candidate(3, quality=7.0, error=2.0),
        _candidate(4, quality=6.0, error=1.0),
    ]
    first = select_source_blind_references(rows, policy=_policy(seed=42))
    replayed = replay_source_blind_selection(
        copy.deepcopy(list(reversed(rows))),
        policy=_policy(seed=42),
        expected_replay_sha256=first.replay_sha256,
    )

    assert replayed == first
    assert first.trace_sha256 == _sha_for_payload(first.trace_payload())
    with pytest.raises(R03SelectionError, match="replay digest mismatch"):
        replay_source_blind_selection(
            rows,
            policy=_policy(seed=43),
            expected_replay_sha256=first.replay_sha256,
        )


def _sha_for_payload(payload: object) -> str:
    import json

    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def test_normalization_and_all_policy_choices_are_digest_bound() -> None:
    rows = [
        _candidate(1, quality=9.0, error=4.0),
        _candidate(2, quality=8.0, error=3.0),
        _candidate(3, quality=7.0, error=2.0),
        _candidate(4, quality=6.0, error=1.0),
    ]
    identity = select_source_blind_references(rows, policy=_policy())
    minmax_policy = copy.deepcopy(_policy())
    minmax_policy = SelectorPolicy(
        objective_ids=minmax_policy.objective_ids,
        selected_count=minmax_policy.selected_count,
        candidate_order_policy=minmax_policy.candidate_order_policy,
        normalization=NormalizationPolicy(
            method="minmax",
            scope="per_sentence",
            clipping_bounds=(0.0, 1.0),
            constant_objective_policy="error",
        ),
        pareto=minmax_policy.pareto,
        diversity=minmax_policy.diversity,
    )
    minmax = select_source_blind_references(rows, policy=minmax_policy)

    assert identity.policy_sha256 != minmax.policy_sha256
    assert identity.replay_sha256 != minmax.replay_sha256
    assert identity.sentences[0].normalized_view_sha256 != (
        minmax.sentences[0].normalized_view_sha256
    )


def test_constant_axis_normalization_policy_is_enforced() -> None:
    rows = [_candidate(index, quality=1.0, error=float(index)) for index in range(1, 5)]
    base = _policy()
    strict_minmax = SelectorPolicy(
        objective_ids=base.objective_ids,
        selected_count=base.selected_count,
        candidate_order_policy=base.candidate_order_policy,
        normalization=NormalizationPolicy(
            method="minmax",
            scope="per_sentence",
            clipping_bounds=None,
            constant_objective_policy="error",
        ),
        pareto=base.pareto,
        diversity=base.diversity,
    )

    with pytest.raises(R03SelectionError, match="constant objective"):
        select_source_blind_references(rows, policy=strict_minmax)
