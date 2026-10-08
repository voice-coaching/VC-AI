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
    llm_directory = body.get('llmDirectory', 'canonical_dlpc_gop_cuda_20261007_v2_h5')
    if llm_directory not in {'canonical_dlpc_gop_cuda_20261007_v2_h5',
                             'canonical_dlpc_gop_announcer_20261007_v1_h5',
                             'canonical_dlpc_gop_announcer_20261007_v2_h5'}:
        raise ValueError('OVERLAY_LLM_DIRECTORY')
    roots = {'app': Path('/opt/intelligentai/app'),
             'core': Path('/workspace/intelligentai/releases/canonical_dlpc_gop_cuda_20261007_v2'),
             'llm': Path('/workspace/intelligentai/validation') / llm_directory}
    roots['mfa'] = Path('/workspace/intelligentai/validation/finish_20260916/latency_optimization_20260921T044013_2c3be9/mfa_startup_recovery_20260921T065201_ac461c/mfa_preflight_optimization_20260922T043055Z_0d7f6a/candidate')
    decoded = []
    for name, row in body['files'].items():
        role, relative = name.split('/', 1)
        if role not in roots or PurePosixPath(relative).is_absolute() or any(x in ('', '.', '..') for x in relative.split('/')) or '\\' in relative:
            raise ValueError('OVERLAY_PATH')
        if role == 'mfa' and (relative != 'mfa.py' or row['sha256'] != 'ebecb9600d0cfc90264042db5b1cd43d0f7ca5aa83275924a5e3fef8b7fd7aee'):
            raise ValueError('MFA_OPTIMIZATION_PIN')
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
