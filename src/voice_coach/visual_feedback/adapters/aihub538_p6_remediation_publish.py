"""Exclusive 0700/0400 persistence for P6 remediation ladder evidence."""

from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import stat

from ..models.aihub538_p6_remediation import (
    Aihub538P6RemediationAuditReceipt,
)
from ..models.integrity import canonical_json_bytes


AIHUB538_P6_REMEDIATION_RECEIPT_NAME = (
    "p6-photometric-remediation-label-ladder-v2.json"
)
_MAXIMUM_RECEIPT_BYTES = 2 * 1024 * 1024


class Aihub538P6RemediationPublishError(RuntimeError):
    """Path-free storage or strict-reload failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def publish_aihub538_p6_remediation_receipt(
    *,
    receipt: Aihub538P6RemediationAuditReceipt,
    output_directory: Path,
) -> Path:
    """Create one new private directory and immutable aggregate receipt."""

    try:
        receipt._validate()
    except (TypeError, ValueError):
        raise Aihub538P6RemediationPublishError(
            "aihub538_remediation_receipt_invalid"
        ) from None
    if (
        not output_directory.is_absolute()
        or output_directory.name in {"", ".", ".."}
    ):
        raise Aihub538P6RemediationPublishError(
            "aihub538_remediation_output_directory_invalid"
        )

    parent_descriptor: int | None = None
    directory_descriptor: int | None = None
    receipt_descriptor: int | None = None
    verification_descriptor: int | None = None
    directory_created = False
    receipt_created = False
    try:
        parent = output_directory.parent.resolve(strict=True)
        if parent != output_directory.parent:
            raise OSError
        expected_parent = os.stat(parent, follow_symlinks=False)
        parent_descriptor = os.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        _require_same_directory_identity(
            expected_parent,
            os.fstat(parent_descriptor),
        )
        os.mkdir(output_directory.name, 0o700, dir_fd=parent_descriptor)
        directory_created = True
        directory_descriptor = os.open(
            output_directory.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        os.fchmod(directory_descriptor, 0o700)
        created_entry = os.stat(
            output_directory.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        _require_same_directory_identity(
            created_entry,
            os.fstat(directory_descriptor),
        )
        receipt_descriptor = os.open(
            AIHUB538_P6_REMEDIATION_RECEIPT_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o400,
            dir_fd=directory_descriptor,
        )
        receipt_created = True
        os.fchmod(receipt_descriptor, 0o400)
        payload = canonical_json_bytes(receipt.to_json_object()) + b"\n"
        _write_all(receipt_descriptor, payload)
        os.fsync(receipt_descriptor)
        os.fsync(directory_descriptor)
        os.fsync(parent_descriptor)
        verification_descriptor = os.open(
            AIHUB538_P6_REMEDIATION_RECEIPT_NAME,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        if (
            load_aihub538_p6_remediation_receipt_descriptor(
                verification_descriptor
            )
            != receipt
        ):
            raise OSError
        _require_same_directory_identity(
            expected_parent,
            os.stat(parent, follow_symlinks=False),
        )
        _require_same_directory_identity(
            os.fstat(directory_descriptor),
            os.stat(
                output_directory.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            ),
        )
    except Exception:
        if receipt_descriptor is not None:
            os.close(receipt_descriptor)
            receipt_descriptor = None
        cleanup_failed = False
        if directory_descriptor is not None and receipt_created:
            try:
                os.unlink(
                    AIHUB538_P6_REMEDIATION_RECEIPT_NAME,
                    dir_fd=directory_descriptor,
                )
            except OSError:
                cleanup_failed = True
        if directory_descriptor is not None:
            os.close(directory_descriptor)
            directory_descriptor = None
        if parent_descriptor is not None and directory_created:
            try:
                os.rmdir(output_directory.name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise Aihub538P6RemediationPublishError(
                "aihub538_remediation_failure_cleanup_failed"
            ) from None
        raise Aihub538P6RemediationPublishError(
            "aihub538_remediation_publish_failed"
        ) from None
    finally:
        if verification_descriptor is not None:
            os.close(verification_descriptor)
        if receipt_descriptor is not None:
            os.close(receipt_descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
    return parent / output_directory.name / AIHUB538_P6_REMEDIATION_RECEIPT_NAME


def load_aihub538_p6_remediation_receipt_descriptor(
    descriptor: int,
) -> Aihub538P6RemediationAuditReceipt:
    """Strictly reload one immutable receipt from a caller-owned descriptor."""

    try:
        if (
            isinstance(descriptor, bool)
            or not isinstance(descriptor, int)
            or descriptor < 3
        ):
            raise ValueError
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_nlink != 1
            or not 1 <= before.st_size <= _MAXIMUM_RECEIPT_BYTES
            or (
                fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            )
            != os.O_RDONLY
        ):
            raise ValueError
        payload = bytearray()
        offset = 0
        while offset < before.st_size:
            chunk = os.pread(
                descriptor,
                min(64 * 1024, before.st_size - offset),
                offset,
            )
            if not chunk:
                raise ValueError
            payload.extend(chunk)
            offset += len(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_mode,
            before.st_nlink,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_mode,
            after.st_nlink,
        ):
            raise ValueError
        document = json.loads(
            bytes(payload).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        return Aihub538P6RemediationAuditReceipt.from_json_object(document)
    except Exception:
        raise Aihub538P6RemediationPublishError(
            "aihub538_remediation_receipt_reload_failed"
        ) from None


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError
        offset += written


def _reject_duplicate_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _require_same_directory_identity(
    expected: os.stat_result,
    observed: os.stat_result,
) -> None:
    if (
        not stat.S_ISDIR(expected.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or (expected.st_dev, expected.st_ino)
        != (observed.st_dev, observed.st_ino)
    ):
        raise OSError


__all__ = [
    "AIHUB538_P6_REMEDIATION_RECEIPT_NAME",
    "Aihub538P6RemediationPublishError",
    "load_aihub538_p6_remediation_receipt_descriptor",
    "publish_aihub538_p6_remediation_receipt",
]
