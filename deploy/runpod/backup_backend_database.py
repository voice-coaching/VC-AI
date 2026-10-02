"""One-shot, private pre-rollout database backup; never migrates or restores.

Run as root on the approved Backend host. Uses only the database credentials
from its existing service env and an already pulled, digest-pinned pg18 client.
The archive is not a cluster/PITR backup or proof of a successful restore.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import urllib.parse


IMAGE = 'postgres@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873'
ROOT = Path('/var/backups/alpha-canonical')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def inspect_archive(archive):
    require(archive.parent.parent == ROOT and archive.name == 'database.dump'
            and archive.resolve(strict=True) == archive, 'BACKUP_PATH')
    for directory in (ROOT, archive.parent):
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o700, 'BACKUP_DIRECTORY_PERMISSIONS')
    info = archive.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size > 0, 'BACKUP_PERMISSIONS_OR_SIZE')
    base = ['docker', 'run', '--rm', '--pull=never', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--memory=128m', '--cpus=0.5']
    with archive.open('rb') as stream:
        inspection = subprocess.run(base + ['--network=none', '-i', '--entrypoint=pg_restore',
                                            IMAGE, '--list'], stdin=stream,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    require(inspection.returncode == 0 and not inspection.stderr, 'BACKUP_INSPECTION_FAILED')
    require(b'flyway_schema_history' in inspection.stdout, 'BACKUP_HISTORY_MISSING')
    hasher = hashlib.sha256()
    with archive.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    receipt = dict(observedAtUtc=archive.parent.name, archive=str(archive), sha256=hasher.hexdigest(),
                   byteSize=archive.stat().st_size, clientImage=IMAGE,
                   archiveInspection='PASS', restorePerformed=False,
                   scope='single_database_not_cluster_or_PITR')
    with (archive.parent / 'receipt.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(receipt))


def main():
    require(os.geteuid() == 0, 'ROOT_REQUIRED')
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == '--finish-backup':
        inspect_archive(Path(sys.argv[2]))
        return
    require(len(sys.argv) == 1, 'ARGUMENTS')
    source = Path('/etc/alpha-backend.env')
    info = source.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o600, 'ENV_PERMISSIONS')
    values = {}
    for line in source.read_text().splitlines():
        name, sep, value = line.partition('=')
        if name in ('DB_URL', 'DB_USERNAME', 'DB_PASSWORD'):
            require(sep and name not in values, 'DB_ENV_AMBIGUOUS')
            values[name] = value.strip().strip('"').strip("'")
    require(set(values) == {'DB_URL', 'DB_USERNAME', 'DB_PASSWORD'}, 'DB_ENV_MISSING')
    url = urllib.parse.urlsplit(values['DB_URL'].removeprefix('jdbc:'))
    require(url.scheme in ('postgres', 'postgresql') and url.hostname and url.path, 'DB_URL_INVALID')
    env = dict(os.environ, PGHOST=url.hostname, PGPORT=str(url.port or 5432),
               PGDATABASE=url.path.lstrip('/'), PGUSER=values['DB_USERNAME'],
               PGPASSWORD=values['DB_PASSWORD'], PGCONNECT_TIMEOUT='10',
               PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=120000')
    params = urllib.parse.parse_qs(url.query)
    # Match the existing explicit TLS setting; never disable TLS to fix a failure.
    if 'sslmode' in params:
        require(len(params['sslmode']) == 1 and params['sslmode'][0] != 'disable', 'DB_TLS_SETTING')
        env['PGSSLMODE'] = params['sslmode'][0]
    else:
        env['PGSSLMODE'] = 'require'
    for key in ('sslrootcert', 'sslcert', 'sslkey'):
        require(key not in params, 'CLIENT_TLS_FILE_REQUIRES_EXPLICIT_MOUNT')
    require(ROOT.parent.resolve() == ROOT.parent, 'BACKUP_PARENT_SYMLINK')
    if not ROOT.exists():
        ROOT.mkdir(mode=0o700)
    info = ROOT.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o700, 'BACKUP_DIRECTORY_PERMISSIONS')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = ROOT / stamp
    target.mkdir(mode=0o700, exist_ok=False)
    archive = target / 'database.dump'
    base = ['docker', 'run', '--rm', '--pull=never', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--memory=128m', '--cpus=0.5']
    command = base + ['--network=host']
    for key in ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD',
                'PGCONNECT_TIMEOUT', 'PGOPTIONS', 'PGSSLMODE'):
        command += ['--env', key]
    command += ['--entrypoint=pg_dump', IMAGE, '-Fc', '--lock-wait-timeout=10s']
    descriptor = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.PIPE, timeout=150)
        output.flush()
        os.fsync(output.fileno())
    require(result.returncode == 0 and not result.stderr, 'BACKUP_FAILED_OR_WARNED')
    require(archive.stat().st_size > 0, 'BACKUP_EMPTY')
    # Archive inspection only: no SQL is executed or emitted to the console.
    inspect_archive(archive)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not print subprocess diagnostics, env, DB URL or private contents.
        print(json.dumps({'status': 'BACKUP_NOT_CONFIRMED', 'errorType': type(error).__name__}))
        raise SystemExit(1) from None
