"""Descriptor and filesystem boundary for the P8 all-label download."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
from typing import Any

from ..errors import VisualFeedbackContractError
from ..models.integrity import canonical_json_bytes
from ..models.p8_locked_cohort import P8LockedCohortDiscoverySeal
from ..models.p8_locked_label_download import (
    P8LockedLabelDownloadCompletion,
    P8LockedLabelDownloadError,
    P8LockedLabelDownloadTransaction,
    P8LockedLabelStateRootSeal,
    p8_locked_reference_commitments_root_sha256,
)
from ..models.p8_actual_source_state import P8ActualSourceReadZeroSeal
from ..models.p8_locked_evaluation import (
    load_p8_state_bound_read_zero_document,
)


P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_FILENAME = (
    "p8_locked_label_download_completion.json"
)
_P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_PENDING_FILENAME = (
    "p8_locked_label_download_completion.json.pending"
)
_MAXIMUM_TYPED_RECEIPT_BYTES = 256 * 1024
_P8_BASE_SEAL_FILENAME = "p8_read_zero_seal.json"
_P8_ACTUAL_SOURCE_SEAL_FILENAME = "p8_actual_source_read_zero_seal.json"
_P8_DISCOVERY_SEAL_FILENAME = "p8_locked_cohort_discovery_seal.json"
_P8_LABEL_TRANSACTION_FILENAME = "p8_locked_label_download_transaction.json"
_P8_LABEL_STATE_ROOT_SEAL_FILENAME = "p8_locked_label_state_root_seal.json"
_P8_SOURCE_ACCESS_TRANSACTION_FILENAME = "p8_access_transaction.json"


class P8LockedLabelDownloadAdapterError(RuntimeError):
    """Credential-free, path-free adapter failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class P8LockedLabelDescriptorIdentity:
    device: int
    inode: int
    size_bytes: int
    mode: int
    modified_time_ns: int
    changed_time_ns: int


@dataclass(frozen=True, slots=True)
class P8LockedLabelOutputDirectoryIdentity:
    device: int
    inode: int


def capture_p8_locked_label_descriptor_identity(
    descriptor: int,
    *,
    require_mode: int = 0o400,
) -> P8LockedLabelDescriptorIdentity:
    """Capture one non-empty, caller-owned, read-only regular descriptor."""

    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_authorization_descriptor_invalid"
        )
    try:
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        metadata = os.fstat(descriptor)
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_authorization_descriptor_invalid"
        ) from None
    if (
        flags & os.O_ACCMODE != os.O_RDONLY
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != require_mode
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > _MAXIMUM_TYPED_RECEIPT_BYTES
    ):
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_authorization_descriptor_invalid"
        )
    return P8LockedLabelDescriptorIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size_bytes=metadata.st_size,
        mode=metadata.st_mode,
        modified_time_ns=metadata.st_mtime_ns,
        changed_time_ns=metadata.st_ctime_ns,
    )


def assert_p8_locked_label_descriptor_identity(
    descriptor: int,
    expected: P8LockedLabelDescriptorIdentity,
) -> None:
    if capture_p8_locked_label_descriptor_identity(descriptor) != expected:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_authorization_descriptor_changed"
        )


def load_p8_locked_cohort_discovery_seal_descriptor(
    descriptor: int,
) -> P8LockedCohortDiscoverySeal:
    identity = capture_p8_locked_label_descriptor_identity(descriptor)
    document = _read_strict_json_descriptor(descriptor, identity)
    try:
        seal = P8LockedCohortDiscoverySeal.from_json_object(document)
    except VisualFeedbackContractError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_discovery_seal_invalid"
        ) from None
    assert_p8_locked_label_descriptor_identity(descriptor, identity)
    return seal


def load_p8_locked_label_download_transaction_descriptor(
    descriptor: int,
) -> P8LockedLabelDownloadTransaction:
    identity = capture_p8_locked_label_descriptor_identity(descriptor)
    document = _read_strict_json_descriptor(descriptor, identity)
    try:
        transaction = P8LockedLabelDownloadTransaction.from_json_object(
            document
        )
    except (P8LockedLabelDownloadError, VisualFeedbackContractError):
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_download_transaction_invalid"
        ) from None
    assert_p8_locked_label_descriptor_identity(descriptor, identity)
    return transaction


def lock_p8_locked_label_download_transaction_descriptor(
    descriptor: int,
) -> None:
    """Hold one non-blocking process lock for the request or exact resume."""

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_download_transaction_in_use"
        ) from None


def lock_p8_locked_label_state_directory(descriptor: int) -> None:
    """Hold the common state-root lock across label authorization and download."""

    _require_private_state_directory_descriptor(descriptor)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_state_directory_in_use"
        ) from None


def open_p8_locked_label_state_directory(state_directory: Path) -> int:
    """Open the canonical state directory for inherited lineage validation."""

    if not isinstance(state_directory, Path) or not state_directory.is_absolute():
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_state_directory_invalid"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(state_directory, flags)
        _require_private_state_directory_descriptor(descriptor)
        return descriptor
    except (OSError, P8LockedLabelDownloadAdapterError):
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_state_directory_invalid"
        ) from None


def require_p8_locked_label_transaction_state_binding(
    *,
    state_directory_descriptor: int,
    transaction_descriptor: int,
) -> P8LockedLabelDownloadTransaction:
    """Reload the durable base/actual/discovery/transaction lineage by dirfd."""

    state_before = _require_private_state_directory_descriptor(
        state_directory_descriptor
    )
    transaction_identity = capture_p8_locked_label_descriptor_identity(
        transaction_descriptor
    )
    named_transaction_descriptor = _open_state_json_at(
        state_directory_descriptor,
        _P8_LABEL_TRANSACTION_FILENAME,
    )
    try:
        named_identity = capture_p8_locked_label_descriptor_identity(
            named_transaction_descriptor
        )
        if named_identity != transaction_identity:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_transaction_not_durable_state_member"
            )
        transaction = load_p8_locked_label_download_transaction_descriptor(
            named_transaction_descriptor
        )
    finally:
        os.close(named_transaction_descriptor)
    state_descriptors: list[int] = []
    try:
        base_descriptor = _open_state_json_at(
            state_directory_descriptor,
            _P8_BASE_SEAL_FILENAME,
        )
        state_descriptors.append(base_descriptor)
        actual_descriptor = _open_state_json_at(
            state_directory_descriptor,
            _P8_ACTUAL_SOURCE_SEAL_FILENAME,
        )
        state_descriptors.append(actual_descriptor)
        discovery_descriptor = _open_state_json_at(
            state_directory_descriptor,
            _P8_DISCOVERY_SEAL_FILENAME,
        )
        state_descriptors.append(discovery_descriptor)
        state_root_descriptor = _open_state_json_at(
            state_directory_descriptor,
            _P8_LABEL_STATE_ROOT_SEAL_FILENAME,
        )
        state_descriptors.append(state_root_descriptor)
        try:
            base = load_p8_state_bound_read_zero_document(
                _read_strict_json_descriptor(
                    base_descriptor,
                    capture_p8_locked_label_descriptor_identity(
                        base_descriptor
                    ),
                ),
                state_directory_device=state_before[0],
                state_directory_inode=state_before[1],
            )
            actual = P8ActualSourceReadZeroSeal.from_json_object(
                _read_strict_json_descriptor(
                    actual_descriptor,
                    capture_p8_locked_label_descriptor_identity(
                        actual_descriptor
                    ),
                )
            )
            discovery = P8LockedCohortDiscoverySeal.from_json_object(
                _read_strict_json_descriptor(
                    discovery_descriptor,
                    capture_p8_locked_label_descriptor_identity(
                        discovery_descriptor
                    ),
                )
            )
            state_root = P8LockedLabelStateRootSeal.from_json_object(
                _read_strict_json_descriptor(
                    state_root_descriptor,
                    capture_p8_locked_label_descriptor_identity(
                        state_root_descriptor
                    ),
                )
            )
        except VisualFeedbackContractError:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_state_lineage_invalid"
            ) from None
    finally:
        for state_descriptor in reversed(state_descriptors):
            os.close(state_descriptor)
    if (
        actual.base_read_zero_seal_sha256 != base.seal_sha256
        or actual.p6_evidence_receipt_sha256
        != base.p6_evidence_receipt_sha256
        or discovery.reference_identity_set_commitments_sha256
        != p8_locked_reference_commitments_root_sha256(actual)
        or discovery.state_directory_device != state_before[0]
        or discovery.state_directory_inode != state_before[1]
        or transaction.actual_source_seal_sha256
        != actual.actual_source_seal_sha256
        or transaction.discovery_seal_sha256 != discovery.seal_sha256
        or transaction.reference_identity_set_commitments_sha256
        != discovery.reference_identity_set_commitments_sha256
        or state_root.base_read_zero_seal_sha256 != base.seal_sha256
        or state_root.actual_source_seal_sha256
        != actual.actual_source_seal_sha256
        or state_root.discovery_seal_sha256 != discovery.seal_sha256
        or state_root.state_directory_device != state_before[0]
        or state_root.state_directory_inode != state_before[1]
        or transaction.state_root_seal_sha256
        != state_root.state_root_seal_sha256
        or transaction.state_directory_device != state_before[0]
        or transaction.state_directory_inode != state_before[1]
    ):
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_state_lineage_invalid"
        )
    try:
        os.stat(
            _P8_SOURCE_ACCESS_TRANSACTION_FILENAME,
            dir_fd=state_directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        pass
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_state_inspection_failed"
        ) from None
    else:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_after_source_consumption_forbidden"
        )
    if (
        _require_private_state_directory_descriptor(
            state_directory_descriptor
        )
        != state_before
    ):
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_state_directory_changed"
        )
    assert_p8_locked_label_descriptor_identity(
        transaction_descriptor,
        transaction_identity,
    )
    return transaction


def open_p8_locked_label_output_directory(
    output_directory: Path,
) -> tuple[int, P8LockedLabelOutputDirectoryIdentity]:
    """Open a preexisting owner-only directory without following its leaf."""

    if not isinstance(output_directory, Path) or not output_directory.is_absolute():
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_directory_invalid"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(output_directory, flags)
        metadata = os.fstat(descriptor)
    except OSError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_directory_invalid"
        ) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        os.close(descriptor)
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_directory_permissions_invalid"
        )
    return descriptor, P8LockedLabelOutputDirectoryIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )


def require_p8_locked_label_transaction_output_binding(
    transaction: P8LockedLabelDownloadTransaction,
    output_directory: Path,
) -> None:
    descriptor, identity = open_p8_locked_label_output_directory(
        output_directory
    )
    os.close(descriptor)
    if (
        transaction.output_directory_device != identity.device
        or transaction.output_directory_inode != identity.inode
    ):
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_download_output_binding_mismatch"
        )


def open_p8_locked_label_output_member(
    directory_descriptor: int,
    filename: str,
    *,
    require_mode: int,
) -> int:
    """Open one fixed output member read-only and no-follow."""

    if filename not in {"download.tar", "download.receipt.json"}:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_member_invalid"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
        metadata = os.fstat(descriptor)
    except OSError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_member_invalid"
        ) from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != require_mode
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
    ):
        os.close(descriptor)
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_member_permissions_invalid"
        )
    return descriptor


def reconcile_p8_locked_label_pending_publications(
    directory_descriptor: int,
) -> None:
    """Remove only a verified same-inode pending alias left after publication."""

    pairs = (
        ("download.receipt.json.pending", "download.receipt.json"),
        (
            _P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_PENDING_FILENAME,
            P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_FILENAME,
        ),
    )
    for pending_filename, final_filename in pairs:
        pending = _stat_optional_at(directory_descriptor, pending_filename)
        final = _stat_optional_at(directory_descriptor, final_filename)
        if pending is None or final is None:
            continue
        if (
            not stat.S_ISREG(pending.st_mode)
            or not stat.S_ISREG(final.st_mode)
            or stat.S_IMODE(pending.st_mode) != 0o400
            or stat.S_IMODE(final.st_mode) != 0o400
            or pending.st_uid != os.geteuid()
            or final.st_uid != os.geteuid()
            or pending.st_nlink != 2
            or final.st_nlink != 2
            or (pending.st_dev, pending.st_ino, pending.st_size)
            != (final.st_dev, final.st_ino, final.st_size)
        ):
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_pending_publication_mismatch"
            )
        try:
            os.unlink(pending_filename, dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
        except OSError:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_pending_publication_cleanup_failed"
            ) from None


def publish_p8_locked_label_download_completion(
    *,
    directory_descriptor: int,
    completion: P8LockedLabelDownloadCompletion,
) -> P8LockedLabelDownloadCompletion:
    """Publish a fully fsynced aggregate terminal without a partial final path."""

    if not isinstance(completion, P8LockedLabelDownloadCompletion):
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_download_completion_invalid"
        )
    payload = canonical_json_bytes(completion.to_json_object()) + b"\n"
    if len(payload) > _MAXIMUM_TYPED_RECEIPT_BYTES:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_download_completion_too_large"
        )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    pending_already_exists = False
    try:
        descriptor = os.open(
            _P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_PENDING_FILENAME,
            flags,
            0o600,
            dir_fd=directory_descriptor,
        )
    except FileExistsError:
        if _remove_abandoned_pending_at(
            directory_descriptor,
            pending_filename=(
                _P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_PENDING_FILENAME
            ),
            final_filename=P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_FILENAME,
        ):
            try:
                descriptor = os.open(
                    _P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_PENDING_FILENAME,
                    flags,
                    0o600,
                    dir_fd=directory_descriptor,
                )
            except OSError:
                raise P8LockedLabelDownloadAdapterError(
                    "p8_locked_label_download_completion_create_failed"
                ) from None
        else:
            pending_already_exists = True
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_download_completion_create_failed"
        ) from None
    if not pending_already_exists:
        assert descriptor is not None
        try:
            _write_all(descriptor, payload)
            os.fchmod(descriptor, 0o400)
            os.fsync(descriptor)
        except OSError:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_download_completion_write_failed"
            ) from None
        finally:
            os.close(descriptor)
    pending_descriptor: int | None = None
    try:
        pending_descriptor = _open_state_json_at(
            directory_descriptor,
            _P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_PENDING_FILENAME,
        )
        pending_identity = capture_p8_locked_label_descriptor_identity(
            pending_descriptor
        )
        pending = P8LockedLabelDownloadCompletion.from_json_object(
            _read_strict_json_descriptor(
                pending_descriptor,
                pending_identity,
            )
        )
        if (
            pending.transaction_sha256 != completion.transaction_sha256
            or pending.discovery_seal_sha256
            != completion.discovery_seal_sha256
            or pending.request_sha256 != completion.request_sha256
            or pending.archive_size_bytes != completion.archive_size_bytes
            or pending.archive_sha256 != completion.archive_sha256
            or pending.secure_download_receipt_sha256
            != completion.secure_download_receipt_sha256
        ):
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_download_completion_pending_mismatch"
            )
        os.fsync(pending_descriptor)
        assert_p8_locked_label_descriptor_identity(
            pending_descriptor,
            pending_identity,
        )
        _assert_pending_publication_descriptor_binding(
            directory_descriptor,
            pending_filename=(
                _P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_PENDING_FILENAME
            ),
            final_filename=P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_FILENAME,
            pending_descriptor=pending_descriptor,
            linked=False,
        )
        os.link(
            _P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_PENDING_FILENAME,
            P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_FILENAME,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        _assert_pending_publication_descriptor_binding(
            directory_descriptor,
            pending_filename=(
                _P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_PENDING_FILENAME
            ),
            final_filename=P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_FILENAME,
            pending_descriptor=pending_descriptor,
            linked=True,
        )
        os.fsync(directory_descriptor)
        os.unlink(
            _P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_PENDING_FILENAME,
            dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
        return pending
    except FileExistsError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_download_completion_already_exists"
        ) from None
    except (OSError, P8LockedLabelDownloadError, VisualFeedbackContractError):
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_download_completion_publish_failed"
        ) from None
    finally:
        if pending_descriptor is not None:
            os.close(pending_descriptor)


def load_p8_locked_label_download_completion_at(
    directory_descriptor: int,
) -> P8LockedLabelDownloadCompletion:
    descriptor = _open_state_json_at(
        directory_descriptor,
        P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_FILENAME,
    )
    try:
        identity = capture_p8_locked_label_descriptor_identity(descriptor)
        document = _read_strict_json_descriptor(descriptor, identity)
        try:
            completion = P8LockedLabelDownloadCompletion.from_json_object(
                document
            )
        except (P8LockedLabelDownloadError, VisualFeedbackContractError):
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_download_completion_invalid"
            ) from None
        assert_p8_locked_label_descriptor_identity(descriptor, identity)
        return completion
    finally:
        os.close(descriptor)


def _require_private_state_directory_descriptor(
    descriptor: int,
) -> tuple[int, int, int, int, int]:
    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_state_directory_descriptor_invalid"
        )
    try:
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        metadata = os.fstat(descriptor)
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_state_directory_descriptor_invalid"
        ) from None
    if (
        flags & os.O_ACCMODE != os.O_RDONLY
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_state_directory_descriptor_invalid"
        )
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_state_json_at(directory_descriptor: int, filename: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
        capture_p8_locked_label_descriptor_identity(descriptor)
        return descriptor
    except (OSError, P8LockedLabelDownloadAdapterError):
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_state_document_invalid"
        ) from None


def _stat_optional_at(
    directory_descriptor: int,
    filename: str,
) -> os.stat_result | None:
    try:
        return os.stat(
            filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_inspection_failed"
        ) from None


def _remove_abandoned_pending_at(
    directory_descriptor: int,
    *,
    pending_filename: str,
    final_filename: str,
) -> bool:
    pending = _stat_optional_at(directory_descriptor, pending_filename)
    final = _stat_optional_at(directory_descriptor, final_filename)
    if pending is None or final is not None:
        return False
    if (
        not stat.S_ISREG(pending.st_mode)
        or stat.S_IMODE(pending.st_mode) != 0o600
        or pending.st_uid != os.geteuid()
        or pending.st_nlink != 1
    ):
        return False
    try:
        os.unlink(pending_filename, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_abandoned_pending_cleanup_failed"
        ) from None
    return True


def _assert_pending_publication_descriptor_binding(
    directory_descriptor: int,
    *,
    pending_filename: str,
    final_filename: str,
    pending_descriptor: int,
    linked: bool,
) -> None:
    try:
        descriptor = os.fstat(pending_descriptor)
        pending = os.stat(
            pending_filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        final = (
            os.stat(
                final_filename,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if linked
            else None
        )
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_pending_publication_changed"
        ) from None
    expected_links = 2 if linked else 1
    expected = (
        descriptor.st_dev,
        descriptor.st_ino,
        descriptor.st_mode,
        descriptor.st_uid,
        descriptor.st_nlink,
        descriptor.st_size,
    )
    if (
        not stat.S_ISREG(descriptor.st_mode)
        or stat.S_IMODE(descriptor.st_mode) != 0o400
        or descriptor.st_uid != os.geteuid()
        or descriptor.st_nlink != expected_links
        or expected
        != (
            pending.st_dev,
            pending.st_ino,
            pending.st_mode,
            pending.st_uid,
            pending.st_nlink,
            pending.st_size,
        )
        or (
            linked
            and final is not None
            and expected
            != (
                final.st_dev,
                final.st_ino,
                final.st_mode,
                final.st_uid,
                final.st_nlink,
                final.st_size,
            )
        )
    ):
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_pending_publication_changed"
        )


def _read_strict_json_descriptor(
    descriptor: int,
    identity: P8LockedLabelDescriptorIdentity,
) -> object:
    duplicate: int | None = None
    try:
        duplicate = os.dup(descriptor)
        os.set_inheritable(duplicate, False)
        os.lseek(duplicate, 0, os.SEEK_SET)
        payload = bytearray()
        while len(payload) <= _MAXIMUM_TYPED_RECEIPT_BYTES:
            chunk = os.read(
                duplicate,
                min(
                    64 * 1024,
                    _MAXIMUM_TYPED_RECEIPT_BYTES + 1 - len(payload),
                ),
            )
            if not chunk:
                break
            payload.extend(chunk)
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_authorization_read_failed"
        ) from None
    finally:
        if duplicate is not None:
            os.close(duplicate)
    assert_p8_locked_label_descriptor_identity(descriptor, identity)
    if len(payload) != identity.size_bytes:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_authorization_read_failed"
        )

    def reject_duplicate_pairs(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise P8LockedLabelDownloadAdapterError(
                    "p8_locked_label_authorization_duplicate_key"
                )
            document[key] = value
        return document

    try:
        value: Any = json.loads(
            bytes(payload),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_authorization_json_invalid"
        ) from None
    return value


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError
        view = view[written:]


__all__ = [
    "P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_FILENAME",
    "P8LockedLabelDescriptorIdentity",
    "P8LockedLabelDownloadAdapterError",
    "P8LockedLabelOutputDirectoryIdentity",
    "assert_p8_locked_label_descriptor_identity",
    "capture_p8_locked_label_descriptor_identity",
    "load_p8_locked_cohort_discovery_seal_descriptor",
    "load_p8_locked_label_download_transaction_descriptor",
    "load_p8_locked_label_download_completion_at",
    "lock_p8_locked_label_download_transaction_descriptor",
    "lock_p8_locked_label_state_directory",
    "open_p8_locked_label_output_directory",
    "open_p8_locked_label_output_member",
    "open_p8_locked_label_state_directory",
    "publish_p8_locked_label_download_completion",
    "reconcile_p8_locked_label_pending_publications",
    "require_p8_locked_label_transaction_output_binding",
    "require_p8_locked_label_transaction_state_binding",
]
