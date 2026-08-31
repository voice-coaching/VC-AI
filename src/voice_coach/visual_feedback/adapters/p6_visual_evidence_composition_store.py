"""Owner-only canonical persistence for P6 visual-evidence composition receipts."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import stat

from ..models.integrity import canonical_json_bytes
from ..models.p6_visual_evidence_composition import (
    P6VisualEvidenceCompositionReceipt,
)


_MAXIMUM_RECEIPT_BYTES = 256 * 1024


class P6VisualEvidenceCompositionStoreError(RuntimeError):
    """Path-free failure for immutable composition receipt I/O."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def write_p6_visual_evidence_composition_receipt(
    *,
    receipt: P6VisualEvidenceCompositionReceipt,
    output_path: Path,
) -> Path:
    """Write one previously absent canonical receipt as owner-only ``0400``."""

    parent_descriptor: int | None = None
    try:
        if (
            type(receipt) is not P6VisualEvidenceCompositionReceipt
            or not output_path.is_absolute()
            or output_path.name in {"", ".", ".."}
            or output_path.parent == output_path
        ):
            raise ValueError
        receipt._validate()
        parent = output_path.parent.resolve(strict=True)
        if parent != output_path.parent:
            raise ValueError
        parent_metadata = os.stat(parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        ):
            raise ValueError
        parent_descriptor = os.open(
            parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        opened_parent_metadata = os.fstat(parent_descriptor)
        if (
            not os.path.samestat(parent_metadata, opened_parent_metadata)
            or not stat.S_ISDIR(opened_parent_metadata.st_mode)
            or opened_parent_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(opened_parent_metadata.st_mode) != 0o700
        ):
            raise ValueError
        try:
            os.stat(
                output_path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise ValueError
    except (OSError, TypeError, ValueError):
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise P6VisualEvidenceCompositionStoreError(
            "p6_visual_evidence_composition_output_invalid"
        ) from None

    assert parent_descriptor is not None
    output_descriptor: int | None = None
    output_created = False
    try:
        output_descriptor = os.open(
            output_path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | os.O_NOFOLLOW,
            0o400,
            dir_fd=parent_descriptor,
        )
        output_created = True
        payload = canonical_json_bytes(receipt.to_json_object()) + b"\n"
        os.fchmod(output_descriptor, 0o400)
        _write_all(output_descriptor, payload)
        os.fsync(output_descriptor)
        output_metadata = os.fstat(output_descriptor)
        named_metadata = os.stat(
            output_path.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if not _is_owner_only_artifact(output_metadata) or not os.path.samestat(
            output_metadata, named_metadata
        ):
            raise OSError
        os.fsync(parent_descriptor)
        reload_descriptor = os.open(
            output_path.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        try:
            reloaded = load_p6_visual_evidence_composition_receipt_descriptor(
                reload_descriptor
            )
            if reloaded != receipt or not os.path.samestat(
                os.fstat(reload_descriptor), output_metadata
            ):
                raise OSError
        finally:
            os.close(reload_descriptor)
    except Exception:
        if output_created and output_descriptor is not None:
            try:
                created_metadata = os.fstat(output_descriptor)
                named_metadata = os.stat(
                    output_path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if os.path.samestat(created_metadata, named_metadata):
                    os.unlink(output_path.name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
            except OSError:
                pass
        raise P6VisualEvidenceCompositionStoreError(
            "p6_visual_evidence_composition_write_failed"
        ) from None
    finally:
        if output_descriptor is not None:
            os.close(output_descriptor)
        os.close(parent_descriptor)
    return output_path


def load_p6_visual_evidence_composition_receipt_descriptor(
    descriptor: int,
) -> P6VisualEvidenceCompositionReceipt:
    """Reload only a stable owner-owned ``0400`` canonical JSON descriptor."""

    try:
        if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
            raise ValueError
        before = os.fstat(descriptor)
        if (
            not _is_owner_only_artifact(before)
            or not 1 <= before.st_size <= _MAXIMUM_RECEIPT_BYTES
            or (fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE)
            != os.O_RDONLY
        ):
            raise ValueError
        payload = _read_all(descriptor, before.st_size)
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if payload != canonical_json_bytes(document) + b"\n":
            raise ValueError
        receipt = P6VisualEvidenceCompositionReceipt.from_json_object(document)
        if _stat_identity(os.fstat(descriptor)) != _stat_identity(before):
            raise ValueError
        return receipt
    except Exception:
        raise P6VisualEvidenceCompositionStoreError(
            "p6_visual_evidence_composition_receipt_reload_failed"
        ) from None


def _is_owner_only_artifact(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) == 0o400
        and metadata.st_nlink == 1
    )


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_all(descriptor: int, size: int) -> bytes:
    payload = bytearray()
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(64 * 1024, size - offset), offset)
        if not chunk:
            raise ValueError
        payload.extend(chunk)
        offset += len(chunk)
    return bytes(payload)


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


__all__ = [
    "P6VisualEvidenceCompositionStoreError",
    "load_p6_visual_evidence_composition_receipt_descriptor",
    "write_p6_visual_evidence_composition_receipt",
]
