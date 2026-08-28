"""Fail-closed archive boundary for the approved AI Hub 538 training shard.

This module never opens the P8 locked partition.  It only validates the
credential-free receipt for file keys 54725/54944 and provides bounded TAR,
split-ZIP, and ZIP primitives for the non-locked TS174/TL174 smoke shard.

Archive member names stay inside this adapter.  Every returned receipt is
aggregate-only and therefore safe to serialize without disclosing restricted
paths, transcripts, or participant identifiers.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile, ZipInfo

from ..models.integrity import sha256_document


AIHUB538_TRAINING_DATASET_KEY = "538"
AIHUB538_TRAINING_FILE_KEYS = ("54725", "54944")
AIHUB538_DOWNLOAD_RECEIPT_SCHEMA = "voice_coach.aihub_secure_download_receipt.v1"

_DOWNLOAD_RECEIPT_KEYS = {
    "archive_extracted",
    "archive_sha256",
    "archive_size_bytes",
    "credential_persisted",
    "dataset_key",
    "download_completed_unix_seconds",
    "file_keys",
    "schema_version",
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SPLIT_PART_PATTERN = re.compile(
    r"^(?P<stem>[^/\\\x00]+\.zip)\.part(?P<offset>0|[1-9][0-9]*)$"
)
_CHUNK_BYTES = 4 * 1024 * 1024


class Aihub538ArchiveError(RuntimeError):
    """Path-free archive failure suitable for restricted-data logs."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538ArchiveSecurityPolicy:
    """Frozen upper bounds large enough for TS174/TL174, not the full corpus."""

    maximum_download_bytes: int = 64 * 1024**3
    maximum_receipt_bytes: int = 64 * 1024
    maximum_tar_member_count: int = 20_000
    maximum_tar_member_bytes: int = 32 * 1024**3
    maximum_tar_uncompressed_bytes: int = 64 * 1024**3
    maximum_split_part_count: int = 512
    maximum_split_part_bytes: int = 32 * 1024**3
    maximum_merged_zip_bytes: int = 32 * 1024**3
    maximum_zip_member_count: int = 200_000
    maximum_zip_member_bytes: int = 16 * 1024**3
    maximum_zip_uncompressed_bytes: int = 128 * 1024**3
    maximum_zip_compression_ratio: float = 200.0
    maximum_path_bytes: int = 1024
    maximum_path_depth: int = 16

    def __post_init__(self) -> None:
        integer_bounds = (
            self.maximum_download_bytes,
            self.maximum_receipt_bytes,
            self.maximum_tar_member_count,
            self.maximum_tar_member_bytes,
            self.maximum_tar_uncompressed_bytes,
            self.maximum_split_part_count,
            self.maximum_split_part_bytes,
            self.maximum_merged_zip_bytes,
            self.maximum_zip_member_count,
            self.maximum_zip_member_bytes,
            self.maximum_zip_uncompressed_bytes,
            self.maximum_path_bytes,
            self.maximum_path_depth,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in integer_bounds
        ):
            raise ValueError("aihub538_archive_policy_invalid")
        if (
            isinstance(self.maximum_zip_compression_ratio, bool)
            or not isinstance(self.maximum_zip_compression_ratio, (int, float))
            or self.maximum_zip_compression_ratio <= 1.0
        ):
            raise ValueError("aihub538_archive_policy_invalid")


DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY = Aihub538ArchiveSecurityPolicy()


@dataclass(frozen=True, slots=True)
class VerifiedAihub538Download:
    """Digest binding between the secure receipt and completed download TAR."""

    archive_size_bytes: int
    archive_sha256: str
    receipt_sha256: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_training_download_verification.v1",
            "dataset_id": 538,
            "dataset_partition": "nonlocked_training_ts174_tl174",
            "file_keys": list(AIHUB538_TRAINING_FILE_KEYS),
            "archive_size_bytes": self.archive_size_bytes,
            "archive_sha256": self.archive_sha256,
            "secure_download_receipt_sha256": self.receipt_sha256,
            "credential_persisted": False,
            "locked_partition_accessed": False,
        }


@dataclass(frozen=True, slots=True)
class TarExtractionReceipt:
    """Aggregate result of a fully verified, newly-created TAR extraction."""

    archive_sha256: str
    regular_file_count: int
    directory_count: int
    uncompressed_bytes: int
    content_root_sha256: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_tar_extraction_receipt.v2",
            "archive_sha256": self.archive_sha256,
            "regular_file_count": self.regular_file_count,
            "directory_count": self.directory_count,
            "uncompressed_bytes": self.uncompressed_bytes,
            "content_root_sha256": self.content_root_sha256,
            "destination_was_new": True,
            "file_mode": "0600",
            "directory_mode": "0700",
            "fsync_completed": True,
            "aggregate_only": True,
        }


@dataclass(frozen=True, slots=True)
class NamedSplitZipPart:
    """Private split-part name paired with a caller-owned read-only descriptor."""

    name: str
    descriptor: int


@dataclass(frozen=True, slots=True)
class SplitZipMergeReceipt:
    """Aggregate result of concatenating one byte-offset-ordered split ZIP set."""

    part_count: int
    last_part_offset_bytes: int
    merged_bytes: int
    merged_sha256: str
    ordered_part_root_sha256: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_split_zip_merge_receipt.v2",
            "part_count": self.part_count,
            "first_part_offset_bytes": 0,
            "last_part_offset_bytes": self.last_part_offset_bytes,
            "same_stem_validated": True,
            "offset_continuity_validated": True,
            "merged_bytes": self.merged_bytes,
            "merged_sha256": self.merged_sha256,
            "ordered_part_root_sha256": self.ordered_part_root_sha256,
            "destination_was_new": True,
            "file_mode": "0600",
            "fsync_completed": True,
            "aggregate_only": True,
        }


@dataclass(frozen=True, slots=True)
class ZipValidationReceipt:
    """Aggregate result after central-directory, payload-size, and CRC checks."""

    archive_sha256: str
    member_count: int
    regular_file_count: int
    directory_count: int
    compressed_bytes: int
    uncompressed_bytes: int
    content_root_sha256: str

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_zip_validation_receipt.v2",
            "archive_sha256": self.archive_sha256,
            "member_count": self.member_count,
            "regular_file_count": self.regular_file_count,
            "directory_count": self.directory_count,
            "compressed_bytes": self.compressed_bytes,
            "uncompressed_bytes": self.uncompressed_bytes,
            "content_root_sha256": self.content_root_sha256,
            "encrypted_members": 0,
            "links_or_special_members": 0,
            "duplicate_or_case_colliding_members": 0,
            "crc_validated": True,
            "aggregate_only": True,
        }


@dataclass(frozen=True, slots=True)
class _TarScan:
    members: tuple[tarfile.TarInfo, ...]
    file_content_sha256: tuple[str, ...]
    regular_file_count: int
    directory_count: int
    uncompressed_bytes: int
    content_root_sha256: str


@dataclass(frozen=True, slots=True)
class _ZipScan:
    infos: tuple[ZipInfo, ...]
    normalized_names: tuple[str, ...]
    file_content_sha256: tuple[str, ...]
    receipt: ZipValidationReceipt


def verify_aihub538_training_download(
    *,
    archive_descriptor: int,
    receipt_descriptor: int,
    policy: Aihub538ArchiveSecurityPolicy = DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
) -> VerifiedAihub538Download:
    """Verify the exact 538/54725+54944 receipt, byte count, and archive SHA."""

    receipt_payload = _read_bounded_descriptor(
        receipt_descriptor,
        maximum_bytes=policy.maximum_receipt_bytes,
        reason_prefix="aihub538_download_receipt",
    )
    document = _load_exact_json_object(
        receipt_payload,
        reason_code="aihub538_download_receipt_invalid",
    )
    if set(document) != _DOWNLOAD_RECEIPT_KEYS:
        raise Aihub538ArchiveError("aihub538_download_receipt_schema_invalid")
    if (
        document["schema_version"] != AIHUB538_DOWNLOAD_RECEIPT_SCHEMA
        or document["dataset_key"] != AIHUB538_TRAINING_DATASET_KEY
        or document["file_keys"] != list(AIHUB538_TRAINING_FILE_KEYS)
        or document["credential_persisted"] is not False
        or document["archive_extracted"] is not False
    ):
        raise Aihub538ArchiveError("aihub538_download_receipt_contract_mismatch")
    archive_size = _strict_positive_integer(
        document["archive_size_bytes"],
        "aihub538_download_receipt_schema_invalid",
    )
    _strict_positive_integer(
        document["download_completed_unix_seconds"],
        "aihub538_download_receipt_schema_invalid",
    )
    expected_sha256 = document["archive_sha256"]
    if not isinstance(expected_sha256, str) or not _SHA256_PATTERN.fullmatch(
        expected_sha256
    ):
        raise Aihub538ArchiveError("aihub538_download_receipt_schema_invalid")

    archive = _duplicate_read_only_regular(
        archive_descriptor,
        maximum_bytes=policy.maximum_download_bytes,
        reason_prefix="aihub538_download_archive",
    )
    try:
        metadata = os.fstat(archive)
        if metadata.st_size != archive_size:
            raise Aihub538ArchiveError("aihub538_download_size_mismatch")
        actual_sha256 = _sha256_descriptor(archive)
        if actual_sha256 != expected_sha256:
            raise Aihub538ArchiveError("aihub538_download_sha256_mismatch")
    finally:
        os.close(archive)
    return VerifiedAihub538Download(
        archive_size_bytes=archive_size,
        archive_sha256=expected_sha256,
        receipt_sha256=hashlib.sha256(receipt_payload).hexdigest(),
    )


def extract_verified_tar_descriptor(
    *,
    archive_descriptor: int,
    destination: Path,
    policy: Aihub538ArchiveSecurityPolicy = DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
) -> TarExtractionReceipt:
    """Pre-read every TAR payload, then extract into one absent private root."""

    descriptor = _duplicate_read_only_regular(
        archive_descriptor,
        maximum_bytes=policy.maximum_download_bytes,
        reason_prefix="aihub538_tar",
    )
    try:
        archive_sha256 = _sha256_descriptor(descriptor)
        scan = _scan_tar_descriptor(descriptor, policy=policy)
        root_descriptor, parent_descriptor, root_name = _create_private_root(destination)
        try:
            try:
                _extract_tar_to_root(
                    descriptor,
                    root_descriptor=root_descriptor,
                    scan=scan,
                    policy=policy,
                )
                os.fsync(root_descriptor)
                os.fsync(parent_descriptor)
                if _sha256_descriptor(descriptor) != archive_sha256:
                    raise Aihub538ArchiveError(
                        "aihub538_tar_changed_during_extraction"
                    )
            except BaseException as extraction_error:
                try:
                    _remove_created_root(
                        root_descriptor=root_descriptor,
                        parent_descriptor=parent_descriptor,
                        root_name=root_name,
                    )
                except Aihub538ArchiveError as cleanup_error:
                    raise cleanup_error from extraction_error
                raise
        finally:
            os.close(root_descriptor)
            os.close(parent_descriptor)
    finally:
        os.close(descriptor)
    return TarExtractionReceipt(
        archive_sha256=archive_sha256,
        regular_file_count=scan.regular_file_count,
        directory_count=scan.directory_count,
        uncompressed_bytes=scan.uncompressed_bytes,
        content_root_sha256=scan.content_root_sha256,
    )


def merge_split_zip_parts(
    *,
    parts: Sequence[NamedSplitZipPart],
    destination: Path,
    policy: Aihub538ArchiveSecurityPolicy = DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
) -> SplitZipMergeReceipt:
    """Concatenate one complete byte-offset split set into an absent 0600 ZIP."""

    ordered = _validate_split_part_set(parts, policy=policy)
    parent_descriptor, destination_name = _open_private_destination_parent(destination)
    output_descriptor: int | None = None
    created = False
    part_rows: list[dict[str, object]] = []
    merged_digest = hashlib.sha256()
    merged_bytes = 0
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
            raise Aihub538ArchiveError("aihub538_split_destination_not_new") from None
        os.fchmod(output_descriptor, 0o600)
        for offset_bytes, expected_part_bytes, part in ordered:
            if offset_bytes != merged_bytes:
                raise Aihub538ArchiveError(
                    "aihub538_split_part_offsets_not_contiguous"
                )
            source = _duplicate_read_only_regular(
                part.descriptor,
                maximum_bytes=policy.maximum_split_part_bytes,
                reason_prefix="aihub538_split_part",
            )
            part_digest = hashlib.sha256()
            part_bytes = 0
            try:
                while chunk := os.read(source, _CHUNK_BYTES):
                    _write_all(output_descriptor, chunk)
                    merged_digest.update(chunk)
                    part_digest.update(chunk)
                    part_bytes += len(chunk)
                    merged_bytes += len(chunk)
                    if merged_bytes > policy.maximum_merged_zip_bytes:
                        raise Aihub538ArchiveError(
                            "aihub538_split_merged_size_outside_policy"
                        )
            finally:
                os.close(source)
            if part_bytes != expected_part_bytes:
                raise Aihub538ArchiveError("aihub538_split_part_size_changed")
            part_rows.append(
                {
                    "offset_bytes": offset_bytes,
                    "bytes": part_bytes,
                    "sha256": part_digest.hexdigest(),
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
    if merged_bytes <= 0:
        raise Aihub538ArchiveError("aihub538_split_merged_file_empty")
    return SplitZipMergeReceipt(
        part_count=len(ordered),
        last_part_offset_bytes=ordered[-1][0],
        merged_bytes=merged_bytes,
        merged_sha256=merged_digest.hexdigest(),
        ordered_part_root_sha256=sha256_document(
            {
                "schema_version": "voice_coach.aihub538_ordered_split_parts.v2",
                "parts": part_rows,
            }
        ),
    )


@contextmanager
def open_validated_zip_descriptor(
    descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy = DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
) -> Iterator[tuple[ZipFile, tuple[ZipInfo, ...], tuple[str, ...], ZipValidationReceipt]]:
    """Yield one CRC-verified ZIP while retaining its private member-name index."""

    duplicate = _duplicate_read_only_regular(
        descriptor,
        maximum_bytes=policy.maximum_merged_zip_bytes,
        reason_prefix="aihub538_zip",
    )
    file_object = os.fdopen(duplicate, "rb", closefd=True)
    try:
        archive_sha256 = _sha256_file_object(file_object)
        try:
            zip_file = ZipFile(file_object, mode="r")
        except (BadZipFile, OSError):
            raise Aihub538ArchiveError("aihub538_zip_invalid") from None
        try:
            scan = _scan_zip_file(
                zip_file,
                archive_sha256=archive_sha256,
                policy=policy,
            )
            yield (
                zip_file,
                scan.infos,
                scan.normalized_names,
                scan.receipt,
            )
            if _sha256_file_object(file_object) != archive_sha256:
                raise Aihub538ArchiveError("aihub538_zip_changed_while_open")
        finally:
            zip_file.close()
    finally:
        file_object.close()


def validate_zip_descriptor(
    descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy = DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
) -> ZipValidationReceipt:
    """Return only aggregate ZIP evidence after all payload CRCs are checked."""

    with open_validated_zip_descriptor(descriptor, policy=policy) as opened:
        return opened[3]


def extract_verified_zip_descriptor(
    *,
    archive_descriptor: int,
    destination: Path,
    policy: Aihub538ArchiveSecurityPolicy = DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
) -> ZipValidationReceipt:
    """Extract an already CRC-verified ZIP into one absent private root."""

    root_descriptor: int | None = None
    parent_descriptor: int | None = None
    root_name: str | None = None
    receipt: ZipValidationReceipt | None = None
    try:
        with open_validated_zip_descriptor(
            archive_descriptor,
            policy=policy,
        ) as opened:
            zip_file, infos, normalized_names, receipt = opened
            root_descriptor, parent_descriptor, root_name = _create_private_root(
                destination
            )
            try:
                content_hash_index = iter(
                    _zip_file_hashes_by_info(infos, normalized_names, zip_file)
                )
                for info, normalized in zip(infos, normalized_names, strict=True):
                    parts = PurePosixPath(normalized).parts
                    if info.is_dir():
                        directory = _open_directory_chain(
                            root_descriptor,
                            parts,
                            create=True,
                        )
                        os.fsync(directory)
                        os.close(directory)
                        continue
                    expected_hash = next(content_hash_index)
                    parent = _open_directory_chain(
                        root_descriptor,
                        parts[:-1],
                        create=True,
                    )
                    try:
                        output = _create_private_file(parent, parts[-1])
                        digest = hashlib.sha256()
                        try:
                            with zip_file.open(info, "r") as source:
                                while chunk := source.read(_CHUNK_BYTES):
                                    _write_all(output, chunk)
                                    digest.update(chunk)
                            os.fsync(output)
                        finally:
                            os.close(output)
                        if digest.hexdigest() != expected_hash:
                            raise Aihub538ArchiveError(
                                "aihub538_zip_payload_changed"
                            )
                        os.fsync(parent)
                    finally:
                        os.close(parent)
                os.fsync(root_descriptor)
                os.fsync(parent_descriptor)
            except Aihub538ArchiveError:
                raise
            except (
                BadZipFile,
                RuntimeError,
                NotImplementedError,
                OSError,
                EOFError,
            ):
                raise Aihub538ArchiveError(
                    "aihub538_zip_extraction_failed"
                ) from None
    except BaseException as extraction_error:
        if (
            root_descriptor is not None
            and parent_descriptor is not None
            and root_name is not None
        ):
            try:
                _remove_created_root(
                    root_descriptor=root_descriptor,
                    parent_descriptor=parent_descriptor,
                    root_name=root_name,
                )
            except Aihub538ArchiveError as cleanup_error:
                raise cleanup_error from extraction_error
        raise
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
    if receipt is None:
        raise AssertionError("unreachable missing ZIP validation receipt")
    return receipt


def _scan_tar_descriptor(
    descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> _TarScan:
    os.lseek(descriptor, 0, os.SEEK_SET)
    duplicate = os.dup(descriptor)
    try:
        with os.fdopen(duplicate, "rb", closefd=True) as source:
            duplicate = -1
            try:
                archive = tarfile.open(fileobj=source, mode="r:*")
            except (tarfile.TarError, OSError):
                raise Aihub538ArchiveError("aihub538_tar_invalid") from None
            with archive:
                member_list: list[tarfile.TarInfo] = []
                seen: set[str] = set()
                file_hashes: list[str] = []
                content_rows: list[dict[str, object]] = []
                regular_files = 0
                directories = 0
                total_bytes = 0
                for member in archive:
                    member_list.append(member)
                    if len(member_list) > policy.maximum_tar_member_count:
                        raise Aihub538ArchiveError(
                            "aihub538_tar_member_count_outside_policy"
                        )
                    normalized = _safe_archive_name(member.name, policy=policy)
                    folded = normalized.casefold()
                    if folded in seen:
                        raise Aihub538ArchiveError(
                            "aihub538_tar_duplicate_or_case_collision"
                        )
                    seen.add(folded)
                    if member.isfile():
                        if not 0 < member.size <= policy.maximum_tar_member_bytes:
                            raise Aihub538ArchiveError(
                                "aihub538_tar_member_size_outside_policy"
                            )
                        total_bytes += member.size
                        if total_bytes > policy.maximum_tar_uncompressed_bytes:
                            raise Aihub538ArchiveError(
                                "aihub538_tar_total_size_outside_policy"
                            )
                        payload = archive.extractfile(member)
                        if payload is None:
                            raise Aihub538ArchiveError("aihub538_tar_payload_missing")
                        digest, observed_bytes = _sha256_stream(payload)
                        if observed_bytes != member.size:
                            raise Aihub538ArchiveError("aihub538_tar_payload_truncated")
                        file_hashes.append(digest)
                        content_rows.append(
                            _content_root_row(
                                normalized_path=normalized,
                                size_bytes=member.size,
                                content_sha256=digest,
                            )
                        )
                        regular_files += 1
                    elif member.isdir():
                        directories += 1
                    else:
                        raise Aihub538ArchiveError(
                            "aihub538_tar_link_or_special_member_forbidden"
                        )
                if regular_files == 0:
                    raise Aihub538ArchiveError("aihub538_tar_regular_files_missing")
                members = tuple(member_list)
    except Aihub538ArchiveError:
        raise
    except (tarfile.TarError, OSError, UnicodeError):
        raise Aihub538ArchiveError("aihub538_tar_invalid") from None
    finally:
        if duplicate >= 0:
            os.close(duplicate)
    return _TarScan(
        members=members,
        file_content_sha256=tuple(file_hashes),
        regular_file_count=regular_files,
        directory_count=directories,
        uncompressed_bytes=total_bytes,
        content_root_sha256=sha256_document(
            {
                "schema_version": "voice_coach.aihub538_tar_content_root.v2",
                "entries": sorted(
                    content_rows,
                    key=lambda row: str(row["path_sha256"]),
                ),
                "regular_file_count": regular_files,
                "uncompressed_bytes": total_bytes,
            }
        ),
    )


def _extract_tar_to_root(
    descriptor: int,
    *,
    root_descriptor: int,
    scan: _TarScan,
    policy: Aihub538ArchiveSecurityPolicy,
) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    duplicate = os.dup(descriptor)
    file_hash_index = iter(scan.file_content_sha256)
    try:
        with os.fdopen(duplicate, "rb", closefd=True) as source:
            duplicate = -1
            with tarfile.open(fileobj=source, mode="r:*") as archive:
                observed_member_count = 0
                for member in archive:
                    if observed_member_count >= len(scan.members):
                        raise Aihub538ArchiveError(
                            "aihub538_tar_changed_after_scan"
                        )
                    scanned_member = scan.members[observed_member_count]
                    observed_member_count += 1
                    if (
                        member.name != scanned_member.name
                        or member.type != scanned_member.type
                        or member.size != scanned_member.size
                    ):
                        raise Aihub538ArchiveError(
                            "aihub538_tar_changed_after_scan"
                        )
                    normalized = _safe_archive_name(member.name, policy=policy)
                    parts = PurePosixPath(normalized).parts
                    if member.isdir():
                        directory = _open_directory_chain(
                            root_descriptor,
                            parts,
                            create=True,
                        )
                        os.fsync(directory)
                        os.close(directory)
                        continue
                    expected_hash = next(file_hash_index)
                    payload = archive.extractfile(member)
                    if payload is None:
                        raise Aihub538ArchiveError("aihub538_tar_payload_missing")
                    parent = _open_directory_chain(
                        root_descriptor,
                        parts[:-1],
                        create=True,
                    )
                    try:
                        output = _create_private_file(parent, parts[-1])
                        digest = hashlib.sha256()
                        observed = 0
                        try:
                            while chunk := payload.read(_CHUNK_BYTES):
                                _write_all(output, chunk)
                                digest.update(chunk)
                                observed += len(chunk)
                            os.fsync(output)
                        finally:
                            os.close(output)
                        if (
                            observed != member.size
                            or digest.hexdigest() != expected_hash
                        ):
                            raise Aihub538ArchiveError(
                                "aihub538_tar_payload_changed_after_scan"
                            )
                        os.fsync(parent)
                    finally:
                        os.close(parent)
                if observed_member_count != len(scan.members):
                    raise Aihub538ArchiveError("aihub538_tar_changed_after_scan")
    except Aihub538ArchiveError:
        raise
    except (tarfile.TarError, OSError):
        raise Aihub538ArchiveError("aihub538_tar_extraction_failed") from None
    finally:
        if duplicate >= 0:
            os.close(duplicate)


def _scan_zip_file(
    zip_file: ZipFile,
    *,
    archive_sha256: str,
    policy: Aihub538ArchiveSecurityPolicy,
) -> _ZipScan:
    try:
        infos = tuple(zip_file.infolist())
    except (BadZipFile, OSError):
        raise Aihub538ArchiveError("aihub538_zip_invalid") from None
    if not 1 <= len(infos) <= policy.maximum_zip_member_count:
        raise Aihub538ArchiveError("aihub538_zip_member_count_outside_policy")
    seen: set[str] = set()
    normalized_names: list[str] = []
    compressed_bytes = 0
    uncompressed_bytes = 0
    regular_files = 0
    directories = 0
    for info in infos:
        normalized = _safe_zip_info(info, policy=policy)
        folded = normalized.casefold()
        if folded in seen:
            raise Aihub538ArchiveError(
                "aihub538_zip_duplicate_or_case_collision"
            )
        seen.add(folded)
        normalized_names.append(normalized)
        if info.is_dir():
            directories += 1
            continue
        if info.flag_bits & 0x1:
            raise Aihub538ArchiveError("aihub538_zip_encrypted_member_forbidden")
        if not 0 < info.file_size <= policy.maximum_zip_member_bytes:
            raise Aihub538ArchiveError("aihub538_zip_member_size_outside_policy")
        if info.compress_size <= 0:
            raise Aihub538ArchiveError(
                "aihub538_zip_compression_ratio_outside_policy"
            )
        ratio = info.file_size / info.compress_size
        if ratio > policy.maximum_zip_compression_ratio:
            raise Aihub538ArchiveError(
                "aihub538_zip_compression_ratio_outside_policy"
            )
        compressed_bytes += info.compress_size
        uncompressed_bytes += info.file_size
        regular_files += 1
        if uncompressed_bytes > policy.maximum_zip_uncompressed_bytes:
            raise Aihub538ArchiveError("aihub538_zip_total_size_outside_policy")
    if regular_files == 0:
        raise Aihub538ArchiveError("aihub538_zip_regular_files_missing")

    file_hashes: list[str] = []
    content_rows: list[dict[str, object]] = []
    try:
        for info, normalized in zip(infos, normalized_names, strict=True):
            if info.is_dir():
                continue
            with zip_file.open(info, "r") as source:
                digest, observed_bytes = _sha256_stream(source)
            if observed_bytes != info.file_size:
                raise Aihub538ArchiveError("aihub538_zip_payload_size_mismatch")
            file_hashes.append(digest)
            content_rows.append(
                _content_root_row(
                    normalized_path=normalized,
                    size_bytes=info.file_size,
                    content_sha256=digest,
                )
            )
    except Aihub538ArchiveError:
        raise
    except (BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError):
        raise Aihub538ArchiveError("aihub538_zip_crc_or_payload_invalid") from None
    receipt = ZipValidationReceipt(
        archive_sha256=archive_sha256,
        member_count=len(infos),
        regular_file_count=regular_files,
        directory_count=directories,
        compressed_bytes=compressed_bytes,
        uncompressed_bytes=uncompressed_bytes,
        content_root_sha256=sha256_document(
            {
                "schema_version": "voice_coach.aihub538_zip_content_root.v2",
                "entries": sorted(
                    content_rows,
                    key=lambda row: str(row["path_sha256"]),
                ),
                "regular_file_count": regular_files,
                "uncompressed_bytes": uncompressed_bytes,
            }
        ),
    )
    return _ZipScan(
        infos=infos,
        normalized_names=tuple(normalized_names),
        file_content_sha256=tuple(file_hashes),
        receipt=receipt,
    )


def _zip_file_hashes_by_info(
    infos: tuple[ZipInfo, ...],
    normalized_names: tuple[str, ...],
    zip_file: ZipFile,
) -> tuple[str, ...]:
    if len(infos) != len(normalized_names):
        raise Aihub538ArchiveError("aihub538_zip_changed_after_scan")
    hashes: list[str] = []
    try:
        for info in infos:
            if info.is_dir():
                continue
            with zip_file.open(info, "r") as source:
                digest, observed = _sha256_stream(source)
            if observed != info.file_size:
                raise Aihub538ArchiveError("aihub538_zip_payload_changed")
            hashes.append(digest)
    except Aihub538ArchiveError:
        raise
    except (BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError):
        raise Aihub538ArchiveError("aihub538_zip_payload_changed") from None
    return tuple(hashes)


def _safe_archive_name(
    name: str,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> str:
    try:
        encoded_length = len(name.encode("utf-8")) if isinstance(name, str) else 0
    except UnicodeEncodeError:
        raise Aihub538ArchiveError("aihub538_archive_member_path_invalid") from None
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or "\\" in name
        or encoded_length > policy.maximum_path_bytes
    ):
        raise Aihub538ArchiveError("aihub538_archive_member_path_invalid")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise Aihub538ArchiveError("aihub538_archive_member_path_invalid")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if (
        not parts
        or len(parts) > policy.maximum_path_depth
        or any(part in {"", ".", ".."} for part in parts)
        or ":" in parts[0]
    ):
        raise Aihub538ArchiveError("aihub538_archive_member_path_invalid")
    return PurePosixPath(*parts).as_posix()


def _safe_zip_info(
    info: ZipInfo,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> str:
    normalized = _safe_archive_name(info.filename, policy=policy)
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise Aihub538ArchiveError(
            "aihub538_zip_link_or_special_member_forbidden"
        )
    if info.is_dir() and file_type not in {0, stat.S_IFDIR}:
        raise Aihub538ArchiveError(
            "aihub538_zip_link_or_special_member_forbidden"
        )
    if not info.is_dir() and file_type == stat.S_IFDIR:
        raise Aihub538ArchiveError(
            "aihub538_zip_link_or_special_member_forbidden"
        )
    return normalized


def _content_root_row(
    *,
    normalized_path: str,
    size_bytes: int,
    content_sha256: str,
) -> dict[str, object]:
    """Bind a private normalized path to size and content without exposing it."""

    path_sha256 = hashlib.sha256(
        b"voice_coach.aihub538.archive_relative_path.v1\x00"
        + normalized_path.encode("utf-8")
    ).hexdigest()
    return {
        "path_sha256": path_sha256,
        "size_bytes": size_bytes,
        "content_sha256": content_sha256,
    }


def _validate_split_part_set(
    parts: Sequence[NamedSplitZipPart],
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> tuple[tuple[int, int, NamedSplitZipPart], ...]:
    if not 1 <= len(parts) <= policy.maximum_split_part_count:
        raise Aihub538ArchiveError("aihub538_split_part_count_outside_policy")
    offset_parts: list[tuple[int, int, NamedSplitZipPart]] = []
    expected_stem: str | None = None
    for part in parts:
        if not isinstance(part, NamedSplitZipPart):
            raise Aihub538ArchiveError("aihub538_split_part_contract_invalid")
        match = _SPLIT_PART_PATTERN.fullmatch(part.name)
        if match is None:
            raise Aihub538ArchiveError("aihub538_split_part_name_invalid")
        stem = match.group("stem")
        if expected_stem is not None and stem != expected_stem:
            raise Aihub538ArchiveError("aihub538_split_part_set_mismatch")
        expected_stem = stem
        source = _duplicate_read_only_regular(
            part.descriptor,
            maximum_bytes=policy.maximum_split_part_bytes,
            reason_prefix="aihub538_split_part",
        )
        try:
            part_bytes = os.fstat(source).st_size
        finally:
            os.close(source)
        offset_parts.append((int(match.group("offset")), part_bytes, part))
    offset_parts.sort(key=lambda row: row[0])
    expected_offset = 0
    for offset_bytes, part_bytes, _part in offset_parts:
        if offset_bytes != expected_offset:
            raise Aihub538ArchiveError(
                "aihub538_split_part_offsets_not_contiguous"
            )
        expected_offset += part_bytes
        if expected_offset > policy.maximum_merged_zip_bytes:
            raise Aihub538ArchiveError(
                "aihub538_split_merged_size_outside_policy"
            )
    return tuple(offset_parts)


def _create_private_root(destination: Path) -> tuple[int, int, str]:
    parent_descriptor, root_name = _open_private_destination_parent(destination)
    root_descriptor: int | None = None
    created = False
    try:
        os.mkdir(root_name, 0o700, dir_fd=parent_descriptor)
        created = True
        root_descriptor = os.open(
            root_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        os.fchmod(root_descriptor, 0o700)
        os.fsync(parent_descriptor)
    except OSError:
        if root_descriptor is not None:
            os.close(root_descriptor)
        if created:
            try:
                os.rmdir(root_name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError:
                pass
        os.close(parent_descriptor)
        raise Aihub538ArchiveError("aihub538_extraction_destination_not_new") from None
    if root_descriptor is None:
        os.close(parent_descriptor)
        raise AssertionError("unreachable missing extraction root descriptor")
    return root_descriptor, parent_descriptor, root_name


def _remove_created_root(
    *,
    root_descriptor: int,
    parent_descriptor: int,
    root_name: str,
) -> None:
    """Remove only the fresh extraction root through already-open dirfds."""

    try:
        _remove_directory_contents(root_descriptor)
        os.rmdir(root_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        try:
            os.stat(root_name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise Aihub538ArchiveError("aihub538_extraction_cleanup_failed")
    except Aihub538ArchiveError:
        raise
    except OSError:
        raise Aihub538ArchiveError("aihub538_extraction_cleanup_failed") from None


def _remove_directory_contents(directory_descriptor: int) -> None:
    """Recursively unlink entries without resolving an archive-controlled path."""

    try:
        entry_names = os.listdir(directory_descriptor)
    except OSError:
        raise Aihub538ArchiveError("aihub538_extraction_cleanup_failed") from None
    for entry_name in entry_names:
        try:
            metadata = os.stat(
                entry_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISDIR(metadata.st_mode):
                child_descriptor = os.open(
                    entry_name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_descriptor,
                )
                try:
                    _remove_directory_contents(child_descriptor)
                finally:
                    os.close(child_descriptor)
                os.rmdir(entry_name, dir_fd=directory_descriptor)
            else:
                os.unlink(entry_name, dir_fd=directory_descriptor)
        except Aihub538ArchiveError:
            raise
        except OSError:
            raise Aihub538ArchiveError(
                "aihub538_extraction_cleanup_failed"
            ) from None
    try:
        os.fsync(directory_descriptor)
    except OSError:
        raise Aihub538ArchiveError("aihub538_extraction_cleanup_failed") from None


def _open_private_destination_parent(destination: Path) -> tuple[int, str]:
    if not destination.is_absolute() or destination.name in {"", ".", ".."}:
        raise Aihub538ArchiveError("aihub538_destination_invalid")
    parent = destination.parent
    try:
        if parent.resolve(strict=True) != parent:
            raise Aihub538ArchiveError("aihub538_destination_parent_not_canonical")
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except Aihub538ArchiveError:
        raise
    except OSError:
        raise Aihub538ArchiveError("aihub538_destination_parent_invalid") from None
    return descriptor, destination.name


def _open_directory_chain(
    root_descriptor: int,
    parts: Sequence[str],
    *,
    create: bool,
) -> int:
    current = os.dup(root_descriptor)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=current)
                    os.fsync(current)
                except FileExistsError:
                    pass
                except OSError:
                    raise Aihub538ArchiveError(
                        "aihub538_extraction_directory_creation_failed"
                    ) from None
            try:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
            except OSError:
                raise Aihub538ArchiveError(
                    "aihub538_extraction_directory_invalid"
                ) from None
            os.close(current)
            current = child
            os.fchmod(current, 0o700)
        return current
    except Exception:
        os.close(current)
        raise


def _create_private_file(parent_descriptor: int, name: str) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o600)
        return descriptor
    except OSError:
        raise Aihub538ArchiveError("aihub538_extraction_file_not_new") from None


def _duplicate_read_only_regular(
    descriptor: int,
    *,
    maximum_bytes: int,
    reason_prefix: str,
) -> int:
    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
        raise Aihub538ArchiveError(f"{reason_prefix}_descriptor_invalid")
    try:
        duplicate = os.dup(descriptor)
        metadata = os.fstat(duplicate)
        access_mode = fcntl.fcntl(duplicate, fcntl.F_GETFL) & os.O_ACCMODE
        if (
            not stat.S_ISREG(metadata.st_mode)
            or access_mode != os.O_RDONLY
            or metadata.st_size <= 0
            or metadata.st_size > maximum_bytes
            or (stat.S_IMODE(metadata.st_mode) & 0o077) != 0
        ):
            raise Aihub538ArchiveError(f"{reason_prefix}_descriptor_invalid")
        os.lseek(duplicate, 0, os.SEEK_SET)
        return duplicate
    except Aihub538ArchiveError:
        try:
            os.close(duplicate)
        except (OSError, UnboundLocalError):
            pass
        raise
    except OSError:
        raise Aihub538ArchiveError(f"{reason_prefix}_descriptor_invalid") from None


def _read_bounded_descriptor(
    descriptor: int,
    *,
    maximum_bytes: int,
    reason_prefix: str,
) -> bytes:
    duplicate = _duplicate_read_only_regular(
        descriptor,
        maximum_bytes=maximum_bytes,
        reason_prefix=reason_prefix,
    )
    try:
        expected = os.fstat(duplicate).st_size
        chunks: list[bytes] = []
        observed = 0
        while chunk := os.read(duplicate, min(_CHUNK_BYTES, expected - observed)):
            chunks.append(chunk)
            observed += len(chunk)
        if observed != expected:
            raise Aihub538ArchiveError(f"{reason_prefix}_truncated")
        return b"".join(chunks)
    finally:
        os.close(duplicate)


def _sha256_descriptor(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, _CHUNK_BYTES):
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _sha256_file_object(file_object: BinaryIO) -> str:
    file_object.seek(0)
    digest = hashlib.sha256()
    while chunk := file_object.read(_CHUNK_BYTES):
        digest.update(chunk)
    file_object.seek(0)
    return digest.hexdigest()


def _sha256_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    observed = 0
    while chunk := stream.read(_CHUNK_BYTES):
        digest.update(chunk)
        observed += len(chunk)
    return digest.hexdigest(), observed


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError:
            raise Aihub538ArchiveError("aihub538_storage_write_failed") from None
        if written <= 0:
            raise Aihub538ArchiveError("aihub538_storage_write_failed")
        view = view[written:]


class _DuplicateJsonKeyError(ValueError):
    pass


def _load_exact_json_object(payload: bytes, *, reason_code: str) -> dict[str, object]:
    def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJsonKeyError(key)
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKeyError, ValueError):
        raise Aihub538ArchiveError(reason_code) from None
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise Aihub538ArchiveError(reason_code)
    return value


def _strict_positive_integer(value: object, reason_code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Aihub538ArchiveError(reason_code)
    return value


__all__ = [
    "AIHUB538_TRAINING_DATASET_KEY",
    "AIHUB538_TRAINING_FILE_KEYS",
    "Aihub538ArchiveError",
    "Aihub538ArchiveSecurityPolicy",
    "DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY",
    "NamedSplitZipPart",
    "SplitZipMergeReceipt",
    "TarExtractionReceipt",
    "VerifiedAihub538Download",
    "ZipValidationReceipt",
    "extract_verified_tar_descriptor",
    "extract_verified_zip_descriptor",
    "merge_split_zip_parts",
    "open_validated_zip_descriptor",
    "validate_zip_descriptor",
    "verify_aihub538_training_download",
]
