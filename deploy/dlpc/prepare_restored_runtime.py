"""Recreate private runtime directories after a verified B2 asset restore.

Requires separately received root-only settings. Does not start services or
enable production admission, and never prints credential values.
"""
import os
from pathlib import Path
import pwd
import socket
import stat


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    if pwd.getpwuid(10001).pw_name != 'intelligentai':
        raise ValueError('SERVICE_IDENTITY_REQUIRED')
    for p in (Path('/etc/intelligentai/analysis.env'), Path('/etc/intelligentai/tts-token')):
        info = p.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('PRIVATE_CONFIG_REQUIRED')
    os.umask(0o077)
    for name, owner, mode in (
        ('/run/intelligentai', 0, 0o711), ('/run/intelligentai/logs', 0, 0o700),
        ('/run/intelligentai/worker', 10001, 0o700),
        ('/run/intelligentai/worker/tmp', 10001, 0o700),
        ('/run/intelligentai/worker/tmp/matplotlib', 10001, 0o700),
        ('/run/intelligentai/worker/tmp/huggingface', 10001, 0o700),
        ('/run/intelligentai/worker/work', 10001, 0o700),
        ('/run/intelligentai/canonical', 10001, 0o700),
        ('/run/voice-coaching-tts', 10001, 0o700),
    ):
        p = Path(name)
        p.mkdir(parents=True, exist_ok=True)
        if p.is_symlink() or not p.is_dir():
            raise ValueError('PRIVATE_DIRECTORY_REQUIRED')
        os.chown(p, owner, 10001 if owner else 0)
        p.chmod(mode)
    token = Path('/etc/intelligentai/tts-token').read_bytes().strip()
    if not 32 <= len(token) <= 512 or any(c in token for c in b' \t\r\n'):
        raise ValueError('TTS_TOKEN_SHAPE')
    destination = Path('/run/voice-coaching-tts/token')
    with destination.open('xb') as stream:
        stream.write(token + b'\n')
    os.chown(destination, 10001, 10001)
    destination.chmod(0o600)
    lock = Path('/run/intelligentai/gpu.lock')
    with lock.open('xb'):
        pass
    os.chown(lock, 10001, 10001)
    lock.chmod(0o600)
    print('Private runtime directories, GPU lock and TTS token prepared; services not started')


if __name__ == '__main__':
    main()
