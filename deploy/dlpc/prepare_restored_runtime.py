"""Recreate private runtime directories after a verified B2 asset restore.

Requires separately received root-only settings. Does not start services or
enable production admission, and never prints credential values.
"""
import os
import hmac
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
        ('/var/lib/intelligentai', 0, 0o711),
        ('/var/lib/intelligentai/work', 10001, 0o700),
        ('/var/lib/intelligentai/work/tmp', 10001, 0o700),
        ('/var/lib/intelligentai/work/tmp/matplotlib', 10001, 0o700),
        ('/var/lib/intelligentai/work/tmp/huggingface', 10001, 0o700),
        ('/var/lib/intelligentai/work/analysis', 10001, 0o700),
        ('/var/lib/intelligentai/work/canonical', 10001, 0o700),
    ):
        p = Path(name)
        p.mkdir(parents=True, exist_ok=True)
        if p.resolve(strict=True) != p or not p.is_dir():
            raise ValueError('PRIVATE_DIRECTORY_REQUIRED')
        os.chown(p, owner, 10001 if owner else 0)
        p.chmod(mode)
    token = Path('/etc/intelligentai/tts-token').read_bytes().strip()
    if not 32 <= len(token) <= 512 or any(c in token for c in b' \t\r\n'):
        raise ValueError('TTS_TOKEN_SHAPE')
    destination = Path('/run/voice-coaching-tts/token')
    if destination.exists() or destination.is_symlink():
        info = destination.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 10001
                or stat.S_IMODE(info.st_mode) != 0o600
                or not hmac.compare_digest(destination.read_bytes().strip(), token)):
            raise ValueError('EXISTING_TTS_TOKEN_DIFFERS')
    else:
        with destination.open('xb') as stream:
            stream.write(token + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(destination, 10001, 10001)
        destination.chmod(0o600)
    lock = Path('/run/intelligentai/gpu.lock')
    if lock.exists() or lock.is_symlink():
        info = lock.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 10001
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('EXISTING_GPU_LOCK_INVALID')
        # Never replace a live flock inode during an idempotent preparation.
    else:
        with lock.open('xb'):
            pass
        os.chown(lock, 10001, 10001)
        lock.chmod(0o600)
    print('Private runtime directories, GPU lock and TTS token prepared; services not started')


if __name__ == '__main__':
    main()
