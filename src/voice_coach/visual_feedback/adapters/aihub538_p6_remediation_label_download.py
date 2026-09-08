"""Descriptor boundary for a staged P6 remediation label download."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat

from ..models.aihub538_p6_remediation_label_download import (
    Aihub538P6RemediationLabelDownloadTransaction,
)
from ..models.integrity import canonical_json_bytes


AIHUB538_P6_REMEDIATION_LABEL_TRANSACTION_NAME = (
    "p6-remediation-label-download-transaction-v1.json"
)
_MAXIMUM_TRANSACTION_BYTES = 256 * 1024


class Aihub538P6RemediationLabelDownloadAdapterError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538P6RemediationLabelDescriptorIdentity:
    device: int
    inode: int
    size_bytes: int
    mode: int
    modified_time_ns: int
    changed_time_ns: int


@dataclass(frozen=True, slots=True)
class Aihub538P6RemediationLabelOutputIdentity:
    device: int
    inode: int


def capture_aihub538_p6_remediation_label_descriptor_identity(
    descriptor: int,
) -> Aihub538P6RemediationLabelDescriptorIdentity:
    try:
        if (
            isinstance(descriptor, bool)
            or not isinstance(descriptor, int)
            or descriptor < 3
        ):
            raise ValueError
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        metadata = os.fstat(descriptor)
        if (
            flags & os.O_ACCMODE != os.O_RDONLY
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or not 1 <= metadata.st_size <= _MAXIMUM_TRANSACTION_BYTES
        ):
            raise ValueError
        return Aihub538P6RemediationLabelDescriptorIdentity(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size_bytes=metadata.st_size,
            mode=metadata.st_mode,
            modified_time_ns=metadata.st_mtime_ns,
            changed_time_ns=metadata.st_ctime_ns,
        )
    except (OSError, ValueError):
        raise Aihub538P6RemediationLabelDownloadAdapterError(
            "aihub538_remediation_label_transaction_descriptor_invalid"
        ) from None


def assert_aihub538_p6_remediation_label_descriptor_identity(
    descriptor: int,
    expected: Aihub538P6RemediationLabelDescriptorIdentity,
) -> None:
    if (
        capture_aihub538_p6_remediation_label_descriptor_identity(descriptor)
        != expected
    ):
        raise Aihub538P6RemediationLabelDownloadAdapterError(
            "aihub538_remediation_label_transaction_descriptor_changed"
        )


def load_aihub538_p6_remediation_label_transaction_descriptor(
    descriptor: int,
) -> Aihub538P6RemediationLabelDownloadTransaction:
    identity = capture_aihub538_p6_remediation_label_descriptor_identity(
        descriptor
    )
    try:
        payload = bytearray()
        offset = 0
        while offset < identity.size_bytes:
            chunk = os.pread(
                descriptor,
                min(64 * 1024, identity.size_bytes - offset),
                offset,
            )
            if not chunk:
                raise ValueError
            payload.extend(chunk)
            offset += len(chunk)
        document = json.loads(
            bytes(payload).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        transaction = (
            Aihub538P6RemediationLabelDownloadTransaction.from_json_object(
                document
            )
        )
        assert_aihub538_p6_remediation_label_descriptor_identity(
            descriptor,
            identity,
        )
        return transaction
    except Exception:
        raise Aihub538P6RemediationLabelDownloadAdapterError(
            "aihub538_remediation_label_transaction_reload_failed"
        ) from None


def lock_aihub538_p6_remediation_label_transaction_descriptor(
    descriptor: int,
) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise Aihub538P6RemediationLabelDownloadAdapterError(
            "aihub538_remediation_label_transaction_in_use"
        ) from None


def open_aihub538_p6_remediation_label_output_directory(
    output_directory: Path,
) -> tuple[int, Aihub538P6RemediationLabelOutputIdentity]:
    if not isinstance(output_directory, Path) or not output_directory.is_absolute():
        raise Aihub538P6RemediationLabelDownloadAdapterError(
            "aihub538_remediation_label_output_directory_invalid"
        )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            output_directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        metadata = os.fstat(descriptor)
        named = os.stat(output_directory, follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
            or (metadata.st_dev, metadata.st_ino)
            != (named.st_dev, named.st_ino)
        ):
            raise ValueError
        return descriptor, Aihub538P6RemediationLabelOutputIdentity(
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )
    except (OSError, ValueError):
        if descriptor is not None:
            os.close(descriptor)
        raise Aihub538P6RemediationLabelDownloadAdapterError(
            "aihub538_remediation_label_output_directory_invalid"
        ) from None


def require_aihub538_p6_remediation_label_transaction_output_binding(
    transaction: Aihub538P6RemediationLabelDownloadTransaction,
    output_directory: Path,
) -> None:
    descriptor, identity = (
        open_aihub538_p6_remediation_label_output_directory(output_directory)
    )
    os.close(descriptor)
    if (
        transaction.output_directory_device != identity.device
        or transaction.output_directory_inode != identity.inode
    ):
        raise Aihub538P6RemediationLabelDownloadAdapterError(
            "aihub538_remediation_label_output_binding_mismatch"
        )


def publish_aihub538_p6_remediation_label_transaction(
    *,
    transaction: Aihub538P6RemediationLabelDownloadTransaction,
    output_directory: Path,
) -> Path:
    """Publish into a new directory and retain parent/child inode binding."""

    try:
        validated = Aihub538P6RemediationLabelDownloadTransaction.from_json_object(
            transaction.to_json_object()
        )
    except Exception:
        raise Aihub538P6RemediationLabelDownloadAdapterError(
            "aihub538_remediation_label_transaction_invalid"
        ) from None
    if (
        not isinstance(output_directory, Path)
        or not output_directory.is_absolute()
        or output_directory.name in {"", ".", ".."}
    ):
        raise Aihub538P6RemediationLabelDownloadAdapterError(
            "aihub538_remediation_label_transaction_output_invalid"
        )
    parent_descriptor: int | None = None
    directory_descriptor: int | None = None
    receipt_descriptor: int | None = None
    verification_descriptor: int | None = None
    created = False
    try:
        canonical_parent = output_directory.parent.resolve(strict=True)
        if canonical_parent != output_directory.parent:
            raise OSError
        expected_parent = os.stat(canonical_parent, follow_symlinks=False)
        parent_descriptor = os.open(
            canonical_parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        _require_same_directory(expected_parent, os.fstat(parent_descriptor))
        os.mkdir(output_directory.name, 0o700, dir_fd=parent_descriptor)
        created = True
        directory_descriptor = os.open(
            output_directory.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        os.fchmod(directory_descriptor, 0o700)
        _require_same_directory(
            os.fstat(directory_descriptor),
            os.stat(
                output_directory.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            ),
        )
        receipt_descriptor = os.open(
            AIHUB538_P6_REMEDIATION_LABEL_TRANSACTION_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o400,
            dir_fd=directory_descriptor,
        )
        payload = canonical_json_bytes(validated.to_json_object()) + b"\n"
        _write_all(receipt_descriptor, payload)
        os.fchmod(receipt_descriptor, 0o400)
        os.fsync(receipt_descriptor)
        os.fsync(directory_descriptor)
        os.fsync(parent_descriptor)
        verification_descriptor = os.open(
            AIHUB538_P6_REMEDIATION_LABEL_TRANSACTION_NAME,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        if (
            load_aihub538_p6_remediation_label_transaction_descriptor(
                verification_descriptor
            )
            != validated
        ):
            raise OSError
        _require_same_directory(
            expected_parent,
            os.stat(canonical_parent, follow_symlinks=False),
        )
        _require_same_directory(
            os.fstat(directory_descriptor),
            os.stat(
                output_directory.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            ),
        )
        return (
            canonical_parent
            / output_directory.name
            / AIHUB538_P6_REMEDIATION_LABEL_TRANSACTION_NAME
        )
    except Exception:
        if receipt_descriptor is not None:
            os.close(receipt_descriptor)
            receipt_descriptor = None
        if directory_descriptor is not None:
            try:
                os.unlink(
                    AIHUB538_P6_REMEDIATION_LABEL_TRANSACTION_NAME,
                    dir_fd=directory_descriptor,
                )
            except OSError:
                pass
            os.close(directory_descriptor)
            directory_descriptor = None
        if parent_descriptor is not None and created:
            try:
                os.rmdir(output_directory.name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError:
                raise Aihub538P6RemediationLabelDownloadAdapterError(
                    "aihub538_remediation_label_transaction_cleanup_failed"
                ) from None
        raise Aihub538P6RemediationLabelDownloadAdapterError(
            "aihub538_remediation_label_transaction_publish_failed"
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


def _reject_duplicate_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError
        offset += written


def _require_same_directory(
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
    "AIHUB538_P6_REMEDIATION_LABEL_TRANSACTION_NAME",
    "Aihub538P6RemediationLabelDownloadAdapterError",
    "Aihub538P6RemediationLabelDescriptorIdentity",
    "Aihub538P6RemediationLabelOutputIdentity",
    "assert_aihub538_p6_remediation_label_descriptor_identity",
    "capture_aihub538_p6_remediation_label_descriptor_identity",
    "load_aihub538_p6_remediation_label_transaction_descriptor",
    "lock_aihub538_p6_remediation_label_transaction_descriptor",
    "open_aihub538_p6_remediation_label_output_directory",
    "publish_aihub538_p6_remediation_label_transaction",
    "require_aihub538_p6_remediation_label_transaction_output_binding",
]
