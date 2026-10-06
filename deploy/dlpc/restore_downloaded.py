"""Restore pinned, downloaded DLPC assets into an empty recreated container.

Verify both hashes and every tar member against the original inventory before
extracting into staging. Refuse existing installations. No secret or admission
configuration is restored, and no service is started by this program.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import grp
import shutil
import socket
import subprocess
import sys
import tarfile
import time

from b2_restore import RUN, OBJECTS

MOVES = ('workspace', 'opt/intelligentai', 'opt/dlpc-migration-20261005',
         'usr/bin/python3.10', 'usr/lib/python3.10')


def save(state):
    p = RUN / 'restore-state.tmp'
    p.write_text(json.dumps(state, indent=2) + '\n')
    p.replace(RUN / 'restore-state.json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    os.umask(0o077)
    if not args.worker:
        if (RUN / 'restore-state.json').exists():
            raise ValueError('EXISTING_RESTORE_REQUIRES_INSPECTION')
        save(dict(status='starting', startedAt=time.time()))
        with (RUN / 'restore.log').open('xb') as log:
            process = subprocess.Popen(['/usr/bin/python3', str(Path(__file__).resolve()), '--worker'],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                env=dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1'))
        (RUN / 'restore-launcher.json').write_text(json.dumps(dict(pid=process.pid)))
        print(json.dumps(dict(restoreStarted=True, pid=process.pid)))
        return
    state = dict(status='verifying_hashes', startedAt=time.time(), moved=[])
    save(state)
    try:
        if json.loads((RUN / 'state.json').read_bytes())['status'] != 'download_verified':
            raise ValueError('VERIFIED_DOWNLOAD_REQUIRED')
        for name, file_id, size, expected in OBJECTS:
            p = RUN / name
            if p.is_symlink() or p.stat().st_size != size:
                raise ValueError('PINNED_FILE_SIZE')
            with p.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('PINNNED_FILE_DIGEST')
        manifest = json.loads((RUN / 'source-manifest.json').read_bytes())
        rows = {row['path']: row for row in manifest['inventory']['paths']}
        if len(rows) != len(manifest['inventory']['paths']):
            raise ValueError('DUPLICATE_INVENTORY_PATH')
        allowed = ('opt/intelligentai', 'opt/dlpc-migration-20261005',
                   'workspace/intelligentai', 'workspace/voice-coaching-tts',
                   'usr/bin/python3.10', 'usr/lib/python3.10')
        for name, row in rows.items():
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or str(p) != name:
                raise ValueError('UNSAFE_INVENTORY_PATH')
            if not any(name == r or name.startswith(r + '/') for r in allowed):
                raise ValueError('UNEXPECTED_ROOT')
            if any(str(parent) in rows and rows[str(parent)]['type'] != 'directory' for parent in p.parents):
                raise ValueError('NON_DIRECTORY_ANCESTOR')
        state.update(status='validating_archive', members=0)
        save(state)
        seen = set()
        with tarfile.open(RUN / 'runtime-assets.tar.gz', 'r|gz') as archive:
            for member in archive:
                name = member.name.rstrip('/')
                if name not in rows or name in seen:
                    raise ValueError('UNEXPECTED_TAR_MEMBER')
                row = rows[name]
                if member.mode != int(row['mode'], 8) or (member.uid, member.gid) != (row['uid'], row['gid']):
                    raise ValueError('TAR_METADATA_MISMATCH')
                if row['type'] == 'directory':
                    valid = member.isdir()
                elif row['type'] == 'symlink':
                    valid = member.issym() and member.linkname == row['linkTarget']
                else:
                    valid = member.isfile() and member.size == row['bytes']
                    if member.islnk():
                        valid = member.linkname in seen and rows[member.linkname]['type'] == 'file'
                if not valid:
                    raise ValueError('TAR_TYPE_OR_CONTENT_METADATA')
                seen.add(name)
                if len(seen) % 10000 == 0:
                    state['members'] = len(seen)
                    save(state)
        if seen != set(rows):
            raise ValueError('MISSING_TAR_MEMBERS')
        if any(os.path.lexists('/' + name) for name in MOVES):
            raise ValueError('EXISTING_INSTALLATION_REFUSED')
        if shutil.disk_usage(RUN).free < manifest['inventory']['sourceBytes'] + 5 * 1024**3:
            raise ValueError('RESTORE_DISK_BUDGET')
        staging = RUN / 'rootfs'
        staging.mkdir(mode=0o700, exist_ok=False)
        state.update(status='extracting', members=len(seen))
        save(state)
        subprocess.run(['nice', '-n', '10', 'tar', '--numeric-owner', '--same-owner',
            '--same-permissions', '--keep-old-files', '-xzf', str(RUN / 'runtime-assets.tar.gz'),
            '-C', str(staging)], check=True)
        # tar creates omitted parent directories under the private umask.
        # Restore traversability for the worker without changing archived rows.
        for base in ('workspace', 'opt/intelligentai'):
            for folder, dirs, files in os.walk(staging / base, followlinks=False):
                p = Path(folder)
                if str(p.relative_to(staging)) not in rows:
                    p.chmod(0o750)
                    os.chown(p, 0, 10001)
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
        state['status'] = 'installing'
        save(state)
        for name in MOVES:
            source, target = staging / name, Path('/') / name
            if not source.exists() or os.path.lexists(target):
                raise ValueError('INSTALL_TARGET_CHANGED')
            os.rename(source, target)
            state['moved'].append(str(target))
            save(state)
        state.update(status='assets_restored_not_started', finishedAt=time.time(),
                     manifestSha256=OBJECTS[0][3], archiveSha256=OBJECTS[1][3])
        save(state)
    except Exception as error:
        state.update(status='failed', errorType=type(error).__name__)
        if isinstance(error, ValueError):
            state['errorCode'] = str(error)
        save(state)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
