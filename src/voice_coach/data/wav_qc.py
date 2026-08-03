"""WAV integrity and duplicate-content helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import wave


def pcm_sha256(path: str | Path, *, chunk_frames: int = 1_048_576) -> str:
    """Hash decoded PCM geometry and frames, excluding mutable RIFF metadata."""

    if chunk_frames <= 0:
        raise ValueError("chunk_frames must be positive")
    digest = hashlib.sha256()
    with wave.open(str(path), "rb") as reader:
        digest.update(
            struct.pack(
                ">IIII",
                reader.getnchannels(),
                reader.getsampwidth(),
                reader.getframerate(),
                reader.getnframes(),
            )
        )
        while payload := reader.readframes(chunk_frames):
            digest.update(payload)
    return digest.hexdigest()
