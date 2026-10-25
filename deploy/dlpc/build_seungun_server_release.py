"""Derive the J406 runtime: Core-row MFA, four BLAS threads and GOP overlap."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'runpod'))
sys.path.insert(0, str(HERE / 'performance'))
from release_packaging import encode, load_files, replace_once, sha, write_release
from build_performance_release import patch_runner
from mfa_scalar_disambiguation import source as scalar_source

CORE = '55cdf30b40dd463080c2aeab8832821a2f6874a9994cc30657d8c5ef3eb6b714'
H5 = '0bc2d14c5229a932f56d0b984f357ccfcc3833775860a9857e986a36a55d2806'
LOCK = 'eb73ac8878e3154b6fc4c5b0609adb30bb0361e49048ad9b4bfbf5bde92e5e00'
NAME = 'canonical_dlpc_seungun_server_20261025_v3'


def build(core, h5, mfa_source, destination):
    optimized = scalar_source(mfa_source.read_bytes())
    manifest, files = load_files(core, 'release_manifest.json', CORE, 'file_sha256')
    lm_raw = (h5 / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(lm_raw) != H5:
        raise ValueError('SEUNGUN_SERVER_H5_PARENT')
    lm = json.loads(lm_raw)
    if lm['parent_core_manifest_sha256'] != CORE or lm['dependency_lock_sha256'] != LOCK:
        raise ValueError('SEUNGUN_SERVER_PARENT_PAIR')
    _, llm = load_files(h5, 'contracts/llm/dependency_lock.json', LOCK, 'files')
    config = json.loads(llm['contracts/llm/request_config.json'])
    if (config['model'], config['reasoning_effort'], config.get('service_tier')) != ('gpt-6-luna', 'none', 'default'):
        raise ValueError('SEUNGUN_SERVER_GPT_POLICY')
    files['source/scripts/run_canonical_batch.py'] = patch_runner(files['source/scripts/run_canonical_batch.py'])
    files['source/scripts/gop_task.py'] = (HERE / 'performance/gop_task.py').read_bytes()
    text = replace_once(files['runtime_bundle/resident_worker.py'].decode(),
        'from mfa_disambiguation import install as install_disambiguation',
        'from mfa_scalar_disambiguation import install as install_disambiguation')
    text = replace_once(text, "ctx.params['blas_num_threads']=1", "ctx.params['blas_num_threads']=4")
    files['runtime_bundle/resident_worker.py'] = text.encode()
    common = replace_once(files['runtime_bundle/demo_common.py'].decode(),
        ' return e\n',
        " if kind == 'mfa':\n"
        "  e.update(OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',NUMEXPR_NUM_THREADS='4')\n"
        ' return e\n')
    files['runtime_bundle/demo_common.py'] = common.encode()
    files['runtime_bundle/mfa_scalar_disambiguation.py'] = (HERE / 'performance/mfa_scalar_disambiguation.py').read_bytes()
    files['runtime_bundle/cgroup_resources.py'] = (HERE.parents[1] / 'src/voice_coach/backend_analysis/cgroup_resources.py').read_bytes()
    watchdog = files['runtime_bundle/memory_watchdog.py'].decode()
    start = watchdog.index('    for version, root, cap_name, used_name in (')
    end = watchdog.index('    if not headroom:', start)
    watchdog = watchdog[:start] + '''    from cgroup_resources import memory_limits
    limits = memory_limits()
    for item in limits:
        headroom.append(item['available'])
        sources.append('cgroup_v' + str(item['version']))
    if limits:
        tightest = min(limits, key=lambda item: item['available'])
        record.update(current=tightest['current'], max=tightest['limit'],
                      cgroup_version=tightest['version'], hard_limit_observed=True)
''' + watchdog[end:]
    files['runtime_bundle/memory_watchdog.py'] = watchdog.encode()
    identity = json.loads(files['config/historical_runtime_identity.json'])
    for name in ('resident_worker.py', 'demo_common.py', 'mfa_scalar_disambiguation.py',
                 'memory_watchdog.py', 'cgroup_resources.py'):
        raw = files['runtime_bundle/' + name]
        identity['source_files'].setdefault(name, dict(source='deploy/dlpc/performance/' + name)).update(
            sha256=sha(raw), copied_sha256=sha(raw), bytes=len(raw))
    identity['dlpc_derivation'] = 'J406: Core rows instead of mapped lexicon objects, MFA BLAS=4, request-owned unchanged GOP overlap'
    files['config/historical_runtime_identity.json'] = encode(identity)
    preflight = json.loads(files['config/release_preflight_identity.json'])
    preflight.update(release_blackbox_runner_sha256=sha(files['source/scripts/run_canonical_batch.py']),
                     status='SEUNGUN_SERVER_CANDIDATE_REAL_AUDIO_MEASUREMENT_PENDING')
    files['config/release_preflight_identity.json'] = encode(preflight)
    files['config/dlpc_execution_profile.json'] = encode(dict(
        profile='j406-seungun-v1', nativeDevice='cuda:0', nativeDtype='float32', nativeThreads=1,
        mfaJobs=1, mfaBlasThreads=4, gopDevice='cpu', gopDtype='float64',
        overlap='GOP with MFA after Native and full metadata', maxActive=1,
        mfaRequestState='fresh DB and features; no audio/dictionary/feature result cache'))
    manifest.update(release_version=NAME, release_root=str(destination / NAME), parent_manifest_sha256=CORE,
        deployed=False, production_modified=False, optimized_disambiguation_sha256=sha(optimized.encode()),
        verification_scope='hashes and compilation; actual audio timing and output comparison pending')
    manifest['file_sha256'] = {name: sha(raw) for name, raw in sorted(files.items())}
    core_raw = encode(manifest)
    files['release_manifest.json'] = core_raw
    llm['bridge/adapter.py'] = replace_once(llm['bridge/adapter.py'].decode(),
        'PARENT = "' + CORE + '"', 'PARENT = "' + sha(core_raw) + '"').encode()
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(llm.items())}))
    lm.update(contract_version=NAME + '_h5', parent_llm_manifest_sha256=H5,
              parent_core_manifest_sha256=sha(core_raw), dependency_lock_sha256=sha(lock))
    llm_raw = encode(lm)
    llm['contracts/llm/dependency_lock.json'] = lock
    llm['contracts/llm/llm_contract_manifest.json'] = llm_raw
    if (destination / NAME).exists() or (destination / (NAME + '_h5')).exists():
        raise ValueError('SEUNGUN_SERVER_TARGET_EXISTS')
    for package in (files, llm):
        for name, raw in package.items():
            if name.endswith('.py'):
                compile(raw, name, 'exec')
    write_release(destination / NAME, files)
    write_release(destination / (NAME + '_h5'), llm)
    return dict(directory=NAME, coreManifestSha256=sha(core_raw), llmManifestSha256=sha(llm_raw),
                llmLockSha256=sha(lock), optimizedFunctionSha256=sha(optimized.encode()),
                promptSha256=lm['prompt_sha256'], activated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('core', 'h5', 'mfa-source', 'destination'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.core, args.h5, args.mfa_source, args.destination), sort_keys=True))
