"""Download the pinned DLPC backup after recreation; never restore secrets.

Credentials are passed on stdin only. Extraction/activation is a separate step
after both pinned SHA-256 digests match. Existing target trees are not replaced.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from b2_backup import B2, encoded


RUN = Path('/opt/intelligentai-b2-restore-20261006')
OBJECTS = (
    ('source-manifest.json',
     '4_ze5cc71977ef1880ca60b0c17_f116916fb8a29905c_d20261005_m162935_c005_v0501045_t0059_u01791217775463',
     42449956, 'f806086d4196c1df6ff2f63e0ea3b8565197a0a390139aca0beacc8a20920bda'),
    ('runtime-assets.tar.gz',
     '4_ze5cc71977ef1880ca60b0c17_f20016754e80dd30c_d20261005_m154312_c005_v0501036_t0023_u01791214992290',
     16062581191, 'b30e4d258ce42f4d40188d4a295784748fcba94c31dad78e5cc51f24334c7eed'),
)


def save(state):
    temporary = RUN / 'state.tmp'
    temporary.write_bytes(encoded(state))
    temporary.replace(RUN / 'state.json')


def worker(auth):
    state = dict(status='downloading', startedAt=time.time(), objects=[])
    save(state)
    try:
        client = B2(auth)
        for name, file_id, size, digest in OBJECTS:
            state.update(currentObject=name, downloadedBytes=0, expectedBytes=size)
            save(state)
            h = hashlib.sha256()
            total = 0
            last = 0
            path = RUN / name
            with client.read(file_id) as reply, path.with_suffix(path.suffix + '.partial').open('xb') as out:
                if int(reply.headers['Content-Length']) != size:
                    raise ValueError('DOWNLOAD_SIZE_HEADER')
                while raw := reply.read(1024 ** 2):
                    total += len(raw)
                    if total > size:
                        raise ValueError('DOWNLOAD_TOO_LARGE')
                    h.update(raw)
                    out.write(raw)
                    if total - last >= 64 * 1024 ** 2:
                        state['downloadedBytes'] = total
                        save(state)
                        last = total
                out.flush()
                os.fsync(out.fileno())
            if total != size or h.hexdigest() != digest:
                raise ValueError('DOWNLOAD_HASH_MISMATCH')
            path.with_suffix(path.suffix + '.partial').rename(path)
            state['objects'].append(dict(name=name, fileId=file_id, bytes=total, sha256=digest))
        state.update(status='download_verified', downloadedBytes=total, finishedAt=time.time())
        save(state)
    except Exception as error:
        state.update(status='failed', errorType=type(error).__name__)
        # Do not serialize provider errors or request headers.
        if isinstance(error, ValueError) and str(error) in (
                'DOWNLOAD_SIZE_HEADER', 'DOWNLOAD_TOO_LARGE', 'DOWNLOAD_HASH_MISMATCH'):
            state['errorCode'] = str(error)
        save(state)
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    os.umask(0o077)
    auth = json.loads(sys.stdin.buffer.read(65537))
    if args.worker:
        worker(auth)
        return
    if shutil.disk_usage(RUN.parent).free < 46_000_000_000:
        raise ValueError('INSUFFICIENT_DOWNLOAD_AND_RESTORE_SPACE')
    if any(Path(p).exists() for p in ('/opt/intelligentai', '/workspace/intelligentai',
                                     '/workspace/voice-coaching-tts')):
        raise ValueError('RESTORE_REQUIRES_EMPTY_RUNTIME_TARGET')
    RUN.mkdir(mode=0o700, exist_ok=False)
    with (RUN / 'worker.log').open('xb') as log:
        process = subprocess.Popen(['/usr/bin/python3', str(Path(__file__).resolve()), '--worker'],
            stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True,
            env=dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1'))
    process.stdin.write(encoded(auth))
    process.stdin.close()
    auth.clear()
    (RUN / 'launcher.json').write_bytes(encoded(dict(pid=process.pid)))
    print(json.dumps(dict(downloadStarted=True, pid=process.pid, statePath=str(RUN / 'state.json'))))


if __name__ == '__main__':
    main()
