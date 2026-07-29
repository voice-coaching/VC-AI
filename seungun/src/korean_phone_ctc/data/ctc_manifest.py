"""Build conservative observed-phone records for CTC training."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .textgrid_target_audit import (
    MODERN_KOREAN_PHONE_INVENTORY,
    split_known_phone_sequence,
)

SOURCE_SPLIT_MAP = {
    "train": "train",
    "stack": "train",
    "calibration": "train",
    "dev": "dev",
    "locked_test": "test",
}


@dataclass(frozen=True)
class PreparedRecord:
    record: dict[str, Any] | None
    exclusion_reason: str | None


def normalize_observed_intervals(
    intervals: Sequence[Mapping[str, Any]],
) -> tuple[list[str] | None, str | None]:
    """Normalize a TextGrid phone tier without guessing unknown labels."""

    phones: list[str] = []
    for interval in intervals:
        label = interval.get("label")
        if not isinstance(label, str):
            return None, "invalid_interval_label"
        tokens, _ = split_known_phone_sequence(label, MODERN_KOREAN_PHONE_INVENTORY)
        if tokens is None:
            return None, "unresolved_textgrid_label"
        phones.extend(tokens)
    if not phones:
        return None, "empty_observed_phone_sequence"
    return phones, None


def prepare_ctc_record(
    enriched: Mapping[str, Any],
    raw: Mapping[str, Any],
    *,
    max_duration_seconds: float,
) -> PreparedRecord:
    """Create one portable manifest record or return an explicit rejection."""

    if enriched.get("id") != raw.get("id"):
        raise ValueError(
            f"manifest order mismatch: {enriched.get('id')} != {raw.get('id')}"
        )
    flags = enriched.get("qc_flags") or []
    if flags:
        return PreparedRecord(None, "source_qc_flag")
    duration = enriched.get("duration_seconds")
    if not isinstance(duration, int | float) or duration <= 0:
        return PreparedRecord(None, "invalid_audio_duration")
    if duration > max_duration_seconds:
        return PreparedRecord(None, "audio_too_long")
    source_split = enriched.get("split")
    split = SOURCE_SPLIT_MAP.get(str(source_split))
    if split is None:
        return PreparedRecord(None, "unknown_source_split")
    intervals = enriched.get("realized_phone_intervals") or []
    phones, error = normalize_observed_intervals(intervals)
    if error is not None or phones is None:
        return PreparedRecord(None, error)

    json_metadata = raw.get("json_metadata") or {}
    recording = json_metadata.get("RecordingMetadata") or {}
    speaker = json_metadata.get("SpeakerMetadata") or {}
    evaluation = json_metadata.get("EvaluationMetadata") or {}
    annotations = raw.get("annotation_counts") or {}
    source_paths = raw.get("source_paths") or {}
    record = {
        "utterance_id": enriched.get("id"),
        "speaker_id": enriched.get("speaker_id"),
        "split": split,
        "source_split": source_split,
        "audio_path": source_paths.get("wav"),
        "duration_seconds": float(duration),
        "sample_rate_hz": enriched.get("sample_rate_hz"),
        "pcm_sha256": enriched.get("pcm_sha256"),
        "prompt": recording.get("prompt", ""),
        "phones": phones,
        "error_presence": bool(enriched.get("error_presence")),
        "error_tag_count": int(annotations.get("error_tags", 0)),
        "language": speaker.get("language", "<missing>"),
        "nationality": speaker.get("nationality", "<missing>"),
        "gender": speaker.get("gender", "<missing>"),
        "proficiency": speaker.get("proficiency", "<missing>"),
        "pronunciation_score": evaluation.get("PronunProfEval"),
    }
    if not isinstance(record["audio_path"], str) or not record["audio_path"]:
        return PreparedRecord(None, "missing_audio_path")
    if not isinstance(record["speaker_id"], str) or not record["speaker_id"]:
        return PreparedRecord(None, "missing_speaker_id")
    if not isinstance(record["pcm_sha256"], str) or not record["pcm_sha256"]:
        return PreparedRecord(None, "missing_pcm_hash")
    return PreparedRecord(record, None)
