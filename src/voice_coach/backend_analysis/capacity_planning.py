"""Planning budgets for short audio, not measured peaks or benchmark results."""
from dataclasses import asdict, dataclass
import shutil
import subprocess
import time

GIB = 1024 ** 3
MIB = 1024 ** 2
MAX_CANONICAL_WORKERS = 128
SHORT_AUDIO_MAX_MS = 10000
_gpu_cache = None


def gpu_memory():
    """Read inventory only; never initialize a CUDA context or inherit secrets."""
    global _gpu_cache
    now = time.monotonic()
    if _gpu_cache is not None and now - _gpu_cache[0] < 2:
        return _gpu_cache[1]
    result = None
    try:
        reply = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,memory.total,memory.free',
             '--format=csv,noheader,nounits'], capture_output=True, text=True,
            timeout=3, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C'})
        if reply.returncode == 0:
            rows = [tuple(int(field.strip()) for field in line.split(','))
                    for line in reply.stdout.splitlines() if line.strip()]
            result = {index: dict(total=total * MIB, free=free * MIB)
                      for index, total, free in rows}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    _gpu_cache = now, result
    return result


@dataclass(frozen=True)
class CapacityPlan:
    request_limit: int
    compute_limit: int
    ram_limit: int
    gpu_limit: int
    disk_limit: int
    tmp_limit: int
    ram_per_worker: int
    gpu_per_worker: int
    disk_per_worker: int
    tmp_per_worker: int
    cpu_threads_per_compute: int
    gpu_indices: tuple[int, ...]
    inventory_complete: bool

    def document(self):
        return dict(basis='SHORT_AUDIO_PLANNING_ESTIMATE_NOT_PEAK_QA', **asdict(self))


def capacity_plan(*, profile, cpu_count, memory_available, disk_free, tmp_free, gpus):
    # Current bounded profile runs Native on cuda:0 behind its existing lock.
    # Only the reviewed j406 release also uses GOP devices 1 and 2.
    indices = (0, 1, 2) if profile == 'dlpc-j406' else (0,)
    ram_per_worker = (8 if profile == 'dlpc-j406' else 3) * GIB
    gpu_per_worker = 2 * GIB
    disk_per_worker, tmp_per_worker = 256 * MIB, 64 * MIB
    threads = 12 if profile == 'dlpc-j406' else 4
    complete = (memory_available is not None and disk_free is not None
                and tmp_free is not None and gpus is not None
                and all(index in gpus for index in indices))
    # Available RAM already excludes resident allocations. Admission and slot
    # planning use the same per-worker budget without a fixed global reserve.
    ram = max(0, memory_available // ram_per_worker) if memory_available else 0
    gpu = (min(max(0, (gpus[i]['total'] - 8 * GIB) // gpu_per_worker) for i in indices)
           if gpus is not None and all(i in gpus for i in indices) else 0)
    disk = max(0, (disk_free - 8 * GIB) // disk_per_worker) if disk_free else 0
    temporary = max(0, (tmp_free - GIB) // tmp_per_worker) if tmp_free else 0
    limit = min(MAX_CANONICAL_WORKERS, ram, gpu, disk, temporary) if complete else 0
    compute = min(limit, max(1, (cpu_count - min(4, max(0, cpu_count - 1))) // threads))
    return CapacityPlan(limit, compute, ram, gpu, disk, temporary, ram_per_worker,
                        gpu_per_worker, disk_per_worker, tmp_per_worker, threads, indices, complete)


def free_bytes(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None
