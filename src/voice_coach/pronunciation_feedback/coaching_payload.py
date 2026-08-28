"""The structured payload a coaching LLM is allowed to see, and its return contract.

Two constraints shape this module, both from the paper (§5.4) and both enforced by
types rather than by prompt text:

* **The model never scores speech.** The payload carries no audio, no path, and no
  waveform — only numbers and labels that earlier stages already decided. A model
  that cannot hear the take cannot overrule the scorer.
* **The model never moves a location or a score.** It returns a message and an
  advice id, nothing else. Spans, severity and confidence are attached afterwards
  from the issue the pipeline actually detected.

The payload is also a privacy boundary: it is checked for filesystem paths,
digests and speaker identifiers before it leaves the process, because the same
object is what a local model reads and what a run receipt may quote.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import json
import re
from typing import Any, Mapping

from .domain import DiagnosticIssueDraft, MetricState, PromptSpec

PAYLOAD_SCHEMA_VERSION = "voice_coach.coaching_payload.v1"
#: Cap on how many issues reach the model. The paper fixes the coached count at
#: one to control cognitive load; a couple more are passed as context only.
MAXIMUM_PAYLOAD_ISSUES = 3
MAXIMUM_MESSAGE_CHARACTERS = 400

_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
_PATH_LIKE = re.compile(r"(?:^|[\s\"'(])(?:/|\.{1,2}/|[A-Za-z]:\\)|\.(?:wav|parquet|json|safetensors)\b")
_SPEAKER_LIKE = re.compile(r"\b(?:spk|speaker|utt|utterance)[_-][0-9A-Za-z]{4,}\b", re.I)


class CoachingPayloadError(ValueError):
    """Raised when a payload or a generated response breaks the contract."""


@dataclass(frozen=True, slots=True)
class PayloadLocation:
    start_ms: int
    end_ms: int
    syllable_index: int

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise CoachingPayloadError("location must be a positive forward range")
        if self.syllable_index < 0:
            raise CoachingPayloadError("syllable_index must not be negative")


@dataclass(frozen=True, slots=True)
class PayloadIssue:
    """One issue as the model sees it.

    ``observed`` is the phone the learner actually produced. It stays ``None``
    until a realized-phone recogniser exists; the model must therefore be able to
    advise from the target alone rather than assuming a substitution.
    """

    issue_type: str
    target: str | None
    observed: str | None
    location: PayloadLocation | None
    severity: float
    confidence: float
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.issue_type:
            raise CoachingPayloadError("issue_type must not be empty")
        for name in ("severity", "confidence"):
            value = getattr(self, name)
            if not 0.0 <= float(value) <= 1.0:
                raise CoachingPayloadError(f"{name} must be within [0, 1]")
        if not self.evidence:
            raise CoachingPayloadError("an issue must carry at least one evidence tag")


@dataclass(frozen=True, slots=True)
class PayloadProsody:
    """Measured prosody context. Unmeasured axes are omitted, never zero-filled."""

    values: Mapping[str, float] = field(default_factory=dict)
    boundary_tone: str | None = None


@dataclass(frozen=True, slots=True)
class CoachingPayload:
    """Exactly what a coaching model receives."""

    schema_version: str
    target_text: str
    learner_level: str
    top_issues: tuple[PayloadIssue, ...]
    allowed_advice_ids: tuple[str, ...]
    prosody: PayloadProsody | None = None

    def __post_init__(self) -> None:
        if self.schema_version != PAYLOAD_SCHEMA_VERSION:
            raise CoachingPayloadError("payload schema version differs")
        if not self.target_text:
            raise CoachingPayloadError("target_text must not be empty")
        if not self.learner_level:
            raise CoachingPayloadError("learner_level must be named, including unknown")
        if not self.top_issues:
            raise CoachingPayloadError("payload must carry at least one issue")
        if len(self.top_issues) > MAXIMUM_PAYLOAD_ISSUES:
            raise CoachingPayloadError(
                f"payload carries more than {MAXIMUM_PAYLOAD_ISSUES} issues"
            )
        if not self.allowed_advice_ids:
            raise CoachingPayloadError("allowed_advice_ids must not be empty")
        if len(set(self.allowed_advice_ids)) != len(self.allowed_advice_ids):
            raise CoachingPayloadError("allowed_advice_ids must be unique")

    def to_json_object(self) -> dict[str, Any]:
        """Serialize in the paper's field naming, for the model prompt."""

        issues: list[dict[str, Any]] = []
        for issue in self.top_issues:
            record: dict[str, Any] = {
                "type": issue.issue_type,
                "target": issue.target,
                "observed": issue.observed,
                "severity": round(issue.severity, 4),
                "confidence": round(issue.confidence, 4),
                "evidence": list(issue.evidence),
            }
            if issue.location is not None:
                record["location"] = {
                    "startMs": issue.location.start_ms,
                    "endMs": issue.location.end_ms,
                    "syllableIndex": issue.location.syllable_index,
                }
            else:
                record["location"] = None
            issues.append(record)
        document: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "targetText": self.target_text,
            "learnerLevel": self.learner_level,
            "topIssues": issues,
            "allowedAdviceIds": list(self.allowed_advice_ids),
        }
        if self.prosody is not None:
            prosody: dict[str, Any] = {
                key: round(value, 4) for key, value in sorted(self.prosody.values.items())
            }
            if self.prosody.boundary_tone is not None:
                prosody["boundaryTone"] = self.prosody.boundary_tone
            if prosody:
                document["prosody"] = prosody
        return document

    def to_json(self) -> str:
        return json.dumps(
            self.to_json_object(),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )


def assert_payload_is_privacy_safe(payload: CoachingPayload) -> None:
    """Reject a payload that would carry a path, digest or speaker id to a model.

    Checked on the serialized form so a value hidden in any field is caught, not
    just the ones this function knows to look at.
    """

    serialized = payload.to_json()
    if _SHA256.search(serialized):
        raise CoachingPayloadError("payload would carry a digest")
    if _PATH_LIKE.search(serialized):
        raise CoachingPayloadError("payload would carry a filesystem path")
    if _SPEAKER_LIKE.search(serialized):
        raise CoachingPayloadError("payload would carry a speaker identifier")


def build_coaching_payload(
    *,
    prompt_spec: PromptSpec,
    coached_issues: Sequence[DiagnosticIssueDraft],
    allowed_advice_ids: Sequence[str],
    learner_level: str = "unknown",
    prosody: PayloadProsody | None = None,
) -> CoachingPayload:
    """Assemble the payload from what the pipeline already decided."""

    if not coached_issues:
        raise CoachingPayloadError("no coached issue to advise on")
    issues: list[PayloadIssue] = []
    for issue in coached_issues[:MAXIMUM_PAYLOAD_ISSUES]:
        location: PayloadLocation | None = None
        slot = (
            prompt_spec.syllable(issue.syllable_index)
            if issue.syllable_index is not None
            else None
        )
        if (
            issue.start_ms is not None
            and issue.end_ms is not None
            and slot is not None
        ):
            location = PayloadLocation(
                start_ms=issue.start_ms,
                end_ms=issue.end_ms,
                syllable_index=slot.index,
            )
        target = slot.syllable if slot is not None else None
        issues.append(
            PayloadIssue(
                issue_type=issue.issue_type,
                target=target,
                # No realized-phone recogniser yet: the model must advise from the
                # target, not from an assumed substitution.
                observed=None,
                location=location,
                severity=issue.severity,
                confidence=issue.confidence,
                evidence=tuple(issue.evidence),
            )
        )
    payload = CoachingPayload(
        schema_version=PAYLOAD_SCHEMA_VERSION,
        target_text=prompt_spec.normalized_text,
        learner_level=learner_level,
        top_issues=tuple(issues),
        allowed_advice_ids=tuple(dict.fromkeys(allowed_advice_ids)),
        prosody=prosody,
    )
    assert_payload_is_privacy_safe(payload)
    return payload


@dataclass(frozen=True, slots=True)
class GeneratedAdvice:
    """What a generator may return: a message bound to one allowlisted advice id.

    Deliberately carries no span, severity or confidence. Those come from the
    issue the pipeline detected, so a model cannot relocate a highlight or inflate
    a score by writing different numbers.
    """

    issue_type: str
    advice_id: str
    message: str


def validate_generated_advice(
    *,
    payload: CoachingPayload,
    generated: Sequence[GeneratedAdvice],
) -> tuple[GeneratedAdvice, ...]:
    """Check a model response against the payload it was given.

    A model can ignore instructions, so the allowlist is enforced here rather
    than trusted to the prompt. Any violation discards the whole response: a
    partially trustworthy set of coaching messages is not something to show a
    learner, and the caller degrades to ``uncertain``.
    """

    if not generated:
        raise CoachingPayloadError("generator returned no advice")
    allowed_ids = set(payload.allowed_advice_ids)
    payload_types = {issue.issue_type for issue in payload.top_issues}
    seen: set[str] = set()
    for item in generated:
        if item.advice_id not in allowed_ids:
            raise CoachingPayloadError(
                f"advice id outside the allowlist: {item.advice_id}"
            )
        if item.issue_type not in payload_types:
            raise CoachingPayloadError(
                f"advice refers to an issue outside the payload: {item.issue_type}"
            )
        if item.issue_type in seen:
            raise CoachingPayloadError("duplicate advice for one issue type")
        seen.add(item.issue_type)
        message = item.message.strip()
        if not message:
            raise CoachingPayloadError("advice message must not be empty")
        if len(message) > MAXIMUM_MESSAGE_CHARACTERS:
            raise CoachingPayloadError("advice message exceeds the length limit")
        if _SHA256.search(message):
            raise CoachingPayloadError("advice message contains a digest")
        if _PATH_LIKE.search(message):
            raise CoachingPayloadError("advice message contains a filesystem path")
        if _SPEAKER_LIKE.search(message):
            raise CoachingPayloadError("advice message contains a speaker identifier")
    return tuple(generated)


class UnavailableAdviceGenerator:
    """Explicit stand-in when no local coaching model is present.

    The pipeline then serves the frozen allowlist messages instead. Declaring the
    absence keeps "no model" distinguishable from "model produced nothing".
    """

    def __init__(self, detail: str | None = None) -> None:
        self._detail = detail or "no local coaching model is available"

    @property
    def generator_id(self) -> str:
        return "unavailable-advice-generator"

    @property
    def revision(self) -> str:
        return "n/a"

    def generate(self, payload: CoachingPayload) -> tuple[GeneratedAdvice, ...]:
        del payload
        raise CoachingPayloadError(self._detail)


__all__ = [
    "MAXIMUM_MESSAGE_CHARACTERS",
    "MAXIMUM_PAYLOAD_ISSUES",
    "PAYLOAD_SCHEMA_VERSION",
    "CoachingPayload",
    "CoachingPayloadError",
    "GeneratedAdvice",
    "PayloadIssue",
    "PayloadLocation",
    "PayloadProsody",
    "UnavailableAdviceGenerator",
    "assert_payload_is_privacy_safe",
    "build_coaching_payload",
    "validate_generated_advice",
]
