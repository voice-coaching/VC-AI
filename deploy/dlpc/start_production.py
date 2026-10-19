"""Resume the pinned DLPC production installation after verified recovery.

Called by the persistent recovery hook. Existing operator holds and interrupted
job receipts remain authoritative. No recording is submitted or replayed here.
"""
import configparser
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

import deployed_release
import host_identity

APP = Path('/opt/intelligentai/app')
CONFIG = APP / 'deploy/dlpc/supervisord.conf'
ENV = Path('/etc/intelligentai/analysis.env')
HOLD = Path('/run/intelligentai/direct-admission.closed')
MARKER = 'dlpc-production-recovery-v1\n'
BIN = Path(host_identity.current().supervisor_bin)
TOOLS = Path(__file__).resolve().parent
FLAGS = ('AI_CANONICAL_BOOTSTRAP_READY', 'AI_CANONICAL_HANDOFF_ENABLED',
         'AI_CANONICAL_ADMISSION_ENABLED', 'AI_DIRECT_ENABLED')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def get(url, token=None):
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=10) as reply:
            raw = reply.read(131073)
            if len(raw) > 131072:
                raise ValueError('HEALTH_RESPONSE_TOO_LARGE')
            body = json.loads(raw)
            return reply.status, body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        return status, {}
    except (OSError, ValueError):
        return None, {}


def backend_ready(values, *, require_admission=True):
    if values.get('AI_ANALYSIS_CALLBACK_BASE_URL') != 'https://api.voice-coaching.site':
        raise ValueError('BACKEND_ORIGIN_CHANGED')
    names = ('runpod_http_control_v1.schema.json', 'runpod_http_control_v1_2.schema.json',
             'runpod_canonical_parent.schema.json', 'runpod_canonical_input.schema.json',
             'runpod_canonical_output.schema.json', 'runpod_canonical_journal_v1.schema.json',
             'runpod_analysis_request_v4.schema.json', 'runpod_result_v6.schema.json',
             'runpod_canonical_handoff_v2.schema.json', 'runpod_media_preparation_v1.schema.json',
             'runpod_visual_coaching_input_v1.schema.json')
    expected = {name: hashlib.sha256((APP / 'docs/contracts' / name).read_bytes()).hexdigest()
                for name in names}
    status, body = get(values['AI_ANALYSIS_CALLBACK_BASE_URL'] +
                       '/api/internal/ai/worker-readiness/handoff', values['AI_ANALYSIS_CALLBACK_TOKEN'])
    return (status == 200 and body.get('contractVersion') == 'voice-coaching.canonical-handoff.v2'
            and body.get('supported') is True
            and (not require_admission or body.get('admissionEnabled') is True)
            and body.get('schemaDigests') == expected)


def run(argv, timeout=180):
    result = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    if result.returncode:
        raise ValueError('PRODUCTION_COMMAND_FAILED')


def service_state(name):
    result = subprocess.run([str(BIN / 'supervisorctl'), '-c', str(CONFIG), 'status', name],
                            capture_output=True, text=True, timeout=15)
    fields = result.stdout.split()
    if len(fields) >= 2 and fields[0] == name:
        return fields[1]
    if not Path('/run/intelligentai/dlpc-supervisor.sock').exists():
        return 'ABSENT'
    raise ValueError('SUPERVISOR_STATE_UNKNOWN')


def start(name):
    state = service_state(name)
    if state in ('RUNNING', 'STARTING'):
        return
    if state not in ('STOPPED', 'EXITED', 'FATAL'):
        raise ValueError('SERVICE_STATE_REQUIRES_RECONCILIATION')
    run([str(BIN / 'supervisorctl'), '-c', str(CONFIG), 'start', name])


def wait_for(check, reason, seconds=1200):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(5)
    raise ValueError(reason)


def replace(path, raw, mode, gid=0):
    temporary = path.with_name(path.name + '.production-pending')
    with temporary.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(temporary, 0, gid)
    temporary.chmod(mode)
    temporary.replace(path)


def main():
    identity = host_identity.require_root()
    os.umask(0o077)
    with open('/run/intelligentai-bootstrap.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = json.loads(Path('/opt/intelligentai/deployment.json').read_bytes())
        # The pinned baseline, or the release the Actions controller last deployed
        # (proven by its root-only receipt), so a restart resumes the deployed main.
        REVISION, SHA256 = deployed_release.accepted(metadata)
        # Read literal private settings with the deployed launcher's parser.
        import importlib.util
        spec = importlib.util.spec_from_file_location('dlpc_launch', APP / 'deploy/dlpc/launch.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        values = launcher.private_settings(ENV)
        if values.get('VOICE_COACH_BUILD_REVISION') != REVISION:
            raise ValueError('RECOVERY_SETTINGS_RELEASE_CHANGED')
        if any(not shutil.which(name) for name in ('ffmpeg', 'ffprobe', 'nvidia-smi')):
            raise ValueError('RUNTIME_SYSTEM_DEPENDENCIES_MISSING')
        if HOLD.exists() and (HOLD.is_symlink() or HOLD.read_text() != MARKER):
            raise ValueError('EXISTING_OPERATOR_HOLD')
        # Backend admission depends on our live /health/handoff executor.
        # Before startup require its own support and exact contracts; require
        # full admission again after our executor becomes ready.
        wait_for(lambda: backend_ready(values, require_admission=False),
                 'BACKEND_CONTRACT_NOT_READY', seconds=180)
        run([host_identity.current().tools_python, str(TOOLS / 'prepare_restored_runtime.py')])
        if shutil.disk_usage('/var/lib').free < 14 * 1024**3:
            raise ValueError('DISK_HEADROOM_REQUIRED')
        raw = CONFIG.read_text()
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(raw)
        diagnostic = '/usr/bin/python3 /opt/intelligentai/app/deploy/dlpc/launch.py analysis'
        command = config['program:analysis']['command']
        if command not in (diagnostic, diagnostic + ' --admit') or 'program:aws-tunnel' in config:
            raise ValueError('SUPERVISOR_CONFIGURATION_CHANGED')
        changed = command == diagnostic or any(values.get(key) != 'true' for key in FLAGS)
        analysis_state = service_state('analysis')
        if changed and analysis_state not in ('ABSENT', 'STOPPED', 'EXITED', 'FATAL'):
            raise ValueError('LIVE_CONFIGURATION_REQUIRES_RECONCILIATION')
        if analysis_state in ('ABSENT', 'STOPPED', 'EXITED', 'FATAL') and not HOLD.exists():
            with HOLD.open('x') as stream:
                stream.write(MARKER)
        if changed:
            backup = Path('/opt/intelligentai/ops/production-recovery')
            backup.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name, data in (('analysis.env.before', ENV.read_bytes()),
                               ('supervisord.conf.before', CONFIG.read_bytes())):
                path = backup / name
                if not path.exists():
                    with path.open('xb') as stream:
                        stream.write(data)
            if not HOLD.exists():
                with HOLD.open('x') as stream:
                    stream.write(MARKER)
            values.update({key: 'true' for key in FLAGS})
            replace(ENV, ''.join(k + '=' + v + '\n' for k, v in sorted(values.items())).encode(), 0o600)
            if command == diagnostic:
                old = 'command=' + diagnostic + '\n'
                if raw.count(old) != 1:
                    raise ValueError('SUPERVISOR_COMMAND_SHAPE')
                replace(CONFIG, raw.replace(old, 'command=' + diagnostic + ' --admit\n').encode(), 0o640, 10001)
        host = host_identity.current()
        run(['/usr/sbin/runuser', '-u', host.operator, '--', host.tools_python,
             str(host.home / '.local/share/intelligentai/aws-tunnel/start_aws_tunnel.py')])
        if service_state('analysis') == 'ABSENT':
            run([str(BIN / 'supervisord'), '-c', str(CONFIG)])
        else:
            run([str(BIN / 'supervisorctl'), '-c', str(CONFIG), 'reread'])
            run([str(BIN / 'supervisorctl'), '-c', str(CONFIG), 'update', 'analysis'])
        start('analysis')
        print('Waiting for analysis executor and Backend handoff readiness', flush=True)
        def analysis_ready():
            status, body = get('http://127.0.0.1:8080/health/services', values['AI_ANALYSIS_API_TOKEN'])
            if (status != 200 or body.get('executorReady') is not True or body.get('handoffReady') is not True
                    or body.get('workerRevision') != REVISION):
                return False
            status, body = get('http://127.0.0.1:8080/health/deployment', values['AI_ANALYSIS_API_TOKEN'])
            return status == 200 and body.get('sourceGitRevision') == REVISION and body.get('bundleSha256') == SHA256
        wait_for(analysis_ready, 'ANALYSIS_READINESS_TIMEOUT')
        start('tts')
        print('Waiting for TTS readiness', flush=True)
        token = Path('/etc/intelligentai/tts-token').read_text().strip()
        def tts_ready():
            status, body = get('http://127.0.0.1:8083/health/ready', token)
            return status == 200 and body.get('revision') == 'melo-ko-practice-dlpc-r1'
        wait_for(tts_ready, 'TTS_READINESS_TIMEOUT', seconds=600)
        if (get('http://127.0.0.1:8080/health/services')[0] not in (401, 403) or
                get('http://127.0.0.1:8083/health/ready')[0] not in (401, 403)):
            raise ValueError('SERVICE_AUTHENTICATION_REQUIRED')
        wait_for(lambda: backend_ready(values), 'BACKEND_ADMISSION_CHANGED', seconds=900)
        print('Checking direct and public readiness', flush=True)
        released_hold = False
        if HOLD.exists():
            if HOLD.is_symlink() or HOLD.read_text() != MARKER:
                raise ValueError('ADMISSION_HOLD_CHANGED')
            HOLD.unlink()
            released_hold = True
        try:
            wait_for(lambda: get('http://127.0.0.1:8080/direct/readyz')[1].get('ready') is True,
                     'DIRECT_READINESS_TIMEOUT', seconds=600)
            def public_ready():
                status, body = get('https://ai.voice-coaching.site/health/ready', token)
                return (status == 200 and body.get('revision') == 'melo-ko-practice-dlpc-r1'
                        and get('https://ai.voice-coaching.site/readyz')[0] == 200
                        and get('https://ai.voice-coaching.site/direct/readyz')[1].get('ready') is True)
            wait_for(public_ready, 'PUBLIC_TUNNEL_READINESS_TIMEOUT', seconds=180)
        except BaseException:
            # Only restore the hold released by this recovery. Preserve any
            # newer operator hold, including one created concurrently.
            if released_hold:
                try:
                    with HOLD.open('x') as stream:
                        stream.write(MARKER)
                except FileExistsError:
                    pass
            raise
        print(json.dumps(dict(productionServicesReady=True, productionAdmission=True,
                              bundleSha256=SHA256, inferenceQualityVerified=False)))


if __name__ == '__main__':
    main()
