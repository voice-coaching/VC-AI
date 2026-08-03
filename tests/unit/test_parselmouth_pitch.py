from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import numpy as np
from numpy.typing import NDArray
import pytest

from voice_coach.features import parselmouth_pitch
from voice_coach.features.parselmouth_pitch import (
    EXPECTED_PARSELMOUTH_VERSION,
    ParselmouthPitchContour,
    ParselmouthPitchExtractionError,
    extract_parselmouth_pitch_contour,
)


class _FakeSelectedArray:
    def __init__(self, frequencies: object) -> None:
        self._frequencies = frequencies

    def __getitem__(self, key: str) -> object:
        if key != "frequency":
            raise KeyError(key)
        return self._frequencies


class _FakePitch:
    def __init__(self, times: object, frequencies: object) -> None:
        self._times = times
        self._selected_array = _FakeSelectedArray(frequencies)

    @property
    def selected_array(self) -> _FakeSelectedArray:
        return self._selected_array

    def xs(self) -> object:
        return self._times


class _FakeSound:
    def __init__(
        self,
        pitch: _FakePitch,
        calls: list[dict[str, object]],
        error: Exception | None = None,
    ) -> None:
        self._pitch = pitch
        self._calls = calls
        self._error = error

    def to_pitch(
        self,
        *,
        time_step: float,
        pitch_floor: float,
        pitch_ceiling: float,
    ) -> _FakePitch:
        self._calls.append(
            {
                "time_step": time_step,
                "pitch_floor": pitch_floor,
                "pitch_ceiling": pitch_ceiling,
            }
        )
        if self._error is not None:
            raise self._error
        return self._pitch


class _FakeParselmouth:
    def __init__(
        self,
        *,
        times: object = np.array([0.01, 0.02, 0.03, 0.04]),
        frequencies: object = np.array([180.0, 0.0, 182.0, 178.0]),
        error: Exception | None = None,
    ) -> None:
        self._pitch = _FakePitch(times, frequencies)
        self._error = error
        self.sound_calls: list[dict[str, object]] = []
        self.pitch_calls: list[dict[str, object]] = []

    def Sound(
        self,
        values: NDArray[np.float64],
        *,
        sampling_frequency: float,
    ) -> _FakeSound:
        self.sound_calls.append(
            {
                "dtype": values.dtype,
                "ndim": values.ndim,
                "sampling_frequency": sampling_frequency,
            }
        )
        return _FakeSound(self._pitch, self.pitch_calls, self._error)


def _audio() -> np.ndarray:
    time = np.arange(1_600, dtype=np.float64) / 16_000
    return 0.1 * np.sin(2 * np.pi * 180 * time)


def _extract(
    backend: _FakeParselmouth,
    *,
    unvoiced_policy: str = "keep_zero",
) -> ParselmouthPitchContour:
    return extract_parselmouth_pitch_contour(
        _audio(),
        16_000,
        time_step_seconds=0.01,
        pitch_floor_hz=75.0,
        pitch_ceiling_hz=300.0,
        unvoiced_policy=unvoiced_policy,  # type: ignore[arg-type]
        backend=backend,
        backend_version=EXPECTED_PARSELMOUTH_VERSION,
    )


def test_extracts_fake_180_hz_contour_and_voiced_summary() -> None:
    backend = _FakeParselmouth()

    result = _extract(backend)

    assert result.parselmouth_version == EXPECTED_PARSELMOUTH_VERSION
    assert result.sample_rate_hz == 16_000
    assert result.time_seconds == (0.01, 0.02, 0.03, 0.04)
    assert result.f0_hz == (180.0, 0.0, 182.0, 178.0)
    assert result.backend_frame_count == 4
    assert result.returned_frame_count == 4
    assert result.voiced_frame_count == 3
    assert result.unvoiced_frame_count == 1
    assert result.voiced_median_hz == 180.0
    assert result.voiced_range_hz == 4.0
    assert len(result.contour_sha256) == 64
    assert backend.sound_calls == [
        {
            "dtype": np.dtype(np.float64),
            "ndim": 1,
            "sampling_frequency": 16_000.0,
        }
    ]
    assert backend.pitch_calls == [
        {
            "time_step": 0.01,
            "pitch_floor": 75.0,
            "pitch_ceiling": 300.0,
        }
    ]


def test_drop_policy_removes_unvoiced_frames_and_changes_digest() -> None:
    keep_result = _extract(_FakeParselmouth(), unvoiced_policy="keep_zero")
    drop_result = _extract(_FakeParselmouth(), unvoiced_policy="drop")

    assert drop_result.time_seconds == (0.01, 0.03, 0.04)
    assert drop_result.f0_hz == (180.0, 182.0, 178.0)
    assert drop_result.backend_frame_count == 4
    assert drop_result.returned_frame_count == 3
    assert drop_result.voiced_frame_count == 3
    assert drop_result.unvoiced_frame_count == 1
    assert drop_result.contour_sha256 != keep_result.contour_sha256


def test_digest_is_deterministic_and_result_does_not_retain_pcm() -> None:
    audio = _audio()
    first = extract_parselmouth_pitch_contour(
        audio,
        16_000,
        time_step_seconds=0.01,
        pitch_floor_hz=75.0,
        pitch_ceiling_hz=300.0,
        unvoiced_policy="keep_zero",
        backend=_FakeParselmouth(),
        backend_version=EXPECTED_PARSELMOUTH_VERSION,
    )
    audio[:] = 0.0
    second = _extract(_FakeParselmouth())

    assert first.contour_sha256 == second.contour_sha256
    assert {"samples", "audio", "path", "speaker_id", "text"}.isdisjoint(
        first.__dataclass_fields__
    )
    assert isinstance(first.time_seconds, tuple)
    assert isinstance(first.f0_hz, tuple)


@pytest.mark.parametrize(
    ("samples", "sample_rate_hz", "message"),
    [
        (np.zeros((1, 20)), 16_000, "mono"),
        ([[1.0], [1.0, 2.0]], 16_000, "real numeric PCM array"),
        (np.array([]), 16_000, "empty"),
        (np.array([np.nan]), 16_000, "NaN"),
        (np.array([np.inf]), 16_000, "NaN or Inf"),
        (np.array([True, False]), 16_000, "real numeric PCM"),
        (np.array(["1.0", "2.0"]), 16_000, "real numeric PCM"),
        (np.zeros(20), 48_000, "integer 16000"),
        (np.zeros(20), True, "integer 16000"),
    ],
)
def test_audio_contract_fails_before_backend_call(
    samples: object,
    sample_rate_hz: int,
    message: str,
) -> None:
    backend = _FakeParselmouth()

    with pytest.raises(ParselmouthPitchExtractionError, match=message):
        extract_parselmouth_pitch_contour(
            samples,
            sample_rate_hz,
            time_step_seconds=0.01,
            pitch_floor_hz=75.0,
            pitch_ceiling_hz=300.0,
            unvoiced_policy="keep_zero",
            backend=backend,
            backend_version=EXPECTED_PARSELMOUTH_VERSION,
        )

    assert backend.sound_calls == []


@pytest.mark.parametrize(
    (
        "time_step_seconds",
        "pitch_floor_hz",
        "pitch_ceiling_hz",
        "unvoiced_policy",
        "message",
    ),
    [
        (0.0, 75.0, 300.0, "keep_zero", "time_step_seconds"),
        (np.inf, 75.0, 300.0, "keep_zero", "time_step_seconds"),
        (0.01, np.nan, 300.0, "keep_zero", "pitch_floor_hz"),
        (0.01, 75.0, 75.0, "keep_zero", "greater"),
        (0.01, 75.0, 300.0, "interpolate", "unvoiced_policy"),
        (0.01, 75.0, 300.0, ["drop"], "unvoiced_policy"),
    ],
)
def test_scientific_parameters_fail_closed_before_backend_call(
    time_step_seconds: float,
    pitch_floor_hz: float,
    pitch_ceiling_hz: float,
    unvoiced_policy: object,
    message: str,
) -> None:
    backend = _FakeParselmouth()

    with pytest.raises(ParselmouthPitchExtractionError, match=message):
        extract_parselmouth_pitch_contour(
            _audio(),
            16_000,
            time_step_seconds=time_step_seconds,
            pitch_floor_hz=pitch_floor_hz,
            pitch_ceiling_hz=pitch_ceiling_hz,
            unvoiced_policy=unvoiced_policy,  # type: ignore[arg-type]
            backend=backend,
            backend_version=EXPECTED_PARSELMOUTH_VERSION,
        )

    assert backend.sound_calls == []


def test_scientific_parameters_have_no_defaults() -> None:
    with pytest.raises(TypeError):
        extract_parselmouth_pitch_contour(_audio(), 16_000)  # type: ignore[call-arg]


def test_backend_and_version_must_be_injected_together() -> None:
    with pytest.raises(ParselmouthPitchExtractionError, match="provided together"):
        extract_parselmouth_pitch_contour(
            _audio(),
            16_000,
            time_step_seconds=0.01,
            pitch_floor_hz=75.0,
            pitch_ceiling_hz=300.0,
            unvoiced_policy="keep_zero",
            backend=_FakeParselmouth(),
        )


def test_rejects_wrong_backend_version_before_extraction() -> None:
    backend = _FakeParselmouth()

    with pytest.raises(ParselmouthPitchExtractionError, match="version differs"):
        extract_parselmouth_pitch_contour(
            _audio(),
            16_000,
            time_step_seconds=0.01,
            pitch_floor_hz=75.0,
            pitch_ceiling_hz=300.0,
            unvoiced_policy="keep_zero",
            backend=backend,
            backend_version="9.9.9",
        )

    assert backend.sound_calls == []


@pytest.mark.parametrize(
    ("distribution_version", "module_version"),
    [
        ("9.9.9", EXPECTED_PARSELMOUTH_VERSION),
        (EXPECTED_PARSELMOUTH_VERSION, "9.9.9"),
    ],
)
def test_lazy_backend_rejects_distribution_or_module_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    distribution_version: str,
    module_version: str,
) -> None:
    monkeypatch.setattr(
        parselmouth_pitch.metadata,
        "version",
        lambda distribution_name: distribution_version,
    )
    monkeypatch.setattr(
        parselmouth_pitch.importlib,
        "import_module",
        lambda module_name: SimpleNamespace(__version__=module_version),
    )

    with pytest.raises(ParselmouthPitchExtractionError, match="version differs"):
        extract_parselmouth_pitch_contour(
            _audio(),
            16_000,
            time_step_seconds=0.01,
            pitch_floor_hz=75.0,
            pitch_ceiling_hz=300.0,
            unvoiced_policy="keep_zero",
        )


@pytest.mark.parametrize(
    ("times", "frequencies", "message"),
    [
        (
            np.array([[0.01, 0.02]]),
            np.array([180.0, 181.0]),
            "one-dimensional",
        ),
        (np.array([0.01]), np.array([180.0, 181.0]), "shapes differ"),
        (np.array([]), np.array([]), "empty pitch contour"),
        (np.array([0.01, np.inf]), np.array([180.0, 181.0]), "NaN or Inf"),
        (np.array([0.01, 0.02]), np.array([180.0, np.nan]), "NaN or Inf"),
        (
            np.array([0.02, 0.01]),
            np.array([180.0, 181.0]),
            "strictly increasing",
        ),
        (np.array([0.01, 0.02]), np.array([180.0, -1.0]), "use zero"),
        (np.array([0.01, 0.02]), np.array([74.0, 180.0]), "outside"),
        (np.array([0.01, 0.02]), np.array([180.0, 301.0]), "outside"),
    ],
)
def test_rejects_invalid_backend_contours(
    times: np.ndarray,
    frequencies: np.ndarray,
    message: str,
) -> None:
    with pytest.raises(ParselmouthPitchExtractionError, match=message):
        _extract(_FakeParselmouth(times=times, frequencies=frequencies))


def test_rejects_contour_without_voiced_frames() -> None:
    with pytest.raises(ParselmouthPitchExtractionError, match="no voiced frames"):
        _extract(
            _FakeParselmouth(
                times=np.array([0.01, 0.02]),
                frequencies=np.zeros(2),
            )
        )


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: RuntimeError("backend details"),
        lambda: ValueError("backend details"),
    ],
)
def test_maps_backend_failures_without_exposing_details(
    error_factory: Callable[[], Exception],
) -> None:
    with pytest.raises(
        ParselmouthPitchExtractionError,
        match="pitch extraction failed",
    ) as error:
        _extract(_FakeParselmouth(error=error_factory()))

    assert "backend details" not in str(error.value)
