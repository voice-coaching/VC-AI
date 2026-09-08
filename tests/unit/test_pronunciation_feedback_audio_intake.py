"""Stage B contracts: decode failure and quality control stay distinguishable."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from voice_coach.pronunciation_feedback.adapters.audio_intake import (
    AudioQcPolicy,
    SoundfileAudioIntake,
)
from voice_coach.pronunciation_feedback.domain import AudioQcStatus


def _speechlike(seconds: float, *, sample_rate: int = 16_000, amplitude: float = 0.2):
    time = np.arange(round(seconds * sample_rate), dtype=np.float64) / sample_rate
    tone = np.sin(2 * np.pi * 180.0 * time) + 0.4 * np.sin(2 * np.pi * 540.0 * time)
    return (amplitude * tone / np.max(np.abs(tone))).astype(np.float32)


def _write(path: Path, samples: np.ndarray, sample_rate: int = 16_000) -> Path:
    sf.write(path, samples, sample_rate, subtype="PCM_16")
    return path


def test_valid_recording_passes_qc_and_reports_identity(tmp_path: Path) -> None:
    path = _write(tmp_path / "ok.wav", _speechlike(1.5))

    observation = SoundfileAudioIntake().observe(path)

    assert observation.status is AudioQcStatus.QC_PASSED
    assert observation.qc_failures == ()
    assert observation.sample_rate == 16_000
    assert observation.source_channels == 1
    assert observation.duration_seconds == pytest.approx(1.5, abs=1e-3)
    assert observation.audio_sha256 is not None and len(observation.audio_sha256) == 64
    assert observation.decoder_revision.startswith("soundfile-")


def test_stereo_and_offrate_input_is_downmixed_and_resampled(tmp_path: Path) -> None:
    mono = _speechlike(1.0, sample_rate=8_000)
    stereo = np.stack([mono, 0.5 * mono], axis=1)
    path = _write(tmp_path / "stereo.wav", stereo, sample_rate=8_000)

    observation = SoundfileAudioIntake().observe(path)

    assert observation.status is AudioQcStatus.QC_PASSED
    assert observation.source_channels == 2
    assert observation.source_sample_rate == 8_000
    assert observation.sample_rate == 16_000


def test_corrupt_container_is_decode_failed_not_qc_failed(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"RIFFnot-really-a-wav")

    observation = SoundfileAudioIntake().observe(path)

    assert observation.status is AudioQcStatus.DECODE_FAILED
    assert observation.audio_sha256 is None
    assert observation.detail


def test_missing_file_is_decode_failed(tmp_path: Path) -> None:
    observation = SoundfileAudioIntake().observe(tmp_path / "absent.wav")

    assert observation.status is AudioQcStatus.DECODE_FAILED


def test_silence_fails_qc(tmp_path: Path) -> None:
    path = _write(tmp_path / "silent.wav", np.zeros(16_000, dtype=np.float32))

    observation = SoundfileAudioIntake().observe(path)

    assert observation.status is AudioQcStatus.QC_FAILED
    assert "silence" in observation.qc_failures


def test_too_short_recording_fails_qc(tmp_path: Path) -> None:
    path = _write(tmp_path / "short.wav", _speechlike(0.1))

    observation = SoundfileAudioIntake().observe(path)

    assert observation.status is AudioQcStatus.QC_FAILED
    assert "too_short" in observation.qc_failures


def test_too_long_recording_fails_qc(tmp_path: Path) -> None:
    policy = AudioQcPolicy(maximum_duration_seconds=1.0)
    path = _write(tmp_path / "long.wav", _speechlike(2.0))

    observation = SoundfileAudioIntake(policy).observe(path)

    assert observation.status is AudioQcStatus.QC_FAILED
    assert "too_long" in observation.qc_failures


def test_clipped_recording_fails_qc(tmp_path: Path) -> None:
    clipped = np.ones(16_000, dtype=np.float32)
    clipped[::2] = -1.0
    path = _write(tmp_path / "clipped.wav", clipped)

    observation = SoundfileAudioIntake().observe(path)

    assert observation.status is AudioQcStatus.QC_FAILED
    assert "clipping" in observation.qc_failures


def test_policy_rejects_inconsistent_limits() -> None:
    with pytest.raises(ValueError):
        AudioQcPolicy(minimum_duration_seconds=2.0, maximum_duration_seconds=1.0)
