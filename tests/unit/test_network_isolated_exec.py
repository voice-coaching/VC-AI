"""Independent contract tests for the shared isolated-process launcher."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from voice_coach.pronunciation_attempts.adapters.bounded_isolated_process import (
    IsolatedProcessError,
    IsolatedProcessResult,
    run_bounded_isolated_process,
)


_ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024


def _minimal_environment() -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("LANG", "LC_ALL", "PATH", "TMPDIR")
        if key in os.environ
    }
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment


def _run_python(
    tmp_path: Path,
    source: str,
    *,
    input_bytes: bytes = b"",
    allow_local_sockets: bool = False,
    timeout_seconds: float = 5.0,
    maximum_output_bytes: int = 16 * 1024,
) -> IsolatedProcessResult:
    interpreter = str(Path(sys.executable).resolve())
    return run_bounded_isolated_process(
        launcher_python=interpreter,
        target=[interpreter, "-c", source],
        input_bytes=input_bytes,
        cwd=str(tmp_path),
        environment=_minimal_environment(),
        timeout_seconds=timeout_seconds,
        maximum_output_bytes=maximum_output_bytes,
        address_space_bytes=_ADDRESS_SPACE_BYTES,
        allow_local_sockets=allow_local_sockets,
    )


def test_default_policy_denies_inet_and_unix_sockets(tmp_path: Path) -> None:
    result = _run_python(
        tmp_path,
        """
import errno
import json
import os
import socket

observed = {}
for name, family in (("inet", socket.AF_INET), ("unix", socket.AF_UNIX)):
    try:
        opened = socket.socket(family, socket.SOCK_STREAM)
    except OSError as error:
        observed[name] = error.errno
    else:
        opened.close()
        observed[name] = None
print(json.dumps(observed, sort_keys=True))
""",
    )

    assert result.return_code == 0
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "inet": errno.EPERM,
        "unix": errno.EPERM,
    }


def test_local_policy_denies_inet_but_allows_unix_socketpair(tmp_path: Path) -> None:
    result = _run_python(
        tmp_path,
        """
import ctypes
import json
import os
import socket

try:
    inet = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
except OSError as error:
    inet_errno = error.errno
else:
    inet.close()
    inet_errno = None

try:
    unix_endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
except OSError as error:
    unix_endpoint_errno = error.errno
else:
    unix_endpoint.close()
    unix_endpoint_errno = None

left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    os.write(left.fileno(), b"local-only")
    local_payload = os.read(right.fileno(), 32).decode("ascii")
finally:
    left.close()
    right.close()

listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
client = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
try:
    address = b"\\0voice-coach-isolation-test"
    listener.bind(address)
    listener.listen(1)
    listener_status = "created"
    try:
        client.connect(address)
    except OSError as error:
        connect_errno = error.errno
    else:
        connect_errno = None
finally:
    listener.close()
    client.close()

seccomp = ctypes.CDLL("libseccomp.so.2")
seccomp.seccomp_syscall_resolve_name.argtypes = (ctypes.c_char_p,)
seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
io_uring_setup = seccomp.seccomp_syscall_resolve_name(b"io_uring_setup")
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall(io_uring_setup, 1, None)
io_uring_errno = ctypes.get_errno()

print(
    json.dumps(
        {
            "inet_errno": inet_errno,
            "io_uring_errno": io_uring_errno,
            "local_payload": local_payload,
            "listener_status": listener_status,
            "connect_errno": connect_errno,
            "unix_endpoint_errno": unix_endpoint_errno,
        },
        sort_keys=True,
    )
)
""",
        allow_local_sockets=True,
    )

    assert result.return_code == 0
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "inet_errno": errno.EPERM,
        "io_uring_errno": errno.EPERM,
        "local_payload": "local-only",
        "listener_status": "created",
        "connect_errno": errno.ENOENT,
        "unix_endpoint_errno": errno.EPERM,
    }


def test_empty_stdin_is_closed_and_target_exits_normally(tmp_path: Path) -> None:
    result = _run_python(
        tmp_path,
        """
import sys

payload = sys.stdin.buffer.read()
if payload:
    raise SystemExit(7)
sys.stdout.buffer.write(b"empty-stdin-ok")
""",
        input_bytes=b"",
    )

    assert result.return_code == 0
    assert result.stdout == b"empty-stdin-ok"
    assert result.stderr == b""


def test_process_clone_is_denied_but_runtime_threads_remain_available(
    tmp_path: Path,
) -> None:
    result = _run_python(
        tmp_path,
        """
import json
import os
import threading

observed = []
thread = threading.Thread(target=lambda: observed.append("thread-ok"))
thread.start()
thread.join()
try:
    child_pid = os.fork()
except OSError as error:
    fork_errno = error.errno
else:
    if child_pid == 0:
        os._exit(0)
    os.waitpid(child_pid, 0)
    fork_errno = None
print(json.dumps({"fork_errno": fork_errno, "observed": observed}, sort_keys=True))
""",
        allow_local_sockets=True,
    )

    assert result.return_code == 0
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "fork_errno": errno.EPERM,
        "observed": ["thread-ok"],
    }


def test_parent_death_guard_is_inherited_and_cannot_be_cleared(
    tmp_path: Path,
) -> None:
    result = _run_python(
        tmp_path,
        """
import ctypes
import json
import os
import signal

libc = ctypes.CDLL(None, use_errno=True)
parent_death_signal = ctypes.c_int(0)
get_result = libc.prctl(2, ctypes.byref(parent_death_signal), 0, 0, 0)
ctypes.set_errno(0)
clear_result = libc.prctl(1, 0, 0, 0, 0)
clear_errno = ctypes.get_errno()
try:
    os.kill(os.getppid(), 0)
except OSError as error:
    parent_signal_errno = error.errno
else:
    parent_signal_errno = None
print(
    json.dumps(
        {
            "clear_errno": clear_errno,
            "clear_result": clear_result,
            "get_result": get_result,
            "parent_death_signal": parent_death_signal.value,
            "parent_signal_errno": parent_signal_errno,
        },
        sort_keys=True,
    )
)
""",
        allow_local_sockets=True,
    )

    assert result.return_code == 0
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "clear_errno": errno.EPERM,
        "clear_result": -1,
        "get_result": 0,
        "parent_death_signal": 9,
        "parent_signal_errno": errno.EPERM,
    }


def test_parent_death_guard_terminates_target_with_nested_controller(
    tmp_path: Path,
) -> None:
    """Killing a disposable controller must not orphan its isolated target."""

    marker_path = tmp_path / "isolated-target.pid"
    identity_marker = f"pdeath-{os.getpid()}-{time.monotonic_ns()}"
    source_root = Path(__file__).resolve().parents[2] / "src"
    controller_source = r'''
import os
from pathlib import Path
import sys

from voice_coach.pronunciation_attempts.adapters.bounded_isolated_process import (
    run_bounded_isolated_process,
)

marker_path = Path(sys.argv[1])
identity_marker = sys.argv[2]
target_source = r"""
import os
from pathlib import Path
import sys
import time

marker_path = Path(sys.argv[1])
descriptor = os.open(
    marker_path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
    0o600,
)
try:
    payload = str(os.getpid()).encode("ascii")
    if os.write(descriptor, payload) != len(payload):
        raise SystemExit(7)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
time.sleep(30)
"""
interpreter = str(Path(sys.executable).resolve())
run_bounded_isolated_process(
    launcher_python=interpreter,
    target=[
        interpreter,
        "-c",
        target_source,
        str(marker_path),
        identity_marker,
    ],
    input_bytes=b"",
    cwd=str(marker_path.parent),
    environment={
        "PATH": os.environ.get("PATH", ""),
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
    },
    timeout_seconds=30.0,
    maximum_output_bytes=4096,
    address_space_bytes=1024 * 1024 * 1024,
)
'''
    controller_environment = _minimal_environment()
    controller_environment["PYTHONPATH"] = str(source_root)
    controller = subprocess.Popen(
        [
            str(Path(sys.executable).resolve()),
            "-c",
            controller_source,
            str(marker_path),
            identity_marker,
        ],
        cwd=str(tmp_path),
        env=controller_environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        shell=False,
        start_new_session=True,
    )
    target_pid: int | None = None

    def exact_target_is_alive() -> bool:
        if target_pid is None:
            return False
        try:
            command_line = Path(f"/proc/{target_pid}/cmdline").read_bytes()
        except OSError:
            return False
        return identity_marker.encode("ascii") in command_line

    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if marker_path.exists():
                target_pid = int(marker_path.read_text(encoding="ascii"))
                break
            if controller.poll() is not None:
                pytest.fail(
                    f"nested isolation controller exited early: {controller.returncode}"
                )
            time.sleep(0.02)
        if target_pid is None:
            pytest.fail("isolated target did not publish its PID marker")
        assert exact_target_is_alive(), "isolated target identity marker differs"

        controller.kill()
        controller.wait(timeout=2.0)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and exact_target_is_alive():
            time.sleep(0.02)
        assert not exact_target_is_alive(), (
            "isolated target survived its controller's death"
        )
    finally:
        if controller.poll() is None:
            controller.kill()
            controller.wait(timeout=2.0)
        # Never signal a PID based on the marker file alone.  The unique argv
        # marker prevents a recycled PID or unrelated process from being hit.
        if target_pid is not None and exact_target_is_alive():
            try:
                os.killpg(target_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_combined_output_bound_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(IsolatedProcessError) as captured:
        _run_python(
            tmp_path,
            """
import os

os.write(1, b"o" * 768)
os.write(2, b"e" * 768)
""",
            maximum_output_bytes=1024,
        )

    assert captured.value.reason_code == "isolated_process_output_exceeded"
    assert str(captured.value) == "isolated_process_output_exceeded"


def test_wall_timeout_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(IsolatedProcessError) as captured:
        _run_python(
            tmp_path,
            """
import time

time.sleep(60)
""",
            timeout_seconds=0.2,
        )

    assert captured.value.reason_code == "isolated_process_timeout"
    assert str(captured.value) == "isolated_process_timeout"
