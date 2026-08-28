"""Stage H: turn a passed decision into learner-facing feedback.

This module composes; it never judges.  It cannot introduce an issue, move a
location, or write a message that is not in the advice allowlist.  Two rules do
the work:

* a span is emitted only when syllable index, character offsets, and a time
  range are all present — otherwise the issue is reported without a location
  instead of guessing one;
* an unmeasured axis is reported with its :class:`MetricState`, never as a low
  score.
"""

from __future__ import annotations

from collections.abc import Mapping

from .domain import (
    AdviceItem,
    AlignmentObservation,
    AspectSummary,
    DiagnosticIssueDraft,
    FeedbackSpan,
    LearnerFeedback,
    MetricState,
    MultiReferenceDistance,
    PracticePrompt,
    PromptSpec,
    ReferenceBank,
    S1ScoreBundle,
)
from .ports import AdviceCatalogPort
from .thresholds import RESEARCH_VALIDATED, ThresholdSet

#: Named from the loaded threshold set rather than fixed here.  A constant
#: would keep telling the learner which cohort the numbers came from long after
#: the thresholds were refrozen on a different one, which is a provenance claim
#: the pipeline would have no way to keep true.
COHORT_NOTICE_TEMPLATE = (
    "이 진단의 임계값은 '{cohort}' 코호트에서 고정되었다. "
    "다른 코호트, 모어 화자, 아나운서 규범 통과 여부로 해석하지 않는다."
)


def cohort_notice(thresholds: ThresholdSet) -> str:
    return COHORT_NOTICE_TEMPLATE.format(cohort=thresholds.cohort)


ENGINEERING_SCOPE_NOTICE = (
    "이 결과의 주장 범위는 engineering_provisional이다. "
    "전문가 감사와 locked 평가를 통과한 검증 결과가 아니다."
)
UNCALIBRATED_NOTICE = (
    "보정된 threshold artifact가 없는 profile이므로 점수와 판정은 참고용이다."
)
NO_REFERENCE_NOTICE = "권리·metadata 요건을 충족한 기준 음성 bank가 없어 기준 음성 비교를 제공하지 않는다."
SYNTHETIC_REFERENCE_NOTICE = (
    "제공된 기준 음성은 AI가 생성한 합성음성이다. 모어 화자 녹음이 아니다."
)
_SEGMENTAL_SOFT_INFORMATION_ISSUES = frozenset(
    {"final_consonant_deletion", "vowel_quality"}
)
MULTI_REFERENCE_NOTICE = (
    "기준 음성은 단일 정답이 아니라 허용 가능한 여러 발화 중 하나이며, "
    "비교는 그 집합에서 가장 가까운 발화와의 거리로 한다."
)


def resolve_span(
    issue: DiagnosticIssueDraft,
    *,
    prompt_spec: PromptSpec,
) -> FeedbackSpan | None:
    """Join a draft issue to its prompt location, or return ``None``."""

    if issue.syllable_index is None or issue.start_ms is None or issue.end_ms is None:
        return None
    if issue.end_ms <= issue.start_ms:
        return None
    slot = prompt_spec.syllable(issue.syllable_index)
    if slot is None:
        return None
    return FeedbackSpan(
        syllable_index=slot.index,
        char_start=slot.char_start,
        char_end=slot.char_end,
        start_ms=issue.start_ms,
        end_ms=issue.end_ms,
        syllable=slot.syllable,
    )


def summarize_aspects(
    bundle: S1ScoreBundle,
    alignment: AlignmentObservation,
) -> tuple[AspectSummary, ...]:
    """Report every axis with its state; missing stays missing."""

    summaries = [
        AspectSummary(
            name="content",
            score=alignment.content_score,
            state=alignment.content_state,
        )
    ]
    summaries.extend(
        AspectSummary(name=branch.name, score=branch.score, state=branch.state)
        for branch in bundle.branches
    )
    return tuple(summaries)


def compose_learner_feedback(
    *,
    prompt: PracticePrompt,
    prompt_spec: PromptSpec,
    alignment: AlignmentObservation,
    bundle: S1ScoreBundle,
    coached_issues: tuple[DiagnosticIssueDraft, ...],
    advice_catalog: AdviceCatalogPort,
    thresholds: ThresholdSet,
    next_action: str,
    message_overrides: Mapping[str, str] | None = None,
    reference_bank: ReferenceBank | None = None,
    reference_distance: MultiReferenceDistance | None = None,
) -> LearnerFeedback:
    advice: list[AdviceItem] = []
    soft_information_by_syllable = {
        item.syllable_index: item for item in bundle.soft_information
    }
    unlocated: list[str] = []
    for issue in coached_issues:
        entry = advice_catalog.lookup(issue.issue_type)
        if entry is None:
            # Defensive: the gate is expected to have filtered this already.
            continue
        advice_id, catalog_message = entry
        message = (
            message_overrides.get(issue.issue_type, catalog_message)
            if message_overrides is not None
            else catalog_message
        )
        span = resolve_span(issue, prompt_spec=prompt_spec)
        if span is None:
            unlocated.append(issue.issue_type)
        advice.append(
            AdviceItem(
                issue_type=issue.issue_type,
                advice_id=advice_id,
                message=message,
                span=span,
                severity=issue.severity,
                confidence=issue.confidence,
                soft_information=(
                    soft_information_by_syllable.get(issue.syllable_index)
                    if (
                        issue.syllable_index is not None
                        and issue.issue_type in _SEGMENTAL_SOFT_INFORMATION_ISSUES
                    )
                    else None
                ),
            )
        )

    exemplars = reference_bank.exemplars if reference_bank is not None else ()
    notices = [cohort_notice(thresholds)]
    if exemplars:
        notices.append(MULTI_REFERENCE_NOTICE)
        if any(item.synthetic for item in exemplars):
            notices.append(SYNTHETIC_REFERENCE_NOTICE)
    else:
        notices.append(NO_REFERENCE_NOTICE)
    if thresholds.claim_scope != RESEARCH_VALIDATED:
        notices.insert(0, ENGINEERING_SCOPE_NOTICE)
    if not thresholds.calibrated:
        notices.insert(0, UNCALIBRATED_NOTICE)
    aspects = summarize_aspects(bundle, alignment)
    if any(summary.state is not MetricState.OBSERVED for summary in aspects):
        notices.append("측정되지 않은 축은 점수 대신 상태로 표시했다.")

    return LearnerFeedback(
        prompt_id=prompt.prompt_id,
        display_text=prompt.display_text,
        next_action=next_action,
        advice=tuple(advice),
        aspects=aspects,
        unlocated_issue_types=tuple(dict.fromkeys(unlocated)),
        notices=tuple(notices),
        reference_audio=exemplars,
        reference_distance=reference_distance if exemplars else None,
    )
