"""Operator/platform entry point for diagnostic DLPC startup, without RunPod.

Asset restoration and role secrets must be provisioned first. This deliberately
does not infer approval from old backup receipts, enable admission or replace a
live service. Install a supported platform hook separately.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time


BIN = Path('/usr/gatoai/python/extra/system-py3.12/bin')
APP = Path('/opt/intelligentai/app')
CONFIG = APP / 'deploy/dlpc/supervisord.conf'
TOOLS = Path(__file__).resolve().parent
TUNNEL = Path('/home/2020067/.local/share/intelligentai/aws-tunnel/start_aws_tunnel.py')


def command(argv):
    subprocess.run(argv, check=True, timeout=120)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    os.umask(0o077)
    # /run is private to this container; this lock does not claim persistence.
    with open('/run/intelligentai-bootstrap.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for path in (CONFIG, TOOLS / 'prepare_restored_runtime.py',
                     Path('/etc/intelligentai/analysis.env'), Path('/etc/intelligentai/tts-token'),
                     Path('/opt/intelligentai/envs/scorer/bin/python'),
                     Path('/workspace/voice-coaching-tts/venv/bin/python')):
            if not path.is_file():
                raise ValueError('ASSET_OR_PRIVATE_SETTINGS_RESTORE_REQUIRED')
        if shutil.disk_usage('/var/lib').free < 14 * 1024**3:
            raise ValueError('DISK_HEADROOM_REQUIRED')
        command(['/usr/bin/python3', str(TOOLS / 'prepare_restored_runtime.py')])
        if args.prepare_only:
            print('Private runtime prepared; services and tunnel unchanged')
            return
        # A deployed config may have been intentionally changed for production.
        # Never start an --admit command from a diagnostic bootstrap.
        if 'launch.py analysis\n' not in CONFIG.read_text() or '--admit' in CONFIG.read_text():
            raise ValueError('DIAGNOSTIC_SUPERVISOR_REQUIRED')
        command(['/usr/sbin/runuser', '-u', '2020067', '--', '/usr/bin/python3', str(TUNNEL)])
        ctl = [str(BIN / 'supervisorctl'), '-c', str(CONFIG)]
        result = subprocess.run(ctl + ['status'], capture_output=True, text=True, timeout=10)
        if not Path('/run/intelligentai/dlpc-supervisor.sock').exists():
            command([str(BIN / 'supervisord'), '-c', str(CONFIG)])
            time.sleep(12)
        elif result.returncode not in (0, 3):
            raise ValueError('SUPERVISOR_STATE_UNKNOWN')
        for name in ('analysis', 'tts'):
            result = subprocess.run(ctl + ['status', name], capture_output=True, text=True, timeout=10)
            if 'RUNNING' not in result.stdout:
                if not any(s in result.stdout for s in ('STOPPED', 'EXITED', 'FATAL')):
                    raise ValueError('SERVICE_TRANSITION_IN_PROGRESS')
                command(ctl + ['start', name])
        print(json.dumps(dict(diagnosticServicesRunning=True, productionAdmission=False,
                              inferenceQualityVerified=False,
                              platformHookFilesPresent=(Path('/home/2020067/.jupyter/jupyter_server_config.py').is_file()
                                  and (TOOLS / 'recover_on_start.py').is_file()))))


if __name__ == '__main__':
    main()
