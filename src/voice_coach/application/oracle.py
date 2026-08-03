"""Oracle loop for validating contracts independently of model errors."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from voice_coach.coaching.catalog import ADVICE_CATALOG
from voice_coach.coaching.prioritizer import rank_issues
from voice_coach.domain.models import (
    AspectScores,
    AuditResult,
    AuditStatus,
    CoachingResult,
    ContentMatch,
    ContractModel,
    DiagnosticIssue,
    DiagnosticProfile,
)


class OracleAttempt(ContractModel):
    attempt_id: UUID
    target_text: str = Field(min_length=1)
    content_match: ContentMatch
    aspects: AspectScores
    gold_issues: list[DiagnosticIssue] = Field(default_factory=list)
    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)


def analyze_oracle_attempt(attempt: OracleAttempt) -> DiagnosticProfile:
    ranked = rank_issues(attempt.gold_issues)
    top_issue = ranked[0] if ranked else None

    if attempt.content_match.score < 0.8:
        coaching = CoachingResult(
            action="rerecord",
            message="목표 문장과 녹음 내용이 충분히 일치하지 않습니다. 다시 녹음해 주세요.",
        )
    elif top_issue is None:
        coaching = CoachingResult(
            action="none",
            message="현재 근거에서 우선 교정할 오류가 없습니다.",
        )
    elif top_issue.confidence < attempt.confidence_threshold:
        coaching = CoachingResult(
            action="rerecord",
            message="판단 신뢰도가 낮습니다. 조용한 환경에서 다시 녹음해 주세요.",
        )
    elif top_issue.issue_type not in ADVICE_CATALOG:
        coaching = CoachingResult(
            action="expert_review",
            message="검수된 조언 범위 밖의 오류이므로 전문가 확인이 필요합니다.",
        )
    else:
        advice_id, message = ADVICE_CATALOG[top_issue.issue_type]
        coaching = CoachingResult(
            advice_id=advice_id,
            action="coach",
            message=message,
        )

    return DiagnosticProfile(
        attempt_id=attempt.attempt_id,
        target_text=attempt.target_text,
        content_match=attempt.content_match,
        aspects=attempt.aspects,
        issues=ranked[:3],
        audit=AuditResult(status=AuditStatus.NOT_RUN),
        coaching=coaching,
        analysis_source="oracle",
    )
