"""Derive a DLPC CUDA package from the merged GOP release, never activate.

GOP extraction and the GPT prompt are preserved. The new immutable tuple must
be registered in AI and Backend; CUDA sample execution is a separate gate.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runpod'))
from release_packaging import encode, load_files, replace_once, sha, write_release

CORE = '818a3c16cba39dfcfa9f1083865db7f122a3eba40bc332d85256da94d1d6fb1e'
H5 = 'f90440b764f78f32c989dc0785f41262397cc22e1d1ca48c40361189b81f86d5'


def build(core, h5, destination, device):
    manifest, files = load_files(core, 'release_manifest.json', CORE, 'file_sha256')
    parent_raw = (h5 / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(parent_raw) != H5:
        raise ValueError('DLPC_H5_PARENT_IDENTITY')
    lm = json.loads(parent_raw)
    _, llm = load_files(h5, 'contracts/llm/dependency_lock.json', lm['dependency_lock_sha256'], 'files')
    suffix = 'cuda' if device == 'cuda:0' else 'cpu'
    name = 'canonical_dlpc_gop_' + suffix + '_20261007_v1'
    runner = files['source/scripts/run_canonical_batch.py'].decode()
    runner = replace_once(runner, 'import torch\n', 'import torch\nfrom gpu_budget import configure_torch, gpu_slot\n')
    runner = replace_once(runner, 'torch.set_num_threads(8)', 'configure_torch(torch)')
    if device == 'cuda:0':
        runner = replace_once(runner, "    if not torch.backends.mkldnn.is_available():\n        raise RuntimeError('MKLDNN_UNAVAILABLE')",
            "    if not torch.cuda.is_available():\n        raise RuntimeError('DLPC_CUDA_UNAVAILABLE')")
        runner = replace_once(runner, "    model = Wav2Vec2ForCTC.from_pretrained(str(MODEL), local_files_only=True).to('cpu').float().eval()",
            "    with gpu_slot():\n        model = Wav2Vec2ForCTC.from_pretrained(str(MODEL), local_files_only=True).to('cuda:0').float().eval()")
        runner = replace_once(runner, '    with torch.inference_mode():', '    with gpu_slot(), torch.inference_mode():')
        runner = replace_once(runner, "inp.input_values.to('cpu')", "inp.input_values.to('cuda:0')")
        runner = runner.replace("'device': 'cpu'", "'device': 'cuda:0'")
    files['source/scripts/run_canonical_batch.py'] = runner.encode()
    preflight = json.loads(files['config/release_preflight_identity.json'])
    preflight['release_blackbox_runner_sha256'] = sha(runner.encode())
    preflight['status'] = 'DLPC_DERIVED_DEVELOPER_QA_REQUIRED'
    files['config/release_preflight_identity.json'] = encode(preflight)
    files['source/scripts/gpu_budget.py'] = (Path(__file__).parent / 'gpu_budget.py').read_bytes().replace(b'\r\n', b'\n')
    common = files['runtime_bundle/demo_common.py'].decode()
    for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        common = replace_once(common, key + "='2'", key + "='1'")
    files['runtime_bundle/demo_common.py'] = common.encode()
    watchdog = files['runtime_bundle/memory_watchdog.py'].decode()
    start = watchdog.index('def cgroup():')
    end = watchdog.index('def snapshot(', start)
    accounting = (Path(__file__).parent / 'memory_accounting.py').read_text()
    accounting = accounting[accounting.index('def memory_snapshot():'):].replace(
        'def memory_snapshot():', 'def cgroup():', 1)
    watchdog = watchdog[:start] + accounting + '\n\n' + watchdog[end:]
    watchdog = replace_once(watchdog, "cg['max']-cg['current']", "cg['available_bytes']")
    watchdog = replace_once(watchdog, "s['cgroup']['max']-s['cgroup']['current']", "s['cgroup']['available_bytes']")
    watchdog = replace_once(watchdog, "return 'CGROUP_HEADROOM'", "return 'MEMORY_HEADROOM'")
    files['runtime_bundle/memory_watchdog.py'] = watchdog.encode()
    config = json.loads(files['runtime_bundle/configs/supervisor.json'])
    config['other_service_reserve_bytes'] = 2 * 1024**3
    config['admission_reserve_bytes'] = config['other_service_reserve_bytes'] + config['abort_combined_rss_bytes']
    config['dlpc_basis'] = dict(hostRamGiB=11, admissionAnalyses=1,
        accounting='minimum observed MemAvailable and finite cgroup headroom',
        historicalRssLimitsPreserved=True, hardCapEstablished=False)
    files['runtime_bundle/configs/supervisor.json'] = encode(config)
    identity = json.loads(files['config/historical_runtime_identity.json'])
    for relative in ('demo_common.py', 'memory_watchdog.py', 'configs/supervisor.json'):
        identity['source_files'][relative]['sha256'] = sha(files['runtime_bundle/' + relative])
        identity['source_files'][relative]['copied_sha256'] = sha(files['runtime_bundle/' + relative])
    identity['dlpc_derivation'] = 'thread and measured memory admission changes; historical QA is not DLPC QA'
    files['config/historical_runtime_identity.json'] = encode(identity)
    # Serialize cold preparation; the 11 GiB host must not load both generations.
    server = files['scripts/run_resident.py'].decode()
    start = server.index("        with (generation / 'mfa.log').open('xb') as log:")
    end = server.index('        # Import and model preparation', start)
    launch = server[start:end]
    server = server[:start] + server[end:]
    server = replace_once(server, "        while not (generation / 'host_ready.json').exists():", launch + "        while not (generation / 'host_ready.json').exists():")
    server = server.replace('# Import and model preparation overlap the independent MFA bootstrap.', '# Load Native before starting MFA to bound cold RAM demand.')
    files['scripts/run_resident.py'] = server.encode()
    profile = dict(profile='dlpc-4cpu', device=device, dtype='float32', cpuThreads=1,
                   activeAnalyses=1, standbyGenerations=0, parentCoreSha256=CORE,
                   executionApproved=False, qualityVerified=False)
    files['config/dlpc_execution_profile.json'] = encode(profile)
    manifest.update(release_version=name, parent_manifest_sha256=CORE,
                    release_root='/workspace/intelligentai/releases/' + name,
                    deployed=False, production_modified=False,
                    verification_scope='source integrity and compilation; developer QA pending')
    manifest['file_sha256'] = {n: sha(raw) for n, raw in sorted(files.items())}
    core_raw = encode(manifest)
    files['release_manifest.json'] = core_raw
    llm['bridge/adapter.py'] = replace_once(llm['bridge/adapter.py'].decode(), 'PARENT = "' + CORE + '"', 'PARENT = "' + sha(core_raw) + '"').encode()
    lock = encode(dict(files={n: sha(raw) for n, raw in sorted(llm.items())}))
    lm.update(contract_version=name + '_h5', parent_llm_manifest_sha256=H5,
              parent_core_manifest_sha256=sha(core_raw), dependency_lock_sha256=sha(lock))
    llm_raw = encode(lm)
    llm['contracts/llm/dependency_lock.json'] = lock
    llm['contracts/llm/llm_contract_manifest.json'] = llm_raw
    for package in (files, llm):
        for filename, raw in package.items():
            if filename.endswith('.py'): compile(raw, filename, 'exec')
    if (destination / name).exists() or (destination / (name + '_h5')).exists():
        raise ValueError('DLPC_DERIVATIVE_ALREADY_EXISTS')
    write_release(destination / name, files)
    write_release(destination / (name + '_h5'), llm)
    return dict(profile, coreManifestSha256=sha(core_raw), llmManifestSha256=sha(llm_raw), llmLockSha256=sha(lock))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--h5', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--device', choices=('cpu', 'cuda:0'), required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.core, args.h5, args.destination, args.device)))
