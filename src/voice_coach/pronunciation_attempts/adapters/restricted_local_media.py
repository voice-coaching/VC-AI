"""Local, purpose-limited storage for one admitted audiovisual attempt."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import threading
from typing import BinaryIO

from ..models import (
    MediaErrorCode,
    PronunciationAttemptMediaError,
    RestrictedMediaDeletionReceipt,
    RestrictedPcmAudio,
    RestrictedVideoAsset,
    HttpRestrictedVideoAsset,
    VideoAdmissionPolicy,
    VideoConsentAuthorization,
    VideoConsentDecision,
)


_COPY_CHUNK_BYTES = 1024 * 1024
_PREOPENED_FD_PREFIX = "fd:"


@dataclass(slots=True)
class _DerivedRecord:
    audio: RestrictedPcmAudio
    path: Path


@dataclass(slots=True)
class _AssetRecord:
    asset: RestrictedVideoAsset
    workspace_path: Path
    container_path: Path
    derived_audio: dict[str, _DerivedRecord] = field(default_factory=dict)
    pending_artifacts: set[Path] = field(default_factory=set)
    active_leases: int = 0


class _DerivedAudioWriter:
    """Hashing writer that can commit only its exact path-free audio contract."""

    def __init__(
        self,
        *,
        artifact_id: str,
        asset: RestrictedVideoAsset,
        output: BinaryIO,
    ) -> None:
        self._artifact_id = artifact_id
        self._asset = asset
        self._output = output
        self._sha256 = hashlib.sha256()
        self._byte_count = 0
        self._committed_audio: RestrictedPcmAudio | None = None

    @property
    def artifact_id(self) -> str:
        return self._artifact_id

    @property
    def committed_audio(self) -> RestrictedPcmAudio | None:
        return self._committed_audio

    def write(self, payload: bytes) -> int:
        """Write all bytes or fail closed without leaking the output locator."""

        if self._committed_audio is not None:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_WRITE_FAILED
            )
        if not isinstance(payload, bytes):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_WRITE_FAILED
            )
        try:
            written = self._output.write(payload)
        except OSError as error:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_WRITE_FAILED
            ) from None
        if written != len(payload):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_WRITE_FAILED
            )
        self._sha256.update(payload)
        self._byte_count += written
        return written

    def commit(self, audio: RestrictedPcmAudio) -> None:
        """Bind the writer counters to the immutable derivation receipt."""

        if self._committed_audio is not None:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_WRITE_FAILED
            )
        receipt = audio.derivation_receipt
        if (
            audio.artifact_id != self._artifact_id
            or audio.asset_handle_id != self._asset.handle_id
            or audio.attempt_id_sha256 != self._asset.attempt_id_sha256
            or audio.byte_count != self._byte_count
            or receipt.container_sha256 != self._asset.container_sha256
            or receipt.pcm_sha256 != self._sha256.hexdigest()
        ):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_ASSET_MISMATCH
            )
        self._committed_audio = audio


class LocalRestrictedVideoAdmissionAdapter:
    """Copy a regular local source into a mode-restricted opaque workspace.

    Consent is checked before this adapter opens or stats the source locator.
    Paths are opened read-only with ``O_NOFOLLOW``.  The internal ``fd:N``
    form duplicates a trusted caller's already-open read-only descriptor, so a
    participant-sensitive path need not be present in process arguments.  The
    caller's original file or descriptor is never deleted or closed here.
    """

    def __init__(
        self,
        *,
        restricted_root: str | os.PathLike[str],
        clock: Callable[[], datetime],
    ) -> None:
        self._restricted_root = Path(restricted_root).absolute()
        self._clock = clock
        self._records: dict[str, _AssetRecord] = {}
        self._deleted_assets: dict[str, RestrictedVideoAsset] = {}
        self._lock = threading.RLock()

    def admit(
        self,
        source_path: str | os.PathLike[str],
        *,
        attempt_id_sha256: str,
        authorization: VideoConsentAuthorization,
        policy: VideoAdmissionPolicy,
    ) -> RestrictedVideoAsset:
        """Admit one file after a no-I/O consent preflight."""

        self._validate_consent_preflight(
            attempt_id_sha256=attempt_id_sha256,
            authorization=authorization,
            policy=policy,
        )
        self._ensure_restricted_root()
        source_fd = self._open_regular_source(source_path, policy)
        workspace_path: Path | None = None
        container_path: Path | None = None

        try:
            initial_stat = self._validate_open_source(source_fd, policy)
            handle_id, workspace_path, container_path = self._create_workspace()
            container_sha256, byte_count = self._copy_and_hash(
                source_fd=source_fd,
                destination_path=container_path,
                initial_stat=initial_stat,
                maximum_source_bytes=policy.maximum_source_bytes,
            )
            asset = RestrictedVideoAsset(
                handle_id=handle_id,
                attempt_id_sha256=attempt_id_sha256,
                container_sha256=container_sha256,
                byte_count=byte_count,
                consent_receipt_sha256=authorization.consent_receipt_sha256,
            )
            record = _AssetRecord(
                asset=asset,
                workspace_path=workspace_path,
                container_path=container_path,
            )
            with self._lock:
                if handle_id in self._records or handle_id in self._deleted_assets:
                    raise PronunciationAttemptMediaError(
                        MediaErrorCode.RESTRICTED_WORKSPACE_UNAVAILABLE
                    )
                self._records[handle_id] = record
            return asset
        except BaseException:
            if workspace_path is not None and container_path is not None:
                self._cleanup_unregistered_workspace(
                    workspace_path=workspace_path,
                    paths=(container_path,),
                )
            raise
        finally:
            try:
                os.close(source_fd)
            except OSError:
                pass

    def admit_http(self, source_path, *, attempt_id_sha256, expected_sha256, policy):
        """Copy already server-admitted media and verify its registered digest."""
        self._ensure_restricted_root()
        source_fd = self._open_regular_source(source_path, policy)
        workspace_path: Path | None = None
        container_path: Path | None = None

        try:
            initial_stat = self._validate_open_source(source_fd, policy)
            handle_id, workspace_path, container_path = self._create_workspace()
            container_sha256, byte_count = self._copy_and_hash(
                source_fd=source_fd,
                destination_path=container_path,
                initial_stat=initial_stat,
                maximum_source_bytes=policy.maximum_source_bytes,
            )
            asset = HttpRestrictedVideoAsset(
                handle_id=handle_id,
                attempt_id_sha256=attempt_id_sha256,
                container_sha256=container_sha256,
                byte_count=byte_count,
            )
            if container_sha256 != expected_sha256:
                raise ValueError("http_video_integrity_mismatch")
            record = _AssetRecord(
                asset=asset,
                workspace_path=workspace_path,
                container_path=container_path,
            )
            with self._lock:
                if handle_id in self._records or handle_id in self._deleted_assets:
                    raise PronunciationAttemptMediaError(
                        MediaErrorCode.RESTRICTED_WORKSPACE_UNAVAILABLE
                    )
                self._records[handle_id] = record
            return asset
        except BaseException:
            if workspace_path is not None and container_path is not None:
                self._cleanup_unregistered_workspace(
                    workspace_path=workspace_path,
                    paths=(container_path,),
                )
            raise
        finally:
            try:
                os.close(source_fd)
            except OSError:
                pass

    @contextmanager
    def open_container(self, asset: RestrictedVideoAsset) -> Iterator[BinaryIO]:
        """Yield a read-only descriptor for an exact registered asset."""

        record = self._acquire_asset_lease(asset)
        input_file: BinaryIO | None = None
        try:
            input_file = self._open_restricted_file(
                record.container_path,
                expected_byte_count=asset.byte_count,
                expected_sha256=asset.container_sha256,
            )
            yield input_file
        finally:
            if input_file is not None:
                try:
                    input_file.close()
                except OSError:
                    pass
            self._release_asset_lease(asset.handle_id)

    @contextmanager
    def create_derived_audio(
        self, asset: RestrictedVideoAsset
    ) -> Iterator[_DerivedAudioWriter]:
        """Create a 0600 artifact and delete it unless explicitly committed."""

        record = self._acquire_asset_lease(asset)
        artifact_id = self._new_opaque_id()
        artifact_path = record.workspace_path / artifact_id
        output_file: BinaryIO | None = None
        writer: _DerivedAudioWriter | None = None
        must_delete = True
        body_error: BaseException | None = None

        try:
            with self._lock:
                current = self._records.get(asset.handle_id)
                if current is not record:
                    raise PronunciationAttemptMediaError(
                        MediaErrorCode.RESTRICTED_ASSET_UNAVAILABLE
                    )
                current.pending_artifacts.add(artifact_path)
            output_file = self._create_restricted_output(artifact_path)
            writer = _DerivedAudioWriter(
                artifact_id=artifact_id,
                asset=asset,
                output=output_file,
            )
            try:
                yield writer
            except BaseException as error:
                body_error = error
            if body_error is None and writer.committed_audio is None:
                body_error = PronunciationAttemptMediaError(
                    MediaErrorCode.DERIVED_AUDIO_NOT_COMMITTED
                )
            if body_error is None:
                try:
                    output_file.flush()
                    os.fsync(output_file.fileno())
                    os.fchmod(output_file.fileno(), 0o400)
                    output_file.close()
                except OSError:
                    body_error = PronunciationAttemptMediaError(
                        MediaErrorCode.RESTRICTED_WRITE_FAILED
                    )
                output_file = None
            if body_error is None and writer.committed_audio is not None:
                with self._lock:
                    current = self._records.get(asset.handle_id)
                    if current is not record:
                        body_error = PronunciationAttemptMediaError(
                            MediaErrorCode.RESTRICTED_ASSET_UNAVAILABLE
                        )
                    elif artifact_id in current.derived_audio:
                        body_error = PronunciationAttemptMediaError(
                            MediaErrorCode.RESTRICTED_ASSET_MISMATCH
                        )
                    else:
                        current.derived_audio[artifact_id] = _DerivedRecord(
                            audio=writer.committed_audio,
                            path=artifact_path,
                        )
                        current.pending_artifacts.discard(artifact_path)
                        must_delete = False
        finally:
            if output_file is not None:
                try:
                    output_file.close()
                except OSError:
                    pass
            cleanup_failed = False
            if must_delete:
                try:
                    os.unlink(artifact_path)
                    with self._lock:
                        current = self._records.get(asset.handle_id)
                        if current is record:
                            current.pending_artifacts.discard(artifact_path)
                except FileNotFoundError:
                    with self._lock:
                        current = self._records.get(asset.handle_id)
                        if current is record:
                            current.pending_artifacts.discard(artifact_path)
                except OSError:
                    cleanup_failed = True
            self._release_asset_lease(asset.handle_id)
            if cleanup_failed:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.CLEANUP_FAILED
                ) from None
        if body_error is not None:
            raise body_error

    @contextmanager
    def open_derived_audio(
        self, audio: RestrictedPcmAudio
    ) -> Iterator[BinaryIO]:
        """Yield a read-only descriptor only for the exact committed artifact."""

        with self._lock:
            record = self._records.get(audio.asset_handle_id)
            if record is None:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_ASSET_UNAVAILABLE
                )
            derived_record = record.derived_audio.get(audio.artifact_id)
            if derived_record is None or derived_record.audio != audio:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_ASSET_MISMATCH
                )
            record.active_leases += 1
        input_file: BinaryIO | None = None
        try:
            input_file = self._open_restricted_file(
                derived_record.path,
                expected_byte_count=audio.byte_count,
                expected_sha256=audio.derivation_receipt.pcm_sha256,
            )
            yield input_file
        finally:
            if input_file is not None:
                try:
                    input_file.close()
                except OSError:
                    pass
            self._release_asset_lease(audio.asset_handle_id)

    def delete(
        self, asset: RestrictedVideoAsset
    ) -> RestrictedMediaDeletionReceipt:
        """Delete only the restricted copy and its derived artifacts."""

        with self._lock:
            deleted_asset = self._deleted_assets.get(asset.handle_id)
            if deleted_asset is not None:
                if deleted_asset != asset:
                    raise PronunciationAttemptMediaError(
                        MediaErrorCode.RESTRICTED_ASSET_MISMATCH
                    )
                return self._deletion_receipt(asset, status="already_deleted")

            record = self._records.get(asset.handle_id)
            if record is None:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_ASSET_UNAVAILABLE
                )
            if record.asset != asset:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_ASSET_MISMATCH
                )
            if record.active_leases:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_ASSET_BUSY
                )

            cleanup_failed = False
            paths = tuple(
                derived_record.path
                for derived_record in record.derived_audio.values()
            ) + tuple(record.pending_artifacts) + (record.container_path,)
            for path in paths:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                except OSError:
                    cleanup_failed = True
            try:
                os.rmdir(record.workspace_path)
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_failed = True
            if cleanup_failed:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.CLEANUP_FAILED
                )

            del self._records[asset.handle_id]
            self._deleted_assets[asset.handle_id] = asset
            return self._deletion_receipt(asset, status="deleted")

    def _validate_consent_preflight(
        self,
        *,
        attempt_id_sha256: str,
        authorization: VideoConsentAuthorization,
        policy: VideoAdmissionPolicy,
    ) -> None:
        """Perform only pure checks so rejected consent cannot touch source media."""

        if authorization.decision is not VideoConsentDecision.GRANTED:
            raise PronunciationAttemptMediaError(MediaErrorCode.CONSENT_NOT_GRANTED)
        if authorization.attempt_id_sha256 != attempt_id_sha256:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONSENT_ATTEMPT_MISMATCH
            )
        if authorization.purpose != policy.required_purpose:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONSENT_PURPOSE_MISMATCH
            )
        if authorization.data_category != policy.required_data_category:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONSENT_DATA_CATEGORY_MISMATCH
            )
        if (
            authorization.retention_policy_id
            not in policy.allowed_retention_policy_ids
        ):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RETENTION_POLICY_NOT_ALLOWED
            )
        if not authorization.delete_on_completion:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.DELETE_ON_COMPLETION_REQUIRED
            )
        if authorization.remote_egress_allowed:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.REMOTE_EGRESS_NOT_ALLOWED
            )
        now_utc = self._clock()
        if not isinstance(now_utc, datetime):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONSENT_NOT_YET_EFFECTIVE
            )
        if now_utc.tzinfo is None or now_utc.utcoffset() is None:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONSENT_NOT_YET_EFFECTIVE
            )
        if now_utc.utcoffset().total_seconds() != 0:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONSENT_NOT_YET_EFFECTIVE
            )
        if now_utc < authorization.issued_at_utc:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONSENT_NOT_YET_EFFECTIVE
            )
        if now_utc >= authorization.expires_at_utc:
            raise PronunciationAttemptMediaError(MediaErrorCode.CONSENT_EXPIRED)

    def _ensure_restricted_root(self) -> None:
        try:
            os.mkdir(self._restricted_root, mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_WORKSPACE_UNAVAILABLE
            ) from None
        try:
            root_stat = os.lstat(self._restricted_root)
        except OSError:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_WORKSPACE_UNAVAILABLE
            ) from None
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_WORKSPACE_UNAVAILABLE
            )
        if stat.S_IMODE(root_stat.st_mode) != 0o700:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_WORKSPACE_UNAVAILABLE
            )

    def _open_regular_source(
        self,
        source_path: str | os.PathLike[str],
        policy: VideoAdmissionPolicy,
    ) -> int:
        del policy
        if isinstance(source_path, str) and source_path.startswith(
            _PREOPENED_FD_PREFIX
        ):
            return self._duplicate_read_only_source_fd(source_path)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise PronunciationAttemptMediaError(MediaErrorCode.NOFOLLOW_UNAVAILABLE)
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | nofollow
        try:
            return os.open(source_path, flags)
        except OSError:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.SOURCE_OPEN_FAILED
            ) from None

    @staticmethod
    def _duplicate_read_only_source_fd(locator: str) -> int:
        """Duplicate an already-open regular-file candidate without a path.

        This internal locator is used by the privacy-preserving CLI boundary.
        Duplication occurs only after the same consent preflight as path input.
        The descriptor must have been opened read-only by the trusted caller;
        its regular-file and size properties are checked by the common path.
        """

        descriptor_text = locator.removeprefix(_PREOPENED_FD_PREFIX)
        if (
            not descriptor_text
            or not descriptor_text.isascii()
            or not descriptor_text.isdecimal()
            or len(descriptor_text) > 10
        ):
            raise PronunciationAttemptMediaError(MediaErrorCode.SOURCE_OPEN_FAILED)
        source_fd = int(descriptor_text)
        if source_fd < 3:
            raise PronunciationAttemptMediaError(MediaErrorCode.SOURCE_OPEN_FAILED)
        duplicated_fd: int | None = None
        try:
            duplicated_fd = os.dup(source_fd)
            os.set_inheritable(duplicated_fd, False)
            descriptor_flags = fcntl.fcntl(duplicated_fd, fcntl.F_GETFL)
            if descriptor_flags & os.O_ACCMODE != os.O_RDONLY:
                raise OSError
            descriptor_stat = os.fstat(duplicated_fd)
            if stat.S_ISREG(descriptor_stat.st_mode):
                os.lseek(duplicated_fd, 0, os.SEEK_SET)
            return duplicated_fd
        except (OSError, OverflowError, ValueError):
            if duplicated_fd is not None:
                try:
                    os.close(duplicated_fd)
                except OSError:
                    pass
            raise PronunciationAttemptMediaError(
                MediaErrorCode.SOURCE_OPEN_FAILED
            ) from None

    def _validate_open_source(
        self,
        source_fd: int,
        policy: VideoAdmissionPolicy,
    ) -> os.stat_result:
        try:
            source_stat = os.fstat(source_fd)
        except OSError:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.SOURCE_OPEN_FAILED
            ) from None
        if not stat.S_ISREG(source_stat.st_mode):
            raise PronunciationAttemptMediaError(MediaErrorCode.SOURCE_NOT_REGULAR)
        if source_stat.st_size <= 0:
            raise PronunciationAttemptMediaError(MediaErrorCode.SOURCE_EMPTY)
        if source_stat.st_size > policy.maximum_source_bytes:
            raise PronunciationAttemptMediaError(MediaErrorCode.SOURCE_TOO_LARGE)
        return source_stat

    def _create_workspace(self) -> tuple[str, Path, Path]:
        for _ in range(16):
            handle_id = self._new_opaque_id()
            workspace_path = self._restricted_root / handle_id
            try:
                os.mkdir(workspace_path, mode=0o700)
            except FileExistsError:
                continue
            except OSError:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_WORKSPACE_UNAVAILABLE
                ) from None
            try:
                os.chmod(workspace_path, 0o700)
            except OSError:
                try:
                    os.rmdir(workspace_path)
                except OSError:
                    raise PronunciationAttemptMediaError(
                        MediaErrorCode.CLEANUP_FAILED
                    ) from None
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_WORKSPACE_UNAVAILABLE
                ) from None
            return handle_id, workspace_path, workspace_path / "container"
        raise PronunciationAttemptMediaError(
            MediaErrorCode.RESTRICTED_WORKSPACE_UNAVAILABLE
        )

    def _copy_and_hash(
        self,
        *,
        source_fd: int,
        destination_path: Path,
        initial_stat: os.stat_result,
        maximum_source_bytes: int,
    ) -> tuple[str, int]:
        destination_fd: int | None = None
        digest = hashlib.sha256()
        byte_count = 0
        try:
            destination_fd = os.open(
                destination_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600,
            )
            os.fchmod(destination_fd, 0o600)
            while True:
                read_size = min(
                    _COPY_CHUNK_BYTES,
                    maximum_source_bytes - byte_count + 1,
                )
                try:
                    chunk = os.read(source_fd, read_size)
                except OSError:
                    raise PronunciationAttemptMediaError(
                        MediaErrorCode.SOURCE_READ_FAILED
                    ) from None
                if not chunk:
                    break
                byte_count += len(chunk)
                if byte_count > maximum_source_bytes:
                    raise PronunciationAttemptMediaError(
                        MediaErrorCode.SOURCE_TOO_LARGE
                    )
                digest.update(chunk)
                self._write_all(destination_fd, chunk)
            try:
                os.fsync(destination_fd)
                os.fchmod(destination_fd, 0o400)
                final_stat = os.fstat(source_fd)
            except OSError:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.SOURCE_READ_FAILED
                ) from None
        finally:
            if destination_fd is not None:
                try:
                    os.close(destination_fd)
                except OSError:
                    pass

        stable_fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if byte_count != initial_stat.st_size or any(
            getattr(initial_stat, field_name) != getattr(final_stat, field_name)
            for field_name in stable_fields
        ):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.SOURCE_CHANGED_DURING_COPY
            )
        return digest.hexdigest(), byte_count

    @staticmethod
    def _write_all(destination_fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            try:
                written = os.write(destination_fd, view)
            except OSError:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_WRITE_FAILED
                ) from None
            if written <= 0:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_WRITE_FAILED
                )
            view = view[written:]

    def _acquire_asset_lease(self, asset: RestrictedVideoAsset) -> _AssetRecord:
        with self._lock:
            record = self._records.get(asset.handle_id)
            if record is None:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_ASSET_UNAVAILABLE
                )
            if record.asset != asset:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_ASSET_MISMATCH
                )
            record.active_leases += 1
            return record

    def _release_asset_lease(self, handle_id: str) -> None:
        with self._lock:
            record = self._records.get(handle_id)
            if record is not None and record.active_leases > 0:
                record.active_leases -= 1

    @staticmethod
    def _open_restricted_file(
        path: Path,
        *,
        expected_byte_count: int,
        expected_sha256: str,
    ) -> BinaryIO:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise PronunciationAttemptMediaError(MediaErrorCode.NOFOLLOW_UNAVAILABLE)
        try:
            file_descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | nofollow)
            file_stat = os.fstat(file_descriptor)
        except OSError:
            try:
                os.close(file_descriptor)
            except (OSError, UnboundLocalError):
                pass
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_ASSET_UNAVAILABLE
            ) from None
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != os.geteuid()
            or stat.S_IMODE(file_stat.st_mode) != 0o400
            or file_stat.st_size != expected_byte_count
        ):
            os.close(file_descriptor)
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_ASSET_MISMATCH
            )
        input_file = os.fdopen(file_descriptor, "rb", closefd=True)
        try:
            digest = hashlib.sha256()
            while True:
                payload = input_file.read(_COPY_CHUNK_BYTES)
                if not payload:
                    break
                digest.update(payload)
            if digest.hexdigest() != expected_sha256:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.RESTRICTED_ASSET_MISMATCH
                )
            input_file.seek(0)
            return input_file
        except OSError:
            input_file.close()
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_ASSET_UNAVAILABLE
            ) from None
        except BaseException:
            input_file.close()
            raise

    @staticmethod
    def _create_restricted_output(path: Path) -> BinaryIO:
        try:
            file_descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600,
            )
            os.fchmod(file_descriptor, 0o600)
            return os.fdopen(file_descriptor, "wb", closefd=True)
        except OSError:
            try:
                os.close(file_descriptor)
            except (OSError, UnboundLocalError):
                pass
            raise PronunciationAttemptMediaError(
                MediaErrorCode.RESTRICTED_WRITE_FAILED
            ) from None

    @staticmethod
    def _cleanup_unregistered_workspace(
        *,
        workspace_path: Path,
        paths: tuple[Path, ...],
    ) -> None:
        cleanup_failed = False
        for path in paths:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_failed = True
        try:
            os.rmdir(workspace_path)
        except FileNotFoundError:
            pass
        except OSError:
            cleanup_failed = True
        if cleanup_failed:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CLEANUP_FAILED
            ) from None

    @staticmethod
    def _new_opaque_id() -> str:
        return secrets.token_hex(32)

    @staticmethod
    def _deletion_receipt(
        asset: RestrictedVideoAsset,
        *,
        status: str,
    ) -> RestrictedMediaDeletionReceipt:
        document = {
            "asset_handle_id": asset.handle_id,
            "attempt_id_sha256": asset.attempt_id_sha256,
            "container_sha256": asset.container_sha256,
            "status": status,
        }
        payload = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return RestrictedMediaDeletionReceipt(
            asset_handle_id=asset.handle_id,
            status=status,
            deletion_receipt_sha256=hashlib.sha256(payload).hexdigest(),
        )


__all__ = ["LocalRestrictedVideoAdmissionAdapter"]
