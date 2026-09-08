"""Strict, CPU-only in-memory torchcrepe pitch-contour extraction."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
from importlib import metadata
import json
from math import isfinite
from numbers import Integral, Real
import threading
from typing import Any, Literal, Protocol, cast

import numpy as np
from numpy.typing import NDArray


EXPECTED_TORCHCREPE_VERSION = "0.0.24"
EXPECTED_SAMPLE_RATE_HZ = 16_000
TORCHCREPE_WINDOW_SIZE_SAMPLES = 1_024
MAX_PITCH_CEILING_HZ = 2_006.0

FloatArray = NDArray[np.float64]
TorchcrepeModel = Literal["tiny", "full"]
TorchcrepeDecoder = Literal["viterbi", "weighted_argmax", "argmax"]
TorchcrepeUnvoicedPolicy = Literal["keep_zero", "drop"]

_NUMPY_RNG_LOCK = threading.Lock()


class TorchcrepePitchExtractionError(ValueError):
    """Raised when the pinned torchcrepe extraction contract is violated."""


class _TorchcrepeBackend(Protocol):
    def predict(
        self,
        audio: NDArray[np.float32],
        *,
        sample_rate_hz: int,
        hop_length_samples: int,
        pitch_floor_hz: float,
        pitch_ceiling_hz: float,
        model: TorchcrepeModel,
        decoder: TorchcrepeDecoder,
        batch_size_frames: int,
        pad: bool,
        silence_threshold_dbfs: float,
        numpy_random_seed: int,
        torch_random_seed: int,
    ) -> tuple[object, object, object]: ...


@dataclass(frozen=True, slots=True)
class TorchcrepePitchContour:
    """A replayable contour and voiced summary without source identifiers."""

    torchcrepe_version: str
    sample_rate_hz: int
    hop_length_samples: int
    pitch_floor_hz: float
    pitch_ceiling_hz: float
    model: TorchcrepeModel
    decoder: TorchcrepeDecoder
    batch_size_frames: int
    pad: bool
    periodicity_threshold: float
    silence_threshold_dbfs: float
    numpy_random_seed: int
    torch_random_seed: int
    unvoiced_policy: TorchcrepeUnvoicedPolicy
    time_seconds: tuple[float, ...]
    f0_hz: tuple[float, ...]
    periodicity: tuple[float, ...]
    backend_frame_count: int
    returned_frame_count: int
    voiced_frame_count: int
    unvoiced_frame_count: int
    voiced_median_hz: float
    voiced_range_hz: float
    contour_sha256: str


@dataclass(slots=True)
class _RuntimeTorchcrepeBackend:
    torchcrepe: Any
    torch: Any

    def predict(
        self,
        audio: NDArray[np.float32],
        *,
        sample_rate_hz: int,
        hop_length_samples: int,
        pitch_floor_hz: float,
        pitch_ceiling_hz: float,
        model: TorchcrepeModel,
        decoder: TorchcrepeDecoder,
        batch_size_frames: int,
        pad: bool,
        silence_threshold_dbfs: float,
        numpy_random_seed: int,
        torch_random_seed: int,
    ) -> tuple[object, object, object]:
        del numpy_random_seed  # NumPy state is scoped by the public adapter.
        generator = self.torch.default_generator
        torch_rng_state = generator.get_state()
        try:
            generator.manual_seed(torch_random_seed)
            tensor = self.torch.from_numpy(np.array(audio, copy=True)).unsqueeze(0)
            decoder_fn = getattr(self.torchcrepe.decode, decoder)
            pitch, periodicity = self.torchcrepe.predict(
                audio=tensor,
                sample_rate=sample_rate_hz,
                hop_length=hop_length_samples,
                fmin=pitch_floor_hz,
                fmax=pitch_ceiling_hz,
                model=model,
                decoder=decoder_fn,
                return_harmonicity=False,
                return_periodicity=True,
                batch_size=batch_size_frames,
                device="cpu",
                pad=pad,
            )
            silence_periodicity = self.torchcrepe.threshold.Silence(
                silence_threshold_dbfs
            )(
                periodicity,
                tensor,
                sample_rate=sample_rate_hz,
                hop_length=hop_length_samples,
                pad=pad,
            )
            return (
                pitch.detach().cpu().numpy().copy(),
                periodicity.detach().cpu().numpy().copy(),
                silence_periodicity.detach().cpu().numpy().copy(),
            )
        finally:
            generator.set_state(torch_rng_state)


def _validate_audio(samples: object, sample_rate_hz: object) -> NDArray[np.float32]:
    if (
        isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, Integral)
        or int(sample_rate_hz) != EXPECTED_SAMPLE_RATE_HZ
    ):
        raise TorchcrepePitchExtractionError(
            f"sample_rate_hz must be the integer {EXPECTED_SAMPLE_RATE_HZ}"
        )
    if type(samples) is not np.ndarray:
        raise TorchcrepePitchExtractionError(
            "samples must be a base float32 or float64 ndarray"
        )
    base: object = samples
    while isinstance(base, np.ndarray):
        if isinstance(base, np.memmap):
            raise TorchcrepePitchExtractionError(
                "samples must not use memory-mapped storage"
            )
        base = base.base
    if samples.ndim != 1:
        raise TorchcrepePitchExtractionError("samples must be mono")
    if samples.size == 0:
        raise TorchcrepePitchExtractionError("samples must not be empty")
    if samples.dtype not in {np.dtype(np.float32), np.dtype(np.float64)}:
        raise TorchcrepePitchExtractionError(
            "samples dtype must be exactly float32 or float64"
        )
    if not np.isfinite(samples).all():
        raise TorchcrepePitchExtractionError("samples contain NaN or Inf")
    if np.any(np.abs(samples) > 1.0):
        raise TorchcrepePitchExtractionError(
            "samples must be normalized to the closed interval [-1, 1]"
        )
    audio = np.asarray(samples, dtype=np.float32)
    if not np.isfinite(audio).all():
        raise TorchcrepePitchExtractionError("float32 conversion produced NaN or Inf")
    return np.array(audio, dtype=np.float32, copy=True)


def _positive_finite_scalar(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TorchcrepePitchExtractionError(
            f"{field_name} must be a finite positive real number"
        )
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0.0:
        raise TorchcrepePitchExtractionError(
            f"{field_name} must be a finite positive real number"
        )
    return normalized


def _positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or int(value) <= 0:
        raise TorchcrepePitchExtractionError(f"{field_name} must be a positive integer")
    return int(value)


def _seed(value: object, field_name: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, Integral)
        or int(value) < 0
        or int(value) > maximum
    ):
        raise TorchcrepePitchExtractionError(
            f"{field_name} is outside its supported integer range"
        )
    return int(value)


def _finite_scalar(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TorchcrepePitchExtractionError(f"{field_name} must be finite")
    normalized = float(value)
    if not isfinite(normalized):
        raise TorchcrepePitchExtractionError(f"{field_name} must be finite")
    return normalized


def _load_pinned_backend() -> tuple[_TorchcrepeBackend, str]:
    try:
        distribution_version = metadata.version("torchcrepe")
    except metadata.PackageNotFoundError:
        raise TorchcrepePitchExtractionError(
            "torchcrepe is unavailable in this environment"
        ) from None
    if distribution_version != EXPECTED_TORCHCREPE_VERSION:
        raise TorchcrepePitchExtractionError(
            "torchcrepe version differs from the pinned extraction contract"
        )
    try:
        torchcrepe = importlib.import_module("torchcrepe")
        torch = importlib.import_module("torch")
    except (ImportError, OSError, RuntimeError):
        raise TorchcrepePitchExtractionError(
            "torchcrepe is unavailable in this environment"
        ) from None
    return _RuntimeTorchcrepeBackend(
        torchcrepe=torchcrepe,
        torch=torch,
    ), distribution_version


def _backend_matrix(value: object, field_name: str) -> FloatArray:
    try:
        untyped = np.asarray(value)
    except (TypeError, ValueError):
        raise TorchcrepePitchExtractionError(
            "torchcrepe returned an invalid pitch result"
        ) from None
    if untyped.ndim != 2 or untyped.shape[0] != 1 or untyped.shape[1] == 0:
        raise TorchcrepePitchExtractionError(
            f"torchcrepe {field_name} must have shape [1, frames]"
        )
    if untyped.dtype.kind not in {"f", "i", "u"}:
        raise TorchcrepePitchExtractionError(
            "torchcrepe returned a non-numeric pitch result"
        )
    result = np.asarray(untyped, dtype=np.float64)
    if not np.isfinite(result).all():
        raise TorchcrepePitchExtractionError(
            f"torchcrepe {field_name} contains NaN or Inf"
        )
    return result


def _time_grid(
    *,
    sample_count: int,
    hop_length_samples: int,
    pad: bool,
) -> FloatArray:
    if pad:
        frame_count = 1 + sample_count // hop_length_samples
        first_center_samples = 0
    else:
        if sample_count < TORCHCREPE_WINDOW_SIZE_SAMPLES:
            raise TorchcrepePitchExtractionError(
                "unpadded audio must contain at least 1024 samples"
            )
        frame_count = (
            1 + (sample_count - TORCHCREPE_WINDOW_SIZE_SAMPLES) // hop_length_samples
        )
        first_center_samples = TORCHCREPE_WINDOW_SIZE_SAMPLES // 2
    times = (
        first_center_samples
        + np.arange(frame_count, dtype=np.float64) * hop_length_samples
    ) / EXPECTED_SAMPLE_RATE_HZ
    if (
        not np.isfinite(times).all()
        or np.any(times < 0.0)
        or (times.size > 1 and not np.all(np.diff(times) > 0.0))
        or times[-1] > sample_count / EXPECTED_SAMPLE_RATE_HZ
    ):
        raise TorchcrepePitchExtractionError("derived torchcrepe time grid is invalid")
    return times


def _contour_digest(
    *,
    contract: dict[str, object],
    time_seconds: FloatArray,
    f0_hz: FloatArray,
    periodicity: FloatArray,
) -> str:
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            contract,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    for name, values in (
        ("time_seconds", time_seconds),
        ("f0_hz", f0_hz),
        ("periodicity", periodicity),
    ):
        digest.update(b"\x00" + name.encode("ascii") + b"\x00")
        digest.update(np.asarray(values, dtype="<f8").tobytes(order="C"))
    return digest.hexdigest()


def extract_torchcrepe_pitch_contour(
    samples: object,
    sample_rate_hz: int,
    *,
    hop_length_samples: int,
    pitch_floor_hz: float,
    pitch_ceiling_hz: float,
    model: TorchcrepeModel,
    decoder: TorchcrepeDecoder,
    batch_size_frames: int,
    pad: bool,
    periodicity_threshold: float,
    silence_threshold_dbfs: float,
    numpy_random_seed: int,
    torch_random_seed: int,
    unvoiced_policy: TorchcrepeUnvoicedPolicy,
    backend: _TorchcrepeBackend | None = None,
    backend_version: str | None = None,
) -> TorchcrepePitchContour:
    """Extract a strict, caller-parameterized torchcrepe contour on CPU."""

    audio = _validate_audio(samples, sample_rate_hz)
    hop = _positive_integer(hop_length_samples, "hop_length_samples")
    floor = _positive_finite_scalar(pitch_floor_hz, "pitch_floor_hz")
    ceiling = _positive_finite_scalar(pitch_ceiling_hz, "pitch_ceiling_hz")
    if ceiling <= floor or ceiling > MAX_PITCH_CEILING_HZ:
        raise TorchcrepePitchExtractionError(
            "pitch_ceiling_hz must be greater than pitch_floor_hz and at most 2006"
        )
    batch_size = _positive_integer(batch_size_frames, "batch_size_frames")
    if not isinstance(pad, bool):
        raise TorchcrepePitchExtractionError("pad must be a boolean")
    if not isinstance(model, str) or model not in {"tiny", "full"}:
        raise TorchcrepePitchExtractionError("model must be exactly 'tiny' or 'full'")
    if not isinstance(decoder, str) or decoder not in {
        "viterbi",
        "weighted_argmax",
        "argmax",
    }:
        raise TorchcrepePitchExtractionError(
            "decoder must be exactly 'viterbi', 'weighted_argmax', or 'argmax'"
        )
    periodicity_cutoff = _finite_scalar(periodicity_threshold, "periodicity_threshold")
    if not 0.0 <= periodicity_cutoff <= 1.0:
        raise TorchcrepePitchExtractionError(
            "periodicity_threshold must be between zero and one"
        )
    silence_cutoff = _finite_scalar(silence_threshold_dbfs, "silence_threshold_dbfs")
    numpy_seed = _seed(numpy_random_seed, "numpy_random_seed", 2**32 - 1)
    torch_seed = _seed(torch_random_seed, "torch_random_seed", 2**63 - 1)
    if not isinstance(unvoiced_policy, str) or unvoiced_policy not in {
        "keep_zero",
        "drop",
    }:
        raise TorchcrepePitchExtractionError(
            "unvoiced_policy must be exactly 'keep_zero' or 'drop'"
        )
    policy = cast(TorchcrepeUnvoicedPolicy, unvoiced_policy)
    model_name = cast(TorchcrepeModel, model)
    decoder_name = cast(TorchcrepeDecoder, decoder)
    expected_times = _time_grid(
        sample_count=int(audio.size),
        hop_length_samples=hop,
        pad=pad,
    )

    if (backend is None) != (backend_version is None):
        raise TorchcrepePitchExtractionError(
            "backend and backend_version must be provided together"
        )
    if backend is None:
        backend, version = _load_pinned_backend()
    else:
        version = str(backend_version)
        if version != EXPECTED_TORCHCREPE_VERSION:
            raise TorchcrepePitchExtractionError(
                "backend version differs from the pinned extraction contract"
            )

    with _NUMPY_RNG_LOCK:
        numpy_rng_state = np.random.get_state()
        try:
            np.random.seed(numpy_seed)
            result = backend.predict(
                audio,
                sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ,
                hop_length_samples=hop,
                pitch_floor_hz=floor,
                pitch_ceiling_hz=ceiling,
                model=model_name,
                decoder=decoder_name,
                batch_size_frames=batch_size,
                pad=pad,
                silence_threshold_dbfs=silence_cutoff,
                numpy_random_seed=numpy_seed,
                torch_random_seed=torch_seed,
            )
        except Exception:  # noqa: BLE001
            raise TorchcrepePitchExtractionError(
                "torchcrepe pitch extraction failed"
            ) from None
        finally:
            np.random.set_state(numpy_rng_state)

    try:
        pitch_value, periodicity_value, silence_periodicity_value = result
    except (TypeError, ValueError):
        raise TorchcrepePitchExtractionError(
            "torchcrepe returned an invalid pitch result"
        ) from None
    pitch = _backend_matrix(pitch_value, "pitch")
    raw_periodicity = _backend_matrix(periodicity_value, "periodicity")
    silence_periodicity = _backend_matrix(
        silence_periodicity_value, "silence periodicity"
    )
    expected_shape = (1, int(expected_times.size))
    if (
        pitch.shape != expected_shape
        or raw_periodicity.shape != expected_shape
        or silence_periodicity.shape != expected_shape
    ):
        raise TorchcrepePitchExtractionError(
            "torchcrepe output shape differs from the expected time grid"
        )
    if np.any(pitch < floor) or np.any(pitch > ceiling):
        raise TorchcrepePitchExtractionError(
            "torchcrepe pitch falls outside the caller-provided bounds"
        )
    for periodicity_values in (raw_periodicity, silence_periodicity):
        if np.any(periodicity_values < 0.0) or np.any(periodicity_values > 1.0):
            raise TorchcrepePitchExtractionError(
                "torchcrepe periodicity falls outside zero-to-one bounds"
            )

    pitch_1d = pitch[0]
    silence_periodicity_1d = silence_periodicity[0]
    voiced_mask = (silence_periodicity_1d > 0.0) & (
        silence_periodicity_1d >= periodicity_cutoff
    )
    if not np.any(voiced_mask):
        raise TorchcrepePitchExtractionError(
            "torchcrepe pitch contour contains no voiced frames"
        )
    canonical_f0 = np.where(voiced_mask, pitch_1d, 0.0)
    canonical_periodicity = np.where(voiced_mask, silence_periodicity_1d, 0.0)
    if policy == "drop":
        returned_times = expected_times[voiced_mask]
        returned_f0 = canonical_f0[voiced_mask]
        returned_periodicity = canonical_periodicity[voiced_mask]
    else:
        returned_times = expected_times
        returned_f0 = canonical_f0
        returned_periodicity = canonical_periodicity

    voiced_f0 = pitch_1d[voiced_mask]
    backend_frame_count = int(expected_times.size)
    voiced_frame_count = int(voiced_f0.size)
    contract: dict[str, object] = {
        "batch_size_frames": batch_size,
        "decoder": decoder_name,
        "hop_length_samples": hop,
        "model": model_name,
        "numpy_random_seed": numpy_seed,
        "pad": pad,
        "periodicity_threshold": periodicity_cutoff,
        "pitch_ceiling_hz": ceiling,
        "pitch_floor_hz": floor,
        "sample_rate_hz": EXPECTED_SAMPLE_RATE_HZ,
        "silence_threshold_dbfs": silence_cutoff,
        "torch_random_seed": torch_seed,
        "torchcrepe_version": version,
        "unvoiced_policy": policy,
    }
    contour_sha256 = _contour_digest(
        contract=contract,
        time_seconds=returned_times,
        f0_hz=returned_f0,
        periodicity=returned_periodicity,
    )
    return TorchcrepePitchContour(
        torchcrepe_version=version,
        sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ,
        hop_length_samples=hop,
        pitch_floor_hz=floor,
        pitch_ceiling_hz=ceiling,
        model=model_name,
        decoder=decoder_name,
        batch_size_frames=batch_size,
        pad=pad,
        periodicity_threshold=periodicity_cutoff,
        silence_threshold_dbfs=silence_cutoff,
        numpy_random_seed=numpy_seed,
        torch_random_seed=torch_seed,
        unvoiced_policy=policy,
        time_seconds=tuple(float(value) for value in returned_times),
        f0_hz=tuple(float(value) for value in returned_f0),
        periodicity=tuple(float(value) for value in returned_periodicity),
        backend_frame_count=backend_frame_count,
        returned_frame_count=int(returned_times.size),
        voiced_frame_count=voiced_frame_count,
        unvoiced_frame_count=backend_frame_count - voiced_frame_count,
        voiced_median_hz=float(np.median(voiced_f0)),
        voiced_range_hz=float(np.max(voiced_f0) - np.min(voiced_f0)),
        contour_sha256=contour_sha256,
    )


__all__ = [
    "EXPECTED_SAMPLE_RATE_HZ",
    "EXPECTED_TORCHCREPE_VERSION",
    "TorchcrepeDecoder",
    "TorchcrepeModel",
    "TorchcrepePitchContour",
    "TorchcrepePitchExtractionError",
    "TorchcrepeUnvoicedPolicy",
    "extract_torchcrepe_pitch_contour",
]
