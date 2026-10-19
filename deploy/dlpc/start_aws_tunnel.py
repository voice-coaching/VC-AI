"""Start the restricted AWS reverse tunnel using persistent home SSH settings.

This manager is separate from the ephemeral inference installation. Invoke
again after container recreation; platform startup-hook integration is separate.
"""
import argparse
import os
from pathlib import Path
import shlex
import stat
import subprocess
import time
import host_identity


def run_client(command):
    # A disconnected AWS sshd may retain its forwards until its keepalive expires.
    # Keep this supervisor child alive while SSH retries, so a temporary bind
    # conflict cannot exhaust Supervisor's startup retries and strand ingress.
    delay = 5
    while True:
        started = time.monotonic()
        try:
            result = subprocess.run(shlex.split(command), stdin=subprocess.DEVNULL)
            code = result.returncode
        except OSError:
            code = 'SSH_START_FAILED'
        if time.monotonic() - started >= 90:
            delay = 5
        print(f'AWS tunnel client exited ({code}); reconnecting in {delay}s', flush=True)
        time.sleep(delay)
        delay = min(delay * 2, 60)


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-only', action='store_true')
    mode.add_argument('--upgrade-client', action='store_true',
                      help='Replace only the recognized previous SSH supervisor command')
    mode.add_argument('--run-client', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    identity = host_identity.require_operator()
    aws = identity.aws()
    SUPERVISOR = Path(identity.supervisor_bin)
    RUNTIME = Path(f'/tmp/intelligentai-aws-tunnel-{identity.uid}')
    key, known = aws['key'], aws['known_hosts']
    for path in (key, known):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != identity.uid or info.st_mode & 0o077:
            raise ValueError('PRIVATE_SSH_SETTINGS_REQUIRED')
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    info = RUNTIME.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != identity.uid or info.st_mode & 0o077:
        raise ValueError('PRIVATE_RUNTIME_REQUIRED')
    config = RUNTIME / 'supervisord.conf'
    ssh_command = (
        f"/usr/bin/ssh -F /dev/null -NT -p {aws['port']} -o BatchMode=yes -o IdentitiesOnly=yes "
        '-o ConnectTimeout=10 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 '
        f"-o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -o HostKeyAlias={aws['host']} "
        f'-o UserKnownHostsFile={known} -i {key} '
        f"-R 127.0.0.1:{aws['tunnel']['analysis']}:127.0.0.1:8080 "
        f"-R 127.0.0.1:{aws['tunnel']['tts']}:127.0.0.1:8083 "
        f"ec2-user@{aws['host']}"
    )
    if args.run_client:
        run_client(ssh_command)
        return
    command = shlex.join([identity.tools_python, str(Path(__file__).resolve()), '--run-client'])
    content = f'''[unix_http_server]
file={RUNTIME}/supervisor.sock
chmod=0600
[supervisord]
pidfile={RUNTIME}/supervisor.pid
logfile={RUNTIME}/supervisor.log
logfile_maxbytes=1MB
logfile_backups=2
childlogdir={RUNTIME}
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://{RUNTIME}/supervisor.sock
[program:aws-tunnel]
command={command}
autostart=true
autorestart=true
startsecs=15
startretries=3
stopasgroup=true
killasgroup=true
stdout_logfile={RUNTIME}/ssh.log
stdout_logfile_maxbytes=1MB
stdout_logfile_backups=2
redirect_stderr=true
'''
    if config.exists():
        info = config.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != identity.uid or info.st_mode & 0o077:
            raise ValueError('PRIVATE_TUNNEL_CONFIG_REQUIRED')
        previous = config.read_text()
        legacy = content.replace('command=' + command + '\n', 'command=' + ssh_command + '\n', 1)
        if previous != content and (not args.upgrade_client or previous != legacy):
            raise ValueError('EXISTING_TUNNEL_CONFIG_DIFFERS')
        if previous != content:
            pending = RUNTIME / 'supervisord.conf.pending'
            with pending.open('x') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            pending.replace(config)
    else:
        with config.open('x') as stream:
            stream.write(content)
    if args.prepare_only:
        print(f"AWS {aws['port']} tunnel prepared; not started")
        return
    ctl = [str(SUPERVISOR / 'supervisorctl'), '-c', str(config)]
    if (RUNTIME / 'supervisor.sock').exists():
        subprocess.run(ctl + ['reread'], check=True, timeout=15)
        subprocess.run(ctl + ['update', 'aws-tunnel'], check=True, timeout=45)
    else:
        subprocess.run([str(SUPERVISOR / 'supervisord'), '-c', str(config)], check=True, timeout=15)
        print('Supervisor started; confirm remote listeners before claiming connectivity')
        return
    status = subprocess.run(ctl + ['status', 'aws-tunnel'], capture_output=True, text=True, timeout=10)
    if 'RUNNING' in status.stdout or 'STARTING' in status.stdout:
        print(status.stdout.strip())
    elif any(x in status.stdout for x in ('STOPPED', 'FATAL', 'EXITED')):
        subprocess.run(ctl + ['start', 'aws-tunnel'], check=True, timeout=30)
    else:
        raise ValueError('TUNNEL_SUPERVISOR_STATE_UNKNOWN')


if __name__ == '__main__':
    main()
