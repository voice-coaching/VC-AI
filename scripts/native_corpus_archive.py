#!/usr/bin/env python3
"""Inspect and safely extract native Korean corpus tar archives.

Only Python's standard library is used. The script rejects path traversal,
links, device files, and case-insensitive path collisions before extraction.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile
from typing import Any


def _gzip_crc32(path: Path) -> str | None:
    with path.open("rb") as stream:
        if stream.read(2) != b"\x1f\x8b":
            return None
        stream.seek(-8, os.SEEK_END)
        footer = stream.read(8)
    if len(footer) != 8:
        raise ValueError(f"truncated gzip footer: {path}")
    return f"{int.from_bytes(footer[:4], 'little'):08x}"


def _safe_relative_path(name: str) -> PurePosixPath:
    if "\\" in name:
        raise ValueError(f"backslash path is not allowed in archive: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ValueError(f"absolute archive path is not allowed: {name!r}")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts:
        return PurePosixPath(".")
    if any(part == ".." for part in parts):
        raise ValueError(f"path traversal is not allowed in archive: {name!r}")
    if ":" in parts[0]:
        raise ValueError(f"drive-qualified archive path is not allowed: {name!r}")
    return PurePosixPath(*parts)


def _scan_members(archive: tarfile.TarFile) -> tuple[list[tarfile.TarInfo], dict[str, Any]]:
    members: list[tarfile.TarInfo] = []
    seen_casefolded: dict[str, str] = {}
    regular_files = 0
    regular_bytes = 0
    directories = 0

    for member in archive:
        relative = _safe_relative_path(member.name)
        normalized = relative.as_posix()
        collision_key = normalized.casefold()
        previous = seen_casefolded.get(collision_key)
        if previous is not None and previous != normalized:
            raise ValueError(
                "case-insensitive archive path collision: "
                f"{previous!r} and {normalized!r}"
            )
        seen_casefolded[collision_key] = normalized

        if member.issym() or member.islnk():
            raise ValueError(f"archive links are not allowed: {member.name!r}")
        if member.isdev() or member.isfifo():
            raise ValueError(f"archive special files are not allowed: {member.name!r}")
        if not member.isfile() and not member.isdir():
            raise ValueError(
                f"unsupported archive member type {member.type!r}: {member.name!r}"
            )

        if member.isfile():
            regular_files += 1
            regular_bytes += member.size
        else:
            directories += 1
        members.append(member)

    return members, {
        "member_count": len(members),
        "regular_files": regular_files,
        "regular_bytes": regular_bytes,
        "directories": directories,
    }


def inspect_archive(
    path: Path, expected_crc32: str | None, destination: Path | None = None
) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)

    actual_crc32 = _gzip_crc32(path)
    if expected_crc32:
        if actual_crc32 is None:
            raise ValueError(f"expected gzip archive, got another format: {path}")
        if actual_crc32.lower() != expected_crc32.lower():
            raise ValueError(
                f"gzip CRC32 mismatch for {path}: "
                f"{actual_crc32} != {expected_crc32.lower()}"
            )

    with tarfile.open(path, mode="r:*") as archive:
        members, stats = _scan_members(archive)

    if destination is not None:
        remaining_files = 0
        remaining_bytes = 0
        for member in members:
            if not member.isfile():
                continue
            target = _ensure_target_stays_inside(
                destination, _safe_relative_path(member.name)
            )
            if not target.is_file() or target.stat().st_size != member.size:
                remaining_files += 1
                remaining_bytes += member.size
        stats["remaining_files"] = remaining_files
        stats["remaining_bytes"] = remaining_bytes

    return {
        "archive": str(path.resolve()),
        "bytes": path.stat().st_size,
        "gzip_crc32": actual_crc32,
        **stats,
    }


def _ensure_target_stays_inside(destination: Path, relative: PurePosixPath) -> Path:
    target = destination.joinpath(*relative.parts)
    destination_resolved = destination.resolve()
    target_resolved = target.resolve(strict=False)
    try:
        target_resolved.relative_to(destination_resolved)
    except ValueError as error:
        raise ValueError(f"extraction target escapes destination: {relative}") from error
    return target


def extract_archive(
    path: Path, destination: Path, expected_crc32: str | None
) -> dict[str, Any]:
    inspection = inspect_archive(path, expected_crc32, destination)
    destination.mkdir(parents=True, exist_ok=True)

    extracted = 0
    resumed = 0
    with tarfile.open(path, mode="r:*") as archive:
        members, _ = _scan_members(archive)
        for member in members:
            relative = _safe_relative_path(member.name)
            target = _ensure_target_stays_inside(destination, relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            if target.is_file() and target.stat().st_size == member.size:
                resumed += 1
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            archive.extract(member, path=destination, filter="data")
            extracted += 1

    verified_files = 0
    verified_bytes = 0
    with tarfile.open(path, mode="r:*") as archive:
        members, _ = _scan_members(archive)
        for member in members:
            if not member.isfile():
                continue
            relative = _safe_relative_path(member.name)
            target = _ensure_target_stays_inside(destination, relative)
            if not target.is_file():
                raise FileNotFoundError(f"missing extracted file: {target}")
            actual_size = target.stat().st_size
            if actual_size != member.size:
                raise ValueError(
                    f"extracted size mismatch for {target}: "
                    f"{actual_size} != {member.size}"
                )
            verified_files += 1
            verified_bytes += actual_size

    if verified_files != inspection["regular_files"]:
        raise ValueError(
            "extracted file count mismatch: "
            f"{verified_files} != {inspection['regular_files']}"
        )
    if verified_bytes != inspection["regular_bytes"]:
        raise ValueError(
            "extracted byte count mismatch: "
            f"{verified_bytes} != {inspection['regular_bytes']}"
        )

    return {
        **inspection,
        "destination": str(destination.resolve()),
        "newly_extracted_files": extracted,
        "resumed_files": resumed,
        "verified_files": verified_files,
        "verified_bytes": verified_bytes,
    }


def validate_fleurs_pair(
    archive_path: Path,
    tsv_path: Path,
    expected_rows: int,
    expected_crc32: str | None,
) -> dict[str, Any]:
    inspection = inspect_archive(archive_path, expected_crc32)

    tsv_audio_names: list[str] = []
    with tsv_path.open("r", encoding="utf-8", newline="") as stream:
        for row_number, line in enumerate(stream, start=1):
            # FLEURS uses literal quote characters inside text fields; it is
            # tab-delimited data, not RFC-style quoted CSV.
            row = line.rstrip("\r\n").split("\t")
            if len(row) != 7:
                raise ValueError(
                    f"{tsv_path}:{row_number}: expected 7 columns, got {len(row)}"
                )
            if not row[1].lower().endswith(".wav"):
                raise ValueError(
                    f"{tsv_path}:{row_number}: invalid audio filename {row[1]!r}"
                )
            try:
                int(row[0])
                int(row[5])
            except ValueError as error:
                raise ValueError(
                    f"{tsv_path}:{row_number}: id and sample count must be integers"
                ) from error
            tsv_audio_names.append(row[1])

    if len(tsv_audio_names) != expected_rows:
        raise ValueError(
            f"TSV row count mismatch for {tsv_path}: "
            f"{len(tsv_audio_names)} != {expected_rows}"
        )
    if len(set(tsv_audio_names)) != len(tsv_audio_names):
        raise ValueError(f"duplicate audio filename in TSV: {tsv_path}")

    with tarfile.open(archive_path, mode="r:*") as archive:
        members, _ = _scan_members(archive)
    archive_audio_names = [
        PurePosixPath(member.name).name
        for member in members
        if member.isfile() and member.name.lower().endswith(".wav")
    ]
    if len(set(archive_audio_names)) != len(archive_audio_names):
        raise ValueError(f"duplicate audio basename in archive: {archive_path}")

    missing_audio = sorted(set(tsv_audio_names) - set(archive_audio_names))
    extra_audio = sorted(set(archive_audio_names) - set(tsv_audio_names))
    if missing_audio or extra_audio:
        raise ValueError(
            "FLEURS TSV/audio mismatch: "
            f"missing={missing_audio[:5]!r} ({len(missing_audio)} total), "
            f"extra={extra_audio[:5]!r} ({len(extra_audio)} total)"
        )

    return {
        **inspection,
        "tsv": str(tsv_path.resolve()),
        "tsv_rows": len(tsv_audio_names),
        "archive_audio_files": len(archive_audio_names),
        "metadata_audio_match": True,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("inspect", "extract"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--archive", type=Path, required=True)
        subparser.add_argument("--expected-gzip-crc32")
        subparser.add_argument("--destination", type=Path)

    fleurs_parser = subparsers.add_parser("validate-fleurs")
    fleurs_parser.add_argument("--archive", type=Path, required=True)
    fleurs_parser.add_argument("--tsv", type=Path, required=True)
    fleurs_parser.add_argument("--expected-rows", type=int, required=True)
    fleurs_parser.add_argument("--expected-gzip-crc32")
    return parser


def main() -> int:
    arguments = _build_parser().parse_args()
    if arguments.command == "inspect":
        result = inspect_archive(
            arguments.archive,
            arguments.expected_gzip_crc32,
            arguments.destination,
        )
    elif arguments.command == "extract":
        if arguments.destination is None:
            raise ValueError("--destination is required for extract")
        result = extract_archive(
            arguments.archive,
            arguments.destination,
            arguments.expected_gzip_crc32,
        )
    else:
        result = validate_fleurs_pair(
            arguments.archive,
            arguments.tsv,
            arguments.expected_rows,
            arguments.expected_gzip_crc32,
        )
    json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
