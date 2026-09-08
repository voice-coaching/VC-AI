"""Security and contract tests for the optional real landmark runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from voice_coach.pronunciation_attempts.adapters.mediapipe_landmark_worker import (
    BACKEND_REVISION,
    PROTOCOL_VERSION,
)
from voice_coach.pronunciation_attempts.adapters.mediapipe_lip_landmarks import (
    LipLandmarkRuntimeError,
    MediaPipeLandmarkPolicy,
    MediaPipeLipLandmarkAdapter,
)
from voice_coach.pronunciation_attempts.models import DecodedRgbFrame, Rgb24Sample
import voice_coach.pronunciation_attempts.adapters.mediapipe_landmark_worker as worker_module

ROOT = Path(__file__).resolve().parents[2]


def _policy() -> MediaPipeLandmarkPolicy:
    return MediaPipeLandmarkPolicy(
        minimum_face_detection_confidence=0.5,
        minimum_face_presence_confidence=0.5,
        minimum_tracking_confidence=0.5,
        maximum_frame_count=4,
        maximum_total_rgb_bytes=100_000,
        subprocess_timeout_seconds=10.0,
        maximum_subprocess_output_bytes=100_000,
    )


def _sample() -> Rgb24Sample:
    return Rgb24Sample(
        asset_handle_id="a" * 64,
        container_sha256="b" * 64,
        video_stream_index=0,
        frames=(
            DecodedRgbFrame(
                timestamp_us=0,
                width=16,
                height=16,
                rgb24=b"\x00" * (16 * 16 * 3),
            ),
        ),
        ffmpeg_revision="ffmpeg_test_v1",
    )


def test_worker_seccomp_denies_network_socket_creation() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            (
                "voice_coach.pronunciation_attempts.adapters."
                "mediapipe_landmark_worker"
            ),
            "--network-isolation-self-test",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    response = json.loads(completed.stdout)
    assert response == {
        "network_isolation": "seccomp_socket_denied",
        "protocol_version": PROTOCOL_VERSION,
        "status": "passed",
    }


def test_model_digest_mismatch_fails_before_landmark_inference(tmp_path: Path) -> None:
    model = tmp_path / "face_landmarker.task"
    model.write_bytes(b"not-a-model")
    adapter = MediaPipeLipLandmarkAdapter(
        model_path=model,
        expected_model_sha256="c" * 64,
        policy=_policy(),
        repository_root=ROOT,
    )

    with pytest.raises(LipLandmarkRuntimeError) as caught:
        adapter.detect(_sample())

    assert caught.value.reason_code == "landmark_model_integrity_failed"
    assert str(model) not in str(caught.value)


def test_symlink_model_is_rejected_without_path_leak(tmp_path: Path) -> None:
    target = tmp_path / "target.task"
    target.write_bytes(b"model")
    model = tmp_path / "linked.task"
    model.symlink_to(target)
    digest = hashlib.sha256(b"model").hexdigest()
    adapter = MediaPipeLipLandmarkAdapter(
        model_path=model,
        expected_model_sha256=digest,
        policy=_policy(),
        repository_root=ROOT,
    )

    with pytest.raises(LipLandmarkRuntimeError) as caught:
        adapter.detect(_sample())

    assert caught.value.reason_code == "landmark_model_unavailable"
    assert str(model) not in str(caught.value)


def test_model_descriptor_is_passed_explicitly_without_raw_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "face_landmarker.task"
    payload = b"descriptor-model"
    model.write_bytes(payload)
    descriptor = os.open(model, os.O_RDONLY | os.O_NOFOLLOW)
    digest = hashlib.sha256(payload).hexdigest()
    adapter = MediaPipeLipLandmarkAdapter(
        model_path="must_not_be_used",
        model_descriptor=descriptor,
        expected_model_sha256=digest,
        policy=_policy(),
        repository_root=ROOT,
    )
    observed: dict[str, object] = {}

    def run_fixture(
        *,
        request_bytes: bytes,
        environment: dict[str, str],
        pass_fds: tuple[int, ...],
    ) -> tuple[int, bytes, bytes]:
        del environment
        request = json.loads(request_bytes)
        observed["model_path"] = request["model_path"]
        observed["pass_fds"] = pass_fds
        assert len(pass_fds) == 1
        assert os.get_inheritable(pass_fds[0]) is False
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "status": "completed",
            "backend_revision": BACKEND_REVISION,
            "model_sha256": digest,
            "network_isolation": "seccomp_socket_denied",
            "frames": [
                {
                    "timestamp_ms": 0,
                    "face_count": 0,
                    "lip_geometry": None,
                }
            ],
        }
        return 0, json.dumps(response).encode("utf-8"), b""

    monkeypatch.setattr(adapter, "_run_bounded_worker", run_fixture)
    try:
        result = adapter.detect(_sample())
    finally:
        os.close(descriptor)

    passed_descriptor = observed["pass_fds"][0]
    assert observed["model_path"] == f"fd:{passed_descriptor}"
    assert str(model) not in str(observed)
    assert result.model_sha256 == digest


def test_worker_reads_exact_read_only_descriptor_bytes(tmp_path: Path) -> None:
    model = tmp_path / "face_landmarker.task"
    payload = b"exact-descriptor-model"
    model.write_bytes(payload)
    descriptor = os.open(model, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert worker_module._read_model_bytes(
            f"fd:{descriptor}", hashlib.sha256(payload).hexdigest()
        ) == payload
    finally:
        os.close(descriptor)


def test_writeable_model_descriptor_is_rejected_before_worker_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "face_landmarker.task"
    payload = b"writeable-model"
    model.write_bytes(payload)
    descriptor = os.open(model, os.O_RDWR | os.O_NOFOLLOW)
    adapter = MediaPipeLipLandmarkAdapter(
        model_path="must_not_be_used",
        model_descriptor=descriptor,
        expected_model_sha256=hashlib.sha256(payload).hexdigest(),
        policy=_policy(),
        repository_root=ROOT,
    )
    monkeypatch.setattr(
        adapter,
        "_run_bounded_worker",
        lambda **_values: (_ for _ in ()).throw(AssertionError()),
    )
    try:
        with pytest.raises(LipLandmarkRuntimeError) as caught:
            adapter.detect(_sample())
    finally:
        os.close(descriptor)

    assert caught.value.reason_code == "landmark_model_unavailable"
    assert str(model) not in str(caught.value)


def test_proc_fd_path_cannot_bypass_nofollow_path_mode(tmp_path: Path) -> None:
    model = tmp_path / "face_landmarker.task"
    payload = b"proc-path-model"
    model.write_bytes(payload)
    descriptor = os.open(model, os.O_RDONLY | os.O_NOFOLLOW)
    adapter = MediaPipeLipLandmarkAdapter(
        model_path=f"/proc/self/fd/{descriptor}",
        expected_model_sha256=hashlib.sha256(payload).hexdigest(),
        policy=_policy(),
        repository_root=ROOT,
    )
    try:
        with pytest.raises(LipLandmarkRuntimeError) as caught:
            adapter.detect(_sample())
    finally:
        os.close(descriptor)

    assert caught.value.reason_code == "landmark_model_unavailable"
    assert "/proc/" not in str(caught.value)
