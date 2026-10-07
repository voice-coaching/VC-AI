"""Shared local core slot for direct and Backend lanes; no HTTP or secrets."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import time


@contextmanager
def core_slot(deadline):
    path = Path('/run/intelligentai/analysis.lock')
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    started = time.monotonic()
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise RuntimeError('ANALYSIS_COORDINATOR_PERMISSIONS')
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('ANALYSIS_COORDINATOR_DEADLINE')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(.05, max(0, deadline-time.monotonic())))
        yield time.monotonic()-started
    finally:
        os.close(fd)  # Kernel releases the slot on exit, including process death.
