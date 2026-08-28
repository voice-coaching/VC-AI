"""Deterministic grapheme-level syllable mapping for learner-facing spans.

Hangul syllable decomposition is arithmetic on Unicode code points, so this
adapter needs no model and no external dependency.  It produces the character
offset anchors that highlighting requires.

These jamo are *written* graphemes.  They are not expected phones: no
phonological rule (연음, 경음화, 유음화, 장단음) has been applied.  Expected
phones come from :mod:`voice_coach.phonology.g2pk_expected` through
``ExpectedPhonePort`` and are kept in a separate field of ``PromptSpec``.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

from ..domain import SyllableSlot

_SYLLABLE_BASE = 0xAC00
_SYLLABLE_LAST = 0xD7A3
_ONSETS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_NUCLEI = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_CODAS = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def normalize_prompt_text(text: str) -> str:
    """Apply the R00 card rule: NFKC, then collapse Unicode whitespace."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.split())


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decompose_syllables(normalized_text: str) -> tuple[SyllableSlot, ...]:
    """Map every Hangul syllable to its character offset and jamo structure.

    Non-Hangul characters (spaces, punctuation) hold no slot; offsets refer to
    the normalized text so that a caller can highlight the original display
    string it was given.
    """

    slots: list[SyllableSlot] = []
    for offset, char in enumerate(normalized_text):
        code = ord(char)
        if not _SYLLABLE_BASE <= code <= _SYLLABLE_LAST:
            continue
        remainder = code - _SYLLABLE_BASE
        coda_index = remainder % 28
        nucleus_index = (remainder // 28) % 21
        onset_index = remainder // (28 * 21)
        slots.append(
            SyllableSlot(
                index=len(slots),
                char_start=offset,
                char_end=offset + 1,
                syllable=char,
                onset=_ONSETS[onset_index],
                nucleus=_NUCLEI[nucleus_index],
                coda=None if coda_index == 0 else _CODAS[coda_index],
            )
        )
    return tuple(slots)


def syllable_map_sha256(slots: tuple[SyllableSlot, ...]) -> str:
    """Hash the mapping so an attempt can prove which anchors it used."""

    payload = [
        [slot.index, slot.char_start, slot.char_end, slot.onset, slot.nucleus, slot.coda]
        for slot in slots
    ]
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
