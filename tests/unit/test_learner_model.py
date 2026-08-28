"""Trainability must be measured or declared unknown — never a silent constant.

The priority formula multiplies four factors. If the fourth arrives as a constant
the ranking is really three-factor while looking like the paper's. These tests fix
that the estimate is derived from the learner's own reduction curve, that it says
so when it cannot be, and that learner history cannot retain audio-derived data or
be rewritten after the fact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_coach.pronunciation_feedback.domain import MetricState
from voice_coach.pronunciation_feedback.learner_model import (
    MINIMUM_OBSERVATIONS_FOR_TRAINABILITY,
    AttemptRecord,
    JsonlLearnerStore,
    LearnerModel,
    LearnerModelError,
    LearnerState,
    TrainabilityEstimate,
    build_learner_state,
    estimate_trainability,
    priority_with_trainability,
    select_next_targets,
)

LEARNER = "a" * 64
OTHER_LEARNER = "b" * 64


def _record(
    *,
    attempt_index: int = 1,
    issue_type: str | None = "final_consonant_deletion",
    severity: float | None = 0.8,
    prompt_id: str = "R00-01",
    learner: str = LEARNER,
    status: str = "coaching_ready",
) -> AttemptRecord:
    return AttemptRecord(
        learner_id_sha256=learner,
        prompt_id=prompt_id,
        attempt_index=attempt_index,
        recorded_at_utc=f"2026-08-08T0{attempt_index}:00:00+00:00",
        status=status,
        issue_type=issue_type,
        severity=severity,
        confidence=0.9,
    )


# -- identifier and record hygiene -------------------------------------------


@pytest.mark.parametrize("value", ["", "u_9f31c2", "A" * 64, "a" * 63, "z" * 64])
def test_only_a_hashed_identifier_is_accepted(value: str) -> None:
    with pytest.raises(LearnerModelError, match="sha256"):
        _record(learner=value)


def test_attempt_index_starts_at_one() -> None:
    with pytest.raises(LearnerModelError, match="starts at 1"):
        _record(attempt_index=0)


def test_severity_without_an_issue_type_is_rejected() -> None:
    with pytest.raises(LearnerModelError, match="meaningless"):
        _record(issue_type=None, severity=0.5)


def test_no_issue_on_an_attempt_is_recordable_as_evidence_of_improvement() -> None:
    record = _record(issue_type=None, severity=None, status="completed_no_issue")

    assert record.issue_type is None


@pytest.mark.parametrize(
    "field", ["audio_path", "path", "pcm_sha256", "learner_id", "speaker_id"]
)
def test_reading_a_record_with_an_audio_derived_field_fails_closed(field: str) -> None:
    document = _record().to_json_object()
    document[field] = "anything"

    with pytest.raises(LearnerModelError, match="forbidden field"):
        AttemptRecord.from_json_object(document)


def test_record_round_trips_through_json() -> None:
    original = _record()

    restored = AttemptRecord.from_json_object(original.to_json_object())

    assert restored == original


def test_schema_version_drift_fails_closed() -> None:
    document = _record().to_json_object()
    document["schema_version"] = "voice_coach.learner_attempt.v0"

    with pytest.raises(LearnerModelError, match="schema version differs"):
        AttemptRecord.from_json_object(document)


# -- trainability estimation --------------------------------------------------


def test_too_few_observations_yields_missing_not_a_guess() -> None:
    estimate = estimate_trainability(issue_type="vowel_quality", severities=[0.9, 0.4])

    assert estimate.state is MetricState.MISSING
    assert estimate.value is None
    assert estimate.basis == "insufficient_observations"
    assert estimate.observation_count == 2


def test_falling_severity_reads_as_trainable() -> None:
    estimate = estimate_trainability(
        issue_type="vowel_quality", severities=[0.9, 0.8, 0.3, 0.2]
    )

    assert estimate.state is MetricState.OBSERVED
    assert estimate.value is not None
    assert estimate.value > 0.5


def test_flat_severity_reads_as_neutral() -> None:
    estimate = estimate_trainability(
        issue_type="vowel_quality", severities=[0.6, 0.6, 0.6, 0.6]
    )

    assert estimate.value == pytest.approx(0.5)


def test_worsening_severity_reads_as_low_trainability() -> None:
    estimate = estimate_trainability(
        issue_type="vowel_quality", severities=[0.2, 0.3, 0.8, 0.9]
    )

    assert estimate.value is not None
    assert estimate.value < 0.5


def test_a_resolved_issue_counts_as_corrected() -> None:
    """``None`` means the issue did not recur, which is improvement."""

    estimate = estimate_trainability(
        issue_type="vowel_quality", severities=[0.9, 0.8, None, None]
    )

    assert estimate.value is not None
    assert estimate.value > 0.5


def test_estimate_is_clamped_to_the_unit_interval() -> None:
    estimate = estimate_trainability(
        issue_type="vowel_quality", severities=[1.0, 1.0, 0.0, 0.0]
    )

    assert estimate.value == pytest.approx(1.0)


def test_out_of_range_severity_fails_closed() -> None:
    with pytest.raises(LearnerModelError, match="within"):
        estimate_trainability(issue_type="x", severities=[0.5, 1.5, 0.5])


def test_minimum_observation_threshold_is_respected_exactly() -> None:
    at_threshold = estimate_trainability(
        issue_type="x", severities=[0.5] * MINIMUM_OBSERVATIONS_FOR_TRAINABILITY
    )

    assert at_threshold.state is MetricState.OBSERVED


# -- state assembly -----------------------------------------------------------


def test_state_counts_attempts_and_finds_recurring_types() -> None:
    records = [
        _record(attempt_index=1, severity=0.9),
        _record(attempt_index=2, severity=0.6),
        _record(attempt_index=3, issue_type="vowel_quality", severity=0.4),
    ]

    state = build_learner_state(learner_id_sha256=LEARNER, records=records)

    assert state.attempt_count == 3
    assert state.recurring_issue_types == ("final_consonant_deletion",)
    assert state.trainability_for("vowel_quality") is not None


def test_mixed_learner_history_fails_closed() -> None:
    records = [_record(), _record(attempt_index=2, learner=OTHER_LEARNER)]

    with pytest.raises(LearnerModelError, match="mixes learners"):
        build_learner_state(learner_id_sha256=LEARNER, records=records)


def test_unknown_level_is_a_valid_declared_state() -> None:
    state = build_learner_state(learner_id_sha256=LEARNER, records=[])

    assert state.level == "unknown"
    assert state.attempt_count == 0


def test_invalid_level_is_rejected() -> None:
    with pytest.raises(LearnerModelError, match="level must be one of"):
        LearnerState(
            learner_id_sha256=LEARNER,
            level="expert",
            attempt_count=0,
            recurring_issue_types=(),
        )


# -- next-target selection ----------------------------------------------------


def test_known_trainable_targets_rank_before_unknown_ones() -> None:
    state = LearnerState(
        learner_id_sha256=LEARNER,
        level="unknown",
        attempt_count=6,
        recurring_issue_types=("unknown_type", "trainable_type"),
        trainability=(
            TrainabilityEstimate(
                issue_type="trainable_type",
                value=0.9,
                state=MetricState.OBSERVED,
                observation_count=4,
                basis="mean_severity_reduction_across_attempts",
            ),
            TrainabilityEstimate(
                issue_type="unknown_type",
                value=None,
                state=MetricState.MISSING,
                observation_count=1,
                basis="insufficient_observations",
            ),
        ),
    )

    assert select_next_targets(state) == ("trainable_type", "unknown_type")


def test_unknown_trainability_neither_promotes_nor_excludes() -> None:
    state = LearnerState(
        learner_id_sha256=LEARNER,
        level="unknown",
        attempt_count=2,
        recurring_issue_types=("only_type",),
    )

    assert select_next_targets(state) == ("only_type",)


def test_target_count_is_capped() -> None:
    state = LearnerState(
        learner_id_sha256=LEARNER,
        level="unknown",
        attempt_count=9,
        recurring_issue_types=tuple(f"type_{index}" for index in range(7)),
    )

    assert len(select_next_targets(state)) == 3
    assert len(select_next_targets(state, maximum=1)) == 1


def test_non_positive_maximum_is_rejected() -> None:
    state = build_learner_state(learner_id_sha256=LEARNER, records=[])

    with pytest.raises(LearnerModelError, match="at least 1"):
        select_next_targets(state, maximum=0)


# -- priority with an explicit unknown case -----------------------------------


def test_measured_trainability_is_used_and_its_basis_reported() -> None:
    estimate = TrainabilityEstimate(
        issue_type="x",
        value=0.5,
        state=MetricState.OBSERVED,
        observation_count=4,
        basis="mean_severity_reduction_across_attempts",
    )

    score, basis = priority_with_trainability(
        severity=0.8,
        confidence=0.5,
        comprehensibility_impact=0.5,
        trainability=estimate,
        declared_prior=1.0,
    )

    assert score == pytest.approx(0.8 * 0.5 * 0.5 * 0.5)
    assert basis == "mean_severity_reduction_across_attempts"


def test_unknown_trainability_uses_the_declared_prior_and_says_so() -> None:
    score, basis = priority_with_trainability(
        severity=0.8,
        confidence=0.5,
        comprehensibility_impact=0.5,
        trainability=None,
        declared_prior=0.5,
    )

    assert score == pytest.approx(0.8 * 0.5 * 0.5 * 0.5)
    assert basis == "declared_prior_no_learner_history"


def test_out_of_range_factor_fails_closed() -> None:
    with pytest.raises(LearnerModelError, match="within"):
        priority_with_trainability(
            severity=1.2,
            confidence=0.5,
            comprehensibility_impact=0.5,
            trainability=None,
            declared_prior=0.5,
        )


# -- append-only store and retry bound ---------------------------------------


def test_store_appends_and_reads_back_only_that_learner(tmp_path: Path) -> None:
    store = JsonlLearnerStore(tmp_path / "history.jsonl")
    store.append(_record(attempt_index=1))
    store.append(_record(attempt_index=2, learner=OTHER_LEARNER))

    mine = store.read(LEARNER)

    assert [item.learner_id_sha256 for item in mine] == [LEARNER]


def test_store_never_rewrites_an_earlier_line(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    store = JsonlLearnerStore(path)
    store.append(_record(attempt_index=1, severity=0.9))
    first_line = path.read_text(encoding="utf-8").splitlines()[0]

    store.append(_record(attempt_index=2, severity=0.2))

    assert path.read_text(encoding="utf-8").splitlines()[0] == first_line
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_hand_edited_history_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")

    with pytest.raises(LearnerModelError, match="invalid JSON"):
        JsonlLearnerStore(path).read(LEARNER)


def test_absent_history_reads_as_empty(tmp_path: Path) -> None:
    assert JsonlLearnerStore(tmp_path / "absent.jsonl").read(LEARNER) == ()


def test_attempt_index_advances_per_prompt(tmp_path: Path) -> None:
    store = JsonlLearnerStore(tmp_path / "history.jsonl")
    model = LearnerModel(store, maximum_attempts_per_prompt=3)

    first = model.record_attempt(
        learner_id_sha256=LEARNER, prompt_id="R00-01", status="rerecord_required"
    )
    second = model.record_attempt(
        learner_id_sha256=LEARNER, prompt_id="R00-01", status="rerecord_required"
    )
    other_prompt = model.record_attempt(
        learner_id_sha256=LEARNER, prompt_id="R00-02", status="coaching_ready"
    )

    assert (first.attempt_index, second.attempt_index) == (1, 2)
    assert other_prompt.attempt_index == 1


def test_retries_are_bounded(tmp_path: Path) -> None:
    """Without a bound, "record again" can loop forever."""

    store = JsonlLearnerStore(tmp_path / "history.jsonl")
    model = LearnerModel(store, maximum_attempts_per_prompt=3)

    assert model.retries_remaining(LEARNER, "R00-01") == 3
    for _ in range(3):
        model.record_attempt(
            learner_id_sha256=LEARNER, prompt_id="R00-01", status="rerecord_required"
        )

    assert model.retries_remaining(LEARNER, "R00-01") == 0


def test_retry_bound_must_be_declared_positive(tmp_path: Path) -> None:
    store = JsonlLearnerStore(tmp_path / "history.jsonl")

    with pytest.raises(LearnerModelError, match="at least 1"):
        LearnerModel(store, maximum_attempts_per_prompt=0)


def test_model_builds_state_from_its_own_store(tmp_path: Path) -> None:
    store = JsonlLearnerStore(tmp_path / "history.jsonl")
    model = LearnerModel(store, maximum_attempts_per_prompt=5)
    for index, severity in enumerate([0.9, 0.7, 0.3, 0.2], start=1):
        model.record_attempt(
            learner_id_sha256=LEARNER,
            prompt_id="R00-01",
            status="coaching_ready",
            issue_type="final_consonant_deletion",
            severity=severity,
            confidence=0.9,
        )

    state = model.state_for(LEARNER)
    estimate = state.trainability_for("final_consonant_deletion")

    assert state.attempt_count == 4
    assert estimate is not None
    assert estimate.state is MetricState.OBSERVED
    assert estimate.value is not None and estimate.value > 0.5


def test_stored_line_carries_no_audio_derived_field(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    store = JsonlLearnerStore(path)
    store.append(_record())

    document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    for forbidden in ("audio", "path", "pcm", "waveform"):
        assert not any(forbidden in key for key in document)
