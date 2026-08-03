"""Deterministic, evidence-aware issue prioritization."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from voice_coach.domain.models import DiagnosticIssue


def priority_score(issue: DiagnosticIssue) -> float:
    return (
        issue.severity
        * issue.confidence
        * issue.comprehensibility_impact
        * issue.trainability
    )


def rank_issues(issues: list[DiagnosticIssue]) -> list[DiagnosticIssue]:
    scored = [
        issue.model_copy(update={"priority": priority_score(issue)}) for issue in issues
    ]
    return sorted(
        scored,
        key=lambda issue: (
            -(issue.priority or 0.0),
            issue.location.start_ms,
            issue.location.end_ms,
            issue.issue_type,
            issue.target or "",
            issue.observed or "",
            tuple(str(item) for item in issue.evidence),
        ),
    )


BlockedReason = Literal["below_confidence", "unsupported_claim"]
SelectionAction = Literal["coach", "rerecord", "expert_review", "none"]


@dataclass(frozen=True, slots=True)
class BlockedCoachingIssue:
    """A ranked-window issue for which no coaching claim may be emitted."""

    issue: DiagnosticIssue
    reason: BlockedReason


@dataclass(frozen=True, slots=True)
class CoachingIssueSelection:
    """Deterministic top-1/top-3 selection with explicit abstentions."""

    ranked_window: tuple[DiagnosticIssue, ...]
    selected: tuple[DiagnosticIssue, ...]
    blocked: tuple[BlockedCoachingIssue, ...]
    action: SelectionAction


def select_coaching_issues(
    issues: list[DiagnosticIssue],
    *,
    supported_issue_types: Collection[str],
    confidence_threshold: float,
    top_k: Literal[1, 3] = 1,
) -> CoachingIssueSelection:
    """Select only supported, confident issues from the fixed ranked window.

    Blocked items in the first ``top_k`` positions are never replaced by lower
    ranked issues.  This makes top-1/top-3 reproducible and prevents the policy
    from manufacturing a supported claim by silently skipping a more important
    unsupported or low-confidence issue.
    """

    if top_k not in (1, 3):
        raise ValueError("top_k must be exactly 1 or 3")
    if (
        isinstance(confidence_threshold, bool)
        or not isinstance(confidence_threshold, (int, float))
        or not 0.0 <= float(confidence_threshold) <= 1.0
    ):
        raise ValueError("confidence_threshold must be within [0, 1]")

    ranked_window = tuple(rank_issues(issues)[:top_k])
    if not ranked_window:
        return CoachingIssueSelection((), (), (), "none")

    supported = frozenset(supported_issue_types)
    selected: list[DiagnosticIssue] = []
    blocked: list[BlockedCoachingIssue] = []
    for issue in ranked_window:
        if issue.confidence < confidence_threshold:
            blocked.append(BlockedCoachingIssue(issue, "below_confidence"))
        elif issue.issue_type not in supported:
            blocked.append(BlockedCoachingIssue(issue, "unsupported_claim"))
        else:
            selected.append(issue)

    if selected:
        action: SelectionAction = "coach"
    elif blocked[0].reason == "below_confidence":
        action = "rerecord"
    else:
        action = "expert_review"
    return CoachingIssueSelection(
        ranked_window=ranked_window,
        selected=tuple(selected),
        blocked=tuple(blocked),
        action=action,
    )
