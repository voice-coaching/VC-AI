"""Host admission budgets, separate from leases and model execution policy."""
from dataclasses import dataclass
from pathlib import Path
import os
import shutil


def effective_cpu_count():
    """Affinity plus the tightest CPU quota, including cgroup ancestors."""
    try:
        count = len(os.sched_getaffinity(0))
    except (OSError, AttributeError):
        count = os.cpu_count() or 1
    roots = [(Path('/sys/fs/cgroup'), 2), (Path('/sys/fs/cgroup/cpu'), 1)]
    try:
        for line in Path('/proc/self/cgroup').read_text().splitlines():
            _, controllers, relative = line.split(':', 2)
            if not controllers:
                roots.append((Path('/sys/fs/cgroup') / relative.lstrip('/'), 2))
            elif 'cpu' in controllers.split(','):
                roots.append((Path('/sys/fs/cgroup/cpu') / relative.lstrip('/'), 1))
    except (OSError, ValueError):
        pass
    for root, version in roots:
        boundary = Path('/sys/fs/cgroup') if version == 2 else Path('/sys/fs/cgroup/cpu')
        while root.is_relative_to(boundary):
            try:
                if version == 2:
                    quota, period = (root / 'cpu.max').read_text().split()
                    quota, period = int(quota), int(period)
                else:
                    quota = int((root / 'cpu.cfs_quota_us').read_text())
                    period = int((root / 'cpu.cfs_period_us').read_text())
                if quota > 0 and period > 0:
                    count = min(count, max(1, quota // period))
            except (OSError, ValueError):
                pass
            root = root.parent
    return max(1, count)


def core_cpu_threads(env):
    profile = env.get('AI_ANALYSIS_RESOURCE_PROFILE', 'default')
    if profile == 'dlpc-j406':
        # Native/GOP get eight, MFA four, the rest is service/encoder headroom.
        if effective_cpu_count() < 16:
            raise ValueError('J406_CPU_CAPACITY_REQUIRED')
        return 8
    return 1 if profile == 'dlpc-4cpu' else 2


def available_memory_bytes():
    """Use the tightest known host/container headroom; unknown fails closed."""
    values = []
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                values.append(int(line.split()[1]) * 1024)
    except (OSError, ValueError, IndexError):
        pass
    for root, limit, current in (
        ('/sys/fs/cgroup', 'memory.max', 'memory.current'),
        ('/sys/fs/cgroup/memory', 'memory.limit_in_bytes', 'memory.usage_in_bytes'),
    ):
        try:
            cap = int((Path(root) / limit).read_text())
            used = int((Path(root) / current).read_text())
            if 0 < cap < 2**60 and used >= 0:
                values.append(max(0, cap - used))
        except (OSError, ValueError):
            pass
    return min(values) if values else None


@dataclass(frozen=True)
class ResourceBudget:
    max_active: int = 3
    minimum_available_bytes: int = 0
    disk_root: Path | None = None
    minimum_disk_free_bytes: int = 0
    minimum_free_inodes: int = 0

    @classmethod
    def from_environment(cls, env):
        profile = env.get('AI_ANALYSIS_RESOURCE_PROFILE', 'default')
        if profile == 'default':
            return cls()
        if profile in ('dlpc-4cpu', 'dlpc-j406'):
            if profile == 'dlpc-j406':
                core_cpu_threads(env)
            # Two-request overlap is not approved by selecting this profile.
            return cls(max_active=1, minimum_available_bytes=(8 if profile == 'dlpc-j406' else 2) * 1024**3,
                       disk_root=Path('/var/lib/intelligentai/work'),
                       minimum_disk_free_bytes=14 * 1024**3,
                       minimum_free_inodes=10000)
        raise ValueError('ANALYSIS_RESOURCE_PROFILE_INVALID')

    def can_admit(self):
        if self.minimum_available_bytes:
            free = available_memory_bytes()
            if free is None or free < self.minimum_available_bytes:
                return False
        if self.disk_root is not None:
            try:
                root = self.disk_root
                if not root.is_dir() or root.resolve(strict=True) != root:
                    return False
                # 8 GiB reserve + 6 GiB initial working headroom. This is an
                # admission floor, not a filesystem quota or peak-size proof.
                if shutil.disk_usage(root).free < self.minimum_disk_free_bytes:
                    return False
                if os.statvfs(root).f_favail < self.minimum_free_inodes:
                    return False
            except (OSError, ValueError, AttributeError):
                return False
        return True
