"""Main-only Actions client for the separately installed DLPC controller.

Compatible application source can roll forward automatically. Frozen contracts,
model/prompt releases and dependencies need their existing coordinated rollout.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

CONTROLLER = '/opt/intelligentai/cicd/deploy_source.py'
PREFIXES = ('src/', 'seungun/src/', 'configs/', 'deploy/runpod/', 'docs/contracts/')
PROTECTED = ('docs/contracts/', 'configs/coaching/', 'configs/deployment/',
             'deploy/runpod/harnesses/', 'deploy/runpod/boot.sh',
             'deploy/runpod/prepare_runtime.py', 'deploy/runpod/run_services.sh',
             'deploy/runpod/http-requirements.lock')
DEPENDENCIES = ('pyproject.toml', 'requirements/', 'requirements.txt', 'uv.lock', 'poetry.lock')
MAX_BYTES = 24 * 1024**2


def git(*args):
    return subprocess.check_output(['git', *args])


def controller(request):
    raw = json.dumps(request, separators=(',', ':')).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('DEPLOYMENT_PAYLOAD_TOO_LARGE')
    result = subprocess.run(['sudo', '-n', '/usr/bin/python3', '-I', CONTROLLER],
                            input=raw, capture_output=True, timeout=2700)
    if result.returncode:
        # Controller output is a fixed, sanitized receipt; never print subprocess stderr.
        try: doc = json.loads(result.stdout)
        except ValueError: doc = {'status': 'failed', 'reason': 'CONTROLLER_UNAVAILABLE'}
        Path('dlpc-deployment-result.json').write_text(json.dumps(doc, indent=2)+'\n')
        raise ValueError(doc.get('reason', 'DEPLOYMENT_FAILED'))
    return json.loads(result.stdout)


def blob(revision, path):
    rows = git('ls-tree', revision, '--', path).decode().splitlines()
    if not rows:
        return None, None
    mode, _, _ = rows[0].split(None, 2)
    if mode not in ('100644', '100755'):
        raise ValueError('REGULAR_SOURCE_FILES_REQUIRED')
    return git('show', revision+':'+path), mode


def main():
    if (os.environ.get('GITHUB_REPOSITORY') != 'voice-coaching/intelligentAI'
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_EVENT_NAME') not in ('push', 'workflow_dispatch')):
        raise ValueError('MAIN_DEPLOYMENT_ONLY')
    target = os.environ['DEPLOY_REVISION']
    if not re.fullmatch('[0-9a-f]{40}', target) or git('rev-parse', 'HEAD').decode().strip() != target:
        raise ValueError('CHECKOUT_REVISION_MISMATCH')
    request = urllib.request.Request('https://api.github.com/repos/voice-coaching/intelligentAI/git/ref/heads/main',
        headers={'Authorization': 'Bearer '+os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):return None
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=20) as reply:
        latest = json.loads(reply.read(65536))['object']['sha']
    if target != latest:
        result = {'status': 'skipped', 'reason': 'SUPERSEDED_MAIN_REVISION', 'revision': target}
    else:
        state = controller({'operation': 'status'})
        base = state['sourceGitRevision']
        if not re.fullmatch('[0-9a-f]{40}', base):
            raise ValueError('INVALID_DEPLOYED_REVISION')
        subprocess.run(['git', 'merge-base', '--is-ancestor', base, target], check=True)
        paths = git('diff', '--no-renames', '--name-only', '-z', base, target).decode().split('\0')
        changes = []
        for path in filter(None, paths):
            if path.startswith(DEPENDENCIES):
                raise ValueError('DEPENDENCY_RELEASE_REQUIRED')
            if not path.startswith(PREFIXES):
                continue
            before, _ = blob(base, path)
            after, mode = blob(target, path)
            if path.startswith(PROTECTED):
                raise ValueError('COORDINATED_IMMUTABLE_RELEASE_REQUIRED:'+path)
            changes.append(dict(path=path,
                beforeSha256=hashlib.sha256(before).hexdigest() if before is not None else None,
                afterSha256=hashlib.sha256(after).hexdigest() if after is not None else None,
                afterBase64=base64.b64encode(after).decode() if after is not None else None, mode=mode))
        result = (controller(dict(operation='deploy', baseRevision=base, targetRevision=target,
                                  githubToken=os.environ['GH_TOKEN'], changes=changes))
                  if changes else dict(status='skipped', reason='NO_COMPATIBLE_RUNTIME_CHANGES', revision=target))
    Path('dlpc-deployment-result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        reason = str(error)
        if not re.fullmatch('[A-Z_]+(?::[a-zA-Z0-9_./-]+)?', reason):
            reason = 'DEPLOYMENT_CLIENT_FAILED'
        if not Path('dlpc-deployment-result.json').exists():
            Path('dlpc-deployment-result.json').write_text(json.dumps(dict(status='failed', reason=reason))+'\n')
        print(json.dumps(dict(status='failed', reason=reason)))
        raise SystemExit(1)
