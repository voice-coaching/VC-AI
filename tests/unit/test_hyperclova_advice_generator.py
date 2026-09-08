"""Local HyperCLOVA transport remains loopback-only and schema-bound."""

from __future__ import annotations

import json

import pytest

from voice_coach.pronunciation_feedback.adapters.advice_generator_hyperclova import (
    SYSTEM_PROMPT,
    LocalHyperClovaAdviceGenerator,
    _completion_content,
)
from voice_coach.pronunciation_feedback.coaching_payload import CoachingPayloadError
from voice_coach.pronunciation_feedback.grounded_advice import (
    GROUNDED_INPUT_SCHEMA_VERSION,
    GROUNDED_OUTPUT_SCHEMA_VERSION,
    ApprovedAdviceClaim,
    GroundedCoachingPayload,
)

ADVICE_ID = "ARTICULATION_RULE_017"
CLAIM_ID = f"{ADVICE_ID}:SOURCE_MESSAGE"


def test_system_prompt_requires_plain_language_grounded_coaching_json() -> None:
    assert "Markdown" in SYSTEM_PROMPT
    assert "첫 문자는 {이고 마지막 문자는 }" in SYSTEM_PROMPT
    assert "일반 사용자를 돕는 친절한 발음 코치" in SYSTEM_PROMPT
    assert "2~3개의 짧은 문장" in SYSTEM_PROMPT
    assert "같은 연습 행동" in SYSTEM_PROMPT
    assert "candidateDisclosure는 항상 omit" in SYSTEM_PROMPT
    assert '"원래 속도"를 "빠르게"로 바꾸지 않는다' in SYSTEM_PROMPT
    assert "learnerMessage" in SYSTEM_PROMPT
    assert "입력 claim에 없는 혀 위치" in SYSTEM_PROMPT
    assert "issue.location.learnerAnchor를 한 글자도 바꾸지 않고" in SYSTEM_PROMPT
    assert "‘깨끗이’의 두 번째 음절 ‘끗’" in SYSTEM_PROMPT
    assert '"문장에서 ‘ㅅ’"처럼 음소만 말하지 않는다' in SYSTEM_PROMPT


def _payload() -> GroundedCoachingPayload:
    return GroundedCoachingPayload(
        schema_version=GROUNDED_INPUT_SCHEMA_VERSION,
        target_text="쌀과 콩을",
        issue_id="issue-1",
        issue_type="final_consonant_deletion",
        location=None,
        observed_candidate=None,
        observed_candidate_state="not_available",
        evidence_state="gate_passed",
        severity=0.78,
        confidence=0.87,
        detector_score=None,
        operating_threshold=None,
        score_semantics=None,
        advice_id=ADVICE_ID,
        approved_claims=(ApprovedAdviceClaim(CLAIM_ID, "받침을 유지해 보세요."),),
        claim_scope="engineering_provisional",
    )


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://127.0.0.1:8000/v1",
        "http://192.168.1.20:8000/v1",
        "http://user:secret@localhost:8000/v1",
        "http://localhost:8000/v1?token=secret",
    ),
)
def test_adapter_rejects_non_loopback_or_credential_bearing_endpoint(
    endpoint: str,
) -> None:
    with pytest.raises(CoachingPayloadError):
        LocalHyperClovaAdviceGenerator(endpoint=endpoint, revision="pinned")


def test_adapter_requires_a_pinned_model_revision() -> None:
    with pytest.raises(CoachingPayloadError, match="must be pinned"):
        LocalHyperClovaAdviceGenerator(endpoint="http://127.0.0.1:8000/v1", revision="")


def test_adapter_parses_and_validates_model_plan() -> None:
    class FixedResponseGenerator(LocalHyperClovaAdviceGenerator):
        def _complete(self, payload: GroundedCoachingPayload) -> str:
            del payload
            return json.dumps(
                {
                    "schemaVersion": GROUNDED_OUTPUT_SCHEMA_VERSION,
                    "status": "rendered",
                    "issueId": "issue-1",
                    "adviceId": ADVICE_ID,
                    "selectedClaimIds": [CLAIM_ID],
                    "style": "supportive",
                    "candidateDisclosure": "omit",
                    "learnerMessage": (
                        "이번에는 받침 소리를 또렷하게 유지하는 연습을 해보세요. "
                        "먼저 천천히 한 번 말한 뒤 원래 속도로 다시 말하고 녹음해 비교해 보세요."
                    ),
                }
            )

    generator = FixedResponseGenerator(
        endpoint="http://127.0.0.1:8000/v1", revision="pinned-commit"
    )

    plan = generator.generate(_payload())

    assert plan.selected_claim_ids == (CLAIM_ID,)


def test_adapter_rejects_a_model_plan_that_changes_issue_identity() -> None:
    class IdentityChangingGenerator(LocalHyperClovaAdviceGenerator):
        def _complete(self, payload: GroundedCoachingPayload) -> str:
            del payload
            return json.dumps(
                {
                    "schemaVersion": GROUNDED_OUTPUT_SCHEMA_VERSION,
                    "status": "rendered",
                    "issueId": "invented-issue",
                    "adviceId": ADVICE_ID,
                    "selectedClaimIds": [CLAIM_ID],
                    "style": "concise",
                    "candidateDisclosure": "omit",
                    "learnerMessage": (
                        "이번에는 받침 소리를 또렷하게 유지하는 연습을 해보세요. "
                        "천천히 말한 뒤 다시 녹음해 앞선 발음과 비교해 보세요."
                    ),
                }
            )

    generator = IdentityChangingGenerator(
        endpoint="http://localhost:8000/v1", revision="pinned-commit"
    )

    with pytest.raises(CoachingPayloadError, match="changes issue"):
        generator.generate(_payload())


def test_completion_response_must_bind_the_pinned_model_revision() -> None:
    response = {
        "model": "model-v1",
        "revision": "revision-v1",
        "choices": [{"message": {"content": "{}"}}],
    }

    assert (
        _completion_content(
            response,
            expected_model="model-v1",
            expected_revision="revision-v1",
        )
        == "{}"
    )
    with pytest.raises(CoachingPayloadError, match="revision differs"):
        _completion_content(
            response,
            expected_model="model-v1",
            expected_revision="revision-v2",
        )
