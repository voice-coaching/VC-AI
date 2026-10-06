"""Install the reviewed persistent Jupyter hook; never restart Jupyter.

Install only after the fixed AWS recovery exporter and its B2 reader exist.
Existing Jupyter configuration is an operator reconciliation case, not overwritten.
"""
import os
from pathlib import Path
import socket

HOME = Path('/home/2020067')
SOURCE = Path(__file__).resolve().parent
TARGET = HOME / '.local/share/intelligentai/recovery/startup-20261006'
FILES = ('recover_on_start.py', 'bootstrap.py', 'prepare_restored_runtime.py',
         'receive_settings.py', 'restore_assets.py', 'restore_downloaded.py',
         'b2_restore.py', 'b2_backup.py', 'supervisord.conf',
         'install_runtime_overlay.py', 'restore_runtime_overlay.py', 'restore_cuda_libraries.py')


def main():
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    config = HOME / '.jupyter/jupyter_server_config.py'
    if config.exists() or config.is_symlink() or TARGET.exists() or TARGET.is_symlink():
        raise ValueError('EXISTING_HOOK_REQUIRES_RECONCILIATION')
    contents = {name: (SOURCE / name).read_bytes() for name in FILES}
    hook = (SOURCE / 'jupyter_server_config.py').read_bytes()
    for name, raw in dict(contents, **{'jupyter_server_config.py': hook}).items():
        if name.endswith('.py'):
            compile(raw, name, 'exec')
    os.umask(0o077)
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
