"""Root-installed deployment controller. No arbitrary command or caller path.

Updates compatible source only; preserves frozen core/H5, role secrets, TTS and
supervisor configuration. Private transaction backups support source rollback.
"""
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

APP = Path('/opt/intelligentai/app')
OPS = Path('/opt/intelligentai/cicd')
METADATA = Path('/opt/intelligentai/deployment.json')
ENV = Path('/etc/intelligentai/analysis.env')
HOLD = Path('/run/intelligentai/direct-admission.closed')
CTL = ['/usr/gatoai/python/extra/system-py3.12/bin/supervisorctl', '-c', str(APP/'deploy/dlpc/supervisord.conf')]
PREFIXES = ('src/', 'seungun/src/', 'configs/', 'deploy/runpod/')
PROTECTED = ('docs/contracts/', 'configs/coaching/', 'configs/deployment/', 'deploy/runpod/harnesses/',
             'deploy/runpod/boot.sh', 'deploy/runpod/prepare_runtime.py',
             'deploy/runpod/run_services.sh', 'deploy/runpod/http-requirements.lock')
DEPENDENCIES = ('pyproject.toml', 'requirements/', 'requirements.txt', 'uv.lock', 'poetry.lock')


def verified_changes(body, token):
    """A runner label/env is not a trust boundary: verify main and its blobs here."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def github(path):
        request = urllib.request.Request('https://api.github.com/repos/voice-coaching/intelligentAI/'+path,
            headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'})
        with opener.open(request, timeout=20) as response:
            raw = response.read(8*1024**2+1)
            if len(raw)>8*1024**2:raise ValueError('GITHUB_COMPARISON_TOO_LARGE')
            return json.loads(raw)
    base, target = body['baseRevision'], body['targetRevision']
    if not all(re.fullmatch('[0-9a-f]{40}', revision) for revision in (base, target)):
        raise ValueError('INVALID_REVISION')
    if github('git/ref/heads/main')['object']['sha'] != target:
        raise ValueError('SUPERSEDED_MAIN_REVISION')
    compare = github('compare/'+base+'...'+target)
    if compare.get('status')!='ahead' or compare.get('merge_base_commit',{}).get('sha')!=base:
        raise ValueError('MAIN_ANCESTRY_REQUIRED')
    files = compare.get('files', [])
    if len(files)>=300:raise ValueError('COORDINATED_LARGE_RELEASE_REQUIRED')
    def tree(revision):
        tree_sha = github('git/commits/'+revision)['tree']['sha']
        document = github('git/trees/'+tree_sha+'?recursive=1')
        if document.get('truncated'):raise ValueError('GITHUB_TREE_TRUNCATED')
        return {row['path']:row for row in document['tree'] if row['type']=='blob'}
    old_tree, new_tree = tree(base), tree(target)
    expected = {}
    for row in files:
        names = [row['filename']]
        if row.get('status')=='renamed':names.append(row['previous_filename'])
        for name in names:
            if name.startswith(PROTECTED) or name.startswith(DEPENDENCIES):
                raise ValueError('COORDINATED_IMMUTABLE_RELEASE_REQUIRED')
            if name.startswith(PREFIXES):
                previous, following = old_tree.get(name), new_tree.get(name)
                if any(item and item['mode'] not in ('100644','100755') for item in (previous,following)):
                    raise ValueError('REGULAR_GIT_SOURCE_REQUIRED')
                expected[name] = dict(before=previous['sha'] if previous else None,
                    after=following['sha'] if following else None, mode=following['mode'] if following else None)
    rows = body['changes']
    if len(rows)!=len(expected) or {row['path'] for row in rows}!=set(expected):
        raise ValueError('GITHUB_SOURCE_MANIFEST_MISMATCH')
    return expected


def atomic(path, data, mode=0o640, gid=10001):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_relative_to(APP):
        parent = path.parent
        while parent != APP:
            if parent.is_symlink():raise ValueError('SOURCE_DIRECTORY_SYMLINK')
            os.chown(parent, 0, 10001); parent.chmod(0o750)
            parent = parent.parent
    temporary = path.with_name(path.name+'.actions-pending')
    with temporary.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.chown(temporary, 0, gid); temporary.chmod(mode); temporary.replace(path)


def values():
    if ENV.is_symlink() or ENV.stat().st_uid != 0 or ENV.stat().st_mode & 0o077:
        raise ValueError('PRIVATE_SETTINGS_REQUIRED')
    return dict(line.split('=', 1) for line in ENV.read_text().splitlines() if line and not line.startswith('#'))


def get(path):
    token = values()['AI_ANALYSIS_API_TOKEN']
    request = urllib.request.Request('http://127.0.0.1:8080'+path, headers={'Authorization':'Bearer '+token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as reply:
        return json.loads(reply.read(131073))


def service(action):
    result = subprocess.run(CTL+[action, 'analysis'], capture_output=True, timeout=180)
    if result.returncode:
        raise ValueError('ANALYSIS_SERVICE_CONTROL_FAILED')


def warm(expected):
    until = time.monotonic()+600
    while time.monotonic() < until:
        try:
            get('/readyz')
            state = get('/health/deployment')
            if all(state.get(k) == expected[k] for k in ('sourceGitRevision','coreManifestSha256','llmManifestSha256','llmLockSha256')):
                return
        except (OSError, ValueError):pass
        time.sleep(5)
    raise ValueError('DEPLOYMENT_READINESS_TIMEOUT')


def deploy(body, raw, token):
    # A lost Actions client must not interrupt an in-flight source transaction.
    # The private receipt and hold remain authoritative until the bounded worker ends.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    before = get('/health/deployment')
    if before['sourceGitRevision'] != body['baseRevision']:
        raise ValueError('DEPLOYMENT_BASE_CHANGED')
    if not re.fullmatch('[0-9a-f]{40}', body['targetRevision']):
        raise ValueError('INVALID_TARGET_REVISION')
    approved = verified_changes(body, token)
    if shutil.disk_usage('/var/lib').free < 14*1024**3+2*len(raw):
        raise ValueError('DEPLOYMENT_DISK_HEADROOM_REQUIRED')
    changes, seen = [], set()
    preflight_deadline = time.monotonic()+120
    for row in body['changes']:
        if time.monotonic()>=preflight_deadline:raise ValueError('SOURCE_PREFLIGHT_TIMEOUT')
        name = row['path']; relative = PurePosixPath(name)
        if (name in seen or not name.startswith(PREFIXES) or name.startswith(PROTECTED)
                or relative.is_absolute() or '..' in relative.parts or '\\' in name):
            raise ValueError('UNSUPPORTED_SOURCE_PATH')
        seen.add(name)
        path = APP/name
        if path.resolve() != path or path.is_symlink():
            raise ValueError('SOURCE_PATH_SYMLINK')
        old = path.read_bytes() if path.exists() else None
        digest = hashlib.sha256(old).hexdigest() if old is not None else None
        old_blob = hashlib.sha1(b'blob '+str(len(old)).encode()+b'\0'+old).hexdigest() if old is not None else None
        if digest != row['beforeSha256'] or old_blob != approved[name]['before']:
            raise ValueError('RUNTIME_SOURCE_DRIFT')
        new = base64.b64decode(row['afterBase64'], validate=True) if row['afterBase64'] is not None else None
        git_blob = hashlib.sha1(b'blob '+str(len(new)).encode()+b'\0'+new).hexdigest() if new is not None else None
        if git_blob != approved[name]['after'] or row['mode'] != approved[name]['mode']:
            raise ValueError('GITHUB_BLOB_MISMATCH')
        if new is not None:
            if len(new) > 2*1024**2 or hashlib.sha256(new).hexdigest() != row['afterSha256']:
                raise ValueError('SOURCE_DIGEST_MISMATCH')
            if row['mode'] not in ('100644', '100755'):
                raise ValueError('SOURCE_MODE_INVALID')
            if name.endswith('.py'):
                compile(new, name, 'exec')
                result = subprocess.run(['/opt/intelligentai/envs/scorer/bin/python', '-c',
                    'import sys; compile(sys.stdin.buffer.read(), "candidate", "exec")'],
                    input=new, capture_output=True, timeout=min(10, max(0.1, preflight_deadline-time.monotonic())))
                if result.returncode:raise ValueError('PRODUCTION_PYTHON_COMPILE_FAILED')
            if name.endswith('.json'):json.loads(new)
        changes.append((path, old, path.stat().st_mode & 0o777 if old is not None else None, new, row['mode']))
    if not changes:
        raise ValueError('EMPTY_DEPLOYMENT')
    job = OPS/'transactions'/body['targetRevision']
    job.mkdir(parents=True, exist_ok=False)
    def record(status, **extra):
        result = dict(status=status, revision=body['targetRevision'], at=datetime.now(timezone.utc).isoformat(), **extra)
        (job/'state.json').write_text(json.dumps(result)+'\n')
        return result
    record('preparing')
    metadata, environment = METADATA.read_bytes(), ENV.read_bytes()
    (job/'metadata.before').write_bytes(metadata); (job/'analysis.env.before').write_bytes(environment)
    for path, old, _, _, _ in changes:
        if old is not None:
            saved = job/'source.before'/path.relative_to(APP)
            saved.parent.mkdir(parents=True, exist_ok=True); saved.write_bytes(old)
    marker = 'github-actions:'+body['targetRevision']+'\n'
    with HOLD.open('x') as stream:stream.write(marker)
    stopped = mutated = False
    try:
        record('draining')
        until, consecutive = time.monotonic()+600, 0
        while time.monotonic() < until:
            state = get('/health/deployment')
            consecutive = consecutive+1 if state.get('activeJobs') == 0 and state.get('directJobs') == 0 else 0
            if consecutive == 2:break
            time.sleep(5)
        else:raise ValueError('DEPLOYMENT_DRAIN_TIMEOUT')
        # Uvicorn/supervisor perform graceful shutdown. Internal callers may receive
        # retryable unavailability; this controller never replays uncertain jobs.
        service('stop'); stopped = True
        record('installing'); mutated = True
        for path, _, _, new, mode in changes:
            if new is None:path.unlink()
            else:atomic(path, new, 0o750 if mode == '100755' else 0o640)
        doc = json.loads(metadata)
        doc['canonicalIntegration'].update(sourceGitRevision=body['targetRevision'], bundleSha256=hashlib.sha256(raw).hexdigest())
        atomic(METADATA, (json.dumps(doc, sort_keys=True)+'\n').encode())
        env = values(); env['VOICE_COACH_BUILD_REVISION'] = body['targetRevision']
        atomic(ENV, ''.join(k+'='+v+'\n' for k,v in sorted(env.items())).encode(), 0o600, 0)
        service('start'); stopped = False
        expected = dict(before, sourceGitRevision=body['targetRevision'])
        record('warming'); warm(expected)
        if HOLD.read_text() != marker:raise ValueError('ADMISSION_HOLD_CHANGED')
        HOLD.unlink()
        try:get('/direct/readyz')
        except Exception:
            HOLD.write_text(marker)
            raise ValueError('DIRECT_READINESS_FAILED') from None
        return record('succeeded', coreManifestSha256=before['coreManifestSha256'], llmManifestSha256=before['llmManifestSha256'])
    except Exception:
        if mutated:
            if not stopped:service('stop')
            for path, old, mode, _, _ in changes:
                if old is None:path.unlink(missing_ok=True)
                else:atomic(path, old, mode)
            atomic(METADATA, metadata); atomic(ENV, environment, 0o600, 0)
            service('start'); warm(before)
        # A failed/uncertain stop must not reopen admission over an unknown service.
        warm(before)
        if HOLD.exists() and HOLD.read_text() == marker:HOLD.unlink()
        record('failed_rolled_back' if mutated else 'failed_before_install')
        raise


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT')
    os.umask(0o077)
    raw = sys.stdin.buffer.read(24*1024**2+1)
    if len(raw) > 24*1024**2:raise ValueError('PAYLOAD_TOO_LARGE')
    body = json.loads(raw)
    OPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (OPS/'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if body == {'operation':'status'}:
            return get('/health/deployment')
        if body.get('operation') != 'deploy':raise ValueError('UNKNOWN_OPERATION')
        token = body.pop('githubToken')
        # Tokens are transient verification input, never part of a release digest/archive.
        public_payload = json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
        return deploy(body, public_payload, token)


if __name__ == '__main__':
    try:print(json.dumps(main()))
    except Exception as error:
        reason = str(error) if re.fullmatch('[A-Z_]+', str(error)) else 'INSPECT_PRIVATE_DEPLOYMENT_STATE'
        print(json.dumps(dict(status='failed', reason=reason, admissionMayRemainClosed=True)))
        raise SystemExit(1)
