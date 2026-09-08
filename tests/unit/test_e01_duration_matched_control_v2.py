from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import subprocess
from typing import Any, cast

import numpy as np
import pytest

from voice_coach.evaluation import e01_duration_matched_control_v2 as primitive
from voice_coach.evaluation.e01_duration_matched_control_v2 import (
    DURATION_TIER_POINT_COUNT,
    E01DurationMatchedControlError,
    EXPECTED_PARSELMOUTH_VERSION,
    MATCHED_CONTROL_EXECUTION_PATH_ID,
    PRAAT_PROCESS_ISOLATION_POLICY,
    apply_duration_matched_control_v2,
)
from voice_coach.experiments import (
    e01_duration_matched_control_receipt_v2 as application,
)


ROOT = Path(__file__).parents[2]
CONFIG_PATH = ROOT / "configs/experiments/e01_duration_matched_control_v2.json"
RUNNER_PATH = ROOT / "scripts/run_e01_duration_matched_control_receipt_v2.py"
WORKER_PATH = ROOT / "scripts/run_e01_duration_matched_control_worker_v2.py"
ACOUSTIC_PYTHON = ROOT / ".venv-paper-acoustics/bin/python"
SCORER_PYTHON = ROOT / ".venv-scorer/bin/python"
REVIEWED_WHEEL = (
    Path.home()
    / ".cache/pip/wheels/ec/06/e8/"
    / "9b60c72eb3dcc075dc5b5706fb5c1d3be64f9367253e18a15c"
    / "praat_parselmouth-0.4.7-cp312-cp312-linux_aarch64.whl"
)


def _waveform() -> np.ndarray:
    sample_rate = 16_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    waveform = 0.1 * np.sin(2.0 * np.pi * 180.0 * time)
    generator = np.random.Generator(np.random.PCG64(777))
    waveform[4_000:5_200] = generator.normal(0.0, 0.02, 1_200)
    waveform[10_400:11_800] = generator.normal(0.0, 0.02, 1_400)
    return np.asarray(waveform, dtype=np.float64)


class _FakeBackend:
    def __init__(self, **overrides: object) -> None:
        self.calls: list[dict[str, object]] = []
        self._overrides = overrides

    def apply_duration_matched_control_v2(
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
    ) -> primitive._BackendDurationScalingOutput:
        self.calls.append(
            {
                "samples": samples.copy(),
                "duration_times": duration_tier_times_seconds.copy(),
                "duration_values": duration_tier_scale_values.copy(),
                "seed": praat_random_seed,
            }
        )
        continuous_duration = float(
            np.trapezoid(
                duration_tier_scale_values,
                duration_tier_times_seconds,
            )
        )
        output_count = int(round(continuous_duration * sample_rate_hz))
        source_grid = np.arange(samples.size, dtype=np.float64)
        target_grid = np.linspace(
            0.0,
            float(samples.size - 1),
            output_count,
            dtype=np.float64,
        )
        waveform = np.interp(target_grid, source_grid, samples) * 0.8
        pitch_times = np.array([0.1, 0.2, 0.3], dtype=np.float64)
        pitch_values = np.array([100.0, 110.0, 120.0], dtype=np.float64)
        pulse_times = np.array([0.1, 0.15, 0.2, 0.25], dtype=np.float64)
        values: dict[str, object] = {
            "waveform": np.asarray(waveform, dtype=np.float64),
            "sample_rate_hz": float(sample_rate_hz),
            "output_xmin_seconds": 0.0,
            "output_xmax_seconds": continuous_duration,
            "source_pitch_times_seconds": pitch_times,
            "source_pitch_values_hz": pitch_values,
            "final_pitch_times_seconds": pitch_times.copy(),
            "final_pitch_values_hz": pitch_values.copy(),
            "source_point_process_times_seconds": pulse_times,
            "final_point_process_times_seconds": pulse_times.copy(),
            "duration_tier_times_seconds": duration_tier_times_seconds.copy(),
            "duration_tier_scale_values": duration_tier_scale_values.copy(),
            "duration_tier_point_count_before": 0,
            "duration_tier_point_count_after": DURATION_TIER_POINT_COUNT,
            "psola_resynthesis_call_count": 1,
        }
        values.update(self._overrides)
        return primitive._BackendDurationScalingOutput(**values)


def _transform(
    role: str,
    duration_scale_factor: float,
    *,
    backend: object | None = None,
    maximum_duration_error_samples: int = 0,
) -> primitive.E01DurationMatchedControlResult:
    return apply_duration_matched_control_v2(
        _waveform(),
        role=role,  # type: ignore[arg-type]
        sample_rate_hz=16_000,
        time_step_seconds=0.01,
        analysis_pitch_floor_hz=75.0,
        analysis_pitch_ceiling_hz=300.0,
        segment_start_seconds=0.2,
        segment_end_seconds=0.4,
        transition_seconds=0.02,
        duration_scale_factor=duration_scale_factor,
        praat_random_seed=5_489,
        praat_process_isolation_policy=PRAAT_PROCESS_ISOLATION_POLICY,
        expected_sample_count_rounding="half_up",
        maximum_duration_error_samples=maximum_duration_error_samples,
        minimum_pitch_tier_points=3,
        peak_limit=0.5,
        max_output_samples=20_000,
        backend=cast(primitive._DurationScalingBackend, backend or _FakeBackend()),
        backend_version=EXPECTED_PARSELMOUTH_VERSION,
    )


@pytest.mark.parametrize(
    ("role", "factor"),
    [
        ("identity_psola_control", 1.2),
        ("duration_treatment", 1.0),
        ("copy_control", 1.0),
    ],
)
def test_role_and_k_contract_fails_closed(role: str, factor: float) -> None:
    with pytest.raises(E01DurationMatchedControlError):
        _transform(role, factor)


def test_both_roles_use_the_same_six_point_psola_path_without_fast_path() -> None:
    identity_backend = _FakeBackend()
    treatment_backend = _FakeBackend()

    identity = _transform(
        "identity_psola_control",
        1.0,
        backend=identity_backend,
    )
    treatment = _transform(
        "duration_treatment",
        1.2,
        backend=treatment_backend,
    )

    assert len(identity_backend.calls) == len(treatment_backend.calls) == 1
    identity_values = cast(np.ndarray, identity_backend.calls[0]["duration_values"])
    treatment_values = cast(
        np.ndarray,
        treatment_backend.calls[0]["duration_values"],
    )
    np.testing.assert_array_equal(identity_values, np.ones(6, dtype=np.float64))
    np.testing.assert_allclose(
        treatment_values,
        np.array(
            [1.0, 1.0, 11.0 / 9.0, 11.0 / 9.0, 1.0, 1.0],
            dtype=np.float64,
        ),
        rtol=0.0,
        atol=1e-15,
    )
    assert identity.metadata.execution_path_id == treatment.metadata.execution_path_id
    assert identity.metadata.execution_path_id == MATCHED_CONTROL_EXECUTION_PATH_ID
    assert identity.metadata.no_copy_fast_path is True
    assert identity.metadata.psola_resynthesis_call_count == 1
    assert identity.metadata.input_sample_count == identity.metadata.output_sample_count
    assert identity.metadata.duration_tier_area_seconds == 1.0
    assert identity.metadata.duration_tier_plateau_scale_factor == 1.0
    assert not np.array_equal(identity.waveform, _waveform())
    assert treatment.metadata.duration_tier_area_seconds == 1.04
    assert treatment.metadata.duration_tier_plateau_scale_factor == 11.0 / 9.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"psola_resynthesis_call_count": 0},
        {"psola_resynthesis_call_count": 2},
        {
            "source_point_process_times_seconds": np.array(
                [0.1, 0.2],
                dtype=np.float64,
            )
        },
        {
            "final_point_process_times_seconds": np.array(
                [0.1, 0.16, 0.2, 0.25],
                dtype=np.float64,
            )
        },
        {
            "duration_tier_scale_values": np.ones(
                5,
                dtype=np.float64,
            )
        },
    ],
)
def test_backend_no_fast_path_and_analysis_contract_is_exact(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(E01DurationMatchedControlError):
        _transform(
            "identity_psola_control",
            1.0,
            backend=_FakeBackend(**overrides),
        )


def test_identity_actual_sample_count_is_exact_n_even_with_error_allowance() -> None:
    backend = _FakeBackend(
        waveform=np.zeros(16_001, dtype=np.float64),
    )

    with pytest.raises(
        E01DurationMatchedControlError,
        match="actual output sample count must equal N",
    ):
        _transform(
            "identity_psola_control",
            1.0,
            backend=backend,
            maximum_duration_error_samples=1,
        )


class _BackendSound:
    def __init__(self, values: np.ndarray, *, xmax: float) -> None:
        self.values = values
        self.sampling_frequency = 16_000.0
        self.xmin = 0.0
        self.xmax = xmax


class _PraatHarness:
    def __init__(
        self,
        *,
        fail_resynthesis: bool = False,
        fail_reset: bool = False,
    ) -> None:
        self.manipulation = object()
        self.pitch_tier = object()
        self.point_process = object()
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
        return _BackendSound(
            np.asarray([values], dtype=np.float64),
            xmax=values.size / sampling_frequency,
        )

    def run(self, script: str) -> object:
        self.run_scripts.append(script)
        if self.fail_reset and script == "random_initializeSafelyAndUnpredictably ()":
            raise RuntimeError("private-reset-detail")
        return None

    def call(
        self,
        selected_objects: object,
        command: str,
        *arguments: object,
    ) -> object:
        if command == "To Manipulation":
            return self.manipulation
        if command == "Extract pitch tier":
            return self.pitch_tier
        if command == "Extract pulses":
            return self.point_process
        if command == "Extract duration tier":
            return self.duration_tier
        if command == "Get number of points":
            if selected_objects is self.pitch_tier:
                return 3
            if selected_objects is self.point_process:
                return 4
            if selected_objects is self.duration_tier:
                return len(self.duration_points)
        if command == "Get time from index":
            index = cast(int, arguments[0]) - 1
            if selected_objects is self.pitch_tier:
                return (0.1, 0.2, 0.3)[index]
            if selected_objects is self.point_process:
                return (0.1, 0.15, 0.2, 0.25)[index]
            if selected_objects is self.duration_tier:
                return self.duration_points[index][0]
        if command == "Get value at index":
            index = cast(int, arguments[0]) - 1
            if selected_objects is self.pitch_tier:
                return (100.0, 110.0, 120.0)[index]
            if selected_objects is self.duration_tier:
                return self.duration_points[index][1]
        if command == "Add point":
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
                raise RuntimeError("private-resynthesis-detail")
            return _BackendSound(
                np.zeros((1, 16_000), dtype=np.float64),
                xmax=1.0,
            )
        raise AssertionError(f"unexpected fake command: {command}")


def _call_backend(harness: _PraatHarness) -> object:
    backend = primitive._ParselmouthPsolaDurationBackend(
        sound_factory=harness.sound_factory,
        praat_call=harness.call,
        praat_run=harness.run,
    )
    return backend.apply_duration_matched_control_v2(
        _waveform(),
        sample_rate_hz=16_000,
        time_step_seconds=0.01,
        analysis_pitch_floor_hz=75.0,
        analysis_pitch_ceiling_hz=300.0,
        duration_tier_times_seconds=np.array(
            [0.0, 0.2, 0.22, 0.38, 0.4, 1.0],
            dtype=np.float64,
        ),
        duration_tier_scale_values=np.ones(6, dtype=np.float64),
        praat_random_seed=5_489,
    )


def test_praat_rng_is_reset_in_finally_on_resynthesis_failure() -> None:
    harness = _PraatHarness(fail_resynthesis=True)

    with pytest.raises(RuntimeError, match="private-resynthesis-detail"):
        _call_backend(harness)

    assert harness.run_scripts == [
        "random_initializeWithSeedUnsafelyButPredictably (5489)",
        "random_initializeSafelyAndUnpredictably ()",
    ]


def test_praat_rng_reset_failure_is_sanitized_and_fails_closed() -> None:
    harness = _PraatHarness(fail_reset=True)

    with pytest.raises(E01DurationMatchedControlError, match="safe RNG") as captured:
        _call_backend(harness)

    assert "private-reset-detail" not in str(captured.value)
    assert harness.run_scripts == [
        "random_initializeWithSeedUnsafelyButPredictably (5489)",
        "random_initializeSafelyAndUnpredictably ()",
    ]


def _config_document() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(CONFIG_PATH.read_text(encoding="utf-8")))


def test_config_is_canonical_and_all_new_source_pins_match() -> None:
    config_bytes = CONFIG_PATH.read_bytes()
    config = application.validate_config(config_bytes)

    assert application.canonical_json_bytes(config) == config_bytes
    for source_path, expected_sha256 in config["source_sha256"].items():
        assert application._sha256((ROOT / source_path).read_bytes()) == expected_sha256


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("roles", "identity_psola_control", "duration_scale_factor"), 1.2),
        (("roles", "duration_treatment", "duration_scale_factor"), 1.0),
        (("roles", "identity_psola_control", "role"), "duration_treatment"),
        (("execution_policy", "worker_process_count"), 1),
        (("execution_policy", "job_count_per_process"), 2),
        (("completion_limits", "paper_ready"), True),
        (
            ("execution_policy", "same_seed_interpretation"),
            "equal_rng_draws",
        ),
        (
            ("trust_boundary", "hostile_same_user_race_resistance_claimed"),
            True,
        ),
    ],
)
def test_config_rejects_role_k_scope_and_claim_mutations(
    path: tuple[str, ...],
    value: object,
) -> None:
    document = _config_document()
    cursor: dict[str, Any] = document
    for component in path[:-1]:
        cursor = cast(dict[str, Any], cursor[component])
    cursor[path[-1]] = value

    with pytest.raises(application.E01DurationMatchedControlReceiptError):
        application.validate_config(application.canonical_json_bytes(document))


def test_source_pin_mismatch_is_detected_before_worker_execution() -> None:
    document = _config_document()
    document["source_sha256"][WORKER_PATH.relative_to(ROOT).as_posix()] = "0" * 64
    config = application.validate_config(application.canonical_json_bytes(document))

    with pytest.raises(
        application.E01DurationMatchedControlReceiptError,
        match="source_sha256_mismatch",
    ):
        application.verify_source_pins(ROOT, config)


def test_private_pcm_ipc_tamper_and_role_mismatch_fail_closed() -> None:
    values = (0.1, -0.2, 0.3)
    pcm = b"".join(__import__("struct").pack("<d", value) for value in values)
    private = {
        "encoding": "base64_little_endian_float64",
        "sample_count": 3,
        "pcm_sha256": application._sha256(pcm),
        "payload": base64.b64encode(pcm).decode("ascii"),
        "publication_forbidden": True,
    }
    transform = {"output_sample_count": 3}
    metrics = {"candidate_pcm_sha256": application._sha256(pcm)}

    assert (
        application._decode_private_pair_pcm(
            private,
            role="identity_psola_control",
            transform=transform,
            direct_metrics=metrics,
        )
        == values
    )
    tampered = dict(private)
    tampered["pcm_sha256"] = "0" * 64
    with pytest.raises(
        application.E01DurationMatchedControlReceiptError,
        match="digest",
    ):
        application._decode_private_pair_pcm(
            tampered,
            role="duration_treatment",
            transform=transform,
            direct_metrics=metrics,
        )


def test_pair_metrics_are_direct_and_bind_both_pcm_digests() -> None:
    identity = (0.0, 0.1, -0.1)
    treatment = (0.0, 0.2, -0.2, 0.3)

    metrics = application._direct_pair_comparison(identity, treatment)

    assert metrics["control_subtraction_used"] is False
    assert metrics["computed_directly_from_ephemeral_worker_pcm"] is True
    assert metrics["candidate_minus_reference_sample_count"] == 1
    assert metrics["reference_pcm_sha256"] == application._pcm_digest(identity)
    assert metrics["candidate_pcm_sha256"] == application._pcm_digest(treatment)


@pytest.mark.skipif(
    not (
        ACOUSTIC_PYTHON.exists() and SCORER_PYTHON.exists() and REVIEWED_WHEEL.exists()
    ),
    reason="pinned local acoustic runtime is unavailable",
)
@pytest.mark.parametrize(
    ("mutation_path", "mutation_value"),
    [
        (
            ("runtime_build_provenance", "python", "hidden_payload"),
            "must-not-publish",
        ),
        (
            ("raw_to_output_direct_metrics", "mean_absolute_error"),
            "/private/must-not-publish",
        ),
        (
            ("transform_aggregate", "output_peak"),
            "/private/must-not-publish",
        ),
        (
            ("execution", "worker_process_count"),
            True,
        ),
        (
            ("raw_to_output_direct_metrics", "pearson_correlation"),
            float("nan"),
        ),
        (
            ("raw_to_output_direct_metrics", "maximum_absolute_error"),
            -0.1,
        ),
        (
            ("raw_to_output_direct_metrics", "mean_absolute_error"),
            1e300,
        ),
        (
            ("runtime_build_provenance", "reviewed_wheel", "size_bytes"),
            10**100,
        ),
        (
            ("runtime_build_provenance", "installed_extension", "size_bytes"),
            10**100,
        ),
        (
            (
                "runtime_build_provenance",
                "kernel_network_guard",
                "libseccomp_size_bytes",
            ),
            10**100,
        ),
    ],
)
def test_nested_worker_payload_has_recursive_exact_allowlist(
    mutation_path: tuple[str, ...],
    mutation_value: object,
) -> None:
    config = _config_document()
    config_sha256 = application._sha256(CONFIG_PATH.read_bytes())
    receipt_root = (
        ROOT / "artifacts/phase1/e01_duration_matched_control_pair_v2_receipts"
    )
    current_receipts = [
        document
        for path in sorted(receipt_root.glob("*/receipt.json"))
        if (document := json.loads(path.read_text(encoding="utf-8")))["lineage"][
            "config_sha256"
        ]
        == config_sha256
    ]
    if not current_receipts:
        subprocess.run(
            _runner_command(),
            capture_output=True,
            check=True,
            cwd=ROOT,
            text=True,
            timeout=120,
        )
        current_receipts = [
            document
            for path in sorted(receipt_root.glob("*/receipt.json"))
            if (document := json.loads(path.read_text(encoding="utf-8")))["lineage"][
                "config_sha256"
            ]
            == config_sha256
        ]
    current_receipt = current_receipts[-1]
    worker = copy.deepcopy(
        current_receipt["worker_receipts"]["identity_psola_control"]["worker_receipt"]
    )
    mutation_target = worker
    for component in mutation_path[:-1]:
        mutation_target = mutation_target[component]
    mutation_target[mutation_path[-1]] = mutation_value

    with pytest.raises(application.E01DurationMatchedControlReceiptError):
        application._validate_publishable_worker_nested_contract(
            worker,
            config=config,
            role="identity_psola_control",
        )


@pytest.mark.skipif(
    not (
        ACOUSTIC_PYTHON.exists() and SCORER_PYTHON.exists() and REVIEWED_WHEEL.exists()
    ),
    reason="pinned local acoustic runtime is unavailable",
)
@pytest.mark.parametrize(
    ("mean_absolute_error", "root_mean_squared_error", "maximum_absolute_error"),
    [
        (0.5, 0.1, 0.2),
        (0.1, 0.4, 0.5),
        (0.1, 0.2, 0.15),
    ],
)
def test_direct_metric_semantic_relationships_fail_closed(
    mean_absolute_error: float,
    root_mean_squared_error: float,
    maximum_absolute_error: float,
) -> None:
    config = _config_document()
    worker = _current_identity_worker_receipt(config)
    metrics = worker["raw_to_output_direct_metrics"]
    metrics["mean_absolute_error"] = mean_absolute_error
    metrics["root_mean_squared_error"] = root_mean_squared_error
    metrics["maximum_absolute_error"] = maximum_absolute_error

    with pytest.raises(
        application.E01DurationMatchedControlReceiptError,
        match="semantic",
    ):
        application._validate_publishable_worker_nested_contract(
            worker,
            config=config,
            role="identity_psola_control",
        )


def test_partial_second_worker_failure_never_publishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config_document()
    publish_calls: list[bytes] = []

    class _Store:
        def __init__(self, _root: Path) -> None:
            pass

        def publish(self, receipt_bytes: bytes) -> tuple[str, str, bool]:
            publish_calls.append(receipt_bytes)
            return "0" * 64, "forbidden", True

    identity_worker = {
        "completion_limits": config["completion_limits"],
    }
    call_count = 0

    def _worker(*_args: object, **_kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return identity_worker, (0.0,)
        raise application.E01DurationMatchedControlReceiptError("second_worker_failure")

    monkeypatch.setattr(application, "validate_config", lambda _bytes: config)
    monkeypatch.setattr(application, "_validated_workspace_root", lambda root: root)
    monkeypatch.setattr(application, "_ImmutableReceiptStore", _Store)
    monkeypatch.setattr(application, "verify_source_pins", lambda *_args: {})
    monkeypatch.setattr(application, "_run_worker", _worker)

    with pytest.raises(
        application.E01DurationMatchedControlReceiptError,
        match="second_worker_failure",
    ):
        application.run_receipt(
            workspace_root=tmp_path,
            config_bytes=b"ignored",
            reviewed_wheel_path=REVIEWED_WHEEL,
        )

    assert call_count == 2
    assert publish_calls == []


def test_immutable_store_rejects_hardlinked_existing_receipt(
    tmp_path: Path,
) -> None:
    store = application._ImmutableReceiptStore(tmp_path)
    receipt_bytes = b'{"status":"test"}\n'
    digest, relative_path, created = store.publish(receipt_bytes)
    receipt_path = tmp_path / relative_path
    hardlink_path = receipt_path.with_name("extra-hardlink")
    os.link(receipt_path, hardlink_path)

    assert created is True
    assert digest == application._sha256(receipt_bytes)
    with pytest.raises(application.E01DurationMatchedControlReceiptError):
        store.publish(receipt_bytes)


def test_immutable_store_rejects_symlinked_output_parent(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    phase1 = tmp_path / "artifacts" / "phase1"
    phase1.mkdir(parents=True)
    output_name = application.OUTPUT_RELATIVE_ROOT.parts[-1]
    (phase1 / output_name).symlink_to(outside, target_is_directory=True)
    store = application._ImmutableReceiptStore(tmp_path)

    with pytest.raises(application.E01DurationMatchedControlReceiptError):
        store.publish(b'{"status":"test"}\n')


def test_immutable_store_rejects_workspace_root_replacement(
    tmp_path: Path,
) -> None:
    store = application._ImmutableReceiptStore(tmp_path)
    original = tmp_path.with_name(f"{tmp_path.name}-original")
    tmp_path.rename(original)
    tmp_path.mkdir(mode=0o700)
    try:
        with pytest.raises(
            application.E01DurationMatchedControlReceiptError,
            match="workspace_root_changed",
        ):
            store.publish(b'{"status":"test"}\n')
        assert not (tmp_path / "artifacts").exists()
    finally:
        tmp_path.rmdir()
        original.rename(tmp_path)


def test_immutable_store_rejects_tampered_existing_receipt(
    tmp_path: Path,
) -> None:
    store = application._ImmutableReceiptStore(tmp_path)
    receipt_bytes = b'{"status":"test"}\n'
    _digest, relative_path, _created = store.publish(receipt_bytes)
    receipt_path = tmp_path / relative_path
    receipt_path.write_bytes(b'{"status":"tampered"}\n')

    with pytest.raises(application.E01DurationMatchedControlReceiptError):
        store.publish(receipt_bytes)


def _runner_command() -> list[str]:
    return [
        str(SCORER_PYTHON),
        str(RUNNER_PATH),
        "--workspace-root",
        str(ROOT),
        "--reviewed-wheel",
        str(REVIEWED_WHEEL),
    ]


def _current_identity_worker_receipt(config: dict[str, Any]) -> dict[str, Any]:
    config_sha256 = application._sha256(CONFIG_PATH.read_bytes())
    receipt_root = (
        ROOT / "artifacts/phase1/e01_duration_matched_control_pair_v2_receipts"
    )
    current_receipts = [
        document
        for path in sorted(receipt_root.glob("*/receipt.json"))
        if (document := json.loads(path.read_text(encoding="utf-8")))["lineage"][
            "config_sha256"
        ]
        == config_sha256
    ]
    if not current_receipts:
        subprocess.run(
            _runner_command(),
            capture_output=True,
            check=True,
            cwd=ROOT,
            text=True,
            timeout=120,
        )
        current_receipts = [
            document
            for path in sorted(receipt_root.glob("*/receipt.json"))
            if (document := json.loads(path.read_text(encoding="utf-8")))["lineage"][
                "config_sha256"
            ]
            == config_sha256
        ]
    if not current_receipts:
        raise AssertionError("current matched-control receipt was not created")
    receipt = current_receipts[-1]
    assert receipt["lineage"]["source_sha256"] == config["source_sha256"]
    return copy.deepcopy(
        receipt["worker_receipts"]["identity_psola_control"]["worker_receipt"]
    )


def test_runner_and_worker_argument_failures_are_sanitized() -> None:
    sentinel = "/private/do-not-publish-argument"
    runner = subprocess.run(
        [
            str(SCORER_PYTHON),
            str(RUNNER_PATH),
            "--workspace-root",
            str(ROOT),
            "--reviewed-wheel",
            sentinel,
            "--unexpected",
            sentinel,
        ],
        capture_output=True,
        check=False,
        cwd=ROOT,
        text=True,
        timeout=30,
    )
    worker = subprocess.run(
        [
            str(ACOUSTIC_PYTHON),
            str(WORKER_PATH),
            "--workspace-root",
            str(ROOT),
            "--reviewed-wheel",
            sentinel,
            "--role",
            sentinel,
        ],
        input=CONFIG_PATH.read_text(encoding="utf-8"),
        capture_output=True,
        check=False,
        cwd=ROOT,
        text=True,
        timeout=30,
    )

    assert runner.returncode == worker.returncode == 2
    assert sentinel not in runner.stdout + runner.stderr
    assert sentinel not in worker.stdout + worker.stderr
    assert json.loads(runner.stdout)["failure_code"] == (
        "matched_control_v2_runner_failure"
    )
    assert json.loads(worker.stdout)["failure_code"] == "worker_bootstrap_failure"


@pytest.mark.skipif(
    not (
        ACOUSTIC_PYTHON.exists() and SCORER_PYTHON.exists() and REVIEWED_WHEEL.exists()
    ),
    reason="pinned local acoustic runtime is unavailable",
)
def test_actual_fresh_process_voiced_unvoiced_pair_is_byte_identical() -> None:
    first = subprocess.run(
        _runner_command(),
        capture_output=True,
        check=True,
        cwd=ROOT,
        text=True,
        timeout=120,
    )
    second = subprocess.run(
        _runner_command(),
        capture_output=True,
        check=True,
        cwd=ROOT,
        text=True,
        timeout=120,
    )
    first_result = json.loads(first.stdout)
    second_result = json.loads(second.stdout)
    assert first.stderr == second.stderr == ""
    assert first_result["receipt_sha256"] == second_result["receipt_sha256"]
    assert second_result["idempotent_reuse"] is True
    receipt_path = ROOT / second_result["receipt_relative_path"]
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    assert application._sha256(receipt_bytes) == second_result["receipt_sha256"]
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    assert receipt_path.stat().st_nlink == 1
    assert receipt["pair_design"]["fresh_process_per_role"] is True
    assert receipt["pair_design"]["rng_draw_count_equality_assumed"] is False
    assert receipt["publication"]["private_pair_ipc_included"] is False
    assert receipt["trust_boundary"] == {
        "acoustic_startup_import_closure_attested": False,
        "external_trusted_launcher_completed": False,
        "full_os_image_attested": False,
        "hostile_same_user_race_resistance_claimed": False,
        "numpy_content_attested": False,
        "numpy_version_verified": True,
        "provenance_claim": (
            "selected_artifact_hashes_with_explicit_trusted_host_venv_assumption"
        ),
        "runner_startup_bytes_attested": False,
        "scorer_startup_import_closure_attested": False,
        "selected_artifact_hashes_verified": True,
        "threat_model": ("trusted_local_host_and_venv_no_hostile_same_user_race"),
    }
    assert (
        "private_pair_ipc"
        not in receipt["worker_receipts"]["identity_psola_control"]["worker_receipt"]
    )
    assert "payload" not in receipt_bytes.decode("utf-8")
    for role in ("identity_psola_control", "duration_treatment"):
        worker = receipt["worker_receipts"][role]["worker_receipt"]
        assert worker["role"] == role
        assert worker["execution"]["worker_process_count"] == 1
        assert worker["execution"]["job_count"] == 1
        assert worker["execution"]["kernel_seccomp_mode"] == 2
        assert worker["execution"]["kernel_network_guard_probe_block_count"] == 1
        assert worker["execution"]["python_network_intercept_count"] == 0
        assert worker["transform_aggregate"]["psola_resynthesis_call_count"] == 1
        assert worker["transform_aggregate"]["no_copy_fast_path"] is True
    identity = receipt["worker_receipts"]["identity_psola_control"]["worker_receipt"][
        "transform_aggregate"
    ]
    treatment = receipt["worker_receipts"]["duration_treatment"]["worker_receipt"][
        "transform_aggregate"
    ]
    assert identity["output_sample_count"] == identity["input_sample_count"] == 16_000
    assert treatment["output_sample_count"] == 16_640
    assert identity["praat_random_seed"] == treatment["praat_random_seed"] == 5_489
    assert (
        receipt["direct_comparisons"]["identity_to_treatment_incremental_estimate"][
            "control_subtraction_used"
        ]
        is False
    )


@pytest.mark.skipif(
    not ACOUSTIC_PYTHON.exists(),
    reason="pinned local acoustic runtime is unavailable",
)
def test_actual_treatment_pcm_is_identical_to_public_v1_primitive() -> None:
    code = """
import hashlib
import importlib.util
import pathlib
import sys
import numpy as np
p = pathlib.Path("src/voice_coach/evaluation/e01_duration_transform.py")
s = importlib.util.spec_from_file_location("v1_parity", p)
assert s is not None and s.loader is not None
m = importlib.util.module_from_spec(s)
sys.modules["v1_parity"] = m
s.loader.exec_module(m)
t = np.arange(16000, dtype=np.float64) / 16000
x = 0.1 * np.sin(2 * np.pi * 180 * t)
rng = np.random.Generator(np.random.PCG64(777))
x[4000:5200] = rng.normal(0, 0.02, 1200)
x[10400:11800] = rng.normal(0, 0.02, 1400)
r = m.scale_aligned_segment_duration(
    x,
    sample_rate_hz=16000,
    time_step_seconds=0.01,
    analysis_pitch_floor_hz=75.0,
    analysis_pitch_ceiling_hz=300.0,
    segment_start_seconds=0.2,
    segment_end_seconds=0.4,
    transition_seconds=0.02,
    duration_scale_factor=1.2,
    praat_random_seed=5489,
    praat_process_isolation_policy=m.PRAAT_PROCESS_ISOLATION_POLICY,
    expected_sample_count_rounding="half_up",
    maximum_duration_error_samples=0,
    minimum_pitch_tier_points=3,
    peak_limit=0.5,
    max_output_samples=20000,
)
print(hashlib.sha256(np.asarray(r.waveform, dtype="<f8").tobytes()).hexdigest())
"""
    completed = subprocess.run(
        [str(ACOUSTIC_PYTHON), "-c", code],
        capture_output=True,
        check=True,
        cwd=ROOT,
        text=True,
        timeout=30,
    )
    current_receipt_root = (
        ROOT / "artifacts/phase1/e01_duration_matched_control_pair_v2_receipts"
    )
    receipt_candidates = sorted(current_receipt_root.glob("*/receipt.json"))
    matching_digest = completed.stdout.strip()

    assert matching_digest == (
        "25d4c96b55dc56115aa7dd6cfd8b66adaeb3ec862944ec1d7b3f874d981e000f"
    )
    current_config_sha256 = application._sha256(CONFIG_PATH.read_bytes())
    assert any(
        (
            (receipt := json.loads(path.read_text(encoding="utf-8")))["lineage"][
                "config_sha256"
            ]
            == current_config_sha256
            and receipt["direct_comparisons"]["raw_to_treatment_total"][
                "candidate_pcm_sha256"
            ]
            == matching_digest
        )
        for path in receipt_candidates
    )


def test_runtime_version_assumption_matches_installed_test_environment() -> None:
    completed = subprocess.run(
        [
            str(ACOUSTIC_PYTHON),
            "-c",
            (
                "from importlib import metadata;"
                "print(metadata.version('praat-parselmouth'))"
            ),
        ],
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    )
    assert completed.stdout.strip() == EXPECTED_PARSELMOUTH_VERSION
    assert WORKER_PATH.is_file()
