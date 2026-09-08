"""Read-only, aggregate-only QC for a pinned CoreaSpeech Parquet shard.

The public dataset policy forbids redistribution and secondary modification.
This module therefore never materializes audio, never emits transcript or
speaker values, and only returns aggregate header statistics for a shard whose
byte size and SHA-256 are pinned in a reviewed manifest.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import io
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import soundfile as sf  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]


REQUIRED_COLUMNS = (
    "audio",
    "duration",
    "text",
    "tr",
    "N2gkPlus",
    "speaker",
)


class CoreaSpeechAuditError(ValueError):
    """Raised when a pinned snapshot or its privacy boundary is invalid."""


@dataclass(frozen=True, slots=True)
class CoreaSpeechShardSpec:
    revision: str
    relative_path: str
    size_bytes: int
    sha256: str
    expected_rows: int
    expected_speakers: int
    expected_duration_hours: float
    expected_sample_rate_hz: int
    expected_channels: int
    expected_formats: tuple[str, ...]


def sha256_file(path: Path) -> str:
    """Hash a local shard without loading the whole file into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CoreaSpeechAuditError(f"{name} must be a mapping")
    return value


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CoreaSpeechAuditError(f"{name} must be a positive integer")
    return value


def _positive_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CoreaSpeechAuditError(f"{name} must be numeric")
    parsed = float(value)
    if not isfinite(parsed) or parsed <= 0:
        raise CoreaSpeechAuditError(f"{name} must be finite and positive")
    return parsed


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CoreaSpeechAuditError(f"{name} must be a lowercase SHA-256")
    return value


def _public_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise CoreaSpeechAuditError("remote shard path must be non-empty")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.name != path.as_posix().split("/")[-1]
    ):
        raise CoreaSpeechAuditError("remote shard path is unsafe")
    if path.suffix != ".parquet":
        raise CoreaSpeechAuditError("remote shard must be Parquet")
    return path.name


def load_coreaspeech_shard_spec(path: str | Path) -> CoreaSpeechShardSpec:
    """Load the exact QC-only shard contract from the tracked manifest."""

    source = Path(path).resolve(strict=True)
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    root = _mapping(document, "manifest")
    if root.get("schema_version") != "voice_coach.external_dataset_manifest.v1":
        raise CoreaSpeechAuditError("manifest schema version differs")
    dataset = _mapping(root.get("dataset"), "dataset")
    if dataset.get("id") != "coreaspeech":
        raise CoreaSpeechAuditError("manifest dataset id differs")
    access = _mapping(dataset.get("access"), "dataset.access")
    if (
        access.get("private") is not False
        or access.get("gated") is not False
        or access.get("authentication_required") is not False
    ):
        raise CoreaSpeechAuditError("public access contract differs")
    policy = _mapping(root.get("research_policy"), "research_policy")
    authorization = _mapping(
        root.get("training_authorization"),
        "training_authorization",
    )
    if (
        policy.get("research_only") is not True
        or policy.get("commercial_use_allowed") is not False
        or policy.get("redistribution_allowed") is not False
        or policy.get("secondary_modification_allowed") is not False
        or authorization.get("current_status") != "qc_only"
    ):
        raise CoreaSpeechAuditError("QC-only research policy differs")

    snapshot = _mapping(root.get("smoke_snapshot"), "smoke_snapshot")
    shard = _mapping(snapshot.get("shard"), "smoke_snapshot.shard")
    expected_audio = _mapping(
        snapshot.get("expected_audio_header"),
        "smoke_snapshot.expected_audio_header",
    )
    formats = expected_audio.get("formats")
    if (
        not isinstance(formats, list)
        or not formats
        or not all(isinstance(value, str) and value for value in formats)
    ):
        raise CoreaSpeechAuditError("expected audio formats are invalid")
    revision = dataset.get("revision")
    if not isinstance(revision, str) or len(revision) != 40:
        raise CoreaSpeechAuditError("dataset revision must be a full commit id")
    return CoreaSpeechShardSpec(
        revision=revision,
        relative_path=_public_relative_path(shard.get("remote_path")),
        size_bytes=_positive_integer(shard.get("bytes"), "shard bytes"),
        sha256=_sha256(shard.get("sha256"), "shard sha256"),
        expected_rows=_positive_integer(snapshot.get("rows"), "snapshot rows"),
        expected_speakers=_positive_integer(
            snapshot.get("speakers"),
            "snapshot speakers",
        ),
        expected_duration_hours=_positive_float(
            snapshot.get("duration_hours"),
            "snapshot duration hours",
        ),
        expected_sample_rate_hz=_positive_integer(
            expected_audio.get("sample_rate_hz"),
            "expected sample rate",
        ),
        expected_channels=_positive_integer(
            expected_audio.get("channels"),
            "expected channels",
        ),
        expected_formats=tuple(str(value).upper() for value in formats),
    )


def resolve_pinned_shard(
    dataset_root: str | Path,
    spec: CoreaSpeechShardSpec,
) -> Path:
    """Resolve the shard below a revision-named, non-symlink corpus root."""

    root = Path(dataset_root).resolve(strict=True)
    if not root.is_dir() or root.name != spec.revision:
        raise CoreaSpeechAuditError(
            "dataset root must be the pinned revision directory"
        )
    candidate = root / spec.relative_path
    if candidate.is_symlink():
        raise CoreaSpeechAuditError("pinned shard may not be a symlink")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CoreaSpeechAuditError("pinned shard escapes dataset root") from exc
    if not resolved.is_file():
        raise CoreaSpeechAuditError("pinned shard is not a file")
    return resolved


def _validate_schema(parquet: pq.ParquetFile) -> None:
    schema = parquet.schema_arrow
    missing = [column for column in REQUIRED_COLUMNS if column not in schema.names]
    if missing:
        raise CoreaSpeechAuditError(
            f"Parquet required columns are missing: {','.join(missing)}"
        )
    audio = schema.field("audio").type
    if not pa.types.is_struct(audio):
        raise CoreaSpeechAuditError("audio column must be a struct")
    audio_fields = {field.name: field.type for field in audio}
    if "bytes" not in audio_fields or not pa.types.is_binary(audio_fields["bytes"]):
        raise CoreaSpeechAuditError("audio.bytes must be binary")
    if "path" not in audio_fields or not pa.types.is_string(audio_fields["path"]):
        raise CoreaSpeechAuditError("audio.path must be string")


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def audit_coreaspeech_shard(
    *,
    manifest_path: str | Path,
    dataset_root: str | Path,
    row_limit: int | None = None,
    duration_tolerance_seconds: float = 0.05,
) -> dict[str, object]:
    """Inspect headers without decoding or materializing audio.

    A limited run is explicitly a sampled smoke.  Only an unlimited run that
    covers every pinned row can return ``full_header_qc_passed``.
    """

    if row_limit is not None and row_limit <= 0:
        raise CoreaSpeechAuditError("row_limit must be positive")
    if (
        not isfinite(duration_tolerance_seconds)
        or duration_tolerance_seconds < 0
        or duration_tolerance_seconds > 1
    ):
        raise CoreaSpeechAuditError(
            "duration tolerance must be finite and between 0 and 1 second"
        )

    spec = load_coreaspeech_shard_spec(manifest_path)
    shard = resolve_pinned_shard(dataset_root, spec)
    actual_size = shard.stat().st_size
    if actual_size != spec.size_bytes:
        raise CoreaSpeechAuditError("pinned shard byte size differs")
    actual_sha256 = sha256_file(shard)
    if actual_sha256 != spec.sha256:
        raise CoreaSpeechAuditError("pinned shard SHA-256 differs")

    parquet = pq.ParquetFile(shard)
    _validate_schema(parquet)
    metadata_rows = int(parquet.metadata.num_rows)
    if metadata_rows != spec.expected_rows:
        raise CoreaSpeechAuditError("pinned shard row count differs")

    scanned = 0
    duration_sum = 0.0
    duration_header_sum = 0.0
    max_duration_difference = 0.0
    duration_mismatches = 0
    empty_counts: Counter[str] = Counter()
    sample_rates: Counter[int] = Counter()
    channels: Counter[int] = Counter()
    formats: Counter[str] = Counter()
    subtypes: Counter[str] = Counter()
    speaker_hashes: set[str] = set()
    header_failures = 0
    missing_audio_bytes = 0
    path_value_present = 0

    for batch in parquet.iter_batches(batch_size=8, columns=list(REQUIRED_COLUMNS)):
        for row in batch.to_pylist():
            if row_limit is not None and scanned >= row_limit:
                break
            scanned += 1
            for column in ("text", "tr", "N2gkPlus", "speaker"):
                if not _nonempty_text(row.get(column)):
                    empty_counts[column] += 1
            speaker = row.get("speaker")
            if _nonempty_text(speaker):
                speaker_hashes.add(
                    hashlib.sha256(str(speaker).encode("utf-8")).hexdigest()
                )
            duration = row.get("duration")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not isfinite(float(duration))
                or float(duration) <= 0
            ):
                empty_counts["duration"] += 1
                duration_value = 0.0
            else:
                duration_value = float(duration)
                duration_sum += duration_value

            audio = row.get("audio")
            if not isinstance(audio, Mapping):
                missing_audio_bytes += 1
                continue
            payload = audio.get("bytes")
            if audio.get("path") not in (None, ""):
                path_value_present += 1
            if not isinstance(payload, bytes) or not payload:
                missing_audio_bytes += 1
                continue
            try:
                info = sf.info(io.BytesIO(payload))
            except (OSError, RuntimeError, sf.LibsndfileError):
                header_failures += 1
                continue
            if info.samplerate <= 0 or info.channels <= 0 or info.frames <= 0:
                header_failures += 1
                continue
            sample_rates[int(info.samplerate)] += 1
            channels[int(info.channels)] += 1
            formats[str(info.format).upper()] += 1
            subtypes[str(info.subtype).upper()] += 1
            header_duration = float(info.frames / info.samplerate)
            duration_header_sum += header_duration
            difference = abs(duration_value - header_duration)
            max_duration_difference = max(max_duration_difference, difference)
            if difference > duration_tolerance_seconds:
                duration_mismatches += 1
        if row_limit is not None and scanned >= row_limit:
            break

    full_scan = row_limit is None or row_limit >= metadata_rows
    failed_checks: list[str] = []
    if not full_scan:
        failed_checks.append("not_full_shard")
    if scanned != (metadata_rows if full_scan else min(row_limit or 0, metadata_rows)):
        failed_checks.append("scanned_row_count")
    if empty_counts:
        failed_checks.append("empty_required_value")
    if missing_audio_bytes:
        failed_checks.append("missing_audio_bytes")
    if header_failures:
        failed_checks.append("audio_header_failure")
    if duration_mismatches:
        failed_checks.append("duration_header_mismatch")
    if full_scan and len(speaker_hashes) != spec.expected_speakers:
        failed_checks.append("speaker_count")
    if set(sample_rates) != {spec.expected_sample_rate_hz}:
        failed_checks.append("sample_rate")
    if set(channels) != {spec.expected_channels}:
        failed_checks.append("channels")
    if not set(formats).issubset(set(spec.expected_formats)):
        failed_checks.append("audio_format")
    expected_duration_seconds = spec.expected_duration_hours * 3600
    if full_scan and abs(duration_sum - expected_duration_seconds) > 1:
        failed_checks.append("duration_total")

    return {
        "schema_version": "voice_coach.coreaspeech_header_audit.v1",
        "status": (
            "full_header_qc_passed"
            if not failed_checks
            else (
                "sampled_header_qc_completed"
                if failed_checks == ["not_full_shard"]
                else "header_qc_failed"
            )
        ),
        "dataset": {
            "id": "coreaspeech",
            "revision": spec.revision,
            "shard_public_name": spec.relative_path,
            "shard_bytes": actual_size,
            "shard_sha256": actual_sha256,
        },
        "scope": {
            "full_shard": full_scan,
            "metadata_rows": metadata_rows,
            "rows_scanned": scanned,
            "audio_materialized": False,
            "audio_decoded_to_samples": False,
            "training_authorized": False,
            "research_result": False,
        },
        "aggregate": {
            "speaker_count": len(speaker_hashes),
            "duration_metadata_hours": duration_sum / 3600,
            "duration_header_hours": duration_header_sum / 3600,
            "maximum_duration_difference_seconds": max_duration_difference,
            "duration_mismatch_count": duration_mismatches,
            "empty_required_value_counts": dict(sorted(empty_counts.items())),
            "missing_audio_bytes": missing_audio_bytes,
            "embedded_audio_path_value_ignored_count": path_value_present,
            "audio_header_failures": header_failures,
            "sample_rate_hz_counts": {
                str(key): value for key, value in sorted(sample_rates.items())
            },
            "channel_counts": {
                str(key): value for key, value in sorted(channels.items())
            },
            "format_counts": dict(sorted(formats.items())),
            "subtype_counts": dict(sorted(subtypes.items())),
        },
        "thresholds": {
            "duration_tolerance_seconds": duration_tolerance_seconds,
            "expected_sample_rate_hz": spec.expected_sample_rate_hz,
            "expected_channels": spec.expected_channels,
            "expected_formats": list(spec.expected_formats),
        },
        "privacy": {
            "contains_transcript_text": False,
            "contains_speaker_value": False,
            "contains_audio_bytes": False,
            "contains_local_source_path": False,
            "aggregate_only": True,
        },
        "failed_checks": failed_checks,
    }


__all__ = [
    "CoreaSpeechAuditError",
    "CoreaSpeechShardSpec",
    "audit_coreaspeech_shard",
    "load_coreaspeech_shard_spec",
    "resolve_pinned_shard",
    "sha256_file",
]
