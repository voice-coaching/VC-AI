"""Bridge frozen ``seungun`` phone evidence into grounded learner feedback."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .adapters.syllables import decompose_syllables
from .coaching_payload import CoachingPayloadError
from .ctc_alignment import (
    CtcAlignmentError,
    expected_token_owners,
    written_jamo_groups,
)
from .grounded_advice import (
    GROUNDED_INPUT_SCHEMA_VERSION,
    ApprovedAdviceClaim,
    GroundedCoachingPayload,
    GroundedLocation,
    render_grounded_advice_message,
    validate_grounded_advice_plan,
)
from .ports import GroundedAdviceGeneratorPort

SEUNGUN_SCHEMA_VERSION = "korean_phone_ctc.pronunciation_analysis.v1"
SEUNGUN_SCORE_SEMANTICS = "detector_ranking_score_not_calibrated_correctness_confidence"
PHONE_ADVICE_ID = "PHONE_RETRY_RULE_001"
PHONE_CLAIM_ID = f"{PHONE_ADVICE_ID}:SOURCE_MESSAGE"
_HANGUL_WORD = re.compile(r"[가-힣]+")


@dataclass(frozen=True, slots=True)
class GroundedPhoneFeedback:
    """Safe presentation result plus whether a local model was actually used."""

    message: str
    payload: GroundedCoachingPayload
    generator_status: str
    generator_id: str | None
    generator_revision: str | None
    fallback_reason: str | None


def _phone_claims(
    *,
    phone: Mapping[str, Any],
    expected_phone: str,
    learner_anchor: str,
    word_unit: str,
    start_ms: int | None,
) -> tuple[ApprovedAdviceClaim, ...]:
    """Describe admitted evidence and a word-context practice sequence.

    A phone token is an analysis label, not an instruction to pronounce a
    written coda in isolation.  CTC substitutions/deletions are recognition
    outcomes, not independently verified articulatory error diagnoses.  In
    particular, recovered deletion times are host estimates, not acoustic
    observations, and must not be presented as measured phone durations.
    """

    operation = phone.get("alignment_operation")
    focus = (
        f"음성 분석에서 {learner_anchor}에 연결된 ‘{expected_phone}’ 지점을 "
        "다시 확인할 부분으로 골랐습니다."
    )
    focus_fragments = [learner_anchor]
    if start_ms is not None and operation in {"correct", "substitution"}:
        time_anchor = f"약 {start_ms / 1000:.2f}초"
        focus += f" 자동 정렬 위치는 녹음 {time_anchor} 부근입니다."
        focus_fragments.append(time_anchor)

    if operation == "deletion":
        observation = (
            "음성 인식이 이 위치의 소리를 대응시키지 못했지만, "
            "실제 생략 여부는 확정되지 않았습니다."
        )
        observation_fragment = "실제 생략 여부는 확정되지 않았습니다"
    elif operation == "substitution":
        observation = (
            "음성 인식에서 다른 소리 후보가 나왔지만, "
            "실제 대치 여부나 오류 원인은 확정되지 않았습니다."
        )
        observation_fragment = "실제 대치 여부나 오류 원인은 확정되지 않았습니다"
    else:
        observation = (
            "이 분석은 재확인할 위치를 고른 것이며, "
            "혀 위치나 구체적인 오류 원인은 확인되지 않았습니다."
        )
        observation_fragment = "구체적인 오류 원인은 확인되지 않았습니다"

    word_practice = (
        f"‘{word_unit}’ 전체를 소리를 끊지 않고 천천히 한 번 읽은 뒤, "
        "같은 단어를 원래 속도로 한 번 더 읽어 보세요."
    )
    replay = (
        "이어서 문장 전체를 다시 녹음하고, 처음 녹음과 번갈아 들으며 "
        f"‘{word_unit}’의 앞뒤 소리가 이어지는 부분을 비교해 보세요."
    )
    return (
        ApprovedAdviceClaim(
            PHONE_CLAIM_ID,
            focus,
            required_fragments=tuple(focus_fragments),
        ),
        ApprovedAdviceClaim(
            f"{PHONE_ADVICE_ID}:WORD_PRACTICE",
            word_practice,
            required_fragments=(
                f"‘{word_unit}’ 전체",
                "소리를 끊지 않고",
                "원래 속도",
            ),
        ),
        ApprovedAdviceClaim(
            f"{PHONE_ADVICE_ID}:SENTENCE_REPLAY",
            replay,
            required_fragments=("문장 전체를 다시 녹음", "처음 녹음과 번갈아"),
        ),
        ApprovedAdviceClaim(
            f"{PHONE_ADVICE_ID}:EVIDENCE_LIMIT",
            observation,
            required_fragments=(observation_fragment,),
        ),
    )


def _ordinal(position: int) -> str:
    names = {1: "첫 번째", 2: "두 번째", 3: "세 번째", 4: "네 번째"}
    return names.get(position, f"{position}번째")


def _phone_text_context(
    *,
    analysis: Mapping[str, Any],
    target_text: str,
    expected_index: int,
    expected_phone: str,
) -> tuple[int, str, str, str]:
    """Map a detector phone index to one exact host-authored text anchor."""

    raw_expected = analysis.get("expected_phones")
    if not isinstance(raw_expected, list | tuple) or not raw_expected:
        raise CoachingPayloadError("seungun expected phone sequence is missing")
    if not all(isinstance(token, str) and token for token in raw_expected):
        raise CoachingPayloadError("seungun expected phone sequence is invalid")
    expected_tokens = tuple(raw_expected)
    if (
        expected_index >= len(expected_tokens)
        or expected_tokens[expected_index] != expected_phone
    ):
        raise CoachingPayloadError("seungun selected phone differs from its sequence")

    slots = decompose_syllables(target_text)
    try:
        owners = expected_token_owners(
            written_groups=written_jamo_groups(
                tuple((slot.onset, slot.nucleus, slot.coda) for slot in slots)
            ),
            expected_tokens=expected_tokens,
        )
    except CtcAlignmentError as error:
        raise CoachingPayloadError(
            "seungun phone-to-syllable mapping is unavailable"
        ) from error
    owner = owners[expected_index]
    if owner is None or owner >= len(slots):
        raise CoachingPayloadError("seungun selected phone has no syllable owner")
    slot = slots[owner]
    for match in _HANGUL_WORD.finditer(target_text):
        if match.start() <= slot.char_start < match.end():
            word = match.group(0)
            position = slot.char_start - match.start() + 1
            learner_anchor = f"‘{word}’의 {_ordinal(position)} 음절 ‘{slot.syllable}’"
            return slot.index, slot.syllable, word, learner_anchor
    raise CoachingPayloadError("seungun selected phone has no learner word anchor")


def _selected_phone(document: Mapping[str, Any]) -> Mapping[str, Any]:
    if document.get("schema_version") != SEUNGUN_SCHEMA_VERSION:
        raise CoachingPayloadError("seungun analysis schema differs")
    phones = document.get("phones")
    if not isinstance(phones, list | tuple):
        raise CoachingPayloadError("seungun analysis phones must be an array")
    candidates: list[Mapping[str, Any]] = []
    for phone in phones:
        if not isinstance(phone, dict) or phone.get("is_error") is not True:
            continue
        score = phone.get("error_score")
        threshold = phone.get("operating_threshold")
        if not isinstance(score, int | float) or not isinstance(threshold, int | float):
            continue
        if not 0.0 <= float(threshold) <= float(score) <= 1.0:
            continue
        if phone.get("score_semantics") != SEUNGUN_SCORE_SEMANTICS:
            continue
        candidates.append(phone)
    if not candidates:
        raise CoachingPayloadError("seungun analysis has no threshold-passed phone")
    return min(
        candidates,
        key=lambda phone: (
            -float(phone["error_score"]),
            int(phone.get("expected_index", 2**31 - 1)),
        ),
    )


def build_grounded_payload_from_seungun(
    *,
    analysis: Mapping[str, Any],
    target_text: str,
) -> GroundedCoachingPayload:
    """Bind the selected detector issue to evidence and contextual practice."""

    phone = _selected_phone(analysis)
    expected_phone = phone.get("expected_phone")
    expected_index = phone.get("expected_index")
    if not isinstance(expected_phone, str) or not expected_phone:
        raise CoachingPayloadError("seungun expected phone is missing")
    if not isinstance(expected_index, int) or expected_index < 0:
        raise CoachingPayloadError("seungun expected phone index is invalid")
    observed = phone.get("observed_candidate")
    if not isinstance(observed, str) or observed == expected_phone:
        observed = None
    start_ms = phone.get("start_ms")
    end_ms = phone.get("end_ms")
    if not (isinstance(start_ms, int) and isinstance(end_ms, int)):
        start_ms = None
        end_ms = None
    syllable_index, syllable_unit, word_unit, learner_anchor = _phone_text_context(
        analysis=analysis,
        target_text=target_text,
        expected_index=expected_index,
        expected_phone=expected_phone,
    )
    location = GroundedLocation(
        unit_type="phone",
        unit_index=expected_index,
        target_unit=expected_phone,
        start_ms=start_ms,
        end_ms=end_ms,
        syllable_index=syllable_index,
        syllable_unit=syllable_unit,
        word_unit=word_unit,
        learner_anchor=learner_anchor,
    )
    claims = _phone_claims(
        phone=phone,
        expected_phone=expected_phone,
        learner_anchor=learner_anchor,
        word_unit=word_unit,
        start_ms=start_ms,
    )
    return GroundedCoachingPayload(
        schema_version=GROUNDED_INPUT_SCHEMA_VERSION,
        target_text=target_text,
        issue_id=f"phone-{expected_index}",
        issue_type="phone_pronunciation_error",
        location=location,
        observed_candidate=observed,
        observed_candidate_state=(
            "model_candidate_not_ground_truth" if observed else "not_available"
        ),
        evidence_state="frozen_detector_threshold_passed",
        severity=None,
        confidence=None,
        detector_score=float(phone["error_score"]),
        operating_threshold=float(phone["operating_threshold"]),
        score_semantics=SEUNGUN_SCORE_SEMANTICS,
        advice_id=PHONE_ADVICE_ID,
        approved_claims=claims,
        claim_scope="engineering_provisional",
    )


def generate_grounded_phone_feedback(
    *,
    analysis: Mapping[str, Any],
    target_text: str,
    generator: GroundedAdviceGeneratorPort | None,
) -> GroundedPhoneFeedback:
    """Generate through the constrained selector or preserve every claim."""

    payload = build_grounded_payload_from_seungun(
        analysis=analysis, target_text=target_text
    )
    source_message = " ".join(claim.text for claim in payload.approved_claims)
    if generator is None:
        return GroundedPhoneFeedback(
            message=source_message,
            payload=payload,
            generator_status="catalog_fallback",
            generator_id=None,
            generator_revision=None,
            fallback_reason="generator_not_configured",
        )
    try:
        plan = generator.generate(payload)
        validate_grounded_advice_plan(payload=payload, plan=plan)
        message = render_grounded_advice_message(payload=payload, plan=plan)
    except Exception as error:  # noqa: BLE001 - exact claim fallback is mandatory
        return GroundedPhoneFeedback(
            message=source_message,
            payload=payload,
            generator_status="catalog_fallback",
            generator_id=generator.generator_id,
            generator_revision=generator.revision,
            fallback_reason=type(error).__name__,
        )
    return GroundedPhoneFeedback(
        message=message,
        payload=payload,
        generator_status="validated_grounded_plan",
        generator_id=generator.generator_id,
        generator_revision=generator.revision,
        fallback_reason=None,
    )


__all__ = [
    "PHONE_ADVICE_ID",
    "PHONE_CLAIM_ID",
    "SEUNGUN_SCHEMA_VERSION",
    "SEUNGUN_SCORE_SEMANTICS",
    "GroundedPhoneFeedback",
    "build_grounded_payload_from_seungun",
    "generate_grounded_phone_feedback",
]
