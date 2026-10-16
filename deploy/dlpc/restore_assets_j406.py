"""J406 cold recovery: stream the pinned tar.zst from B2 into a private staging root.

Pass the short-lived reader authorization (AWS exporter operation 'b2-j406') on
stdin. The overlay root cannot hold the archive and the trees at once, so the
download is decompressed while hashed; the trees are moved into place only after
the full SHA-256 matches the pin. A mismatched staging tree is discarded.
Private settings are supplied separately; nothing is started here.
"""
import fcntl
import grp
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from b2_backup import B2, encoded
import host_identity

# Pinned from the verified J406 backup manifest (b2_backup_j406.py result.json).
# runtime-assets-20261016T094028Z.tar.zst; full ranged re-download verified 2026-10-16T10:19Z.
ARCHIVE = dict(fileId='4_ze5cc71977ef1880ca60b0c17_f2004652b10a216b6_d20261016_m094030_c005_v0501034_t0027_u01792143630426',
               bytes=15000940702, members=151046,
               sha256='f355ec2fcee01dcf8d344f4dd6b1ee4cad8d5ae194232fa669f4bf7a7df80baa')
BUNDLE_SHA256 = 'd5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443'
TREES = ('opt/intelligentai', 'workspace/intelligentai', 'workspace/voice-coaching-tts')
RAW_BYTES = 31_000_000_000  # extracted size budget (measured 29.0 GB apparent)
STAGE = Path('/var/lib/intelligentai-restore-j406')
# Measured on J406: direct DLPC egress 0.3 MiB/s, platform proxy 8.8 MiB/s (B2 download).
HTTPS_PROXY = 'http://proxy.ai.gato:3128'
# A single long response was cut after ~1.7 GB; fetch fixed ranges, each retried whole.
RANGE_BYTES = 256 * 1024**2


def ranged(client, file_id, size):
    """Yield the object as complete Range slices; a partial slice is never yielded."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': HTTPS_PROXY}))
    url = client.download + '/b2api/v4/b2_download_file_by_id?' + urllib.parse.urlencode({'fileId': file_id})
    for start in range(0, size, RANGE_BYTES):
        end = min(start + RANGE_BYTES, size) - 1
        for attempt in range(6):
            try:
                request = urllib.request.Request(url, headers={'Authorization': client.token,
                    'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'})
                with opener.open(request, timeout=120) as reply:
                    if reply.status != 206:
                        raise ValueError('B2_RANGE_NOT_HONORED')
                    raw = reply.read(end - start + 2)
                if len(raw) != end - start + 1:
                    raise ConnectionError('short range')
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt)
        yield raw


def save(state):
    temporary = STAGE / 'state.tmp'
    temporary.write_bytes(encoded(state))
    temporary.replace(STAGE / 'state.json')


def service_account():
    try:
        if grp.getgrgid(10001).gr_name != 'intelligentai':
            raise ValueError('SERVICE_GID_OCCUPIED')
    except KeyError:
        subprocess.run(['/usr/sbin/groupadd', '-g', '10001', 'intelligentai'], check=True)
    try:
        if pwd.getpwuid(10001).pw_name != 'intelligentai':
            raise ValueError('SERVICE_UID_OCCUPIED')
    except KeyError:
        subprocess.run(['/usr/sbin/useradd', '-u', '10001', '-g', '10001', '-M', '-s',
                        '/usr/sbin/nologin', 'intelligentai'], check=True)


def main():
    identity = host_identity.require_root()
    if identity.hostname != 'J406.2020067.notebook.dlpc' or ARCHIVE['fileId'].startswith('PENDING'):
        raise ValueError('J406_RESTORE_PIN_REQUIRED')
    os.umask(0o077)
    auth = json.loads(sys.stdin.buffer.read(65537))
    with open('/run/intelligentai-asset-restore.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any(os.path.lexists('/' + t) for t in TREES):
            raise ValueError('EXISTING_INSTALLATION_REQUIRES_RECONCILIATION')
        if STAGE.exists():
            raise ValueError('RESTORE_STAGING_EXISTS_RECONCILE')
        if shutil.disk_usage('/var/lib').free < RAW_BYTES + 14 * 1024**3:
            raise ValueError('RESTORE_DISK_BUDGET')
        STAGE.mkdir(mode=0o700)
        root = STAGE / 'rootfs'
        root.mkdir(mode=0o700)
        state = dict(status='downloading', startedAt=time.time(), downloadedBytes=0)
        save(state)
        try:
            extract = subprocess.Popen('/usr/bin/zstd -q -dc | /usr/bin/tar -C ' + str(root)
                                       + ' -xpf - --numeric-owner --same-owner --keep-old-files',
                                       shell=True, stdin=subprocess.PIPE)
            digest, total, last = hashlib.sha256(), 0, 0
            for raw in ranged(B2(auth), ARCHIVE['fileId'], ARCHIVE['bytes']):
                total += len(raw)
                digest.update(raw)
                extract.stdin.write(raw)
                if total - last >= 1024**3:
                    state['downloadedBytes'] = last = total
                    save(state)
            extract.stdin.close()
            if extract.wait() != 0:
                raise ValueError('EXTRACT_FAILED')
            if total != ARCHIVE['bytes'] or digest.hexdigest() != ARCHIVE['sha256']:
                raise ValueError('DOWNLOAD_HASH_MISMATCH')
        except Exception as error:
            # Unverified content is never activated; discard it to free the overlay.
            shutil.rmtree(root, ignore_errors=True)
            state.update(status='failed', errorType=type(error).__name__,
                         errorCode=str(error) if isinstance(error, ValueError) else None)
            save(state)
            raise
        service_account()
        state['status'] = 'installing'
        save(state)
        for tree in TREES:
            source, target = root / tree, Path('/') / tree
            if not source.is_dir() or os.path.lexists(target):
                raise ValueError('INSTALL_TARGET_CHANGED')
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            os.rename(source, target)
        deployment = json.loads(Path('/opt/intelligentai/deployment.json').read_bytes())
        if deployment.get('canonicalIntegration', {}).get('bundleSha256') != BUNDLE_SHA256:
            raise ValueError('RESTORED_BUNDLE_PIN_MISMATCH')
        shutil.rmtree(root)
        state.update(status='assets_restored_not_started', finishedAt=time.time(),
                     archiveSha256=ARCHIVE['sha256'])
        save(state)
        print(json.dumps(dict(assetsRestored=True, privateSettingsRequired=True,
                              servicesStarted=False, productionAdmission=False)))


if __name__ == '__main__':
    main()
