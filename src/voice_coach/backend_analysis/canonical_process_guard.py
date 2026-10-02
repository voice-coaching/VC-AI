"""Linux, stdlib-only child subreaper; invoked by canonical_process, not HTTP.

The caller keeps stdin open as a lifetime pipe. EOF, TERM, or the monotonic
deadline fences the invocation. Exit is reported only AFTER all owned children
are reaped, including MFA descendants that start new sessions. No process-group
or global PID killing, and no output of argv, environment, or private logs.
"""
from __future__ import annotations

import argparse
import ctypes
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time


def _children() -> set[int]:
    children = set()
    for task in Path('/proc/self/task').iterdir():
        try:
            children.update(map(int, (task / 'children').read_text().split()))
        except FileNotFoundError:
            continue
    return children


def _reap_owned_tree() -> None:
    # Killing a direct child reparents its descendants to this subreaper.
    # Repeat until waitpid proves there are no children, not merely no known PIDs.
    # A task stuck in kernel I/O keeps this guard alive and the caller's slot held.
    while True:
        for pid in _children():
            try:
                handle = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            try:
                try:
                    # Prove this stable pidfd is our waitable direct child.
                    # ECHILD prevents signalling a reused/non-child PID.
                    os.waitid(os.P_PIDFD, handle, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
                except (ChildProcessError, ProcessLookupError):
                    pass
            finally:
                os.close(handle)
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                break
        time.sleep(0.05)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--deadline', type=float, required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if (sys.platform != 'linux' or not math.isfinite(args.deadline)
            or not args.command or args.command[0] != '--' or len(args.command) < 2):
        return 125
    if not all((hasattr(os, 'pidfd_open'), hasattr(os, 'P_PIDFD'),
                hasattr(signal, 'pidfd_send_signal'))):
        return 125
    os.umask(0o077)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        return 125
    enabled = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(enabled), 0, 0, 0) != 0 or enabled.value != 1:
        return 125
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with selectors.DefaultSelector() as selector:
        selector.register(0, selectors.EVENT_READ)
        child = None
        try:
            # The owner may already have died before the subreaper initialized.
            if selector.select(0) or time.monotonic() >= args.deadline:
                return 124
            child = subprocess.Popen(args.command[1:], cwd=args.cwd, stdin=subprocess.DEVNULL,
                                     close_fds=True, start_new_session=True)
            while child.poll() is None:
                if stopping or time.monotonic() >= args.deadline or selector.select(0.1):
                    return 124
            return child.returncode if 0 <= child.returncode <= 123 else 125
        finally:
            _reap_owned_tree()


if __name__ == '__main__':
    try:
        code = main()
    except BaseException:
        # No traceback/private paths to the service log. Cleanup errors must not
        # pretend to be successful reclamation or permit another execution.
        while True:
            try:
                _reap_owned_tree()
                break
            except BaseException:
                time.sleep(1)
        code = 125
    sys.exit(code)

