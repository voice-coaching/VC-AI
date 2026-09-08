#!/usr/bin/env python3
"""Safely and resumably extract a trusted, checksum-verified ZIP archive."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--exclude-prefix",
        action="append",
        default=["__MACOSX/"],
        help="Archive path prefix to omit (repeatable).",
    )
    parser.add_argument(
        "--strip-leading-separators",
        action="store_true",
        help="Normalize non-standard entries beginning with '/' or '\\'.",
    )
    parser.add_argument(
        "--no-fsync",
        action="store_true",
        help="Skip per-file fsync; final size and ZIP CRC verification still apply.",
    )
    return parser.parse_args()


def safe_target(
    destination: Path, name: str, *, strip_leading_separators: bool = False
) -> Path:
    if strip_leading_separators:
        name = name.lstrip("/\\")
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe ZIP entry path: {name!r}")
    target = destination.joinpath(*path.parts).resolve()
    target.relative_to(destination.resolve())
    return target


def is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    archive = args.archive.resolve()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    marker = destination / ".verified-zip-extraction-complete.json"

    with zipfile.ZipFile(archive) as reader:
        entries = [
            info
            for info in reader.infolist()
            if not info.is_dir()
            and not any(info.filename.startswith(prefix) for prefix in args.exclude_prefix)
        ]
        if any(is_symlink(info) for info in entries):
            raise ValueError("ZIP contains a symbolic-link entry")

        extracted_bytes = 0
        for info in entries:
            target = safe_target(
                destination,
                info.filename,
                strip_leading_separators=args.strip_leading_separators,
            )
            if target.is_file() and target.stat().st_size == info.file_size:
                extracted_bytes += info.file_size
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.part")
            with reader.open(info) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                if not args.no_fsync:
                    os.fsync(output.fileno())
            if temporary.stat().st_size != info.file_size:
                raise IOError(f"size mismatch after extracting {info.filename!r}")
            temporary.replace(target)
            extracted_bytes += info.file_size

        for info in entries:
            target = safe_target(
                destination,
                info.filename,
                strip_leading_separators=args.strip_leading_separators,
            )
            if not target.is_file() or target.stat().st_size != info.file_size:
                raise IOError(f"verification failed for {info.filename!r}")

    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "completed_at": datetime.now().astimezone().isoformat(),
                "archive": archive.name,
                "archive_sha256": sha256_file(archive),
                "file_count": len(entries),
                "uncompressed_bytes": extracted_bytes,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"VERIFIED_EXTRACTION {archive.name}: "
        f"{len(entries)} files, {extracted_bytes} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
