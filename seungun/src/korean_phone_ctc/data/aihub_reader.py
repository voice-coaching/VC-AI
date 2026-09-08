"""Streaming readers for AI Hub 71490 pronunciation ZIP archives."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZipFile, ZipInfo

_ITEM_RE = re.compile(r"^\s*item\s*\[\d+\]\s*:\s*$")
_INTERVAL_RE = re.compile(r"^\s*intervals\s*\[\d+\]\s*:\s*$")
_ASSIGNMENT_RE = re.compile(r"^\s*(\w+)\s*=\s*(.*?)\s*$")


@dataclass(frozen=True)
class PhoneInterval:
    phone: str
    start: float
    end: float


@dataclass(frozen=True)
class ZipMember:
    archive: Path
    member: str
    stem: str
    size: int
    crc: int


@dataclass(frozen=True)
class AihubRecord:
    utterance_id: str
    speaker_id: str
    prompt: str
    recorded_time: float | None
    phones: tuple[PhoneInterval, ...]
    error_tags: tuple[Mapping[str, Any], ...]
    speaker_metadata: Mapping[str, Any]
    evaluation_metadata: Mapping[str, Any]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_json_record(payload: bytes, *, utterance_id: str) -> AihubRecord:
    data = json.loads(payload)
    if not isinstance(data, Mapping):
        raise ValueError("JSON root must be an object")
    recording = _mapping(data.get("RecordingMetadata"))
    phonemic = _mapping(recording.get("phonemic"))
    raw_phones = phonemic.get("phones", [])
    if not isinstance(raw_phones, list):
        raise ValueError("RecordingMetadata.phonemic.phones must be a list")

    phones: list[PhoneInterval] = []
    for item in raw_phones:
        phone = _mapping(item)
        label = phone.get("phone")
        start = _float_or_none(phone.get("start"))
        end = _float_or_none(phone.get("end"))
        if not isinstance(label, str) or start is None or end is None:
            raise ValueError(f"invalid phone entry: {item!r}")
        phones.append(PhoneInterval(label, start, end))

    raw_errors = phonemic.get("error_tags", [])
    errors = (
        tuple(_mapping(item) for item in raw_errors)
        if isinstance(raw_errors, list)
        else ()
    )
    return AihubRecord(
        utterance_id=utterance_id,
        speaker_id=str(data.get("UserID", "")),
        prompt=str(recording.get("prompt", "")),
        recorded_time=_float_or_none(recording.get("RecordedTime")),
        phones=tuple(phones),
        error_tags=errors,
        speaker_metadata=_mapping(data.get("SpeakerMetadata")),
        evaluation_metadata=_mapping(data.get("EvaluationMetadata")),
    )


def _praat_string(value: str) -> str:
    stripped = value.strip()
    if len(stripped) < 2 or not (stripped.startswith('"') and stripped.endswith('"')):
        raise ValueError(f"expected quoted Praat string, got {value!r}")
    return stripped[1:-1].replace('""', '"')


def parse_textgrid_tiers(text: str) -> dict[str, list[PhoneInterval]]:
    tiers: dict[str, list[PhoneInterval]] = {}
    tier_name: str | None = None
    interval: dict[str, str] | None = None

    def flush() -> None:
        nonlocal interval
        if interval is None:
            return
        if tier_name is None or not {"xmin", "xmax", "text"} <= interval.keys():
            raise ValueError("incomplete TextGrid interval")
        tiers.setdefault(tier_name, []).append(
            PhoneInterval(
                _praat_string(interval["text"]),
                float(interval["xmin"]),
                float(interval["xmax"]),
            )
        )
        interval = None

    for line in text.splitlines():
        if _ITEM_RE.match(line):
            flush()
            tier_name = None
            continue
        if _INTERVAL_RE.match(line):
            flush()
            interval = {}
            continue
        match = _ASSIGNMENT_RE.match(line)
        if match is None:
            continue
        key, value = match.groups()
        if interval is not None and key in {"xmin", "xmax", "text"}:
            interval[key] = value
        elif interval is None and key == "name":
            tier_name = _praat_string(value)
    flush()
    return tiers


def iter_members(archive: Path, suffix: str) -> Iterator[ZipMember]:
    with ZipFile(archive) as zipped:
        for info in zipped.infolist():
            if info.is_dir() or not info.filename.casefold().endswith(
                suffix.casefold()
            ):
                continue
            yield member_from_info(archive, info)


def member_from_info(archive: Path, info: ZipInfo) -> ZipMember:
    return ZipMember(
        archive=archive,
        member=info.filename,
        stem=Path(info.filename).stem,
        size=info.file_size,
        crc=info.CRC,
    )
