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
import host_identity

TOOLS = Path(__file__).resolve().parent
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
    identity = host_identity.current()
    argv = (['/usr/sbin/runuser', '-u', identity.operator, '--'] + identity.ssh_base()
            + ['ec2-user@' + identity.aws()['host'], 'dlpc-recovery'])
    result = subprocess.run(argv, input=json.dumps({'operation': operation}).encode(),
                            capture_output=True, timeout=60)
    if result.returncode or not 1 <= len(result.stdout) <= 131072:
        raise ValueError('AWS_EXPORT_UNAVAILABLE')
    json.loads(result.stdout)
    return result.stdout


def released(metadata):
    # Pinned baseline or the Actions-deployed release with its root-only receipt.
    import deployed_release
    try:
        deployed_release.accepted(metadata)
        return True
    except ValueError:
        return False


def run(name, data=None, timeout=180):
    # Root-only diagnostics retain the failing phase without logging stdin.
    with open('/run/intelligentai-recovery.log', 'ab') as log:
        log.write(('\nSTEP ' + name + '\n').encode())
        log.flush()
        result = subprocess.run([host_identity.current().tools_python, str(TOOLS / name)], input=data,
                                stdout=log, stderr=log, timeout=timeout)
    if result.returncode:
        raise ValueError('RECOVERY_STEP_FAILED_' + name.removesuffix('.py').upper())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore-only', action='store_true',
                        help='Restore and verify assets/settings without starting services')
    args = parser.parse_args()
    identity = host_identity.require_root()
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
        from restore_runtime_overlay import SHA256
        if identity.recovery == 'dlpc-j406-20261016':
            # One verified J406 archive holds the trees, current overlay and cuDNN.
            from restore_assets_j406 import BUNDLE_SHA256, STAGE
            if BUNDLE_SHA256 != SHA256:
                raise ValueError('J406_RECOVERY_BUNDLE_PIN_MISMATCH')
            j406_state = STAGE / 'state.json'
            if all(assets) and j406_state.exists():
                if json.loads(j406_state.read_bytes()).get('status') != 'assets_restored_not_started':
                    raise ValueError('INCOMPLETE_RESTORE_REQUIRES_OPERATOR')
            if not any(assets):
                record('restoring_assets')
                run('restore_assets_j406.py', export('b2-j406'), timeout=10800)
            record('checking_cuda_libraries')
            from restore_cuda_libraries import verify
            verify()
        else:
            if not any(assets):
                record('restoring_assets')
                run('restore_assets.py', export('b2'), timeout=10800)
            metadata = json.loads(Path('/opt/intelligentai/deployment.json').read_bytes())
            if not released(metadata):
                if (not restore_state.exists() or
                        Path('/run/intelligentai/dlpc-supervisor.sock').exists()):
                    raise ValueError('LIVE_OVERLAY_RECONCILIATION_REQUIRED')
                record('restoring_gop_overlay')
                run('restore_runtime_overlay.py', export('b2'), timeout=180)
            record('checking_cuda_libraries')
            run('restore_cuda_libraries.py', export('b2'), timeout=900)
        metadata = json.loads(Path('/opt/intelligentai/deployment.json').read_bytes())
        if not released(metadata):
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
        if identity.recovery == 'dlpc-j406-20261016':
            # The B2 archive predates this patch; reapply it on every recovery.
            record('checking_host_compat')
            run('restore_host_compat.py')
        record('restoring_tts_runtime')
        run('restore_tts_runtime.py')
        if args.restore_only:
            record('production_assets_restored_not_started')
            return
        # Production ownership is explicit per host. Until the cutover writes this
        # host's name here, recovery only starts closed diagnostic services, so a
        # restart cannot admit in parallel with the server that owns the public route.
        owner = identity.home / '.local/share/intelligentai/recovery/production-owner'
        if identity.recovery == 'dlpc-j406-20261016' and (
                owner.is_symlink() or not owner.is_file() or owner.read_text() != identity.hostname + '\n'):
            record('starting_diagnostic_services_not_production_owner')
            run('bootstrap.py')
            record('diagnostic_services_started_not_production_owner')
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
