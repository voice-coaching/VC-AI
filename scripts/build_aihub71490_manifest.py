#!/usr/bin/env python3
"""Build raw and optional speaker-disjoint manifests for AI Hub dataset 71490.

The script only reads the restricted source tree.  It joins WAV, JSON, and
TextGrid files by case-insensitive file stem, decodes WAV payloads to count the
frames that can actually be read, and writes:

* ``aihub71490_raw.jsonl`` (always; ``split`` is null)
* ``aihub71490_qc_summary.json`` (always)
* ``aihub71490_mvp_speaker_disjoint.jsonl`` (with
  ``--with-speaker-splits``)

Only Python's standard library is used.  Paths stored in JSONL are relative to
``--dataset-root`` and use POSIX separators so that manifests remain portable
between Windows and Linux.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import itertools
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any, Iterable, Iterator, NamedTuple, Sequence, TextIO
import wave


SCHEMA_VERSION = "aihub71490.raw.v2"
DATASET_ID = "71490"
SPEAKER_HASH_NAMESPACE = "intelligentAI:aihub71490:speaker:v1"
RAW_MANIFEST_NAME = "aihub71490_raw.jsonl"
MVP_MANIFEST_NAME = "aihub71490_mvp_speaker_disjoint.jsonl"
QC_SUMMARY_NAME = "aihub71490_qc_summary.json"

MODALITY_SUFFIX = {
    "wav": ".wav",
    "json": ".json",
    "textgrid": ".TextGrid",
}

MVP_SPLITS: tuple[tuple[str, float], ...] = (
    ("train", 0.50),
    ("dev", 0.10),
    ("stack", 0.10),
    ("calibration", 0.10),
    ("locked_test", 0.20),
)


class SourceRoot(NamedTuple):
    modality: str
    path: Path
    relative_path: str
    official_partition: str | None
    source_kind: str


class FileRow(NamedTuple):
    stem_key: str
    stem: str
    modality: str
    relative_path: str
    official_partition: str | None
    source_kind: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Join AI Hub 71490 WAV/JSON/TextGrid files by stem and create a "
            "split-free raw JSONL manifest plus a QC summary."
        )
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("data/restricted/aihub/dataset-71490"),
        help=(
            "AI Hub 71490 root directory "
            "(default: data/restricted/aihub/dataset-71490)"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/aihub71490/manifests"),
        help=(
            "Directory for JSONL/QC outputs. It must not be inside the raw "
            "dataset root (default: data/processed/aihub71490/manifests)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        help=(
            "Smoke-test mode: process at most N anchor stems. QC then covers "
            "only those selected stems, not the complete corpus."
        ),
    )
    parser.add_argument(
        "--with-speaker-splits",
        action="store_true",
        help=(
            "Also write an MVP manifest with deterministic, UserID-disjoint "
            "train/dev/stack/calibration/locked_test splits."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed used for deterministic speaker split assignment (default: 42).",
    )
    parser.add_argument(
        "--wav-chunk-frames",
        type=positive_int,
        default=1_048_576,
        help="Frames read per WAV decoding chunk (default: 1048576).",
    )
    parser.add_argument(
        "--progress-every",
        type=positive_int,
        default=1_000,
        help="Print progress after this many records (default: 1000).",
    )
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def portable_relative_path(path: Path, dataset_root: Path) -> str:
    return path.resolve().relative_to(dataset_root).as_posix()


def official_partition_from_path(path: Path) -> str | None:
    for part in path.parts:
        folded = part.casefold()
        if folded == "training":
            return "Training"
        if folded == "validation":
            return "Validation"
    return None


def classify_source_directory(path: Path, dataset_root: Path) -> SourceRoot | None:
    relative = portable_relative_path(path, dataset_root)
    parts = tuple(part.casefold() for part in Path(relative).parts)
    name = path.name.casefold()
    in_labels = any("라벨링데이터" in part for part in parts)
    in_audio = any("원천데이터" in part for part in parts)

    modality: str | None = None
    source_kind = "unknown"

    if in_labels and (
        name.endswith("_pronunciation_lab")
        or name.endswith("_speech_lab")
        or name.endswith("_lab")
    ):
        modality = "json"
        source_kind = "pronunciation" if "pronunciation" in name else "speech"
    elif in_labels and (
        name.endswith("_pronunciation_tag") or name.endswith("_tag")
    ):
        modality = "textgrid"
        source_kind = "pronunciation"
    elif in_audio and name.startswith("extracted"):
        modality = "wav"
        source_kind = "pronunciation" if "pronunciation" in name else "speech"

    if modality is None:
        return None

    return SourceRoot(
        modality=modality,
        path=path,
        relative_path=relative,
        official_partition=official_partition_from_path(path),
        source_kind=source_kind,
    )


def discover_source_roots(dataset_root: Path) -> list[SourceRoot]:
    """Find known extracted leaf roots without enumerating their huge file lists."""

    discovered: list[SourceRoot] = []
    stack = [dataset_root]
    while stack:
        current = stack.pop()
        children: list[Path] = []
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_directory:
                        children.append(Path(entry.path))
        except OSError as exc:
            raise RuntimeError(f"cannot scan directory {current}: {exc}") from exc

        for child in sorted(children, key=lambda item: item.name.casefold(), reverse=True):
            classified = classify_source_directory(child, dataset_root)
            if classified is not None:
                discovered.append(classified)
            else:
                stack.append(child)

    return sorted(
        discovered,
        key=lambda root: (root.modality, root.relative_path.casefold()),
    )


def iter_modality_files(source_root: SourceRoot) -> Iterator[Path]:
    expected_suffix = MODALITY_SUFFIX[source_root.modality].casefold()
    stack = [source_root.path]

    while stack:
        current = stack.pop()
        subdirectories: list[Path] = []
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            subdirectories.append(Path(entry.path))
                        elif (
                            entry.is_file(follow_symlinks=False)
                            and not entry.name.startswith(".")
                            and Path(entry.name).suffix.casefold() == expected_suffix.casefold()
                        ):
                            yield Path(entry.path)
                    except OSError:
                        continue
        except OSError as exc:
            raise RuntimeError(f"cannot scan source root {current}: {exc}") from exc

        for subdirectory in sorted(
            subdirectories, key=lambda item: item.name.casefold(), reverse=True
        ):
            stack.append(subdirectory)


def source_root_summary(source_roots: Sequence[SourceRoot]) -> list[dict[str, Any]]:
    return [
        {
            "modality": root.modality,
            "path": root.relative_path,
            "official_partition": root.official_partition,
            "source_kind": root.source_kind,
        }
        for root in source_roots
    ]


def file_row(path: Path, source_root: SourceRoot, dataset_root: Path) -> FileRow:
    return FileRow(
        stem_key=path.stem.casefold(),
        stem=path.stem,
        modality=source_root.modality,
        relative_path=portable_relative_path(path, dataset_root),
        official_partition=source_root.official_partition,
        source_kind=source_root.source_kind,
    )


def choose_limited_anchor_rows(
    source_roots: Sequence[SourceRoot],
    dataset_root: Path,
    limit: int,
) -> list[FileRow]:
    """Select N stems quickly, preferring JSON as the authoritative anchor."""

    roots_by_priority = [
        [root for root in source_roots if root.modality == modality]
        for modality in ("json", "wav", "textgrid")
    ]
    anchor_roots = next((roots for roots in roots_by_priority if roots), [])
    selected: dict[str, FileRow] = {}

    for source_root in anchor_roots:
        for path in iter_modality_files(source_root):
            row = file_row(path, source_root, dataset_root)
            selected.setdefault(row.stem_key, row)
            if len(selected) >= limit:
                return list(selected.values())

    return list(selected.values())


def direct_candidates_for_anchor(
    anchor: FileRow,
    source_roots: Sequence[SourceRoot],
    dataset_root: Path,
) -> list[FileRow]:
    """Resolve a limited-mode stem in the flat 71490 extraction roots.

    Dataset 71490's extracted modality roots are flat.  A recursive fallback is
    intentionally avoided in ``--limit`` mode because it would enumerate the
    entire corpus and defeat the purpose of a quick smoke test.
    """

    rows: list[FileRow] = []
    seen_paths: set[Path] = set()
    for source_root in source_roots:
        suffix = MODALITY_SUFFIX[source_root.modality]
        candidates = (
            source_root.path / f"{anchor.stem}{suffix}",
            source_root.path / f"{anchor.stem}{suffix.casefold()}",
        )
        for candidate in candidates:
            if candidate in seen_paths or not candidate.is_file():
                continue
            seen_paths.add(candidate)
            rows.append(file_row(candidate, source_root, dataset_root))
    return sorted(rows, key=lambda row: (row.modality, row.relative_path.casefold()))


def limited_groups(
    source_roots: Sequence[SourceRoot],
    dataset_root: Path,
    limit: int,
) -> Iterator[tuple[str, list[FileRow]]]:
    anchors = choose_limited_anchor_rows(source_roots, dataset_root, limit)
    for anchor in anchors:
        candidates = direct_candidates_for_anchor(anchor, source_roots, dataset_root)
        if not any(row.relative_path == anchor.relative_path for row in candidates):
            candidates.append(anchor)
        yield anchor.stem_key, sorted(
            candidates, key=lambda row: (row.modality, row.relative_path.casefold())
        )


def create_inventory_database(output_dir: Path) -> tuple[sqlite3.Connection, Path]:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=".aihub71490_inventory_", suffix=".sqlite3", dir=output_dir
    )
    os.close(descriptor)
    database_path = Path(raw_path)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute(
        """
        CREATE TABLE files (
            stem_key TEXT NOT NULL,
            stem TEXT NOT NULL,
            modality TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            official_partition TEXT,
            source_kind TEXT NOT NULL
        )
        """
    )
    return connection, database_path


def populate_inventory_database(
    connection: sqlite3.Connection,
    source_roots: Sequence[SourceRoot],
    dataset_root: Path,
) -> int:
    inserted = 0
    batch: list[tuple[str, str, str, str, str | None, str]] = []

    for source_root in source_roots:
        for path in iter_modality_files(source_root):
            row = file_row(path, source_root, dataset_root)
            batch.append(tuple(row))
            if len(batch) >= 10_000:
                connection.executemany(
                    "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?)", batch
                )
                inserted += len(batch)
                batch.clear()

    if batch:
        connection.executemany("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?)", batch)
        inserted += len(batch)
    connection.commit()
    connection.execute(
        "CREATE INDEX files_by_stem ON files(stem_key, modality, relative_path)"
    )
    connection.commit()
    return inserted


def full_groups(
    connection: sqlite3.Connection,
) -> Iterator[tuple[str, list[FileRow]]]:
    cursor = connection.execute(
        """
        SELECT stem_key, stem, modality, relative_path,
               official_partition, source_kind
        FROM files
        ORDER BY stem_key, modality, relative_path
        """
    )
    for stem_key, rows in itertools.groupby(cursor, key=lambda row: row[0]):
        yield stem_key, [FileRow(*row) for row in rows]


def resolve_relative_path(dataset_root: Path, relative_path: str) -> Path:
    # Relative paths in the inventory always use "/", including on Windows.
    return dataset_root.joinpath(*Path(relative_path).parts)


def compact_json_metadata(document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Keep corpus metadata/transcripts while leaving dense phone arrays in source."""

    compact: dict[str, Any] = {}
    annotation_counts = {
        "words": 0,
        "target_phones": 0,
        "error_tags": 0,
    }

    for key, value in document.items():
        if key != "RecordingMetadata":
            compact[key] = value
            continue
        if not isinstance(value, dict):
            compact[key] = value
            continue

        recording = dict(value)
        phonemic = recording.pop("phonemic", None)
        compact[key] = recording
        if isinstance(phonemic, dict):
            for source_key, output_key in (
                ("words", "words"),
                ("phones", "target_phones"),
                ("error_tags", "error_tags"),
            ):
                values = phonemic.get(source_key)
                if isinstance(values, list):
                    annotation_counts[output_key] = len(values)

    return compact, annotation_counts


def load_json_metadata(path: Path) -> tuple[dict[str, Any] | None, dict[str, int], str | None]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            document = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, {"words": 0, "target_phones": 0, "error_tags": 0}, str(exc)

    if not isinstance(document, dict):
        return (
            None,
            {"words": 0, "target_phones": 0, "error_tags": 0},
            f"top-level JSON value is {type(document).__name__}, expected object",
        )
    metadata, counts = compact_json_metadata(document)
    return metadata, counts, None


def decode_wav(path: Path, chunk_frames: int) -> tuple[dict[str, Any] | None, str | None]:
    """Read every WAV data frame instead of deriving duration from file size."""

    try:
        with wave.open(str(path), "rb") as reader:
            channels = reader.getnchannels()
            sample_width_bytes = reader.getsampwidth()
            sample_rate_hz = reader.getframerate()
            header_frame_count = reader.getnframes()
            compression_type = reader.getcomptype()
            compression_name = reader.getcompname()
            frame_width_bytes = channels * sample_width_bytes

            if channels <= 0 or sample_width_bytes <= 0 or sample_rate_hz <= 0:
                raise wave.Error(
                    "invalid WAV parameters: "
                    f"channels={channels}, width={sample_width_bytes}, "
                    f"rate={sample_rate_hz}"
                )

            decoded_frame_count = 0
            trailing_partial_frame_bytes = 0
            while True:
                payload = reader.readframes(chunk_frames)
                if not payload:
                    break
                decoded_frame_count += len(payload) // frame_width_bytes
                trailing_partial_frame_bytes += len(payload) % frame_width_bytes

        decoded_sample_count_all_channels = decoded_frame_count * channels
        duration_seconds = decoded_frame_count / sample_rate_hz
        return (
            {
                "sample_rate_hz": sample_rate_hz,
                "channels": channels,
                "sample_width_bytes": sample_width_bytes,
                "compression_type": compression_type,
                "compression_name": compression_name,
                "header_frame_count": header_frame_count,
                "decoded_frame_count": decoded_frame_count,
                "decoded_sample_count_per_channel": decoded_frame_count,
                "decoded_sample_count_all_channels": decoded_sample_count_all_channels,
                "duration_seconds": round(duration_seconds, 9),
                "trailing_partial_frame_bytes": trailing_partial_frame_bytes,
                "header_matches_decoded_frames": header_frame_count
                == decoded_frame_count,
            },
            None,
        )
    except (OSError, EOFError, wave.Error) as exc:
        return None, str(exc)


def primary_and_alternates(
    rows: Sequence[FileRow], modality: str
) -> tuple[FileRow | None, list[str]]:
    candidates = sorted(
        (row for row in rows if row.modality == modality),
        key=lambda row: row.relative_path.casefold(),
    )
    if not candidates:
        return None, []
    return candidates[0], [candidate.relative_path for candidate in candidates[1:]]


def infer_source_kind(
    rows: Sequence[FileRow], annotation_counts: dict[str, int]
) -> str:
    if annotation_counts["target_phones"] > 0:
        return "pronunciation"
    kinds = {row.source_kind for row in rows}
    if "pronunciation" in kinds:
        return "pronunciation"
    if "speech" in kinds:
        return "speech"
    return "unknown"


def build_record(
    stem_key: str,
    rows: Sequence[FileRow],
    dataset_root: Path,
    wav_chunk_frames: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    wav_row, wav_alternates = primary_and_alternates(rows, "wav")
    json_row, json_alternates = primary_and_alternates(rows, "json")
    textgrid_row, textgrid_alternates = primary_and_alternates(rows, "textgrid")
    canonical_stem = min((row.stem for row in rows), key=str.casefold)

    metadata: dict[str, Any] | None = None
    annotation_counts = {"words": 0, "target_phones": 0, "error_tags": 0}
    json_error: str | None = None
    if json_row is not None:
        metadata, annotation_counts, json_error = load_json_metadata(
            resolve_relative_path(dataset_root, json_row.relative_path)
        )
    raw_speaker_id = extract_speaker_id(metadata)
    if metadata is not None:
        metadata.pop("UserID", None)

    wav_info: dict[str, Any] | None = None
    wav_error: str | None = None
    if wav_row is not None:
        wav_info, wav_error = decode_wav(
            resolve_relative_path(dataset_root, wav_row.relative_path),
            wav_chunk_frames,
        )

    source_kind = infer_source_kind(rows, annotation_counts)
    official_partitions = sorted(
        {
            row.official_partition
            for row in rows
            if row.official_partition is not None
        }
    )
    target_phone_path = (
        json_row.relative_path
        if json_row is not None and source_kind == "pronunciation"
        else None
    )
    realized_phone_path = (
        textgrid_row.relative_path if textgrid_row is not None else None
    )

    duplicate_counts = collections.Counter(row.modality for row in rows)
    missing_modalities = [
        modality
        for modality, primary in (
            ("wav", wav_row),
            ("json", json_row),
            ("textgrid", textgrid_row),
        )
        if primary is None
    ]
    record_errors = {
        key: value
        for key, value in (("json", json_error), ("wav", wav_error))
        if value is not None
    }
    alternates = {
        key: value
        for key, value in (
            ("wav", wav_alternates),
            ("json", json_alternates),
            ("textgrid", textgrid_alternates),
        )
        if value
    }

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "id": canonical_stem,
        "stem_key": stem_key,
        "speaker_id": (
            hash_speaker_id(raw_speaker_id)
            if raw_speaker_id is not None
            else None
        ),
        "split": None,
        "official_partitions": official_partitions,
        "source_kind": source_kind,
        "source_paths": {
            "wav": wav_row.relative_path if wav_row is not None else None,
            "json": json_row.relative_path if json_row is not None else None,
            "textgrid": (
                textgrid_row.relative_path if textgrid_row is not None else None
            ),
            "alternates": alternates,
        },
        "phone_sources": {
            "target": (
                {
                    "path": target_phone_path,
                    "format": "aihub_pronunciation_json",
                    "json_pointer": "/RecordingMetadata/phonemic/phones",
                }
                if target_phone_path is not None
                else None
            ),
            "realized": (
                {
                    "path": realized_phone_path,
                    "format": "praat_textgrid",
                    "tier": "phones",
                }
                if realized_phone_path is not None
                else None
            ),
        },
        "audio": wav_info,
        "json_metadata": metadata,
        "annotation_counts": annotation_counts,
        "qc": {
            "missing_modalities": missing_modalities,
            "duplicate_file_counts": {
                modality: count
                for modality, count in sorted(duplicate_counts.items())
                if count > 1
            },
            "errors": record_errors,
        },
    }

    metrics = {
        "file_counts": dict(duplicate_counts),
        "missing_modalities": missing_modalities,
        "source_kind": source_kind,
        "official_partitions": official_partitions,
        "target_phone_present": target_phone_path is not None,
        "realized_phone_present": realized_phone_path is not None,
        "json_error": json_error,
        "wav_error": wav_error,
        "duration_seconds": (
            wav_info["duration_seconds"] if wav_info is not None else 0.0
        ),
        "sample_rate_hz": (
            wav_info["sample_rate_hz"] if wav_info is not None else None
        ),
        "header_frame_mismatch": (
            wav_info is not None and not wav_info["header_matches_decoded_frames"]
        ),
        "speaker_id": raw_speaker_id,
        "is_model_complete": (
            wav_row is not None
            and json_row is not None
            and json_error is None
            and wav_error is None
            and (
                source_kind != "pronunciation"
                or (target_phone_path is not None and realized_phone_path is not None)
            )
        ),
    }
    return record, metrics


def extract_speaker_id(metadata: dict[str, Any] | None) -> str | None:
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("UserID")
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def hash_speaker_id(speaker_id: str) -> str:
    payload = f"{SPEAKER_HASH_NAMESPACE}:{speaker_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def deterministic_speaker_split(speaker_id: str, seed: int) -> str:
    digest = hashlib.sha256(f"{seed}:{speaker_id}".encode("utf-8")).digest()
    point = int.from_bytes(digest, byteorder="big") / (1 << (8 * len(digest)))
    cumulative = 0.0
    for split_name, ratio in MVP_SPLITS:
        cumulative += ratio
        if point < cumulative:
            return split_name
    return MVP_SPLITS[-1][0]


def json_line(handle: TextIO, value: dict[str, Any]) -> None:
    handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    handle.write("\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class QcAccumulator:
    def __init__(self) -> None:
        self.records = 0
        self.files = collections.Counter()
        self.present = collections.Counter()
        self.missing = collections.Counter()
        self.duplicate_stems = collections.Counter()
        self.duplicate_extra_files = collections.Counter()
        self.source_kinds = collections.Counter()
        self.official_partitions = collections.Counter()
        self.sample_rates = collections.Counter()
        self.error_counts = collections.Counter()
        self.error_examples: list[dict[str, str]] = []
        self.duration_seconds = 0.0
        self.model_complete_records = 0
        self.speakers: set[str] = set()
        self.speaker_missing = 0
        self.split_records = collections.Counter()
        self.split_speakers: dict[str, set[str]] = collections.defaultdict(set)

    def add(
        self,
        record: dict[str, Any],
        metrics: dict[str, Any],
        assigned_split: str | None,
    ) -> None:
        self.records += 1
        for modality in MODALITY_SUFFIX:
            file_count = metrics["file_counts"].get(modality, 0)
            self.files[modality] += file_count
            if file_count:
                self.present[modality] += 1
            else:
                self.missing[modality] += 1
            if file_count > 1:
                self.duplicate_stems[modality] += 1
                self.duplicate_extra_files[modality] += file_count - 1

        self.source_kinds[metrics["source_kind"]] += 1
        for partition in metrics["official_partitions"]:
            self.official_partitions[partition] += 1
        self.duration_seconds += metrics["duration_seconds"]
        if metrics["sample_rate_hz"] is not None:
            self.sample_rates[str(metrics["sample_rate_hz"])] += 1
        if metrics["header_frame_mismatch"]:
            self.error_counts["wav_header_frame_mismatch"] += 1
        if metrics["json_error"] is not None:
            self._add_error(record["id"], "json_decode", metrics["json_error"])
        if metrics["wav_error"] is not None:
            self._add_error(record["id"], "wav_decode", metrics["wav_error"])
        if metrics["is_model_complete"]:
            self.model_complete_records += 1

        if metrics["target_phone_present"]:
            self.present["target_phone_source"] += 1
        elif metrics["source_kind"] == "pronunciation":
            self.missing["target_phone_source_required"] += 1
        if metrics["realized_phone_present"]:
            self.present["realized_phone_source"] += 1
        elif metrics["source_kind"] == "pronunciation":
            self.missing["realized_phone_source_required"] += 1

        speaker_id = metrics["speaker_id"]
        if speaker_id is None:
            self.speaker_missing += 1
        else:
            self.speakers.add(speaker_id)

        if assigned_split is not None:
            self.split_records[assigned_split] += 1
            if speaker_id is not None:
                self.split_speakers[assigned_split].add(speaker_id)

    def _add_error(self, stem: str, category: str, detail: str) -> None:
        self.error_counts[category] += 1
        if len(self.error_examples) < 50:
            self.error_examples.append(
                {"id": stem, "category": category, "detail": detail}
            )

    def split_summary(self, enabled: bool, seed: int) -> dict[str, Any]:
        if not enabled:
            return {
                "enabled": False,
                "raw_manifest_split": None,
            }

        overlaps: dict[str, int] = {}
        names = [name for name, _ in MVP_SPLITS]
        for index, left in enumerate(names):
            for right in names[index + 1 :]:
                overlap = self.split_speakers[left] & self.split_speakers[right]
                overlaps[f"{left}__{right}"] = len(overlap)
        return {
            "enabled": True,
            "method": "sha256(seed:UserID)",
            "seed": seed,
            "ratios": dict(MVP_SPLITS),
            "record_counts": dict(self.split_records),
            "speaker_counts": {
                name: len(self.split_speakers[name]) for name in names
            },
            "speaker_overlap_counts": overlaps,
            "speaker_disjoint_verified": all(count == 0 for count in overlaps.values()),
            "unassigned_records_missing_UserID": self.speaker_missing,
        }

    def summary(
        self,
        *,
        dataset_root: Path,
        output_dir: Path,
        source_roots: Sequence[SourceRoot],
        limited: bool,
        limit: int | None,
        raw_manifest_path: Path,
        mvp_manifest_path: Path | None,
        with_speaker_splits: bool,
        seed: int,
        elapsed_seconds: float,
    ) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": DATASET_ID,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "dataset_root": str(dataset_root),
            "output_dir": str(output_dir),
            "scope": {
                "mode": "limited_json_anchor_stems" if limited else "full_union",
                "limit": limit,
                "whole_corpus_counts": not limited,
                "raw_manifest_split_applied": False,
            },
            "source_roots": source_root_summary(source_roots),
            "counts": {
                "unique_stems": self.records,
                "records_written": self.records,
                "files": dict(self.files),
                "stems_with_modality": {
                    modality: self.present[modality] for modality in MODALITY_SUFFIX
                },
                "missing_by_modality": {
                    modality: self.missing[modality] for modality in MODALITY_SUFFIX
                },
                "missing_required_phone_sources": {
                    "target": self.missing["target_phone_source_required"],
                    "realized": self.missing["realized_phone_source_required"],
                },
                "duplicate_stems": {
                    modality: self.duplicate_stems[modality]
                    for modality in MODALITY_SUFFIX
                },
                "duplicate_extra_files": {
                    modality: self.duplicate_extra_files[modality]
                    for modality in MODALITY_SUFFIX
                },
                "model_complete_records": self.model_complete_records,
                "unique_speakers": len(self.speakers),
                "records_missing_UserID": self.speaker_missing,
                "source_kinds": dict(self.source_kinds),
                "official_partitions": dict(self.official_partitions),
                "sample_rates_hz": dict(self.sample_rates),
                "errors": dict(self.error_counts),
            },
            "audio": {
                "duration_source": "decoded_frame_count / sample_rate_hz",
                "decoded_duration_seconds": round(self.duration_seconds, 6),
                "decoded_duration_hours": round(self.duration_seconds / 3600.0, 6),
            },
            "speaker_split": self.split_summary(with_speaker_splits, seed),
            "outputs": {
                "raw_manifest": {
                    "path": str(raw_manifest_path),
                    "sha256": sha256_file(raw_manifest_path),
                },
                "mvp_speaker_disjoint_manifest": (
                    {
                        "path": str(mvp_manifest_path),
                        "sha256": sha256_file(mvp_manifest_path),
                    }
                    if mvp_manifest_path is not None
                    else None
                ),
            },
            "error_examples": self.error_examples,
            "elapsed_seconds": round(elapsed_seconds, 3),
        }


def atomic_output_handle(final_path: Path) -> tuple[TextIO, Path]:
    temporary_path = final_path.with_name(f".{final_path.name}.tmp")
    handle = temporary_path.open("w", encoding="utf-8", newline="\n")
    return handle, temporary_path


def run(args: argparse.Namespace) -> int:
    import time

    started = time.monotonic()
    dataset_root = args.dataset_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not dataset_root.is_dir():
        raise RuntimeError(f"dataset root does not exist: {dataset_root}")
    if path_is_within(output_dir, dataset_root):
        raise RuntimeError(
            "output directory must not be inside the raw dataset root: "
            f"{output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    source_roots = discover_source_roots(dataset_root)
    if not source_roots:
        raise RuntimeError(
            "no extracted AI Hub 71490 WAV/JSON/TextGrid source roots were found"
        )
    available_modalities = collections.Counter(root.modality for root in source_roots)
    print(
        "Discovered roots: "
        + ", ".join(
            f"{modality}={available_modalities[modality]}"
            for modality in ("wav", "json", "textgrid")
        ),
        file=sys.stderr,
    )

    inventory_connection: sqlite3.Connection | None = None
    inventory_path: Path | None = None
    if args.limit is not None:
        groups: Iterable[tuple[str, list[FileRow]]] = limited_groups(
            source_roots, dataset_root, args.limit
        )
    else:
        inventory_connection, inventory_path = create_inventory_database(output_dir)
        file_count = populate_inventory_database(
            inventory_connection, source_roots, dataset_root
        )
        print(f"Indexed source files: {file_count:,}", file=sys.stderr)
        groups = full_groups(inventory_connection)

    raw_path = output_dir / RAW_MANIFEST_NAME
    mvp_path = (
        output_dir / MVP_MANIFEST_NAME if args.with_speaker_splits else None
    )
    qc_path = output_dir / QC_SUMMARY_NAME

    raw_handle, raw_temporary_path = atomic_output_handle(raw_path)
    mvp_handle: TextIO | None = None
    mvp_temporary_path: Path | None = None
    if mvp_path is not None:
        mvp_handle, mvp_temporary_path = atomic_output_handle(mvp_path)

    accumulator = QcAccumulator()
    succeeded = False
    try:
        for stem_key, rows in groups:
            record, metrics = build_record(
                stem_key, rows, dataset_root, args.wav_chunk_frames
            )
            json_line(raw_handle, record)

            assigned_split: str | None = None
            if mvp_handle is not None:
                speaker_id = metrics["speaker_id"]
                if speaker_id is not None:
                    assigned_split = deterministic_speaker_split(
                        speaker_id, args.seed
                    )
                mvp_record = dict(record)
                mvp_record["split"] = assigned_split
                json_line(mvp_handle, mvp_record)

            accumulator.add(record, metrics, assigned_split)
            if accumulator.records % args.progress_every == 0:
                print(
                    f"Processed records: {accumulator.records:,}",
                    file=sys.stderr,
                )

        raw_handle.flush()
        os.fsync(raw_handle.fileno())
        raw_handle.close()
        os.replace(raw_temporary_path, raw_path)

        if mvp_handle is not None and mvp_temporary_path is not None and mvp_path is not None:
            mvp_handle.flush()
            os.fsync(mvp_handle.fileno())
            mvp_handle.close()
            os.replace(mvp_temporary_path, mvp_path)

        elapsed = time.monotonic() - started
        summary = accumulator.summary(
            dataset_root=dataset_root,
            output_dir=output_dir,
            source_roots=source_roots,
            limited=args.limit is not None,
            limit=args.limit,
            raw_manifest_path=raw_path,
            mvp_manifest_path=mvp_path,
            with_speaker_splits=args.with_speaker_splits,
            seed=args.seed,
            elapsed_seconds=elapsed,
        )
        qc_temporary_path = qc_path.with_name(f".{qc_path.name}.tmp")
        with qc_temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(qc_temporary_path, qc_path)
        succeeded = True
    finally:
        if not raw_handle.closed:
            raw_handle.close()
        if mvp_handle is not None and not mvp_handle.closed:
            mvp_handle.close()
        if inventory_connection is not None:
            inventory_connection.close()
        if inventory_path is not None:
            try:
                inventory_path.unlink()
            except FileNotFoundError:
                pass
        if not succeeded:
            for path in (raw_temporary_path, mvp_temporary_path):
                if path is not None:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass

    print(f"Raw manifest: {raw_path}", file=sys.stderr)
    if mvp_path is not None:
        print(f"Speaker-disjoint MVP manifest: {mvp_path}", file=sys.stderr)
    print(f"QC summary: {qc_path}", file=sys.stderr)
    print(f"Records written: {accumulator.records:,}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except (RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
