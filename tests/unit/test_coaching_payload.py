"""A coaching model must not be able to score speech, move a span, or escape the allowlist.

These are the tests that make a local LLM safe to put in front of a learner. The
prompt asks it to follow rules; this file is what actually enforces them, because
an instruction-tuned model can and will ignore instructions.
"""

from __future__ import annotations

import json

import pytest

from voice_coach.pronunciation_feedback.adapters.advice_generator_qwen import (
    parse_generated_response,
)
from voice_coach.pronunciation_feedback.coaching_payload import (
    MAXIMUM_MESSAGE_CHARACTERS,
    MAXIMUM_PAYLOAD_ISSUES,
    PAYLOAD_SCHEMA_VERSION,
    CoachingPayload,
    CoachingPayloadError,
    GeneratedAdvice,
    PayloadIssue,
    PayloadLocation,
    PayloadProsody,
    UnavailableAdviceGenerator,
    assert_payload_is_privacy_safe,
    build_coaching_payload,
    validate_generated_advice,
)
from voice_coach.pronunciation_feedback.domain import (
    DiagnosticIssueDraft,
    MetricState,
    PromptSpec,
    SyllableSlot,
    TargetLanguage,
)

ADVICE_ID = "ARTICULATION_RULE_017"


def _prompt_spec(text: str = "쌀과 콩을") -> PromptSpec:
    syllables = tuple(
        SyllableSlot(
            index=index,
            char_start=index,
            char_end=index + 1,
            syllable=character,
            onset=character,
            nucleus=character,
            coda=None,
        )
        for index, character in enumerate(text)
        if not character.isspace()
    )
    return PromptSpec(
        prompt_id="R00-01",
        prompt_version="v1",
        target_language=TargetLanguage.KOREAN,
        normalized_text=text,
        normalized_text_sha256="0" * 64,
        syllables=syllables,
        syllable_map_sha256="1" * 64,
        expected_phones=None,
        expected_phone_state=MetricState.MISSING,
    )


def _draft(
    *,
    issue_type: str = "final_consonant_deletion",
    syllable_index: int | None = 0,
    start_ms: int | None = 120,
    end_ms: int | None = 260,
    evidence: tuple[str, ...] = ("gop_low", "duration_short"),
) -> DiagnosticIssueDraft:
    return DiagnosticIssueDraft(
        issue_type=issue_type,
        syllable_index=syllable_index,
        start_ms=start_ms,
        end_ms=end_ms,
        severity=0.78,
        confidence=0.87,
        evidence=evidence,
    )


def _payload(**overrides: object) -> CoachingPayload:
    options: dict[str, object] = {
        "prompt_spec": _prompt_spec(),
        "coached_issues": [_draft()],
        "allowed_advice_ids": [ADVICE_ID],
    }
    options.update(overrides)
    return build_coaching_payload(**options)  # type: ignore[arg-type]


# -- payload shape ------------------------------------------------------------


def test_payload_matches_the_documented_field_naming() -> None:
    document = _payload().to_json_object()

    assert document["schemaVersion"] == PAYLOAD_SCHEMA_VERSION
    assert document["targetText"] == "쌀과 콩을"
    assert document["learnerLevel"] == "unknown"
    assert document["allowedAdviceIds"] == [ADVICE_ID]
    issue = document["topIssues"][0]
    assert issue["type"] == "final_consonant_deletion"
    assert issue["location"] == {"startMs": 120, "endMs": 260, "syllableIndex": 0}
    assert issue["observed"] is None
    assert issue["evidence"] == ["gop_low", "duration_short"]


def test_payload_carries_no_audio_or_path_field() -> None:
    document = _payload().to_json_object()
    serialized = json.dumps(document, ensure_ascii=False)

    for forbidden in ("audio", "path", "wav", "sha256", "speaker"):
        assert forbidden not in serialized.lower()


def test_unlocatable_issue_yields_a_null_location_not_a_guess() -> None:
    payload = _payload(coached_issues=[_draft(start_ms=None, end_ms=None)])

    assert payload.to_json_object()["topIssues"][0]["location"] is None


def test_unmeasured_prosody_axis_is_omitted_not_zero_filled() -> None:
    payload = _payload(prosody=PayloadProsody(values={"pitchRangeZ": -1.2}))

    prosody = payload.to_json_object()["prosody"]
    assert prosody == {"pitchRangeZ": -1.2}
    assert "energy" not in prosody


def test_absent_prosody_omits_the_key_entirely() -> None:
    assert "prosody" not in _payload().to_json_object()


def test_issue_count_is_capped() -> None:
    drafts = [_draft(issue_type=f"type_{index}") for index in range(6)]

    payload = _payload(coached_issues=drafts)

    assert len(payload.top_issues) == MAXIMUM_PAYLOAD_ISSUES


def test_payload_requires_an_issue_and_an_allowlist() -> None:
    with pytest.raises(CoachingPayloadError, match="no coached issue"):
        _payload(coached_issues=[])
    with pytest.raises(CoachingPayloadError, match="allowed_advice_ids"):
        _payload(allowed_advice_ids=[])


def test_issue_without_evidence_is_rejected() -> None:
    with pytest.raises(CoachingPayloadError, match="evidence"):
        _payload(coached_issues=[_draft(evidence=())])


@pytest.mark.parametrize(
    ("start_ms", "end_ms"),
    [(-1, 100), (100, 100), (200, 100)],
)
def test_malformed_location_is_rejected(start_ms: int, end_ms: int) -> None:
    with pytest.raises(CoachingPayloadError, match="positive forward range"):
        PayloadLocation(start_ms=start_ms, end_ms=end_ms, syllable_index=0)


def test_privacy_scan_rejects_a_leaked_digest_or_path() -> None:
    base = _payload()

    with_digest = CoachingPayload(
        schema_version=PAYLOAD_SCHEMA_VERSION,
        target_text="a" * 64,
        learner_level="unknown",
        top_issues=base.top_issues,
        allowed_advice_ids=base.allowed_advice_ids,
    )
    with pytest.raises(CoachingPayloadError, match="digest"):
        assert_payload_is_privacy_safe(with_digest)

    with_path = CoachingPayload(
        schema_version=PAYLOAD_SCHEMA_VERSION,
        target_text="녹음은 /data/restricted 에 있다",
        learner_level="unknown",
        top_issues=base.top_issues,
        allowed_advice_ids=base.allowed_advice_ids,
    )
    with pytest.raises(CoachingPayloadError, match="filesystem path"):
        assert_payload_is_privacy_safe(with_path)


def test_privacy_scan_rejects_a_speaker_identifier() -> None:
    base = _payload()
    leaked = CoachingPayload(
        schema_version=PAYLOAD_SCHEMA_VERSION,
        target_text="화자 spk_9f31c2 의 발화",
        learner_level="unknown",
        top_issues=base.top_issues,
        allowed_advice_ids=base.allowed_advice_ids,
    )

    with pytest.raises(CoachingPayloadError, match="speaker identifier"):
        assert_payload_is_privacy_safe(leaked)


# -- response validation: the actual control ---------------------------------


def test_valid_response_passes() -> None:
    payload = _payload()
    generated = [
        GeneratedAdvice(
            issue_type="final_consonant_deletion",
            advice_id=ADVICE_ID,
            message="받침을 유지해 보세요.",
        )
    ]

    assert validate_generated_advice(payload=payload, generated=generated) == tuple(
        generated
    )


def test_advice_id_outside_the_allowlist_discards_the_response() -> None:
    payload = _payload()
    generated = [
        GeneratedAdvice(
            issue_type="final_consonant_deletion",
            advice_id="MADE_UP_RULE_999",
            message="아무 조언",
        )
    ]

    with pytest.raises(CoachingPayloadError, match="outside the allowlist"):
        validate_generated_advice(payload=payload, generated=generated)


def test_advice_for_an_issue_not_in_the_payload_is_rejected() -> None:
    payload = _payload()
    generated = [
        GeneratedAdvice(
            issue_type="misplaced_pause",
            advice_id=ADVICE_ID,
            message="멈추지 마세요.",
        )
    ]

    with pytest.raises(CoachingPayloadError, match="outside the payload"):
        validate_generated_advice(payload=payload, generated=generated)


def test_one_bad_entry_discards_every_entry() -> None:
    """A partially trustworthy coaching set is not shown; the caller degrades."""

    payload = _payload(
        coached_issues=[_draft(), _draft(issue_type="vowel_quality", syllable_index=1)],
        allowed_advice_ids=[ADVICE_ID, "ARTICULATION_RULE_021"],
    )
    generated = [
        GeneratedAdvice("final_consonant_deletion", ADVICE_ID, "좋은 조언"),
        GeneratedAdvice("vowel_quality", "NOT_ALLOWED", "나쁜 조언"),
    ]

    with pytest.raises(CoachingPayloadError, match="outside the allowlist"):
        validate_generated_advice(payload=payload, generated=generated)


def test_duplicate_advice_for_one_issue_is_rejected() -> None:
    payload = _payload()
    generated = [
        GeneratedAdvice("final_consonant_deletion", ADVICE_ID, "첫 번째"),
        GeneratedAdvice("final_consonant_deletion", ADVICE_ID, "두 번째"),
    ]

    with pytest.raises(CoachingPayloadError, match="duplicate advice"):
        validate_generated_advice(payload=payload, generated=generated)


def test_empty_response_is_rejected() -> None:
    with pytest.raises(CoachingPayloadError, match="no advice"):
        validate_generated_advice(payload=_payload(), generated=[])


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("   ", "must not be empty"),
        ("가" * (MAXIMUM_MESSAGE_CHARACTERS + 1), "length limit"),
        ("증거는 " + "a" * 64 + " 입니다", "digest"),
        ("파일 /data/restricted/x.wav 을 보세요", "filesystem path"),
        ("화자 speaker_9f31c2 참고", "speaker identifier"),
    ],
)
def test_unsafe_or_malformed_message_is_rejected(message: str, expected: str) -> None:
    payload = _payload()
    generated = [GeneratedAdvice("final_consonant_deletion", ADVICE_ID, message)]

    with pytest.raises(CoachingPayloadError, match=expected):
        validate_generated_advice(payload=payload, generated=generated)


def test_generated_advice_cannot_carry_a_span_or_a_score() -> None:
    """The type has no such field, so a model cannot relocate or re-score."""

    fields = set(GeneratedAdvice.__dataclass_fields__)

    assert fields == {"issue_type", "advice_id", "message"}


# -- model response parsing ---------------------------------------------------


def test_parser_accepts_the_documented_array() -> None:
    raw = json.dumps(
        [
            {
                "issueType": "final_consonant_deletion",
                "adviceId": ADVICE_ID,
                "message": "받침을 유지해 보세요.",
            }
        ],
        ensure_ascii=False,
    )

    parsed = parse_generated_response(raw)

    assert parsed[0].advice_id == ADVICE_ID


def test_parser_strips_one_fenced_block() -> None:
    raw = (
        "```json\n"
        '[{"issueType": "final_consonant_deletion", "adviceId": "'
        + ADVICE_ID
        + '", "message": "받침 유지"}]\n```'
    )

    assert parse_generated_response(raw)[0].issue_type == "final_consonant_deletion"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("설명을 드리자면 ...", "not valid JSON"),
        ("{}", "non-empty JSON array"),
        ("[]", "non-empty JSON array"),
        ('["문자열"]', "must be an object"),
        ('[{"issueType": "x", "adviceId": "y"}]', "schema differs"),
        (
            '[{"issueType": "x", "adviceId": "y", "message": "z", "extra": 1}]',
            "schema differs",
        ),
        ('[{"issueType": "", "adviceId": "y", "message": "z"}]', "issueType"),
        ('[{"issueType": "x", "adviceId": "y", "message": "  "}]', "message"),
    ],
)
def test_parser_rejects_anything_else(raw: str, expected: str) -> None:
    with pytest.raises(CoachingPayloadError, match=expected):
        parse_generated_response(raw)


def test_unavailable_generator_declares_its_absence() -> None:
    generator = UnavailableAdviceGenerator()

    assert generator.generator_id == "unavailable-advice-generator"
    with pytest.raises(CoachingPayloadError, match="no local coaching model"):
        generator.generate(_payload())
