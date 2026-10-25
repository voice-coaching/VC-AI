"""Restore the user-authorized direct Luna release from pinned recovery assets.

Install this module and its luna-runtime-20261024 payload in the persistent
recovery directory. Analysis must be stopped when any runtime file changes.
Backend handoff settings and its approved release remain independent.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import host_identity

APP = Path('/opt/intelligentai/app')
PAYLOAD = Path(__file__).resolve().parent / 'luna-runtime-20261024'
DIRECTORY = 'canonical_dlpc_gop_luna_20261024_v1_h5'
LLM = Path('/workspace/intelligentai/validation') / DIRECTORY
MANIFEST = '1c62cd5f42625d80c2ca772d631ec3a2f9b6286d7e2a4b48e1b0b4f5a290899e'
LOCK = '8da3410fba23a1f0c802b436edca181a7fa0c3a14f2b455f4a26b9f7a0330c73'
FILES = {
    'src/voice_coach/backend_analysis/canonical_harness.py': (
        'd0188e838ebd70a72cae25258c81af2f0a9ce06bcf36a6e39c38e9a18247ddb3',
        'ea60fec8344225a29b24732d96c167b1ddb654fcfa48b963d8814ae83e4bf1ca'),
    'src/voice_coach/backend_analysis/native_execution.py': (
        'db47f6a093eb443bf47f2ef8eda6632904bfd1514e385a2718f882d7b9f501c8',
        '0c04a6902997054bb58fbcc43539d2b202b9d6ac71a3e025f1e441f940bab36e'),
    'src/voice_coach/backend_analysis/canonical_transport.py': (
        'd8666038c7a7ef44e7dee77811730e32c2b71a7709e6b89157dcfde4c915b7da',
        'c16b40de5db54e1447278722e4596f5d3f6e1dba8e0ed953f1a535f8b5889eb8'),
    'src/voice_coach/direct_analysis/service.py': (
        '47758c852a7d0e7133b135ef2d75220db71761c3385191c03846e47381595fa0',
        '911e2fcaebe929dff5ce2dcb5b303a0b125cdcc057acdc00bc24a6fe7b890f66'),
    'src/voice_coach/direct_analysis/worker.py': (
        '77e4de9f289b0cc2722569f78bec0e4af686ba555c3f7748ae31c82b948726dd',
        '5e8a18ce408e47ceb19b61c0512dfac61ef6098d47165f384c44657f49b6e2ac'),
    'docs/contracts/runpod_result_v4.schema.json': (
        '2f2fb264f8af45f18aa2752a8d5495ae445bb2e04599420e686ffb0d97df71ff',
        '66085b9c29b48e2d651221b79213e4307423c87e6919af830bc0ee95a2e489aa'),
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pinned(path, digest):
    if path.is_symlink() or sha(path.read_bytes()) != digest:
        raise ValueError('LUNA_ASSET_DIGEST_MISMATCH')
    return path.read_bytes()


def replace(path, raw, mode=0o640, gid=10001):
    pending = path.with_name(path.name + '.luna-pending')
    with pending.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.chown(pending, 0, gid); pending.chmod(mode); pending.replace(path)


def main():
    host = host_identity.require_root()
    os.umask(0o077)
    changes = []
    for relative, (before, after) in FILES.items():
        target = APP / relative
        if target.resolve(strict=True) != target or target.stat().st_uid != 0:
            raise ValueError('LUNA_RUNTIME_PATH_CHANGED')
        old = target.read_bytes()
        if sha(old) == after:
            continue
        if sha(old) != before:
            raise ValueError('LUNA_RUNTIME_REVISION_CHANGED')
        raw = pinned(PAYLOAD / relative, after)
        if relative.endswith('.py'):
            compile(raw, relative, 'exec')
        changes.append((target, old, raw))
    # Keep the serialized request, adapter and output validation pinned together.
    source = PAYLOAD / 'h5'
    manifest = json.loads(pinned(source / 'contracts/llm/llm_contract_manifest.json', MANIFEST))
    lock = json.loads(pinned(source / 'contracts/llm/dependency_lock.json', LOCK))
    if manifest['dependency_lock_sha256'] != LOCK:
        raise ValueError('LUNA_LOCK_MISMATCH')
    assets = {name: pinned(source / name, digest) for name, digest in lock['files'].items()}
    assets.update({'contracts/llm/llm_contract_manifest.json': pinned(
        source / 'contracts/llm/llm_contract_manifest.json', MANIFEST),
        'contracts/llm/dependency_lock.json': pinned(source / 'contracts/llm/dependency_lock.json', LOCK)})
    if LLM.exists():
        for name, raw in assets.items():
            pinned(LLM / name, sha(raw))
    else:
        LLM.mkdir(mode=0o755)
        LLM.chmod(0o755)
        for name, raw in assets.items():
            target = LLM / name
            target.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
            for parent in target.parents:
                if parent == LLM:
                    break
                parent.chmod(0o755)
            replace(target, raw, 0o440)
    sys.path.insert(0, str(APP / 'deploy/dlpc'))
    from launch import private_settings
    env = Path('/etc/intelligentai/analysis.env')
    values = private_settings(env)
    configured = values.get('AI_DIRECT_LLM_ROOT')
    if configured not in (None, str(LLM)):
        raise ValueError('LUNA_EXISTING_DIRECT_OVERRIDE')
    if changes or configured is None:
        from restore_direct_resident import CTL
        if Path('/run/intelligentai/dlpc-supervisor.sock').exists():
            result = subprocess.run(CTL + ['status', 'analysis'], capture_output=True, text=True, timeout=15)
            fields = result.stdout.split()
            if len(fields) < 2 or fields[1] not in {'STOPPED', 'EXITED', 'FATAL'}:
                raise ValueError('ANALYSIS_MUST_BE_STOPPED')
        backup = Path('/opt/intelligentai/ops/luna-model-20261024')
        backup.mkdir(mode=0o700, exist_ok=True)
        for target, old, raw in changes:
            saved = backup / target.relative_to(APP)
            saved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not saved.exists():
                saved.write_bytes(old)
            elif saved.read_bytes() != old:
                raise ValueError('LUNA_BACKUP_CHANGED')
            replace(target, raw)
        if configured is None:
            saved = backup / 'analysis.env.before'
            with saved.open('xb') as stream:
                stream.write(env.read_bytes())
            values['AI_DIRECT_LLM_ROOT'] = str(LLM)
            replace(env, ''.join(k + '=' + v + '\n' for k, v in sorted(values.items())).encode(), 0o600, 0)
    metadata = Path('/opt/intelligentai/deployment.json')
    doc = json.loads(metadata.read_bytes())
    doc.setdefault('operationalPatches', {})['direct-luna-model-20261024'] = dict(
        model='gpt-6-luna', llmManifestSha256=MANIFEST, llmLockSha256=LOCK,
        files={k: dict(beforeSha256=v[0], afterSha256=v[1]) for k, v in FILES.items()})
    replace(metadata, (json.dumps(doc, sort_keys=True) + '\n').encode())
    print('Pinned direct Luna release installed; Backend handoff release preserved')


if __name__ == '__main__':
    main()
