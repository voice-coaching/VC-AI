"""Model-input audio decoding, batching, and label-aware chunking."""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd, isfinite
from pathlib import Path
from typing import Literal, Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray
import soundfile as sf
from scipy.signal import resample_poly

from .intervals import Interval

Float32Array = NDArray[np.float32]
Int64Array = NDArray[np.int64]
BoundaryKind = Literal[
    "utterance_end",
    "max_duration",
    "label_boundary",
    "forced_max_duration",
]


@dataclass(frozen=True, slots=True)
class DecodedAudio:
    """Finite mono audio at the requested model sample rate."""

    samples: Float32Array
    sample_rate: int
    source_sample_rate: int
    source_channels: int

    @property
    def duration_seconds(self) -> float:
        return self.samples.size / self.sample_rate


@dataclass(frozen=True, slots=True)
class PaddedAudioBatch:
    """Right-padded model inputs and a 1-for-audio/0-for-padding mask."""

    input_values: Float32Array
    attention_mask: Int64Array
    lengths: Int64Array


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """A chunk span plus an audit trail for any label interval it cuts."""

    start_seconds: float
    end_seconds: float
    interval_indices: tuple[int, ...]
    cut_interval_indices: tuple[int, ...]
    overlong_interval_indices: tuple[int, ...]
    boundary_kind: BoundaryKind

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def end_forced_through_label(self) -> bool:
        return self.boundary_kind == "forced_max_duration"


def _mono_float32(samples: ArrayLike, *, field_name: str = "samples") -> Float32Array:
    audio = np.asarray(samples)
    if audio.ndim != 1:
        raise ValueError(f"{field_name} must be mono")
    if audio.size == 0:
        raise ValueError(f"{field_name} must not be empty")
    if not np.issubdtype(audio.dtype, np.number) or np.issubdtype(
        audio.dtype,
        np.complexfloating,
    ):
        raise ValueError(f"{field_name} must contain real numeric values")
    if not np.isfinite(audio).all():
        raise ValueError(f"{field_name} contains NaN or Inf")
    return np.ascontiguousarray(audio, dtype=np.float32)


def _sample_rate(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return int(value)


def resample_audio(
    samples: ArrayLike,
    source_sample_rate: int,
    target_sample_rate: int = 16_000,
) -> Float32Array:
    """Validate mono audio and resample it with a polyphase anti-aliasing filter."""

    source_sample_rate = _sample_rate(
        source_sample_rate,
        field_name="source_sample_rate",
    )
    target_sample_rate = _sample_rate(
        target_sample_rate,
        field_name="target_sample_rate",
    )
    audio = _mono_float32(samples)
    if source_sample_rate == target_sample_rate:
        return audio.copy()

    common = gcd(source_sample_rate, target_sample_rate)
    output = resample_poly(
        audio,
        up=target_sample_rate // common,
        down=source_sample_rate // common,
    )
    if output.size == 0 or not np.isfinite(output).all():
        raise ValueError("resampling produced empty or non-finite audio")
    return np.ascontiguousarray(output, dtype=np.float32)


def decode_audio(
    path: str | Path,
    *,
    target_sample_rate: int = 16_000,
) -> DecodedAudio:
    """Decode with libsndfile, downmix channels, validate, and resample.

    Integer PCM is decoded to floating point in libsndfile's normalized range.
    Multichannel inputs are deterministically downmixed by an arithmetic mean.
    The original file is read-only and is never rewritten.
    """

    target_sample_rate = _sample_rate(
        target_sample_rate,
        field_name="target_sample_rate",
    )
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"audio file does not exist: {source}")
    try:
        decoded, source_sample_rate = sf.read(
            source,
            dtype="float32",
            always_2d=True,
        )
    except (OSError, RuntimeError, sf.LibsndfileError) as exc:
        raise ValueError(f"failed to decode audio: {source}") from exc

    source_sample_rate = _sample_rate(
        source_sample_rate,
        field_name="decoded sample rate",
    )
    if decoded.ndim != 2 or decoded.shape[0] == 0 or decoded.shape[1] == 0:
        raise ValueError("decoded audio must contain frames and channels")
    if not np.isfinite(decoded).all():
        raise ValueError("decoded audio contains NaN or Inf")

    source_channels = int(decoded.shape[1])
    if source_channels == 1:
        mono = decoded[:, 0]
    else:
        mono = np.mean(decoded, axis=1, dtype=np.float64).astype(np.float32)
    samples = resample_audio(
        mono,
        source_sample_rate=source_sample_rate,
        target_sample_rate=target_sample_rate,
    )
    return DecodedAudio(
        samples=samples,
        sample_rate=target_sample_rate,
        source_sample_rate=source_sample_rate,
        source_channels=source_channels,
    )


def pad_audio_batch(
    waveforms: Sequence[ArrayLike],
    *,
    pad_value: float = 0.0,
    pad_to_multiple_of: int | None = None,
) -> PaddedAudioBatch:
    """Right-pad finite mono waveforms and create a Transformer attention mask."""

    if len(waveforms) == 0:
        raise ValueError("waveforms must not be empty")
    if not isfinite(pad_value):
        raise ValueError("pad_value must be finite")
    if pad_to_multiple_of is not None and pad_to_multiple_of <= 0:
        raise ValueError("pad_to_multiple_of must be positive")

    validated = [
        _mono_float32(waveform, field_name=f"waveforms[{index}]")
        for index, waveform in enumerate(waveforms)
    ]
    lengths = np.asarray([waveform.size for waveform in validated], dtype=np.int64)
    padded_length = int(lengths.max())
    if pad_to_multiple_of is not None:
        padded_length = (
            (padded_length + pad_to_multiple_of - 1) // pad_to_multiple_of
        ) * pad_to_multiple_of

    input_values = np.full(
        (len(validated), padded_length),
        pad_value,
        dtype=np.float32,
    )
    attention_mask = np.zeros(
        (len(validated), padded_length),
        dtype=np.int64,
    )
    for row, waveform in enumerate(validated):
        length = waveform.size
        input_values[row, :length] = waveform
        attention_mask[row, :length] = 1
    return PaddedAudioBatch(
        input_values=input_values,
        attention_mask=attention_mask,
        lengths=lengths,
    )


def make_label_aware_chunks(
    duration_seconds: float,
    intervals: Sequence[Interval],
    *,
    max_duration_seconds: float = 20.0,
) -> list[AudioChunk]:
    """Partition an utterance without cutting labeled intervals when possible.

    A boundary inside a label is used only when no safe boundary exists before
    the duration limit. An interval longer than the limit is therefore split,
    and its original index appears in ``overlong_interval_indices`` and
    ``cut_interval_indices`` so callers cannot silently treat it as intact.
    Blank labels are treated as safe-to-cut silence.
    """

    if not isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration_seconds must be finite and positive")
    if not isfinite(max_duration_seconds) or max_duration_seconds <= 0:
        raise ValueError("max_duration_seconds must be finite and positive")

    labeled: list[tuple[int, Interval]] = []
    for index, interval in enumerate(intervals):
        if not isfinite(interval.start) or not isfinite(interval.end):
            raise ValueError(f"intervals[{index}] has a non-finite boundary")
        if interval.start < 0 or interval.end < interval.start:
            raise ValueError(f"intervals[{index}] has invalid boundaries")
        if interval.end > duration_seconds:
            raise ValueError(f"intervals[{index}] exceeds the audio duration")
        if interval.label.strip() and interval.end > interval.start:
            labeled.append((index, interval))

    boundary_candidates = {0.0, duration_seconds}
    for _, interval in labeled:
        boundary_candidates.add(interval.start)
        boundary_candidates.add(interval.end)

    def is_safe_boundary(point: float) -> bool:
        return not any(
            interval.start < point < interval.end
            for _, interval in labeled
        )

    output: list[AudioChunk] = []
    start = 0.0
    while start < duration_seconds:
        limit = min(duration_seconds, start + max_duration_seconds)
        if limit == duration_seconds:
            end = duration_seconds
        else:
            candidates = [
                point
                for point in (*boundary_candidates, limit)
                if start < point <= limit and is_safe_boundary(point)
            ]
            end = max(candidates) if candidates else limit

        if end <= start:
            raise RuntimeError("chunking failed to make forward progress")
        active = tuple(
            index
            for index, interval in labeled
            if interval.end > start and interval.start < end
        )
        cut = tuple(
            index
            for index, interval in labeled
            if interval.start < start < interval.end
            or interval.start < end < interval.end
        )
        overlong = tuple(
            index
            for index, interval in labeled
            if index in active and interval.duration > max_duration_seconds
        )
        if end == duration_seconds:
            boundary_kind: BoundaryKind = "utterance_end"
        elif not is_safe_boundary(end):
            boundary_kind = "forced_max_duration"
        elif end == limit:
            boundary_kind = "max_duration"
        else:
            boundary_kind = "label_boundary"

        output.append(
            AudioChunk(
                start_seconds=start,
                end_seconds=end,
                interval_indices=active,
                cut_interval_indices=cut,
                overlong_interval_indices=overlong,
                boundary_kind=boundary_kind,
            )
        )
        start = end
    return output
