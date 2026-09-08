"""Contract tests for AI Hub 539 non-locked actual-media acquisition."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from voice_coach.visual_feedback.adapters.aihub539_actual_media_download import (
    Aihub539ActualMediaDownloadAdapterError,
    Aihub539ActualMediaDownloadRequest,
    bind_aihub539_actual_media_download,
    capture_aihub539_actual_media_authorization_descriptor_identity,
    load_aihub539_actual_media_authorization_descriptor,
)
from voice_coach.visual_feedback.models.aihub539_actual_media_authorization import (
    AIHUB539_ACTUAL_MEDIA_DATASET_ID,
    AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS,
    AIHUB539_ACTUAL_MEDIA_PARTITION,
    Aihub539ActualMediaAuthorizationReceipt,
)
from voice_coach.visual_feedback.services.aihub539_actual_media_authorization import (
    Aihub539ActualMediaAuthorizationRequest,
    Aihub539ActualMediaAuthorizationService,
    Aihub539ActualMediaAuthorizationServiceError,
)


ROOT = Path(__file__).resolve().parents[2]
SECURE_DOWNLOAD_SCRIPT = ROOT / "scripts" / "download_aihub_secure.py"
AUTHORIZATION_SCRIPT = (
    ROOT / "scripts" / "authorize_aihub539_actual_media_download.py"
)
SPEC = importlib.util.spec_from_file_location(
    "download_aihub_secure_aihub539_test",
    SECURE_DOWNLOAD_SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
SECURE_DOWNLOAD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SECURE_DOWNLOAD
SPEC.loader.exec_module(SECURE_DOWNLOAD)


def _private_directory(path: Path) -> Path:
    path.mkdir()
    path.chmod(0o700)
    return path.absolute()


def _request(
    *,
    destination: Path,
    authorization_output: Path,
    dataset_id: int = AIHUB539_ACTUAL_MEDIA_DATASET_ID,
    partition: str = AIHUB539_ACTUAL_MEDIA_PARTITION,
    ordered_file_keys: tuple[str, ...] = (
        AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS
    ),
    permission_confirmed: bool = True,
    locked_partition_requested: bool = False,
    validation_requested: bool = False,
) -> Aihub539ActualMediaAuthorizationRequest:
    return Aihub539ActualMediaAuthorizationRequest(
        dataset_id=dataset_id,
        partition=partition,
        ordered_file_keys=ordered_file_keys,
        download_destination_directory=destination,
        authorization_output_directory=authorization_output,
        dataset_access_permission_confirmed=permission_confirmed,
        locked_partition_requested=locked_partition_requested,
        validation_requested=validation_requested,
    )


def _issue(
    tmp_path: Path,
) -> tuple[Path, Path, Aihub539ActualMediaAuthorizationReceipt]:
    destination = _private_directory(tmp_path / "download")
    authorization_output = (tmp_path / "authorization").absolute()
    receipt, receipt_path = Aihub539ActualMediaAuthorizationService().issue(
        _request(
            destination=destination,
            authorization_output=authorization_output,
        )
    )
    return destination, receipt_path, receipt


def test_exact_training_tl8_ts8_authorization_is_non_promotional(
    tmp_path: Path,
) -> None:
    destination, receipt_path, receipt = _issue(tmp_path)

    document = receipt.to_json_object()
    assert document["dataset_id"] == 539
    assert document["partition"] == "Training"
    assert document["ordered_archives"] == [
        {
            "archive_name": "TL8.zip",
            "file_key": "54575",
            "role": "training_label_metadata",
        },
        {
            "archive_name": "TS8.zip",
            "file_key": "54620",
            "role": "training_actual_media",
        },
    ]
    assert document["ordered_file_keys"] == ["54575", "54620"]
    assert document["archive_download_authorized"] is True
    assert document["archive_admission_status"] == "not_started"
    assert document["archive_admission_authorized"] is False
    assert document["archive_open_authorized"] is False
    assert document["actual_media_decode_authorized"] is False
    assert document["actual_media_sync_validation_authorized"] is False
    assert document["actual_media_sync_validated"] is False
    assert document["locked_partition_authorized"] is False
    assert document["validation_partition_authorized"] is False
    assert document["p8_authorized"] is False
    assert document["promotion_authorized"] is False
    assert not list(destination.iterdir())
    assert receipt_path.exists()


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    (
        (
            {"dataset_id": 538},
            "aihub539_actual_media_dataset_mismatch",
        ),
        (
            {"ordered_file_keys": ("54575", "99999")},
            "aihub539_actual_media_file_keys_invalid",
        ),
        (
            {"ordered_file_keys": ("54620", "54575")},
            "aihub539_actual_media_file_key_order_invalid",
        ),
        (
            {"permission_confirmed": False},
            "aihub539_dataset_access_permission_not_confirmed",
        ),
    ),
)
def test_wrong_dataset_keys_order_or_permission_fail_closed(
    tmp_path: Path,
    overrides: dict[str, object],
    reason_code: str,
) -> None:
    destination = _private_directory(tmp_path / "download")
    request = _request(
        destination=destination,
        authorization_output=(tmp_path / "authorization").absolute(),
        **overrides,
    )

    with pytest.raises(
        Aihub539ActualMediaAuthorizationServiceError
    ) as caught:
        Aihub539ActualMediaAuthorizationService().issue(request)

    assert caught.value.reason_code == reason_code
    assert not (tmp_path / "authorization").exists()
    assert not list(destination.iterdir())


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    (
        (
            {"partition": "Validation"},
            "aihub539_actual_media_partition_not_authorized",
        ),
        (
            {"locked_partition_requested": True},
            "aihub539_locked_partition_request_forbidden",
        ),
        (
            {"validation_requested": True},
            "aihub539_validation_request_forbidden",
        ),
    ),
)
def test_locked_or_validation_authorization_is_rejected(
    tmp_path: Path,
    overrides: dict[str, object],
    reason_code: str,
) -> None:
    destination = _private_directory(tmp_path / "download")
    request = _request(
        destination=destination,
        authorization_output=(tmp_path / "authorization").absolute(),
        **overrides,
    )

    with pytest.raises(
        Aihub539ActualMediaAuthorizationServiceError
    ) as caught:
        Aihub539ActualMediaAuthorizationService().issue(request)

    assert caught.value.reason_code == reason_code
    assert not (tmp_path / "authorization").exists()


def test_receipt_is_owner_only_immutable_and_strictly_reloadable(
    tmp_path: Path,
) -> None:
    _destination, receipt_path, expected = _issue(tmp_path)
    receipt_metadata = receipt_path.stat()
    output_metadata = receipt_path.parent.stat()

    assert stat.S_IMODE(output_metadata.st_mode) == 0o700
    assert output_metadata.st_uid == os.geteuid()
    assert stat.S_IMODE(receipt_metadata.st_mode) == 0o400
    assert receipt_metadata.st_uid == os.geteuid()
    assert receipt_metadata.st_nlink == 1
    descriptor = os.open(receipt_path, os.O_RDONLY)
    try:
        identity = (
            capture_aihub539_actual_media_authorization_descriptor_identity(
                descriptor
            )
        )
        assert identity.mode == receipt_metadata.st_mode
        assert (
            load_aihub539_actual_media_authorization_descriptor(descriptor)
            == expected
        )
    finally:
        os.close(descriptor)


def test_read_write_or_hardlinked_descriptor_is_rejected(
    tmp_path: Path,
) -> None:
    _destination, receipt_path, _receipt = _issue(tmp_path)
    receipt_path.chmod(0o600)
    descriptor = os.open(receipt_path, os.O_RDWR)
    try:
        with pytest.raises(
            Aihub539ActualMediaDownloadAdapterError,
            match="descriptor_invalid",
        ):
            capture_aihub539_actual_media_authorization_descriptor_identity(
                descriptor
            )
    finally:
        os.close(descriptor)

    receipt_path.chmod(0o400)
    os.link(receipt_path, tmp_path / "receipt-hardlink.json")
    descriptor = os.open(receipt_path, os.O_RDONLY)
    try:
        with pytest.raises(
            Aihub539ActualMediaDownloadAdapterError,
            match="descriptor_invalid",
        ):
            capture_aihub539_actual_media_authorization_descriptor_identity(
                descriptor
            )
    finally:
        os.close(descriptor)


def test_descriptor_mutation_after_binding_is_detected(
    tmp_path: Path,
) -> None:
    destination, receipt_path, _receipt = _issue(tmp_path)
    descriptor = os.open(receipt_path, os.O_RDONLY)
    try:
        binding = bind_aihub539_actual_media_download(
            Aihub539ActualMediaDownloadRequest(
                dataset_key="539",
                ordered_file_keys=("54575", "54620"),
                destination=destination,
                authorization_descriptor=descriptor,
            )
        )
        receipt_path.chmod(0o600)
        with pytest.raises(
            Aihub539ActualMediaDownloadAdapterError,
            match="authorization_changed",
        ):
            binding.assert_stable()
    finally:
        os.close(descriptor)


def test_receipt_serializes_no_credentials_raw_members_or_local_paths(
    tmp_path: Path,
) -> None:
    _destination, receipt_path, receipt = _issue(tmp_path)
    serialized = json.dumps(
        receipt.to_json_object(),
        ensure_ascii=False,
        sort_keys=True,
    )
    lowered = serialized.lower()

    for forbidden in (
        "apikey",
        "api_key",
        "bearer",
        "cookie",
        "password",
        "private-key",
        "secret-marker",
    ):
        assert forbidden not in lowered
    assert str(tmp_path) not in serialized
    assert "/home/" not in serialized
    assert "raw_identifier" not in lowered
    assert "source_path" not in lowered
    assert "member_name" not in lowered
    assert "member_names" not in lowered
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == (
        receipt.to_json_object()
    )


@pytest.mark.parametrize(
    ("dataset_key", "file_keys", "reason_code"),
    (
        (
            "538",
            "54575,54620",
            "aihub539_actual_media_dataset_mismatch",
        ),
        (
            "539",
            "54575,99999",
            "aihub539_actual_media_file_keys_invalid",
        ),
        (
            "539",
            "54620,54575",
            "aihub539_actual_media_file_key_order_invalid",
        ),
    ),
)
def test_secure_downloader_reserves_wrong_dataset_key_or_order(
    tmp_path: Path,
    dataset_key: str,
    file_keys: str,
    reason_code: str,
) -> None:
    arguments = argparse.Namespace(
        dataset_key=dataset_key,
        file_keys=file_keys,
        destination=(tmp_path / "must-not-exist").absolute(),
        timeout_seconds=1.0,
    )

    with pytest.raises(SECURE_DOWNLOAD.DownloadError, match=reason_code):
        SECURE_DOWNLOAD._aihub539_actual_media_authorization_guard(
            arguments
        )
    assert not arguments.destination.exists()


def test_secure_downloader_reserves_every_other_539_key(tmp_path: Path) -> None:
    arguments = argparse.Namespace(
        dataset_key="539",
        file_keys="99999",
        destination=(tmp_path / "must-not-exist").absolute(),
        timeout_seconds=1.0,
    )
    with pytest.raises(SECURE_DOWNLOAD.DownloadError, match="not_authorized"):
        SECURE_DOWNLOAD._aihub539_actual_media_authorization_guard(arguments)


def test_partial_symlink_cannot_receive_download_payload(tmp_path: Path) -> None:
    destination = _private_directory(tmp_path / "download")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"sentinel")
    (destination / "download.tar.partial").symlink_to(outside)
    with pytest.raises(SECURE_DOWNLOAD.DownloadError):
        SECURE_DOWNLOAD._stream_response(
            io.BytesIO(b"payload"),
            partial_path=destination / "download.tar.partial",
            append=True,
            expected_total=16,
        )
    assert outside.read_bytes() == b"sentinel"


def test_secure_downloader_requires_and_rechecks_bound_descriptor_before_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination, receipt_path, _receipt = _issue(tmp_path)
    arguments = argparse.Namespace(
        dataset_key="539",
        file_keys="54575,54620",
        destination=destination,
        timeout_seconds=1.0,
    )
    monkeypatch.delenv(
        "VOICE_COACH_AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FD",
        raising=False,
    )
    monkeypatch.setenv("AIHUB_API_KEY", "secret-marker")

    with pytest.raises(
        SECURE_DOWNLOAD.DownloadError,
        match="aihub539_actual_media_authorization_required",
    ):
        SECURE_DOWNLOAD.download(arguments)
    assert not list(destination.iterdir())

    descriptor = os.open(receipt_path, os.O_RDONLY)
    try:
        monkeypatch.setenv(
            "VOICE_COACH_AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FD",
            str(descriptor),
        )
        assert_stable = (
            SECURE_DOWNLOAD._aihub539_actual_media_authorization_guard(
                arguments
            )
        )
        assert_stable()
        receipt_path.chmod(0o600)
        with pytest.raises(
            SECURE_DOWNLOAD.DownloadError,
            match="aihub539_actual_media_authorization_changed",
        ):
            assert_stable()
    finally:
        os.close(descriptor)


def test_nonempty_destination_is_not_authorized(tmp_path: Path) -> None:
    destination = _private_directory(tmp_path / "download")
    (destination / "existing.partial").write_bytes(b"partial")
    request = _request(
        destination=destination,
        authorization_output=(tmp_path / "authorization").absolute(),
    )

    with pytest.raises(
        Aihub539ActualMediaAuthorizationServiceError,
        match="destination_not_empty",
    ):
        Aihub539ActualMediaAuthorizationService().issue(request)

    assert not (tmp_path / "authorization").exists()


def test_cli_issues_receipt_offline_without_claiming_admission(
    tmp_path: Path,
) -> None:
    destination = _private_directory(tmp_path / "download")
    authorization_output = (tmp_path / "authorization").absolute()

    completed = subprocess.run(
        [
            sys.executable,
            str(AUTHORIZATION_SCRIPT),
            "--download-destination-directory",
            str(destination),
            "--authorization-output-directory",
            str(authorization_output),
            "--dataset-access-permission-confirmed",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result["status"] == (
        "authorized_nonlocked_training_acquisition"
    )
    assert result["ordered_file_keys"] == ["54575", "54620"]
    assert result["network_download_performed"] is False
    assert result["archive_admission_completed"] is False
    assert result["actual_media_sync_validated"] is False
    assert result["locked_partition_accessed"] is False
    assert result["p8_authorized"] is False
    assert result["promotion_authorized"] is False
    assert Path(result["authorization_path"]).is_file()
    assert not list(destination.iterdir())
