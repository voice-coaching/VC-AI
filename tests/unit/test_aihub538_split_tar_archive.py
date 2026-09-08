"""Security fixtures for byte-offset inner-TAR merge and ZIP repack."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import stat
import tarfile
from zipfile import ZipFile

import pytest

from voice_coach.visual_feedback.adapters import aihub538_split_tar_archive as module
from voice_coach.visual_feedback.adapters.aihub538_split_tar_archive import (
    NamedSplitTarPart,
    merge_split_tar_parts,
    repack_verified_inner_tar_to_zip,
)
from voice_coach.visual_feedback.adapters.aihub538_training_archive import (
    Aihub538ArchiveError,
    Aihub538ArchiveSecurityPolicy,
)


_PRIVATE_PATH = "restricted/Label/TL174/group/private-C032.json"
_PRIVATE_TRANSCRIPT = "비공개 전사"


def _private(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _readonly(stack: ExitStack, path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    stack.callback(os.close, descriptor)
    return descriptor


def _regular_info(name: str, payload: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o777
    return info


def _tar_payload(members: list[tuple[tarfile.TarInfo, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for info, payload in members:
            archive.addfile(info, io.BytesIO(payload) if info.isfile() else None)
    return buffer.getvalue()


def _split_parts(root: Path, payload: bytes) -> tuple[Path, Path]:
    first_size = len(payload) // 2
    first = _private(root / "opaque.tar.part0", payload[:first_size])
    second = _private(
        root / f"opaque.tar.part{first_size}",
        payload[first_size:],
    )
    return first, second


def test_offset_merge_and_repack_are_deterministic_private_and_parity_bound(
    tmp_path: Path,
) -> None:
    payloads = {
        _PRIVATE_PATH: json.dumps(
            {"speaker": "C032", "transcript": _PRIVATE_TRANSCRIPT},
            ensure_ascii=False,
        ).encode("utf-8"),
        "restricted/Label/TL174/group/second.json": b"{}",
    }
    tar_payload = _tar_payload(
        [
            (_regular_info(name, payload), payload)
            for name, payload in payloads.items()
        ]
    )
    first, second = _split_parts(tmp_path, tar_payload)
    merged = tmp_path / "merged.tar"
    with ExitStack() as stack:
        merge_receipt = merge_split_tar_parts(
            # Reverse caller order to prove byte-offset sorting.
            parts=(
                NamedSplitTarPart(second.name, _readonly(stack, second)),
                NamedSplitTarPart(first.name, _readonly(stack, first)),
            ),
            destination=merged,
        )
    assert merged.read_bytes() == tar_payload
    assert stat.S_IMODE(merged.stat().st_mode) == 0o600
    assert merge_receipt.first_part_offset_bytes == 0
    assert merge_receipt.last_part_offset_bytes == first.stat().st_size

    receipts = []
    validations = []
    for index in (0, 1):
        with ExitStack() as stack:
            receipt, validation = repack_verified_inner_tar_to_zip(
                archive_descriptor=_readonly(stack, merged),
                destination=tmp_path / f"canonical-{index}.zip",
            )
        receipts.append(receipt)
        validations.append(validation)
    assert receipts[0] == receipts[1]
    assert validations[0] == validations[1]
    assert receipts[0].repacked_zip_content_root_sha256 == (
        validations[0].content_root_sha256
    )
    assert (tmp_path / "canonical-0.zip").read_bytes() == (
        tmp_path / "canonical-1.zip"
    ).read_bytes()
    assert stat.S_IMODE((tmp_path / "canonical-0.zip").stat().st_mode) == 0o600
    with ZipFile(tmp_path / "canonical-0.zip", "r") as archive:
        assert archive.namelist() == sorted(payloads)
        assert {name: archive.read(name) for name in archive.namelist()} == payloads

    serialized = json.dumps(
        {
            "merge": merge_receipt.to_json_object(),
            "repack": receipts[0].to_json_object(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    for forbidden in (
        _PRIVATE_PATH,
        _PRIVATE_TRANSCRIPT,
        "C032",
        str(tmp_path),
        "opaque.tar",
    ):
        assert forbidden not in serialized


def test_split_tar_offsets_must_equal_prior_cumulative_bytes(
    tmp_path: Path,
) -> None:
    first = _private(tmp_path / "opaque.tar.part0", b"abc")
    wrong = _private(tmp_path / "opaque.tar.part4", b"def")
    destination = tmp_path / "must-not-exist.tar"
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        merge_split_tar_parts(
            parts=(
                NamedSplitTarPart(first.name, _readonly(stack, first)),
                NamedSplitTarPart(wrong.name, _readonly(stack, wrong)),
            ),
            destination=destination,
        )
    assert caught.value.reason_code == (
        "aihub538_split_tar_part_offsets_not_contiguous"
    )
    assert not destination.exists()


@pytest.mark.parametrize("unsafe_kind", ("traversal", "symlink", "fifo", "case"))
def test_inner_tar_repack_rejects_unsafe_members_without_output(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    safe_payload = b"private"
    if unsafe_kind == "traversal":
        members = [(_regular_info("../escape", safe_payload), safe_payload)]
    elif unsafe_kind == "case":
        members = [
            (_regular_info("Private/File.bin", safe_payload), safe_payload),
            (_regular_info("private/file.bin", safe_payload), safe_payload),
        ]
    else:
        info = tarfile.TarInfo("restricted/special")
        info.type = tarfile.SYMTYPE if unsafe_kind == "symlink" else tarfile.FIFOTYPE
        if unsafe_kind == "symlink":
            info.linkname = "target"
        members = [(info, b"")]
    archive = _private(tmp_path / "unsafe.tar", _tar_payload(members))
    destination = tmp_path / "must-not-exist.zip"

    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError):
        repack_verified_inner_tar_to_zip(
            archive_descriptor=_readonly(stack, archive),
            destination=destination,
        )
    assert not destination.exists()


def test_inner_tar_member_bound_is_checked_before_output_creation(
    tmp_path: Path,
) -> None:
    archive = _private(
        tmp_path / "large.tar",
        _tar_payload([(_regular_info("private.bin", b"1234"), b"1234")]),
    )
    destination = tmp_path / "must-not-exist.zip"
    policy = replace(
        Aihub538ArchiveSecurityPolicy(),
        maximum_tar_member_bytes=3,
    )
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        repack_verified_inner_tar_to_zip(
            archive_descriptor=_readonly(stack, archive),
            destination=destination,
            policy=policy,
        )
    assert caught.value.reason_code == (
        "aihub538_inner_tar_member_size_outside_policy"
    )
    assert not destination.exists()


def test_post_write_validation_failure_removes_only_fresh_repack_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _private(
        tmp_path / "safe.tar",
        _tar_payload([(_regular_info("private.bin", b"payload"), b"payload")]),
    )
    destination = tmp_path / "must-be-cleaned.zip"

    def fail_validation(*args: object, **kwargs: object) -> object:
        raise Aihub538ArchiveError("fixture_post_write_validation_failure")

    monkeypatch.setattr(module, "validate_zip_descriptor", fail_validation)
    with ExitStack() as stack, pytest.raises(Aihub538ArchiveError) as caught:
        repack_verified_inner_tar_to_zip(
            archive_descriptor=_readonly(stack, archive),
            destination=destination,
        )
    assert caught.value.reason_code == "fixture_post_write_validation_failure"
    assert not destination.exists()
