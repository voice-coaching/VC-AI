"""D1 expand-only Backend deployment with admission OFF and JAR-only rollback.

Runs on the approved AWS host as root. No secret changes, table removal, backup
restore, inference, fixtures or acceptance tests. Health is operational only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile


BASE_SHA = 'd47e0db8b71906f63a41fd62f422447f4f5f9c8dfa2aab10fdedb01d5824773b'
TARGET_SHA = '86c2aa795348c716ff0319b9d014089620ae11505db703b55ee2890c63ea9f65'
BASE_JAR = Path('/opt/alpha/releases/b410076071c0f0e93e6103872e54082805a5b7da.jar')
LIVE = Path('/opt/alpha/app.jar')
RELEASE = Path('/opt/alpha/releases/canonical-v4-' + TARGET_SHA + '.jar')
BACKUP = Path('/var/backups/alpha-canonical/20261001T151200Z/database.dump')
BACKUP_SHA = '4d52ffd7f6afbc34283d72113369040a584ce811a9d2636462409fb2544fad88'
MIGRATIONS = {
    '33': 'create canonical execution registry',
    '34': 'create canonical evidence receipts',
    '35': 'create canonical callback inbox and results',
    '36': 'create canonical backend journal',
}


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def environment():
    path = Path('/etc/alpha-backend.env')
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o600, 'ENV_PERMISSIONS')
    # Read the exact environment resolved by systemd for this service. Do not
    # reinterpret/modify duplicate assignments or quoted values in its env file.
    result = subprocess.run(['systemctl', 'show', 'alpha-backend.service', '-p', 'MainPID', '--value'],
                            capture_output=True, text=True, timeout=5)
    require(result.returncode == 0 and result.stdout.strip().isdigit(), 'SERVICE_PID')
    pid = int(result.stdout.strip())
    require(pid > 1, 'SERVICE_NOT_RUNNING')
    process = Path('/proc') / str(pid)
    require(b'/opt/alpha/app.jar' in (process / 'cmdline').read_bytes().split(b'\0'), 'SERVICE_COMMAND')
    names = {'DB_URL', 'DB_USERNAME', 'DB_PASSWORD', 'AI_ANALYSIS_CALLBACK_TOKEN',
             'SERVER_PORT', 'MANAGEMENT_SERVER_PORT'}
    values = {}
    for item in (process / 'environ').read_bytes().split(b'\0'):
        name, sep, value = item.partition(b'=')
        key = name.decode('utf-8')
        if sep and (key in names or key.startswith('ANALYSIS_CANONICAL_')):
            require(key not in values, 'SERVICE_ENV_AMBIGUOUS')
            values[key] = value.decode('utf-8')
    for name, value in values.items():
        if name.startswith('ANALYSIS_CANONICAL_') and name.endswith('_ENABLED'):
            require(value == 'false', 'CANONICAL_MUST_REMAIN_DISABLED')
    return values


def query(values, sql):
    url = urllib.parse.urlsplit(values['DB_URL'].removeprefix('jdbc:'))
    require(url.scheme in ('postgres', 'postgresql'), 'DB_SCHEME')
    env = dict(os.environ, PGHOST=url.hostname, PGPORT=str(url.port or 5432),
               PGDATABASE=url.path.lstrip('/'), PGUSER=values['DB_USERNAME'],
               PGPASSWORD=values['DB_PASSWORD'], PGCONNECT_TIMEOUT='10',
               PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=10000')
    params = urllib.parse.parse_qs(url.query)
    if 'sslmode' in params:
        env['PGSSLMODE'] = params['sslmode'][0]
    result = subprocess.run(['psql', '-X', '-w', '-A', '-t', '-F', '|', '-c', sql],
                            env=env, capture_output=True, text=True, timeout=15)
    require(result.returncode == 0, 'READ_ONLY_DB_CHECK_FAILED')
    return result.stdout.strip()


def health(values, canonical):
    # Use only fixed loopback destinations and never emit authentication headers.
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        port = int(values.get('MANAGEMENT_SERVER_PORT', '9091'))
        with opener.open('http://127.0.0.1:' + str(port) + '/internal/actuator/health', timeout=3) as reply:
            if reply.status != 200 or json.load(reply).get('status') != 'UP':
                return False
        port = int(values.get('SERVER_PORT', '8080'))
        headers = {'Authorization': 'Bearer ' + values['AI_ANALYSIS_CALLBACK_TOKEN']}
        request = urllib.request.Request('http://127.0.0.1:' + str(port) + '/api/internal/ai/worker-readiness', headers=headers)
        with opener.open(request, timeout=3) as reply:
            if reply.status != 200 or json.load(reply).get('status') != 'ready':
                return False
        if canonical:
            request = urllib.request.Request(request.full_url + '/v2', headers=headers)
            try:
                with opener.open(request, timeout=3):
                    return False
            except urllib.error.HTTPError as reply:
                body = json.load(reply)
                if reply.code != 503 or body.get('status') != 'not_ready':
                    return False
        return True
    except Exception:
        return False


def switch(target):
    require(target.is_file() and target.parent == LIVE.parent / 'releases', 'RELEASE_PATH')
    temporary = LIVE.with_name('app.jar.canonical-next')
    require(not temporary.exists() and not temporary.is_symlink(), 'SWITCH_ALREADY_PENDING')
    os.symlink(str(target), temporary)
    os.replace(temporary, LIVE)
    descriptor = os.open(LIVE.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def restart_and_observe(values, canonical):
    result = subprocess.run(['systemctl', 'restart', 'alpha-backend.service'],
                            capture_output=True, timeout=110)
    if result.returncode:
        return False
    end = time.monotonic() + 120
    while time.monotonic() < end:
        if health(values, canonical):
            return True
        time.sleep(2)
    return False


def main():
    require(os.geteuid() == 0 and (len(sys.argv) == 2 or
            len(sys.argv) == 3 and sys.argv[2] == '--check'), 'ROOT_AND_STAGED_JAR_REQUIRED')
    os.umask(0o077)
    staged = Path(sys.argv[1])
    require(staged.parent.parent == Path('/tmp')
            and staged.parent.name.startswith('canonical-v4-rollout.')
            and staged.resolve(strict=True) == staged and sha(staged) == TARGET_SHA, 'STAGED_ARTIFACT')
    require(sha(LIVE) == BASE_SHA and sha(BASE_JAR) == BASE_SHA, 'LIVE_BASE_CHANGED')
    require(sha(BACKUP) == BACKUP_SHA, 'BACKUP_CHANGED')
    receipt = json.loads(BACKUP.with_name('receipt.json').read_text())
    require(receipt['sha256'] == BACKUP_SHA and receipt['archiveInspection'] == 'PASS', 'BACKUP_RECEIPT')
    env_sha = sha(Path('/etc/alpha-backend.env'))
    values = environment()
    require(health(values, False), 'LEGACY_BASELINE_NOT_READY')
    require(query(values, 'SELECT max(version::integer) FROM flyway_schema_history WHERE success') == '32',
            'MIGRATION_BASE_CHANGED')
    require(query(values, "SELECT count(*) FROM analysis_results WHERE status IN ('PENDING','PROCESSING') "
                  "AND claim_expires_at>CURRENT_TIMESTAMP") == '0', 'ACTIVE_WORKER_LEASE')
    with zipfile.ZipFile(staged) as artifact:
        names = artifact.namelist()
        for version, description in MIGRATIONS.items():
            filename = 'BOOT-INF/classes/db/migration/V' + version + '__' + description.replace(' ', '_') + '.sql'
            require(names.count(filename) == 1, 'MIGRATION_ARTIFACT_MISSING')
        require(any(n.endswith('/TrainingAnalysisSchemaAdmissionService.class') for n in names), 'ADMISSION_GUARD_MISSING')
    if len(sys.argv) == 3:
        print(json.dumps({'status': 'D1_PREFLIGHT_READY_ADMISSION_OFF'}), flush=True)
        return
    require(not RELEASE.exists(), 'RELEASE_ALREADY_STAGED_RECONCILE_FIRST')
    descriptor = os.open(RELEASE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'wb') as output, staged.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(RELEASE, 0o644)
    require(sha(RELEASE) == TARGET_SHA and sha(LIVE) == BASE_SHA, 'RELEASE_OR_LIVE_CHANGED')
    require(sha(Path('/etc/alpha-backend.env')) == env_sha, 'SERVICE_ENV_CHANGED')
    switched = False
    try:
        switch(RELEASE)
        switched = True
        print(json.dumps({'status': 'SWITCHED_ADMISSION_OFF', 'jarSha256': TARGET_SHA}), flush=True)
        require(restart_and_observe(values, True), 'DEPLOYMENT_HEALTH_FAILED')
        expected = '\n'.join(v + '|' + d + '|t' for v, d in MIGRATIONS.items())
        actual = query(values, 'SELECT version,description,success FROM flyway_schema_history '
                              'WHERE version::integer BETWEEN 33 AND 36 ORDER BY version::integer')
        require(actual == expected, 'MIGRATION_RESULT_MISMATCH')
        require(sha(LIVE) == TARGET_SHA, 'LIVE_RELEASE_DRIFT')
        print(json.dumps({'status': 'D1_DEPLOYED_ADMISSION_OFF', 'jarSha256': TARGET_SHA,
                          'migrations': list(MIGRATIONS), 'legacyReadiness': 'ready',
                          'canonicalReadiness': 'not_ready', 'backup': str(BACKUP),
                          'rollbackJar': str(BASE_JAR)}), flush=True)
    except Exception:
        if switched and sha(LIVE) == TARGET_SHA:
            # Keep additive schema and all evidence. Never restore/drop the live DB here.
            switch(BASE_JAR)
            restored = restart_and_observe(values, False)
            print(json.dumps({'status': 'JAR_ROLLED_BACK', 'legacyReadiness': restored,
                              'schemaPreserved': True, 'jarSha256': sha(LIVE)}), flush=True)
        elif switched:
            print(json.dumps({'status': 'EXTERNAL_RELEASE_DRIFT_NO_OVERWRITE'}), flush=True)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error) if type(error) is RuntimeError and re.fullmatch('[A-Z_]+', str(error)) else None
        print(json.dumps({'status': 'D1_NOT_CONFIRMED', 'errorType': type(error).__name__, 'reason': code}), flush=True)
        raise SystemExit(1) from None
