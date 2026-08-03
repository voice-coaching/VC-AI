import pytest

from voice_coach.coaching.prioritizer import rank_issues
from voice_coach.domain.models import DiagnosticIssue, EvidenceId


def issue(issue_type: str, severity: float, confidence: float) -> DiagnosticIssue:
    return DiagnosticIssue(
        type=issue_type,
        location={"startMs": 10, "endMs": 20},
        severity=severity,
        confidence=confidence,
        evidence=[EvidenceId.GOLD_LABEL],
    )


def test_rank_issues_uses_multiplicative_priority() -> None:
    ranked = rank_issues(
        [
            issue("low", severity=0.9, confidence=0.4),
            issue("high", severity=0.8, confidence=0.9),
        ]
    )

    assert ranked[0].issue_type == "high"
    assert ranked[0].priority == pytest.approx(0.72)
