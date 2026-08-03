from __future__ import annotations

from importlib import metadata
from types import SimpleNamespace

import numpy as np
import pytest

from voice_coach.evaluation import e01_f0_transform
from voice_coach.evaluation.e01_f0_transform import (
    E01F0TransformError,
    EXPECTED_PARSELMOUTH_VERSION,
    F0_SCALING_ALGORITHM_ID,
    scale_global_f0,
)


class _FakeBackend:
    def __init__(self, **overrides: object) -> None:
        self.calls: list[dict[str, object]] = []
        self._overrides = overrides

    def scale_global_f0(
        self,
        samples: np.ndarray,
        *,
        sample_rate_hz: int,
        time_step_seconds: float,
        analysis_pitch_floor_hz: float,
        analysis_pitch_ceiling_hz: float,
        f0_scale_factor: float,
    ) -> e01_f0_transform._BackendF0ScalingOutput:
        self.calls.append(
            {
                "samples": samples.copy(),
                "sample_rate_hz": sample_rate_hz,
                "time_step_seconds": time_step_seconds,
                "analysis_pitch_floor_hz": analysis_pitch_floor_hz,
                "analysis_pitch_ceiling_hz": analysis_pitch_ceiling_hz,
                "f0_scale_factor": f0_scale_factor,
            }
        )
        source_times = np.array(
            [0.00001, 0.0001, 0.0002],
            dtype=np.float64,
        )
        source_values = np.array([100.0, 110.0, 120.0], dtype=np.float64)
        values: dict[str, object] = {
            "waveform": np.asarray(samples * 0.8, dtype=np.float64),
            "sample_rate_hz": float(sample_rate_hz),
            "source_pitch_times_seconds": source_times,
            "source_pitch_values_hz": source_values,
            "scaled_pitch_times_seconds": source_times.copy(),
            "scaled_pitch_values_hz": source_values * f0_scale_factor,
            "duration_tier_point_count_before": 0,
            "duration_tier_point_count_after": 0,
        }
        values.update(self._overrides)
        return e01_f0_transform._BackendF0ScalingOutput(**values)


class _FailingBackend:
    def scale_global_f0(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError("private-backend-detail")


class _InvalidContractBackend:
    def scale_global_f0(self, *_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(waveform=np.array([0.0], dtype=np.float64))


class _VersionString(str):
    pass


def _waveform(dtype: np.dtype[np.floating] = np.dtype(np.float64)) -> np.ndarray:
    return np.array([0.05, -0.1, 0.075, -0.025], dtype=dtype)


def _transform(
    waveform: np.ndarray,
    selected_backend: object | None = None,
    **overrides: object,
):
    arguments: dict[str, object] = {
        "sample_rate_hz": 16_000,
        "time_step_seconds": 0.01,
        "analysis_pitch_floor_hz": 75.0,
        "analysis_pitch_ceiling_hz": 300.0,
        "f0_scale_factor": 1.2,
        "allowed_scaled_pitch_floor_hz": 80.0,
        "allowed_scaled_pitch_ceiling_hz": 200.0,
        "minimum_pitch_tier_points": 3,
        "peak_limit": 1.0,
        "max_output_samples": 32,
        "backend": (
            selected_backend if selected_backend is not None else _FakeBackend()
        ),
        "backend_version": EXPECTED_PARSELMOUTH_VERSION,
    }
    arguments.update(overrides)
    return scale_global_f0(waveform, **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("dtype", [np.dtype(np.float32), np.dtype(np.float64)])
def test_f0_scale_preserves_input_dtype_sample_count_and_source(
    dtype: np.dtype[np.floating],
) -> None:
    original = _waveform(dtype)
    before = original.copy()
    backend = _FakeBackend()

    result = _transform(original, backend)

    np.testing.assert_array_equal(original, before)
    assert result.waveform.dtype == dtype
    assert result.waveform.shape == original.shape
    assert not np.shares_memory(original, result.waveform)
    np.testing.assert_allclose(result.waveform, original * 0.8)
    assert result.metadata.input_sample_count == original.size
    assert result.metadata.output_sample_count == original.size
    assert result.metadata.sample_rate_hz == 16_000
    backend_samples = backend.calls[0]["samples"]
    assert isinstance(backend_samples, np.ndarray)
    assert backend_samples.dtype == np.dtype(np.float64)


def test_metadata_records_the_exact_control_and_pitch_tier_audit() -> None:
    result = _transform(_waveform())
    metadata_result = result.metadata

    assert metadata_result.transform == "f0"
    assert metadata_result.operation == "scale_global_f0"
    assert metadata_result.algorithm_id == F0_SCALING_ALGORITHM_ID
    assert metadata_result.parselmouth_version == EXPECTED_PARSELMOUTH_VERSION
    assert metadata_result.f0_scale_factor == 1.2
    assert metadata_result.pitch_tier_point_count == 3
    assert metadata_result.source_pitch_tier_min_hz == 100.0
    assert metadata_result.source_pitch_tier_max_hz == 120.0
    assert metadata_result.scaled_pitch_tier_min_hz == 120.0
    assert metadata_result.scaled_pitch_tier_max_hz == 144.0
    assert metadata_result.maximum_pitch_tier_scale_error_hz == 0.0
    assert len(metadata_result.output_digest_sha256) == 64


def test_digest_and_output_are_deterministic() -> None:
    first = _transform(_waveform())
    second = _transform(_waveform())

    np.testing.assert_array_equal(first.waveform, second.waveform)
    assert first.metadata == second.metadata


def test_digest_binds_caller_parameters() -> None:
    first = _transform(_waveform())
    second = _transform(
        _waveform(),
        allowed_scaled_pitch_ceiling_hz=210.0,
    )

    np.testing.assert_array_equal(first.waveform, second.waveform)
    assert first.metadata.output_digest_sha256 != second.metadata.output_digest_sha256


@pytest.mark.parametrize(
    "waveform",
    [
        [],
        np.array([], dtype=np.float64),
        np.zeros((1, 2), dtype=np.float32),
        np.array([1], dtype=np.int16),
        np.array([True], dtype=np.bool_),
        np.array([np.nan], dtype=np.float64),
        np.array([np.inf], dtype=np.float32),
    ],
)
def test_invalid_waveform_fails_before_backend(waveform: object) -> None:
    backend = _FakeBackend()

    with pytest.raises(E01F0TransformError):
        _transform(waveform, backend)  # type: ignore[arg-type]

    assert backend.calls == []


def test_array_subclass_and_memmap_are_rejected(
    tmp_path,
) -> None:
    base = _waveform()
    subclass = base.view(np.recarray)
    mapped_path = tmp_path / "f0-pcm.bin"
    mapped = np.memmap(
        mapped_path,
        dtype=np.float32,
        mode="w+",
        shape=(4,),
    )
    mapped[:] = base

    for invalid in (subclass, mapped):
        with pytest.raises(E01F0TransformError, match="base NumPy"):
            _transform(invalid)


@pytest.mark.parametrize("sample_rate_hz", [0, 8_000, 16_000.0, True])
def test_sample_rate_must_be_exact_integer_16khz(
    sample_rate_hz: object,
) -> None:
    backend = _FakeBackend()

    with pytest.raises(E01F0TransformError, match="sample_rate_hz"):
        _transform(
            _waveform(),
            backend,
            sample_rate_hz=sample_rate_hz,
        )

    assert backend.calls == []


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("time_step_seconds", 0.0),
        ("time_step_seconds", np.nan),
        ("analysis_pitch_floor_hz", 0.0),
        ("analysis_pitch_ceiling_hz", np.inf),
        ("f0_scale_factor", 0.0),
        ("f0_scale_factor", 1.0),
        ("f0_scale_factor", True),
        ("allowed_scaled_pitch_floor_hz", -1.0),
        ("allowed_scaled_pitch_ceiling_hz", np.nan),
        ("minimum_pitch_tier_points", 0),
        ("minimum_pitch_tier_points", 1.5),
        ("minimum_pitch_tier_points", True),
        ("peak_limit", 0.0),
        ("peak_limit", 1.1),
        ("max_output_samples", 0),
        ("max_output_samples", True),
    ],
)
def test_invalid_parameters_fail_before_backend(
    field_name: str,
    invalid_value: object,
) -> None:
    backend = _FakeBackend()

    with pytest.raises(E01F0TransformError):
        _transform(
            _waveform(),
            backend,
            **{field_name: invalid_value},
        )

    assert backend.calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "analysis_pitch_floor_hz": 300.0,
            "analysis_pitch_ceiling_hz": 300.0,
        },
        {
            "allowed_scaled_pitch_floor_hz": 200.0,
            "allowed_scaled_pitch_ceiling_hz": 100.0,
        },
    ],
)
def test_reversed_pitch_bounds_fail_before_backend(
    overrides: dict[str, object],
) -> None:
    backend = _FakeBackend()

    with pytest.raises(E01F0TransformError, match="ceiling"):
        _transform(_waveform(), backend, **overrides)

    assert backend.calls == []


def test_input_peak_and_sample_budget_fail_before_backend() -> None:
    backend = _FakeBackend()

    with pytest.raises(E01F0TransformError, match="input waveform peak"):
        _transform(
            np.array([0.6], dtype=np.float64),
            backend,
            peak_limit=0.5,
        )
    with pytest.raises(E01F0TransformError, match="max_output_samples"):
        _transform(
            _waveform(),
            backend,
            max_output_samples=3,
        )

    assert backend.calls == []


def test_backend_and_version_must_be_injected_together() -> None:
    with pytest.raises(E01F0TransformError, match="provided together"):
        _transform(
            _waveform(),
            _FakeBackend(),
            backend_version=None,
        )
    with pytest.raises(E01F0TransformError, match="provided together"):
        _transform(
            _waveform(),
            None,
            backend=None,
            backend_version=EXPECTED_PARSELMOUTH_VERSION,
        )


def test_injected_backend_version_is_exactly_pinned() -> None:
    backend = _FakeBackend()

    for invalid_version in (
        "0.4.6",
        _VersionString(EXPECTED_PARSELMOUTH_VERSION),
    ):
        with pytest.raises(E01F0TransformError, match="version differs"):
            _transform(
                _waveform(),
                backend,
                backend_version=invalid_version,
            )

    assert backend.calls == []


def test_backend_exception_is_mapped_without_leaking_details() -> None:
    with pytest.raises(
        E01F0TransformError,
        match="Parselmouth PSOLA F0 scaling failed",
    ) as error:
        _transform(_waveform(), _FailingBackend())

    assert "private-backend-detail" not in str(error.value)


def test_invalid_backend_result_contract_fails_closed() -> None:
    with pytest.raises(E01F0TransformError, match="result contract"):
        _transform(_waveform(), _InvalidContractBackend())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sample_rate_hz": 8_000.0}, "sample rate"),
        (
            {"waveform": np.zeros(3, dtype=np.float64)},
            "discrete waveform duration",
        ),
        (
            {"waveform": np.array([0.0, np.nan, 0.0, 0.0])},
            "NaN or Inf",
        ),
        (
            {
                "source_pitch_times_seconds": np.array(
                    [[0.01, 0.02, 0.03]],
                    dtype=np.float64,
                )
            },
            "one-dimensional",
        ),
        (
            {
                "source_pitch_values_hz": np.array(
                    [100.0, 110.0],
                    dtype=np.float64,
                )
            },
            "shapes differ",
        ),
        (
            {
                "source_pitch_times_seconds": np.array(
                    [0.01, 0.01, 0.03],
                    dtype=np.float64,
                ),
                "scaled_pitch_times_seconds": np.array(
                    [0.01, 0.01, 0.03],
                    dtype=np.float64,
                ),
            },
            "increasing",
        ),
        (
            {
                "scaled_pitch_times_seconds": np.array(
                    [0.00001, 0.00011, 0.0002],
                    dtype=np.float64,
                )
            },
            "time grid",
        ),
        (
            {
                "source_pitch_times_seconds": np.array(
                    [0.00001, 0.0001, 0.0003],
                    dtype=np.float64,
                ),
                "scaled_pitch_times_seconds": np.array(
                    [0.00001, 0.0001, 0.0003],
                    dtype=np.float64,
                ),
            },
            "waveform duration",
        ),
        (
            {
                "source_pitch_values_hz": np.array(
                    [70.0, 110.0, 120.0],
                    dtype=np.float64,
                ),
                "scaled_pitch_values_hz": np.array(
                    [84.0, 132.0, 144.0],
                    dtype=np.float64,
                ),
            },
            "analysis bounds",
        ),
        (
            {
                "scaled_pitch_values_hz": np.array(
                    [120.0, 132.0, 145.0],
                    dtype=np.float64,
                )
            },
            "exact global",
        ),
        (
            {"duration_tier_point_count_before": 1},
            "DurationTier",
        ),
        (
            {"duration_tier_point_count_after": True},
            "DurationTier",
        ),
    ],
)
def test_malformed_backend_evidence_fails_closed(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(E01F0TransformError, match=message):
        _transform(
            _waveform(),
            _FakeBackend(**overrides),
        )


def test_too_few_pitch_points_and_scaled_bounds_fail_closed() -> None:
    with pytest.raises(E01F0TransformError, match="fewer"):
        _transform(
            _waveform(),
            minimum_pitch_tier_points=4,
        )
    with pytest.raises(E01F0TransformError, match="caller-provided bounds"):
        _transform(
            _waveform(),
            allowed_scaled_pitch_ceiling_hz=140.0,
        )


def test_resynthesis_peak_is_never_normalized_or_clipped() -> None:
    output = np.array([0.0, 0.51, -0.2, 0.1], dtype=np.float64)

    with pytest.raises(E01F0TransformError, match="normalization and clipping"):
        _transform(
            _waveform(),
            _FakeBackend(waveform=output),
            peak_limit=0.5,
        )


def test_metadata_allowlist_retains_no_pcm_or_identifier() -> None:
    metadata_result = _transform(_waveform()).metadata.to_dict()

    assert set(metadata_result) == {
        "transform",
        "operation",
        "algorithm_id",
        "parselmouth_version",
        "sample_rate_hz",
        "time_step_seconds",
        "analysis_pitch_floor_hz",
        "analysis_pitch_ceiling_hz",
        "f0_scale_factor",
        "allowed_scaled_pitch_floor_hz",
        "allowed_scaled_pitch_ceiling_hz",
        "minimum_pitch_tier_points",
        "peak_limit",
        "max_output_samples",
        "input_sample_count",
        "output_sample_count",
        "pitch_tier_point_count",
        "source_pitch_tier_min_hz",
        "source_pitch_tier_max_hz",
        "scaled_pitch_tier_min_hz",
        "scaled_pitch_tier_max_hz",
        "maximum_pitch_tier_scale_error_hz",
        "input_peak",
        "output_peak",
        "output_digest_sha256",
    }
    assert all(not isinstance(value, np.ndarray) for value in metadata_result.values())
    assert not any(
        forbidden in key
        for key in metadata_result
        for forbidden in (
            "waveform",
            "path",
            "speaker",
            "participant",
            "item_id",
            "audio_id",
            "transcript",
        )
    )


def test_missing_distribution_maps_to_fixed_unavailable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing_distribution(_name: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(
        e01_f0_transform.metadata,
        "version",
        _missing_distribution,
    )

    with pytest.raises(E01F0TransformError, match="unavailable"):
        scale_global_f0(
            _waveform(),
            sample_rate_hz=16_000,
            time_step_seconds=0.01,
            analysis_pitch_floor_hz=75.0,
            analysis_pitch_ceiling_hz=300.0,
            f0_scale_factor=1.2,
            allowed_scaled_pitch_floor_hz=80.0,
            allowed_scaled_pitch_ceiling_hz=200.0,
            minimum_pitch_tier_points=3,
            peak_limit=1.0,
            max_output_samples=32,
        )


@pytest.mark.parametrize(
    ("distribution_version", "module_version"),
    [
        ("0.4.6", EXPECTED_PARSELMOUTH_VERSION),
        (EXPECTED_PARSELMOUTH_VERSION, "0.4.6"),
    ],
)
def test_loaded_distribution_and_module_versions_must_both_match(
    monkeypatch: pytest.MonkeyPatch,
    distribution_version: str,
    module_version: str,
) -> None:
    monkeypatch.setattr(
        e01_f0_transform.metadata,
        "version",
        lambda _name: distribution_version,
    )
    monkeypatch.setattr(
        e01_f0_transform,
        "import_module",
        lambda _name: SimpleNamespace(__version__=module_version),
    )

    with pytest.raises(E01F0TransformError, match="version differs"):
        scale_global_f0(
            _waveform(),
            sample_rate_hz=16_000,
            time_step_seconds=0.01,
            analysis_pitch_floor_hz=75.0,
            analysis_pitch_ceiling_hz=300.0,
            f0_scale_factor=1.2,
            allowed_scaled_pitch_floor_hz=80.0,
            allowed_scaled_pitch_ceiling_hz=200.0,
            minimum_pitch_tier_points=3,
            peak_limit=1.0,
            max_output_samples=32,
        )
