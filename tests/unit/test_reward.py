"""The generator's reward must be scalar, independent, and refused when hacked.

Three failures this file exists to prevent: a reward that backpropagates into the
generator while being documented as reward-guided; a reward model that is really
one of S1's own branches, so the loop congratulates itself; and a reward that
climbs while an axis it cannot see falls.
"""

from __future__ import annotations

import pytest

from voice_coach.pronunciation_feedback.domain import IndependenceReceipt, MetricState
from voice_coach.pronunciation_feedback.reward import (
    REWARD_AXES,
    RewardComponent,
    RewardError,
    RewardGuidedPolicyContract,
    RewardSignal,
    UnavailableRewardModel,
    assert_reward_independence,
    combine_reward,
    detect_reward_hacking,
)

S1_BACKENDS = ("wavlm-large-s104-adapter", "b07-gop", "explicit")


def _component(
    name: str,
    value: float | None = 0.8,
    *,
    state: MetricState = MetricState.OBSERVED,
    weight: float = 1.0,
) -> RewardComponent:
    return RewardComponent(name=name, value=value, state=state, weight=weight)


def _receipt(independent: bool = True) -> IndependenceReceipt:
    return IndependenceReceipt(
        s1_backend_ids=S1_BACKENDS,
        s2_backend_id="hubert-large-auditor",
        independent=independent,
        reason="distinct backend and alignment path",
    )


def _signal(**overrides: object) -> RewardSignal:
    options: dict[str, object] = {
        "backend_id": "hubert-large-auditor",
        "revision": "rev-1",
        "scalar_reward": 0.8,
        "state": MetricState.OBSERVED,
        "signal_kind": "reward_guided_scalar",
        "components": (_component("content_fidelity"),),
        "independence": _receipt(),
        "reward_hacking_flags": (),
    }
    options.update(overrides)
    return RewardSignal(**options)  # type: ignore[arg-type]


# -- component and signal invariants ------------------------------------------


def test_unknown_axis_is_rejected() -> None:
    with pytest.raises(RewardError, match="unknown reward axis"):
        RewardComponent(
            name="charisma", value=0.9, state=MetricState.OBSERVED, weight=1.0
        )


def test_observed_component_needs_a_value_in_range() -> None:
    with pytest.raises(RewardError, match="carries no value"):
        _component("naturalness", None)
    with pytest.raises(RewardError, match="within"):
        _component("naturalness", 1.5)


def test_unobserved_component_must_not_carry_a_value() -> None:
    with pytest.raises(RewardError, match="not observed but carries a value"):
        _component("naturalness", 0.5, state=MetricState.MISSING)


def test_unobserved_signal_must_not_carry_a_reward() -> None:
    with pytest.raises(RewardError, match="must not carry a value"):
        _signal(state=MetricState.BACKEND_UNAVAILABLE, scalar_reward=0.5)


def test_signal_must_name_its_backend_and_revision() -> None:
    with pytest.raises(RewardError, match="backend and revision"):
        _signal(revision="")


# -- combining axes -----------------------------------------------------------


def test_weighted_mean_over_observed_axes() -> None:
    components = [
        _component("content_fidelity", 1.0, weight=3.0),
        _component("naturalness", 0.5, weight=1.0),
    ]

    value, state = combine_reward(components)

    assert state is MetricState.OBSERVED
    assert value == pytest.approx((1.0 * 3 + 0.5) / 4)


def test_missing_axis_is_dropped_not_scored_zero() -> None:
    """A missing naturalness score must not read as maximally unnatural."""

    with_missing = [
        _component("content_fidelity", 0.8, weight=1.0),
        _component("naturalness", None, state=MetricState.MISSING, weight=1.0),
    ]

    value, state = combine_reward(with_missing)

    assert state is MetricState.OBSERVED
    assert value == pytest.approx(0.8)


def test_all_axes_missing_yields_no_reward() -> None:
    value, state = combine_reward(
        [_component("naturalness", None, state=MetricState.MISSING)]
    )

    assert value is None
    assert state is MetricState.MISSING


def test_empty_or_zero_weight_component_set_yields_no_reward() -> None:
    assert combine_reward([]) == (None, MetricState.MISSING)
    assert combine_reward([_component("naturalness", 0.9, weight=0.0)]) == (
        None,
        MetricState.MISSING,
    )


def test_duplicate_axis_fails_closed() -> None:
    with pytest.raises(RewardError, match="duplicate reward axis"):
        combine_reward([_component("naturalness"), _component("naturalness")])


def test_content_fidelity_is_the_first_declared_axis() -> None:
    """P8: a candidate that says the wrong words cannot be rescued by sounding nice."""

    assert REWARD_AXES[0] == "content_fidelity"


# -- independence -------------------------------------------------------------


def test_distinct_backend_and_alignment_are_independent() -> None:
    receipt = assert_reward_independence(
        reward_backend_id="hubert-large-auditor",
        s1_backend_ids=S1_BACKENDS,
        s1_alignment_path="mfa_ctc_segmentation",
        reward_alignment_path="alignment_free_dtw",
    )

    assert receipt.independent is True


def test_reward_backend_that_is_an_s1_branch_is_not_independent() -> None:
    receipt = assert_reward_independence(
        reward_backend_id="b07-gop",
        s1_backend_ids=S1_BACKENDS,
        s1_alignment_path="mfa_ctc_segmentation",
        reward_alignment_path="alignment_free_dtw",
    )

    assert receipt.independent is False
    assert "also an S1 branch" in receipt.reason


def test_shared_alignment_path_defeats_independence() -> None:
    """Different weights that consume the same forced alignment are not independent."""

    receipt = assert_reward_independence(
        reward_backend_id="whisper-large-v3",
        s1_backend_ids=S1_BACKENDS,
        s1_alignment_path="mfa_ctc_segmentation",
        reward_alignment_path="mfa_ctc_segmentation",
    )

    assert receipt.independent is False
    assert "alignment path" in receipt.reason


def test_unnamed_reward_backend_fails_closed() -> None:
    with pytest.raises(RewardError, match="must be named"):
        assert_reward_independence(
            reward_backend_id="",
            s1_backend_ids=S1_BACKENDS,
            s1_alignment_path="a",
            reward_alignment_path="b",
        )


# -- reward hacking -----------------------------------------------------------


def test_high_reward_with_wrong_content_is_flagged() -> None:
    flags = detect_reward_hacking(
        reward=0.95, content_fidelity=0.4, artifact_free_score=0.9
    )

    assert "high_reward_with_content_mismatch" in flags


def test_high_reward_with_audible_artifact_is_flagged() -> None:
    flags = detect_reward_hacking(
        reward=0.95, content_fidelity=0.95, artifact_free_score=0.2
    )

    assert "high_reward_with_audible_artifact" in flags


def test_reward_up_while_an_unseen_axis_falls_is_flagged() -> None:
    flags = detect_reward_hacking(
        reward=0.6,
        content_fidelity=0.9,
        artifact_free_score=0.9,
        reward_delta=0.05,
        independent_axis_delta=-0.03,
    )

    assert flags == ("reward_up_while_independent_axis_down",)


def test_healthy_reward_raises_no_flag() -> None:
    assert (
        detect_reward_hacking(
            reward=0.9,
            content_fidelity=0.95,
            artifact_free_score=0.9,
            reward_delta=0.04,
            independent_axis_delta=0.01,
        )
        == ()
    )


def test_absent_reward_raises_no_flag() -> None:
    assert detect_reward_hacking(
        reward=None, content_fidelity=0.1, artifact_free_score=0.1
    ) == ()


# -- policy usability ---------------------------------------------------------


def test_a_flagged_reward_is_not_used_rather_than_discounted() -> None:
    flagged = _signal(reward_hacking_flags=("high_reward_with_content_mismatch",))

    assert flagged.is_usable_for_policy_update is False


def test_a_non_independent_reward_is_not_usable() -> None:
    assert _signal(independence=_receipt(independent=False)).is_usable_for_policy_update is False
    assert _signal(independence=None).is_usable_for_policy_update is False


def test_an_unobserved_reward_is_not_usable() -> None:
    unobserved = _signal(state=MetricState.MISSING, scalar_reward=None)

    assert unobserved.is_usable_for_policy_update is False


def test_a_clean_independent_reward_is_usable() -> None:
    assert _signal().is_usable_for_policy_update is True


# -- P3: differentiability is declared, not inferred --------------------------


def test_scalar_reward_may_not_declare_a_gradient_path() -> None:
    with pytest.raises(RewardError, match="must not provide a gradient"):
        RewardGuidedPolicyContract(
            signal_kind="reward_guided_scalar",
            gradient_from_reward_allowed=True,
            policy_update_rule="grpo",
        )


def test_adversarial_training_must_declare_its_gradient_path() -> None:
    with pytest.raises(RewardError, match="requires a gradient path"):
        RewardGuidedPolicyContract(
            signal_kind="adversarial_gradient",
            gradient_from_reward_allowed=False,
            policy_update_rule="minmax",
        )


def test_the_reward_hacking_audit_cannot_be_waived() -> None:
    with pytest.raises(RewardError, match="cannot be waived"):
        RewardGuidedPolicyContract(
            signal_kind="reward_guided_scalar",
            gradient_from_reward_allowed=False,
            policy_update_rule="grpo",
            reward_hacking_audit_required=False,
        )


def test_both_declared_regimes_are_constructible() -> None:
    reward_guided = RewardGuidedPolicyContract(
        signal_kind="reward_guided_scalar",
        gradient_from_reward_allowed=False,
        policy_update_rule="grpo_or_rejection_sampling",
    )
    adversarial = RewardGuidedPolicyContract(
        signal_kind="adversarial_gradient",
        gradient_from_reward_allowed=True,
        policy_update_rule="offline_discriminator_minmax",
    )

    assert reward_guided.policy_update_rule.startswith("grpo")
    assert adversarial.gradient_from_reward_allowed is True


def test_unavailable_reward_model_declares_its_absence() -> None:
    signal = UnavailableRewardModel().reward_for()

    assert signal.state is MetricState.BACKEND_UNAVAILABLE
    assert signal.scalar_reward is None
    assert signal.is_usable_for_policy_update is False
