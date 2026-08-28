"""Security and lifecycle tests for local restricted video admission."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
import stat

import pytest

from voice_coach.pronunciation_attempts.adapters.restricted_local_media import (
    LocalRestrictedVideoAdmissionAdapter,
)
from voice_coach.pronunciation_attempts.models import (
    MediaErrorCode,
    PronunciationAttemptMediaError,
    VideoAdmissionPolicy,
    VideoConsentAuthorization,
    VideoConsentDecision,
)


NOW = datetime(2026, 8, 28, 3, 0, tzinfo=UTC)
ATTEMPT_SHA256 = hashlib.sha256(b"attempt-1").hexdigest()
CONSENT_SHA256 = hashlib.sha256(b"consent-1").hexdigest()


def _authorization(
    *,
    decision: VideoConsentDecision = VideoConsentDecision.GRANTED,
    attempt_id_sha256: str = ATTEMPT_SHA256,
) -> VideoConsentAuthorization:
    return VideoConsentAuthorization(
        attempt_id_sha256=attempt_id_sha256,
        consent_receipt_sha256=CONSENT_SHA256,
        decision=decision,
        purpose="lip_shape_feedback",
        data_category="facial_video",
        retention_policy_id="delete_after_attempt",
        delete_on_completion=True,
        remote_egress_allowed=False,
        issued_at_utc=NOW - timedelta(minutes=1),
        expires_at_utc=NOW + timedelta(minutes=5),
    )


def _policy(*, maximum_source_bytes: int = 4096) -> VideoAdmissionPolicy:
    return VideoAdmissionPolicy(
        maximum_source_bytes=maximum_source_bytes,
        required_purpose="lip_shape_feedback",
        required_data_category="facial_video",
        allowed_retention_policy_ids=("delete_after_attempt",),
    )


def _adapter(tmp_path: Path) -> LocalRestrictedVideoAdmissionAdapter:
    return LocalRestrictedVideoAdmissionAdapter(
        restricted_root=tmp_path / "restricted",
        clock=lambda: NOW,
    )


def test_consent_is_rejected_before_any_source_open_or_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(tmp_path)
    source = tmp_path / "secret-name.mp4"
    observed_calls: list[str] = []

    monkeypatch.setattr(os, "open", lambda *args, **kwargs: observed_calls.append("open"))
    monkeypatch.setattr(os, "stat", lambda *args, **kwargs: observed_calls.append("stat"))
    monkeypatch.setattr(os, "lstat", lambda *args, **kwargs: observed_calls.append("lstat"))

    with pytest.raises(PronunciationAttemptMediaError) as captured:
        adapter.admit(
            source,
            attempt_id_sha256=ATTEMPT_SHA256,
            authorization=_authorization(decision=VideoConsentDecision.DENIED),
            policy=_policy(),
        )

    assert captured.value.code is MediaErrorCode.CONSENT_NOT_GRANTED
    assert observed_calls == []
    assert str(source) not in str(captured.value)


def test_admission_creates_opaque_0700_workspace_and_read_only_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "participant-name.mp4"
    payload = b"not-yet-decoded-container"
    source.write_bytes(payload)
    adapter = _adapter(tmp_path)

    asset = adapter.admit(
        source,
        attempt_id_sha256=ATTEMPT_SHA256,
        authorization=_authorization(),
        policy=_policy(),
    )

    restricted_root = tmp_path / "restricted"
    workspace = restricted_root / asset.handle_id
    restricted_copy = workspace / "container"
    assert source.read_bytes() == payload
    assert stat.S_IMODE(restricted_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
    assert stat.S_IMODE(restricted_copy.stat().st_mode) == 0o400
    assert restricted_copy.read_bytes() == payload
    assert asset.container_sha256 == hashlib.sha256(payload).hexdigest()
    assert str(source) not in repr(asset)

    with adapter.open_container(asset) as opened:
        assert opened.read() == payload

    first = adapter.delete(asset)
    second = adapter.delete(asset)
    assert first.status == "deleted"
    assert second.status == "already_deleted"
    assert source.read_bytes() == payload
    assert list(restricted_root.iterdir()) == []


def test_preopened_read_only_fd_is_admitted_from_start_without_locator_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "participant-sensitive-name.mp4"
    payload = b"preopened-container"
    source.write_bytes(payload)
    source_fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    adapter = _adapter(tmp_path)
    try:
        os.lseek(source_fd, len(payload), os.SEEK_SET)
        asset = adapter.admit(
            f"fd:{source_fd}",
            attempt_id_sha256=ATTEMPT_SHA256,
            authorization=_authorization(),
            policy=_policy(),
        )
        with adapter.open_container(asset) as opened:
            assert opened.read() == payload
        assert os.fstat(source_fd).st_ino == source.stat().st_ino
        assert str(source) not in repr(asset)
    finally:
        if "asset" in locals():
            adapter.delete(asset)
        os.close(source_fd)


def test_preopened_fd_requires_consent_and_read_only_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    adapter = _adapter(tmp_path)
    observed_duplications: list[int] = []
    monkeypatch.setattr(
        os,
        "dup",
        lambda descriptor: observed_duplications.append(descriptor) or descriptor,
    )
    with pytest.raises(PronunciationAttemptMediaError) as denied:
        adapter.admit(
            "fd:77",
            attempt_id_sha256=ATTEMPT_SHA256,
            authorization=_authorization(decision=VideoConsentDecision.DENIED),
            policy=_policy(),
        )
    assert denied.value.code is MediaErrorCode.CONSENT_NOT_GRANTED
    assert observed_duplications == []

    monkeypatch.undo()
    writable_fd = os.open(source, os.O_RDWR | os.O_CLOEXEC)
    try:
        with pytest.raises(PronunciationAttemptMediaError) as writable:
            adapter.admit(
                f"fd:{writable_fd}",
                attempt_id_sha256=ATTEMPT_SHA256,
                authorization=_authorization(),
                policy=_policy(),
            )
        assert writable.value.code is MediaErrorCode.SOURCE_OPEN_FAILED
        assert list((tmp_path / "restricted").iterdir()) == []
    finally:
        os.close(writable_fd)


def test_symlink_and_non_regular_source_fail_closed_without_temp_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    symlink = tmp_path / "link.mp4"
    symlink.symlink_to(source)
    adapter = _adapter(tmp_path)

    with pytest.raises(PronunciationAttemptMediaError) as symlink_error:
        adapter.admit(
            symlink,
            attempt_id_sha256=ATTEMPT_SHA256,
            authorization=_authorization(),
            policy=_policy(),
        )
    assert symlink_error.value.code is MediaErrorCode.SOURCE_OPEN_FAILED
    assert str(symlink) not in str(symlink_error.value)
    assert list((tmp_path / "restricted").iterdir()) == []

    fifo = tmp_path / "source.fifo"
    os.mkfifo(fifo)
    with pytest.raises(PronunciationAttemptMediaError) as fifo_error:
        adapter.admit(
            fifo,
            attempt_id_sha256=ATTEMPT_SHA256,
            authorization=_authorization(),
            policy=_policy(),
        )
    assert fifo_error.value.code is MediaErrorCode.SOURCE_NOT_REGULAR
    assert list((tmp_path / "restricted").iterdir()) == []


def test_source_size_limit_and_uncommitted_derivation_leave_no_partial_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"12345")
    adapter = _adapter(tmp_path)

    with pytest.raises(PronunciationAttemptMediaError) as too_large:
        adapter.admit(
            source,
            attempt_id_sha256=ATTEMPT_SHA256,
            authorization=_authorization(),
            policy=_policy(maximum_source_bytes=4),
        )
    assert too_large.value.code is MediaErrorCode.SOURCE_TOO_LARGE
    assert list((tmp_path / "restricted").iterdir()) == []

    asset = adapter.admit(
        source,
        attempt_id_sha256=ATTEMPT_SHA256,
        authorization=_authorization(),
        policy=_policy(maximum_source_bytes=5),
    )
    with pytest.raises(PronunciationAttemptMediaError) as uncommitted:
        with adapter.create_derived_audio(asset) as writer:
            writer.write(b"\x00\x00")
    assert uncommitted.value.code is MediaErrorCode.DERIVED_AUDIO_NOT_COMMITTED
    assert sorted(path.name for path in (tmp_path / "restricted" / asset.handle_id).iterdir()) == [
        "container"
    ]

    adapter.delete(asset)
    assert source.exists()
