"""Real local ffprobe/ffmpeg tests over synthetic, non-person media."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

import pytest

from voice_coach.pronunciation_attempts.adapters.ffmpeg_av_container import (
    FfmpegAvContainerAdapter,
)
from voice_coach.pronunciation_attempts.adapters.restricted_local_media import (
    LocalRestrictedVideoAdmissionAdapter,
)
from voice_coach.pronunciation_attempts.models import (
    AvContainerPolicy,
    MediaErrorCode,
    PronunciationAttemptMediaError,
    VideoAdmissionPolicy,
    VideoConsentAuthorization,
    VideoConsentDecision,
)
from voice_coach.pronunciation_attempts.phone_window import (
    PhoneWindowDecodePolicy,
    PhoneWindowDecodeRequest,
)


HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
NOW = datetime(2026, 8, 28, 3, 0, tzinfo=UTC)
ATTEMPT_SHA256 = hashlib.sha256(b"synthetic-attempt").hexdigest()
CONSENT_SHA256 = hashlib.sha256(b"synthetic-consent").hexdigest()


def _authorization() -> VideoConsentAuthorization:
    return VideoConsentAuthorization(
        attempt_id_sha256=ATTEMPT_SHA256,
        consent_receipt_sha256=CONSENT_SHA256,
        decision=VideoConsentDecision.GRANTED,
        purpose="lip_shape_feedback",
        data_category="facial_video",
        retention_policy_id="delete_after_attempt",
        delete_on_completion=True,
        remote_egress_allowed=False,
        issued_at_utc=NOW - timedelta(minutes=1),
        expires_at_utc=NOW + timedelta(minutes=5),
    )


def _admission_policy() -> VideoAdmissionPolicy:
    return VideoAdmissionPolicy(
        maximum_source_bytes=5_000_000,
        required_purpose="lip_shape_feedback",
        required_data_category="facial_video",
        allowed_retention_policy_ids=("delete_after_attempt",),
    )


def _container_policy(**overrides: object) -> AvContainerPolicy:
    values: dict[str, object] = {
        "minimum_duration_ms": 900,
        "maximum_duration_ms": 1_100,
        "minimum_width": 160,
        "maximum_width": 640,
        "minimum_height": 120,
        "maximum_height": 480,
        "minimum_frames_per_second": 20.0,
        "maximum_frames_per_second": 30.0,
        "minimum_frame_count": 20,
        "maximum_frame_count": 30,
        "maximum_rgb_sample_frames": 5,
        "maximum_rgb_sample_bytes": 2_000_000,
        "maximum_pcm_bytes": 64_000,
        "subprocess_timeout_seconds": 10.0,
        "maximum_subprocess_output_bytes": 1_000_000,
    }
    values.update(overrides)
    return AvContainerPolicy(**values)  # type: ignore[arg-type]


def _generate_fixture(path: Path, *, with_audio: bool = True, codec: str = "libx264") -> None:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=25",
    ]
    if with_audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
            ]
        )
    command.extend(["-t", "1", "-c:v", codec])
    if codec in {"libx264", "libx265"}:
        command.extend(["-pix_fmt", "yuv420p"])
    if codec == "libx265":
        command.extend(["-x265-params", "pools=1:frame-threads=1"])
    if with_audio:
        command.extend(["-c:a", "aac", "-shortest"])
    command.extend(["-movflags", "+faststart", "-y", str(path)])
    subprocess.run(command, check=True, shell=False, timeout=20)


def _admit(tmp_path: Path, source: Path):
    store = LocalRestrictedVideoAdmissionAdapter(
        restricted_root=tmp_path / "restricted",
        clock=lambda: NOW,
    )
    asset = store.admit(
        source,
        attempt_id_sha256=ATTEMPT_SHA256,
        authorization=_authorization(),
        policy=_admission_policy(),
    )
    return store, asset


@pytest.mark.skipif(not HAS_FFMPEG, reason="local ffmpeg/ffprobe required")
def test_strict_probe_bounded_rgb_decode_and_audio_derivation(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.mp4"
    _generate_fixture(source)
    store, asset = _admit(tmp_path, source)
    adapter = FfmpegAvContainerAdapter(store)
    policy = _container_policy()

    probe = adapter.probe(asset, policy)
    assert probe.format_name == "mp4"
    assert probe.video_stream.codec_name == "h264"
    assert probe.audio_stream.codec_name == "aac"
    assert (probe.video_stream.width, probe.video_stream.height) == (320, 240)
    assert probe.video_stream.frames_per_second == 25.0
    assert probe.video_stream.frame_count == 25
    assert len(probe.video_frame_timestamps_us) == 25

    sample = adapter.decode_rgb24_sample(asset, policy, frame_count=3)
    assert len(sample.frames) == 3
    assert all(len(frame.rgb24) == 320 * 240 * 3 for frame in sample.frames)
    assert [frame.timestamp_us for frame in sample.frames] == [
        probe.video_frame_timestamps_us[index] for index in (0, 12, 24)
    ]

    audio = adapter.extract_embedded_audio(asset, policy)
    receipt = audio.derivation_receipt
    assert receipt.container_sha256 == asset.container_sha256
    assert receipt.audio_stream_index == probe.audio_stream.stream_index
    assert receipt.tool_revision.startswith("ffmpeg:")
    assert receipt.sample_rate_hz == 16_000
    assert receipt.channels == 1
    assert receipt.source_time_base == probe.audio_stream.time_base
    assert receipt.pcm_time_base == "1/16000"
    assert audio.byte_count == audio.sample_count * 2

    with store.open_derived_audio(audio) as opened:
        pcm = opened.read()
    assert len(pcm) == audio.byte_count
    assert hashlib.sha256(pcm).hexdigest() == receipt.pcm_sha256
    derived_path = tmp_path / "restricted" / asset.handle_id / audio.artifact_id
    assert stat.S_IMODE(derived_path.stat().st_mode) == 0o400

    store.delete(asset)
    assert source.exists()
    assert list((tmp_path / "restricted").iterdir()) == []


@pytest.mark.skipif(not HAS_FFMPEG, reason="local ffmpeg/ffprobe required")
def test_hevc_aac_mp4_is_explicitly_allowlisted(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-hevc.mp4"
    _generate_fixture(source, codec="libx265")
    store, asset = _admit(tmp_path, source)
    adapter = FfmpegAvContainerAdapter(store)

    probe = adapter.probe(asset, _container_policy())

    assert probe.video_stream.codec_name == "hevc"
    assert probe.audio_stream.codec_name == "aac"
    sample = adapter.decode_rgb24_sample(
        asset,
        _container_policy(),
        frame_count=3,
    )
    assert len(sample.frames) == 3
    audio = adapter.extract_embedded_audio(asset, _container_policy())
    assert audio.derivation_receipt.container_sha256 == asset.container_sha256
    assert audio.derivation_receipt.pcm_sha256
    store.delete(asset)


@pytest.mark.skipif(not HAS_FFMPEG, reason="local ffmpeg/ffprobe required")
@pytest.mark.skipif(
    not os.environ.get("VOICE_COACH_PRIVATE_VIDEO_SAMPLE"),
    reason="approved private device sample was not supplied",
)
def test_approved_private_device_capture_keeps_one_container_lineage(
    tmp_path: Path,
) -> None:
    source = Path(os.environ["VOICE_COACH_PRIVATE_VIDEO_SAMPLE"]).resolve(strict=True)
    store, asset = _admit(tmp_path, source)
    adapter = FfmpegAvContainerAdapter(store)
    policy = _container_policy(
        maximum_duration_ms=5_000,
        maximum_width=1_920,
        maximum_height=1_920,
        maximum_frame_count=200,
        maximum_rgb_sample_bytes=20_000_000,
        maximum_pcm_bytes=200_000,
        subprocess_timeout_seconds=20.0,
    )

    probe = adapter.probe(asset, policy)
    sample = adapter.decode_rgb24_sample(asset, policy, frame_count=3)
    audio = adapter.extract_embedded_audio(asset, policy)

    assert probe.container_sha256 == asset.container_sha256
    assert sample.container_sha256 == asset.container_sha256
    assert audio.derivation_receipt.container_sha256 == asset.container_sha256
    assert sample.asset_handle_id == asset.handle_id
    assert audio.asset_handle_id == asset.handle_id
    assert probe.video_stream.codec_name in {"h264", "hevc"}
    assert probe.audio_stream.codec_name == "aac"
    store.delete(asset)


@pytest.mark.skipif(not HAS_FFMPEG, reason="local ffmpeg/ffprobe required")
def test_phone_window_decode_binds_exact_pts_and_decompressed_bytes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic-window.mp4"
    _generate_fixture(source)
    store, asset = _admit(tmp_path, source)
    adapter = FfmpegAvContainerAdapter(store)
    policy = _container_policy()
    probe = adapter.probe(asset, policy)
    window_policy = PhoneWindowDecodePolicy(
        policy_revision="selected_phone_test_v1",
        expected_av_sync_policy_sha256="1" * 64,
        context_contract_sha256="2" * 64,
        maximum_absolute_offset_ms=100,
        padding_before_ms=0,
        padding_after_ms=0,
        minimum_frame_count=2,
        maximum_frame_count=5,
        maximum_window_duration_ms=500,
        maximum_rgb_bytes=2_000_000,
    )
    request = PhoneWindowDecodeRequest.issue(
        attempt_id_sha256=asset.attempt_id_sha256,
        binding_receipt_sha256="3" * 64,
        container_sha256=asset.container_sha256,
        detection_receipt_sha256="4" * 64,
        phone_anchor_sha256="5" * 64,
        av_sync_receipt_sha256="6" * 64,
        av_sync_offset_receipt_sha256="7" * 64,
        policy=window_policy,
        video_start_ms=200,
        video_end_ms=600,
    )

    window = adapter.decode_rgb24_window(
        asset,
        policy,
        probe=probe,
        request=request,
    )

    assert window.decode_receipt.selected_frame_indices == (5, 7, 10, 12, 14)
    assert window.decode_receipt.decoded_timestamps_us == (
        200_000,
        280_000,
        400_000,
        480_000,
        560_000,
    )
    assert tuple(frame.timestamp_us for frame in window.sample.frames) == (
        window.decode_receipt.decoded_timestamps_us
    )
    assert sum(len(frame.rgb24) for frame in window.sample.frames) <= (
        request.maximum_rgb_bytes
    )
    assert window.decode_receipt.receipt_sha256 not in {
        request.request_sha256,
        window.decode_receipt.decoded_rgb_sha256,
    }
    adapter_module_path = getattr(
        sys.modules[FfmpegAvContainerAdapter.__module__], "__file__", None
    )
    assert isinstance(adapter_module_path, str)
    assert window.decode_receipt.decoder_code_sha256 == hashlib.sha256(
        Path(adapter_module_path).read_bytes()
    ).hexdigest()
    store.delete(asset)


@pytest.mark.skipif(not HAS_FFMPEG, reason="local ffmpeg/ffprobe required")
@pytest.mark.parametrize(
    ("with_audio", "codec"),
    [(False, "libx264"), (True, "mpeg4")],
)
def test_non_single_av_or_non_allowlisted_video_container_is_unsupported(
    tmp_path: Path,
    with_audio: bool,
    codec: str,
) -> None:
    source = tmp_path / "unsupported.mp4"
    _generate_fixture(source, with_audio=with_audio, codec=codec)
    store, asset = _admit(tmp_path, source)
    adapter = FfmpegAvContainerAdapter(store)

    with pytest.raises(PronunciationAttemptMediaError) as captured:
        adapter.probe(asset, _container_policy())

    assert captured.value.code is MediaErrorCode.CONTAINER_UNSUPPORTED
    assert str(source) not in str(captured.value)
    store.delete(asset)


@pytest.mark.skipif(not HAS_FFMPEG, reason="local ffmpeg/ffprobe required")
def test_geometry_policy_and_corrupt_container_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.mp4"
    _generate_fixture(source)
    store, asset = _admit(tmp_path, source)
    adapter = FfmpegAvContainerAdapter(store)

    with pytest.raises(PronunciationAttemptMediaError) as geometry_error:
        adapter.probe(asset, _container_policy(maximum_width=300))
    assert geometry_error.value.code is MediaErrorCode.CONTAINER_POLICY_REJECTED
    store.delete(asset)

    corrupt_source = tmp_path / "corrupt-private-name.mp4"
    corrupt_source.write_bytes(b"not an mp4")
    corrupt_store, corrupt_asset = _admit(tmp_path, corrupt_source)
    corrupt_adapter = FfmpegAvContainerAdapter(corrupt_store)
    with pytest.raises(PronunciationAttemptMediaError) as corrupt_error:
        corrupt_adapter.probe(corrupt_asset, _container_policy())
    assert corrupt_error.value.code is MediaErrorCode.CONTAINER_INVALID
    assert str(corrupt_source) not in str(corrupt_error.value)
    corrupt_store.delete(corrupt_asset)


def test_missing_tool_is_path_free_and_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "private.mp4"
    source.write_bytes(b"bytes")
    store, asset = _admit(tmp_path, source)
    adapter = FfmpegAvContainerAdapter(
        store,
        ffprobe_binary="definitely-missing-ffprobe-binary",
    )

    with pytest.raises(PronunciationAttemptMediaError) as captured:
        adapter.probe(asset, _container_policy())

    assert captured.value.code is MediaErrorCode.TOOL_UNAVAILABLE
    assert str(source) not in str(captured.value)
    store.delete(asset)


def test_ffmpeg_launcher_denies_descendant_pipe_holder_before_escape() -> None:
    descendant_pipe_holder = """
import os
import time

child_pid = os.fork()
if child_pid == 0:
    time.sleep(5)
    os._exit(0)
os._exit(0)
"""
    started = time.monotonic()

    return_code = FfmpegAvContainerAdapter._stream_process_stdout(
        command=[sys.executable, "-c", descendant_pipe_holder],
        pass_fds=(),
        timeout_seconds=0.1,
        maximum_stdout_bytes=1024,
        maximum_stderr_bytes=1024,
        stdout_limit_error=MediaErrorCode.TOOL_OUTPUT_LIMIT_EXCEEDED,
        consume_stdout=lambda _payload: None,
    )

    elapsed_seconds = time.monotonic() - started
    assert return_code != 0
    assert elapsed_seconds < 1.0
