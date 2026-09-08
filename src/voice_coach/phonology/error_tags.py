"""Stable ontology for all AI Hub 71490 pronunciation-error tags."""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
import unicodedata


class ErrorTagId(StrEnum):
    """Language-neutral identifiers persisted in manifests and metrics."""

    INITIAL_CONSONANT = "initial_consonant"
    FINAL_CONSONANT = "final_consonant"
    VOWEL_ERROR = "vowel_error"
    TENSIFICATION = "tensification"
    OTHER = "other"
    REPETITION = "repetition"
    NASALIZATION = "nasalization"
    ASPIRATION = "aspiration"
    LIQUIDIZATION = "liquidization"
    LIAISON_RULE = "liaison_rule"
    BOUNDARY_INTONATION_ERROR = "boundary_intonation_error"
    N_INSERTION = "n_insertion"
    PALATALIZATION = "palatalization"
    STRESS_ERROR = "stress_error"
    INTERJECTION = "interjection"
    VOWEL_INSERTION = "vowel_insertion"
    PHONEME_DELETION = "phoneme_deletion"
    UNKNOWN = "unknown"


RAW_ERROR_TAG_TO_ID = MappingProxyType(
    {
        "초성": ErrorTagId.INITIAL_CONSONANT,
        "종성": ErrorTagId.FINAL_CONSONANT,
        "모음오류": ErrorTagId.VOWEL_ERROR,
        "경음화": ErrorTagId.TENSIFICATION,
        "기타": ErrorTagId.OTHER,
        "반복": ErrorTagId.REPETITION,
        "비음화": ErrorTagId.NASALIZATION,
        "격음화": ErrorTagId.ASPIRATION,
        "유음화": ErrorTagId.LIQUIDIZATION,
        "연음규칙": ErrorTagId.LIAISON_RULE,
        "경계억양 오류": ErrorTagId.BOUNDARY_INTONATION_ERROR,
        "ㄴ-첨가": ErrorTagId.N_INSERTION,
        "구개음화": ErrorTagId.PALATALIZATION,
        "강세 오류": ErrorTagId.STRESS_ERROR,
        "간투사": ErrorTagId.INTERJECTION,
        "모음삽입": ErrorTagId.VOWEL_INSERTION,
        "음운탈락": ErrorTagId.PHONEME_DELETION,
    }
)

ERROR_TAG_ID_TO_RAW_LABEL = MappingProxyType(
    {tag_id: raw_label for raw_label, tag_id in RAW_ERROR_TAG_TO_ID.items()}
)


def normalize_error_tag(label: str | None) -> ErrorTagId:
    """Map an exact dataset label to its stable ID, or return ``UNKNOWN``.

    Leading and trailing annotation whitespace is ignored and NFC is applied.
    No fuzzy matching, case folding, or synonym inference is performed.
    """

    if label is None:
        return ErrorTagId.UNKNOWN
    if not isinstance(label, str):
        raise TypeError("error tag label must be a string or None")
    normalized = unicodedata.normalize("NFC", label).strip()
    return RAW_ERROR_TAG_TO_ID.get(normalized, ErrorTagId.UNKNOWN)
