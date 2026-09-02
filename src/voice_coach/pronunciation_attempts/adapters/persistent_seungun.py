"""Bounded supervisor for a warm, network-isolated Seungun model process."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import threading
import time
from typing import Mapping, Sequence
from uuid import uuid4

from .bounded_isolated_process import IsolatedProcessError, terminate_isolated_process
from .network_isolated_exec import isolated_command
from .runtime import PronunciationAttemptRuntimeError


_SERVER_PROTOCOL_VERSION = "voice_coach.seungun_runtime_server.v1"
_DEVICE_PATTERN = re.compile(r"^(?:cpu|cuda(?::[0-9]+)?)$")
_LAUNCHER = Path(__file__).with_name("network_isolated_exec.py")
_MAXIMUM_REQUEST_BYTES = 65_536
_READY_BYTES = 4096


class PersistentIsolatedFrozenSeungunDetectorAdapter:
    """Reuse a frozen model in one isolated child and recycle it predictably."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        device: str,
        worker_python: str | Path | None = None,
        startup_timeout_seconds: float = 300.0,
        request_timeout_seconds: float = 180.0,
        maximum_output_bytes: int = 4 * 1024 * 1024,
        maximum_requests_per_process: int = 100,
        start_immediately: bool = False,
        server_target: Sequence[str] | None = None,
    ) -> None:
        root = Path(repository_root)
        python = Path(worker_python or sys.executable).absolute()
        if (
            not root.is_absolute()
            or _DEVICE_PATTERN.fullmatch(device) is None
            or not math.isfinite(startup_timeout_seconds)
            or not 1 <= startup_timeout_seconds <= 900
            or not math.isfinite(request_timeout_seconds)
            or not 1 <= request_timeout_seconds <= 600
            or not 1 <= maximum_output_bytes <= 16 * 1024 * 1024
            or not 1 <= maximum_requests_per_process <= 1000
        ):
            raise ValueError("seungun_persistent_worker_configuration_invalid")
        if server_target is not None and (
            not server_target
            or not os.path.isabs(server_target[0])
            or any(not isinstance(item, str) or not item for item in server_target)
        ):
            raise ValueError("seungun_persistent_worker_configuration_invalid")
        self._root = root
        self._device = device
        self._worker_python = python
        self._startup_timeout_seconds = startup_timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._maximum_output_bytes = maximum_output_bytes
        self._maximum_requests_per_process = maximum_requests_per_process
        self._server_target = tuple(server_target) if server_target is not None else None
        self._process: subprocess.Popen[bytes] | None = None
        self._stdout_buffer = bytearray()
        self._requests = 0
        self._lock = threading.Lock()
        if start_immediately:
            self.start()

    def start(self) -> None:
        with self._lock:
            self._ensure_started()

    def analyze(
        self,
        *,
        audio_path: str | Path,
        expected_phones: Sequence[str],
    ) -> Mapping[str, object]:
        phones = list(expected_phones)
        if (
            not phones
            or len(phones) > 4096
            or any(
                not isinstance(phone, str) or not phone or len(phone) > 16
                for phone in phones
            )
        ):
            raise PronunciationAttemptRuntimeError("seungun_worker_invalid")
        request_id = str(uuid4())
        request = {
            "protocol_version": _SERVER_PROTOCOL_VERSION,
            "request_id": request_id,
            "audio_path": str(Path(audio_path).absolute()),
            "expected_phones": phones,
        }
        payload = (
            json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
        if len(payload) > _MAXIMUM_REQUEST_BYTES:
            raise PronunciationAttemptRuntimeError("seungun_worker_invalid")

        with self._lock:
            try:
                self._ensure_started()
                process = self._process
                if process is None or process.stdin is None:
                    raise IsolatedProcessError("isolated_process_unavailable")
                self._write_all(process, payload, self._request_timeout_seconds)
                response = self._read_json_line(
                    process,
                    timeout_seconds=self._request_timeout_seconds,
                    maximum_bytes=self._maximum_output_bytes,
                )
                self._requests += 1
            except (IsolatedProcessError, OSError, ValueError):
                self._terminate()
                raise PronunciationAttemptRuntimeError("seungun_worker_failed") from None

            completed_keys = {
                "protocol_version",
                "request_id",
                "status",
                "network_isolation",
                "analysis",
            }
            failed_keys = {
                "protocol_version",
                "request_id",
                "status",
                "reason_code",
            }
            if (
                isinstance(response, dict)
                and set(response) == completed_keys
                and response["protocol_version"] == _SERVER_PROTOCOL_VERSION
                and response["request_id"] == request_id
                and response["status"] == "completed"
                and response["network_isolation"] == "seccomp_socket_denied"
                and isinstance(response["analysis"], Mapping)
            ):
                return dict(response["analysis"])
            if (
                isinstance(response, dict)
                and set(response) == failed_keys
                and response["protocol_version"] == _SERVER_PROTOCOL_VERSION
                and response["request_id"] == request_id
                and response["status"] == "failed"
                and isinstance(response["reason_code"], str)
                and 0 < len(response["reason_code"]) <= 100
            ):
                raise PronunciationAttemptRuntimeError("seungun_worker_failed")
            self._terminate()
            raise PronunciationAttemptRuntimeError("seungun_worker_invalid")

    def close(self) -> None:
        with self._lock:
            self._terminate()

    def cancel_current(self) -> None:
        """Interrupt the current inference without waiting for its request lock."""

        process = self._process
        if process is not None and process.poll() is None:
            terminate_isolated_process(process)

    def _ensure_started(self) -> None:
        if (
            self._process is not None
            and self._process.poll() is None
            and self._requests < self._maximum_requests_per_process
        ):
            return
        self._terminate()
        if not self._worker_python.is_file() or not self._root.is_dir():
            raise IsolatedProcessError("isolated_process_unavailable")
        target = list(
            self._server_target
            or (
                str(self._worker_python),
                "-m",
                "voice_coach.pronunciation_attempts.adapters.seungun_runtime_server",
                "--device",
                self._device,
            )
        )
        command = isolated_command(
            launcher_python=sys.executable,
            launcher_path=str(_LAUNCHER),
            target=target,
            cpu_seconds=3600,
            address_space_bytes=64 * 1024 * 1024 * 1024,
            allow_local_sockets=True,
        )
        environment = {
            "DO_NOT_TRACK": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_OFFLINE": "1",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join(
                (str(self._root / "src"), str(self._root / "seungun/src"))
            ),
            "TOKENIZERS_PARALLELISM": "false",
            "TRANSFORMERS_OFFLINE": "1",
        }
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                shell=False,
                cwd=str(self._root),
                env=environment,
                start_new_session=True,
                bufsize=0,
            )
        except (OSError, ValueError):
            raise IsolatedProcessError("isolated_process_unavailable") from None
        if process.stdin is None or process.stdout is None:
            terminate_isolated_process(process)
            raise IsolatedProcessError("isolated_process_unavailable")
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        self._process = process
        self._stdout_buffer.clear()
        self._requests = 0
        try:
            ready = self._read_json_line(
                process,
                timeout_seconds=self._startup_timeout_seconds,
                maximum_bytes=_READY_BYTES,
            )
        except BaseException:
            self._terminate()
            raise
        if not (
            isinstance(ready, dict)
            and set(ready) == {
                "protocol_version",
                "status",
                "network_isolation",
            }
            and ready["protocol_version"] == _SERVER_PROTOCOL_VERSION
            and ready["status"] == "ready"
            and ready["network_isolation"] == "seccomp_socket_denied"
        ):
            self._terminate()
            raise IsolatedProcessError("isolated_process_protocol_invalid")

    def _read_json_line(
        self,
        process: subprocess.Popen[bytes],
        *,
        timeout_seconds: float,
        maximum_bytes: int,
    ) -> object:
        line = self._read_line(
            process,
            timeout_seconds=timeout_seconds,
            maximum_bytes=maximum_bytes,
        )
        try:
            return json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise IsolatedProcessError("isolated_process_protocol_invalid") from None

    def _read_line(
        self,
        process: subprocess.Popen[bytes],
        *,
        timeout_seconds: float,
        maximum_bytes: int,
    ) -> bytes:
        if process.stdout is None:
            raise IsolatedProcessError("isolated_process_unavailable")
        deadline = time.monotonic() + timeout_seconds
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                newline = self._stdout_buffer.find(b"\n")
                if newline >= 0:
                    line = bytes(self._stdout_buffer[:newline])
                    del self._stdout_buffer[: newline + 1]
                    if not line or len(line) > maximum_bytes:
                        raise IsolatedProcessError("isolated_process_protocol_invalid")
                    return line
                if len(self._stdout_buffer) > maximum_bytes:
                    raise IsolatedProcessError("isolated_process_output_exceeded")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise IsolatedProcessError("isolated_process_timeout")
                events = selector.select(timeout=min(remaining, 0.1))
                if not events:
                    if process.poll() is not None:
                        raise IsolatedProcessError("isolated_process_io_failed")
                    continue
                try:
                    chunk = os.read(process.stdout.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise IsolatedProcessError("isolated_process_io_failed")
                self._stdout_buffer.extend(chunk)
        finally:
            selector.close()

    @staticmethod
    def _write_all(
        process: subprocess.Popen[bytes], payload: bytes, timeout_seconds: float
    ) -> None:
        if process.stdin is None:
            raise IsolatedProcessError("isolated_process_unavailable")
        deadline = time.monotonic() + timeout_seconds
        offset = 0
        selector = selectors.DefaultSelector()
        selector.register(process.stdin, selectors.EVENT_WRITE)
        try:
            while offset < len(payload):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise IsolatedProcessError("isolated_process_timeout")
                if not selector.select(timeout=min(remaining, 0.1)):
                    if process.poll() is not None:
                        raise IsolatedProcessError("isolated_process_io_failed")
                    continue
                try:
                    written = os.write(process.stdin.fileno(), payload[offset:])
                except BlockingIOError:
                    if process.poll() is not None:
                        raise IsolatedProcessError("isolated_process_io_failed") from None
                    continue
                except BrokenPipeError:
                    raise IsolatedProcessError("isolated_process_io_failed") from None
                if written <= 0:
                    raise IsolatedProcessError("isolated_process_io_failed")
                offset += written
        finally:
            selector.close()

    def _terminate(self) -> None:
        process = self._process
        self._process = None
        self._stdout_buffer.clear()
        self._requests = 0
        if process is not None:
            try:
                terminate_isolated_process(process)
            finally:
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass


__all__ = ["PersistentIsolatedFrozenSeungunDetectorAdapter"]
