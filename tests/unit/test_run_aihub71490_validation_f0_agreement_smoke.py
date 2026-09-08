from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
import stat
import sys
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import soundfile as sf

from voice_coach.experiments.actual_f0_agreement_smoke import verify_payload_sha256


SCRIPT = (
    Path(__file__).parents[2]
    / "scripts"
    / "run_aihub71490_validation_f0_agreement_smoke.py"
)
SPEC = importlib.util.spec_from_file_location(
    "run_aihub71490_validation_f0_agreement_smoke",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inventory_root(tmp_path: Path, *, count: int = 6) -> Path:
    root = (tmp_path / "dataset").resolve()
    source = root / "원천데이터" / "Validation" / "extracted_pronunciation_audio"
    source.mkdir(parents=True)
    for index in range(count):
        (source / f"private-utterance-{index}.wav").write_bytes(b"not-opened-by-fake")
    return root


def _modeling_manifest(
    tmp_path: Path,
    root: Path,
    *,
    splits: tuple[str, ...] | None = None,
) -> Path:
    source = root / "원천데이터" / "Validation" / "extracted_pronunciation_audio"
    paths = tuple(sorted(source.glob("private-utterance-*.wav")))
    normalized_splits = splits or ("dev",) * len(paths)
    assert len(normalized_splits) == len(paths)
    rows = [
        {
            "audio_path": path.relative_to(root).as_posix(),
            "official_partitions": ["Validation"],
            "pcm_sha256": f"{index + 1:064x}",
            "split": split,
        }
        for index, (path, split) in enumerate(
            zip(paths, normalized_splits, strict=True)
        )
    ]
    schema = pa.schema(
        [
            pa.field("audio_path", pa.string()),
            pa.field("official_partitions", pa.list_(pa.string())),
            pa.field("pcm_sha256", pa.string()),
            pa.field("split", pa.string()),
        ]
    )
    manifest = (tmp_path / "modeling.parquet").resolve()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), manifest)
    return manifest


def _configuration(
    tmp_path: Path,
    *,
    sample_size: int = 4,
    splits: tuple[str, ...] | None = None,
) -> Any:
    root = _inventory_root(tmp_path)
    manifest = _modeling_manifest(tmp_path, root, splits=splits)
    output = (tmp_path / "aggregate.json").resolve()
    python = (Path(__file__).parents[2] / ".venv-scorer/bin/python").resolve()
    return MODULE.SmokeConfiguration(
        dataset_root=root,
        modeling_manifest_path=manifest,
        expected_modeling_manifest_sha256=_sha256(manifest),
        output_path=output,
        private_temp_root=tmp_path.resolve(),
        parselmouth_python=python,
        torchcrepe_python=python,
        expected_source_sha256=tuple(
            (role, _sha256(path))
            for role, path in sorted(MODULE.IMPLEMENTATION_PATHS.items())
        ),
        seed=20_260_729,
        sample_size=sample_size,
        target_sample_rate_hz=16_000,
        maximum_duration_seconds=20.0,
        maximum_channel_count=2,
        downmix_policy=MODULE.DOWNMIX_POLICY,
        resample_policy=MODULE.RESAMPLE_POLICY,
        parselmouth_time_step_seconds=0.01,
        pitch_floor_hz=75.0,
        pitch_ceiling_hz=500.0,
        parselmouth_unvoiced_policy="keep_zero",
        torchcrepe_hop_length_samples=160,
        torchcrepe_model="tiny",
        torchcrepe_decoder="viterbi",
        torchcrepe_batch_size_frames=32,
        torchcrepe_pad=True,
        torchcrepe_periodicity_threshold=0.21,
        torchcrepe_silence_threshold_dbfs=-80.0,
        numpy_random_seed=20_260_729,
        torch_random_seed=20_260_729,
        torchcrepe_unvoiced_policy="keep_zero",
        common_grid_policy=MODULE.COMMON_GRID_POLICY,
        interpolation_policy="linear",
        endpoint_policy="trim_to_shared_support",
        agreement_unvoiced_policy="preserve_gaps",
        octave_tolerance_cents=50.0,
        maximum_agreement_frame_count=4_000,
        reference_role=MODULE.REFERENCE_ROLE,
        worker_timeout_seconds=60,
    )


def _probe(backend: str, configuration: Any) -> dict[str, object]:
    del configuration
    environment = (
        MODULE.EXPECTED_PARSELMOUTH_ENVIRONMENT
        if backend == "parselmouth"
        else MODULE.EXPECTED_TORCHCREPE_ENVIRONMENT
    )
    return {
        "status": "ok",
        "backend": backend,
        "environment": dict(environment),
        "network_socket_attempt_count": 0,
    }


def _decoder(source_path: Path, configuration: Any) -> Any:
    del source_path, configuration
    time = np.arange(3_200, dtype=np.float32) / 16_000
    samples = np.asarray(
        0.1 * np.sin(2.0 * np.pi * 180.0 * time),
        dtype=np.float32,
    )
    return MODULE.DecodedPcm(
        samples=samples,
        was_resampled=True,
        was_multichannel=False,
    )


def _worker(
    backend: str,
    pcm_npy: Path,
    configuration: Any,
) -> Any:
    assert stat.S_IMODE(pcm_npy.stat().st_mode) == 0o600
    assert stat.S_IMODE(pcm_npy.parent.stat().st_mode) == 0o700
    samples = np.load(pcm_npy, allow_pickle=False)
    assert samples.dtype == np.dtype(np.float32)
    assert samples.ndim == 1
    digest = hashlib.sha256(
        np.asarray(samples, dtype="<f4").tobytes(order="C")
    ).hexdigest()
    if backend == "parselmouth":
        times = (0.01, 0.02, 0.03, 0.04)
        frequencies = (180.0, 181.0, 0.0, 179.0)
        environment = dict(MODULE.EXPECTED_PARSELMOUTH_ENVIRONMENT)
    else:
        times = (0.0, 0.01, 0.02, 0.03, 0.04, 0.05)
        frequencies = (0.0, 181.0, 180.0, 182.0, 178.0, 0.0)
        environment = dict(MODULE.EXPECTED_TORCHCREPE_ENVIRONMENT)
    contour = MODULE.WorkerContour(
        backend=backend,
        environment=environment,
        pcm_sha256=digest,
        time_seconds=times,
        f0_hz=frequencies,
        backend_frame_count=len(times),
        voiced_frame_count=sum(value > 0.0 for value in frequencies),
        unvoiced_frame_count=sum(value == 0.0 for value in frequencies),
        network_socket_attempt_count=0,
    )
    del configuration
    return MODULE.WorkerOutcome(
        backend=backend,
        contour=contour,
        failure_code=None,
        network_socket_attempt_count=0,
    )


def test_runner_selects_before_decode_uses_private_npy_and_publishes_aggregates(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path)
    events: list[str] = []

    def recording_probe(backend: str, config: Any) -> dict[str, object]:
        events.append(f"probe:{backend}")
        return _probe(backend, config)

    def recording_decoder(path: Path, config: Any) -> Any:
        events.append("decode")
        return _decoder(path, config)

    report = MODULE.run_validation_f0_smoke(
        configuration,
        worker_probe=recording_probe,
        worker_execute=_worker,
        decoder=recording_decoder,
        pcm_integrity_verifier=lambda path, expected: bool(path and expected),
    )

    assert events[:2] == ["probe:parselmouth", "probe:torchcrepe"]
    assert events.count("decode") == 4
    assert report["status"] == "passed_bounded_technical_diagnostic"
    assert report["selection"]["sample_fixed_before_audio_read_count"] == 4
    assert report["selection"]["selected_project_split_counts"] == {"dev": 4}
    assert report["manifest_metadata"]["full_manifest_metadata_scan_count"] == 2
    assert (
        report["manifest_metadata"]["audio_content_read_during_metadata_scan"] is False
    )
    assert report["selection"]["failure_backfill_enabled"] is False
    assert report["execution"][
        "selected_source_audio_access_counts_by_project_split"
    ] == {"dev": 4}
    assert report["execution"]["locked_test_source_audio_access_count"] == 0
    assert report["execution"]["comparison_completed_count"] == 4
    assert report["execution"]["private_npy_created_count"] == 4
    assert report["execution"]["private_npy_cleaned_count"] == 4
    assert report["execution"]["network_socket_attempt_count"] == 0
    assert report["metrics"]["comparison_completed_count"] == 4
    assert report["scope"]["accuracy_claim"] is False
    assert report["scope"]["phase1_m4_completion_claim"] is False
    assert verify_payload_sha256(report)
    assert configuration.output_path.exists()
    assert stat.S_IMODE(configuration.output_path.stat().st_mode) == 0o600
    assert not tuple(tmp_path.glob(".phase1-f0-*"))

    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for index in range(6):
        assert f"private-utterance-{index}" not in serialized
    for forbidden in (str(configuration.dataset_root), str(configuration.output_path)):
        assert forbidden not in serialized
    for index in range(6):
        assert f"{index + 1:064x}" not in serialized


def test_selected_decode_failure_is_counted_without_backfill(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path, sample_size=4)
    attempts = 0
    worker_calls = 0

    def sometimes_failing_decoder(path: Path, config: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MODULE.PcmDecodeError("source_decode_failure")
        return _decoder(path, config)

    def counting_worker(backend: str, pcm_npy: Path, config: Any) -> Any:
        nonlocal worker_calls
        worker_calls += 1
        return _worker(backend, pcm_npy, config)

    report = MODULE.run_validation_f0_smoke(
        configuration,
        worker_probe=_probe,
        worker_execute=counting_worker,
        decoder=sometimes_failing_decoder,
        pcm_integrity_verifier=lambda path, expected: bool(path and expected),
    )

    assert attempts == 4
    assert worker_calls == 6
    assert report["status"] == "incomplete_bounded_technical_diagnostic"
    assert report["execution"]["comparison_completed_count"] == 3
    assert report["execution"]["primary_failure_reason_counts"] == {
        "source_decode_failure": 1
    }
    assert report["selection"]["sample_fixed_before_audio_read_count"] == 4
    assert report["selection"]["failure_backfill_enabled"] is False


def test_cross_process_pcm_mismatch_is_aggregate_failure(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path, sample_size=1)

    def mismatching_worker(backend: str, pcm_npy: Path, config: Any) -> Any:
        outcome = _worker(backend, pcm_npy, config)
        if backend == "torchcrepe":
            assert outcome.contour is not None
            contour = MODULE.WorkerContour(
                backend=outcome.contour.backend,
                environment=outcome.contour.environment,
                pcm_sha256="0" * 64,
                time_seconds=outcome.contour.time_seconds,
                f0_hz=outcome.contour.f0_hz,
                backend_frame_count=outcome.contour.backend_frame_count,
                voiced_frame_count=outcome.contour.voiced_frame_count,
                unvoiced_frame_count=outcome.contour.unvoiced_frame_count,
                network_socket_attempt_count=0,
            )
            return MODULE.WorkerOutcome(
                backend=backend,
                contour=contour,
                failure_code=None,
                network_socket_attempt_count=0,
            )
        return outcome

    report = MODULE.run_validation_f0_smoke(
        configuration,
        worker_probe=_probe,
        worker_execute=mismatching_worker,
        decoder=_decoder,
        pcm_integrity_verifier=lambda path, expected: bool(path and expected),
    )

    assert report["metrics"] is None
    assert report["execution"]["primary_failure_reason_counts"] == {
        "cross_process_pcm_digest_mismatch": 1
    }


def test_runner_never_opens_non_dev_or_locked_sources(tmp_path: Path) -> None:
    configuration = _configuration(
        tmp_path,
        sample_size=2,
        splits=("locked_test", "train", "dev", "calibration", "dev", "stack"),
    )
    integrity_paths: list[Path] = []
    decode_paths: list[Path] = []

    def recording_integrity(path: Path, expected: str) -> bool:
        assert expected
        integrity_paths.append(path)
        return True

    def recording_decoder(path: Path, config: Any) -> Any:
        decode_paths.append(path)
        return _decoder(path, config)

    report = MODULE.run_validation_f0_smoke(
        configuration,
        worker_probe=_probe,
        worker_execute=_worker,
        decoder=recording_decoder,
        pcm_integrity_verifier=recording_integrity,
    )

    assert {path.name for path in integrity_paths} == {
        "private-utterance-2.wav",
        "private-utterance-4.wav",
    }
    assert decode_paths == integrity_paths
    assert report["selection"]["official_inventory_project_split_counts"] == {
        "calibration": 1,
        "dev": 2,
        "locked_test": 1,
        "stack": 1,
        "train": 1,
    }
    assert report["execution"]["locked_test_source_audio_access_count"] == 0


def test_manifest_pin_mismatch_fails_before_any_audio_read(tmp_path: Path) -> None:
    configuration = replace(
        _configuration(tmp_path, sample_size=1),
        expected_modeling_manifest_sha256="0" * 64,
    )
    audio_read_count = 0

    def forbidden_integrity(path: Path, expected: str) -> bool:
        nonlocal audio_read_count
        del path, expected
        audio_read_count += 1
        return True

    with pytest.raises(MODULE.ValidationF0SmokeError, match="manifest source pin"):
        MODULE.run_validation_f0_smoke(
            configuration,
            worker_probe=_probe,
            worker_execute=_worker,
            decoder=_decoder,
            pcm_integrity_verifier=forbidden_integrity,
        )

    assert audio_read_count == 0


def test_manifest_change_during_initial_scan_fails_before_audio_read(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path, sample_size=1)
    audio_read_count = 0

    def mutating_reader(path: Path):
        yield from MODULE._iter_modeling_manifest_metadata(path)
        path.write_bytes(path.read_bytes() + b"changed")

    def forbidden_integrity(path: Path, expected: str) -> bool:
        nonlocal audio_read_count
        del path, expected
        audio_read_count += 1
        return True

    with pytest.raises(MODULE.ValidationF0SmokeError, match="manifest source pin"):
        MODULE.run_validation_f0_smoke(
            configuration,
            worker_probe=_probe,
            worker_execute=_worker,
            decoder=_decoder,
            manifest_metadata_reader=mutating_reader,
            pcm_integrity_verifier=forbidden_integrity,
        )

    assert audio_read_count == 0


def test_source_pcm_mismatch_is_not_decoded_or_backfilled(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path, sample_size=2)
    integrity_attempts = 0
    decoder_attempts = 0

    def first_mismatch(path: Path, expected: str) -> bool:
        nonlocal integrity_attempts
        del path, expected
        integrity_attempts += 1
        return integrity_attempts != 1

    def recording_decoder(path: Path, config: Any) -> Any:
        nonlocal decoder_attempts
        decoder_attempts += 1
        return _decoder(path, config)

    report = MODULE.run_validation_f0_smoke(
        configuration,
        worker_probe=_probe,
        worker_execute=_worker,
        decoder=recording_decoder,
        pcm_integrity_verifier=first_mismatch,
    )

    assert integrity_attempts == 2
    assert decoder_attempts == 1
    assert report["execution"]["source_pcm_integrity_match_count"] == 1
    assert report["execution"]["primary_failure_reason_counts"] == {
        "source_pcm_integrity_mismatch": 1
    }
    assert report["selection"]["failure_backfill_enabled"] is False


def test_private_pcm_context_cleans_after_worker_exception(tmp_path: Path) -> None:
    samples = np.zeros(160, dtype=np.float32)

    with pytest.raises(RuntimeError, match="synthetic"):
        with MODULE._private_pcm_npy(samples, tmp_path.resolve()) as npy_path:
            assert npy_path.exists()
            raise RuntimeError("synthetic")

    assert not tuple(tmp_path.glob(".phase1-f0-*"))


def test_decode_resamples_same_float32_pcm_without_normalization(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path, sample_size=1)
    audio_path = tmp_path / "synthetic.wav"
    time = np.arange(800, dtype=np.float64) / 8_000
    stereo = np.column_stack(
        (
            0.1 * np.sin(2.0 * np.pi * 180.0 * time),
            0.2 * np.sin(2.0 * np.pi * 180.0 * time),
        )
    )
    sf.write(audio_path, stereo, 8_000, subtype="FLOAT")

    result = MODULE._decode_selected_pcm(audio_path, configuration)

    assert result.samples.dtype == np.dtype(np.float32)
    assert result.samples.ndim == 1
    assert result.samples.flags.c_contiguous
    assert result.samples.size == 1_600
    assert result.was_resampled
    assert result.was_multichannel
    assert float(np.max(np.abs(result.samples))) < 0.3


def test_decode_rejects_duration_resource_bound_without_echoing_path(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path, sample_size=1)
    configuration = replace(configuration, maximum_duration_seconds=0.01)
    audio_path = tmp_path / "private-name.wav"
    sf.write(audio_path, np.zeros(1_600, dtype=np.float32), 16_000)

    with pytest.raises(
        MODULE.PcmDecodeError,
        match="source_duration_resource_bound",
    ) as captured:
        MODULE._decode_selected_pcm(audio_path, configuration)

    assert "private-name" not in str(captured.value)


def test_worker_payload_parser_rejects_unknown_failure_code(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path, sample_size=1)

    with pytest.raises(MODULE.ValidationF0SmokeError, match="failure code"):
        MODULE._parse_worker_outcome(
            {
                "backend": "parselmouth",
                "status": "error",
                "failure_code": "contains-private-details",
                "network_socket_attempt_count": 0,
            },
            backend="parselmouth",
            configuration=configuration,
        )
