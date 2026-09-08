"""Decode and quality-control one learner recording.

Wraps :func:`voice_coach.data.decode_audio` (libsndfile, deterministic downmix,
pinned resample) and adds the checks a practice attempt needs before any model
runs: duration bounds, silence, clipping, and finite PCM.

Two failure kinds are kept apart on purpose: a container/codec problem is
``decode_failed`` (the file is unusable), while a too-short, silent, or clipped
recording is ``qc_failed`` (the learner should record again).  Raw audio is read
only; nothing is written and no waveform is retained on the observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from voice_coach.data import decode_audio
from voice_coach.data.wav_qc import pcm_sha256

from ..domain import AudioObservation, AudioQcStatus

DECODER_ID = "soundfile-decode-audio"


@dataclass(frozen=True, slots=True)
class AudioQcPolicy:
    """Frozen intake limits for a practice recording."""

    target_sample_rate: int = 16_000
    minimum_duration_seconds: float = 0.4
    maximum_duration_seconds: float = 30.0
    minimum_rms: float = 1e-3
    maximum_clipping_ratio: float = 0.01
    clipping_amplitude: float = 0.999

    def __post_init__(self) -> None:
        if self.minimum_duration_seconds <= 0:
            raise ValueError("minimum_duration_seconds must be positive")
        if self.maximum_duration_seconds <= self.minimum_duration_seconds:
            raise ValueError("maximum_duration_seconds must exceed the minimum")
        if not 0 < self.minimum_rms < 1:
            raise ValueError("minimum_rms must be within (0, 1)")
        if not 0 <= self.maximum_clipping_ratio <= 1:
            raise ValueError("maximum_clipping_ratio must be within [0, 1]")


class SoundfileAudioIntake:
    """``AudioIntakePort`` implementation for local research runs."""

    def __init__(self, policy: AudioQcPolicy | None = None) -> None:
        self._policy = policy or AudioQcPolicy()
        self._revision = f"soundfile-{sf.__version__}"

    @property
    def policy(self) -> AudioQcPolicy:
        return self._policy

    def observe(self, audio_path: Path) -> AudioObservation:
        path = Path(audio_path)
        try:
            decoded = decode_audio(
                path, target_sample_rate=self._policy.target_sample_rate
            )
        except (ValueError, OSError) as exc:
            return AudioObservation(
                status=AudioQcStatus.DECODE_FAILED,
                audio_sha256=None,
                duration_seconds=None,
                sample_rate=None,
                source_sample_rate=None,
                source_channels=None,
                decoder_id=DECODER_ID,
                decoder_revision=self._revision,
                detail=str(exc),
            )

        samples = np.asarray(decoded.samples, dtype=np.float32)
        duration_seconds = float(samples.size) / float(decoded.sample_rate)
        failures: list[str] = []
        if not np.isfinite(samples).all():
            failures.append("non_finite_pcm")
        if duration_seconds < self._policy.minimum_duration_seconds:
            failures.append("too_short")
        if duration_seconds > self._policy.maximum_duration_seconds:
            failures.append("too_long")
        if samples.size:
            rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
            clipped = int(
                np.count_nonzero(np.abs(samples) >= self._policy.clipping_amplitude)
            )
            clipping_ratio = clipped / samples.size
            if rms < self._policy.minimum_rms:
                failures.append("silence")
            if clipping_ratio > self._policy.maximum_clipping_ratio:
                failures.append("clipping")
        else:
            failures.append("empty_pcm")

        status = (
            AudioQcStatus.QC_PASSED if not failures else AudioQcStatus.QC_FAILED
        )
        return AudioObservation(
            status=status,
            audio_sha256=pcm_sha256(path),
            duration_seconds=duration_seconds,
            sample_rate=decoded.sample_rate,
            source_sample_rate=decoded.source_sample_rate,
            source_channels=decoded.source_channels,
            decoder_id=DECODER_ID,
            decoder_revision=self._revision,
            qc_failures=tuple(failures),
        )
