"""Service-owned prepared WAV measurements; no source-duration substitution."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import re
import stat
import wave

from .canonical_bridge import CanonicalIntegrationError, _require


@dataclass(frozen=True, slots=True)
class PreparedAudioIdentity:
    sha256: str
    frame_count: int
    sample_rate: int

    def to_document(self) -> dict:
        _require(isinstance(self.sha256, str) and re.fullmatch(r'[0-9a-f]{64}', self.sha256)
                 and type(self.frame_count) is int and 0 <= self.frame_count <= 9007199254740991
                 and type(self.sample_rate) is int and 0 < self.sample_rate <= 2147483647,
                 'PREPARED_DURATION_INVALID')
        return dict(sha256=self.sha256, frameCount=self.frame_count, sampleRate=self.sample_rate,
                    measurementSource='SERVER_PREPARED_WAV_PCM_FRAMES')

    @classmethod
    def from_document(cls, value: dict) -> PreparedAudioIdentity:
        _require(type(value) is dict and set(value) == {
            'sha256', 'frameCount', 'sampleRate', 'measurementSource'}, 'PREPARED_DURATION_INVALID')
        result = cls(value['sha256'], value['frameCount'], value['sampleRate'])
        _require(result.to_document() == value, 'PREPARED_DURATION_INVALID')
        return result

    @property
    def duration_s(self) -> Decimal:
        self.to_document()
        return Decimal(self.frame_count) / Decimal(self.sample_rate)


def measure_prepared_wav(path: Path, expected_sha256: str) -> PreparedAudioIdentity:
    """Call on a finished server-owned file inside the executor's private workspace.

    Hash and count actual PCM bytes from one no-follow file descriptor. Preserve
    the measurement in the private association; this does not establish source
    playback mapping, articulation duration, speech rate, or Backend verification.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            _require(stat.S_ISREG(before.st_mode), 'PREPARED_AUDIO_PATH_INVALID')
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            _require(sha == expected_sha256, 'PREPARED_AUDIO_MISMATCH')
            stream.seek(0)
            with wave.open(stream, 'rb') as pcm:
                channels, width, rate, frames = (pcm.getnchannels(), pcm.getsampwidth(),
                                                pcm.getframerate(), pcm.getnframes())
                _require(pcm.getcomptype() == 'NONE' and channels > 0 and width > 0,
                         'PREPARED_PCM_INVALID')
                expected_bytes, count = frames * channels * width, 0
                while chunk := pcm.readframes(8192):
                    count += len(chunk)
                    _require(count <= expected_bytes, 'PREPARED_PCM_LENGTH_MISMATCH')
                _require(count == expected_bytes, 'PREPARED_PCM_LENGTH_MISMATCH')
            after = os.fstat(stream.fileno())
            _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                     == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                     'PREPARED_AUDIO_CHANGED')
        identity = PreparedAudioIdentity(sha, frames, rate)
        identity.to_document()
        return identity
    except CanonicalIntegrationError:
        raise
    except (OSError, ValueError, EOFError, wave.Error):
        raise CanonicalIntegrationError('PREPARED_PCM_INVALID') from None

