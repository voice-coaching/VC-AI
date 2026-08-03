#!/usr/bin/env python3
"""Verified bootstrap for the E01 synthetic duration worker receipt."""

from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from types import ModuleType
from typing import Any, Mapping, NoReturn, cast


CONFIG_RELATIVE_PATH = PurePosixPath(
    "configs/experiments/e01_duration_matched_control_v2.json"
)
APPLICATION_RELATIVE_PATH = PurePosixPath(
    "src/voice_coach/experiments/e01_duration_matched_control_receipt_v2.py"
)
SOURCE_RELATIVE_PATHS = (
    PurePosixPath("scripts/run_e01_duration_matched_control_receipt_v2.py"),
    PurePosixPath("scripts/run_e01_duration_matched_control_worker_v2.py"),
    APPLICATION_RELATIVE_PATH,
    PurePosixPath("src/voice_coach/evaluation/e01_duration_matched_control_v2.py"),
)
MAXIMUM_CONFIG_BYTES = 32_768
MAXIMUM_SOURCE_BYTES = 2_000_000


class BootstrapError(ValueError):
    """Raised before any worker or artifact write on bootstrap mismatch."""


class _SanitizedArgumentParser(argparse.ArgumentParser):
    """Route all parser failures to the fixed JSON failure response."""

    def error(self, message: str) -> NoReturn:
        raise BootstrapError("argument_contract")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise BootstrapError("argument_contract")


class _VerifiedBytesLoader(importlib.abc.Loader):
    def __init__(self, source: bytes, origin: str) -> None:
        self._source = source
        self._origin = origin

    def create_module(self, spec: object) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        code = compile(self._source, self._origin, "exec", dont_inherit=True)
        exec(code, module.__dict__)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _validated_root(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise BootstrapError("workspace_root_contract")
    try:
        information = value.lstat()
        resolved = value.resolve(strict=True)
    except (OSError, RuntimeError):
        raise BootstrapError("workspace_root_unavailable") from None
    if value.is_symlink() or resolved != value or not stat.S_ISDIR(information.st_mode):
        raise BootstrapError("workspace_root_identity")
    return value


def _read_workspace_file(
    root: Path,
    relative_path: PurePosixPath,
    *,
    maximum_bytes: int,
) -> bytes:
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise BootstrapError("relative_path_contract")
    descriptors: list[int] = []
    try:
        root_fd = os.open(
            root,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptors.append(root_fd)
        directory_fd = root_fd
        for component in relative_path.parts[:-1]:
            directory_fd = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            descriptors.append(directory_fd)
            if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
                raise BootstrapError("directory_identity")
        file_fd = os.open(
            relative_path.parts[-1],
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        descriptors.append(file_fd)
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
        ):
            raise BootstrapError("regular_file_contract")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(file_fd, min(remaining, 1 << 20))
            if not chunk:
                raise BootstrapError("regular_file_short_read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_fd, 1):
            raise BootstrapError("regular_file_growth")
        after = os.fstat(file_fd)
    except OSError:
        raise BootstrapError("workspace_file_unavailable") from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    if (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise BootstrapError("regular_file_changed")
    return b"".join(chunks)


def _verified_inputs(root: Path) -> tuple[bytes, bytes, str]:
    config_bytes = _read_workspace_file(
        root,
        CONFIG_RELATIVE_PATH,
        maximum_bytes=MAXIMUM_CONFIG_BYTES,
    )
    try:
        config = json.loads(config_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BootstrapError("config_json_contract") from None
    if type(config) is not dict or _canonical_json_bytes(config) != config_bytes:
        raise BootstrapError("config_canonical_contract")
    source_pins = cast(dict[str, object], config.get("source_sha256"))
    if type(source_pins) is not dict or set(source_pins) != {
        path.as_posix() for path in SOURCE_RELATIVE_PATHS
    }:
        raise BootstrapError("source_pin_contract")
    source_bytes: dict[str, bytes] = {}
    for relative_path in SOURCE_RELATIVE_PATHS:
        content = _read_workspace_file(
            root,
            relative_path,
            maximum_bytes=MAXIMUM_SOURCE_BYTES,
        )
        digest = hashlib.sha256(content).hexdigest()
        if source_pins[relative_path.as_posix()] != digest:
            raise BootstrapError("source_pin_mismatch")
        source_bytes[relative_path.as_posix()] = content
    application_source = source_bytes[APPLICATION_RELATIVE_PATH.as_posix()]
    application_sha256 = hashlib.sha256(application_source).hexdigest()
    return config_bytes, application_source, application_sha256


def _load_application(source: bytes, source_sha256: str) -> ModuleType:
    module_name = (
        f"_voice_coach_e01_duration_matched_control_receipt_v2_{source_sha256}"
    )
    loader = _VerifiedBytesLoader(source, APPLICATION_RELATIVE_PATH.as_posix())
    specification = importlib.util.spec_from_loader(
        module_name,
        loader,
        origin=APPLICATION_RELATIVE_PATH.as_posix(),
    )
    if specification is None:
        raise BootstrapError("application_import_contract")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise BootstrapError("application_import_contract") from None
    return module


def _build_parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(add_help=False)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--reviewed-wheel", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Verify all source pins, run one worker, and publish one receipt."""

    try:
        arguments = _build_parser().parse_args(argv)
        root = _validated_root(arguments.workspace_root)
        config_bytes, application_source, application_sha256 = _verified_inputs(root)
        application = _load_application(application_source, application_sha256)
        result = application.run_receipt(
            workspace_root=root,
            config_bytes=config_bytes,
            reviewed_wheel_path=arguments.reviewed_wheel,
        )
        print(_canonical_json_bytes(result).decode("utf-8"), end="")
        return 0
    except Exception:
        payload = {
            "status": "error",
            "failure_code": "matched_control_v2_runner_failure",
        }
        print(_canonical_json_bytes(payload).decode("utf-8"), end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
