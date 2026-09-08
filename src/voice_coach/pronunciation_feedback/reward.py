"""Loop A reward path: S2 as a generator reward model, kept apart from S2 as auditor.

The paper gives S2 two jobs — auditing S1's verdict on learner speech (§4.4) and
rewarding the generator (§4.1, R&D-2/GRPO). Running both from one model is what
creates the failure the paper names in §1(c): the generator drifts toward the
reward model's blind spots, and the same model's audit of S1 stops being
independent of the references the learner heard.

This module keeps three things explicit that a scalar reward would otherwise hide:

* **P3, differentiability.** A reward is a scalar with no gradient path. A signal
  that backpropagates into the generator is adversarial training, a different
  regime with different guarantees, and it is labelled as such.
* **P2, independence.** A reward backend that shares an identity with an S1 branch
  fails closed rather than quietly co-adapting.
* **P4, Goodhart.** Reward going up while an independent axis goes down is the
  signature of reward hacking, so it is detected here rather than discovered in a
  human evaluation months later.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .domain import IndependenceReceipt, MetricState

#: A scalar reward carries no gradient; adversarial training does. They are
#: different regimes (paper §9.1 Table 8) and must be named, not inferred.
SignalKind = Literal["reward_guided_scalar", "adversarial_gradient"]

#: Axes the generator is rewarded on. Content fidelity is first on purpose: a
#: candidate that says the wrong words cannot be rescued by sounding pleasant
#: (P8 lexicographic order).
REWARD_AXES: tuple[str, ...] = (
    "content_fidelity",
    "pronunciation_accuracy",
    "prosody_fit",
    "naturalness",
    "speaker_consistency",
)


class RewardError(ValueError):
    """Raised when a reward signal or its provenance breaks the contract."""


@dataclass(frozen=True, slots=True)
class RewardComponent:
    """One rewarded axis with its own state and weight."""

    name: str
    value: float | None
    state: MetricState
    weight: float

    def __post_init__(self) -> None:
        if self.name not in REWARD_AXES:
            raise RewardError(f"unknown reward axis: {self.name}")
        if self.weight < 0:
            raise RewardError("weight must not be negative")
        if self.state is MetricState.OBSERVED:
            if self.value is None:
                raise RewardError(f"{self.name} is observed but carries no value")
            if not 0.0 <= float(self.value) <= 1.0:
                raise RewardError(f"{self.name} must be within [0, 1]")
        elif self.value is not None:
            raise RewardError(f"{self.name} is not observed but carries a value")


@dataclass(frozen=True, slots=True)
class RewardSignal:
    """What the generator is allowed to optimise against."""

    backend_id: str
    revision: str
    scalar_reward: float | None
    state: MetricState
    signal_kind: SignalKind
    components: tuple[RewardComponent, ...]
    independence: IndependenceReceipt | None
    reward_hacking_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.backend_id or not self.revision:
            raise RewardError("a reward signal must name its backend and revision")
        if self.state is MetricState.OBSERVED:
            if self.scalar_reward is None:
                raise RewardError("an observed reward needs a value")
            if not 0.0 <= float(self.scalar_reward) <= 1.0:
                raise RewardError("scalar_reward must be within [0, 1]")
        elif self.scalar_reward is not None:
            raise RewardError("a non-observed reward must not carry a value")

    @property
    def is_usable_for_policy_update(self) -> bool:
        """Whether a policy step may consume this signal.

        Three conditions, all of them refusals rather than penalties: the reward
        must be observed, independence must be established, and no reward-hacking
        flag may be raised. A flagged reward is not scaled down — it is not used.
        """

        return (
            self.state is MetricState.OBSERVED
            and self.independence is not None
            and self.independence.independent
            and not self.reward_hacking_flags
        )


def combine_reward(components: Sequence[RewardComponent]) -> tuple[float | None, MetricState]:
    """Weighted mean over the axes that were actually measured.

    Unmeasured axes are dropped and the remaining weights renormalised, never
    zero-filled: a missing naturalness score must not read as maximally
    unnatural, which would make the generator chase an artefact of the gap.
    """

    if not components:
        return None, MetricState.MISSING
    names = [item.name for item in components]
    if len(set(names)) != len(names):
        raise RewardError("duplicate reward axis")
    observed = [
        item
        for item in components
        if item.state is MetricState.OBSERVED and item.weight > 0
    ]
    total_weight = sum(item.weight for item in observed)
    if not observed or total_weight <= 0:
        return None, MetricState.MISSING
    value = sum(
        float(item.value) * item.weight for item in observed  # type: ignore[arg-type]
    ) / total_weight
    return max(0.0, min(1.0, value)), MetricState.OBSERVED


def assert_reward_independence(
    *,
    reward_backend_id: str,
    s1_backend_ids: Sequence[str],
    s1_alignment_path: str,
    reward_alignment_path: str,
) -> IndependenceReceipt:
    """Prove the reward model is not one of S1's own branches.

    Checked on the alignment path too: two models with different weights that
    agree because they consume the same forced alignment are not independent in
    the way the design needs.
    """

    if not reward_backend_id:
        raise RewardError("reward backend id must be named")
    shared = [item for item in s1_backend_ids if item == reward_backend_id]
    if shared:
        return IndependenceReceipt(
            s1_backend_ids=tuple(s1_backend_ids),
            s2_backend_id=reward_backend_id,
            independent=False,
            reason=f"reward backend is also an S1 branch: {reward_backend_id}",
        )
    if s1_alignment_path and s1_alignment_path == reward_alignment_path:
        return IndependenceReceipt(
            s1_backend_ids=tuple(s1_backend_ids),
            s2_backend_id=reward_backend_id,
            independent=False,
            reason=f"reward shares S1's alignment path: {reward_alignment_path}",
        )
    return IndependenceReceipt(
        s1_backend_ids=tuple(s1_backend_ids),
        s2_backend_id=reward_backend_id,
        independent=True,
        reason="distinct backend and alignment path",
    )


def detect_reward_hacking(
    *,
    reward: float | None,
    content_fidelity: float | None,
    artifact_free_score: float | None,
    independent_axis_delta: float | None = None,
    reward_delta: float | None = None,
    reward_ceiling: float = 0.8,
    content_floor: float = 0.7,
    artifact_floor: float = 0.5,
) -> tuple[str, ...]:
    """Flag the shapes a hacked reward takes.

    Each flag is a *pattern*, not a threshold on quality: a high reward paired
    with wrong content, or with audible artefacts, or moving opposite to an axis
    the reward model does not see. The thresholds only decide when the pattern is
    worth naming.
    """

    flags: list[str] = []
    if reward is None:
        return ()
    if reward >= reward_ceiling:
        if content_fidelity is not None and content_fidelity < content_floor:
            flags.append("high_reward_with_content_mismatch")
        if artifact_free_score is not None and artifact_free_score < artifact_floor:
            flags.append("high_reward_with_audible_artifact")
    if (
        reward_delta is not None
        and independent_axis_delta is not None
        and reward_delta > 0
        and independent_axis_delta < 0
    ):
        flags.append("reward_up_while_independent_axis_down")
    return tuple(flags)


@dataclass(frozen=True, slots=True)
class RewardGuidedPolicyContract:
    """Declares how the generator may consume the reward (P3).

    ``gradient_from_reward_allowed`` stays false for the reward-guided regime: the
    generator sees a scalar and updates by policy gradient or rejection sampling,
    not by differentiating through the reward model.
    """

    signal_kind: SignalKind
    gradient_from_reward_allowed: bool
    policy_update_rule: str
    reward_hacking_audit_required: bool = True

    def __post_init__(self) -> None:
        if self.signal_kind == "reward_guided_scalar" and (
            self.gradient_from_reward_allowed
        ):
            raise RewardError(
                "a scalar reward must not provide a gradient; "
                "declare adversarial_gradient instead"
            )
        if self.signal_kind == "adversarial_gradient" and (
            not self.gradient_from_reward_allowed
        ):
            raise RewardError(
                "adversarial training requires a gradient path; "
                "declare reward_guided_scalar instead"
            )
        if not self.policy_update_rule:
            raise RewardError("policy_update_rule must be named")
        if not self.reward_hacking_audit_required:
            raise RewardError("the reward-hacking audit cannot be waived")


class UnavailableRewardModel:
    """Explicit stand-in until an independent reward backend exists.

    Loop A's generator stays at the MVP stage without it: candidate selection can
    still run on hard constraints and the Pareto frontier, which need no reward.
    """

    def __init__(self, detail: str | None = None) -> None:
        self._detail = detail or "no independent reward backend is available"

    @property
    def backend_id(self) -> str:
        return "unavailable-reward-model"

    @property
    def revision(self) -> str:
        return "n/a"

    def reward_for(self, **_kwargs: object) -> RewardSignal:
        return RewardSignal(
            backend_id=self.backend_id,
            revision=self.revision,
            scalar_reward=None,
            state=MetricState.BACKEND_UNAVAILABLE,
            signal_kind="reward_guided_scalar",
            components=(),
            independence=None,
            reward_hacking_flags=(),
        )


__all__ = [
    "REWARD_AXES",
    "RewardComponent",
    "RewardError",
    "RewardGuidedPolicyContract",
    "RewardSignal",
    "SignalKind",
    "UnavailableRewardModel",
    "assert_reward_independence",
    "combine_reward",
    "detect_reward_hacking",
]
