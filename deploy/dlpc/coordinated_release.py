"""Root-only coordinated source/schema promotion from a verified main tree.

The normal runner remains limited to compatible source updates. This path is
for an explicitly authorized release that includes contracts or dependencies.
It preserves role secrets, frozen model trees and the live supervisor config.
"""
import argparse
import datetime
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import shutil
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_identity
import deploy_source as controller

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS/'coordinated-source'
SELECTED = ROOT/'selected.json'
APP = controller.APP
PRESERVED = {'deploy/dlpc/supervisord.conf'}


def require(value, code):
    if not value:
        raise ValueError(code)


def encoded(doc):
    return (json.dumps(doc, sort_keys=True, separators=(',', ':'))+'\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def owned(path, limit=4*1024**2):
    info = path.lstat()
    require(path.resolve() == path and path.is_file() and info.st_uid == 0
            and not info.st_mode & 0o022 and info.st_size <= limit, 'COORDINATED_PRIVATE_ASSET')
    return path.read_bytes()


def relative(name):
    value = PurePosixPath(name)
    require(isinstance(name, str) and not value.is_absolute() and '..' not in value.parts
            and '\\' not in name and value.as_posix() == name
            and value.parts[0] != '.git', 'COORDINATED_SOURCE_PATH')
    return APP/name


def source_bytes(path):
    if not path.exists() and not path.is_symlink():
        return None
    return owned(path)


def load(revision, pin=None):
    require(re.fullmatch('[0-9a-f]{40}', revision), 'COORDINATED_REVISION')
    payload = ROOT/'releases'/revision
    raw = owned(payload/'manifest.json')
    if pin:
        require(digest(raw) == pin, 'COORDINATED_MANIFEST_PIN')
    manifest = json.loads(raw)
    require(manifest['revision'] == revision and manifest['preserved'] == sorted(PRESERVED),
            'COORDINATED_MANIFEST')
    require(len(manifest['files']) <= 10000, 'COORDINATED_SOURCE_COUNT')
    for name, row in manifest['files'].items():
        relative(name)
        require(name not in PRESERVED and row['mode'] in ('100644', '100755'), 'COORDINATED_SOURCE_MODE')
        content = owned(payload/'source'/name)
        require(digest(content) == row['sha256'] and
                hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest() == row['blob'],
                'COORDINATED_SOURCE_DIGEST')
        if name.endswith('.py'):
            compile(content, name, 'exec')
        elif name.endswith('.json'):
            json.loads(content)
    for name in manifest['remove']:
        relative(name)
        require(name not in PRESERVED and name not in manifest['files'], 'COORDINATED_REMOVAL')
    return payload, manifest, digest(raw)


def verify_main(manifest, token):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def github(path):
        request = urllib.request.Request('https://api.github.com/repos/voice-coaching/intelligentAI/'+path,
            headers={'Authorization': 'Bearer '+token, 'Accept': 'application/vnd.github+json'})
        with opener.open(request, timeout=20) as response:
            raw = response.read(4*1024**2+1)
            require(len(raw) <= 4*1024**2, 'COORDINATED_GITHUB_BOUND')
            return json.loads(raw)
    revision = manifest['revision']
    require(github('git/ref/heads/main')['object']['sha'] == revision, 'COORDINATED_MAIN_CHANGED')
    tree = github('git/trees/'+revision+'?recursive=1')
    require(not tree.get('truncated') and tree['sha'] == manifest['tree'], 'COORDINATED_TREE')
    expected = {row['path']: (row['sha'], row['mode']) for row in tree['tree']
                if row['type'] == 'blob' and row['path'] not in PRESERVED}
    require(expected == {name: (row['blob'], row['mode']) for name, row in manifest['files'].items()},
            'COORDINATED_GITHUB_SOURCE')


def install(payload, manifest):
    for name, row in manifest['files'].items():
        target = relative(name)
        current = source_bytes(target)
        if current is not None and digest(current) == row['sha256']:
            continue
        controller.atomic(target, owned(payload/'source'/name),
                          0o750 if row['mode'] == '100755' else 0o640)
    for name in manifest['remove']:
        path = relative(name)
        if source_bytes(path) is not None:
            path.unlink()


def settings(manifest):
    values = controller.values()
    values.update(manifest['selection'])
    values['VOICE_COACH_BUILD_REVISION'] = manifest['revision']
    controller.atomic(controller.ENV,
        ''.join(k+'='+v+'\n' for k,v in sorted(values.items())).encode(), 0o600, 0)


def receipts_ready(manifest):
    # A stale READY file must belong to the currently supervised analysis PID.
    import subprocess
    fields = subprocess.run(controller.CTL+['status', 'analysis'], capture_output=True,
                            text=True, timeout=15).stdout.split()
    if len(fields) < 4 or fields[1:3] != ['RUNNING', 'pid']:
        return False
    parent = int(fields[3].rstrip(','))
    def belongs(pid):
        for _ in range(10):
            try:
                raw = Path('/proc', str(pid), 'stat').read_text()
                data = raw[raw.rindex(')')+2:].split()
                if data[0] in ('Z', 'X'):
                    return False
                if pid == parent:
                    return True
                pid = int(data[1])
                if pid <= 1:
                    return False
            except (OSError, ValueError):
                return False
        return False
    execution = False
    for path in Path('/var/lib/intelligentai/work/direct').glob('execution-*/ready.json'):
        record = json.loads(path.read_bytes())
        if (record.get('parent') == parent and record.get('status') == 'READY'
                and record.get('llmManifestSha256') == manifest['directH5'] and belongs(record['pid'])):
            execution = True
            break
    if not execution:
        return False
    for path in Path('/tmp').glob('vc-core-*/host_ready.json'):
        try:
            record = json.loads(path.read_bytes())
            ready = json.loads((path.parent/'ready.json').read_bytes())
            if (record.get('status') == 'READY' and ready.get('coreManifestSha256') == manifest['directCore']
                    and belongs(record['worker_pid'])):
                return True
        except (OSError, ValueError, KeyError):
            continue
    return False


def wait_ready(manifest):
    until = time.monotonic()+300
    while time.monotonic() < until:
        try:
            state = controller.get('/health/deployment')
            if state.get('sourceGitRevision') == manifest['revision'] and receipts_ready(manifest):
                return
        except (OSError, ValueError):
            pass
        time.sleep(3)
    raise ValueError('COORDINATED_READINESS_TIMEOUT')


def activate(payload, manifest, bundle):
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    verify_main(manifest, getpass.getpass('GitHub credential: '))
    require(not controller.HOLD.exists(), 'COORDINATED_EXISTING_HOLD')
    require(all(controller.values().get(k) == v for k,v in manifest['selection'].items()),
            'COORDINATED_SELECTION_CHANGED')
    require(shutil.disk_usage('/var/lib').free >= 14*1024**3 +
            2*sum(len(owned(payload/'source'/n)) for n in manifest['files']),
            'COORDINATED_DISK_HEADROOM')
    for name, before in manifest['before'].items():
        raw = source_bytes(relative(name))
        require((digest(raw) if raw is not None else None) == before, 'COORDINATED_SOURCE_CHANGED')
    revision = manifest['revision']
    job = controller.OPS/'transactions'/revision
    job.mkdir(mode=0o700, parents=True, exist_ok=False)
    def record(status):
        doc = dict(status=status, revision=revision, bundleSha256=bundle,
                   at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   kind='COORDINATED_MAIN_TREE', tree=manifest['tree'])
        controller.atomic(job/'state.json', encoded(doc), 0o600, 0)
        print(json.dumps(doc), flush=True)
        return doc
    old_metadata, old_env = controller.METADATA.read_bytes(), controller.ENV.read_bytes()
    controller.atomic(job/'metadata.before', old_metadata, 0o600, 0)
    controller.atomic(job/'analysis.env.before', old_env, 0o600, 0)
    saved = {}
    for name in manifest['before']:
        path = relative(name);raw = source_bytes(path)
        saved[name] = (raw, path.stat().st_mode & 0o777 if raw is not None else 0o640)
        if raw is not None:
            controller.atomic(job/'source.before'/name, raw, 0o600, 0)
    marker = 'coordinated-main:'+revision+'\n'
    with controller.HOLD.open('x') as stream:
        stream.write(marker)
    mutated = stopped = False
    try:
        record('draining')
        until, zeros = time.monotonic()+600, 0
        while time.monotonic() < until:
            state = controller.get('/health/deployment')
            zeros = zeros+1 if state.get('activeJobs') == 0 and state.get('directJobs') == 0 else 0
            if zeros == 2:
                break
            time.sleep(3)
        require(zeros == 2, 'COORDINATED_DRAIN_TIMEOUT')
        controller.service('stop');stopped = True
        record('installing');mutated = True
        install(payload, manifest)
        doc = json.loads(old_metadata)
        doc['canonicalIntegration'].update(sourceGitRevision=revision, bundleSha256=bundle)
        doc.setdefault('operationalPatches', {})['coordinated-main'] = dict(
            revision=revision, tree=manifest['tree'], sourceManifestSha256=bundle)
        controller.atomic(controller.METADATA, encoded(doc))
        settings(manifest)
        controller.service('start');stopped = False
        record('warming');wait_ready(manifest)
        require(controller.HOLD.read_text() == marker, 'COORDINATED_HOLD_CHANGED')
        controller.HOLD.unlink()
        controller.get('/direct/readyz')
        completion = dict(receipt=record('succeeded'), metadata=doc)
        raw = encoded(completion)
        controller.atomic(payload/'completed.json', raw, 0o600, 0)
        controller.atomic(SELECTED, encoded(dict(revision=revision, manifestSha256=bundle,
                          completionSha256=digest(raw))), 0o600, 0)
    except BaseException:
        if mutated:
            if not stopped:
                controller.service('stop')
            for name, (raw, mode) in saved.items():
                if raw is None:
                    relative(name).unlink(missing_ok=True)
                else:
                    controller.atomic(relative(name), raw, mode)
            controller.atomic(controller.METADATA, old_metadata)
            controller.atomic(controller.ENV, old_env, 0o600, 0)
            controller.service('start')
        record('failed_rolled_back' if mutated else 'failed_before_install')
        # Keep admission closed until the restored runtime has been reconciled.
        raise


def restore():
    selected = json.loads(owned(SELECTED))
    payload, manifest, bundle = load(selected['revision'], selected['manifestSha256'])
    raw = owned(payload/'completed.json')
    require(digest(raw) == selected['completionSha256'], 'COORDINATED_COMPLETION_PIN')
    completed = json.loads(raw)
    require(completed['receipt']['status'] == 'succeeded'
            and completed['receipt']['bundleSha256'] == bundle, 'COORDINATED_COMPLETION')
    # Install the previously observed release, never run an inference warmup.
    if any((source_bytes(relative(n)) is None or digest(source_bytes(relative(n))) != row['sha256'])
           for n, row in manifest['files'].items()):
        import restore_gpt_latency
        restore_gpt_latency.stopped()
    import restore_sol61_model
    restore_sol61_model.assets()
    install(payload, manifest)
    settings(manifest)
    controller.atomic(controller.METADATA, encoded(completed['metadata']))
    receipt = controller.OPS/'transactions'/manifest['revision']/'state.json'
    controller.atomic(receipt, encoded(completed['receipt']), 0o600, 0)
    print('Previously verified coordinated source, frozen assets and release receipt restored')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate', metavar='REVISION')
    args = parser.parse_args()
    require(host_identity.require_root().recovery == 'dlpc-j406-20261016', 'COORDINATED_HOST')
    os.umask(0o077)
    controller.OPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ('deploy_source.py', 'host_identity.py'):
        target = controller.OPS/name
        if not target.exists():
            controller.atomic(target, owned(TOOLS/name), 0o600, 0)
    with (controller.OPS/'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.activate:
            activate(*load(args.activate))
        else:
            restore()


if __name__ == '__main__':
    main()
