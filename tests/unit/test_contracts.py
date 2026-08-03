import pytest
from pydantic import ValidationError

from voice_coach.domain.models import DiagnosticIssue, EvidenceId, TimeRange


def test_time_range_rejects_reversed_interval() -> None:
    with pytest.raises(ValidationError):
        TimeRange(start_ms=200, end_ms=100)


def test_issue_serializes_external_type_name() -> None:
    issue = DiagnosticIssue(
        type="duration_short",
        location={"startMs": 10, "endMs": 20},
        severity=0.8,
        confidence=0.9,
        evidence=[EvidenceId.GOLD_LABEL],
    )

    assert issue.model_dump(by_alias=True)["type"] == "duration_short"
