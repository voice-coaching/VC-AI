from __future__ import annotations

import re

import numpy as np
import pytest

from voice_coach.evaluation.e01_waveform_transforms import (
    E01WaveformTransformError,
    add_leading_and_trailing_silence,
    change_global_gain_db,
)


def _silence_transform(
    waveform: np.ndarray,
    **overrides: object,
):
    arguments: dict[str, object] = {
        "sample_rate_hz": 4,
        "leading_silence_seconds": 0.5,
        "trailing_silence_seconds": 0.25,
        "sample_count_rounding": "half_up",
        "peak_limit": 1.0,
        "max_output_samples": 32,
    }
    arguments.update(overrides)
    return add_leading_and_trailing_silence(waveform, **arguments)  # type: ignore[arg-type]


def _gain_transform(
    waveform: np.ndarray,
    **overrides: object,
):
    arguments: dict[str, object] = {
        "sample_rate_hz": 16_000,
        "gain_db": 6.020599913279624,
        "clipping_policy": "error",
        "peak_limit": 1.0,
        "max_output_samples": 32,
    }
    arguments.update(overrides)
    return change_global_gain_db(waveform, **arguments)  # type: ignore[arg-type]


def test_silence_has_exact_zeros_and_does_not_mutate_the_original() -> None:
    original = np.array([0.25, -0.5, 0.125], dtype=np.float32)
    before = original.copy()

    result = _silence_transform(original)

    np.testing.assert_array_equal(original, before)
    assert not np.shares_memory(original, result.waveform)
    np.testing.assert_array_equal(result.waveform[:2], np.zeros(2, dtype=np.float32))
    np.testing.assert_array_equal(result.waveform[2:5], original)
    np.testing.assert_array_equal(result.waveform[5:], np.zeros(1, dtype=np.float32))
    assert result.waveform.dtype == original.dtype
    assert result.metadata.leading_silence_samples == 2
    assert result.metadata.trailing_silence_samples == 1
    assert result.metadata.input_sample_count == 3
    assert result.metadata.output_sample_count == 6


@pytest.mark.parametrize(
    ("rounding_policy", "expected_count"),
    [
        ("floor", 0),
        ("ceil", 1),
        ("half_up", 1),
    ],
)
def test_silence_sample_count_uses_the_explicit_rounding_policy(
    rounding_policy: str,
    expected_count: int,
) -> None:
    result = _silence_transform(
        np.array([0.25], dtype=np.float64),
        sample_rate_hz=4,
        leading_silence_seconds=0.125,
        trailing_silence_seconds=0.0,
        sample_count_rounding=rounding_policy,
    )

    assert result.metadata.leading_silence_samples == expected_count
    assert result.metadata.output_sample_count == 1 + expected_count


def test_silence_is_deterministic_even_when_no_samples_are_added() -> None:
    waveform = np.array([0.125, -0.25], dtype=np.float64)

    first = _silence_transform(
        waveform,
        leading_silence_seconds=0.0,
        trailing_silence_seconds=0.0,
    )
    second = _silence_transform(
        waveform,
        leading_silence_seconds=0.0,
        trailing_silence_seconds=0.0,
    )

    np.testing.assert_array_equal(first.waveform, second.waveform)
    assert first.metadata == second.metadata
    assert not np.shares_memory(waveform, first.waveform)


def test_gain_uses_the_known_db_ratio_and_does_not_mutate_input() -> None:
    original = np.array([0.1, -0.2, 0.3], dtype=np.float64)
    before = original.copy()

    result = _gain_transform(original)

    np.testing.assert_array_equal(original, before)
    assert not np.shares_memory(original, result.waveform)
    np.testing.assert_allclose(result.waveform, original * 2.0, rtol=1e-14)
    assert result.metadata.linear_gain == pytest.approx(2.0)
    assert result.metadata.input_sample_count == result.metadata.output_sample_count


def test_gain_clipping_fails_unless_clip_is_explicit() -> None:
    waveform = np.array([0.75, -0.6], dtype=np.float32)

    with pytest.raises(E01WaveformTransformError, match="clipping_policy=error"):
        _gain_transform(waveform)

    result = _gain_transform(waveform, clipping_policy="clip")

    np.testing.assert_array_equal(
        result.waveform,
        np.array([1.0, -1.0], dtype=np.float32),
    )
    assert result.metadata.output_peak == 1.0
    assert result.metadata.clipping_policy == "clip"


@pytest.mark.parametrize(
    "waveform",
    [
        np.array([], dtype=np.float32),
        np.zeros((1, 2), dtype=np.float32),
        np.array([np.nan], dtype=np.float32),
        np.array([np.inf], dtype=np.float64),
        np.array([1], dtype=np.int16),
    ],
)
def test_invalid_waveforms_fail_closed(waveform: np.ndarray) -> None:
    with pytest.raises(E01WaveformTransformError):
        _silence_transform(waveform)
    with pytest.raises(E01WaveformTransformError):
        _gain_transform(waveform)


def test_non_array_and_invalid_sample_rates_fail_closed() -> None:
    waveform = np.array([0.1], dtype=np.float32)

    with pytest.raises(E01WaveformTransformError, match="NumPy array"):
        _silence_transform([0.1])  # type: ignore[arg-type]
    for invalid_rate in (0, -1, 1.5, True):
        with pytest.raises(E01WaveformTransformError, match="sample_rate_hz"):
            _silence_transform(waveform, sample_rate_hz=invalid_rate)
        with pytest.raises(E01WaveformTransformError, match="sample_rate_hz"):
            _gain_transform(waveform, sample_rate_hz=invalid_rate)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("leading_silence_seconds", -0.1),
        ("leading_silence_seconds", float("nan")),
        ("trailing_silence_seconds", float("inf")),
        ("sample_count_rounding", "nearest_even"),
        ("peak_limit", 0.0),
        ("peak_limit", float("nan")),
        ("max_output_samples", 0),
        ("max_output_samples", True),
    ],
)
def test_silence_invalid_parameters_fail_closed(
    field: str,
    invalid_value: object,
) -> None:
    with pytest.raises(E01WaveformTransformError):
        _silence_transform(
            np.array([0.1], dtype=np.float32),
            **{field: invalid_value},
        )


def test_unhashable_policy_values_fail_with_the_contract_error() -> None:
    waveform = np.array([0.1], dtype=np.float32)

    with pytest.raises(E01WaveformTransformError, match="sample_count_rounding"):
        _silence_transform(waveform, sample_count_rounding=[])
    with pytest.raises(E01WaveformTransformError, match="clipping_policy"):
        _gain_transform(waveform, clipping_policy=[])


def test_silence_output_budget_prevents_sample_count_overflow() -> None:
    waveform = np.array([0.1, -0.1], dtype=np.float32)

    with pytest.raises(E01WaveformTransformError, match="max_output_samples"):
        _silence_transform(
            waveform,
            leading_silence_seconds=10.0,
            trailing_silence_seconds=10.0,
            max_output_samples=3,
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("gain_db", float("nan")),
        ("gain_db", float("inf")),
        ("clipping_policy", "normalize"),
        ("peak_limit", -1.0),
        ("max_output_samples", 2),
    ],
)
def test_gain_invalid_parameters_fail_closed(
    field: str,
    invalid_value: object,
) -> None:
    with pytest.raises(E01WaveformTransformError):
        _gain_transform(
            np.array([0.1, -0.1, 0.2], dtype=np.float64),
            **{field: invalid_value},
        )


@pytest.mark.parametrize("gain_db", [10_000.0, -10_000.0])
def test_gain_factor_overflow_or_underflow_fails_closed(gain_db: float) -> None:
    with pytest.raises(E01WaveformTransformError, match="gain"):
        _gain_transform(
            np.array([0.1], dtype=np.float64),
            gain_db=gain_db,
        )


def test_input_above_peak_limit_is_never_implicitly_clipped() -> None:
    waveform = np.array([1.1], dtype=np.float64)

    with pytest.raises(E01WaveformTransformError, match="input waveform peak"):
        _gain_transform(
            waveform,
            clipping_policy="clip",
            peak_limit=1.0,
        )


def test_explicit_clip_uses_a_float32_value_not_above_decimal_peak_limit() -> None:
    waveform = np.array([0.01], dtype=np.float32)

    result = _gain_transform(
        waveform,
        gain_db=40.0,
        clipping_policy="clip",
        peak_limit=0.1,
    )

    assert float(result.waveform[0]) <= 0.1
    assert result.metadata.output_peak <= 0.1


def test_memmap_is_rejected_to_preserve_the_in_memory_boundary(tmp_path) -> None:
    mapped_path = tmp_path / "mapped-pcm.bin"
    mapped = np.memmap(mapped_path, dtype=np.float32, mode="w+", shape=(2,))
    mapped[:] = [0.1, -0.1]

    with pytest.raises(E01WaveformTransformError, match="in-memory"):
        _silence_transform(mapped)


def test_metadata_is_aggregate_safe_and_contains_no_waveform_or_identifier() -> None:
    sentinel = 0.123456789
    result = _gain_transform(
        np.array([sentinel, -0.25], dtype=np.float64),
        gain_db=0.0,
    )
    metadata = result.metadata.to_dict()

    assert set(metadata) == {
        "transform",
        "sample_rate_hz",
        "gain_db",
        "linear_gain",
        "clipping_policy",
        "peak_limit",
        "max_output_samples",
        "input_sample_count",
        "output_sample_count",
        "input_peak",
        "output_peak",
        "output_digest_sha256",
    }
    assert all(not isinstance(value, np.ndarray) for value in metadata.values())
    assert not any(
        forbidden in key
        for key in metadata
        for forbidden in ("path", "speaker", "participant", "item_id", "audio_id")
    )
    assert re.fullmatch(r"[0-9a-f]{64}", result.metadata.output_digest_sha256)


def test_silence_metadata_contains_only_parameters_counts_peaks_and_digest() -> None:
    result = _silence_transform(np.array([0.1, -0.2], dtype=np.float32))
    metadata = result.metadata.to_dict()

    assert set(metadata) == {
        "transform",
        "sample_rate_hz",
        "leading_silence_seconds",
        "trailing_silence_seconds",
        "sample_count_rounding",
        "leading_silence_samples",
        "trailing_silence_samples",
        "peak_limit",
        "max_output_samples",
        "input_sample_count",
        "output_sample_count",
        "input_peak",
        "output_peak",
        "output_digest_sha256",
    }
    assert all(not isinstance(value, np.ndarray) for value in metadata.values())
    assert re.fullmatch(r"[0-9a-f]{64}", result.metadata.output_digest_sha256)


def test_digest_changes_with_output_and_is_repeatable() -> None:
    waveform = np.array([0.125, -0.25], dtype=np.float32)

    unchanged_first = _gain_transform(waveform, gain_db=0.0)
    unchanged_second = _gain_transform(waveform, gain_db=0.0)
    changed = _gain_transform(waveform, gain_db=-6.0)

    assert (
        unchanged_first.metadata.output_digest_sha256
        == unchanged_second.metadata.output_digest_sha256
    )
    assert (
        unchanged_first.metadata.output_digest_sha256
        != changed.metadata.output_digest_sha256
    )
