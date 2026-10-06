"""Enable only the fixed recovery exporter on the dedicated DLPC listener.

Requires authorization to extend the previously forward-only key with recovery
settings access. Does not change the normal SSH daemon or its authorized keys.
"""
import os
from pathlib import Path
import signal
import socket
import subprocess

CONFIG = Path('/etc/ssh/sshd_config_intelligentai_dlpc')
KEYS = Path('/etc/ssh/authorized_keys_intelligentai_dlpc')
TARGET = Path('/opt/alpha-deploy/dlpc-20261006/aws_recovery_export.py')
COMMAND = '/usr/bin/sudo -n /usr/bin/python3 -I ' + str(TARGET)


def main():
    if os.geteuid() != 0 or not socket.gethostname().startswith('ip-10-20-9-222'):
        raise ValueError('EXPECTED_AWS_ROOT_REQUIRED')
    raw = (Path(__file__).resolve().parent / TARGET.name).read_bytes()
    compile(raw, TARGET.name, 'exec')
    config, keys = CONFIG.read_text(), KEYS.read_text()
    if ('MaxSessions 0\n' not in config or 'ForceCommand /bin/false\n' not in config
            or keys.count('command="/bin/false"') != 1
            or 'AllowUsers ec2-user@203.246.112.21\n' not in config
            or 'PermitOpen none\n' not in config):
        raise ValueError('EXPECTED_FORWARD_ONLY_CONFIGURATION_REQUIRED')
    changed = config.replace('MaxSessions 0\n', 'MaxSessions 1\n').replace(
        'ForceCommand /bin/false\n', 'ForceCommand ' + COMMAND + '\n')
    changed_keys = keys.replace('command="/bin/false"', 'command="' + COMMAND + '"')
    backup = Path('/root/.config/intelligentai/dlpc-recovery-listener-backup-20261006')
    os.umask(0o077)
    backup.mkdir(mode=0o700, exist_ok=False)
    (backup / 'sshd_config').write_text(config)
    (backup / 'authorized_keys').write_text(keys)
    TARGET.write_bytes(raw)
    TARGET.chmod(0o600)
    os.chown(TARGET, 0, 0)
    try:
        CONFIG.write_text(changed)
        KEYS.write_text(changed_keys)
        CONFIG.chmod(0o600)
        KEYS.chmod(0o644)
        subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(CONFIG)], check=True)
        pid = int(subprocess.check_output(['systemctl', 'show', '-p', 'MainPID', '--value',
                                           'intelligentai-dlpc-sshd.service']))
        if pid <= 1:
            raise ValueError('LISTENER_NOT_RUNNING')
        os.kill(pid, signal.SIGHUP)
    except Exception:
        CONFIG.write_text(config)
        KEYS.write_text(keys)
        raise
    print('Dedicated listener reloaded: fixed recovery export plus existing two reverse forwards')


if __name__ == '__main__':
    main()
