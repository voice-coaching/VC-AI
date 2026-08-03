"""Pooling helpers for frozen self-supervised speech representations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math

import torch

from voice_coach.data import Interval


@dataclass(frozen=True, slots=True)
class WavLMFrameGeometry:
    """Exact convolutional frame geometry for a WavLM checkpoint."""

    sample_rate_hz: int
    conv_kernels: tuple[int, ...]
    conv_strides: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if not self.conv_kernels or len(self.conv_kernels) != len(self.conv_strides):
            raise ValueError("conv kernels and strides must have equal length")
        if any(value <= 0 for value in (*self.conv_kernels, *self.conv_strides)):
            raise ValueError("conv kernels and strides must be positive")

    @property
    def frame_stride_samples(self) -> int:
        return math.prod(self.conv_strides)

    @property
    def receptive_field_samples(self) -> int:
        receptive_field = 1
        accumulated_stride = 1
        for kernel, stride in zip(
            self.conv_kernels,
            self.conv_strides,
            strict=True,
        ):
            receptive_field += (kernel - 1) * accumulated_stride
            accumulated_stride *= stride
        return receptive_field

    def output_frame_count(self, input_sample_count: int) -> int:
        """Apply the valid Conv1d length formula layer by layer."""

        if input_sample_count < 0:
            raise ValueError("input_sample_count must not be negative")
        length = input_sample_count
        for kernel, stride in zip(
            self.conv_kernels,
            self.conv_strides,
            strict=True,
        ):
            length = (length - kernel) // stride + 1
            if length <= 0:
                return 0
        return length

    def frame_center_seconds(self, frame_index: int) -> float:
        if frame_index < 0:
            raise ValueError("frame_index must not be negative")
        center_sample = (
            frame_index * self.frame_stride_samples
            + (self.receptive_field_samples - 1) / 2
        )
        return center_sample / self.sample_rate_hz

    def interval_frame_span(
        self,
        *,
        start_seconds: float,
        end_seconds: float,
        input_sample_count: int,
    ) -> tuple[int, int]:
        """Map a half-open time interval to frame centers.

        A positive interval shorter than one frame step receives the nearest
        frame.  This makes phone pooling total while keeping adjacent ordinary
        intervals disjoint under the center-based assignment.
        """

        if (
            not math.isfinite(start_seconds)
            or not math.isfinite(end_seconds)
            or start_seconds < 0
            or end_seconds < start_seconds
        ):
            raise ValueError("interval has invalid bounds")
        frame_count = self.output_frame_count(input_sample_count)
        if frame_count == 0 or end_seconds == start_seconds:
            return (0, 0)

        offset = (self.receptive_field_samples - 1) / 2
        start_sample = start_seconds * self.sample_rate_hz
        end_sample = end_seconds * self.sample_rate_hz
        first = math.ceil((start_sample - offset) / self.frame_stride_samples)
        stop = math.ceil((end_sample - offset) / self.frame_stride_samples)
        first = max(0, min(frame_count, first))
        stop = max(first, min(frame_count, stop))
        if stop > first:
            return (first, stop)

        midpoint = (start_sample + end_sample) / 2
        nearest = round((midpoint - offset) / self.frame_stride_samples)
        nearest = max(0, min(frame_count - 1, nearest))
        return (nearest, nearest + 1)


WAVLM_BASE_PLUS_FRAME_GEOMETRY = WavLMFrameGeometry(
    sample_rate_hz=16_000,
    conv_kernels=(10, 3, 3, 3, 3, 2, 2),
    conv_strides=(5, 2, 2, 2, 2, 2, 2),
)


def prepare_wavlm_input(
    samples: torch.Tensor,
    *,
    do_normalize: bool,
    epsilon: float = 1e-7,
) -> torch.Tensor:
    """Prepare one finite mono waveform for WavLM.

    The pinned Base+ revision declares ``do_normalize=false``.  The optional
    normalization branch is retained and tested so a future revision cannot
    silently change preprocessing semantics.
    """

    if samples.ndim != 1:
        raise ValueError("WavLM samples must be one-dimensional")
    if samples.numel() == 0:
        raise ValueError("WavLM samples must not be empty")
    values = samples.to(dtype=torch.float32)
    if not torch.isfinite(values).all():
        raise ValueError("WavLM samples contain NaN or Inf")
    if not do_normalize:
        return values
    if epsilon <= 0:
        raise ValueError("epsilon must be positive")
    variance, mean = torch.var_mean(values, correction=0)
    return (values - mean) / torch.sqrt(variance + epsilon)


def interval_mean_pool(
    hidden_states: torch.Tensor,
    intervals: Iterable[Interval],
    *,
    audio_duration_seconds: float,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Mean-pool ``[batch, frames, dim]`` states within time intervals.

    Returns pooled embeddings and a boolean mask identifying non-empty spans.
    The same interval boundaries are applied to each item in the batch.
    """

    if hidden_states.ndim != 3:
        raise ValueError("hidden_states must have shape [batch, frames, dim]")
    if audio_duration_seconds <= 0:
        raise ValueError("audio_duration_seconds must be positive")
    batch, frame_count, dimension = hidden_states.shape
    items = list(intervals)
    pooled = hidden_states.new_zeros((batch, len(items), dimension))
    valid = torch.zeros(len(items), dtype=torch.bool, device=hidden_states.device)

    for index, interval in enumerate(items):
        start = round(interval.start / audio_duration_seconds * frame_count)
        end = round(interval.end / audio_duration_seconds * frame_count)
        start = min(frame_count, max(0, start))
        end = min(frame_count, max(start + 1, end))
        if start >= frame_count or interval.duration <= 0:
            continue
        pooled[:, index] = hidden_states[:, start:end].mean(dim=1)
        valid[index] = True
    return pooled, valid


def utterance_mean_std_pool(hidden_states: torch.Tensor) -> torch.Tensor:
    """Concatenate utterance-level mean and population standard deviation."""

    if hidden_states.ndim != 3:
        raise ValueError("hidden_states must have shape [batch, frames, dim]")
    mean = hidden_states.mean(dim=1)
    standard_deviation = hidden_states.std(dim=1, correction=0)
    return torch.cat((mean, standard_deviation), dim=-1)
