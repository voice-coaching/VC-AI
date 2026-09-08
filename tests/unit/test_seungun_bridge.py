"""Frozen phone output reaches HyperCLOVA without changing its decision."""

from __future__ import annotations

from dataclasses import replace

from voice_coach.pronunciation_feedback.grounded_advice import (
    GROUNDED_OUTPUT_SCHEMA_VERSION,
    CandidateDisclosure,
    GroundedAdvicePlan,
    GroundedAdviceStyle,
    GroundedPlanStatus,
)
from voice_coach.pronunciation_feedback.seungun_bridge import (
    PHONE_ADVICE_ID,
    PHONE_CLAIM_ID,
    SEUNGUN_SCHEMA_VERSION,
    SEUNGUN_SCORE_SEMANTICS,
    build_grounded_payload_from_seungun,
    generate_grounded_phone_feedback,
)


def _phone(
    *,
    index: int,
    expected: str,
    observed: str | None,
    score: float,
    is_error: bool,
) -> dict:
    return {
        "expected_index": index,
        "expected_phone": expected,
        "observed_candidate": observed,
        "alignment_operation": "substitution" if observed else "deletion",
        "start_ms": 120 if observed else None,
        "end_ms": 220 if observed else None,
        "error_score": score,
        "operating_threshold": 0.8134703636169434,
        "is_error": is_error,
        "score_semantics": SEUNGUN_SCORE_SEMANTICS,
        "soft_features": {},
    }


def _analysis() -> dict:
    return {
        "schema_version": SEUNGUN_SCHEMA_VERSION,
        "expected_phones": ["ㄱ", "ㅏ"],
        "phones": [
            _phone(index=0, expected="ㄱ", observed="ㅋ", score=0.91, is_error=True),
            _phone(index=1, expected="ㅏ", observed="ㅏ", score=0.1, is_error=False),
        ],
    }


class FixedGenerator:
    generator_id = "fixture-hyperclova"
    revision = "a6cdfd3464d1b767259cad23e164eaf39d3e3960"

    def generate(self, payload):  # noqa: ANN001, ANN201 - protocol double
        return GroundedAdvicePlan(
            schema_version=GROUNDED_OUTPUT_SCHEMA_VERSION,
            status=GroundedPlanStatus.RENDERED,
            issue_id=payload.issue_id,
            advice_id=payload.advice_id,
            selected_claim_ids=(PHONE_CLAIM_ID,),
            style=GroundedAdviceStyle.FOCUS,
            candidate_disclosure=CandidateDisclosure.MODEL_CANDIDATE,
        )


def test_bridge_preserves_phone_score_semantics_and_location() -> None:
    payload = build_grounded_payload_from_seungun(
        analysis=_analysis(), target_text="가"
    )

    assert payload.advice_id == PHONE_ADVICE_ID
    assert payload.detector_score == 0.91
    assert payload.confidence is None
    assert payload.score_semantics == SEUNGUN_SCORE_SEMANTICS
    assert payload.location is not None
    assert payload.location.unit_type == "phone"
    assert payload.location.target_unit == "ㄱ"
    assert payload.location.syllable_unit == "가"
    assert payload.location.word_unit == "가"
    assert payload.location.learner_anchor == "‘가’의 첫 번째 음절 ‘가’"
    assert payload.observed_candidate == "ㅋ"


def test_bridge_maps_a_repeated_phone_to_its_exact_word_and_syllable() -> None:
    expected = [
        "ㅆ",
        "ㅏ",
        "ㄹ",
        "ㅡ",
        "ㄹ",
        "ㄲ",
        "ㅐ",
        "ㄲ",
        "ㅡ",
        "ㅅ",
        "ㅣ",
        "ㅆ",
        "ㅣ",
        "ㅅ",
        "ㅓ",
        "ㅇ",
        "ㅛ",
    ]
    analysis = {
        "schema_version": SEUNGUN_SCHEMA_VERSION,
        "expected_phones": expected,
        "phones": [
            _phone(
                index=index,
                expected=phone,
                observed="ㄱ" if index == 9 else phone,
                score=0.93 if index == 9 else 0.1,
                is_error=index == 9,
            )
            for index, phone in enumerate(expected)
        ],
    }

    payload = build_grounded_payload_from_seungun(
        analysis=analysis,
        target_text="쌀을 깨끗이 씻어요",
    )

    assert payload.location is not None
    assert payload.location.target_unit == "ㅅ"
    assert payload.location.syllable_index == 3
    assert payload.location.syllable_unit == "끗"
    assert payload.location.word_unit == "깨끗이"
    assert payload.location.learner_anchor == "‘깨끗이’의 두 번째 음절 ‘끗’"
    assert "‘깨끗이’의 두 번째 음절 ‘끗’" in payload.approved_claims[0].text


def test_bridge_runs_validated_selector_and_deterministic_renderer() -> None:
    feedback = generate_grounded_phone_feedback(
        analysis=_analysis(), target_text="가", generator=FixedGenerator()
    )

    assert feedback.generator_status == "validated_grounded_plan"
    assert "‘ㄱ’" in feedback.message and "‘ㅋ’" in feedback.message
    assert "확정한 전사가 아닙니다" in feedback.message
    assert feedback.payload.approved_claims[0].text in feedback.message


def test_invalid_generator_plan_falls_back_to_exact_generic_claim() -> None:
    class InvalidGenerator(FixedGenerator):
        def generate(self, payload):  # noqa: ANN001, ANN201 - protocol double
            return replace(super().generate(payload), selected_claim_ids=("invented",))

    feedback = generate_grounded_phone_feedback(
        analysis=_analysis(), target_text="가", generator=InvalidGenerator()
    )

    assert feedback.generator_status == "catalog_fallback"
    assert feedback.fallback_reason == "CoachingPayloadError"
    assert feedback.message == feedback.payload.approved_claims[0].text
