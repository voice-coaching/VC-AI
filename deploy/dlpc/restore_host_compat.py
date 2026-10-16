"""Apply pinned host-compatibility patches to the restored app with analysis stopped.

J406 runs a Linux 3.10 host kernel and Ubuntu 22.04 userland, unlike J3990
(5.14 / 24.04). Each patch is pinned by before/after SHA-256, recorded in
deployment.json operationalPatches, and the previous file is kept in ops/.
  guard-kernel-compat-20261016: no pidfd before Linux 5.3; child ownership is
      proven with waitid(P_PID, WNOWAIT) before SIGKILL (pidfd path unchanged).
  core-libstdcxx-preload-20261016: preload the core env's libstdc++ ahead of the
      cuDNN preload so its ICU does not bind the older system libstdc++.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import host_identity

TOOLS = Path(__file__).resolve().parent
SRC = Path('/opt/intelligentai/app/src/voice_coach/backend_analysis')
PATCHES = (
    ('guard-kernel-compat-20261016', SRC / 'canonical_process_guard.py',
     '30aebcb494bbb88e7e03496d03a3ba791a92d14c3dcf8d1d0e3ee35c17c6119c',
     '99f07e20c9b7ba9003f66bdee20dd93b7d1856318187d19e74faedcdcb436dea',
     'host kernel without pidfd (Linux < 5.3)'),
    ('core-libstdcxx-preload-20261016', SRC / 'canonical_producer.py',
     '4a6f1e39e1d2bf5f4255b4e0d91f4d42e4d26c027a4d88dd1c9f80e7e349331c',
     '2d24fd595ceeedcb4515acb7ba934b3458328d705af8aaabb6ae901d13a35954',
     'system libstdc++ lacks CXXABI_1.3.15 (Ubuntu 22.04)'),
)


def analysis_stopped(identity):
    if not Path('/run/intelligentai/dlpc-supervisor.sock').exists():
        return True
    ctl = [str(Path(identity.supervisor_bin) / 'supervisorctl'), '-c',
           '/opt/intelligentai/app/deploy/dlpc/supervisord.conf']
    fields = subprocess.run(ctl + ['status', 'analysis'], capture_output=True, text=True, timeout=15).stdout.split()
    return len(fields) >= 2 and fields[1] in {'STOPPED', 'EXITED', 'FATAL'}


def replace(target, value):
    info = target.stat()
    pending = target.with_name(target.name + '.compat-pending')
    with pending.open('xb') as stream:
        stream.write(value); stream.flush(); os.fsync(stream.fileno())
    os.chown(pending, info.st_uid, info.st_gid)
    pending.chmod(info.st_mode & 0o777)
    pending.replace(target)


def main():
    identity = host_identity.require_root()
    os.umask(0o077)
    pending = []
    # Patched sources are copied beside this helper as <stem>.host-compat.py by
    # install_startup_hook.py (from src/ of the same checkout).
    for name, target, before, after, reason in PATCHES:
        if target.resolve(strict=True) != target or target.stat().st_uid != 0:
            raise ValueError('COMPAT_TARGET_CHANGED')
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != before:
            # Already patched, or a later main deployment owns this file now.
            continue
        raw = (TOOLS / (target.stem + '.host-compat.py')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != after:
            raise ValueError('COMPAT_PATCH_DIGEST')
        compile(raw, str(target), 'exec')
        pending.append((name, target, before, after, reason, raw))
    metadata = Path('/opt/intelligentai/deployment.json')
    doc = json.loads(metadata.read_bytes())
    patches = doc.setdefault('operationalPatches', {})
    if not pending:
        print('Host compatibility patches already installed or superseded by a deployment')
        return
    if pending and not analysis_stopped(identity):
        raise ValueError('ANALYSIS_MUST_BE_STOPPED')
    for name, target, before, after, reason, raw in pending:
        backup = Path('/opt/intelligentai/ops') / name
        backup.mkdir(mode=0o700, exist_ok=True)
        saved = backup / (target.name + '.before')
        previous = target.read_bytes()
        if saved.exists():
            if saved.is_symlink() or saved.read_bytes() != previous:
                raise ValueError('COMPAT_BACKUP_CHANGED')
        else:
            with saved.open('xb') as stream:
                stream.write(previous)
        replace(target, raw)
    for name, target, before, after, reason, _ in pending:
        patches[name] = {'path': str(target), 'beforeSha256': before, 'afterSha256': after, 'reason': reason}
    replace(metadata, (json.dumps(doc, sort_keys=True) + '\n').encode())
    print('Host compatibility patches installed: ' + ', '.join(p[0] for p in pending) + '; analysis remains stopped')


if __name__ == '__main__':
    main()
