from voice_coach.coaching.prioritizer import select_coaching_issues
from voice_coach.domain.models import DiagnosticIssue, EvidenceId


def issue(
    issue_type: str,
    *,
    severity: float,
    confidence: float,
    start_ms: int,
) -> DiagnosticIssue:
    return DiagnosticIssue(
        type=issue_type,
        location={"startMs": start_ms, "endMs": start_ms + 10},
        target="ㄱ",
        severity=severity,
        confidence=confidence,
        evidence=[EvidenceId.GOP_LOW],
    )


def test_top_one_low_confidence_abstains_to_rerecord() -> None:
    selection = select_coaching_issues(
        [issue("supported", severity=1.0, confidence=0.74, start_ms=0)],
        supported_issue_types={"supported"},
        confidence_threshold=0.75,
        top_k=1,
    )

    assert selection.selected == ()
    assert selection.blocked[0].reason == "below_confidence"
    assert selection.action == "rerecord"


def test_top_one_unsupported_claim_abstains_to_expert_review() -> None:
    selection = select_coaching_issues(
        [issue("unsupported", severity=1.0, confidence=0.9, start_ms=0)],
        supported_issue_types={"supported"},
        confidence_threshold=0.75,
        top_k=1,
    )

    assert selection.selected == ()
    assert selection.blocked[0].reason == "unsupported_claim"
    assert selection.action == "expert_review"


def test_top_three_does_not_backfill_blocked_ranked_window_items() -> None:
    issues = [
        issue("unsupported", severity=1.0, confidence=0.95, start_ms=0),
        issue("supported_a", severity=0.9, confidence=0.95, start_ms=10),
        issue("supported_low", severity=0.8, confidence=0.70, start_ms=20),
        issue("supported_backfill", severity=0.5, confidence=0.95, start_ms=30),
    ]
    selection = select_coaching_issues(
        issues,
        supported_issue_types={
            "supported_a",
            "supported_low",
            "supported_backfill",
        },
        confidence_threshold=0.75,
        top_k=3,
    )

    assert [item.issue_type for item in selection.ranked_window] == [
        "unsupported",
        "supported_a",
        "supported_low",
    ]
    assert [item.issue_type for item in selection.selected] == ["supported_a"]
    assert [item.reason for item in selection.blocked] == [
        "unsupported_claim",
        "below_confidence",
    ]
    assert selection.action == "coach"


def test_ranking_ties_are_independent_of_input_order() -> None:
    first = issue("b", severity=0.8, confidence=0.8, start_ms=0)
    second = issue("a", severity=0.8, confidence=0.8, start_ms=0)
    forward = select_coaching_issues(
        [first, second],
        supported_issue_types={"a", "b"},
        confidence_threshold=0.75,
        top_k=1,
    )
    reverse = select_coaching_issues(
        [second, first],
        supported_issue_types={"a", "b"},
        confidence_threshold=0.75,
        top_k=1,
    )

    assert forward.selected[0].issue_type == "a"
    assert reverse.selected[0].issue_type == "a"
