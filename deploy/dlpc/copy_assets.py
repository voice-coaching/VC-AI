"""Resumable, explicit RunPod asset copy. No keys, user recordings or activation.

Run on the verified source Pod. The destination is an isolated, user-owned
staging directory. rsync never deletes destination files or starts a service.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.target):
        raise ValueError('SSH_ALIAS_REQUIRED')
    if not re.fullmatch(r'/opt/dlpc-migration-[0-9]+/rootfs', args.destination):
        raise ValueError('ISOLATED_DESTINATION_REQUIRED')
    inventory = json.loads(args.inventory.read_bytes())
    paths = inventory['paths']
    if not isinstance(paths, list) or not paths or len(paths) != len(set(paths)):
        raise ValueError('ASSET_INVENTORY_INVALID')
    for name in paths:
        p = Path(name)
        if (not p.is_absolute() or '..' in p.parts or not p.exists()
                or not (any(p.is_relative_to(root) for root in ('/opt/intelligentai', '/workspace/intelligentai', '/workspace/voice-coaching-tts'))
                        or name in ('/usr/bin/python3.10', '/usr/lib/python3.10'))):
            raise ValueError('ASSET_PATH_INVALID')
    manifest = Path('/workspace/intelligentai/releases/canonical_native_core_20261005_v4/release_manifest.json')
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != inventory['coreManifestSha256']:
        raise ValueError('MIGRATION_SOURCE_RELEASE_CHANGED')
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock = args.state.with_suffix('.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = dict(status='copying', startedAt=time.time(), completed=[])

    def save():
        tmp = args.state.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, indent=2) + '\n')
        tmp.replace(args.state)

    save()
    try:
        for name in paths:
            state['current'] = name
            save()
            # Keep at least 5 GiB of the local container disk unallocated.
            probe = subprocess.run(['ssh', '-o', 'BatchMode=yes', args.target,
                'df -B1 --output=avail ' + args.destination], capture_output=True, text=True, check=True)
            free = int(probe.stdout.splitlines()[-1])
            size = int(subprocess.run(['du', '-sb', name], capture_output=True, text=True,
                                      check=True).stdout.split()[0])
            # Conservative for resumed paths and excludes; never overfills disk.
            if free < size + 5 * 1024**3:
                raise RuntimeError('TARGET_CAPACITY_RESERVE')
            subprocess.run(['rsync', '-aRz', '--no-owner', '--no-group', '--partial',
                '--include=**/certifi/cacert.pem',
                '--exclude=__pycache__', '--exclude=.git', '--exclude=.env*', '--exclude=*.env', '--exclude=*.key',
                '--exclude=*.pem', '--exclude=secrets', '--exclude=*.log',
                '-e', 'ssh -o BatchMode=yes -o ConnectTimeout=15',
                name, args.target + ':' + args.destination + '/'], check=True)
            state['completed'].append(name)
            save()
        state['status'] = 'copied_pending_integrity_verification'
    except Exception as error:
        state['status'] = 'blocked'
        state['reason'] = type(error).__name__
        raise
    finally:
        state['updatedAt'] = time.time()
        save()


if __name__ == '__main__':
    main()
