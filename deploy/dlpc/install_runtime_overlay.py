"""Install a hash-pinned, secret-free DLPC app/GOP overlay while analysis is stopped."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket


def install(raw, expected):
    if os.geteuid() != 0 or socket.gethostname() != 'J3990.2020067.notebook.dlpc':
        raise ValueError('EXPECTED_DLPC_ROOT_REQUIRED')
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('OVERLAY_DIGEST_MISMATCH')
    body = json.loads(raw)
    roots = {'app': Path('/opt/intelligentai/app'),
             'core': Path('/workspace/intelligentai/releases/canonical_dlpc_gop_cuda_20261007_v1'),
             'llm': Path('/workspace/intelligentai/validation/canonical_dlpc_gop_cuda_20261007_v1_h5')}
    decoded = []
    for name, row in body['files'].items():
        role, relative = name.split('/', 1)
        if role not in roots or PurePosixPath(relative).is_absolute() or any(x in ('', '.', '..') for x in relative.split('/')) or '\\' in relative:
            raise ValueError('OVERLAY_PATH')
        data = base64.b64decode(row['base64'], validate=True)
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('OVERLAY_FILE_HASH')
        if relative.endswith('.py'): compile(data, name, 'exec')
        target = roots[role] / relative
        if target.resolve() != target or target.is_symlink():
            raise ValueError('OVERLAY_SYMLINK')
        decoded.append((target, data, role))
    os.umask(0o027)
    for target, data, role in decoded:
        target.parent.mkdir(parents=True, exist_ok=True)
        parent = target.parent
        while parent != roots[role].parent:
            os.chown(parent, 0, 10001); parent.chmod(0o750); parent = parent.parent
        temp = target.with_name(target.name + '.dlpc-install')
        with temp.open('xb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.chown(temp, 0, 10001); temp.chmod(0o440 if role != 'app' else 0o640)
        temp.replace(target)
    for role in ('core', 'llm'):
        for folder in sorted((p for p in roots[role].rglob('*') if p.is_dir()), reverse=True):folder.chmod(0o550)
        roots[role].chmod(0o550)
    metadata = Path('/opt/intelligentai/deployment.json')
    doc = json.loads(metadata.read_bytes())
    doc['canonicalIntegration'] = {'sourceGitRevision': body['sourceGitRevision'], 'bundleSha256': expected,
                                  'admissionMode': 'BACKEND_READINESS_GATED'}
    metadata.write_text(json.dumps(doc, sort_keys=True) + '\n')
    os.chown(metadata, 0, 10001); metadata.chmod(0o640)
    print(json.dumps({'installedFiles': len(decoded), 'sourceGitRevision': body['sourceGitRevision'], 'bundleSha256': expected}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('file', type=Path)
    parser.add_argument('sha256')
    args = parser.parse_args()
    install(args.file.read_bytes(), args.sha256)
