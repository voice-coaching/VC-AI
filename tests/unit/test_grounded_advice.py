"""The coaching LLM may select approved claims but cannot author new facts."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

from voice_coach.pronunciation_feedback.coaching_payload import CoachingPayloadError
from voice_coach.pronunciation_feedback.domain import (
    DiagnosticIssueDraft,
    MetricState,
    PromptSpec,
    TargetLanguage,
)
from voice_coach.pronunciation_feedback.grounded_advice import (
    GROUNDED_OUTPUT_SCHEMA_VERSION,
    CandidateDisclosure,
    GroundedAdvicePlan,
    GroundedAdviceStyle,
    GroundedLocation,
    GroundedPlanStatus,
    build_grounded_coaching_payload,
    parse_grounded_advice_plan,
    render_grounded_advice_message,
    validate_grounded_advice_plan,
)
from voice_coach.pronunciation_feedback.adapters.syllables import (
    decompose_syllables,
    syllable_map_sha256,
)

ADVICE_ID = "ARTICULATION_RULE_017"
CLAIM_TEXT = "받침 위치에서 공기를 더 내보내지 말고 혀와 입의 접촉을 유지해 보세요."
CLAIM_ID = f"{ADVICE_ID}:SOURCE_MESSAGE"


def _prompt_spec(text: str = "쌀과 콩을") -> PromptSpec:
    slots = decompose_syllables(text)
    return PromptSpec(
        prompt_id="R00-01",
        prompt_version="fixture-v1",
        target_language=TargetLanguage.KOREAN,
        normalized_text=text,
        normalized_text_sha256="0" * 64,
        syllables=slots,
        syllable_map_sha256=syllable_map_sha256(slots),
        expected_phones=None,
        expected_phone_state=MetricState.MISSING,
    )


def _issue(
    evidence: tuple[str, ...] = ("gop_low", "heard_syllable=사"),
) -> DiagnosticIssueDraft:
    return DiagnosticIssueDraft(
        issue_type="final_consonant_deletion",
        syllable_index=0,
        start_ms=120,
        end_ms=260,
        severity=0.78,
        confidence=0.87,
        evidence=evidence,
    )


def _payload(**overrides: object):  # noqa: ANN202 - compact fixture builder
    options: dict[str, object] = {
        "prompt_spec": _prompt_spec(),
        "issue": _issue(),
        "advice_id": ADVICE_ID,
        "advice_message": CLAIM_TEXT,
    }
    options.update(overrides)
    return build_grounded_coaching_payload(**options)  # type: ignore[arg-type]


def _plan(**overrides: object) -> GroundedAdvicePlan:
    options: dict[str, object] = {
        "schema_version": GROUNDED_OUTPUT_SCHEMA_VERSION,
        "status": GroundedPlanStatus.RENDERED,
        "issue_id": "issue-1",
        "advice_id": ADVICE_ID,
        "selected_claim_ids": (CLAIM_ID,),
        "style": GroundedAdviceStyle.FOCUS,
        "candidate_disclosure": CandidateDisclosure.MODEL_CANDIDATE,
    }
    options.update(overrides)
    return GroundedAdvicePlan(**options)  # type: ignore[arg-type]


def test_grounded_payload_exposes_only_approved_claim_and_bounded_candidate() -> None:
    document = _payload().to_json_object()

    assert document["task"] == "select_approved_pronunciation_feedback_plan"
    assert document["issue"]["observedCandidate"] == "사"
    assert document["issue"]["observedCandidateState"] == (
        "model_candidate_not_ground_truth"
    )
    evidence = document["issue"]["technicalEvidence"]
    assert evidence["severity"] == 0.78
    assert evidence["confidence"] == 0.87
    assert document["approvedGuidance"]["claims"] == [
        {"claimId": CLAIM_ID, "text": CLAIM_TEXT}
    ]
    serialized = json.dumps(document, ensure_ascii=False).lower()
    for forbidden in ("audio", "path", "sha256", "speaker"):
        assert forbidden not in serialized


def test_grounded_payload_strict_round_trip() -> None:
    payload = _payload()

    assert type(payload).from_json_object(payload.to_json_object()) == payload

    changed = payload.to_json_object()
    changed["constraints"] = {**changed["constraints"], "mayAddPhone": True}
    with pytest.raises(CoachingPayloadError, match="constraints differ"):
        type(payload).from_json_object(changed)

    changed = payload.to_json_object()
    changed["issue"]["location"]["unitIndex"] = "0"
    with pytest.raises(CoachingPayloadError, match="location value"):
        type(payload).from_json_object(changed)


def test_candidate_is_omitted_without_one_unambiguous_safe_evidence_value() -> None:
    ambiguous = _payload(
        issue=_issue(("heard_syllable=사", "heard_syllable=쌀", "gop_low"))
    )
    unsafe = _payload(issue=_issue(("heard_syllable=/tmp/take.wav", "gop_low")))

    assert ambiguous.observed_candidate is None
    assert unsafe.observed_candidate is None


def test_response_parser_rejects_fences_prose_and_extra_fields() -> None:
    valid = {
        "schemaVersion": GROUNDED_OUTPUT_SCHEMA_VERSION,
        "status": "rendered",
        "issueId": "issue-1",
        "adviceId": ADVICE_ID,
        "selectedClaimIds": [CLAIM_ID],
        "style": "direct",
        "candidateDisclosure": "omit",
        "learnerMessage": (
            "이번에는 ‘쌀’의 받침을 끝까지 유지해 보세요. "
            "천천히 한 번 말한 뒤 원래 속도로 다시 녹음해 비교해 보세요."
        ),
    }

    assert parse_grounded_advice_plan(json.dumps(valid)).advice_id == ADVICE_ID
    with pytest.raises(CoachingPayloadError):
        parse_grounded_advice_plan(f"```json\n{json.dumps(valid)}\n```")
    with pytest.raises(CoachingPayloadError, match="schema differs"):
        parse_grounded_advice_plan(json.dumps({**valid, "explanation": "new fact"}))


def test_plan_cannot_select_an_unknown_claim_or_invent_a_candidate() -> None:
    payload = _payload(issue=_issue(("gop_low",)))

    with pytest.raises(CoachingPayloadError, match="every approved claim"):
        validate_grounded_advice_plan(
            payload=payload,
            plan=_plan(
                selected_claim_ids=("MADE_UP:CLAIM",),
                candidate_disclosure=CandidateDisclosure.OMIT,
            ),
        )
    with pytest.raises(CoachingPayloadError, match="invents an observed candidate"):
        validate_grounded_advice_plan(payload=payload, plan=_plan())


def test_renderer_uses_exact_claim_and_labels_candidate_as_non_ground_truth() -> None:
    message = render_grounded_advice_message(payload=_payload(), plan=_plan())

    assert CLAIM_TEXT in message
    assert "‘쌀’" in message and "‘사’" in message
    assert "확정한 전사가 아닙니다" in message
    assert "집중" in message


def test_renderer_uses_validated_plain_language_learner_message() -> None:
    learner_message = (
        "이번에는 ‘쌀’의 받침을 끝까지 유지하는 데 집중해 보세요. "
        "분석에서 ‘사’처럼 나타날 가능성이 있었지만 확정된 결과는 아니에요. "
        "천천히 한 번 말한 뒤 원래 속도로 다시 녹음해 비교해 보세요."
    )

    message = render_grounded_advice_message(
        payload=_payload(),
        plan=_plan(
            style=GroundedAdviceStyle.SUPPORTIVE,
            learner_message=learner_message,
        ),
    )

    assert message == learner_message


def test_learner_message_rejects_internal_jargon_and_missing_target() -> None:
    with pytest.raises(CoachingPayloadError, match="internal terminology"):
        validate_grounded_advice_plan(
            payload=_payload(),
            plan=_plan(
                learner_message=(
                    "‘쌀’ 발음의 detector score가 기준보다 낮습니다. "
                    "천천히 다시 말한 뒤 녹음해 비교해 보세요."
                )
            ),
        )


def test_learner_message_requires_disclosed_candidate_uncertainty() -> None:
    with pytest.raises(CoachingPayloadError, match="candidate uncertainty"):
        validate_grounded_advice_plan(
            payload=_payload(),
            plan=_plan(
                learner_message=(
                    "이번에는 ‘쌀’의 받침을 끝까지 유지해 보세요. "
                    "천천히 한 번 말한 뒤 원래 속도로 다시 녹음해 비교해 보세요."
                )
            ),
        )


def test_learner_message_may_not_turn_original_speed_into_fast_speech() -> None:
    payload = _payload(advice_message="천천히 말한 뒤 원래 속도로 다시 말해 보세요.")
    with pytest.raises(CoachingPayloadError, match="faster speech"):
        validate_grounded_advice_plan(
            payload=payload,
            plan=_plan(
                learner_message=(
                    "이번에는 ‘쌀’의 받침을 천천히 확인해 보세요. "
                    "마지막에는 빠르게 말하고 다시 녹음해 비교해 보세요."
                )
            ),
        )
    with pytest.raises(CoachingPayloadError, match="omits the target unit"):
        validate_grounded_advice_plan(
            payload=_payload(),
            plan=_plan(
                learner_message=(
                    "이번 발음은 조금 더 또렷하게 연습하면 좋아요. "
                    "천천히 한 번 말한 뒤 원래 속도로 다시 녹음해 비교해 보세요."
                )
            ),
        )


def test_phone_learner_message_must_name_the_exact_word_and_syllable() -> None:
    payload = replace(
        _payload(),
        issue_type="phone_pronunciation_error",
        location=GroundedLocation(
            unit_type="phone",
            unit_index=9,
            target_unit="ㅅ",
            start_ms=2652,
            end_ms=2673,
            syllable_index=3,
            syllable_unit="끗",
            word_unit="깨끗이",
            learner_anchor="‘깨끗이’의 두 번째 음절 ‘끗’",
        ),
    )

    with pytest.raises(CoachingPayloadError, match="exact syllable anchor"):
        validate_grounded_advice_plan(
            payload=payload,
            plan=_plan(
                candidate_disclosure=CandidateDisclosure.OMIT,
                learner_message=(
                    "‘쌀을 깨끗이 씻어요’에서 ‘ㅅ’ 소리를 연습해 보세요. "
                    "천천히 한 번 말한 뒤 원래 속도로 다시 녹음해 보세요."
                ),
            ),
        )


def test_abstention_has_no_message_and_fails_to_catalog_boundary() -> None:
    abstained = replace(
        _plan(),
        status=GroundedPlanStatus.ABSTAINED,
        selected_claim_ids=(),
        candidate_disclosure=CandidateDisclosure.OMIT,
    )

    validate_grounded_advice_plan(payload=_payload(), plan=abstained)
    with pytest.raises(CoachingPayloadError, match="no learner message"):
        render_grounded_advice_message(payload=_payload(), plan=abstained)


def test_privacy_boundary_rejects_path_digest_and_speaker_identifier() -> None:
    for leaked in ("/restricted/take.wav", "a" * 64, "speaker_abcd"):
        with pytest.raises(CoachingPayloadError):
            _payload(advice_message=f"확인 {leaked}")
