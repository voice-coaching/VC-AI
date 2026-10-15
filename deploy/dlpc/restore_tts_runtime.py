"""Restore the previously deployed TTS patch and verify its synthesis identity."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

APP = Path('/opt/intelligentai/app')
ROOT = Path('/workspace/voice-coaching-tts/service')
BEFORE = '8b41fcf817af76a9c0aaabae0df7c3a8efcd03c754fe92bffe3963daa17b4282'
AFTER = '8f25077eab50503b914e530866225d8984899dac2dcbf2791c622d90c36ea83b'
FINGERPRINT = 'ace97bc52df31f304513bc5f18177669723fe996f5cd19235691a5449d9f3766'


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    os.umask(0o077)
    source = ROOT / 'server.py'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest not in (BEFORE, AFTER):
        raise ValueError('TTS_SOURCE_CHANGED')
    if digest == BEFORE:
        if Path('/run/intelligentai/dlpc-supervisor.sock').exists():
            from start_production import service_state
            if service_state('tts') not in ('STOPPED', 'EXITED', 'FATAL'):
                raise ValueError('TTS_MUST_BE_STOPPED')
        backup = Path('/opt/intelligentai/ops/tts-recovery')
        backup.mkdir(parents=True, mode=0o700, exist_ok=True)
        saved = backup / 'server.py.before'
        if not saved.exists():
            saved.write_bytes(source.read_bytes())
        with tempfile.TemporaryDirectory(dir=backup) as staging:
            destination = Path(staging) / 'service'
            subprocess.run(['/usr/bin/python3', str(APP / 'deploy/dlpc/build_tts.py'),
                '--source', str(source), '--destination', str(destination)], check=True, timeout=60)
            if hashlib.sha256((destination / 'server.py').read_bytes()).hexdigest() != AFTER:
                raise ValueError('TTS_REBUILT_HASH_MISMATCH')
            # Install dependencies before the server, which is the completion marker.
            for name in ('gpu_budget.py', 'resource_budget.py', 'dlpc-manifest.json', 'server.py'):
                target = ROOT / name
                pending = target.with_suffix(target.suffix + '.recovery-pending')
                shutil.copyfile(destination / name, pending)
                os.chown(pending, 0, 10001)
                pending.chmod(0o640)
                pending.replace(target)
    result = subprocess.run(['/usr/bin/python3', str(APP / 'deploy/dlpc/tts_synthesis_identity.py')],
                            capture_output=True, timeout=60)
    if result.returncode or json.loads(result.stdout).get('fingerprint') != FINGERPRINT:
        raise ValueError('TTS_SYNTHESIS_IDENTITY_MISMATCH')
    print('Pinned TTS runtime and synthesis fingerprint verified')


if __name__ == '__main__':
    main()
