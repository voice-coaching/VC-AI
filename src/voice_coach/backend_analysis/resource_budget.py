"""Host admission budgets, separate from leases and model execution policy."""
from dataclasses import dataclass
from pathlib import Path
import os
import shutil
from .cgroup_resources import cpu_quotas, memory_limits

from .capacity_planning import (MAX_CANONICAL_WORKERS, GIB, capacity_plan, free_bytes, gpu_memory)


def effective_cpu_count():
    """Affinity plus the tightest CPU quota, including cgroup ancestors."""
    try:
        count = len(os.sched_getaffinity(0))
    except (OSError, AttributeError):
        count = os.cpu_count() or 1
    return max(1, min([count] + cpu_quotas()))


def core_cpu_threads(env):
    profile = env.get('AI_ANALYSIS_RESOURCE_PROFILE', 'default')
    if profile == 'dlpc-j406':
        # Native/GOP get eight, MFA four, the rest is service/encoder headroom.
        if effective_cpu_count() < 16:
            raise ValueError('J406_CPU_CAPACITY_REQUIRED')
        return 8
    return 1 if profile == 'dlpc-4cpu' else 2


def available_memory_bytes():
    """Use the tightest host/container working-set headroom; unknown fails closed."""
    values = []
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                values.append(int(line.split()[1]) * 1024)
    except (OSError, ValueError, IndexError):
        pass
    values.extend(item['available'] for item in memory_limits(reclaim_file_cache=True))
    return min(values) if values else None


@dataclass(frozen=True)
class ResourceBudget:
    max_active: int = 3
    minimum_available_bytes: int = 0
    disk_root: Path | None = None
    minimum_disk_free_bytes: int = 0
    minimum_free_inodes: int = 0
    compute_active: int = 3
    capacity_estimate: dict | None = None
    minimum_tmp_free_bytes: int = 0
    gpu_indices: tuple[int, ...] = ()
    minimum_gpu_free_bytes: int = 0

    @classmethod
    def from_environment(cls, env):
        profile = env.get('AI_ANALYSIS_RESOURCE_PROFILE', 'default')
        if profile == 'default':
            return cls()
        if profile in ('dlpc-4cpu', 'dlpc-j406'):
            if profile == 'dlpc-j406':
                core_cpu_threads(env)
            work = Path("/var/lib/intelligentai/work")
            plan = capacity_plan(profile=profile, cpu_count=effective_cpu_count(),
                memory_available=available_memory_bytes(), disk_free=free_bytes(work),
                tmp_free=free_bytes("/tmp"), gpus=gpu_memory())
            return cls(max_active=plan.request_limit,
                       compute_active=plan.compute_limit, capacity_estimate=plan.document(),
                       # Already resident models are charged in available RAM.
                       # Require one worker budget, without a second global
                       # 24 GiB reserve. The current 3 GiB profile also covers
                       # the designated video tree's observed 2.10 GiB peak.
                       minimum_available_bytes=plan.ram_per_worker,
                       disk_root=work, minimum_disk_free_bytes=8 * GIB + plan.disk_per_worker,
                       minimum_tmp_free_bytes=GIB + plan.tmp_per_worker,
                       gpu_indices=plan.gpu_indices, minimum_gpu_free_bytes=8 * GIB + plan.gpu_per_worker,
                       minimum_free_inodes=10000)
        raise ValueError('ANALYSIS_RESOURCE_PROFILE_INVALID')

    def can_admit(self, *, large_input=False):
        if self.minimum_available_bytes:
            free = available_memory_bytes()
            # Large inputs retain their disk/GPU checks below. RAM admission
            # uses the worker budget rather than an unrelated 32 GiB floor.
            required_memory = self.minimum_available_bytes
            if free is None or free < required_memory:
                return False
        if self.disk_root is not None:
            try:
                root = self.disk_root
                if not root.is_dir() or root.resolve(strict=True) != root:
                    return False
                # Short-audio planning replaces the old fixed 6 GiB request
                # headroom. Video/long audio retains the original 14 GiB floor.
                required_disk = max(self.minimum_disk_free_bytes, 14 * GIB if large_input else 0)
                if shutil.disk_usage(root).free < required_disk:
                    return False
                if os.statvfs(root).f_favail < self.minimum_free_inodes:
                    return False
            except (OSError, ValueError, AttributeError):
                return False
        if self.minimum_tmp_free_bytes:
            temporary = free_bytes("/tmp")
            if temporary is None or temporary < self.minimum_tmp_free_bytes:
                return False
        if self.gpu_indices:
            inventory = gpu_memory()
            required_gpu = max(self.minimum_gpu_free_bytes, 16 * GIB if large_input else 0)
            if inventory is None or any(
                    i not in inventory or inventory[i]["free"] < required_gpu
                    for i in self.gpu_indices):
                return False
        return True
