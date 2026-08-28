"""Stage F: decide whether coaching may be emitted at all.

Every check is a veto.  Coaching requires all of: content agreement, the
required S1 branches actually observed, S1/S2 confidence above frozen
thresholds, an independent S2 approval, a calibrated threshold set, and an issue
that is both located and present in the advice allowlist.  Anything else
degrades to rerecord, uncertain, or failed_closed — never to a lower score.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain import (
    AlignmentObservation,
    AttemptStatus,
    AuditVerdict,
    DiagnosticIssueDraft,
    FailureCode,
    MetricState,
    S1ScoreBundle,
    S2AuditResult,
)
from .ports import AdviceCatalogPort
from .thresholds import ThresholdSet


@dataclass(frozen=True, slots=True)
class GateDecision:
    status: AttemptStatus
    failure_code: FailureCode | None
    reason: str
    coached_issues: tuple[DiagnosticIssueDraft, ...] = ()
    next_action: str = "none"


def _rerecord(code: FailureCode, reason: str) -> GateDecision:
    return GateDecision(
        status=AttemptStatus.RERECORD_REQUIRED,
        failure_code=code,
        reason=reason,
        next_action="rerecord",
    )


def _uncertain(code: FailureCode, reason: str) -> GateDecision:
    return GateDecision(
        status=AttemptStatus.UNCERTAIN,
        failure_code=code,
        reason=reason,
        next_action="expert_review",
    )


def rank_issue_drafts(
    issues: tuple[DiagnosticIssueDraft, ...],
) -> tuple[DiagnosticIssueDraft, ...]:
    """Deterministic severity x confidence ordering with stable tie-breaks."""

    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                -(issue.severity * issue.confidence),
                issue.start_ms if issue.start_ms is not None else 0,
                issue.syllable_index if issue.syllable_index is not None else -1,
                issue.issue_type,
            ),
        )
    )


def evaluate_gate(
    *,
    alignment: AlignmentObservation,
    bundle: S1ScoreBundle,
    audit: S2AuditResult,
    issues: tuple[DiagnosticIssueDraft, ...],
    thresholds: ThresholdSet,
    advice_catalog: AdviceCatalogPort,
    max_coached_issues: int = 1,
) -> GateDecision:
    if alignment.content_state is not MetricState.OBSERVED:
        return _uncertain(
            FailureCode.CONTENT_BACKEND_UNAVAILABLE,
            f"content score unusable: {alignment.content_state.value}",
        )
    if (
        thresholds.min_content_score is not None
        and alignment.content_score is not None
        and alignment.content_score < thresholds.min_content_score
    ):
        return _rerecord(
            FailureCode.PROMPT_MISMATCH,
            "read text does not match the prompt; pronunciation was not judged",
        )
    if (
        thresholds.min_alignment_confidence is not None
        and (
            alignment.alignment_confidence is None
            or alignment.alignment_confidence < thresholds.min_alignment_confidence
        )
    ):
        return _rerecord(
            FailureCode.ALIGNMENT_LOW_CONFIDENCE,
            "alignment confidence below the frozen threshold",
        )

    observed = set(bundle.observed_names())
    missing_required = [
        name for name in thresholds.required_branches if name not in observed
    ]
    if missing_required:
        return _uncertain(
            FailureCode.S1_REQUIRED_METRIC_MISSING,
            "required S1 branches unavailable: " + ", ".join(sorted(missing_required)),
        )
    if bundle.confidence_state is not MetricState.OBSERVED:
        return _uncertain(
            FailureCode.ANALYSIS_LOW_CONFIDENCE,
            f"S1 confidence unusable: {bundle.confidence_state.value}",
        )
    if (
        thresholds.min_s1_confidence is not None
        and (
            bundle.overall_confidence is None
            or bundle.overall_confidence < thresholds.min_s1_confidence
        )
    ):
        return _rerecord(
            FailureCode.ANALYSIS_LOW_CONFIDENCE,
            "S1 confidence below the frozen threshold",
        )

    if audit.independence is None or not audit.independence.independent:
        reason = (
            audit.independence.reason
            if audit.independence is not None
            else "no independence receipt"
        )
        return GateDecision(
            status=AttemptStatus.FAILED_CLOSED,
            failure_code=FailureCode.S1_S2_NOT_INDEPENDENT,
            reason=f"S1/S2 independence not established: {reason}",
            next_action="none",
        )
    if audit.verdict is AuditVerdict.NOT_RUN:
        return _uncertain(FailureCode.S2_NOT_RUN, "independent audit did not run")
    if audit.verdict is AuditVerdict.DISAGREEMENT:
        return _uncertain(
            FailureCode.S1_S2_DISAGREEMENT,
            "independent auditor disagrees with S1",
        )
    if audit.verdict is AuditVerdict.UNCERTAIN:
        return _uncertain(
            FailureCode.ANALYSIS_LOW_CONFIDENCE, "independent auditor is uncertain"
        )
    if (
        thresholds.min_s2_confidence is not None
        and (
            audit.confidence is None
            or audit.confidence < thresholds.min_s2_confidence
        )
    ):
        return _uncertain(
            FailureCode.ANALYSIS_LOW_CONFIDENCE,
            "S2 confidence below the frozen threshold",
        )

    if not thresholds.calibrated:
        # The run is complete and internally consistent, but no calibration
        # artifact backs the thresholds, so coaching stays unauthorized.
        return _uncertain(
            FailureCode.UNCALIBRATED_PROFILE,
            "profile has no calibration artifact; coaching not authorized",
        )

    eligible: list[DiagnosticIssueDraft] = []
    blocked_unsupported = False
    for issue in rank_issue_drafts(issues):
        if (
            thresholds.min_issue_confidence is not None
            and issue.confidence < thresholds.min_issue_confidence
        ):
            continue
        if (
            thresholds.min_issue_severity is not None
            and issue.severity < thresholds.min_issue_severity
        ):
            continue
        if not issue.evidence:
            continue
        if advice_catalog.lookup(issue.issue_type) is None:
            blocked_unsupported = True
            continue
        eligible.append(issue)
        if len(eligible) >= max_coached_issues:
            break

    if eligible:
        return GateDecision(
            status=AttemptStatus.COACHING_READY,
            failure_code=None,
            reason="gate passed",
            coached_issues=tuple(eligible),
            next_action="coach",
        )
    if blocked_unsupported:
        return _uncertain(
            FailureCode.ADVICE_NOT_ALLOWED,
            "detected issue type is outside the approved advice allowlist",
        )
    return GateDecision(
        status=AttemptStatus.COMPLETED_NO_ISSUE,
        failure_code=None,
        reason="no supported issue above threshold",
        next_action="none",
    )
