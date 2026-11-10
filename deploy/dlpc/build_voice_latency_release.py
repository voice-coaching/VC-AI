"""Derive GOP-overlap/feedback-only candidates; keep original media and scores."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / 'runpod'))
sys.path.insert(0, str(HERE))
from release_packaging import encode, load_files, replace_once, sha, write_release
from build_performance_release import patch_runner

PARENTS = {
    'direct': ('880c8d77649024063e664e7bb9b80b26db07e016612bac664d578637e44e1806',
               '5aa736e70fd9ef2602d21f55d891a126cb63235103d2ab13e1ffcf41cc3534e7'),
    'internal': ('dae36e72646904bcdefc081654f67c06f80ff0565c384d439a5692e51ab7026b',
                 'be11bd3693996c72adf15eb5ace75408d41f8200549bcd052fb282a1f69bee24'),
}


def build(core, h5, destination, lane):
    core_pin, llm_pin = PARENTS[lane]
    manifest, core_files = load_files(core, 'release_manifest.json', core_pin, 'file_sha256')
    lm_raw = (h5 / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(lm_raw) != llm_pin:
        raise ValueError('VOICE_LATENCY_PARENT_H5')
    lm = json.loads(lm_raw)
    if lm['parent_core_manifest_sha256'] != core_pin:
        raise ValueError('VOICE_LATENCY_PARENT_PAIR')
    _, llm = load_files(h5, 'contracts/llm/dependency_lock.json', lm['dependency_lock_sha256'], 'files')
    packages = []
    selected_core = core
    if lane == 'internal':
        core_files['source/scripts/run_canonical_batch.py'] = patch_runner(core_files['source/scripts/run_canonical_batch.py'])
        core_files['source/scripts/gop_task.py'] = (HERE / 'performance/gop_task.py').read_bytes()
        worker = core_files['runtime_bundle/resident_worker.py'].decode()
        worker = 'import mfa_timing\n' + worker
        worker = replace_once(worker, "        with command.make_context('align_hf',argv[1:]) as ctx:",
            "        preparation = mfa_timing.begin()\n        with command.make_context('align_hf',argv[1:]) as ctx:")
        worker = replace_once(worker, "            record['initial_state']=",
            "            mfa_timing.finish(record, 'request_preparation', preparation)\n            record['initial_state']=")
        worker = replace_once(worker, '            feature_original=aligner.generate_features',
            '            mfa_timing.attach(aligner, record)\n            feature_original=aligner.generate_features')
        core_files['runtime_bundle/resident_worker.py'] = worker.encode()
        core_files['runtime_bundle/mfa_timing.py'] = (HERE / 'performance/mfa_timing.py').read_bytes()
        identity = json.loads(core_files['config/historical_runtime_identity.json'])
        for name in ('resident_worker.py', 'mfa_timing.py'):
            raw = core_files['runtime_bundle/' + name]
            identity['source_files'].setdefault(name, {'source': 'deploy/dlpc/performance/' + name}).update(
                sha256=sha(raw), copied_sha256=sha(raw), bytes=len(raw))
        identity['dlpc_derivation'] = 'Request-owned original GOP overlaps original MFA; timing only; no numerical/thread/cache change'
        core_files['config/historical_runtime_identity.json'] = encode(identity)
        preflight = json.loads(core_files['config/release_preflight_identity.json'])
        preflight.update(release_blackbox_runner_sha256=sha(core_files['source/scripts/run_canonical_batch.py']),
                         status='VOICE_LATENCY_PARITY_AND_PERFORMANCE_PENDING')
        core_files['config/release_preflight_identity.json'] = encode(preflight)
        name = 'canonical_voice_latency_internal_20261110_v1'
        selected_core = destination / name
        manifest.update(release_version=name, release_root=str(selected_core), parent_manifest_sha256=core_pin,
                        deployed=False, production_modified=False,
                        verification_scope='GOP/MFA scheduling and bounded timing; actual parity/performance pending')
        manifest['file_sha256'] = {name: sha(raw) for name, raw in sorted(core_files.items())}
        core_raw = encode(manifest)
        core_files['release_manifest.json'] = core_raw
        core_pin = sha(core_raw)
        packages.append((selected_core, core_files))
    adapter = llm['bridge/adapter.py'].decode()
    adapter = replace_once(adapter, 'PARENT = "' + PARENTS[lane][0] + '"', 'PARENT = "' + core_pin + '"')
    before = 'native_rubric_policy=self.native_rubric_policy(), visual=visual)'
    count = adapter.count(before)
    if count != 2:
        raise ValueError('VOICE_LATENCY_ADAPTER_CALL_BOUNDARY')
    adapter = adapter.replace(before, before[:-1] + ', feedback_only=True)')
    llm['bridge/adapter.py'] = adapter.encode()
    llm['bridge/pipeline.py'] = (ROOT / 'src/voice_coach/six_dimension/pipeline.py').read_bytes()
    llm['contracts/llm/llm_system_prompt.txt'] += (ROOT / 'configs/coaching/feedback_only_output_v1.txt').read_bytes()
    name = f'canonical_voice_latency_{lane}_20261110_v1_h5'
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(llm.items())}))
    parent = json.loads(lm_raw)
    parent.update(contract_version=name, parent_llm_manifest_sha256=llm_pin,
                  parent_core_manifest_sha256=core_pin, dependency_lock_sha256=sha(lock),
                  prompt_sha256=sha(llm['contracts/llm/llm_system_prompt.txt']),
                  productionActivated=False, developerQaVerified=False)
    llm['contracts/llm/dependency_lock.json'] = lock
    llm['contracts/llm/llm_contract_manifest.json'] = encode(parent)
    packages.append((destination / name, llm))
    for target, files in packages:
        if target.exists():
            raise ValueError('VOICE_LATENCY_REQUIRES_NEW_DIRECTORY')
        for relative, raw in files.items():
            if relative.endswith('.py'):
                compile(raw, relative, 'exec')
    for target, files in packages:
        write_release(target, files)
    return dict(lane=lane, coreRoot=str(selected_core), h5Root=str(destination/name),
                coreSha256=core_pin, manifestSha256=sha(llm['contracts/llm/llm_contract_manifest.json']),
                lockSha256=sha(lock), promptSha256=parent['prompt_sha256'], productionActivated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('core', 'h5', 'destination'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--lane', choices=tuple(PARENTS), required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.core, args.h5, args.destination, args.lane)))
