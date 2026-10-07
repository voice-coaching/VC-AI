"""Enable approved loopback-only DLPC admin forwarding; retain API sessions."""
import os
from pathlib import Path
import socket
import subprocess

CONFIG = Path('/etc/ssh/sshd_config_intelligentai_dlpc')
KEYS = Path('/etc/ssh/authorized_keys_intelligentai_dlpc')
KEY = Path('/root/.ssh/intelligentai_dlpc_admin_20261007')


def main():
    if os.geteuid() != 0 or not socket.gethostname().startswith('ip-10-20-9-222'):
        raise ValueError('EXPECTED_AWS_ROOT')
    os.umask(0o077)
    config, keys = CONFIG.read_text(), KEYS.read_text()
    old = 'PermitListen 127.0.0.1:18080 127.0.0.1:18083\n'
    new = old.rstrip() + ' 127.0.0.1:18022\n'
    option = 'permitlisten="127.0.0.1:18083"'
    extra = option + ',permitlisten="127.0.0.1:18022"'
    if old not in config and new not in config:
        raise ValueError('UNEXPECTED_LISTENER_CONFIG')
    if keys.count(option) != 1:
        raise ValueError('UNEXPECTED_TUNNEL_KEY')
    if not KEY.exists():
        KEY.parent.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C',
                        'intelligentai-aws-admin-20261007', '-f', str(KEY)], check=True)
    KEY.chmod(0o600)
    for path, before, after in ((CONFIG, config, config.replace(old, new)),
            (KEYS, keys, keys if extra in keys else keys.replace(option, extra))):
        backup = path.with_name(path.name + '.before-admin-20261007')
        if not backup.exists():
            backup.write_text(before); backup.chmod(0o600)
        temp = path.with_name(path.name + '.admin-pending')
        with temp.open('x') as stream:
            stream.write(after); stream.flush(); os.fsync(stream.fileno())
        # sshd reads AuthorizedKeysFile as the login user. This file contains
        # public keys only; root ownership prevents ec2-user from changing it.
        temp.chmod(0o644 if path == KEYS else 0o600)
        os.chown(temp, 0, 0)
        temp.replace(path)
    result = subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(CONFIG)], capture_output=True)
    if result.returncode:
        CONFIG.write_text(config); KEYS.write_text(keys)
        CONFIG.chmod(0o600); KEYS.chmod(0o644)
        raise ValueError('LISTENER_VALIDATION_FAILED')
    # Only the listener reloads; established reverse API/TTS sessions survive.
    subprocess.run(['systemctl', 'kill', '--kill-who=main', '--signal=HUP',
                    'intelligentai-dlpc-sshd.service'], check=True)
    print('AWS loopback port 18022 allowed; API/TTS sessions preserved')
    print(KEY.with_suffix('.pub').read_text().strip())


if __name__ == '__main__':main()
