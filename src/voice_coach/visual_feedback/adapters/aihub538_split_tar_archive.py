"""Fail-closed split-inner-TAR merge and deterministic ZIP repack boundary.

AI Hub 538 training exports may wrap TS174/TL174 as byte-offset split TARs.
This adapter keeps every archive-controlled name private, validates the whole
merged TAR before creating output, and streams regular files into a canonical
ZIP_STORED/Zip64 archive accepted by the existing dataset adapter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from .aihub538_training_archive import (
    Aihub538ArchiveError,
    Aihub538ArchiveSecurityPolicy,
    DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
    ZipValidationReceipt,
    validate_zip_descriptor,
)
from ..models.integrity import sha256_document


_SPLIT_TAR_PART_PATTERN = re.compile(
    r"^(?P<stem>[^/\\\x00]+\.tar)\.part(?P<offset>[0-9]+)$"
)
_CHUNK_BYTES = 4 * 1024 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class NamedSplitTarPart:
    """Private split-TAR part name plus caller-owned read-only descriptor."""

    name: str
    descriptor: int


@dataclass(frozen=True, slots=True)
class SplitTarMergeReceipt:
    """Aggregate receipt for byte-offset concatenation of one inner TAR."""

    part_count: int
    first_part_offset_bytes: int
    last_part_offset_bytes: int
    merged_bytes: int
    merged_sha256: str
    ordered_part_root_sha256: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_split_tar_merge_receipt.v1",
            "archive_kind": "inner_posix_tar",
            "part_count": self.part_count,
            "first_part_offset_bytes": self.first_part_offset_bytes,
            "last_part_offset_bytes": self.last_part_offset_bytes,
            "offset_continuity_validated": True,
            "same_stem_validated": True,
            "merged_bytes": self.merged_bytes,
            "merged_sha256": self.merged_sha256,
            "ordered_part_root_sha256": self.ordered_part_root_sha256,
            "destination_was_new": True,
            "file_mode": "0600",
            "fsync_completed": True,
            "raw_part_names_serialized": False,
            "aggregate_only": True,
        }


@dataclass(frozen=True, slots=True)
class InnerTarZipRepackReceipt:
    """Aggregate TAR-to-ZIP parity proof without private member names."""

    source_tar_sha256: str
    source_tar_regular_file_count: int
    source_tar_directory_count: int
    source_tar_uncompressed_bytes: int
    source_tar_content_root_sha256: str
    repacked_zip_sha256: str
    repacked_zip_bytes: int
    repacked_zip_content_root_sha256: str
    file_parity_root_sha256: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_inner_tar_zip_repack.v1",
            "status": "verified_inner_tar_repacked_deterministically",
            "source_archive_kind": "inner_posix_tar",
            "destination_archive_kind": "zip_stored_zip64",
            "source_tar_sha256": self.source_tar_sha256,
            "source_tar_regular_file_count": self.source_tar_regular_file_count,
            "source_tar_directory_count": self.source_tar_directory_count,
            "source_tar_uncompressed_bytes": self.source_tar_uncompressed_bytes,
            "source_tar_content_root_sha256": (
                self.source_tar_content_root_sha256
            ),
            "repacked_zip_sha256": self.repacked_zip_sha256,
            "repacked_zip_bytes": self.repacked_zip_bytes,
            "repacked_zip_content_root_sha256": (
                self.repacked_zip_content_root_sha256
            ),
            "file_parity_root_sha256": self.file_parity_root_sha256,
            "regular_file_count_parity": True,
            "normalized_path_hash_size_sha256_parity": True,
            "zip_compression": "stored",
            "zip64_forced_for_streamed_members": True,
            "fixed_zip_timestamp": "1980-01-01T00:00:00",
            "directory_entries_omitted": True,
            "destination_was_new": True,
            "file_mode": "0600",
            "fsync_completed": True,
            "raw_member_names_serialized": False,
            "source_paths_serialized": False,
            "transcripts_serialized": False,
            "participant_identifiers_serialized": False,
            "aggregate_only": True,
        }


@dataclass(frozen=True, slots=True)
class _TarFileEntry:
    member: tarfile.TarInfo
    normalized_name: str
    size_bytes: int
    content_sha256: str
    content_row: dict[str, object]


@dataclass(frozen=True, slots=True)
class _InnerTarScan:
    entries: tuple[_TarFileEntry, ...]
    directory_count: int
    uncompressed_bytes: int
    tar_content_root_sha256: str
    expected_zip_content_root_sha256: str
    file_parity_root_sha256: str


def merge_split_tar_parts(
    *,
    parts: Sequence[NamedSplitTarPart],
    destination: Path,
    policy: Aihub538ArchiveSecurityPolicy = DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
) -> SplitTarMergeReceipt:
    """Merge exact ``.tar.part<BYTE_OFFSET>`` parts into an absent 0600 TAR."""

    ordered = _validate_split_tar_parts(parts, policy=policy)
    parent_descriptor, destination_name = _open_destination_parent(destination)
    output_descriptor: int | None = None
    created = False
    merged_digest = hashlib.sha256()
    merged_bytes = 0
    part_rows: list[dict[str, object]] = []
    try:
        try:
            output_descriptor = os.open(
                destination_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_descriptor,
            )
            created = True
        except OSError:
            raise Aihub538ArchiveError(
                "aihub538_split_tar_destination_not_new"
            ) from None
        os.fchmod(output_descriptor, 0o600)
        for offset, expected_size, part in ordered:
            source = _duplicate_read_only_regular(
                part.descriptor,
                maximum_bytes=policy.maximum_split_part_bytes,
                reason_code="aihub538_split_tar_part_descriptor_invalid",
            )
            digest = hashlib.sha256()
            observed = 0
            try:
                while chunk := os.read(source, _CHUNK_BYTES):
                    _write_all(output_descriptor, chunk)
                    digest.update(chunk)
                    merged_digest.update(chunk)
                    observed += len(chunk)
                    merged_bytes += len(chunk)
                    if merged_bytes > policy.maximum_download_bytes:
                        raise Aihub538ArchiveError(
                            "aihub538_split_tar_merged_size_outside_policy"
                        )
            finally:
                os.close(source)
            if observed != expected_size:
                raise Aihub538ArchiveError(
                    "aihub538_split_tar_part_changed_during_merge"
                )
            part_rows.append(
                {
                    "offset_bytes": offset,
                    "size_bytes": observed,
                    "sha256": digest.hexdigest(),
                }
            )
        os.fsync(output_descriptor)
        os.fsync(parent_descriptor)
    except Exception:
        if output_descriptor is not None:
            os.close(output_descriptor)
            output_descriptor = None
        if created:
            try:
                os.unlink(destination_name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError:
                pass
        raise
    finally:
        if output_descriptor is not None:
            os.close(output_descriptor)
        os.close(parent_descriptor)
    return SplitTarMergeReceipt(
        part_count=len(ordered),
        first_part_offset_bytes=ordered[0][0],
        last_part_offset_bytes=ordered[-1][0],
        merged_bytes=merged_bytes,
        merged_sha256=merged_digest.hexdigest(),
        ordered_part_root_sha256=sha256_document(
            {
                "schema_version": "voice_coach.aihub538_ordered_split_tar_parts.v1",
                "parts": part_rows,
            }
        ),
    )


def repack_verified_inner_tar_to_zip(
    *,
    archive_descriptor: int,
    destination: Path,
    policy: Aihub538ArchiveSecurityPolicy = DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
) -> tuple[InnerTarZipRepackReceipt, ZipValidationReceipt]:
    """Pre-validate one POSIX TAR and stream it into deterministic ZIP_STORED."""

    descriptor = _duplicate_read_only_regular(
        archive_descriptor,
        maximum_bytes=policy.maximum_download_bytes,
        reason_code="aihub538_inner_tar_descriptor_invalid",
    )
    parent_descriptor: int | None = None
    output_descriptor: int | None = None
    created = False
    destination_name = ""
    try:
        source_tar_sha256 = _sha256_descriptor(descriptor)
        scan = _scan_inner_tar(descriptor, policy=policy)
        if _sha256_descriptor(descriptor) != source_tar_sha256:
            raise Aihub538ArchiveError("aihub538_inner_tar_changed_after_scan")
        parent_descriptor, destination_name = _open_destination_parent(destination)
        try:
            output_descriptor = os.open(
                destination_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_descriptor,
            )
            created = True
        except OSError:
            raise Aihub538ArchiveError(
                "aihub538_inner_tar_repack_destination_not_new"
            ) from None
        os.fchmod(output_descriptor, 0o600)
        _stream_tar_to_deterministic_zip(
            descriptor,
            output_descriptor=output_descriptor,
            scan=scan,
            policy=policy,
        )
        os.fsync(output_descriptor)
        os.fsync(parent_descriptor)
        os.close(output_descriptor)
        output_descriptor = None
        if _sha256_descriptor(descriptor) != source_tar_sha256:
            raise Aihub538ArchiveError("aihub538_inner_tar_changed_during_repack")

        repacked_descriptor = os.open(
            destination_name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        try:
            validation = validate_zip_descriptor(repacked_descriptor, policy=policy)
            repacked_bytes = os.fstat(repacked_descriptor).st_size
        finally:
            os.close(repacked_descriptor)
        if (
            validation.regular_file_count != len(scan.entries)
            or validation.directory_count != 0
            or validation.uncompressed_bytes != scan.uncompressed_bytes
            or validation.content_root_sha256
            != scan.expected_zip_content_root_sha256
        ):
            raise Aihub538ArchiveError(
                "aihub538_inner_tar_zip_file_parity_mismatch"
            )
        receipt = InnerTarZipRepackReceipt(
            source_tar_sha256=source_tar_sha256,
            source_tar_regular_file_count=len(scan.entries),
            source_tar_directory_count=scan.directory_count,
            source_tar_uncompressed_bytes=scan.uncompressed_bytes,
            source_tar_content_root_sha256=scan.tar_content_root_sha256,
            repacked_zip_sha256=validation.archive_sha256,
            repacked_zip_bytes=repacked_bytes,
            repacked_zip_content_root_sha256=validation.content_root_sha256,
            file_parity_root_sha256=scan.file_parity_root_sha256,
        )
        return receipt, validation
    except Exception:
        if output_descriptor is not None:
            os.close(output_descriptor)
            output_descriptor = None
        if created and parent_descriptor is not None:
            try:
                os.unlink(destination_name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError:
                pass
        raise
    finally:
        if output_descriptor is not None:
            os.close(output_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        os.close(descriptor)


def _validate_split_tar_parts(
    parts: Sequence[NamedSplitTarPart],
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> tuple[tuple[int, int, NamedSplitTarPart], ...]:
    if not 1 <= len(parts) <= policy.maximum_split_part_count:
        raise Aihub538ArchiveError(
            "aihub538_split_tar_part_count_outside_policy"
        )
    expected_stem: str | None = None
    indexed: list[tuple[int, int, NamedSplitTarPart]] = []
    for part in parts:
        if not isinstance(part, NamedSplitTarPart):
            raise Aihub538ArchiveError("aihub538_split_tar_part_contract_invalid")
        match = _SPLIT_TAR_PART_PATTERN.fullmatch(part.name)
        if match is None:
            raise Aihub538ArchiveError("aihub538_split_tar_part_name_invalid")
        offset_text = match.group("offset")
        if len(offset_text) > 1 and offset_text.startswith("0"):
            raise Aihub538ArchiveError("aihub538_split_tar_part_offset_invalid")
        stem = match.group("stem")
        if expected_stem is not None and stem != expected_stem:
            raise Aihub538ArchiveError("aihub538_split_tar_part_set_mismatch")
        expected_stem = stem
        duplicate = _duplicate_read_only_regular(
            part.descriptor,
            maximum_bytes=policy.maximum_split_part_bytes,
            reason_code="aihub538_split_tar_part_descriptor_invalid",
        )
        try:
            part_size = os.fstat(duplicate).st_size
        finally:
            os.close(duplicate)
        indexed.append((int(offset_text), part_size, part))
    indexed.sort(key=lambda row: row[0])
    expected_offset = 0
    for offset, part_size, _ in indexed:
        if offset != expected_offset:
            raise Aihub538ArchiveError(
                "aihub538_split_tar_part_offsets_not_contiguous"
            )
        expected_offset += part_size
    return tuple(indexed)


def _scan_inner_tar(
    descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> _InnerTarScan:
    os.lseek(descriptor, 0, os.SEEK_SET)
    duplicate = os.dup(descriptor)
    entries: list[_TarFileEntry] = []
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    directory_count = 0
    uncompressed_bytes = 0
    member_count = 0
    try:
        with os.fdopen(duplicate, "rb", closefd=True) as source:
            duplicate = -1
            with tarfile.open(fileobj=source, mode="r:") as archive:
                for member in archive:
                    member_count += 1
                    if member_count > policy.maximum_tar_member_count:
                        raise Aihub538ArchiveError(
                            "aihub538_inner_tar_member_count_outside_policy"
                        )
                    normalized = _safe_member_name(member.name, policy=policy)
                    folded = normalized.casefold()
                    if folded in seen:
                        raise Aihub538ArchiveError(
                            "aihub538_inner_tar_duplicate_or_case_collision"
                        )
                    seen.add(folded)
                    if member.isdir():
                        directory_count += 1
                        continue
                    if not member.isfile():
                        raise Aihub538ArchiveError(
                            "aihub538_inner_tar_link_or_special_member_forbidden"
                        )
                    if not 0 < member.size <= policy.maximum_tar_member_bytes:
                        raise Aihub538ArchiveError(
                            "aihub538_inner_tar_member_size_outside_policy"
                        )
                    uncompressed_bytes += member.size
                    if uncompressed_bytes > policy.maximum_tar_uncompressed_bytes:
                        raise Aihub538ArchiveError(
                            "aihub538_inner_tar_total_size_outside_policy"
                        )
                    payload = archive.extractfile(member)
                    if payload is None:
                        raise Aihub538ArchiveError(
                            "aihub538_inner_tar_payload_missing"
                        )
                    digest, observed = _sha256_stream(payload)
                    if observed != member.size:
                        raise Aihub538ArchiveError(
                            "aihub538_inner_tar_payload_truncated"
                        )
                    row = _content_root_row(
                        normalized_name=normalized,
                        size_bytes=member.size,
                        content_sha256=digest,
                    )
                    rows.append(row)
                    entries.append(
                        _TarFileEntry(
                            member=member,
                            normalized_name=normalized,
                            size_bytes=member.size,
                            content_sha256=digest,
                            content_row=row,
                        )
                    )
    except Aihub538ArchiveError:
        raise
    except (OSError, tarfile.TarError, UnicodeError):
        raise Aihub538ArchiveError("aihub538_inner_tar_invalid") from None
    finally:
        if duplicate >= 0:
            os.close(duplicate)
    if not entries:
        raise Aihub538ArchiveError("aihub538_inner_tar_regular_files_missing")
    sorted_rows = sorted(rows, key=lambda row: str(row["path_sha256"]))
    return _InnerTarScan(
        entries=tuple(sorted(entries, key=lambda entry: entry.normalized_name)),
        directory_count=directory_count,
        uncompressed_bytes=uncompressed_bytes,
        tar_content_root_sha256=sha256_document(
            {
                "schema_version": "voice_coach.aihub538_tar_content_root.v2",
                "entries": sorted_rows,
                "regular_file_count": len(entries),
                "uncompressed_bytes": uncompressed_bytes,
            }
        ),
        expected_zip_content_root_sha256=sha256_document(
            {
                "schema_version": "voice_coach.aihub538_zip_content_root.v2",
                "entries": sorted_rows,
                "regular_file_count": len(entries),
                "uncompressed_bytes": uncompressed_bytes,
            }
        ),
        file_parity_root_sha256=sha256_document(
            {
                "schema_version": "voice_coach.aihub538_tar_zip_file_parity.v1",
                "entries": sorted_rows,
                "regular_file_count": len(entries),
                "uncompressed_bytes": uncompressed_bytes,
            }
        ),
    )


def _stream_tar_to_deterministic_zip(
    descriptor: int,
    *,
    output_descriptor: int,
    scan: _InnerTarScan,
    policy: Aihub538ArchiveSecurityPolicy,
) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    source_duplicate = os.dup(descriptor)
    output_duplicate = os.dup(output_descriptor)
    try:
        with os.fdopen(source_duplicate, "rb", closefd=True) as source:
            source_duplicate = -1
            with tarfile.open(fileobj=source, mode="r:") as archive:
                with os.fdopen(output_duplicate, "wb", closefd=True) as output:
                    output_duplicate = -1
                    with ZipFile(
                        output,
                        mode="w",
                        compression=ZIP_STORED,
                        allowZip64=True,
                        strict_timestamps=True,
                    ) as destination:
                        for entry in scan.entries:
                            payload = archive.extractfile(entry.member)
                            if payload is None:
                                raise Aihub538ArchiveError(
                                    "aihub538_inner_tar_payload_missing"
                                )
                            info = ZipInfo(entry.normalized_name, _ZIP_TIMESTAMP)
                            info.compress_type = ZIP_STORED
                            info.create_system = 3
                            info.external_attr = (stat.S_IFREG | 0o600) << 16
                            digest = hashlib.sha256()
                            observed = 0
                            with destination.open(
                                info,
                                mode="w",
                                force_zip64=True,
                            ) as target:
                                while chunk := payload.read(_CHUNK_BYTES):
                                    target.write(chunk)
                                    digest.update(chunk)
                                    observed += len(chunk)
                            if (
                                observed != entry.size_bytes
                                or digest.hexdigest() != entry.content_sha256
                            ):
                                raise Aihub538ArchiveError(
                                    "aihub538_inner_tar_changed_during_repack"
                                )
                            if (
                                os.fstat(output_descriptor).st_size
                                > policy.maximum_merged_zip_bytes
                            ):
                                raise Aihub538ArchiveError(
                                    "aihub538_inner_tar_repacked_zip_outside_policy"
                                )
                    output.flush()
    except Aihub538ArchiveError:
        raise
    except (OSError, tarfile.TarError, RuntimeError, ValueError):
        raise Aihub538ArchiveError(
            "aihub538_inner_tar_repack_failed"
        ) from None
    finally:
        if output_duplicate >= 0:
            os.close(output_duplicate)
        if source_duplicate >= 0:
            os.close(source_duplicate)


def _safe_member_name(
    name: str,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> str:
    try:
        encoded_length = len(name.encode("utf-8")) if isinstance(name, str) else 0
    except UnicodeEncodeError:
        raise Aihub538ArchiveError(
            "aihub538_inner_tar_member_path_invalid"
        ) from None
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or "\\" in name
        or encoded_length > policy.maximum_path_bytes
    ):
        raise Aihub538ArchiveError("aihub538_inner_tar_member_path_invalid")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise Aihub538ArchiveError("aihub538_inner_tar_member_path_invalid")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if (
        not parts
        or len(parts) > policy.maximum_path_depth
        or any(part in {"", ".", ".."} for part in parts)
        or ":" in parts[0]
    ):
        raise Aihub538ArchiveError("aihub538_inner_tar_member_path_invalid")
    return PurePosixPath(*parts).as_posix()


def _content_root_row(
    *,
    normalized_name: str,
    size_bytes: int,
    content_sha256: str,
) -> dict[str, object]:
    return {
        "path_sha256": hashlib.sha256(
            b"voice_coach.aihub538.archive_relative_path.v1\x00"
            + normalized_name.encode("utf-8")
        ).hexdigest(),
        "size_bytes": size_bytes,
        "content_sha256": content_sha256,
    }


def _open_destination_parent(destination: Path) -> tuple[int, str]:
    if not destination.is_absolute() or destination.name in {"", ".", ".."}:
        raise Aihub538ArchiveError("aihub538_inner_archive_destination_invalid")
    try:
        if destination.parent.resolve(strict=True) != destination.parent:
            raise Aihub538ArchiveError(
                "aihub538_inner_archive_destination_invalid"
            )
        descriptor = os.open(
            destination.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        return descriptor, destination.name
    except Aihub538ArchiveError:
        raise
    except OSError:
        raise Aihub538ArchiveError(
            "aihub538_inner_archive_destination_invalid"
        ) from None


def _duplicate_read_only_regular(
    descriptor: int,
    *,
    maximum_bytes: int,
    reason_code: str,
) -> int:
    if (
        isinstance(descriptor, bool)
        or not isinstance(descriptor, int)
        or descriptor < 3
    ):
        raise Aihub538ArchiveError(reason_code)
    duplicate: int | None = None
    try:
        duplicate = os.dup(descriptor)
        metadata = os.fstat(duplicate)
        access_mode = fcntl.fcntl(duplicate, fcntl.F_GETFL) & os.O_ACCMODE
        if (
            not stat.S_ISREG(metadata.st_mode)
            or access_mode != os.O_RDONLY
            or metadata.st_size <= 0
            or metadata.st_size > maximum_bytes
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise Aihub538ArchiveError(reason_code)
        os.lseek(duplicate, 0, os.SEEK_SET)
        return duplicate
    except Aihub538ArchiveError:
        if duplicate is not None:
            os.close(duplicate)
        raise
    except OSError:
        if duplicate is not None:
            os.close(duplicate)
        raise Aihub538ArchiveError(reason_code) from None


def _sha256_descriptor(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, _CHUNK_BYTES):
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _sha256_stream(stream: object) -> tuple[str, int]:
    digest = hashlib.sha256()
    observed = 0
    while chunk := stream.read(_CHUNK_BYTES):  # type: ignore[attr-defined]
        digest.update(chunk)
        observed += len(chunk)
    return digest.hexdigest(), observed


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError:
            raise Aihub538ArchiveError(
                "aihub538_inner_archive_write_failed"
            ) from None
        if written <= 0:
            raise Aihub538ArchiveError("aihub538_inner_archive_write_failed")
        view = view[written:]


__all__ = [
    "InnerTarZipRepackReceipt",
    "NamedSplitTarPart",
    "SplitTarMergeReceipt",
    "merge_split_tar_parts",
    "repack_verified_inner_tar_to_zip",
]
