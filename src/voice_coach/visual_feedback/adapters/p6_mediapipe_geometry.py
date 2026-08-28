"""Bounded adapter for the isolated P6 MediaPipe calibration worker."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from .aihub68_geometry_dataset import Aihub68GeometrySample
from .p6_mediapipe_geometry_worker import (
    BACKEND_REVISION,
    MEDIAPIPE_LIP_CANDIDATE_INDICES,
    PROTOCOL_VERSION,
)
from ...pronunciation_attempts.adapters.network_isolated_exec import isolated_command


_ISOLATION_LAUNCHER = Path(__file__).parents[2] / (
    "pronunciation_attempts/adapters/network_isolated_exec.py"
)
_PROCESS_CHUNK_BYTES = 64 * 1024
_MAXIMUM_OUTPUT_BYTES = 4 * 1024 * 1024
_SUBPROCESS_ADDRESS_SPACE_BYTES = 4 * 1024 * 1024 * 1024


class P6MediaPipeRuntimeError(RuntimeError):
    """Path-free reason for calibration worker failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class P6MediaPipeObservation:
    """One restricted candidate observation joined by an opaque digest."""

    sample_sha256: str
    face_count: int
    candidate_points_xy: tuple[tuple[float, float], ...] | None


@dataclass(frozen=True, slots=True)
class P6MediaPipeBatch:
    """Inference result and immutable worker/model identity."""

    observations: tuple[P6MediaPipeObservation, ...]
    backend_revision: str
    model_sha256: str
    network_isolation: str
    candidate_indices: tuple[int, ...]


class P6MediaPipeGeometryAdapter:
    """Run all samples in bounded batches without exposing archive paths."""

    def __init__(
        self,
        *,
        model_path: str | Path,
        expected_model_sha256: str,
        worker_python: str | Path | None = None,
        repository_root: str | Path | None = None,
        batch_size: int = 96,
        timeout_seconds_per_batch: float = 90.0,
        maximum_image_dimension: int = 640,
    ) -> None:
        if (
            len(expected_model_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_model_sha256
            )
        ):
            raise ValueError("expected_model_sha256 must be lowercase SHA-256")
        if not 1 <= batch_size <= 128:
            raise ValueError("batch_size must be in [1, 128]")
        if not 256 <= maximum_image_dimension <= 1024:
            raise ValueError("maximum_image_dimension must be in [256, 1024]")
        if (
            isinstance(timeout_seconds_per_batch, bool)
            or not isinstance(timeout_seconds_per_batch, (int, float))
            or not math.isfinite(float(timeout_seconds_per_batch))
            or timeout_seconds_per_batch <= 0
        ):
            raise ValueError("timeout_seconds_per_batch must be positive")
        self._model_path = Path(model_path)
        self._expected_model_sha256 = expected_model_sha256
        self._worker_python = Path(worker_python or sys.executable)
        self._repository_root = Path(
            repository_root
            if repository_root is not None
            else Path(__file__).resolve().parents[4]
        )
        self._batch_size = batch_size
        self._timeout_seconds = float(timeout_seconds_per_batch)
        self._maximum_image_dimension = maximum_image_dimension

    def infer(
        self, samples: tuple[Aihub68GeometrySample, ...]
    ) -> P6MediaPipeBatch:
        """Infer the fixed candidate set for each sample exactly once."""

        if not samples:
            raise P6MediaPipeRuntimeError("p6_samples_missing")
        observations: list[P6MediaPipeObservation] = []
        for offset in range(0, len(samples), self._batch_size):
            observations.extend(
                self._infer_batch(samples[offset : offset + self._batch_size])
            )
        if len(observations) != len(samples):
            raise P6MediaPipeRuntimeError("p6_worker_output_invalid")
        return P6MediaPipeBatch(
            observations=tuple(observations),
            backend_revision=BACKEND_REVISION,
            model_sha256=self._expected_model_sha256,
            network_isolation="seccomp_socket_denied",
            candidate_indices=MEDIAPIPE_LIP_CANDIDATE_INDICES,
        )

    def _infer_batch(
        self, samples: tuple[Aihub68GeometrySample, ...]
    ) -> tuple[P6MediaPipeObservation, ...]:
        request = {
            "protocol_version": PROTOCOL_VERSION,
            "model_path": str(self._model_path),
            "expected_model_sha256": self._expected_model_sha256,
            "minimum_face_detection_confidence": 0.5,
            "minimum_face_presence_confidence": 0.5,
            "maximum_image_dimension": self._maximum_image_dimension,
            "frames": [
                {
                    "sample_sha256": sample.sample_sha256,
                    "expected_width": sample.width,
                    "expected_height": sample.height,
                    "jpeg_base64": base64.b64encode(sample.jpeg_bytes).decode("ascii"),
                }
                for sample in samples
            ],
        }
        request_bytes = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return_code, stdout = self._run_worker(request_bytes)
        try:
            response = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise P6MediaPipeRuntimeError("p6_worker_output_invalid") from None
        if not isinstance(response, dict):
            raise P6MediaPipeRuntimeError("p6_worker_output_invalid")
        if return_code != 0 or response.get("status") != "completed":
            reason = response.get("reason_code")
            if not isinstance(reason, str) or not reason.startswith("p6_"):
                reason = "p6_landmark_inference_failed"
            raise P6MediaPipeRuntimeError(reason)
        return self._parse_response(samples=samples, response=response)

    def _run_worker(self, request_bytes: bytes) -> tuple[int, bytes]:
        worker_python = self._worker_python.absolute()
        if not worker_python.is_file():
            raise P6MediaPipeRuntimeError("p6_landmark_backend_unavailable")
        target = [
            str(worker_python),
            "-m",
            "voice_coach.visual_feedback.adapters.p6_mediapipe_geometry_worker",
        ]
        command = isolated_command(
            launcher_python=sys.executable,
            launcher_path=str(_ISOLATION_LAUNCHER),
            target=target,
            cpu_seconds=max(1, math.ceil(self._timeout_seconds) + 2),
            address_space_bytes=_SUBPROCESS_ADDRESS_SPACE_BYTES,
        )
        environment = {
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(self._repository_root / "src"),
        }
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                shell=False,
                cwd=self._repository_root,
                env=environment,
                start_new_session=True,
            )
        except (OSError, ValueError):
            raise P6MediaPipeRuntimeError("p6_landmark_backend_unavailable") from None
        if process.stdin is None or process.stdout is None or process.stderr is None:
            self._kill_process_group(process)
            raise P6MediaPipeRuntimeError("p6_landmark_backend_unavailable")

        stdout = bytearray()
        stderr_count = 0
        input_offset = 0
        deadline = time.monotonic() + self._timeout_seconds
        selector = selectors.DefaultSelector()
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise P6MediaPipeRuntimeError("p6_worker_timeout")
                events = selector.select(timeout=min(remaining, 0.1))
                if not events and process.poll() is not None:
                    events = [
                        (key, selectors.EVENT_READ)
                        for key in tuple(selector.get_map().values())
                    ]
                for key, _mask in events:
                    if key.data == "stdin":
                        try:
                            written = os.write(
                                key.fd,
                                request_bytes[
                                    input_offset : input_offset + _PROCESS_CHUNK_BYTES
                                ],
                            )
                        except BrokenPipeError:
                            written = 0
                            input_offset = len(request_bytes)
                        input_offset += written
                        if input_offset >= len(request_bytes):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    try:
                        payload = os.read(key.fd, _PROCESS_CHUNK_BYTES)
                    except OSError:
                        raise P6MediaPipeRuntimeError("p6_worker_output_invalid") from None
                    if not payload:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        stdout.extend(payload)
                    else:
                        stderr_count += len(payload)
                    if len(stdout) + stderr_count > _MAXIMUM_OUTPUT_BYTES:
                        raise P6MediaPipeRuntimeError("p6_worker_output_exceeded")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise P6MediaPipeRuntimeError("p6_worker_timeout")
            try:
                return_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise P6MediaPipeRuntimeError("p6_worker_timeout") from None
            return return_code, bytes(stdout)
        except BaseException:
            self._kill_process_group(process)
            raise
        finally:
            selector.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except OSError:
                    pass

    def _parse_response(
        self,
        *,
        samples: tuple[Aihub68GeometrySample, ...],
        response: dict[str, object],
    ) -> tuple[P6MediaPipeObservation, ...]:
        if set(response) != {
            "protocol_version",
            "status",
            "backend_revision",
            "model_sha256",
            "network_isolation",
            "candidate_indices",
            "frames",
        } or any(
            (
                response["protocol_version"] != PROTOCOL_VERSION,
                response["backend_revision"] != BACKEND_REVISION,
                response["model_sha256"] != self._expected_model_sha256,
                response["network_isolation"] != "seccomp_socket_denied",
                response["candidate_indices"]
                != list(MEDIAPIPE_LIP_CANDIDATE_INDICES),
            )
        ):
            raise P6MediaPipeRuntimeError("p6_worker_output_invalid")
        frames = response["frames"]
        if not isinstance(frames, list) or len(frames) != len(samples):
            raise P6MediaPipeRuntimeError("p6_worker_output_invalid")
        observations: list[P6MediaPipeObservation] = []
        for sample, frame in zip(samples, frames, strict=True):
            if not isinstance(frame, dict) or set(frame) != {
                "sample_sha256",
                "face_count",
                "candidate_points_xy",
            }:
                raise P6MediaPipeRuntimeError("p6_worker_output_invalid")
            face_count = frame["face_count"]
            if (
                frame["sample_sha256"] != sample.sample_sha256
                or isinstance(face_count, bool)
                or not isinstance(face_count, int)
                or face_count < 0
            ):
                raise P6MediaPipeRuntimeError("p6_worker_output_invalid")
            points_value = frame["candidate_points_xy"]
            points: tuple[tuple[float, float], ...] | None = None
            if points_value is not None:
                if face_count != 1 or not isinstance(points_value, list) or len(
                    points_value
                ) != len(MEDIAPIPE_LIP_CANDIDATE_INDICES):
                    raise P6MediaPipeRuntimeError("p6_worker_output_invalid")
                parsed: list[tuple[float, float]] = []
                for point in points_value:
                    if not isinstance(point, list) or len(point) != 2:
                        raise P6MediaPipeRuntimeError("p6_worker_output_invalid")
                    try:
                        x, y = float(point[0]), float(point[1])
                    except (TypeError, ValueError):
                        raise P6MediaPipeRuntimeError(
                            "p6_worker_output_invalid"
                        ) from None
                    if not all(math.isfinite(value) and 0 <= value <= 1 for value in (x, y)):
                        raise P6MediaPipeRuntimeError("p6_worker_output_invalid")
                    parsed.append((x, y))
                points = tuple(parsed)
            elif face_count == 1:
                raise P6MediaPipeRuntimeError("p6_worker_output_invalid")
            observations.append(
                P6MediaPipeObservation(
                    sample_sha256=sample.sample_sha256,
                    face_count=face_count,
                    candidate_points_xy=points,
                )
            )
        return tuple(observations)

    @staticmethod
    def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                try:
                    process.kill()
                except OSError:
                    pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


__all__ = [
    "P6MediaPipeBatch",
    "P6MediaPipeGeometryAdapter",
    "P6MediaPipeObservation",
    "P6MediaPipeRuntimeError",
]
