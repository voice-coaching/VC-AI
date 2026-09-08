"""Production runner and deep validator for WP4 S106 stack-only fusion.

After the predecessor deep-audit lock is validated, the fusion runner consumes
a content-addressed bundle of frozen component probabilities.  Fusion feature
construction never reuses raw audio, train, dev, predecessor dev predictions,
calibration, or locked-test data.  Component seeds are aggregated before the feature join,
speakers are assigned to five deterministic folds, fusion hyperparameters are
selected from OOF predictions, and one final full-stack model is fitted without
computing or publishing an in-sample metric.

All row-level outputs remain D1 artifacts.  A seven-file content-addressed
generation is published before a completed registry and immutable commit.  The
read-only validator reconstructs features, folds, OOF probabilities, metrics,
disagreement summaries, final coefficients, and the entire hash chain.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
from typing import Any
from types import MappingProxyType
from uuid import uuid4
import warnings

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import sklearn  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from voice_coach.training.wavlm_s106_component_projection import (
    COMPONENT_PREDICTION_ROOT,
    PREDICTION_RECORD_SCHEMA_VERSION,
    PROJECTION_REPRESENTATIONS,
)
from voice_coach.training.wavlm_s106_fusion_contract import (
    ARTIFACT_LAYOUT,
    COMPONENTS,
    EXACT_FEATURES,
    EXECUTION_LOCK_SCHEMA_VERSION,
    OOF_FOLD_COUNT,
    OOF_NAMESPACE,
    SEEDS,
    S106ContractError,
    aggregate_component_probabilities,
    alignment_confidence_features,
    assign_speaker_fold,
    canonical_json_bytes,
    load_config,
    load_json_strict,
    sha256_file,
    validate_config,
    validate_implementation_sources,
    validate_predecessor_canonical_sources,
)


COMPONENT_BUNDLE_SCHEMA_VERSION = "voice_coach.wavlm_s106_component_bundle_commit.v3"
COMPONENT_GENERATOR_COMMIT_SCHEMA_VERSION = (
    "voice_coach.wavlm_s106_component_input_commit.v2"
)
COMPONENT_GENERATOR_REGISTRY_SCHEMA_VERSION = (
    "voice_coach.wavlm_s106_component_input_registry.v1"
)
COMPONENT_FEATURE_FREEZE_SCHEMA_VERSION = (
    "voice_coach.wavlm_s106_component_feature_freeze.v1"
)
COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s106_stack_fusion_commit.v2"
MATRIX_SCHEMA_VERSION = "voice_coach.wavlm_s106_component_matrix.v1"
FOLD_SCHEMA_VERSION = "voice_coach.wavlm_s106_speaker_folds.v1"
OOF_PREDICTION_SCHEMA_VERSION = "voice_coach.wavlm_s106_oof_predictions.v1"
OOF_REPORT_SCHEMA_VERSION = "voice_coach.wavlm_s106_oof_report.v1"
DISAGREEMENT_SCHEMA_VERSION = "voice_coach.wavlm_s106_disagreement_report.v1"
CHECKPOINT_SCHEMA_VERSION = "voice_coach.wavlm_s106_fusion_checkpoint.v1"
REPORT_SCHEMA_VERSION = "voice_coach.wavlm_s106_stack_report.v1"
FEATURE_IDENTITY_COLUMNS = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "alignment_confidence",
    "qc_flagged",
    "reserved_prompt",
)
LABEL_COLUMNS = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "reference_binary_error_label",
)
COMPONENT_COLUMNS = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "probability",
)
MATRIX_ID_COLUMNS = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "fold",
)
OOF_COLUMNS = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "fold",
    "reference_binary_error_label",
    "probability",
    "prediction",
)
SHA256_HEX = frozenset("0123456789abcdef")


class S106TrainingError(RuntimeError):
    """Raised when the S106 execution or evidence chain is invalid."""


@dataclass(frozen=True, slots=True)
class StackRow:
    utterance_id_sha256: str
    speaker_id_sha256: str
    phone_index: int
    alignment_confidence: float | None

    @property
    def key(self) -> tuple[str, int]:
        return (self.utterance_id_sha256, self.phone_index)


@dataclass(frozen=True, slots=True)
class BundleEvidence:
    path: Path
    sha256: str
    normalized_records_sha256: str
    document: Mapping[str, Any]
    frozen_components: Mapping[str, Mapping[str, Any]]
    feature_identity_record: Mapping[str, Any]
    label_record: Mapping[str, Any]
    component_records: Mapping[str, Mapping[int, Mapping[str, Any]]]


@dataclass(frozen=True, slots=True)
class MatrixBundle:
    rows: tuple[StackRow, ...]
    feature_rows: tuple[Mapping[str, Any], ...]
    features: np.ndarray
    feature_freeze_sha256: str
    targets: np.ndarray
    speakers: tuple[str, ...]
    folds: np.ndarray
    component_probabilities: Mapping[str, np.ndarray]
    component_missing: Mapping[str, np.ndarray]


@dataclass(frozen=True, slots=True)
class FitParameters:
    mean: np.ndarray
    scale: np.ndarray
    coefficients: np.ndarray
    intercept: float


@dataclass(frozen=True, slots=True)
class SelectionResult:
    selected_c: float
    selected_threshold: float
    selected_metric: float
    oof_probabilities: np.ndarray
    oof_predictions: np.ndarray
    candidates: tuple[Mapping[str, Any], ...]
    fold_training_speakers: Mapping[int, int]
    fold_validation_speakers: Mapping[int, int]


@dataclass(frozen=True, slots=True)
class PublishedGeneration:
    generation_id: str
    path: Path
    records: tuple[Mapping[str, Any], ...]
    created: bool


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S106TrainingError(f"{name} must be a mapping")
    return value


def _deep_freeze(value: Any) -> Any:
    """Defensively copy JSON-compatible evidence into immutable containers."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S106TrainingError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S106TrainingError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S106TrainingError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S106TrainingError(f"{name} must be at least {minimum}")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S106TrainingError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise S106TrainingError(f"{name} must be finite")
    return result


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and set(value).issubset(SHA256_HEX)
    )


def _is_git_oid(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) in {40, 64}
        and set(value).issubset(SHA256_HEX)
    )


def _relative_path(value: object, name: str) -> str:
    text = _string(value, name)
    path = Path(text)
    if (
        path.is_absolute()
        or text != path.as_posix()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.startswith(".env") for part in path.parts)
    ):
        raise S106TrainingError(f"{name} must be a safe workspace-relative path")
    return text


def _reject_symlink_chain(root: Path, relative: str, name: str) -> Path:
    candidate = root / relative
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise S106TrainingError(f"{name} cannot traverse a symlink")
    return candidate


def _resolve_file(root: Path, value: object, name: str) -> Path:
    relative = _relative_path(value, name)
    unresolved = _reject_symlink_chain(root, relative, name)
    path = unresolved.resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as error:
        raise S106TrainingError(f"{name} escapes the workspace") from error
    if not path.is_file() or path.is_symlink():
        raise S106TrainingError(f"{name} must be a regular non-symlink file")
    return path


def _resolve_output(root: Path, value: object, name: str) -> Path:
    relative = _relative_path(value, name)
    unresolved = _reject_symlink_chain(root, relative, name)
    path = unresolved.resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as error:
        raise S106TrainingError(f"{name} escapes the workspace") from error
    if path.exists() and path.is_symlink():
        raise S106TrainingError(f"{name} cannot be a symlink")
    return path


def _workspace_relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as error:
        raise S106TrainingError("path is outside the workspace") from error


def _record(root: Path, path: Path) -> Mapping[str, Any]:
    return {
        "path": _workspace_relative(root, path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _require_sealed_public_file(path: Path, name: str) -> None:
    information = os.lstat(path)
    if (
        not stat.S_ISREG(information.st_mode)
        or stat.S_ISLNK(information.st_mode)
        or stat.S_IMODE(information.st_mode) != 0o440
        or information.st_nlink != 1
    ):
        raise S106TrainingError(f"{name} must be a sealed single-link regular file")


def _stable_immutable_json_path(
    *,
    root: Path,
    path: Path,
    name: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Read one sealed single-link JSON file through one no-follow descriptor."""

    try:
        relative = _workspace_relative(root, path)
        information = os.lstat(path)
    except OSError as error:
        raise S106TrainingError(f"{name} is absent") from error
    if (
        not stat.S_ISREG(information.st_mode)
        or stat.S_ISLNK(information.st_mode)
        or stat.S_IMODE(information.st_mode) != 0o440
        or information.st_nlink != 1
    ):
        raise S106TrainingError(f"{name} must be a sealed single-link regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    path_after = os.stat(path, follow_symlinks=False)
    if _stat_identity(before) != _stat_identity(after) or _stat_identity(
        before
    ) != _stat_identity(path_after):
        raise S106TrainingError(f"{name} changed during stable read")
    payload = b"".join(chunks)

    def unique_pairs(pairs: list[tuple[str, Any]]) -> Mapping[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise S106TrainingError(f"{name} has duplicate JSON key")
            output[key] = value
        return output

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise S106TrainingError(f"{name} is invalid JSON") from error
    document = _mapping(value, name)
    return document, {
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _stable_immutable_json_record(
    *,
    root: Path,
    value: object,
    name: str,
    expected_path: object,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    record = _mapping(value, f"{name} record")
    if set(record) != {"path", "sha256", "size_bytes"}:
        raise S106TrainingError(f"{name} record schema differs")
    path = _resolve_file(root, record.get("path"), f"{name}.path")
    _require_sealed_public_file(path, name)
    canonical = _resolve_file(root, expected_path, f"{name} canonical path")
    if path != canonical:
        raise S106TrainingError(f"{name} canonical path differs")
    document, actual = _stable_immutable_json_path(root=root, path=path, name=name)
    if dict(record) != actual:
        raise S106TrainingError(f"{name} file binding differs")
    return document, actual


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _stable_parquet_table(
    *,
    root: Path,
    record: Mapping[str, Any],
    name: str,
) -> pa.Table:
    """Parse the exact bytes hashed from one no-follow file descriptor."""

    path = _resolve_file(root, record.get("path"), f"{name}.path")
    _require_sealed_public_file(path, name)
    expected_sha = record.get("sha256")
    expected_size = record.get("size_bytes")
    if not _is_sha256(expected_sha) or not isinstance(expected_size, int):
        raise S106TrainingError(f"{name} stable-read binding differs")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise S106TrainingError(f"{name} stable-read open failed") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise S106TrainingError(f"{name} stable-read source is not regular")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        after_read = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after_read):
            raise S106TrainingError(f"{name} changed during stable read")
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != expected_sha or len(payload) != expected_size:
            raise S106TrainingError(f"{name} stable-read content binding differs")
        try:
            table = pq.read_table(pa.BufferReader(payload))
        except Exception as error:
            raise S106TrainingError(f"{name} Parquet payload is invalid") from error
        after_parse = os.fstat(descriptor)
        path_after = os.stat(path, follow_symlinks=False)
        if _stat_identity(before) != _stat_identity(after_parse) or _stat_identity(
            before
        ) != _stat_identity(path_after):
            raise S106TrainingError(f"{name} changed during stable read")
        return table
    finally:
        os.close(descriptor)


def _validate_record(
    *,
    root: Path,
    value: object,
    name: str,
    expected_path: Path | None = None,
) -> tuple[Mapping[str, Any], Path]:
    record = _mapping(value, name)
    if set(record) != {"path", "sha256", "size_bytes", "row_count"}:
        raise S106TrainingError(f"{name} record schema differs")
    path = _resolve_file(root, record.get("path"), f"{name}.path")
    _require_sealed_public_file(path, name)
    if expected_path is not None and path != expected_path:
        raise S106TrainingError(f"{name} path differs")
    if (
        not _is_sha256(record.get("sha256"))
        or record.get("sha256") != sha256_file(path)
        or record.get("size_bytes") != path.stat().st_size
        or _integer(record.get("row_count"), f"{name}.row_count", minimum=0) <= 0
    ):
        raise S106TrainingError(f"{name} file binding differs")
    return record, path


def _validate_deferred_content_record(
    *,
    root: Path,
    value: object,
    name: str,
) -> tuple[Mapping[str, Any], Path]:
    """Validate declared path/hash syntax/stat; defer content and computed hash."""

    record = _mapping(value, name)
    if set(record) != {"path", "sha256", "size_bytes", "row_count"}:
        raise S106TrainingError(f"{name} record schema differs")
    path = _resolve_file(root, record.get("path"), f"{name}.path")
    _require_sealed_public_file(path, name)
    if (
        not _is_sha256(record.get("sha256"))
        or record.get("size_bytes") != path.stat().st_size
        or _integer(record.get("row_count"), f"{name}.row_count", minimum=0) <= 0
    ):
        raise S106TrainingError(f"{name} deferred file binding differs")
    return record, path


def _frozen_source_lineage_sha256(
    frozen_component: Mapping[str, Any],
    component: str,
) -> str:
    if component == "explicit":
        value = _mapping(
            _mapping(
                frozen_component.get("sources"),
                "explicit frozen sources",
            ).get("registry"),
            "explicit frozen registry source",
        ).get("sha256")
    else:
        value = frozen_component.get("source_commit_sha256")
    if not _is_sha256(value):
        raise S106TrainingError(f"{component} source lineage SHA-256 is invalid")
    return str(value)


def _frozen_checkpoint_hashes(
    frozen_component: Mapping[str, Any],
    *,
    component: str,
    seed: int,
) -> tuple[str, ...]:
    records = frozen_component.get("projection_checkpoints")
    if not isinstance(records, (list, tuple)):
        raise S106TrainingError(f"{component} frozen projection checkpoints are absent")
    matches = [
        _mapping(value, f"{component} projection checkpoint")
        for value in records
        if isinstance(value, Mapping) and value.get("seed") == seed
    ]
    if len(matches) != 1:
        raise S106TrainingError(
            f"{component} seed {seed} frozen checkpoint binding differs"
        )
    record = matches[0]
    if component == "gop_acoustic_posterior":
        raw_hashes = tuple(
            _mapping(
                record.get(f"{kind}_checkpoint"),
                f"{component} {kind} checkpoint",
            ).get("sha256")
            for kind in ("acoustic", "error")
        )
    else:
        raw_hashes = (
            _mapping(
                record.get("checkpoint"),
                f"{component} checkpoint",
            ).get("sha256"),
        )
    if any(not _is_sha256(value) for value in raw_hashes) or len(
        set(raw_hashes)
    ) != len(raw_hashes):
        raise S106TrainingError(
            f"{component} seed {seed} checkpoint SHA-256 binding differs"
        )
    return tuple(str(value) for value in raw_hashes)


def _validate_frozen_checkpoint_files(
    root: Path,
    *,
    frozen_component: Mapping[str, Any],
    component: str,
) -> None:
    records = frozen_component.get("projection_checkpoints")
    if not isinstance(records, list) or len(records) != len(SEEDS):
        raise S106TrainingError(
            f"{component} projection checkpoint cardinality differs"
        )
    for seed in SEEDS:
        matches = [
            _mapping(value, f"{component} checkpoint record")
            for value in records
            if isinstance(value, Mapping) and value.get("seed") == seed
        ]
        if len(matches) != 1:
            raise S106TrainingError(
                f"{component} seed {seed} checkpoint record differs"
            )
        record = matches[0]
        checkpoint_values: tuple[object, ...]
        if component == "gop_acoustic_posterior":
            if set(record) != {
                "seed",
                "acoustic_checkpoint",
                "error_checkpoint",
                "projection_validation",
            }:
                raise S106TrainingError("B07 checkpoint record schema differs")
            projection = _mapping(
                record.get("projection_validation"),
                "B07 projection validation",
            )
            if projection != {
                "status": "projection_ready",
                "label_argument_present": False,
                "dev_prediction_reused": False,
                "realized_phone_identity_used": False,
                "acoustic_schema_version": (
                    "voice_coach.b07_gop_acoustic_checkpoint.v1"
                ),
                "error_schema_version": "voice_coach.b07_gop_error_checkpoint.v1",
                "seed": seed,
            }:
                raise S106TrainingError("B07 projection validation differs")
            checkpoint_values = (
                record.get("acoustic_checkpoint"),
                record.get("error_checkpoint"),
            )
        else:
            if set(record) != {"seed", "checkpoint"}:
                raise S106TrainingError(f"{component} checkpoint record schema differs")
            checkpoint_values = (record.get("checkpoint"),)
        for raw_checkpoint in checkpoint_values:
            checkpoint = _mapping(
                raw_checkpoint,
                f"{component} seed {seed} checkpoint",
            )
            if set(checkpoint) != {"path", "sha256", "size_bytes"}:
                raise S106TrainingError(
                    f"{component} seed {seed} checkpoint file schema differs"
                )
            path = _resolve_file(
                root,
                checkpoint.get("path"),
                f"{component} seed {seed} checkpoint path",
            )
            if (
                checkpoint.get("sha256") != sha256_file(path)
                or checkpoint.get("size_bytes") != path.stat().st_size
            ):
                raise S106TrainingError(
                    f"{component} seed {seed} checkpoint file binding differs"
                )


def _validate_prediction_record(
    *,
    root: Path,
    value: object,
    component: str,
    seed: int,
    frozen_component: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Path]:
    name = f"bundle {component} seed {seed}"
    record = _mapping(value, name)
    expected_keys = {
        "path",
        "sha256",
        "size_bytes",
        "row_count",
        "schema_version",
        "component",
        "seed",
        "split",
        "source_lineage_sha256",
        "source_checkpoint_sha256s",
        "projection_attestation",
    }
    if set(record) != expected_keys:
        raise S106TrainingError(f"{name} prediction record schema differs")
    expected_relative = (
        f"{COMPONENT_PREDICTION_ROOT}/{component}/seed_{seed}_stack_predictions.parquet"
    )
    path = _resolve_file(root, record.get("path"), f"{name}.path")
    _require_sealed_public_file(path, name)
    raw_checkpoints = record.get("source_checkpoint_sha256s")
    if not isinstance(raw_checkpoints, (list, tuple)):
        raise S106TrainingError(f"{name} checkpoint hash list differs")
    checkpoints = tuple(raw_checkpoints)
    attestation = _mapping(
        record.get("projection_attestation"),
        f"{name} projection attestation",
    )
    expected_attestation = {
        "label_free": True,
        "stack_only": True,
        "binary_error_label_used": False,
        "realized_phone_identity_used": False,
        "dev_prediction_reused": False,
        "representation": PROJECTION_REPRESENTATIONS[component],
    }
    if (
        record.get("path") != expected_relative
        or "dev" in {part.casefold() for part in Path(expected_relative).parts}
        or record.get("schema_version") != PREDICTION_RECORD_SCHEMA_VERSION
        or record.get("component") != component
        or record.get("seed") != seed
        or record.get("split") != "stack"
        or record.get("source_lineage_sha256")
        != _frozen_source_lineage_sha256(frozen_component, component)
        or checkpoints
        != _frozen_checkpoint_hashes(
            frozen_component,
            component=component,
            seed=seed,
        )
        or dict(attestation) != expected_attestation
        or not _is_sha256(record.get("sha256"))
        or record.get("sha256") != sha256_file(path)
        or record.get("size_bytes") != path.stat().st_size
        or _integer(record.get("row_count"), f"{name}.row_count", minimum=0) <= 0
    ):
        raise S106TrainingError(f"{name} stack provenance or file binding differs")
    return record, path


def _binding(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("binding_sha256", None)
    return hashlib.sha256(canonical_json_bytes(unsigned, pretty=False)).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: object, name: str) -> datetime:
    text = _string(value, name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise S106TrainingError(f"{name} is not ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise S106TrainingError(f"{name} must be UTC")
    return parsed


def _write_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    replaced = False
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)


def _publish_new_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    """Create one JSON path atomically without ever replacing a concurrent run."""

    encoded = canonical_json_bytes(value, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise S106TrainingError(
                f"concurrent S106 publication already exists: {path}"
            ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _publish_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value, pretty=True)
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != encoded:
            raise S106TrainingError(f"immutable JSON already differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not path.is_file() or path.is_symlink() or path.read_bytes() != encoded:
                raise S106TrainingError(
                    f"immutable JSON already differs: {path}"
                ) from None
    finally:
        temporary.unlink(missing_ok=True)


def _git_metadata(root: Path) -> Mapping[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "commit": None, "dirty": None}
    return {
        "available": _is_git_oid(commit),
        "commit": commit if _is_git_oid(commit) else None,
        "dirty": dirty,
    }


def validate_execution_lock(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    lock_path: Path,
) -> Mapping[str, Any]:
    """Validate the no-metric preflight lock without resolving a new split."""

    lock_sha = sha256_file(lock_path)
    lock = load_json_strict(lock_path, "S106 execution lock")
    if set(lock) != {
        "schema_version",
        "status",
        "experiment_id",
        "stage",
        "config",
        "implementation",
        "predecessors",
        "frozen_components",
        "stack_source",
        "component_bundle_contract",
        "contract_hashes",
        "access_ledger",
        "next_action",
    } or (
        lock.get("schema_version") != EXECUTION_LOCK_SCHEMA_VERSION
        or lock.get("status") != "ready"
        or lock.get("experiment_id")
        != _mapping(config["experiment"], "experiment").get("id")
        or lock.get("stage") != "s106_stack_only_confidence_aware_fusion"
    ):
        raise S106TrainingError("S106 execution lock status differs")
    config_record = _mapping(lock.get("config"), "execution lock config")
    if config_record.get("path") != _workspace_relative(
        root, config_path
    ) or config_record.get("sha256") != sha256_file(config_path):
        raise S106TrainingError("execution lock config binding differs")
    implementation = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )
    if lock.get("implementation") != implementation:
        raise S106TrainingError("execution lock implementation binding differs")
    predecessors = _mapping(lock.get("predecessors"), "lock predecessors")
    if set(predecessors) != {"s104", "s105", "b07"}:
        raise S106TrainingError("execution lock predecessor set differs")
    try:
        validate_predecessor_canonical_sources(
            workspace_root=root,
            execution_lock=lock,
        )
    except S106ContractError as error:
        raise S106TrainingError(
            "execution lock predecessor canonical-source binding differs"
        ) from error
    for stage in ("s104", "s105", "b07"):
        record = _mapping(predecessors.get(stage), f"lock predecessor {stage}")
        commit_path = _resolve_file(
            root,
            record.get("commit_path"),
            f"lock predecessor {stage} commit",
        )
        if (
            record.get("commit_sha256") != sha256_file(commit_path)
            or not _is_sha256(record.get("generation_id"))
            or not _is_sha256(record.get("report_sha256"))
        ):
            raise S106TrainingError(f"lock predecessor {stage} binding differs")
    if (
        _mapping(predecessors["s104"], "S104 predecessor").get("selected_variant")
        != "base_plus"
        or _mapping(predecessors["s105"], "S105 predecessor").get("selected_variant")
        not in {"simple_head_control", "levelwise_multi_aspect_hierarchy"}
        or _mapping(predecessors["s105"], "S105 predecessor").get("adoption_status")
        not in {"passed", "failed"}
    ):
        raise S106TrainingError("execution lock selected component differs")
    s104 = _mapping(predecessors["s104"], "S104 predecessor")
    s105 = _mapping(predecessors["s105"], "S105 predecessor")
    b07 = _mapping(predecessors["b07"], "B07 predecessor")
    expected_deep_audits = {
        "s104": {
            "validator_status": "valid",
            "development_only": True,
            "locked_test_accessed": False,
            "fit_metadata_read": True,
            "dev_truth_read": True,
            "per_seed_dev_predictions_read": True,
            "access_basis": "pinned_public_validator_contract_inspection",
        },
        "s105": {
            "validator_status": "valid",
            "development_only": True,
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "manifest_reservation_labels_read": True,
            "dev_predictions_read": True,
            "nested_s104_full_validator": True,
            "access_basis": "pinned_public_validator_contract_inspection",
        },
        "b07": {
            "validator_status": "valid",
            "full_train_selection_dev_recomputed": True,
            "serialized_acoustic_checkpoint_replayed": True,
            "serialized_error_checkpoint_replayed": True,
            "canonical_train_read": True,
            "canonical_dev_read": True,
            "stored_dev_predictions_read": True,
            "fusion_component_reuse": False,
            "access_basis": "validator_return_and_pinned_public_contract",
        },
    }
    if any(
        _mapping(predecessors[stage], f"{stage} predecessor").get("deep_audit")
        != expected
        for stage, expected in expected_deep_audits.items()
    ):
        raise S106TrainingError("execution lock predecessor deep-audit differs")
    expected_s105 = (
        "levelwise_multi_aspect_hierarchy"
        if s105.get("adoption_status") == "passed"
        else "simple_head_control"
    )
    if s105.get("selected_variant") != expected_s105:
        raise S106TrainingError("execution lock S105 adoption binding differs")
    b07_registry_path = _resolve_file(
        root,
        b07.get("registry_path"),
        "lock predecessor B07 registry",
    )
    if (
        b07.get("selected_variant") != "gop_acoustic_posterior"
        or b07.get("adoption_status") != "passed"
        or b07.get("registry_sha256") != sha256_file(b07_registry_path)
        or not isinstance(b07.get("projection_checkpoints"), list)
        or len(b07["projection_checkpoints"]) != len(SEEDS)
    ):
        raise S106TrainingError("execution lock B07 binding differs")
    frozen = _mapping(lock.get("frozen_components"), "locked frozen components")
    frozen_explicit = _mapping(
        frozen.get("explicit"),
        "locked explicit component",
    )
    if (
        frozen.get("order") != list(COMPONENTS)
        or frozen_explicit.get("name") != "explicit"
        or frozen_explicit.get("status") != "frozen"
        or frozen_explicit.get("seeds") != list(SEEDS)
        or _mapping(frozen.get("s104_adapter"), "locked S104 component").get(
            "source_commit_sha256"
        )
        != s104.get("commit_sha256")
        or _mapping(frozen.get("s104_adapter"), "locked S104 component").get(
            "selected_variant"
        )
        != "base_plus"
        or _mapping(frozen.get("s104_adapter"), "locked S104 component").get(
            "projection_checkpoints"
        )
        != s104.get("projection_checkpoints")
        or _mapping(frozen.get("s105_selected_hierarchy"), "locked S105 component").get(
            "source_commit_sha256"
        )
        != s105.get("commit_sha256")
        or _mapping(frozen.get("s105_selected_hierarchy"), "locked S105 component").get(
            "selected_variant"
        )
        != expected_s105
        or _mapping(frozen.get("s105_selected_hierarchy"), "locked S105 component").get(
            "adoption_status"
        )
        != s105.get("adoption_status")
        or _mapping(frozen.get("s105_selected_hierarchy"), "locked S105 component").get(
            "projection_checkpoints"
        )
        != s105.get("projection_checkpoints")
        or _mapping(frozen.get("gop_acoustic_posterior"), "locked B07 component").get(
            "source_commit_sha256"
        )
        != b07.get("commit_sha256")
        or _mapping(frozen.get("gop_acoustic_posterior"), "locked B07 component").get(
            "source_registry_sha256"
        )
        != b07.get("registry_sha256")
        or _mapping(frozen.get("gop_acoustic_posterior"), "locked B07 component").get(
            "generation_id"
        )
        != b07.get("generation_id")
        or _mapping(frozen.get("gop_acoustic_posterior"), "locked B07 component").get(
            "selected_variant"
        )
        != "gop_acoustic_posterior"
        or _mapping(frozen.get("gop_acoustic_posterior"), "locked B07 component").get(
            "adoption_status"
        )
        != "passed"
        or _mapping(frozen.get("gop_acoustic_posterior"), "locked B07 component").get(
            "seeds"
        )
        != list(SEEDS)
        or _mapping(frozen.get("gop_acoustic_posterior"), "locked B07 component").get(
            "projection_checkpoints"
        )
        != b07.get("projection_checkpoints")
        or _mapping(frozen.get("gop_acoustic_posterior"), "locked B07 component").get(
            "label_free_projection"
        )
        is not True
        or _mapping(frozen.get("gop_acoustic_posterior"), "locked B07 component").get(
            "dev_prediction_reused"
        )
        is not False
    ):
        raise S106TrainingError("execution lock frozen-component binding differs")
    for component in COMPONENTS:
        _validate_frozen_checkpoint_files(
            root,
            frozen_component=_mapping(
                frozen.get(component),
                f"locked {component} component",
            ),
            component=component,
        )
    stack = _mapping(lock.get("stack_source"), "execution lock stack source")
    stack_path = _resolve_file(root, stack.get("path"), "execution lock stack source")
    stack_config = _mapping(_mapping(config["data"], "data")["stack"], "data.stack")
    if (
        stack.get("path") != stack_config.get("manifest")
        or stack.get("sha256") != stack_config.get("manifest_sha256")
        or stack.get("sha256") != sha256_file(stack_path)
        or stack.get("size_bytes") != stack_path.stat().st_size
        or stack.get("split_value") != "stack"
        or stack.get("row_data_read_by_preflight") is not False
    ):
        raise S106TrainingError("execution lock stack-source binding differs")
    contract_hashes = _mapping(
        lock.get("contract_hashes"),
        "execution lock contract hashes",
    )
    expected_contract_hashes = {
        f"{name}_sha256": hashlib.sha256(
            canonical_json_bytes(
                _mapping(config[name], name),
                pretty=False,
            )
        ).hexdigest()
        for name in ("seed_aggregation", "feature_contract", "oof", "reporting")
    }
    if contract_hashes != expected_contract_hashes:
        raise S106TrainingError("execution lock contract hashes differ")
    bundle_contract = _mapping(
        lock.get("component_bundle_contract"),
        "execution lock component-bundle contract",
    )
    inputs = _mapping(config["inputs"], "inputs")
    if (
        bundle_contract.get("path") != inputs.get("component_bundle_commit")
        or bundle_contract.get("schema_version")
        != inputs.get("component_bundle_schema_version")
        or bundle_contract.get("resolved_or_read_by_preflight") is not False
    ):
        raise S106TrainingError("execution lock component-bundle contract differs")
    access = _mapping(lock.get("access_ledger"), "execution lock access ledger")
    required_false = (
        "gpu_initialized",
        "fusion_fit_performed",
        "metrics_computed",
        "metrics_reported",
    )
    predecessor_audit = _mapping(
        access.get("predecessor_deep_audit"),
        "execution lock predecessor deep audit",
    )
    fusion_access = _mapping(
        access.get("fusion_input_access"),
        "execution lock fusion input access",
    )
    if (
        access.get("preflight_cpu_only") is not True
        or any(access.get(name) is not False for name in required_false)
        or access.get("environment_files_read") != []
        or access.get("component_prediction_files_written") != 0
        or predecessor_audit
        != {
            "public_validator_call_order": [
                "s104_direct",
                "s105_with_nested_s104",
                "b07",
            ],
            "validator_invocation_counts": {"s104": 2, "s105": 1, "b07": 1},
            "train_or_fit_inputs_read": True,
            "dev_inputs_read": True,
            "dev_prediction_artifacts_read": True,
            **expected_deep_audits,
        }
        or fusion_access
        != {
            "stack_rows_read_by_preflight": False,
            "stack_labels_read_by_preflight": False,
            "fusion_feature_train_read": False,
            "fusion_feature_dev_read": False,
            "predecessor_dev_predictions_reused_as_components": False,
            "b07_dev_predictions_reused_as_component": False,
            "calibration_read": False,
            "locked_test_read": False,
        }
    ):
        raise S106TrainingError("execution lock access ledger differs")
    if sha256_file(lock_path) != lock_sha:
        raise S106TrainingError("execution lock changed during validation")
    return lock


def _expected_component_generator_identity(
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    implementation = _mapping(config.get("implementation"), "implementation")
    roles = (
        "component_projection_module",
        "component_generator_config",
        "component_generator_script",
        "component_generator_module",
    )
    return {
        "mode": "production",
        "production_schema_allowed": True,
        "dependency_injection_allowed": False,
        "backend_factory": (
            "voice_coach.training.wavlm_s106_component_generator."
            "production_backend_factory"
        ),
        "preflight": (
            "voice_coach.training.wavlm_s106_fusion_contract.prepare_s106_execution"
        ),
        "bundle_builder": (
            "voice_coach.training.wavlm_s106_component_projection."
            "build_component_bundle_document"
        ),
        "pinned_sources": {
            role: dict(_mapping(implementation.get(role), f"implementation.{role}"))
            for role in roles
        },
    }


def _validate_component_generator_lineage(
    *,
    root: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    bundle_path: Path,
    bundle: Mapping[str, Any],
    bundle_file_record: Mapping[str, Any],
) -> None:
    """Bind a production bundle to its sealed generator evidence DAG."""

    inputs = _mapping(config.get("inputs"), "inputs")
    expected_identity = _expected_component_generator_identity(config)
    if bundle.get("producer_identity") != expected_identity:
        raise S106TrainingError("bundle producer identity differs")
    commit, commit_record = _stable_immutable_json_record(
        root=root,
        value=bundle.get("generator_commit"),
        name="component generator commit",
        expected_path=inputs.get("component_generator_commit"),
    )
    receipt, receipt_record = _stable_immutable_json_record(
        root=root,
        value=bundle.get("feature_freeze_receipt"),
        name="component feature-freeze receipt",
        expected_path=inputs.get("component_feature_freeze_receipt"),
    )
    try:
        from voice_coach.training.wavlm_s106_component_generator import (
            S106ComponentGeneratorError,
            validate_component_producer_evidence,
            validate_feature_freeze_receipt,
        )
    except ImportError as error:
        raise S106TrainingError(
            "component feature-freeze receipt contract differs"
        ) from error
    try:
        validate_feature_freeze_receipt(receipt)
    except S106ComponentGeneratorError as error:
        raise S106TrainingError(
            "component feature-freeze receipt contract differs"
        ) from error
    if (
        receipt.get("schema_version")
        != inputs.get("component_feature_freeze_receipt_schema_version")
        or receipt.get("generation_id") != receipt.get("feature_digest_sha256")
        or receipt.get("execution_lock") != bundle.get("execution_lock")
        or receipt.get("stack_source") != bundle.get("stack_source")
        or receipt.get("feature_identity_matrix")
        != bundle.get("feature_identity_matrix")
        or receipt.get("components") != bundle.get("components")
    ):
        raise S106TrainingError("bundle feature-freeze receipt lineage differs")
    expected_commit_keys = {
        "schema_version",
        "status",
        "generation_id",
        "producer_identity",
        "generator_config",
        "execution_lock",
        "feature_freeze_receipt",
        "label_matrix",
        "prediction_file_count",
        "feature_rows",
        "label_rows",
        "excluded_utterances",
        "access_audit",
        "binding_sha256",
    }
    access_audit = {
        "predecessor_validation_completed_before_runtime_access": True,
        "label_free_scan_before_checkpoint_probe": True,
        "eligible_raw_ids_retained_in_memory_only": True,
        "restricted_parquet_disk_snapshot_created": False,
        "label_scanner_exact_eligible_id_held_fd_predicate_pushed_down": True,
        "label_staging_anonymous_unlinked": True,
        "source_parquet_scanner_batches_bounded": True,
        "label_rows_streamed_in_bounded_batches": True,
        "production_publication_serialized": True,
        "phone_alignments_opened_after_feature_freeze": True,
        "operation_opened_after_feature_freeze": True,
        "audio_pcm_hash_and_decode_used_same_held_bytes": True,
        "models_loaded_from_private_immutable_snapshot": True,
        "production_dependencies_immutable": True,
        "b07_unadapted_forward_separate": True,
        "s104_s105_adapted_forward_shared_per_seed": True,
        "completed_registry_published_before_visibility_bundle": True,
        "bundle_is_final_visibility_commit": True,
        "development_only": True,
        "stack_only": True,
    }
    excluded = commit.get("excluded_utterances")
    generator_source = _mapping(
        _mapping(config.get("implementation"), "implementation").get(
            "component_generator_config"
        ),
        "implementation.component_generator_config",
    )
    generator_config_path = _resolve_file(
        root,
        generator_source.get("path"),
        "component generator config",
    )
    expected_generator_config_record = _record(root, generator_config_path)
    expected_lock_record = {
        "path": _workspace_relative(root, execution_lock_path),
        "sha256": sha256_file(execution_lock_path),
    }
    if (
        set(commit) != expected_commit_keys
        or commit.get("schema_version")
        != inputs.get("component_generator_commit_schema_version")
        or commit.get("status") != inputs.get("required_status")
        or commit.get("binding_sha256") != _binding(commit)
        or commit.get("producer_identity") != expected_identity
        or commit.get("generator_config") != expected_generator_config_record
        or commit.get("execution_lock") != expected_lock_record
        or commit.get("feature_freeze_receipt") != receipt_record
        or commit.get("label_matrix") != bundle.get("label_matrix")
        or commit.get("generation_id") != receipt.get("generation_id")
        or commit.get("prediction_file_count") != len(COMPONENTS) * len(SEEDS)
        or commit.get("feature_rows") != receipt.get("row_count")
        or commit.get("label_rows") != receipt.get("row_count")
        or commit.get("access_audit") != access_audit
        or not isinstance(excluded, Mapping)
        or set(excluded) != {"qc_flagged", "reserved_prompt"}
        or any(
            isinstance(excluded.get(name), bool)
            or not isinstance(excluded.get(name), int)
            or int(excluded[name]) < 0
            for name in ("qc_flagged", "reserved_prompt")
        )
    ):
        raise S106TrainingError("component generator commit lineage differs")
    registry_path = _resolve_file(
        root,
        inputs.get("component_generator_registry"),
        "component generator registry",
    )
    registry, registry_record = _stable_immutable_json_path(
        root=root,
        path=registry_path,
        name="component generator registry",
    )
    expected_registry_keys = {
        "schema_version",
        "status",
        "run_id",
        "failure",
        "generation_id",
        "producer_identity",
        "commit",
        "component_bundle",
        "feature_freeze_receipt",
        "artifact_counts",
        "privacy",
        "binding_sha256",
    }
    if (
        set(registry) != expected_registry_keys
        or registry.get("schema_version")
        != inputs.get("component_generator_registry_schema_version")
        or registry.get("status") != "completed"
        or registry.get("run_id") != "aihub71490-wavlm-s106-component-inputs-v1"
        or registry.get("failure") is not None
        or registry.get("generation_id") != commit.get("generation_id")
        or registry.get("producer_identity") != expected_identity
        or registry.get("commit") != commit_record
        or registry.get("component_bundle") != bundle_file_record
        or registry.get("feature_freeze_receipt") != receipt_record
        or registry.get("artifact_counts")
        != {
            "feature_identity": 1,
            "component_predictions": 12,
            "label_matrix": 1,
            "feature_freeze_receipt": 1,
            "component_bundle": 1,
            "generator_commit": 1,
        }
        or registry.get("privacy")
        != {
            "raw_identifiers_published": False,
            "raw_paths_published": False,
            "target_or_realized_phone_identity_published": False,
            "row_level_scope": "D1_restricted_internal",
        }
        or registry.get("binding_sha256") != _binding(registry)
        or registry_record.get("path")
        != _relative_path(
            inputs.get("component_generator_registry"),
            "component generator registry",
        )
        or bundle_file_record.get("path") != _workspace_relative(root, bundle_path)
    ):
        raise S106TrainingError("component generator registry lineage differs")
    try:
        producer_evidence = validate_component_producer_evidence(
            workspace_root=root,
            generator_config_path=generator_config_path,
            generation_id=str(commit.get("generation_id")),
            receipt_path=Path(root / str(receipt_record["path"])),
            receipt=receipt,
            label_record=_mapping(
                commit.get("label_matrix"),
                "component generator label matrix",
            ),
        )
    except S106ComponentGeneratorError as error:
        raise S106TrainingError("component producer evidence tree differs") from error
    if producer_evidence.get("producer_identity") != expected_identity:
        raise S106TrainingError("component producer evidence identity differs")


def validate_component_bundle_commit(
    *,
    root: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    execution_lock: Mapping[str, Any],
    bundle_path: Path,
) -> BundleEvidence:
    """Validate every source file hash without yet interpreting row content."""

    try:
        validate_predecessor_canonical_sources(
            workspace_root=root,
            execution_lock=execution_lock,
        )
    except S106ContractError as error:
        raise S106TrainingError(
            "component bundle predecessor canonical-source binding differs"
        ) from error
    bundle, bundle_file_record = _stable_immutable_json_path(
        root=root,
        path=bundle_path,
        name="S106 component bundle commit",
    )
    bundle_sha = str(bundle_file_record["sha256"])
    if set(bundle) != {
        "schema_version",
        "status",
        "generator_commit",
        "feature_freeze_receipt",
        "producer_identity",
        "execution_lock",
        "stack_source",
        "frozen_components",
        "feature_identity_matrix",
        "label_matrix",
        "components",
        "projection_audit",
        "binding_sha256",
    }:
        raise S106TrainingError("component bundle commit schema differs")
    if (
        bundle.get("schema_version") != COMPONENT_BUNDLE_SCHEMA_VERSION
        or bundle.get("status") != "committed"
        or bundle.get("binding_sha256") != _binding(bundle)
    ):
        raise S106TrainingError("component bundle commit binding differs")
    _validate_component_generator_lineage(
        root=root,
        config=config,
        execution_lock_path=execution_lock_path,
        bundle_path=bundle_path,
        bundle=bundle,
        bundle_file_record=bundle_file_record,
    )
    lock_record = _mapping(bundle.get("execution_lock"), "bundle execution lock")
    if set(lock_record) != {"path", "sha256"} or (
        lock_record.get("path") != _workspace_relative(root, execution_lock_path)
        or lock_record.get("sha256") != sha256_file(execution_lock_path)
    ):
        raise S106TrainingError("bundle execution-lock binding differs")
    stack_record = _mapping(bundle.get("stack_source"), "bundle stack source")
    locked_stack = _mapping(execution_lock.get("stack_source"), "locked stack source")
    if set(stack_record) != {"path", "sha256"} or (
        stack_record.get("path") != locked_stack.get("path")
        or stack_record.get("sha256") != locked_stack.get("sha256")
    ):
        raise S106TrainingError("bundle stack-source binding differs")
    bundle_frozen = _mapping(
        bundle.get("frozen_components"),
        "bundle frozen components",
    )
    locked_frozen = _mapping(
        execution_lock.get("frozen_components"),
        "locked frozen components",
    )
    if bundle_frozen != locked_frozen:
        raise S106TrainingError("bundle frozen-component lineage differs")
    feature_identity_record, _ = _validate_record(
        root=root,
        value=bundle.get("feature_identity_matrix"),
        name="bundle feature identity matrix",
    )
    label_record, _ = _validate_deferred_content_record(
        root=root,
        value=bundle.get("label_matrix"),
        name="bundle label matrix",
    )
    if label_record["row_count"] != feature_identity_record["row_count"]:
        raise S106TrainingError("bundle identity/label row counts differ")
    projection_audit = _mapping(
        bundle.get("projection_audit"),
        "bundle projection audit",
    )
    if projection_audit != {
        "component_order": list(COMPONENTS),
        "seed_order": list(SEEDS),
        "prediction_file_count": len(COMPONENTS) * len(SEEDS),
        "seed_aggregation_occurs_before_join": True,
        "feature_identity_built_before_label_open": True,
        "label_content_and_computed_hash_deferred_until_feature_freeze": True,
        "feature_builder_received_labels": False,
        "alignment_confidence_source": "absent_from_modeling_manifest",
        "alignment_confidence_non_null_allowed": False,
        "alignment_confidence_missing_indicator_forced": True,
        "b07_dev_predictions_reused": False,
        "realized_phone_identity_used": False,
        "binary_error_label_used_by_projectors": False,
    }:
        raise S106TrainingError("bundle projection audit differs")
    raw_components = _mapping(bundle.get("components"), "bundle components")
    if set(raw_components) != set(COMPONENTS):
        raise S106TrainingError("bundle component set differs")
    component_records: dict[str, Mapping[int, Mapping[str, Any]]] = {}
    for component in COMPONENTS:
        frozen_component = _mapping(
            locked_frozen.get(component),
            f"locked frozen component {component}",
        )
        raw_seeds = _mapping(
            raw_components.get(component),
            f"bundle component {component}",
        )
        if tuple(raw_seeds) != tuple(f"seed_{seed}" for seed in SEEDS):
            raise S106TrainingError(f"{component} seed record set/order differs")
        seed_records: dict[int, Mapping[str, Any]] = {}
        for seed in SEEDS:
            record, _ = _validate_prediction_record(
                root=root,
                value=raw_seeds.get(f"seed_{seed}"),
                component=component,
                seed=seed,
                frozen_component=frozen_component,
            )
            seed_records[seed] = record
            if record["row_count"] != feature_identity_record["row_count"]:
                raise S106TrainingError(
                    f"bundle {component} prediction row count differs from "
                    "stack universe"
                )
        component_records[component] = seed_records
    if sha256_file(bundle_path) != bundle_sha:
        raise S106TrainingError("component bundle changed during validation")
    normalized_records = {
        "generator_commit": bundle["generator_commit"],
        "feature_freeze_receipt": bundle["feature_freeze_receipt"],
        "producer_identity": bundle["producer_identity"],
        "frozen_components": bundle_frozen,
        "feature_identity_matrix": feature_identity_record,
        "label_matrix": label_record,
        "components": {
            component: {
                f"seed_{seed}": component_records[component][seed] for seed in SEEDS
            }
            for component in COMPONENTS
        },
    }
    normalized_records_sha256 = hashlib.sha256(
        canonical_json_bytes(normalized_records, pretty=False)
    ).hexdigest()
    return BundleEvidence(
        path=bundle_path,
        sha256=bundle_sha,
        normalized_records_sha256=normalized_records_sha256,
        document=_deep_freeze(bundle),
        frozen_components=_deep_freeze(bundle_frozen),
        feature_identity_record=_deep_freeze(feature_identity_record),
        label_record=_deep_freeze(label_record),
        component_records=_deep_freeze(component_records),
    )


def _table_columns(table: pa.Table, expected: Sequence[str], name: str) -> None:
    if tuple(table.column_names) != tuple(expected):
        raise S106TrainingError(f"{name} exact column contract differs")
    if table.num_rows <= 0:
        raise S106TrainingError(f"{name} is empty")


def _output_table_contract(
    table: pa.Table,
    expected_columns: Sequence[str],
    expected_schema_version: str,
    name: str,
) -> None:
    _table_columns(table, expected_columns, name)
    metadata = table.schema.metadata
    if metadata != {
        b"voice_coach_schema_version": expected_schema_version.encode("utf-8")
    }:
        raise S106TrainingError(f"{name} schema metadata differs")


def _load_feature_identity_matrix(
    root: Path,
    record: Mapping[str, Any],
) -> tuple[StackRow, ...]:
    table = _stable_parquet_table(
        root=root,
        record=record,
        name="feature identity matrix",
    )
    _table_columns(table, FEATURE_IDENTITY_COLUMNS, "feature identity matrix")
    if table.num_rows != record["row_count"]:
        raise S106TrainingError("feature identity matrix row count differs")
    values = table.to_pylist()
    rows: list[StackRow] = []
    seen: set[tuple[str, int]] = set()
    utterance_speakers: dict[str, str] = {}
    for index, value in enumerate(values):
        utterance = _string(
            value.get("utterance_id_sha256"),
            f"feature identity row {index} utterance",
        )
        speaker = _string(
            value.get("speaker_id_sha256"),
            f"feature identity row {index} speaker",
        )
        if not _is_sha256(utterance) or not _is_sha256(speaker):
            raise S106TrainingError(
                "feature identity identifiers must be lowercase SHA-256"
            )
        phone_index = _integer(
            value.get("phone_index"),
            f"feature identity row {index} phone_index",
            minimum=0,
        )
        if value.get("split") != "stack":
            raise S106TrainingError("feature identity matrix contains a non-stack row")
        confidence_value = value.get("alignment_confidence")
        if confidence_value is not None:
            raise S106TrainingError(
                "canonical v2 identity requires null alignment confidence "
                "until a realized-independent source is reviewed"
            )
        confidence = None
        alignment_confidence_features(confidence)
        if value.get("qc_flagged") is not False:
            raise S106TrainingError("QC-flagged row reached S106")
        if value.get("reserved_prompt") is not False:
            raise S106TrainingError("reserved prompt row reached S106")
        key = (utterance, phone_index)
        if key in seen:
            raise S106TrainingError("feature identity row key is duplicated")
        seen.add(key)
        previous = utterance_speakers.setdefault(utterance, speaker)
        if previous != speaker:
            raise S106TrainingError("utterance has multiple speaker hashes")
        rows.append(
            StackRow(
                utterance_id_sha256=utterance,
                speaker_id_sha256=speaker,
                phone_index=phone_index,
                alignment_confidence=confidence,
            )
        )
    rows.sort(key=lambda value: value.key)
    return tuple(rows)


def _load_label_targets(
    root: Path,
    record: Mapping[str, Any],
    *,
    frozen_rows: Sequence[StackRow],
) -> np.ndarray:
    """Open labels only after the label-free feature matrix has been frozen."""

    table = _stable_parquet_table(
        root=root,
        record=record,
        name="label matrix",
    )
    _table_columns(table, LABEL_COLUMNS, "label matrix")
    if table.num_rows != record["row_count"]:
        raise S106TrainingError("label matrix row count differs")
    frozen_by_key = {row.key: row for row in frozen_rows}
    labels_by_key: dict[tuple[str, int], int] = {}
    for index, value in enumerate(table.to_pylist()):
        utterance = _string(
            value.get("utterance_id_sha256"),
            f"label row {index} utterance",
        )
        speaker = _string(
            value.get("speaker_id_sha256"),
            f"label row {index} speaker",
        )
        if not _is_sha256(utterance) or not _is_sha256(speaker):
            raise S106TrainingError("label identifiers must be lowercase SHA-256")
        phone_index = _integer(
            value.get("phone_index"),
            f"label row {index} phone_index",
            minimum=0,
        )
        if value.get("split") != "stack":
            raise S106TrainingError("label matrix contains a non-stack row")
        key = (utterance, phone_index)
        frozen = frozen_by_key.get(key)
        if frozen is None:
            raise S106TrainingError("label matrix contains a non-feature key")
        if speaker != frozen.speaker_id_sha256:
            raise S106TrainingError("label matrix speaker binding differs")
        label = _integer(value.get("reference_binary_error_label"), "binary label")
        if label not in {0, 1}:
            raise S106TrainingError("binary label is outside {0,1}")
        if key in labels_by_key:
            raise S106TrainingError("label row key is duplicated")
        labels_by_key[key] = label
    if set(labels_by_key) != set(frozen_by_key):
        raise S106TrainingError("label matrix does not match frozen feature universe")
    targets: np.ndarray = np.asarray(
        [labels_by_key[row.key] for row in frozen_rows],
        dtype=np.int64,
    )
    if len(set(targets.tolist())) != 2:
        raise S106TrainingError("stack labels must contain both binary classes")
    targets.setflags(write=False)
    return targets


def _load_component_seed(
    *,
    root: Path,
    record: Mapping[str, Any],
    stack_rows: Mapping[tuple[str, int], StackRow],
    component: str,
    seed: int,
    frozen_component: Mapping[str, Any],
) -> Mapping[tuple[str, int], float]:
    _validate_prediction_record(
        root=root,
        value=record,
        component=component,
        seed=seed,
        frozen_component=frozen_component,
    )
    table = _stable_parquet_table(
        root=root,
        record=record,
        name=f"{component} seed {seed}",
    )
    _table_columns(table, COMPONENT_COLUMNS, f"{component} seed {seed}")
    attestation = _mapping(
        record.get("projection_attestation"),
        f"{component} seed {seed} projection attestation",
    )
    expected_metadata = {
        b"voice_coach_schema_version": PREDICTION_RECORD_SCHEMA_VERSION.encode(),
        b"voice_coach_component": component.encode(),
        b"voice_coach_seed": str(seed).encode(),
        b"voice_coach_split": b"stack",
        b"voice_coach_source_lineage_sha256": str(
            record["source_lineage_sha256"]
        ).encode(),
        b"voice_coach_source_checkpoint_sha256s": ",".join(
            str(value) for value in record["source_checkpoint_sha256s"]
        ).encode(),
        b"voice_coach_projection_attestation_sha256": hashlib.sha256(
            canonical_json_bytes(dict(attestation), pretty=False)
        )
        .hexdigest()
        .encode(),
    }
    if table.schema.metadata != expected_metadata:
        raise S106TrainingError(
            f"{component} seed {seed} Parquet provenance metadata differs"
        )
    if table.num_rows != record["row_count"]:
        raise S106TrainingError(f"{component} seed {seed} row count differs")
    output: dict[tuple[str, int], float] = {}
    for index, value in enumerate(table.to_pylist()):
        utterance = _string(
            value.get("utterance_id_sha256"),
            f"{component} seed {seed} row {index} utterance",
        )
        speaker = _string(
            value.get("speaker_id_sha256"),
            f"{component} seed {seed} row {index} speaker",
        )
        phone_index = _integer(
            value.get("phone_index"),
            f"{component} seed {seed} phone index",
            minimum=0,
        )
        if value.get("split") != "stack":
            raise S106TrainingError("component prediction contains a non-stack row")
        key = (utterance, phone_index)
        stack_row = stack_rows.get(key)
        if stack_row is None:
            raise S106TrainingError("component prediction contains a non-stack key")
        if speaker != stack_row.speaker_id_sha256:
            raise S106TrainingError("component prediction speaker binding differs")
        probability = _finite(value.get("probability"), "component probability")
        if probability < 0.0 or probability > 1.0:
            raise S106TrainingError("component probability is outside [0,1]")
        if key in output:
            raise S106TrainingError("component prediction row key is duplicated")
        output[key] = probability
    return output


def _assemble_feature_rows(
    rows: Sequence[StackRow],
    component_feature_values: Mapping[str, Sequence[Mapping[str, float]]],
) -> tuple[
    tuple[Mapping[str, Any], ...],
    np.ndarray,
    tuple[str, ...],
    np.ndarray,
    str,
]:
    """Build features from identifiers/confidence only; no target is accepted."""

    feature_rows: list[Mapping[str, Any]] = []
    feature_vectors: list[list[float]] = []
    speakers: list[str] = []
    folds: list[int] = []
    for row_index, row in enumerate(rows):
        fold = assign_speaker_fold(row.speaker_id_sha256)
        features: dict[str, float] = {}
        for component in COMPONENTS:
            aggregate = component_feature_values[component][row_index]
            for suffix in (
                "probability_mean",
                "logit_from_mean_probability",
                "margin_abs_2p_minus_1",
                "entropy_binary",
                "seed_probability_variance_population",
                "missing",
            ):
                features[f"{component}__{suffix}"] = float(aggregate[suffix])
        confidence = alignment_confidence_features(row.alignment_confidence)
        features["alignment__target_interval_confidence"] = float(
            confidence["target_interval_confidence"]
        )
        features["alignment__confidence_missing"] = float(
            confidence["confidence_missing"]
        )
        if tuple(features) != EXACT_FEATURES:
            raise S106TrainingError("constructed feature order differs")
        feature_rows.append(
            {
                "utterance_id_sha256": row.utterance_id_sha256,
                "speaker_id_sha256": row.speaker_id_sha256,
                "phone_index": row.phone_index,
                "split": "stack",
                "fold": fold,
                **features,
            }
        )
        feature_vectors.append([features[name] for name in EXACT_FEATURES])
        speakers.append(row.speaker_id_sha256)
        folds.append(fold)
    feature_array: np.ndarray = np.asarray(feature_vectors, dtype=np.float64)
    if not np.isfinite(feature_array).all():
        raise S106TrainingError("component matrix contains a non-finite value")
    if set(folds) != set(range(OOF_FOLD_COUNT)):
        raise S106TrainingError("not every preregistered OOF fold is represented")
    feature_freeze_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                "schema_version": "voice_coach.wavlm_s106_feature_freeze.v2",
                "feature_columns": list(EXACT_FEATURES),
                "rows": list(feature_rows),
            },
            pretty=False,
        )
    ).hexdigest()
    feature_array.setflags(write=False)
    return (
        tuple(feature_rows),
        feature_array,
        tuple(speakers),
        np.asarray(folds, dtype=np.int64),
        feature_freeze_sha256,
    )


def build_component_matrix(
    *,
    root: Path,
    bundle: BundleEvidence,
) -> MatrixBundle:
    """Interpret committed files while keeping labels outside feature assembly."""

    rows = _load_feature_identity_matrix(
        root,
        bundle.feature_identity_record,
    )
    rows_by_key = {row.key: row for row in rows}
    per_component: dict[str, np.ndarray] = {}
    per_missing: dict[str, np.ndarray] = {}
    component_feature_values: dict[str, list[Mapping[str, float]]] = {}
    for component in COMPONENTS:
        seed_maps = {
            seed: _load_component_seed(
                root=root,
                record=bundle.component_records[component][seed],
                stack_rows=rows_by_key,
                component=component,
                seed=seed,
                frozen_component=bundle.frozen_components[component],
            )
            for seed in SEEDS
        }
        aggregates: list[Mapping[str, float]] = []
        for row in rows:
            present = [seed for seed in SEEDS if row.key in seed_maps[seed]]
            if present and tuple(present) != SEEDS:
                raise S106TrainingError(
                    f"{component} has partial seed presence for one row"
                )
            seed_values = (
                {seed: seed_maps[seed][row.key] for seed in SEEDS} if present else {}
            )
            try:
                aggregates.append(aggregate_component_probabilities(seed_values))
            except S106ContractError as error:
                raise S106TrainingError(str(error)) from error
        component_feature_values[component] = aggregates
        per_component[component] = np.asarray(
            [value["probability_mean"] for value in aggregates],
            dtype=np.float64,
        )
        per_missing[component] = np.asarray(
            [value["missing"] for value in aggregates],
            dtype=np.float64,
        )
    (
        feature_rows,
        feature_array,
        speakers,
        folds,
        feature_freeze_sha256,
    ) = _assemble_feature_rows(
        rows,
        component_feature_values,
    )
    if feature_array.flags.writeable:
        raise S106TrainingError("feature matrix was not frozen before label open")
    targets = _load_label_targets(
        root,
        bundle.label_record,
        frozen_rows=rows,
    )
    return MatrixBundle(
        rows=rows,
        feature_rows=feature_rows,
        features=feature_array,
        feature_freeze_sha256=feature_freeze_sha256,
        targets=targets,
        speakers=speakers,
        folds=folds,
        component_probabilities=per_component,
        component_missing=per_missing,
    )


def _standardize_fit(features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = np.mean(features, axis=0, dtype=np.float64)
    scale = np.std(features, axis=0, dtype=np.float64)
    scale = np.where(scale == 0.0, 1.0, scale)
    return mean, scale


def _fit_logistic(
    features: np.ndarray,
    targets: np.ndarray,
    *,
    regularization_c: float,
) -> tuple[LogisticRegression, np.ndarray, np.ndarray]:
    if len(np.unique(targets)) != 2:
        raise S106TrainingError("fusion training partition lacks both classes")
    mean, scale = _standardize_fit(features)
    standardized = (features - mean) / scale
    model = LogisticRegression(
        C=regularization_c,
        l1_ratio=0.0,
        solver="lbfgs",
        max_iter=2000,
        tol=1e-8,
        class_weight=None,
        random_state=0,
    )
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        try:
            model.fit(standardized, targets)
        except (ValueError, ConvergenceWarning) as error:
            raise S106TrainingError("fusion logistic fit failed") from error
    if tuple(model.classes_) != (0, 1):
        raise S106TrainingError("fusion class order differs")
    return model, mean, scale


def _speaker_macro_f1(
    targets: np.ndarray,
    predictions: np.ndarray,
    speakers: Sequence[str],
) -> float:
    values: list[float] = []
    speaker_array = np.asarray(speakers, dtype=object)
    for speaker in sorted(set(speakers)):
        mask = speaker_array == speaker
        truth = targets[mask]
        predicted = predictions[mask]
        tp = int(np.sum((truth == 1) & (predicted == 1)))
        tn = int(np.sum((truth == 0) & (predicted == 0)))
        fp = int(np.sum((truth == 0) & (predicted == 1)))
        fn = int(np.sum((truth == 1) & (predicted == 0)))
        positive_denominator = 2 * tp + fp + fn
        negative_denominator = 2 * tn + fp + fn
        positive = 0.0 if positive_denominator == 0 else 2.0 * tp / positive_denominator
        negative = 0.0 if negative_denominator == 0 else 2.0 * tn / negative_denominator
        values.append((positive + negative) / 2.0)
    if not values:
        raise S106TrainingError("speaker-macro F1 has no speakers")
    return float(np.mean(values))


def _thresholds(config: Mapping[str, Any]) -> tuple[float, ...]:
    selection = _mapping(
        _mapping(config["oof"], "oof").get("oof_selection"),
        "oof selection",
    )
    grid = _mapping(selection.get("threshold_grid"), "threshold grid")
    start = _finite(grid.get("start"), "threshold start")
    stop = _finite(grid.get("stop"), "threshold stop")
    step = _finite(grid.get("step"), "threshold step")
    count = int(round((stop - start) / step)) + 1
    values = tuple(round(start + step * index, 10) for index in range(count))
    if values[-1] != stop:
        raise S106TrainingError("threshold grid arithmetic differs")
    return values


def select_oof_fusion(
    *,
    matrix: MatrixBundle,
    config: Mapping[str, Any],
) -> SelectionResult:
    """Run deterministic speaker-OOF selection exactly once."""

    selection = _mapping(
        _mapping(config["oof"], "oof").get("oof_selection"),
        "oof selection",
    )
    c_values = tuple(float(value) for value in selection["regularization_c_grid"])
    thresholds = _thresholds(config)
    candidates: list[Mapping[str, Any]] = []
    probabilities_by_c: dict[float, np.ndarray] = {}
    fold_training_speakers: dict[int, int] = {}
    fold_validation_speakers: dict[int, int] = {}
    speakers_array = np.asarray(matrix.speakers, dtype=object)
    for regularization_c in c_values:
        probabilities: np.ndarray = np.full(
            len(matrix.targets),
            np.nan,
            dtype=np.float64,
        )
        for fold in range(OOF_FOLD_COUNT):
            validation_mask = matrix.folds == fold
            training_mask = ~validation_mask
            validation_speakers = set(speakers_array[validation_mask].tolist())
            training_speakers = set(speakers_array[training_mask].tolist())
            if (
                not validation_mask.any()
                or not training_mask.any()
                or validation_speakers & training_speakers
            ):
                raise S106TrainingError("OOF speaker partition is invalid")
            fold_training_speakers[fold] = len(training_speakers)
            fold_validation_speakers[fold] = len(validation_speakers)
            model, mean, scale = _fit_logistic(
                matrix.features[training_mask],
                matrix.targets[training_mask],
                regularization_c=regularization_c,
            )
            standardized = (matrix.features[validation_mask] - mean) / scale
            with threadpool_limits(limits=1):
                probabilities[validation_mask] = model.predict_proba(standardized)[:, 1]
        if not np.isfinite(probabilities).all():
            raise S106TrainingError("OOF predictions are incomplete")
        probabilities_by_c[regularization_c] = probabilities
        for threshold in thresholds:
            predictions = (probabilities >= threshold).astype(np.int64)
            metric = _speaker_macro_f1(
                matrix.targets,
                predictions,
                matrix.speakers,
            )
            candidates.append(
                {
                    "regularization_c": regularization_c,
                    "threshold": threshold,
                    "speaker_macro_phone_macro_f1": metric,
                }
            )
    selected = max(
        candidates,
        key=lambda value: (
            float(value["speaker_macro_phone_macro_f1"]),
            -float(value["regularization_c"]),
            -abs(float(value["threshold"]) - 0.5),
            -float(value["threshold"]),
        ),
    )
    selected_c = float(selected["regularization_c"])
    selected_threshold = float(selected["threshold"])
    selected_probabilities = probabilities_by_c[selected_c]
    selected_predictions = (selected_probabilities >= selected_threshold).astype(
        np.int64
    )
    return SelectionResult(
        selected_c=selected_c,
        selected_threshold=selected_threshold,
        selected_metric=float(selected["speaker_macro_phone_macro_f1"]),
        oof_probabilities=selected_probabilities,
        oof_predictions=selected_predictions,
        candidates=tuple(candidates),
        fold_training_speakers=fold_training_speakers,
        fold_validation_speakers=fold_validation_speakers,
    )


def fit_full_stack(
    *,
    matrix: MatrixBundle,
    regularization_c: float,
) -> FitParameters:
    """Fit once on full stack; this function intentionally has no predict call."""

    model, mean, scale = _fit_logistic(
        matrix.features,
        matrix.targets,
        regularization_c=regularization_c,
    )
    coefficients = np.asarray(model.coef_[0], dtype=np.float64)
    intercept = float(model.intercept_[0])
    if (
        coefficients.shape != (len(EXACT_FEATURES),)
        or not np.isfinite(coefficients).all()
        or not math.isfinite(intercept)
    ):
        raise S106TrainingError("final fusion parameters are invalid")
    return FitParameters(
        mean=mean,
        scale=scale,
        coefficients=coefficients,
        intercept=intercept,
    )


def _control_metrics(
    matrix: MatrixBundle,
) -> Mapping[str, Any]:
    controls: dict[str, Any] = {}
    for component in COMPONENTS:
        probabilities = matrix.component_probabilities[component]
        predictions = (probabilities >= 0.5).astype(np.int64)
        control_name = (
            "gop_only" if component == "gop_acoustic_posterior" else f"{component}_only"
        )
        controls[control_name] = {
            "threshold": 0.5,
            "speaker_macro_phone_macro_f1": _speaker_macro_f1(
                matrix.targets,
                predictions,
                matrix.speakers,
            ),
        }
    unweighted = np.mean(
        np.column_stack(
            [matrix.component_probabilities[component] for component in COMPONENTS]
        ),
        axis=1,
    )
    controls["unweighted_component_probability_mean"] = {
        "threshold": 0.5,
        "speaker_macro_phone_macro_f1": _speaker_macro_f1(
            matrix.targets,
            (unweighted >= 0.5).astype(np.int64),
            matrix.speakers,
        ),
    }
    return controls


def build_disagreement_report(matrix: MatrixBundle) -> Mapping[str, Any]:
    pairs: list[Mapping[str, Any]] = []
    for left_index, left in enumerate(COMPONENTS):
        for right in COMPONENTS[left_index + 1 :]:
            left_values = matrix.component_probabilities[left]
            right_values = matrix.component_probabilities[right]
            eligible = (matrix.component_missing[left] == 0.0) & (
                matrix.component_missing[right] == 0.0
            )
            if eligible.any():
                absolute = np.abs(left_values[eligible] - right_values[eligible])
                decisions = (left_values[eligible] >= 0.5) != (
                    right_values[eligible] >= 0.5
                )
                eligible_rows = int(np.sum(eligible))
                mean_absolute = float(np.mean(absolute))
                decision_rate = float(np.mean(decisions))
            else:
                eligible_rows = 0
                mean_absolute = None
                decision_rate = None
            pairs.append(
                {
                    "left": left,
                    "right": right,
                    "eligible_rows": eligible_rows,
                    "mean_absolute_probability_difference": mean_absolute,
                    "decision_disagreement_rate_at_0_5": decision_rate,
                }
            )
    return {
        "schema_version": DISAGREEMENT_SCHEMA_VERSION,
        "status": "completed",
        "stack_only": True,
        "row_count": len(matrix.rows),
        "component_missing_rows": {
            component: int(np.sum(matrix.component_missing[component]))
            for component in COMPONENTS
        },
        "pairs": pairs,
        "labels_used": False,
        "full_stack_in_sample_metric": None,
    }


def _write_parquet(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    schema_version: str,
) -> None:
    if not rows:
        raise S106TrainingError("cannot write an empty parquet artifact")
    table = pa.Table.from_pylist(list(rows)).replace_schema_metadata(
        {b"voice_coach_schema_version": schema_version.encode("utf-8")}
    )
    pq.write_table(
        table,
        path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )


def _fold_manifest(matrix: MatrixBundle) -> Mapping[str, Any]:
    speakers: list[Mapping[str, Any]] = []
    speaker_array = np.asarray(matrix.speakers, dtype=object)
    for speaker in sorted(set(matrix.speakers)):
        mask = speaker_array == speaker
        fold_values = set(matrix.folds[mask].tolist())
        if len(fold_values) != 1:
            raise S106TrainingError("speaker appears in multiple generated folds")
        speakers.append(
            {
                "speaker_id_sha256": speaker,
                "fold": int(next(iter(fold_values))),
                "row_count": int(np.sum(mask)),
            }
        )
    return {
        "schema_version": FOLD_SCHEMA_VERSION,
        "status": "completed",
        "assignment_namespace": OOF_NAMESPACE,
        "fold_count": OOF_FOLD_COUNT,
        "assignment_unit": "speaker_id_sha256",
        "row_count": len(matrix.rows),
        "speaker_count": len(speakers),
        "speakers": speakers,
        "speaker_overlap_maximum": 0,
    }


def _matrix_rows(matrix: MatrixBundle) -> tuple[Mapping[str, Any], ...]:
    return matrix.feature_rows


def _oof_rows(
    matrix: MatrixBundle,
    selection: SelectionResult,
) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(matrix.rows):
        rows.append(
            {
                "utterance_id_sha256": row.utterance_id_sha256,
                "speaker_id_sha256": row.speaker_id_sha256,
                "phone_index": row.phone_index,
                "split": "stack",
                "fold": int(matrix.folds[index]),
                "reference_binary_error_label": int(matrix.targets[index]),
                "probability": float(selection.oof_probabilities[index]),
                "prediction": int(selection.oof_predictions[index]),
            }
        )
    return tuple(rows)


def _oof_report(
    matrix: MatrixBundle,
    selection: SelectionResult,
) -> Mapping[str, Any]:
    fold_rows = {
        str(fold): int(np.sum(matrix.folds == fold)) for fold in range(OOF_FOLD_COUNT)
    }
    return {
        "schema_version": OOF_REPORT_SCHEMA_VERSION,
        "status": "completed",
        "stack_only": True,
        "metric": "speaker_macro_phone_macro_f1",
        "selection_scope": "aggregate_oof_predictions_only",
        "selection_passes": 1,
        "row_count": len(matrix.rows),
        "speaker_count": len(set(matrix.speakers)),
        "fold_count": OOF_FOLD_COUNT,
        "fold_rows": fold_rows,
        "fold_training_speakers": {
            str(key): value for key, value in selection.fold_training_speakers.items()
        },
        "fold_validation_speakers": {
            str(key): value for key, value in selection.fold_validation_speakers.items()
        },
        "selected_regularization_c": selection.selected_c,
        "selected_threshold": selection.selected_threshold,
        "selected_oof_metric": selection.selected_metric,
        "candidate_results": list(selection.candidates),
        "controls_at_fixed_0_5": _control_metrics(matrix),
        "every_row_exactly_one_oof_prediction": True,
        "speaker_overlap_maximum": 0,
        "full_stack_in_sample_metric": None,
    }


def _checkpoint(
    *,
    parameters: FitParameters,
    selection: SelectionResult,
    config: Mapping[str, Any],
    execution_lock_sha256: str,
    component_bundle_commit_sha256: str,
    feature_freeze_sha256: str,
) -> Mapping[str, Any]:
    feature_contract = _mapping(config["feature_contract"], "feature contract")
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "status": "completed",
        "family": "standardized_l2_logistic_regression",
        "feature_order": list(EXACT_FEATURES),
        "feature_contract_sha256": hashlib.sha256(
            canonical_json_bytes(feature_contract, pretty=False)
        ).hexdigest(),
        "source_execution_lock_sha256": execution_lock_sha256,
        "source_component_bundle_commit_sha256": component_bundle_commit_sha256,
        "feature_freeze_sha256": feature_freeze_sha256,
        "selected_regularization_c": selection.selected_c,
        "selected_threshold": selection.selected_threshold,
        "standardizer_mean": parameters.mean.tolist(),
        "standardizer_scale": parameters.scale.tolist(),
        "coefficients": parameters.coefficients.tolist(),
        "intercept": parameters.intercept,
        "fit_source": "full_stack_after_oof_selection",
        "fit_count": 1,
        "in_sample_prediction_computed": False,
        "in_sample_metric_computed": False,
        "in_sample_metric_reported": False,
    }


def _stack_report(
    *,
    root: Path,
    config_path: Path,
    execution_lock_path: Path,
    bundle: BundleEvidence,
    matrix: MatrixBundle,
    selection: SelectionResult,
) -> Mapping[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "completed",
        "experiment_id": "aihub71490_wavlm_s106_stack_fusion_v1",
        "stage": "s106_stack_only_confidence_aware_fusion",
        "development_only": True,
        "oracle_alignment": True,
        "silver_label": True,
        "promotion_decision_allowed": False,
        "sources": {
            "config": {
                "path": _workspace_relative(root, config_path),
                "sha256": sha256_file(config_path),
            },
            "execution_lock": {
                "path": _workspace_relative(root, execution_lock_path),
                "sha256": sha256_file(execution_lock_path),
            },
            "component_bundle_commit": {
                "path": _workspace_relative(root, bundle.path),
                "sha256": bundle.sha256,
            },
        },
        "matrix": {
            "row_count": len(matrix.rows),
            "speaker_count": len(set(matrix.speakers)),
            "feature_count": len(EXACT_FEATURES),
            "feature_order": list(EXACT_FEATURES),
            "feature_freeze_sha256": matrix.feature_freeze_sha256,
            "feature_matrix_writeable_when_labels_opened": False,
            "label_in_feature_matrix": False,
        },
        "oof_evidence": {
            "selected_regularization_c": selection.selected_c,
            "selected_threshold": selection.selected_threshold,
            "selected_speaker_macro_phone_macro_f1": selection.selected_metric,
        },
        "final_fit": {
            "source": "full_stack_after_oof_selection",
            "fit_count": 1,
            "selected_settings_frozen": True,
            "in_sample_prediction_computed": False,
            "in_sample_metric_computed": False,
            "in_sample_metric_reported": False,
            "evidence_role": "checkpoint_only",
        },
        "leakage": {
            "stack_accessed": True,
            "fusion_feature_train_accessed": False,
            "fusion_feature_dev_accessed": False,
            "predecessor_dev_predictions_reused_as_components": False,
            "b07_dev_predictions_reused_as_component": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "environment_files_read": [],
            "feature_builder_received_labels": False,
            "realized_phone_feature_used": False,
            "alignment_operation_feature_used": False,
            "error_tag_feature_used": False,
        },
        "predecessor_deep_audit": {
            "completed_before_stack_resolution": True,
            "validator_invocation_counts": {"s104": 2, "s105": 1, "b07": 1},
            "train_or_fit_inputs_read": True,
            "dev_inputs_read": True,
            "dev_prediction_artifacts_read": True,
            "evidence_role": "read_only_predecessor_recomputation_not_fusion_input",
        },
        "artifacts_expected": 7,
        "result_status": "engineering_stack_only",
        "s107_calibration_allowed_only_after_commit_validation": True,
    }


def build_generation(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    bundle: BundleEvidence,
    temporary: Path,
) -> tuple[MatrixBundle, SelectionResult]:
    matrix = build_component_matrix(root=root, bundle=bundle)
    selection = select_oof_fusion(matrix=matrix, config=config)
    parameters = fit_full_stack(
        matrix=matrix,
        regularization_c=selection.selected_c,
    )
    temporary.mkdir(parents=True, exist_ok=False)
    _write_parquet(
        temporary / "stack_component_matrix.parquet",
        _matrix_rows(matrix),
        schema_version=MATRIX_SCHEMA_VERSION,
    )
    _write_atomic_json(
        temporary / "speaker_fold_manifest.json",
        _fold_manifest(matrix),
    )
    _write_parquet(
        temporary / "oof_predictions.parquet",
        _oof_rows(matrix, selection),
        schema_version=OOF_PREDICTION_SCHEMA_VERSION,
    )
    _write_atomic_json(
        temporary / "oof_aggregate_report.json",
        _oof_report(matrix, selection),
    )
    _write_atomic_json(
        temporary / "component_disagreement_report.json",
        build_disagreement_report(matrix),
    )
    _write_atomic_json(
        temporary / "final_fusion_checkpoint.json",
        _checkpoint(
            parameters=parameters,
            selection=selection,
            config=config,
            execution_lock_sha256=sha256_file(execution_lock_path),
            component_bundle_commit_sha256=bundle.sha256,
            feature_freeze_sha256=matrix.feature_freeze_sha256,
        ),
    )
    _write_atomic_json(
        temporary / "stack_only_report.json",
        _stack_report(
            root=root,
            config_path=config_path,
            execution_lock_path=execution_lock_path,
            bundle=bundle,
            matrix=matrix,
            selection=selection,
        ),
    )
    return matrix, selection


def artifact_records(root: Path) -> tuple[Mapping[str, Any], ...]:
    expected_files = {relative for _, relative, _, _ in ARTIFACT_LAYOUT}
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise S106TrainingError("S106 generation contains a symlink")
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            actual_files.add(relative)
        elif path.is_dir():
            actual_directories.add(relative)
        else:
            raise S106TrainingError("S106 generation node type differs")
    if actual_files != expected_files or actual_directories:
        raise S106TrainingError("S106 generation exact layout differs")
    records: list[Mapping[str, Any]] = []
    for name, relative, format_name, access_scope in ARTIFACT_LAYOUT:
        path = root / relative
        if path.stat().st_size <= 0:
            raise S106TrainingError(f"S106 artifact is empty: {name}")
        records.append(
            {
                "name": name,
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "format": format_name,
                "access_scope": access_scope,
            }
        )
    return tuple(records)


def _generation_id(records: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema_version": COMMIT_SCHEMA_VERSION,
                "artifacts": list(records),
            },
            pretty=False,
        )
    ).hexdigest()


def publish_generation(
    *,
    temporary: Path,
    generation_root: Path,
) -> PublishedGeneration:
    records = artifact_records(temporary)
    generation_id = _generation_id(records)
    destination = generation_root / generation_id
    generation_root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_dir() or artifact_records(destination) != records:
            raise S106TrainingError("content-addressed S106 generation differs")
        shutil.rmtree(temporary)
        created = False
    else:
        os.replace(temporary, destination)
        created = True
    return PublishedGeneration(
        generation_id=generation_id,
        path=destination,
        records=records,
        created=created,
    )


def _registry_path(root: Path, config: Mapping[str, Any]) -> Path:
    outputs = _mapping(config["outputs"], "outputs")
    registry_root = _resolve_output(
        root,
        outputs["registry_root"],
        "outputs.registry_root",
    )
    return (
        registry_root
        / _string(outputs["planned_registry_run_id"], "registry run id")
        / "metadata.json"
    )


def _runtime_metadata(root: Path) -> Mapping[str, Any]:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pyarrow": pa.__version__,
        "scikit_learn": sklearn.__version__,
        "device": "cpu",
        "cuda_used": False,
        "thread_count": 1,
        "git": _git_metadata(root),
    }


def _registry_base(
    *,
    root: Path,
    config_path: Path,
    execution_lock_path: Path,
    bundle_path: Path,
    run_id: str,
) -> Mapping[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "stage": "s106_stack_only_confidence_aware_fusion",
        "inputs": {
            "config": _record(root, config_path),
            "execution_lock": _record(root, execution_lock_path),
            "component_bundle_commit": _record(root, bundle_path),
        },
        "runtime": _runtime_metadata(root),
        "seeds": {
            "component_prediction_seeds": list(SEEDS),
            "fold_namespace": OOF_NAMESPACE,
        },
        "split_access": {
            "stack": True,
            "train": False,
            "dev": False,
            "calibration": False,
            "locked_test": False,
        },
        "environment_files_read": [],
    }


def _running_registry(
    *,
    root: Path,
    config_path: Path,
    execution_lock_path: Path,
    bundle_path: Path,
    run_id: str,
) -> Mapping[str, Any]:
    return {
        **_registry_base(
            root=root,
            config_path=config_path,
            execution_lock_path=execution_lock_path,
            bundle_path=bundle_path,
            run_id=run_id,
        ),
        "status": "running",
        "started_at_utc": _now_utc(),
        "completed_at_utc": None,
        "failure": None,
        "generation_id": None,
        "generation_path": None,
        "artifacts": [],
        "metrics": [],
    }


def _failed_registry(
    running: Mapping[str, Any],
    error: BaseException,
) -> Mapping[str, Any]:
    return {
        **running,
        "status": "failed",
        "completed_at_utc": _now_utc(),
        "failure": {
            "type": type(error).__name__,
            "message": "S106 execution failed; inspect restricted logs",
        },
    }


def _completed_registry(
    *,
    root: Path,
    running: Mapping[str, Any],
    generation: PublishedGeneration,
    selection: SelectionResult,
) -> Mapping[str, Any]:
    artifacts = [
        {
            **record,
            "path": _workspace_relative(
                root,
                generation.path / str(record["path"]),
            ),
        }
        for record in generation.records
    ]
    return {
        **running,
        "status": "completed",
        "completed_at_utc": _now_utc(),
        "failure": None,
        "generation_id": generation.generation_id,
        "generation_path": _workspace_relative(root, generation.path),
        "artifacts": artifacts,
        "metrics": [
            {
                "name": "speaker_macro_phone_macro_f1",
                "split": "stack_oof",
                "value": selection.selected_metric,
            }
        ],
        "selection": {
            "regularization_c": selection.selected_c,
            "threshold": selection.selected_threshold,
            "scope": "aggregate_oof_predictions_only",
        },
        "final_fit_in_sample_metric_reported": False,
    }


def _build_commit(
    *,
    root: Path,
    config_path: Path,
    execution_lock_path: Path,
    bundle: BundleEvidence,
    generation: PublishedGeneration,
    registry_path: Path,
) -> Mapping[str, Any]:
    commit: dict[str, Any] = {
        "schema_version": COMMIT_SCHEMA_VERSION,
        "status": "committed",
        "generation_id": generation.generation_id,
        "generation_path": _workspace_relative(root, generation.path),
        "artifacts": list(generation.records),
        "config": _record(root, config_path),
        "execution_lock": _record(root, execution_lock_path),
        "component_bundle_commit": _record(root, bundle.path),
        "registry": {
            **_record(root, registry_path),
            "run_id": load_json_strict(registry_path, "S106 registry")["run_id"],
            "status": "completed",
            "artifact_count": len(generation.records),
        },
    }
    commit["binding_sha256"] = _binding(commit)
    return commit


def _compare_float_array(
    actual: Sequence[object],
    expected: np.ndarray,
    name: str,
) -> None:
    values: np.ndarray = np.asarray(
        [_finite(value, name) for value in actual],
        dtype=np.float64,
    )
    if values.shape != expected.shape or not np.allclose(
        values,
        expected,
        rtol=1e-12,
        atol=1e-12,
    ):
        raise S106TrainingError(f"{name} differs")


def _validate_matrix_artifact(path: Path, matrix: MatrixBundle) -> None:
    table = pq.read_table(path)
    _output_table_contract(
        table,
        (*MATRIX_ID_COLUMNS, *EXACT_FEATURES),
        MATRIX_SCHEMA_VERSION,
        "published component matrix",
    )
    rows = table.to_pylist()
    if len(rows) != len(matrix.feature_rows):
        raise S106TrainingError("published component matrix row count differs")
    for actual, expected in zip(rows, matrix.feature_rows, strict=True):
        for key in MATRIX_ID_COLUMNS:
            if actual.get(key) != expected.get(key):
                raise S106TrainingError("published matrix identifier differs")
        _compare_float_array(
            [actual[name] for name in EXACT_FEATURES],
            np.asarray([expected[name] for name in EXACT_FEATURES]),
            "published matrix features",
        )
        if any(name in actual for name in ("reference_binary_error_label", "y_true")):
            raise S106TrainingError("label leaked into published feature matrix")


def _validate_oof_artifact(
    path: Path,
    matrix: MatrixBundle,
    selection: SelectionResult,
) -> None:
    table = pq.read_table(path)
    _output_table_contract(
        table,
        OOF_COLUMNS,
        OOF_PREDICTION_SCHEMA_VERSION,
        "published OOF predictions",
    )
    rows = table.to_pylist()
    expected = _oof_rows(matrix, selection)
    if len(rows) != len(expected):
        raise S106TrainingError("published OOF row count differs")
    for actual, target in zip(rows, expected, strict=True):
        for name in OOF_COLUMNS:
            if name == "probability":
                if not math.isclose(
                    _finite(actual.get(name), name),
                    float(target[name]),
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                ):
                    raise S106TrainingError("published OOF probability differs")
            elif actual.get(name) != target[name]:
                raise S106TrainingError("published OOF row differs")


def _validate_json_equal(path: Path, expected: Mapping[str, Any], name: str) -> None:
    actual = load_json_strict(path, name)
    if actual != expected:
        raise S106TrainingError(f"{name} differs from recomputation")


def _validate_checkpoint(
    path: Path,
    expected: Mapping[str, Any],
) -> None:
    actual = load_json_strict(path, "S106 final checkpoint")
    if set(actual) != set(expected):
        raise S106TrainingError("S106 final checkpoint schema differs")
    for key in (
        "standardizer_mean",
        "standardizer_scale",
        "coefficients",
    ):
        _compare_float_array(
            _list(actual.get(key), f"checkpoint {key}"),
            np.asarray(expected[key], dtype=np.float64),
            f"checkpoint {key}",
        )
    for key, value in expected.items():
        if key in {"standardizer_mean", "standardizer_scale", "coefficients"}:
            continue
        if key == "intercept":
            if not math.isclose(
                _finite(actual.get(key), "checkpoint intercept"),
                float(value),
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise S106TrainingError("checkpoint intercept differs")
        elif actual.get(key) != value:
            raise S106TrainingError(f"checkpoint {key} differs")


def validate_generation_deep(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    bundle: BundleEvidence,
    generation_path: Path,
) -> Mapping[str, Any]:
    """Recompute all allowed evidence without trusting generated reports."""

    matrix = build_component_matrix(root=root, bundle=bundle)
    selection = select_oof_fusion(matrix=matrix, config=config)
    parameters = fit_full_stack(
        matrix=matrix,
        regularization_c=selection.selected_c,
    )
    records = artifact_records(generation_path)
    generation_id = _generation_id(records)
    if generation_path.name != generation_id:
        raise S106TrainingError("S106 generation directory identity differs")
    _validate_matrix_artifact(
        generation_path / "stack_component_matrix.parquet",
        matrix,
    )
    _validate_json_equal(
        generation_path / "speaker_fold_manifest.json",
        _fold_manifest(matrix),
        "S106 fold manifest",
    )
    _validate_oof_artifact(
        generation_path / "oof_predictions.parquet",
        matrix,
        selection,
    )
    _validate_json_equal(
        generation_path / "oof_aggregate_report.json",
        _oof_report(matrix, selection),
        "S106 OOF report",
    )
    _validate_json_equal(
        generation_path / "component_disagreement_report.json",
        build_disagreement_report(matrix),
        "S106 disagreement report",
    )
    _validate_checkpoint(
        generation_path / "final_fusion_checkpoint.json",
        _checkpoint(
            parameters=parameters,
            selection=selection,
            config=config,
            execution_lock_sha256=sha256_file(execution_lock_path),
            component_bundle_commit_sha256=bundle.sha256,
            feature_freeze_sha256=matrix.feature_freeze_sha256,
        ),
    )
    _validate_json_equal(
        generation_path / "stack_only_report.json",
        _stack_report(
            root=root,
            config_path=config_path,
            execution_lock_path=execution_lock_path,
            bundle=bundle,
            matrix=matrix,
            selection=selection,
        ),
        "S106 stack-only report",
    )
    return {
        "status": "valid",
        "generation_id": generation_id,
        "artifact_count": len(records),
        "row_count": len(matrix.rows),
        "speaker_count": len(set(matrix.speakers)),
        "feature_freeze_sha256": matrix.feature_freeze_sha256,
        "selected_regularization_c": selection.selected_c,
        "selected_threshold": selection.selected_threshold,
        "selected_oof_metric": selection.selected_metric,
        "full_stack_in_sample_metric_reported": False,
    }


def _validate_registry(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    bundle: BundleEvidence,
    generation_path: Path,
    generation_records: Sequence[Mapping[str, Any]],
    registry_path: Path,
    validation: Mapping[str, Any],
) -> Mapping[str, Any]:
    registry_sha = sha256_file(registry_path)
    registry = load_json_strict(registry_path, "S106 registry")
    outputs = _mapping(config["outputs"], "outputs")
    if (
        registry.get("schema_version") != 1
        or registry.get("run_id") != outputs.get("planned_registry_run_id")
        or registry.get("stage") != "s106_stack_only_confidence_aware_fusion"
        or registry.get("status") != "completed"
        or registry.get("failure") is not None
        or registry.get("generation_id") != generation_path.name
        or registry.get("generation_path") != _workspace_relative(root, generation_path)
        or registry.get("final_fit_in_sample_metric_reported") is not False
        or registry.get("environment_files_read") != []
    ):
        raise S106TrainingError("completed S106 registry status differs")
    started = _parse_utc(registry.get("started_at_utc"), "registry started_at_utc")
    completed = _parse_utc(
        registry.get("completed_at_utc"),
        "registry completed_at_utc",
    )
    if completed < started:
        raise S106TrainingError("registry completion precedes start")
    split_access = _mapping(registry.get("split_access"), "registry split access")
    if split_access != {
        "stack": True,
        "train": False,
        "dev": False,
        "calibration": False,
        "locked_test": False,
    }:
        raise S106TrainingError("registry split access differs")
    inputs = _mapping(registry.get("inputs"), "registry inputs")
    expected_inputs = {
        "config": _record(root, config_path),
        "execution_lock": _record(root, execution_lock_path),
        "component_bundle_commit": _record(root, bundle.path),
    }
    if inputs != expected_inputs:
        raise S106TrainingError("registry input binding differs")
    artifacts = _list(registry.get("artifacts"), "registry artifacts")
    expected_artifacts = [
        {
            **record,
            "path": _workspace_relative(
                root,
                generation_path / str(record["path"]),
            ),
        }
        for record in generation_records
    ]
    if artifacts != expected_artifacts:
        raise S106TrainingError("registry artifact binding differs")
    metrics = registry.get("metrics")
    if metrics != [
        {
            "name": "speaker_macro_phone_macro_f1",
            "split": "stack_oof",
            "value": validation["selected_oof_metric"],
        }
    ]:
        raise S106TrainingError("registry OOF metric differs")
    selection = _mapping(registry.get("selection"), "registry selection")
    if selection != {
        "regularization_c": validation["selected_regularization_c"],
        "threshold": validation["selected_threshold"],
        "scope": "aggregate_oof_predictions_only",
    }:
        raise S106TrainingError("registry selection differs")
    runtime = _mapping(registry.get("runtime"), "registry runtime")
    if (
        runtime.get("python") != platform.python_version()
        or runtime.get("numpy") != np.__version__
        or runtime.get("pyarrow") != pa.__version__
        or runtime.get("scikit_learn") != sklearn.__version__
        or runtime.get("device") != "cpu"
        or runtime.get("cuda_used") is not False
        or runtime.get("thread_count") != 1
    ):
        raise S106TrainingError("registry runtime differs")
    git = _mapping(runtime.get("git"), "registry git metadata")
    if (
        not isinstance(git.get("available"), bool)
        or (
            git.get("available") is True
            and (
                not _is_git_oid(git.get("commit"))
                or not isinstance(git.get("dirty"), bool)
            )
        )
        or (
            git.get("available") is False
            and (git.get("commit") is not None or git.get("dirty") is not None)
        )
    ):
        raise S106TrainingError("registry git metadata differs")
    if registry.get("seeds") != {
        "component_prediction_seeds": list(SEEDS),
        "fold_namespace": OOF_NAMESPACE,
    }:
        raise S106TrainingError("registry seed contract differs")
    if sha256_file(registry_path) != registry_sha:
        raise S106TrainingError("registry changed during validation")
    return registry


def _precommit_revalidate(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    bundle: BundleEvidence,
    generation: PublishedGeneration,
    registry_path: Path,
    validation: Mapping[str, Any],
) -> None:
    """Recheck every mutable evidence edge before publishing the final marker."""

    final_lock = validate_execution_lock(
        root=root,
        config_path=config_path,
        config=config,
        lock_path=execution_lock_path,
    )
    final_bundle = validate_component_bundle_commit(
        root=root,
        config=config,
        execution_lock_path=execution_lock_path,
        execution_lock=final_lock,
        bundle_path=bundle.path,
    )
    final_validation = validate_generation_deep(
        root=root,
        config_path=config_path,
        config=config,
        execution_lock_path=execution_lock_path,
        bundle=final_bundle,
        generation_path=generation.path,
    )
    if (
        final_bundle.sha256 != bundle.sha256
        or final_bundle.normalized_records_sha256 != bundle.normalized_records_sha256
        or final_validation != validation
        or final_validation.get("feature_freeze_sha256")
        != validation.get("feature_freeze_sha256")
        or artifact_records(generation.path) != generation.records
    ):
        raise S106TrainingError("S106 evidence changed before commit publication")
    _validate_registry(
        root=root,
        config_path=config_path,
        config=config,
        execution_lock_path=execution_lock_path,
        bundle=final_bundle,
        generation_path=generation.path,
        generation_records=generation.records,
        registry_path=registry_path,
        validation=validation,
    )


def validate_s106_commit(
    *,
    workspace_root: Path,
    config_path: Path,
) -> Mapping[str, Any]:
    """Read-only deep validation of the completed S106 evidence chain."""

    root = workspace_root.resolve(strict=True)
    resolved_config = config_path
    if not resolved_config.is_absolute():
        resolved_config = root / resolved_config
    resolved_config = resolved_config.resolve(strict=True)
    config = load_config(resolved_config)
    validate_config(config)
    validate_implementation_sources(workspace_root=root, config=config)
    outputs = _mapping(config["outputs"], "outputs")
    lock_path = _resolve_file(
        root,
        outputs["execution_lock"],
        "S106 execution lock",
    )
    lock = validate_execution_lock(
        root=root,
        config_path=resolved_config,
        config=config,
        lock_path=lock_path,
    )
    bundle_path = _resolve_file(
        root,
        _mapping(config["inputs"], "inputs")["component_bundle_commit"],
        "S106 component bundle commit",
    )
    bundle = validate_component_bundle_commit(
        root=root,
        config=config,
        execution_lock_path=lock_path,
        execution_lock=lock,
        bundle_path=bundle_path,
    )
    commit_path = _resolve_file(
        root,
        outputs["commit_manifest"],
        "S106 commit",
    )
    commit_sha = sha256_file(commit_path)
    commit = load_json_strict(commit_path, "S106 commit")
    if set(commit) != {
        "schema_version",
        "status",
        "generation_id",
        "generation_path",
        "artifacts",
        "config",
        "execution_lock",
        "component_bundle_commit",
        "registry",
        "binding_sha256",
    }:
        raise S106TrainingError("S106 commit schema differs")
    if (
        commit.get("schema_version") != COMMIT_SCHEMA_VERSION
        or commit.get("status") != "committed"
        or not _is_sha256(commit.get("generation_id"))
        or commit.get("binding_sha256") != _binding(commit)
    ):
        raise S106TrainingError("S106 commit binding differs")
    generation_root = _resolve_output(
        root,
        outputs["generation_root"],
        "S106 generation root",
    )
    generation_path = _resolve_output(
        root,
        commit.get("generation_path"),
        "S106 generation path",
    )
    if (
        not generation_path.is_dir()
        or generation_path.parent != generation_root
        or generation_path.name != commit.get("generation_id")
    ):
        raise S106TrainingError("S106 commit generation path differs")
    validation = validate_generation_deep(
        root=root,
        config_path=resolved_config,
        config=config,
        execution_lock_path=lock_path,
        bundle=bundle,
        generation_path=generation_path,
    )
    records = artifact_records(generation_path)
    if commit.get("artifacts") != list(records):
        raise S106TrainingError("S106 commit artifact records differ")
    if commit.get("config") != _record(root, resolved_config):
        raise S106TrainingError("S106 commit config binding differs")
    if commit.get("execution_lock") != _record(root, lock_path):
        raise S106TrainingError("S106 commit execution-lock binding differs")
    if commit.get("component_bundle_commit") != _record(root, bundle.path):
        raise S106TrainingError("S106 commit bundle binding differs")
    registry_path = _registry_path(root, config)
    registry = _validate_registry(
        root=root,
        config_path=resolved_config,
        config=config,
        execution_lock_path=lock_path,
        bundle=bundle,
        generation_path=generation_path,
        generation_records=records,
        registry_path=registry_path,
        validation=validation,
    )
    registry_record = _mapping(commit.get("registry"), "commit registry record")
    if registry_record != {
        **_record(root, registry_path),
        "run_id": registry["run_id"],
        "status": "completed",
        "artifact_count": len(records),
    }:
        raise S106TrainingError("S106 commit registry binding differs")
    final_lock = validate_execution_lock(
        root=root,
        config_path=resolved_config,
        config=config,
        lock_path=lock_path,
    )
    final_bundle = validate_component_bundle_commit(
        root=root,
        config=config,
        execution_lock_path=lock_path,
        execution_lock=final_lock,
        bundle_path=bundle.path,
    )
    final_implementation = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )
    final_validation = validate_generation_deep(
        root=root,
        config_path=resolved_config,
        config=config,
        execution_lock_path=lock_path,
        bundle=final_bundle,
        generation_path=generation_path,
    )
    if (
        sha256_file(commit_path) != commit_sha
        or sha256_file(resolved_config) != commit["config"]["sha256"]
        or sha256_file(lock_path) != commit["execution_lock"]["sha256"]
        or sha256_file(bundle.path) != bundle.sha256
        or final_bundle.sha256 != bundle.sha256
        or final_bundle.normalized_records_sha256 != bundle.normalized_records_sha256
        or final_validation != validation
        or final_implementation != lock["implementation"]
        or sha256_file(registry_path) != registry_record["sha256"]
        or artifact_records(generation_path) != records
    ):
        raise S106TrainingError("S106 evidence changed during validation")
    return {
        **validation,
        "commit_sha256": commit_sha,
        "registry_sha256": registry_record["sha256"],
        "execution_lock_sha256": commit["execution_lock"]["sha256"],
        "component_bundle_commit_sha256": bundle.sha256,
        "stack_only": True,
        "calibration_accessed": False,
        "locked_test_accessed": False,
    }


def _recover_completed_registry(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    bundle: BundleEvidence,
    registry_path: Path,
    commit_path: Path,
) -> Mapping[str, Any]:
    registry = load_json_strict(registry_path, "S106 recovery registry")
    if registry.get("status") != "completed":
        raise S106TrainingError("existing S106 registry is not recoverable")
    generation_path = _resolve_output(
        root,
        registry.get("generation_path"),
        "recoverable generation",
    )
    validation = validate_generation_deep(
        root=root,
        config_path=config_path,
        config=config,
        execution_lock_path=execution_lock_path,
        bundle=bundle,
        generation_path=generation_path,
    )
    records = artifact_records(generation_path)
    generation = PublishedGeneration(
        generation_id=generation_path.name,
        path=generation_path,
        records=records,
        created=False,
    )
    _precommit_revalidate(
        root=root,
        config_path=config_path,
        config=config,
        execution_lock_path=execution_lock_path,
        bundle=bundle,
        generation=generation,
        registry_path=registry_path,
        validation=validation,
    )
    commit = _build_commit(
        root=root,
        config_path=config_path,
        execution_lock_path=execution_lock_path,
        bundle=bundle,
        generation=generation,
        registry_path=registry_path,
    )
    _publish_immutable_json(commit_path, commit)
    return validate_s106_commit(
        workspace_root=root,
        config_path=config_path,
    )


def run_s106_training(
    *,
    workspace_root: Path,
    config_path: Path,
) -> Mapping[str, Any]:
    """Run or safely recover the complete S106 stack-only publication."""

    root = workspace_root.resolve(strict=True)
    resolved_config = config_path
    if not resolved_config.is_absolute():
        resolved_config = root / resolved_config
    resolved_config = resolved_config.resolve(strict=True)
    config = load_config(resolved_config)
    validate_config(config)
    validate_implementation_sources(workspace_root=root, config=config)
    outputs = _mapping(config["outputs"], "outputs")
    lock_path = _resolve_file(
        root,
        outputs["execution_lock"],
        "S106 execution lock",
    )
    lock = validate_execution_lock(
        root=root,
        config_path=resolved_config,
        config=config,
        lock_path=lock_path,
    )
    bundle_path = _resolve_file(
        root,
        _mapping(config["inputs"], "inputs")["component_bundle_commit"],
        "S106 component bundle commit",
    )
    commit_path = _resolve_output(
        root,
        outputs["commit_manifest"],
        "outputs.commit_manifest",
    )
    registry_path = _registry_path(root, config)
    if commit_path.exists():
        validate_component_bundle_commit(
            root=root,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            bundle_path=bundle_path,
        )
        return validate_s106_commit(
            workspace_root=root,
            config_path=resolved_config,
        )
    if registry_path.exists():
        bundle = validate_component_bundle_commit(
            root=root,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            bundle_path=bundle_path,
        )
        return _recover_completed_registry(
            root=root,
            config_path=resolved_config,
            config=config,
            execution_lock_path=lock_path,
            bundle=bundle,
            registry_path=registry_path,
            commit_path=commit_path,
        )
    run_id = _string(outputs["planned_registry_run_id"], "registry run id")
    running = _running_registry(
        root=root,
        config_path=resolved_config,
        execution_lock_path=lock_path,
        bundle_path=bundle_path,
        run_id=run_id,
    )
    _publish_new_atomic_json(registry_path, running)
    generation_root = _resolve_output(
        root,
        outputs["generation_root"],
        "outputs.generation_root",
    )
    generation_root.mkdir(parents=True, exist_ok=True)
    temporary = generation_root / f".part-{uuid4().hex}"
    try:
        bundle = validate_component_bundle_commit(
            root=root,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            bundle_path=bundle_path,
        )
        _, selection = build_generation(
            root=root,
            config_path=resolved_config,
            config=config,
            execution_lock_path=lock_path,
            bundle=bundle,
            temporary=temporary,
        )
        generation = publish_generation(
            temporary=temporary,
            generation_root=generation_root,
        )
        validation = validate_generation_deep(
            root=root,
            config_path=resolved_config,
            config=config,
            execution_lock_path=lock_path,
            bundle=bundle,
            generation_path=generation.path,
        )
        if (
            validation["selected_regularization_c"] != selection.selected_c
            or validation["selected_threshold"] != selection.selected_threshold
            or not math.isclose(
                float(validation["selected_oof_metric"]),
                selection.selected_metric,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            raise S106TrainingError("post-publish selection validation differs")
        completed = _completed_registry(
            root=root,
            running=running,
            generation=generation,
            selection=selection,
        )
        _write_atomic_json(registry_path, completed)
        _precommit_revalidate(
            root=root,
            config_path=resolved_config,
            config=config,
            execution_lock_path=lock_path,
            bundle=bundle,
            generation=generation,
            registry_path=registry_path,
            validation=validation,
        )
        commit = _build_commit(
            root=root,
            config_path=resolved_config,
            execution_lock_path=lock_path,
            bundle=bundle,
            generation=generation,
            registry_path=registry_path,
        )
        _publish_immutable_json(commit_path, commit)
        return validate_s106_commit(
            workspace_root=root,
            config_path=resolved_config,
        )
    except BaseException as error:
        if temporary.exists():
            shutil.rmtree(temporary)
        if registry_path.exists():
            current = load_json_strict(registry_path, "S106 failure registry")
            if current.get("status") == "running":
                _write_atomic_json(
                    registry_path,
                    _failed_registry(running, error),
                )
        raise


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "COMMIT_SCHEMA_VERSION",
    "COMPONENT_BUNDLE_SCHEMA_VERSION",
    "DISAGREEMENT_SCHEMA_VERSION",
    "FEATURE_IDENTITY_COLUMNS",
    "FOLD_SCHEMA_VERSION",
    "LABEL_COLUMNS",
    "MATRIX_SCHEMA_VERSION",
    "OOF_PREDICTION_SCHEMA_VERSION",
    "OOF_REPORT_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "S106TrainingError",
    "artifact_records",
    "build_component_matrix",
    "build_disagreement_report",
    "build_generation",
    "fit_full_stack",
    "publish_generation",
    "run_s106_training",
    "select_oof_fusion",
    "validate_component_bundle_commit",
    "validate_execution_lock",
    "validate_generation_deep",
    "validate_s106_commit",
]
