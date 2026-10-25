"""Install pinned process-health and import preflight changes before startup."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import host_identity

FILES = {
    'resident_health.py': (None, '7a5a7e4f93bf351a8193c677e193f4fb872c9265e644a0a8467d88d3e62d1214'),
    'canonical_resident.py': ('b309c2be1642af920f39b9d5d6aeffd56e7064f0373046d696fc3c36b0ca7095',
                              '5eb5a13e765c6e5d8f9b72cf3cb23a2f7f7a55b773951c14898ad77eaaf736f7'),
}
SOURCE = Path('/home/2020067/intelligentAI/src/voice_coach/backend_analysis')
TARGET = Path('/opt/intelligentai/app/src/voice_coach/backend_analysis')


def main():
    identity = host_identity.require_root()
    os.umask(0o077)
    if TARGET.resolve(strict=True) != TARGET or TARGET.stat().st_uid != 0:
        raise ValueError('HEALTH_TARGET_CHANGED')
    changes = []
    for name, (before, after) in FILES.items():
        p = TARGET / name
        if p.is_symlink():
            raise ValueError('HEALTH_TARGET_SYMLINK')
        old = p.read_bytes() if p.exists() else None
        digest = hashlib.sha256(old).hexdigest() if old is not None else None
        if name == 'resident_health.py' and digest == '37f57a765b3ebe560ca82f97f95564335b8330e1eb1ad31b053309ad4178986c':
            # The prepared-MFA recovery stage owns this newer pinned overlay.
            continue
        if name == 'canonical_resident.py' and digest == '3c1398727bd81bdd2d23d9e51e7a984de8b3ba684bd3c81d2c2e6929c221f9df':
            continue  # Newer pinned pipeline stage owns this overlay.
        if digest == after:
            continue
        if digest != before:
            raise ValueError('HEALTH_SOURCE_REVISION_CHANGED')
        raw = (SOURCE / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != after:
            raise ValueError('HEALTH_PATCH_DIGEST')
        compile(raw, name, 'exec')
        changes.append((p, old, raw))
    if changes and Path('/run/intelligentai/dlpc-supervisor.sock').exists():
        from restore_direct_resident import CTL
        result = subprocess.run(CTL + ['status', 'analysis'], capture_output=True, text=True, timeout=15)
        fields = result.stdout.split()
        if len(fields) < 2 or fields[1] not in {'STOPPED', 'EXITED', 'FATAL'}:
            raise ValueError('ANALYSIS_MUST_BE_STOPPED')
    backup = Path('/opt/intelligentai/ops/resident-health-20261010')
    backup.mkdir(mode=0o700, exist_ok=True)
    for target, old, raw in changes:
        if old is not None:
            saved = backup / (target.name + '.before')
            if saved.exists():
                if saved.is_symlink() or saved.read_bytes() != old:
                    raise ValueError('HEALTH_BACKUP_CHANGED')
            else:
                with saved.open('xb') as stream:
                    stream.write(old)
        pending = target.with_name(target.name + '.health-pending')
        with pending.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.chown(pending, 0, 10001); pending.chmod(0o640); pending.replace(target)
    metadata = Path('/opt/intelligentai/deployment.json')
    doc = json.loads(metadata.read_bytes())
    doc.setdefault('operationalPatches', {})['resident-health-20261010'] = {
        name: {'beforeSha256': before, 'afterSha256': after} for name, (before, after) in FILES.items()}
    pending = metadata.with_suffix('.health-pending')
    with pending.open('xb') as stream:
        stream.write((json.dumps(doc, sort_keys=True)+'\n').encode()); stream.flush(); os.fsync(stream.fileno())
    os.chown(pending, 0, 10001); pending.chmod(0o640); pending.replace(metadata)
    print('Resident process health and MFA import preflight installed')


if __name__ == '__main__':
    main()
