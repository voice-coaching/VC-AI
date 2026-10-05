"""Private durable asset archive under the DLPC user's own NAS quota."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--verification', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0 or args.staging.resolve(strict=True) != args.staging:
        raise ValueError('BACKUP_REQUIRES_MIGRATION_USER')
    verification = json.loads(args.verification.read_bytes())
    if verification.get('status') != 'verified' or verification.get('destination') != str(args.staging):
        raise ValueError('BACKUP_REQUIRES_VERIFIED_COPY')
    # Public certifi roots are runtime dependencies, not private PEM credentials.
    # The copy allowlist admits only these PEMs; reject unexpected additions.
    for pem in args.staging.rglob('*.pem'):
        if pem.is_symlink() or tuple(pem.parts[-2:]) != ('certifi', 'cacert.pem'):
            raise ValueError('BACKUP_UNEXPECTED_PEM')
        raw = pem.read_bytes()
        if b'-----BEGIN CERTIFICATE-----' not in raw or b'PRIVATE KEY' in raw:
            raise ValueError('BACKUP_INVALID_PUBLIC_CA')
    os.umask(0o077)
    root = Path.home() / '.local/share/intelligentai/migration/20261005'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    archive = root / 'runtime-assets.tar.gz'
    partial = root / 'runtime-assets.tar.gz.partial'
    if archive.exists() or partial.exists():
        raise ValueError('BACKUP_DESTINATION_EXISTS')
    paths = root / 'archive-paths.txt'
    names = [str(Path(p['path']).relative_to('/')) for p in verification['paths']]
    if any('\n' in name or '..' in Path(name).parts for name in names):
        raise ValueError('BACKUP_PATH_INVALID')
    paths.write_text('\n'.join(names) + '\n')
    state = dict(status='archiving', startedAt=time.time(), verificationSha256=hashlib.sha256(args.verification.read_bytes()).hexdigest())
    record = root / 'backup.json'
    record.write_text(json.dumps(state, indent=2) + '\n')
    with (root / 'archive.log').open('xb') as log:
        reply = subprocess.run(['nice', '-n', '10', 'tar', '-I', 'gzip -1', '-cf', str(partial),
            '-C', str(args.staging), '--exclude=.env*', '--exclude=*.env', '--exclude=*.key',
            '--exclude=secrets', '--exclude=__pycache__', '--exclude=*.log',
            '-T', str(paths)], stdout=log, stderr=log)
    if reply.returncode:
        state.update(status='failed', exitCode=reply.returncode,
                     partialBytes=partial.stat().st_size if partial.exists() else 0)
        record.write_text(json.dumps(state, indent=2) + '\n')
        # Only this incomplete archive, never source files or another backup.
        partial.unlink(missing_ok=True)
        raise RuntimeError('BACKUP_FAILED_CHECK_PRIVATE_LOG')
    with partial.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    partial.rename(archive)
    state.update(status='archived', finishedAt=time.time(), bytes=archive.stat().st_size,
                 sha256=digest, inferenceQualityVerified=False)
    record.write_text(json.dumps(state, indent=2) + '\n')
    print(json.dumps(state))


if __name__ == '__main__':
    main()
