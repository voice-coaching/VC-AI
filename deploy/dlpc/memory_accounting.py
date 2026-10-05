"""Measured memory headroom for DLPC; no invented cgroup/OOM counters."""


def memory_snapshot():
    from pathlib import Path

    headroom = []
    sources = []
    record = dict(current=None, max=None, failcnt=None, oom_control=None,
                  cgroup_version=None, hard_limit_observed=False)
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                value = int(line.split()[1]) * 1024
                if value >= 0:
                    headroom.append(value)
                    sources.append('proc_meminfo')
    except (OSError, ValueError, IndexError):
        pass
    for version, root, cap_name, used_name in (
        (2, '/sys/fs/cgroup', 'memory.max', 'memory.current'),
        (1, '/sys/fs/cgroup/memory', 'memory.limit_in_bytes', 'memory.usage_in_bytes'),
    ):
        try:
            cap = int((Path(root) / cap_name).read_text())
            used = int((Path(root) / used_name).read_text())
            if 0 < cap < 2**60 and used >= 0:
                headroom.append(max(0, cap - used))
                sources.append('cgroup_v' + str(version))
                record.update(current=used, max=cap, cgroup_version=version,
                              hard_limit_observed=True)
        except (OSError, ValueError):
            pass
    if not headroom:
        raise RuntimeError('MEMORY_MEASUREMENT_UNAVAILABLE')
    record.update(available_bytes=min(headroom), measurement_sources=sources)
    return record
