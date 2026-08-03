#!/usr/bin/env python3
"""Render and validate the aggregate-only AI Hub 71490 cohort-flow SVG."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
import sys


def _capture_cli_start() -> tuple[Path, bytes, os.stat_result]:
    path = Path(__file__).absolute()
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("CLI_START_NOT_REGULAR")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        descriptor_before = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            descriptor_before.st_dev,
            descriptor_before.st_ino,
            descriptor_before.st_mode,
            descriptor_before.st_nlink,
            descriptor_before.st_size,
            descriptor_before.st_mtime_ns,
            descriptor_before.st_ctime_ns,
        ):
            raise RuntimeError("CLI_START_PATH_FD_MISMATCH")
        payload = bytearray()
        remaining = descriptor_before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise RuntimeError("CLI_START_SHORT_READ")
            payload.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RuntimeError("CLI_START_GREW")
        descriptor_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = path.lstat()
    identity = (
        descriptor_before.st_dev,
        descriptor_before.st_ino,
        descriptor_before.st_mode,
        descriptor_before.st_nlink,
        descriptor_before.st_size,
        descriptor_before.st_mtime_ns,
        descriptor_before.st_ctime_ns,
    )
    if identity != (
        descriptor_after.st_dev,
        descriptor_after.st_ino,
        descriptor_after.st_mode,
        descriptor_after.st_nlink,
        descriptor_after.st_size,
        descriptor_after.st_mtime_ns,
        descriptor_after.st_ctime_ns,
    ) or identity != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise RuntimeError("CLI_START_PATH_CHANGED")
    return path.resolve(strict=True), bytes(payload), descriptor_after


_CLI_START_PATH, _CLI_START_PAYLOAD, _CLI_START_METADATA = _capture_cli_start()

from voice_coach.reproducibility.cohort_flow_svg import (  # noqa: E402
    CohortFlowValidationError,
    FileIdentity,
    FileRecord,
    FileSnapshot,
    config_summary,
    load_render_config,
    production_implementation_snapshot,
    render_and_publish,
    validate_published,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLI_START_SNAPSHOT = FileSnapshot(
    record=FileRecord(
        path="scripts/render_aihub71490_cohort_flow.py",
        sha256=hashlib.sha256(_CLI_START_PAYLOAD).hexdigest(),
        size_bytes=len(_CLI_START_PAYLOAD),
    ),
    identity=FileIdentity(
        device=_CLI_START_METADATA.st_dev,
        inode=_CLI_START_METADATA.st_ino,
        mode=_CLI_START_METADATA.st_mode,
        nlink=_CLI_START_METADATA.st_nlink,
        size_bytes=_CLI_START_METADATA.st_size,
        mtime_ns=_CLI_START_METADATA.st_mtime_ns,
        ctime_ns=_CLI_START_METADATA.st_ctime_ns,
    ),
    payload=_CLI_START_PAYLOAD,
    absolute_path=_CLI_START_PATH,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate aggregate evidence and deterministically render the "
            "AI Hub 71490 cohort flow without Mermaid runtime dependencies."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/paper/aihub71490_cohort_flow_v1.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--config-only",
        action="store_true",
        help="Validate only YAML structure; do not read sources or write outputs.",
    )
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="Recompute and byte-validate the existing SVG and receipt.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_render_config(
            args.config,
            workspace_root=args.workspace_root,
        )
        if args.config_only:
            result = config_summary(config)
        else:
            implementation = production_implementation_snapshot(
                config,
                cli_start_snapshot=CLI_START_SNAPSHOT,
            )
            if args.validate_only:
                result = validate_published(
                    config,
                    implementation=implementation,
                )
            else:
                result = render_and_publish(
                    config,
                    implementation=implementation,
                )
    except CohortFlowValidationError as exc:
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
