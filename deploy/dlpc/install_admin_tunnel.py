"""Approved DLPC platform-terminal bootstrap; no public SSH listener added."""
import argparse
import os
from pathlib import Path
import pwd
import re
import socket
import subprocess

HOME = Path('/home/2020067')
TOOLS = HOME / '.local/share/intelligentai/admin-tunnel-20261007'
START = r'''import os
from pathlib import Path
import socket
import subprocess

if os.getuid() != 120067 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
    raise ValueError('EXPECTED_DLPC_USER')
runtime = Path('/tmp/intelligentai-admin-tunnel-120067')
os.umask(0o077)
runtime.mkdir(mode=0o700, exist_ok=True)
if runtime.is_symlink() or runtime.stat().st_uid != 120067:
    raise ValueError('PRIVATE_RUNTIME_REQUIRED')
command = ('/usr/bin/ssh -F /dev/null -NT -p 22132 -o BatchMode=yes '
    '-o IdentitiesOnly=yes -o ConnectTimeout=10 -o ExitOnForwardFailure=yes '
    '-o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes '
    '-o HostKeyAlias=13.209.184.226 '
    '-o UserKnownHostsFile=/home/2020067/.ssh/intelligentai_aws_known_hosts '
    '-i /home/2020067/.ssh/intelligentai_aws_20261005 '
    '-R 127.0.0.1:18022:127.0.0.1:22 ec2-user@13.209.184.226')
config = runtime / 'supervisord.conf'
text = f"""[unix_http_server]
file={runtime}/supervisor.sock
chmod=0600
[supervisord]
pidfile={runtime}/supervisor.pid
logfile={runtime}/supervisor.log
logfile_maxbytes=1MB
logfile_backups=2
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://{runtime}/supervisor.sock
[program:admin-tunnel]
command={command}
autostart=true
autorestart=true
startsecs=5
startretries=10
stopasgroup=true
killasgroup=true
stdout_logfile={runtime}/ssh.log
stdout_logfile_maxbytes=1MB
stdout_logfile_backups=2
redirect_stderr=true
"""
if config.exists() and (config.is_symlink() or config.read_text() != text):
    raise ValueError('EXISTING_ADMIN_CONFIG_DIFFERS')
config.write_text(text)
bin = '/usr/gatoai/python/extra/system-py3.12/bin/'
ctl = [bin+'supervisorctl','-c',str(config)]
if not (runtime/'supervisor.sock').exists():
    subprocess.run([bin+'supervisord','-c',str(config)],check=True)
else:
    result = subprocess.run(ctl+['status'],capture_output=True,text=True)
    if any(x in result.stdout for x in ('STOPPED','EXITED','FATAL')):
        subprocess.run(ctl+['start','admin-tunnel'],check=True)
print('Administration tunnel supervisor started; confirm AWS port 18022')
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--public-key', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT')
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+ intelligentai-aws-admin-20261007', args.public_key):
        raise ValueError('EXPECTED_ADMIN_PUBLIC_KEY')
    account = pwd.getpwnam('2020067')
    os.umask(0o077)
    ssh = HOME / '.ssh'; authorized = ssh / 'authorized_keys'
    hook = HOME / '.jupyter/jupyter_server_config.py'
    if ssh.is_symlink() or authorized.is_symlink() or hook.is_symlink() or not hook.is_file():
        raise ValueError('EXISTING_SAFE_SSH_AND_HOOK_REQUIRED')
    ssh.mkdir(mode=0o700, exist_ok=True)
    before = authorized.read_text() if authorized.exists() else ''
    line = 'from="127.0.0.1",restrict ' + args.public_key
    if args.public_key not in before:
        with authorized.open('a') as stream:
            stream.write(('\n' if before and not before.endswith('\n') else '') + line + '\n')
    elif line not in before.splitlines():raise ValueError('EXISTING_ADMIN_KEY_OPTIONS_DIFFER')
    authorized.chmod(0o600); os.chown(authorized, account.pw_uid, account.pw_gid)
    TOOLS.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = TOOLS / 'start.py'
    if path.is_symlink() or (path.exists() and path.read_text() != START):
        raise ValueError('EXISTING_ADMIN_START_DIFFERS')
    compile(START, str(path), 'exec')
    path.write_text(START); path.chmod(0o600)
    for p in (TOOLS, path):os.chown(p, account.pw_uid, account.pw_gid)
    marker = '# intelligentai-admin-tunnel-20261007'
    if marker not in hook.read_text():
        addition = '\n'+marker+'\nimport subprocess as _admin_subprocess\n'
        addition += '_admin_subprocess.Popen(["/usr/bin/python3", "'+str(path)+'"], stdin=_admin_subprocess.DEVNULL, stdout=_admin_subprocess.DEVNULL, stderr=_admin_subprocess.DEVNULL, start_new_session=True)\n'
        compile(hook.read_text()+addition, str(hook), 'exec')
        with hook.open('a') as stream:stream.write(addition)
    subprocess.run(['/usr/sbin/runuser','-u','2020067','--','/usr/bin/python3',str(path)],check=True)
    print('DLPC_SSH_HOST_KEY', Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().strip())
    print('Bootstrap installed; analysis/TTS unchanged')


if __name__ == '__main__':main()
