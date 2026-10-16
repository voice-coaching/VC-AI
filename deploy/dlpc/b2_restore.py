"""Download the pinned DLPC backup after recreation; never restore secrets.

Credentials are passed on stdin only. Extraction/activation is a separate step
after both pinned SHA-256 digests match. Existing target trees are not replaced.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from b2_backup import B2, encoded
import host_identity


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


def read_range(client, file_id, start, end, size):
    """Read one bounded range; credentials never follow redirects."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    raw = bytearray()
    failures = 0
    while start + len(raw) <= end:
        offset = start + len(raw)
        request = urllib.request.Request(client.download + '/b2api/v4/b2_download_file_by_id?' +
            urllib.parse.urlencode({'fileId': file_id}), headers={
                'Authorization': client.token, 'Accept-Encoding': 'identity',
                'Range': f'bytes={offset}-{end}'})
        deadline = time.monotonic() + 90
        before = len(raw)
        try:
            with opener.open(request, timeout=30) as reply:
                partial_response = (reply.status == 206 and
                    reply.headers.get('Content-Range') == f'bytes {offset}-{end}/{size}')
                # B2 may return 200 if the range covers the entire object.
                full_response = (reply.status == 200 and offset == 0 and end == size - 1
                                 and reply.headers.get('Content-Range') is None)
                if (not (partial_response or full_response)
                        or int(reply.headers.get('Content-Length', -1)) != end - offset + 1):
                    raise ValueError('DOWNLOAD_SIZE_HEADER')
                while start + len(raw) <= end:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('DOWNLOAD_RANGE_DEADLINE')
                    block = reply.read1(min(1024**2, end - start - len(raw) + 1))
                    if not block:
                        raise ConnectionError('SHORT_DOWNLOAD')
                    raw.extend(block)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            failures = failures + 1 if len(raw) == before else 0
            if failures >= 4:
                raise
            time.sleep(min(2 ** failures, 8))
    return raw


def download(client, file_id, path, size, digest, state):
    """Resume only the pinned object; a short response never becomes complete."""
    if path.exists():
        with path.open('rb') as stream:
            if path.is_symlink() or path.stat().st_size != size or hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('DOWNLOAD_HASH_MISMATCH')
        return
    partial = path.with_suffix(path.suffix + '.partial')
    if partial.is_symlink():
        raise ValueError('DOWNLOAD_HASH_MISMATCH')
    total = partial.stat().st_size if partial.exists() else 0
    if total > size:
        raise ValueError('DOWNLOAD_TOO_LARGE')
    state['downloadedBytes'] = total
    save(state)
    # Eight 8 MiB requests bound memory to about 64 MiB. Append only complete
    # ranges in order, so any interrupted file remains a contiguous prefix.
    chunk = 8 * 1024**2
    with ThreadPoolExecutor(max_workers=8) as pool, partial.open('ab') as out:
        while total < size:
            pending = [pool.submit(read_range, client, file_id, start, min(start + chunk, size) - 1, size)
                       for start in range(total, min(total + 8 * chunk, size), chunk)]
            for future in pending:
                raw = future.result()
                out.write(raw)
                total += len(raw)
                out.flush()
                state['downloadedBytes'] = total
                save(state)
        os.fsync(out.fileno())
    with partial.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
            raise ValueError('DOWNLOAD_HASH_MISMATCH')
    partial.rename(path)


def worker(auth):
    state = dict(status='downloading', startedAt=time.time(), objects=[])
    save(state)
    try:
        client = B2(auth)
        for name, file_id, size, digest in OBJECTS:
            state.update(currentObject=name, downloadedBytes=0, expectedBytes=size)
            save(state)
            path = RUN / name
            download(client, file_id, path, size, digest, state)
            state['objects'].append(dict(name=name, fileId=file_id, bytes=size, sha256=digest))
        state.update(status='download_verified', downloadedBytes=size, finishedAt=time.time())
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
    parser.add_argument('--resume', action='store_true', help='Resume an inspected failed download without discarding partial bytes')
    args = parser.parse_args()
    identity = host_identity.require_root()
    os.umask(0o077)
    auth = json.loads(sys.stdin.buffer.read(65537))
    if args.worker:
        with (RUN / 'download.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            worker(auth)
        return
    recovered = sum(p.stat().st_size for p in RUN.glob('*') if p.is_file()) if args.resume else 0
    if shutil.disk_usage(RUN.parent).free + recovered < 46_000_000_000:
        raise ValueError('INSUFFICIENT_DOWNLOAD_AND_RESTORE_SPACE')
    if any(Path(p).exists() for p in ('/opt/intelligentai', '/workspace/intelligentai',
                                     '/workspace/voice-coaching-tts')):
        raise ValueError('RESTORE_REQUIRES_EMPTY_RUNTIME_TARGET')
    if args.resume:
        if ((RUN / 'restore-state.json').exists() or
                json.loads((RUN / 'state.json').read_bytes()).get('status') != 'failed'):
            raise ValueError('FAILED_DOWNLOAD_REQUIRED')
    else:
        RUN.mkdir(mode=0o700, exist_ok=False)
    with (RUN / 'worker.log').open('ab' if args.resume else 'xb') as log:
        process = subprocess.Popen([host_identity.current().tools_python, str(Path(__file__).resolve()), '--worker'],
            stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True,
            env=dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1'))
    process.stdin.write(encoded(auth))
    process.stdin.close()
    auth.clear()
    (RUN / 'launcher.json').write_bytes(encoded(dict(pid=process.pid)))
    print(json.dumps(dict(downloadStarted=True, pid=process.pid, statePath=str(RUN / 'state.json'))))


if __name__ == '__main__':
    main()
