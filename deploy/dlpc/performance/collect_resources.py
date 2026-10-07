"""Operator-run read-only DLPC snapshot. No inference, env or command-line dump."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time


def read(path):
    try:
        return Path(path).read_text()
    except (OSError, UnicodeError):
        return None


def process(pid):
    root = Path('/proc') / str(pid)
    status = read(root / 'status')
    stat = read(root / 'stat')
    if not status or not stat:
        return None
    fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
    try:
        executable = str((root / 'exe').resolve(strict=True))
        cwd = str((root / 'cwd').resolve(strict=True))
        if not any(prefix in executable or prefix in cwd for prefix in (
                '/workspace/intelligentai', '/workspace/voice-coaching-tts', '/opt/intelligentai')):
            return None
        # Parse only known role names; arguments/text/credentials never leave memory.
        args = (root / 'cmdline').read_bytes().split(b'\0')
        roles = sorted({Path(a.decode()).name for a in args if a.endswith((
            b'/run_resident.py', b'/blackbox_host.py', b'/resident_worker.py',
            b'/mfa_sidecar.py', b'/bounded_supervisor.py'))})
        values = stat[stat.rfind(')')+2:].split()
        rollup = read(root / 'smaps_rollup') or ''
        pss = next((line.split(':', 1)[1].strip() for line in rollup.splitlines()
                    if line.startswith('Pss:')), None)
        return dict(pid=pid, parentPid=int(fields['PPid']), executable=executable, cwd=cwd,
            roles=roles, affinity=sorted(os.sched_getaffinity(pid)),
            threads=fields.get('Threads', '').strip(), rss=fields.get('VmRSS', '').strip(),
            pss=pss, cgroup=read(root/'cgroup'),
            startTicks=int(values[19]), cpuTicks=int(values[11])+int(values[12]))
    except (OSError, ValueError, UnicodeError):
        return None


def snapshot():
    rows = [process(int(p.name)) for p in Path('/proc').iterdir() if p.name.isdecimal()]
    return {r['pid']: r for r in rows if r is not None}


def cgroups(rows):
    # Visible cgroup-v2 hierarchy only; hidden ancestor limits stay unknown.
    directories = {Path('/sys/fs/cgroup')}
    for row in rows.values():
        for line in (row['cgroup'] or '').splitlines():
            if line.startswith('0::'):
                path = Path('/sys/fs/cgroup') / line[3:].lstrip('/')
                if '..' not in path.parts:
                    directories.add(path)
                    directories.update(p for p in path.parents if str(p).startswith('/sys/fs/cgroup'))
    return {str(p): {name: read(p/name) for name in (
        'cpu.max', 'cpu.stat', 'cpuset.cpus.effective', 'memory.max', 'memory.current',
        'memory.events', 'cpu.pressure', 'memory.pressure', 'io.pressure')}
        for p in sorted(directories)}


def main():
    before = snapshot()
    first_groups = cgroups(before)
    start = time.monotonic()
    time.sleep(2)
    after = snapshot()
    elapsed = time.monotonic()-start
    ticks = os.sysconf('SC_CLK_TCK')
    for pid, row in after.items():
        previous = before.get(pid)
        row['intervalCpuCores'] = ((row['cpuTicks']-previous['cpuTicks'])/ticks/elapsed
            if previous and previous['startTicks'] == row['startTicks'] else None)
    gpu = []
    for query in ('--query-gpu=index,name,memory.total,memory.used,utilization.gpu,utilization.memory',
                  '--query-compute-apps=pid,process_name,used_memory'):
        try:
            result = subprocess.run(['nvidia-smi', query, '--format=csv'], capture_output=True,
                text=True, timeout=10)
            gpu.append(dict(exitCode=result.returncode, output=result.stdout if result.returncode == 0 else None))
        except (OSError, subprocess.TimeoutExpired):
            gpu.append(dict(status='UNAVAILABLE'))
    worker = Path('/workspace/intelligentai/direct-spool/6b33fdfb-9d05-4adc-9da8-a6c5248b6f70/worker-complete.json')
    prior = read(worker)
    prior = json.loads(prior) if prior else {}
    print(json.dumps(dict(utc=datetime.now(timezone.utc).isoformat(),
        scope='read-only interval snapshot; no inference or peak-load claim',
        collectorAffinity=sorted(os.sched_getaffinity(0)), intervalSeconds=elapsed,
        meminfo=[line for line in (read('/proc/meminfo') or '').splitlines()
            if line.split(':')[0] in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree')],
        processes=list(after.values()), cgroupsBefore=first_groups, cgroupsAfter=cgroups(after),
        gpu=gpu, priorWorkerTiming={k:v for k,v in prior.items()
            if k in ('coreTiming', 'producerTiming', 'executionId', 'jobId')},
        priorWorkerTimingAvailable=bool(prior)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
