"""Add a dedicated SSH listener for the already authorized DLPC reverse tunnel.

Preserves the existing sshd and nginx. No new key or general shell access.
The AWS security group still requires the operator's explicit ingress rule.
"""
import os
from pathlib import Path
import socket
import subprocess


CONFIG = Path('/etc/ssh/sshd_config_intelligentai_dlpc')
KEYS = Path('/etc/ssh/authorized_keys_intelligentai_dlpc')
UNIT = Path('/etc/systemd/system/intelligentai-dlpc-sshd.service')
PUBLIC = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKc5rmO7+Tai7SoO/egAKeY8VEADsu3bK34wT49bCqdW'


def main():
    if os.geteuid() != 0:
        raise ValueError('ROOT_REQUIRED')
    # Operate only on the inspected EC2 host, not any Linux machine.
    if socket.gethostname() not in ('ip-10-20-9-222.ap-northeast-2.compute.internal', 'ip-10-20-9-222'):
        raise ValueError('EXPECTED_EC2_HOST_REQUIRED')
    existing = Path('/home/ec2-user/.ssh/authorized_keys').read_text().splitlines()
    matches = [line for line in existing if PUBLIC in line]
    if len(matches) != 1 or not all(part in matches[0] for part in (
            'restrict,port-forwarding,', 'permitopen="none"',
            'permitlisten="127.0.0.1:18080"', 'permitlisten="127.0.0.1:18083"',
            'command="/bin/false"')):
        raise ValueError('EXISTING_RESTRICTED_KEY_REQUIRED')
    if any(p.exists() or p.is_symlink() for p in (CONFIG, KEYS, UNIT)):
        raise ValueError('CANDIDATE_ALREADY_EXISTS_INSPECT_BEFORE_RETRY')
    with socket.socket() as probe:
        probe.bind(('0.0.0.0', 22132))
    config = '''Port 22132
AddressFamily inet
ListenAddress 0.0.0.0
HostKey /etc/ssh/ssh_host_ed25519_key
PidFile /run/intelligentai-dlpc-sshd.pid
AuthorizedKeysFile /etc/ssh/authorized_keys_intelligentai_dlpc
AuthorizedKeysCommand none
AllowUsers ec2-user@203.246.112.21
PermitRootLogin no
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM yes
AllowTcpForwarding remote
PermitOpen none
PermitListen 127.0.0.1:18080 127.0.0.1:18083
GatewayPorts no
AllowStreamLocalForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
PermitTTY no
PermitUserRC no
MaxSessions 0
ForceCommand /bin/false
LoginGraceTime 20
MaxAuthTries 3
MaxStartups 3:30:10
ClientAliveInterval 30
ClientAliveCountMax 3
LogLevel VERBOSE
'''
    unit = '''[Unit]
Description=Restricted DLPC reverse SSH listener
After=network.target

[Service]
Type=simple
ExecStartPre=/usr/sbin/sshd -t -f /etc/ssh/sshd_config_intelligentai_dlpc
ExecStart=/usr/sbin/sshd -D -e -f /etc/ssh/sshd_config_intelligentai_dlpc
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'''
    os.umask(0o077)
    # authorized_keys permitopen accepts host:port, not sshd_config's "none".
    # The dedicated daemon enforces PermitOpen none / remote-only forwarding.
    restricted_key = matches[0].replace(',permitopen="none"', '')
    for path, body, mode in ((KEYS, restricted_key + '\n', 0o644),
                             (CONFIG, config, 0o600), (UNIT, unit, 0o644)):
        with path.open('x') as stream:
            stream.write(body)
        path.chmod(mode)
    subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(CONFIG)], check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', UNIT.name], check=True)
    subprocess.run(['systemctl', 'is-active', UNIT.name], check=True)
    print('Dedicated listener ready; existing sshd/nginx unchanged; ingress remains separate.')


if __name__ == '__main__':
    main()
