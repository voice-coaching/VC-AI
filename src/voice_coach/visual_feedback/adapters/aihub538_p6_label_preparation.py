"""Private filesystem boundary for one P6 remediation label archive.

Archive-controlled names are consumed only while traversing the freshly
extracted outer TAR.  Public receipts contain aggregate counts and commitments,
never paths, participant identifiers, transcripts, or per-sample digests.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterator, Mapping

from .aihub538_admitted_archive_binding import (
    Aihub538DescriptorIdentity,
    assert_aihub538_descriptor_identity,
    capture_aihub538_descriptor_identity,
)
from .aihub538_split_tar_archive import NamedSplitTarPart
from .aihub538_label_capacity import Aihub538LabelCapacityAdapter
from .aihub538_training_archive import (
    AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY,
    Aihub538ArchiveSecurityPolicy,
    NamedSplitZipPart,
)
from ..models.aihub538_p6_label_preparation import (
    AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS,
    Aihub538P6LabelPreparationReceipt,
)
from ..models.integrity import sha256_document


_SPLIT_ARCHIVE_PATTERN = re.compile(
    r"^(?P<stem>[^/\\\x00]+\.(?P<kind>zip|tar))\.part(?P<offset>0|[1-9][0-9]*)$"
)
_RECEIPT_FILE_NAMES = frozenset(
    {
        "00-download-verification.json",
        "10-outer-tar-extraction.json",
        "20-split-label-merge.json",
        "25-inner-tar-repack.json",
        "30-label-zip-validation.json",
        "40-label-role-admission.json",
        "99-completion.json",
    }
)
_MAXIMUM_COMPLETION_RECEIPT_BYTES = 1024 * 1024


class Aihub538P6LabelPreparationAdapterError(RuntimeError):
    """Stable, path-free failure at the preparation filesystem boundary."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538P6SplitArchiveGroup:
    """Exactly one privately named ZIP or TAR split group."""

    kind: str
    zip_parts: tuple[NamedSplitZipPart, ...] = ()
    tar_parts: tuple[NamedSplitTarPart, ...] = ()

    @property
    def descriptors(self) -> tuple[int, ...]:
        return tuple(
            part.descriptor for part in self.zip_parts + self.tar_parts
        )


@dataclass(frozen=True, slots=True)
class Aihub538P6InputDescriptorBinding:
    """Process-local TOCTOU binding for completed download inputs."""

    archive: Aihub538DescriptorIdentity
    receipt: Aihub538DescriptorIdentity


def capture_aihub538_p6_completed_download_binding(
    *,
    archive_descriptor: int,
    receipt_descriptor: int,
) -> Aihub538P6InputDescriptorBinding:
    """Require the secure downloader's final 0600/0400 file modes."""

    try:
        archive = capture_aihub538_descriptor_identity(
            archive_descriptor,
            require_mode=0o600,
        )
        receipt = capture_aihub538_descriptor_identity(
            receipt_descriptor,
            require_mode=0o400,
        )
        for descriptor in (archive_descriptor, receipt_descriptor):
            metadata = os.fstat(descriptor)
            if metadata.st_uid != os.geteuid() or metadata.st_nlink != 1:
                raise OSError
        return Aihub538P6InputDescriptorBinding(
            archive=archive,
            receipt=receipt,
        )
    except Exception:
        raise Aihub538P6LabelPreparationAdapterError(
            "aihub538_p6_label_preparation_input_descriptor_invalid"
        ) from None


def assert_aihub538_p6_completed_download_binding(
    *,
    archive_descriptor: int,
    receipt_descriptor: int,
    binding: Aihub538P6InputDescriptorBinding,
) -> None:
    """Fail closed if either completed input changes during preparation."""

    if type(binding) is not Aihub538P6InputDescriptorBinding:
        raise Aihub538P6LabelPreparationAdapterError(
            "aihub538_p6_label_preparation_input_descriptor_invalid"
        )
    try:
        assert_aihub538_descriptor_identity(
            archive_descriptor,
            binding.archive,
        )
        assert_aihub538_descriptor_identity(
            receipt_descriptor,
            binding.receipt,
        )
    except Exception:
        raise Aihub538P6LabelPreparationAdapterError(
            "aihub538_p6_label_preparation_input_changed"
        ) from None


class Aihub538P6LabelPreparationOutput:
    """O_EXCL output transaction that removes only its own fresh root."""

    def __init__(self, destination: Path) -> None:
        self.path = destination
        self._root_descriptor: int | None = None
        self._parent_descriptor: int | None = None
        self._root_name: str | None = None
        self._parent_identity: tuple[int, int] | None = None
        self._root_identity: tuple[int, int] | None = None
        self._committed = False
        self._create()

    @property
    def root_descriptor(self) -> int:
        if self._root_descriptor is None:
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_output_closed"
            )
        return self._root_descriptor

    def persist_receipt(
        self,
        name: str,
        document: Mapping[str, object],
    ) -> None:
        """Persist one exact-name, 0400, fsynced aggregate JSON receipt."""

        if name not in _RECEIPT_FILE_NAMES:
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_receipt_name_invalid"
            )
        try:
            payload = (
                json.dumps(
                    dict(document),
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError):
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_receipt_invalid"
            ) from None
        descriptor: int | None = None
        created = False
        try:
            self.assert_identity()
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
                dir_fd=self.root_descriptor,
            )
            created = True
            os.fchmod(descriptor, 0o400)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            os.fsync(self.root_descriptor)
        except Aihub538P6LabelPreparationAdapterError:
            raise
        except OSError:
            if created:
                try:
                    os.unlink(name, dir_fd=self.root_descriptor)
                    os.fsync(self.root_descriptor)
                except OSError:
                    pass
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_receipt_write_failed"
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def open_outer_directory(self) -> int:
        """Open only the freshly extracted private outer workspace."""

        self.assert_identity()
        try:
            descriptor = os.open(
                "outer",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=self.root_descriptor,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise OSError
            return descriptor
        except OSError:
            try:
                os.close(descriptor)
            except (NameError, OSError):
                pass
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_outer_invalid"
            ) from None

    def open_label_zip(self) -> int:
        """Open the newly merged 0600 label ZIP by an anchored dirfd."""

        self.assert_identity()
        try:
            descriptor = os.open(
                "label.zip",
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=self.root_descriptor,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_size <= 0
            ):
                raise OSError
            return descriptor
        except OSError:
            try:
                os.close(descriptor)
            except (NameError, OSError):
                pass
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_label_zip_invalid"
            ) from None

    def remove_file(self, name: str) -> None:
        if name != "label.tar":
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_cleanup_target_invalid"
            )
        try:
            os.unlink(name, dir_fd=self.root_descriptor)
            os.fsync(self.root_descriptor)
        except OSError:
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_temporary_cleanup_failed"
            ) from None

    def remove_outer_workspace(self) -> None:
        """Delete extracted private names after all aggregate checks finish."""

        try:
            outer = os.open(
                "outer",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=self.root_descriptor,
            )
            try:
                _remove_directory_contents(outer)
            finally:
                os.close(outer)
            os.rmdir("outer", dir_fd=self.root_descriptor)
            os.fsync(self.root_descriptor)
        except OSError:
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_outer_cleanup_failed"
            ) from None

    def assert_identity(self) -> None:
        """Bind path lookup and both directory descriptors to created inodes."""

        if (
            self._root_descriptor is None
            or self._parent_descriptor is None
            or self._root_name is None
            or self._parent_identity is None
            or self._root_identity is None
        ):
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_output_closed"
            )
        try:
            parent_metadata = os.fstat(self._parent_descriptor)
            root_metadata = os.fstat(self._root_descriptor)
            linked_metadata = os.stat(
                self._root_name,
                dir_fd=self._parent_descriptor,
                follow_symlinks=False,
            )
            if (
                (parent_metadata.st_dev, parent_metadata.st_ino)
                != self._parent_identity
                or (root_metadata.st_dev, root_metadata.st_ino)
                != self._root_identity
                or (linked_metadata.st_dev, linked_metadata.st_ino)
                != self._root_identity
                or stat.S_IMODE(root_metadata.st_mode) != 0o700
                or root_metadata.st_uid != os.geteuid()
            ):
                raise OSError
        except OSError:
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_output_identity_changed"
            ) from None

    def commit(self) -> None:
        """Validate final modes and make later ``abort`` a no-op."""

        self.assert_identity()
        label = self.open_label_zip()
        completion: int | None = None
        try:
            completion = os.open(
                "99-completion.json",
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=self.root_descriptor,
            )
            completion_metadata = os.fstat(completion)
            if (
                not stat.S_ISREG(completion_metadata.st_mode)
                or stat.S_IMODE(completion_metadata.st_mode) != 0o400
                or completion_metadata.st_nlink != 1
            ):
                raise OSError
            receipt = load_aihub538_p6_label_preparation_receipt_descriptor(
                completion
            )
            expected_names = _prepared_output_names(receipt)
            observed_names = frozenset(os.listdir(self.root_descriptor))
            if observed_names != expected_names:
                raise OSError
            for name in observed_names - {"label.zip"}:
                metadata = os.stat(
                    name,
                    dir_fd=self.root_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_IMODE(metadata.st_mode) != 0o400
                    or metadata.st_uid != os.geteuid()
                    or metadata.st_nlink != 1
                ):
                    raise OSError
            os.fsync(self.root_descriptor)
            os.fsync(self._parent_descriptor)
            self.assert_identity()
            self._committed = True
        except OSError:
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_commit_failed"
            ) from None
        finally:
            if completion is not None:
                os.close(completion)
            os.close(label)

    def abort(self) -> None:
        """Remove only this object's verified fresh root."""

        if self._committed or self._root_descriptor is None:
            return
        try:
            self.assert_identity()
            _remove_directory_contents(self._root_descriptor)
            assert self._parent_descriptor is not None
            assert self._root_name is not None
            os.rmdir(
                self._root_name,
                dir_fd=self._parent_descriptor,
            )
            os.fsync(self._parent_descriptor)
        except Exception:
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_failure_cleanup_failed"
            ) from None

    def close(self) -> None:
        for name in ("_root_descriptor", "_parent_descriptor"):
            descriptor = getattr(self, name)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)

    def _create(self) -> None:
        destination = self.path
        if (
            not isinstance(destination, Path)
            or not destination.is_absolute()
            or destination.name in {"", ".", ".."}
        ):
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_output_invalid"
            )
        parent_descriptor: int | None = None
        root_descriptor: int | None = None
        created = False
        try:
            if destination.parent.resolve(strict=True) != destination.parent:
                raise OSError
            parent_descriptor = os.open(
                destination.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            parent_metadata = os.fstat(parent_descriptor)
            if (
                not stat.S_ISDIR(parent_metadata.st_mode)
            ):
                raise OSError
            os.mkdir(destination.name, 0o700, dir_fd=parent_descriptor)
            created = True
            root_descriptor = os.open(
                destination.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_descriptor,
            )
            os.fchmod(root_descriptor, 0o700)
            root_metadata = os.fstat(root_descriptor)
            linked_metadata = os.stat(
                destination.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(root_metadata.st_mode)
                or (root_metadata.st_dev, root_metadata.st_ino)
                != (linked_metadata.st_dev, linked_metadata.st_ino)
            ):
                raise OSError
            os.fsync(parent_descriptor)
            self._root_descriptor = root_descriptor
            self._parent_descriptor = parent_descriptor
            self._root_name = destination.name
            self._parent_identity = (
                parent_metadata.st_dev,
                parent_metadata.st_ino,
            )
            self._root_identity = (
                root_metadata.st_dev,
                root_metadata.st_ino,
            )
        except OSError:
            if root_descriptor is not None:
                os.close(root_descriptor)
            if created and parent_descriptor is not None:
                try:
                    os.rmdir(destination.name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
                except OSError:
                    pass
            if parent_descriptor is not None:
                os.close(parent_descriptor)
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_label_preparation_output_not_new"
            ) from None


def discover_aihub538_p6_label_split_group(
    outer_descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> Aihub538P6SplitArchiveGroup:
    """Discover exactly one split group without exporting member names."""

    zip_groups: dict[tuple[tuple[str, ...], str], list[NamedSplitZipPart]] = {}
    tar_groups: dict[tuple[tuple[str, ...], str], list[NamedSplitTarPart]] = {}
    opened: list[int] = []

    def visit(directory_descriptor: int, parents: tuple[str, ...]) -> None:
        names = sorted(os.listdir(directory_descriptor))
        for name in names:
            metadata = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISDIR(metadata.st_mode):
                if stat.S_IMODE(metadata.st_mode) != 0o700:
                    raise OSError
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_descriptor,
                )
                try:
                    visit(child, parents + (name,))
                finally:
                    os.close(child)
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise OSError
            match = _SPLIT_ARCHIVE_PATTERN.fullmatch(name)
            if match is None:
                raise OSError
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            opened.append(descriptor)
            key = (parents, match.group("stem"))
            if match.group("kind") == "zip":
                zip_groups.setdefault(key, []).append(
                    NamedSplitZipPart(name=name, descriptor=descriptor)
                )
            else:
                tar_groups.setdefault(key, []).append(
                    NamedSplitTarPart(name=name, descriptor=descriptor)
                )

    try:
        visit(outer_descriptor, ())
        if len(zip_groups) + len(tar_groups) != 1:
            raise OSError
        if zip_groups:
            parts = next(iter(zip_groups.values()))
            if not 1 <= len(parts) <= policy.maximum_split_part_count:
                raise OSError
            result = Aihub538P6SplitArchiveGroup(
                kind="zip",
                zip_parts=tuple(parts),
            )
        else:
            parts = next(iter(tar_groups.values()))
            if not 1 <= len(parts) <= policy.maximum_split_part_count:
                raise OSError
            result = Aihub538P6SplitArchiveGroup(
                kind="tar",
                tar_parts=tuple(parts),
            )
        opened.clear()
        return result
    except (OSError, ValueError):
        raise Aihub538P6LabelPreparationAdapterError(
            "aihub538_p6_label_preparation_split_group_invalid"
        ) from None
    finally:
        for descriptor in opened:
            os.close(descriptor)


def close_aihub538_p6_split_group(
    group: Aihub538P6SplitArchiveGroup,
) -> None:
    """Close caller-owned part descriptors returned by discovery."""

    for descriptor in group.descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass


def load_aihub538_p6_label_preparation_receipt_descriptor(
    descriptor: int,
) -> Aihub538P6LabelPreparationReceipt:
    """Strictly reload one stable, owner-only 0400 completion receipt."""

    try:
        before = capture_aihub538_descriptor_identity(
            descriptor,
            require_mode=0o400,
        )
        metadata = os.fstat(descriptor)
        if (
            before.size_bytes > _MAXIMUM_COMPLETION_RECEIPT_BYTES
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
        ):
            raise ValueError
        payload = bytearray()
        offset = 0
        while offset < before.size_bytes:
            chunk = os.pread(
                descriptor,
                min(64 * 1024, before.size_bytes - offset),
                offset,
            )
            if not chunk:
                raise ValueError
            payload.extend(chunk)
            offset += len(chunk)
        assert_aihub538_descriptor_identity(descriptor, before)
        document = json.loads(
            bytes(payload).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        return Aihub538P6LabelPreparationReceipt.from_json_object(document)
    except Exception:
        raise Aihub538P6LabelPreparationAdapterError(
            "aihub538_p6_label_preparation_receipt_reload_failed"
        ) from None


@contextmanager
def open_aihub538_p6_prepared_label_directory(
    directory: Path,
    *,
    identity_hash_key: bytes,
) -> Iterator[tuple[int, Aihub538P6LabelPreparationReceipt]]:
    """Open exact prepared names and verify their aggregate lineage."""

    directory_descriptor: int | None = None
    label_descriptor: int | None = None
    receipt_descriptor: int | None = None
    try:
        if not directory.is_absolute() or directory.resolve(strict=True) != directory:
            raise OSError
        directory_descriptor = os.open(
            directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        directory_metadata = os.fstat(directory_descriptor)
        if stat.S_IMODE(directory_metadata.st_mode) != 0o700:
            raise OSError
        label_descriptor = os.open(
            "label.zip",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        label_identity = capture_aihub538_descriptor_identity(
            label_descriptor,
            require_mode=0o600,
        )
        receipt_descriptor = os.open(
            "99-completion.json",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        receipt = load_aihub538_p6_label_preparation_receipt_descriptor(
            receipt_descriptor
        )
        if frozenset(os.listdir(directory_descriptor)) != _prepared_output_names(
            receipt
        ):
            raise ValueError
        download_stage = _read_strict_receipt_at(
            directory_descriptor,
            "00-download-verification.json",
        )
        extraction_stage = _read_strict_receipt_at(
            directory_descriptor,
            "10-outer-tar-extraction.json",
        )
        merge_stage = _read_strict_receipt_at(
            directory_descriptor,
            "20-split-label-merge.json",
        )
        validation_stage = _read_strict_receipt_at(
            directory_descriptor,
            "30-label-zip-validation.json",
        )
        admission_stage = _read_strict_receipt_at(
            directory_descriptor,
            "40-label-role-admission.json",
        )
        admission_unsigned = dict(admission_stage)
        admission_sha256 = admission_unsigned.pop("receipt_sha256", None)
        if (
            sha256_document(download_stage)
            != receipt.download_verification_stage_sha256
            or sha256_document(extraction_stage)
            != receipt.outer_extraction_stage_sha256
            or sha256_document(merge_stage)
            != receipt.split_merge_receipt_sha256
            or sha256_document(validation_stage)
            != receipt.label_zip_validation_stage_sha256
            or admission_sha256 != receipt.label_admission_receipt_sha256
            or admission_sha256 != sha256_document(admission_unsigned)
        ):
            raise ValueError
        shard_spec = next(
            shard
            for shard in AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS
            if shard.shard_id == receipt.shard_id
        )
        aggregate = Aihub538LabelCapacityAdapter(
            shard_spec=shard_spec,
            archive_policy=AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY,
        ).audit(
            label_zip_descriptor=label_descriptor,
            identity_hash_key=identity_hash_key,
        )
        actor_count = len({item.actor_token_sha256 for item in aggregate.items})
        attempt_count = len(
            {item.attempt_token_sha256 for item in aggregate.items}
        )
        metadata_excluded_count = sum(
            not item.metadata_bbox_observation_available
            for item in aggregate.items
        )
        admission_lineage = {
            "shard_id": receipt.shard_id,
            "label_role_component": receipt.label_role_component,
            "label_file_key": receipt.label_file_key,
            "shard_spec_sha256": receipt.shard_spec_sha256,
            "label_archive_sha256": receipt.label_zip_sha256,
            "label_content_root_sha256": receipt.label_zip_content_root_sha256,
            "label_member_count": receipt.label_member_count,
            "actor_count": receipt.actor_count,
            "attempt_count": receipt.attempt_count,
            "metadata_excluded_label_count": receipt.metadata_excluded_label_count,
            "terminal_media_overrun_label_count": receipt.terminal_media_overrun_label_count,
            "terminal_frame_tail_exception_label_count": receipt.terminal_frame_tail_exception_label_count,
            "maximum_complete_tail_frames_observed": receipt.maximum_complete_tail_frames_observed,
            "nonterminal_media_overrun_label_count": receipt.nonterminal_media_overrun_label_count,
            "sentence_overlap_label_count": receipt.sentence_overlap_label_count,
            "invalid_time_label_count": receipt.invalid_time_label_count,
            "nonconforming_fps_label_count": receipt.nonconforming_fps_label_count,
            "terminal_frame_tail_policy_sha256": receipt.terminal_frame_tail_policy_sha256,
            "identity_namespace_commitment_sha256": receipt.identity_namespace_commitment_sha256,
            "member_namespace_commitment_sha256": receipt.member_namespace_commitment_sha256,
        }
        audited_lineage = {
            **admission_lineage,
            "label_archive_sha256": aggregate.label_archive_sha256,
            "label_content_root_sha256": aggregate.label_content_root_sha256,
            "label_member_count": len(aggregate.items),
            "actor_count": actor_count,
            "attempt_count": attempt_count,
            "metadata_excluded_label_count": metadata_excluded_count,
            "terminal_media_overrun_label_count": aggregate.terminal_media_overrun_label_count,
            "terminal_frame_tail_exception_label_count": aggregate.terminal_frame_tail_exception_label_count,
            "maximum_complete_tail_frames_observed": aggregate.maximum_complete_tail_frames_observed,
            "nonterminal_media_overrun_label_count": aggregate.nonterminal_media_overrun_label_count,
            "sentence_overlap_label_count": aggregate.sentence_overlap_label_count,
            "invalid_time_label_count": aggregate.invalid_time_label_count,
            "nonconforming_fps_label_count": aggregate.nonconforming_fps_label_count,
            "terminal_frame_tail_policy_sha256": aggregate.terminal_frame_tail_policy_sha256,
            "identity_namespace_commitment_sha256": aggregate.identity_namespace_commitment_sha256,
            "member_namespace_commitment_sha256": aggregate.member_namespace_commitment_sha256,
        }
        if (
            any(admission_stage.get(key) != value for key, value in admission_lineage.items())
            or admission_lineage != audited_lineage
            or validation_stage.get("archive_sha256") != receipt.label_zip_sha256
            or validation_stage.get("content_root_sha256")
            != receipt.label_zip_content_root_sha256
            or validation_stage.get("regular_file_count")
            != receipt.label_member_count
        ):
            raise ValueError
        if receipt.inner_tar_repack_receipt_sha256 is not None:
            repack_stage = _read_strict_receipt_at(
                directory_descriptor,
                "25-inner-tar-repack.json",
            )
            if sha256_document(repack_stage) != (
                receipt.inner_tar_repack_receipt_sha256
            ):
                raise ValueError
        if _sha256_descriptor(label_descriptor) != receipt.label_zip_sha256:
            raise ValueError
        assert_aihub538_descriptor_identity(label_descriptor, label_identity)
    except Exception:
        for descriptor in (
            receipt_descriptor,
            label_descriptor,
            directory_descriptor,
        ):
            if descriptor is not None:
                os.close(descriptor)
        raise Aihub538P6LabelPreparationAdapterError(
            "aihub538_p6_prepared_label_directory_invalid"
        ) from None
    try:
        yield label_descriptor, receipt
        try:
            assert_aihub538_descriptor_identity(label_descriptor, label_identity)
        except Exception:
            raise Aihub538P6LabelPreparationAdapterError(
                "aihub538_p6_prepared_label_changed_while_open"
            ) from None
    finally:
        assert receipt_descriptor is not None
        assert label_descriptor is not None
        assert directory_descriptor is not None
        os.close(receipt_descriptor)
        os.close(label_descriptor)
        os.close(directory_descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError
        remaining = remaining[written:]


def _remove_directory_contents(directory_descriptor: int) -> None:
    for name in os.listdir(directory_descriptor):
        metadata = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            try:
                _remove_directory_contents(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory_descriptor)
        else:
            os.unlink(name, dir_fd=directory_descriptor)
    os.fsync(directory_descriptor)


def _reject_duplicate_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError
        document[key] = value
    return document


def _read_strict_receipt_at(
    directory_descriptor: int,
    name: str,
) -> dict[str, object]:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW,
        dir_fd=directory_descriptor,
    )
    try:
        identity = capture_aihub538_descriptor_identity(
            descriptor,
            require_mode=0o400,
        )
        metadata = os.fstat(descriptor)
        if (
            metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or identity.size_bytes > _MAXIMUM_COMPLETION_RECEIPT_BYTES
        ):
            raise ValueError
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
        assert_aihub538_descriptor_identity(descriptor, identity)
        document = json.loads(
            bytes(payload).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(document, dict):
            raise ValueError
        return document
    finally:
        os.close(descriptor)


def _prepared_output_names(
    receipt: Aihub538P6LabelPreparationReceipt,
) -> frozenset[str]:
    names = {
        "00-download-verification.json",
        "10-outer-tar-extraction.json",
        "20-split-label-merge.json",
        "30-label-zip-validation.json",
        "40-label-role-admission.json",
        "99-completion.json",
        "label.zip",
    }
    if receipt.inner_tar_repack_receipt_sha256 is not None:
        names.add("25-inner-tar-repack.json")
    return frozenset(names)


def _sha256_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    size = os.fstat(descriptor).st_size
    while offset < size:
        chunk = os.pread(descriptor, min(4 * 1024 * 1024, size - offset), offset)
        if not chunk:
            raise OSError
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


__all__ = [
    "Aihub538P6InputDescriptorBinding",
    "Aihub538P6LabelPreparationAdapterError",
    "Aihub538P6LabelPreparationOutput",
    "Aihub538P6SplitArchiveGroup",
    "assert_aihub538_p6_completed_download_binding",
    "capture_aihub538_p6_completed_download_binding",
    "close_aihub538_p6_split_group",
    "discover_aihub538_p6_label_split_group",
    "load_aihub538_p6_label_preparation_receipt_descriptor",
    "open_aihub538_p6_prepared_label_directory",
]
