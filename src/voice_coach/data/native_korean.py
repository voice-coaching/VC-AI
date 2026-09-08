"""Privacy-reviewed restricted manifest builder for native Korean corpora.

The operational manifest contains source locators, so it remains restricted
even though it never stores transcript text, direct speaker identifiers, or
embedded audio bytes.  All on-disk audio is opened read-only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import io
from itertools import islice
import json
import os
from pathlib import Path
import tempfile
from typing import Any


@dataclass(frozen=True, slots=True)
class NativeCorpusPolicy:
    source_id: str
    license_id: str
    intended_role: str
    noncommercial_only: bool
    raw_redistribution_allowed: bool


CORPUS_POLICIES: Mapping[str, NativeCorpusPolicy] = {
    "fleurs-ko-kr": NativeCorpusPolicy(
        source_id="fleurs-ko-kr",
        license_id="CC-BY-4.0",
        intended_role="external_native_korean_asr_alignment_evaluation",
        noncommercial_only=False,
        raw_redistribution_allowed=True,
    ),
    "kss": NativeCorpusPolicy(
        source_id="kss",
        license_id="CC-BY-NC-SA-4.0",
        intended_role="research_only_native_korean_tts_baseline",
        noncommercial_only=True,
        raw_redistribution_allowed=False,
    ),
    "seoul-corpus": NativeCorpusPolicy(
        source_id="seoul-corpus",
        license_id="CC-BY-NC-2.0",
        intended_role=(
            "research_only_korean_phone_alignment_and_prosody_reference"
        ),
        noncommercial_only=True,
        raw_redistribution_allowed=False,
    ),
    "zeroth-korean": NativeCorpusPolicy(
        source_id="zeroth-korean",
        license_id="CC-BY-4.0",
        intended_role="native_korean_asr_alignment_baseline",
        noncommercial_only=False,
        raw_redistribution_allowed=True,
    ),
}

EXPECTED_SOURCE_COUNTS: Mapping[str, int] = {
    "fleurs-ko-kr": 2_915,
    "kss": 12_854,
    "seoul-corpus": 240,
    "zeroth-korean": 22_720,
}


@dataclass(frozen=True, slots=True)
class NativeKoreanRecord:
    """One restricted operational audio locator in the unified manifest."""

    stable_id: str
    source_id: str
    audio_storage: str
    relative_path: str | None
    embedded_reference: str | None
    transcript_available: bool
    transcript_reference: str | None
    sample_rate_hz: int
    channels: int
    num_frames: int
    duration_seconds: float
    audio_format: str
    split: str
    license_id: str
    noncommercial_only: bool
    raw_redistribution_allowed: bool
    intended_role: str
    access_scope: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NativeCorpusRoots:
    fleurs: Path
    kss: Path
    seoul: Path
    zeroth: Path

    def all(self) -> tuple[Path, ...]:
        return (self.fleurs, self.kss, self.seoul, self.zeroth)


DEFAULT_ROOTS = NativeCorpusRoots(
    fleurs=Path("data/raw/open/fleurs-ko-kr"),
    kss=Path("data/raw/research-noncommercial/kss"),
    seoul=Path("data/raw/research-noncommercial/seoul-corpus"),
    zeroth=Path("data/raw/open/zeroth-korean"),
)


def stable_native_id(source_id: str, canonical_locator: str) -> str:
    """Create a stable, non-identifying ID from the source and locator."""

    digest = hashlib.sha256(
        f"{source_id}\0{canonical_locator}".encode("utf-8")
    ).hexdigest()
    return f"{source_id}:{digest}"


def _relative_file(path: Path, root: Path) -> str:
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"path escapes corpus root: {path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"corpus file is missing: {path}")
    return relative.as_posix()


def _audio_metadata(source: Path | bytes) -> dict[str, Any]:
    try:
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError(
            "soundfile is required to inspect native corpus audio"
        ) from exc

    target: Path | io.BytesIO
    target = io.BytesIO(source) if isinstance(source, bytes) else source
    try:
        info = sf.info(target)
    except (OSError, RuntimeError, sf.LibsndfileError) as exc:
        raise ValueError("failed to inspect native corpus audio") from exc
    if info.samplerate <= 0 or info.channels <= 0 or info.frames <= 0:
        raise ValueError("native corpus audio has invalid header metadata")
    return {
        "sample_rate_hz": int(info.samplerate),
        "channels": int(info.channels),
        "num_frames": int(info.frames),
        "duration_seconds": float(info.frames / info.samplerate),
        "audio_format": str(info.format).lower(),
    }


def _record(
    *,
    policy: NativeCorpusPolicy,
    locator: str,
    audio_storage: str,
    relative_path: str | None,
    embedded_reference: str | None,
    transcript_available: bool,
    transcript_reference: str | None,
    audio_metadata: Mapping[str, Any],
    split: str,
) -> NativeKoreanRecord:
    if (relative_path is None) == (embedded_reference is None):
        raise ValueError(
            "exactly one of relative_path and embedded_reference is required"
        )
    return NativeKoreanRecord(
        stable_id=stable_native_id(policy.source_id, locator),
        source_id=policy.source_id,
        audio_storage=audio_storage,
        relative_path=relative_path,
        embedded_reference=embedded_reference,
        transcript_available=transcript_available,
        transcript_reference=transcript_reference,
        sample_rate_hz=int(audio_metadata["sample_rate_hz"]),
        channels=int(audio_metadata["channels"]),
        num_frames=int(audio_metadata["num_frames"]),
        duration_seconds=float(audio_metadata["duration_seconds"]),
        audio_format=str(audio_metadata["audio_format"]),
        split=split,
        license_id=policy.license_id,
        noncommercial_only=policy.noncommercial_only,
        raw_redistribution_allowed=policy.raw_redistribution_allowed,
        intended_role=policy.intended_role,
        access_scope="restricted_internal",
    )


def _limited(
    records: Iterable[NativeKoreanRecord],
    limit: int | None,
) -> Iterator[NativeKoreanRecord]:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    yield from records if limit is None else islice(records, limit)


def iter_fleurs_records(
    root: str | Path,
    *,
    limit: int | None = None,
) -> Iterator[NativeKoreanRecord]:
    """Yield FLEURS TSV/audio pairs without retaining transcription text."""

    root = Path(root)
    policy = CORPUS_POLICIES["fleurs-ko-kr"]
    split_inputs: list[tuple[str, Path, Path]] = []
    for split in ("train", "dev", "test"):
        tsv_path = root / f"{split}.tsv"
        audio_directory = root / "extracted" / split / split
        if not tsv_path.is_file():
            raise FileNotFoundError(f"FLEURS metadata is missing: {tsv_path}")
        if not audio_directory.is_dir():
            alternate = root / "extracted" / split
            if not alternate.is_dir():
                raise FileNotFoundError(
                    f"FLEURS audio directory is missing: {audio_directory}"
                )
            audio_directory = alternate
        split_inputs.append((split, tsv_path, audio_directory))

    def generate() -> Iterator[NativeKoreanRecord]:
        for split, tsv_path, audio_directory in split_inputs:
            with tsv_path.open("r", encoding="utf-8", newline="") as stream:
                for line_number, line in enumerate(stream, start=1):
                    # FLEURS text contains literal quotes, so split tabs rather
                    # than applying RFC CSV quoting semantics.
                    row = line.rstrip("\r\n").split("\t")
                    if len(row) != 7:
                        raise ValueError(
                            f"{tsv_path}:{line_number}: expected 7 columns"
                        )
                    filename = row[1]
                    if Path(filename).name != filename or not filename.endswith(
                        ".wav"
                    ):
                        raise ValueError(
                            f"{tsv_path}:{line_number}: unsafe audio filename"
                        )
                    audio_path = audio_directory / filename
                    relative_path = _relative_file(audio_path, root)
                    transcript_available = bool(
                        row[2].strip() or row[3].strip()
                    )
                    transcript_reference = (
                        f"{tsv_path.name}#line={line_number}:transcription"
                        if transcript_available
                        else None
                    )
                    yield _record(
                        policy=policy,
                        locator=relative_path,
                        audio_storage="relative_file",
                        relative_path=relative_path,
                        embedded_reference=None,
                        transcript_available=transcript_available,
                        transcript_reference=transcript_reference,
                        audio_metadata=_audio_metadata(audio_path),
                        split=split,
                    )

    yield from _limited(generate(), limit)


def iter_kss_records(
    root: str | Path,
    *,
    limit: int | None = None,
) -> Iterator[NativeKoreanRecord]:
    """Yield KSS embedded-audio locators without materializing audio files."""

    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("pyarrow is required to inspect KSS Parquet") from exc

    root = Path(root)
    policy = CORPUS_POLICIES["kss"]
    shards = sorted(root.glob("train-*.parquet"))
    if not shards:
        raise FileNotFoundError(f"KSS Parquet shards are missing under {root}")

    def generate() -> Iterator[NativeKoreanRecord]:
        for shard_path in shards:
            shard_relative = _relative_file(shard_path, root)
            parquet = pq.ParquetFile(shard_path)
            row_index = 0
            for batch in parquet.iter_batches(
                batch_size=32,
                columns=["audio", "original_script"],
            ):
                for row in batch.to_pylist():
                    audio = row.get("audio")
                    if not isinstance(audio, Mapping):
                        raise ValueError(
                            f"{shard_path}: row {row_index} has no audio struct"
                        )
                    audio_bytes = audio.get("bytes")
                    if not isinstance(audio_bytes, bytes) or not audio_bytes:
                        raise ValueError(
                            f"{shard_path}: row {row_index} has no embedded audio"
                        )
                    embedded_reference = (
                        f"{shard_relative}#row={row_index}:audio.bytes"
                    )
                    transcript = row.get("original_script")
                    transcript_available = (
                        isinstance(transcript, str) and bool(transcript.strip())
                    )
                    transcript_reference = (
                        f"{shard_relative}#row={row_index}:original_script"
                        if transcript_available
                        else None
                    )
                    yield _record(
                        policy=policy,
                        locator=embedded_reference,
                        audio_storage="parquet_embedded",
                        relative_path=None,
                        embedded_reference=embedded_reference,
                        transcript_available=transcript_available,
                        transcript_reference=transcript_reference,
                        audio_metadata=_audio_metadata(audio_bytes),
                        split="train",
                    )
                    row_index += 1

    yield from _limited(generate(), limit)


def iter_seoul_records(
    root: str | Path,
    *,
    limit: int | None = None,
) -> Iterator[NativeKoreanRecord]:
    """Yield Seoul Corpus FLAC/TextGrid pairs as noncommercial references."""

    root = Path(root)
    policy = CORPUS_POLICIES["seoul-corpus"]
    audio_directory = root / "materialized" / "audio"
    label_directory = root / "materialized" / "labels"
    audio_paths = sorted(audio_directory.glob("*.flac"))
    if not audio_paths:
        raise FileNotFoundError(
            f"Seoul Corpus FLAC files are missing under {audio_directory}"
        )

    def generate() -> Iterator[NativeKoreanRecord]:
        for audio_path in audio_paths:
            relative_path = _relative_file(audio_path, root)
            label_path = label_directory / f"{audio_path.stem}.TextGrid"
            transcript_available = (
                label_path.is_file() and label_path.stat().st_size > 0
            )
            transcript_reference = (
                _relative_file(label_path, root)
                if transcript_available
                else None
            )
            yield _record(
                policy=policy,
                locator=relative_path,
                audio_storage="relative_file",
                relative_path=relative_path,
                embedded_reference=None,
                transcript_available=transcript_available,
                transcript_reference=transcript_reference,
                audio_metadata=_audio_metadata(audio_path),
                split="unspecified",
            )

    yield from _limited(generate(), limit)


def _zeroth_transcript_index(root: Path) -> dict[str, tuple[str, bool]]:
    index: dict[str, tuple[str, bool]] = {}
    for transcript_path in sorted((root / "extracted").rglob("*.trans.txt")):
        relative_path = _relative_file(transcript_path, root)
        with transcript_path.open("r", encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, start=1):
                utterance_id, separator, transcript = line.strip().partition(" ")
                if not utterance_id:
                    continue
                if utterance_id in index:
                    raise ValueError(
                        f"duplicate ZEROTH transcript ID: {utterance_id}"
                    )
                reference = f"{relative_path}#line={line_number}"
                index[utterance_id] = (
                    reference,
                    bool(separator and transcript.strip()),
                )
    return index


def iter_zeroth_records(
    root: str | Path,
    *,
    limit: int | None = None,
) -> Iterator[NativeKoreanRecord]:
    """Yield ZEROTH FLAC locators joined to transcript files by utterance ID."""

    root = Path(root)
    policy = CORPUS_POLICIES["zeroth-korean"]
    extracted = root / "extracted"
    audio_paths = sorted(extracted.rglob("*.flac"))
    if not audio_paths:
        raise FileNotFoundError(
            f"ZEROTH FLAC files are missing under {extracted}"
        )
    transcript_index = _zeroth_transcript_index(root)

    def generate() -> Iterator[NativeKoreanRecord]:
        for audio_path in audio_paths:
            relative_path = _relative_file(audio_path, root)
            transcript_reference, transcript_available = transcript_index.get(
                audio_path.stem,
                (None, False),
            )
            first_component = audio_path.relative_to(extracted).parts[0]
            if first_component.startswith("train"):
                split = "train"
            elif first_component.startswith("test"):
                split = "test"
            else:
                split = "unspecified"
            yield _record(
                policy=policy,
                locator=relative_path,
                audio_storage="relative_file",
                relative_path=relative_path,
                embedded_reference=None,
                transcript_available=transcript_available,
                transcript_reference=transcript_reference,
                audio_metadata=_audio_metadata(audio_path),
                split=split,
            )

    yield from _limited(generate(), limit)


def iter_native_korean_records(
    roots: NativeCorpusRoots = DEFAULT_ROOTS,
    *,
    limit_per_source: int | None = None,
) -> Iterator[NativeKoreanRecord]:
    """Yield all four corpora in a stable source order.

    ``limit_per_source`` is intentionally per corpus so a smoke run always
    exercises FLEURS, KSS, Seoul Corpus, and ZEROTH.
    """

    if limit_per_source is not None and limit_per_source <= 0:
        raise ValueError("limit_per_source must be positive")
    yield from iter_fleurs_records(roots.fleurs, limit=limit_per_source)
    yield from iter_kss_records(roots.kss, limit=limit_per_source)
    yield from iter_seoul_records(roots.seoul, limit=limit_per_source)
    yield from iter_zeroth_records(roots.zeroth, limit=limit_per_source)


def _manifest_schema() -> Any:
    try:
        import pyarrow as pa
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("pyarrow is required to build the manifest") from exc
    return pa.schema(
        [
            pa.field("stable_id", pa.string(), nullable=False),
            pa.field("source_id", pa.string(), nullable=False),
            pa.field("audio_storage", pa.string(), nullable=False),
            pa.field("relative_path", pa.string()),
            pa.field("embedded_reference", pa.string()),
            pa.field("transcript_available", pa.bool_(), nullable=False),
            pa.field("transcript_reference", pa.string()),
            pa.field("sample_rate_hz", pa.int32(), nullable=False),
            pa.field("channels", pa.int16(), nullable=False),
            pa.field("num_frames", pa.int64(), nullable=False),
            pa.field("duration_seconds", pa.float64(), nullable=False),
            pa.field("audio_format", pa.string(), nullable=False),
            pa.field("split", pa.string(), nullable=False),
            pa.field("license_id", pa.string(), nullable=False),
            pa.field("noncommercial_only", pa.bool_(), nullable=False),
            pa.field(
                "raw_redistribution_allowed",
                pa.bool_(),
                nullable=False,
            ),
            pa.field("intended_role", pa.string(), nullable=False),
            pa.field("access_scope", pa.string(), nullable=False),
        ],
        metadata={
            b"schema_version": b"1",
            b"privacy_reviewed": b"true",
            b"contains_direct_identifiers": b"false",
            b"contains_transcript_text": b"false",
            b"contains_audio_bytes": b"false",
            b"path_policy": b"corpus-relative-or-parquet-embedded-reference",
            b"access_scope": b"restricted_internal",
            b"shareable": b"false",
        },
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_write_parquet(path: Path, records: list[NativeKoreanRecord]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("pyarrow is required to build the manifest") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        table = pa.Table.from_pylist(
            [record.to_dict() for record in records],
            schema=_manifest_schema(),
        )
        pq.write_table(
            table,
            temporary_path,
            compression="zstd",
            write_statistics=True,
        )
        with temporary_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_safe_output(path: Path, roots: NativeCorpusRoots) -> None:
    resolved_output = path.resolve()
    for root in roots.all():
        resolved_root = root.resolve()
        if resolved_output == resolved_root or resolved_root in resolved_output.parents:
            raise ValueError(
                "manifest outputs must be outside raw/native corpus roots"
            )


def _count_by(
    records: Iterable[NativeKoreanRecord],
    attribute: str,
) -> dict[str, int]:
    values = Counter(str(getattr(record, attribute)) for record in records)
    return dict(sorted(values.items()))


def build_native_korean_manifest(
    *,
    roots: NativeCorpusRoots = DEFAULT_ROOTS,
    output_path: str | Path,
    report_path: str | Path,
    limit_per_source: int | None = None,
) -> dict[str, Any]:
    """Build the unified Parquet and aggregate JSON report atomically."""

    output = Path(output_path)
    report_output = Path(report_path)
    if output.suffix.casefold() != ".parquet":
        raise ValueError("output_path must use the .parquet suffix")
    if report_output.suffix.casefold() != ".json":
        raise ValueError("report_path must use the .json suffix")
    if output.resolve() == report_output.resolve():
        raise ValueError("Parquet and report outputs must be different files")
    _assert_safe_output(output, roots)
    _assert_safe_output(report_output, roots)

    records = list(
        iter_native_korean_records(
            roots,
            limit_per_source=limit_per_source,
        )
    )
    if not records:
        raise ValueError("native Korean manifest would be empty")
    stable_ids = [record.stable_id for record in records]
    if len(stable_ids) != len(set(stable_ids)):
        raise ValueError("native Korean stable IDs are not unique")
    source_counts = _count_by(records, "source_id")
    expected_source_counts = {
        source_id: (
            expected_count
            if limit_per_source is None
            else min(expected_count, limit_per_source)
        )
        for source_id, expected_count in EXPECTED_SOURCE_COUNTS.items()
    }
    if source_counts != expected_source_counts:
        raise ValueError(
            "native Korean source counts do not match the pinned corpus "
            f"contract: {source_counts!r} != {expected_source_counts!r}"
        )

    invalid_decode_metadata_count = sum(
        record.sample_rate_hz <= 0
        or record.channels <= 0
        or record.num_frames <= 0
        or record.duration_seconds <= 0
        or not record.audio_format
        for record in records
    )
    locator_contract_violation_count = sum(
        (record.relative_path is None) == (record.embedded_reference is None)
        for record in records
    )
    transcript_reference_violation_count = sum(
        record.transcript_available
        != (record.transcript_reference is not None)
        for record in records
    )
    if (
        invalid_decode_metadata_count
        or locator_contract_violation_count
        or transcript_reference_violation_count
    ):
        raise ValueError("native Korean manifest contract validation failed")

    _atomic_write_parquet(output, records)
    manifest_sha256 = _sha256(output)
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "manifest": {
            "sha256": manifest_sha256,
            "bytes": output.stat().st_size,
            "records": len(records),
        },
        "limit_per_source": limit_per_source,
        "counts": {
            "source": source_counts,
            "split": _count_by(records, "split"),
            "license": _count_by(records, "license_id"),
            "intended_role": _count_by(records, "intended_role"),
            "audio_storage": _count_by(records, "audio_storage"),
            "sample_rate_hz": _count_by(records, "sample_rate_hz"),
            "channels": _count_by(records, "channels"),
            "audio_format": _count_by(records, "audio_format"),
            "transcript_available": _count_by(
                records,
                "transcript_available",
            ),
            "noncommercial_only": _count_by(
                records,
                "noncommercial_only",
            ),
            "raw_redistribution_allowed": _count_by(
                records,
                "raw_redistribution_allowed",
            ),
        },
        "audio_summary": {
            "total_duration_seconds": sum(
                record.duration_seconds for record in records
            ),
            "minimum_duration_seconds": min(
                record.duration_seconds for record in records
            ),
            "maximum_duration_seconds": max(
                record.duration_seconds for record in records
            ),
        },
        "validation": {
            "expected_source_counts": expected_source_counts,
            "source_counts_match_expected": True,
            "stable_id_count": len(stable_ids),
            "stable_id_unique_count": len(set(stable_ids)),
            "decode_metadata_valid_count": (
                len(records) - invalid_decode_metadata_count
            ),
            "decode_metadata_invalid_count": invalid_decode_metadata_count,
            "locator_contract_violation_count": (
                locator_contract_violation_count
            ),
            "transcript_reference_violation_count": (
                transcript_reference_violation_count
            ),
            "manifest_sha256_recorded": True,
        },
        "privacy_checks": {
            "privacy_reviewed": True,
            "contains_direct_identifiers": False,
            "contains_transcript_text": False,
            "contains_audio_bytes": False,
            "contains_speaker_id_column": False,
            "report_contains_audio_locator": False,
            "report_contains_transcript_reference": False,
            "report_contains_raw_file_stem": False,
            "absolute_relative_path_count": sum(
                bool(
                    record.relative_path
                    and Path(record.relative_path).is_absolute()
                )
                for record in records
            ),
            "stable_ids_unique": True,
        },
        "privacy_check_semantics": (
            "Privacy-check field names identify prohibited value classes; "
            "the aggregate report contains no source locator, transcript "
            "reference, or raw file-stem values."
        ),
        "sharing_boundary": {
            "operational_manifest": {
                "classification": "restricted_internal",
                "shareable": False,
                "contains_source_audio_locators": True,
                "contains_transcript_references": True,
                "contains_transcript_text": False,
                "contains_audio_bytes": False,
            },
            "aggregate_report": {
                "classification": "public_aggregate",
                "shareable": True,
                "contains_source_audio_locators": False,
                "contains_transcript_references": False,
                "contains_raw_file_stems": False,
            },
        },
        "source_policies": {
            source_id: asdict(policy)
            for source_id, policy in sorted(CORPUS_POLICIES.items())
        },
    }
    _atomic_write_json(report_output, report)
    return report
