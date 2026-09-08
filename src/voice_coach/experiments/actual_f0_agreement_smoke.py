"""Bounded, privacy-minimal orchestration primitives for actual F0 agreement.

The module fixes a deterministic project-``dev`` sample from the official
Validation pronunciation filename inventory before source audio is read.
Project roles come only from a pinned modeling-manifest metadata scan; an
official partition name is never treated as a project split.  Selection never
uses an acoustic score, never backfills failed items, and public evidence never
contains source paths, identifiers, PCM hashes, or per-item measurements.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Protocol, cast

from voice_coach.evaluation.pitch_contour_agreement import (
    PitchContourAgreementResult,
)


REPORT_SCHEMA_VERSION = "voice_coach.aihub71490_validation_f0_agreement_smoke.v2"
SELECTION_POLICY_ID = (
    "sha256_seeded_official_validation_project_dev_filename_inventory_v2"
)
SELECTION_DOMAIN = b"voice-coach:aihub71490:validation-project-dev-f0-smoke:v2\0"
MAXIMUM_SAMPLE_SIZE = 8
FIXED_DATASET_ID = "71490"
FIXED_OFFICIAL_PARTITION = "Validation"
FIXED_SOURCE_KIND = "pronunciation_audio"
FIXED_PROJECT_SPLIT = "dev"
KNOWN_PROJECT_SPLITS = frozenset(
    {"train", "dev", "stack", "calibration", "locked_test"}
)
MODELING_MANIFEST_COLUMNS = (
    "audio_path",
    "official_partitions",
    "pcm_sha256",
    "split",
)


class ActualF0AgreementSmokeError(ValueError):
    """Raised when the bounded smoke contract cannot be satisfied safely."""


@dataclass(frozen=True, slots=True)
class ModelingManifestMetadataRow:
    """Restricted metadata fields deserialized from one modeling-manifest row."""

    audio_path: str
    official_partitions: tuple[str, ...]
    pcm_sha256: str
    split: str


@dataclass(frozen=True, slots=True)
class SelectedAudioSource:
    """Private source location and expected decoded-PCM identity."""

    path: Path
    expected_pcm_sha256: str


@dataclass(frozen=True, slots=True)
class SelectedAudioInventory:
    """Private selections plus aggregate-only reproducibility evidence."""

    selected_sources: tuple[SelectedAudioSource, ...]
    official_filename_count: int
    eligible_regular_file_count: int
    rejected_inventory_entry_count: int
    manifest_metadata_row_count: int
    manifest_project_split_counts: tuple[tuple[str, int], ...]
    manifest_unique_inventory_join_count: int
    manifest_duplicate_inventory_join_count: int
    manifest_missing_inventory_join_count: int
    official_partition_mismatch_count: int
    official_inventory_project_split_counts: tuple[tuple[str, int], ...]
    project_dev_eligible_count: int
    official_inventory_digest_sha256: str
    project_dev_inventory_digest_sha256: str
    selection_digest_sha256: str

    @property
    def selected_paths(self) -> tuple[Path, ...]:
        """Expose private paths to the parent orchestrator only."""

        return tuple(source.path for source in self.selected_sources)


@dataclass(frozen=True, slots=True)
class AggregateAgreement:
    """Pooled technical statistics that retain no per-item measurements."""

    comparison_completed_count: int
    requested_common_frame_count: int
    evaluated_common_frame_count: int
    trimmed_common_frame_count: int
    jointly_voiced_frame_count: int
    parselmouth_only_voiced_frame_count: int
    torchcrepe_only_voiced_frame_count: int
    jointly_unvoiced_frame_count: int
    pooled_voicing_agreement_rate: float
    pooled_jointly_voiced_rate: float
    pooled_mean_torchcrepe_minus_parselmouth_hz: float
    pooled_mean_absolute_hz_error: float
    pooled_root_mean_squared_hz_error: float
    maximum_absolute_hz_error: float
    pooled_mean_torchcrepe_minus_parselmouth_cents: float
    pooled_mean_absolute_cents_error: float
    pooled_root_mean_squared_cents_error: float
    maximum_absolute_cents_error: float
    octave_error_frame_count: int
    pooled_octave_error_rate: float

    def to_dict(self) -> dict[str, int | float]:
        """Return the exact aggregate-only JSON representation."""

        return {
            "comparison_completed_count": self.comparison_completed_count,
            "requested_common_frame_count": self.requested_common_frame_count,
            "evaluated_common_frame_count": self.evaluated_common_frame_count,
            "trimmed_common_frame_count": self.trimmed_common_frame_count,
            "jointly_voiced_frame_count": self.jointly_voiced_frame_count,
            "parselmouth_only_voiced_frame_count": (
                self.parselmouth_only_voiced_frame_count
            ),
            "torchcrepe_only_voiced_frame_count": (
                self.torchcrepe_only_voiced_frame_count
            ),
            "jointly_unvoiced_frame_count": self.jointly_unvoiced_frame_count,
            "pooled_voicing_agreement_rate": self.pooled_voicing_agreement_rate,
            "pooled_jointly_voiced_rate": self.pooled_jointly_voiced_rate,
            "pooled_mean_torchcrepe_minus_parselmouth_hz": (
                self.pooled_mean_torchcrepe_minus_parselmouth_hz
            ),
            "pooled_mean_absolute_hz_error": self.pooled_mean_absolute_hz_error,
            "pooled_root_mean_squared_hz_error": (
                self.pooled_root_mean_squared_hz_error
            ),
            "maximum_absolute_hz_error": self.maximum_absolute_hz_error,
            "pooled_mean_torchcrepe_minus_parselmouth_cents": (
                self.pooled_mean_torchcrepe_minus_parselmouth_cents
            ),
            "pooled_mean_absolute_cents_error": (self.pooled_mean_absolute_cents_error),
            "pooled_root_mean_squared_cents_error": (
                self.pooled_root_mean_squared_cents_error
            ),
            "maximum_absolute_cents_error": self.maximum_absolute_cents_error,
            "octave_error_frame_count": self.octave_error_frame_count,
            "pooled_octave_error_rate": self.pooled_octave_error_rate,
        }


def _lowercase_sha256(value: object, *, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ActualF0AgreementSmokeError(
            f"{field_name} must be a lowercase SHA-256 digest"
        )
    return cast(str, value)


def _positive_integer(
    value: object,
    *,
    field_name: str,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise ActualF0AgreementSmokeError(f"{field_name} must be a positive integer")
    normalized = cast(int, value)
    if normalized <= 0 or (maximum is not None and normalized > maximum):
        raise ActualF0AgreementSmokeError(
            f"{field_name} is outside the supported resource bound"
        )
    return normalized


def _real_dataset_root(value: object) -> Path:
    if not isinstance(value, Path):
        raise ActualF0AgreementSmokeError("dataset_root must be a Path")
    root = cast(Path, value)
    if not root.is_absolute():
        raise ActualF0AgreementSmokeError("dataset_root must be absolute")
    try:
        information = root.lstat()
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ActualF0AgreementSmokeError("dataset_root is unavailable") from None
    if root.is_symlink() or not stat.S_ISDIR(information.st_mode) or resolved != root:
        raise ActualF0AgreementSmokeError("dataset_root must be a real directory")
    return root


def _is_official_validation_pronunciation_wav(
    relative_parts: tuple[str, ...],
    filename: str,
) -> bool:
    folded_parts = tuple(part.casefold() for part in relative_parts)
    return (
        filename.casefold().endswith(".wav")
        and any(part == "validation" for part in folded_parts)
        and any("원천데이터" in part for part in relative_parts)
        and any(
            part.startswith("extracted") and "pronunciation" in part
            for part in folded_parts
        )
    )


class _Digest(Protocol):
    def update(self, value: bytes) -> None: ...


def _length_prefixed_update(digest: _Digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, byteorder="big", signed=False))
    digest.update(value)


def _filename_inventory(
    dataset_root: Path,
) -> tuple[tuple[bytes, str, Path], ...]:
    entries: list[tuple[bytes, str, Path]] = []
    for directory, child_directories, filenames in os.walk(
        dataset_root,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        child_directories[:] = sorted(
            child
            for child in child_directories
            if not (directory_path / child).is_symlink()
        )
        try:
            relative_directory = directory_path.relative_to(dataset_root)
        except ValueError:
            raise ActualF0AgreementSmokeError(
                "inventory traversal escaped dataset_root"
            ) from None
        relative_parts = relative_directory.parts
        for filename in sorted(filenames):
            if not _is_official_validation_pronunciation_wav(
                relative_parts,
                filename,
            ):
                continue
            path = directory_path / filename
            relative_path = relative_directory / filename
            relative_posix = relative_path.as_posix()
            relative_bytes = os.fsencode(relative_posix)
            entries.append((relative_bytes, relative_posix, path))
    return tuple(entries)


def _canonical_manifest_relative_path(value: object) -> str:
    if type(value) is not str or not cast(str, value):
        raise ActualF0AgreementSmokeError(
            "modeling manifest audio_path must be a non-empty string"
        )
    normalized = cast(str, value)
    candidate = PurePosixPath(normalized)
    if (
        candidate.is_absolute()
        or normalized != candidate.as_posix()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or "\x00" in normalized
    ):
        raise ActualF0AgreementSmokeError(
            "modeling manifest audio_path must be canonical and relative"
        )
    return normalized


def _validated_manifest_row(
    value: object,
) -> ModelingManifestMetadataRow:
    if type(value) is not ModelingManifestMetadataRow:
        raise ActualF0AgreementSmokeError(
            "manifest metadata rows must use the exact row contract"
        )
    row = cast(ModelingManifestMetadataRow, value)
    audio_path = _canonical_manifest_relative_path(row.audio_path)
    if (
        type(row.official_partitions) is not tuple
        or not row.official_partitions
        or any(
            type(partition) is not str or not partition
            for partition in row.official_partitions
        )
    ):
        raise ActualF0AgreementSmokeError(
            "modeling manifest official_partitions is invalid"
        )
    if type(row.split) is not str or row.split not in KNOWN_PROJECT_SPLITS:
        raise ActualF0AgreementSmokeError("modeling manifest project split is invalid")
    return ModelingManifestMetadataRow(
        audio_path=audio_path,
        official_partitions=row.official_partitions,
        pcm_sha256=_lowercase_sha256(
            row.pcm_sha256,
            field_name="modeling_manifest.pcm_sha256",
        ),
        split=row.split,
    )


def select_project_dev_validation_audio(
    dataset_root: Path,
    *,
    manifest_metadata_rows: Iterable[ModelingManifestMetadataRow],
    manifest_sha256: str,
    seed: int,
    sample_size: int,
) -> SelectedAudioInventory:
    """Fix an official-Validation/project-``dev`` sample before audio read.

    Every modeling-manifest row is scanned through the restricted
    ``audio_path``, ``official_partitions``, ``pcm_sha256``, and ``split``
    metadata contract.  The complete official Validation pronunciation
    filename inventory must join one-to-one.  Only rows explicitly assigned to
    project ``dev`` are rank eligible.  PCM identity is retained privately for
    post-selection integrity verification and never affects the rank.
    """

    root = _real_dataset_root(dataset_root)
    normalized_manifest_sha256 = _lowercase_sha256(
        manifest_sha256,
        field_name="manifest_sha256",
    )
    normalized_seed = _positive_integer(
        seed,
        field_name="seed",
        maximum=2**63 - 1,
    )
    normalized_sample_size = _positive_integer(
        sample_size,
        field_name="sample_size",
        maximum=MAXIMUM_SAMPLE_SIZE,
    )
    filename_entries = _filename_inventory(root)
    official_filename_count = len(filename_entries)
    regular_entries: list[tuple[bytes, str, Path]] = []
    rejected_count = 0
    for relative_bytes, relative_posix, path in filename_entries:
        try:
            information = path.lstat()
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            rejected_count += 1
            continue
        if (
            path.is_symlink()
            or not stat.S_ISREG(information.st_mode)
            or resolved != path
        ):
            rejected_count += 1
            continue
        regular_entries.append((relative_bytes, relative_posix, path))

    if len(regular_entries) < normalized_sample_size:
        raise ActualF0AgreementSmokeError(
            "official Validation inventory is smaller than sample_size"
        )

    regular_by_relative = {
        relative_posix: (relative_bytes, path)
        for relative_bytes, relative_posix, path in regular_entries
    }
    if len(regular_by_relative) != len(regular_entries):
        raise ActualF0AgreementSmokeError(
            "official Validation filename inventory is not unique"
        )

    manifest_row_count = 0
    manifest_split_counts: Counter[str] = Counter()
    joined_rows: dict[str, ModelingManifestMetadataRow] = {}
    duplicate_join_count = 0
    for raw_row in manifest_metadata_rows:
        row = _validated_manifest_row(raw_row)
        manifest_row_count += 1
        manifest_split_counts[row.split] += 1
        if row.audio_path not in regular_by_relative:
            continue
        if row.audio_path in joined_rows:
            duplicate_join_count += 1
            continue
        joined_rows[row.audio_path] = row

    missing_join_count = len(regular_entries) - len(joined_rows)
    official_partition_mismatch_count = sum(
        row.official_partitions != (FIXED_OFFICIAL_PARTITION,)
        for row in joined_rows.values()
    )
    if (
        manifest_row_count <= 0
        or duplicate_join_count
        or missing_join_count
        or official_partition_mismatch_count
    ):
        raise ActualF0AgreementSmokeError(
            "official Validation inventory does not have a one-to-one "
            "modeling-manifest role join"
        )

    official_inventory_split_counts: Counter[str] = Counter(
        row.split for row in joined_rows.values()
    )
    dev_entries = tuple(
        (
            regular_by_relative[relative_posix][0],
            regular_by_relative[relative_posix][1],
            row.pcm_sha256,
        )
        for relative_posix, row in joined_rows.items()
        if row.split == FIXED_PROJECT_SPLIT
    )
    if len(dev_entries) < normalized_sample_size:
        raise ActualF0AgreementSmokeError(
            "project dev inventory is smaller than sample_size"
        )

    official_inventory_digest = hashlib.sha256()
    official_inventory_digest.update(SELECTION_DOMAIN)
    official_inventory_digest.update(b"official-inventory\0")
    for relative_bytes, _, _ in sorted(regular_entries, key=lambda item: item[0]):
        _length_prefixed_update(official_inventory_digest, relative_bytes)

    project_dev_inventory_digest = hashlib.sha256()
    project_dev_inventory_digest.update(SELECTION_DOMAIN)
    project_dev_inventory_digest.update(b"project-dev-inventory\0")
    project_dev_inventory_digest.update(bytes.fromhex(normalized_manifest_sha256))
    for relative_bytes, _, _ in sorted(dev_entries, key=lambda item: item[0]):
        _length_prefixed_update(project_dev_inventory_digest, relative_bytes)

    seed_bytes = normalized_seed.to_bytes(8, byteorder="big", signed=False)
    ranked_entries = sorted(
        (
            (
                hashlib.sha256(
                    SELECTION_DOMAIN
                    + b"rank\0"
                    + seed_bytes
                    + len(relative_bytes).to_bytes(8, "big")
                    + relative_bytes
                ).digest(),
                path,
                pcm_sha256,
            )
            for relative_bytes, path, pcm_sha256 in dev_entries
        ),
        key=lambda item: item[0],
    )
    selected_entries = tuple(ranked_entries[:normalized_sample_size])

    selection_digest = hashlib.sha256()
    selection_digest.update(SELECTION_DOMAIN)
    selection_digest.update(b"selection\0")
    selection_digest.update(seed_bytes)
    selection_digest.update(normalized_sample_size.to_bytes(2, "big"))
    selection_digest.update(bytes.fromhex(normalized_manifest_sha256))
    selection_digest.update(project_dev_inventory_digest.digest())
    for rank_digest, _, _ in selected_entries:
        selection_digest.update(rank_digest)

    return SelectedAudioInventory(
        selected_sources=tuple(
            SelectedAudioSource(
                path=path,
                expected_pcm_sha256=pcm_sha256,
            )
            for _, path, pcm_sha256 in selected_entries
        ),
        official_filename_count=official_filename_count,
        eligible_regular_file_count=len(regular_entries),
        rejected_inventory_entry_count=rejected_count,
        manifest_metadata_row_count=manifest_row_count,
        manifest_project_split_counts=tuple(sorted(manifest_split_counts.items())),
        manifest_unique_inventory_join_count=len(joined_rows),
        manifest_duplicate_inventory_join_count=duplicate_join_count,
        manifest_missing_inventory_join_count=missing_join_count,
        official_partition_mismatch_count=official_partition_mismatch_count,
        official_inventory_project_split_counts=tuple(
            sorted(official_inventory_split_counts.items())
        ),
        project_dev_eligible_count=len(dev_entries),
        official_inventory_digest_sha256=official_inventory_digest.hexdigest(),
        project_dev_inventory_digest_sha256=(project_dev_inventory_digest.hexdigest()),
        selection_digest_sha256=selection_digest.hexdigest(),
    )


def _finite_sum(values: tuple[float, ...], *, field_name: str) -> float:
    try:
        result = math.fsum(values)
    except (OverflowError, ValueError):
        raise ActualF0AgreementSmokeError(
            f"{field_name} aggregate is non-finite"
        ) from None
    if not math.isfinite(result):
        raise ActualF0AgreementSmokeError(f"{field_name} aggregate is non-finite")
    return 0.0 if result == 0.0 else result


def aggregate_pitch_agreements(
    results: tuple[PitchContourAgreementResult, ...],
) -> AggregateAgreement | None:
    """Pool frame-weighted technical statistics without retaining item rows."""

    if type(results) is not tuple:
        raise ActualF0AgreementSmokeError("results must be an exact tuple")
    if not results:
        return None
    if any(type(result) is not PitchContourAgreementResult for result in results):
        raise ActualF0AgreementSmokeError(
            "results must contain exact PitchContourAgreementResult values"
        )

    requested_count = sum(result.requested_common_frame_count for result in results)
    evaluated_count = sum(result.evaluated_common_frame_count for result in results)
    trimmed_count = sum(result.trimmed_common_frame_count for result in results)
    jointly_voiced_count = sum(result.jointly_voiced_frame_count for result in results)
    parselmouth_only_count = sum(
        result.reference_only_voiced_frame_count for result in results
    )
    torchcrepe_only_count = sum(
        result.candidate_only_voiced_frame_count for result in results
    )
    jointly_unvoiced_count = sum(
        result.jointly_unvoiced_frame_count for result in results
    )
    if evaluated_count <= 0 or jointly_voiced_count <= 0:
        raise ActualF0AgreementSmokeError("agreement aggregates have no valid frames")

    def weighted_mean(field_name: str) -> float:
        weighted = tuple(
            cast(float, getattr(result, field_name)) * result.jointly_voiced_frame_count
            for result in results
        )
        return _finite_sum(weighted, field_name=field_name) / jointly_voiced_count

    def pooled_root_mean_square(field_name: str) -> float:
        weighted_squares = tuple(
            cast(float, getattr(result, field_name)) ** 2
            * result.jointly_voiced_frame_count
            for result in results
        )
        mean_square = (
            _finite_sum(
                weighted_squares,
                field_name=field_name,
            )
            / jointly_voiced_count
        )
        result = math.sqrt(mean_square)
        if not math.isfinite(result):
            raise ActualF0AgreementSmokeError(f"{field_name} aggregate is non-finite")
        return result

    octave_count = sum(result.octave_error_frame_count for result in results)
    return AggregateAgreement(
        comparison_completed_count=len(results),
        requested_common_frame_count=requested_count,
        evaluated_common_frame_count=evaluated_count,
        trimmed_common_frame_count=trimmed_count,
        jointly_voiced_frame_count=jointly_voiced_count,
        parselmouth_only_voiced_frame_count=parselmouth_only_count,
        torchcrepe_only_voiced_frame_count=torchcrepe_only_count,
        jointly_unvoiced_frame_count=jointly_unvoiced_count,
        pooled_voicing_agreement_rate=(jointly_voiced_count + jointly_unvoiced_count)
        / evaluated_count,
        pooled_jointly_voiced_rate=jointly_voiced_count / evaluated_count,
        pooled_mean_torchcrepe_minus_parselmouth_hz=weighted_mean(
            "mean_candidate_minus_reference_hz"
        ),
        pooled_mean_absolute_hz_error=weighted_mean("mean_absolute_hz_error"),
        pooled_root_mean_squared_hz_error=pooled_root_mean_square(
            "root_mean_squared_hz_error"
        ),
        maximum_absolute_hz_error=max(
            result.maximum_absolute_hz_error for result in results
        ),
        pooled_mean_torchcrepe_minus_parselmouth_cents=weighted_mean(
            "mean_candidate_minus_reference_cents"
        ),
        pooled_mean_absolute_cents_error=weighted_mean("mean_absolute_cents_error"),
        pooled_root_mean_squared_cents_error=pooled_root_mean_square(
            "root_mean_squared_cents_error"
        ),
        maximum_absolute_cents_error=max(
            result.maximum_absolute_cents_error for result in results
        ),
        octave_error_frame_count=octave_count,
        pooled_octave_error_rate=octave_count / jointly_voiced_count,
    )


def canonical_payload_sha256(payload: dict[str, object]) -> str:
    """Hash a report payload before its self-describing digest is attached."""

    if type(payload) is not dict:
        raise ActualF0AgreementSmokeError("payload must be an exact dictionary")
    if "payload_sha256" in payload:
        raise ActualF0AgreementSmokeError(
            "payload must not already contain payload_sha256"
        )
    try:
        serialized = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ActualF0AgreementSmokeError("payload is not canonical JSON") from None
    return hashlib.sha256(serialized).hexdigest()


def attach_payload_sha256(payload: dict[str, object]) -> dict[str, object]:
    """Return a shallow report copy with its canonical payload digest."""

    result = dict(payload)
    result["payload_sha256"] = canonical_payload_sha256(payload)
    return result


def verify_payload_sha256(report: dict[str, object]) -> bool:
    """Verify a report's embedded payload digest without mutating it."""

    if type(report) is not dict:
        return False
    expected = report.get("payload_sha256")
    try:
        normalized_expected = _lowercase_sha256(
            expected,
            field_name="payload_sha256",
        )
    except ActualF0AgreementSmokeError:
        return False
    payload = dict(report)
    del payload["payload_sha256"]
    return canonical_payload_sha256(payload) == normalized_expected


__all__ = [
    "ActualF0AgreementSmokeError",
    "AggregateAgreement",
    "FIXED_DATASET_ID",
    "FIXED_OFFICIAL_PARTITION",
    "FIXED_PROJECT_SPLIT",
    "FIXED_SOURCE_KIND",
    "KNOWN_PROJECT_SPLITS",
    "MAXIMUM_SAMPLE_SIZE",
    "MODELING_MANIFEST_COLUMNS",
    "ModelingManifestMetadataRow",
    "REPORT_SCHEMA_VERSION",
    "SELECTION_POLICY_ID",
    "SelectedAudioInventory",
    "SelectedAudioSource",
    "aggregate_pitch_agreements",
    "attach_payload_sha256",
    "canonical_payload_sha256",
    "select_project_dev_validation_audio",
    "verify_payload_sha256",
]
