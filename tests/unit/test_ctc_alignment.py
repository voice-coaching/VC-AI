"""CTC decoding, content scoring, and span attribution must not invent evidence.

These tests fix the properties the gate depends on: a span only exists for a
prompt syllable the model actually matched, confidence is ``None`` rather than
zero when nothing was attributed, and a posterior matrix that breaks the
contract raises instead of decoding to something plausible.
"""

from __future__ import annotations

import pytest

from voice_coach.pronunciation_feedback.ctc_alignment import (
    BLANK_TOKEN,
    DEFAULT_FRAME_MS,
    WORD_DELIMITER_TOKEN,
    CtcAlignmentError,
    CtcVocabulary,
    alignment_confidence,
    attribute_jamo_spans,
    attribute_spans,
    content_cer,
    content_score,
    content_wer,
    edit_alignment,
    error_rate,
    expected_phone_agreement,
    expected_token_owners,
    greedy_ctc_decode,
    syllable_tokens,
    word_tokens,
    written_jamo_groups,
)

TOKENS = (BLANK_TOKEN, WORD_DELIMITER_TOKEN, "가", "나", "다")
VOCAB = CtcVocabulary(tokens=TOKENS)


def _one_hot(label: str, probability: float = 1.0) -> list[float]:
    row = [0.0] * len(TOKENS)
    index = TOKENS.index(label)
    row[index] = probability
    if probability < 1.0:
        # Spread the remainder over blank so the row stays a distribution.
        row[0] = 1.0 - probability
    return row


def _frames(*specs: tuple[str, int, float]) -> list[list[float]]:
    rows: list[list[float]] = []
    for label, count, probability in specs:
        rows.extend(_one_hot(label, probability) for _ in range(count))
    return rows


# -- vocabulary ---------------------------------------------------------------


def test_vocabulary_rejects_a_missing_blank_at_index_zero() -> None:
    with pytest.raises(CtcAlignmentError, match="index 0 must be the blank"):
        CtcVocabulary(tokens=("가", BLANK_TOKEN))


def test_vocabulary_rejects_duplicates_and_empty_tokens() -> None:
    with pytest.raises(CtcAlignmentError, match="duplicate"):
        CtcVocabulary(tokens=(BLANK_TOKEN, "가", "가"))
    with pytest.raises(CtcAlignmentError, match="empty token"):
        CtcVocabulary(tokens=(BLANK_TOKEN, ""))


def test_vocabulary_from_texts_is_codepoint_sorted_and_reproducible() -> None:
    first = CtcVocabulary.from_texts(["다나", "가 나"])
    second = CtcVocabulary.from_texts(["나 가", "다"])

    assert first.tokens == (BLANK_TOKEN, WORD_DELIMITER_TOKEN, "가", "나", "다")
    assert first.tokens == second.tokens
    assert first.digest == second.digest


def test_vocabulary_from_texts_drops_non_hangul_and_rejects_empty_corpus() -> None:
    vocabulary = CtcVocabulary.from_texts(["가?", "나."])

    assert vocabulary.tokens == (BLANK_TOKEN, WORD_DELIMITER_TOKEN, "가", "나")
    with pytest.raises(CtcAlignmentError, match="no Hangul syllables"):
        CtcVocabulary.from_texts(["abc", "123"])


def test_digest_changes_with_the_label_order() -> None:
    reordered = CtcVocabulary(tokens=(BLANK_TOKEN, WORD_DELIMITER_TOKEN, "나", "가"))

    assert reordered.digest != CtcVocabulary(
        tokens=(BLANK_TOKEN, WORD_DELIMITER_TOKEN, "가", "나")
    ).digest


# -- decoding -----------------------------------------------------------------


def test_greedy_decode_collapses_repeats_and_drops_blanks() -> None:
    posteriors = _frames(
        ("가", 3, 1.0),
        (BLANK_TOKEN, 2, 1.0),
        ("가", 1, 1.0),
        ("나", 2, 1.0),
    )

    decoding = greedy_ctc_decode(posteriors, VOCAB)

    assert decoding.hypothesis == "가가나"
    assert [segment.label for segment in decoding.segments] == ["가", "가", "나"]
    assert decoding.frame_count == 8


def test_repeats_without_a_blank_between_them_merge() -> None:
    decoding = greedy_ctc_decode(_frames(("가", 5, 1.0)), VOCAB)

    assert decoding.hypothesis == "가"
    assert len(decoding.segments) == 1


def test_word_delimiter_becomes_a_space_and_is_not_a_syllable() -> None:
    decoding = greedy_ctc_decode(
        _frames(("가", 2, 1.0), (WORD_DELIMITER_TOKEN, 1, 1.0), ("나", 2, 1.0)),
        VOCAB,
    )

    assert decoding.hypothesis == "가 나"
    assert [segment.label for segment in decoding.syllable_segments] == ["가", "나"]


def test_segment_frame_ranges_convert_to_milliseconds() -> None:
    decoding = greedy_ctc_decode(
        _frames((BLANK_TOKEN, 2, 1.0), ("가", 3, 1.0)),
        VOCAB,
    )
    segment = decoding.segments[0]

    assert segment.start_frame == 2
    assert segment.end_frame == 4
    assert segment.start_ms(DEFAULT_FRAME_MS) == 40
    assert segment.end_ms(DEFAULT_FRAME_MS) == 100


def test_mean_probability_averages_the_frames_of_the_segment() -> None:
    posteriors = [_one_hot("가", 0.6), _one_hot("가", 0.8)]

    decoding = greedy_ctc_decode(posteriors, VOCAB)

    assert decoding.segments[0].mean_probability == pytest.approx(0.7)


def test_all_blank_input_decodes_to_an_empty_hypothesis() -> None:
    decoding = greedy_ctc_decode(_frames((BLANK_TOKEN, 4, 1.0)), VOCAB)

    assert decoding.hypothesis == ""
    assert decoding.segments == ()


@pytest.mark.parametrize(
    ("posteriors", "message"),
    [
        ([], "no frames"),
        ([[1.0, 0.0]], "expected 5"),
        ([[0.0, 0.0, 0.0, 0.0, float("nan")]], "NaN"),
        ([[0.0, 0.0, 0.0, 0.0, 1.5]], "outside"),
        ([[0.0, 0.0, 0.0, 0.0, -0.5]], "outside"),
    ],
)
def test_broken_posterior_matrices_fail_closed(
    posteriors: list[list[float]],
    message: str,
) -> None:
    with pytest.raises(CtcAlignmentError, match=message):
        greedy_ctc_decode(posteriors, VOCAB)


def test_logits_are_rejected_rather_than_silently_decoded() -> None:
    with pytest.raises(CtcAlignmentError, match="probabilities, not logits"):
        greedy_ctc_decode([[-2.0, 3.0, 1.0, 0.5, 0.1]], VOCAB)


# -- edit alignment and error rates -------------------------------------------


def test_edit_alignment_reports_each_operation() -> None:
    steps, distance = edit_alignment(["가", "나", "다"], ["가", "다"])

    assert distance == 1
    assert [step.operation for step in steps] == ["match", "delete", "match"]
    assert [step.reference_index for step in steps] == [0, 1, 2]


def test_edit_alignment_marks_substitutions_and_insertions() -> None:
    substituted, _ = edit_alignment(["가", "나"], ["가", "다"])
    inserted, _ = edit_alignment(["가"], ["가", "나"])

    assert [step.operation for step in substituted] == ["match", "substitute"]
    assert [step.operation for step in inserted] == ["match", "insert"]


def test_identical_sequences_have_zero_distance() -> None:
    steps, distance = edit_alignment(["가", "나"], ["가", "나"])

    assert distance == 0
    assert all(step.operation == "match" for step in steps)


def test_error_rate_is_none_without_a_reference() -> None:
    assert error_rate([], ["가"]) is None
    assert content_wer("", "가") is None
    assert content_cer("", "가") is None
    assert content_score("", "가") is None


def test_word_and_syllable_tokenization_match_the_frozen_definitions() -> None:
    assert word_tokens(" 학교에  갔습니다. ") == ("학교에", "갔습니다.")
    assert syllable_tokens("학교에 갔습니다.") == (
        "학",
        "교",
        "에",
        "갔",
        "습",
        "니",
        "다",
    )


def test_content_rates_are_corpus_level_edits_over_reference_length() -> None:
    assert content_wer("가나 다라", "가나 다라") == pytest.approx(0.0)
    assert content_wer("가나 다라", "가나") == pytest.approx(0.5)
    assert content_cer("가나다", "가나") == pytest.approx(1 / 3)


def test_content_score_is_one_minus_cer_floored_at_zero() -> None:
    assert content_score("가나다", "가나다") == pytest.approx(1.0)
    assert content_score("가나다", "가나") == pytest.approx(2 / 3)
    assert content_score("가", "나다라마바") == pytest.approx(0.0)


# -- span attribution ---------------------------------------------------------


def test_only_matched_prompt_syllables_receive_a_span() -> None:
    decoding = greedy_ctc_decode(
        _frames(
            ("가", 2, 0.9),
            (BLANK_TOKEN, 1, 1.0),
            ("다", 2, 0.8),
        ),
        VOCAB,
    )

    spans, unattributed = attribute_spans(
        decoding=decoding,
        prompt_syllables=["가", "나", "다"],
    )

    assert [span.syllable_index for span in spans] == [0, 2]
    assert unattributed == (1,)


def test_a_substituted_syllable_does_not_inherit_the_emitted_span() -> None:
    decoding = greedy_ctc_decode(_frames(("나", 3, 0.95)), VOCAB)

    spans, unattributed = attribute_spans(
        decoding=decoding,
        prompt_syllables=["가"],
    )

    assert spans == ()
    assert unattributed == (0,)


def test_span_times_come_from_the_emitting_frames() -> None:
    decoding = greedy_ctc_decode(
        _frames((BLANK_TOKEN, 5, 1.0), ("가", 4, 0.7)),
        VOCAB,
    )

    spans, _ = attribute_spans(decoding=decoding, prompt_syllables=["가"])

    assert spans[0].start_ms == 100
    assert spans[0].end_ms == 180
    assert spans[0].confidence == pytest.approx(0.7)


def test_spans_are_monotonic_in_time_and_index() -> None:
    decoding = greedy_ctc_decode(
        _frames(
            ("가", 2, 0.9),
            (BLANK_TOKEN, 1, 1.0),
            ("나", 2, 0.9),
            (BLANK_TOKEN, 1, 1.0),
            ("다", 2, 0.9),
        ),
        VOCAB,
    )

    spans, _ = attribute_spans(decoding=decoding, prompt_syllables=["가", "나", "다"])

    assert [span.syllable_index for span in spans] == [0, 1, 2]
    assert [span.start_ms for span in spans] == sorted(span.start_ms for span in spans)


def test_word_delimiters_do_not_consume_a_prompt_syllable() -> None:
    decoding = greedy_ctc_decode(
        _frames(("가", 2, 0.9), (WORD_DELIMITER_TOKEN, 1, 1.0), ("나", 2, 0.9)),
        VOCAB,
    )

    spans, unattributed = attribute_spans(
        decoding=decoding,
        prompt_syllables=["가", "나"],
    )

    assert [span.syllable_index for span in spans] == [0, 1]
    assert unattributed == ()


# -- alignment confidence -----------------------------------------------------


def test_alignment_confidence_scales_with_coverage() -> None:
    decoding = greedy_ctc_decode(_frames(("가", 2, 1.0)), VOCAB)
    spans, _ = attribute_spans(decoding=decoding, prompt_syllables=["가", "나"])

    assert alignment_confidence(spans=spans, prompt_syllable_count=2) == pytest.approx(
        0.5
    )


def test_full_coverage_and_full_confidence_reach_one() -> None:
    decoding = greedy_ctc_decode(
        _frames(("가", 2, 1.0), (BLANK_TOKEN, 1, 1.0), ("나", 2, 1.0)),
        VOCAB,
    )
    spans, _ = attribute_spans(decoding=decoding, prompt_syllables=["가", "나"])

    assert alignment_confidence(spans=spans, prompt_syllable_count=2) == pytest.approx(
        1.0
    )


def test_no_attributed_span_yields_none_not_zero() -> None:
    assert alignment_confidence(spans=[], prompt_syllable_count=3) is None


def test_non_positive_prompt_length_fails_closed() -> None:
    with pytest.raises(CtcAlignmentError, match="must be positive"):
        alignment_confidence(spans=[], prompt_syllable_count=0)


# -- expected phone agreement -------------------------------------------------


def test_expected_phone_agreement_reports_unscorable_labels() -> None:
    report = expected_phone_agreement(
        expected_tokens=["ㄱ", "ㅏ", "ㅙ", WORD_DELIMITER_TOKEN],
        scorable_inventory=["ㄱ", "ㅏ", "ㅚ"],
    )

    assert report["agrees"] is False
    assert report["labels_outside_inventory"] == ("ㅙ",)
    assert report["expected_token_count"] == 4


def test_expected_phone_agreement_passes_when_every_label_is_scorable() -> None:
    report = expected_phone_agreement(
        expected_tokens=["ㄱ", "ㅏ"],
        scorable_inventory=["ㄱ", "ㅏ", "ㅚ"],
    )

    assert report["agrees"] is True
    assert report["labels_outside_inventory"] == ()


# -- jamo-level attribution ---------------------------------------------------

JAMO_TOKENS = (
    BLANK_TOKEN,
    WORD_DELIMITER_TOKEN,
    "ㄱ",
    "ㄲ",
    "ㄴ",
    "ㅅ",
    "ㅇ",
    "ㅎ",
    "ㅏ",
    "ㅗ",
    "ㅛ",
)
JAMO_VOCAB = CtcVocabulary(tokens=JAMO_TOKENS)


def _jamo_one_hot(label: str, probability: float = 1.0) -> list[float]:
    row = [0.0] * len(JAMO_TOKENS)
    row[JAMO_TOKENS.index(label)] = probability
    if probability < 1.0:
        row[0] = 1.0 - probability
    return row


def _jamo_frames(*specs: tuple[str, int, float]) -> list[list[float]]:
    rows: list[list[float]] = []
    for label, count, probability in specs:
        rows.extend(_jamo_one_hot(label, probability) for _ in range(count))
    return rows


def test_written_jamo_groups_keep_optional_codas() -> None:
    groups = written_jamo_groups([("ㅎ", "ㅏ", "ㄱ"), ("ㄱ", "ㅛ", None)])

    assert groups == (("ㅎ", "ㅏ", "ㄱ"), ("ㄱ", "ㅛ"))


def test_written_jamo_groups_reject_an_incomplete_syllable() -> None:
    with pytest.raises(CtcAlignmentError, match="onset and a nucleus"):
        written_jamo_groups([("ㅎ", "", None)])
    with pytest.raises(CtcAlignmentError, match="no syllable"):
        written_jamo_groups([])


def test_rule_inserted_phone_belongs_to_the_syllable_it_strengthens() -> None:
    """학교 -> ㅎㅏㄱㄲㅛ: the tensified ㄲ is the second syllable's onset."""

    groups = written_jamo_groups([("ㅎ", "ㅏ", "ㄱ"), ("ㄱ", "ㅛ", None)])
    expected = ("ㅎ", "ㅏ", "ㄱ", "ㄲ", "ㅛ")

    owners = expected_token_owners(written_groups=groups, expected_tokens=expected)

    assert owners == (0, 0, 0, 1, 1)


def test_word_delimiters_own_no_syllable() -> None:
    groups = written_jamo_groups([("ㅎ", "ㅏ", None), ("ㄴ", "ㅏ", None)])
    expected = ("ㅎ", "ㅏ", WORD_DELIMITER_TOKEN, "ㄴ", "ㅏ")

    owners = expected_token_owners(written_groups=groups, expected_tokens=expected)

    assert owners == (0, 0, None, 1, 1)


def test_every_expected_phone_receives_an_owner() -> None:
    groups = written_jamo_groups([("ㅎ", "ㅏ", "ㄱ"), ("ㄱ", "ㅛ", None)])
    expected = ("ㅎ", "ㅏ", "ㄱ", "ㄲ", "ㅛ", "ㅇ")

    owners = expected_token_owners(written_groups=groups, expected_tokens=expected)

    assert all(owner is not None for owner in owners)


def test_correct_jamo_reading_gives_every_syllable_a_span() -> None:
    groups = written_jamo_groups([("ㅎ", "ㅏ", "ㄱ"), ("ㄱ", "ㅛ", None)])
    expected = ("ㅎ", "ㅏ", "ㄱ", "ㄲ", "ㅛ")
    owners = expected_token_owners(written_groups=groups, expected_tokens=expected)
    decoding = greedy_ctc_decode(
        _jamo_frames(
            ("ㅎ", 2, 1.0),
            ("ㅏ", 2, 1.0),
            ("ㄱ", 2, 1.0),
            ("ㄲ", 2, 1.0),
            ("ㅛ", 2, 1.0),
        ),
        JAMO_VOCAB,
    )

    spans, unattributed = attribute_jamo_spans(
        decoding=decoding,
        expected_tokens=expected,
        owners=owners,
        syllable_count=2,
    )

    assert [span.syllable_index for span in spans] == [0, 1]
    assert unattributed == ()
    assert spans[0].start_ms == 0
    assert spans[0].end_ms == 120
    assert spans[1].start_ms == 120
    assert spans[1].end_ms == 200
    assert all(span.confidence == pytest.approx(1.0) for span in spans)


def test_a_substituted_phone_still_locates_its_syllable() -> None:
    """Coaching needs the time range of the mispronounced syllable."""

    groups = written_jamo_groups([("ㅎ", "ㅏ", None)])
    expected = ("ㅎ", "ㅏ")
    owners = expected_token_owners(written_groups=groups, expected_tokens=expected)
    decoding = greedy_ctc_decode(
        _jamo_frames(("ㅅ", 2, 0.9), ("ㅏ", 2, 0.9)),
        JAMO_VOCAB,
    )

    spans, unattributed = attribute_jamo_spans(
        decoding=decoding,
        expected_tokens=expected,
        owners=owners,
        syllable_count=1,
    )

    assert [span.syllable_index for span in spans] == [0]
    assert unattributed == ()


def test_a_fully_deleted_syllable_has_no_span() -> None:
    groups = written_jamo_groups([("ㅎ", "ㅏ", None), ("ㄴ", "ㅏ", None)])
    expected = ("ㅎ", "ㅏ", "ㄴ", "ㅏ")
    owners = expected_token_owners(written_groups=groups, expected_tokens=expected)
    decoding = greedy_ctc_decode(
        _jamo_frames(("ㅎ", 2, 1.0), ("ㅏ", 2, 1.0)),
        JAMO_VOCAB,
    )

    spans, unattributed = attribute_jamo_spans(
        decoding=decoding,
        expected_tokens=expected,
        owners=owners,
        syllable_count=2,
    )

    assert [span.syllable_index for span in spans] == [0]
    assert unattributed == (1,)


def test_partial_syllable_coverage_lowers_its_confidence() -> None:
    groups = written_jamo_groups([("ㅎ", "ㅏ", "ㄱ")])
    expected = ("ㅎ", "ㅏ", "ㄱ")
    owners = expected_token_owners(written_groups=groups, expected_tokens=expected)
    decoding = greedy_ctc_decode(_jamo_frames(("ㅎ", 2, 1.0)), JAMO_VOCAB)

    spans, _ = attribute_jamo_spans(
        decoding=decoding,
        expected_tokens=expected,
        owners=owners,
        syllable_count=1,
    )

    assert spans[0].confidence == pytest.approx(1 / 3)


def test_owner_count_mismatch_fails_closed() -> None:
    decoding = greedy_ctc_decode(_jamo_frames(("ㅎ", 1, 1.0)), JAMO_VOCAB)

    with pytest.raises(CtcAlignmentError, match="owner count"):
        attribute_jamo_spans(
            decoding=decoding,
            expected_tokens=("ㅎ", "ㅏ"),
            owners=(0,),
            syllable_count=1,
        )


def test_non_positive_syllable_count_fails_closed() -> None:
    decoding = greedy_ctc_decode(_jamo_frames(("ㅎ", 1, 1.0)), JAMO_VOCAB)

    with pytest.raises(CtcAlignmentError, match="syllable_count must be positive"):
        attribute_jamo_spans(
            decoding=decoding,
            expected_tokens=("ㅎ",),
            owners=(0,),
            syllable_count=0,
        )


def test_phoneless_expected_stream_fails_closed() -> None:
    decoding = greedy_ctc_decode(_jamo_frames(("ㅎ", 1, 1.0)), JAMO_VOCAB)

    with pytest.raises(CtcAlignmentError, match="no phone"):
        attribute_jamo_spans(
            decoding=decoding,
            expected_tokens=(WORD_DELIMITER_TOKEN,),
            owners=(None,),
            syllable_count=1,
        )
