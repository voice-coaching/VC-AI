from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import NonNegativeFiniteFloat, PositiveFiniteFloat, Sha256


AIHUB_UNICODE_NFC_V1 = "AIHUB_UNICODE_NFC_V1"
AIHUB_COLLAPSE_WHITESPACE_V1 = "AIHUB_COLLAPSE_WHITESPACE_V1"
AIHUB_PARALLEL_SPOKEN_FORM_V1 = "AIHUB_PARALLEL_SPOKEN_FORM_V1"
_PARALLEL_PATTERN = re.compile(r"\(([^()]*)\)/\(([^()/]*)\)")


class NormalizationStatus(str, Enum):
    UNCHANGED = "unchanged"
    NORMALIZED = "normalized"
    UNSUPPORTED = "unsupported"


class AnchorDataErrorCode(str, Enum):
    MISSING_AUDIO = "missing_audio"
    MISSING_LABEL = "missing_label"
    DUPLICATE_STEM = "duplicate_stem"
    JSON_PARSE_FAILED = "json_parse_failed"
    REQUIRED_FIELD_MISSING = "required_field_missing"
    EMPTY_SCRIPT = "empty_script"
    UNSUPPORTED_TRANSCRIPTION = "unsupported_transcription"


class AnchorDataFailure(Exception):
    def __init__(
        self,
        code: AnchorDataErrorCode,
        message: str,
        *,
        sample_id: str | None = None,
        path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.sample_id = sample_id
        self.path = path


class AnchorNormalization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_script: str
    normalized_script: str | None
    status: NormalizationStatus
    rules_applied: tuple[str, ...] = ()


class AnchorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str = Field(min_length=1)
    script_id: str = Field(min_length=1)
    audio_path: Path
    label_path: Path
    audio_sha256: Sha256
    label_sha256: Sha256
    original_script: str = Field(min_length=1)
    normalized_script: str | None
    speaker_id: str = Field(min_length=1)
    age: str = Field(min_length=1)
    sex: str = Field(min_length=1)
    job: str = Field(min_length=1)
    utterance_start: NonNegativeFiniteFloat
    utterance_end: NonNegativeFiniteFloat
    audio_duration: PositiveFiniteFloat
    normalization_status: NormalizationStatus
    normalization_rules_applied: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_times_and_normalization(self) -> AnchorRecord:
        if self.utterance_start > self.utterance_end:
            raise ValueError("utterance_start must not exceed utterance_end")
        if self.utterance_end > self.audio_duration:
            raise ValueError("utterance_end must not exceed audio_duration")
        if (
            self.normalization_status is NormalizationStatus.UNSUPPORTED
            and self.normalized_script is not None
        ):
            raise ValueError("unsupported scripts must not have normalized_script")
        if (
            self.normalization_status is not NormalizationStatus.UNSUPPORTED
            and self.normalized_script is None
        ):
            raise ValueError("supported scripts require normalized_script")
        return self


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_anchor_script(script: str) -> AnchorNormalization:
    original = script
    rules: list[str] = []
    normalized = unicodedata.normalize("NFC", script)
    if normalized != script:
        rules.append(AIHUB_UNICODE_NFC_V1)
    collapsed = _collapse_whitespace(normalized)
    if collapsed != normalized:
        rules.append(AIHUB_COLLAPSE_WHITESPACE_V1)
    normalized = collapsed

    matches = list(_PARALLEL_PATTERN.finditer(normalized))
    slash_positions = (
        index for index, character in enumerate(normalized) if character == "/"
    )
    unsupported = any(
        not any(match.start() <= index < match.end() for match in matches)
        for index in slash_positions
    ) or any(
        not match.group(1).strip()
        or not match.group(2).strip()
        or (match.start() > 0 and normalized[match.start() - 1] == "(")
        or (match.end() < len(normalized) and normalized[match.end()] == ")")
        for match in matches
    )
    if unsupported:
        return AnchorNormalization(
            original_script=original,
            normalized_script=None,
            status=NormalizationStatus.UNSUPPORTED,
            rules_applied=(),
        )

    if matches:
        normalized = _PARALLEL_PATTERN.sub(lambda match: match.group(2), normalized)
        rules.append(AIHUB_PARALLEL_SPOKEN_FORM_V1)
    return AnchorNormalization(
        original_script=original,
        normalized_script=normalized,
        status=(
            NormalizationStatus.NORMALIZED if rules else NormalizationStatus.UNCHANGED
        ),
        rules_applied=tuple(rules),
    )


def _category_root(root: Path, prefix: str, code: AnchorDataErrorCode) -> Path:
    matches = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and path.name.startswith(prefix)
    )
    if len(matches) != 1:
        raise AnchorDataFailure(code, f"{prefix} category directory is unavailable")
    return matches[0]


def _index_files(
    category_root: Path,
    suffix: str,
) -> tuple[dict[Path, Path], dict[str, list[Path]]]:
    by_relative_path: dict[Path, Path] = {}
    by_stem: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(category_root.rglob(f"*{suffix}")):
        relative = path.relative_to(category_root).with_suffix("")
        by_relative_path[relative] = path
        by_stem[path.stem].append(path)
    return by_relative_path, by_stem


def _reject_duplicate_stems(index: dict[str, list[Path]]) -> None:
    for stem, paths in sorted(index.items()):
        if len(paths) > 1:
            raise AnchorDataFailure(
                AnchorDataErrorCode.DUPLICATE_STEM,
                "duplicate sample stem",
                sample_id=stem,
                path=paths[0],
            )


def _mapping(value: Any, field: str, sample_id: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnchorDataFailure(
            AnchorDataErrorCode.REQUIRED_FIELD_MISSING,
            f"required mapping is missing: {field}",
            sample_id=sample_id,
            path=path,
        )
    return value


def _required_string(
    mapping: dict[str, Any],
    field: str,
    sample_id: str,
    path: Path,
) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AnchorDataFailure(
            AnchorDataErrorCode.REQUIRED_FIELD_MISSING,
            f"required string is missing: {field}",
            sample_id=sample_id,
            path=path,
        )
    return value


def _required_float(
    mapping: dict[str, Any],
    field: str,
    sample_id: str,
    path: Path,
) -> float:
    value = mapping.get(field)
    if not isinstance(value, (str, int, float)):
        raise AnchorDataFailure(
            AnchorDataErrorCode.REQUIRED_FIELD_MISSING,
            f"required numeric field is missing: {field}",
            sample_id=sample_id,
            path=path,
        )
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AnchorDataFailure(
            AnchorDataErrorCode.REQUIRED_FIELD_MISSING,
            f"required numeric field is missing: {field}",
            sample_id=sample_id,
            path=path,
        ) from exc
    return parsed


def _read_record(audio_path: Path, label_path: Path) -> AnchorRecord:
    sample_id = label_path.stem
    try:
        payload: Any = json.loads(label_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnchorDataFailure(
            AnchorDataErrorCode.JSON_PARSE_FAILED,
            "label JSON could not be parsed",
            sample_id=sample_id,
            path=label_path,
        ) from exc
    root = _mapping(payload, "root", sample_id, label_path)
    script = _mapping(root.get("script"), "script", sample_id, label_path)
    speaker = _mapping(root.get("speaker"), "speaker", sample_id, label_path)
    file_information = _mapping(
        root.get("file_information"),
        "file_information",
        sample_id,
        label_path,
    )
    original_script = script.get("text")
    if not isinstance(original_script, str):
        raise AnchorDataFailure(
            AnchorDataErrorCode.REQUIRED_FIELD_MISSING,
            "required string is missing: text",
            sample_id=sample_id,
            path=label_path,
        )
    if not _collapse_whitespace(original_script):
        raise AnchorDataFailure(
            AnchorDataErrorCode.EMPTY_SCRIPT,
            "script text is empty",
            sample_id=sample_id,
            path=label_path,
        )
    normalization = normalize_anchor_script(original_script)
    try:
        return AnchorRecord(
            sample_id=sample_id,
            script_id=_required_string(script, "id", sample_id, label_path),
            audio_path=audio_path.resolve(strict=True),
            label_path=label_path.resolve(strict=True),
            audio_sha256=_sha256(audio_path),
            label_sha256=_sha256(label_path),
            original_script=original_script,
            normalized_script=normalization.normalized_script,
            speaker_id=_required_string(speaker, "id", sample_id, label_path),
            age=_required_string(speaker, "age", sample_id, label_path),
            sex=_required_string(speaker, "sex", sample_id, label_path),
            job=_required_string(speaker, "job", sample_id, label_path),
            utterance_start=_required_float(
                file_information,
                "utterance_start",
                sample_id,
                label_path,
            ),
            utterance_end=_required_float(
                file_information,
                "utterance_end",
                sample_id,
                label_path,
            ),
            audio_duration=_required_float(
                file_information,
                "audio_duration",
                sample_id,
                label_path,
            ),
            normalization_status=normalization.status,
            normalization_rules_applied=normalization.rules_applied,
        )
    except AnchorDataFailure:
        raise
    except Exception as exc:
        raise AnchorDataFailure(
            AnchorDataErrorCode.REQUIRED_FIELD_MISSING,
            "required label field is invalid",
            sample_id=sample_id,
            path=label_path,
        ) from exc


def load_anchor_records(dataset_root: Path) -> list[AnchorRecord]:
    root = dataset_root.expanduser().resolve(strict=True)
    audio_root = _category_root(root, "01.", AnchorDataErrorCode.MISSING_AUDIO)
    label_root = _category_root(root, "02.", AnchorDataErrorCode.MISSING_LABEL)
    audio_by_path, audio_by_stem = _index_files(audio_root, ".wav")
    label_by_path, label_by_stem = _index_files(label_root, ".json")
    _reject_duplicate_stems(audio_by_stem)
    _reject_duplicate_stems(label_by_stem)

    missing_audio = sorted(set(label_by_path) - set(audio_by_path))
    if missing_audio:
        key = missing_audio[0]
        raise AnchorDataFailure(
            AnchorDataErrorCode.MISSING_AUDIO,
            "matching WAV is missing",
            sample_id=key.name,
            path=label_by_path[key],
        )
    missing_label = sorted(set(audio_by_path) - set(label_by_path))
    if missing_label:
        key = missing_label[0]
        raise AnchorDataFailure(
            AnchorDataErrorCode.MISSING_LABEL,
            "matching JSON label is missing",
            sample_id=key.name,
            path=audio_by_path[key],
        )
    return [
        _read_record(audio_by_path[key], label_by_path[key])
        for key in sorted(audio_by_path)
    ]


def select_stratified_smoke_records(
    records: list[AnchorRecord],
) -> list[AnchorRecord]:
    target_speakers = ("SPK014", "SPK035", "SPK064", "SPK082")
    by_speaker = {
        speaker: sorted(
            (record for record in records if record.speaker_id == speaker),
            key=lambda record: record.sample_id,
        )
        for speaker in target_speakers
    }
    if any(len(items) < 3 for items in by_speaker.values()):
        raise ValueError("each target speaker requires at least three records")

    shared_pairs = sorted(
        (
            (first, second)
            for first in by_speaker["SPK014"]
            for second in by_speaker["SPK064"]
            if first.original_script == second.original_script
        ),
        key=lambda pair: (pair[0].sample_id, pair[1].sample_id),
    )
    if not shared_pairs:
        raise ValueError("SPK014 and SPK064 require a shared-script pair")
    selected_by_speaker: dict[str, list[AnchorRecord]] = {
        speaker: [] for speaker in target_speakers
    }
    first, second = shared_pairs[0]
    selected_by_speaker["SPK014"].append(first)
    selected_by_speaker["SPK064"].append(second)

    priority = (
        NormalizationStatus.UNSUPPORTED,
        NormalizationStatus.NORMALIZED,
        NormalizationStatus.UNCHANGED,
    )
    for speaker in target_speakers:
        selected = selected_by_speaker[speaker]
        for status in priority:
            for record in by_speaker[speaker]:
                if len(selected) >= 3:
                    break
                if record not in selected and record.normalization_status is status:
                    selected.append(record)
                    break
        for record in by_speaker[speaker]:
            if len(selected) >= 3:
                break
            if record not in selected:
                selected.append(record)
    return [
        record
        for speaker in target_speakers
        for record in sorted(
            selected_by_speaker[speaker],
            key=lambda item: item.sample_id,
        )
    ]
