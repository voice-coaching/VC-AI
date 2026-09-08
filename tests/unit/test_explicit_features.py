import numpy as np
import pytest

from voice_coach.features.explicit import extract_explicit_features, npvi


def sine(frequency: float, seconds: float, sample_rate: int = 16_000) -> np.ndarray:
    time = np.arange(round(seconds * sample_rate)) / sample_rate
    return 0.2 * np.sin(2 * np.pi * frequency * time)


def test_f0_is_stable_under_global_gain() -> None:
    audio = sine(200.0, 0.5)
    original = extract_explicit_features(audio, 16_000)
    quieter = extract_explicit_features(audio * 0.25, 16_000)

    assert original.f0_median_hz == pytest.approx(200.0, rel=0.02)
    assert quieter.f0_median_hz == pytest.approx(original.f0_median_hz, rel=0.01)
    assert quieter.rms == pytest.approx(original.rms * 0.25)


def test_pause_is_detected_between_voiced_regions() -> None:
    audio = np.concatenate([sine(180.0, 0.2), np.zeros(3_200), sine(180.0, 0.2)])
    features = extract_explicit_features(audio, 16_000)

    assert features.pause_count == 1
    assert features.pause_ratio > 0.2


def test_npvi() -> None:
    assert npvi(np.array([0.1, 0.2])) == pytest.approx(66.6666667)
    assert npvi(np.array([0.1])) is None


def test_f0_can_be_skipped_for_cheap_feature_profile() -> None:
    features = extract_explicit_features(
        sine(200.0, 0.5),
        16_000,
        compute_f0=False,
    )

    assert features.f0_median_hz is None
    assert features.f0_range_hz is None
    assert features.rms > 0
