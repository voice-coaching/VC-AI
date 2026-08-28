"""Private key store for repeatable AI Hub 538 identity intersections.

The key is created once with ``O_EXCL`` in a canonical 0700 state directory,
stored as exactly 32 bytes with mode 0400, and fsynced with its directory.  The
key never appears in a receipt, exception, log message, or object repr.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import hashlib
import hmac
import os
from pathlib import Path
import stat


_KEY_FILE_NAME = "aihub538-identity-hmac-v1.key"
_KEY_BYTES = 32


class Aihub538IdentityKeyStoreError(RuntimeError):
    """Secret-free key-store failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538IdentityKeyMaterial:
    """Ephemeral in-memory key; repr intentionally suppresses key bytes."""

    key: bytes = field(repr=False)
    namespace_commitment_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, bytes) or len(self.key) != _KEY_BYTES:
            raise ValueError("aihub538_identity_key_material_invalid")


@dataclass(frozen=True, slots=True)
class Aihub538IdentityKeyStoreReceipt:
    """Non-secret storage receipt safe for aggregate logs."""

    status: str
    namespace_commitment_sha256: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_identity_key_store_receipt.v1",
            "status": self.status,
            "key_byte_count": _KEY_BYTES,
            "file_mode": "0400",
            "state_directory_mode": "0700",
            "namespace_commitment_sha256": self.namespace_commitment_sha256,
            "key_serialized": False,
            "fsync_completed": True,
        }


def create_aihub538_identity_key(
    *,
    state_directory: Path,
) -> tuple[Aihub538IdentityKeyMaterial, Aihub538IdentityKeyStoreReceipt]:
    """Create one new persisted key and fail if a prior key already exists."""

    directory_descriptor = _open_or_create_private_state_directory(state_directory)
    descriptor: int | None = None
    created = False
    key = os.urandom(_KEY_BYTES)
    try:
        try:
            descriptor = os.open(
                _KEY_FILE_NAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
                dir_fd=directory_descriptor,
            )
            created = True
        except OSError:
            raise Aihub538IdentityKeyStoreError(
                "aihub538_identity_key_already_exists_or_unwritable"
            ) from None
        os.fchmod(descriptor, 0o400)
        _write_all(descriptor, key)
        os.fsync(descriptor)
        os.fsync(directory_descriptor)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        if created:
            try:
                os.unlink(_KEY_FILE_NAME, dir_fd=directory_descriptor)
                os.fsync(directory_descriptor)
            except OSError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_descriptor)
    material = _material(key)
    return material, Aihub538IdentityKeyStoreReceipt(
        status="created",
        namespace_commitment_sha256=material.namespace_commitment_sha256,
    )


def load_aihub538_identity_key(
    *,
    state_directory: Path,
) -> tuple[Aihub538IdentityKeyMaterial, Aihub538IdentityKeyStoreReceipt]:
    """Reload the exact same private key for a later locked one-shot process."""

    directory_descriptor = _open_existing_private_state_directory(state_directory)
    try:
        try:
            descriptor = os.open(
                _KEY_FILE_NAME,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
        except OSError:
            raise Aihub538IdentityKeyStoreError(
                "aihub538_identity_key_missing_or_invalid"
            ) from None
        try:
            key = _read_exact_private_key_descriptor(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)
    material = _material(key)
    return material, Aihub538IdentityKeyStoreReceipt(
        status="loaded",
        namespace_commitment_sha256=material.namespace_commitment_sha256,
    )


def load_aihub538_identity_key_descriptor(
    descriptor: int,
) -> Aihub538IdentityKeyMaterial:
    """Descriptor-only loader for a caller that owns state-directory traversal."""

    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
        raise Aihub538IdentityKeyStoreError(
            "aihub538_identity_key_descriptor_invalid"
        )
    try:
        duplicate = os.dup(descriptor)
    except OSError:
        raise Aihub538IdentityKeyStoreError(
            "aihub538_identity_key_descriptor_invalid"
        ) from None
    try:
        return _material(_read_exact_private_key_descriptor(duplicate))
    finally:
        os.close(duplicate)


def _open_or_create_private_state_directory(path: Path) -> int:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise Aihub538IdentityKeyStoreError(
            "aihub538_identity_state_directory_invalid"
        )
    try:
        if not path.exists():
            if path.parent.resolve(strict=True) != path.parent:
                raise Aihub538IdentityKeyStoreError(
                    "aihub538_identity_state_directory_invalid"
                )
            parent = os.open(
                path.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                os.mkdir(path.name, 0o700, dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(parent)
        return _open_existing_private_state_directory(path)
    except Aihub538IdentityKeyStoreError:
        raise
    except OSError:
        raise Aihub538IdentityKeyStoreError(
            "aihub538_identity_state_directory_invalid"
        ) from None


def _open_existing_private_state_directory(path: Path) -> int:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise Aihub538IdentityKeyStoreError(
            "aihub538_identity_state_directory_invalid"
        )
    try:
        if path.resolve(strict=True) != path:
            raise Aihub538IdentityKeyStoreError(
                "aihub538_identity_state_directory_invalid"
            )
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
            os.close(descriptor)
            raise Aihub538IdentityKeyStoreError(
                "aihub538_identity_state_directory_mode_invalid"
            )
        return descriptor
    except Aihub538IdentityKeyStoreError:
        raise
    except OSError:
        raise Aihub538IdentityKeyStoreError(
            "aihub538_identity_state_directory_invalid"
        ) from None


def _read_exact_private_key_descriptor(descriptor: int) -> bytes:
    try:
        metadata = os.fstat(descriptor)
        access_mode = fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
        if (
            not stat.S_ISREG(metadata.st_mode)
            or access_mode != os.O_RDONLY
            or metadata.st_size != _KEY_BYTES
            or stat.S_IMODE(metadata.st_mode) != 0o400
        ):
            raise Aihub538IdentityKeyStoreError(
                "aihub538_identity_key_descriptor_invalid"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        key = os.read(descriptor, _KEY_BYTES + 1)
        if len(key) != _KEY_BYTES:
            raise Aihub538IdentityKeyStoreError(
                "aihub538_identity_key_descriptor_invalid"
            )
        return key
    except Aihub538IdentityKeyStoreError:
        raise
    except OSError:
        raise Aihub538IdentityKeyStoreError(
            "aihub538_identity_key_descriptor_invalid"
        ) from None


def _material(key: bytes) -> Aihub538IdentityKeyMaterial:
    return Aihub538IdentityKeyMaterial(
        key=key,
        namespace_commitment_sha256=hmac.new(
            key,
            b"voice_coach.aihub538.identity_namespace.v1",
            hashlib.sha256,
        ).hexdigest(),
    )


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError:
            raise Aihub538IdentityKeyStoreError(
                "aihub538_identity_key_write_failed"
            ) from None
        if written <= 0:
            raise Aihub538IdentityKeyStoreError(
                "aihub538_identity_key_write_failed"
            )
        view = view[written:]


__all__ = [
    "Aihub538IdentityKeyMaterial",
    "Aihub538IdentityKeyStoreError",
    "Aihub538IdentityKeyStoreReceipt",
    "create_aihub538_identity_key",
    "load_aihub538_identity_key",
    "load_aihub538_identity_key_descriptor",
]
