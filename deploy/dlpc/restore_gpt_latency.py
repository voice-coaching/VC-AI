"""Install or roll back the pinned P0/P1 direct patch with analysis stopped."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import host_identity

APP = Path('/opt/intelligentai/app')
PAYLOAD = Path(__file__).resolve().parent / 'gpt-latency-20261024'
SELECTOR = PAYLOAD / 'selected'
BACKUP = Path('/opt/intelligentai/ops/gpt-latency-20261024')
ENV = Path('/etc/intelligentai/analysis.env')
LLM = Path('/workspace/intelligentai/validation/canonical_dlpc_gop_luna_none_20261024_v1_h5')
LOW = '/workspace/intelligentai/validation/canonical_dlpc_gop_luna_20261024_v1_h5'
MANIFEST = 'a0be7ab1bd5738c14798f15e6c72f82558106f717798ad7e5da90f52e2dc5691'
LOCK = 'ed9bab85e9fbbda03196c77ebc7d191f382dfb108670a6fb296f15f83f061869'
PATCH = 'gpt-latency-20261024'
FILES = {
    'src/voice_coach/backend_analysis/canonical_harness.py': (
        ('d0188e838ebd70a72cae25258c81af2f0a9ce06bcf36a6e39c38e9a18247ddb3',
         'ea60fec8344225a29b24732d96c167b1ddb654fcfa48b963d8814ae83e4bf1ca'),
        '85b663205c1b7d6d54e5564a94ba7e3b8e24c43de697f833d6e04ceaf96ad84c'),
    'src/voice_coach/backend_analysis/native_execution.py': (
        ('db47f6a093eb443bf47f2ef8eda6632904bfd1514e385a2718f882d7b9f501c8',
         '0c04a6902997054bb58fbcc43539d2b202b9d6ac71a3e025f1e441f940bab36e'),
        'a85827b118a7e418d3a58c238989f2427fb8990204ccce98fc5c65caadcbcb0a'),
    'src/voice_coach/backend_analysis/canonical_transport.py': (
        ('d8666038c7a7ef44e7dee77811730e32c2b71a7709e6b89157dcfde4c915b7da',
         'c16b40de5db54e1447278722e4596f5d3f6e1dba8e0ed953f1a535f8b5889eb8'),
        '7e596127768e7f3dcc29de93af7f05e3983a6db4c86fc4ef8a654d9eee11bb22'),
    'src/voice_coach/backend_analysis/canonical_provider_observation.py': (
        (None,), '161cd31f74bc074ce854dc51ae28866dc71ecdd6044d0de320780e47458e1571'),
    'src/voice_coach/direct_analysis/service.py': (
        ('47758c852a7d0e7133b135ef2d75220db71761c3385191c03846e47381595fa0',),
        '911e2fcaebe929dff5ce2dcb5b303a0b125cdcc057acdc00bc24a6fe7b890f66'),
    'src/voice_coach/direct_analysis/worker.py': (
        ('77e4de9f289b0cc2722569f78bec0e4af686ba555c3f7748ae31c82b948726dd',
         '5e8a18ce408e47ceb19b61c0512dfac61ef6098d47165f384c44657f49b6e2ac'),
        '94312cce15b5f110905b467323a81817edf4fa557149cf0649e5a522821df0a1'),
    'docs/contracts/runpod_result_v4.schema.json': (
        ('2f2fb264f8af45f18aa2752a8d5495ae445bb2e04599420e686ffb0d97df71ff',
         '66085b9c29b48e2d651221b79213e4307423c87e6919af830bc0ee95a2e489aa'),
        '4f29d85d935396ed38734c15e34fc8c72b11a73fce4ee2521cf533a593c6377f'),
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def owned(path, *, optional=False):
    if optional and not path.exists() and not path.is_symlink():
        return None
    if path.resolve(strict=True) != path or path.stat().st_uid != 0:
        raise ValueError('LATENCY_ROOT_ASSET_REQUIRED')
    return path.read_bytes()


def pinned(path, digest):
    # Network H5 assets can expose synthetic ownership; the closed SHA is authoritative.
    if path.resolve(strict=True) != path:
        raise ValueError('LATENCY_ASSET_SYMLINK')
    raw = path.read_bytes()
    if sha(raw) != digest:
        raise ValueError('LATENCY_ASSET_DIGEST')
    return raw


def replace(path, raw, mode=0o640, gid=10001):
    pending = path.with_name(path.name + '.latency-pending')
    with pending.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.chown(pending, 0, gid); pending.chmod(mode); pending.replace(path)


def stopped():
    if Path('/run/intelligentai/dlpc-supervisor.sock').exists():
        from restore_direct_resident import CTL
        result = subprocess.run(CTL + ['status', 'analysis'], capture_output=True, text=True, timeout=15)
        fields = result.stdout.split()
        if len(fields) < 2 or fields[1] not in {'STOPPED', 'EXITED', 'FATAL'}:
            raise ValueError('ANALYSIS_MUST_BE_STOPPED')


def h5():
    source = PAYLOAD / 'h5'
    manifest_raw = pinned(source / 'contracts/llm/llm_contract_manifest.json', MANIFEST)
    lock_raw = pinned(source / 'contracts/llm/dependency_lock.json', LOCK)
    manifest = json.loads(manifest_raw)
    if manifest['dependency_lock_sha256'] != LOCK:
        raise ValueError('LATENCY_H5_LOCK')
    assets = {name: pinned(source / name, digest) for name, digest in json.loads(lock_raw)['files'].items()}
    assets.update({'contracts/llm/llm_contract_manifest.json': manifest_raw,
                   'contracts/llm/dependency_lock.json': lock_raw})
    if LLM.exists():
        for name, raw in assets.items():
            pinned(LLM / name, sha(raw))
        return
    LLM.mkdir(mode=0o755); LLM.chmod(0o755)
    for name, raw in assets.items():
        target = LLM / name
        target.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
        for parent in target.parents:
            if parent == LLM:
                break
            parent.chmod(0o755)
        replace(target, raw, 0o440)


def rollback(values):
    stopped()
    state = json.loads(owned(BACKUP / 'state.json'))
    changes = []
    for name, (_, after) in FILES.items():
        target = APP / name
        before = state['beforeSha256'][name]
        current = owned(target, optional=True)
        if sha(current) not in (before, after):
            raise ValueError('LATENCY_ROLLBACK_REVISION_CHANGED')
        raw = None if before is None else pinned(BACKUP / name, before)
        changes.append((target, raw))
    if values.get('AI_DIRECT_LLM_ROOT') not in (str(LLM), state['previousDirectLLMRoot']):
        raise ValueError('LATENCY_ROLLBACK_SELECTION_CHANGED')
    for target, raw in changes:
        if raw is None:
            if target.exists(): target.unlink()
        else:
            replace(target, raw)
    if state['previousDirectLLMRoot'] is None:
        values.pop('AI_DIRECT_LLM_ROOT', None)
    else:
        values['AI_DIRECT_LLM_ROOT'] = state['previousDirectLLMRoot']
    replace(ENV, ''.join(k + '=' + v + '\n' for k, v in sorted(values.items())).encode(), 0o600, 0)
    metadata = Path('/opt/intelligentai/deployment.json')
    doc = json.loads(owned(metadata));doc.get('operationalPatches', {}).pop(PATCH, None)
    replace(metadata, (json.dumps(doc, sort_keys=True) + '\n').encode())
    replace(SELECTOR, b'disabled\n', 0o600, 0)
    print('Previous direct runtime, release selection and recovery selection restored; analysis remains stopped')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if host_identity.require_root().recovery != 'dlpc-j406-20261016':
        raise ValueError('LATENCY_J406_REQUIRED')
    os.umask(0o077)
    sys.path.insert(0, str(APP / 'deploy/dlpc'))
    from launch import private_settings
    values = private_settings(ENV)
    if args.rollback:
        rollback(values)
        return
    if owned(SELECTOR) != b'none\n':
        raise ValueError('LATENCY_RECOVERY_SELECTION_REQUIRED')
    if values.get('AI_DIRECT_LLM_ROOT') not in (None, LOW, str(LLM)):
        raise ValueError('LATENCY_EXISTING_SELECTION')
    changes = []
    old_files = {}
    for name, (before, after) in FILES.items():
        target = APP / name
        old = owned(target, optional=True)
        old_files[name] = old
        if sha(old) == after:
            continue
        if sha(old) not in before:
            raise ValueError('LATENCY_RUNTIME_REVISION_CHANGED')
        raw = pinned(PAYLOAD / name, after)
        if name.endswith('.py'):
            compile(raw, name, 'exec')
        changes.append((target, raw))
    h5()
    if changes or values.get('AI_DIRECT_LLM_ROOT') != str(LLM):
        stopped()
        BACKUP.mkdir(mode=0o700, exist_ok=True)
        state_path = BACKUP / 'state.json'
        if not state_path.exists():
            for name, raw in old_files.items():
                if raw is not None:
                    saved = BACKUP / name
                    saved.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    with saved.open('xb') as stream:stream.write(raw)
            state = dict(beforeSha256={name: sha(raw) for name, raw in old_files.items()},
                previousDirectLLMRoot=values.get('AI_DIRECT_LLM_ROOT'), preparedAt=time.time())
            replace(state_path, (json.dumps(state, sort_keys=True) + '\n').encode(), 0o600, 0)
        for target, raw in changes:replace(target, raw)
        values['AI_DIRECT_LLM_ROOT'] = str(LLM)
        # Read current secrets through the existing parser; never copy them into payload or backups.
        replace(ENV, ''.join(k + '=' + v + '\n' for k, v in sorted(values.items())).encode(), 0o600, 0)
    metadata = Path('/opt/intelligentai/deployment.json')
    doc = json.loads(owned(metadata))
    doc.setdefault('operationalPatches', {})[PATCH] = dict(stage='none', model='gpt-6-luna',
        reasoningEffort='none', serviceTier='default', llmManifestSha256=MANIFEST,
        llmLockSha256=LOCK, files={k: dict(allowedBeforeSha256=v[0], afterSha256=v[1]) for k, v in FILES.items()})
    replace(metadata, (json.dumps(doc, sort_keys=True) + '\n').encode())
    print('P0 observations and P1 Luna none/default installed; compact/cache remain inactive')


if __name__ == '__main__':
    main()
