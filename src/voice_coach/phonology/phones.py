"""Conservative normalization of AI Hub 71490 realized-phone labels.

TextGrid phone labels contain both valid Korean compatibility jamo and free-form
annotator notes.  Only transformations guaranteed by the dataset policy are
performed here.  In particular, Hangul syllables and compatibility-jamo
clusters are not guessed or decomposed into phones.
"""

from __future__ import annotations

from collections.abc import Iterable
import unicodedata

UNKNOWN_PHONE_TOKEN = "<unk>"
SPEECH_NOISE_TOKEN = "spn"

# The modern 19 consonants and 21 vowels represented as Hangul Compatibility
# Jamo.  Keep these code points as-is: NFKC would convert them to conjoining
# jamo and violate the dataset contract.
KOREAN_COMPATIBILITY_JAMO: tuple[str, ...] = (
    "ㄱ",
    "ㄲ",
    "ㄴ",
    "ㄷ",
    "ㄸ",
    "ㄹ",
    "ㅁ",
    "ㅂ",
    "ㅃ",
    "ㅅ",
    "ㅆ",
    "ㅇ",
    "ㅈ",
    "ㅉ",
    "ㅊ",
    "ㅋ",
    "ㅌ",
    "ㅍ",
    "ㅎ",
    "ㅏ",
    "ㅐ",
    "ㅑ",
    "ㅒ",
    "ㅓ",
    "ㅔ",
    "ㅕ",
    "ㅖ",
    "ㅗ",
    "ㅘ",
    "ㅙ",
    "ㅚ",
    "ㅛ",
    "ㅜ",
    "ㅝ",
    "ㅞ",
    "ㅟ",
    "ㅠ",
    "ㅡ",
    "ㅢ",
    "ㅣ",
)
_KOREAN_COMPATIBILITY_JAMO_SET = frozenset(KOREAN_COMPATIBILITY_JAMO)

# These labels mean that the interval has no phone target.  Matching is exact
# after NFC normalization and whitespace removal; e.g. "--" remains unknown.
EMPTY_REALIZED_PHONE_LABELS = frozenset(("", "-", "_", ".", "－"))


def normalize_realized_phone_label(label: str | None) -> tuple[str, ...]:
    """Normalize one TextGrid label into zero or more conservative tokens.

    Rules, in order:

    * ``None`` and configured blank markers yield an empty sequence.
    * Unicode is normalized with NFC only, then all whitespace is removed.
    * standalone ``spn`` is case-insensitively normalized to lowercase.
    * each of the 40 modern Korean compatibility jamo is one token; a
      concatenation of those jamo becomes a token sequence.
    * every other non-empty label yields exactly ``("<unk>",)``.

    The unknown token preserves annotation uncertainty without inventing a
    pronunciation from Hangul syllables, Latin notes, punctuation, or complex
    compatibility-jamo clusters.
    """

    if label is None:
        return ()
    if not isinstance(label, str):
        raise TypeError("realized phone label must be a string or None")

    normalized = unicodedata.normalize("NFC", label)
    compact = "".join(normalized.split())
    if compact in EMPTY_REALIZED_PHONE_LABELS:
        return ()
    if compact.casefold() == SPEECH_NOISE_TOKEN:
        return (SPEECH_NOISE_TOKEN,)
    if all(character in _KOREAN_COMPATIBILITY_JAMO_SET for character in compact):
        return tuple(compact)
    return (UNKNOWN_PHONE_TOKEN,)


def normalize_realized_phone_labels(
    labels: Iterable[str | None],
) -> tuple[str, ...]:
    """Normalize and flatten an ordered sequence of TextGrid phone labels."""

    return tuple(
        token
        for label in labels
        for token in normalize_realized_phone_label(label)
    )
