#!/usr/bin/env python3
"""Private CPU-only F0 worker for the bounded actual-audio smoke.

This executable intentionally avoids importing the project packages through
their ``__init__`` modules because the two pinned research environments have
different optional dependencies.  It reads one mode-0600 float32 NPY, emits a
contour only through stdout for capture by the scorer parent, and never accepts
a corpus path or identifier.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
from importlib import metadata
import json
from pathlib import Path
import platform
import socket
import stat
import sys
from types import ModuleType
from typing import Any, Iterator, cast

import numpy as np
from numpy.typing import NDArray


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATHS = {
    "parselmouth": (
        WORKSPACE_ROOT / "src" / "voice_coach" / "features" / "parselmouth_pitch.py"
    ),
    "torchcrepe": (
        WORKSPACE_ROOT / "src" / "voice_coach" / "features" / "torchcrepe_pitch.py"
    ),
}
EXPECTED_ENVIRONMENTS = {
    "parselmouth": {
        "python": "3.12.3",
        "numpy": "2.5.1",
        "praat-parselmouth": "0.4.7",
    },
    "torchcrepe": {
        "python": "3.12.3",
        "numpy": "2.4.6",
        "torch": "2.11.0+cpu",
        "torchcrepe": "0.0.24",
    },
}


class WorkerError(ValueError):
    """Raised for a generic private-worker contract failure."""


class _BlockedNetworkSocket(socket.socket):
    """Socket subtype that keeps import-time type checks valid and blocks IP."""

    attempt_counter: list[int] | None = None

    def _record_blocked_attempt(self) -> None:
        counter = type(self).attempt_counter
        if counter is None:
            raise WorkerError("network guard is inactive")
        counter[0] += 1

    def connect(self, address: Any) -> None:
        if self.family in {socket.AF_INET, socket.AF_INET6}:
            self._record_blocked_attempt()
            raise OSError("network disabled")
        super().connect(address)

    def connect_ex(self, address: Any) -> int:
        if self.family in {socket.AF_INET, socket.AF_INET6}:
            self._record_blocked_attempt()
            raise OSError("network disabled")
        return super().connect_ex(address)

    def sendto(self, data: Any, *args: Any) -> int:  # type: ignore[override]
        if self.family in {socket.AF_INET, socket.AF_INET6}:
            self._record_blocked_attempt()
            raise OSError("network disabled")
        return super().sendto(data, *args)


def _sanitized_extraction_failure_code(backend: str, error: Exception) -> str:
    """Map adapter-owned fixed messages to identifier-free failure categories."""

    message = str(error)
    error_type = type(error).__name__
    if backend == "parselmouth":
        if message == "Parselmouth pitch contour contains no voiced frames":
            return "parselmouth_no_voiced_failure"
        if message == "Parselmouth pitch extraction failed":
            return "parselmouth_backend_call_failure"
        return "parselmouth_output_contract_failure"
    if error_type == "TypeError":
        return "torchcrepe_unexpected_type_error"
    if error_type == "AttributeError":
        return "torchcrepe_unexpected_attribute_error"
    if error_type not in {"TorchcrepePitchExtractionError", "ValueError"}:
        return "torchcrepe_unexpected_exception_type"
    if message == "torchcrepe pitch extraction failed":
        return "torchcrepe_backend_call_failure"
    if message.startswith("sample_rate_hz ") or message.startswith("samples "):
        return "torchcrepe_input_contract_failure"
    if message == "float32 conversion produced NaN or Inf":
        return "torchcrepe_input_contract_failure"
    if message in {
        "torchcrepe is unavailable in this environment",
        "torchcrepe version differs from the pinned extraction contract",
        "backend version differs from the pinned extraction contract",
    }:
        return "torchcrepe_backend_availability_failure"
    if message in {
        "torchcrepe returned an invalid pitch result",
        "torchcrepe returned a non-numeric pitch result",
    }:
        return "torchcrepe_invalid_result_failure"
    if message.startswith("torchcrepe ") and message.endswith(
        "must have shape [1, frames]"
    ):
        return "torchcrepe_backend_matrix_shape_failure"
    if message.startswith("torchcrepe ") and message.endswith("contains NaN or Inf"):
        return "torchcrepe_nonfinite_result_failure"
    if message == "torchcrepe pitch contour contains no voiced frames":
        return "torchcrepe_no_voiced_failure"
    if message == "torchcrepe output shape differs from the expected time grid":
        return "torchcrepe_output_shape_failure"
    if message == "torchcrepe pitch falls outside the caller-provided bounds":
        return "torchcrepe_pitch_bound_failure"
    if message == "torchcrepe periodicity falls outside zero-to-one bounds":
        return "torchcrepe_periodicity_bound_failure"
    if (
        message.startswith("pitch_")
        or message.startswith("hop_length_samples ")
        or message.startswith("batch_size_frames ")
        or message.startswith("numpy_random_seed ")
        or message.startswith("torch_random_seed ")
        or message.startswith("model ")
        or message.startswith("decoder ")
        or message.startswith("periodicity_threshold ")
        or message.startswith("unvoiced_policy ")
        or message.startswith("pad ")
        or message.startswith("backend and backend_version ")
        or message.startswith("derived torchcrepe time grid ")
        or message.startswith("unpadded audio ")
    ):
        return "torchcrepe_parameter_contract_failure"
    return "torchcrepe_output_contract_failure"


def _environment_versions(backend: str) -> dict[str, str]:
    package_names = (
        ("numpy", "praat-parselmouth")
        if backend == "parselmouth"
        else ("numpy", "torch", "torchcrepe")
    )
    versions = {"python": platform.python_version()}
    try:
        versions.update({name: metadata.version(name) for name in package_names})
    except metadata.PackageNotFoundError:
        raise WorkerError("environment pin mismatch") from None
    if versions != EXPECTED_ENVIRONMENTS[backend]:
        raise WorkerError("environment pin mismatch")
    return versions


def _load_adapter(backend: str) -> ModuleType:
    path = ADAPTER_PATHS[backend]
    module_name = f"_voice_coach_phase1_{backend}_adapter"
    specification = importlib.util.spec_from_file_location(module_name, path)
    if specification is None or specification.loader is None:
        raise WorkerError("adapter unavailable")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        specification.loader.exec_module(module)
    except Exception:  # noqa: BLE001
        raise WorkerError("adapter unavailable") from None
    return module


def _sha256_pcm(samples: NDArray[np.float32]) -> str:
    canonical = np.asarray(samples, dtype="<f4", order="C")
    return hashlib.sha256(canonical.tobytes(order="C")).hexdigest()


def _load_private_pcm(path: Path, sample_rate_hz: int) -> NDArray[np.float32]:
    if sample_rate_hz != 16_000:
        raise WorkerError("private PCM contract failure")
    try:
        information = path.lstat()
        values = np.load(path, allow_pickle=False)
    except (OSError, TypeError, ValueError):
        raise WorkerError("private PCM contract failure") from None
    if (
        path.is_symlink()
        or not stat.S_ISREG(information.st_mode)
        or stat.S_IMODE(information.st_mode) != 0o600
        or type(values) is not np.ndarray
        or values.dtype != np.dtype(np.float32)
        or values.ndim != 1
        or values.size == 0
        or not np.isfinite(values).all()
        or np.any(np.abs(values) > 1.0)
    ):
        raise WorkerError("private PCM contract failure")
    return np.array(values, dtype=np.float32, order="C", copy=True)


@contextmanager
def _blocked_network_sockets() -> Iterator[list[int]]:
    original_socket = socket.socket
    attempts = [0]
    _BlockedNetworkSocket.attempt_counter = attempts
    socket.socket = _BlockedNetworkSocket  # type: ignore[misc,assignment]
    try:
        yield attempts
    finally:
        socket.socket = original_socket  # type: ignore[misc,assignment]
        _BlockedNetworkSocket.attempt_counter = None


def _probe_payload(backend: str) -> dict[str, object]:
    environment = _environment_versions(backend)
    payload: dict[str, object] = {
        "status": "ok",
        "backend": backend,
        "environment": environment,
        "network_socket_attempt_count": 0,
    }
    if backend == "torchcrepe":
        import torch

        payload.update(
            {
                "device": "cpu",
                "cuda_available": bool(torch.cuda.is_available()),
                "torch_cuda_version": torch.version.cuda,
            }
        )
    return payload


def _execute_payload(arguments: argparse.Namespace) -> dict[str, object]:
    backend = cast(str, arguments.backend)
    environment = _environment_versions(backend)
    try:
        pcm = _load_private_pcm(arguments.pcm_npy, arguments.sample_rate_hz)
    except WorkerError:
        return {
            "status": "error",
            "backend": backend,
            "failure_code": "private_pcm_contract_failure",
            "network_socket_attempt_count": 0,
        }

    adapter = _load_adapter(backend)
    with _blocked_network_sockets() as socket_attempts:
        try:
            contour: Any
            if backend == "parselmouth":
                contour = adapter.extract_parselmouth_pitch_contour(
                    pcm,
                    arguments.sample_rate_hz,
                    time_step_seconds=arguments.parselmouth_time_step_seconds,
                    pitch_floor_hz=arguments.pitch_floor_hz,
                    pitch_ceiling_hz=arguments.pitch_ceiling_hz,
                    unvoiced_policy=arguments.parselmouth_unvoiced_policy,
                )
            else:
                contour = adapter.extract_torchcrepe_pitch_contour(
                    pcm,
                    arguments.sample_rate_hz,
                    hop_length_samples=arguments.torchcrepe_hop_length_samples,
                    pitch_floor_hz=arguments.pitch_floor_hz,
                    pitch_ceiling_hz=arguments.pitch_ceiling_hz,
                    model=arguments.torchcrepe_model,
                    decoder=arguments.torchcrepe_decoder,
                    batch_size_frames=arguments.torchcrepe_batch_size_frames,
                    pad=arguments.torchcrepe_pad,
                    periodicity_threshold=(arguments.torchcrepe_periodicity_threshold),
                    silence_threshold_dbfs=(
                        arguments.torchcrepe_silence_threshold_dbfs
                    ),
                    numpy_random_seed=arguments.numpy_random_seed,
                    torch_random_seed=arguments.torch_random_seed,
                    unvoiced_policy=arguments.torchcrepe_unvoiced_policy,
                )
        except Exception as error:  # noqa: BLE001
            return {
                "status": "error",
                "backend": backend,
                "failure_code": _sanitized_extraction_failure_code(backend, error),
                "network_socket_attempt_count": socket_attempts[0],
            }
    return {
        "status": "ok",
        "backend": backend,
        "environment": environment,
        "pcm_sha256": _sha256_pcm(pcm),
        "time_seconds": list(contour.time_seconds),
        "f0_hz": list(contour.f0_hz),
        "backend_frame_count": contour.backend_frame_count,
        "voiced_frame_count": contour.voiced_frame_count,
        "unvoiced_frame_count": contour.unvoiced_frame_count,
        "network_socket_attempt_count": socket_attempts[0],
    }


def _parse_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("value must be true or false")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--backend", required=True, choices=("parselmouth", "torchcrepe")
    )
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--pcm-npy", type=Path)
    parser.add_argument("--sample-rate-hz", type=int)
    parser.add_argument("--pitch-floor-hz", type=float)
    parser.add_argument("--pitch-ceiling-hz", type=float)
    parser.add_argument("--parselmouth-time-step-seconds", type=float)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    """Probe the pinned environment or extract one private contour."""

    arguments = _build_parser().parse_args(argv)
    try:
        payload = (
            _probe_payload(arguments.backend)
            if arguments.probe_only
            else _execute_payload(arguments)
        )
    except WorkerError:
        payload = {
            "status": "error",
            "backend": arguments.backend,
            "failure_code": "worker_internal_failure",
            "network_socket_attempt_count": 0,
        }
    print(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
