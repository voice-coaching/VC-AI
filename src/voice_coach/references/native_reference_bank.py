"""Pseudonymized native multi-speaker reference-bank primitives.

The bank accepts only metadata and already-filtered native records. It never
stores audio bytes, raw locators, transcripts, or identity mappings. A speaker
hash must be supplied by an authorized metadata manifest; a source or stable
record hash is not an acceptable substitute for speaker identity.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from voice_coach.data.adversarial_speech import NativeSpeechRecord

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class NativeReferenceBankError(ValueError):
    """Raised when a bank would make an unsupported multi-speaker claim."""


@dataclass(frozen=True, slots=True)
class NativeReferenceEntry:
    record_id_sha256: str
    source_id: str
    speaker_id_sha256: str
    sentence_key_sha256: str
    style: str
    duration_seconds: float
    quality_target: float

    def to_public_dict(self) -> dict[str, object]:
        return {
            "record_id_sha256": self.record_id_sha256,
            "source_id": self.source_id,
            "speaker_id_sha256": self.speaker_id_sha256,
            "sentence_key_sha256": self.sentence_key_sha256,
            "style": self.style,
            "duration_seconds": self.duration_seconds,
            "quality_target": self.quality_target,
        }


@dataclass(frozen=True, slots=True)
class NativeReferenceBank:
    entries: tuple[NativeReferenceEntry, ...]
    minimum_speakers_per_sentence: int
    speaker_count: int
    sentence_count: int
    status: str

    def to_report(self) -> dict[str, object]:
        by_sentence: defaultdict[str, set[str]] = defaultdict(set)
        for entry in self.entries:
            by_sentence[entry.sentence_key_sha256].add(entry.speaker_id_sha256)
        return {
            "schema_version": "voice_coach.native_reference_bank.v1",
            "status": self.status,
            "entry_count": len(self.entries),
            "speaker_count": self.speaker_count,
            "sentence_count": self.sentence_count,
            "minimum_speakers_per_sentence": self.minimum_speakers_per_sentence,
            "speaker_counts_by_sentence": {
                key: len(value) for key, value in sorted(by_sentence.items())
            },
            "contains_audio_bytes": False,
            "contains_transcript": False,
            "contains_raw_locator": False,
        }


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise NativeReferenceBankError(f"{field} must be a lowercase SHA-256")
    return value


def _sentence_key(record: NativeSpeechRecord, supplied: Mapping[str, str] | None) -> str:
    if supplied is not None and record.stable_id in supplied:
        return _sha256(supplied[record.stable_id], "sentence_key_sha256")
    token_bytes = ",".join(str(value) for value in record.text_tokens).encode("utf-8")
    return hashlib.sha256(token_bytes).hexdigest()


def build_native_reference_bank(
    records: Sequence[NativeSpeechRecord],
    *,
    speaker_ids_by_record: Mapping[str, str] | None = None,
    sentence_keys_by_record: Mapping[str, str] | None = None,
    style_by_record: Mapping[str, str] | None = None,
    minimum_speakers_per_sentence: int = 2,
    required_source_ids: Sequence[str] = ("kss",),
) -> NativeReferenceBank:
    """Build a metadata-only bank and require explicit speaker diversity."""

    if not records:
        raise NativeReferenceBankError("reference records must not be empty")
    if speaker_ids_by_record is None and not all(
        record.speaker_id_sha256 is not None for record in records
    ):
        raise NativeReferenceBankError(
            "speaker metadata is required; record IDs cannot stand in for speakers"
        )
    if (
        isinstance(minimum_speakers_per_sentence, bool)
        or not isinstance(minimum_speakers_per_sentence, int)
        or minimum_speakers_per_sentence < 2
    ):
        raise NativeReferenceBankError("minimum_speakers_per_sentence must be at least 2")
    allowed_sources = set(required_source_ids)
    if not allowed_sources or any(not isinstance(value, str) or not value for value in allowed_sources):
        raise NativeReferenceBankError("required_source_ids must be non-empty")
    entries: list[NativeReferenceEntry] = []
    for record in records:
        if record.source_id not in allowed_sources:
            continue
        speaker_id = (
            speaker_ids_by_record.get(record.stable_id)
            if speaker_ids_by_record is not None
            else record.speaker_id_sha256
        )
        entry = NativeReferenceEntry(
            record_id_sha256=_sha256(
                hashlib.sha256(record.stable_id.encode("utf-8")).hexdigest(),
                "record_id_sha256",
            ),
            source_id=record.source_id,
            speaker_id_sha256=_sha256(speaker_id, "speaker_id_sha256"),
            sentence_key_sha256=_sentence_key(record, sentence_keys_by_record),
            style=(style_by_record or {}).get(record.stable_id, "neutral"),
            duration_seconds=float(record.duration_seconds),
            quality_target=float(record.quality_target),
        )
        entries.append(entry)
    if not entries:
        raise NativeReferenceBankError("no records match the required native sources")
    by_sentence: defaultdict[str, set[str]] = defaultdict(set)
    for entry in entries:
        by_sentence[entry.sentence_key_sha256].add(entry.speaker_id_sha256)
    insufficient = {
        key: len(speakers)
        for key, speakers in by_sentence.items()
        if len(speakers) < minimum_speakers_per_sentence
    }
    if insufficient:
        raise NativeReferenceBankError(
            "native reference bank lacks the required speaker floor: "
            + json.dumps({"sentence_count": len(insufficient)}, sort_keys=True)
        )
    return NativeReferenceBank(
        entries=tuple(sorted(entries, key=lambda item: (item.sentence_key_sha256, item.speaker_id_sha256, item.record_id_sha256))),
        minimum_speakers_per_sentence=minimum_speakers_per_sentence,
        speaker_count=len({entry.speaker_id_sha256 for entry in entries}),
        sentence_count=len(by_sentence),
        status="native_multispeaker_bank_ready",
    )


def select_reference_entries(
    bank: NativeReferenceBank,
    *,
    sentence_key_sha256: str,
    count: int,
    seed: int,
) -> tuple[NativeReferenceEntry, ...]:
    """Select deterministic speaker-diverse entries without exposing locators."""

    _sha256(sentence_key_sha256, "sentence_key_sha256")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise NativeReferenceBankError("count must be positive")
    candidates = [
        entry for entry in bank.entries if entry.sentence_key_sha256 == sentence_key_sha256
    ]
    if len({entry.speaker_id_sha256 for entry in candidates}) < min(count, bank.minimum_speakers_per_sentence):
        raise NativeReferenceBankError("requested reference count exceeds speaker diversity")
    ordered = sorted(
        candidates,
        key=lambda entry: hashlib.sha256(
            f"{seed}:{entry.speaker_id_sha256}:{entry.record_id_sha256}".encode()
        ).hexdigest(),
    )
    selected: list[NativeReferenceEntry] = []
    seen: set[str] = set()
    for entry in ordered:
        if entry.speaker_id_sha256 not in seen:
            selected.append(entry)
            seen.add(entry.speaker_id_sha256)
        if len(selected) == count:
            return tuple(selected)
    raise NativeReferenceBankError("not enough distinct speakers for selection")


__all__ = [
    "NativeReferenceBank",
    "NativeReferenceBankError",
    "NativeReferenceEntry",
    "build_native_reference_bank",
    "select_reference_entries",
]
