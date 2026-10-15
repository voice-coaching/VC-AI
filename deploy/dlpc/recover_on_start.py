"""Persistent-home entry point: AWS private settings + pinned B2 assets.

Invoked in the background by Jupyter's Python configuration. Restore pinned
assets and role settings, then resume production after Backend/readiness gates.
"""
import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time

TOOLS = Path(__file__).resolve().parent
HOME = Path('/home/2020067')
STATE = Path('/run/intelligentai-recovery-state.json')
STEP = 'checking'


def record(status, **fields):
    global STEP
    STEP = status
    tmp = STATE.with_suffix('.tmp')
    admission = True if status == 'production_services_ready' else None
    tmp.write_text(json.dumps(dict(status=status, at=time.time(),
        productionAdmission=admission, **fields)))
    tmp.replace(STATE)


def export(operation):
    argv = ['/usr/sbin/runuser', '-u', '2020067', '--', '/usr/bin/ssh', '-F', '/dev/null',
            '-T', '-p', '22132', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'HostKeyAlias=13.209.184.226',
            '-o', 'UserKnownHostsFile=' + str(HOME / '.ssh/intelligentai_aws_known_hosts'),
            '-i', str(HOME / '.ssh/intelligentai_aws_20261005'),
            'ec2-user@13.209.184.226', 'dlpc-recovery']
    result = subprocess.run(argv, input=json.dumps({'operation': operation}).encode(),
                            capture_output=True, timeout=60)
    if result.returncode or not 1 <= len(result.stdout) <= 131072:
        raise ValueError('AWS_EXPORT_UNAVAILABLE')
    json.loads(result.stdout)
    return result.stdout


def run(name, data=None, timeout=180):
    # Root-only diagnostics retain the failing phase without logging stdin.
    with open('/run/intelligentai-recovery.log', 'ab') as log:
        log.write(('\nSTEP ' + name + '\n').encode())
        log.flush()
        result = subprocess.run(['/usr/bin/python3', str(TOOLS / name)], input=data,
                                stdout=log, stderr=log, timeout=timeout)
    if result.returncode:
        raise ValueError('RECOVERY_STEP_FAILED_' + name.removesuffix('.py').upper())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore-only', action='store_true',
                        help='Restore and verify assets/settings without starting services')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    os.umask(0o077)
    with open('/run/intelligentai-recovery.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        record('checking')
        assets = [Path(p).exists() for p in ('/opt/intelligentai/app',
                  '/workspace/intelligentai', '/workspace/voice-coaching-tts')]
        if any(assets) and not all(assets):
            raise ValueError('PARTIAL_ASSETS_REQUIRE_OPERATOR')
        restore_state = Path('/opt/intelligentai-b2-restore-20261006/restore-state.json')
        if all(assets) and restore_state.exists():
            if json.loads(restore_state.read_bytes()).get('status') != 'assets_restored_not_started':
                raise ValueError('INCOMPLETE_RESTORE_REQUIRES_OPERATOR')
        if not any(assets):
            record('restoring_assets')
            run('restore_assets.py', export('b2'), timeout=10800)
        from restore_runtime_overlay import SHA256
        metadata = json.loads(Path('/opt/intelligentai/deployment.json').read_bytes())
        if metadata.get('canonicalIntegration', {}).get('bundleSha256') != SHA256:
            if (not restore_state.exists() or
                    Path('/run/intelligentai/dlpc-supervisor.sock').exists()):
                raise ValueError('LIVE_OVERLAY_RECONCILIATION_REQUIRED')
            record('restoring_gop_overlay')
            run('restore_runtime_overlay.py', export('b2'), timeout=180)
        record('checking_cuda_libraries')
        run('restore_cuda_libraries.py', export('b2'), timeout=900)
        metadata = json.loads(Path('/opt/intelligentai/deployment.json').read_bytes())
        if metadata.get('canonicalIntegration', {}).get('bundleSha256') != SHA256:
            raise ValueError('GOP_OVERLAY_RECONCILIATION_REQUIRED')
        files = [Path('/etc/intelligentai') / n for n in ('analysis.env', 'tts-token')]
        if any(p.exists() for p in files) and not all(p.exists() for p in files):
            raise ValueError('PARTIAL_SETTINGS_REQUIRE_OPERATOR')
        if not any(p.exists() for p in files):
            record('receiving_settings')
            run('receive_settings.py', export('settings'))
        record('checking_direct_resident_recovery')
        run('restore_direct_resident.py')
        record('checking_resident_process_health')
        run('restore_resident_health.py')
        record('restoring_tts_runtime')
        run('restore_tts_runtime.py')
        if args.restore_only:
            record('production_assets_restored_not_started')
            return
        record('starting_production_services')
        run('start_production.py', timeout=3600)
        record('production_services_ready')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch('[A-Z_]+', str(error)) else type(error).__name__
        record('failed_operator_reconciliation_required', failedStep=STEP, errorCode=code)
        sys.stderr.write('DLPC_RECOVERY_FAILED; inspect private state and prerequisite files\n')
        sys.exit(1)
