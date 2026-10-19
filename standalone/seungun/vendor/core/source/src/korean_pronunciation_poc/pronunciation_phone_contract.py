"""Production-only detector-facing expected-phone contract.

The frozen historical ``build_expected_jamo_sequence`` path remains untouched.
This module derives a separate sequence solely for production detector input while
retaining the full g2pK representation for presentation and provenance.
"""
from __future__ import annotations
import unicodedata
from dataclasses import dataclass
from .g2p import PronunciationResult
from .pronunciation_reliability import ExpectedPhoneMetadata, build_expected_phone_metadata
from .script import ScriptResult
from .phone_symbols import _COMBINING_TO_COMPATIBILITY_JAMO, _COMPOUND_JAMO_DECOMPOSITIONS
DETECTOR_PHONE_SOURCE_V2 = 'g2pk_detector_phone_contract_v2'

@dataclass(frozen=True)
class PronunciationPhoneContract:
    """One g2pK result represented for presentation and detector consumers."""
    full_metadata: tuple[ExpectedPhoneMetadata, ...]
    detector_metadata: tuple[ExpectedPhoneMetadata, ...]
    detector_phones: tuple[str, ...]
    detector_phone_source: str = DETECTOR_PHONE_SOURCE_V2

    @property
    def full_phones(self) -> tuple[str, ...]:
        return tuple((item.phone for item in self.full_metadata))

def _full_compatibility_phones(display_jamo: str) -> tuple[str, ...]:
    phones: list[str] = []
    for character in display_jamo:
        if character.isspace() or unicodedata.category(character).startswith('P'):
            continue
        mapped = _COMBINING_TO_COMPATIBILITY_JAMO.get(character)
        if mapped is None:
            continue
        phones.extend(_COMPOUND_JAMO_DECOMPOSITIONS.get(mapped, (mapped,)))
    return tuple(phones)

def _detector_phones_for_metadata(metadata: tuple[ExpectedPhoneMetadata, ...]) -> tuple[ExpectedPhoneMetadata, ...]:
    detector_metadata: list[ExpectedPhoneMetadata] = []
    for source in metadata:
        if source.role == 'onset' and source.phone == 'ㅇ':
            continue
        expanded = {'ㅙ': ('ㅗ', 'ㅐ'), 'ㅞ': ('ㅜ', 'ㅔ')}.get(source.phone, (source.phone,))
        for phone in expanded:
            detector_metadata.append(source.model_copy(update={'expected_index': len(detector_metadata), 'phone': phone, 'source_expected_index': source.expected_index, 'source_phone': source.phone}))
    return tuple(detector_metadata)

def build_pronunciation_phone_contract(*, script: ScriptResult, pronunciation: PronunciationResult) -> PronunciationPhoneContract:
    """Build the full and detector-facing views from the same g2pK result."""
    full_metadata = build_expected_phone_metadata(script=script, pronunciation=pronunciation, expected_phones=_full_compatibility_phones(pronunciation.display_jamo))
    detector_metadata = _detector_phones_for_metadata(full_metadata)
    return PronunciationPhoneContract(full_metadata=full_metadata, detector_metadata=detector_metadata, detector_phones=tuple((item.phone for item in detector_metadata)))
