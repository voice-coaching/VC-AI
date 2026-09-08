"""Application-level coaching -> re-record closed-loop state machine."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Any, Sequence

from voice_coach.coaching.catalog import ADVICE_CATALOG
from voice_coach.coaching.prioritizer import rank_issues
from voice_coach.domain.models import DiagnosticIssue, DiagnosticProfile

if TYPE_CHECKING:
    from voice_coach.models.generator_adapters import (
        GeneratorCandidate,
        GeneratorFamily,
        GeneratorFamilyChain,
    )
else:
    # Preserve runtime annotation introspection without importing scorer-only
    # modules from the base API process.
    GeneratorCandidate = Any
    GeneratorFamily = Any
    GeneratorFamilyChain = Any


class ClosedLoopApplicationError(RuntimeError):
    """Raised when a loop transition or generated reference is invalid."""


@dataclass(frozen=True, slots=True)
class ClosedLoopDecision:
    action: str
    reason: str
    attempt_index: int
    advice_id: str | None
    next_state: str
    reference_family: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.closed_loop_decision.v1",
            "action": self.action,
            "reason": self.reason,
            "attempt_index": self.attempt_index,
            "advice_id": self.advice_id,
            "next_state": self.next_state,
            "reference_family": self.reference_family,
        }


@dataclass(slots=True)
class ClosedLoopSession:
    """Finite coaching session with explicit re-record transitions."""

    confidence_threshold: float = 0.75
    minimum_content_match: float = 0.8
    max_attempts: int = 3
    attempt_index: int = 0
    state: str = "awaiting_attempt"

    def __post_init__(self) -> None:
        if not 0 <= self.confidence_threshold <= 1:
            raise ClosedLoopApplicationError("confidence_threshold must be in [0, 1]")
        if not 0 <= self.minimum_content_match <= 1:
            raise ClosedLoopApplicationError("minimum_content_match must be in [0, 1]")
        if self.max_attempts < 1:
            raise ClosedLoopApplicationError("max_attempts must be positive")

    def decide(
        self,
        profile: DiagnosticProfile,
        *,
        reference: GeneratorCandidate | None = None,
    ) -> ClosedLoopDecision:
        if self.state not in {"awaiting_attempt", "awaiting_rerecord"}:
            raise ClosedLoopApplicationError(f"session is not accepting an attempt: {self.state}")
        self.attempt_index += 1
        reference_family = reference.family.value if reference is not None else None
        if profile.content_match.score < self.minimum_content_match:
            reason = "content_mismatch"
            action = "rerecord"
            advice_id = None
        elif profile.audit.s2_confidence is not None and profile.audit.s2_confidence < self.confidence_threshold:
            reason = "independent_s2_low_confidence"
            action = "rerecord"
            advice_id = None
        else:
            ranked = rank_issues(profile.issues)
            top_issue = ranked[0] if ranked else None
            if top_issue is None:
                reason = "no_supported_issue"
                action = "none"
                advice_id = None
            elif top_issue.confidence < self.confidence_threshold:
                reason = "diagnostic_low_confidence"
                action = "rerecord"
                advice_id = None
            elif top_issue.issue_type not in ADVICE_CATALOG:
                reason = "unsupported_issue_type"
                action = "expert_review"
                advice_id = None
            else:
                reason = "catalog_supported_issue"
                action = "coach"
                advice_id = ADVICE_CATALOG[top_issue.issue_type][0]
        if action == "rerecord" and self.attempt_index < self.max_attempts:
            self.state = "awaiting_rerecord"
            next_state = self.state
        elif action == "rerecord":
            self.state = "completed_abstained"
            next_state = self.state
        else:
            self.state = "completed"
            next_state = self.state
        return ClosedLoopDecision(
            action=action,
            reason=reason,
            attempt_index=self.attempt_index,
            advice_id=advice_id,
            next_state=next_state,
            reference_family=reference_family,
        )

    def start_rerecord(self) -> None:
        if self.state != "awaiting_rerecord":
            raise ClosedLoopApplicationError("session is not waiting for a re-record")
        self.state = "awaiting_attempt"


class ClosedLoopTTSService:
    """Connect generator-family reference creation to the session state machine."""

    def __init__(self, generator_chain: GeneratorFamilyChain) -> None:
        self.generator_chain = generator_chain

    def generate_reference(
        self,
        learner_waveform: Any,
        learner_text_tokens: Any,
        native_reference_waveform: Any,
        *,
        families: Sequence[GeneratorFamily],
    ) -> tuple[GeneratorCandidate, ...]:
        try:
            import_module("torch")
        except (ImportError, OSError) as exc:
            raise ClosedLoopApplicationError(
                "TTS reference generation requires the optional scorer dependencies; "
                "install voice-coach[scorer] and verify that torch can be loaded"
            ) from exc
        return self.generator_chain.generate_candidates(
            learner_waveform,
            learner_text_tokens,
            native_reference_waveform,
            families=families,
        )

    def decide_attempt(
        self,
        session: ClosedLoopSession,
        profile: DiagnosticProfile,
        *,
        reference: GeneratorCandidate | None = None,
    ) -> ClosedLoopDecision:
        return session.decide(profile, reference=reference)


__all__ = [
    "ClosedLoopApplicationError",
    "ClosedLoopDecision",
    "ClosedLoopSession",
    "ClosedLoopTTSService",
]
