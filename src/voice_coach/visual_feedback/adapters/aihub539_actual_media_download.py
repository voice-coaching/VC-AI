"""Filesystem and descriptor boundary for AI Hub 539 actual-media download."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat

from ..models.aihub539_actual_media_authorization import (
    AIHUB539_ACTUAL_MEDIA_DATASET_ID,
    AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS,
    Aihub539ActualMediaAuthorizationError,
    Aihub539ActualMediaAuthorizationReceipt,
)
from ..models.integrity import canonical_json_bytes


AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FILENAME = (
    "aihub539-actual-media-acquisition-authorization-v1.json"
)
_MAXIMUM_AUTHORIZATION_BYTES = 64 * 1024


class Aihub539ActualMediaDownloadAdapterError(RuntimeError):
    """Credential-free and restricted-payload-free adapter failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub539ActualMediaAuthorizationDescriptorIdentity:
    device: int
    inode: int
    size_bytes: int
    mode: int
    modified_time_ns: int
    changed_time_ns: int


@dataclass(frozen=True, slots=True)
class Aihub539ActualMediaOutputDirectoryIdentity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class Aihub539ActualMediaDownloadRequest:
    """Typed request passed from the secure downloader to this adapter."""

    dataset_key: str
    ordered_file_keys: tuple[str, ...]
    destination: Path
    authorization_descriptor: int

    def validate(self) -> None:
        if self.dataset_key != str(AIHUB539_ACTUAL_MEDIA_DATASET_ID):
            raise Aihub539ActualMediaDownloadAdapterError(
                "aihub539_actual_media_dataset_mismatch"
            )
        if (
            not isinstance(self.ordered_file_keys, tuple)
            or not all(
                isinstance(file_key, str)
                for file_key in self.ordered_file_keys
            )
        ):
            raise Aihub539ActualMediaDownloadAdapterError(
                "aihub539_actual_media_file_keys_invalid"
            )
        if self.ordered_file_keys != AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS:
            if (
                len(self.ordered_file_keys)
                == len(AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS)
                and set(self.ordered_file_keys)
                == set(AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS)
            ):
                reason_code = (
                    "aihub539_actual_media_file_key_order_invalid"
                )
            else:
                reason_code = "aihub539_actual_media_file_keys_invalid"
            raise Aihub539ActualMediaDownloadAdapterError(reason_code)
        if (
            not isinstance(self.destination, Path)
            or not self.destination.is_absolute()
            or isinstance(self.authorization_descriptor, bool)
            or not isinstance(self.authorization_descriptor, int)
            or self.authorization_descriptor < 3
        ):
            raise Aihub539ActualMediaDownloadAdapterError(
                "aihub539_actual_media_download_request_invalid"
            )


@dataclass(frozen=True, slots=True)
class Aihub539ActualMediaDownloadBinding:
    """Process-local binding held across all download network I/O."""

    request: Aihub539ActualMediaDownloadRequest
    authorization: Aihub539ActualMediaAuthorizationReceipt
    descriptor_identity: Aihub539ActualMediaAuthorizationDescriptorIdentity

    def assert_stable(self) -> None:
        try:
            assert_aihub539_actual_media_authorization_descriptor_identity(
                self.request.authorization_descriptor,
                self.descriptor_identity,
            )
            require_aihub539_actual_media_authorization_output_binding(
                self.authorization,
                self.request.destination,
            )
        except Aihub539ActualMediaDownloadAdapterError:
            raise Aihub539ActualMediaDownloadAdapterError(
                "aihub539_actual_media_authorization_changed"
            ) from None


def capture_aihub539_actual_media_authorization_descriptor_identity(
    descriptor: int,
) -> Aihub539ActualMediaAuthorizationDescriptorIdentity:
    """Require one owner-only immutable authorization descriptor."""

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
            or not 1 <= metadata.st_size <= _MAXIMUM_AUTHORIZATION_BYTES
        ):
            raise ValueError
        return Aihub539ActualMediaAuthorizationDescriptorIdentity(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size_bytes=metadata.st_size,
            mode=metadata.st_mode,
            modified_time_ns=metadata.st_mtime_ns,
            changed_time_ns=metadata.st_ctime_ns,
        )
    except (OSError, ValueError):
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_authorization_descriptor_invalid"
        ) from None


def assert_aihub539_actual_media_authorization_descriptor_identity(
    descriptor: int,
    expected: Aihub539ActualMediaAuthorizationDescriptorIdentity,
) -> None:
    if (
        capture_aihub539_actual_media_authorization_descriptor_identity(
            descriptor
        )
        != expected
    ):
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_authorization_descriptor_changed"
        )


def load_aihub539_actual_media_authorization_descriptor(
    descriptor: int,
) -> Aihub539ActualMediaAuthorizationReceipt:
    identity = (
        capture_aihub539_actual_media_authorization_descriptor_identity(
            descriptor
        )
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
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError()
            ),
        )
        authorization = (
            Aihub539ActualMediaAuthorizationReceipt.from_json_object(
                document
            )
        )
        assert_aihub539_actual_media_authorization_descriptor_identity(
            descriptor,
            identity,
        )
        return authorization
    except Exception:
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_authorization_reload_failed"
        ) from None


def lock_aihub539_actual_media_authorization_descriptor(
    descriptor: int,
) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_authorization_in_use"
        ) from None


def open_aihub539_actual_media_output_directory(
    output_directory: Path,
) -> tuple[int, Aihub539ActualMediaOutputDirectoryIdentity]:
    if (
        not isinstance(output_directory, Path)
        or not output_directory.is_absolute()
    ):
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_output_directory_invalid"
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
        return descriptor, Aihub539ActualMediaOutputDirectoryIdentity(
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )
    except (OSError, ValueError):
        if descriptor is not None:
            os.close(descriptor)
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_output_directory_invalid"
        ) from None


def require_aihub539_actual_media_authorization_output_binding(
    authorization: Aihub539ActualMediaAuthorizationReceipt,
    output_directory: Path,
) -> None:
    try:
        validated = Aihub539ActualMediaAuthorizationReceipt.from_json_object(
            authorization.to_json_object()
        )
    except (AttributeError, Aihub539ActualMediaAuthorizationError):
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_authorization_invalid"
        ) from None
    descriptor, identity = open_aihub539_actual_media_output_directory(
        output_directory
    )
    os.close(descriptor)
    if (
        validated.output_directory_device != identity.device
        or validated.output_directory_inode != identity.inode
    ):
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_output_binding_mismatch"
        )


def bind_aihub539_actual_media_download(
    request: Aihub539ActualMediaDownloadRequest,
) -> Aihub539ActualMediaDownloadBinding:
    """Validate, bind, and lock one exact acquisition request."""

    if not isinstance(request, Aihub539ActualMediaDownloadRequest):
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_download_request_invalid"
        )
    request.validate()
    identity = (
        capture_aihub539_actual_media_authorization_descriptor_identity(
            request.authorization_descriptor
        )
    )
    authorization = load_aihub539_actual_media_authorization_descriptor(
        request.authorization_descriptor
    )
    require_aihub539_actual_media_authorization_output_binding(
        authorization,
        request.destination,
    )
    lock_aihub539_actual_media_authorization_descriptor(
        request.authorization_descriptor
    )
    assert_aihub539_actual_media_authorization_descriptor_identity(
        request.authorization_descriptor,
        identity,
    )
    binding = Aihub539ActualMediaDownloadBinding(
        request=request,
        authorization=authorization,
        descriptor_identity=identity,
    )
    binding.assert_stable()
    return binding


def publish_aihub539_actual_media_authorization(
    *,
    authorization: Aihub539ActualMediaAuthorizationReceipt,
    output_directory: Path,
) -> Path:
    """Publish one mode-0400 receipt inside a new owner-only directory."""

    try:
        validated = Aihub539ActualMediaAuthorizationReceipt.from_json_object(
            authorization.to_json_object()
        )
    except Exception:
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_authorization_invalid"
        ) from None
    if (
        not isinstance(output_directory, Path)
        or not output_directory.is_absolute()
        or output_directory.name in {"", ".", ".."}
    ):
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_authorization_output_invalid"
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
            AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FILENAME,
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
            AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FILENAME,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        reloaded = load_aihub539_actual_media_authorization_descriptor(
            verification_descriptor
        )
        if reloaded != validated:
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
            / AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FILENAME
        )
    except Exception:
        if receipt_descriptor is not None:
            os.close(receipt_descriptor)
            receipt_descriptor = None
        if directory_descriptor is not None:
            try:
                os.unlink(
                    AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FILENAME,
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
                raise Aihub539ActualMediaDownloadAdapterError(
                    "aihub539_actual_media_authorization_cleanup_failed"
                ) from None
        raise Aihub539ActualMediaDownloadAdapterError(
            "aihub539_actual_media_authorization_publish_failed"
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
    "AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FILENAME",
    "Aihub539ActualMediaAuthorizationDescriptorIdentity",
    "Aihub539ActualMediaDownloadAdapterError",
    "Aihub539ActualMediaDownloadBinding",
    "Aihub539ActualMediaDownloadRequest",
    "Aihub539ActualMediaOutputDirectoryIdentity",
    "assert_aihub539_actual_media_authorization_descriptor_identity",
    "bind_aihub539_actual_media_download",
    "capture_aihub539_actual_media_authorization_descriptor_identity",
    "load_aihub539_actual_media_authorization_descriptor",
    "lock_aihub539_actual_media_authorization_descriptor",
    "open_aihub539_actual_media_output_directory",
    "publish_aihub539_actual_media_authorization",
    "require_aihub539_actual_media_authorization_output_binding",
]
