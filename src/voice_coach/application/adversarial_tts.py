"""Serving-neutral adapters for generated pronunciation-reference audio."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import torch


class GeneratedAudioError(ValueError):
    """Raised when generated audio cannot be safely materialized."""


@dataclass(frozen=True, slots=True)
class GeneratedAudioArtifact:
    schema_version: str
    media_label: str
    relative_name: str
    sample_rate_hz: int
    sample_count: int
    sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_no_overwrite(temporary: Path, destination: Path) -> None:
    """Atomically publish a same-filesystem temporary without replacement."""

    os.link(temporary, destination)
    temporary.unlink()


def write_generated_waveform(
    path: str | Path,
    waveform: torch.Tensor,
    *,
    sample_rate_hz: int = 16_000,
) -> GeneratedAudioArtifact:
    """Atomically write one generated WAV plus a mandatory provenance sidecar.

    The sidecar intentionally excludes source paths, prompt text, speaker IDs,
    and evaluator feedback.  Existing files are never overwritten.
    """

    try:
        import soundfile as sf  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise GeneratedAudioError(
            "soundfile is required to materialize generated audio"
        ) from exc
    output = Path(path)
    if output.suffix.lower() != ".wav":
        raise GeneratedAudioError("generated audio path must end in .wav")
    if (
        isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, int)
        or sample_rate_hz <= 0
    ):
        raise GeneratedAudioError("sample_rate_hz must be a positive integer")
    if not isinstance(waveform, torch.Tensor):
        raise GeneratedAudioError("waveform must be a tensor")
    if waveform.ndim == 2 and waveform.shape[0] == 1:
        waveform = waveform.squeeze(0)
    if (
        waveform.ndim != 1
        or waveform.numel() < 16
        or not torch.is_floating_point(waveform)
        or not torch.isfinite(waveform).all()
    ):
        raise GeneratedAudioError(
            "waveform must be one finite floating-point audio item"
        )
    if float(waveform.detach().abs().max()) > 1:
        raise GeneratedAudioError("waveform must be normalized to [-1, 1]")
    sidecar = output.with_suffix(output.suffix + ".generated.json")
    if output.exists() or sidecar.exists():
        raise GeneratedAudioError("generated audio output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    samples = np.ascontiguousarray(
        waveform.detach().to(device="cpu", dtype=torch.float32).numpy()
    )
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".part",
        dir=output.parent,
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    sidecar_temporary: Path | None = None
    published_audio = False
    try:
        sf.write(
            temporary,
            samples,
            sample_rate_hz,
            format="WAV",
            subtype="PCM_16",
        )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        _publish_no_overwrite(temporary, output)
        published_audio = True
        artifact = GeneratedAudioArtifact(
            schema_version="voice_coach.generated_audio.v1",
            media_label="AI-generated pronunciation reference",
            relative_name=output.name,
            sample_rate_hz=sample_rate_hz,
            sample_count=int(samples.size),
            sha256=_sha256_file(output),
        )
        sidecar_descriptor, sidecar_name = tempfile.mkstemp(
            prefix=f".{sidecar.name}.",
            suffix=".part",
            dir=sidecar.parent,
        )
        sidecar_temporary = Path(sidecar_name)
        serialized = (
            json.dumps(
                asdict(artifact),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        with os.fdopen(
            sidecar_descriptor,
            "w",
            encoding="utf-8",
        ) as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        with sidecar_temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        _publish_no_overwrite(sidecar_temporary, sidecar)
        return artifact
    except Exception:
        temporary.unlink(missing_ok=True)
        if sidecar_temporary is not None:
            sidecar_temporary.unlink(missing_ok=True)
        if published_audio and output.exists() and not sidecar.exists():
            output.unlink()
        raise
