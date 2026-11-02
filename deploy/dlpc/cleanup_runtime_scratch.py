"""Inventory or remove stopped J406 analysis scratch; never touch model/B2 assets."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
from datetime import datetime, timezone

UID = 10001
CONFIG = '/opt/intelligentai/app/deploy/dlpc/supervisord.conf'
CTL = '/usr/gatoai/python/extra/system-py3.10/bin/supervisorctl'
AUDIO = {'.wav', '.mp3', '.m4a', '.webm', '.flac', '.ogg', '.pcm'}
ROOTS = (Path('/var/lib/intelligentai/work/canonical'),
         Path('/var/lib/intelligentai/work/direct'),
         Path('/workspace/intelligentai/direct-spool'),
         Path('/run/intelligentai/worker/work'))
TEMP = re.compile(r'(vc-core-[a-z0-9_]+|canonical_ai_release_[0-9a-f]{12})')


def stopped():
    reply = subprocess.run([CTL, '-c', CONFIG, 'status', 'analysis'],
                           capture_output=True, text=True, check=False)
    fields = reply.stdout.split()
    if fields[:2] != ['analysis', 'STOPPED']:
        raise ValueError('ANALYSIS_MUST_BE_STOPPED')
    # Supervisor state alone cannot prove that a detached analysis guard exited.
    markers = (b"backend_analysis", b"direct_analysis", b"run_resident.py",
               b"canonical_process_guard.py", b"blackbox_host.py", b"bounded_supervisor.py")
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            status = (proc / "status").read_text()
            uid = next(line for line in status.splitlines() if line.startswith("Uid:")).split()[1]
            if int(uid) == UID:
                command = (proc / "cmdline").read_bytes()
                if any(marker in command for marker in markers):
                    raise ValueError("ANALYSIS_CHILD_STILL_RUNNING")
        except (FileNotFoundError, ProcessLookupError):
            continue
    # Do not discard accepted work or an unresolved result transfer.
    for path in ROOTS[2].glob('*/job.json'):
        if json.loads(path.read_bytes())['status'] not in ('RESULT_READY', 'FAILED', 'CANCELLED'):
            raise ValueError('PENDING_DIRECT_JOB')
    for work in ROOTS[0].iterdir():
        if (work / 'handoff.json').exists() and not (work / 'completed.json').exists():
            raise ValueError('PENDING_HANDOFF')


def owned(path, directory=False):
    info = path.lstat()
    if info.st_uid != UID or (directory and not stat.S_ISDIR(info.st_mode)):
        raise ValueError('UNEXPECTED_SCRATCH_OWNER_OR_TYPE')
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('UNEXPECTED_SCRATCH_LINK')
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'J406.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_J406_ROOT')
    if args.apply:
        stopped()
    trees = [p for p in Path('/tmp').iterdir() if TEMP.fullmatch(p.name)]
    for path in trees:
        owned(path, directory=True)
    audio = []
    for root in ROOTS:
        if not root.exists():
            continue
        owned(root, directory=True)
        for base, directories, files in os.walk(root, followlinks=False):
            directories[:] = [d for d in directories if not (Path(base) / d).is_symlink()]
            for name in files:
                path = Path(base) / name
                if path.suffix.lower() in AUDIO:
                    info = owned(path)
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError('UNEXPECTED_AUDIO_TYPE')
                    audio.append((path, info.st_size))
    report = dict(observedAt=datetime.now(timezone.utc).isoformat(), host=socket.gethostname(),
                  applied=args.apply, temporaryTrees=len(trees),
                  audioFilesOutsideTemporaryTrees=len(audio), audioBytesOutsideTemporaryTrees=sum(n for _, n in audio))
    if args.apply:
        # Recheck immediately before mutation. Results, journals and manifests survive.
        stopped()
        for path, _ in audio:
            owned(path)
            path.unlink()
        for path in trees:
            owned(path, directory=True)
            shutil.rmtree(path)
    report['tmpFreeBytes'] = shutil.disk_usage('/tmp').free
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
