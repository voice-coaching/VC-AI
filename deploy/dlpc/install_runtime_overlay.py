"""Install a hash-pinned, secret-free DLPC app/GOP overlay while analysis is stopped."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket
import host_identity


def install(raw, expected):
    identity = host_identity.require_root()
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
    legacy_links = {}
    legacy_pointers = {
        'configs/pronunciation_feedback/prompts/coaching/active.md': 'pronunciation-coaching-v1.md',
        'configs/pronunciation_feedback/prompts/scoring/active-hierarchy.md': 'pronunciation-hierarchy-v3.md',
        'configs/pronunciation_feedback/prompts/scoring/active.md': 'pronunciation-v2.md',
    }
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
            # The base backup used symlinks for these three active pointers;
            # the pinned current Git blobs contain the same relative names.
            # Replacing only the leaf preserves the referenced prompt bytes.
            pointer = legacy_pointers.get(relative) if role == 'app' else None
            if (not pointer or not target.is_symlink() or target.parent.resolve() != target.parent
                    or os.readlink(target) != pointer or data != pointer.encode()
                    or 'app/' + str(PurePosixPath(relative).parent / pointer) not in body['files']):
                raise ValueError('OVERLAY_SYMLINK')
            legacy_links[str(target)] = pointer
        decoded.append((target, data, role))
    # Git stores symlink targets as blobs, not regular file contents. Keep this
    # separate inventory so an active prompt link cannot overwrite its target.
    links=[]
    for relative, value in body.get('appSymlinks', {}).items():
        path=PurePosixPath(relative); destination=PurePosixPath(value)
        if (not relative.startswith('configs/pronunciation_feedback/prompts/')
                or path.is_absolute() or any(x in ('', '.', '..') for x in relative.split('/'))
                or '\\' in relative or destination.is_absolute() or len(destination.parts)!=1
                or value in ('', '.', '..') or '\\' in value or 'app/'+relative in body['files']):
            raise ValueError('OVERLAY_GIT_SYMLINK_PATH')
        target=roots['app']/relative
        if target.parent.resolve()!=target.parent:
            raise ValueError('OVERLAY_GIT_SYMLINK_PARENT')
        if target.is_symlink():
            if os.readlink(target)!=value:raise ValueError('OVERLAY_GIT_SYMLINK_DRIFT')
        elif target.exists():raise ValueError('OVERLAY_GIT_SYMLINK_DRIFT')
        referent=target.parent/value
        if not referent.is_relative_to(roots['app']) or 'app/'+str(referent.relative_to(roots['app'])) not in body['files']:
            raise ValueError('OVERLAY_GIT_SYMLINK_REFERENT')
        links.append((target,value))
    os.umask(0o027)
    if legacy_links:
        backup = Path('/opt/intelligentai/ops/overlay-link-recovery') / expected
        backup.mkdir(parents=True, mode=0o700, exist_ok=True)
        receipt = backup / 'links.json'
        original = (json.dumps(legacy_links, sort_keys=True) + '\n').encode()
        if receipt.exists():
            if receipt.is_symlink() or receipt.read_bytes() != original:
                raise ValueError('OVERLAY_LINK_BACKUP_CHANGED')
        else:
            with receipt.open('xb') as stream:
                stream.write(original); stream.flush(); os.fsync(stream.fileno())
            receipt.chmod(0o600)
    for target, data, role in decoded:
        target.parent.mkdir(parents=True, exist_ok=True)
        parent = target.parent
        # MFA's optimization directory is absent from the base archive. mkdir
        # creates it as root:root 750, so fixing only candidate/ leaves the
        # service unable to import mfa.py when the first request arrives.
        boundary = roots[role].parent.parent if role == 'mfa' else roots[role].parent
        while parent != boundary:
            os.chown(parent, 0, 10001); parent.chmod(0o750); parent = parent.parent
        temp = target.with_name(target.name + '.dlpc-install')
        with temp.open('xb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.chown(temp, 0, 10001); temp.chmod(0o440 if role != 'app' else 0o640)
        temp.replace(target)
    for target,value in links:
        if not target.is_symlink():
            target.symlink_to(value);os.lchown(target,0,10001)
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
