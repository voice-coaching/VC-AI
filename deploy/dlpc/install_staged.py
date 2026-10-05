"""Install a verified staging tree into an empty DLPC container; no cutover.

Preserve all historical absolute paths (conda/RPATH/frozen contracts). Refuse
existing installations. Container recreation requires restoring the assets and
separately injecting private settings again; this is not a NAS backup tool.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--verification', type=Path, required=True)
    args = parser.parse_args()
    root = args.staging
    if (os.geteuid() != 0 or not re.fullmatch(r'/opt/dlpc-migration-[0-9]+/rootfs', str(root))
            or root.resolve(strict=True) != root):
        raise ValueError('DLPC_STAGING_INVALID')
    receipt = json.loads(args.verification.read_bytes())
    inventory = Path(__file__).with_name('assets-20261005.json').read_bytes()
    if (receipt.get('status') != 'verified' or not receipt.get('paths')
            or receipt.get('destination') != str(root)
            or receipt.get('inventorySha256') != hashlib.sha256(inventory).hexdigest()
            or sorted(p['path'] for p in receipt['paths']) != sorted(json.loads(inventory)['paths'])
            or not all(p['checksumAndLinksMatch'] for p in receipt['paths'])):
        raise ValueError('DLPC_INTEGRITY_RECEIPT_REQUIRED')
    moves = [(root / 'workspace', Path('/workspace')),
             (root / 'opt/intelligentai', Path('/opt/intelligentai')),
             (root / 'usr/bin/python3.10', Path('/usr/bin/python3.10')),
             (root / 'usr/lib/python3.10', Path('/usr/lib/python3.10'))]
    for source, target in moves:
        if not source.exists() or target.exists() or target.is_symlink():
            raise ValueError('DLPC_INSTALL_REQUIRES_EMPTY_TARGET')
    manifest = root / 'workspace/intelligentai/releases/canonical_native_core_20261005_v4/release_manifest.json'
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != 'f1009fcdbf75515ddc13e915dcbbf19dfac751ce39779e404749c4f51dd74841':
        raise ValueError('DLPC_CORE_IDENTITY_CHANGED')
    try:
        if pwd.getpwuid(10001).pw_name != 'intelligentai':
            raise ValueError('DLPC_SERVICE_UID_OCCUPIED')
    except KeyError:
        subprocess.run(['groupadd', '-g', '10001', 'intelligentai'], check=True)
        subprocess.run(['useradd', '-u', '10001', '-g', '10001', '-M', '-s', '/usr/sbin/nologin', 'intelligentai'], check=True)
    state = root.parent / 'installation.json'
    record = dict(status='installing', moved=[])
    for source, target in moves:
        os.rename(source, target)
        record['moved'].append(str(target))
        state.write_text(json.dumps(record, indent=2) + '\n')
    # Read-only artifacts for the dedicated worker. Do not traverse symlinks.
    for base in (Path('/opt/intelligentai'), Path('/workspace'), Path('/usr/lib/python3.10')):
        for folder, dirs, files in os.walk(base, followlinks=False):
            for p in [Path(folder), *(Path(folder) / n for n in files)]:
                if p.is_symlink():
                    continue
                mode = p.stat().st_mode
                os.chown(p, 0, 10001)
                p.chmod(0o550 if p.is_dir() or mode & 0o111 else 0o440)
    os.chown('/usr/bin/python3.10', 0, 10001)
    os.chmod('/usr/bin/python3.10', 0o550)
    for name, owner, mode in (
        ('/run/intelligentai', 0, 0o711), ('/run/intelligentai/logs', 0, 0o700),
        ('/run/intelligentai/worker', 10001, 0o700),
        ('/run/intelligentai/worker/tmp', 10001, 0o700),
        ('/run/intelligentai/worker/tmp/matplotlib', 10001, 0o700),
        ('/run/intelligentai/worker/tmp/huggingface', 10001, 0o700),
        ('/run/intelligentai/worker/work', 10001, 0o700),
        ('/run/intelligentai/canonical', 10001, 0o700),
        ('/run/voice-coaching-tts', 10001, 0o700), ('/etc/intelligentai', 0, 0o700),
    ):
        p = Path(name)
        p.mkdir(parents=True, exist_ok=True)
        os.chown(p, owner, 10001 if owner else 0)
        p.chmod(mode)
    lock = Path('/run/intelligentai/gpu.lock')
    with lock.open('x'):
        pass
    os.chown(lock, 10001, 10001)
    lock.chmod(0o600)
    record['status'] = 'installed_not_started'
    state.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(dict(status=record['status'])))


if __name__ == '__main__':
    main()
