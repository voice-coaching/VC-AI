"""Apply the pinned direct-resident recovery fix with analysis stopped."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import host_identity

BEFORE = '213ea2a95d66a464bb6d5d3deb4c216a0cdbbd1988464d3b38c1d69466c78f9e'
AFTER = '47758c852a7d0e7133b135ef2d75220db71761c3385191c03846e47381595fa0'
SOURCE = Path('/home/2020067/intelligentAI/src/voice_coach/direct_analysis/service.py')
TARGET = Path('/opt/intelligentai/app/src/voice_coach/direct_analysis/service.py')
CTL = [str(Path(host_identity.current().supervisor_bin) / 'supervisorctl'), '-c',
       '/opt/intelligentai/app/deploy/dlpc/supervisord.conf']


def main():
    identity = host_identity.require_root()
    os.umask(0o077)
    if TARGET.resolve(strict=True) != TARGET or TARGET.stat().st_uid != 0:
        raise ValueError('DIRECT_SERVICE_PATH_CHANGED')
    previous = TARGET.read_bytes()
    digest = hashlib.sha256(previous).hexdigest()
    if digest == AFTER:
        print('Direct resident recovery patch already installed')
        return
    if digest != BEFORE:
        raise ValueError('DIRECT_SERVICE_REVISION_CHANGED')
    raw = SOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != AFTER:
        raise ValueError('DIRECT_SERVICE_PATCH_DIGEST')
    compile(raw, str(TARGET), 'exec')
    if Path('/run/intelligentai/dlpc-supervisor.sock').exists():
        status = subprocess.run(CTL + ['status', 'analysis'], capture_output=True,
                                text=True, timeout=15)
        fields = status.stdout.split()
        if len(fields) < 2 or fields[1] not in {'STOPPED', 'EXITED', 'FATAL'}:
            raise ValueError('ANALYSIS_MUST_BE_STOPPED')
    backup = Path('/opt/intelligentai/ops/direct-resident-recovery-20261010')
    backup.mkdir(mode=0o700, exist_ok=True)
    saved = backup / 'service.py.before'
    if saved.exists():
        if saved.is_symlink() or saved.read_bytes() != previous:
            raise ValueError('DIRECT_SERVICE_BACKUP_CHANGED')
    else:
        with saved.open('xb') as stream:
            stream.write(previous)
    metadata = Path('/opt/intelligentai/deployment.json')
    doc = json.loads(metadata.read_bytes())
    doc.setdefault('operationalPatches', {})['direct-resident-recovery-20261010'] = {
        'path': str(TARGET), 'beforeSha256': BEFORE, 'afterSha256': AFTER}
    for target, value in ((TARGET, raw), (metadata, (json.dumps(doc, sort_keys=True)+'\n').encode())):
        info = target.stat()
        pending = target.with_name(target.name + '.resident-pending')
        with pending.open('xb') as stream:
            stream.write(value); stream.flush(); os.fsync(stream.fileno())
        os.chown(pending, info.st_uid, info.st_gid)
        pending.chmod(info.st_mode & 0o777)
        pending.replace(target)
    print('Pinned direct resident recovery patch installed; analysis remains stopped')


if __name__ == '__main__':
    main()
