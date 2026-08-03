from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from voice_coach.data import (
    Interval,
    decode_audio,
    make_label_aware_chunks,
    pad_audio_batch,
    resample_audio,
)


def _sine(
    frequency_hz: float,
    seconds: float,
    sample_rate: int,
    amplitude: float,
) -> np.ndarray:
    time = np.arange(round(seconds * sample_rate), dtype=np.float64) / sample_rate
    return amplitude * np.sin(2 * np.pi * frequency_hz * time)


def test_decode_downmixes_and_resamples_synthetic_wav(tmp_path: Path) -> None:
    source_rate = 8_000
    left = _sine(220.0, 0.1, source_rate, 0.2)
    right = _sine(220.0, 0.1, source_rate, 0.4)
    path = tmp_path / "stereo.wav"
    sf.write(path, np.column_stack((left, right)), source_rate, subtype="PCM_16")

    audio = decode_audio(path)

    assert audio.sample_rate == 16_000
    assert audio.source_sample_rate == source_rate
    assert audio.source_channels == 2
    assert audio.samples.dtype == np.float32
    assert audio.samples.shape == (1_600,)
    assert np.isfinite(audio.samples).all()
    assert np.max(np.abs(audio.samples)) == pytest.approx(0.3, abs=0.01)
    assert audio.duration_seconds == pytest.approx(0.1)


def test_decode_rejects_non_finite_audio(tmp_path: Path) -> None:
    path = tmp_path / "nan.wav"
    sf.write(
        path,
        np.asarray([0.0, np.nan, 0.1], dtype=np.float32),
        16_000,
        subtype="FLOAT",
    )

    with pytest.raises(ValueError, match="NaN or Inf"):
        decode_audio(path)


def test_resample_rejects_non_mono_or_non_finite_input() -> None:
    with pytest.raises(ValueError, match="mono"):
        resample_audio(np.zeros((2, 2)), 16_000)
    with pytest.raises(ValueError, match="NaN or Inf"):
        resample_audio(np.asarray([0.0, np.inf]), 16_000)


def test_pad_audio_batch_builds_attention_mask() -> None:
    batch = pad_audio_batch(
        [
            np.asarray([0.1, 0.2, 0.3], dtype=np.float32),
            np.asarray([-0.1], dtype=np.float32),
        ],
        pad_to_multiple_of=4,
    )

    np.testing.assert_allclose(
        batch.input_values,
        np.asarray(
            [
                [0.1, 0.2, 0.3, 0.0],
                [-0.1, 0.0, 0.0, 0.0],
            ],
            dtype=np.float32,
        ),
    )
    np.testing.assert_array_equal(
        batch.attention_mask,
        np.asarray([[1, 1, 1, 0], [1, 0, 0, 0]], dtype=np.int64),
    )
    np.testing.assert_array_equal(batch.lengths, np.asarray([3, 1]))


def test_label_aware_chunks_move_boundary_before_crossing_interval() -> None:
    intervals = [
        Interval(0.0, 8.0, "a"),
        Interval(8.0, 19.0, "b"),
        Interval(19.0, 21.0, "c"),
        Interval(21.0, 39.0, "d"),
        Interval(39.0, 45.0, "e"),
    ]

    chunks = make_label_aware_chunks(45.0, intervals)

    assert [(chunk.start_seconds, chunk.end_seconds) for chunk in chunks] == [
        (0.0, 19.0),
        (19.0, 39.0),
        (39.0, 45.0),
    ]
    assert [chunk.boundary_kind for chunk in chunks] == [
        "label_boundary",
        "max_duration",
        "utterance_end",
    ]
    assert all(chunk.duration_seconds <= 20.0 for chunk in chunks)
    assert all(not chunk.cut_interval_indices for chunk in chunks)


def test_overlong_interval_is_forced_and_explicitly_marked() -> None:
    chunks = make_label_aware_chunks(
        45.0,
        [Interval(0.0, 45.0, "single-long-label")],
    )

    assert [(chunk.start_seconds, chunk.end_seconds) for chunk in chunks] == [
        (0.0, 20.0),
        (20.0, 40.0),
        (40.0, 45.0),
    ]
    assert [chunk.end_forced_through_label for chunk in chunks] == [
        True,
        True,
        False,
    ]
    assert all(chunk.cut_interval_indices == (0,) for chunk in chunks)
    assert all(chunk.overlong_interval_indices == (0,) for chunk in chunks)
    assert all(chunk.duration_seconds <= 20.0 for chunk in chunks)


def test_blank_label_is_safe_to_cut_and_out_of_bounds_label_is_rejected() -> None:
    chunks = make_label_aware_chunks(
        25.0,
        [Interval(0.0, 25.0, "")],
    )
    assert [(chunk.start_seconds, chunk.end_seconds) for chunk in chunks] == [
        (0.0, 20.0),
        (20.0, 25.0),
    ]
    assert all(not chunk.cut_interval_indices for chunk in chunks)

    with pytest.raises(ValueError, match="exceeds"):
        make_label_aware_chunks(
            10.0,
            [Interval(0.0, 10.1, "outside")],
        )
