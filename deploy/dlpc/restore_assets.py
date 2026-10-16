"""Operator-driven pinned B2 restoration, independent of a running RunPod.

Pass the short-lived Native API auth object on stdin, as for b2_restore.py.
Private runtime settings are supplied separately after this command succeeds.
Pinned interrupted downloads resume; partial installation requires reconciliation.
"""
import fcntl
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import time

from b2_restore import RUN, OBJECTS
import host_identity


def main():
    identity = host_identity.require_root()
    os.umask(0o077)
    tools = Path(__file__).resolve().parent
    with open('/run/intelligentai-asset-restore.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        installed = [Path(p).exists() for p in (
            '/opt/intelligentai', '/workspace/intelligentai', '/workspace/voice-coaching-tts')]
        if any(installed):
            raise ValueError('EXISTING_INSTALLATION_REQUIRES_RECONCILIATION')
        if (RUN / 'restore-state.json').exists():
            raise ValueError('RESTORE_ALREADY_ATTEMPTED_RECONCILE_STATE')
        recovered = sum((RUN / (name + suffix)).stat().st_size
                        for name, _, _, _ in OBJECTS for suffix in ('', '.partial')
                        if (RUN / (name + suffix)).is_file())
        if shutil.disk_usage(RUN.parent).free + recovered < 46_000_000_000:
            raise ValueError('INSUFFICIENT_DOWNLOAD_AND_RESTORE_SPACE')
        RUN.mkdir(mode=0o700, exist_ok=True)
        if (RUN / 'state.json').exists():
            shutil.copyfile(RUN / 'state.json', RUN / ('state.before-' + str(time.time_ns()) + '.json'))
        # The worker locks the download, resumes pinned partial bytes and
        # rechecks completed objects. Run synchronously so failures propagate;
        # an old failed state cannot race with a new background launcher.
        subprocess.run([host_identity.current().tools_python, str(tools / 'b2_restore.py'), '--worker'], check=True)
        subprocess.run([host_identity.current().tools_python, str(tools / 'restore_downloaded.py'), '--worker'], check=True)
        state = json.loads((RUN / 'restore-state.json').read_bytes())
        if state.get('status') != 'assets_restored_not_started':
            raise ValueError('ASSET_INSTALLATION_INCOMPLETE')
        # Install the recovery tools from this reviewed operator package; the
        # old backup's supervisor also contained a retired AWS:22 program.
        destination = Path('/opt/intelligentai/ops/dlpc-20261006')
        destination.mkdir(parents=True, mode=0o700, exist_ok=False)
        for name in ('bootstrap.py', 'prepare_restored_runtime.py'):
            raw = (tools / name).read_bytes()
            compile(raw, name, 'exec')
            with (destination / name).open('xb') as out:
                out.write(raw)
        config = Path('/opt/intelligentai/app/deploy/dlpc/supervisord.conf')
        reviewed = (tools / 'supervisord.conf').read_bytes()
        if b'[program:aws-tunnel]' in reviewed or b'--admit' in reviewed:
            raise ValueError('DIAGNOSTIC_SUPERVISOR_REQUIRED')
        historical = destination / 'restored-supervisord.conf'
        with historical.open('xb') as out:
            out.write(config.read_bytes())
        config.write_bytes(reviewed)
        os.chown(config, 0, 10001)
        config.chmod(0o640)
        name, _, size, digest = OBJECTS[1]
        archive = RUN / name
        if (state.get('archiveSha256') != digest or archive.is_symlink()
                or archive.resolve() != RUN / 'runtime-assets.tar.gz'
                or archive.stat().st_size != size):
            raise ValueError('VERIFIED_TEMP_ARCHIVE_REQUIRED')
        # Only the verified local download is removed; B2 and installed assets
        # remain. Retaining it would prevent the 14 GiB startup headroom check.
        archive.unlink()
        print(json.dumps(dict(assetsRestored=True, privateSettingsRequired=True,
                              localArchiveRemoved=True, servicesStarted=False,
                              productionAdmission=False)))


if __name__ == '__main__':
    main()
