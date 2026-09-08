from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_coach.visual_feedback.adapters.aihub538_photometric_v4_sampling import (
    Aihub538PhotometricV4EightFrameSampler,
    Aihub538PhotometricV4SamplingError,
    aihub538_v4_interior_timestamps_us,
    require_aihub538_photometric_v4_eight_frame_sample,
)
from voice_coach.visual_feedback.adapters.aihub538_photometric_v4_production_factory import (
    Aihub538PhotometricV4ProductionFactory,
)
from voice_coach.visual_feedback.adapters.aihub538_ffmpeg_colorimetry import FfmpegRgbColorimetryAdapter
from voice_coach.visual_feedback.models.aihub538_photometric_v4_production_policy import (
    AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY,
    AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256,
    Aihub538PhotometricV4ProductionExecutionPolicy,
    load_aihub538_photometric_v4_production_policy_descriptor,
)


def test_bundle_sha_is_independently_canonical_and_factories_are_exact() -> None:
    raw = json.dumps(
        AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(raw).hexdigest() == (
        "e8da8270b167610e3ba70b04e02fedba59b03f5800179dd9b77582b7a6a83fd4"
    )
    bundle = Aihub538PhotometricV4ProductionExecutionPolicy.frozen()
    assert bundle.policy_sha256 == AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
    assert bundle.video_qc_policy().to_json_object() == bundle.document["video_qc"]
    assert bundle.landmark_policy().maximum_frame_count == 8
    assert bundle.container_policy().maximum_rgb_sample_frames == 8
    assert bundle.integration_binding()["promotion_authorized"] is False
    factory = Aihub538PhotometricV4ProductionFactory()
    assert factory.video_qc_policy() == bundle.video_qc_policy()
    assert factory.landmark_policy() == bundle.landmark_policy()
    assert factory.container_policy() == bundle.container_policy()
    assert set(factory.worker_environment().values()) == {"1"}


def test_strict_reload_and_policy_mutation(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    path.chmod(0o400)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert load_aihub538_photometric_v4_production_policy_descriptor(fd).policy_sha256 == (
            AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
        )
    finally:
        os.close(fd)


def test_reload_rejects_noncanonical_bytes_and_writable_fd(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    canonical = json.dumps(AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY, sort_keys=True, separators=(",", ":"))
    path.write_text(canonical + "\n", encoding="utf-8")
    path.chmod(0o400)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(ValueError, match="reload_failed"):
            load_aihub538_photometric_v4_production_policy_descriptor(fd)
    finally:
        os.close(fd)
    path.chmod(0o600)
    path.write_text(canonical, encoding="utf-8")
    path.chmod(0o600)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        with pytest.raises(ValueError, match="reload_failed"):
            load_aihub538_photometric_v4_production_policy_descriptor(fd)
    finally:
        os.close(fd)
    mutated = json.loads(json.dumps(AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY))
    mutated["video_qc"]["minimum_single_face_frames"] = 5
    path.chmod(0o600)
    path.write_text(json.dumps(mutated, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    path.chmod(0o400)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(ValueError):
            load_aihub538_photometric_v4_production_policy_descriptor(fd)
    finally:
        os.close(fd)


def test_eight_interior_timestamps_are_exact_monotonic_and_bounded() -> None:
    assert aihub538_v4_interior_timestamps_us(1_000_000) == (
        62_500, 187_500, 312_500, 437_500,
        562_500, 687_500, 812_500, 937_500,
    )
    with pytest.raises(ValueError, match="usable"):
        aihub538_v4_interior_timestamps_us(8)
    for duration, count in ((True, 8), (2**63, 8), (1_000_000, True)):
        with pytest.raises(ValueError, match="policy_invalid"):
            aihub538_v4_interior_timestamps_us(duration, frame_count=count)


class _FakeColor:
    _ffprobe_binary = "/usr/bin/ffprobe"
    _ffmpeg_binary = "/usr/bin/ffmpeg"

    def __init__(self, *, fail_index: int | None = None, fps: str = "30/1") -> None:
        self.calls = 0
        self.fail_index = fail_index
        self.fps = fps

    def _run(self, argv, **_kwargs):
        if argv[0].endswith("ffprobe"):
            return SimpleNamespace(
                return_code=0,
                stderr=b"",
                stdout=json.dumps(
                    {
                        "streams": [{
                            "index": 0, "width": 160, "height": 120,
                            "pix_fmt": "yuv420p", "color_range": "tv",
                            "color_space": "bt709", "color_transfer": "bt709",
                            "color_primaries": "bt709", "avg_frame_rate": self.fps,
                            "r_frame_rate": self.fps, "nb_frames": "30",
                        }],
                        "format": {"duration": "1.000000"},
                    }
                ).encode(),
            )
        index = self.calls
        self.calls += 1
        payload = bytes((index,)) * (160 * 120 * 3)
        if index == self.fail_index:
            payload = b""
        return SimpleNamespace(return_code=0, stderr=b"", stdout=payload)

    def verify_conversion_contract(self):
        return SimpleNamespace(receipt_sha256="a" * 64)

    def _ffmpeg_revision(self):
        return "ffmpeg-test"


def _exact_color(**kwargs):
    fake = _FakeColor(**kwargs)
    adapter = object.__new__(FfmpegRgbColorimetryAdapter)
    adapter._ffprobe_binary = fake._ffprobe_binary
    adapter._ffmpeg_binary = fake._ffmpeg_binary
    adapter._run = fake._run
    adapter.verify_conversion_contract = fake.verify_conversion_contract
    adapter._ffmpeg_revision = fake._ffmpeg_revision
    adapter.issue_sample_lineage = lambda **_kwargs: (SimpleNamespace(), SimpleNamespace())
    return adapter


def test_sampler_emits_exactly_eight_policy_bound_frames(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("voice_coach.visual_feedback.adapters.aihub538_photometric_v4_sampling.require_adapter_issued_color_lineage", lambda **_kwargs: None)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"synthetic-container")
    fd = os.open(video, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        sampler = Aihub538PhotometricV4EightFrameSampler(
            policy=Aihub538PhotometricV4ProductionExecutionPolicy.frozen(),
            color_adapter=_exact_color(),
        )
        sample, receipt = sampler.sample(video_fd=fd)
    finally:
        os.close(fd)
    assert len(sample.frames) == len(receipt.frame_receipts) == 8
    assert tuple(frame.timestamp_us for frame in sample.frames) == aihub538_v4_interior_timestamps_us(1_000_000)
    assert len({item.ffmpeg_argv_sha256 for item in receipt.frame_receipts}) == 8
    assert receipt.production_execution_policy_sha256 == AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
    assert require_aihub538_photometric_v4_eight_frame_sample(
        sample, receipt=receipt,
        policy=Aihub538PhotometricV4ProductionExecutionPolicy.frozen(),
    ) is sample
    with pytest.raises(Aihub538PhotometricV4SamplingError):
        require_aihub538_photometric_v4_eight_frame_sample(
            sample, receipt=receipt,
            policy=Aihub538PhotometricV4ProductionExecutionPolicy.frozen(),
        )


def test_sampler_fails_closed_when_any_of_eight_frames_is_unusable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("voice_coach.visual_feedback.adapters.aihub538_photometric_v4_sampling.require_adapter_issued_color_lineage", lambda **_kwargs: None)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"synthetic-container")
    fd = os.open(video, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(Aihub538PhotometricV4SamplingError):
            Aihub538PhotometricV4EightFrameSampler(
                policy=Aihub538PhotometricV4ProductionExecutionPolicy.frozen(),
                color_adapter=_exact_color(fail_index=7),
            ).sample(video_fd=fd)
    finally:
        os.close(fd)


def test_sampler_rejects_ten_fps_container(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("voice_coach.visual_feedback.adapters.aihub538_photometric_v4_sampling.require_adapter_issued_color_lineage", lambda **_kwargs: None)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"synthetic-container")
    fd = os.open(video, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(Aihub538PhotometricV4SamplingError):
            Aihub538PhotometricV4EightFrameSampler(
                policy=Aihub538PhotometricV4ProductionExecutionPolicy.frozen(),
                color_adapter=_exact_color(fps="10/1"),
            ).sample(video_fd=fd)
    finally:
        os.close(fd)
