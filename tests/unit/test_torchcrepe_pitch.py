from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from numpy.typing import NDArray
import pytest

from voice_coach.features import torchcrepe_pitch
from voice_coach.features.torchcrepe_pitch import (
    EXPECTED_TORCHCREPE_VERSION,
    TorchcrepePitchContour,
    TorchcrepePitchExtractionError,
    extract_torchcrepe_pitch_contour,
)


class _FakeBackend:
    def __init__(
        self,
        *,
        pitch: object | None = None,
        periodicity: object | None = None,
        silence_periodicity: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.pitch = pitch
        self.periodicity = periodicity
        self.silence_periodicity = silence_periodicity
        self.error = error
        self.calls: list[dict[str, object]] = []

    def predict(
        self,
        audio: NDArray[np.float32],
        *,
        sample_rate_hz: int,
        hop_length_samples: int,
        pitch_floor_hz: float,
        pitch_ceiling_hz: float,
        model: str,
        decoder: str,
        batch_size_frames: int,
        pad: bool,
        silence_threshold_dbfs: float,
        numpy_random_seed: int,
        torch_random_seed: int,
    ) -> tuple[object, object, object]:
        frame_count = (
            1 + audio.size // hop_length_samples
            if pad
            else 1 + (audio.size - 1_024) // hop_length_samples
        )
        self.calls.append(
            {
                "audio_dtype": audio.dtype,
                "audio_ndim": audio.ndim,
                "audio_size": audio.size,
                "sample_rate_hz": sample_rate_hz,
                "hop_length_samples": hop_length_samples,
                "pitch_floor_hz": pitch_floor_hz,
                "pitch_ceiling_hz": pitch_ceiling_hz,
                "model": model,
                "decoder": decoder,
                "batch_size_frames": batch_size_frames,
                "pad": pad,
                "silence_threshold_dbfs": silence_threshold_dbfs,
                "numpy_random_seed": numpy_random_seed,
                "torch_random_seed": torch_random_seed,
                "numpy_random_probe": float(np.random.random()),
            }
        )
        if self.error is not None:
            raise self.error
        pitch: object = (
            np.full((1, frame_count), 180.0, dtype=np.float32)
            if self.pitch is None
            else self.pitch
        )
        periodicity: object = (
            np.full((1, frame_count), 0.8, dtype=np.float32)
            if self.periodicity is None
            else self.periodicity
        )
        if self.silence_periodicity is None:
            silence_periodicity_array = np.full((1, frame_count), 0.8, dtype=np.float32)
            silence_periodicity_array[0, 0] = 0.0
            silence_periodicity: object = silence_periodicity_array
        else:
            silence_periodicity = self.silence_periodicity
        return pitch, periodicity, silence_periodicity


def _audio(sample_count: int = 1_600) -> np.ndarray:
    time = np.arange(sample_count, dtype=np.float32) / 16_000
    return np.asarray(
        0.1 * np.sin(2 * np.pi * 180 * time),
        dtype=np.float32,
    )


def _extract(
    backend: _FakeBackend,
    *,
    samples: object | None = None,
    pad: bool = True,
    model: str = "tiny",
    decoder: str = "viterbi",
    unvoiced_policy: str = "keep_zero",
) -> TorchcrepePitchContour:
    return extract_torchcrepe_pitch_contour(
        _audio() if samples is None else samples,
        16_000,
        hop_length_samples=160,
        pitch_floor_hz=75.0,
        pitch_ceiling_hz=300.0,
        model=model,  # type: ignore[arg-type]
        decoder=decoder,  # type: ignore[arg-type]
        batch_size_frames=32,
        pad=pad,
        periodicity_threshold=0.21,
        silence_threshold_dbfs=-60.0,
        numpy_random_seed=20_260_729,
        torch_random_seed=20_260_729,
        unvoiced_policy=unvoiced_policy,  # type: ignore[arg-type]
        backend=backend,
        backend_version=EXPECTED_TORCHCREPE_VERSION,
    )


def test_extracts_cpu_contract_and_preserves_numpy_rng_state() -> None:
    backend = _FakeBackend()
    np.random.seed(987)
    state_before = np.random.get_state()

    result = _extract(backend)

    state_after = np.random.get_state()
    assert all(
        np.array_equal(before, after)
        if isinstance(before, np.ndarray)
        else before == after
        for before, after in zip(state_before, state_after, strict=True)
    )
    assert result.torchcrepe_version == EXPECTED_TORCHCREPE_VERSION
    assert result.time_seconds == tuple(index * 0.01 for index in range(11))
    assert result.f0_hz == (0.0,) + (180.0,) * 10
    assert result.periodicity == pytest.approx((0.0,) + (0.8,) * 10)
    assert result.backend_frame_count == 11
    assert result.returned_frame_count == 11
    assert result.voiced_frame_count == 10
    assert result.unvoiced_frame_count == 1
    assert result.voiced_median_hz == 180.0
    assert result.voiced_range_hz == 0.0
    expected_probe = float(np.random.RandomState(20_260_729).random_sample())
    assert backend.calls == [
        {
            "audio_dtype": np.dtype(np.float32),
            "audio_ndim": 1,
            "audio_size": 1_600,
            "sample_rate_hz": 16_000,
            "hop_length_samples": 160,
            "pitch_floor_hz": 75.0,
            "pitch_ceiling_hz": 300.0,
            "model": "tiny",
            "decoder": "viterbi",
            "batch_size_frames": 32,
            "pad": True,
            "silence_threshold_dbfs": -60.0,
            "numpy_random_seed": 20_260_729,
            "torch_random_seed": 20_260_729,
            "numpy_random_probe": expected_probe,
        }
    ]


def test_drop_policy_returns_only_voiced_frames() -> None:
    result = _extract(_FakeBackend(), unvoiced_policy="drop")

    assert result.time_seconds == tuple(index * 0.01 for index in range(1, 11))
    assert result.f0_hz == (180.0,) * 10
    assert result.periodicity == pytest.approx((0.8,) * 10)
    assert result.returned_frame_count == 10


@pytest.mark.parametrize("model", ["tiny", "full"])
@pytest.mark.parametrize("decoder", ["viterbi", "weighted_argmax", "argmax"])
def test_accepts_only_supported_model_and_decoder_enums(
    model: str,
    decoder: str,
) -> None:
    backend = _FakeBackend()

    result = _extract(backend, model=model, decoder=decoder)

    assert result.model == model
    assert result.decoder == decoder
    assert backend.calls[0]["model"] == model
    assert backend.calls[0]["decoder"] == decoder


def test_unpadded_time_grid_uses_window_centers() -> None:
    result = _extract(_FakeBackend(), pad=False)

    assert result.backend_frame_count == 4
    assert result.time_seconds == pytest.approx((0.032, 0.042, 0.052, 0.062))


def test_digest_is_deterministic_and_result_retains_no_source_fields() -> None:
    first = _extract(_FakeBackend())
    second = _extract(_FakeBackend())

    assert first.contour_sha256 == second.contour_sha256
    assert len(first.contour_sha256) == 64
    assert {
        "samples",
        "audio",
        "path",
        "speaker_id",
        "utterance_id",
        "text",
    }.isdisjoint(first.__dataclass_fields__)


@pytest.mark.parametrize(
    ("samples", "sample_rate_hz", "message"),
    [
        (np.zeros((1, 20)), 16_000, "mono"),
        ([[1.0], [1.0, 2.0]], 16_000, "base float32 or float64 ndarray"),
        (np.array([], dtype=np.float32), 16_000, "empty"),
        (np.array([np.nan], dtype=np.float32), 16_000, "NaN"),
        (np.array([np.inf], dtype=np.float64), 16_000, "NaN or Inf"),
        (np.array([True, False]), 16_000, "dtype"),
        (np.array([1, 2], dtype=np.int16), 16_000, "dtype"),
        (np.array([1, 2], dtype=np.uint16), 16_000, "dtype"),
        (np.array([1 + 0j]), 16_000, "dtype"),
        (np.array(["1.0", "2.0"]), 16_000, "dtype"),
        (np.array([1.01], dtype=np.float32), 16_000, "closed interval"),
        (np.array([-1.01], dtype=np.float64), 16_000, "closed interval"),
        (np.zeros(20, dtype=np.float32), 48_000, "integer 16000"),
        (np.zeros(20, dtype=np.float32), True, "integer 16000"),
    ],
)
def test_audio_contract_fails_before_backend_call(
    samples: object,
    sample_rate_hz: int,
    message: str,
) -> None:
    backend = _FakeBackend()

    with pytest.raises(TorchcrepePitchExtractionError, match=message):
        extract_torchcrepe_pitch_contour(
            samples,
            sample_rate_hz,
            hop_length_samples=160,
            pitch_floor_hz=75.0,
            pitch_ceiling_hz=300.0,
            model="tiny",
            decoder="viterbi",
            batch_size_frames=32,
            pad=True,
            periodicity_threshold=0.21,
            silence_threshold_dbfs=-60.0,
            numpy_random_seed=1,
            torch_random_seed=1,
            unvoiced_policy="keep_zero",
            backend=backend,
            backend_version=EXPECTED_TORCHCREPE_VERSION,
        )

    assert backend.calls == []


def test_accepts_normalized_float64_and_converts_a_copy_to_float32() -> None:
    source = _audio().astype(np.float64)
    backend = _FakeBackend()

    result = _extract(backend, samples=source)
    source[:] = 0.0

    assert result.voiced_frame_count == 10
    assert backend.calls[0]["audio_dtype"] == np.dtype(np.float32)


def test_rejects_ndarray_subclass_before_backend_call() -> None:
    class _ArraySubclass(np.ndarray):
        pass

    backend = _FakeBackend()
    samples = _audio().view(_ArraySubclass)

    with pytest.raises(TorchcrepePitchExtractionError, match="base float32"):
        _extract(backend, samples=samples)

    assert backend.calls == []


def test_rejects_memory_mapped_storage(tmp_path: Path) -> None:
    path = tmp_path / "audio.bin"
    mapped = np.memmap(path, mode="w+", dtype=np.float32, shape=(1_600,))
    mapped[:] = _audio()
    samples = np.asarray(mapped)
    backend = _FakeBackend()

    with pytest.raises(TorchcrepePitchExtractionError, match="memory-mapped"):
        _extract(backend, samples=samples)

    assert backend.calls == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"hop_length_samples": 0}, "hop_length_samples"),
        ({"hop_length_samples": True}, "hop_length_samples"),
        ({"pitch_floor_hz": np.nan}, "pitch_floor_hz"),
        ({"pitch_ceiling_hz": 75.0}, "pitch_ceiling_hz"),
        ({"pitch_ceiling_hz": 2_007.0}, "at most 2006"),
        ({"model": "small"}, "model"),
        ({"model": ["tiny"]}, "model"),
        ({"decoder": "median"}, "decoder"),
        ({"batch_size_frames": 0}, "batch_size_frames"),
        ({"pad": 1}, "pad"),
        ({"periodicity_threshold": -0.1}, "between zero and one"),
        ({"periodicity_threshold": 1.1}, "between zero and one"),
        ({"silence_threshold_dbfs": np.inf}, "silence_threshold_dbfs"),
        ({"numpy_random_seed": -1}, "numpy_random_seed"),
        ({"numpy_random_seed": 2**32}, "numpy_random_seed"),
        ({"torch_random_seed": -1}, "torch_random_seed"),
        ({"unvoiced_policy": "keep_nan"}, "unvoiced_policy"),
        ({"unvoiced_policy": ["drop"]}, "unvoiced_policy"),
    ],
)
def test_scientific_parameters_fail_before_backend_call(
    overrides: dict[str, object],
    message: str,
) -> None:
    backend = _FakeBackend()
    parameters: dict[str, object] = {
        "hop_length_samples": 160,
        "pitch_floor_hz": 75.0,
        "pitch_ceiling_hz": 300.0,
        "model": "tiny",
        "decoder": "viterbi",
        "batch_size_frames": 32,
        "pad": True,
        "periodicity_threshold": 0.21,
        "silence_threshold_dbfs": -60.0,
        "numpy_random_seed": 1,
        "torch_random_seed": 1,
        "unvoiced_policy": "keep_zero",
    }
    parameters.update(overrides)

    with pytest.raises(TorchcrepePitchExtractionError, match=message):
        extract_torchcrepe_pitch_contour(
            _audio(),
            16_000,
            backend=backend,
            backend_version=EXPECTED_TORCHCREPE_VERSION,
            **parameters,  # type: ignore[arg-type]
        )

    assert backend.calls == []


def test_scientific_parameters_have_no_defaults() -> None:
    with pytest.raises(TypeError):
        extract_torchcrepe_pitch_contour(_audio(), 16_000)  # type: ignore[call-arg]


def test_rejects_short_unpadded_audio_before_backend_call() -> None:
    backend = _FakeBackend()

    with pytest.raises(TorchcrepePitchExtractionError, match="at least 1024"):
        _extract(backend, samples=_audio(1_000), pad=False)

    assert backend.calls == []


def test_backend_and_version_must_be_injected_together() -> None:
    with pytest.raises(TorchcrepePitchExtractionError, match="provided together"):
        extract_torchcrepe_pitch_contour(
            _audio(),
            16_000,
            hop_length_samples=160,
            pitch_floor_hz=75.0,
            pitch_ceiling_hz=300.0,
            model="tiny",
            decoder="viterbi",
            batch_size_frames=32,
            pad=True,
            periodicity_threshold=0.21,
            silence_threshold_dbfs=-60.0,
            numpy_random_seed=1,
            torch_random_seed=1,
            unvoiced_policy="keep_zero",
            backend=_FakeBackend(),
        )


def test_rejects_wrong_backend_version_before_extraction() -> None:
    backend = _FakeBackend()

    with pytest.raises(TorchcrepePitchExtractionError, match="version differs"):
        extract_torchcrepe_pitch_contour(
            _audio(),
            16_000,
            hop_length_samples=160,
            pitch_floor_hz=75.0,
            pitch_ceiling_hz=300.0,
            model="tiny",
            decoder="viterbi",
            batch_size_frames=32,
            pad=True,
            periodicity_threshold=0.21,
            silence_threshold_dbfs=-60.0,
            numpy_random_seed=1,
            torch_random_seed=1,
            unvoiced_policy="keep_zero",
            backend=backend,
            backend_version="9.9.9",
        )

    assert backend.calls == []


def test_lazy_backend_rejects_distribution_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(torchcrepe_pitch.metadata, "version", lambda name: "9.9.9")
    monkeypatch.setattr(
        torchcrepe_pitch.importlib,
        "import_module",
        lambda name: SimpleNamespace(),
    )

    with pytest.raises(TorchcrepePitchExtractionError, match="version differs"):
        extract_torchcrepe_pitch_contour(
            _audio(),
            16_000,
            hop_length_samples=160,
            pitch_floor_hz=75.0,
            pitch_ceiling_hz=300.0,
            model="tiny",
            decoder="viterbi",
            batch_size_frames=32,
            pad=True,
            periodicity_threshold=0.21,
            silence_threshold_dbfs=-60.0,
            numpy_random_seed=1,
            torch_random_seed=1,
            unvoiced_policy="keep_zero",
        )


def test_lazy_backend_maps_missing_distribution_without_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_distribution(name: str) -> str:
        raise torchcrepe_pitch.metadata.PackageNotFoundError(
            "secret environment detail"
        )

    monkeypatch.setattr(torchcrepe_pitch.metadata, "version", missing_distribution)

    with pytest.raises(
        TorchcrepePitchExtractionError,
        match="unavailable",
    ) as error:
        torchcrepe_pitch._load_pinned_backend()

    assert "secret" not in str(error.value)
    assert error.value.__cause__ is None


def test_lazy_backend_maps_import_failure_without_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        torchcrepe_pitch.metadata,
        "version",
        lambda name: EXPECTED_TORCHCREPE_VERSION,
    )

    def import_failure(name: str) -> object:
        raise OSError("secret /native/library/path")

    monkeypatch.setattr(torchcrepe_pitch.importlib, "import_module", import_failure)

    with pytest.raises(
        TorchcrepePitchExtractionError,
        match="unavailable",
    ) as error:
        torchcrepe_pitch._load_pinned_backend()

    assert "secret" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    ("backend", "message"),
    [
        (_FakeBackend(pitch=np.full(11, 180.0)), "shape"),
        (_FakeBackend(pitch=np.full((2, 11), 180.0)), "shape"),
        (_FakeBackend(pitch=np.full((1, 10), 180.0)), "expected time grid"),
        (
            _FakeBackend(pitch=np.array([[180.0] * 10 + [np.nan]])),
            "NaN or Inf",
        ),
        (
            _FakeBackend(periodicity=np.array([[0.8] * 10 + [np.inf]])),
            "NaN or Inf",
        ),
        (_FakeBackend(pitch=np.full((1, 11), 74.0)), "outside"),
        (_FakeBackend(periodicity=np.full((1, 11), 1.1)), "zero-to-one"),
        (
            _FakeBackend(silence_periodicity=np.full((1, 11), -0.1)),
            "zero-to-one",
        ),
    ],
)
def test_rejects_invalid_backend_outputs(
    backend: _FakeBackend,
    message: str,
) -> None:
    with pytest.raises(TorchcrepePitchExtractionError, match=message):
        _extract(backend)


def test_silence_or_threshold_without_voiced_frames_fails_closed() -> None:
    backend = _FakeBackend(silence_periodicity=np.zeros((1, 11)))

    with pytest.raises(TorchcrepePitchExtractionError, match="no voiced frames"):
        _extract(backend)


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: RuntimeError("secret /restricted/audio.wav speaker-7"),
        lambda: ValueError("secret transcript"),
    ],
)
def test_maps_backend_failures_and_restores_numpy_rng(
    error_factory: Callable[[], Exception],
) -> None:
    np.random.seed(987)
    expected_next = float(np.random.random())
    np.random.seed(987)

    with pytest.raises(
        TorchcrepePitchExtractionError,
        match="pitch extraction failed",
    ) as error:
        _extract(_FakeBackend(error=error_factory()))

    assert "secret" not in str(error.value)
    assert "audio.wav" not in str(error.value)
    assert error.value.__cause__ is None
    assert float(np.random.random()) == expected_next
