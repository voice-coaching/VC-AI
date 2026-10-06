"""Stream installed DLPC assets to private B2; secrets enter only on stdin.

No recording, /etc, /run, home, SSH key or credential archive. A completed
backup requires full download SHA-256 and gzip/tar validation, not just upload.
The launcher passes a short-lived B2 authorization response to its child pipe;
neither the master key nor authorization tokens are persisted.
"""
import argparse
import concurrent.futures
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PART_BYTES = 64 * 1024**2
PREFIX = 'intelligentai/recovery/dlpc-20261006/'
RUN = Path('/run/intelligentai-b2-backup-20261006')
INSTALL = Path('/opt/dlpc-migration-20261005')
EXCLUDES = ('.git', '.ssh', '.aws', '__pycache__', 'secrets', 'credentials',
            'token', '.env*', '*.env', '*.key', '*.log', 'id_ed25519*', 'id_rsa*')


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def save(name, value):
    path = RUN / name
    temporary = path.with_suffix('.tmp')
    with temporary.open('wb') as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def checked_url(value):
    u = urllib.parse.urlsplit(value)
    if (u.scheme != 'https' or not u.hostname
            or not u.hostname.endswith(('.backblazeb2.com', '.backblaze.com'))
            or u.username or u.password or u.port not in (None, 443) or u.fragment):
        raise ValueError('B2_ENDPOINT_INVALID')
    return value


class B2:
    def __init__(self, auth):
        self.api = checked_url(auth['apiUrl']).rstrip('/')
        self.download = checked_url(auth['downloadUrl']).rstrip('/')
        self.token = auth['authorizationToken']
        self.account = auth['accountId']
        self.bucket = auth['bucketId']
        self.name = auth['bucketName']

    def call(self, method, body):
        request = urllib.request.Request(self.api + '/b2api/v4/' + method,
            data=encoded(body), headers={'Authorization': self.token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=60) as reply:
            return json.load(reply)

    def read(self, file_id):
        request = urllib.request.Request(self.download + '/b2api/v4/b2_download_file_by_id?' +
            urllib.parse.urlencode({'fileId': file_id}),
            headers={'Authorization': self.token, 'Accept-Encoding': 'identity'})
        return urllib.request.urlopen(request, timeout=120)

    def upload_part(self, file_id, number, raw):
        digest = hashlib.sha1(raw).hexdigest()
        for attempt in range(4):
            try:
                target = self.call('b2_get_upload_part_url', {'fileId': file_id})
                request = urllib.request.Request(checked_url(target['uploadUrl']), data=raw,
                    headers={'Authorization': target['authorizationToken'],
                             'X-Bz-Part-Number': str(number), 'Content-Length': str(len(raw)),
                             'X-Bz-Content-Sha1': digest})
                with urllib.request.urlopen(request, timeout=180) as reply:
                    receipt = json.load(reply)
                if (receipt['fileId'] != file_id or receipt['partNumber'] != number
                        or receipt['contentLength'] != len(raw) or receipt['contentSha1'] != digest):
                    raise ValueError('B2_PART_RECEIPT_MISMATCH')
                return {'partNumber': number, 'bytes': len(raw), 'sha1': digest}
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)

    def upload_manifest(self, name, raw):
        digest = hashlib.sha1(raw).hexdigest()
        sha256 = hashlib.sha256(raw).hexdigest()
        target = self.call('b2_get_upload_url', {'bucketId': self.bucket})
        request = urllib.request.Request(checked_url(target['uploadUrl']), data=raw,
            headers={'Authorization': target['authorizationToken'],
                     'X-Bz-File-Name': urllib.parse.quote(name, safe='/'),
                     'Content-Type': 'application/json', 'Content-Length': str(len(raw)),
                     'X-Bz-Content-Sha1': digest, 'X-Bz-Info-sha256': sha256})
        with urllib.request.urlopen(request, timeout=90) as reply:
            item = json.load(reply)
        if (item['fileName'] != name or item['contentLength'] != len(raw)
                or item['contentSha1'] != digest or item['bucketId'] != self.bucket):
            raise ValueError('B2_MANIFEST_RECEIPT_MISMATCH')
        with self.read(item['fileId']) as reply:
            downloaded = reply.read(len(raw) + 1)
        if downloaded != raw:
            raise ValueError('B2_MANIFEST_DOWNLOAD_MISMATCH')
        return {'fileId': item['fileId'], 'fileName': name, 'bytes': len(raw), 'sha256': sha256}


def selected_paths():
    inventory_path = Path(__file__).with_name('assets-20261005.json')
    inventory = json.loads(inventory_path.read_bytes())
    if json.loads((INSTALL / 'installation.json').read_bytes())['status'] != 'installed_not_started':
        raise ValueError('DLPC_INSTALLATION_RECEIPT_REQUIRED')
    if json.loads((INSTALL / 'verification.json').read_bytes())['status'] != 'verified':
        raise ValueError('DLPC_COPY_RECEIPT_REQUIRED')
    roots = [Path(p) for p in inventory['paths']]
    for variant in ('cpu', 'cuda'):
        name = 'canonical_dlpc_' + variant + '_20261005_v2'
        roots.extend([Path('/workspace/intelligentai/releases') / name,
                      Path('/workspace/intelligentai/validation') / (name + '_h5')])
    roots.extend(INSTALL / name for name in ('verification.json', 'installation.json',
        'configuration.json', 'public-ca-receipt.json', 'operational-receipt.json'))
    names = set()

    def select(path):
        if any(any(fnmatch.fnmatch(part, rule) for rule in EXCLUDES) for part in path.parts):
            return False
        if path.suffix == '.pem':
            if tuple(path.parts[-2:]) != ('certifi', 'cacert.pem') or path.is_symlink():
                return False
            raw = path.read_bytes()
            if b'PRIVATE KEY' in raw or b'-----BEGIN CERTIFICATE-----' not in raw:
                raise ValueError('INVALID_PUBLIC_CA')
        names.add(str(path.relative_to('/')))
        return True

    for root in roots:
        if (not root.is_absolute() or '..' in root.parts or not root.exists()
                or not any(root.is_relative_to(x) for x in
                    ('/opt/intelligentai', '/opt/dlpc-migration-20261005', '/workspace/intelligentai',
                     '/workspace/voice-coaching-tts', '/usr/bin/python3.10', '/usr/lib/python3.10'))):
            raise ValueError('BACKUP_SOURCE_INVALID')
        if not select(root):
            raise ValueError('BACKUP_ROOT_EXCLUDED')
        if root.is_dir() and not root.is_symlink():
            for parent, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [name for name in sorted(dirs) if select(Path(parent) / name)]
                for name in sorted(files):
                    select(Path(parent) / name)
    ordered = sorted(names)
    paths = RUN / 'archive-paths.nul'
    paths.write_bytes(b''.join(os.fsencode(p) + b'\0' for p in ordered))
    metadata = []
    source_bytes = 0
    for name in ordered:
        p = Path('/') / name
        info = p.lstat()
        kind = 'symlink' if p.is_symlink() else 'directory' if p.is_dir() else 'file' if p.is_file() else None
        if kind is None:
            raise ValueError('BACKUP_SPECIAL_FILE_UNSUPPORTED')
        row = dict(path=name, type=kind, bytes=info.st_size, mode=oct(info.st_mode & 0o777),
                   uid=info.st_uid, gid=info.st_gid, mtimeNs=info.st_mtime_ns)
        if kind == 'symlink':
            row['linkTarget'] = os.readlink(p)
        elif kind == 'file':
            source_bytes += info.st_size
        metadata.append(row)
    save('source-inventory.json', dict(paths=metadata, roots=[str(x) for x in roots],
        sourceBytes=source_bytes, sourceInventorySha256=hashlib.sha256(inventory_path.read_bytes()).hexdigest()))
    return paths, source_bytes, len(ordered)


def verify_download(client, archive, state):
    state.update(status='verifying_download', verifiedBytes=0)
    save('state.json', state)
    digest = hashlib.sha256()
    total = 0
    with (RUN / 'archive-verification.log').open('ab') as error:
        checker = subprocess.Popen(['nice', '-n', '10', 'tar', '-tzf', '-'],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=error, start_new_session=True)
        try:
            with client.read(archive['fileId']) as reply:
                if int(reply.headers['Content-Length']) != archive['bytes']:
                    raise ValueError('B2_DOWNLOAD_SIZE_HEADER')
                while raw := reply.read(1024**2):
                    digest.update(raw)
                    total += len(raw)
                    checker.stdin.write(raw)
                    if total - state['verifiedBytes'] >= PART_BYTES:
                        state['verifiedBytes'] = total
                        save('state.json', state)
            checker.stdin.close()
            if checker.wait(timeout=60) != 0:
                raise ValueError('B2_ARCHIVE_STRUCTURE_INVALID')
        finally:
            if checker.poll() is None:
                os.killpg(checker.pid, signal.SIGKILL)
                checker.wait()
    if total != archive['bytes'] or digest.hexdigest() != archive['sha256']:
        raise ValueError('B2_FULL_DOWNLOAD_HASH_MISMATCH')
    archive.update(fullDownloadVerified=True, tarStructureVerified=True)
    state['verifiedBytes'] = total
    save('archive.json', archive)


def worker(auth, verify_only=False):
    client = B2(auth)
    auth.clear()
    state = dict(status='checking', startedAt=time.time(), sourceHost=socket.gethostname())
    save('state.json', state)
    archive = None
    tar = None
    try:
        buckets = client.call('b2_list_buckets', {'accountId': client.account, 'bucketId': client.bucket})['buckets']
        if len(buckets) != 1 or buckets[0]['bucketName'] != client.name or buckets[0]['bucketType'] != 'allPrivate':
            raise ValueError('B2_PRIVATE_BUCKET_REQUIRED')
        if verify_only:
            archive = json.loads((RUN / 'archive.json').read_bytes())
            if not archive.get('uploaded') or archive['bucketId'] != client.bucket or not archive['fileName'].startswith(PREFIX):
                raise ValueError('B2_COMPLETED_UPLOAD_RECEIPT_REQUIRED')
            verify_download(client, archive, state)
            finalize_manifest(client, archive, state)
            return
        existing = client.call('b2_list_file_names', {'bucketId': client.bucket, 'prefix': PREFIX, 'maxFileCount': 1})
        if existing['files']:
            raise ValueError('B2_BACKUP_PREFIX_ALREADY_EXISTS')
        paths, source_bytes, members = selected_paths()
        state.update(status='uploading', sourceBytes=source_bytes, members=members, uploadedBytes=0, parts=0)
        item = client.call('b2_start_large_file', dict(bucketId=client.bucket,
            fileName=PREFIX + 'runtime-assets.tar.gz', contentType='application/gzip',
            fileInfo={'source': 'DLPC installed runtime', 'scope': 'no secrets or user recordings',
                      'backup_format': 'dlpc-runtime-v1'}))
        archive = dict(fileId=item['fileId'], fileName=item['fileName'], bucketId=client.bucket,
                       bucketName=client.name, parts=[], bytes=0)
        save('archive.json', archive)
        save('state.json', state)
        digest = hashlib.sha256()
        with (RUN / 'tar.log').open('xb') as error:
            tar = subprocess.Popen(['nice', '-n', '10', 'tar', '-I', 'gzip -1', '-cf', '-',
                '-C', '/', '--no-recursion', '--null', '-T', str(paths)],
                stdout=subprocess.PIPE, stderr=error, start_new_session=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                pending = []
                number = 0
                while raw := tar.stdout.read(PART_BYTES):
                    number += 1
                    if number > 10000:
                        raise ValueError('B2_PART_LIMIT')
                    digest.update(raw)
                    archive['bytes'] += len(raw)
                    pending.append(pool.submit(client.upload_part, archive['fileId'], number, raw))
                    if len(pending) == 2:
                        row = pending.pop(0).result()
                        archive['parts'].append(row)
                        state.update(uploadedBytes=state['uploadedBytes'] + row['bytes'], parts=len(archive['parts']))
                        save('archive.json', archive)
                        save('state.json', state)
                for future in pending:
                    row = future.result()
                    archive['parts'].append(row)
                    state.update(uploadedBytes=state['uploadedBytes'] + row['bytes'], parts=len(archive['parts']))
                tar.stdout.close()
                if tar.wait(timeout=60) != 0:
                    raise ValueError('SOURCE_ARCHIVE_FAILED_OR_CHANGED')
        archive['sha256'] = digest.hexdigest()
        state['status'] = 'finishing_upload'
        save('archive.json', archive)
        save('state.json', state)
        item = client.call('b2_finish_large_file', {'fileId': archive['fileId'],
            'partSha1Array': [r['sha1'] for r in archive['parts']]})
        if item['contentLength'] != archive['bytes'] or item['fileName'] != archive['fileName']:
            raise ValueError('B2_FINISHED_FILE_MISMATCH')
        archive['uploaded'] = True
        save('archive.json', archive)
        verify_download(client, archive, state)
        finalize_manifest(client, archive, state)
    except BaseException as error:
        previous = state['status']
        state.update(status='failed', failedStage=previous, errorType=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch('[A-Z_]{1,100}', str(error)):
            state['errorCode'] = str(error)
        if isinstance(error, urllib.error.HTTPError):
            state['httpStatus'] = error.code
        if archive and not archive.get('uploaded'):
            # Cancel only our newly started, still unfinished object. An
            # ambiguous finish response is checked before attempting cleanup.
            try:
                info = client.call('b2_get_file_info', {'fileId': archive['fileId']})
                if info.get('action') == 'upload':
                    state['uploadedObjectRetainedForVerification'] = archive['fileId']
                elif info.get('action') == 'start':
                    client.call('b2_cancel_large_file', {'fileId': archive['fileId']})
                    state['unfinishedUploadCancelled'] = True
            except urllib.error.HTTPError as lookup:
                if lookup.code in (400, 404):
                    try:
                        client.call('b2_cancel_large_file', {'fileId': archive['fileId']})
                        state['unfinishedUploadCancelled'] = True
                    except Exception:
                        state['unfinishedCleanupRequired'] = archive['fileId']
            except Exception:
                state['unfinishedCleanupRequired'] = archive['fileId']
        save('state.json', state)
        raise SystemExit(1)
    finally:
        if tar and tar.poll() is None:
            os.killpg(tar.pid, signal.SIGTERM)
            try:
                tar.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(tar.pid, signal.SIGKILL)
                tar.wait()


def finalize_manifest(client, archive, state):
    manifest = dict(format='dlpc-runtime-backup.v1', archivedAt=time.time(),
        archive=archive, inventory=json.loads((RUN / 'source-inventory.json').read_bytes()),
        backupProgramSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        sourceHost=state['sourceHost'], excludes=list(EXCLUDES) + ['PEM except public certifi CA'],
        includesOsImage=False, includesSecrets=False, includesUserRecordings=False,
        includesPendingJournals=False, restoreBootVerified=False, inferenceQualityVerified=False,
        restorePrerequisites=['Ubuntu 24.04 platform and compatible NVIDIA driver',
            'dedicated service UID/GID 10001; historical absolute paths',
            'secure analysis/TTS settings re-injection; SSH key separately provisioned',
            'private runtime directories and platform startup hook',
            'reviewed release identity registration and developer QA before admission'])
    raw = encoded(manifest)
    save('source-manifest.json', manifest)
    result = client.upload_manifest(PREFIX + 'source-manifest.json', raw)
    state.update(status='verified', finishedAt=time.time(), archiveFileId=archive['fileId'],
        archiveBytes=archive['bytes'], archiveSha256=archive['sha256'],
        manifest=result, fullDownloadVerified=True, tarStructureVerified=True)
    save('state.json', state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('VERIFIED_DLPC_ROOT_REQUIRED')
    os.umask(0o077)
    auth = json.loads(sys.stdin.buffer.read(65537))
    if set(auth) != {'apiUrl', 'downloadUrl', 'authorizationToken', 'accountId', 'bucketId', 'bucketName'}:
        raise ValueError('B2_PROCESS_SETTINGS_INVALID')
    if args.worker:
        worker(auth, args.verify_only)
    else:
        if args.verify_only:
            if json.loads((RUN / 'state.json').read_bytes())['status'] != 'failed':
                raise ValueError('B2_VERIFY_REQUIRES_FAILED_PRIOR_JOB')
        else:
            RUN.mkdir(mode=0o700, exist_ok=False)
        env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1')
        with (RUN / 'worker.log').open('ab' if args.verify_only else 'xb') as log:
            command = ['/usr/bin/python3', str(Path(__file__).resolve()), '--worker']
            if args.verify_only:
                command.append('--verify-only')
            process = subprocess.Popen(command,
                stdin=subprocess.PIPE, stdout=log, stderr=log, env=env, start_new_session=True)
        process.stdin.write(encoded(auth))
        process.stdin.close()
        auth.clear()
        save('launcher.json', {'pid': process.pid, 'startedAt': time.time()})
        print(json.dumps({'backupStarted': True, 'pid': process.pid, 'statePath': str(RUN / 'state.json')}))


if __name__ == '__main__':
    main()
