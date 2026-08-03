#!/usr/bin/env python3
"""Run a bounded CPU-only actual-audio F0 backend agreement diagnostic.

The parent scans only restricted metadata from a pinned modeling manifest,
joins the official Validation pronunciation inventory, and fixes a project
``dev`` sample before opening audio.  It then verifies source PCM identity,
decodes and resamples each fixed selection, and passes an exact private
float32 NPY to isolated Parselmouth and torchcrepe workers.  Worker contours
travel only through captured subprocess pipes.  Only aggregate statistics are
published.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import stat
import subprocess
import tempfile
from typing import Any, cast
import wave

import numpy as np
from numpy.typing import NDArray
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.signal import resample_poly  # type: ignore[import-untyped]
import soundfile as sf  # type: ignore[import-untyped]

from voice_coach.data.wav_qc import pcm_sha256 as source_pcm_sha256
from voice_coach.evaluation.pitch_contour_agreement import (
    PitchContourAgreementError,
    PitchContourAgreementResult,
    PitchContourSeries,
    PitchEndpointPolicy,
    PitchInterpolationPolicy,
    PitchUnvoicedPolicy,
    evaluate_pitch_contour_agreement,
)
from voice_coach.experiments.actual_f0_agreement_smoke import (
    ActualF0AgreementSmokeError,
    FIXED_DATASET_ID,
    FIXED_OFFICIAL_PARTITION,
    FIXED_PROJECT_SPLIT,
    FIXED_SOURCE_KIND,
    MAXIMUM_SAMPLE_SIZE,
    MODELING_MANIFEST_COLUMNS,
    ModelingManifestMetadataRow,
    REPORT_SCHEMA_VERSION,
    SELECTION_POLICY_ID,
    aggregate_pitch_agreements,
    attach_payload_sha256,
    select_project_dev_validation_audio,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
WORKER_PATH = WORKSPACE_ROOT / "scripts" / "run_phase1_f0_backend_worker.py"
IMPLEMENTATION_PATHS: dict[str, Path] = {
    "runner": Path(__file__).resolve(),
    "worker": WORKER_PATH,
    "smoke_module": (
        WORKSPACE_ROOT
        / "src"
        / "voice_coach"
        / "experiments"
        / "actual_f0_agreement_smoke.py"
    ),
    "agreement_source": (
        WORKSPACE_ROOT
        / "src"
        / "voice_coach"
        / "evaluation"
        / "pitch_contour_agreement.py"
    ),
    "parselmouth_adapter_source": (
        WORKSPACE_ROOT / "src" / "voice_coach" / "features" / "parselmouth_pitch.py"
    ),
    "torchcrepe_adapter_source": (
        WORKSPACE_ROOT / "src" / "voice_coach" / "features" / "torchcrepe_pitch.py"
    ),
    "source_pcm_hash_source": (
        WORKSPACE_ROOT / "src" / "voice_coach" / "data" / "wav_qc.py"
    ),
    "scorer_environment_lock": (
        WORKSPACE_ROOT / "requirements" / "scorer-lock-linux-aarch64-cu130.txt"
    ),
    "parselmouth_environment_lock": (
        WORKSPACE_ROOT / "requirements" / "paper-acoustic-tools-linux-aarch64.lock.txt"
    ),
    "torchcrepe_environment_lock": (
        WORKSPACE_ROOT / "requirements" / "paper-torchcrepe-linux-aarch64.lock.txt"
    ),
}
EXPECTED_PARENT_ENVIRONMENT = {
    "python": "3.12.3",
    "numpy": "2.5.1",
    "pyarrow": "25.0.0",
    "scipy": "1.18.0",
    "soundfile": "0.14.0",
}
EXPECTED_PARSELMOUTH_ENVIRONMENT = {
    "python": "3.12.3",
    "numpy": "2.5.1",
    "praat-parselmouth": "0.4.7",
}
EXPECTED_TORCHCREPE_ENVIRONMENT = {
    "python": "3.12.3",
    "numpy": "2.4.6",
    "torch": "2.11.0+cpu",
    "torchcrepe": "0.0.24",
}
DOWNMIX_POLICY = "mean_channels_float64_then_float32_v1"
RESAMPLE_POLICY = "scipy_resample_poly_gcd_float64_then_float32_v1"
COMMON_GRID_POLICY = "torchcrepe_native_grid_trim_to_shared_support_v1"
REFERENCE_ROLE = "parselmouth_reference_for_error_sign_only"
WORKER_OUTPUT_MAX_BYTES = 16 * 1024 * 1024


class ValidationF0SmokeError(ValueError):
    """Raised for a fail-closed orchestration or provenance violation."""


class PcmDecodeError(ValueError):
    """A generic, identifier-free selected-source decoding failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SmokeConfiguration:
    """Caller-explicit paths, scientific policies, bounds, and source pins."""

    dataset_root: Path
    modeling_manifest_path: Path
    expected_modeling_manifest_sha256: str
    output_path: Path
    private_temp_root: Path
    parselmouth_python: Path
    torchcrepe_python: Path
    expected_source_sha256: tuple[tuple[str, str], ...]
    seed: int
    sample_size: int
    target_sample_rate_hz: int
    maximum_duration_seconds: float
    maximum_channel_count: int
    downmix_policy: str
    resample_policy: str
    parselmouth_time_step_seconds: float
    pitch_floor_hz: float
    pitch_ceiling_hz: float
    parselmouth_unvoiced_policy: str
    torchcrepe_hop_length_samples: int
    torchcrepe_model: str
    torchcrepe_decoder: str
    torchcrepe_batch_size_frames: int
    torchcrepe_pad: bool
    torchcrepe_periodicity_threshold: float
    torchcrepe_silence_threshold_dbfs: float
    numpy_random_seed: int
    torch_random_seed: int
    torchcrepe_unvoiced_policy: str
    common_grid_policy: str
    interpolation_policy: str
    endpoint_policy: str
    agreement_unvoiced_policy: str
    octave_tolerance_cents: float
    maximum_agreement_frame_count: int
    reference_role: str
    worker_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class DecodedPcm:
    """Private exact PCM plus non-identifying operational flags."""

    samples: NDArray[np.float32]
    was_resampled: bool
    was_multichannel: bool


@dataclass(frozen=True, slots=True)
class WorkerContour:
    """Private worker-pipe contour; never serialized into the artifact."""

    backend: str
    environment: dict[str, str]
    pcm_sha256: str
    time_seconds: tuple[float, ...]
    f0_hz: tuple[float, ...]
    backend_frame_count: int
    voiced_frame_count: int
    unvoiced_frame_count: int
    network_socket_attempt_count: int


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    """Success or an identifier-free worker failure for one fixed selection."""

    backend: str
    contour: WorkerContour | None
    failure_code: str | None
    network_socket_attempt_count: int


WorkerProbe = Callable[[str, SmokeConfiguration], dict[str, object]]
WorkerExecute = Callable[[str, Path, SmokeConfiguration], WorkerOutcome]
PcmDecoder = Callable[[Path, SmokeConfiguration], DecodedPcm]
ManifestMetadataReader = Callable[
    [Path],
    Iterator[ModelingManifestMetadataRow],
]
PcmIntegrityVerifier = Callable[[Path, str], bool]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        raise ValidationF0SmokeError(
            "a required provenance source could not be read"
        ) from None
    return digest.hexdigest()


def _sha256_pcm(samples: NDArray[np.float32]) -> str:
    canonical = np.asarray(samples, dtype="<f4", order="C")
    return hashlib.sha256(canonical.tobytes(order="C")).hexdigest()


def _lowercase_sha256(value: object, *, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValidationF0SmokeError(f"{field_name} must be a lowercase SHA-256 digest")
    return cast(str, value)


def _positive_integer(
    value: object,
    *,
    field_name: str,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise ValidationF0SmokeError(f"{field_name} must be a positive integer")
    normalized = cast(int, value)
    if normalized <= 0 or (maximum is not None and normalized > maximum):
        raise ValidationF0SmokeError(
            f"{field_name} is outside the supported resource bound"
        )
    return normalized


def _finite_real(
    value: object,
    *,
    field_name: str,
    positive: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationF0SmokeError(f"{field_name} must be finite")
    normalized = float(value)
    if not math.isfinite(normalized) or (positive and normalized <= 0.0):
        raise ValidationF0SmokeError(f"{field_name} must be finite")
    return normalized


def _real_directory(path: object, *, field_name: str) -> Path:
    if not isinstance(path, Path):
        raise ValidationF0SmokeError(f"{field_name} must be a Path")
    candidate = cast(Path, path)
    if not candidate.is_absolute():
        raise ValidationF0SmokeError(f"{field_name} must be absolute")
    try:
        information = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValidationF0SmokeError(f"{field_name} is unavailable") from None
    if (
        candidate.is_symlink()
        or resolved != candidate
        or not stat.S_ISDIR(information.st_mode)
    ):
        raise ValidationF0SmokeError(f"{field_name} must be a real directory")
    return candidate


def _real_regular_file(path: object, *, field_name: str) -> Path:
    if not isinstance(path, Path):
        raise ValidationF0SmokeError(f"{field_name} must be a Path")
    candidate = cast(Path, path)
    if not candidate.is_absolute():
        raise ValidationF0SmokeError(f"{field_name} must be absolute")
    try:
        information = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValidationF0SmokeError(f"{field_name} is unavailable") from None
    if (
        candidate.is_symlink()
        or resolved != candidate
        or not stat.S_ISREG(information.st_mode)
    ):
        raise ValidationF0SmokeError(f"{field_name} must be a real regular file")
    return candidate


def _python_executable(path: object, *, field_name: str) -> Path:
    if not isinstance(path, Path):
        raise ValidationF0SmokeError(f"{field_name} must be a Path")
    candidate = cast(Path, path)
    if not candidate.is_absolute():
        raise ValidationF0SmokeError(f"{field_name} must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValidationF0SmokeError(f"{field_name} is unavailable") from None
    if not resolved.is_file() or not os.access(candidate, os.X_OK):
        raise ValidationF0SmokeError(f"{field_name} must be executable")
    return candidate


def _validate_output_path(path: object) -> Path:
    if not isinstance(path, Path):
        raise ValidationF0SmokeError("output_path must be a Path")
    candidate = cast(Path, path)
    if not candidate.is_absolute() or candidate.name in {"", ".", ".."}:
        raise ValidationF0SmokeError("output_path must be an absolute artifact file")
    if candidate.exists() and candidate.is_symlink():
        raise ValidationF0SmokeError("output_path must not be a symlink")
    _real_directory(candidate.parent, field_name="output_parent")
    return candidate


def _validate_configuration(value: object) -> SmokeConfiguration:
    if type(value) is not SmokeConfiguration:
        raise ValidationF0SmokeError(
            "configuration must be an exact SmokeConfiguration"
        )
    configuration = cast(SmokeConfiguration, value)
    dataset_root = _real_directory(
        configuration.dataset_root,
        field_name="dataset_root",
    )
    modeling_manifest_path = _real_regular_file(
        configuration.modeling_manifest_path,
        field_name="modeling_manifest_path",
    )
    expected_modeling_manifest_sha256 = _lowercase_sha256(
        configuration.expected_modeling_manifest_sha256,
        field_name="expected_modeling_manifest_sha256",
    )
    output_path = _validate_output_path(configuration.output_path)
    private_temp_root = _real_directory(
        configuration.private_temp_root,
        field_name="private_temp_root",
    )
    parselmouth_python = _python_executable(
        configuration.parselmouth_python,
        field_name="parselmouth_python",
    )
    torchcrepe_python = _python_executable(
        configuration.torchcrepe_python,
        field_name="torchcrepe_python",
    )
    expected_sources = dict(configuration.expected_source_sha256)
    if len(expected_sources) != len(configuration.expected_source_sha256):
        raise ValidationF0SmokeError("expected source roles must be unique")
    if set(expected_sources) != set(IMPLEMENTATION_PATHS):
        raise ValidationF0SmokeError("expected source roles are incomplete")
    normalized_sources = tuple(
        (
            role,
            _lowercase_sha256(
                expected_sources[role],
                field_name=f"expected_source_sha256.{role}",
            ),
        )
        for role in sorted(expected_sources)
    )

    seed = _positive_integer(
        configuration.seed,
        field_name="seed",
        maximum=2**63 - 1,
    )
    sample_size = _positive_integer(
        configuration.sample_size,
        field_name="sample_size",
        maximum=MAXIMUM_SAMPLE_SIZE,
    )
    if configuration.target_sample_rate_hz != 16_000:
        raise ValidationF0SmokeError("target_sample_rate_hz must be exactly 16000")
    maximum_duration_seconds = _finite_real(
        configuration.maximum_duration_seconds,
        field_name="maximum_duration_seconds",
        positive=True,
    )
    if maximum_duration_seconds > 30.0:
        raise ValidationF0SmokeError(
            "maximum_duration_seconds exceeds the bounded smoke contract"
        )
    maximum_channel_count = _positive_integer(
        configuration.maximum_channel_count,
        field_name="maximum_channel_count",
        maximum=16,
    )
    if configuration.downmix_policy != DOWNMIX_POLICY:
        raise ValidationF0SmokeError("downmix_policy differs from the fixed contract")
    if configuration.resample_policy != RESAMPLE_POLICY:
        raise ValidationF0SmokeError("resample_policy differs from the fixed contract")

    parselmouth_time_step = _finite_real(
        configuration.parselmouth_time_step_seconds,
        field_name="parselmouth_time_step_seconds",
        positive=True,
    )
    pitch_floor = _finite_real(
        configuration.pitch_floor_hz,
        field_name="pitch_floor_hz",
        positive=True,
    )
    pitch_ceiling = _finite_real(
        configuration.pitch_ceiling_hz,
        field_name="pitch_ceiling_hz",
        positive=True,
    )
    if pitch_ceiling <= pitch_floor or pitch_ceiling > 2_006.0:
        raise ValidationF0SmokeError("pitch range is invalid")
    if configuration.parselmouth_unvoiced_policy != "keep_zero":
        raise ValidationF0SmokeError(
            "parselmouth_unvoiced_policy must be explicitly keep_zero"
        )

    hop_length = _positive_integer(
        configuration.torchcrepe_hop_length_samples,
        field_name="torchcrepe_hop_length_samples",
    )
    if configuration.torchcrepe_model not in {"tiny", "full"}:
        raise ValidationF0SmokeError("torchcrepe_model is unsupported")
    if configuration.torchcrepe_decoder not in {
        "viterbi",
        "weighted_argmax",
        "argmax",
    }:
        raise ValidationF0SmokeError("torchcrepe_decoder is unsupported")
    batch_size = _positive_integer(
        configuration.torchcrepe_batch_size_frames,
        field_name="torchcrepe_batch_size_frames",
    )
    if type(configuration.torchcrepe_pad) is not bool:
        raise ValidationF0SmokeError("torchcrepe_pad must be an explicit boolean")
    periodicity_threshold = _finite_real(
        configuration.torchcrepe_periodicity_threshold,
        field_name="torchcrepe_periodicity_threshold",
    )
    if not 0.0 <= periodicity_threshold <= 1.0:
        raise ValidationF0SmokeError("torchcrepe_periodicity_threshold is invalid")
    silence_threshold = _finite_real(
        configuration.torchcrepe_silence_threshold_dbfs,
        field_name="torchcrepe_silence_threshold_dbfs",
    )
    numpy_seed = _positive_integer(
        configuration.numpy_random_seed,
        field_name="numpy_random_seed",
        maximum=2**32 - 1,
    )
    torch_seed = _positive_integer(
        configuration.torch_random_seed,
        field_name="torch_random_seed",
        maximum=2**63 - 1,
    )
    if configuration.torchcrepe_unvoiced_policy != "keep_zero":
        raise ValidationF0SmokeError(
            "torchcrepe_unvoiced_policy must be explicitly keep_zero"
        )

    if configuration.common_grid_policy != COMMON_GRID_POLICY:
        raise ValidationF0SmokeError("common_grid_policy differs from the contract")
    if configuration.interpolation_policy != PitchInterpolationPolicy.LINEAR.value:
        raise ValidationF0SmokeError("interpolation_policy must be explicitly linear")
    if (
        configuration.endpoint_policy
        != PitchEndpointPolicy.TRIM_TO_SHARED_SUPPORT.value
    ):
        raise ValidationF0SmokeError(
            "endpoint_policy must be explicitly trim_to_shared_support"
        )
    if (
        configuration.agreement_unvoiced_policy
        != PitchUnvoicedPolicy.PRESERVE_GAPS.value
    ):
        raise ValidationF0SmokeError(
            "agreement_unvoiced_policy must be explicitly preserve_gaps"
        )
    octave_tolerance = _finite_real(
        configuration.octave_tolerance_cents,
        field_name="octave_tolerance_cents",
    )
    if not 0.0 <= octave_tolerance < 600.0:
        raise ValidationF0SmokeError("octave_tolerance_cents is invalid")
    maximum_frames = _positive_integer(
        configuration.maximum_agreement_frame_count,
        field_name="maximum_agreement_frame_count",
    )
    if configuration.reference_role != REFERENCE_ROLE:
        raise ValidationF0SmokeError("reference_role differs from the contract")
    worker_timeout = _positive_integer(
        configuration.worker_timeout_seconds,
        field_name="worker_timeout_seconds",
        maximum=600,
    )
    return SmokeConfiguration(
        dataset_root=dataset_root,
        modeling_manifest_path=modeling_manifest_path,
        expected_modeling_manifest_sha256=expected_modeling_manifest_sha256,
        output_path=output_path,
        private_temp_root=private_temp_root,
        parselmouth_python=parselmouth_python,
        torchcrepe_python=torchcrepe_python,
        expected_source_sha256=normalized_sources,
        seed=seed,
        sample_size=sample_size,
        target_sample_rate_hz=16_000,
        maximum_duration_seconds=maximum_duration_seconds,
        maximum_channel_count=maximum_channel_count,
        downmix_policy=DOWNMIX_POLICY,
        resample_policy=RESAMPLE_POLICY,
        parselmouth_time_step_seconds=parselmouth_time_step,
        pitch_floor_hz=pitch_floor,
        pitch_ceiling_hz=pitch_ceiling,
        parselmouth_unvoiced_policy="keep_zero",
        torchcrepe_hop_length_samples=hop_length,
        torchcrepe_model=configuration.torchcrepe_model,
        torchcrepe_decoder=configuration.torchcrepe_decoder,
        torchcrepe_batch_size_frames=batch_size,
        torchcrepe_pad=configuration.torchcrepe_pad,
        torchcrepe_periodicity_threshold=periodicity_threshold,
        torchcrepe_silence_threshold_dbfs=silence_threshold,
        numpy_random_seed=numpy_seed,
        torch_random_seed=torch_seed,
        torchcrepe_unvoiced_policy="keep_zero",
        common_grid_policy=COMMON_GRID_POLICY,
        interpolation_policy=PitchInterpolationPolicy.LINEAR.value,
        endpoint_policy=PitchEndpointPolicy.TRIM_TO_SHARED_SUPPORT.value,
        agreement_unvoiced_policy=PitchUnvoicedPolicy.PRESERVE_GAPS.value,
        octave_tolerance_cents=octave_tolerance,
        maximum_agreement_frame_count=maximum_frames,
        reference_role=REFERENCE_ROLE,
        worker_timeout_seconds=worker_timeout,
    )


def _verify_source_pins(
    configuration: SmokeConfiguration,
) -> dict[str, str]:
    expected = dict(configuration.expected_source_sha256)
    observed = {role: _sha256_file(path) for role, path in IMPLEMENTATION_PATHS.items()}
    if observed != expected:
        raise ValidationF0SmokeError("implementation source pin mismatch")
    return {role: observed[role] for role in sorted(observed)}


def _verify_modeling_manifest_pin(configuration: SmokeConfiguration) -> str:
    observed = _sha256_file(configuration.modeling_manifest_path)
    if observed != configuration.expected_modeling_manifest_sha256:
        raise ValidationF0SmokeError("modeling manifest source pin mismatch")
    return observed


def _iter_modeling_manifest_metadata(
    path: Path,
) -> Iterator[ModelingManifestMetadataRow]:
    """Read only the four restricted fields required for project-role selection."""

    expected_types = {
        "audio_path": pa.string(),
        "official_partitions": pa.list_(pa.string()),
        "pcm_sha256": pa.string(),
        "split": pa.string(),
    }
    try:
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
        if any(
            field_name not in schema.names
            or schema.field(field_name).type != expected_type
            for field_name, expected_type in expected_types.items()
        ):
            raise ValidationF0SmokeError(
                "modeling manifest restricted metadata schema mismatch"
            )
        for batch in parquet.iter_batches(
            batch_size=4_096,
            columns=list(MODELING_MANIFEST_COLUMNS),
            use_threads=False,
        ):
            columns = batch.to_pydict()
            values = zip(
                columns["audio_path"],
                columns["official_partitions"],
                columns["pcm_sha256"],
                columns["split"],
                strict=True,
            )
            for audio_path, official_partitions, pcm_sha256, split in values:
                if type(official_partitions) is not list:
                    raise ValidationF0SmokeError(
                        "modeling manifest restricted metadata row mismatch"
                    )
                yield ModelingManifestMetadataRow(
                    audio_path=audio_path,
                    official_partitions=tuple(official_partitions),
                    pcm_sha256=pcm_sha256,
                    split=split,
                )
    except ValidationF0SmokeError:
        raise
    except (OSError, TypeError, ValueError, pa.ArrowException):
        raise ValidationF0SmokeError(
            "modeling manifest restricted metadata scan failed"
        ) from None


def _default_pcm_integrity_verifier(
    source_path: Path,
    expected_pcm_sha256: str,
) -> bool:
    try:
        observed = source_pcm_sha256(source_path)
    except (EOFError, OSError, ValueError, wave.Error):
        return False
    return observed == expected_pcm_sha256


def _environment_versions(package_names: tuple[str, ...]) -> dict[str, str]:
    versions = {"python": platform.python_version()}
    try:
        versions.update({name: metadata.version(name) for name in package_names})
    except metadata.PackageNotFoundError:
        raise ValidationF0SmokeError(
            "a pinned environment package is missing"
        ) from None
    return versions


def _parent_environment() -> dict[str, str]:
    observed = _environment_versions(("numpy", "pyarrow", "scipy", "soundfile"))
    if observed != EXPECTED_PARENT_ENVIRONMENT:
        raise ValidationF0SmokeError("parent environment differs from its pin")
    return observed


def _decode_selected_pcm(
    source_path: Path,
    configuration: SmokeConfiguration,
) -> DecodedPcm:
    try:
        information = sf.info(str(source_path))
    except (OSError, RuntimeError, TypeError, ValueError):
        raise PcmDecodeError("source_metadata_unavailable") from None
    if (
        information.samplerate <= 0
        or information.frames <= 0
        or information.channels <= 0
    ):
        raise PcmDecodeError("invalid_source_audio_metadata")
    if information.channels > configuration.maximum_channel_count:
        raise PcmDecodeError("source_channel_resource_bound")
    source_duration = information.frames / information.samplerate
    if source_duration > configuration.maximum_duration_seconds:
        raise PcmDecodeError("source_duration_resource_bound")

    try:
        decoded, sample_rate = sf.read(
            str(source_path),
            dtype="float32",
            always_2d=True,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        raise PcmDecodeError("source_decode_failure") from None
    if (
        decoded.ndim != 2
        or decoded.shape[0] == 0
        or decoded.shape[1] != information.channels
        or sample_rate != information.samplerate
        or not np.isfinite(decoded).all()
    ):
        raise PcmDecodeError("decoded_pcm_contract_failure")

    mono_float64 = np.mean(
        np.asarray(decoded, dtype=np.float64),
        axis=1,
        dtype=np.float64,
    )
    was_resampled = sample_rate != configuration.target_sample_rate_hz
    if was_resampled:
        common_divisor = math.gcd(sample_rate, configuration.target_sample_rate_hz)
        up = configuration.target_sample_rate_hz // common_divisor
        down = sample_rate // common_divisor
        try:
            resampled = resample_poly(
                mono_float64,
                up,
                down,
                padtype="constant",
            )
        except (MemoryError, TypeError, ValueError):
            raise PcmDecodeError("resample_failure") from None
        pcm = np.array(resampled, dtype=np.float32, order="C", copy=True)
    else:
        pcm = np.array(mono_float64, dtype=np.float32, order="C", copy=True)

    maximum_samples = math.ceil(
        configuration.maximum_duration_seconds * configuration.target_sample_rate_hz
    )
    if (
        type(pcm) is not np.ndarray
        or pcm.ndim != 1
        or pcm.size == 0
        or pcm.size > maximum_samples
        or pcm.dtype != np.dtype(np.float32)
        or not pcm.flags.c_contiguous
        or not np.isfinite(pcm).all()
    ):
        raise PcmDecodeError("derived_pcm_contract_failure")
    if np.any(np.abs(pcm) > 1.0):
        raise PcmDecodeError("derived_pcm_outside_closed_unit_interval")
    return DecodedPcm(
        samples=pcm,
        was_resampled=was_resampled,
        was_multichannel=information.channels > 1,
    )


@contextmanager
def _private_pcm_npy(
    samples: NDArray[np.float32],
    private_temp_root: Path,
) -> Iterator[Path]:
    temporary_directory = tempfile.mkdtemp(
        prefix=".phase1-f0-",
        dir=private_temp_root,
    )
    directory_path = Path(temporary_directory)
    os.chmod(directory_path, 0o700)
    npy_path = directory_path / "pcm.npy"
    descriptor = os.open(
        npy_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            np.save(handle, samples, allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(npy_path, 0o600)
        if stat.S_IMODE(npy_path.stat().st_mode) != 0o600:
            raise ValidationF0SmokeError("private PCM mode is not 0600")
        yield npy_path
    finally:
        try:
            if npy_path.exists():
                npy_path.unlink()
            directory_path.rmdir()
        except OSError:
            raise ValidationF0SmokeError("private PCM cleanup failed") from None


def _worker_environment(configuration: SmokeConfiguration) -> dict[str, str]:
    environment = dict(os.environ)
    existing_python_path = environment.get("PYTHONPATH")
    source_path = str(WORKSPACE_ROOT / "src")
    environment["PYTHONPATH"] = (
        source_path
        if not existing_python_path
        else source_path + os.pathsep + existing_python_path
    )
    environment.update(
        {
            "CUDA_VISIBLE_DEVICES": "",
            "HIP_VISIBLE_DEVICES": "",
            "ROCR_VISIBLE_DEVICES": "",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "PYTHONNOUSERSITE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    del configuration
    return environment


def _worker_command(
    backend: str,
    configuration: SmokeConfiguration,
    *,
    pcm_npy: Path | None,
) -> tuple[Path, list[str]]:
    if backend == "parselmouth":
        executable = configuration.parselmouth_python
    elif backend == "torchcrepe":
        executable = configuration.torchcrepe_python
    else:
        raise ValidationF0SmokeError("worker backend is unsupported")
    command = [
        str(executable),
        str(WORKER_PATH),
        "--backend",
        backend,
    ]
    if pcm_npy is None:
        command.append("--probe-only")
        return executable, command
    command.extend(
        [
            "--pcm-npy",
            str(pcm_npy),
            "--sample-rate-hz",
            str(configuration.target_sample_rate_hz),
            "--pitch-floor-hz",
            repr(configuration.pitch_floor_hz),
            "--pitch-ceiling-hz",
            repr(configuration.pitch_ceiling_hz),
            "--parselmouth-time-step-seconds",
            repr(configuration.parselmouth_time_step_seconds),
            "--parselmouth-unvoiced-policy",
            configuration.parselmouth_unvoiced_policy,
            "--torchcrepe-hop-length-samples",
            str(configuration.torchcrepe_hop_length_samples),
            "--torchcrepe-model",
            configuration.torchcrepe_model,
            "--torchcrepe-decoder",
            configuration.torchcrepe_decoder,
            "--torchcrepe-batch-size-frames",
            str(configuration.torchcrepe_batch_size_frames),
            "--torchcrepe-pad",
            "true" if configuration.torchcrepe_pad else "false",
            "--torchcrepe-periodicity-threshold",
            repr(configuration.torchcrepe_periodicity_threshold),
            "--torchcrepe-silence-threshold-dbfs",
            repr(configuration.torchcrepe_silence_threshold_dbfs),
            "--numpy-random-seed",
            str(configuration.numpy_random_seed),
            "--torch-random-seed",
            str(configuration.torch_random_seed),
            "--torchcrepe-unvoiced-policy",
            configuration.torchcrepe_unvoiced_policy,
        ]
    )
    return executable, command


def _call_worker(
    backend: str,
    configuration: SmokeConfiguration,
    *,
    pcm_npy: Path | None,
) -> dict[str, object]:
    _, command = _worker_command(backend, configuration, pcm_npy=pcm_npy)
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            env=_worker_environment(configuration),
            timeout=configuration.worker_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValidationF0SmokeError("isolated worker process failed") from None
    if (
        completed.returncode != 0
        or len(completed.stdout) == 0
        or len(completed.stdout) > WORKER_OUTPUT_MAX_BYTES
    ):
        raise ValidationF0SmokeError("isolated worker process failed")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationF0SmokeError("isolated worker returned invalid JSON") from None
    if type(payload) is not dict:
        raise ValidationF0SmokeError("isolated worker returned invalid JSON")
    return cast(dict[str, object], payload)


def _normalize_environment(value: object) -> dict[str, str]:
    if type(value) is not dict:
        raise ValidationF0SmokeError("worker environment is invalid")
    result: dict[str, str] = {}
    for key, item in cast(dict[object, object], value).items():
        if type(key) is not str or type(item) is not str:
            raise ValidationF0SmokeError("worker environment is invalid")
        result[cast(str, key)] = cast(str, item)
    return result


def _default_worker_probe(
    backend: str,
    configuration: SmokeConfiguration,
) -> dict[str, object]:
    payload = _call_worker(backend, configuration, pcm_npy=None)
    if (
        payload.get("status") != "ok"
        or payload.get("backend") != backend
        or payload.get("network_socket_attempt_count") != 0
    ):
        raise ValidationF0SmokeError("isolated worker environment probe failed")
    environment = _normalize_environment(payload.get("environment"))
    expected = (
        EXPECTED_PARSELMOUTH_ENVIRONMENT
        if backend == "parselmouth"
        else EXPECTED_TORCHCREPE_ENVIRONMENT
    )
    if environment != expected:
        raise ValidationF0SmokeError("isolated worker environment differs from pin")
    if backend == "torchcrepe" and (
        payload.get("device") != "cpu"
        or payload.get("cuda_available") is not False
        or payload.get("torch_cuda_version") is not None
    ):
        raise ValidationF0SmokeError("torchcrepe worker is not CPU-only")
    return payload


def _numeric_tuple(
    value: object,
    *,
    field_name: str,
    maximum_count: int,
) -> tuple[float, ...]:
    if type(value) is not list or not value or len(value) > maximum_count:
        raise ValidationF0SmokeError(f"{field_name} worker contour is invalid")
    result: list[float] = []
    for item in cast(list[object], value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValidationF0SmokeError(f"{field_name} worker contour is invalid")
        normalized = float(item)
        if not math.isfinite(normalized):
            raise ValidationF0SmokeError(f"{field_name} worker contour is invalid")
        result.append(normalized)
    return tuple(result)


def _parse_worker_outcome(
    payload: dict[str, object],
    *,
    backend: str,
    configuration: SmokeConfiguration,
) -> WorkerOutcome:
    if payload.get("backend") != backend:
        raise ValidationF0SmokeError("isolated worker backend mismatch")
    socket_attempts = payload.get("network_socket_attempt_count")
    if isinstance(socket_attempts, bool) or type(socket_attempts) is not int:
        raise ValidationF0SmokeError("worker socket counter is invalid")
    attempt_count = cast(int, socket_attempts)
    if attempt_count < 0:
        raise ValidationF0SmokeError("worker socket counter is invalid")
    if payload.get("status") == "error":
        failure_code = payload.get("failure_code")
        if failure_code not in {
            "private_pcm_contract_failure",
            "parselmouth_extraction_failure",
            "parselmouth_backend_call_failure",
            "parselmouth_no_voiced_failure",
            "parselmouth_output_contract_failure",
            "torchcrepe_extraction_failure",
            "torchcrepe_backend_availability_failure",
            "torchcrepe_backend_call_failure",
            "torchcrepe_backend_matrix_shape_failure",
            "torchcrepe_input_contract_failure",
            "torchcrepe_invalid_result_failure",
            "torchcrepe_no_voiced_failure",
            "torchcrepe_nonfinite_result_failure",
            "torchcrepe_output_contract_failure",
            "torchcrepe_output_shape_failure",
            "torchcrepe_parameter_contract_failure",
            "torchcrepe_periodicity_bound_failure",
            "torchcrepe_pitch_bound_failure",
            "torchcrepe_unexpected_attribute_error",
            "torchcrepe_unexpected_exception_type",
            "torchcrepe_unexpected_type_error",
            "worker_internal_failure",
        }:
            raise ValidationF0SmokeError("worker failure code is invalid")
        return WorkerOutcome(
            backend=backend,
            contour=None,
            failure_code=cast(str, failure_code),
            network_socket_attempt_count=attempt_count,
        )
    if payload.get("status") != "ok":
        raise ValidationF0SmokeError("worker status is invalid")
    environment = _normalize_environment(payload.get("environment"))
    expected_environment = (
        EXPECTED_PARSELMOUTH_ENVIRONMENT
        if backend == "parselmouth"
        else EXPECTED_TORCHCREPE_ENVIRONMENT
    )
    if environment != expected_environment:
        raise ValidationF0SmokeError("worker environment differs from probe pin")
    pcm_sha256 = _lowercase_sha256(
        payload.get("pcm_sha256"),
        field_name="worker.pcm_sha256",
    )
    times = _numeric_tuple(
        payload.get("time_seconds"),
        field_name="time_seconds",
        maximum_count=configuration.maximum_agreement_frame_count,
    )
    frequencies = _numeric_tuple(
        payload.get("f0_hz"),
        field_name="f0_hz",
        maximum_count=configuration.maximum_agreement_frame_count,
    )
    if len(times) != len(frequencies):
        raise ValidationF0SmokeError("worker contour frame counts differ")
    if any(value < 0.0 for value in times) or any(
        current <= previous for previous, current in zip(times, times[1:])
    ):
        raise ValidationF0SmokeError("worker time contour is invalid")
    if any(value < 0.0 for value in frequencies):
        raise ValidationF0SmokeError("worker F0 contour is invalid")

    count_values: dict[str, int] = {}
    for field_name in (
        "backend_frame_count",
        "voiced_frame_count",
        "unvoiced_frame_count",
    ):
        value = payload.get(field_name)
        if isinstance(value, bool) or type(value) is not int or cast(int, value) < 0:
            raise ValidationF0SmokeError("worker frame count is invalid")
        count_values[field_name] = cast(int, value)
    if (
        count_values["backend_frame_count"] != len(times)
        or count_values["voiced_frame_count"] + count_values["unvoiced_frame_count"]
        != count_values["backend_frame_count"]
    ):
        raise ValidationF0SmokeError("worker frame count is inconsistent")
    return WorkerOutcome(
        backend=backend,
        contour=WorkerContour(
            backend=backend,
            environment=environment,
            pcm_sha256=pcm_sha256,
            time_seconds=times,
            f0_hz=frequencies,
            backend_frame_count=count_values["backend_frame_count"],
            voiced_frame_count=count_values["voiced_frame_count"],
            unvoiced_frame_count=count_values["unvoiced_frame_count"],
            network_socket_attempt_count=attempt_count,
        ),
        failure_code=None,
        network_socket_attempt_count=attempt_count,
    )


def _default_worker_execute(
    backend: str,
    pcm_npy: Path,
    configuration: SmokeConfiguration,
) -> WorkerOutcome:
    return _parse_worker_outcome(
        _call_worker(backend, configuration, pcm_npy=pcm_npy),
        backend=backend,
        configuration=configuration,
    )


def _agreement(
    parselmouth: WorkerContour,
    torchcrepe: WorkerContour,
    configuration: SmokeConfiguration,
) -> PitchContourAgreementResult:
    return evaluate_pitch_contour_agreement(
        PitchContourSeries(
            time_seconds=parselmouth.time_seconds,
            f0_hz=parselmouth.f0_hz,
        ),
        PitchContourSeries(
            time_seconds=torchcrepe.time_seconds,
            f0_hz=torchcrepe.f0_hz,
        ),
        torchcrepe.time_seconds,
        interpolation_policy=PitchInterpolationPolicy.LINEAR,
        endpoint_policy=PitchEndpointPolicy.TRIM_TO_SHARED_SUPPORT,
        unvoiced_policy=PitchUnvoicedPolicy.PRESERVE_GAPS,
        octave_tolerance_cents=configuration.octave_tolerance_cents,
        max_frame_count=configuration.maximum_agreement_frame_count,
    )


def _write_report_atomically(
    output_path: Path,
    report: dict[str, object],
) -> None:
    serialized = (
        json.dumps(
            report,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".part",
        dir=output_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
        os.chmod(output_path, 0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def run_validation_f0_smoke(
    configuration: SmokeConfiguration,
    *,
    worker_probe: WorkerProbe = _default_worker_probe,
    worker_execute: WorkerExecute = _default_worker_execute,
    decoder: PcmDecoder = _decode_selected_pcm,
    manifest_metadata_reader: ManifestMetadataReader = (
        _iter_modeling_manifest_metadata
    ),
    pcm_integrity_verifier: PcmIntegrityVerifier = (_default_pcm_integrity_verifier),
) -> dict[str, object]:
    """Execute fixed selections and publish only aggregate diagnostic evidence."""

    validated = _validate_configuration(configuration)
    source_sha256 = _verify_source_pins(validated)
    modeling_manifest_sha256 = _verify_modeling_manifest_pin(validated)
    inventory = select_project_dev_validation_audio(
        validated.dataset_root,
        manifest_metadata_rows=manifest_metadata_reader(
            validated.modeling_manifest_path
        ),
        manifest_sha256=modeling_manifest_sha256,
        seed=validated.seed,
        sample_size=validated.sample_size,
    )
    if _verify_modeling_manifest_pin(validated) != modeling_manifest_sha256:
        raise ValidationF0SmokeError(
            "modeling manifest changed during initial metadata scan"
        )
    parent_environment = _parent_environment()
    parselmouth_probe = worker_probe("parselmouth", validated)
    torchcrepe_probe = worker_probe("torchcrepe", validated)

    counters: Counter[str] = Counter()
    failure_conditions: Counter[str] = Counter()
    primary_failures: Counter[str] = Counter()
    agreements: list[PitchContourAgreementResult] = []

    for selected_source in inventory.selected_sources:
        counters["selected_before_audio_read_count"] += 1
        counters["source_pcm_integrity_read_attempt_count"] += 1
        counters["project_dev_audio_read_item_count"] += 1
        integrity_matches = pcm_integrity_verifier(
            selected_source.path,
            selected_source.expected_pcm_sha256,
        )
        if type(integrity_matches) is not bool:
            raise ValidationF0SmokeError(
                "source PCM integrity verifier returned an invalid contract"
            )
        if not integrity_matches:
            failure_conditions["source_pcm_integrity_mismatch"] += 1
            primary_failures["source_pcm_integrity_mismatch"] += 1
            continue
        counters["source_pcm_integrity_match_count"] += 1
        counters["source_metadata_probe_attempt_count"] += 1
        try:
            decoded = decoder(selected_source.path, validated)
        except PcmDecodeError as exc:
            failure_conditions[exc.code] += 1
            primary_failures[exc.code] += 1
            continue
        counters["decode_completed_count"] += 1
        counters["resampled_count"] += int(decoded.was_resampled)
        counters["multichannel_downmix_count"] += int(decoded.was_multichannel)
        parent_pcm_sha256 = _sha256_pcm(decoded.samples)

        with _private_pcm_npy(decoded.samples, validated.private_temp_root) as pcm_npy:
            counters["private_npy_created_count"] += 1
            outcomes = {
                backend: worker_execute(backend, pcm_npy, validated)
                for backend in ("parselmouth", "torchcrepe")
            }
        counters["private_npy_cleaned_count"] += 1

        for backend, outcome in outcomes.items():
            counters[f"{backend}_attempted_count"] += 1
            counters["network_socket_attempt_count"] += (
                outcome.network_socket_attempt_count
            )
            if outcome.contour is None:
                failure_code = outcome.failure_code or f"{backend}_worker_failure"
                failure_conditions[failure_code] += 1
            else:
                counters[f"{backend}_completed_count"] += 1

        parselmouth_outcome = outcomes["parselmouth"]
        torchcrepe_outcome = outcomes["torchcrepe"]
        if parselmouth_outcome.contour is None:
            primary_failures[
                parselmouth_outcome.failure_code or "parselmouth_worker_failure"
            ] += 1
            continue
        if torchcrepe_outcome.contour is None:
            primary_failures[
                torchcrepe_outcome.failure_code or "torchcrepe_worker_failure"
            ] += 1
            continue
        if not (
            parselmouth_outcome.contour.pcm_sha256
            == torchcrepe_outcome.contour.pcm_sha256
            == parent_pcm_sha256
        ):
            failure_conditions["cross_process_pcm_digest_mismatch"] += 1
            primary_failures["cross_process_pcm_digest_mismatch"] += 1
            continue
        counters["cross_process_pcm_digest_match_count"] += 1
        try:
            result = _agreement(
                parselmouth_outcome.contour,
                torchcrepe_outcome.contour,
                validated,
            )
        except PitchContourAgreementError:
            failure_conditions["agreement_contract_failure"] += 1
            primary_failures["agreement_contract_failure"] += 1
            continue
        agreements.append(result)
        counters["comparison_completed_count"] += 1

    modeling_manifest_sha256_at_end = _verify_modeling_manifest_pin(validated)
    if modeling_manifest_sha256_at_end != modeling_manifest_sha256:
        raise ValidationF0SmokeError("modeling manifest changed during run")
    inventory_at_end = select_project_dev_validation_audio(
        validated.dataset_root,
        manifest_metadata_rows=manifest_metadata_reader(
            validated.modeling_manifest_path
        ),
        manifest_sha256=modeling_manifest_sha256_at_end,
        seed=validated.seed,
        sample_size=validated.sample_size,
    )
    if _verify_modeling_manifest_pin(validated) != modeling_manifest_sha256:
        raise ValidationF0SmokeError(
            "modeling manifest changed during final metadata scan"
        )
    if inventory_at_end != inventory:
        raise ValidationF0SmokeError(
            "project dev filename inventory changed during run"
        )
    source_sha256_at_end = _verify_source_pins(validated)
    if source_sha256_at_end != source_sha256:
        raise ValidationF0SmokeError("implementation sources changed during run")

    aggregate = aggregate_pitch_agreements(tuple(agreements))
    completed_count = counters["comparison_completed_count"]
    passed = (
        completed_count == validated.sample_size
        and not failure_conditions
        and counters["network_socket_attempt_count"] == 0
    )
    payload: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": (
            "passed_bounded_technical_diagnostic"
            if passed
            else "incomplete_bounded_technical_diagnostic"
        ),
        "scope": {
            "dataset_id": FIXED_DATASET_ID,
            "official_partition": FIXED_OFFICIAL_PARTITION,
            "project_split": FIXED_PROJECT_SPLIT,
            "source_kind": FIXED_SOURCE_KIND,
            "sample_size": validated.sample_size,
            "accuracy_claim": False,
            "quality_claim": False,
            "learner_vs_native_claim": False,
            "phase1_m4_completion_claim": False,
        },
        "selection": {
            "policy_id": SELECTION_POLICY_ID,
            "seed": validated.seed,
            "population": (
                "official_validation_pronunciation_wav_filenames_joined_to_"
                "pinned_project_dev_metadata_before_audio_read"
            ),
            "official_filename_count": inventory.official_filename_count,
            "eligible_regular_file_count": inventory.eligible_regular_file_count,
            "rejected_inventory_entry_count": (
                inventory.rejected_inventory_entry_count
            ),
            "manifest_unique_inventory_join_count": (
                inventory.manifest_unique_inventory_join_count
            ),
            "manifest_duplicate_inventory_join_count": (
                inventory.manifest_duplicate_inventory_join_count
            ),
            "manifest_missing_inventory_join_count": (
                inventory.manifest_missing_inventory_join_count
            ),
            "official_partition_mismatch_count": (
                inventory.official_partition_mismatch_count
            ),
            "official_inventory_project_split_counts": dict(
                inventory.official_inventory_project_split_counts
            ),
            "project_dev_eligible_count": inventory.project_dev_eligible_count,
            "selected_project_split_counts": {
                FIXED_PROJECT_SPLIT: validated.sample_size
            },
            "sample_fixed_before_audio_read_count": validated.sample_size,
            "failure_backfill_enabled": False,
            "acoustic_threshold_used_for_selection": False,
            "official_inventory_digest_sha256": (
                inventory.official_inventory_digest_sha256
            ),
            "project_dev_inventory_digest_sha256": (
                inventory.project_dev_inventory_digest_sha256
            ),
            "selection_digest_sha256": inventory.selection_digest_sha256,
            "inventory_unchanged_at_end": True,
        },
        "manifest_metadata": {
            "restricted_columns": list(MODELING_MANIFEST_COLUMNS),
            "full_manifest_metadata_row_count_per_scan": (
                inventory.manifest_metadata_row_count
            ),
            "full_manifest_metadata_scan_count": 2,
            "full_manifest_metadata_rows_scanned_total": (
                inventory.manifest_metadata_row_count * 2
            ),
            "full_manifest_project_split_counts": dict(
                inventory.manifest_project_split_counts
            ),
            "audio_content_read_during_metadata_scan": False,
            "official_partition_used_as_project_split": False,
        },
        "parameters": {
            "target_sample_rate_hz": validated.target_sample_rate_hz,
            "maximum_duration_seconds": validated.maximum_duration_seconds,
            "maximum_channel_count": validated.maximum_channel_count,
            "downmix_policy": validated.downmix_policy,
            "resample_policy": validated.resample_policy,
            "parselmouth_time_step_seconds": (validated.parselmouth_time_step_seconds),
            "pitch_floor_hz": validated.pitch_floor_hz,
            "pitch_ceiling_hz": validated.pitch_ceiling_hz,
            "parselmouth_unvoiced_policy": (validated.parselmouth_unvoiced_policy),
            "torchcrepe_hop_length_samples": (validated.torchcrepe_hop_length_samples),
            "torchcrepe_model": validated.torchcrepe_model,
            "torchcrepe_decoder": validated.torchcrepe_decoder,
            "torchcrepe_batch_size_frames": (validated.torchcrepe_batch_size_frames),
            "torchcrepe_pad": validated.torchcrepe_pad,
            "torchcrepe_periodicity_threshold": (
                validated.torchcrepe_periodicity_threshold
            ),
            "torchcrepe_silence_threshold_dbfs": (
                validated.torchcrepe_silence_threshold_dbfs
            ),
            "numpy_random_seed": validated.numpy_random_seed,
            "torch_random_seed": validated.torch_random_seed,
            "torchcrepe_unvoiced_policy": validated.torchcrepe_unvoiced_policy,
            "common_grid_policy": validated.common_grid_policy,
            "interpolation_policy": validated.interpolation_policy,
            "endpoint_policy": validated.endpoint_policy,
            "agreement_unvoiced_policy": validated.agreement_unvoiced_policy,
            "octave_tolerance_cents": validated.octave_tolerance_cents,
            "maximum_agreement_frame_count": (validated.maximum_agreement_frame_count),
            "reference_role": validated.reference_role,
        },
        "execution": {
            "selected_before_audio_read_count": counters[
                "selected_before_audio_read_count"
            ],
            "source_pcm_integrity_read_attempt_count": counters[
                "source_pcm_integrity_read_attempt_count"
            ],
            "source_pcm_integrity_match_count": counters[
                "source_pcm_integrity_match_count"
            ],
            "selected_source_audio_access_counts_by_project_split": {
                FIXED_PROJECT_SPLIT: counters["project_dev_audio_read_item_count"]
            },
            "locked_test_source_audio_access_count": 0,
            "source_metadata_probe_attempt_count": counters[
                "source_metadata_probe_attempt_count"
            ],
            "decode_completed_count": counters["decode_completed_count"],
            "resampled_count": counters["resampled_count"],
            "multichannel_downmix_count": counters["multichannel_downmix_count"],
            "private_npy_created_count": counters["private_npy_created_count"],
            "private_npy_cleaned_count": counters["private_npy_cleaned_count"],
            "private_npy_mode": "0600",
            "private_temp_directory_mode": "0700",
            "worker_contour_transport": "captured_subprocess_stdout_memory_pipe",
            "parselmouth_attempted_count": counters["parselmouth_attempted_count"],
            "parselmouth_completed_count": counters["parselmouth_completed_count"],
            "torchcrepe_attempted_count": counters["torchcrepe_attempted_count"],
            "torchcrepe_completed_count": counters["torchcrepe_completed_count"],
            "cross_process_pcm_digest_match_count": counters[
                "cross_process_pcm_digest_match_count"
            ],
            "comparison_completed_count": completed_count,
            "network_socket_attempt_count": counters["network_socket_attempt_count"],
            "primary_failure_reason_counts": dict(sorted(primary_failures.items())),
            "failure_condition_counts": dict(sorted(failure_conditions.items())),
        },
        "metrics": None if aggregate is None else aggregate.to_dict(),
        "provenance": {
            "implementation_sha256": source_sha256,
            "implementation_verified_at_start": True,
            "implementation_unchanged_at_end": True,
            "modeling_manifest_sha256": modeling_manifest_sha256,
            "modeling_manifest_verified_before_and_after_each_scan": True,
            "modeling_manifest_unchanged_at_end": True,
            "parent_environment": parent_environment,
            "parselmouth_environment": _normalize_environment(
                parselmouth_probe.get("environment")
            ),
            "torchcrepe_environment": _normalize_environment(
                torchcrepe_probe.get("environment")
            ),
            "platform": {
                "system": platform.system(),
                "machine": platform.machine(),
            },
            "gpu_used": False,
            "worker_device": "cpu",
            "network_disabled": True,
        },
        "privacy": {
            "raw_audio_published": False,
            "source_filename_published": False,
            "source_path_published": False,
            "source_pcm_sha256_published": False,
            "utterance_identifier_published": False,
            "speaker_identifier_published": False,
            "transcript_published": False,
            "per_item_contour_published": False,
            "per_item_metric_published": False,
            "temporary_pcm_cleanup_complete": (
                counters["private_npy_created_count"]
                == counters["private_npy_cleaned_count"]
            ),
        },
        "interpretation": {
            "parselmouth_is_gold": False,
            "result_is_backend_agreement_only": True,
            "thresholds_are_smoke_parameters_not_validated_scientific_defaults": True,
            "official_partition_is_not_project_split": True,
            "project_split_policy": "dev_only_before_audio_read",
            "project_dev_source_audio_access_count": counters[
                "project_dev_audio_read_item_count"
            ],
            "project_locked_test_source_audio_access_count": 0,
            "remaining_m4_work": (
                "speaker_slice_preregistration_calibration_ablation_and_human_validation"
            ),
        },
    }
    report = attach_payload_sha256(payload)
    _write_report_atomically(validated.output_path, report)
    return report


def _parse_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("value must be true or false")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run aggregate-only actual Validation F0 agreement smoke."
    )
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--modeling-manifest", type=Path)
    parser.add_argument("--expected-modeling-manifest-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--private-temp-root", type=Path)
    parser.add_argument("--parselmouth-python", type=Path)
    parser.add_argument("--torchcrepe-python", type=Path)
    for role in sorted(IMPLEMENTATION_PATHS):
        parser.add_argument(f"--expected-{role.replace('_', '-')}-sha256")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--target-sample-rate-hz", type=int)
    parser.add_argument("--maximum-duration-seconds", type=float)
    parser.add_argument("--maximum-channel-count", type=int)
    parser.add_argument("--downmix-policy")
    parser.add_argument("--resample-policy")
    parser.add_argument("--parselmouth-time-step-seconds", type=float)
    parser.add_argument("--pitch-floor-hz", type=float)
    parser.add_argument("--pitch-ceiling-hz", type=float)
    parser.add_argument("--parselmouth-unvoiced-policy")
    parser.add_argument("--torchcrepe-hop-length-samples", type=int)
    parser.add_argument("--torchcrepe-model")
    parser.add_argument("--torchcrepe-decoder")
    parser.add_argument("--torchcrepe-batch-size-frames", type=int)
    parser.add_argument("--torchcrepe-pad", type=_parse_bool)
    parser.add_argument("--torchcrepe-periodicity-threshold", type=float)
    parser.add_argument("--torchcrepe-silence-threshold-dbfs", type=float)
    parser.add_argument("--numpy-random-seed", type=int)
    parser.add_argument("--torch-random-seed", type=int)
    parser.add_argument("--torchcrepe-unvoiced-policy")
    parser.add_argument("--common-grid-policy")
    parser.add_argument("--interpolation-policy")
    parser.add_argument("--endpoint-policy")
    parser.add_argument("--agreement-unvoiced-policy")
    parser.add_argument("--octave-tolerance-cents", type=float)
    parser.add_argument("--maximum-agreement-frame-count", type=int)
    parser.add_argument("--reference-role")
    parser.add_argument("--worker-timeout-seconds", type=int)

    return parser


def _required(value: object, field_name: str) -> Any:
    if value is None:
        raise ValidationF0SmokeError(f"{field_name} is required")
    return value


def _configuration_from_arguments(arguments: argparse.Namespace) -> SmokeConfiguration:
    expected_sources = tuple(
        (
            role,
            cast(
                str,
                _required(
                    getattr(arguments, f"expected_{role}_sha256"),
                    f"expected_{role}_sha256",
                ),
            ),
        )
        for role in sorted(IMPLEMENTATION_PATHS)
    )
    return SmokeConfiguration(
        dataset_root=cast(Path, _required(arguments.dataset_root, "dataset_root")),
        modeling_manifest_path=cast(
            Path,
            _required(arguments.modeling_manifest, "modeling_manifest"),
        ),
        expected_modeling_manifest_sha256=cast(
            str,
            _required(
                arguments.expected_modeling_manifest_sha256,
                "expected_modeling_manifest_sha256",
            ),
        ),
        output_path=cast(Path, _required(arguments.output, "output")),
        private_temp_root=cast(
            Path,
            _required(arguments.private_temp_root, "private_temp_root"),
        ),
        parselmouth_python=cast(
            Path,
            _required(arguments.parselmouth_python, "parselmouth_python"),
        ),
        torchcrepe_python=cast(
            Path,
            _required(arguments.torchcrepe_python, "torchcrepe_python"),
        ),
        expected_source_sha256=expected_sources,
        seed=cast(int, _required(arguments.seed, "seed")),
        sample_size=cast(int, _required(arguments.sample_size, "sample_size")),
        target_sample_rate_hz=cast(
            int,
            _required(arguments.target_sample_rate_hz, "target_sample_rate_hz"),
        ),
        maximum_duration_seconds=cast(
            float,
            _required(
                arguments.maximum_duration_seconds,
                "maximum_duration_seconds",
            ),
        ),
        maximum_channel_count=cast(
            int,
            _required(arguments.maximum_channel_count, "maximum_channel_count"),
        ),
        downmix_policy=cast(
            str,
            _required(arguments.downmix_policy, "downmix_policy"),
        ),
        resample_policy=cast(
            str,
            _required(arguments.resample_policy, "resample_policy"),
        ),
        parselmouth_time_step_seconds=cast(
            float,
            _required(
                arguments.parselmouth_time_step_seconds,
                "parselmouth_time_step_seconds",
            ),
        ),
        pitch_floor_hz=cast(
            float,
            _required(arguments.pitch_floor_hz, "pitch_floor_hz"),
        ),
        pitch_ceiling_hz=cast(
            float,
            _required(arguments.pitch_ceiling_hz, "pitch_ceiling_hz"),
        ),
        parselmouth_unvoiced_policy=cast(
            str,
            _required(
                arguments.parselmouth_unvoiced_policy,
                "parselmouth_unvoiced_policy",
            ),
        ),
        torchcrepe_hop_length_samples=cast(
            int,
            _required(
                arguments.torchcrepe_hop_length_samples,
                "torchcrepe_hop_length_samples",
            ),
        ),
        torchcrepe_model=cast(
            str,
            _required(arguments.torchcrepe_model, "torchcrepe_model"),
        ),
        torchcrepe_decoder=cast(
            str,
            _required(arguments.torchcrepe_decoder, "torchcrepe_decoder"),
        ),
        torchcrepe_batch_size_frames=cast(
            int,
            _required(
                arguments.torchcrepe_batch_size_frames,
                "torchcrepe_batch_size_frames",
            ),
        ),
        torchcrepe_pad=cast(
            bool,
            _required(arguments.torchcrepe_pad, "torchcrepe_pad"),
        ),
        torchcrepe_periodicity_threshold=cast(
            float,
            _required(
                arguments.torchcrepe_periodicity_threshold,
                "torchcrepe_periodicity_threshold",
            ),
        ),
        torchcrepe_silence_threshold_dbfs=cast(
            float,
            _required(
                arguments.torchcrepe_silence_threshold_dbfs,
                "torchcrepe_silence_threshold_dbfs",
            ),
        ),
        numpy_random_seed=cast(
            int,
            _required(arguments.numpy_random_seed, "numpy_random_seed"),
        ),
        torch_random_seed=cast(
            int,
            _required(arguments.torch_random_seed, "torch_random_seed"),
        ),
        torchcrepe_unvoiced_policy=cast(
            str,
            _required(
                arguments.torchcrepe_unvoiced_policy,
                "torchcrepe_unvoiced_policy",
            ),
        ),
        common_grid_policy=cast(
            str,
            _required(arguments.common_grid_policy, "common_grid_policy"),
        ),
        interpolation_policy=cast(
            str,
            _required(arguments.interpolation_policy, "interpolation_policy"),
        ),
        endpoint_policy=cast(
            str,
            _required(arguments.endpoint_policy, "endpoint_policy"),
        ),
        agreement_unvoiced_policy=cast(
            str,
            _required(
                arguments.agreement_unvoiced_policy,
                "agreement_unvoiced_policy",
            ),
        ),
        octave_tolerance_cents=cast(
            float,
            _required(
                arguments.octave_tolerance_cents,
                "octave_tolerance_cents",
            ),
        ),
        maximum_agreement_frame_count=cast(
            int,
            _required(
                arguments.maximum_agreement_frame_count,
                "maximum_agreement_frame_count",
            ),
        ),
        reference_role=cast(
            str,
            _required(arguments.reference_role, "reference_role"),
        ),
        worker_timeout_seconds=cast(
            int,
            _required(
                arguments.worker_timeout_seconds,
                "worker_timeout_seconds",
            ),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the aggregate parent entrypoint."""

    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        configuration = _configuration_from_arguments(arguments)
        report = run_validation_f0_smoke(configuration)
    except (
        ActualF0AgreementSmokeError,
        ValidationF0SmokeError,
    ) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "comparison_completed_count": cast(
                    Mapping[str, object],
                    report["execution"],
                )["comparison_completed_count"],
                "payload_sha256": report["payload_sha256"],
                "status": report["status"],
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
