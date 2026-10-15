"""Install the reviewed persistent Jupyter hook; never restart Jupyter.

Install only after the fixed AWS recovery exporter and its B2 reader exist.
--update preserves the installed Jupyter config and backs up existing helpers.
"""
import argparse
import fcntl
import os
from pathlib import Path
import shutil
import socket
import time

HOME = Path('/home/2020067')
SOURCE = Path(__file__).resolve().parent
TARGET = HOME / '.local/share/intelligentai/recovery/startup-20261006'
FILES = ('recover_on_start.py', 'bootstrap.py', 'prepare_restored_runtime.py',
         'receive_settings.py', 'restore_assets.py', 'restore_downloaded.py',
         'b2_restore.py', 'b2_backup.py', 'supervisord.conf',
         'install_runtime_overlay.py', 'restore_runtime_overlay.py', 'restore_cuda_libraries.py')
FILES += ('restore_direct_resident.py', 'restore_resident_health.py',
          'restore_tts_runtime.py', 'start_production.py')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--update', action='store_true', help='Update an existing matching hook, preserving other Jupyter settings')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    config = HOME / '.jupyter/jupyter_server_config.py'
    if not args.update and (config.exists() or config.is_symlink() or TARGET.exists() or TARGET.is_symlink()):
        raise ValueError('EXISTING_HOOK_REQUIRES_RECONCILIATION')
    contents = {name: (SOURCE / name).read_bytes() for name in FILES}
    hook = (SOURCE / 'jupyter_server_config.py').read_bytes()
    for name, raw in dict(contents, **{'jupyter_server_config.py': hook}).items():
        if name.endswith('.py'):
            compile(raw, name, 'exec')
    os.umask(0o077)
    if args.update:
        if (config.is_symlink() or TARGET.is_symlink() or not TARGET.is_dir()
                or TARGET.resolve() != TARGET
                or str(TARGET / 'recover_on_start.py') not in config.read_text()):
            raise ValueError('EXISTING_HOOK_IDENTITY_CHANGED')
        with open('/run/intelligentai-recovery.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            backup = TARGET.parent / ('before-production-' + str(time.time_ns()))
            backup.mkdir(mode=0o700)
            shutil.copyfile(config, backup / 'jupyter_server_config.py')
            for name in FILES:
                target = TARGET / name
                if target.is_symlink():
                    raise ValueError('EXISTING_HELPER_SYMLINK')
                if target.exists():
                    shutil.copyfile(target, backup / name)
            for name, raw in contents.items():
                pending = TARGET / (name + '.install-pending')
                with pending.open('xb') as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                os.chown(pending, 120067, 1001)
                pending.chmod(0o600)
                pending.replace(TARGET / name)
        print('Persistent recovery helpers updated; Jupyter configuration preserved; services not started')
        return
    TARGET.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chown(TARGET, 120067, 1001)
    for name, raw in contents.items():
        with (TARGET / name).open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(TARGET / name, 120067, 1001)
    if not config.parent.exists():
        config.parent.mkdir(mode=0o700)
        os.chown(config.parent, 120067, 1001)
    if config.parent.is_symlink():
        raise ValueError('CONFIG_DIRECTORY_SYMLINK')
    with config.open('xb') as stream:
        stream.write(hook)
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(config, 120067, 1001)
    print('Persistent Jupyter recovery hook installed; Jupyter not restarted; cold recovery unverified')


if __name__ == '__main__':
    main()
