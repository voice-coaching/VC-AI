"""Read this process's visible cgroup hierarchy, including v1 combined mounts."""
from functools import lru_cache
from pathlib import Path


def _unescape(value):
    for escaped, plain in (('\\040', ' '), ('\\011', '\t'), ('\\012', '\n'), ('\\134', '\\')):
        value = value.replace(escaped, plain)
    return value


@lru_cache(maxsize=1)
def _mounts():
    # Mount topology is fixed for a service process. Membership and all resource
    # counters below are read afresh; a host-wide unlimited root is insufficient.
    mounts = []
    try:
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left, right = line.split(' - ', 1)
            fields, filesystem = left.split(), right.split()
            if filesystem[0] in ('cgroup', 'cgroup2'):
                mounts.append((filesystem[0], Path(_unescape(fields[3])),
                               Path(_unescape(fields[4])), set(filesystem[2].split(','))))
    except (OSError, ValueError, IndexError):
        pass
    return mounts


def hierarchies(controller):
    """Yield (version, member/ancestor path) within each visible mount."""
    try:
        membership = Path('/proc/self/cgroup').read_text().splitlines()
    except OSError:
        return
    seen = set()
    for line in membership:
        try:
            _, controls, relative = line.split(':', 2)
            for filesystem, root, mount, available in _mounts():
                v2 = filesystem == 'cgroup2' and not controls
                if not v2 and not (filesystem == 'cgroup' and controller in controls.split(',')
                                   and controller in available):
                    continue
                member = Path(relative)
                if member.is_relative_to(root):
                    start = mount / member.relative_to(root)
                elif relative == '/':
                    start = mount  # A cgroup namespace exposes its own root.
                else:
                    continue
                if '..' in start.parts or not start.is_relative_to(mount):
                    continue
                while start.is_relative_to(mount):
                    key = (2 if v2 else 1, start)
                    if key not in seen:
                        seen.add(key)
                        yield key
                    if start == mount:
                        break
                    start = start.parent
        except ValueError:
            continue


def memory_limits():
    values = []
    for version, root in hierarchies('memory'):
        try:
            cap = int((root / ('memory.max' if version == 2 else 'memory.limit_in_bytes')).read_text())
            used = int((root / ('memory.current' if version == 2 else 'memory.usage_in_bytes')).read_text())
            if 0 < cap < 2**60 and used >= 0:
                values.append(dict(version=version, limit=cap, current=used,
                                   available=max(0, cap-used)))
        except (OSError, ValueError):
            pass
    return values


def cpu_quotas():
    values = []
    for version, root in hierarchies('cpu'):
        try:
            if version == 2:
                quota, period = map(int, (root / 'cpu.max').read_text().split())
            else:
                quota = int((root / 'cpu.cfs_quota_us').read_text())
                period = int((root / 'cpu.cfs_period_us').read_text())
            if quota > 0 and period > 0:
                values.append(max(1, quota // period))
        except (OSError, ValueError):
            pass
    return values
