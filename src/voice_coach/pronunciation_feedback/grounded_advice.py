"""Grounded contract between a decided diagnosis and a local coaching LLM.

The acoustic and policy stages have already finished when this module is used.
The model may explain evidence and add general pronunciation teaching guidance.
Source claims are optional reference material, not mandatory wording. Identity,
privacy and wire-shape checks remain; generation failures retain source fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import re
from typing import Any, Mapping

from .coaching_payload import CoachingPayloadError
from .domain import DiagnosticIssueDraft, PromptSpec

GROUNDED_INPUT_SCHEMA_VERSION = "voice_coach.grounded_advice_input.v6"
_EXPANDED_INPUT_SCHEMA_VERSION = "voice_coach.grounded_advice_input.v5"
_PREVIOUS_INPUT_SCHEMA_VERSION = "voice_coach.grounded_advice_input.v4"
_LEGACY_INPUT_SCHEMA_VERSION = "voice_coach.grounded_advice_input.v3"
GROUNDED_OUTPUT_SCHEMA_VERSION = "voice_coach.grounded_advice_output.v3"
MAXIMUM_GROUNDED_MESSAGE_CHARACTERS = 20000  # Backend summaryFeedback contract limit

_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
_PATH_LIKE = re.compile(
    r"(?:^|[\s\"'(])(?:/|\.{1,2}/|[A-Za-z]:\\)|"
    r"\.(?:wav|parquet|json|safetensors)\b"
)
_SPEAKER_LIKE = re.compile(
    r"\b(?:spk|speaker|utt|utterance)[_-][0-9A-Za-z]{4,}\b", re.I
)
_SAFE_CANDIDATE = re.compile(r"^[가-힣ㄱ-ㅎㅏ-ㅣ]{1,4}$")


class GroundedPlanStatus(StrEnum):
    """Whether the model selected an approved plan or explicitly abstained."""

    RENDERED = "rendered"
    ABSTAINED = "abstained"


class GroundedAdviceStyle(StrEnum):
    """Small, deterministic presentation choices available to the model."""

    DIRECT = "direct"
    FOCUS = "focus"
    SUPPORTIVE = "supportive"
    CONCISE = "concise"


class CandidateDisclosure(StrEnum):
    """How an upstream model candidate may be described to the learner."""

    OMIT = "omit"
    MODEL_CANDIDATE = "model_candidate"


@dataclass(frozen=True, slots=True)
class GroundedLocation:
    """A phone or syllable location already decided before the LLM.

    Phone locations also carry a host-authored learner anchor.  The LLM must
    copy that anchor instead of guessing which repeated phone in the sentence
    the detector selected.
    """

    unit_type: str
    unit_index: int
    target_unit: str
    start_ms: int | None
    end_ms: int | None
    syllable_index: int | None = None
    syllable_unit: str | None = None
    word_unit: str | None = None
    learner_anchor: str | None = None

    def __post_init__(self) -> None:
        if self.unit_type not in {"phone", "syllable"}:
            raise CoachingPayloadError("location unit_type must be phone or syllable")
        if self.unit_index < 0:
            raise CoachingPayloadError("unit_index must not be negative")
        if not self.target_unit:
            raise CoachingPayloadError("target_unit must not be empty")
        if (self.start_ms is None) != (self.end_ms is None):
            raise CoachingPayloadError("location time range must be paired")
        if self.start_ms is not None and (
            self.start_ms < 0 or self.end_ms <= self.start_ms
        ):
            raise CoachingPayloadError("location must be a positive forward range")
        text_context = (
            self.syllable_index,
            self.syllable_unit,
            self.word_unit,
            self.learner_anchor,
        )
        if any(item is not None for item in text_context) and not all(
            item is not None for item in text_context
        ):
            raise CoachingPayloadError("location learner text context must be complete")
        if self.syllable_index is not None:
            if self.syllable_index < 0:
                raise CoachingPayloadError("syllable_index must not be negative")
            assert self.syllable_unit is not None
            assert self.word_unit is not None
            assert self.learner_anchor is not None
            if not re.fullmatch(r"[가-힣]", self.syllable_unit):
                raise CoachingPayloadError("syllable_unit must be one Hangul syllable")
            if not self.word_unit or not self.learner_anchor:
                raise CoachingPayloadError("location learner text must not be empty")
            _assert_privacy_safe(self.word_unit, field="location word unit")
            _assert_privacy_safe(self.learner_anchor, field="location learner anchor")


@dataclass(frozen=True, slots=True)
class ApprovedAdviceClaim:
    """One immutable fact or action that may reach the learner."""

    claim_id: str
    text: str
    required_fragments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.claim_id or not self.text.strip():
            raise CoachingPayloadError("approved claims need an id and text")
        _assert_privacy_safe(self.claim_id, field="claim id")
        _assert_privacy_safe(self.text, field="claim text")
        if not isinstance(self.required_fragments, tuple) or any(
            not isinstance(fragment, str)
            or not fragment.strip()
            or fragment not in self.text
            for fragment in self.required_fragments
        ):
            raise CoachingPayloadError("required fragments must come from claim text")
        if len(set(self.required_fragments)) != len(self.required_fragments):
            raise CoachingPayloadError("required fragments must be unique")


@dataclass(frozen=True, slots=True)
class GroundedCoachingPayload:
    """The complete and privacy-safe input a coaching LLM may see."""

    schema_version: str
    target_text: str
    issue_id: str
    issue_type: str
    location: GroundedLocation | None
    observed_candidate: str | None
    observed_candidate_state: str
    evidence_state: str
    severity: float | None
    confidence: float | None
    detector_score: float | None
    operating_threshold: float | None
    score_semantics: str | None
    advice_id: str
    approved_claims: tuple[ApprovedAdviceClaim, ...]
    claim_scope: str
    feedback_evidence: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.schema_version != GROUNDED_INPUT_SCHEMA_VERSION:
            raise CoachingPayloadError("grounded input schema version differs")
        for name in (
            "target_text",
            "issue_id",
            "issue_type",
            "observed_candidate_state",
            "evidence_state",
            "advice_id",
            "claim_scope",
        ):
            value = getattr(self, name)
            if not value:
                raise CoachingPayloadError(f"{name} must not be empty")
            _assert_privacy_safe(value, field=name)
        if self.feedback_evidence is not None and (
            not isinstance(self.feedback_evidence, dict)
            or self.feedback_evidence.get("schemaVersion") != "voice_coach.feedback_evidence.v1"
        ):
            raise CoachingPayloadError("feedback evidence schema differs")
        if not self.approved_claims:
            raise CoachingPayloadError("grounded payload needs an approved claim")
        if self.issue_type == "phone_pronunciation_error" and (
            self.location is None or self.location.learner_anchor is None
        ):
            raise CoachingPayloadError(
                "phone feedback requires an exact learner syllable anchor"
            )
        decision_pair = (self.severity, self.confidence)
        detector_triple = (
            self.detector_score,
            self.operating_threshold,
            self.score_semantics,
        )
        if any(value is not None for value in decision_pair) and not all(
            value is not None for value in decision_pair
        ):
            raise CoachingPayloadError("severity and confidence must appear together")
        if any(value is not None for value in detector_triple) and not all(
            value is not None for value in detector_triple
        ):
            raise CoachingPayloadError(
                "detector score, threshold, and semantics must appear together"
            )
        if not any(value is not None for value in (*decision_pair, *detector_triple)):
            raise CoachingPayloadError("grounded payload needs technical evidence")
        for name in (
            "severity",
            "confidence",
            "detector_score",
            "operating_threshold",
        ):
            value = getattr(self, name)
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise CoachingPayloadError(f"{name} must be within [0, 1]")
        if self.score_semantics is not None:
            _assert_privacy_safe(self.score_semantics, field="score semantics")
        claim_ids = [item.claim_id for item in self.approved_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise CoachingPayloadError("approved claim ids must be unique")
        if self.observed_candidate is not None:
            if not _SAFE_CANDIDATE.fullmatch(self.observed_candidate):
                raise CoachingPayloadError("observed candidate has an unsafe shape")
            if self.observed_candidate_state != "model_candidate_not_ground_truth":
                raise CoachingPayloadError(
                    "an observed candidate must be labelled as a model candidate"
                )
        elif self.observed_candidate_state != "not_available":
            raise CoachingPayloadError(
                "a missing observed candidate must use the not_available state"
            )
        _assert_privacy_safe(self.to_json(), field="serialized payload")

    def to_json_object(self) -> dict[str, Any]:
        location = None
        if self.location is not None:
            location = {
                "unitType": self.location.unit_type,
                "unitIndex": self.location.unit_index,
                "targetUnit": self.location.target_unit,
                "startMs": self.location.start_ms,
                "endMs": self.location.end_ms,
                "syllableIndex": self.location.syllable_index,
                "syllableUnit": self.location.syllable_unit,
                "wordUnit": self.location.word_unit,
                "learnerAnchor": self.location.learner_anchor,
            }
        return {
            "schemaVersion": self.schema_version,
            "task": "select_approved_pronunciation_feedback_plan",
            "targetText": self.target_text,
            "issue": {
                "issueId": self.issue_id,
                "type": self.issue_type,
                "location": location,
                "observedCandidate": self.observed_candidate,
                "observedCandidateState": self.observed_candidate_state,
                "evidenceState": self.evidence_state,
                "technicalEvidence": {
                    "severity": (
                        round(self.severity, 4) if self.severity is not None else None
                    ),
                    "confidence": (
                        round(self.confidence, 4)
                        if self.confidence is not None
                        else None
                    ),
                    "detectorScore": (
                        round(self.detector_score, 6)
                        if self.detector_score is not None
                        else None
                    ),
                    "operatingThreshold": (
                        round(self.operating_threshold, 6)
                        if self.operating_threshold is not None
                        else None
                    ),
                    "scoreSemantics": self.score_semantics,
                },
            },
            "approvedGuidance": {
                "adviceId": self.advice_id,
                "claims": [
                    {
                        "claimId": item.claim_id,
                        "text": item.text,
                        "requiredFragments": [],
                    }
                    for item in self.approved_claims
                ],
            },
            "claimScope": self.claim_scope,
            "constraints": {},
            "feedbackEvidence": self.feedback_evidence,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_json_object(),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )

    @classmethod
    def from_json_object(cls, value: object) -> GroundedCoachingPayload:
        """Strictly reconstruct a payload returned across an application boundary."""

        if not isinstance(value, Mapping):
            raise CoachingPayloadError("grounded payload must be an object")
        required = {
            "schemaVersion",
            "task",
            "targetText",
            "issue",
            "approvedGuidance",
            "claimScope",
            "constraints",
        }
        if value.get("schemaVersion") == GROUNDED_INPUT_SCHEMA_VERSION:
            required.add("feedbackEvidence")
        if set(value) != required:
            raise CoachingPayloadError("grounded payload schema differs")
        if value["schemaVersion"] not in (
            GROUNDED_INPUT_SCHEMA_VERSION, _EXPANDED_INPUT_SCHEMA_VERSION, _PREVIOUS_INPUT_SCHEMA_VERSION,
            _LEGACY_INPUT_SCHEMA_VERSION
        ):
            raise CoachingPayloadError("grounded input schema version differs")
        legacy = value["schemaVersion"] == _LEGACY_INPUT_SCHEMA_VERSION
        if value["task"] != "select_approved_pronunciation_feedback_plan":
            raise CoachingPayloadError("grounded payload task differs")
        issue = value["issue"]
        guidance = value["approvedGuidance"]
        constraints = value["constraints"]
        if not isinstance(issue, Mapping) or set(issue) != {
            "issueId",
            "type",
            "location",
            "observedCandidate",
            "observedCandidateState",
            "evidenceState",
            "technicalEvidence",
        }:
            raise CoachingPayloadError("grounded payload issue schema differs")
        technical = issue["technicalEvidence"]
        if not isinstance(technical, Mapping) or set(technical) != {
            "severity",
            "confidence",
            "detectorScore",
            "operatingThreshold",
            "scoreSemantics",
        }:
            raise CoachingPayloadError(
                "grounded payload technical evidence schema differs"
            )
        if not isinstance(guidance, Mapping) or set(guidance) != {
            "adviceId",
            "claims",
        }:
            raise CoachingPayloadError("grounded payload guidance schema differs")
        expected_constraints = {
            "mayAddIssue": False,
            "mayAddPhone": False,
            "mayAddLocation": False,
            "mayAddScore": False,
            "mayAddArticulationFact": False,
            "maximumIssues": 1,
        }
        accepted_constraints = (
            {} if value["schemaVersion"] in (GROUNDED_INPUT_SCHEMA_VERSION, _EXPANDED_INPUT_SCHEMA_VERSION)
            else expected_constraints
        )
        if constraints != accepted_constraints:
            raise CoachingPayloadError("grounded payload constraints differ")

        raw_location = issue["location"]
        location = None
        if raw_location is not None:
            if not isinstance(raw_location, Mapping) or set(raw_location) != {
                "unitType",
                "unitIndex",
                "targetUnit",
                "startMs",
                "endMs",
                "syllableIndex",
                "syllableUnit",
                "wordUnit",
                "learnerAnchor",
            }:
                raise CoachingPayloadError("grounded payload location schema differs")
            if (
                not isinstance(raw_location["unitType"], str)
                or not isinstance(raw_location["targetUnit"], str)
                or isinstance(raw_location["unitIndex"], bool)
                or not isinstance(raw_location["unitIndex"], int)
            ):
                raise CoachingPayloadError("grounded payload location value is invalid")
            for field in ("startMs", "endMs", "syllableIndex"):
                raw = raw_location[field]
                if raw is not None and (
                    isinstance(raw, bool) or not isinstance(raw, int)
                ):
                    raise CoachingPayloadError(
                        "grounded payload location value is invalid"
                    )
            for field in ("syllableUnit", "wordUnit", "learnerAnchor"):
                raw = raw_location[field]
                if raw is not None and not isinstance(raw, str):
                    raise CoachingPayloadError(
                        "grounded payload location value is invalid"
                    )
            location = GroundedLocation(
                unit_type=raw_location["unitType"],
                unit_index=raw_location["unitIndex"],
                target_unit=raw_location["targetUnit"],
                start_ms=raw_location["startMs"],
                end_ms=raw_location["endMs"],
                syllable_index=raw_location["syllableIndex"],
                syllable_unit=raw_location["syllableUnit"],
                word_unit=raw_location["wordUnit"],
                learner_anchor=raw_location["learnerAnchor"],
            )

        raw_claims = guidance["claims"]
        if not isinstance(raw_claims, list) or not raw_claims:
            raise CoachingPayloadError("grounded payload claims must be an array")
        claims: list[ApprovedAdviceClaim] = []
        for raw_claim in raw_claims:
            claim_fields = {"claimId", "text"}
            if not legacy:
                claim_fields.add("requiredFragments")
            if not isinstance(raw_claim, Mapping) or set(raw_claim) != claim_fields:
                raise CoachingPayloadError("grounded payload claim schema differs")
            if not isinstance(raw_claim["claimId"], str) or not isinstance(
                raw_claim["text"], str
            ):
                raise CoachingPayloadError("grounded payload claim value is invalid")
            fragments = [] if legacy else raw_claim["requiredFragments"]
            if not isinstance(fragments, list) or not all(
                isinstance(fragment, str) for fragment in fragments
            ):
                raise CoachingPayloadError("required fragments must be a string array")
            claims.append(
                ApprovedAdviceClaim(
                    claim_id=raw_claim["claimId"],
                    text=raw_claim["text"],
                    required_fragments=tuple(fragments),
                )
            )

        def optional_float(field: str) -> float | None:
            raw = technical[field]
            if raw is None:
                return None
            if isinstance(raw, bool) or not isinstance(raw, int | float):
                raise CoachingPayloadError(
                    f"grounded payload {field} must be numeric or null"
                )
            return float(raw)

        string_fields = {
            "schemaVersion": value["schemaVersion"],
            "targetText": value["targetText"],
            "issueId": issue["issueId"],
            "type": issue["type"],
            "observedCandidateState": issue["observedCandidateState"],
            "evidenceState": issue["evidenceState"],
            "adviceId": guidance["adviceId"],
            "claimScope": value["claimScope"],
        }
        if any(not isinstance(item, str) for item in string_fields.values()):
            raise CoachingPayloadError("grounded payload string field has a wrong type")
        observed = issue["observedCandidate"]
        if observed is not None and not isinstance(observed, str):
            raise CoachingPayloadError("grounded payload candidate has a wrong type")
        score_semantics = technical["scoreSemantics"]
        if score_semantics is not None and not isinstance(score_semantics, str):
            raise CoachingPayloadError(
                "grounded payload score semantics has a wrong type"
            )
        return cls(
            schema_version=GROUNDED_INPUT_SCHEMA_VERSION,
            target_text=string_fields["targetText"],
            issue_id=string_fields["issueId"],
            issue_type=string_fields["type"],
            location=location,
            observed_candidate=observed,
            observed_candidate_state=string_fields["observedCandidateState"],
            evidence_state=string_fields["evidenceState"],
            severity=optional_float("severity"),
            confidence=optional_float("confidence"),
            detector_score=optional_float("detectorScore"),
            operating_threshold=optional_float("operatingThreshold"),
            score_semantics=score_semantics,
            advice_id=string_fields["adviceId"],
            approved_claims=tuple(claims),
            claim_scope=string_fields["claimScope"],
            feedback_evidence=value.get("feedbackEvidence"),
        )


@dataclass(frozen=True, slots=True)
class GroundedAdvicePlan:
    """Constrained claim selection with optional validated learner prose."""

    schema_version: str
    status: GroundedPlanStatus
    issue_id: str
    advice_id: str
    selected_claim_ids: tuple[str, ...]
    style: GroundedAdviceStyle
    candidate_disclosure: CandidateDisclosure
    learner_message: str | None = None


def _assert_privacy_safe(value: str, *, field: str) -> None:
    if _SHA256.search(value):
        raise CoachingPayloadError(f"{field} contains a digest")
    if _PATH_LIKE.search(value):
        raise CoachingPayloadError(f"{field} contains a filesystem path")
    if _SPEAKER_LIKE.search(value):
        raise CoachingPayloadError(f"{field} contains a speaker identifier")


def _observed_candidate(issue: DiagnosticIssueDraft) -> str | None:
    candidates = {
        item.removeprefix("heard_syllable=").strip()
        for item in issue.evidence
        if item.startswith("heard_syllable=")
    }
    safe = {item for item in candidates if _SAFE_CANDIDATE.fullmatch(item)}
    return next(iter(safe)) if len(safe) == 1 else None


def build_grounded_coaching_payload(
    *,
    prompt_spec: PromptSpec,
    issue: DiagnosticIssueDraft,
    advice_id: str,
    advice_message: str,
    issue_id: str = "issue-1",
    claim_scope: str = "engineering_provisional",
) -> GroundedCoachingPayload:
    """Build a grounded payload only from a gate-approved issue and catalog entry."""

    slot = (
        prompt_spec.syllable(issue.syllable_index)
        if issue.syllable_index is not None
        else None
    )
    location = None
    if slot is not None and issue.start_ms is not None and issue.end_ms is not None:
        location = GroundedLocation(
            unit_type="syllable",
            unit_index=slot.index,
            target_unit=slot.syllable,
            start_ms=issue.start_ms,
            end_ms=issue.end_ms,
        )
    candidate = _observed_candidate(issue)
    claim = ApprovedAdviceClaim(
        claim_id=f"{advice_id}:SOURCE_MESSAGE",
        text=advice_message.strip(),
    )
    return GroundedCoachingPayload(
        schema_version=GROUNDED_INPUT_SCHEMA_VERSION,
        target_text=prompt_spec.normalized_text,
        issue_id=issue_id,
        issue_type=issue.issue_type,
        location=location,
        observed_candidate=candidate,
        observed_candidate_state=(
            "model_candidate_not_ground_truth" if candidate else "not_available"
        ),
        evidence_state="gate_passed",
        severity=issue.severity,
        confidence=issue.confidence,
        detector_score=None,
        operating_threshold=None,
        score_semantics=None,
        advice_id=advice_id,
        approved_claims=(claim,),
        claim_scope=claim_scope,
    )


def parse_grounded_advice_plan(raw: str) -> GroundedAdvicePlan:
    """Parse one JSON object, optionally inside one complete JSON code fence."""

    try:
        content = raw.strip()
        fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", content, re.DOTALL)
        document = json.loads(fenced.group(1) if fenced else content)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CoachingPayloadError("grounded generator response is not JSON") from error
    if not isinstance(document, dict):
        raise CoachingPayloadError("grounded generator response must be an object")
    required = {
        "schemaVersion",
        "status",
        "issueId",
        "adviceId",
        "selectedClaimIds",
        "style",
        "candidateDisclosure",
        "learnerMessage",
    }
    if set(document) != required:
        raise CoachingPayloadError("grounded generator response schema differs")
    if not all(
        isinstance(document[key], str)
        for key in required - {"selectedClaimIds", "learnerMessage"}
    ):
        raise CoachingPayloadError("grounded generator string field has a wrong type")
    selected = document["selectedClaimIds"]
    if not isinstance(selected, list) or not all(
        isinstance(item, str) and item for item in selected
    ):
        raise CoachingPayloadError("selectedClaimIds must be a string array")
    learner_message = document["learnerMessage"]
    if learner_message is not None and not isinstance(learner_message, str):
        raise CoachingPayloadError("learnerMessage must be a string or null")
    try:
        return GroundedAdvicePlan(
            schema_version=document["schemaVersion"],
            status=GroundedPlanStatus(document["status"]),
            issue_id=document["issueId"],
            advice_id=document["adviceId"],
            selected_claim_ids=tuple(selected),
            style=GroundedAdviceStyle(document["style"]),
            candidate_disclosure=CandidateDisclosure(document["candidateDisclosure"]),
            learner_message=learner_message,
        )
    except ValueError as error:
        raise CoachingPayloadError(
            "grounded generator enum value is invalid"
        ) from error


def validate_grounded_advice_plan(
    *, payload: GroundedCoachingPayload, plan: GroundedAdvicePlan
) -> GroundedAdvicePlan:
    """Bind a model plan to the exact issue, advice, and claims it received."""

    if plan.schema_version != GROUNDED_OUTPUT_SCHEMA_VERSION:
        raise CoachingPayloadError("grounded output schema version differs")
    if plan.issue_id != payload.issue_id or plan.advice_id != payload.advice_id:
        raise CoachingPayloadError("grounded output changes issue or advice identity")
    if len(plan.selected_claim_ids) != len(set(plan.selected_claim_ids)):
        raise CoachingPayloadError("selected claim ids must be unique")
    allowed = {item.claim_id for item in payload.approved_claims}
    if plan.status is GroundedPlanStatus.ABSTAINED:
        if plan.selected_claim_ids:
            raise CoachingPayloadError("an abstained plan must not select claims")
        if plan.candidate_disclosure is not CandidateDisclosure.OMIT:
            raise CoachingPayloadError(
                "an abstained plan must omit candidate disclosure"
            )
        if plan.learner_message is not None:
            raise CoachingPayloadError("an abstained plan must omit learner message")
        return plan
    if not set(plan.selected_claim_ids).issubset(allowed):
        raise CoachingPayloadError("grounded output refers to an unknown source claim")
    if (
        plan.candidate_disclosure is CandidateDisclosure.MODEL_CANDIDATE
        and payload.observed_candidate is None
        and not any(row.get("observed_candidate") is not None for row in
                    (payload.feedback_evidence or {}).get("audio", {}).get("phones", []))
    ):
        raise CoachingPayloadError("grounded output invents an observed candidate")
    if plan.learner_message is not None:
        message = plan.learner_message.strip()
        if not 1 <= len(message) <= MAXIMUM_GROUNDED_MESSAGE_CHARACTERS:
            raise CoachingPayloadError("learner message exceeds the backend contract")
        _assert_privacy_safe(message, field="learner message")

    return plan


def render_grounded_advice_message(
    *, payload: GroundedCoachingPayload, plan: GroundedAdvicePlan
) -> str:
    """Render learner prose from immutable inputs after validating the plan."""

    validated = validate_grounded_advice_plan(payload=payload, plan=plan)
    if validated.status is GroundedPlanStatus.ABSTAINED:
        raise CoachingPayloadError("an abstained plan has no learner message")
    if validated.learner_message is not None:
        return validated.learner_message.strip()
    claims = {item.claim_id: item.text for item in payload.approved_claims}
    parts: list[str] = []
    target = payload.location.target_unit if payload.location is not None else None
    if (
        validated.candidate_disclosure is CandidateDisclosure.MODEL_CANDIDATE
        and payload.observed_candidate is not None
        and target is not None
    ):
        parts.append(
            f"분석에서는 ‘{target}’이 ‘{payload.observed_candidate}’ 쪽 후보로 "
            "나타났습니다. 이 후보는 실제 발음을 확정한 전사가 아닙니다."
        )
    if validated.style is GroundedAdviceStyle.FOCUS and target is not None:
        parts.append(f"다음 시도에서는 표시된 ‘{target}’에 집중해 보세요.")
    parts.extend(claims[claim_id] for claim_id in validated.selected_claim_ids)
    message = " ".join(parts).strip()
    if not message or len(message) > MAXIMUM_GROUNDED_MESSAGE_CHARACTERS:
        raise CoachingPayloadError("rendered grounded message has an invalid length")
    _assert_privacy_safe(message, field="rendered grounded message")
    return message


__all__ = [
    "GROUNDED_INPUT_SCHEMA_VERSION",
    "GROUNDED_OUTPUT_SCHEMA_VERSION",
    "ApprovedAdviceClaim",
    "CandidateDisclosure",
    "GroundedAdvicePlan",
    "GroundedAdviceStyle",
    "GroundedCoachingPayload",
    "GroundedLocation",
    "GroundedPlanStatus",
    "build_grounded_coaching_payload",
    "parse_grounded_advice_plan",
    "render_grounded_advice_message",
    "validate_grounded_advice_plan",
]
