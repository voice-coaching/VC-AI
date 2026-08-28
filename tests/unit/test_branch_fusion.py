"""Fusion must let branches cover each other's failures, not average them away.

The paper's claim is specific: low alignment confidence should reduce the duration
and GOP branches, and a noisy recording should reduce the explicit F0 and energy
branches. These tests hold that behaviour, and hold the three ways a fused number
could lie — zero-filling a missing branch, degrading on an unmeasured condition,
and producing a score when nothing was left to weigh.
"""

from __future__ import annotations

import pytest

from voice_coach.pronunciation_feedback.branch_fusion import (
    DEGRADED_WEIGHT_FACTOR,
    BranchFusionError,
    BranchWeight,
    EvidenceConditions,
    FusedAspect,
    fuse_aspect,
    required_branches_missing,
    resolve_weights,
)
from voice_coach.pronunciation_feedback.domain import MetricState, S1Branch


def _branch(
    name: str,
    score: float | None = 0.8,
    *,
    state: MetricState = MetricState.OBSERVED,
    confidence: float | None = None,
) -> S1Branch:
    return S1Branch(
        name=name,
        score=score,
        confidence=confidence,
        state=state,
        backend_id=f"{name}-backend",
        revision="rev-1",
    )


def _weight_of(weights: tuple[BranchWeight, ...], name: str) -> BranchWeight:
    for item in weights:
        if item.branch == name:
            return item
    raise AssertionError(f"{name} not in weights")


# -- normalisation ------------------------------------------------------------


def test_weights_normalise_to_one_over_observed_branches() -> None:
    weights = resolve_weights(
        branches=[_branch("f0"), _branch("duration")],
        base_weights={"f0": 3.0, "duration": 1.0},
        conditions=EvidenceConditions(),
    )

    assert sum(item.effective_weight for item in weights) == pytest.approx(1.0)
    assert _weight_of(weights, "f0").effective_weight == pytest.approx(0.75)


def test_fused_score_is_the_weighted_mean() -> None:
    result = fuse_aspect(
        aspect="prosody",
        branches=[_branch("f0", 1.0), _branch("energy", 0.0)],
        base_weights={"f0": 3.0, "energy": 1.0},
    )

    assert result.state is MetricState.OBSERVED
    assert result.score == pytest.approx(0.75)
    assert result.weight_sum == pytest.approx(1.0)


# -- a missing branch is dropped, not zero-filled -----------------------------


def test_missing_branch_is_excluded_and_the_rest_renormalise() -> None:
    """Zero-filling would make a broken microphone look like bad pronunciation."""

    result = fuse_aspect(
        aspect="prosody",
        branches=[
            _branch("f0", 0.9),
            _branch("energy", None, state=MetricState.BACKEND_UNAVAILABLE),
        ],
    )

    assert result.score == pytest.approx(0.9)
    assert result.contributing_branches == ("f0",)
    excluded = _weight_of(result.weights, "energy")
    assert excluded.effective_weight == 0.0
    assert excluded.excluded_reason == "branch_state_backend_unavailable"


@pytest.mark.parametrize(
    "state",
    [
        MetricState.MISSING,
        MetricState.LOW_CONFIDENCE,
        MetricState.OUT_OF_DISTRIBUTION,
        MetricState.BACKEND_UNAVAILABLE,
    ],
)
def test_every_non_observed_state_excludes_the_branch(state: MetricState) -> None:
    result = fuse_aspect(
        aspect="prosody",
        branches=[_branch("f0", 0.5), _branch("energy", None, state=state)],
    )

    assert result.contributing_branches == ("f0",)


def test_all_branches_missing_yields_no_score() -> None:
    result = fuse_aspect(
        aspect="prosody",
        branches=[_branch("f0", None, state=MetricState.MISSING)],
    )

    assert result.state is MetricState.MISSING
    assert result.score is None
    assert result.contributing_branches == ()


def test_zero_base_weights_everywhere_yields_no_score() -> None:
    result = fuse_aspect(
        aspect="prosody",
        branches=[_branch("f0", 0.9)],
        base_weights={"f0": 0.0},
    )

    assert result.state is MetricState.MISSING
    assert result.score is None


# -- the declared degradation rules ------------------------------------------


def test_low_alignment_confidence_degrades_duration_and_gop() -> None:
    conditions = EvidenceConditions(alignment_confidence=0.2)

    weights = resolve_weights(
        branches=[_branch("duration"), _branch("gop_acoustic_posterior"), _branch("f0")],
        base_weights={},
        conditions=conditions,
    )

    for name in ("duration", "gop_acoustic_posterior"):
        assert "low_alignment_confidence" in _weight_of(weights, name).degraded_by
    assert _weight_of(weights, "f0").degraded_by == ()


def test_noise_degrades_explicit_f0_and_energy_but_not_gop() -> None:
    conditions = EvidenceConditions(noise_level=0.9)

    weights = resolve_weights(
        branches=[_branch("f0"), _branch("energy"), _branch("gop_acoustic_posterior")],
        base_weights={},
        conditions=conditions,
    )

    for name in ("f0", "energy"):
        assert "high_noise_level" in _weight_of(weights, name).degraded_by
    assert _weight_of(weights, "gop_acoustic_posterior").degraded_by == ()


def test_degradation_shifts_weight_toward_the_unaffected_branch() -> None:
    """This is the point of the design: one branch's failure mode is covered."""

    clean = resolve_weights(
        branches=[_branch("duration"), _branch("f0")],
        base_weights={},
        conditions=EvidenceConditions(),
    )
    weak_alignment = resolve_weights(
        branches=[_branch("duration"), _branch("f0")],
        base_weights={},
        conditions=EvidenceConditions(alignment_confidence=0.1),
    )

    assert _weight_of(clean, "f0").effective_weight == pytest.approx(0.5)
    assert _weight_of(weak_alignment, "f0").effective_weight > 0.5
    assert _weight_of(weak_alignment, "duration").effective_weight < 0.5


def test_degraded_branch_keeps_some_weight_rather_than_none() -> None:
    """Dropping it entirely would hand the decision to whoever stayed clean."""

    weights = resolve_weights(
        branches=[_branch("duration"), _branch("f0")],
        base_weights={},
        conditions=EvidenceConditions(alignment_confidence=0.1),
    )

    duration = _weight_of(weights, "duration")
    assert 0.0 < duration.effective_weight
    assert duration.effective_weight == pytest.approx(
        DEGRADED_WEIGHT_FACTOR / (1.0 + DEGRADED_WEIGHT_FACTOR)
    )


def test_both_conditions_compound_on_a_branch_sensitive_to_both() -> None:
    weights = resolve_weights(
        branches=[_branch("rhythm"), _branch("explicit")],
        base_weights={},
        conditions=EvidenceConditions(alignment_confidence=0.1, noise_level=0.9),
    )

    assert _weight_of(weights, "rhythm").degraded_by == ("high_noise_level",)


# -- an unmeasured condition degrades nothing --------------------------------


def test_unmeasured_alignment_confidence_degrades_nothing() -> None:
    """Penalising a branch because its check failed to run would be circular."""

    weights = resolve_weights(
        branches=[_branch("duration"), _branch("f0")],
        base_weights={},
        conditions=EvidenceConditions(alignment_confidence=None, noise_level=None),
    )

    assert all(item.degraded_by == () for item in weights)


def test_alignment_at_the_floor_is_not_weak() -> None:
    conditions = EvidenceConditions(alignment_confidence=0.5, alignment_confidence_floor=0.5)

    assert conditions.alignment_is_weak is False


def test_noise_at_the_ceiling_is_not_noisy() -> None:
    conditions = EvidenceConditions(noise_level=0.5, noise_level_ceiling=0.5)

    assert conditions.recording_is_noisy is False


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_out_of_range_conditions_fail_closed(value: float) -> None:
    with pytest.raises(BranchFusionError, match="within"):
        EvidenceConditions(alignment_confidence=value)
    with pytest.raises(BranchFusionError, match="within"):
        EvidenceConditions(noise_level=value)


# -- branch confidence scales its vote ---------------------------------------


def test_a_branch_that_says_it_is_unsure_carries_less_weight() -> None:
    weights = resolve_weights(
        branches=[_branch("f0", confidence=0.2), _branch("energy", confidence=1.0)],
        base_weights={},
        conditions=EvidenceConditions(),
    )

    assert _weight_of(weights, "f0").effective_weight < _weight_of(
        weights, "energy"
    ).effective_weight


def test_out_of_range_branch_confidence_fails_closed() -> None:
    with pytest.raises(BranchFusionError, match="confidence is out of range"):
        resolve_weights(
            branches=[_branch("f0", confidence=1.5)],
            base_weights={},
            conditions=EvidenceConditions(),
        )


# -- structural invariants ----------------------------------------------------


def test_duplicate_branch_name_fails_closed() -> None:
    with pytest.raises(BranchFusionError, match="duplicate branch name"):
        resolve_weights(
            branches=[_branch("f0"), _branch("f0")],
            base_weights={},
            conditions=EvidenceConditions(),
        )


def test_empty_branch_set_fails_closed() -> None:
    with pytest.raises(BranchFusionError, match="no branch to weigh"):
        resolve_weights(branches=[], base_weights={}, conditions=EvidenceConditions())


def test_negative_base_weight_fails_closed() -> None:
    with pytest.raises(BranchFusionError, match="negative"):
        resolve_weights(
            branches=[_branch("f0")],
            base_weights={"f0": -1.0},
            conditions=EvidenceConditions(),
        )


def test_an_excluded_weight_must_be_zero() -> None:
    with pytest.raises(BranchFusionError, match="must carry zero weight"):
        BranchWeight(
            branch="f0",
            base_weight=1.0,
            effective_weight=0.5,
            excluded_reason="branch_state_missing",
        )


def test_a_non_observed_aspect_must_not_carry_a_score() -> None:
    with pytest.raises(BranchFusionError, match="must not carry a score"):
        FusedAspect(aspect="prosody", score=0.5, state=MetricState.MISSING)


def test_an_observed_aspect_needs_a_contributor() -> None:
    with pytest.raises(BranchFusionError, match="needs a contributor"):
        FusedAspect(aspect="prosody", score=0.5, state=MetricState.OBSERVED)


# -- required branches are the gate's business, not fusion's ------------------


def test_required_branch_absence_is_reported_separately() -> None:
    """Fusion can score from survivors; the gate still needs to know one was absent."""

    branches = [
        _branch("explicit"),
        _branch("gop_acoustic_posterior", None, state=MetricState.BACKEND_UNAVAILABLE),
    ]

    missing = required_branches_missing(
        branches=branches,
        required=["explicit", "gop_acoustic_posterior", "s105_selected_hierarchy"],
    )

    assert missing == ("gop_acoustic_posterior", "s105_selected_hierarchy")
    # Fusion still yields a number from what survived — the gate decides what to do.
    assert fuse_aspect(aspect="segmental", branches=branches).state is (
        MetricState.OBSERVED
    )


def test_all_required_branches_present_reports_nothing_missing() -> None:
    assert (
        required_branches_missing(
            branches=[_branch("explicit"), _branch("gop_acoustic_posterior")],
            required=["explicit", "gop_acoustic_posterior"],
        )
        == ()
    )
