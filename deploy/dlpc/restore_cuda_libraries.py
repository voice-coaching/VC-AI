"""Restore the separately pinned cuDNN set; never change system libraries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
from b2_backup import B2

ROOT = Path('/opt/intelligentai/runtime-libs')
TARGET = ROOT / 'cudnn-9.24.0'
FILE_ID = '4_ze5cc71977ef1880ca60b0c17_f1070e3e27458e096_d20261006_m162315_c005_v0501013_t0003_u01791303795829'
SIZE = 818749951
SHA256 = '3470515d874d346202cc1f09b8fa0be558cf8e471b8616e49bd4f508c87a0cce'
MANIFEST = '05b60abac7117dd40580ad1c831864abc6a9ef8ac870c7482cbe02beaedc63e9'


def verify():
    raw = (TARGET / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST: raise ValueError('CUDNN_MANIFEST_PIN')
    for name, row in json.loads(raw).items():
        p = TARGET / name
        if p.resolve() != p or p.stat().st_size != row['bytes']: raise ValueError('CUDNN_FILE_SCOPE')
        with p.open('rb') as f:
            if hashlib.file_digest(f, 'sha256').hexdigest() != row['sha256']: raise ValueError('CUDNN_FILE_HASH')


def main():
    auth = json.loads(sys.stdin.buffer.read(65537))
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    if ROOT.resolve() != ROOT or ROOT.stat().st_uid != 0:
        raise ValueError('CUDNN_RUNTIME_DIRECTORY_OWNER')
    # Recovery runs with umask 077. mkdir's requested 0755 alone leaves this
    # ancestor at 0700, preventing the inference UID from loading the libraries.
    ROOT.chmod(0o755)
    if TARGET.exists(): verify(); return
    archive = ROOT / 'cudnn-9.24.0.tar.gz.partial'
    digest = hashlib.sha256(); total = 0
    with B2(auth).read(FILE_ID) as response, archive.open('xb') as out:
        while raw := response.read(1024 ** 2):
            total += len(raw)
            if total > SIZE: raise ValueError('CUDNN_BACKUP_SIZE')
            digest.update(raw); out.write(raw)
        out.flush(); os.fsync(out.fileno())
    if total != SIZE or digest.hexdigest() != SHA256: raise ValueError('CUDNN_BACKUP_HASH')
    with tarfile.open(archive, 'r:gz') as t:
        for member in t.getmembers():
            p = Path(member.name)
            if p.is_absolute() or '..' in p.parts or p.parts[0] != TARGET.name or member.islnk():
                raise ValueError('CUDNN_ARCHIVE_SCOPE')
        t.extractall(ROOT, filter='data')
    verify()
    for p in TARGET.rglob('*'):
        if not p.is_symlink(): os.chown(p, 0, 0); p.chmod(0o755 if p.is_dir() else 0o444)
    TARGET.chmod(0o755)
    archive.unlink()


if __name__ == '__main__': main()
