"""Native observations and expected samples; independent of the retired detector."""
from __future__ import annotations

from dataclasses import dataclass

from .phone_rubric import CRITERIA, require

EXPECTED_REVISION = "native-expected-phones-v1"
INVENTORY_REVISION = "native-phone-inventory-v1"
ALIGNMENT_REVISION = "native-phone-alignment-v1"
ASSESSMENT_REVISION = "native-phone-assessment-v1"
EVIDENCE_REVISION = "native-phone-evidence-v1"
RUBRIC_REVISION = "phone-rubric-native-v1"
FEATURE_REVISION = "native-expected-margin-run-fp32-v1"
MODEL_REVISION = "e26ff9dfb62169acf445d0060ef56863c018b20e"
MODEL_WEIGHTS = "b2ec663558606d651a718fa1eaa20c6d504df173725ecbc66df74c22267e2d68"
NUMPY_VERSION = "2.4.6"  # Observed in the pinned Native interpreter on 2026-10-04.
TOKENS = tuple("A B BB CHh D DD E EO EU G GG H I J JJ Kh L M N NG O Ph R S SS Th U euI iA iE iEO iO iU k oA oE p t uEO uI | [UNK] [PAD] <s> </s>".split())
BLANK_ID = 42
BOUNDARY_ID = 40
UNVERIFIED_VOWELS = {6: "ㅐㅔ", 29: "ㅒㅖ", 35: "ㅙㅚㅞ"}
# Only mappings already present in the pinned Native source are active here.
VERIFIED_PHONES = {
    0: "ㅏ", 1: "ㅂ", 2: "ㅃ", 3: "ㅊ", 4: "ㄷ", 5: "ㄸ",
    7: "ㅓ", 8: "ㅡ", 9: "ㄱ", 10: "ㄲ", 11: "ㅎ", 12: "ㅣ",
    13: "ㅈ", 14: "ㅉ", 15: "ㅋ", 16: "ㄹ", 17: "ㅁ", 18: "ㄴ",
    19: "ㅇ", 20: "ㅗ", 21: "ㅍ", 22: "ㄹ", 23: "ㅅ", 24: "ㅆ",
    25: "ㅌ", 26: "ㅜ", 27: "ㅢ", 28: "ㅑ", 30: "ㅕ", 31: "ㅛ",
    32: "ㅠ", 33: "ㄱ", 34: "ㅘ", 36: "ㅂ", 37: "ㄷ", 38: "ㅝ", 39: "ㅟ",
}
VOWELS = frozenset(CRITERIA[0][2])
CODA_ONLY = frozenset((19, 33, 36, 37))
ONSET_ONLY = frozenset((1, 2, 3, 4, 5, 9, 10, 11, 13, 14, 15, 21, 22, 23, 24, 25))


@dataclass(frozen=True, slots=True)
class ExpectedPhone:
    index: int
    phone: str
    role: str
    source_index: int
    word_index: int
    syllable_index: int
    provenance_resolved: bool

    @property
    def criterion(self):
        return next(key for key, _, phones in CRITERIA[:-1] if self.phone in phones)


@dataclass(frozen=True, slots=True)
class ObservedRun:
    index: int
    token_id: int
    start_frame: int
    end_frame: int  # exclusive; a CTC span is not pronunciation duration


def expected_from_metadata(metadata):
    """Use the full g2pK metadata, NOT the split detector-facing sequence.

    Preserve the original source index after removing silent onset ㅇ. Mapping
    uncertainty remains attached to a sample, never removed from the denominator.
    """
    require(type(metadata) is list and 0 < len(metadata) <= 8192, "NATIVE_EXPECTED_METADATA")
    result, excluded = [], []
    for source_index, row in enumerate(metadata):
        require(type(row) is dict and type(row.get("expected_index")) is int
                and row["expected_index"] == source_index, "NATIVE_SOURCE_INDEX")
        phone, role = row.get("phone"), row.get("role")
        require(type(phone) is str and any(phone in group for _, _, group in CRITERIA[:-1])
                and role in {"onset", "nucleus", "coda"}, "NATIVE_EXPECTED_PHONE")
        require((phone in VOWELS) == (role == "nucleus"), "NATIVE_EXPECTED_ROLE")
        require(all(type(row.get(k)) is int and row[k] >= 0
                    for k in ("original_word_index", "syllable_index")), "NATIVE_SOURCE_POSITION")
        if phone == "ㅇ" and role == "onset":
            excluded.append(dict(sourceIndex=source_index, reason="SILENT_ONSET_IEUNG"))
            continue
        result.append(ExpectedPhone(len(result), phone, role, source_index,
                                    row["original_word_index"], row["syllable_index"],
                                    row.get("provenance_confidence") == "high"
                                    and row.get("boundary_relation") != "boundary_ambiguous"))
    require(0 < len(result) <= 4096, "NATIVE_EXPECTED_COUNT")
    return tuple(result), excluded


def role_compatible(token_id, role):
    if token_id >= 40:
        return False
    vowel = token_id in UNVERIFIED_VOWELS or VERIFIED_PHONES.get(token_id) in VOWELS
    if vowel:
        return role == "nucleus"
    if role == "nucleus":
        return False
    if token_id in CODA_ONLY:
        return role == "coda"
    if token_id in ONSET_ONLY:
        return role == "onset"
    return True


def token_classes(expected, mappings):
    return tuple(token for token, phones in mappings.items()
                 if expected.phone in phones and role_compatible(token, expected.role))
