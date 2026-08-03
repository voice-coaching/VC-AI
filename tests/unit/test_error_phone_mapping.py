import pytest

from voice_coach.phonology import (
    ErrorPhoneMatchStatus,
    ErrorTagId,
    ErrorTagInterval,
    TargetPhoneInterval,
    assign_error_tags_to_target_phones,
)


def target(
    index: int,
    label: str,
    start: float,
    end: float,
) -> TargetPhoneInterval:
    return TargetPhoneInterval(index, label, start, end)


def error(
    index: int,
    label: str,
    start: float,
    end: float,
) -> ErrorTagInterval:
    return ErrorTagInterval(index, label, start, end)


def test_maximum_positive_overlap_and_half_open_boundary_are_fixed() -> None:
    assignments = assign_error_tags_to_target_phones(
        [
            error(0, "초성", 0.15, 0.35),
            error(1, "모음오류", 0.40, 0.50),
        ],
        [
            target(0, "ㄱ", 0.00, 0.20),
            target(1, "ㅏ", 0.20, 0.40),
        ],
    )

    assert assignments[0].match_status is ErrorPhoneMatchStatus.MATCHED
    assert assignments[0].target_phone_index == 1
    assert assignments[0].overlap_seconds == pytest.approx(0.15)
    assert assignments[1].match_status is ErrorPhoneMatchStatus.UNMATCHED
    assert assignments[1].target_phone_index is None


def test_exact_overlap_tie_uses_lowest_target_index_not_input_order() -> None:
    source = [error(0, "종성", 0.10, 0.30)]
    targets = [
        target(8, "ㄹ", 0.20, 0.30),
        target(3, "ㄱ", 0.10, 0.20),
    ]

    forward = assign_error_tags_to_target_phones(source, targets)
    reverse = assign_error_tags_to_target_phones(source, list(reversed(targets)))

    assert forward == reverse
    assert forward[0].target_phone_index == 3
    assert forward[0].target_phone_label == "ㄱ"


def test_multi_tag_intervals_are_preserved_without_collapsing() -> None:
    assignments = assign_error_tags_to_target_phones(
        [
            error(2, "경음화", 0.0, 0.2),
            error(1, "초성", 0.0, 0.2),
        ],
        [target(5, "ㄲ", 0.0, 0.2)],
    )

    assert [item.source_error_index for item in assignments] == [1, 2]
    assert [item.tag_id for item in assignments] == [
        ErrorTagId.INITIAL_CONSONANT,
        ErrorTagId.TENSIFICATION,
    ]
    assert [item.target_phone_index for item in assignments] == [5, 5]


@pytest.mark.parametrize(
    ("raw_label", "expected"),
    [
        ("기타", ErrorTagId.OTHER),
        ("반복", ErrorTagId.REPETITION),
        ("경계억양 오류", ErrorTagId.BOUNDARY_INTONATION_ERROR),
        ("새 오류", ErrorTagId.UNKNOWN),
    ],
)
def test_non_phone_localizable_and_unknown_tags_never_gain_target_claim(
    raw_label: str,
    expected: ErrorTagId,
) -> None:
    assignment = assign_error_tags_to_target_phones(
        [error(0, raw_label, 0.0, 0.2)],
        [target(0, "ㄱ", 0.0, 0.2)],
    )[0]

    assert assignment.tag_id is expected
    assert assignment.match_status is ErrorPhoneMatchStatus.NOT_PHONE_LOCALIZABLE
    assert assignment.target_phone_index is None
    assert assignment.target_phone_label is None
    assert assignment.overlap_seconds == 0.0


def test_duplicate_indices_and_invalid_intervals_fail_closed() -> None:
    with pytest.raises(ValueError, match="unique"):
        assign_error_tags_to_target_phones(
            [error(0, "초성", 0.0, 0.1)],
            [
                target(0, "ㄱ", 0.0, 0.1),
                target(0, "ㄴ", 0.1, 0.2),
            ],
        )
    with pytest.raises(ValueError, match="0 <="):
        error(0, "초성", 0.2, 0.1)
