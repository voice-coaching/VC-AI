"""Host admission budgets, separate from leases and model execution policy."""
from dataclasses import dataclass
from pathlib import Path
import os
import shutil


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
        if profile == 'dlpc-4cpu':
            # Two-request overlap is not approved by selecting this profile.
            return cls(max_active=1, minimum_available_bytes=2 * 1024**3,
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
