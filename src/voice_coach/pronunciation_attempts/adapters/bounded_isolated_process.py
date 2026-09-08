"""Bounded byte exchange with a target launched behind local isolation."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from .network_isolated_exec import isolated_command


_CHUNK_BYTES = 64 * 1024
_LAUNCHER = Path(__file__).with_name("network_isolated_exec.py")


class IsolatedProcessError(RuntimeError):
    """Stable, path-free isolated-process failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class IsolatedProcessResult:
    return_code: int
    stdout: bytes
    stderr: bytes


def run_bounded_isolated_process(
    *,
    launcher_python: str,
    target: list[str],
    input_bytes: bytes,
    cwd: str,
    environment: dict[str, str],
    timeout_seconds: float,
    maximum_output_bytes: int,
    address_space_bytes: int,
    allow_local_sockets: bool = False,
) -> IsolatedProcessResult:
    """Run without shell, with pre-exec isolation and combined output limit."""

    if not target or not os.path.isabs(target[0]):
        raise IsolatedProcessError("isolated_process_target_invalid")
    command = isolated_command(
        launcher_python=launcher_python,
        launcher_path=str(_LAUNCHER),
        target=target,
        cpu_seconds=max(1, math.ceil(timeout_seconds) + 2),
        address_space_bytes=address_space_bytes,
        allow_local_sockets=allow_local_sockets,
    )
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            shell=False,
            cwd=cwd,
            env=environment,
            start_new_session=True,
        )
    except (OSError, ValueError):
        raise IsolatedProcessError("isolated_process_unavailable") from None
    if process.stdin is None or process.stdout is None or process.stderr is None:
        _kill_process_group(process)
        raise IsolatedProcessError("isolated_process_unavailable")

    stdout = bytearray()
    stderr = bytearray()
    input_offset = 0
    deadline = time.monotonic() + timeout_seconds
    selector = selectors.DefaultSelector()
    for stream in (process.stdin, process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
    if input_bytes:
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    else:
        process.stdin.close()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IsolatedProcessError("isolated_process_timeout")
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
                            input_bytes[
                                input_offset : input_offset + _CHUNK_BYTES
                            ],
                        )
                    except BrokenPipeError:
                        written = 0
                        input_offset = len(input_bytes)
                    input_offset += written
                    if input_offset >= len(input_bytes):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                try:
                    payload = os.read(key.fd, _CHUNK_BYTES)
                except OSError:
                    raise IsolatedProcessError("isolated_process_io_failed") from None
                if not payload:
                    selector.unregister(key.fileobj)
                    continue
                destination = stdout if key.data == "stdout" else stderr
                destination.extend(payload)
                if len(stdout) + len(stderr) > maximum_output_bytes:
                    raise IsolatedProcessError("isolated_process_output_exceeded")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IsolatedProcessError("isolated_process_timeout")
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise IsolatedProcessError("isolated_process_timeout") from None
        return IsolatedProcessResult(
            return_code=return_code,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
        )
    except BaseException:
        _kill_process_group(process)
        raise
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    # The session leader may already have exited while a descendant still
    # owns stdout/stderr.  Always address the process group first; checking
    # only ``process.poll()`` would leak that descendant on timeout/error.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def terminate_isolated_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate an isolated process group without exposing its command or output."""

    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
    _kill_process_group(process)


__all__ = [
    "IsolatedProcessError",
    "IsolatedProcessResult",
    "run_bounded_isolated_process",
    "terminate_isolated_process",
]
