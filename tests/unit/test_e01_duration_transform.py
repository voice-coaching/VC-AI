from __future__ import annotations

from importlib import metadata
import inspect
import re
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest

from voice_coach.evaluation import e01_duration_transform
from voice_coach.evaluation.e01_duration_transform import (
    DURATION_SCALING_ALGORITHM_ID,
    DURATION_TIER_POINT_COUNT,
    E01DurationTransformError,
    EXPECTED_PARSELMOUTH_VERSION,
    EXPECTED_PRAAT_VERSION,
    PRAAT_PROCESS_ISOLATION_POLICY,
    PRAAT_RNG_RESET_POLICY,
    scale_aligned_segment_duration,
)


class _FakeBackend:
    def __init__(
        self,
        *,
        output_sample_count: int | None = None,
        **overrides: object,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._output_sample_count = output_sample_count
        self._overrides = overrides

    def scale_aligned_segment_duration(
        self,
        samples: np.ndarray,
        *,
        sample_rate_hz: int,
        time_step_seconds: float,
        analysis_pitch_floor_hz: float,
        analysis_pitch_ceiling_hz: float,
        duration_tier_times_seconds: np.ndarray,
        duration_tier_scale_values: np.ndarray,
        praat_random_seed: int,
    ) -> e01_duration_transform._BackendDurationScalingOutput:
        self.calls.append(
            {
                "samples": samples.copy(),
                "sample_rate_hz": sample_rate_hz,
                "time_step_seconds": time_step_seconds,
                "analysis_pitch_floor_hz": analysis_pitch_floor_hz,
                "analysis_pitch_ceiling_hz": analysis_pitch_ceiling_hz,
                "duration_tier_times_seconds": (duration_tier_times_seconds.copy()),
                "duration_tier_scale_values": (duration_tier_scale_values.copy()),
                "praat_random_seed": praat_random_seed,
            }
        )
        output_count = (
            int(
                round(
                    float(
                        np.trapezoid(
                            duration_tier_scale_values,
                            duration_tier_times_seconds,
                        )
                    )
                    * sample_rate_hz
                )
            )
            if self._output_sample_count is None
            else self._output_sample_count
        )
        continuous_output_duration = float(
            np.trapezoid(
                duration_tier_scale_values,
                duration_tier_times_seconds,
            )
        )
        if output_count > 0:
            source_grid = np.arange(samples.size, dtype=np.float64)
            target_grid = np.linspace(
                0.0,
                float(samples.size - 1),
                output_count,
                dtype=np.float64,
            )
            waveform = np.interp(target_grid, source_grid, samples) * 0.8
        else:
            waveform = np.array([], dtype=np.float64)
        pitch_times = np.array([0.1, 0.2, 0.3], dtype=np.float64)
        pitch_values = np.array([100.0, 110.0, 120.0], dtype=np.float64)
        values: dict[str, object] = {
            "waveform": np.asarray(waveform, dtype=np.float64),
            "sample_rate_hz": float(sample_rate_hz),
            "output_xmin_seconds": 0.0,
            "output_xmax_seconds": continuous_output_duration,
            "source_pitch_times_seconds": pitch_times,
            "source_pitch_values_hz": pitch_values,
            "final_pitch_times_seconds": pitch_times.copy(),
            "final_pitch_values_hz": pitch_values.copy(),
            "duration_tier_times_seconds": (duration_tier_times_seconds.copy()),
            "duration_tier_scale_values": (duration_tier_scale_values.copy()),
            "duration_tier_point_count_before": 0,
            "duration_tier_point_count_after": DURATION_TIER_POINT_COUNT,
        }
        values.update(self._overrides)
        return e01_duration_transform._BackendDurationScalingOutput(**values)


class _FailingBackend:
    def scale_aligned_segment_duration(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        raise RuntimeError("private-backend-detail")


class _InvalidContractBackend:
    def scale_aligned_segment_duration(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        return SimpleNamespace(waveform=np.array([0.0], dtype=np.float64))


class _VersionString(str):
    pass


class _BackendSound:
    def __init__(self, values: np.ndarray, *, xmax: float) -> None:
        self.values = values
        self.sampling_frequency = 16_000.0
        self.xmin = 0.0
        self.xmax = xmax


class _PraatBackendHarness:
    def __init__(
        self,
        *,
        fail_resynthesis: bool = False,
        fail_reset: bool = False,
    ) -> None:
        self.manipulation = object()
        self.pitch_tier = object()
        self.duration_tier = object()
        self.duration_points: list[tuple[float, float]] = []
        self.run_scripts: list[str] = []
        self.fail_resynthesis = fail_resynthesis
        self.fail_reset = fail_reset

    def sound_factory(
        self,
        values: np.ndarray,
        *,
        sampling_frequency: float,
    ) -> _BackendSound:
        assert sampling_frequency == 16_000.0
        return _BackendSound(
            np.asarray([values], dtype=np.float64),
            xmax=values.size / sampling_frequency,
        )

    def run(self, script: str) -> object:
        self.run_scripts.append(script)
        if self.fail_reset and script == "random_initializeSafelyAndUnpredictably ()":
            raise RuntimeError("reset-private-detail")
        return None

    def call(
        self,
        selected_objects: object,
        command: str,
        *arguments: object,
    ) -> object:
        selected = selected_objects
        if command == "To Manipulation":
            return self.manipulation
        if command == "Extract pitch tier":
            return self.pitch_tier
        if command == "Extract duration tier":
            return self.duration_tier
        if command == "Get number of points":
            if selected is self.pitch_tier:
                return 3
            if selected is self.duration_tier:
                return len(self.duration_points)
        if command == "Get time from index":
            index = cast(int, arguments[0]) - 1
            if selected is self.pitch_tier:
                return (0.1, 0.2, 0.3)[index]
            if selected is self.duration_tier:
                return self.duration_points[index][0]
        if command == "Get value at index":
            index = cast(int, arguments[0]) - 1
            if selected is self.pitch_tier:
                return (100.0, 110.0, 120.0)[index]
            if selected is self.duration_tier:
                return self.duration_points[index][1]
        if command == "Add point":
            assert selected is self.duration_tier
            self.duration_points.append(
                (
                    float(cast(float, arguments[0])),
                    float(cast(float, arguments[1])),
                )
            )
            return None
        if command == "Replace duration tier":
            return None
        if command == "Get resynthesis (overlap-add)":
            if self.fail_resynthesis:
                raise RuntimeError("resynthesis-private-detail")
            return _BackendSound(
                np.zeros((1, 16_640), dtype=np.float64),
                xmax=1.04,
            )
        raise AssertionError(f"unexpected fake command: {command}")


def _waveform(
    dtype: np.dtype[np.floating] = np.dtype(np.float64),
) -> np.ndarray:
    sample_rate = 16_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    return np.asarray(
        0.1 * np.sin(2.0 * np.pi * 180.0 * time),
        dtype=dtype,
    )


def _transform(
    waveform: object,
    selected_backend: object | None = None,
    **overrides: object,
):
    arguments: dict[str, object] = {
        "sample_rate_hz": 16_000,
        "time_step_seconds": 0.01,
        "analysis_pitch_floor_hz": 75.0,
        "analysis_pitch_ceiling_hz": 300.0,
        "segment_start_seconds": 0.2,
        "segment_end_seconds": 0.4,
        "transition_seconds": 0.02,
        "duration_scale_factor": 1.2,
        "praat_random_seed": 5_489,
        "praat_process_isolation_policy": PRAAT_PROCESS_ISOLATION_POLICY,
        "expected_sample_count_rounding": "half_up",
        "maximum_duration_error_samples": 0,
        "minimum_pitch_tier_points": 3,
        "peak_limit": 1.0,
        "max_output_samples": 20_000,
        "backend": (
            selected_backend if selected_backend is not None else _FakeBackend()
        ),
        "backend_version": EXPECTED_PARSELMOUTH_VERSION,
    }
    arguments.update(overrides)
    return scale_aligned_segment_duration(
        waveform,  # type: ignore[arg-type]
        **arguments,  # type: ignore[arg-type]
    )


def _call_direct_praat_backend(
    harness: _PraatBackendHarness,
) -> e01_duration_transform._BackendDurationScalingOutput:
    backend = e01_duration_transform._ParselmouthPsolaDurationBackend(
        sound_factory=harness.sound_factory,
        praat_call=harness.call,
        praat_run=harness.run,
    )
    return backend.scale_aligned_segment_duration(
        _waveform(),
        sample_rate_hz=16_000,
        time_step_seconds=0.01,
        analysis_pitch_floor_hz=75.0,
        analysis_pitch_ceiling_hz=300.0,
        duration_tier_times_seconds=np.array(
            [0.0, 0.2, 0.22, 0.38, 0.4, 1.0],
            dtype=np.float64,
        ),
        duration_tier_scale_values=np.array(
            [1.0, 1.0, 11.0 / 9.0, 11.0 / 9.0, 1.0, 1.0],
            dtype=np.float64,
        ),
        praat_random_seed=5_489,
    )


def test_praat_backend_seeds_immediately_before_resynthesis_and_resets() -> None:
    harness = _PraatBackendHarness()

    result = _call_direct_praat_backend(harness)

    assert isinstance(result.waveform, np.ndarray)
    assert result.waveform.shape == (16_640,)
    assert harness.run_scripts == [
        "random_initializeWithSeedUnsafelyButPredictably (5489)",
        "random_initializeSafelyAndUnpredictably ()",
    ]


def test_praat_backend_resets_rng_when_resynthesis_fails() -> None:
    harness = _PraatBackendHarness(fail_resynthesis=True)

    with pytest.raises(RuntimeError, match="resynthesis-private-detail"):
        _call_direct_praat_backend(harness)

    assert harness.run_scripts == [
        "random_initializeWithSeedUnsafelyButPredictably (5489)",
        "random_initializeSafelyAndUnpredictably ()",
    ]


def test_praat_backend_fails_closed_when_rng_reset_fails() -> None:
    harness = _PraatBackendHarness(fail_reset=True)

    with pytest.raises(E01DurationTransformError, match="safe RNG"):
        _call_direct_praat_backend(harness)


@pytest.mark.parametrize("dtype", [np.dtype(np.float32), np.dtype(np.float64)])
def test_duration_scale_preserves_dtype_source_and_allocates_new_output(
    dtype: np.dtype[np.floating],
) -> None:
    original = _waveform(dtype)
    before = original.copy()
    backend = _FakeBackend()

    result = _transform(original, backend)

    np.testing.assert_array_equal(original, before)
    assert result.waveform.dtype == dtype
    assert result.waveform.size == 16_640
    assert not np.shares_memory(original, result.waveform)
    assert result.metadata.input_sample_count == 16_000
    assert result.metadata.theoretical_output_sample_count == 16_640
    assert result.metadata.output_sample_count == 16_640
    assert result.metadata.duration_error_samples == 0
    backend_samples = backend.calls[0]["samples"]
    assert isinstance(backend_samples, np.ndarray)
    assert backend_samples.dtype == np.dtype(np.float64)


def test_six_point_trapezoid_is_exact_and_pitch_tier_is_unchanged() -> None:
    backend = _FakeBackend()

    result = _transform(_waveform(), backend)

    np.testing.assert_allclose(
        np.asarray(
            backend.calls[0]["duration_tier_times_seconds"],
            dtype=np.float64,
        ),
        np.array([0.0, 0.2, 0.22, 0.38, 0.4, 1.0], dtype=np.float64),
        rtol=0.0,
        atol=np.finfo(np.float64).eps,
    )
    np.testing.assert_allclose(
        np.asarray(
            backend.calls[0]["duration_tier_scale_values"],
            dtype=np.float64,
        ),
        np.array(
            [1.0, 1.0, 11.0 / 9.0, 11.0 / 9.0, 1.0, 1.0],
            dtype=np.float64,
        ),
        rtol=1e-15,
        atol=0.0,
    )
    assert result.metadata.duration_tier_point_count == 6
    assert result.metadata.pitch_tier_point_count == 3
    assert result.metadata.maximum_pitch_tier_time_change_seconds == 0.0
    assert result.metadata.maximum_pitch_tier_value_change_hz == 0.0
    assert result.metadata.duration_tier_area_seconds == pytest.approx(1.04)
    assert result.metadata.duration_tier_area_error_seconds <= 1e-15


def test_metadata_records_exact_alignment_samples_and_algorithm() -> None:
    result = _transform(_waveform())
    report = result.metadata

    assert report.transform == "duration"
    assert report.operation == "scale_aligned_segment_duration"
    assert report.algorithm_id == DURATION_SCALING_ALGORITHM_ID
    assert report.parselmouth_version == EXPECTED_PARSELMOUTH_VERSION
    assert report.praat_version == EXPECTED_PRAAT_VERSION
    assert report.backend_provenance == "test_injected_backend"
    assert report.paper_evidence_eligibility == "forbidden_test_injection"
    assert report.segment_start_sample == 3_200
    assert report.segment_end_sample == 6_400
    assert report.transition_samples == 320
    assert report.duration_scale_factor == 1.2
    assert report.duration_tier_plateau_scale_factor == pytest.approx(11.0 / 9.0)
    assert report.praat_random_seed == 5_489
    assert report.praat_process_isolation_policy == PRAAT_PROCESS_ISOLATION_POLICY
    assert report.praat_rng_reset_policy == PRAAT_RNG_RESET_POLICY
    assert report.expected_sample_count_rounding == "half_up"
    assert report.output_duration_seconds == pytest.approx(1.04)
    assert re.fullmatch(r"[0-9a-f]{64}", report.output_digest_sha256)


def test_shortening_is_supported_with_explicit_output_budget() -> None:
    result = _transform(
        _waveform(),
        duration_scale_factor=0.8,
    )

    assert result.waveform.size == 15_360
    assert result.metadata.theoretical_output_sample_count == 15_360


def test_theoretical_count_uses_pinned_half_up_decimal_rounding() -> None:
    backend = _FakeBackend(output_sample_count=16_001)

    result = _transform(
        _waveform(),
        backend,
        duration_scale_factor=1.00015625,
    )

    assert result.metadata.theoretical_output_sample_count == 16_001
    assert result.metadata.output_sample_count == 16_001
    assert result.metadata.output_duration_seconds == pytest.approx(1.0000625)
    assert (
        result.metadata.theoretical_continuous_output_duration_seconds
        == pytest.approx(1.00003125)
    )


def test_output_domain_uses_continuous_target_not_discrete_sample_count() -> None:
    with pytest.raises(
        E01DurationTransformError,
        match="continuous DurationTier target",
    ):
        _transform(
            _waveform(),
            _FakeBackend(
                output_sample_count=16_001,
                output_xmax_seconds=16_001 / 16_000,
            ),
            duration_scale_factor=1.00015625,
        )


def test_actual_duration_error_is_caller_bounded_and_reported() -> None:
    accepted = _transform(
        _waveform(),
        _FakeBackend(output_sample_count=16_576),
        maximum_duration_error_samples=64,
    )

    assert accepted.metadata.duration_error_samples == 64

    with pytest.raises(
        E01DurationTransformError,
        match="maximum_duration_error_samples",
    ):
        _transform(
            _waveform(),
            _FakeBackend(output_sample_count=16_576),
            maximum_duration_error_samples=63,
        )


def test_output_and_digest_are_deterministic_and_contract_bound() -> None:
    first = _transform(_waveform())
    second = _transform(_waveform())
    different_contract = _transform(
        _waveform(),
        maximum_duration_error_samples=1,
    )

    np.testing.assert_array_equal(first.waveform, second.waveform)
    assert first.metadata == second.metadata
    np.testing.assert_array_equal(first.waveform, different_contract.waveform)
    assert (
        first.metadata.output_digest_sha256
        != different_contract.metadata.output_digest_sha256
    )


def test_seed_is_forwarded_to_backend_and_changes_contract_digest() -> None:
    first_backend = _FakeBackend()
    second_backend = _FakeBackend()

    first = _transform(_waveform(), first_backend, praat_random_seed=5_489)
    second = _transform(_waveform(), second_backend, praat_random_seed=5_490)

    assert first_backend.calls[0]["praat_random_seed"] == 5_489
    assert second_backend.calls[0]["praat_random_seed"] == 5_490
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

    with pytest.raises(E01DurationTransformError):
        _transform(waveform, backend)

    assert backend.calls == []


def test_array_subclass_and_memmap_are_rejected(tmp_path) -> None:
    base = _waveform()
    subclass = base.view(np.recarray)
    mapped_path = tmp_path / "duration-pcm.bin"
    mapped = np.memmap(
        mapped_path,
        dtype=np.float32,
        mode="w+",
        shape=(16_000,),
    )
    mapped[:] = base

    for invalid in (subclass, mapped):
        with pytest.raises(E01DurationTransformError, match="base NumPy"):
            _transform(invalid)


@pytest.mark.parametrize("sample_rate_hz", [0, 8_000, 16_000.0, True])
def test_sample_rate_must_be_exact_integer_16khz(
    sample_rate_hz: object,
) -> None:
    backend = _FakeBackend()

    with pytest.raises(E01DurationTransformError, match="sample_rate_hz"):
        _transform(
            _waveform(),
            backend,
            sample_rate_hz=sample_rate_hz,
        )

    assert backend.calls == []


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("time_step_seconds", 0.0),
        ("time_step_seconds", float("nan")),
        ("analysis_pitch_floor_hz", -1.0),
        ("analysis_pitch_ceiling_hz", float("inf")),
        ("segment_start_seconds", 0.0),
        ("segment_end_seconds", -1.0),
        ("transition_seconds", 0.0),
        ("duration_scale_factor", 0.0),
        ("duration_scale_factor", 1.0),
        ("duration_scale_factor", float("nan")),
        ("praat_random_seed", 0),
        ("praat_random_seed", 2_147_483_648),
        ("praat_random_seed", 5_489.0),
        ("praat_random_seed", True),
        ("praat_process_isolation_policy", "in_process"),
        ("praat_process_isolation_policy", []),
        ("expected_sample_count_rounding", "floor"),
        ("expected_sample_count_rounding", "ceil"),
        ("expected_sample_count_rounding", "bankers"),
        ("expected_sample_count_rounding", []),
        ("maximum_duration_error_samples", -1),
        ("maximum_duration_error_samples", 1.5),
        ("maximum_duration_error_samples", True),
        ("maximum_duration_error_samples", 161),
        ("minimum_pitch_tier_points", 0),
        ("peak_limit", 0.0),
        ("peak_limit", 1.1),
        ("max_output_samples", 0),
    ],
)
def test_invalid_scalar_parameters_fail_before_backend(
    field: str,
    invalid: object,
) -> None:
    backend = _FakeBackend()

    with pytest.raises(E01DurationTransformError):
        _transform(
            _waveform(),
            backend,
            **{field: invalid},
        )

    assert backend.calls == []


def test_pitch_analysis_bounds_must_be_ordered() -> None:
    with pytest.raises(E01DurationTransformError, match="must exceed"):
        _transform(
            _waveform(),
            analysis_pitch_floor_hz=300.0,
            analysis_pitch_ceiling_hz=75.0,
        )


def test_shortening_factor_must_support_positive_transition_plateau() -> None:
    with pytest.raises(E01DurationTransformError, match="too small"):
        _transform(
            _waveform(),
            duration_scale_factor=0.05,
        )


@pytest.mark.parametrize(
    ("start", "end", "transition", "message"),
    [
        (0.4, 0.2, 0.02, "must exceed"),
        (0.2, 0.24, 0.02, "twice"),
        (0.02, 0.4, 0.02, "after input start"),
        (0.2, 0.98, 0.02, "before input end"),
    ],
)
def test_segment_and_transition_geometry_is_fail_closed(
    start: float,
    end: float,
    transition: float,
    message: str,
) -> None:
    with pytest.raises(E01DurationTransformError, match=message):
        _transform(
            _waveform(),
            segment_start_seconds=start,
            segment_end_seconds=end,
            transition_seconds=transition,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("segment_start_seconds", 0.20001),
        ("segment_end_seconds", 0.40001),
        ("transition_seconds", 0.02001),
    ],
)
def test_alignment_times_must_lie_exactly_on_sample_grid(
    field: str,
    value: float,
) -> None:
    with pytest.raises(E01DurationTransformError, match="sample grid"):
        _transform(
            _waveform(),
            **{field: value},
        )


def test_theoretical_count_and_error_allowance_must_fit_output_budget() -> None:
    with pytest.raises(E01DurationTransformError, match="theoretical output"):
        _transform(
            _waveform(),
            duration_scale_factor=2.0,
            max_output_samples=19_000,
        )
    with pytest.raises(E01DurationTransformError, match="error allowance"):
        _transform(
            _waveform(),
            max_output_samples=16_650,
            maximum_duration_error_samples=11,
        )


def test_praat_three_times_input_cap_is_enforced_before_backend() -> None:
    backend = _FakeBackend()

    with pytest.raises(E01DurationTransformError, match="three-times-input"):
        _transform(
            _waveform(),
            backend,
            duration_scale_factor=11.1,
            max_output_samples=60_000,
        )

    assert backend.calls == []


def test_input_above_peak_or_input_count_budget_fails_before_backend() -> None:
    waveform = _waveform()
    waveform[0] = 0.9

    with pytest.raises(E01DurationTransformError, match="input waveform peak"):
        _transform(waveform, peak_limit=0.5)
    with pytest.raises(E01DurationTransformError, match="input sample count"):
        _transform(waveform, max_output_samples=15_999)


def test_backend_pair_and_exact_version_are_required() -> None:
    arguments = {
        "backend": _FakeBackend(),
        "backend_version": None,
    }
    with pytest.raises(E01DurationTransformError, match="provided together"):
        _transform(_waveform(), **arguments)
    with pytest.raises(E01DurationTransformError, match="version"):
        _transform(
            _waveform(),
            backend_version="latest",
        )
    with pytest.raises(E01DurationTransformError, match="version"):
        _transform(
            _waveform(),
            backend_version=_VersionString(EXPECTED_PARSELMOUTH_VERSION),
        )


def test_backend_errors_are_sanitized() -> None:
    with pytest.raises(
        E01DurationTransformError,
        match="Parselmouth PSOLA duration scaling failed",
    ) as captured:
        _transform(_waveform(), _FailingBackend())

    assert "private-backend-detail" not in str(captured.value)


def test_backend_result_must_have_exact_dataclass_contract() -> None:
    with pytest.raises(E01DurationTransformError, match="invalid result contract"):
        _transform(_waveform(), _InvalidContractBackend())


@pytest.mark.parametrize(
    "overrides",
    [
        {"sample_rate_hz": 8_000.0},
        {"waveform": np.array([], dtype=np.float64)},
        {"waveform": np.zeros(20_001, dtype=np.float64)},
        {"waveform": np.array([np.nan] * 16_640, dtype=np.float64)},
        {"output_xmin_seconds": 0.1},
        {"output_xmax_seconds": 1.03},
        {"output_xmax_seconds": float("nan")},
        {"duration_tier_point_count_before": 1},
        {"duration_tier_point_count_after": 5},
        {
            "duration_tier_times_seconds": np.array(
                [0.0, 0.2, 0.22, 0.38, 0.41, 1.0],
                dtype=np.float64,
            )
        },
        {
            "duration_tier_scale_values": np.array(
                [1.0, 1.0, 11.0 / 9.0, 1.2, 1.0, 1.0],
                dtype=np.float64,
            )
        },
    ],
)
def test_invalid_backend_waveform_and_duration_contracts_fail(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(E01DurationTransformError):
        _transform(
            _waveform(),
            _FakeBackend(**overrides),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "source_pitch_times_seconds": np.array(
                [0.1, 0.2],
                dtype=np.float64,
            )
        },
        {
            "source_pitch_times_seconds": np.array(
                [0.1, 0.1, 0.3],
                dtype=np.float64,
            )
        },
        {
            "source_pitch_times_seconds": np.array(
                [0.1, 0.2, 1.1],
                dtype=np.float64,
            )
        },
        {
            "source_pitch_values_hz": np.array(
                [50.0, 110.0, 120.0],
                dtype=np.float64,
            )
        },
        {
            "final_pitch_times_seconds": np.array(
                [0.1, 0.21, 0.3],
                dtype=np.float64,
            )
        },
        {
            "final_pitch_values_hz": np.array(
                [100.0, 111.0, 120.0],
                dtype=np.float64,
            )
        },
    ],
)
def test_invalid_or_changed_pitch_tier_fails(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(E01DurationTransformError):
        _transform(
            _waveform(),
            _FakeBackend(**overrides),  # type: ignore[arg-type]
        )


def test_minimum_pitch_points_is_enforced() -> None:
    with pytest.raises(
        E01DurationTransformError,
        match="minimum_pitch_tier_points",
    ):
        _transform(
            _waveform(),
            minimum_pitch_tier_points=4,
        )


def test_output_peak_is_not_implicitly_normalized_or_clipped() -> None:
    backend = _FakeBackend(
        waveform=np.full(16_640, 1.01, dtype=np.float64),
    )

    with pytest.raises(E01DurationTransformError, match="normalization"):
        _transform(
            _waveform(),
            backend,
            peak_limit=1.0,
        )


def test_metadata_allowlist_contains_no_pcm_path_or_identity_field() -> None:
    sentinel = 0.0123456789
    waveform = _waveform()
    waveform[0] = sentinel

    metadata_result = _transform(waveform).metadata.to_dict()

    assert all(not isinstance(value, np.ndarray) for value in metadata_result.values())
    assert sentinel not in metadata_result.values()
    for key in metadata_result:
        assert not any(
            forbidden in key
            for forbidden in (
                "path",
                "speaker",
                "participant",
                "item_id",
                "audio_id",
                "pcm",
            )
        )
    assert set(metadata_result) == {
        "transform",
        "operation",
        "algorithm_id",
        "parselmouth_version",
        "praat_version",
        "backend_provenance",
        "paper_evidence_eligibility",
        "sample_rate_hz",
        "time_step_seconds",
        "analysis_pitch_floor_hz",
        "analysis_pitch_ceiling_hz",
        "segment_start_seconds",
        "segment_end_seconds",
        "transition_seconds",
        "segment_start_sample",
        "segment_end_sample",
        "transition_samples",
        "duration_scale_factor",
        "duration_tier_plateau_scale_factor",
        "praat_random_seed",
        "praat_process_isolation_policy",
        "praat_rng_reset_policy",
        "expected_sample_count_rounding",
        "maximum_duration_error_samples",
        "minimum_pitch_tier_points",
        "peak_limit",
        "max_output_samples",
        "input_sample_count",
        "theoretical_output_sample_count",
        "output_sample_count",
        "duration_error_samples",
        "output_duration_seconds",
        "theoretical_continuous_output_duration_seconds",
        "duration_tier_area_seconds",
        "duration_tier_area_error_seconds",
        "duration_tier_point_count",
        "pitch_tier_point_count",
        "maximum_pitch_tier_time_change_seconds",
        "maximum_pitch_tier_value_change_hz",
        "input_peak",
        "output_peak",
        "output_digest_sha256",
    }


def test_public_signature_has_no_default_scientific_parameter() -> None:
    signature = inspect.signature(scale_aligned_segment_duration)

    for name, parameter in signature.parameters.items():
        if name not in {"backend", "backend_version"}:
            assert parameter.default is inspect.Parameter.empty


def test_lazy_backend_rejects_missing_or_wrong_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        e01_duration_transform.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(metadata.PackageNotFoundError("missing")),
    )
    with pytest.raises(E01DurationTransformError, match="unavailable"):
        e01_duration_transform._load_pinned_backend()

    monkeypatch.setattr(
        e01_duration_transform.metadata,
        "version",
        lambda _name: "0.4.6",
    )
    monkeypatch.setattr(
        e01_duration_transform,
        "import_module",
        lambda _name: SimpleNamespace(
            __version__="0.4.6",
            PRAAT_VERSION=EXPECTED_PRAAT_VERSION,
            Sound=lambda *_args, **_kwargs: None,
            praat=SimpleNamespace(
                call=lambda *_args: None,
                run=lambda *_args: None,
            ),
        ),
    )
    with pytest.raises(E01DurationTransformError, match="version differs"):
        e01_duration_transform._load_pinned_backend()


def test_lazy_backend_requires_exact_module_version_and_call_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        e01_duration_transform.metadata,
        "version",
        lambda _name: EXPECTED_PARSELMOUTH_VERSION,
    )
    monkeypatch.setattr(
        e01_duration_transform,
        "import_module",
        lambda _name: SimpleNamespace(
            __version__="latest",
            PRAAT_VERSION=EXPECTED_PRAAT_VERSION,
            Sound=lambda *_args, **_kwargs: None,
            praat=SimpleNamespace(
                call=lambda *_args: None,
                run=lambda *_args: None,
            ),
        ),
    )
    with pytest.raises(E01DurationTransformError, match="version differs"):
        e01_duration_transform._load_pinned_backend()

    monkeypatch.setattr(
        e01_duration_transform,
        "import_module",
        lambda _name: SimpleNamespace(
            __version__=EXPECTED_PARSELMOUTH_VERSION,
            PRAAT_VERSION=EXPECTED_PRAAT_VERSION,
            Sound=None,
            praat=SimpleNamespace(call=None, run=None),
        ),
    )
    with pytest.raises(E01DurationTransformError, match="command boundary"):
        e01_duration_transform._load_pinned_backend()

    monkeypatch.setattr(
        e01_duration_transform,
        "import_module",
        lambda _name: SimpleNamespace(
            __version__=EXPECTED_PARSELMOUTH_VERSION,
            PRAAT_VERSION=EXPECTED_PRAAT_VERSION,
            Sound=lambda *_args, **_kwargs: None,
            praat=SimpleNamespace(
                call=lambda *_args: None,
                run=None,
            ),
        ),
    )
    with pytest.raises(E01DurationTransformError, match="command boundary"):
        e01_duration_transform._load_pinned_backend()


def test_lazy_backend_rejects_wrong_embedded_praat_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        e01_duration_transform.metadata,
        "version",
        lambda _name: EXPECTED_PARSELMOUTH_VERSION,
    )
    monkeypatch.setattr(
        e01_duration_transform,
        "import_module",
        lambda _name: SimpleNamespace(
            __version__=EXPECTED_PARSELMOUTH_VERSION,
            PRAAT_VERSION="latest",
            Sound=lambda *_args, **_kwargs: None,
            praat=SimpleNamespace(
                call=lambda *_args: None,
                run=lambda *_args: None,
            ),
        ),
    )

    with pytest.raises(E01DurationTransformError, match="Praat version differs"):
        e01_duration_transform._load_pinned_backend()
