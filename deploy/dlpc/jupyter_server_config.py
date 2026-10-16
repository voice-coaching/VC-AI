"""Persistent DLPC startup hook; does not block or change Jupyter settings."""
import os as _os
import socket as _socket
import subprocess as _subprocess

# Reviewed per host: (operator uid, recovery interpreter). Unknown hosts do nothing.
_DLPC_HOOK_HOSTS = {
    'J3990.2020067.notebook.dlpc': (120067, '/usr/bin/python3'),
    'J406.2020067.notebook.dlpc': (
        120067, '/home/2020067/.local/share/intelligentai/python/cpython-3.12.3/bin/python3.12'),
}
_dlpc_hook = _DLPC_HOOK_HOSTS.get(_socket.gethostname())
if _dlpc_hook and _os.getuid() == _dlpc_hook[0]:
    _subprocess.Popen(
        ['/usr/bin/sudo', '-n', _dlpc_hook[1],
         '/home/2020067/.local/share/intelligentai/recovery/startup-20261006/recover_on_start.py'],
        stdin=_subprocess.DEVNULL, stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
             'PYTHONDONTWRITEBYTECODE': '1'},
    )
