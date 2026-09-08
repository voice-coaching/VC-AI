"""Pinned, network-isolated MediaPipe lip-landmark adapter."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
from weakref import WeakKeyDictionary,ref

from ..models import Rgb24Sample
from .mediapipe_landmark_worker import BACKEND_REVISION, PROTOCOL_VERSION
from .network_isolated_exec import isolated_command


_PROCESS_CHUNK_BYTES = 64 * 1024
_LIVE_LANDMARK_ADAPTERS=WeakKeyDictionary()
_SUBPROCESS_ADDRESS_SPACE_BYTES = 4 * 1024 * 1024 * 1024
_ISOLATION_LAUNCHER = Path(__file__).with_name("network_isolated_exec.py")
_WORKER_FAILURE_REASON_CODES = frozenset(
    {
        "landmark_frame_timestamps_invalid",
        "landmark_geometry_degenerate",
        "landmark_geometry_out_of_bounds",
        "landmark_model_integrity_failed",
        "landmark_model_unavailable",
        "landmark_worker_protocol_invalid",
        "landmark_worker_unexpected_failure",
        "lip_landmark_backend_unavailable",
        "lip_landmark_inference_failed",
        "network_isolation_backend_unavailable",
    }
)


class LipLandmarkRuntimeError(RuntimeError):
    """Path-free reason for an unavailable or failed landmark runtime."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class MediaPipeLandmarkPolicy:
    """Injected runtime thresholds and resource bounds."""

    minimum_face_detection_confidence: float
    minimum_face_presence_confidence: float
    minimum_tracking_confidence: float
    maximum_frame_count: int
    maximum_total_rgb_bytes: int
    subprocess_timeout_seconds: float
    maximum_subprocess_output_bytes: int

    def __post_init__(self) -> None:
        for name in (
            "minimum_face_detection_confidence",
            "minimum_face_presence_confidence",
            "minimum_tracking_confidence",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"{name} must be finite and in [0, 1]")
        for name in (
            "maximum_frame_count",
            "maximum_total_rgb_bytes",
            "maximum_subprocess_output_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(self.subprocess_timeout_seconds, bool)
            or not isinstance(self.subprocess_timeout_seconds, (int, float))
            or not math.isfinite(float(self.subprocess_timeout_seconds))
            or self.subprocess_timeout_seconds <= 0
        ):
            raise ValueError("subprocess_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class MediaPipeLipGeometry:
    """Minimal normalized visible lip geometry for one detected face."""

    upper_lip_y: float
    lower_lip_y: float
    left_corner_x: float
    right_corner_x: float

    def __post_init__(self) -> None:
        for name in (
            "upper_lip_y",
            "lower_lip_y",
            "left_corner_x",
            "right_corner_x",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be normalized")
        if self.lower_lip_y < self.upper_lip_y:
            raise ValueError("lower lip must not precede upper lip")
        if self.right_corner_x <= self.left_corner_x:
            raise ValueError("lip width must be positive")

    @property
    def aperture_ratio(self) -> float:
        return (self.lower_lip_y - self.upper_lip_y) / (
            self.right_corner_x - self.left_corner_x
        )


@dataclass(frozen=True, slots=True)
class MediaPipeFaceFrame:
    """Face count and optional one-face geometry at a decoded timestamp."""

    timestamp_ms: int
    face_count: int
    lip_geometry: MediaPipeLipGeometry | None

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_ms, bool) or self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        if isinstance(self.face_count, bool) or self.face_count < 0:
            raise ValueError("face_count must be non-negative")
        if (self.face_count == 1) != (self.lip_geometry is not None):
            raise ValueError("lip geometry is permitted for exactly one face")


@dataclass(frozen=True, slots=True)
class MediaPipeLandmarkBatch:
    """Privacy-safe aggregate output; no frame pixels or model path remain."""

    asset_handle_id: str
    container_sha256: str
    backend_revision: str
    model_sha256: str
    network_isolation: str
    frames: tuple[MediaPipeFaceFrame, ...]

    def __post_init__(self) -> None:
        for name in ("asset_handle_id", "container_sha256", "model_sha256"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256")
        if self.backend_revision != BACKEND_REVISION:
            raise ValueError("landmark backend revision differs")
        if self.network_isolation != "seccomp_socket_denied":
            raise ValueError("landmark network isolation differs")
        if not self.frames:
            raise ValueError("landmark batch must contain frames")
        timestamps = tuple(frame.timestamp_ms for frame in self.frames)
        if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
            raise ValueError("landmark timestamps must be strictly increasing")

    @property
    def single_face_coverage(self) -> float:
        return sum(frame.face_count == 1 for frame in self.frames) / len(self.frames)

    @property
    def multiple_face_observed(self) -> bool:
        return any(frame.face_count > 1 for frame in self.frames)

    def receipt_json_object(self) -> dict[str, object]:
        """Return a raw-media-free canonical summary for lineage receipts."""

        return {
            "schema_version": "voice_coach.mediapipe_landmark_evidence.v1",
            "asset_handle_id": self.asset_handle_id,
            "container_sha256": self.container_sha256,
            "backend_revision": self.backend_revision,
            "model_sha256": self.model_sha256,
            "network_isolation": self.network_isolation,
            "frames": [
                {
                    "timestamp_ms": frame.timestamp_ms,
                    "face_count": frame.face_count,
                    "lip_geometry": (
                        None
                        if frame.lip_geometry is None
                        else {
                            "upper_lip_y": frame.lip_geometry.upper_lip_y,
                            "lower_lip_y": frame.lip_geometry.lower_lip_y,
                            "left_corner_x": frame.lip_geometry.left_corner_x,
                            "right_corner_x": frame.lip_geometry.right_corner_x,
                        }
                    ),
                }
                for frame in self.frames
            ],
        }

    @property
    def evidence_sha256(self) -> str:
        payload = json.dumps(
            self.receipt_json_object(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class MediaPipeLipLandmarkAdapter:
    """Invoke the pinned worker over bounded RGB frames and parse strict output."""

    def __init__(
        self,
        *,
        model_path: str | Path,
        expected_model_sha256: str,
        policy: MediaPipeLandmarkPolicy,
        model_descriptor: int | None = None,
        worker_python: str | Path | None = None,
        repository_root: str | Path | None = None,
    ) -> None:
        if (
            len(expected_model_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_model_sha256
            )
        ):
            raise ValueError("expected_model_sha256 must be lowercase SHA-256")
        if model_descriptor is not None and (
            isinstance(model_descriptor, bool)
            or not isinstance(model_descriptor, int)
            or model_descriptor < 3
        ):
            raise ValueError("model_descriptor must be an integer >= 3")
        self._model_path = Path(model_path)
        self._model_descriptor = model_descriptor
        self._expected_model_sha256 = expected_model_sha256
        self._policy = policy
        self._worker_python = Path(worker_python or sys.executable)
        self._repository_root = Path(
            repository_root
            if repository_root is not None
            else Path(__file__).resolve().parents[4]
        )
        _LIVE_LANDMARK_ADAPTERS[self]=(os.getpid(),self._expected_model_sha256,self._policy,ref(self))

    @property
    def production_identity(self) -> tuple[str, MediaPipeLandmarkPolicy]:
        row=_LIVE_LANDMARK_ADAPTERS.get(self)
        if row is None or row[0]!=os.getpid() or row[1]!=self._expected_model_sha256 or row[2]!=self._policy or row[3]() is not self: raise LipLandmarkRuntimeError("landmark_adapter_capability_invalid")
        return self._expected_model_sha256, self._policy

    def detect(self, sample: Rgb24Sample) -> MediaPipeLandmarkBatch:
        if len(sample.frames) > self._policy.maximum_frame_count:
            raise LipLandmarkRuntimeError("landmark_frame_limit_exceeded")
        total_rgb_bytes = sum(len(frame.rgb24) for frame in sample.frames)
        if total_rgb_bytes > self._policy.maximum_total_rgb_bytes:
            raise LipLandmarkRuntimeError("landmark_frame_bytes_exceeded")
        environment = {
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(self._repository_root / "src"),
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "TF_NUM_INTRAOP_THREADS": "1",
            "TF_NUM_INTEROP_THREADS": "1",
        }
        descriptor: int | None = None
        descriptor_identity: tuple[int, int, int, int, int, int] | None = None
        try:
            if self._model_descriptor is None:
                model_locator = str(self._model_path)
                pass_fds: tuple[int, ...] = ()
            else:
                descriptor, descriptor_identity = (
                    self._duplicate_read_only_model_descriptor(
                        self._model_descriptor
                    )
                )
                model_locator = f"fd:{descriptor}"
                pass_fds = (descriptor,)
            request = {
                "protocol_version": PROTOCOL_VERSION,
                "model_path": model_locator,
                "expected_model_sha256": self._expected_model_sha256,
                "options": {
                    "maximum_frame_count": self._policy.maximum_frame_count,
                    "maximum_total_rgb_bytes": (
                        self._policy.maximum_total_rgb_bytes
                    ),
                    "minimum_face_detection_confidence": (
                        self._policy.minimum_face_detection_confidence
                    ),
                    "minimum_face_presence_confidence": (
                        self._policy.minimum_face_presence_confidence
                    ),
                    "minimum_tracking_confidence": (
                        self._policy.minimum_tracking_confidence
                    ),
                },
                "frames": [
                    {
                        "timestamp_us": frame.timestamp_us,
                        "width": frame.width,
                        "height": frame.height,
                        "rgb24_base64": base64.b64encode(frame.rgb24).decode(
                            "ascii"
                        ),
                    }
                    for frame in sample.frames
                ],
            }
            request_bytes = json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            return_code, stdout, _stderr = self._run_bounded_worker(
                request_bytes=request_bytes,
                environment=environment,
                pass_fds=pass_fds,
            )
            if (
                descriptor is not None
                and self._descriptor_identity(os.fstat(descriptor))
                != descriptor_identity
            ):
                raise LipLandmarkRuntimeError(
                    "landmark_model_integrity_failed"
                )
        except OSError:
            raise LipLandmarkRuntimeError("landmark_model_unavailable") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        try:
            response = json.loads(stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LipLandmarkRuntimeError("lip_landmark_worker_invalid") from error
        if not isinstance(response, dict):
            raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
        if return_code != 0 or response.get("status") != "completed":
            reason = response.get("reason_code")
            if reason not in _WORKER_FAILURE_REASON_CODES:
                reason = "lip_landmark_inference_failed"
            assert isinstance(reason, str)
            raise LipLandmarkRuntimeError(reason)
        return self._parse_completed(sample=sample, response=response)

    def _run_bounded_worker(
        self,
        *,
        request_bytes: bytes,
        environment: dict[str, str],
        pass_fds: tuple[int, ...],
    ) -> tuple[int, bytes, bytes]:
        """Exchange bytes without ever buffering unbounded worker output."""

        worker_python = self._worker_python.absolute()
        if not worker_python.is_file():
            raise LipLandmarkRuntimeError(
                "lip_landmark_backend_unavailable"
            )
        target = [
            str(worker_python),
            "-m",
            (
                "voice_coach.pronunciation_attempts.adapters."
                "mediapipe_landmark_worker"
            ),
        ]
        command = isolated_command(
            launcher_python=sys.executable,
            launcher_path=str(_ISOLATION_LAUNCHER),
            target=target,
            cpu_seconds=max(
                1, math.ceil(self._policy.subprocess_timeout_seconds) + 2
            ),
            address_space_bytes=_SUBPROCESS_ADDRESS_SPACE_BYTES,
        )
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=pass_fds,
                close_fds=True,
                shell=False,
                cwd=str(self._repository_root),
                env=environment,
                start_new_session=True,
            )
        except (OSError, ValueError):
            raise LipLandmarkRuntimeError(
                "lip_landmark_backend_unavailable"
            ) from None
        if process.stdin is None or process.stdout is None or process.stderr is None:
            self._kill_process_group(process)
            raise LipLandmarkRuntimeError("lip_landmark_backend_unavailable")

        stdout = bytearray()
        stderr = bytearray()
        input_offset = 0
        deadline = time.monotonic() + self._policy.subprocess_timeout_seconds
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
                    raise LipLandmarkRuntimeError("lip_landmark_worker_timeout")
                events = selector.select(timeout=min(remaining, 0.1))
                if not events and process.poll() is not None:
                    events = [
                        (key, selectors.EVENT_READ)
                        for key in tuple(selector.get_map().values())
                    ]
                for key, _ in events:
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
                        raise LipLandmarkRuntimeError(
                            "lip_landmark_worker_invalid"
                        ) from None
                    if not payload:
                        selector.unregister(key.fileobj)
                        continue
                    destination = stdout if key.data == "stdout" else stderr
                    destination.extend(payload)
                    if (
                        len(stdout) + len(stderr)
                        > self._policy.maximum_subprocess_output_bytes
                    ):
                        raise LipLandmarkRuntimeError(
                            "lip_landmark_worker_output_exceeded"
                        )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LipLandmarkRuntimeError("lip_landmark_worker_timeout")
            try:
                return_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise LipLandmarkRuntimeError(
                    "lip_landmark_worker_timeout"
                ) from None
            return return_code, bytes(stdout), bytes(stderr)
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

    @staticmethod
    def _duplicate_read_only_model_descriptor(
        descriptor: int,
    ) -> tuple[int, tuple[int, int, int, int, int, int]]:
        """Duplicate one caller-owned model FD for an explicit child pass."""

        duplicate = -1
        try:
            duplicate = os.dup(descriptor)
            os.set_inheritable(duplicate, False)
            flags = fcntl.fcntl(duplicate, fcntl.F_GETFL)
            metadata = os.fstat(duplicate)
            if (
                flags & os.O_ACCMODE != os.O_RDONLY
                or not stat.S_ISREG(metadata.st_mode)
                or not 0 < metadata.st_size <= 64 * 1024 * 1024
            ):
                raise OSError
            return duplicate, MediaPipeLipLandmarkAdapter._descriptor_identity(
                metadata
            )
        except OSError:
            if duplicate >= 0:
                os.close(duplicate)
            raise LipLandmarkRuntimeError("landmark_model_unavailable") from None

    @staticmethod
    def _descriptor_identity(
        metadata: os.stat_result,
    ) -> tuple[int, int, int, int, int, int]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

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
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass

    def _parse_completed(
        self, *, sample: Rgb24Sample, response: dict[str, object]
    ) -> MediaPipeLandmarkBatch:
        if set(response) != {
            "protocol_version",
            "status",
            "backend_revision",
            "model_sha256",
            "network_isolation",
            "frames",
        }:
            raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
        if response["protocol_version"] != PROTOCOL_VERSION:
            raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
        if response["model_sha256"] != self._expected_model_sha256:
            raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
        raw_frames = response["frames"]
        if not isinstance(raw_frames, list) or len(raw_frames) != len(sample.frames):
            raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
        frames: list[MediaPipeFaceFrame] = []
        for raw, decoded in zip(raw_frames, sample.frames, strict=True):
            if not isinstance(raw, dict) or set(raw) != {
                "timestamp_ms",
                "face_count",
                "lip_geometry",
            }:
                raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
            if raw["timestamp_ms"] != decoded.timestamp_us // 1000:
                raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
            face_count = raw["face_count"]
            if isinstance(face_count, bool) or not isinstance(face_count, int):
                raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
            geometry_value = raw["lip_geometry"]
            geometry = None
            if geometry_value is not None:
                if not isinstance(geometry_value, dict) or set(geometry_value) != {
                    "upper_lip_y",
                    "lower_lip_y",
                    "left_corner_x",
                    "right_corner_x",
                }:
                    raise LipLandmarkRuntimeError("lip_landmark_worker_invalid")
                try:
                    geometry = MediaPipeLipGeometry(
                        upper_lip_y=float(geometry_value["upper_lip_y"]),
                        lower_lip_y=float(geometry_value["lower_lip_y"]),
                        left_corner_x=float(geometry_value["left_corner_x"]),
                        right_corner_x=float(geometry_value["right_corner_x"]),
                    )
                except (TypeError, ValueError) as error:
                    raise LipLandmarkRuntimeError(
                        "lip_landmark_worker_invalid"
                    ) from error
            try:
                frames.append(
                    MediaPipeFaceFrame(
                        timestamp_ms=int(raw["timestamp_ms"]),
                        face_count=face_count,
                        lip_geometry=geometry,
                    )
                )
            except ValueError as error:
                raise LipLandmarkRuntimeError(
                    "lip_landmark_worker_invalid"
                ) from error
        try:
            return MediaPipeLandmarkBatch(
                asset_handle_id=sample.asset_handle_id,
                container_sha256=sample.container_sha256,
                backend_revision=str(response["backend_revision"]),
                model_sha256=str(response["model_sha256"]),
                network_isolation=str(response["network_isolation"]),
                frames=tuple(frames),
            )
        except ValueError as error:
            raise LipLandmarkRuntimeError("lip_landmark_worker_invalid") from error


__all__ = [
    "LipLandmarkRuntimeError",
    "MediaPipeFaceFrame",
    "MediaPipeLandmarkBatch",
    "MediaPipeLandmarkPolicy",
    "MediaPipeLipGeometry",
    "MediaPipeLipLandmarkAdapter",
]
