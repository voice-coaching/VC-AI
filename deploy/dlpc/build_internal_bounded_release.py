"""Bound only the closed Backend lane's diagnostics, preserving its analysis/H5."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'runpod'))
from release_packaging import encode, load_files, replace_once, sha, write_release
from build_mfa_prepared_release import bounded_observation

CORE = 'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391'
H5 = '3e430635a4d714ead5d8e08352e41ff9a2b06c893d73af7f018fb43b4c37f61c'
LOCK = '58c26086546abb8b94eb6998671101d67633af426adf373cceb42862ccaf0ec4'
NAME = 'canonical_dlpc_internal_bounded_20261025_v1'


def build(core, h5, destination):
    manifest, files = load_files(core, 'release_manifest.json', CORE, 'file_sha256')
    lm_raw = (h5/'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(lm_raw) != H5:
        raise ValueError('INTERNAL_MONITOR_H5_PARENT')
    lm = json.loads(lm_raw)
    if lm['parent_core_manifest_sha256'] != CORE or lm['dependency_lock_sha256'] != LOCK:
        raise ValueError('INTERNAL_MONITOR_PARENT_PAIR')
    _, llm = load_files(h5, 'contracts/llm/dependency_lock.json', LOCK, 'files')
    bounded_observation(files)
    files['runtime_bundle/mfa_memory_watchdog.py'] = (HERE/'performance/mfa_memory_watchdog.py').read_bytes()
    files['runtime_bundle/cgroup_resources.py'] = (HERE.parents[1]/'src/voice_coach/backend_analysis/cgroup_resources.py').read_bytes()
    text = files['runtime_bundle/memory_watchdog.py'].decode()
    a = text.index('    for version, root, cap_name, used_name in (')
    b = text.index('    if not headroom:', a)
    text = text[:a] + '''    from cgroup_resources import memory_limits
    limits = memory_limits()
    for item in limits:
        headroom.append(item['available'])
        sources.append('cgroup_v'+str(item['version']))
    if limits:
        item = min(limits, key=lambda value: value['available'])
        record.update(current=item['current'], max=item['limit'],
                      cgroup_version=item['version'], hard_limit_observed=True)
''' + text[b:]
    files['runtime_bundle/memory_watchdog.py'] = text.encode()
    historical = json.loads(files['config/historical_runtime_identity.json'])
    for name in ('memory_watchdog.py', 'mfa_memory_watchdog.py', 'worker_transport.py',
                 'bounded_supervisor.py', 'mfa_sidecar.py', 'cgroup_resources.py'):
        raw = files['runtime_bundle/'+name]
        historical['source_files'].setdefault(name, dict(source='deploy/dlpc/performance/'+name)).update(
            sha256=sha(raw), copied_sha256=sha(raw), bytes=len(raw))
    historical['dlpc_derivation'] = 'Bounded diagnostics and process cgroup accounting only; numerical paths and provider policy unchanged'
    files['config/historical_runtime_identity.json'] = encode(historical)
    manifest.update(release_version=NAME, release_root=str(destination/NAME),
                    parent_manifest_sha256=CORE, deployed=False, production_modified=False)
    manifest['file_sha256'] = {n:sha(raw) for n,raw in sorted(files.items())}
    core_raw = encode(manifest);files['release_manifest.json'] = core_raw
    llm['bridge/adapter.py'] = replace_once(llm['bridge/adapter.py'].decode(),
        'PARENT = "'+CORE+'"', 'PARENT = "'+sha(core_raw)+'"').encode()
    lock = encode(dict(files={n:sha(raw) for n,raw in sorted(llm.items())}))
    lm.update(contract_version=NAME+'_h5', parent_llm_manifest_sha256=H5,
              parent_core_manifest_sha256=sha(core_raw), dependency_lock_sha256=sha(lock))
    llm_raw = encode(lm)
    llm['contracts/llm/dependency_lock.json']=lock;llm['contracts/llm/llm_contract_manifest.json']=llm_raw
    for package in (files,llm):
        for n,raw in package.items():
            if n.endswith('.py'):compile(raw,n,'exec')
    if (destination/NAME).exists() or (destination/(NAME+'_h5')).exists():
        raise ValueError('INTERNAL_MONITOR_TARGET_EXISTS')
    write_release(destination/NAME,files);write_release(destination/(NAME+'_h5'),llm)
    return dict(directory=NAME,coreManifestSha256=sha(core_raw),llmManifestSha256=sha(llm_raw),
                llmLockSha256=sha(lock),promptSha256=lm['prompt_sha256'],activated=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('core','h5','destination'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.core,args.h5,args.destination),sort_keys=True))
