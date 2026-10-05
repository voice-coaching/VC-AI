"""Stage a separate AWS verifier environment; never restart or reconfigure Backend.

Run as root on the approved AWS host. No credentials are read. Existing targets
are not overwritten; incomplete installation needs inspection before resuming.
"""
import datetime
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

BASE = Path('/opt/alpha-canonical')
TARGET = BASE / 'venvs' / 'py312-jsonschema4251-rfc014-numpy246-native'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def owned_directory(path):
    if not path.exists():
        path.mkdir(mode=0o755)
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and not stat.S_IMODE(info.st_mode) & 0o022, 'VERIFIER_DIRECTORY_PERMISSIONS')


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'ROOT_LINUX_REQUIRED')
    os.umask(0o022)
    # The root-owned /opt parent prevents the service UID from replacing this tree.
    require(BASE.parent.is_dir() and BASE.parent.resolve() == BASE.parent, 'APPLICATION_PARENT_INVALID')
    parent_info = BASE.parent.stat()
    require(parent_info.st_uid == 0 and not stat.S_IMODE(parent_info.st_mode) & 0o022,
            'APPLICATION_PARENT_PERMISSIONS')
    owned_directory(BASE)
    owned_directory(BASE / 'venvs')
    require(not TARGET.exists() and not TARGET.is_symlink(), 'VERIFIER_ENV_ALREADY_EXISTS_INSPECT_FIRST')
    TARGET.mkdir(mode=0o755)
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PIP_CONFIG_FILE': '/dev/null',
                   'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
    subprocess.run(['/usr/bin/python3.12', '-m', 'venv', str(TARGET)], env=environment,
                   check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    python = str(TARGET / 'bin/python')
    completed = subprocess.run([
        python, '-m', 'pip', 'install', '--no-input', '--no-cache-dir', '--only-binary=:all:',
        '--index-url', 'https://pypi.org/simple', '--timeout', '20', '--retries', '1',
        '--report', str(TARGET / 'install-report.json'), 'jsonschema==4.25.1', 'rfc8785==0.1.4', 'numpy==2.4.6',
    ], env=environment, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    require(completed.returncode == 0, 'VERIFIER_DEPENDENCY_INSTALL_FAILED')
    result = subprocess.check_output([python, '-I', '-c',
        "import importlib.metadata as m,json,sys;print(json.dumps({'python':sys.version.split()[0],"
        "'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))"],
        env=environment, timeout=10)
    metadata = json.loads(result)
    require(metadata['python'].startswith('3.12.') and metadata['packages']['jsonschema'] == '4.25.1'
            and metadata['packages']['rfc8785'] == '0.1.4' and metadata['packages']['numpy'] == '2.4.6',
            'VERIFIER_DEPENDENCY_VERSION_MISMATCH')
    metadata.update(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), path=str(TARGET),
                    applicationDeployed=False, runtimeSecretsInjected=False, admissionChanged=False)
    with (TARGET / 'installation.json').open('x', encoding='utf-8') as stream:
        json.dump(metadata, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(metadata))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import re
        code = str(error) if isinstance(error, ValueError) and re.fullmatch('[A-Z0-9_]+', str(error)) else 'VERIFIER_INSTALL_FAILED'
        print(json.dumps({'error': code, 'instruction': 'Inspect the exact staging directory; do not overwrite or delete automatically.'}))
        sys.exit(1)
