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
import host_identity


def main():
    identity = host_identity.require_root()
    if pwd.getpwuid(10001).pw_name != 'intelligentai':
        raise ValueError('SERVICE_IDENTITY_REQUIRED')
    for p in (Path('/etc/intelligentai/analysis.env'), Path('/etc/intelligentai/tts-token')):
        info = p.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('PRIVATE_CONFIG_REQUIRED')
    os.umask(0o077)
    settings = dict(line.split('=', 1) for line in
                    Path('/etc/intelligentai/analysis.env').read_text().splitlines()
                    if line and not line.startswith('#'))
    # These runtime directories are intentionally absent from the asset backup.
    # Provision only the known deployment paths, never chmod an arbitrary path
    # from settings or relocate an existing job journal.
    for key, expected in (
        ('AI_DIRECT_SPOOL_ROOT', '/workspace/intelligentai/direct-spool'),
        ('AI_DIRECT_WORKSPACE_ROOT', '/var/lib/intelligentai/work/direct'),
    ):
        if settings.get(key) != expected:
            raise ValueError('DIRECT_RUNTIME_PATH_CHANGED')
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
        ('/var/lib/intelligentai/work/direct', 10001, 0o700),
        ('/workspace/intelligentai/direct-spool', 10001, 0o700),
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
    for name in ('gpu.lock', 'analysis.lock'):
        lock = Path('/run/intelligentai') / name
        if lock.exists() or lock.is_symlink():
            info = lock.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 10001
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('EXISTING_RESOURCE_LOCK_INVALID')
            # Never replace a live flock inode during idempotent preparation.
        else:
            with lock.open('xb'):
                pass
            os.chown(lock, 10001, 10001)
            lock.chmod(0o600)
    print('Private runtime directories, resource locks and TTS token prepared; services not started')


if __name__ == '__main__':
    main()
