"""Evidence-aware confidence weighting across S1 branches (paper §4.3).

The fusion rule is ``score_a(x) = Σ_m π_{a,m}(x) · score_{a,m}(x)`` with
``Σ_m π_{a,m} = 1``. What makes it evidence-*aware* is that the weights are not
fixed per branch: the paper states that when alignment confidence is low the
duration and GOP branches lose weight, and when the recording is noisy the explicit
F0 and energy branches lose confidence. Each branch's failure mode is therefore
covered by the others rather than averaged into the result.

Three properties this module holds onto, because each of them is a way the fused
number could lie:

* **A missing branch is dropped, not zero-filled.** Weights renormalise over the
  branches that actually reported. Scoring an unmeasured axis as 0 would make a
  broken microphone look like bad pronunciation.
* **Degradation is declared, not discovered.** The conditions that reduce a
  branch's weight are named in :class:`EvidenceConditions` and applied by a
  table, so a run receipt can state why a branch counted for less.
* **Nothing left to weigh means no score.** If every branch is missing, or the
  surviving weights sum to zero, the result is ``MISSING`` with no value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .domain import MetricState, S1Branch

#: Branches whose weight depends on alignment quality: both read from time spans
#: the aligner produced, so a bad alignment corrupts them together.
ALIGNMENT_DEPENDENT_BRANCHES: frozenset[str] = frozenset(
    {"duration", "pause", "gop_acoustic_posterior", "gop", "m3_duration", "m2_gop"}
)
#: Branches measured directly off the waveform, which background noise degrades.
NOISE_SENSITIVE_BRANCHES: frozenset[str] = frozenset(
    {"f0", "energy", "loudness", "m4_f0", "m5_energy", "rhythm", "m6_rhythm"}
)
#: How much weight a branch keeps when its evidence condition is unmet. Not zero:
#: a degraded branch still carries some information, and dropping it entirely
#: would hand the decision to whichever branch happened to stay clean.
DEGRADED_WEIGHT_FACTOR = 0.25


class BranchFusionError(ValueError):
    """Raised when a branch set or weighting breaks the contract."""


@dataclass(frozen=True, slots=True)
class EvidenceConditions:
    """Observed conditions that modulate branch weights.

    ``None`` means the condition itself was not measured. An unmeasured condition
    does not degrade anything — it would be circular to penalise a branch because
    the check on it failed to run.
    """

    alignment_confidence: float | None = None
    noise_level: float | None = None
    alignment_confidence_floor: float = 0.5
    noise_level_ceiling: float = 0.5

    def __post_init__(self) -> None:
        for name in ("alignment_confidence", "noise_level"):
            value = getattr(self, name)
            if value is None:
                continue
            if not 0.0 <= float(value) <= 1.0:
                raise BranchFusionError(f"{name} must be within [0, 1]")
        for name in ("alignment_confidence_floor", "noise_level_ceiling"):
            value = getattr(self, name)
            if not 0.0 <= float(value) <= 1.0:
                raise BranchFusionError(f"{name} must be within [0, 1]")

    @property
    def alignment_is_weak(self) -> bool:
        return (
            self.alignment_confidence is not None
            and self.alignment_confidence < self.alignment_confidence_floor
        )

    @property
    def recording_is_noisy(self) -> bool:
        return (
            self.noise_level is not None
            and self.noise_level > self.noise_level_ceiling
        )


@dataclass(frozen=True, slots=True)
class BranchWeight:
    """One branch's effective weight and why it is what it is."""

    branch: str
    base_weight: float
    effective_weight: float
    degraded_by: tuple[str, ...] = ()
    excluded_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.branch:
            raise BranchFusionError("branch must be named")
        if self.base_weight < 0 or self.effective_weight < 0:
            raise BranchFusionError("weights must not be negative")
        if self.excluded_reason is not None and self.effective_weight != 0.0:
            raise BranchFusionError("an excluded branch must carry zero weight")


@dataclass(frozen=True, slots=True)
class FusedAspect:
    """One aspect's fused score with the weighting that produced it."""

    aspect: str
    score: float | None
    state: MetricState
    weights: tuple[BranchWeight, ...] = field(default_factory=tuple)
    contributing_branches: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.aspect:
            raise BranchFusionError("aspect must be named")
        if self.state is MetricState.OBSERVED:
            if self.score is None:
                raise BranchFusionError("an observed aspect needs a score")
            if not self.contributing_branches:
                raise BranchFusionError("an observed aspect needs a contributor")
        elif self.score is not None:
            raise BranchFusionError("a non-observed aspect must not carry a score")

    @property
    def weight_sum(self) -> float:
        return sum(item.effective_weight for item in self.weights)


def resolve_weights(
    *,
    branches: Sequence[S1Branch],
    base_weights: Mapping[str, float],
    conditions: EvidenceConditions,
) -> tuple[BranchWeight, ...]:
    """Apply the declared degradation rules, then normalise over survivors."""

    if not branches:
        raise BranchFusionError("no branch to weigh")
    names = [branch.name for branch in branches]
    if len(set(names)) != len(names):
        raise BranchFusionError("duplicate branch name")

    resolved: list[BranchWeight] = []
    for branch in branches:
        base = float(base_weights.get(branch.name, 1.0))
        if base < 0:
            raise BranchFusionError(f"base weight for {branch.name} is negative")
        if branch.state is not MetricState.OBSERVED or branch.score is None:
            resolved.append(
                BranchWeight(
                    branch=branch.name,
                    base_weight=base,
                    effective_weight=0.0,
                    excluded_reason=f"branch_state_{branch.state.value}",
                )
            )
            continue
        degraded: list[str] = []
        weight = base
        if conditions.alignment_is_weak and branch.name in ALIGNMENT_DEPENDENT_BRANCHES:
            weight *= DEGRADED_WEIGHT_FACTOR
            degraded.append("low_alignment_confidence")
        if conditions.recording_is_noisy and branch.name in NOISE_SENSITIVE_BRANCHES:
            weight *= DEGRADED_WEIGHT_FACTOR
            degraded.append("high_noise_level")
        # A branch's own confidence scales its weight: a branch that says it is
        # unsure should not carry the same vote as one that is sure.
        if branch.confidence is not None:
            if not 0.0 <= float(branch.confidence) <= 1.0:
                raise BranchFusionError(f"{branch.name} confidence is out of range")
            weight *= float(branch.confidence)
        resolved.append(
            BranchWeight(
                branch=branch.name,
                base_weight=base,
                effective_weight=weight,
                degraded_by=tuple(degraded),
            )
        )

    total = sum(item.effective_weight for item in resolved)
    if total <= 0.0:
        return tuple(resolved)
    return tuple(
        BranchWeight(
            branch=item.branch,
            base_weight=item.base_weight,
            effective_weight=item.effective_weight / total,
            degraded_by=item.degraded_by,
            excluded_reason=item.excluded_reason,
        )
        for item in resolved
    )


def fuse_aspect(
    *,
    aspect: str,
    branches: Sequence[S1Branch],
    base_weights: Mapping[str, float] | None = None,
    conditions: EvidenceConditions | None = None,
) -> FusedAspect:
    """Fuse one aspect from its branches under evidence-aware weights."""

    weights = resolve_weights(
        branches=branches,
        base_weights=base_weights or {},
        conditions=conditions or EvidenceConditions(),
    )
    by_name = {branch.name: branch for branch in branches}
    contributing = tuple(
        item.branch for item in weights if item.effective_weight > 0.0
    )
    if not contributing:
        return FusedAspect(
            aspect=aspect,
            score=None,
            state=MetricState.MISSING,
            weights=weights,
            contributing_branches=(),
        )
    score = sum(
        item.effective_weight * float(by_name[item.branch].score or 0.0)
        for item in weights
        if item.effective_weight > 0.0
    )
    return FusedAspect(
        aspect=aspect,
        score=max(0.0, min(1.0, score)),
        state=MetricState.OBSERVED,
        weights=weights,
        contributing_branches=contributing,
    )


def required_branches_missing(
    *,
    branches: Sequence[S1Branch],
    required: Sequence[str],
) -> tuple[str, ...]:
    """Required branches that did not report an observed value.

    Kept separate from fusion: fusion can produce a number from whatever survived,
    but the gate needs to know a *required* branch was absent so the attempt ends
    as ``uncertain`` rather than being scored on a partial basis.
    """

    observed = {
        branch.name
        for branch in branches
        if branch.state is MetricState.OBSERVED and branch.score is not None
    }
    return tuple(sorted(name for name in required if name not in observed))


__all__ = [
    "ALIGNMENT_DEPENDENT_BRANCHES",
    "DEGRADED_WEIGHT_FACTOR",
    "NOISE_SENSITIVE_BRANCHES",
    "BranchFusionError",
    "BranchWeight",
    "EvidenceConditions",
    "FusedAspect",
    "fuse_aspect",
    "required_branches_missing",
    "resolve_weights",
]
