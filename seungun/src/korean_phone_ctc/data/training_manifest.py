"""Fail-closed manifest loader for ordinary CTC training and dev evaluation."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

_ALLOWED_SOURCE_SPLITS = {
    "train": frozenset({"train", "stack", "calibration"}),
    "dev": frozenset({"dev"}),
}


@dataclass(frozen=True)
class TrainingManifestBundle:
    """Records available to ordinary training without any locked-test access."""

    train: tuple[dict[str, Any], ...]
    dev: tuple[dict[str, Any], ...]
    vocabulary: dict[str, int]


def _required_open_flag(name: str) -> int:
    flag = getattr(os, name, None)
    if not isinstance(flag, int):
        raise RuntimeError(f"platform lacks required safe-open flag: {name}")
    return flag


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | _required_open_flag("O_DIRECTORY")
        | _required_open_flag("O_NOFOLLOW")
    )


def _regular_file_open_flags() -> int:
    return os.O_RDONLY | _required_open_flag("O_NOFOLLOW")


def _absolute_lexical_path(path: Path) -> Path:
    """Make a path absolute without resolving or discarding link components."""

    absolute = path if path.is_absolute() else Path.cwd() / path
    if ".." in absolute.parts:
        raise ValueError("parent traversal is not allowed in manifest paths")
    return absolute


def _stable_file_signature(status: os.stat_result) -> tuple[int, ...]:
    """Capture fields that reveal replacement or mutation during one scan."""

    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _open_absolute_directory_without_symlinks(path: Path) -> int:
    """Open every absolute directory component with no symlink traversal."""

    if not path.is_absolute():
        raise ValueError("safe directory traversal requires an absolute path")
    directory_flags = _directory_open_flags()
    descriptor = os.open(os.sep, directory_flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=descriptor,
            )
            previous_descriptor = descriptor
            descriptor = next_descriptor
            os.close(previous_descriptor)
        return descriptor
    except OSError as error:
        os.close(descriptor)
        message = "cannot safely open workspace root"
        raise ValueError(message) from error


def _open_manifest_directory(
    manifest_dir: Path,
    *,
    workspace_root: Path,
) -> int:
    """Open a workspace-contained manifest directory without following links."""

    absolute_workspace = _absolute_lexical_path(workspace_root)
    absolute_manifest = _absolute_lexical_path(manifest_dir)
    try:
        relative_manifest = absolute_manifest.relative_to(absolute_workspace)
    except ValueError as error:
        raise ValueError("manifest directory is outside workspace root") from error

    descriptor = _open_absolute_directory_without_symlinks(absolute_workspace)
    directory_flags = _directory_open_flags()
    try:
        for component in relative_manifest.parts:
            next_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=descriptor,
            )
            previous_descriptor = descriptor
            descriptor = next_descriptor
            os.close(previous_descriptor)
        return descriptor
    except OSError as error:
        os.close(descriptor)
        raise ValueError("cannot safely open manifest directory") from error


def _open_regular_file_without_following_symlinks(
    directory_descriptor: int,
    file_name: str,
) -> TextIO:
    """Open one regular manifest relative to an already secured directory."""

    try:
        descriptor = os.open(
            file_name,
            _regular_file_open_flags(),
            dir_fd=directory_descriptor,
        )
    except OSError as error:
        message = f"cannot safely open reviewed manifest: {file_name}"
        raise ValueError(message) from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"reviewed manifest is not regular: {file_name}")
        return os.fdopen(descriptor, encoding="utf-8")
    except Exception:
        os.close(descriptor)
        raise


def _iter_reviewed_records(
    directory_descriptor: int,
    *,
    expected_split: str,
) -> Iterator[dict[str, Any]]:
    allowed_source_splits = _ALLOWED_SOURCE_SPLITS[expected_split]
    file_name = f"{expected_split}.jsonl"
    with _open_regular_file_without_following_symlinks(
        directory_descriptor,
        file_name,
    ) as stream:
        initial_signature = _stable_file_signature(os.fstat(stream.fileno()))
        for line_number, line in enumerate(stream, start=1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"non-object record in {file_name} at line {line_number}"
                )
            if value.get("split") != expected_split:
                raise ValueError(
                    f"unexpected split in {file_name} at line {line_number}"
                )
            if value.get("source_split") not in allowed_source_splits:
                raise ValueError(
                    f"unreviewed source split in {file_name} at line {line_number}"
                )
            yield value
        final_signature = _stable_file_signature(os.fstat(stream.fileno()))
        if final_signature != initial_signature:
            raise ValueError(f"reviewed manifest changed during scan: {file_name}")


def _read_reviewed_split(
    directory_descriptor: int,
    *,
    split: str,
    limit: int | None,
) -> tuple[dict[str, Any], ...]:
    if limit is not None and limit <= 0:
        raise ValueError("manifest limit must be positive")
    records: list[dict[str, Any]] = []
    for record in _iter_reviewed_records(
        directory_descriptor,
        expected_split=split,
    ):
        if limit is None or len(records) < limit:
            records.append(record)
    if not records:
        raise ValueError(f"reviewed {split} manifest is empty")
    return tuple(records)


def _read_training_vocabulary(directory_descriptor: int) -> dict[str, int]:
    file_name = "vocab.json"
    with _open_regular_file_without_following_symlinks(
        directory_descriptor,
        file_name,
    ) as stream:
        initial_signature = _stable_file_signature(os.fstat(stream.fileno()))
        value = json.load(stream)
        final_signature = _stable_file_signature(os.fstat(stream.fileno()))
    if final_signature != initial_signature:
        raise ValueError(f"reviewed manifest changed during scan: {file_name}")
    if not isinstance(value, dict) or not all(
        isinstance(token, str)
        and isinstance(token_id, int)
        and not isinstance(token_id, bool)
        for token, token_id in value.items()
    ):
        raise ValueError("training vocabulary must map strings to integer IDs")
    vocabulary = dict(value)
    if len(set(vocabulary.values())) != len(vocabulary):
        raise ValueError("training vocabulary IDs must be unique")
    return vocabulary


def load_training_and_dev_records(
    manifest_dir: Path,
    *,
    workspace_root: Path,
    train_limit: int | None,
    dev_limit: int | None,
) -> TrainingManifestBundle:
    """Load only training and reviewed-dev manifests.

    There is deliberately no split parameter and no locked-test branch. A final
    one-shot evaluator must use a separate protected execution boundary.
    """

    directory_descriptor = _open_manifest_directory(
        manifest_dir,
        workspace_root=workspace_root,
    )
    try:
        return TrainingManifestBundle(
            train=_read_reviewed_split(
                directory_descriptor,
                split="train",
                limit=train_limit,
            ),
            dev=_read_reviewed_split(
                directory_descriptor,
                split="dev",
                limit=dev_limit,
            ),
            vocabulary=_read_training_vocabulary(directory_descriptor),
        )
    finally:
        os.close(directory_descriptor)
