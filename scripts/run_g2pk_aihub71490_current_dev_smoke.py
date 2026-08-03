#!/usr/bin/env python3
"""Run an aggregate-only g2pK smoke on the current project dev split.

The current modeling Parquet is scanned before any corpus source is opened.
Only the fixed 16-row project-dev sample may then open expected-label JSON.
No audio or TextGrid file is accepted or opened, and no transcript, phone
sequence, row identifier, speaker identifier, or source path is serialized.
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

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

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


REPORT_SCHEMA_VERSION = "voice_coach.g2pk_aihub71490_current_dev_smoke.v3"
SPLIT_AUDIT_SCHEMA_VERSION = "voice_coach.g2pk_aihub71490_split_currency_audit.v1"
CURRENT_MANIFEST_SCHEMA_VERSION = "aihub71490.enriched.v1"
EXPECTED_DATASET_ID = "71490"
CURRENT_SPLITS = ("calibration", "dev", "locked_test", "stack", "train")
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
EXPECTED_PYARROW_VERSION = "25.0.0"
_CANONICAL_PHONE_SET = frozenset(KOREAN_COMPATIBILITY_JAMO)
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
    "modeling_manifest_builder": (
        _WORKSPACE_ROOT / "scripts" / "build_aihub71490_modeling_manifest.py"
    ),
    "g2pk_environment_lock": (
        _WORKSPACE_ROOT / "requirements" / "paper-g2pk-linux-aarch64.lock.txt"
    ),
    "scorer_environment_lock": (
        _WORKSPACE_ROOT / "requirements" / "scorer-lock-linux-aarch64-cu130.txt"
    ),
}


class CurrentDevSmokeError(ValueError):
    """Raised when a current-dev input or invariant is unsafe."""


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
class CurrentDevSmokeConfiguration:
    """Pinned split, implementation, environment, and resource contract."""

    modeling_manifest_path: Path
    expected_modeling_manifest_sha256: str
    split_audit_path: Path
    expected_split_audit_sha256: str
    expected_runner_sha256: str
    expected_agreement_source_sha256: str
    expected_g2pk_adapter_source_sha256: str
    expected_phone_inventory_source_sha256: str
    expected_modeling_manifest_builder_sha256: str
    expected_g2pk_environment_lock_sha256: str
    expected_scorer_environment_lock_sha256: str
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
class _SelectedMetadataRow:
    """In-memory dev metadata that is never serialized."""

    selection_score: bytes
    split: str
    json_reference: str
    prompt_sha256: str


@dataclass(frozen=True, slots=True)
class _SelectedExpectedRow:
    """In-memory expected-label state that is never serialized."""

    selection_score: bytes
    transcript: str | None
    expected_phone_tokens: tuple[str, ...] | None
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class _MetadataAudit:
    """Aggregate metadata audit plus the pre-raw fixed sample."""

    counters: Counter[str]
    split_counts: Counter[str]
    split_assignment_sha256: str
    sample_selection_sha256: str
    selected_rows: tuple[_SelectedMetadataRow, ...]


@dataclass(frozen=True, slots=True)
class _RawAudit:
    """Aggregate selected-source audit and in-memory comparison inputs."""

    counters: Counter[str]
    access_by_split: Counter[str]
    rejection_reason_counts: Counter[str]
    rejection_condition_counts: Counter[str]
    raw_content_aggregate_sha256: str
    punctuation_vocabulary_count: int
    punctuation_vocabulary_sha256: str
    noncanonical_vocabulary_count: int
    noncanonical_vocabulary_sha256: str
    maximum_transcript_codepoints_observed: int
    selected_rows: tuple[_SelectedExpectedRow, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise CurrentDevSmokeError(
            "a required provenance file could not be read"
        ) from None
    return digest.hexdigest()


def _validate_sha256(value: object, *, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CurrentDevSmokeError(f"{field_name} must be a lowercase SHA-256 digest")
    return cast(str, value)


def _real_regular_file(path: object, *, field_name: str) -> Path:
    if not isinstance(path, Path):
        raise CurrentDevSmokeError(f"{field_name} must be a Path")
    candidate = path
    if not candidate.is_absolute():
        raise CurrentDevSmokeError(f"{field_name} must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
        information = candidate.lstat()
    except (OSError, RuntimeError):
        raise CurrentDevSmokeError(f"{field_name} is unavailable") from None
    if (
        resolved != candidate
        or candidate.is_symlink()
        or not stat.S_ISREG(information.st_mode)
    ):
        raise CurrentDevSmokeError(f"{field_name} must be a real regular file")
    return candidate


def _real_absolute_directory(path: object, *, field_name: str) -> Path:
    if not isinstance(path, Path):
        raise CurrentDevSmokeError(f"{field_name} must be a Path")
    candidate = path
    if not candidate.is_absolute():
        raise CurrentDevSmokeError(f"{field_name} must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise CurrentDevSmokeError(f"{field_name} is unavailable") from None
    if resolved != candidate or candidate.is_symlink() or not candidate.is_dir():
        raise CurrentDevSmokeError(f"{field_name} must be a real directory")
    return candidate


def _positive_integer(
    value: object,
    *,
    field_name: str,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise CurrentDevSmokeError(f"{field_name} must be a positive integer")
    normalized = cast(int, value)
    if normalized <= 0 or (maximum is not None and normalized > maximum):
        raise CurrentDevSmokeError(
            f"{field_name} is outside the supported resource bound"
        )
    return normalized


def _validate_configuration(
    value: object,
) -> CurrentDevSmokeConfiguration:
    if type(value) is not CurrentDevSmokeConfiguration:
        raise CurrentDevSmokeError(
            "configuration must be an exact CurrentDevSmokeConfiguration"
        )
    configuration = cast(CurrentDevSmokeConfiguration, value)
    if configuration.seed != FIXED_SEED:
        raise CurrentDevSmokeError("seed differs from the fixed smoke contract")
    sample_size = _positive_integer(
        configuration.sample_size,
        field_name="sample_size",
        maximum=MAXIMUM_SAMPLE_SIZE,
    )
    if sample_size != MAXIMUM_SAMPLE_SIZE:
        raise CurrentDevSmokeError(
            "sample_size differs from the fixed current-dev contract"
        )
    if type(configuration.descriptive) is not bool or configuration.descriptive:
        raise CurrentDevSmokeError(
            "descriptive differs from the fixed current-dev contract"
        )
    if type(configuration.group_vowels) is not bool or configuration.group_vowels:
        raise CurrentDevSmokeError(
            "group_vowels differs from the fixed current-dev contract"
        )
    if configuration.punctuation_policy != PUNCTUATION_POLICY_ID:
        raise CurrentDevSmokeError("punctuation_policy differs from the fixed contract")
    if configuration.word_boundary_policy != WORD_BOUNDARY_POLICY:
        raise CurrentDevSmokeError(
            "word_boundary_policy differs from the fixed contract"
        )
    if configuration.output_path.exists() and configuration.output_path.is_symlink():
        raise CurrentDevSmokeError("output_path must not be a symlink")
    if configuration.output_path.name in {"", ".", ".."}:
        raise CurrentDevSmokeError("output_path must name an artifact file")
    return CurrentDevSmokeConfiguration(
        modeling_manifest_path=_real_regular_file(
            configuration.modeling_manifest_path,
            field_name="modeling_manifest_path",
        ),
        expected_modeling_manifest_sha256=_validate_sha256(
            configuration.expected_modeling_manifest_sha256,
            field_name="expected_modeling_manifest_sha256",
        ),
        split_audit_path=_real_regular_file(
            configuration.split_audit_path,
            field_name="split_audit_path",
        ),
        expected_split_audit_sha256=_validate_sha256(
            configuration.expected_split_audit_sha256,
            field_name="expected_split_audit_sha256",
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
        expected_modeling_manifest_builder_sha256=_validate_sha256(
            configuration.expected_modeling_manifest_builder_sha256,
            field_name="expected_modeling_manifest_builder_sha256",
        ),
        expected_g2pk_environment_lock_sha256=_validate_sha256(
            configuration.expected_g2pk_environment_lock_sha256,
            field_name="expected_g2pk_environment_lock_sha256",
        ),
        expected_scorer_environment_lock_sha256=_validate_sha256(
            configuration.expected_scorer_environment_lock_sha256,
            field_name="expected_scorer_environment_lock_sha256",
        ),
        dataset_root=_real_absolute_directory(
            configuration.dataset_root,
            field_name="dataset_root",
        ),
        nltk_data_root=_real_absolute_directory(
            configuration.nltk_data_root,
            field_name="nltk_data_root",
        ),
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
        maximum_codepoints=_positive_integer(
            configuration.maximum_codepoints,
            field_name="maximum_codepoints",
            maximum=1_000_000,
        ),
        max_alignment_cells=_positive_integer(
            configuration.max_alignment_cells,
            field_name="max_alignment_cells",
            maximum=1_000_000,
        ),
    )


def _capture_implementation_sha256() -> dict[str, str]:
    return {
        role: _sha256_file(_real_regular_file(path, field_name=f"{role}_path"))
        for role, path in _IMPLEMENTATION_PATHS.items()
    }


def _expected_implementation_sha256(
    configuration: CurrentDevSmokeConfiguration,
) -> dict[str, str]:
    return {
        "runner": configuration.expected_runner_sha256,
        "agreement_source": configuration.expected_agreement_source_sha256,
        "g2pk_adapter_source": (configuration.expected_g2pk_adapter_source_sha256),
        "phone_inventory_source": (
            configuration.expected_phone_inventory_source_sha256
        ),
        "modeling_manifest_builder": (
            configuration.expected_modeling_manifest_builder_sha256
        ),
        "g2pk_environment_lock": (configuration.expected_g2pk_environment_lock_sha256),
        "scorer_environment_lock": (
            configuration.expected_scorer_environment_lock_sha256
        ),
    }


def _verify_start_snapshot(
    configuration: CurrentDevSmokeConfiguration,
) -> tuple[dict[str, str], dict[str, str]]:
    inputs = {
        "modeling_manifest": _sha256_file(configuration.modeling_manifest_path),
        "split_currency_audit": _sha256_file(configuration.split_audit_path),
    }
    expected_inputs = {
        "modeling_manifest": configuration.expected_modeling_manifest_sha256,
        "split_currency_audit": configuration.expected_split_audit_sha256,
    }
    if inputs != expected_inputs:
        raise CurrentDevSmokeError("an input digest differs from the pinned provenance")
    implementation = _capture_implementation_sha256()
    if implementation != _expected_implementation_sha256(configuration):
        raise CurrentDevSmokeError(
            "implementation or environment-lock digest differs from the pin"
        )
    if pa.__version__ != EXPECTED_PYARROW_VERSION:
        raise CurrentDevSmokeError(
            "pyarrow version differs from the fixed runtime contract"
        )
    return inputs, implementation


def _load_split_audit(
    path: Path,
    *,
    expected_modeling_manifest_sha256: str,
) -> str:
    try:
        parsed = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise CurrentDevSmokeError("split-currency audit could not be parsed") from None
    if type(parsed) is not dict:
        raise CurrentDevSmokeError("split-currency audit has an invalid root shape")
    report = cast(dict[str, object], parsed)
    if (
        report.get("schema_version") != SPLIT_AUDIT_SCHEMA_VERSION
        or report.get("status") != "split_currency_audit_complete"
    ):
        raise CurrentDevSmokeError(
            "split-currency audit is not a completed compatible report"
        )
    provenance = report.get("provenance")
    join_audit = report.get("join_audit")
    legacy_disposition = report.get("legacy_evidence_disposition")
    if (
        type(provenance) is not dict
        or type(join_audit) is not dict
        or type(legacy_disposition) is not dict
    ):
        raise CurrentDevSmokeError(
            "split-currency audit lacks required aggregate evidence"
        )
    provenance_fields = cast(dict[str, object], provenance)
    input_sha256 = provenance_fields.get("input_sha256")
    if (
        type(input_sha256) is not dict
        or cast(dict[str, object], input_sha256).get("current_manifest")
        != expected_modeling_manifest_sha256
        or cast(dict[str, object], join_audit).get("structural_error_count") != 0
        or cast(dict[str, object], legacy_disposition).get("disposition")
        != "superseded_current_split_drift"
    ):
        raise CurrentDevSmokeError(
            "split-currency audit does not authorize a current-dev rerun"
        )
    return _validate_sha256(
        provenance_fields.get("current_split_assignment_sha256"),
        field_name="current_split_assignment_sha256",
    )


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


def _push_sample_candidate(
    heap: list[tuple[int, int, _SelectedMetadataRow]],
    *,
    sample_size: int,
    ordinal: int,
    candidate: _SelectedMetadataRow,
) -> None:
    score_integer = int.from_bytes(
        candidate.selection_score,
        byteorder="big",
        signed=False,
    )
    item = (-score_integer, -ordinal, candidate)
    if len(heap) < sample_size:
        heapq.heappush(heap, item)
        return
    if item > heap[0]:
        heapq.heapreplace(heap, item)


def _safe_relative_json_reference(value: object) -> str | None:
    if type(value) is not str:
        return None
    normalized = cast(str, value)
    relative = Path(normalized)
    if (
        not normalized
        or relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or relative.suffix.lower() != ".json"
    ):
        return None
    return normalized


def _validate_current_schema(parquet: pq.ParquetFile) -> None:
    schema = parquet.schema_arrow
    expected_fields = (
        "schema_version",
        "id",
        "split",
        "json_path",
        "prompt_sha256",
    )
    for name in expected_fields:
        if name not in schema.names or not pa.types.is_string(schema.field(name).type):
            raise CurrentDevSmokeError(
                "current modeling manifest schema violates the smoke contract"
            )


def _scan_current_metadata(
    configuration: CurrentDevSmokeConfiguration,
) -> _MetadataAudit:
    try:
        parquet = pq.ParquetFile(configuration.modeling_manifest_path)
    except (OSError, ValueError):
        raise CurrentDevSmokeError(
            "current modeling manifest could not be opened"
        ) from None
    _validate_current_schema(parquet)
    counters: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    identities: set[str] = set()
    json_references: set[str] = set()
    assignment_digest = hashlib.sha256()
    sample_heap: list[tuple[int, int, _SelectedMetadataRow]] = []
    try:
        batches = parquet.iter_batches(
            batch_size=4096,
            columns=[
                "schema_version",
                "id",
                "split",
                "json_path",
                "prompt_sha256",
            ],
        )
        for batch in batches:
            rows = cast(list[dict[str, object]], batch.to_pylist())
            for row in rows:
                counters["manifest_row_count"] += 1
                ordinal = counters["manifest_row_count"]
                if row.get("schema_version") != CURRENT_MANIFEST_SCHEMA_VERSION:
                    counters["schema_error_count"] += 1
                row_identity = row.get("id")
                split = row.get("split")
                json_reference = _safe_relative_json_reference(row.get("json_path"))
                prompt_sha256 = row.get("prompt_sha256")
                if type(row_identity) is not str or not row_identity:
                    counters["identity_shape_error_count"] += 1
                    continue
                if type(split) is not str or split not in CURRENT_SPLITS:
                    counters["split_contract_error_count"] += 1
                    continue
                if json_reference is None:
                    counters["json_reference_contract_error_count"] += 1
                    continue
                try:
                    prompt_digest = _validate_sha256(
                        prompt_sha256,
                        field_name="prompt_sha256",
                    )
                except CurrentDevSmokeError:
                    counters["prompt_sha256_contract_error_count"] += 1
                    continue
                identity_value = cast(str, row_identity)
                split_value = cast(str, split)
                if identity_value in identities:
                    counters["duplicate_identity_count"] += 1
                    continue
                if json_reference in json_references:
                    counters["duplicate_json_reference_count"] += 1
                    continue
                identities.add(identity_value)
                json_references.add(json_reference)
                split_counts[split_value] += 1
                _update_framed(assignment_digest, identity_value.encode("utf-8"))
                _update_framed(assignment_digest, split_value.encode("ascii"))
                if split_value != FIXED_SPLIT:
                    counters["non_dev_row_skipped_without_raw_source_access_count"] += 1
                    continue
                counters["dev_population_row_count"] += 1
                _push_sample_candidate(
                    sample_heap,
                    sample_size=configuration.sample_size,
                    ordinal=ordinal,
                    candidate=_SelectedMetadataRow(
                        selection_score=_selection_score(
                            seed=configuration.seed,
                            row_identity=identity_value,
                            target_reference=json_reference,
                        ),
                        split=split_value,
                        json_reference=json_reference,
                        prompt_sha256=prompt_digest,
                    ),
                )
    except (OSError, ValueError):
        raise CurrentDevSmokeError(
            "current modeling manifest could not be scanned"
        ) from None
    if parquet.metadata.num_rows != counters["manifest_row_count"]:
        counters["metadata_row_count_mismatch_count"] += 1
    selected_rows = tuple(
        item[2]
        for item in sorted(
            sample_heap,
            key=lambda item: item[2].selection_score,
        )
    )
    if len(selected_rows) != configuration.sample_size:
        counters["sample_cardinality_error_count"] += 1
    selection_digest = hashlib.sha256()
    for selected in selected_rows:
        _update_framed(selection_digest, selected.selection_score)
    return _MetadataAudit(
        counters=counters,
        split_counts=split_counts,
        split_assignment_sha256=assignment_digest.hexdigest(),
        sample_selection_sha256=selection_digest.hexdigest(),
        selected_rows=selected_rows,
    )


def _safe_dataset_file(dataset_root: Path, relative_value: str) -> Path:
    relative = Path(relative_value)
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or relative.suffix.lower() != ".json"
    ):
        raise CurrentDevSmokeError(
            "a selected dev source reference violates the path contract"
        )
    candidate = dataset_root / relative
    try:
        resolved = candidate.resolve(strict=True)
        information = candidate.lstat()
    except (OSError, RuntimeError):
        raise CurrentDevSmokeError(
            "a selected dev expected-label JSON is unavailable"
        ) from None
    if (
        resolved != candidate
        or not resolved.is_relative_to(dataset_root)
        or candidate.is_symlink()
        or not stat.S_ISREG(information.st_mode)
    ):
        raise CurrentDevSmokeError(
            "a selected dev expected-label JSON violates the file contract"
        )
    return candidate


def _load_raw_json(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        raw_bytes = path.read_bytes()
        parsed = json.loads(raw_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise CurrentDevSmokeError(
            "a selected dev expected-label JSON could not be parsed"
        ) from None
    if type(parsed) is not dict:
        raise CurrentDevSmokeError(
            "a selected dev expected-label JSON has an invalid root shape"
        )
    return cast(dict[str, object], parsed), raw_bytes


def _extract_expected_fields(
    payload: dict[str, object],
    *,
    expected_prompt_sha256: str,
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
            hashlib.sha256(transcript_value.encode("utf-8")).hexdigest()
            != expected_prompt_sha256
        ):
            reject("prompt_digest_mismatch")
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
        counters["selected_transcript_punctuation_occurrence_count"] += sum(
            unicodedata.category(character).startswith("P")
            for character in transcript_value
        )
        if row_punctuation:
            counters["selected_transcript_with_punctuation_count"] += 1
        if not row_punctuation <= frozenset(FIXED_ALLOWED_PUNCTUATION):
            counters["selected_transcript_outside_punctuation_policy_count"] += 1
            reject("punctuation_outside_fixed_policy")
        if len(transcript_value) > maximum_codepoints:
            counters["selected_transcript_over_resource_cap_count"] += 1
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
        counters["selected_annotation_phone_occurrence_count"] += 1
        if token_value in _CANONICAL_PHONE_SET:
            counters["selected_canonical_phone_occurrence_count"] += 1
        else:
            contains_noncanonical = True
            counters["selected_noncanonical_phone_occurrence_count"] += 1
            noncanonical_vocabulary.add(token_value)
        tokens.append(token_value)
    if contains_noncanonical:
        counters["selected_row_with_noncanonical_phone_count"] += 1
        reject("noncanonical_annotation_token")
    return (
        transcript_value,
        tuple(tokens),
        rejection_reasons[0] if rejection_reasons else None,
        transcript_codepoints,
    )


def _digest_vocabulary(values: set[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(values):
        _update_framed(digest, value.encode("utf-8"))
    return digest.hexdigest()


def _load_selected_expected_json(
    selected_rows: tuple[_SelectedMetadataRow, ...],
    *,
    configuration: CurrentDevSmokeConfiguration,
) -> _RawAudit:
    counters: Counter[str] = Counter()
    access_by_split: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    rejection_conditions: Counter[str] = Counter()
    punctuation_vocabulary: set[str] = set()
    noncanonical_vocabulary: set[str] = set()
    raw_content_digest = hashlib.sha256()
    expected_rows: list[_SelectedExpectedRow] = []
    maximum_transcript_codepoints = 0
    for selected in selected_rows:
        if selected.split != FIXED_SPLIT:
            raise CurrentDevSmokeError(
                "a non-dev row reached the raw expected-label access boundary"
            )
        counters["expected_label_json_access_attempt_count"] += 1
        access_by_split[selected.split] += 1
        rejection_reason: str | None = None
        transcript: str | None = None
        expected_phone_tokens: tuple[str, ...] | None = None
        try:
            raw_path = _safe_dataset_file(
                configuration.dataset_root,
                selected.json_reference,
            )
            payload, raw_bytes = _load_raw_json(raw_path)
            counters["expected_label_json_access_completed_count"] += 1
            raw_sha256 = hashlib.sha256(raw_bytes).digest()
            _update_framed(raw_content_digest, selected.selection_score)
            _update_framed(raw_content_digest, raw_sha256)
            (
                transcript,
                expected_phone_tokens,
                rejection_reason,
                transcript_codepoints,
            ) = _extract_expected_fields(
                payload,
                expected_prompt_sha256=selected.prompt_sha256,
                maximum_codepoints=configuration.maximum_codepoints,
                counters=counters,
                punctuation_vocabulary=punctuation_vocabulary,
                noncanonical_vocabulary=noncanonical_vocabulary,
                rejection_condition_counts=rejection_conditions,
            )
            maximum_transcript_codepoints = max(
                maximum_transcript_codepoints,
                transcript_codepoints,
            )
        except CurrentDevSmokeError:
            rejection_reason = "raw_provenance_or_shape_error"
            rejection_conditions[rejection_reason] += 1
        if rejection_reason is not None:
            rejection_reasons[rejection_reason] += 1
        expected_rows.append(
            _SelectedExpectedRow(
                selection_score=selected.selection_score,
                transcript=transcript,
                expected_phone_tokens=expected_phone_tokens,
                rejection_reason=rejection_reason,
            )
        )
    return _RawAudit(
        counters=counters,
        access_by_split=access_by_split,
        rejection_reason_counts=rejection_reasons,
        rejection_condition_counts=rejection_conditions,
        raw_content_aggregate_sha256=raw_content_digest.hexdigest(),
        punctuation_vocabulary_count=len(punctuation_vocabulary),
        punctuation_vocabulary_sha256=_digest_vocabulary(punctuation_vocabulary),
        noncanonical_vocabulary_count=len(noncanonical_vocabulary),
        noncanonical_vocabulary_sha256=_digest_vocabulary(noncanonical_vocabulary),
        maximum_transcript_codepoints_observed=maximum_transcript_codepoints,
        selected_rows=tuple(expected_rows),
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

    def _blocked_sendto(
        socket_value: socket.socket,
        *args: object,
        **kwargs: object,
    ) -> int:
        del socket_value, args, kwargs
        attempts["sendto"] += 1
        raise OSError("network socket access is blocked")

    def _blocked_helper(*args: object, **kwargs: object) -> object:
        del args, kwargs
        attempts["socket_helper"] += 1
        raise OSError("network socket access is blocked")

    with (
        mock.patch.object(socket.socket, "connect", new=_blocked_connect),
        mock.patch.object(socket.socket, "connect_ex", new=_blocked_connect_ex),
        mock.patch.object(socket.socket, "sendto", new=_blocked_sendto),
        mock.patch.object(socket, "create_connection", new=_blocked_helper),
        mock.patch.object(socket, "getaddrinfo", new=_blocked_helper),
        mock.patch.object(socket, "gethostbyname", new=_blocked_helper),
        mock.patch.object(socket, "gethostbyname_ex", new=_blocked_helper),
        mock.patch.object(socket, "getnameinfo", new=_blocked_helper),
    ):
        yield attempts


def _aggregate_comparisons(
    selected_rows: tuple[_SelectedExpectedRow, ...],
    *,
    configuration: CurrentDevSmokeConfiguration,
    generator: _ExpectedPhoneGenerator,
) -> tuple[dict[str, object], Counter[str], Counter[str]]:
    failures: Counter[str] = Counter()
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
        raise CurrentDevSmokeError("sample attempt accounting invariant failed")
    if comparison_count + sum(failures.values()) != len(selected_rows):
        raise CurrentDevSmokeError("sample rejection accounting invariant failed")
    agreement_rates = [result.normalized_edit_agreement_rate for result in comparisons]
    exact_match_count = sum(result.is_exact_match for result in comparisons)
    summary: dict[str, object] = {
        "sample_requested_count": configuration.sample_size,
        "sample_fixed_before_raw_access_count": len(selected_rows),
        "sample_policy_rejected_after_raw_access_count": policy_rejected_count,
        "sample_rejected_after_selection_count": sum(failures.values()),
        "g2pk_attempted_count": g2pk_attempted_count,
        "post_attempt_failure_count": g2pk_attempted_count - comparison_count,
        "comparison_completed_count": comparison_count,
        "g2pk_expected_phone_count": sum(
            result.g2pk_expected_phone_count for result in comparisons
        ),
        "aihub_expected_phone_count": sum(
            result.aihub_expected_phone_count for result in comparisons
        ),
        "levenshtein_distance_sum": sum(
            result.levenshtein_distance for result in comparisons
        ),
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


def _metadata_structural_error_count(counters: Counter[str]) -> int:
    return sum(
        counters[key]
        for key in (
            "schema_error_count",
            "identity_shape_error_count",
            "split_contract_error_count",
            "json_reference_contract_error_count",
            "prompt_sha256_contract_error_count",
            "duplicate_identity_count",
            "duplicate_json_reference_count",
            "metadata_row_count_mismatch_count",
            "sample_cardinality_error_count",
        )
    )


def _punctuation_policy_digest() -> str:
    return hashlib.sha256(
        json.dumps(
            FIXED_ALLOWED_PUNCTUATION,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


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


def run_current_dev_smoke(
    configuration: CurrentDevSmokeConfiguration,
    *,
    generator: _ExpectedPhoneGenerator,
) -> dict[str, object]:
    """Run the fixed project-dev expected-source diagnostic."""

    validated = _validate_configuration(configuration)
    input_sha256, implementation_sha256 = _verify_start_snapshot(validated)
    expected_assignment_sha256 = _load_split_audit(
        validated.split_audit_path,
        expected_modeling_manifest_sha256=validated.expected_modeling_manifest_sha256,
    )
    metadata = _scan_current_metadata(validated)
    if metadata.split_assignment_sha256 != expected_assignment_sha256:
        raise CurrentDevSmokeError(
            "current split assignment differs from the split-currency audit"
        )
    if (
        _sha256_file(validated.modeling_manifest_path)
        != input_sha256["modeling_manifest"]
    ):
        raise CurrentDevSmokeError(
            "current modeling manifest changed before raw source access"
        )
    raw_audit = _load_selected_expected_json(
        metadata.selected_rows,
        configuration=validated,
    )
    smoke, failures, network_attempts = _aggregate_comparisons(
        raw_audit.selected_rows,
        configuration=validated,
        generator=generator,
    )
    end_inputs = {
        "modeling_manifest": _sha256_file(validated.modeling_manifest_path),
        "split_currency_audit": _sha256_file(validated.split_audit_path),
    }
    if end_inputs != input_sha256:
        raise CurrentDevSmokeError("an input changed during execution")
    if _capture_implementation_sha256() != implementation_sha256:
        raise CurrentDevSmokeError(
            "implementation or environment lock changed during execution"
        )
    structural_error_count = _metadata_structural_error_count(metadata.counters)
    is_complete = (
        structural_error_count == 0
        and raw_audit.counters["expected_label_json_access_completed_count"]
        == validated.sample_size
        and smoke["comparison_completed_count"] == validated.sample_size
        and not failures
        and not network_attempts
    )
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": (
            "passed_current_project_dev_diagnostic"
            if is_complete
            else "incomplete_current_project_dev_diagnostic"
        ),
        "scope": {
            "dataset_id": EXPECTED_DATASET_ID,
            "split": FIXED_SPLIT,
            "split_source": "current_modeling_manifest",
            "development_only": True,
            "g2pk_unique_row_limit": MAXIMUM_SAMPLE_SIZE,
            "expected_label_source_role": "expected_aihub_json_annotation",
            "g2pk_source_role": "expected_g2pk_transcript_conditioned",
            "audio_accessed": False,
            "textgrid_accessed": False,
            "training_run": False,
            "locked_evaluation_run": False,
        },
        "provenance": {
            "input_sha256": input_sha256,
            "implementation_sha256": implementation_sha256,
            "current_split_assignment_sha256": (metadata.split_assignment_sha256),
            "sample_selection_sha256": metadata.sample_selection_sha256,
            "selected_raw_content_aggregate_sha256": (
                raw_audit.raw_content_aggregate_sha256
            ),
            "inputs_verified_at_start": True,
            "modeling_manifest_unchanged_before_raw_access": True,
            "inputs_unchanged_at_end": True,
            "implementation_verified_at_start": True,
            "implementation_unchanged_at_end": True,
            "runtime": {
                "python": (
                    f"{sys.version_info.major}.{sys.version_info.minor}."
                    f"{sys.version_info.micro}"
                ),
                "pyarrow": pa.__version__,
                "g2pk": EXPECTED_G2PK_VERSION,
            },
        },
        "policies": {
            "sample_policy_id": SAMPLE_POLICY_ID,
            "sample_seed": validated.seed,
            "sample_size": validated.sample_size,
            "sample_population": (
                "all_current_project_dev_metadata_before_raw_expected_label_access"
            ),
            "descriptive": validated.descriptive,
            "group_vowels": validated.group_vowels,
            "word_boundary_policy": validated.word_boundary_policy,
            "punctuation_policy_id": validated.punctuation_policy,
            "allowed_punctuation_count": len(FIXED_ALLOWED_PUNCTUATION),
            "allowed_punctuation_sha256": _punctuation_policy_digest(),
            "aihub_annotation_boundary_policy_id": BOUNDARY_POLICY_ID,
            "noncanonical_annotation_action": "reject_row_after_selection",
            "maximum_codepoints_resource_cap": validated.maximum_codepoints,
            "max_alignment_cells_resource_cap": validated.max_alignment_cells,
            "scientific_decision_threshold": None,
        },
        "metadata_audit": {
            "manifest_row_count": metadata.counters["manifest_row_count"],
            "structural_error_count": structural_error_count,
            "split_counts": {
                split: metadata.split_counts[split] for split in CURRENT_SPLITS
            },
            "dev_population_row_count": metadata.counters["dev_population_row_count"],
            "non_dev_row_skipped_without_raw_source_access_count": (
                metadata.counters["non_dev_row_skipped_without_raw_source_access_count"]
            ),
            "duplicate_identity_count": metadata.counters["duplicate_identity_count"],
            "duplicate_json_reference_count": metadata.counters[
                "duplicate_json_reference_count"
            ],
        },
        "selected_expected_label_audit": {
            "expected_label_json_access_attempt_count": raw_audit.counters[
                "expected_label_json_access_attempt_count"
            ],
            "expected_label_json_access_completed_count": raw_audit.counters[
                "expected_label_json_access_completed_count"
            ],
            "access_count_by_current_split": {
                split: raw_audit.access_by_split[split] for split in CURRENT_SPLITS
            },
            "selected_transcript_with_punctuation_count": raw_audit.counters[
                "selected_transcript_with_punctuation_count"
            ],
            "selected_transcript_punctuation_occurrence_count": (
                raw_audit.counters["selected_transcript_punctuation_occurrence_count"]
            ),
            "selected_transcript_outside_punctuation_policy_count": (
                raw_audit.counters[
                    "selected_transcript_outside_punctuation_policy_count"
                ]
            ),
            "selected_transcript_over_resource_cap_count": raw_audit.counters[
                "selected_transcript_over_resource_cap_count"
            ],
            "maximum_transcript_codepoints_observed": (
                raw_audit.maximum_transcript_codepoints_observed
            ),
            "punctuation_vocabulary_count": (raw_audit.punctuation_vocabulary_count),
            "punctuation_vocabulary_sha256": (raw_audit.punctuation_vocabulary_sha256),
            "selected_annotation_phone_occurrence_count": raw_audit.counters[
                "selected_annotation_phone_occurrence_count"
            ],
            "selected_canonical_phone_occurrence_count": raw_audit.counters[
                "selected_canonical_phone_occurrence_count"
            ],
            "selected_noncanonical_phone_occurrence_count": (
                raw_audit.counters["selected_noncanonical_phone_occurrence_count"]
            ),
            "selected_row_with_noncanonical_phone_count": raw_audit.counters[
                "selected_row_with_noncanonical_phone_count"
            ],
            "noncanonical_vocabulary_count": (raw_audit.noncanonical_vocabulary_count),
            "noncanonical_vocabulary_sha256": (
                raw_audit.noncanonical_vocabulary_sha256
            ),
            "primary_rejection_reason_counts": dict(
                sorted(raw_audit.rejection_reason_counts.items())
            ),
            "rejection_condition_counts": dict(
                sorted(raw_audit.rejection_condition_counts.items())
            ),
        },
        "smoke": smoke,
        "failure_reason_counts": dict(sorted(failures.items())),
        "runtime_access_audit": {
            "network_socket_operations_blocked": True,
            "network_socket_attempt_count": sum(network_attempts.values()),
            "expected_label_json_current_dev_access_count": (
                raw_audit.access_by_split[FIXED_SPLIT]
            ),
            "expected_label_json_non_dev_access_count": sum(
                raw_audit.access_by_split[split]
                for split in CURRENT_SPLITS
                if split != FIXED_SPLIT
            ),
            "audio_file_access_count": 0,
            "textgrid_file_access_count": 0,
            "nltk_data_contract": ("absolute_pinned_root_and_exact_adapter_digests"),
            "hybrid_runtime_locks": (
                "paper_g2pk_lock_plus_scorer_lock_for_pinned_pyarrow"
            ),
        },
        "serialized_sensitive_fields": {
            "transcript": False,
            "phone_sequence": False,
            "row_identifier": False,
            "speaker_identifier": False,
            "source_path": False,
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
    temporary_path: Path | None = None
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
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise CurrentDevSmokeError(
            "current-dev aggregate artifact could not be written"
        ) from None


def _parse_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("must be exactly true or false")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse caller-explicit provenance, policy, and resource arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run an aggregate-only g2pK expected-source smoke on the current "
            "project dev split."
        )
    )
    parser.add_argument("--modeling-manifest", required=True)
    parser.add_argument("--modeling-manifest-sha256", required=True)
    parser.add_argument("--split-audit", required=True)
    parser.add_argument("--split-audit-sha256", required=True)
    parser.add_argument("--runner-sha256", required=True)
    parser.add_argument("--agreement-source-sha256", required=True)
    parser.add_argument("--g2pk-adapter-source-sha256", required=True)
    parser.add_argument("--phone-inventory-source-sha256", required=True)
    parser.add_argument("--modeling-manifest-builder-sha256", required=True)
    parser.add_argument("--g2pk-environment-lock-sha256", required=True)
    parser.add_argument("--scorer-environment-lock-sha256", required=True)
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


def _configuration_from_args(
    args: argparse.Namespace,
) -> CurrentDevSmokeConfiguration:
    return CurrentDevSmokeConfiguration(
        modeling_manifest_path=Path(args.modeling_manifest).resolve(),
        expected_modeling_manifest_sha256=args.modeling_manifest_sha256,
        split_audit_path=Path(args.split_audit).resolve(),
        expected_split_audit_sha256=args.split_audit_sha256,
        expected_runner_sha256=args.runner_sha256,
        expected_agreement_source_sha256=args.agreement_source_sha256,
        expected_g2pk_adapter_source_sha256=(args.g2pk_adapter_source_sha256),
        expected_phone_inventory_source_sha256=(args.phone_inventory_source_sha256),
        expected_modeling_manifest_builder_sha256=(
            args.modeling_manifest_builder_sha256
        ),
        expected_g2pk_environment_lock_sha256=(args.g2pk_environment_lock_sha256),
        expected_scorer_environment_lock_sha256=(args.scorer_environment_lock_sha256),
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
    """Run the bounded current-dev diagnostic and write its aggregate."""

    args = parse_args(argv)
    configuration = _configuration_from_args(args)
    try:
        report = run_current_dev_smoke(
            configuration,
            generator=generate_g2pk_expected_phones,
        )
        _write_json_atomic(configuration.output_path, report)
    except CurrentDevSmokeError as error:
        print(f"configuration_or_audit_error={error}", file=sys.stderr)
        return 2
    smoke = cast(dict[str, object], report["smoke"])
    metadata = cast(dict[str, object], report["metadata_audit"])
    access = cast(dict[str, object], report["runtime_access_audit"])
    print(
        json.dumps(
            {
                "status": report["status"],
                "dev_population_row_count": metadata["dev_population_row_count"],
                "sample_fixed_count": smoke["sample_fixed_before_raw_access_count"],
                "comparison_completed_count": smoke["comparison_completed_count"],
                "non_dev_expected_label_json_access_count": access[
                    "expected_label_json_non_dev_access_count"
                ],
                "artifact_payload_sha256": report["artifact_payload_sha256"],
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed_current_project_dev_diagnostic" else 1


if __name__ == "__main__":
    raise SystemExit(main())
