#!/usr/bin/env python3
"""Run a privacy-minimal g2pK/AI Hub expected-phone dev smoke.

The runner streams the complete manifest, opens raw JSON only for ``dev``
rows, fixes a sample from every dev row before inspecting sample eligibility,
and runs g2pK on at most 16 rows.  It never reads audio or TextGrid data and
never writes transcripts, phone sequences, row identifiers, speaker
identifiers, or source paths.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
from typing import Protocol, cast
import unicodedata
from unittest import mock

from voice_coach.phonology.expected_phone_agreement import (
    ExpectedPhoneAgreementError,
    ExpectedPhoneAgreementResult,
    evaluate_expected_phone_agreement,
)
from voice_coach.phonology.g2pk_expected import (
    EXPECTED_G2PK_VERSION,
    G2PKExpectedPhoneError,
    G2PKExpectedPhones,
    WordBoundaryPolicy,
    generate_g2pk_expected_phones,
)
from voice_coach.phonology.phones import KOREAN_COMPATIBILITY_JAMO


REPORT_SCHEMA_VERSION = "voice_coach.g2pk_aihub71490_dev_smoke.v2"
EXPECTED_MANIFEST_SCHEMA_VERSION = "aihub71490.raw.v2"
EXPECTED_DATASET_ID = "71490"
FIXED_SPLIT = "dev"
FIXED_SEED = 42
MAXIMUM_SAMPLE_SIZE = 16
SAMPLE_POLICY_ID = "sha256_domain_seed_row_identity_target_reference_v1"
SAMPLE_DOMAIN = b"aihub71490-g2pk-dev-smoke-v1\0"
PUNCTUATION_POLICY_ID = "fixed_ascii_sentence_punctuation_v1"
BOUNDARY_POLICY_ID = "reject_all_noncanonical_annotation_tokens_v1"
WORD_BOUNDARY_POLICY: WordBoundaryPolicy = "drop"
FIXED_ALLOWED_PUNCTUATION = tuple(
    sorted({"!", '"', "'", "(", ")", ",", "-", ".", ":", ";", "?"})
)
_CANONICAL_PHONE_SET = frozenset(KOREAN_COMPATIBILITY_JAMO)
_EXPECTED_TARGET_FORMAT = "aihub_pronunciation_json"
_EXPECTED_TARGET_POINTER = "/RecordingMetadata/phonemic/phones"
_WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
_IMPLEMENTATION_PATHS = {
    "runner": Path(__file__).resolve(),
    "agreement_source": (
        _WORKSPACE_ROOT
        / "src"
        / "voice_coach"
        / "phonology"
        / "expected_phone_agreement.py"
    ),
    "g2pk_adapter_source": (
        _WORKSPACE_ROOT / "src" / "voice_coach" / "phonology" / "g2pk_expected.py"
    ),
    "phone_inventory_source": (
        _WORKSPACE_ROOT / "src" / "voice_coach" / "phonology" / "phones.py"
    ),
    "g2pk_environment_lock": (
        _WORKSPACE_ROOT / "requirements" / "paper-g2pk-linux-aarch64.lock.txt"
    ),
}


class DevSmokeError(ValueError):
    """Raised for a safe configuration or aggregate dev-audit failure."""


class _ExpectedPhoneGenerator(Protocol):
    def __call__(
        self,
        text: object,
        *,
        descriptive: bool,
        group_vowels: bool,
        word_boundary_policy: WordBoundaryPolicy,
        allowed_punctuation: tuple[str, ...],
        maximum_codepoints: int,
        nltk_data_root: str | Path | None = None,
    ) -> G2PKExpectedPhones: ...


@dataclass(frozen=True, slots=True)
class SmokeConfiguration:
    """Validated caller-explicit paths, digests, policies, and resource caps."""

    manifest_path: Path
    expected_manifest_sha256: str
    data_config_path: Path
    expected_data_config_sha256: str
    expected_runner_sha256: str
    expected_agreement_source_sha256: str
    expected_g2pk_adapter_source_sha256: str
    expected_phone_inventory_source_sha256: str
    expected_g2pk_environment_lock_sha256: str
    dataset_root: Path
    nltk_data_root: Path
    output_path: Path
    seed: int
    sample_size: int
    descriptive: bool
    group_vowels: bool
    punctuation_policy: str
    word_boundary_policy: WordBoundaryPolicy
    maximum_codepoints: int
    max_alignment_cells: int


@dataclass(frozen=True, slots=True)
class _SelectedRow:
    """Bounded in-memory row state that is never serialized."""

    selection_score: bytes
    transcript: str | None
    expected_phone_tokens: tuple[str, ...] | None
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class _AuditResult:
    """Aggregate audit state plus the already-fixed sample."""

    counters: Counter[str]
    manifest_sha256: str
    data_config_sha256: str
    dev_provenance_sha256: str
    dev_raw_content_aggregate_sha256: str
    punctuation_vocabulary_count: int
    punctuation_vocabulary_sha256: str
    noncanonical_vocabulary_count: int
    noncanonical_vocabulary_sha256: str
    maximum_transcript_codepoints_observed: int
    sample_selection_sha256: str
    dev_rejection_reason_counts: Counter[str]
    dev_rejection_condition_counts: Counter[str]
    selected_rows: tuple[_SelectedRow, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise DevSmokeError("a required provenance file could not be read") from None
    return digest.hexdigest()


def _validate_sha256(value: object, *, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DevSmokeError(f"{field_name} must be a lowercase SHA-256 digest")
    return cast(str, value)


def _real_regular_file(path: object, *, field_name: str) -> Path:
    if not isinstance(path, Path):
        raise DevSmokeError(f"{field_name} must be a Path")
    candidate = path
    try:
        resolved = candidate.resolve(strict=True)
        information = candidate.lstat()
    except (OSError, RuntimeError):
        raise DevSmokeError(f"{field_name} is unavailable") from None
    if (
        resolved != candidate
        or candidate.is_symlink()
        or not stat.S_ISREG(information.st_mode)
    ):
        raise DevSmokeError(f"{field_name} must be a real regular file")
    return candidate


def _real_absolute_directory(path: object, *, field_name: str) -> Path:
    if not isinstance(path, Path):
        raise DevSmokeError(f"{field_name} must be a Path")
    candidate = path
    if not candidate.is_absolute():
        raise DevSmokeError(f"{field_name} must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise DevSmokeError(f"{field_name} is unavailable") from None
    if resolved != candidate or candidate.is_symlink() or not candidate.is_dir():
        raise DevSmokeError(f"{field_name} must be a real directory")
    return candidate


def _positive_integer(
    value: object,
    *,
    field_name: str,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise DevSmokeError(f"{field_name} must be a positive integer")
    normalized = cast(int, value)
    if normalized <= 0 or (maximum is not None and normalized > maximum):
        raise DevSmokeError(f"{field_name} is outside the supported resource bound")
    return normalized


def _validate_configuration(value: object) -> SmokeConfiguration:
    if type(value) is not SmokeConfiguration:
        raise DevSmokeError("configuration must be an exact SmokeConfiguration")
    configuration = cast(SmokeConfiguration, value)
    manifest_path = _real_regular_file(
        configuration.manifest_path,
        field_name="manifest_path",
    )
    data_config_path = _real_regular_file(
        configuration.data_config_path,
        field_name="data_config_path",
    )
    dataset_root = _real_absolute_directory(
        configuration.dataset_root,
        field_name="dataset_root",
    )
    nltk_data_root = _real_absolute_directory(
        configuration.nltk_data_root,
        field_name="nltk_data_root",
    )
    if configuration.output_path.exists() and configuration.output_path.is_symlink():
        raise DevSmokeError("output_path must not be a symlink")
    if configuration.output_path.name in {"", ".", ".."}:
        raise DevSmokeError("output_path must name an artifact file")
    if configuration.seed != FIXED_SEED:
        raise DevSmokeError("seed differs from the fixed dev-smoke contract")
    sample_size = _positive_integer(
        configuration.sample_size,
        field_name="sample_size",
        maximum=MAXIMUM_SAMPLE_SIZE,
    )
    if type(configuration.descriptive) is not bool:
        raise DevSmokeError("descriptive must be an explicit boolean")
    if configuration.descriptive:
        raise DevSmokeError("descriptive differs from the fixed smoke policy")
    if type(configuration.group_vowels) is not bool:
        raise DevSmokeError("group_vowels must be an explicit boolean")
    if configuration.group_vowels:
        raise DevSmokeError("group_vowels differs from the fixed smoke policy")
    if configuration.punctuation_policy != PUNCTUATION_POLICY_ID:
        raise DevSmokeError("punctuation_policy differs from the fixed contract")
    if configuration.word_boundary_policy != WORD_BOUNDARY_POLICY:
        raise DevSmokeError("word_boundary_policy differs from the fixed contract")
    maximum_codepoints = _positive_integer(
        configuration.maximum_codepoints,
        field_name="maximum_codepoints",
        maximum=1_000_000,
    )
    max_alignment_cells = _positive_integer(
        configuration.max_alignment_cells,
        field_name="max_alignment_cells",
        maximum=1_000_000,
    )
    return SmokeConfiguration(
        manifest_path=manifest_path,
        expected_manifest_sha256=_validate_sha256(
            configuration.expected_manifest_sha256,
            field_name="expected_manifest_sha256",
        ),
        data_config_path=data_config_path,
        expected_data_config_sha256=_validate_sha256(
            configuration.expected_data_config_sha256,
            field_name="expected_data_config_sha256",
        ),
        expected_runner_sha256=_validate_sha256(
            configuration.expected_runner_sha256,
            field_name="expected_runner_sha256",
        ),
        expected_agreement_source_sha256=_validate_sha256(
            configuration.expected_agreement_source_sha256,
            field_name="expected_agreement_source_sha256",
        ),
        expected_g2pk_adapter_source_sha256=_validate_sha256(
            configuration.expected_g2pk_adapter_source_sha256,
            field_name="expected_g2pk_adapter_source_sha256",
        ),
        expected_phone_inventory_source_sha256=_validate_sha256(
            configuration.expected_phone_inventory_source_sha256,
            field_name="expected_phone_inventory_source_sha256",
        ),
        expected_g2pk_environment_lock_sha256=_validate_sha256(
            configuration.expected_g2pk_environment_lock_sha256,
            field_name="expected_g2pk_environment_lock_sha256",
        ),
        dataset_root=dataset_root,
        nltk_data_root=nltk_data_root,
        output_path=configuration.output_path,
        seed=configuration.seed,
        sample_size=sample_size,
        descriptive=configuration.descriptive,
        group_vowels=configuration.group_vowels,
        punctuation_policy=configuration.punctuation_policy,
        word_boundary_policy=cast(
            WordBoundaryPolicy,
            configuration.word_boundary_policy,
        ),
        maximum_codepoints=maximum_codepoints,
        max_alignment_cells=max_alignment_cells,
    )


def _expected_implementation_sha256(
    configuration: SmokeConfiguration,
) -> dict[str, str]:
    return {
        "runner": configuration.expected_runner_sha256,
        "agreement_source": configuration.expected_agreement_source_sha256,
        "g2pk_adapter_source": (configuration.expected_g2pk_adapter_source_sha256),
        "phone_inventory_source": (
            configuration.expected_phone_inventory_source_sha256
        ),
        "g2pk_environment_lock": (configuration.expected_g2pk_environment_lock_sha256),
    }


def _capture_implementation_sha256() -> dict[str, str]:
    return {
        role: _sha256_file(_real_regular_file(path, field_name=f"{role}_path"))
        for role, path in _IMPLEMENTATION_PATHS.items()
    }


def _verify_implementation_sha256(
    configuration: SmokeConfiguration,
) -> dict[str, str]:
    expected = _expected_implementation_sha256(configuration)
    actual = _capture_implementation_sha256()
    if actual != expected:
        raise DevSmokeError(
            "implementation or environment-lock digest differs from the pin"
        )
    return actual


def _update_framed(digest: hashlib._Hash, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, byteorder="big", signed=False))
    digest.update(value)


def _selection_score(
    *,
    seed: int,
    row_identity: str,
    target_reference: str,
) -> bytes:
    digest = hashlib.sha256()
    digest.update(SAMPLE_DOMAIN)
    digest.update(str(seed).encode("ascii"))
    digest.update(b"\0")
    digest.update(row_identity.encode("utf-8"))
    digest.update(b"\0")
    digest.update(target_reference.encode("utf-8"))
    return digest.digest()


def _safe_dataset_file(dataset_root: Path, relative_value: object) -> Path:
    if type(relative_value) is not str:
        raise DevSmokeError("a dev source reference has an invalid shape")
    relative = Path(cast(str, relative_value))
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or relative.suffix.lower() != ".json"
    ):
        raise DevSmokeError("a dev source reference violates the path contract")
    candidate = dataset_root / relative
    try:
        resolved = candidate.resolve(strict=True)
        information = candidate.lstat()
    except (OSError, RuntimeError):
        raise DevSmokeError("a dev raw JSON source is unavailable") from None
    if (
        resolved != candidate
        or not resolved.is_relative_to(dataset_root)
        or candidate.is_symlink()
        or not stat.S_ISREG(information.st_mode)
    ):
        raise DevSmokeError("a dev raw JSON source violates the file contract")
    return candidate


def _load_raw_json(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        raw_bytes = path.read_bytes()
        value = json.loads(raw_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise DevSmokeError("a dev raw JSON source could not be parsed") from None
    if type(value) is not dict:
        raise DevSmokeError("a dev raw JSON source has an invalid root shape")
    return cast(dict[str, object], value), raw_bytes


def _extract_raw_expected_fields(
    payload: dict[str, object],
    *,
    maximum_codepoints: int,
    counters: Counter[str],
    punctuation_vocabulary: set[str],
    noncanonical_vocabulary: set[str],
    rejection_condition_counts: Counter[str],
) -> tuple[str | None, tuple[str, ...] | None, str | None, int]:
    recording = payload.get("RecordingMetadata")
    if type(recording) is not dict:
        rejection_condition_counts["raw_recording_shape"] += 1
        return None, None, "raw_recording_shape", 0
    recording_fields = cast(dict[str, object], recording)
    rejection_reasons: list[str] = []

    def reject(reason: str) -> None:
        if reason not in rejection_reasons:
            rejection_reasons.append(reason)
            rejection_condition_counts[reason] += 1

    transcript = recording_fields.get("prompt")
    if type(transcript) is not str:
        transcript_value: str | None = None
        transcript_codepoints = 0
        reject("transcript_shape")
    else:
        transcript_value = cast(str, transcript)
        transcript_codepoints = len(transcript_value)
        if (
            not transcript_value
            or transcript_value != transcript_value.strip()
            or unicodedata.normalize("NFC", transcript_value) != transcript_value
            or any(
                unicodedata.category(character) in {"Cc", "Cs", "Co"}
                for character in transcript_value
            )
        ):
            reject("transcript_contract")

        row_punctuation = {
            character
            for character in transcript_value
            if unicodedata.category(character).startswith("P")
        }
        punctuation_vocabulary.update(row_punctuation)
        counters["dev_transcript_punctuation_occurrence_count"] += sum(
            unicodedata.category(character).startswith("P")
            for character in transcript_value
        )
        if row_punctuation:
            counters["dev_transcript_with_punctuation_count"] += 1
        if not row_punctuation <= frozenset(FIXED_ALLOWED_PUNCTUATION):
            counters["dev_transcript_outside_punctuation_policy_count"] += 1
            reject("punctuation_outside_fixed_policy")
        if len(transcript_value) > maximum_codepoints:
            counters["dev_transcript_over_resource_cap_count"] += 1
            reject("transcript_over_resource_cap")

    phonemic = recording_fields.get("phonemic")
    if type(phonemic) is not dict:
        reject("phonemic_shape")
        return (
            transcript_value,
            None,
            rejection_reasons[0],
            transcript_codepoints,
        )
    phone_entries = cast(dict[str, object], phonemic).get("phones")
    if type(phone_entries) is not list or not phone_entries:
        reject("expected_phone_sequence_shape")
        return (
            transcript_value,
            None,
            rejection_reasons[0],
            transcript_codepoints,
        )

    tokens: list[str] = []
    contains_noncanonical = False
    for entry in cast(list[object], phone_entries):
        if type(entry) is not dict:
            reject("expected_phone_entry_shape")
            return (
                transcript_value,
                None,
                rejection_reasons[0],
                transcript_codepoints,
            )
        token = cast(dict[str, object], entry).get("phone")
        if type(token) is not str:
            reject("expected_phone_entry_shape")
            return (
                transcript_value,
                None,
                rejection_reasons[0],
                transcript_codepoints,
            )
        token_value = cast(str, token)
        counters["dev_annotation_phone_occurrence_count"] += 1
        if token_value in _CANONICAL_PHONE_SET:
            counters["dev_canonical_phone_occurrence_count"] += 1
        else:
            contains_noncanonical = True
            counters["dev_noncanonical_phone_occurrence_count"] += 1
            if token_value == "sil":
                counters["dev_exact_sil_occurrence_count"] += 1
            noncanonical_vocabulary.add(token_value)
        tokens.append(token_value)

    if contains_noncanonical:
        counters["dev_row_with_noncanonical_phone_count"] += 1
        reject("noncanonical_annotation_token")
    return (
        transcript_value,
        tuple(tokens),
        rejection_reasons[0] if rejection_reasons else None,
        transcript_codepoints,
    )


def _push_sample_candidate(
    heap: list[tuple[int, int, _SelectedRow]],
    *,
    sample_size: int,
    line_number: int,
    candidate: _SelectedRow,
) -> None:
    score_integer = int.from_bytes(
        candidate.selection_score,
        byteorder="big",
        signed=False,
    )
    item = (-score_integer, -line_number, candidate)
    if len(heap) < sample_size:
        heapq.heappush(heap, item)
        return
    if item > heap[0]:
        heapq.heapreplace(heap, item)


def _digest_vocabulary(values: set[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(values):
        _update_framed(digest, value.encode("utf-8"))
    return digest.hexdigest()


def _audit_dev_and_fix_sample(
    configuration: SmokeConfiguration,
) -> _AuditResult:
    manifest_sha256 = _sha256_file(configuration.manifest_path)
    if manifest_sha256 != configuration.expected_manifest_sha256:
        raise DevSmokeError("manifest digest differs from the pinned provenance")
    data_config_sha256 = _sha256_file(configuration.data_config_path)
    if data_config_sha256 != configuration.expected_data_config_sha256:
        raise DevSmokeError("data-config digest differs from the pinned provenance")

    counters: Counter[str] = Counter()
    dev_provenance = hashlib.sha256()
    dev_raw_content = hashlib.sha256()
    punctuation_vocabulary: set[str] = set()
    noncanonical_vocabulary: set[str] = set()
    identity_digests: set[bytes] = set()
    target_reference_digests: set[bytes] = set()
    sample_heap: list[tuple[int, int, _SelectedRow]] = []
    dev_rejection_reason_counts: Counter[str] = Counter()
    dev_rejection_condition_counts: Counter[str] = Counter()
    maximum_transcript_codepoints = 0

    try:
        handle = configuration.manifest_path.open("r", encoding="utf-8")
    except OSError:
        raise DevSmokeError("manifest could not be opened for the dev audit") from None

    with handle:
        for line_number, line in enumerate(handle, start=1):
            counters["manifest_row_count"] += 1
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                counters["manifest_parse_error_count"] += 1
                continue
            if type(row) is not dict:
                counters["manifest_non_object_row_count"] += 1
                continue
            row_fields = cast(dict[str, object], row)
            if row_fields.get("split") != FIXED_SPLIT:
                counters["non_dev_row_skipped_without_source_access_count"] += 1
                continue

            counters["dev_row_count"] += 1
            if row_fields.get("schema_version") != EXPECTED_MANIFEST_SCHEMA_VERSION:
                counters["dev_schema_contract_error_count"] += 1
            if row_fields.get("dataset_id") != EXPECTED_DATASET_ID:
                counters["dev_dataset_contract_error_count"] += 1

            row_identity = row_fields.get("id")
            speaker_identity = row_fields.get("speaker_id")
            if type(row_identity) is not str or not row_identity:
                counters["dev_identity_shape_error_count"] += 1
                continue
            if type(speaker_identity) is not str or not speaker_identity:
                counters["dev_speaker_shape_error_count"] += 1
            identity_value = cast(str, row_identity)
            identity_digest = hashlib.sha256(identity_value.encode("utf-8")).digest()
            if identity_digest in identity_digests:
                counters["dev_duplicate_identity_count"] += 1
            identity_digests.add(identity_digest)

            phone_sources = row_fields.get("phone_sources")
            target = (
                cast(dict[str, object], phone_sources).get("target")
                if type(phone_sources) is dict
                else None
            )
            if type(target) is not dict:
                counters["dev_expected_source_contract_error_count"] += 1
                continue
            target_fields = cast(dict[str, object], target)
            if (
                target_fields.get("format") != _EXPECTED_TARGET_FORMAT
                or target_fields.get("json_pointer") != _EXPECTED_TARGET_POINTER
            ):
                counters["dev_expected_source_contract_error_count"] += 1
                continue
            target_reference = target_fields.get("path")
            if type(target_reference) is not str:
                counters["dev_expected_source_contract_error_count"] += 1
                continue
            target_reference_value = cast(str, target_reference)
            selection_score = _selection_score(
                seed=configuration.seed,
                row_identity=identity_value,
                target_reference=target_reference_value,
            )

            rejection_reason: str | None = None
            transcript: str | None = None
            expected_phone_tokens: tuple[str, ...] | None = None
            try:
                raw_path = _safe_dataset_file(
                    configuration.dataset_root,
                    target_reference_value,
                )
                source_paths = row_fields.get("source_paths")
                source_reference = (
                    cast(dict[str, object], source_paths).get("json")
                    if type(source_paths) is dict
                    else None
                )
                if source_reference != target_reference_value:
                    raise DevSmokeError(
                        "a dev source and expected-annotation reference differ"
                    )
                target_reference_digest = hashlib.sha256(
                    target_reference_value.encode("utf-8")
                ).digest()
                if target_reference_digest in target_reference_digests:
                    counters["dev_duplicate_target_reference_count"] += 1
                target_reference_digests.add(target_reference_digest)
                payload, raw_bytes = _load_raw_json(raw_path)
                raw_sha256 = hashlib.sha256(raw_bytes).digest()
                _update_framed(dev_raw_content, raw_sha256)
                _update_framed(dev_provenance, identity_digest)
                _update_framed(dev_provenance, target_reference_digest)
                _update_framed(dev_provenance, raw_sha256)
                counters["dev_raw_json_valid_count"] += 1
                (
                    transcript,
                    expected_phone_tokens,
                    rejection_reason,
                    transcript_codepoints,
                ) = _extract_raw_expected_fields(
                    payload,
                    maximum_codepoints=configuration.maximum_codepoints,
                    counters=counters,
                    punctuation_vocabulary=punctuation_vocabulary,
                    noncanonical_vocabulary=noncanonical_vocabulary,
                    rejection_condition_counts=dev_rejection_condition_counts,
                )
                maximum_transcript_codepoints = max(
                    maximum_transcript_codepoints,
                    transcript_codepoints,
                )
                annotation_counts = row_fields.get("annotation_counts")
                expected_count = (
                    cast(dict[str, object], annotation_counts).get("target_phones")
                    if type(annotation_counts) is dict
                    else None
                )
                if expected_phone_tokens is not None and expected_count != len(
                    expected_phone_tokens
                ):
                    counters["dev_annotation_count_mismatch_count"] += 1
                    rejection_reason = "manifest_annotation_count_mismatch"
                    dev_rejection_condition_counts[rejection_reason] += 1
            except DevSmokeError:
                counters["dev_raw_contract_error_count"] += 1
                rejection_reason = "raw_provenance_or_shape_error"
                dev_rejection_condition_counts[rejection_reason] += 1

            if rejection_reason is not None:
                dev_rejection_reason_counts[rejection_reason] += 1
            _push_sample_candidate(
                sample_heap,
                sample_size=configuration.sample_size,
                line_number=line_number,
                candidate=_SelectedRow(
                    selection_score=selection_score,
                    transcript=transcript,
                    expected_phone_tokens=expected_phone_tokens,
                    rejection_reason=rejection_reason,
                ),
            )

    structural_error_count = sum(
        counters[key]
        for key in (
            "manifest_parse_error_count",
            "manifest_non_object_row_count",
            "dev_schema_contract_error_count",
            "dev_dataset_contract_error_count",
            "dev_identity_shape_error_count",
            "dev_speaker_shape_error_count",
            "dev_duplicate_identity_count",
            "dev_expected_source_contract_error_count",
            "dev_duplicate_target_reference_count",
            "dev_raw_contract_error_count",
            "dev_annotation_count_mismatch_count",
        )
    )
    counters["dev_structural_error_count"] = structural_error_count
    if counters["dev_row_count"] == 0:
        raise DevSmokeError("manifest contains no dev rows")
    if len(sample_heap) != configuration.sample_size:
        raise DevSmokeError("the complete dev population is smaller than sample_size")

    selected_rows = tuple(
        item[2]
        for item in sorted(
            sample_heap,
            key=lambda item: item[2].selection_score,
        )
    )
    selection_digest = hashlib.sha256()
    for selected in selected_rows:
        _update_framed(selection_digest, selected.selection_score)

    return _AuditResult(
        counters=counters,
        manifest_sha256=manifest_sha256,
        data_config_sha256=data_config_sha256,
        dev_provenance_sha256=dev_provenance.hexdigest(),
        dev_raw_content_aggregate_sha256=dev_raw_content.hexdigest(),
        punctuation_vocabulary_count=len(punctuation_vocabulary),
        punctuation_vocabulary_sha256=_digest_vocabulary(punctuation_vocabulary),
        noncanonical_vocabulary_count=len(noncanonical_vocabulary),
        noncanonical_vocabulary_sha256=_digest_vocabulary(noncanonical_vocabulary),
        maximum_transcript_codepoints_observed=maximum_transcript_codepoints,
        sample_selection_sha256=selection_digest.hexdigest(),
        dev_rejection_reason_counts=dev_rejection_reason_counts,
        dev_rejection_condition_counts=dev_rejection_condition_counts,
        selected_rows=selected_rows,
    )


@contextmanager
def _network_socket_block() -> Iterator[Counter[str]]:
    attempts: Counter[str] = Counter()

    def _blocked_connect(socket_value: socket.socket, address: object) -> None:
        del socket_value, address
        attempts["connect"] += 1
        raise OSError("network socket access is blocked")

    def _blocked_connect_ex(socket_value: socket.socket, address: object) -> int:
        del socket_value, address
        attempts["connect_ex"] += 1
        raise OSError("network socket access is blocked")

    def _blocked_call(*args: object, **kwargs: object) -> object:
        del args, kwargs
        attempts["socket_helper"] += 1
        raise OSError("network socket access is blocked")

    with (
        mock.patch.object(socket.socket, "connect", new=_blocked_connect),
        mock.patch.object(socket.socket, "connect_ex", new=_blocked_connect_ex),
        mock.patch.object(
            socket,
            "create_connection",
            new=_blocked_call,
        ),
        mock.patch.object(socket, "getaddrinfo", new=_blocked_call),
    ):
        yield attempts


def _punctuation_policy_digest() -> str:
    return hashlib.sha256(
        json.dumps(
            FIXED_ALLOWED_PUNCTUATION,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _aggregate_comparisons(
    selected_rows: tuple[_SelectedRow, ...],
    *,
    configuration: SmokeConfiguration,
    generator: _ExpectedPhoneGenerator,
) -> tuple[dict[str, object], Counter[str], Counter[str]]:
    failures: Counter[str] = Counter()
    network_attempts: Counter[str]
    comparisons: list[ExpectedPhoneAgreementResult] = []
    result_aggregate = hashlib.sha256()
    policy_rejected_count = 0
    g2pk_attempted_count = 0

    with _network_socket_block() as network_attempts:
        for selected in selected_rows:
            if selected.rejection_reason is not None:
                failures[selected.rejection_reason] += 1
                policy_rejected_count += 1
                continue
            if selected.transcript is None or selected.expected_phone_tokens is None:
                failures["selected_row_state_error"] += 1
                policy_rejected_count += 1
                continue
            g2pk_attempted_count += 1
            try:
                generated = generator(
                    selected.transcript,
                    descriptive=configuration.descriptive,
                    group_vowels=configuration.group_vowels,
                    word_boundary_policy=configuration.word_boundary_policy,
                    allowed_punctuation=FIXED_ALLOWED_PUNCTUATION,
                    maximum_codepoints=configuration.maximum_codepoints,
                    nltk_data_root=configuration.nltk_data_root,
                )
                comparison = evaluate_expected_phone_agreement(
                    g2pk_expected=generated,
                    aihub_expected_phone_tokens=selected.expected_phone_tokens,
                    max_alignment_cells=configuration.max_alignment_cells,
                )
            except G2PKExpectedPhoneError:
                failures["g2pk_generation_contract_error"] += 1
                continue
            except ExpectedPhoneAgreementError:
                failures["expected_phone_agreement_contract_error"] += 1
                continue
            except (
                ImportError,
                LookupError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                failures["unexpected_bounded_smoke_error"] += 1
                continue

            comparisons.append(comparison)
            _update_framed(result_aggregate, selected.selection_score)
            _update_framed(
                result_aggregate,
                bytes.fromhex(comparison.result_digest_sha256),
            )

    comparison_count = len(comparisons)
    if policy_rejected_count + g2pk_attempted_count != len(selected_rows):
        raise DevSmokeError("sample attempt accounting invariant failed")
    if comparison_count + sum(failures.values()) != len(selected_rows):
        raise DevSmokeError("sample rejection accounting invariant failed")
    g2pk_phone_count = sum(result.g2pk_expected_phone_count for result in comparisons)
    aihub_phone_count = sum(result.aihub_expected_phone_count for result in comparisons)
    distance_sum = sum(result.levenshtein_distance for result in comparisons)
    exact_match_count = sum(result.is_exact_match for result in comparisons)
    agreement_rates = [result.normalized_edit_agreement_rate for result in comparisons]
    summary: dict[str, object] = {
        "sample_requested_count": configuration.sample_size,
        "sample_fixed_before_eligibility_count": len(selected_rows),
        "sample_policy_rejected_after_selection_count": policy_rejected_count,
        "sample_rejected_after_selection_count": sum(failures.values()),
        "g2pk_attempted_count": g2pk_attempted_count,
        "post_attempt_failure_count": g2pk_attempted_count - comparison_count,
        "comparison_completed_count": comparison_count,
        "g2pk_expected_phone_count": g2pk_phone_count,
        "aihub_expected_phone_count": aihub_phone_count,
        "levenshtein_distance_sum": distance_sum,
        "exact_match_count": exact_match_count,
        "exact_match_rate": (
            exact_match_count / comparison_count if comparison_count else None
        ),
        "macro_normalized_edit_agreement_rate": (
            math.fsum(agreement_rates) / comparison_count if comparison_count else None
        ),
        "minimum_normalized_edit_agreement_rate": (
            min(agreement_rates) if comparison_count else None
        ),
        "maximum_normalized_edit_agreement_rate": (
            max(agreement_rates) if comparison_count else None
        ),
        "comparison_result_aggregate_sha256": result_aggregate.hexdigest(),
    }
    return summary, failures, network_attempts


def _audit_summary(audit: _AuditResult) -> dict[str, object]:
    counters = audit.counters
    return {
        "manifest_row_count": counters["manifest_row_count"],
        "non_dev_row_skipped_without_source_access_count": counters[
            "non_dev_row_skipped_without_source_access_count"
        ],
        "dev_row_count": counters["dev_row_count"],
        "dev_raw_json_valid_count": counters["dev_raw_json_valid_count"],
        "dev_structural_error_count": counters["dev_structural_error_count"],
        "dev_duplicate_identity_count": counters["dev_duplicate_identity_count"],
        "dev_duplicate_target_reference_count": counters[
            "dev_duplicate_target_reference_count"
        ],
        "dev_annotation_count_mismatch_count": counters[
            "dev_annotation_count_mismatch_count"
        ],
        "dev_transcript_with_punctuation_count": counters[
            "dev_transcript_with_punctuation_count"
        ],
        "dev_transcript_punctuation_occurrence_count": counters[
            "dev_transcript_punctuation_occurrence_count"
        ],
        "dev_transcript_outside_punctuation_policy_count": counters[
            "dev_transcript_outside_punctuation_policy_count"
        ],
        "dev_transcript_over_resource_cap_count": counters[
            "dev_transcript_over_resource_cap_count"
        ],
        "maximum_transcript_codepoints_observed": (
            audit.maximum_transcript_codepoints_observed
        ),
        "punctuation_vocabulary_count": audit.punctuation_vocabulary_count,
        "punctuation_vocabulary_sha256": (audit.punctuation_vocabulary_sha256),
        "dev_annotation_phone_occurrence_count": counters[
            "dev_annotation_phone_occurrence_count"
        ],
        "dev_canonical_phone_occurrence_count": counters[
            "dev_canonical_phone_occurrence_count"
        ],
        "dev_noncanonical_phone_occurrence_count": counters[
            "dev_noncanonical_phone_occurrence_count"
        ],
        "dev_row_with_noncanonical_phone_count": counters[
            "dev_row_with_noncanonical_phone_count"
        ],
        "dev_exact_sil_occurrence_count": counters["dev_exact_sil_occurrence_count"],
        "noncanonical_vocabulary_count": audit.noncanonical_vocabulary_count,
        "noncanonical_vocabulary_sha256": (audit.noncanonical_vocabulary_sha256),
        "dev_primary_rejection_reason_counts": dict(
            sorted(audit.dev_rejection_reason_counts.items())
        ),
        "dev_rejection_condition_counts": dict(
            sorted(audit.dev_rejection_condition_counts.items())
        ),
    }


def _report_digest(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def run_dev_smoke(
    configuration: SmokeConfiguration,
    *,
    generator: _ExpectedPhoneGenerator,
) -> dict[str, object]:
    """Audit all dev provenance, then run a fixed sample without raw outputs."""

    validated = _validate_configuration(configuration)
    implementation_sha256 = _verify_implementation_sha256(validated)
    audit = _audit_dev_and_fix_sample(validated)
    smoke, failures, network_attempts = _aggregate_comparisons(
        audit.selected_rows,
        configuration=validated,
        generator=generator,
    )
    if _capture_implementation_sha256() != implementation_sha256:
        raise DevSmokeError(
            "implementation or environment-lock changed during execution"
        )
    is_complete = (
        audit.counters["dev_structural_error_count"] == 0
        and smoke["comparison_completed_count"] == validated.sample_size
        and not failures
        and not network_attempts
    )
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": (
            "passed_development_diagnostic"
            if is_complete
            else "incomplete_development_diagnostic"
        ),
        "scope": {
            "dataset_id": EXPECTED_DATASET_ID,
            "split": FIXED_SPLIT,
            "development_only": True,
            "g2pk_unique_row_limit": MAXIMUM_SAMPLE_SIZE,
            "audio_accessed": False,
            "textgrid_accessed": False,
            "training_run": False,
            "locked_evaluation_run": False,
        },
        "provenance": {
            "manifest_sha256": audit.manifest_sha256,
            "data_config_sha256": audit.data_config_sha256,
            "dev_provenance_sha256": audit.dev_provenance_sha256,
            "dev_raw_content_aggregate_sha256": (
                audit.dev_raw_content_aggregate_sha256
            ),
            "sample_selection_sha256": audit.sample_selection_sha256,
            "implementation_sha256": implementation_sha256,
            "implementation_verified_at_start": True,
            "implementation_unchanged_at_end": True,
        },
        "policies": {
            "sample_policy_id": SAMPLE_POLICY_ID,
            "sample_seed": validated.seed,
            "sample_size": validated.sample_size,
            "sample_population": "all_dev_rows_before_annotation_eligibility",
            "g2pk_version": EXPECTED_G2PK_VERSION,
            "descriptive": validated.descriptive,
            "group_vowels": validated.group_vowels,
            "word_boundary_policy": validated.word_boundary_policy,
            "punctuation_policy_id": validated.punctuation_policy,
            "allowed_punctuation_count": len(FIXED_ALLOWED_PUNCTUATION),
            "allowed_punctuation_sha256": _punctuation_policy_digest(),
            "aihub_annotation_boundary_policy_id": BOUNDARY_POLICY_ID,
            "noncanonical_annotation_action": "reject_row_after_selection",
            "maximum_codepoints_resource_cap": validated.maximum_codepoints,
            "max_alignment_cells_resource_cap": (validated.max_alignment_cells),
            "scientific_decision_threshold": None,
        },
        "dev_audit": _audit_summary(audit),
        "smoke": smoke,
        "failure_reason_counts": dict(sorted(failures.items())),
        "runtime": {
            "network_socket_connect_blocked": True,
            "network_socket_attempt_count": sum(network_attempts.values()),
            "nltk_data_contract": "absolute_pinned_root_and_exact_adapter_digests",
        },
        "claim_limits": {
            "accuracy_established": False,
            "observed_pronunciation_evaluated": False,
            "phase1_m1_complete": False,
            "primary_model_selection_authorized": False,
            "threshold_selected": False,
        },
    }
    report["artifact_payload_sha256"] = _report_digest(report)
    return report


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.chmod(temporary_path, 0o600)
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError:
        try:
            temporary_path.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        raise DevSmokeError("aggregate artifact could not be written") from None


def _parse_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("must be exactly true or false")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse caller-explicit provenance, scientific, and resource policies."""

    parser = argparse.ArgumentParser(
        description=(
            "Run an aggregate-only g2pK/AI Hub expected-phone smoke on a fixed "
            "development sample."
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--data-config", required=True)
    parser.add_argument("--data-config-sha256", required=True)
    parser.add_argument("--runner-sha256", required=True)
    parser.add_argument("--agreement-source-sha256", required=True)
    parser.add_argument("--g2pk-adapter-source-sha256", required=True)
    parser.add_argument("--phone-inventory-source-sha256", required=True)
    parser.add_argument("--g2pk-environment-lock-sha256", required=True)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--nltk-data-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--sample-size", required=True, type=int)
    parser.add_argument("--descriptive", required=True, type=_parse_bool)
    parser.add_argument("--group-vowels", required=True, type=_parse_bool)
    parser.add_argument(
        "--punctuation-policy",
        required=True,
        choices=(PUNCTUATION_POLICY_ID,),
    )
    parser.add_argument(
        "--word-boundary-policy",
        required=True,
        choices=(WORD_BOUNDARY_POLICY,),
    )
    parser.add_argument("--maximum-codepoints", required=True, type=int)
    parser.add_argument("--max-alignment-cells", required=True, type=int)
    return parser.parse_args(argv)


def _configuration_from_args(args: argparse.Namespace) -> SmokeConfiguration:
    return SmokeConfiguration(
        manifest_path=Path(args.manifest).resolve(),
        expected_manifest_sha256=args.manifest_sha256,
        data_config_path=Path(args.data_config).resolve(),
        expected_data_config_sha256=args.data_config_sha256,
        expected_runner_sha256=args.runner_sha256,
        expected_agreement_source_sha256=args.agreement_source_sha256,
        expected_g2pk_adapter_source_sha256=args.g2pk_adapter_source_sha256,
        expected_phone_inventory_source_sha256=(args.phone_inventory_source_sha256),
        expected_g2pk_environment_lock_sha256=(args.g2pk_environment_lock_sha256),
        dataset_root=Path(args.dataset_root),
        nltk_data_root=Path(args.nltk_data_root),
        output_path=Path(args.output).resolve(),
        seed=args.seed,
        sample_size=args.sample_size,
        descriptive=args.descriptive,
        group_vowels=args.group_vowels,
        punctuation_policy=args.punctuation_policy,
        word_boundary_policy=args.word_boundary_policy,
        maximum_codepoints=args.maximum_codepoints,
        max_alignment_cells=args.max_alignment_cells,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the bounded diagnostic and write only an aggregate JSON artifact."""

    args = parse_args(argv)
    configuration = _configuration_from_args(args)
    try:
        report = run_dev_smoke(
            configuration,
            generator=generate_g2pk_expected_phones,
        )
        _write_json_atomic(configuration.output_path, report)
    except DevSmokeError as error:
        print(f"configuration_or_audit_error={error}", file=sys.stderr)
        return 2

    smoke = cast(dict[str, object], report["smoke"])
    safe_summary = {
        "status": report["status"],
        "dev_row_count": cast(dict[str, object], report["dev_audit"])["dev_row_count"],
        "sample_fixed_count": smoke["sample_fixed_before_eligibility_count"],
        "sample_rejected_count": smoke["sample_rejected_after_selection_count"],
        "comparison_completed_count": smoke["comparison_completed_count"],
        "artifact_payload_sha256": report["artifact_payload_sha256"],
    }
    print(
        json.dumps(
            safe_summary,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed_development_diagnostic" else 1


if __name__ == "__main__":
    raise SystemExit(main())
