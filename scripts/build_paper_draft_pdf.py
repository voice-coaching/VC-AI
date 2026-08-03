#!/usr/bin/env python3
"""Build or deep-validate the aggregate-only canonical paper draft PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping


def _capture_cli_start() -> tuple[Path, bytes, os.stat_result]:
    path = Path(__file__).absolute()
    before = path.lstat()
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise RuntimeError("CLI_START_METADATA_INVALID")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_nlink,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if identity != (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ):
            raise RuntimeError("CLI_START_PATH_FD_MISMATCH")
        payload = bytearray()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise RuntimeError("CLI_START_SHORT_READ")
            payload.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RuntimeError("CLI_START_GREW")
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after_path = path.lstat()
    if identity != (
        after_fd.st_dev,
        after_fd.st_ino,
        after_fd.st_mode,
        after_fd.st_nlink,
        after_fd.st_size,
        after_fd.st_mtime_ns,
        after_fd.st_ctime_ns,
    ) or identity != (
        after_path.st_dev,
        after_path.st_ino,
        after_path.st_mode,
        after_path.st_nlink,
        after_path.st_size,
        after_path.st_mtime_ns,
        after_path.st_ctime_ns,
    ):
        raise RuntimeError("CLI_START_PATH_CHANGED")
    return path.resolve(strict=True), bytes(payload), after_fd


_CLI_PATH, _CLI_PAYLOAD, _CLI_METADATA = _capture_cli_start()

from voice_coach.reproducibility.paper_draft_pdf import (  # noqa: E402
    CANONICAL_CLI,
    FileIdentity,
    FileRecord,
    FileSnapshot,
    PaperDraftPdfError,
    build_and_publish,
    config_only_summary,
    load_render_config,
    production_implementation_snapshot,
    validate_committed,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/paper/draft_pdf_v1.yaml")
CLI_START_SNAPSHOT = FileSnapshot(
    record=FileRecord(
        CANONICAL_CLI,
        hashlib.sha256(_CLI_PAYLOAD).hexdigest(),
        len(_CLI_PAYLOAD),
    ),
    identity=FileIdentity(
        device=_CLI_METADATA.st_dev,
        inode=_CLI_METADATA.st_ino,
        mode=_CLI_METADATA.st_mode,
        nlink=_CLI_METADATA.st_nlink,
        size_bytes=_CLI_METADATA.st_size,
        mtime_ns=_CLI_METADATA.st_mtime_ns,
        ctime_ns=_CLI_METADATA.st_ctime_ns,
    ),
    payload=_CLI_PAYLOAD,
    absolute_path=_CLI_PATH,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the offline A4 paper draft twice, normalize it "
            "deterministically, and publish a content-addressed generation."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--config-only",
        action="store_true",
        help="validate only config structure; access zero sources/tools/outputs",
    )
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="deep-validate the committed generation without LibreOffice/GS",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_render_config(
            args.config,
            workspace_root=args.workspace_root,
        )
        result: Mapping[str, Any]
        if args.config_only:
            result = config_only_summary(config)
        else:
            implementation = production_implementation_snapshot(
                config,
                cli_start_snapshot=CLI_START_SNAPSHOT,
            )
            if args.validate_only:
                result = validate_committed(
                    config,
                    implementation=implementation,
                )
            else:
                result = build_and_publish(
                    config,
                    implementation=implementation,
                )
    except PaperDraftPdfError as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": exc.code,
                    "detail": exc.detail,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except OSError:
        print(
            json.dumps(
                {"status": "failed", "error_code": "FILESYSTEM_ERROR"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
