"""J406 recovery backup: stream runtime trees as tar.zst to a prefix-scoped B2 key.

The writer key (list/read/write, no delete, one prefix) is read from a root-only
file. No /etc, /run, /var/lib work, home, SSH key or credential enters the archive.
Completion requires a full download SHA-256 match and a tar listing that matches
the creation index, not just an upload receipt. Run detached as root:
    python3 b2_backup_j406.py            (starts worker, prints state path)
"""
import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from b2_backup import B2, EXCLUDES, checked_url, encoded
import host_identity
from restore_assets_j406 import ranged

PREFIX = 'intelligentai/recovery/dlpc-j406-20261016/'
BUCKET = 'e5cc71977ef1880ca60b0c17'
KEY = Path('/root/.config/intelligentai/b2-j406-writer.json')
RUN = Path('/var/lib/intelligentai-b2-backup-j406')
TREES = ('opt/intelligentai', 'workspace/intelligentai', 'workspace/voice-coaching-tts')
PART_BYTES = 100 * 1024**2
IN_FLIGHT = 6
HTTPS_PROXY = 'http://proxy.ai.gato:3128'


def save(name, value):
    temporary = RUN / (name + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(RUN / name)


def authorize():
    info = KEY.lstat()
    if not KEY.is_file() or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('PRIVATE_WRITER_KEY_REQUIRED')
    key = json.loads(KEY.read_text())
    basic = base64.b64encode((key['keyId'] + ':' + key['applicationKey']).encode()).decode()
    request = urllib.request.Request('https://api.backblazeb2.com/b2api/v4/b2_authorize_account',
                                     headers={'Authorization': 'Basic ' + basic})
    with urllib.request.urlopen(request, timeout=30) as reply:
        auth = json.load(reply)
    api = auth['apiInfo']['storageApi']
    allowed = api['allowed']
    if (set(allowed['capabilities']) != {'listFiles', 'readFiles', 'writeFiles'}
            or allowed.get('namePrefix') != PREFIX
            or [b['id'] for b in allowed.get('buckets', [])] != [BUCKET]):
        raise ValueError('B2_WRITER_SCOPE_MISMATCH')
    return B2(dict(apiUrl=checked_url(api['apiUrl']), downloadUrl=checked_url(api['downloadUrl']),
                   authorizationToken=auth['authorizationToken'], accountId=auth['accountId'],
                   bucketId=BUCKET, bucketName='speakai2026'))


def read_exact(stream, size):
    chunks, total = [], 0
    while total < size:
        raw = stream.read(size - total)
        if not raw:
            break
        chunks.append(raw)
        total += len(raw)
    return b''.join(chunks)


def worker(stamp):
    state = dict(status='uploading', startedAt=time.time(), host=host_identity.current().hostname)
    save('state.json', state)
    client = authorize()
    name = PREFIX + f'runtime-assets-{stamp}.tar.zst'
    if client.call('b2_list_file_names', {'bucketId': BUCKET, 'prefix': name, 'maxFileCount': 1})['files']:
        raise ValueError('B2_BACKUP_NAME_EXISTS')
    excludes = [f'--exclude={p}' for p in EXCLUDES] + ['--exclude=.env-build-tmp']
    tar = subprocess.Popen(['/usr/bin/tar', '-C', '/', '-cpf', '-', '--numeric-owner', '--sparse',
                            '-v', f'--index-file={RUN / "index.txt"}', *excludes, *TREES],
                           stdout=subprocess.PIPE, stderr=open(RUN / 'tar.err', 'wb'))
    zstd = subprocess.Popen(['/usr/bin/zstd', '-q', '-6', '-T12', '-c'], stdin=tar.stdout,
                            stdout=subprocess.PIPE)
    tar.stdout.close()
    item = client.call('b2_start_large_file', dict(bucketId=BUCKET, fileName=name,
                                                    contentType='application/zstd'))
    archive = dict(fileId=item['fileId'], fileName=name, parts=[], bytes=0)
    state.update(archive=archive)
    save('state.json', state)
    digest = hashlib.sha256()
    with concurrent.futures.ThreadPoolExecutor(IN_FLIGHT) as pool:
        pending, number = [], 0
        while raw := read_exact(zstd.stdout, PART_BYTES):
            number += 1
            digest.update(raw)
            archive['bytes'] += len(raw)
            pending.append(pool.submit(client.upload_part, archive['fileId'], number, raw))
            while len(pending) >= IN_FLIGHT:
                archive['parts'].append(pending.pop(0).result())
                save('state.json', state)
        for future in pending:
            archive['parts'].append(future.result())
    if zstd.wait() != 0 or tar.wait() != 0:
        raise ValueError('ARCHIVE_STREAM_FAILED')
    if number < 2:
        raise ValueError('ARCHIVE_TOO_SMALL_FOR_LARGE_FILE')
    archive['parts'].sort(key=lambda p: p['partNumber'])
    finished = client.call('b2_finish_large_file', {'fileId': archive['fileId'],
                           'partSha1Array': [p['sha1'] for p in archive['parts']]})
    if finished['contentLength'] != archive['bytes']:
        raise ValueError('B2_FINISH_SIZE_MISMATCH')
    archive['sha256'] = digest.hexdigest()
    state.update(status='verifying', archive=archive)
    save('state.json', state)
    verify(client, state, stamp)


def verify(client, state, stamp):
    archive, name = state['archive'], state['archive']['fileName']
    # Full download through the same ranged reader recovery uses: SHA-256 over every
    # byte, and a listing compared with the creation index.
    check = hashlib.sha256()
    lister = subprocess.Popen('/usr/bin/zstd -q -dc | /usr/bin/tar -tf - > ' + str(RUN / 'listing.txt'),
                              shell=True, stdin=subprocess.PIPE)
    total = 0
    for raw in ranged(client, archive['fileId'], archive['bytes']):
        total += len(raw)
        check.update(raw)
        lister.stdin.write(raw)
    lister.stdin.close()
    if lister.wait() != 0 or total != archive['bytes'] or check.hexdigest() != archive['sha256']:
        raise ValueError('B2_DOWNLOAD_VERIFY_FAILED')
    index = (RUN / 'index.txt').read_bytes().splitlines()
    listing = (RUN / 'listing.txt').read_bytes().splitlines()
    if index != listing:
        raise ValueError('ARCHIVE_LISTING_MISMATCH')
    deployment = json.loads(Path('/opt/intelligentai/deployment.json').read_bytes())
    manifest = dict(archive=dict(fileId=archive['fileId'], fileName=name, bytes=archive['bytes'],
                                 sha256=archive['sha256'], members=len(listing)),
                    trees=list(TREES), excludes=list(EXCLUDES), host=state['host'], createdAt=stamp,
                    bundleSha256=deployment.get('canonicalIntegration', {}).get('bundleSha256'))
    manifest['upload'] = client.upload_manifest(PREFIX + f'manifest-{stamp}.json', encoded(manifest))
    save('result.json', manifest)
    state.update(status='verified', finishedAt=time.time(), archive=archive)
    save('state.json', state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', metavar='STAMP')
    parser.add_argument('--verify', metavar='STAMP', help='re-verify a finished upload (no re-upload)')
    args = parser.parse_args()
    host_identity.require_root()
    if host_identity.current().hostname != 'J406.2020067.notebook.dlpc':
        raise ValueError('J406_BACKUP_ONLY')
    os.umask(0o077)
    if args.worker or args.verify:
        try:
            if args.worker:
                worker(args.worker)
            else:
                state = json.loads((RUN / 'state.json').read_bytes())
                if (state.get('errorCode') != 'B2_DOWNLOAD_VERIFY_FAILED' or not state['archive'].get('sha256')
                        or not state['archive']['fileName'].endswith(f'runtime-assets-{args.verify}.tar.zst')):
                    raise ValueError('VERIFY_REQUIRES_FINISHED_UPLOAD')
                state.update(status='verifying', errorType=None, errorCode=None)
                save('state.json', state)
                verify(authorize(), state, args.verify)
        except Exception as error:
            state = json.loads((RUN / 'state.json').read_bytes()) if (RUN / 'state.json').exists() else {}
            # Provider errors can contain request details; keep only the type/code.
            state.update(status='failed', errorType=type(error).__name__,
                         errorCode=str(error) if isinstance(error, ValueError) and str(error).isupper() else None)
            save('state.json', state)
            raise SystemExit(1)
        return
    RUN.mkdir(mode=0o700, exist_ok=True)
    if (RUN / 'state.json').exists() and json.loads((RUN / 'state.json').read_bytes())['status'] in ('uploading', 'verifying'):
        raise ValueError('BACKUP_ALREADY_RUNNING')
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    with (RUN / f'worker-{stamp}.log').open('xb') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', stamp],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                                   env=dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1',
                                            # Direct DLPC egress measured 0.29 MiB/s vs 9.3 MiB/s via the platform proxy.
                                            https_proxy=HTTPS_PROXY))
    print(json.dumps(dict(backupStarted=True, pid=process.pid, statePath=str(RUN / 'state.json'))))


if __name__ == '__main__':
    main()
