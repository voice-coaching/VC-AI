#!/usr/bin/env python3
"""Audit legacy/current AI Hub split currency without opening corpus sources.

Only the legacy metadata JSONL, current modeling Parquet, the legacy aggregate
artifact, and pinned implementation files are accepted as inputs.  Dataset
roots, expected-label JSON, audio, and TextGrid paths are not accepted by this
entrypoint.  Row identifiers and source references are used only in memory and
are never serialized.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import heapq
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


REPORT_SCHEMA_VERSION = "voice_coach.g2pk_aihub71490_split_currency_audit.v1"
LEGACY_MANIFEST_SCHEMA_VERSION = "aihub71490.raw.v2"
CURRENT_MANIFEST_SCHEMA_VERSION = "aihub71490.enriched.v1"
LEGACY_ARTIFACT_SCHEMA_VERSION = "voice_coach.g2pk_aihub71490_dev_smoke.v2"
EXPECTED_DATASET_ID = "71490"
CURRENT_SPLITS = ("calibration", "dev", "locked_test", "stack", "train")
LEGACY_SELECTION_SPLIT = "dev"
FIXED_SEED = 42
FIXED_SAMPLE_SIZE = 16
SAMPLE_POLICY_ID = "sha256_domain_seed_row_identity_target_reference_v1"
SAMPLE_DOMAIN = b"aihub71490-g2pk-dev-smoke-v1\0"
KNOWN_LEGACY_SELECTION_SHA256 = (
    "4ce87fb7ebd6f14e36c5e56ee118b72bc36469cd85de68feac8bdeb4937ad1ea"
)
_EXPECTED_TARGET_FORMAT = "aihub_pronunciation_json"
_EXPECTED_TARGET_POINTER = "/RecordingMetadata/phonemic/phones"
_WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
_IMPLEMENTATION_PATHS = {
    "audit_runner": Path(__file__).resolve(),
    "legacy_smoke_runner": (
        _WORKSPACE_ROOT / "scripts" / "run_g2pk_aihub71490_dev_smoke.py"
    ),
    "modeling_manifest_builder": (
        _WORKSPACE_ROOT / "scripts" / "build_aihub71490_modeling_manifest.py"
    ),
    "scorer_environment_lock": (
        _WORKSPACE_ROOT / "requirements" / "scorer-lock-linux-aarch64-cu130.txt"
    ),
}


class SplitCurrencyAuditError(ValueError):
    """Raised when an audit input or invariant is unsafe or inconsistent."""


@dataclass(frozen=True, slots=True)
class SplitCurrencyAuditConfiguration:
    """Caller-pinned metadata, implementation, and output contract."""

    legacy_manifest_path: Path
    expected_legacy_manifest_sha256: str
    current_manifest_path: Path
    expected_current_manifest_sha256: str
    legacy_artifact_path: Path
    expected_legacy_artifact_sha256: str
    expected_audit_runner_sha256: str
    expected_legacy_smoke_runner_sha256: str
    expected_modeling_manifest_builder_sha256: str
    expected_scorer_environment_lock_sha256: str
    output_path: Path
    seed: int = FIXED_SEED
    sample_size: int = FIXED_SAMPLE_SIZE
    expected_legacy_selection_sha256: str = KNOWN_LEGACY_SELECTION_SHA256


@dataclass(frozen=True, slots=True)
class _CurrentAssignment:
    """Current split state retained only for the in-memory exact join."""

    split: str
    json_reference: str


@dataclass(frozen=True, slots=True)
class _LegacySelectionCandidate:
    """Privacy-restricted selection state that is never serialized."""

    selection_score: bytes
    current_split: str


@dataclass(frozen=True, slots=True)
class _CurrentManifestState:
    """Current manifest aggregate and exact in-memory join map."""

    assignments: dict[str, _CurrentAssignment]
    counters: Counter[str]
    split_counts: Counter[str]
    split_assignment_sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise SplitCurrencyAuditError(
            "a required provenance file could not be read"
        ) from None
    return digest.hexdigest()


def _validate_sha256(value: object, *, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SplitCurrencyAuditError(
            f"{field_name} must be a lowercase SHA-256 digest"
        )
    return cast(str, value)


def _real_regular_file(path: object, *, field_name: str) -> Path:
    if not isinstance(path, Path):
        raise SplitCurrencyAuditError(f"{field_name} must be a Path")
    candidate = path
    if not candidate.is_absolute():
        raise SplitCurrencyAuditError(f"{field_name} must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
        information = candidate.lstat()
    except (OSError, RuntimeError):
        raise SplitCurrencyAuditError(f"{field_name} is unavailable") from None
    if (
        resolved != candidate
        or candidate.is_symlink()
        or not stat.S_ISREG(information.st_mode)
    ):
        raise SplitCurrencyAuditError(f"{field_name} must be a real regular file")
    return candidate


def _validate_configuration(
    value: object,
) -> SplitCurrencyAuditConfiguration:
    if type(value) is not SplitCurrencyAuditConfiguration:
        raise SplitCurrencyAuditError(
            "configuration must be an exact SplitCurrencyAuditConfiguration"
        )
    configuration = cast(SplitCurrencyAuditConfiguration, value)
    if configuration.seed != FIXED_SEED:
        raise SplitCurrencyAuditError("seed differs from the legacy contract")
    if configuration.sample_size != FIXED_SAMPLE_SIZE:
        raise SplitCurrencyAuditError("sample_size differs from the legacy contract")
    if configuration.output_path.exists() and configuration.output_path.is_symlink():
        raise SplitCurrencyAuditError("output_path must not be a symlink")
    if configuration.output_path.name in {"", ".", ".."}:
        raise SplitCurrencyAuditError("output_path must name an artifact file")
    return SplitCurrencyAuditConfiguration(
        legacy_manifest_path=_real_regular_file(
            configuration.legacy_manifest_path,
            field_name="legacy_manifest_path",
        ),
        expected_legacy_manifest_sha256=_validate_sha256(
            configuration.expected_legacy_manifest_sha256,
            field_name="expected_legacy_manifest_sha256",
        ),
        current_manifest_path=_real_regular_file(
            configuration.current_manifest_path,
            field_name="current_manifest_path",
        ),
        expected_current_manifest_sha256=_validate_sha256(
            configuration.expected_current_manifest_sha256,
            field_name="expected_current_manifest_sha256",
        ),
        legacy_artifact_path=_real_regular_file(
            configuration.legacy_artifact_path,
            field_name="legacy_artifact_path",
        ),
        expected_legacy_artifact_sha256=_validate_sha256(
            configuration.expected_legacy_artifact_sha256,
            field_name="expected_legacy_artifact_sha256",
        ),
        expected_audit_runner_sha256=_validate_sha256(
            configuration.expected_audit_runner_sha256,
            field_name="expected_audit_runner_sha256",
        ),
        expected_legacy_smoke_runner_sha256=_validate_sha256(
            configuration.expected_legacy_smoke_runner_sha256,
            field_name="expected_legacy_smoke_runner_sha256",
        ),
        expected_modeling_manifest_builder_sha256=_validate_sha256(
            configuration.expected_modeling_manifest_builder_sha256,
            field_name="expected_modeling_manifest_builder_sha256",
        ),
        expected_scorer_environment_lock_sha256=_validate_sha256(
            configuration.expected_scorer_environment_lock_sha256,
            field_name="expected_scorer_environment_lock_sha256",
        ),
        output_path=configuration.output_path,
        seed=configuration.seed,
        sample_size=configuration.sample_size,
        expected_legacy_selection_sha256=_validate_sha256(
            configuration.expected_legacy_selection_sha256,
            field_name="expected_legacy_selection_sha256",
        ),
    )


def _capture_implementation_sha256() -> dict[str, str]:
    return {
        role: _sha256_file(_real_regular_file(path, field_name=f"{role}_path"))
        for role, path in _IMPLEMENTATION_PATHS.items()
    }


def _expected_implementation_sha256(
    configuration: SplitCurrencyAuditConfiguration,
) -> dict[str, str]:
    return {
        "audit_runner": configuration.expected_audit_runner_sha256,
        "legacy_smoke_runner": (configuration.expected_legacy_smoke_runner_sha256),
        "modeling_manifest_builder": (
            configuration.expected_modeling_manifest_builder_sha256
        ),
        "scorer_environment_lock": (
            configuration.expected_scorer_environment_lock_sha256
        ),
    }


def _verify_start_snapshot(
    configuration: SplitCurrencyAuditConfiguration,
) -> tuple[dict[str, str], dict[str, str]]:
    input_sha256 = {
        "legacy_manifest": _sha256_file(configuration.legacy_manifest_path),
        "current_manifest": _sha256_file(configuration.current_manifest_path),
        "legacy_aggregate_artifact": _sha256_file(configuration.legacy_artifact_path),
    }
    expected_input_sha256 = {
        "legacy_manifest": configuration.expected_legacy_manifest_sha256,
        "current_manifest": configuration.expected_current_manifest_sha256,
        "legacy_aggregate_artifact": (configuration.expected_legacy_artifact_sha256),
    }
    if input_sha256 != expected_input_sha256:
        raise SplitCurrencyAuditError(
            "an input digest differs from the pinned provenance"
        )
    implementation_sha256 = _capture_implementation_sha256()
    if implementation_sha256 != _expected_implementation_sha256(configuration):
        raise SplitCurrencyAuditError(
            "implementation or environment-lock digest differs from the pin"
        )
    return input_sha256, implementation_sha256


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


def _push_candidate(
    heap: list[tuple[int, int, _LegacySelectionCandidate]],
    *,
    sample_size: int,
    ordinal: int,
    candidate: _LegacySelectionCandidate,
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
    expected_fields = ("schema_version", "id", "split", "json_path")
    for name in expected_fields:
        if name not in schema.names or not pa.types.is_string(schema.field(name).type):
            raise SplitCurrencyAuditError(
                "current modeling manifest schema violates the audit contract"
            )


def _load_current_manifest(
    path: Path,
) -> _CurrentManifestState:
    try:
        parquet = pq.ParquetFile(path)
    except (OSError, ValueError):
        raise SplitCurrencyAuditError(
            "current modeling manifest could not be opened"
        ) from None
    _validate_current_schema(parquet)
    assignments: dict[str, _CurrentAssignment] = {}
    json_references: set[str] = set()
    counters: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    assignment_digest = hashlib.sha256()
    try:
        batches = parquet.iter_batches(
            batch_size=4096,
            columns=["schema_version", "id", "split", "json_path"],
        )
        for batch in batches:
            rows = cast(list[dict[str, object]], batch.to_pylist())
            for row in rows:
                counters["row_count"] += 1
                schema_version = row.get("schema_version")
                row_identity = row.get("id")
                split = row.get("split")
                json_reference = _safe_relative_json_reference(row.get("json_path"))
                if schema_version != CURRENT_MANIFEST_SCHEMA_VERSION:
                    counters["schema_error_count"] += 1
                if type(row_identity) is not str or not row_identity:
                    counters["identity_shape_error_count"] += 1
                    continue
                if type(split) is not str or split not in CURRENT_SPLITS:
                    counters["split_contract_error_count"] += 1
                    continue
                if json_reference is None:
                    counters["json_reference_contract_error_count"] += 1
                    continue
                identity_value = cast(str, row_identity)
                split_value = cast(str, split)
                if identity_value in assignments:
                    counters["duplicate_identity_count"] += 1
                    continue
                if json_reference in json_references:
                    counters["duplicate_json_reference_count"] += 1
                    continue
                assignments[identity_value] = _CurrentAssignment(
                    split=split_value,
                    json_reference=json_reference,
                )
                json_references.add(json_reference)
                split_counts[split_value] += 1
                _update_framed(
                    assignment_digest,
                    identity_value.encode("utf-8"),
                )
                _update_framed(
                    assignment_digest,
                    split_value.encode("ascii"),
                )
    except (OSError, ValueError):
        raise SplitCurrencyAuditError(
            "current modeling manifest could not be scanned"
        ) from None
    if parquet.metadata.num_rows != counters["row_count"]:
        counters["metadata_row_count_mismatch_count"] += 1
    return _CurrentManifestState(
        assignments=assignments,
        counters=counters,
        split_counts=split_counts,
        split_assignment_sha256=assignment_digest.hexdigest(),
    )


def _load_legacy_artifact(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise SplitCurrencyAuditError(
            "legacy aggregate artifact could not be parsed"
        ) from None
    if type(payload) is not dict:
        raise SplitCurrencyAuditError(
            "legacy aggregate artifact has an invalid root shape"
        )
    artifact = cast(dict[str, object], payload)
    if artifact.get("schema_version") != LEGACY_ARTIFACT_SCHEMA_VERSION:
        raise SplitCurrencyAuditError(
            "legacy aggregate artifact schema differs from the expected version"
        )
    provenance = artifact.get("provenance")
    scope = artifact.get("scope")
    if type(provenance) is not dict or type(scope) is not dict:
        raise SplitCurrencyAuditError(
            "legacy aggregate artifact lacks required aggregate fields"
        )
    return artifact


def _legacy_target_reference(row: dict[str, object]) -> str | None:
    phone_sources = row.get("phone_sources")
    source_paths = row.get("source_paths")
    target = (
        cast(dict[str, object], phone_sources).get("target")
        if type(phone_sources) is dict
        else None
    )
    if type(target) is not dict or type(source_paths) is not dict:
        return None
    target_fields = cast(dict[str, object], target)
    if (
        target_fields.get("format") != _EXPECTED_TARGET_FORMAT
        or target_fields.get("json_pointer") != _EXPECTED_TARGET_POINTER
    ):
        return None
    target_reference = _safe_relative_json_reference(target_fields.get("path"))
    source_reference = _safe_relative_json_reference(
        cast(dict[str, object], source_paths).get("json")
    )
    if target_reference is None or source_reference != target_reference:
        return None
    return target_reference


def _scan_legacy_and_join(
    configuration: SplitCurrencyAuditConfiguration,
    current_state: _CurrentManifestState,
) -> tuple[
    Counter[str],
    Counter[tuple[str, str]],
    Counter[str],
    str,
]:
    counters: Counter[str] = Counter()
    confusion: Counter[tuple[str, str]] = Counter()
    legacy_split_counts: Counter[str] = Counter()
    legacy_identities: set[str] = set()
    legacy_json_references: set[str] = set()
    selection_heap: list[tuple[int, int, _LegacySelectionCandidate]] = []
    remaining_current = dict(current_state.assignments)
    try:
        handle = configuration.legacy_manifest_path.open("r", encoding="utf-8")
    except OSError:
        raise SplitCurrencyAuditError(
            "legacy metadata manifest could not be opened"
        ) from None
    with handle:
        for line_number, line in enumerate(handle, start=1):
            counters["row_count"] += 1
            try:
                parsed = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                counters["parse_error_count"] += 1
                continue
            if type(parsed) is not dict:
                counters["non_object_row_count"] += 1
                continue
            row = cast(dict[str, object], parsed)
            if row.get("schema_version") != LEGACY_MANIFEST_SCHEMA_VERSION:
                counters["schema_error_count"] += 1
            if row.get("dataset_id") != EXPECTED_DATASET_ID:
                counters["dataset_error_count"] += 1
            row_identity = row.get("id")
            legacy_split = row.get("split")
            target_reference = _legacy_target_reference(row)
            if type(row_identity) is not str or not row_identity:
                counters["identity_shape_error_count"] += 1
                continue
            if type(legacy_split) is not str or legacy_split not in CURRENT_SPLITS:
                counters["split_contract_error_count"] += 1
                continue
            if target_reference is None:
                counters["json_reference_contract_error_count"] += 1
                continue
            identity_value = cast(str, row_identity)
            legacy_split_value = cast(str, legacy_split)
            if identity_value in legacy_identities:
                counters["duplicate_identity_count"] += 1
                continue
            if target_reference in legacy_json_references:
                counters["duplicate_json_reference_count"] += 1
                continue
            legacy_identities.add(identity_value)
            legacy_json_references.add(target_reference)
            legacy_split_counts[legacy_split_value] += 1
            current_assignment = remaining_current.pop(identity_value, None)
            if current_assignment is None:
                counters["legacy_without_current_match_count"] += 1
                continue
            counters["joined_row_count"] += 1
            confusion[(legacy_split_value, current_assignment.split)] += 1
            if current_assignment.json_reference != target_reference:
                counters["joined_json_reference_mismatch_count"] += 1
            if legacy_split_value == LEGACY_SELECTION_SPLIT:
                _push_candidate(
                    selection_heap,
                    sample_size=configuration.sample_size,
                    ordinal=line_number,
                    candidate=_LegacySelectionCandidate(
                        selection_score=_selection_score(
                            seed=configuration.seed,
                            row_identity=identity_value,
                            target_reference=target_reference,
                        ),
                        current_split=current_assignment.split,
                    ),
                )
    counters["current_without_legacy_match_count"] = len(remaining_current)
    counters["unique_identity_count"] = len(legacy_identities)
    counters["unique_json_reference_count"] = len(legacy_json_references)
    for split in CURRENT_SPLITS:
        counters[f"split_{split}_count"] = legacy_split_counts[split]
    selected = tuple(
        item[2]
        for item in sorted(
            selection_heap,
            key=lambda item: item[2].selection_score,
        )
    )
    if len(selected) != configuration.sample_size:
        counters["legacy_selection_cardinality_error_count"] += 1
    selection_digest = hashlib.sha256()
    selected_current_splits: Counter[str] = Counter()
    for candidate in selected:
        _update_framed(selection_digest, candidate.selection_score)
        selected_current_splits[candidate.current_split] += 1
    return (
        counters,
        confusion,
        selected_current_splits,
        selection_digest.hexdigest(),
    )


def _structural_error_count(
    current_counters: Counter[str],
    legacy_counters: Counter[str],
) -> int:
    current_error_keys = (
        "schema_error_count",
        "identity_shape_error_count",
        "split_contract_error_count",
        "json_reference_contract_error_count",
        "duplicate_identity_count",
        "duplicate_json_reference_count",
        "metadata_row_count_mismatch_count",
    )
    legacy_error_keys = (
        "parse_error_count",
        "non_object_row_count",
        "schema_error_count",
        "dataset_error_count",
        "identity_shape_error_count",
        "split_contract_error_count",
        "json_reference_contract_error_count",
        "duplicate_identity_count",
        "duplicate_json_reference_count",
        "legacy_without_current_match_count",
        "current_without_legacy_match_count",
        "joined_json_reference_mismatch_count",
        "legacy_selection_cardinality_error_count",
    )
    return sum(current_counters[key] for key in current_error_keys) + sum(
        legacy_counters[key] for key in legacy_error_keys
    )


def _confusion_matrix(
    confusion: Counter[tuple[str, str]],
) -> dict[str, dict[str, int]]:
    return {
        legacy_split: {
            current_split: confusion[(legacy_split, current_split)]
            for current_split in CURRENT_SPLITS
        }
        for legacy_split in CURRENT_SPLITS
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


def run_split_currency_audit(
    configuration: SplitCurrencyAuditConfiguration,
) -> dict[str, object]:
    """Return a privacy-minimal exact split-currency audit."""

    validated = _validate_configuration(configuration)
    input_sha256, implementation_sha256 = _verify_start_snapshot(validated)
    legacy_artifact = _load_legacy_artifact(validated.legacy_artifact_path)
    current_state = _load_current_manifest(validated.current_manifest_path)
    (
        legacy_counters,
        confusion,
        selected_current_splits,
        legacy_selection_sha256,
    ) = _scan_legacy_and_join(validated, current_state)
    legacy_scope = cast(dict[str, object], legacy_artifact["scope"])
    legacy_provenance = cast(dict[str, object], legacy_artifact["provenance"])
    artifact_selection_sha256 = legacy_provenance.get("sample_selection_sha256")
    selection_evidence_matches = (
        legacy_selection_sha256 == validated.expected_legacy_selection_sha256
        and artifact_selection_sha256 == validated.expected_legacy_selection_sha256
    )
    structural_error_count = _structural_error_count(
        current_state.counters,
        legacy_counters,
    )
    input_end_sha256 = {
        "legacy_manifest": _sha256_file(validated.legacy_manifest_path),
        "current_manifest": _sha256_file(validated.current_manifest_path),
        "legacy_aggregate_artifact": _sha256_file(validated.legacy_artifact_path),
    }
    implementation_end_sha256 = _capture_implementation_sha256()
    if input_end_sha256 != input_sha256:
        raise SplitCurrencyAuditError("an input changed during the audit")
    if implementation_end_sha256 != implementation_sha256:
        raise SplitCurrencyAuditError(
            "implementation or environment lock changed during the audit"
        )
    selected_non_dev_count = validated.sample_size - selected_current_splits["dev"]
    disposition = (
        "superseded_current_split_drift"
        if selection_evidence_matches and selected_non_dev_count > 0
        else "not_superseded_by_selected_split_audit"
    )
    is_complete = structural_error_count == 0 and selection_evidence_matches
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": (
            "split_currency_audit_complete"
            if is_complete
            else "split_currency_audit_incomplete"
        ),
        "scope": {
            "dataset_id": EXPECTED_DATASET_ID,
            "metadata_only": True,
            "row_identifiers_serialized": False,
            "source_paths_serialized": False,
            "transcripts_serialized": False,
            "phone_sequences_serialized": False,
        },
        "provenance": {
            "input_sha256": input_sha256,
            "implementation_sha256": implementation_sha256,
            "current_split_assignment_sha256": (current_state.split_assignment_sha256),
            "current_split_assignment_order": "manifest_physical_row_order",
            "inputs_verified_at_start": True,
            "inputs_unchanged_at_end": True,
            "implementation_verified_at_start": True,
            "implementation_unchanged_at_end": True,
            "runtime": {
                "python": (
                    f"{sys.version_info.major}.{sys.version_info.minor}."
                    f"{sys.version_info.micro}"
                ),
                "pyarrow": pa.__version__,
            },
        },
        "join_audit": {
            "legacy_row_count": legacy_counters["row_count"],
            "legacy_unique_identity_count": legacy_counters["unique_identity_count"],
            "current_row_count": current_state.counters["row_count"],
            "current_unique_identity_count": len(current_state.assignments),
            "joined_row_count": legacy_counters["joined_row_count"],
            "legacy_without_current_match_count": legacy_counters[
                "legacy_without_current_match_count"
            ],
            "current_without_legacy_match_count": legacy_counters[
                "current_without_legacy_match_count"
            ],
            "legacy_duplicate_identity_count": legacy_counters[
                "duplicate_identity_count"
            ],
            "current_duplicate_identity_count": current_state.counters[
                "duplicate_identity_count"
            ],
            "joined_json_reference_mismatch_count": legacy_counters[
                "joined_json_reference_mismatch_count"
            ],
            "structural_error_count": structural_error_count,
            "legacy_split_counts": {
                split: legacy_counters[f"split_{split}_count"]
                for split in CURRENT_SPLITS
            },
            "current_split_counts": {
                split: current_state.split_counts[split] for split in CURRENT_SPLITS
            },
            "legacy_to_current_confusion_matrix": _confusion_matrix(confusion),
        },
        "legacy_selection_replay": {
            "sample_policy_id": SAMPLE_POLICY_ID,
            "seed": validated.seed,
            "sample_size": validated.sample_size,
            "selection_sha256": legacy_selection_sha256,
            "expected_selection_sha256": (validated.expected_legacy_selection_sha256),
            "legacy_artifact_selection_sha256": artifact_selection_sha256,
            "selection_evidence_matches": selection_evidence_matches,
            "selected_current_split_counts": {
                split: selected_current_splits[split] for split in CURRENT_SPLITS
            },
            "selected_current_non_dev_count": selected_non_dev_count,
        },
        "legacy_evidence_disposition": {
            "legacy_artifact_sha256": input_sha256["legacy_aggregate_artifact"],
            "legacy_reported_split": legacy_scope.get("split"),
            "legacy_reported_development_only": legacy_scope.get("development_only"),
            "disposition": disposition,
            "reason_code": (
                "legacy_fixed_sample_contains_current_non_dev_rows"
                if disposition == "superseded_current_split_drift"
                else None
            ),
            "legacy_metric_authorized_as_current_dev_evidence": False,
        },
        "source_access_audit": {
            "legacy_metadata_jsonl_file_access_count": 1,
            "current_modeling_parquet_file_access_count": 1,
            "legacy_aggregate_artifact_file_access_count": 1,
            "dataset_root_argument_supported": False,
            "expected_label_json_file_access_count": 0,
            "audio_file_access_count": 0,
            "textgrid_file_access_count": 0,
            "network_access_count": 0,
        },
        "claim_limits": {
            "agreement_metric_recomputed": False,
            "expected_label_json_evaluated": False,
            "observed_pronunciation_evaluated": False,
            "accuracy_established": False,
            "phase1_m1_complete": False,
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
        raise SplitCurrencyAuditError(
            "split-currency artifact could not be written"
        ) from None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse explicit metadata-only provenance and output arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Audit legacy/current split currency without accepting a corpus "
            "dataset root."
        )
    )
    parser.add_argument("--legacy-manifest", required=True)
    parser.add_argument("--legacy-manifest-sha256", required=True)
    parser.add_argument("--current-manifest", required=True)
    parser.add_argument("--current-manifest-sha256", required=True)
    parser.add_argument("--legacy-artifact", required=True)
    parser.add_argument("--legacy-artifact-sha256", required=True)
    parser.add_argument("--audit-runner-sha256", required=True)
    parser.add_argument("--legacy-smoke-runner-sha256", required=True)
    parser.add_argument("--modeling-manifest-builder-sha256", required=True)
    parser.add_argument("--scorer-environment-lock-sha256", required=True)
    parser.add_argument("--expected-legacy-selection-sha256", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--sample-size", required=True, type=int)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def _configuration_from_args(
    args: argparse.Namespace,
) -> SplitCurrencyAuditConfiguration:
    return SplitCurrencyAuditConfiguration(
        legacy_manifest_path=Path(args.legacy_manifest).resolve(),
        expected_legacy_manifest_sha256=args.legacy_manifest_sha256,
        current_manifest_path=Path(args.current_manifest).resolve(),
        expected_current_manifest_sha256=args.current_manifest_sha256,
        legacy_artifact_path=Path(args.legacy_artifact).resolve(),
        expected_legacy_artifact_sha256=args.legacy_artifact_sha256,
        expected_audit_runner_sha256=args.audit_runner_sha256,
        expected_legacy_smoke_runner_sha256=(args.legacy_smoke_runner_sha256),
        expected_modeling_manifest_builder_sha256=(
            args.modeling_manifest_builder_sha256
        ),
        expected_scorer_environment_lock_sha256=(args.scorer_environment_lock_sha256),
        output_path=Path(args.output).resolve(),
        seed=args.seed,
        sample_size=args.sample_size,
        expected_legacy_selection_sha256=(args.expected_legacy_selection_sha256),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the metadata-only audit and write one aggregate artifact."""

    args = parse_args(argv)
    configuration = _configuration_from_args(args)
    try:
        report = run_split_currency_audit(configuration)
        _write_json_atomic(configuration.output_path, report)
    except SplitCurrencyAuditError as error:
        print(f"configuration_or_audit_error={error}", file=sys.stderr)
        return 2
    selection = cast(dict[str, object], report["legacy_selection_replay"])
    disposition = cast(dict[str, object], report["legacy_evidence_disposition"])
    print(
        json.dumps(
            {
                "status": report["status"],
                "selection_sha256": selection["selection_sha256"],
                "selected_current_split_counts": selection[
                    "selected_current_split_counts"
                ],
                "legacy_disposition": disposition["disposition"],
                "artifact_payload_sha256": report["artifact_payload_sha256"],
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "split_currency_audit_complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
