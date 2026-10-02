"""Per-invocation Linux ownership without inheriting service credentials."""
from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Callable

from .canonical_bridge import CanonicalIntegrationError, _require


class CanonicalProcessCleanupUnconfirmed(CanonicalIntegrationError):
    """Caller must keep admission closed until operator subtree reconciliation."""


class CanonicalProcessFailed(CanonicalIntegrationError):
    """An owned guard exited; retain only its numeric status for private diagnostics."""

    def __init__(self, exit_status: int):
        super().__init__('CANONICAL_PROCESS_FAILED')
        self.exit_status = exit_status


def check_guard_python(guard_python: Path) -> None:
    """Boot/readiness check of the actual guard runtime; no model or child job."""
    _require(guard_python.is_absolute() and guard_python.is_file(),
             'CANONICAL_GUARD_RUNTIME_UNAVAILABLE')
    try:
        result = subprocess.run(
            [str(guard_python), '-I', '-B',
             str(Path(__file__).with_name('canonical_process_guard.py')), '--check'],
            env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise CanonicalIntegrationError('CANONICAL_GUARD_RUNTIME_UNAVAILABLE') from None
    _require(result.returncode == 0, 'CANONICAL_GUARD_RUNTIME_UNAVAILABLE')


def private_directory(path: Path) -> Path:
    """Use a pre-provisioned, owned private directory; never chmod shared paths."""
    _require(sys.platform == 'linux' and path.is_absolute() and path.resolve(strict=True) == path,
             'CANONICAL_WORKSPACE_PATH')
    info = path.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o700, 'CANONICAL_WORKSPACE_PERMISSIONS')
    return path


async def run_guarded(*, guard_python: Path, command: list[str], cwd: Path,
                      environment: dict[str, str], log_path: Path,
                      deadline_monotonic: float, execution_is_active: Callable[[], bool]) -> None:
    """Run a server-built command; caller owns admission, lease and private paths.

    Cancellation closes the lifetime pipe and awaits subtree reclamation. There
    is deliberately no kill(timeout) of the subreaper: killing it can abandon
    setsid descendants. The slot owner MUST await this coroutine's completion.
    guard_python owns Linux supervision; command[0] independently selects the
    immutable model environment. Never infer the guard interpreter from it.
    """
    _require(sys.platform == 'linux' and guard_python.is_absolute() and guard_python.is_file()
             and command and all(type(arg) is str and '\x00' not in arg for arg in command),
             'CANONICAL_PROCESS_CONFIGURATION')
    _require(math.isfinite(deadline_monotonic) and deadline_monotonic > time.monotonic(),
             'CANONICAL_PROCESS_DEADLINE')
    private_directory(log_path.parent)
    _require(execution_is_active() is True, 'CANONICAL_EXECUTION_INACTIVE')
    # Explicit environment provided by each stage, never os.environ.copy().
    fd = os.open(log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    process = None
    waiter = None
    launch = None
    try:
        launch = asyncio.create_task(asyncio.create_subprocess_exec(
            str(guard_python), '-I', '-B', str(Path(__file__).with_name('canonical_process_guard.py')),
            '--deadline', str(deadline_monotonic), '--cwd', str(cwd), '--', *command,
            stdin=asyncio.subprocess.PIPE, stdout=fd, stderr=fd,
            env=environment, cwd=cwd, start_new_session=True, close_fds=True))
        # Shield creation: cancellation must not lose ownership of a started guard.
        process = await asyncio.shield(launch)
        waiter = asyncio.create_task(process.wait())
        while not waiter.done():
            if not execution_is_active() or time.monotonic() >= deadline_monotonic:
                raise CanonicalIntegrationError('CANONICAL_EXECUTION_INACTIVE')
            await asyncio.wait({waiter}, timeout=0.1)
        if waiter.result() < 0:
            raise CanonicalProcessCleanupUnconfirmed('CANONICAL_PROCESS_CLEANUP_UNCONFIRMED')
        if waiter.result() != 0:
            raise CanonicalProcessFailed(waiter.result())
        _require(execution_is_active() is True and time.monotonic() < deadline_monotonic,
                 'CANONICAL_EXECUTION_INACTIVE')
    finally:
        # Also recover the process handle if cancellation occurred during spawn.
        cancelled = False
        if process is None and launch is not None:
            while not launch.done():
                try:
                    await asyncio.shield(launch)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break  # Failed spawn has no process handle to reclaim.
            if not launch.cancelled() and launch.exception() is None:
                process = launch.result()
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if waiter is None:
                waiter = asyncio.create_task(process.wait())
            while not waiter.done():
                try:
                    await asyncio.shield(waiter)
                except asyncio.CancelledError:
                    cancelled = True
        os.close(fd)
        if waiter is not None and waiter.done() and waiter.result() < 0:
            raise CanonicalProcessCleanupUnconfirmed('CANONICAL_PROCESS_CLEANUP_UNCONFIRMED')
        if cancelled:
            raise asyncio.CancelledError()
