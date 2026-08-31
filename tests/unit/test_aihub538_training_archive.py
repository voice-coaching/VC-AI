"""Security tests for the AI Hub 538 non-locked training archive boundary."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import warnings
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from voice_coach.visual_feedback.adapters import aihub538_training_archive as archive_module
from voice_coach.visual_feedback.adapters.aihub538_training_archive import (
    AIHUB538_LOCKED_FILE_KEYS,
    AIHUB538_TRAINING_FILE_KEYS,
    Aihub538ArchiveError,
    Aihub538ArchiveSecurityPolicy,
    NamedSplitZipPart,
    extract_verified_tar_descriptor,
    extract_verified_zip_descriptor,
    merge_split_zip_parts,
    validate_zip_descriptor,
    verify_aihub538_locked_download,
    verify_aihub538_training_download,
)
from voice_coach.visual_feedback.models.integrity import sha256_document


def _private(path: Path) -> Path:
    if not path.exists():
        path.touch(mode=0o600)
    path.chmod(0o600)
    return path


def _readonly(stack: ExitStack, path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    stack.callback(os.close, descriptor)
    return descriptor


def _download_fixture(
    tmp_path: Path,
    *,
    file_keys: tuple[str, ...] = AIHUB538_TRAINING_FILE_KEYS,
) -> tuple[Path, Path]:
    archive = _private(tmp_path / "download.tar")
    archive.write_bytes(b"completed aggregate tar fixture")
    archive.chmod(0o600)
    receipt = tmp_path / "download.receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "voice_coach.aihub_secure_download_receipt.v1",
                "dataset_key": "538",
                "file_keys": list(file_keys),
                "archive_size_bytes": archive.stat().st_size,
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "download_completed_unix_seconds": 1_787_856_000,
                "credential_persisted": False,
                "archive_extracted": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o400)
    return archive, receipt


def _tar_fixture(path: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> Path:
    with tarfile.open(path, "w") as archive:
        for info, payload in members:
            archive.addfile(info, io.BytesIO(payload) if info.isfile() else None)
    return _private(path)


def _regular_tar_info(name: str, payload: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o777
    return info


def _zip_fixture(
    path: Path,
    members: list[tuple[str | ZipInfo, bytes]],
    *,
    compression: int = ZIP_STORED,
) -> Path:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(path, "w", compression=compression) as archive:
            for name, payload in members:
                archive.writestr(name, payload)
    return _private(path)


def test_secure_download_receipt_binds_exact_dataset_keys_size_and_sha(
    tmp_path: Path,
) -> None:
    archive, receipt = _download_fixture(tmp_path)
    with ExitStack() as stack:
        result = verify_aihub538_training_download(
            archive_descriptor=_readonly(stack, archive),
            receipt_descriptor=_readonly(stack, receipt),
        )

    document = result.to_json_object()
    assert document["dataset_id"] == 538
    assert document["file_keys"] == ["54725", "54944"]
    assert document["archive_sha256"] == hashlib.sha256(
        archive.read_bytes()
    ).hexdigest()
    assert document["credential_persisted"] is False
    assert document["locked_partition_accessed"] is False


def test_locked_download_receipt_binds_exact_order_size_and_sha_aggregate_only(
    tmp_path: Path,
) -> None:
    archive, receipt = _download_fixture(
        tmp_path,
        file_keys=AIHUB538_LOCKED_FILE_KEYS,
    )
    with ExitStack() as stack:
        result = verify_aihub538_locked_download(
            archive_descriptor=_readonly(stack, archive),
            receipt_descriptor=_readonly(stack, receipt),
        )

    document = result.to_json_object()
    assert document == {
        "schema_version": "voice_coach.aihub538_locked_download_verification.v1",
        "dataset_id": 538,
        "dataset_partition": "locked_evaluation_vs11_vl11",
        "file_keys": ["55114", "55082"],
        "archive_size_bytes": archive.stat().st_size,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "secure_download_receipt_sha256": hashlib.sha256(
            receipt.read_bytes()
        ).hexdigest(),
        "credential_persisted": False,
        "locked_partition_accessed": True,
        "aggregate_only": True,
    }


@pytest.mark.parametrize(
    ("verifier_name", "file_keys"),
    (
        ("locked", AIHUB538_TRAINING_FILE_KEYS),
        ("training", AIHUB538_LOCKED_FILE_KEYS),
        ("locked", tuple(reversed(AIHUB538_LOCKED_FILE_KEYS))),
        ("training", tuple(reversed(AIHUB538_TRAINING_FILE_KEYS))),
    ),
)
def test_download_verifiers_reject_cross_partition_and_reversed_key_contracts(
    tmp_path: Path,
    verifier_name: str,
    file_keys: tuple[str, ...],
) -> None:
    archive, receipt = _download_fixture(tmp_path, file_keys=file_keys)
    verifier = (
        verify_aihub538_locked_download
        if verifier_name == "locked"
        else verify_aihub538_training_download
    )
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        verifier(
            archive_descriptor=_readonly(stack, archive),
            receipt_descriptor=_readonly(stack, receipt),
        )

    assert caught.value.reason_code == "aihub538_download_receipt_contract_mismatch"


def test_locked_download_verifier_rejects_archive_digest_mismatch(
    tmp_path: Path,
) -> None:
    archive, receipt = _download_fixture(
        tmp_path,
        file_keys=AIHUB538_LOCKED_FILE_KEYS,
    )
    document = json.loads(receipt.read_text(encoding="utf-8"))
    document["archive_sha256"] = "0" * 64
    receipt.chmod(0o600)
    receipt.write_text(json.dumps(document), encoding="utf-8")
    receipt.chmod(0o400)

    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        verify_aihub538_locked_download(
            archive_descriptor=_readonly(stack, archive),
            receipt_descriptor=_readonly(stack, receipt),
        )

    assert caught.value.reason_code == "aihub538_download_sha256_mismatch"


@pytest.mark.parametrize(
    ("field", "value", "reason_code"),
    (
        ("dataset_key", "539", "aihub538_download_receipt_contract_mismatch"),
        ("file_keys", ["55114", "55082"], "aihub538_download_receipt_contract_mismatch"),
        ("archive_size_bytes", 1, "aihub538_download_size_mismatch"),
        ("archive_sha256", "0" * 64, "aihub538_download_sha256_mismatch"),
    ),
)
def test_secure_download_receipt_rejects_contract_or_integrity_mismatch(
    tmp_path: Path,
    field: str,
    value: object,
    reason_code: str,
) -> None:
    archive, receipt = _download_fixture(tmp_path)
    document = json.loads(receipt.read_text(encoding="utf-8"))
    document[field] = value
    receipt.chmod(0o600)
    receipt.write_text(json.dumps(document), encoding="utf-8")
    receipt.chmod(0o400)
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        verify_aihub538_training_download(
            archive_descriptor=_readonly(stack, archive),
            receipt_descriptor=_readonly(stack, receipt),
        )
    assert caught.value.reason_code == reason_code


def test_secure_download_receipt_rejects_duplicate_json_key(tmp_path: Path) -> None:
    archive, receipt = _download_fixture(tmp_path)
    payload = receipt.read_text(encoding="utf-8")
    receipt.chmod(0o600)
    receipt.write_text(
        payload.replace('"dataset_key":"538"', '"dataset_key":"538","dataset_key":"538"'),
        encoding="utf-8",
    )
    receipt.chmod(0o400)
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        verify_aihub538_training_download(
            archive_descriptor=_readonly(stack, archive),
            receipt_descriptor=_readonly(stack, receipt),
        )
    assert caught.value.reason_code == "aihub538_download_receipt_invalid"


def test_tar_extract_is_new_private_fsynced_and_aggregate_only(tmp_path: Path) -> None:
    payloads = (("private/a.bin", b"alpha"), ("private/nested/b.bin", b"beta"))
    archive = _tar_fixture(
        tmp_path / "outer.tar",
        [(_regular_tar_info(name, payload), payload) for name, payload in payloads],
    )
    destination = tmp_path / "extracted"
    with ExitStack() as stack:
        receipt = extract_verified_tar_descriptor(
            archive_descriptor=_readonly(stack, archive),
            destination=destination,
        )

    assert (destination / "private/a.bin").read_bytes() == b"alpha"
    assert stat.S_IMODE((destination / "private/a.bin").stat().st_mode) == 0o600
    assert stat.S_IMODE((destination / "private").stat().st_mode) == 0o700
    serialized = json.dumps(receipt.to_json_object(), sort_keys=True)
    assert "private/a.bin" not in serialized
    assert receipt.regular_file_count == 2
    assert receipt.uncompressed_bytes == 9


def test_tar_content_root_binds_hashed_relative_path_size_and_content(
    tmp_path: Path,
) -> None:
    path = "private/a.bin"
    payload = b"alpha"
    archive = _tar_fixture(
        tmp_path / "outer.tar",
        [(_regular_tar_info(path, payload), payload)],
    )
    with ExitStack() as stack:
        receipt = extract_verified_tar_descriptor(
            archive_descriptor=_readonly(stack, archive),
            destination=tmp_path / "extracted",
        )
    expected = sha256_document(
        {
            "schema_version": "voice_coach.aihub538_tar_content_root.v2",
            "entries": [
                {
                    "path_sha256": hashlib.sha256(
                        b"voice_coach.aihub538.archive_relative_path.v1\x00"
                        + path.encode("utf-8")
                    ).hexdigest(),
                    "size_bytes": len(payload),
                    "content_sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
            "regular_file_count": 1,
            "uncompressed_bytes": len(payload),
        }
    )
    assert receipt.content_root_sha256 == expected
    assert path not in json.dumps(receipt.to_json_object(), sort_keys=True)


def test_tar_content_root_changes_when_only_relative_path_changes(
    tmp_path: Path,
) -> None:
    payload = b"same-content"
    roots = []
    for index, member_name in enumerate(("first.bin", "second.bin")):
        archive = _tar_fixture(
            tmp_path / f"outer-{index}.tar",
            [(_regular_tar_info(member_name, payload), payload)],
        )
        with ExitStack() as stack:
            receipt = extract_verified_tar_descriptor(
                archive_descriptor=_readonly(stack, archive),
                destination=tmp_path / f"extracted-{index}",
            )
        roots.append(receipt.content_root_sha256)
    assert roots[0] != roots[1]


@pytest.mark.parametrize(
    "members",
    (
        [("../escape", b"x")],
        [("a", b"x"), ("a", b"y")],
        [("Case", b"x"), ("case", b"y")],
    ),
)
def test_tar_rejects_traversal_duplicate_and_case_collision(
    tmp_path: Path,
    members: list[tuple[str, bytes]],
) -> None:
    archive = _tar_fixture(
        tmp_path / "unsafe.tar",
        [(_regular_tar_info(name, payload), payload) for name, payload in members],
    )
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError):
        extract_verified_tar_descriptor(
            archive_descriptor=_readonly(stack, archive),
            destination=tmp_path / "must-not-extract",
        )
    assert not (tmp_path / "must-not-extract").exists()


@pytest.mark.parametrize("member_type", (tarfile.SYMTYPE, tarfile.FIFOTYPE))
def test_tar_rejects_links_and_special_members(
    tmp_path: Path,
    member_type: bytes,
) -> None:
    info = tarfile.TarInfo("unsafe")
    info.type = member_type
    if member_type == tarfile.SYMTYPE:
        info.linkname = "target"
    archive = _tar_fixture(tmp_path / "unsafe.tar", [(info, b"")])
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        extract_verified_tar_descriptor(
            archive_descriptor=_readonly(stack, archive),
            destination=tmp_path / "must-not-extract",
        )
    assert caught.value.reason_code == "aihub538_tar_link_or_special_member_forbidden"


def test_tar_rejects_member_outside_bound(tmp_path: Path) -> None:
    archive = _tar_fixture(
        tmp_path / "large.tar",
        [(_regular_tar_info("large.bin", b"1234"), b"1234")],
    )
    policy = replace(
        Aihub538ArchiveSecurityPolicy(),
        maximum_tar_member_bytes=3,
    )
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        extract_verified_tar_descriptor(
            archive_descriptor=_readonly(stack, archive),
            destination=tmp_path / "must-not-extract",
            policy=policy,
        )
    assert caught.value.reason_code == "aihub538_tar_member_size_outside_policy"


def test_split_zip_merge_uses_byte_offsets_and_private_output(
    tmp_path: Path,
) -> None:
    first = _private(tmp_path / "fixture.zip.part0")
    first.write_bytes(b"abc")
    first.chmod(0o600)
    second = _private(tmp_path / "fixture.zip.part3")
    second.write_bytes(b"de")
    second.chmod(0o600)
    third = _private(tmp_path / "fixture.zip.part5")
    third.write_bytes(b"fghi")
    third.chmod(0o600)
    destination = tmp_path / "merged.zip"
    with ExitStack() as stack:
        receipt = merge_split_zip_parts(
            parts=(
                NamedSplitZipPart(first.name, _readonly(stack, first)),
                NamedSplitZipPart(second.name, _readonly(stack, second)),
                NamedSplitZipPart(third.name, _readonly(stack, third)),
            ),
            destination=destination,
        )
    assert destination.read_bytes() == b"abcdefghi"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert receipt.part_count == 3
    assert receipt.merged_sha256 == hashlib.sha256(b"abcdefghi").hexdigest()
    public_receipt = receipt.to_json_object()
    assert public_receipt["schema_version"].endswith(".v2")
    assert public_receipt["first_part_offset_bytes"] == 0
    assert public_receipt["last_part_offset_bytes"] == 5
    assert public_receipt["offset_continuity_validated"] is True
    assert "first_part_index" not in public_receipt
    assert "last_part_index" not in public_receipt
    assert "continuous_indices_validated" not in public_receipt
    assert "fixture" not in json.dumps(public_receipt, sort_keys=True)


def test_split_zip_merge_accepts_more_than_ten_variable_width_byte_offsets(
    tmp_path: Path,
) -> None:
    paths: list[Path] = []
    for offset_bytes in range(12):
        path = _private(tmp_path / f"fixture.zip.part{offset_bytes}")
        path.write_bytes(bytes([offset_bytes]))
        path.chmod(0o600)
        paths.append(path)
    destination = tmp_path / "merged.zip"
    with ExitStack() as stack:
        receipt = merge_split_zip_parts(
            parts=tuple(
                NamedSplitZipPart(path.name, _readonly(stack, path))
                for path in reversed(paths)
            ),
            destination=destination,
        )
    assert destination.read_bytes() == bytes(range(12))
    assert receipt.part_count == 12
    assert receipt.last_part_offset_bytes == 11


@pytest.mark.parametrize(
    ("names", "reason_code"),
    (
        (
            ("fixture.zip.part0", "fixture.zip.part2"),
            "aihub538_split_part_offsets_not_contiguous",
        ),
        (
            ("first.zip.part0", "second.zip.part1"),
            "aihub538_split_part_set_mismatch",
        ),
    ),
)
def test_split_zip_merge_rejects_offset_gap_or_stem_mismatch(
    tmp_path: Path,
    names: tuple[str, str],
    reason_code: str,
) -> None:
    paths = []
    for index, name in enumerate(names):
        path = tmp_path / name
        path.write_bytes(bytes([index + 1]))
        path.chmod(0o600)
        paths.append(path)
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        merge_split_zip_parts(
            parts=tuple(
                NamedSplitZipPart(path.name, _readonly(stack, path))
                for path in paths
            ),
            destination=tmp_path / "merged.zip",
        )
    assert caught.value.reason_code == reason_code
    assert not (tmp_path / "merged.zip").exists()


@pytest.mark.parametrize("ambiguous_offset", ("00", "01"))
def test_split_zip_merge_rejects_leading_zero_offset(
    tmp_path: Path,
    ambiguous_offset: str,
) -> None:
    path = _private(tmp_path / f"fixture.zip.part{ambiguous_offset}")
    path.write_bytes(b"x")
    path.chmod(0o600)
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        merge_split_zip_parts(
            parts=(NamedSplitZipPart(path.name, _readonly(stack, path)),),
            destination=tmp_path / "merged.zip",
        )
    assert caught.value.reason_code == "aihub538_split_part_name_invalid"
    assert not (tmp_path / "merged.zip").exists()


def test_split_zip_merge_rejects_duplicate_offsets(tmp_path: Path) -> None:
    first = _private(tmp_path / "first.bin")
    first.write_bytes(b"a")
    first.chmod(0o600)
    duplicate = _private(tmp_path / "duplicate.bin")
    duplicate.write_bytes(b"b")
    duplicate.chmod(0o600)
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        merge_split_zip_parts(
            parts=(
                NamedSplitZipPart("fixture.zip.part0", _readonly(stack, first)),
                NamedSplitZipPart(
                    "fixture.zip.part0", _readonly(stack, duplicate)
                ),
            ),
            destination=tmp_path / "merged.zip",
        )
    assert caught.value.reason_code == "aihub538_split_part_offsets_not_contiguous"
    assert not (tmp_path / "merged.zip").exists()


def test_zip_validate_and_extract_checks_crc_and_private_modes(tmp_path: Path) -> None:
    archive = _zip_fixture(
        tmp_path / "safe.zip",
        [("private/a.bin", b"alpha"), ("private/b.bin", b"beta")],
        compression=ZIP_DEFLATED,
    )
    destination = tmp_path / "zip-extracted"
    with ExitStack() as stack:
        descriptor = _readonly(stack, archive)
        validation = validate_zip_descriptor(descriptor)
        extraction = extract_verified_zip_descriptor(
            archive_descriptor=descriptor,
            destination=destination,
        )
    assert validation.archive_sha256 == extraction.archive_sha256
    assert validation.regular_file_count == 2
    assert (destination / "private/b.bin").read_bytes() == b"beta"
    assert stat.S_IMODE((destination / "private/b.bin").stat().st_mode) == 0o600
    assert "private" not in json.dumps(validation.to_json_object(), sort_keys=True)


def test_zip_content_root_binds_hashed_relative_path_size_and_content(
    tmp_path: Path,
) -> None:
    path = "private/a.bin"
    payload = b"alpha"
    archive = _zip_fixture(tmp_path / "safe.zip", [(path, payload)])
    with ExitStack() as stack:
        receipt = validate_zip_descriptor(_readonly(stack, archive))
    expected = sha256_document(
        {
            "schema_version": "voice_coach.aihub538_zip_content_root.v2",
            "entries": [
                {
                    "path_sha256": hashlib.sha256(
                        b"voice_coach.aihub538.archive_relative_path.v1\x00"
                        + path.encode("utf-8")
                    ).hexdigest(),
                    "size_bytes": len(payload),
                    "content_sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
            "regular_file_count": 1,
            "uncompressed_bytes": len(payload),
        }
    )
    assert receipt.content_root_sha256 == expected
    assert path not in json.dumps(receipt.to_json_object(), sort_keys=True)


def test_zip_content_root_changes_when_only_relative_path_changes(
    tmp_path: Path,
) -> None:
    payload = b"same-content"
    roots = []
    for index, member_name in enumerate(("first.bin", "second.bin")):
        archive = _zip_fixture(
            tmp_path / f"safe-{index}.zip",
            [(member_name, payload)],
        )
        with ExitStack() as stack:
            receipt = validate_zip_descriptor(_readonly(stack, archive))
        roots.append(receipt.content_root_sha256)
    assert roots[0] != roots[1]


@pytest.mark.parametrize("archive_kind", ("tar", "zip"))
def test_extraction_write_failure_removes_fresh_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archive_kind: str,
) -> None:
    members = [
        ("private/a.bin", b"alpha"),
        ("private/nested/b.bin", b"beta"),
    ]
    if archive_kind == "tar":
        archive = _tar_fixture(
            tmp_path / "archive.tar",
            [
                (_regular_tar_info(member_name, payload), payload)
                for member_name, payload in members
            ],
        )
        extractor = extract_verified_tar_descriptor
    else:
        archive = _zip_fixture(tmp_path / "archive.zip", members)
        extractor = extract_verified_zip_descriptor

    original_write_all = archive_module._write_all
    call_count = 0

    def fail_second_write(descriptor: int, payload: bytes) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Aihub538ArchiveError("injected_write_failure")
        original_write_all(descriptor, payload)

    monkeypatch.setattr(archive_module, "_write_all", fail_second_write)
    destination = tmp_path / "must-be-removed"
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        extractor(
            archive_descriptor=_readonly(stack, archive),
            destination=destination,
        )
    assert caught.value.reason_code == "injected_write_failure"
    assert call_count == 2
    assert not destination.exists()


@pytest.mark.parametrize(
    "members",
    (
        [("../escape", b"x")],
        [("same", b"x"), ("same", b"y")],
        [("Case", b"x"), ("case", b"y")],
    ),
)
def test_zip_rejects_traversal_duplicate_and_case_collision(
    tmp_path: Path,
    members: list[tuple[str, bytes]],
) -> None:
    archive = _zip_fixture(tmp_path / "unsafe.zip", members)
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError):
        validate_zip_descriptor(_readonly(stack, archive))


@pytest.mark.parametrize("file_type", (stat.S_IFLNK, stat.S_IFIFO))
def test_zip_rejects_symlink_and_special_member(
    tmp_path: Path,
    file_type: int,
) -> None:
    info = ZipInfo("unsafe")
    info.create_system = 3
    info.external_attr = (file_type | 0o777) << 16
    archive = _zip_fixture(tmp_path / "unsafe.zip", [(info, b"target")])
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        validate_zip_descriptor(_readonly(stack, archive))
    assert caught.value.reason_code == "aihub538_zip_link_or_special_member_forbidden"


def test_zip_rejects_encrypted_flag(tmp_path: Path) -> None:
    archive = _zip_fixture(tmp_path / "encrypted.zip", [("item.bin", b"payload")])
    payload = bytearray(archive.read_bytes())
    local = payload.find(b"PK\x03\x04")
    central = payload.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    payload[local + 6 : local + 8] = (1).to_bytes(2, "little")
    payload[central + 8 : central + 10] = (1).to_bytes(2, "little")
    archive.write_bytes(payload)
    archive.chmod(0o600)
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        validate_zip_descriptor(_readonly(stack, archive))
    assert caught.value.reason_code == "aihub538_zip_encrypted_member_forbidden"


def test_zip_rejects_crc_corruption(tmp_path: Path) -> None:
    marker = b"unique-payload-marker"
    archive = _zip_fixture(tmp_path / "corrupt.zip", [("item.bin", marker)])
    payload = bytearray(archive.read_bytes())
    offset = payload.find(marker)
    assert offset >= 0
    payload[offset] ^= 0xFF
    archive.write_bytes(payload)
    archive.chmod(0o600)
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        validate_zip_descriptor(_readonly(stack, archive))
    assert caught.value.reason_code == "aihub538_zip_crc_or_payload_invalid"


def test_zip_rejects_compression_ratio_bomb(tmp_path: Path) -> None:
    archive = _zip_fixture(
        tmp_path / "bomb.zip",
        [("bomb.bin", b"x" * 100_000)],
        compression=ZIP_DEFLATED,
    )
    policy = replace(
        Aihub538ArchiveSecurityPolicy(),
        maximum_zip_compression_ratio=2.0,
    )
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        validate_zip_descriptor(_readonly(stack, archive), policy=policy)
    assert caught.value.reason_code == (
        "aihub538_zip_compression_ratio_outside_policy"
    )


def test_all_archive_entrypoints_require_read_only_descriptors(tmp_path: Path) -> None:
    archive, receipt = _download_fixture(tmp_path)
    writable = os.open(archive, os.O_RDWR | os.O_NOFOLLOW)
    readonly_receipt = os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(Aihub538ArchiveError) as caught:
            verify_aihub538_training_download(
                archive_descriptor=writable,
                receipt_descriptor=readonly_receipt,
            )
    finally:
        os.close(writable)
        os.close(readonly_receipt)
    assert caught.value.reason_code == "aihub538_download_archive_descriptor_invalid"


def test_archive_descriptors_reject_group_or_world_readable_files(
    tmp_path: Path,
) -> None:
    archive, receipt = _download_fixture(tmp_path)
    archive.chmod(0o644)
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        verify_aihub538_training_download(
            archive_descriptor=_readonly(stack, archive),
            receipt_descriptor=_readonly(stack, receipt),
        )
    assert caught.value.reason_code == "aihub538_download_archive_descriptor_invalid"
