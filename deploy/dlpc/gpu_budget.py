"""One local GPU execution slot shared by the analysis and TTS processes."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import time


@contextmanager
def gpu_slot(timeout=120):
    # Provisioned by the supervisor, owned by the same dedicated service UID.
    path = Path('/run/intelligentai/gpu.lock')
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise RuntimeError('GPU_COORDINATOR_PERMISSIONS')
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise TimeoutError('GPU_COORDINATOR_DEADLINE')
                time.sleep(.05)
        yield
    finally:
        # Kernel releases the reservation on process death as well.
        os.close(fd)


def configure_torch(torch):
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = False
