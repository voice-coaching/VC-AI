"""Start the restricted AWS reverse tunnel using persistent home SSH settings.

This manager is separate from the ephemeral inference installation. Invoke
again after container recreation; platform startup-hook integration is separate.
"""
import argparse
import os
from pathlib import Path
import socket
import stat
import subprocess


SUPERVISOR = Path('/usr/gatoai/python/extra/system-py3.12/bin')
RUNTIME = Path('/tmp/intelligentai-aws-tunnel-120067')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if os.getuid() != 120067 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_USER_REQUIRED')
    home = Path('/home/2020067')
    key = home / '.ssh/intelligentai_aws_20261005'
    known = home / '.ssh/intelligentai_aws_known_hosts'
    for path in (key, known):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 120067 or info.st_mode & 0o077:
            raise ValueError('PRIVATE_SSH_SETTINGS_REQUIRED')
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    info = RUNTIME.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 120067 or info.st_mode & 0o077:
        raise ValueError('PRIVATE_RUNTIME_REQUIRED')
    config = RUNTIME / 'supervisord.conf'
    command = (
        '/usr/bin/ssh -F /dev/null -NT -p 22132 -o BatchMode=yes -o IdentitiesOnly=yes '
        '-o ConnectTimeout=10 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 '
        '-o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -o HostKeyAlias=13.209.184.226 '
        f'-o UserKnownHostsFile={known} -i {key} '
        '-R 127.0.0.1:18080:127.0.0.1:8080 -R 127.0.0.1:18083:127.0.0.1:8083 '
        'ec2-user@13.209.184.226'
    )
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
        if config.is_symlink() or config.read_text() != content:
            raise ValueError('EXISTING_TUNNEL_CONFIG_DIFFERS')
    else:
        with config.open('x') as stream:
            stream.write(content)
    if args.prepare_only:
        print('AWS 22132 tunnel prepared; not started')
        return
    with socket.create_connection(('13.209.184.226', 22132), timeout=8) as connection:
        connection.settimeout(5)
        if not connection.recv(128).startswith(b'SSH-'):
            raise ValueError('AWS_SSH_BANNER_REQUIRED')
    ctl = [str(SUPERVISOR / 'supervisorctl'), '-c', str(config)]
    status = subprocess.run(ctl + ['status'], capture_output=True, text=True)
    if 'RUNNING' in status.stdout or 'STARTING' in status.stdout:
        print(status.stdout.strip())
    elif (RUNTIME / 'supervisor.sock').exists() and any(x in status.stdout for x in ('STOPPED', 'FATAL', 'EXITED')):
        subprocess.run(ctl + ['start', 'aws-tunnel'], check=True)
    else:
        subprocess.run([str(SUPERVISOR / 'supervisord'), '-c', str(config)], check=True)
        print('Supervisor started; confirm remote listeners before claiming connectivity')


if __name__ == '__main__':
    main()
