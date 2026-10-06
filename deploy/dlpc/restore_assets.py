"""Operator-driven pinned B2 restoration, independent of a running RunPod.

Pass the short-lived Native API auth object on stdin, as for b2_restore.py.
Private runtime settings are supplied separately after this command succeeds.
An interrupted/failed state is retained for reconciliation, never overwritten.
"""
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from b2_restore import RUN, OBJECTS


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    os.umask(0o077)
    tools = Path(__file__).resolve().parent
    with open('/run/intelligentai-asset-restore.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        installed = [Path(p).exists() for p in (
            '/opt/intelligentai', '/workspace/intelligentai', '/workspace/voice-coaching-tts')]
        if any(installed):
            raise ValueError('EXISTING_INSTALLATION_REQUIRES_RECONCILIATION')
        if not RUN.exists():
            subprocess.run(['/usr/bin/python3', str(tools / 'b2_restore.py')], check=True)
        elif not (RUN / 'state.json').is_file():
            raise ValueError('RESTORE_STATE_UNKNOWN')
        deadline = time.monotonic() + 7200
        while time.monotonic() < deadline:
            path = RUN / 'state.json'
            state = json.loads(path.read_bytes()) if path.exists() else {}
            if state.get('status') == 'download_verified':
                break
            if state.get('status') == 'failed':
                raise ValueError('DOWNLOAD_FAILED_RECONCILE_PARTIAL')
            time.sleep(3)
        else:
            # The download may still be running; do not launch another worker.
            raise ValueError('DOWNLOAD_WAIT_TIMEOUT_INSPECT_EXISTING_WORKER')
        if (RUN / 'restore-state.json').exists():
            raise ValueError('RESTORE_ALREADY_ATTEMPTED_RECONCILE_STATE')
        subprocess.run(['/usr/bin/python3', str(tools / 'restore_downloaded.py'), '--worker'], check=True)
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
