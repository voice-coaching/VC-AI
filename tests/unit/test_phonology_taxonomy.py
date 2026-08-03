import pytest

from voice_coach.phonology import (
    ERROR_TAG_ID_TO_RAW_LABEL,
    KOREAN_COMPATIBILITY_JAMO,
    RAW_ERROR_TAG_TO_ID,
    ErrorTagId,
    normalize_error_tag,
    normalize_realized_phone_label,
    normalize_realized_phone_labels,
)


def test_phone_inventory_is_exactly_40_distinct_compatibility_jamo() -> None:
    assert len(KOREAN_COMPATIBILITY_JAMO) == 40
    assert len(set(KOREAN_COMPATIBILITY_JAMO)) == 40
    assert KOREAN_COMPATIBILITY_JAMO[:3] == ("ㄱ", "ㄲ", "ㄴ")
    assert KOREAN_COMPATIBILITY_JAMO[-3:] == ("ㅡ", "ㅢ", "ㅣ")


@pytest.mark.parametrize("label", [None, "", " \t\n", "-", "_", ".", "－"])
def test_empty_realized_phone_markers_are_removed(label: str | None) -> None:
    assert normalize_realized_phone_label(label) == ()


def test_realized_phone_normalization_removes_whitespace_and_splits_jamo() -> None:
    assert normalize_realized_phone_label(" ㄱ ㅘ ㄴ ") == ("ㄱ", "ㅘ", "ㄴ")
    assert normalize_realized_phone_labels(["ㄱ ㅏ", "-", "SPN", None]) == (
        "ㄱ",
        "ㅏ",
        "spn",
    )


@pytest.mark.parametrize("label", ["spn", "SPN", "SpN", " s p n "])
def test_spn_is_normalized_case_insensitively(label: str) -> None:
    assert normalize_realized_phone_label(label) == ("spn",)


@pytest.mark.parametrize(
    "label",
    [
        "가",  # precomposed Hangul syllable
        "ᄀ",  # conjoining jamo: NFC must not be replaced by NFKC
        "ㄻ",  # complex compatibility-jamo cluster outside the modern 40
        "x",
        "note",
        "ㄱ-",
        "SPN.",
        "--",
    ],
)
def test_annotation_garbage_is_preserved_as_unknown(label: str) -> None:
    assert normalize_realized_phone_label(label) == ("<unk>",)


def test_invalid_realized_phone_input_is_not_silently_coerced() -> None:
    with pytest.raises(TypeError):
        normalize_realized_phone_label(123)  # type: ignore[arg-type]


def test_all_17_aihub_error_tags_have_stable_unique_ids() -> None:
    expected = {
        "초성": "initial_consonant",
        "종성": "final_consonant",
        "모음오류": "vowel_error",
        "경음화": "tensification",
        "기타": "other",
        "반복": "repetition",
        "비음화": "nasalization",
        "격음화": "aspiration",
        "유음화": "liquidization",
        "연음규칙": "liaison_rule",
        "경계억양 오류": "boundary_intonation_error",
        "ㄴ-첨가": "n_insertion",
        "구개음화": "palatalization",
        "강세 오류": "stress_error",
        "간투사": "interjection",
        "모음삽입": "vowel_insertion",
        "음운탈락": "phoneme_deletion",
    }
    assert len(RAW_ERROR_TAG_TO_ID) == 17
    assert {raw: tag.value for raw, tag in RAW_ERROR_TAG_TO_ID.items()} == expected
    assert len(set(RAW_ERROR_TAG_TO_ID.values())) == 17
    assert len(ERROR_TAG_ID_TO_RAW_LABEL) == 17
    for raw_label, expected_id in expected.items():
        tag_id = normalize_error_tag(raw_label)
        assert tag_id.value == expected_id
        assert ERROR_TAG_ID_TO_RAW_LABEL[tag_id] == raw_label


def test_error_tag_whitespace_and_unknown_handling_are_explicit() -> None:
    assert normalize_error_tag("  종성\n") is ErrorTagId.FINAL_CONSONANT
    assert normalize_error_tag("종 성") is ErrorTagId.UNKNOWN
    assert normalize_error_tag("") is ErrorTagId.UNKNOWN
    assert normalize_error_tag(None) is ErrorTagId.UNKNOWN
    assert normalize_error_tag("새로운 오류") is ErrorTagId.UNKNOWN


def test_invalid_error_tag_input_is_not_silently_coerced() -> None:
    with pytest.raises(TypeError):
        normalize_error_tag(123)  # type: ignore[arg-type]
