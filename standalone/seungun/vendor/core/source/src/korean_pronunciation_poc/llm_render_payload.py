from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .coaching_policy import CoachingPolicyResult
from .llm_feedback_payload import LlmFeedbackPayload
from .reference_comparison import PopulationId


class _InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LlmFactualSummaryItem(_InternalModel):
    rule_id: str = Field(min_length=1)
    message_ko: str = Field(min_length=1)
    level: Literal["info", "suggestion", "warning"]
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class LlmRenderDirective(_InternalModel):
    directive_id: str = Field(min_length=1)
    title_ko: str = Field(min_length=1)
    instruction_ko: str = Field(min_length=1)
    rationale_ko: str = Field(min_length=1)
    comparison_population: PopulationId
    interpretation_scope: str = Field(min_length=1)


class LlmRenderPayload(_InternalModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    analysis_id: UUID
    script: str = Field(min_length=1)
    factual_summary: tuple[LlmFactualSummaryItem, ...]
    directives: tuple[LlmRenderDirective, ...]
    limitations: tuple[str, ...] = Field(min_length=1)
    prohibited_claims: tuple[str, ...] = Field(min_length=1)
    output_contract: tuple[str, ...] = Field(min_length=1)
    notes: tuple[str, ...]


_FACTUAL_RULE_IDS = frozenset(
    {
        "REPORT_DURATION",
        "REPORT_SPEAKING_RATE",
        "REPORT_F0_RANGE",
        "F0_UNAVAILABLE",
        "ALIGNMENT_UNAVAILABLE",
    }
)
_NON_FACTUAL_PHRASES = (
    "하세요",
    "하십시오",
    "연습",
    "재녹음",
    "다시 녹음",
    "향상시키세요",
    "교정하세요",
    "더 좋은 발음",
    "올바른 억양",
    "부족합니다",
    "발음이 정확",
    "발음이 부정확",
    "아나운서보다 우수",
    "아나운서보다 열등",
    "합격",
    "불합격",
    "점수",
    "등급",
)
_FACTUAL_MARKER = "객관적 분석 결과"


def _factual_summary(
    payload: LlmFeedbackPayload,
) -> tuple[LlmFactualSummaryItem, ...]:
    seen_messages: set[str] = set()
    items: list[LlmFactualSummaryItem] = []
    for feedback in payload.available_feedback:
        if feedback.rule_id not in _FACTUAL_RULE_IDS:
            continue
        if _FACTUAL_MARKER not in feedback.message_ko:
            continue
        if any(phrase in feedback.message_ko for phrase in _NON_FACTUAL_PHRASES):
            continue
        if feedback.message_ko in seen_messages:
            continue
        seen_messages.add(feedback.message_ko)
        items.append(
            LlmFactualSummaryItem(
                rule_id=feedback.rule_id,
                message_ko=feedback.message_ko,
                level=feedback.level,
                evidence_ids=feedback.evidence_ids,
            )
        )
    return tuple(items)


def build_llm_render_payload(
    policy: CoachingPolicyResult,
    feedback_payload: LlmFeedbackPayload,
) -> LlmRenderPayload:
    if policy.analysis_id != feedback_payload.analysis_id:
        raise ValueError("policy and LLM feedback payload IDs must match")
    factual_summary = _factual_summary(feedback_payload)
    notes = (
        ()
        if factual_summary
        else (
            "재사용할 수 있는 기존 사실 문장이 없어 factual_summary가 비어 있습니다.",
        )
    )
    return LlmRenderPayload(
        analysis_id=policy.analysis_id,
        script=feedback_payload.script,
        factual_summary=factual_summary,
        directives=tuple(
            LlmRenderDirective(
                directive_id=directive.directive_id,
                title_ko=directive.title_ko,
                instruction_ko=directive.instruction_ko,
                rationale_ko=directive.rationale_ko,
                comparison_population=directive.comparison_population,
                interpretation_scope=directive.interpretation_scope,
            )
            for directive in policy.directives
        ),
        limitations=policy.limitations,
        prohibited_claims=policy.prohibited_claims,
        output_contract=(
            "factual_summary와 제공된 directive만 자연스러운 한국어로 연결합니다.",
            "directive를 추가·삭제하거나 연습 방향을 변경하지 않습니다.",
            "새로운 수치·점수·등급·품질 판단을 생성하지 않습니다.",
            "출력 항목은 directive_id와 해당 한국어 문장만 포함합니다.",
        ),
        notes=notes,
    )


def load_llm_render_payload(path: Path) -> LlmRenderPayload:
    return LlmRenderPayload.model_validate_json(path.read_text(encoding="utf-8"))
