import pytest
import torch

from voice_coach.data import Interval
from voice_coach.features.ssl import (
    WAVLM_BASE_PLUS_FRAME_GEOMETRY,
    interval_mean_pool,
    prepare_wavlm_input,
    utterance_mean_std_pool,
)


def test_interval_mean_pool_maps_seconds_to_frames() -> None:
    states = torch.arange(8, dtype=torch.float32).reshape(1, 4, 2)
    pooled, valid = interval_mean_pool(
        states,
        [Interval(0.0, 1.0, "a"), Interval(1.0, 2.0, "b")],
        audio_duration_seconds=2.0,
    )
    assert valid.tolist() == [True, True]
    assert torch.equal(pooled[0, 0], torch.tensor([1.0, 2.0]))
    assert torch.equal(pooled[0, 1], torch.tensor([5.0, 6.0]))


def test_utterance_pool_concatenates_mean_and_standard_deviation() -> None:
    states = torch.tensor([[[1.0, 3.0], [3.0, 7.0]]])
    pooled = utterance_mean_std_pool(states)
    assert torch.equal(pooled, torch.tensor([[2.0, 5.0, 1.0, 2.0]]))


def test_pinned_wavlm_frame_geometry_matches_convolution_stack() -> None:
    geometry = WAVLM_BASE_PLUS_FRAME_GEOMETRY

    assert geometry.frame_stride_samples == 320
    assert geometry.receptive_field_samples == 400
    assert geometry.output_frame_count(399) == 0
    assert geometry.output_frame_count(400) == 1
    assert geometry.output_frame_count(16_000) == 49
    assert geometry.frame_center_seconds(0) == 199.5 / 16_000
    assert geometry.frame_center_seconds(1) == 519.5 / 16_000


def test_pinned_wavlm_frame_mapping_uses_half_open_center_spans() -> None:
    geometry = WAVLM_BASE_PLUS_FRAME_GEOMETRY

    first = geometry.interval_frame_span(
        start_seconds=0.0,
        end_seconds=0.02,
        input_sample_count=16_000,
    )
    second = geometry.interval_frame_span(
        start_seconds=0.02,
        end_seconds=0.04,
        input_sample_count=16_000,
    )
    tiny = geometry.interval_frame_span(
        start_seconds=0.019,
        end_seconds=0.0195,
        input_sample_count=16_000,
    )

    assert first == (0, 1)
    assert second == (1, 2)
    assert tiny[1] == tiny[0] + 1


def test_pinned_wavlm_no_normalization_preserves_float32_samples() -> None:
    samples = torch.tensor([-0.5, 0.25, 1.0], dtype=torch.float64)

    prepared = prepare_wavlm_input(samples, do_normalize=False)

    assert prepared.dtype == torch.float32
    assert torch.equal(prepared, samples.to(torch.float32))


def test_optional_wavlm_normalization_is_zero_mean_unit_variance() -> None:
    samples = torch.tensor([-2.0, 0.0, 2.0, 4.0])

    prepared = prepare_wavlm_input(samples, do_normalize=True)

    assert prepared.mean().item() == pytest.approx(0.0, abs=1e-7)
    assert prepared.var(correction=0).item() == pytest.approx(
        1.0,
        rel=1e-6,
    )
