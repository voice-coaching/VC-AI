"""Explicit per-host DLPC identity. Unknown or mismatched hosts fail closed.

Every value is reviewed per host; nothing is inferred from the environment.
Copied beside the persistent recovery tools so standalone scripts can import it.
"""
import os
from pathlib import Path
import pwd
import socket

HOSTS = {
    'J3990.2020067.notebook.dlpc': dict(
        operator='2020067', uid=120067, gid=1001, home='/home/2020067',
        tools_python='/usr/bin/python3',
        supervisor_bin='/usr/gatoai/python/extra/system-py3.12/bin',
        aws_host='13.209.184.226', aws_port=22132,
        aws_key='.ssh/intelligentai_aws_20261005',
        aws_known_hosts='.ssh/intelligentai_aws_known_hosts',
        tunnel=dict(admin=18022, analysis=18080, tts=18083),
        recovery='dlpc-20261006', runner_name='intelligentai-dlpc'),
    # Migration target 2026-10-16. Own key and loopback ports, separate from J3990;
    # AWS host key verified against the PC's trusted session (ED25519 McMjBmO5…).
    'J406.2020067.notebook.dlpc': dict(
        operator='2020067', uid=120067, gid=1000, home='/home/2020067',
        tools_python='/home/2020067/.local/share/intelligentai/python/cpython-3.12.3/bin/python3.12',
        supervisor_bin='/usr/gatoai/python/extra/system-py3.10/bin',
        aws_host='13.209.184.226', aws_port=22132,
        aws_key='.ssh/intelligentai_aws_j406_20261016',
        aws_known_hosts='.ssh/intelligentai_aws_known_hosts',
        tunnel=dict(admin=28022, analysis=28080, tts=28083),
        # Own B2 prefix/reader (AWS exporter operation 'b2-j406'); J3990 cu13 assets do not run here.
        recovery='dlpc-j406-20261016', runner_name='intelligentai-dlpc-j406'),
}


class Identity:
    def __init__(self, hostname, row):
        self.hostname = hostname
        self.__dict__.update(row)
        self.home = Path(row['home'])

    def aws(self):
        if not (self.aws_port and self.aws_key and self.aws_known_hosts and self.tunnel):
            raise ValueError('AWS_TRANSPORT_NOT_CONFIGURED_FOR_HOST')
        return dict(host=self.aws_host, port=self.aws_port, key=self.home / self.aws_key,
                    known_hosts=self.home / self.aws_known_hosts, tunnel=dict(self.tunnel))

    def ssh_base(self):
        """Fixed AWS ssh argv prefix (host key pinned, no agent, no config files)."""
        aws = self.aws()
        return ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-p', str(aws['port']),
                '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10',
                '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=' + aws['host'],
                '-o', 'UserKnownHostsFile=' + str(aws['known_hosts']),
                '-i', str(aws['key'])]


def current():
    hostname = socket.gethostname()
    row = HOSTS.get(hostname)
    if row is None:
        raise ValueError('UNKNOWN_DLPC_HOST')
    try:
        account = pwd.getpwnam(row['operator'])
    except KeyError:
        raise ValueError('DLPC_OPERATOR_ACCOUNT_MISSING') from None
    if (account.pw_uid, account.pw_gid, account.pw_dir) != (row['uid'], row['gid'], row['home']):
        raise ValueError('DLPC_OPERATOR_IDENTITY_MISMATCH')
    return Identity(hostname, row)


def require_root():
    if os.geteuid() != 0:
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    return current()


def require_operator():
    identity = current()
    if os.getuid() != identity.uid:
        raise ValueError('EXPECTED_DLPC_USER_REQUIRED')
    return identity
