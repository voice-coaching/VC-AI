from __future__ import annotations

import numpy as np
import pytest

from voice_coach.features.egemaps import (
    EGeMAPSExtractionError,
    EXPECTED_OPENSMILE_VERSION,
    extract_egemaps_functionals,
)


class _FakeFrame:
    def __init__(
        self,
        values: np.ndarray,
        names: tuple[str, ...] | None = None,
    ) -> None:
        self._values = values
        self.columns = names or tuple(f"feature_{index:02d}" for index in range(88))

    def to_numpy(self, *, dtype: object) -> np.ndarray:
        return self._values.astype(dtype)


class _FakeSmile:
    def __init__(self, frame: _FakeFrame) -> None:
        self._frame = frame
        self.calls = 0

    def process_signal(
        self,
        signal: np.ndarray,
        sampling_rate: int,
    ) -> _FakeFrame:
        assert signal.dtype == np.float32
        assert signal.ndim == 1
        assert sampling_rate == 16_000
        self.calls += 1
        return self._frame


def _audio() -> np.ndarray:
    time = np.arange(1_600, dtype=np.float64) / 16_000
    return 0.1 * np.sin(2 * np.pi * 180 * time)


def test_extracts_exact_ordered_finite_vector() -> None:
    values = np.arange(88, dtype=np.float64)[None, :]
    backend = _FakeSmile(_FakeFrame(values))

    result = extract_egemaps_functionals(
        _audio(),
        16_000,
        backend=backend,
        backend_version=EXPECTED_OPENSMILE_VERSION,
    )

    assert backend.calls == 1
    assert result.feature_set == "eGeMAPSv02"
    assert result.feature_level == "Functionals"
    assert len(result.names) == 88
    assert result.values == tuple(float(value) for value in range(88))
    assert len(result.column_order_sha256) == 64
    assert result.as_mapping()["feature_00"] == 0.0
    assert result.as_mapping()["feature_87"] == 87.0


@pytest.mark.parametrize(
    ("samples", "sample_rate_hz", "message"),
    [
        (np.zeros((2, 10)), 16_000, "mono"),
        (np.array([]), 16_000, "empty"),
        (np.array([np.nan]), 16_000, "NaN"),
        (np.zeros(10), 48_000, "exactly 16000"),
    ],
)
def test_audio_contract_fails_closed(
    samples: np.ndarray,
    sample_rate_hz: int,
    message: str,
) -> None:
    backend = _FakeSmile(_FakeFrame(np.zeros((1, 88))))

    with pytest.raises(EGeMAPSExtractionError, match=message):
        extract_egemaps_functionals(
            samples,
            sample_rate_hz,
            backend=backend,
            backend_version=EXPECTED_OPENSMILE_VERSION,
        )

    assert backend.calls == 0


def test_rejects_wrong_backend_version_before_extraction() -> None:
    backend = _FakeSmile(_FakeFrame(np.zeros((1, 88))))

    with pytest.raises(EGeMAPSExtractionError, match="version differs"):
        extract_egemaps_functionals(
            _audio(),
            16_000,
            backend=backend,
            backend_version="9.9.9",
        )

    assert backend.calls == 0


@pytest.mark.parametrize(
    "frame",
    [
        _FakeFrame(np.zeros((2, 88))),
        _FakeFrame(np.zeros((1, 87))),
        _FakeFrame(np.full((1, 88), np.inf)),
        _FakeFrame(
            np.zeros((1, 88)),
            names=tuple("duplicate" for _ in range(88)),
        ),
    ],
)
def test_rejects_invalid_backend_output(frame: _FakeFrame) -> None:
    with pytest.raises(EGeMAPSExtractionError):
        extract_egemaps_functionals(
            _audio(),
            16_000,
            backend=_FakeSmile(frame),
            backend_version=EXPECTED_OPENSMILE_VERSION,
        )


def test_backend_and_version_must_be_injected_together() -> None:
    with pytest.raises(EGeMAPSExtractionError, match="provided together"):
        extract_egemaps_functionals(
            _audio(),
            16_000,
            backend=_FakeSmile(_FakeFrame(np.zeros((1, 88)))),
        )
