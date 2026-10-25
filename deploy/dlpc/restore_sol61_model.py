"""Restore the user-selected Sol 6.1 low overlay or the exact preceding Luna pair."""
import argparse
import json
import os
from pathlib import Path
import sys

import host_identity
import restore_pipeline_latency as previous

base = previous.base
APP = previous.APP
PAYLOAD = Path(__file__).resolve().parent / 'sol61-light-20261026'
SELECTOR = PAYLOAD / 'selected'
PATCH = 'direct-sol61-light-20261026'
LLM = Path('/workspace/intelligentai/validation/canonical_dlpc_pipeline_sol61_light_20261026_v1_h5')
MANIFEST = '8abd31b94aa245617c8a1caf1218e2dae62fdd19058f83a2876abd0a27e135a9'
LOCK = '1903c5339773f301a1a26ba814e970987b3649a88fb3579c11ec41de30a16893'
FILES = {
    'src/voice_coach/backend_analysis/canonical_harness.py': '1c3c4727c539c4efa3e4d139bfc88cde2decd479b0542dd33fe691eb26429847',
    'src/voice_coach/backend_analysis/native_execution.py': 'e10117b009a06ab21b5a0e18017908cc748b3d9ddec05817c4898cd585b9df78',
    'src/voice_coach/backend_analysis/canonical_transport.py': 'a8726836a5a5ccfc280a234d2d634d75a51a806512904314617c94a7525583f8',
    'docs/contracts/runpod_result_v4.schema.json': '9f6d2c674e207eb67bdbd44e6414e045159c1d1a6d430e32999f4ec64a35683d',
}
AFTER = previous.AFTER | FILES


def assets():
    previous.assets()
    manifest_raw = base.pinned(PAYLOAD/'h5/contracts/llm/llm_contract_manifest.json', MANIFEST)
    lock_raw = base.pinned(PAYLOAD/'h5/contracts/llm/dependency_lock.json', LOCK)
    manifest = json.loads(manifest_raw)
    if (manifest['parent_core_manifest_sha256'] != previous.CORE_SHA
            or manifest['dependency_lock_sha256'] != LOCK
            or manifest['prompt_sha256'] != 'fdd98e37b076c3b36058a09bc40667bcd2cd1d9d3a9d31a7106b5caa7df8ca7a'):
        raise ValueError('SOL61_ASSET_PAIR')
    package = {name: base.pinned(PAYLOAD/'h5'/name, digest)
               for name, digest in json.loads(lock_raw)['files'].items()}
    package.update({'contracts/llm/llm_contract_manifest.json': manifest_raw,
                    'contracts/llm/dependency_lock.json': lock_raw})
    if LLM.exists():
        for name, raw in package.items():
            base.pinned(LLM/name, base.sha(raw))
        return
    LLM.mkdir(mode=0o755)
    LLM.chmod(0o755)
    for name, raw in package.items():
        target = LLM/name
        target.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
        for parent in target.parents:
            if parent == LLM:
                break
            parent.chmod(0o755)
        base.replace(target, raw, 0o440)


def save_settings(values, *, rollback=False):
    # Same numerical/direct core and bounded internal pair; only direct H5 changes.
    values['AI_DIRECT_CORE_ROOT'] = str(previous.CORE)
    values['AI_DIRECT_LLM_ROOT'] = str(previous.LLM if rollback else LLM)
    values['AI_CANONICAL_CORE_ROOT'] = str(previous.INTERNAL_CORE)
    values['AI_CANONICAL_LLM_ROOT'] = str(previous.INTERNAL_LLM)
    base.replace(base.ENV, ''.join(k+'='+v+'\n' for k,v in sorted(values.items())).encode(), 0o600, 0)


def rollback(values):
    base.stopped()
    previous.assets()
    pending = []
    for name, after in FILES.items():
        before = previous.AFTER[name]
        if base.sha(base.owned(APP/name)) not in (before, after):
            raise ValueError('SOL61_ROLLBACK_REVISION')
        pending.append((APP/name, base.pinned(previous.PAYLOAD/name, before)))
    for target, raw in pending:
        base.replace(target, raw)
    save_settings(values, rollback=True)
    base.replace(SELECTOR, b'disabled\n', 0o600, 0)
    base.replace(previous.SELECTOR, b'optimized\n', 0o600, 0)
    metadata = Path('/opt/intelligentai/deployment.json')
    doc = json.loads(base.owned(metadata))
    doc.get('operationalPatches', {}).pop(PATCH, None)
    base.replace(metadata, (json.dumps(doc, sort_keys=True)+'\n').encode())
    print('Exact preceding Luna runtime and H5 restored; analysis remains stopped')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if host_identity.require_root().recovery != 'dlpc-j406-20261016':
        raise ValueError('SOL61_HOST_IDENTITY')
    os.umask(0o077)
    sys.path.insert(0, str(APP/'deploy/dlpc'))
    from launch import private_settings
    values = private_settings(base.ENV)
    if values.get('AI_DIRECT_CORE_ROOT') not in (None, str(previous.previous.CORE), str(previous.CORE)):
        raise ValueError('SOL61_EXISTING_DIRECT_CORE')
    if values.get('AI_DIRECT_LLM_ROOT') not in (None, str(previous.previous.LLM), str(previous.LLM), str(LLM)):
        raise ValueError('SOL61_EXISTING_DIRECT_H5')
    if values.get('AI_CANONICAL_CORE_ROOT') not in (previous.OLD_INTERNAL_CORE, str(previous.INTERNAL_CORE)):
        raise ValueError('SOL61_EXISTING_INTERNAL_CORE')
    if values.get('AI_CANONICAL_LLM_ROOT') not in (previous.OLD_INTERNAL_LLM, str(previous.INTERNAL_LLM)):
        raise ValueError('SOL61_EXISTING_INTERNAL_H5')
    if args.rollback:
        rollback(values)
        return
    if base.owned(SELECTOR) != b'light\n':
        raise ValueError('SOL61_SELECTION_REQUIRED')
    pending = []
    for name, desired in AFTER.items():
        before, old = previous.FILES[name]
        if base.sha(base.owned(APP/name, optional=True)) == desired:
            continue
        if base.sha(base.owned(APP/name, optional=True)) not in (*before, old):
            raise ValueError('SOL61_RUNTIME_REVISION')
        source = PAYLOAD if name in FILES else previous.PAYLOAD
        raw = base.pinned(source/name, desired)
        if name.endswith('.py'):
            compile(raw, name, 'exec')
        pending.append((APP/name, raw))
    assets()
    selected = (str(previous.CORE), str(LLM), str(previous.INTERNAL_CORE), str(previous.INTERNAL_LLM))
    current = tuple(values.get(k) for k in ('AI_DIRECT_CORE_ROOT', 'AI_DIRECT_LLM_ROOT',
                                          'AI_CANONICAL_CORE_ROOT', 'AI_CANONICAL_LLM_ROOT'))
    if pending or current != selected:
        base.stopped()
        for name in FILES:
            base.pinned(previous.PAYLOAD/name, previous.AFTER[name])
        for target, raw in pending:
            base.replace(target, raw)
        save_settings(values)
    metadata = Path('/opt/intelligentai/deployment.json')
    doc = json.loads(base.owned(metadata))
    doc.setdefault('operationalPatches', {})[PATCH] = dict(model='gpt-6.1-sol',
        reasoningEffort='low', userEffortLabel='light', serviceTier='default',
        coreManifestSha256=previous.CORE_SHA, llmManifestSha256=MANIFEST,
        llmLockSha256=LOCK, runtimeSha256=AFTER, previousLlmManifestSha256=previous.LLM_SHA)
    base.replace(metadata, (json.dumps(doc, sort_keys=True)+'\n').encode())
    print('Sol 6.1 low/default H5 and exact runtime installed')


if __name__ == '__main__':
    main()
