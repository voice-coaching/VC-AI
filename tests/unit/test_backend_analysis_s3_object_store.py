from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import replace
import hashlib
import io
import os
from pathlib import Path
from uuid import UUID

import pytest

from voice_coach.backend_analysis.adapters.s3_object_store import (
    S3CompatibleRestrictedObjectStore,
    S3ObjectStoreSettings,
)
from voice_coach.backend_analysis.domain import (
    AnalysisAuthorizationGrant,
    AnalysisRequest,
    AnalysisVisualInput,
)
from voice_coach.backend_analysis.ports import AnalysisAuthorization, BackendAnalysisError


class _Body(io.BytesIO):
    closed_by_adapter = False

    def close(self) -> None:
        self.closed_by_adapter = True
        super().close()


class _Client:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return self.response


def _request(
    *,
    object_key: str = "recordings/users/9/attempt.wav",
    audio_sha256: str | None = None,
) -> AnalysisRequest:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    expected_audio_sha256 = audio_sha256 or hashlib.sha256(b"RIFF").hexdigest()
    grant = AnalysisAuthorizationGrant(
        key_id="test-key",
        request_event_id=UUID("4adfe173-0691-4e89-b94e-a5c5c5085826"),
        analysis_id=35,
        content_id=12,
        prompt_revision="revision-v1",
        script_sha256="a" * 64,
        audio_object_key_sha256=hashlib.sha256(object_key.encode()).hexdigest(),
        audio_sha256=expected_audio_sha256,
        mime_type="audio/wav",
        file_size_bytes=4,
        duration_ms=1200,
        learning_focus="PRONUNCIATION",
        consent_receipt_sha256="b" * 64,
        consent_policy_revision="consent-v1",
        visual_object_key_sha256=None,
        visual_sha256=None,
        visual_mime_type=None,
        visual_file_size_bytes=None,
        visual_consent_receipt_sha256=None,
        visual_consent_policy_revision=None,
        issued_at_utc_text="2026-09-02T00:00:00Z",
        expires_at_utc_text="2026-09-02T00:05:00Z",
        purpose="pronunciation_coaching",
        data_category="learner_voice_recording",
        delete_on_completion=True,
        remote_egress_allowed=False,
        signature="c" * 64,
    )
    return AnalysisRequest(
        event_id=grant.request_event_id,
        analysis_id=35,
        content_id=12,
        prompt_revision="revision-v1",
        script_text="연습 문장입니다.",
        script_sha256="a" * 64,
        audio_object_key=object_key,
        audio_sha256=expected_audio_sha256,
        mime_type="audio/wav",
        file_size_bytes=4,
        duration_ms=1200,
        learning_focus="PRONUNCIATION",
        visual_input=None,
        authorization_grant=grant,
    )


def _authorization() -> AnalysisAuthorization:
    return AnalysisAuthorization(
        receipt_sha256="b" * 64,
        policy_revision="consent-v1",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        delete_on_completion=True,
        remote_egress_allowed=False,
    )


def test_streams_to_read_only_file_and_erases_workspace(tmp_path: Path) -> None:
    body = _Body(b"RIFF")
    client = _Client({"Body": body, "ContentLength": 4, "ContentType": "audio/wav"})
    root = tmp_path / "restricted"
    store = S3CompatibleRestrictedObjectStore(
        client,
        S3ObjectStoreSettings(
            bucket="private-recordings",
            recordings_prefix="recordings/",
            workspace_root=root,
        ),
    )

    with store.materialize(_request(), _authorization()) as audio:
        materialized_path = audio.path
        assert audio.path.read_bytes() == b"RIFF"
        assert audio.sha256 == hashlib.sha256(b"RIFF").hexdigest()
        assert os.stat(audio.path).st_mode & 0o777 == 0o400

    assert client.calls == [
        {"Bucket": "private-recordings", "Key": "recordings/users/9/attempt.wav"}
    ]
    assert body.closed_by_adapter is True
    assert materialized_path.exists() is False
    assert list(root.iterdir()) == []


def test_rejects_key_outside_prefix_before_s3_access(tmp_path: Path) -> None:
    client = _Client({})
    store = S3CompatibleRestrictedObjectStore(
        client,
        S3ObjectStoreSettings(
            bucket="private-recordings",
            recordings_prefix="recordings/",
            workspace_root=tmp_path / "restricted",
        ),
    )

    with pytest.raises(BackendAnalysisError) as error:
        with store.materialize(_request(object_key="other/private.wav"), _authorization()):
            pass

    assert error.value.code == "restricted_audio_unavailable"
    assert client.calls == []


def test_rejects_download_whose_digest_differs_from_backend_claim(tmp_path: Path) -> None:
    body = _Body(b"RIFF")
    client = _Client({"Body": body, "ContentLength": 4, "ContentType": "audio/wav"})
    store = S3CompatibleRestrictedObjectStore(
        client,
        S3ObjectStoreSettings(
            bucket="private-recordings",
            recordings_prefix="recordings/",
            workspace_root=tmp_path / "restricted",
        ),
    )

    with pytest.raises(BackendAnalysisError) as captured:
        with store.materialize(
            _request(audio_sha256="f" * 64), _authorization()
        ):
            raise AssertionError("digest mismatch must fail before yielding media")

    assert captured.value.code == "audio_integrity_mismatch"
    assert body.closed_by_adapter is True


def test_metadata_mismatch_fails_closed_and_cleans_workspace(tmp_path: Path) -> None:
    body = _Body(b"RIFF-extra")
    client = _Client({"Body": body, "ContentLength": 10, "ContentType": "audio/wav"})
    root = tmp_path / "restricted"
    store = S3CompatibleRestrictedObjectStore(
        client,
        S3ObjectStoreSettings(
            bucket="private-recordings",
            recordings_prefix="recordings/",
            workspace_root=root,
        ),
    )

    with pytest.raises(BackendAnalysisError) as error:
        with store.materialize(_request(), _authorization()):
            pass

    assert error.value.code == "audio_metadata_mismatch"
    assert body.closed_by_adapter is True
    assert list(root.iterdir()) == []


def test_materializes_integrity_bound_visual_in_a_separate_workspace(
    tmp_path: Path,
) -> None:
    visual_bytes = b"canonical-mp4"
    visual_key = "recordings/analysis-video/00000000-0000-0000-0000-000000000001.mp4"
    visual = AnalysisVisualInput(
        object_key=visual_key,
        sha256=hashlib.sha256(visual_bytes).hexdigest(),
        mime_type="video/mp4",
        file_size_bytes=len(visual_bytes),
        consent_receipt_sha256="e" * 64,
        consent_policy_revision="voice-video-processing-consent-v1",
    )
    base = _request()
    grant = replace(
        base.authorization_grant,
        visual_object_key_sha256=hashlib.sha256(visual_key.encode()).hexdigest(),
        visual_sha256=visual.sha256,
        visual_mime_type=visual.mime_type,
        visual_file_size_bytes=visual.file_size_bytes,
        visual_consent_receipt_sha256=visual.consent_receipt_sha256,
        visual_consent_policy_revision=visual.consent_policy_revision,
    )
    request = replace(base, visual_input=visual, authorization_grant=grant)
    body = _Body(visual_bytes)
    client = _Client(
        {
            "Body": body,
            "ContentLength": len(visual_bytes),
            "ContentType": "video/mp4",
        }
    )
    root = tmp_path / "restricted"
    store = S3CompatibleRestrictedObjectStore(
        client,
        S3ObjectStoreSettings(
            bucket="private-recordings",
            recordings_prefix="recordings/",
            workspace_root=root,
        ),
    )

    with store.materialize_visual(request, _authorization()) as materialized:
        materialized_path = materialized.path
        assert materialized.path.read_bytes() == visual_bytes
        assert materialized.sha256 == visual.sha256
        assert os.stat(materialized.path).st_mode & 0o777 == 0o400

    assert client.calls == [{"Bucket": "private-recordings", "Key": visual_key}]
    assert body.closed_by_adapter is True
    assert materialized_path.exists() is False
    assert list(root.iterdir()) == []
