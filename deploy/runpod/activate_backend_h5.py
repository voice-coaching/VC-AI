"""Approved H5 Backend transition with exact pins and automatic pre-admission rollback.

Run as root on AWS after gating RunPod /v1/analysis-jobs. No inference, test data,
DB writes or FE deployment. Secret env bytes remain root-only on the same host.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

LIVE = Path('/opt/alpha/app.jar')
ENV = Path('/etc/alpha-canonical-secrets/backend-runtime.env')
BASE_SHA = 'd54e6b582b3693488126041ead50209a46ae78a4e258ac5c00a3c882fc2b7efb'
TARGET_SHA = 'a66bcaca14769c2d081fb243b50d6e884a8dbe19c653fa33a4ee197884848b5e'
TARGET = Path('/opt/alpha/releases/68ecde1b609ee8677c15b3f09ed8c14248797589.jar')
VERIFIER = Path('/opt/alpha-canonical/releases/021e17aa5e3bb51170b2cd58cd86d70739177ea82fef324d4cc310d3cd490ed4')
PYTHON = '/opt/alpha-canonical/venvs/py312-jsonschema4251-rfc014/bin/python'
SCHEMA_SHA = '11f7c609421a0c80cb337cb23c1c203cef540f28eea7fce67b16f37577029d5b'


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def environment():
    pid = subprocess.check_output(['systemctl', 'show', 'alpha-backend.service', '-p', 'MainPID', '--value'], text=True).strip()
    require(pid.isdigit() and int(pid) > 1, 'backend_not_running')
    return {key.decode(): value.decode() for key, value in
            (pair.split(b'=', 1) for pair in Path('/proc', pid, 'environ').read_bytes().split(b'\0') if b'=' in pair)}


def query(env, sql):
    url = urllib.parse.urlsplit(env['DB_URL'].removeprefix('jdbc:'))
    require(url.scheme in ('postgres', 'postgresql'), 'db_scheme')
    child = dict(os.environ, PGHOST=url.hostname, PGPORT=str(url.port or 5432),
                 PGDATABASE=url.path.lstrip('/'), PGUSER=env['DB_USERNAME'], PGPASSWORD=env['DB_PASSWORD'],
                 PGCONNECT_TIMEOUT='10', PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=10000')
    params = urllib.parse.parse_qs(url.query)
    if 'sslmode' in params:
        child['PGSSLMODE'] = params['sslmode'][0]
    result = subprocess.run(['psql', '-X', '-w', '-A', '-t', '-c', sql], env=child,
                            capture_output=True, text=True, timeout=15)
    require(result.returncode == 0, 'db_read_failed')
    return result.stdout.strip()


def atomic(path, raw):
    temporary = path.with_name('.' + path.name + '.h5-pending')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def switch(target):
    temporary = LIVE.with_name('.app.jar.h5-pending')
    require(not temporary.exists() and not temporary.is_symlink(), 'jar_switch_pending')
    temporary.symlink_to(target)
    os.replace(temporary, LIVE)


def ready(token, schema_sha, seconds=180):
    until = time.monotonic() + seconds
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < until:
        try:
            request = urllib.request.Request('http://127.0.0.1:8080/api/internal/ai/worker-readiness/v2',
                                            headers={'Authorization': 'Bearer ' + token})
            with opener.open(request, timeout=10) as response:
                doc = json.load(response)
            schemas = {r['file']: r['sha256'] for r in doc['schemaDigests']}
            # Backend infrastructure must be ready before RunPod gets the same
            # schema. Admission stays closed during that expected mismatch.
            if (doc.get('canonicalStatus') == 'READY' and doc.get('canonicalSupported') is True
                    and schemas.get('runpod_result_v4.schema.json') == schema_sha):
                return
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError('backend_readiness_timeout')


def main():
    require(os.geteuid() == 0, 'root_required')
    os.umask(0o077)
    require(LIVE.is_symlink() and sha(LIVE) == BASE_SHA, 'live_jar_changed')
    require(sha('/tmp/canonical-h5-68ecde1.jar') == TARGET_SHA, 'target_jar_changed')
    require(ENV.stat().st_uid == 0 and ENV.stat().st_mode & 0o777 == 0o600, 'env_permissions')
    prior = LIVE.resolve()
    old_env = ENV.read_bytes()
    values = environment()
    require(query(values, 'SELECT max(version::integer) FROM flyway_schema_history WHERE success') == '37', 'migration_base')
    require(query(values, "SELECT count(*) FROM analysis_results WHERE status IN ('PENDING','PROCESSING') AND claim_expires_at>CURRENT_TIMESTAMP") == '0', 'active_analysis')
    identities = json.loads((VERIFIER / 'deployment.json').read_bytes())
    require(all((VERIFIER / p).resolve().is_relative_to(VERIFIER) and sha(VERIFIER / p) == digest
                for p, digest in identities.items()), 'verifier_files_changed')
    account = pwd.getpwnam('ec2-user')
    check = subprocess.run([PYTHON, '-I', str(VERIFIER / 'app/deploy/runpod/canonical_verify.py'),
                            '--core-root', str(VERIFIER / 'frozen/core'), '--llm-root', str(VERIFIER / 'frozen/llm'),
                            '--recordings-prefix', 'recordings/', '--check-installation'],
                           user=account.pw_uid, group=account.pw_gid, extra_groups=[],
                           env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, capture_output=True, timeout=25)
    require(check.returncode == 0 and json.loads(check.stdout).get('h5ManifestSha256') ==
            '9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45', 'verifier_not_ready')
    replacements = {
        'ANALYSIS_CANONICAL_EVIDENCE_SEMANTIC_SCRIPT': str(VERIFIER / 'app/deploy/runpod/canonical_verify.py'),
        'ANALYSIS_CANONICAL_EVIDENCE_SEMANTIC_CORE_ROOT': str(VERIFIER / 'frozen/core'),
        'ANALYSIS_CANONICAL_EVIDENCE_SEMANTIC_LLM_ROOT': str(VERIFIER / 'frozen/llm'),
    }
    lines = old_env.decode().splitlines(keepends=True)
    for key, value in replacements.items():
        matches = [i for i, line in enumerate(lines) if line.startswith(key + '=')]
        require(len(matches) == 1 and lines[matches[0]].strip().split('=', 1)[1] == values[key], 'effective_env_changed')
        lines[matches[0]] = key + '=' + value + '\n'
    backup = Path('/var/backups/alpha-canonical') / ('h5-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700, exist_ok=False)
    (backup / 'backend-runtime.env').write_bytes(old_env)
    (backup / 'receipt.json').write_text(json.dumps(dict(previousJar=str(prior), previousSha256=BASE_SHA,
        targetJar=str(TARGET), targetSha256=TARGET_SHA, verifier=str(VERIFIER))))
    if TARGET.exists():
        require(sha(TARGET) == TARGET_SHA, 'release_conflict')
    else:
        shutil.copyfile('/tmp/canonical-h5-68ecde1.jar', TARGET)
        TARGET.chmod(0o644)
    try:
        atomic(ENV, ''.join(lines).encode())
        switch(TARGET)
        subprocess.run(['systemctl', 'restart', 'alpha-backend.service'], check=True, timeout=60)
        ready(values['AI_ANALYSIS_CALLBACK_TOKEN'], SCHEMA_SHA)
        effective = environment()
        require(all(effective[k] == v for k, v in replacements.items()), 'effective_verifier_mismatch')
        require(sha(LIVE) == TARGET_SHA, 'installed_jar_mismatch')
        print(json.dumps(dict(status='BACKEND_H5_READY', jarSha256=TARGET_SHA, backup=str(backup),
                              verifier=str(VERIFIER), inferencePerformed=False)), flush=True)
    except BaseException:
        atomic(ENV, old_env)
        switch(prior)
        subprocess.run(['systemctl', 'restart', 'alpha-backend.service'], check=True, timeout=60)
        ready(values['AI_ANALYSIS_CALLBACK_TOKEN'], '7a00a4662badbf98939be7dcdde8a442543ab702c1b8d2e3c75fb9e9dd541d79')
        print(json.dumps(dict(status='BACKEND_H5_ROLLED_BACK', backup=str(backup))), flush=True)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not print provider/DB/env exception bodies.
        print(json.dumps(dict(status='H5_DEPLOYMENT_STOPPED', errorType=type(error).__name__)), flush=True)
        raise SystemExit(1)
