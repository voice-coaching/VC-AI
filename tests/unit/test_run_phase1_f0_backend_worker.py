from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys

import numpy as np
import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "run_phase1_f0_backend_worker.py"
SPEC = importlib.util.spec_from_file_location("run_phase1_f0_backend_worker", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
TORCHCREPE_PYTHON = Path(__file__).parents[2] / ".venv-paper-torchcrepe/bin/python"


def _write_private_npy(path: Path, samples: np.ndarray) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        np.save(handle, samples, allow_pickle=False)
    os.chmod(path, 0o600)


def _worker_command(path: Path) -> list[str]:
    return [
        str(TORCHCREPE_PYTHON),
        str(SCRIPT),
        "--backend",
        "torchcrepe",
        "--pcm-npy",
        str(path),
        "--sample-rate-hz",
        "16000",
        "--pitch-floor-hz",
        "75",
        "--pitch-ceiling-hz",
        "500",
        "--parselmouth-time-step-seconds",
        "0.01",
        "--parselmouth-unvoiced-policy",
        "keep_zero",
        "--torchcrepe-hop-length-samples",
        "160",
        "--torchcrepe-model",
        "tiny",
        "--torchcrepe-decoder",
        "viterbi",
        "--torchcrepe-batch-size-frames",
        "32",
        "--torchcrepe-pad",
        "true",
        "--torchcrepe-periodicity-threshold",
        "0.21",
        "--torchcrepe-silence-threshold-dbfs",
        "-80",
        "--numpy-random-seed",
        "20260729",
        "--torch-random-seed",
        "20260729",
        "--torchcrepe-unvoiced-policy",
        "keep_zero",
    ]


def _cpu_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "CUDA_VISIBLE_DEVICES": "",
            "HIP_VISIBLE_DEVICES": "",
            "ROCR_VISIBLE_DEVICES": "",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "PYTHONNOUSERSITE": "1",
            "HF_HUB_OFFLINE": "1",
        }
    )
    return environment


def test_socket_guard_preserves_type_and_blocks_ip_connect() -> None:
    original_socket_type = socket.socket

    with MODULE._blocked_network_sockets() as attempts:
        assert isinstance(socket.socket, type)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
            with pytest.raises(OSError, match="network disabled"):
                handle.connect(("127.0.0.1", 9))

    assert attempts == [1]
    assert socket.socket is original_socket_type


@pytest.mark.skipif(
    not TORCHCREPE_PYTHON.exists(),
    reason="pinned torchcrepe research environment is unavailable",
)
@pytest.mark.parametrize(
    ("sample_count", "expected_frame_count"),
    [
        (16_000, 101),
        (16_001, 101),
        (16_159, 101),
        (16_160, 102),
    ],
)
def test_actual_torchcrepe_worker_accepts_divisible_and_nondivisible_lengths(
    tmp_path: Path,
    sample_count: int,
    expected_frame_count: int,
) -> None:
    time = np.arange(sample_count, dtype=np.float32) / np.float32(16_000)
    samples = np.asarray(
        np.float32(0.2) * np.sin(np.float32(2.0 * np.pi * 180.0) * time),
        dtype=np.float32,
    )
    path = tmp_path / f"synthetic-{sample_count}.npy"
    _write_private_npy(path, samples)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    result = subprocess.run(
        _worker_command(path),
        check=False,
        capture_output=True,
        env=_cpu_environment(),
        timeout=300,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["status"] == "ok"
    assert payload["backend"] == "torchcrepe"
    assert payload["backend_frame_count"] == expected_frame_count
    assert payload["network_socket_attempt_count"] == 0
    assert payload["environment"] == MODULE.EXPECTED_ENVIRONMENTS["torchcrepe"]
    assert "synthetic-" not in json.dumps(payload, sort_keys=True)


def test_sanitized_failure_mapper_distinguishes_guard_type_regression() -> None:
    result = MODULE._sanitized_extraction_failure_code(
        "torchcrepe",
        TypeError("synthetic private detail"),
    )

    assert result == "torchcrepe_unexpected_type_error"
    assert "synthetic" not in result
