"""Explicitly approved DLPC runner enrollment. Secrets arrive over stdin only.

Prepare downloads the pinned official package and installs a root-owned fixed
controller. Register consumes a short-lived GitHub registration token, then
starts an independent supervisor. Neither operation restarts analysis or TTS.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import sys
import tarfile
import urllib.request

ACCOUNT = 'intelligentai-actions'
# Runner.Listener needs read permission on every ancestor. Keep its private home
# outside the analysis state parent, whose 0711 permission deliberately hides it.
HOME = Path('/var/lib/intelligentai-actions')
OPS = Path('/opt/intelligentai/cicd')
CONTROLLER = OPS / 'deploy_source.py'
SUPERVISOR = OPS / 'runner-supervisord.conf'
SUDOERS = Path('/etc/sudoers.d/intelligentai-actions')
HOOK = Path('/home/2020067/.jupyter/jupyter_server_config.py')
PACKAGE = 'https://github.com/actions/runner/releases/download/v2.337.0/actions-runner-linux-x64-2.337.0.tar.gz'
PACKAGE_SHA256 = '70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613'
BIN = '/usr/gatoai/python/extra/system-py3.12/bin/'
STARTUP = '''import os
from pathlib import Path
import socket
import subprocess
if os.geteuid()!=0 or socket.gethostname()!='J3990.2020067.notebook.dlpc':
    raise SystemExit('EXPECTED_DLPC_ROOT')
if Path('/var/lib/intelligentai-actions/.runner').is_file():
    config='/opt/intelligentai/cicd/runner-supervisord.conf'
    binary='/usr/gatoai/python/extra/system-py3.12/bin/'
    if not Path('/run/intelligentai-actions-supervisor.sock').exists():
        subprocess.run([binary+'supervisord','-c',config],check=True)
    else:
        result=subprocess.run([binary+'supervisorctl','-c',config,'status'],capture_output=True,text=True)
        if result.returncode==0 and any(x in result.stdout for x in ('STOPPED','EXITED','FATAL')):
            subprocess.run([binary+'supervisorctl','-c',config,'start','actions-runner'],check=True)
'''


def safe_file(path, data, mode=0o600):
    if path.is_symlink():raise ValueError('UNSAFE_INSTALL_PATH')
    if path.exists():
        if path.stat().st_uid != 0 or path.read_bytes() != data:
            raise ValueError('EXISTING_INSTALL_DIFFERS')
    else:
        with path.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    path.chmod(mode)


def private_directory(path):
    if path.is_symlink() or (path.exists() and path.stat().st_uid != 0):
        raise ValueError('ROOT_DIRECTORY_REQUIRED')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def account():
    entry = pwd.getpwnam(ACCOUNT)
    if entry.pw_uid in (0, 10001, 120067) or entry.pw_dir != str(HOME) or entry.pw_shell != '/usr/sbin/nologin':
        raise ValueError('UNEXPECTED_RUNNER_ACCOUNT')
    return entry


def prepare(body):
    if shutil.disk_usage('/var/lib').free < 17*1024**3:
        raise ValueError('RUNNER_DISK_HEADROOM_REQUIRED')
    available = next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    if available < 1024**3:raise ValueError('RUNNER_MEMORY_HEADROOM_REQUIRED')
    code = base64.b64decode(body['controllerBase64'], validate=True)
    if len(code)>128*1024 or hashlib.sha256(code).hexdigest()!=body['controllerSha256']:
        raise ValueError('CONTROLLER_DIGEST_MISMATCH')
    compile(code, str(CONTROLLER), 'exec')
    private_directory(OPS)
    safe_file(CONTROLLER, code)
    marker = OPS / 'runner-prepared.json'
    try:entry=account()
    except KeyError:
        if HOME.exists():raise ValueError('EXISTING_RUNNER_HOME_WITHOUT_ACCOUNT')
        subprocess.run(['/usr/sbin/useradd','--system','--user-group','--no-create-home',
                        '--home-dir',str(HOME),'--shell','/usr/sbin/nologin',ACCOUNT],check=True)
        entry=account()
        HOME.mkdir(mode=0o700,parents=True)
        os.chown(HOME,entry.pw_uid,entry.pw_gid)
    if HOME.is_symlink() or HOME.stat().st_uid!=entry.pw_uid:
        raise ValueError('RUNNER_HOME_OWNER_MISMATCH')
    parent=HOME.parent
    while parent!=Path('/'):
        if parent.is_symlink() or parent.stat().st_uid!=0 or parent.stat().st_mode&0o022:
            raise ValueError('RUNNER_PARENT_NOT_ROOT_PROTECTED')
        parent=parent.parent
    HOME.chmod(0o700)
    if (HOME/'.runner').exists():raise ValueError('RUNNER_ALREADY_REGISTERED')
    if not marker.exists():
        # Never silently reuse a partially extracted installation.
        if any(HOME.iterdir()):raise ValueError('INSPECT_PARTIAL_RUNNER_INSTALL')
        package=OPS/'actions-runner.tar.gz'
        if package.exists():raise ValueError('INSPECT_PARTIAL_RUNNER_DOWNLOAD')
        digest=hashlib.sha256();size=0
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(PACKAGE,timeout=30) as response, package.open('xb') as output:
            while True:
                chunk=response.read(1024**2)
                if not chunk:break
                size+=len(chunk)
                if size>256*1024**2:raise ValueError('RUNNER_PACKAGE_TOO_LARGE')
                digest.update(chunk);output.write(chunk)
        if digest.hexdigest()!=PACKAGE_SHA256:raise ValueError('RUNNER_PACKAGE_DIGEST_MISMATCH')
        with tarfile.open(package,'r:gz') as archive:
            members=archive.getmembers()
            if sum(row.size for row in members)>1536*1024**2:
                raise ValueError('RUNNER_EXTRACTED_SIZE_TOO_LARGE')
            archive.extractall(HOME,members=members,filter='data')
        for parent, dirs, files in os.walk(HOME,followlinks=False):
            for name in dirs+files:os.chown(Path(parent)/name,entry.pw_uid,entry.pw_gid,follow_symlinks=False)
        os.chown(HOME,entry.pw_uid,entry.pw_gid); HOME.chmod(0o700)
        marker.write_text(json.dumps(dict(packageSha256=PACKAGE_SHA256,controllerSha256=body['controllerSha256']))+'\n')
        package.unlink()
    prepared=json.loads(marker.read_bytes())
    if prepared!={'packageSha256':PACKAGE_SHA256,'controllerSha256':body['controllerSha256']}:
        raise ValueError('PREPARED_INSTALL_IDENTITY_DIFFERS')
    result=subprocess.run(['/usr/sbin/runuser','-u',ACCOUNT,'--',str(HOME/'bin/Runner.Listener'),'--version'],
                          cwd=HOME,capture_output=True,timeout=30)
    if result.returncode:raise ValueError('RUNNER_EXECUTION_PREREQUISITES_FAILED')
    sudo = (ACCOUNT+' ALL=(root) NOPASSWD: /usr/bin/python3 -I '+str(CONTROLLER)+'\n').encode()
    candidate=OPS/'sudoers.candidate'
    safe_file(candidate,sudo,0o440)
    result=subprocess.run(['/usr/sbin/visudo','-cf',str(candidate)],capture_output=True)
    if result.returncode:raise ValueError('SUDOERS_VALIDATION_FAILED')
    safe_file(SUDOERS,sudo,0o440)
    config=f'''[unix_http_server]
file=/run/intelligentai-actions-supervisor.sock
chmod=0600
[supervisord]
pidfile=/run/intelligentai-actions-supervisor.pid
logfile={OPS}/supervisor.log
logfile_maxbytes=1MB
logfile_backups=2
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix:///run/intelligentai-actions-supervisor.sock
[program:actions-runner]
command={HOME}/run.sh
directory={HOME}
user={ACCOUNT}
environment=HOME="{HOME}",USER="{ACCOUNT}",LOGNAME="{ACCOUNT}"
autostart=true
autorestart=true
startsecs=0
stopwaitsecs=30
stopasgroup=true
killasgroup=true
umask=0077
stdout_logfile={OPS}/runner.log
stdout_logfile_maxbytes=1MB
stdout_logfile_backups=2
redirect_stderr=true
'''
    safe_file(SUPERVISOR,config.encode())
    safe_file(OPS/'start_runner.py',STARTUP.encode())
    return dict(stage='prepared',account=ACCOUNT,packageSha256=PACKAGE_SHA256,
                controllerSha256=hashlib.sha256(code).hexdigest())


def register(body):
    entry=account()
    if not (OPS/'runner-prepared.json').is_file():raise ValueError('RUNNER_PREPARATION_REQUIRED')
    if (HOME/'.runner').exists():raise ValueError('RUNNER_ALREADY_REGISTERED')
    token=body.pop('registrationToken')
    if not isinstance(token,str) or not token or len(token)>4096:
        raise ValueError('INVALID_REGISTRATION_TOKEN')
    result=subprocess.run(['/usr/sbin/runuser','-u',ACCOUNT,'--',str(HOME/'config.sh'),
        '--unattended','--url','https://github.com/voice-coaching/intelligentAI',
        '--token',token,'--name','intelligentai-dlpc',
        '--labels','intelligentai-dlpc-deploy','--work','_work'],
        cwd=HOME,capture_output=True,timeout=120)
    if result.returncode:raise ValueError('RUNNER_REGISTRATION_FAILED_INSPECT_PRIVATE_DIAGNOSTICS')
    token=None
    for name in ('.runner','.credentials','.credentials_rsaparams'):
        p=HOME/name
        if p.exists():p.chmod(0o600)
    if HOOK.is_symlink() or not HOOK.is_file():raise ValueError('EXISTING_JUPYTER_HOOK_REQUIRED')
    marker='# intelligentai-actions-runner-20261008'
    addition='\n'+marker+'\nimport subprocess as _actions_subprocess\n'
    addition+='_actions_subprocess.Popen(["sudo","-n","/usr/bin/python3","-I","/opt/intelligentai/cicd/start_runner.py"], stdin=_actions_subprocess.DEVNULL, stdout=_actions_subprocess.DEVNULL, stderr=_actions_subprocess.DEVNULL, start_new_session=True)\n'
    if marker not in HOOK.read_text():
        compile(HOOK.read_text()+addition,str(HOOK),'exec')
        with HOOK.open('a') as stream:stream.write(addition)
    result=subprocess.run(['/usr/bin/python3','-I',str(OPS/'start_runner.py')],capture_output=True,timeout=30)
    if result.returncode:raise ValueError('RUNNER_SUPERVISOR_START_FAILED')
    return dict(stage='registered',account=ACCOUNT,uid=entry.pw_uid,
                name='intelligentai-dlpc',label='intelligentai-dlpc-deploy')


def main():
    if os.geteuid()!=0 or socket.gethostname()!='J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT')
    os.umask(0o077)
    raw=sys.stdin.buffer.read(256*1024+1)
    if len(raw)>256*1024:raise ValueError('INSTALL_INPUT_TOO_LARGE')
    body=json.loads(raw)
    if body.get('operation')=='prepare':return prepare(body)
    if body.get('operation')=='register':return register(body)
    raise ValueError('UNKNOWN_INSTALL_OPERATION')


if __name__=='__main__':
    try:print(json.dumps(main()))
    except Exception as error:
        reason=str(error) if str(error).replace('_','').isupper() and all(c.isalpha() or c=='_' for c in str(error)) else 'INSPECT_PRIVATE_RUNNER_INSTALL_STATE'
        print(json.dumps(dict(stage='failed',reason=reason)))
        raise SystemExit(1)
