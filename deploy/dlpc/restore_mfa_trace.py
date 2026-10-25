"""Reconcile the pinned M0 direct release pair, preserving internal and TTS settings."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

import host_identity
import restore_gpt_latency as base

APP = base.APP
PAYLOAD = Path(__file__).resolve().parent / 'mfa-trace-20261025'
SELECTOR = PAYLOAD / 'selected'
BACKUP = Path('/opt/intelligentai/ops/mfa-trace-20261025')
CORE = Path('/workspace/intelligentai/validation/canonical_dlpc_mfa_trace_20261025_v1')
LLM = CORE.with_name(CORE.name + '_h5')
CORE_SHA = 'c9c7d893ebbf6e5a2fa7762c3fa2e72b33b4ada391c945ce564c59d4d4a5bf82'
LLM_SHA = '1d227a6654060357c04fb1a592826bba2bfc464cc536837e2f142cc612573211'
LOCK_SHA = '06907765cad8d14300e41fa6cf8709e32e11edf89c9bf021b945e534348e9579'
AFTER = {
    'src/voice_coach/backend_analysis/canonical_harness.py': '6136ff20a613f7b60b931cc25e91bed4641508933a569be9d312187792788eb3',
    'src/voice_coach/backend_analysis/native_execution.py': '9e57101c623066075bc5463b2c7fd5641fc8d4eef3c37a90525f33792cfef4f7',
    'src/voice_coach/direct_analysis/service.py': '9ff2385182ce3c3a1126679bba42805b2a369bbf1161eef6b50b09e6f0e3fc11',
    'src/voice_coach/direct_analysis/release_selection.py': '2ed29bcb230dcbf5d3dced783659c9f1f1855e2c3f319c3abbc9c5f6f2d79bf7',
    'docs/contracts/runpod_result_v4.schema.json': '37839cec40d1825a8c022d370291c939a4726d9c6f87e72ccaa29b0dba7f97e4',
}
FILES = {name: (tuple(before) + (old_after,), AFTER.get(name, old_after))
         for name, (before, old_after) in base.FILES.items()}
FILES['src/voice_coach/direct_analysis/release_selection.py'] = ((None,), AFTER['src/voice_coach/direct_analysis/release_selection.py'])
PATCH = 'mfa-trace-20261025'


def asset_tree(name, target, manifest_name, pin, key):
    source = PAYLOAD / name
    raw = base.pinned(source / manifest_name, pin)
    manifest = json.loads(raw)
    if key == 'files':
        raise ValueError('MFA_TRACE_ASSET_KEY')
    files = {relative: base.pinned(source / relative, digest) for relative, digest in manifest[key].items()}
    files[manifest_name] = raw
    if target.exists():
        for relative, content in files.items():base.pinned(target / relative, base.sha(content))
        return
    target.mkdir(mode=0o755);target.chmod(0o755)
    for relative, content in files.items():
        dest = target / relative
        dest.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
        for parent in dest.parents:
            if parent == target:break
            parent.chmod(0o755)
        base.replace(dest, content, 0o440)


def assets():
    asset_tree('core', CORE, 'release_manifest.json', CORE_SHA, 'file_sha256')
    source = PAYLOAD / 'h5'
    raw = base.pinned(source / 'contracts/llm/llm_contract_manifest.json', LLM_SHA)
    lm = json.loads(raw)
    if lm['parent_core_manifest_sha256'] != CORE_SHA or lm['dependency_lock_sha256'] != LOCK_SHA:
        raise ValueError('MFA_TRACE_PAIR_DIGEST')
    lock = base.pinned(source / 'contracts/llm/dependency_lock.json', LOCK_SHA)
    files = {n: base.pinned(source / n, v) for n, v in json.loads(lock)['files'].items()}
    files.update({'contracts/llm/llm_contract_manifest.json': raw, 'contracts/llm/dependency_lock.json': lock})
    if LLM.exists():
        for n, content in files.items():base.pinned(LLM / n, base.sha(content))
    else:
        LLM.mkdir(mode=0o755);LLM.chmod(0o755)
        for n, content in files.items():
            dest = LLM / n;dest.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
            for parent in dest.parents:
                if parent == LLM:break
                parent.chmod(0o755)
            base.replace(dest, content, 0o440)


def settings(values, core, llm):
    for key, value in [('AI_DIRECT_CORE_ROOT', core), ('AI_DIRECT_LLM_ROOT', llm)]:
        if value is None:values.pop(key, None)
        else:values[key] = value
    base.replace(base.ENV, ''.join(k + '=' + v + '\n' for k, v in sorted(values.items())).encode(), 0o600, 0)


def rollback(values):
    base.stopped()
    state = json.loads(base.owned(BACKUP / 'state.json'))
    changes = []
    for name, (_, after) in FILES.items():
        before = state['beforeSha256'][name]
        if base.sha(base.owned(APP / name, optional=True)) not in (before, after):
            raise ValueError('MFA_TRACE_ROLLBACK_REVISION')
        changes.append((APP / name, None if before is None else base.pinned(BACKUP / name, before)))
    if values.get('AI_DIRECT_CORE_ROOT') not in (str(CORE), state['directCore']):raise ValueError('MFA_TRACE_ROLLBACK_SELECTION')
    if values.get('AI_DIRECT_LLM_ROOT') not in (str(LLM), state['directLlm']):raise ValueError('MFA_TRACE_ROLLBACK_SELECTION')
    for target, raw in changes:
        if raw is None:
            if target.exists():target.unlink()
        else:base.replace(target, raw)
    settings(values, state['directCore'], state['directLlm'])
    meta = Path('/opt/intelligentai/deployment.json');doc = json.loads(base.owned(meta))
    doc.get('operationalPatches', {}).pop(PATCH, None)
    base.replace(meta, (json.dumps(doc, sort_keys=True) + '\n').encode())
    base.replace(SELECTOR, b'disabled\n', 0o600, 0)
    print('Previous runtime, direct pair and recovery selection restored; analysis remains stopped')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if host_identity.require_root().recovery != 'dlpc-j406-20261016':raise ValueError('MFA_TRACE_J406_REQUIRED')
    os.umask(0o077)
    sys.path.insert(0, str(APP / 'deploy/dlpc'))
    from launch import private_settings
    values = private_settings(base.ENV)
    if args.rollback:
        rollback(values)
        return
    if base.owned(SELECTOR) != b'trace\n':raise ValueError('MFA_TRACE_SELECTION_REQUIRED')
    if values.get('AI_DIRECT_CORE_ROOT') not in (None, str(CORE)):raise ValueError('MFA_TRACE_EXISTING_CORE')
    if values.get('AI_DIRECT_LLM_ROOT') not in (None, base.LOW, str(base.LLM), str(LLM)):
        raise ValueError('MFA_TRACE_EXISTING_H5')
    changes, originals = [], {}
    for name, (before, after) in FILES.items():
        old = base.owned(APP / name, optional=True);originals[name] = old
        if base.sha(old) == after:continue
        if base.sha(old) not in before:raise ValueError('MFA_TRACE_RUNTIME_REVISION')
        raw = base.pinned(PAYLOAD / name, after)
        if name.endswith('.py'):compile(raw, name, 'exec')
        changes.append((APP / name, raw))
    assets()
    if changes or values.get('AI_DIRECT_CORE_ROOT') != str(CORE) or values.get('AI_DIRECT_LLM_ROOT') != str(LLM):
        base.stopped();BACKUP.mkdir(mode=0o700, exist_ok=True)
        state_path = BACKUP / 'state.json'
        if not state_path.exists():
            for name, raw in originals.items():
                if raw is None:continue
                saved = BACKUP / name;saved.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                with saved.open('xb') as stream:stream.write(raw)
            state = dict(beforeSha256={n: base.sha(raw) for n, raw in originals.items()},
                directCore=values.get('AI_DIRECT_CORE_ROOT'), directLlm=values.get('AI_DIRECT_LLM_ROOT'), preparedAt=time.time())
            base.replace(state_path, (json.dumps(state, sort_keys=True) + '\n').encode(), 0o600, 0)
        for target, raw in changes:base.replace(target, raw)
        settings(values, str(CORE), str(LLM))
    meta = Path('/opt/intelligentai/deployment.json');doc = json.loads(base.owned(meta))
    doc.setdefault('operationalPatches', {})[PATCH] = dict(stage='M0', includedGptBaseline='gpt-latency-20261024',
        coreManifestSha256=CORE_SHA, llmManifestSha256=LLM_SHA, llmLockSha256=LOCK_SHA,
        files={n: dict(afterSha256=v[1]) for n, v in FILES.items()})
    base.replace(meta, (json.dumps(doc, sort_keys=True) + '\n').encode())
    print('M0 timing-only direct core/H5 installed; GPT, internal Backend and resource settings preserved')


if __name__ == '__main__':main()
