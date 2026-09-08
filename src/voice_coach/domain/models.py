"""Stable JSON contracts for diagnosis, audit, and coaching."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

Probability = Annotated[float, Field(ge=0.0, le=1.0)]
Score = Annotated[float, Field(ge=0.0, le=1.0)]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class AuditStatus(StrEnum):
    APPROVED = "approved"
    DISAGREEMENT = "disagreement"
    UNCERTAIN = "uncertain"
    NOT_RUN = "not_run"


class EvidenceId(StrEnum):
    GOLD_LABEL = "gold_label"
    CTC_COMPETING_PHONE = "ctc_competing_phone"
    GOP_LOW = "gop_low"
    DURATION_SHORT = "duration_short"
    DURATION_LONG = "duration_long"
    ALIGNMENT_LOW = "alignment_low"
    CONTENT_MISMATCH = "content_mismatch"
    PROSODY_OUTLIER = "prosody_outlier"


class TimeRange(ContractModel):
    start_ms: Annotated[int, Field(ge=0)]
    end_ms: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def validate_order(self) -> TimeRange:
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class ContentMatch(ContractModel):
    score: Score
    confidence: Probability


class AspectScores(ContractModel):
    segmental: Score
    fluency: Score
    prosody: Score
    comprehensibility: Score


class DiagnosticIssue(ContractModel):
    issue_type: str = Field(alias="type", min_length=1)
    location: TimeRange
    target: str | None = None
    observed: str | None = None
    severity: Score
    confidence: Probability
    comprehensibility_impact: Score = 1.0
    trainability: Score = 1.0
    evidence: Annotated[list[EvidenceId], Field(min_length=1)]
    priority: Annotated[float | None, Field(ge=0.0, le=1.0)] = None


class AuditResult(ContractModel):
    status: AuditStatus = AuditStatus.NOT_RUN
    s2_confidence: Probability | None = None


class CoachingResult(ContractModel):
    advice_id: str | None = None
    next_text_id: str | None = None
    action: Literal["coach", "rerecord", "expert_review", "none"]
    message: str


class DiagnosticProfile(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    attempt_id: UUID
    target_text: Annotated[str, Field(min_length=1)]
    content_match: ContentMatch
    aspects: AspectScores
    issues: list[DiagnosticIssue]
    audit: AuditResult
    coaching: CoachingResult
    analysis_source: Literal["oracle", "model"]
