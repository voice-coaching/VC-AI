"""One-time, operator-invoked prompt rollout over the existing fixed AWS SSH key.

No reboot hook, new listener, SSH permission or arbitrary remote command.
The staged plan and B2 object identify one reviewed compatible deployment.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request

HOME = Path('/home/2020067')
TOOLS = HOME / '.local/share/intelligentai/recovery/startup-20261006'
WORK = Path('/opt/intelligentai/ops/announcer-20261007')
OLD_BUNDLE = '906df1334b8482a6c1fade401534dd8ea977edec8130da61acca9818b29f344c'
LLM = 'canonical_dlpc_gop_announcer_20261007_v1_h5'
HOLD = Path('/run/intelligentai/direct-admission.closed')
CONFIG = Path('/opt/intelligentai/app/deploy/dlpc/supervisord.conf')
ENV = Path('/etc/intelligentai/analysis.env')
CTL = ['/usr/gatoai/python/extra/system-py3.12/bin/supervisorctl', '-c', str(CONFIG)]


def export(operation, timeout=1800):
    if operation not in ('b2', 'settings'):
        raise ValueError('EXISTING_EXPORT_OPERATION_ONLY')
    argv = ['/usr/sbin/runuser', '-u', '2020067', '--', '/usr/bin/ssh', '-F', '/dev/null',
            '-T', '-p', '22132', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'ConnectTimeout=15', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'HostKeyAlias=13.209.184.226',
            '-o', 'UserKnownHostsFile=' + str(HOME / '.ssh/intelligentai_aws_known_hosts'),
            '-i', str(HOME / '.ssh/intelligentai_aws_20261005'), 'ec2-user@13.209.184.226', 'dlpc-recovery']
    result = subprocess.run(argv, input=json.dumps({'operation': operation}).encode(),
                            capture_output=True, timeout=timeout)
    if result.returncode or len(result.stdout) > 131072:
        raise ValueError('AWS_PROMPT_OPERATION_FAILED')
    return json.loads(result.stdout)


def record(stage):
    raw = json.dumps({'stage': stage, 'at': time.time()}) + '\n'
    (WORK / 'state.json').write_text(raw)
    print(raw.strip(), flush=True)


def private_env():
    if ENV.is_symlink() or ENV.stat().st_uid != 0 or ENV.stat().st_mode & 0o077:
        raise ValueError('PRIVATE_ENV_PERMISSIONS')
    return dict(line.split('=', 1) for line in ENV.read_text().splitlines()
                if line and not line.startswith('#'))


def fetch(path, token=None):
    req = urllib.request.Request('http://127.0.0.1:8080' + path,
                                 headers={'Authorization': 'Bearer ' + token} if token else {})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as reply:
        return json.loads(reply.read(131073))


def service(*args):
    result = subprocess.run(CTL + list(args), capture_output=True, timeout=120)
    if result.returncode: raise ValueError('SUPERVISOR_PROMPT_OPERATION_FAILED')


def backend_ready(values, expected):
    url = values['AI_ANALYSIS_CALLBACK_BASE_URL'].rstrip('/') + '/api/internal/ai/worker-readiness/handoff'
    if url != 'https://api.voice-coaching.site/api/internal/ai/worker-readiness/handoff':
        raise ValueError('EXPECTED_BACKEND_CALLBACK_ORIGIN')
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer '+values['AI_ANALYSIS_CALLBACK_TOKEN']})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=10) as r:
        doc = json.loads(r.read(131073))
    rows = doc.get('schemaDigests')
    actual = rows if isinstance(rows, dict) else {row['file']: row['sha256'] for row in rows or []}
    # Backend admission is intentionally closed by the operator's hold phase.
    # The new, exact contract is the activation signal; resume opens admission
    # only after both sides have passed readiness.
    return doc.get('supported') is True and actual == expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle-sha256', required=True)
    parser.add_argument('--bundle-file-id', required=True)
    parser.add_argument('--bundle-bytes', type=int, required=True)
    parser.add_argument('--resume-waiting', action='store_true',
                        help='After stopping the old installer, resume only its untouched activation wait')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    os.umask(0o077)
    # Creation is the one-time transaction guard. A failed previous attempt must
    # be reconciled; it is never overwritten or inferred to be safe to replay.
    if args.resume_waiting:
        for path in (WORK, WORK / 'state.json', WORK / 'plan.json', WORK / 'runtime.json',
                     WORK / 'analysis.env.before', WORK / 'supervisord.conf.before'):
            if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
                raise ValueError('RESUME_PRIVATE_STATE_REQUIRED')
        if json.loads((WORK / 'state.json').read_bytes()).get('stage') != 'waiting_for_backend_activation':
            raise ValueError('RESUME_REQUIRES_ACTIVATION_WAIT')
        if HOLD.read_text() != 'announcer-prompt-20261007\n':
            raise ValueError('RESUME_HOLD_OWNER_MISMATCH')
        if (ENV.read_bytes() != (WORK / 'analysis.env.before').read_bytes()
                or CONFIG.read_bytes() != (WORK / 'supervisord.conf.before').read_bytes()):
            raise ValueError('RESUME_CONFIGURATION_CHANGED')
    else:
        WORK.mkdir(parents=True, exist_ok=False)
        record('preparing')
    plan = dict(runtimeSha256=args.bundle_sha256, runtimeFileId=args.bundle_file_id, runtimeBytes=args.bundle_bytes)
    if len(args.bundle_sha256) != 64 or not 0 < plan['runtimeBytes'] < 32 * 1024**2:
        raise ValueError('PROMPT_PLAN_INVALID')
    env = private_env()
    token = env['AI_ANALYSIS_API_TOKEN']
    if fetch('/health/deployment', token).get('bundleSha256') != OLD_BUNDLE:
        raise ValueError('PROMPT_SOURCE_DEPLOYMENT_CHANGED')
    # Only the restricted B2 read auth is returned; no master key is installed.
    sys.path.insert(0, str(TOOLS))
    from b2_backup import B2
    if args.resume_waiting:
        prior = json.loads((WORK / 'plan.json').read_bytes())
        if any(prior.get(k) != v for k, v in plan.items()):
            raise ValueError('RESUME_PLAN_CHANGED')
        raw = (WORK / 'runtime.json').read_bytes()
    else:
        with B2(export('b2', 60)).read(plan['runtimeFileId']) as reply:
            raw = reply.read(plan['runtimeBytes'] + 1)
    if len(raw) != plan['runtimeBytes'] or hashlib.sha256(raw).hexdigest() != plan['runtimeSha256']:
        raise ValueError('PROMPT_RUNTIME_HASH_MISMATCH')
    bundle = json.loads(raw)
    if bundle.get('llmDirectory') != LLM:
        raise ValueError('PROMPT_RUNTIME_PLAN_MISMATCH')
    plan['aiRevision'] = bundle['sourceGitRevision']
    expected_schemas = {name: bundle['files']['app/docs/contracts/'+name]['sha256'] for name in (
        'runpod_canonical_input.schema.json', 'runpod_canonical_output.schema.json',
        'runpod_canonical_parent.schema.json', 'runpod_http_control_v1.schema.json',
        'runpod_http_control_v1_2.schema.json', 'runpod_canonical_journal_v1.schema.json',
        'runpod_result_v5.schema.json', 'runpod_analysis_request_v3.schema.json',
        'runpod_canonical_handoff_v1.schema.json')}
    if not args.resume_waiting:
        (WORK / 'runtime.json').write_bytes(raw)
        (WORK / 'plan.json').write_text(json.dumps(plan))
        # Preserve local role secrets only in a root-private rollback directory.
        (WORK / 'analysis.env.before').write_bytes(ENV.read_bytes())
        (WORK / 'supervisord.conf.before').write_bytes(CONFIG.read_bytes())
        if HOLD.exists(): raise ValueError('EXISTING_ADMISSION_HOLD')
        with HOLD.open('x') as f: f.write('announcer-prompt-20261007\n')
    record('draining')
    until, consecutive = time.monotonic() + 1200, 0
    while time.monotonic() < until:
        state = fetch('/health/deployment', token)
        consecutive = consecutive + 1 if state.get('activeJobs') == 0 and state.get('directJobs') == 0 else 0
        if consecutive == 2: break
        time.sleep(5)
    else: raise ValueError('LOCAL_DRAIN_TIMEOUT')
    # The operator runs the existing Backend hold/activate tool over ordinary
    # admin SSH. The restricted recovery key gains no new operations or powers.
    record('waiting_for_backend_activation')
    until = time.monotonic() + 3600
    while time.monotonic() < until:
        try:
            if backend_ready(env, expected_schemas): break
        except (OSError, ValueError): pass
        time.sleep(5)
    else: raise ValueError('WAITING_FOR_OPERATOR_BACKEND_ACTIVATION')
    record('backend_activated')
    state = fetch('/health/deployment', token)
    if state.get('activeJobs') != 0 or state.get('directJobs') != 0:
        raise ValueError('DRAIN_CHANGED')
    service('stop', 'analysis')
    record('installing')
    helper = bundle['files']['app/deploy/dlpc/install_runtime_overlay.py']
    code = base64.b64decode(helper['base64'], validate=True)
    if hashlib.sha256(code).hexdigest() != helper['sha256']: raise ValueError('INSTALLER_DIGEST')
    path = WORK / 'install_runtime_overlay.py'; path.write_bytes(code)
    spec = importlib.util.spec_from_file_location('reviewed_overlay', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.install(raw, plan['runtimeSha256'])
    # Keep the existing admitted supervisor command; TTS is not restarted.
    CONFIG.write_bytes((WORK / 'supervisord.conf.before').read_bytes())
    os.chown(CONFIG, 0, 10001); CONFIG.chmod(0o640)
    env['AI_CANONICAL_LLM_ROOT'] = '/workspace/intelligentai/validation/' + LLM
    env['VOICE_COACH_BUILD_REVISION'] = plan['aiRevision']
    env['AI_CANONICAL_PIPELINE_REVISION'] = 'canonical-dlpc-gop-announcer-20261007-v1'
    tmp = ENV.with_suffix('.prompt-pending')
    with tmp.open('x') as f:
        f.write(''.join(k+'='+v+'\n' for k,v in sorted(env.items()))); f.flush(); os.fsync(f.fileno())
    tmp.chmod(0o600); tmp.replace(ENV)
    # Refresh persistent diagnostic recovery pins, without enabling reboot admission.
    restore = (TOOLS / 'restore_runtime_overlay.py').read_text()
    for key, value in {'FILE_ID': plan['runtimeFileId'], 'SIZE': plan['runtimeBytes'], 'SHA256': plan['runtimeSha256']}.items():
        lines = restore.splitlines()
        matches = [i for i,line in enumerate(lines) if line.startswith(key+' = ')]
        if len(matches) != 1: raise ValueError('RECOVERY_PIN_SOURCE_CHANGED')
        lines[matches[0]] = key+' = '+repr(value)
        restore = '\n'.join(lines)+'\n'
    compile(restore, 'restore_runtime_overlay.py', 'exec')
    for name, data in [('restore_runtime_overlay.py', restore.encode()), ('install_runtime_overlay.py', code)]:
        target = TOOLS / name
        (WORK / (name+'.before')).write_bytes(target.read_bytes())
        target.write_bytes(data); os.chown(target, 120067, 1001); target.chmod(0o600)
    service('start', 'analysis')
    record('warming')
    until = time.monotonic() + 900
    while time.monotonic() < until:
        try:
            fetch('/readyz')
            if fetch('/health/deployment', token).get('bundleSha256') == plan['runtimeSha256']: break
        except (OSError, ValueError): pass
        time.sleep(5)
    else: raise ValueError('RESTORED_PROMPT_READINESS_TIMEOUT')
    record('waiting_for_backend_resume')
    # The operator updates the existing private recovery source only after
    # successful Backend resume; that pinned identity is the completion signal.
    until = time.monotonic() + 3600
    while time.monotonic() < until:
        returned = export('settings', 60)['analysis']
        if (returned.get('AI_CANONICAL_LLM_ROOT') == env['AI_CANONICAL_LLM_ROOT']
                and returned.get('VOICE_COACH_BUILD_REVISION') == plan['aiRevision']): break
        time.sleep(5)
    else: raise ValueError('WAITING_FOR_OPERATOR_BACKEND_RESUME')
    if HOLD.read_text() != 'announcer-prompt-20261007\n': raise ValueError('ADMISSION_HOLD_CHANGED')
    HOLD.unlink()
    try: fetch('/direct/readyz')
    except Exception:
        HOLD.write_text('announcer-prompt-20261007\n')
        raise ValueError('DIRECT_NOT_READY_KEEP_HOLD') from None
    record('complete')


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print(json.dumps({'stage': 'stopped', 'errorType': type(error).__name__,
                          'reason': str(error) if re.fullmatch('[A-Z_]+', str(error)) else 'INSPECT_PRIVATE_STATE',
                          'admissionMayRemainClosed': True}), flush=True)
        raise SystemExit(1)
