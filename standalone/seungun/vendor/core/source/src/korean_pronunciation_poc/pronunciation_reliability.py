from __future__ import annotations
import unicodedata
from collections import defaultdict
from typing import TYPE_CHECKING, Literal
from pydantic import BaseModel, ConfigDict, Field
from .g2p import PronunciationResult
from .schemas import TimeInterval
from .script import ScriptResult
from .phone_symbols import _COMBINING_TO_COMPATIBILITY_JAMO, _COMPOUND_JAMO_DECOMPOSITIONS
if TYPE_CHECKING:
    from .pronunciation_phone_contract import PronunciationPhoneContract
POLICY_VERSION: Literal['pronunciation_reliability_v1'] = 'pronunciation_reliability_v1'
MVP_POLICY_VERSION: Literal['pronunciation_reliability_v2_mvp_review_only'] = 'pronunciation_reliability_v2_mvp_review_only'
_SILENCE_LABELS = {'', 'sil', 'sp', 'spn', '<eps>'}
_PHONE_CLASSES = {'velar_laryngeal_contrast': frozenset(('ㄱ', 'ㄲ', 'ㅋ')), 'alveolar_stop_laryngeal_contrast': frozenset(('ㄷ', 'ㄸ', 'ㅌ')), 'bilabial_stop_laryngeal_contrast': frozenset(('ㅂ', 'ㅃ', 'ㅍ')), 'affricate_laryngeal_contrast': frozenset(('ㅈ', 'ㅉ', 'ㅊ')), 'sibilant_tension_contrast': frozenset(('ㅅ', 'ㅆ'))}
_VOWELS = frozenset('ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ')

class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

class ExpectedPhoneMetadata(_Model):
    expected_index: int = Field(ge=0)
    phone: str
    syllable_index: int = Field(ge=0)
    syllable_text: str
    word_index: int = Field(ge=0)
    word_text: str
    original_word_index: int = Field(ge=0)
    original_word_text: str
    role: Literal['onset', 'nucleus', 'coda']
    acoustically_realized: bool
    boundary_relation: Literal['within_word', 'cross_word_left', 'cross_word_right', 'boundary_ambiguous']
    provenance_confidence: Literal['high', 'ambiguous']
    boundary_position: Literal['interior', 'left_boundary', 'right_boundary', 'cross_word', 'ambiguous']
    source_expected_index: int | None = Field(default=None, ge=0)
    source_phone: str | None = None

class LocalAlignmentItem(_Model):
    operation: Literal['correct', 'substitution', 'deletion', 'insertion']
    expected_index: int | None = Field(default=None, ge=0)
    expected_phone: str | None = None
    observed_phone: str | None = None
    start_ms: float | None = Field(default=None, ge=0.0)
    end_ms: float | None = Field(default=None, ge=0.0)

class PresentationCandidate(_Model):
    tier: Literal['confirmed', 'review', 'suppressed']
    expected_index: int | None = Field(default=None, ge=0)
    expected_phone: str | None = None
    observed_candidate: str | None = None
    raw_alignment_operation: Literal['correct', 'substitution', 'deletion', 'insertion']
    local_alignment_operation: Literal['correct', 'substitution', 'deletion', 'insertion'] | None = None
    alignment_consistency: Literal['agree', 'disagree', 'unavailable']
    word_index: int | None = Field(default=None, ge=0)
    word_text: str | None = None
    start_ms: float | None = Field(default=None, ge=0.0)
    end_ms: float | None = Field(default=None, ge=0.0)
    feedback_category: str
    diagnostic_granularity: Literal['phone', 'phonological_class', 'word']
    reason_codes: tuple[str, ...]

class WordCandidate(_Model):
    word_index: int = Field(ge=0)
    word_text: str
    start_ms: float = Field(ge=0.0)
    end_ms: float = Field(ge=0.0)
    tier: Literal['confirmed', 'review']
    expected_phone_indices: tuple[int, ...]
    operations: tuple[str, ...]
    feedback_category: str
    raw_observed_phones: tuple[str, ...]
    evidence_count: int = Field(ge=1)
    alignment_consistency: Literal['agree', 'disagree', 'unavailable']
    local_word_alignment: tuple[LocalAlignmentItem, ...]

class PresentationSummary(_Model):
    state: Literal['well_done_candidate', 'good_with_tip', 'needs_practice', 'retry_required', 'calibration_required']
    confirmed_phone_count: int = Field(ge=0)
    review_phone_count: int = Field(ge=0)
    suppressed_phone_count: int = Field(ge=0)
    confirmed_word_count: int = Field(ge=0)
    review_word_count: int = Field(ge=0)
    notices: tuple[str, ...] = ()

class PronunciationPresentation(_Model):
    policy_version: Literal['pronunciation_reliability_v1', 'pronunciation_reliability_v2_mvp_review_only'] = POLICY_VERSION
    expected_phone_metadata: tuple[ExpectedPhoneMetadata, ...]
    confirmed_candidates: tuple[PresentationCandidate, ...]
    review_candidates: tuple[PresentationCandidate, ...]
    suppressed_candidates: tuple[PresentationCandidate, ...]
    word_candidates: tuple[WordCandidate, ...]
    summary: PresentationSummary

def _role(character: str) -> Literal['onset', 'nucleus', 'coda'] | None:
    codepoint = ord(character)
    if 4352 <= codepoint <= 4447:
        return 'onset'
    if 4448 <= codepoint <= 4519:
        return 'nucleus'
    if 4520 <= codepoint <= 4607:
        return 'coda'
    return None

def _hangul_syllables(text: str) -> tuple[str, ...]:
    return tuple((character for character in text if '가' <= character <= '힣'))

def _compatibility_phones(jamo: str) -> tuple[str, ...]:
    phones: list[str] = []
    for character in jamo:
        mapped = _COMBINING_TO_COMPATIBILITY_JAMO.get(character)
        if mapped is None:
            continue
        phones.extend(_COMPOUND_JAMO_DECOMPOSITIONS.get(mapped, (mapped,)))
    return tuple(phones)

def _source_candidates_for_target(source: tuple[str, ...], target: tuple[str, ...]) -> tuple[tuple[int, ...], ...]:
    """Return every source index that can match each target index optimally.

    The frozen global detector alignment remains untouched. This separate
    diagnostic calculation only asks whether a provenance mapping is unique;
    multiple equally optimal source owners are deliberately left ambiguous.
    """
    if (len(source) + 1) * (len(target) + 1) > 1000000:
        raise ValueError('NATIVE_PROVENANCE_RESOURCE_LIMIT')
    rows = len(source)
    columns = len(target)
    prefix = [[0] * (columns + 1) for _ in range(rows + 1)]
    for source_index in range(rows + 1):
        prefix[source_index][0] = source_index
    for target_index in range(columns + 1):
        prefix[0][target_index] = target_index
    for source_index in range(1, rows + 1):
        for target_index in range(1, columns + 1):
            prefix[source_index][target_index] = min(prefix[source_index - 1][target_index] + 1, prefix[source_index][target_index - 1] + 1, prefix[source_index - 1][target_index - 1] + int(source[source_index - 1] != target[target_index - 1]))
    suffix = [[0] * (columns + 1) for _ in range(rows + 1)]
    for source_index in range(rows, -1, -1):
        suffix[source_index][columns] = rows - source_index
    for target_index in range(columns, -1, -1):
        suffix[rows][target_index] = columns - target_index
    for source_index in range(rows - 1, -1, -1):
        for target_index in range(columns - 1, -1, -1):
            suffix[source_index][target_index] = min(suffix[source_index + 1][target_index] + 1, suffix[source_index][target_index + 1] + 1, suffix[source_index + 1][target_index + 1] + int(source[source_index] != target[target_index]))
    distance = prefix[rows][columns]
    return tuple((tuple((source_index for source_index in range(rows) if source[source_index] == target[target_index] and prefix[source_index][target_index] + suffix[source_index + 1][target_index + 1] == distance)) for target_index in range(columns)))

def build_expected_phone_metadata(*, script: ScriptResult, pronunciation: PronunciationResult, expected_phones: tuple[str, ...]) -> tuple[ExpectedPhoneMetadata, ...]:
    preliminary: list[ExpectedPhoneMetadata] = []
    global_syllable_index = -1
    for word_index, (source, token) in enumerate(zip(script.tokens, pronunciation.per_token, strict=True)):
        syllables = _hangul_syllables(token.display_hangul)
        local_syllable_index = -1
        for character in token.display_jamo:
            role = _role(character)
            mapped = _COMBINING_TO_COMPATIBILITY_JAMO.get(character)
            if role is None or mapped is None:
                if unicodedata.category(character).startswith(('P', 'Z')):
                    continue
                continue
            if role == 'onset':
                local_syllable_index += 1
                global_syllable_index += 1
            if local_syllable_index < 0 or local_syllable_index >= len(syllables):
                raise ValueError('g2pK Jamo cannot be mapped to its Hangul syllable')
            expanded = _COMPOUND_JAMO_DECOMPOSITIONS.get(mapped, (mapped,))
            for phone in expanded:
                preliminary.append(ExpectedPhoneMetadata(expected_index=len(preliminary), phone=phone, syllable_index=global_syllable_index, syllable_text=syllables[local_syllable_index], word_index=word_index, word_text=source.text, original_word_index=word_index, original_word_text=source.text, role=role, acoustically_realized=not (role == 'onset' and phone == 'ㅇ'), boundary_relation='within_word', provenance_confidence='high', boundary_position='interior'))
    if tuple((item.phone for item in preliminary)) != expected_phones:
        raise ValueError('rich expected-phone metadata changed the frozen flat sequence')
    standalone = pronunciation.standalone_token_jamo or tuple((item.display_jamo for item in pronunciation.per_token))
    source_owners = tuple((word_index for word_index, jamo in enumerate(standalone) for _ in _compatibility_phones(jamo)))
    candidate_sources = _source_candidates_for_target(tuple((phone for jamo in standalone for phone in _compatibility_phones(jamo))), expected_phones)
    first_index_by_word = {word_index: min((item.expected_index for item in preliminary if item.word_index == word_index)) for word_index in range(len(script.tokens)) if any((item.word_index == word_index for item in preliminary))}
    last_index_by_word = {word_index: max((item.expected_index for item in preliminary if item.word_index == word_index)) for word_index in range(len(script.tokens)) if any((item.word_index == word_index for item in preliminary))}
    metadata: list[ExpectedPhoneMetadata] = []
    for item, candidates in zip(preliminary, candidate_sources, strict=True):
        source_word_index = item.word_index
        relation: Literal['within_word', 'cross_word_left', 'cross_word_right', 'boundary_ambiguous'] = 'within_word'
        confidence: Literal['high', 'ambiguous'] = 'high'
        if len(candidates) == 1:
            source_word_index = source_owners[candidates[0]]
            if source_word_index < item.word_index:
                relation = 'cross_word_left'
            elif source_word_index > item.word_index:
                relation = 'cross_word_right'
        else:
            relation, confidence = ('boundary_ambiguous', 'ambiguous')
        if relation == 'within_word':
            if item.expected_index == first_index_by_word[item.word_index]:
                position: Literal['interior', 'left_boundary', 'right_boundary', 'cross_word', 'ambiguous'] = 'left_boundary'
            elif item.expected_index == last_index_by_word[item.word_index]:
                position = 'right_boundary'
            else:
                position = 'interior'
        elif relation == 'boundary_ambiguous':
            position = 'ambiguous'
        else:
            position = 'cross_word'
        metadata.append(item.model_copy(update={'original_word_index': source_word_index, 'original_word_text': script.tokens[source_word_index].text, 'boundary_relation': relation, 'provenance_confidence': confidence, 'boundary_position': position}))
    return tuple(metadata)
