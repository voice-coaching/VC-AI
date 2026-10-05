"""Read-only byte/link comparison after a completed asset copy, not model QA."""
import argparse
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
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--copy-state', type=Path, action='append', default=[])
    args = parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]+', args.target) or not re.fullmatch(r'/opt/dlpc-migration-[0-9]+/rootfs', args.destination):
        raise ValueError('ISOLATED_COPY_TARGET_REQUIRED')
    receipt = dict(status='verifying', startedAt=time.time(), paths=[], destination=args.destination,
                   inventorySha256=hashlib.sha256(args.inventory.read_bytes()).hexdigest())
    for name in json.loads(args.inventory.read_bytes())['paths']:
        until = time.monotonic() + 3600
        while args.copy_state:
            states = [json.loads(p.read_bytes()) for p in args.copy_state]
            if any(name in state.get('completed', []) for state in states):
                break
            if any(state.get('status') in ('blocked', 'failed') for state in states) or time.monotonic() >= until:
                raise RuntimeError('MIGRATION_COPY_NOT_COMPLETE')
            time.sleep(5)
        source = Path(name)
        if not source.is_absolute() or '..' in source.parts or not source.exists():
            raise ValueError('SOURCE_PATH_INVALID')
        reply = subprocess.run(['rsync', '-rlcRn', '--no-owner', '--no-group', '--out-format=%i',
            '--exclude=__pycache__', '--exclude=.git', '--exclude=.env*', '--exclude=*.env',
            '--exclude=*.key', '--exclude=*.pem', '--exclude=secrets', '--exclude=*.log',
            '-e', 'ssh -o BatchMode=yes -o ConnectTimeout=15', name,
            args.target + ':' + args.destination + '/'], capture_output=True, text=True, timeout=900)
        matched = reply.returncode == 0 and not reply.stdout.strip()
        receipt['paths'].append(dict(path=name, checksumAndLinksMatch=matched, exitCode=reply.returncode))
        args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
        if not matched:
            receipt['status'] = 'mismatch_or_unavailable'
            args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
            raise RuntimeError('MIGRATION_INTEGRITY_CHECK_FAILED')
    receipt.update(status='verified', finishedAt=time.time())
    args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(dict(status='verified', paths=len(receipt['paths']))))


if __name__ == '__main__':
    main()
