"""Build a pinned P0/P1 candidate. No inference, allowlist or deployment change."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runpod'))
from release_packaging import encode, load_files, replace_once, sha, write_release

CORE = 'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391'
H5 = 'def65f68a849a2452b0e4dde600f0ef85d99beaa97123d30d97262653ebf8e77'
LOCK = 'b605c3b9ba06bbf18fc126770b18de3d2da0d3f70c08e04873f17bc735b948bc'
PROMPT = '977c49744e7af9fc60fdc2eb13c23985692cb00dd71789d8b3c43c3813e3f1b5'


def source(name):
    return (Path(__file__).parent / 'performance' / name).read_bytes().replace(b'\r\n', b'\n')


def patch_runner(raw):
    runner = raw.decode()
    runner = replace_once(runner, 'import torch\n',
        'import torch\nfrom gop_task import GopTask\nfrom canonical_native.seungun_gop import extract as extract_gop\n')
    runner = replace_once(runner, '    active = {}\n', '    active = {}\n    gop_task = None\n')
    runner = replace_once(runner, "        marks['mfa_start'] = time.perf_counter()",
        "        gop_task.start(state)\n        marks['mfa_start'] = time.perf_counter()")
    runner = replace_once(runner, "            state = {}; marks = {'request_start': time.perf_counter()}",
        "            state = {}; marks = {'request_start': time.perf_counter()}\n"
        "            gop_task = GopTask(marks, extract_gop, vr)")
    start = runner.index('            from canonical_native.seungun_gop import extract as extract_gop\n')
    end = runner.index("            marks['core_json_done']", start)
    runner = runner[:start] + (
        "            assessment = gop_task.result(state)\n"
        "            gop_task.close()  # Join before serialization or request reuse.\n"
        "            if assessment is not None:\n"
        "                envelope['phonemeAssessment'] = assessment\n") + runner[end:]
    return replace_once(runner,
        '        pass  # Resident owner closes the client at the generation boundary.',
        '        if gop_task is not None:\n            gop_task.close()\n'
        '        # Resident owner closes the client at the generation boundary.').encode()


def patch_worker(raw):
    worker = raw.decode()
    worker = replace_once(worker, 'import time\n', 'import time\nimport mfa_timing\n')
    worker = replace_once(worker, '            feature_original=aligner.generate_features',
        '            mfa_timing.attach(aligner, record)\n            feature_original=aligner.generate_features')
    # Inclusive request preparation, including CLI callbacks and asset resolution.
    worker = replace_once(worker, "        with command.make_context('align_hf',argv[1:]) as ctx:",
        "        preparation = mfa_timing.begin()\n        with command.make_context('align_hf',argv[1:]) as ctx:")
    worker = replace_once(worker, "            record['initial_state']=",
        "            mfa_timing.finish(record, 'request_preparation', preparation)\n            record['initial_state']=")
    return worker.encode()


def build(core, h5, destination, threads=2, batch=64):
    if threads not in (1, 2, 3, 4) or batch not in (32, 64, 128):
        raise ValueError('PERFORMANCE_PROFILE_NOT_SUPPORTED')
    manifest, files = load_files(core, 'release_manifest.json', CORE, 'file_sha256')
    lm_raw = (h5 / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(lm_raw) != H5:
        raise ValueError('PERFORMANCE_H5_PARENT_IDENTITY')
    lm = json.loads(lm_raw)
    if lm['dependency_lock_sha256'] != LOCK or lm['parent_core_manifest_sha256'] != CORE:
        raise ValueError('PERFORMANCE_PARENT_TUPLE')
    _, llm = load_files(h5, 'contracts/llm/dependency_lock.json', LOCK, 'files')
    if not any(sha(raw) == PROMPT for raw in llm.values()):
        raise ValueError('PERFORMANCE_RESTORED_PROMPT_REQUIRED')
    name = f'canonical_dlpc_mfa_gop_20261007_t{threads}_b{batch}_v1'
    files['source/scripts/run_canonical_batch.py'] = patch_runner(files['source/scripts/run_canonical_batch.py'])
    files['source/scripts/canonical_native/seungun_gop.py'] = replace_once(
        source('gop_cpu.py').decode(), 'MAX_BATCH = 64', f'MAX_BATCH = {batch}').encode()
    files['source/scripts/gop_task.py'] = source('gop_task.py')
    files['scripts/analysis_budget.py'] = source('analysis_budget.py')
    files['runtime_bundle/mfa_timing.py'] = source('mfa_timing.py')
    files['runtime_bundle/resident_worker.py'] = patch_worker(files['runtime_bundle/resident_worker.py'])
    files['source/scripts/gpu_budget.py'] = replace_once(
        files['source/scripts/gpu_budget.py'].decode(), 'torch.set_num_threads(1)',
        f'torch.set_num_threads({threads})').encode()
    server = files['scripts/run_resident.py'].decode()
    server = replace_once(server, 'from run_release import prepare, APP_PY',
        'from run_release import prepare, APP_PY\nfrom analysis_budget import core_slot')
    server = replace_once(server,
        "                batch.run_batch(cases_value, output / 'candidate', False, mfa_socket, str(runtime), resident=resident)",
        "                with core_slot(request['deadline']) as queue_wait:\n"
        "                    (output / 'core-scheduling.json').write_text(json.dumps(dict(\n"
        "                        executionId=execution, queueWaitSeconds=queue_wait, globalCoreSlots=1)))\n"
        "                    batch.run_batch(cases_value, output / 'candidate', False, mfa_socket, str(runtime), resident=resident)")
    files['scripts/run_resident.py'] = server.encode()
    preflight = json.loads(files['config/release_preflight_identity.json'])
    preflight.update(release_blackbox_runner_sha256=sha(files['source/scripts/run_canonical_batch.py']),
        status='DLPC_PERFORMANCE_CANDIDATE_DEVELOPER_QA_REQUIRED')
    files['config/release_preflight_identity.json'] = encode(preflight)
    identity = json.loads(files['config/historical_runtime_identity.json'])
    record = identity['source_files']['resident_worker.py']
    record['sha256'] = sha(files['runtime_bundle/resident_worker.py'])
    record['copied_sha256'] = sha(files['runtime_bundle/resident_worker.py'])
    identity['source_files']['mfa_timing.py'] = dict(source='deploy/dlpc/performance/mfa_timing.py',
        sha256=sha(source('mfa_timing.py')), copied_sha256=sha(source('mfa_timing.py')))
    identity['dlpc_derivation'] = 'request-local timing; historical QA does not qualify the new overlap candidate'
    files['config/historical_runtime_identity.json'] = encode(identity)
    profile = dict(profile=name, parentCoreSha256=CORE, parentLlmSha256=H5,
        cpuThreads=threads, interopThreads=1, mfaJobs=1, mfaBlasThreads=1,
        gopMaxBatch=batch, gopWorkspaceEstimateBytes=64*1024**2,
        globalCoreSlots=1, overlap='CPU GOP with MFA after Native and full metadata',
        requiredLock='/run/intelligentai/analysis.lock',
        executionApproved=False, qualityVerified=False, productionAllowlisted=False)
    files['config/dlpc_execution_profile.json'] = encode(profile)
    manifest.update(release_version=name, parent_manifest_sha256=CORE,
        release_root='/workspace/intelligentai/validation/' + name,
        deployed=False, production_modified=False,
        verification_scope='source integrity and compilation; developer performance and parity QA pending')
    manifest['file_sha256'] = {n: sha(raw) for n, raw in sorted(files.items())}
    core_raw = encode(manifest)
    files['release_manifest.json'] = core_raw
    llm['bridge/adapter.py'] = replace_once(llm['bridge/adapter.py'].decode(),
        'PARENT = "' + CORE + '"', 'PARENT = "' + sha(core_raw) + '"').encode()
    lock = encode(dict(files={n: sha(raw) for n, raw in sorted(llm.items())}))
    lm.update(contract_version=name + '_h5', parent_llm_manifest_sha256=H5,
        parent_core_manifest_sha256=sha(core_raw), dependency_lock_sha256=sha(lock))
    llm_raw = encode(lm)
    llm['contracts/llm/dependency_lock.json'] = lock
    llm['contracts/llm/llm_contract_manifest.json'] = llm_raw
    for package in (files, llm):
        for filename, raw in package.items():
            if filename.endswith('.py'):
                compile(raw, filename, 'exec')
    if (destination / name).exists() or (destination / (name + '_h5')).exists():
        raise ValueError('PERFORMANCE_DESTINATION_EXISTS')
    write_release(destination / name, files)
    write_release(destination / (name + '_h5'), llm)
    return dict(profile, coreManifestSha256=sha(core_raw), llmManifestSha256=sha(llm_raw),
        llmLockSha256=sha(lock), promptSha256=PROMPT)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--h5', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--threads', type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument('--batch', type=int, choices=(32, 64, 128), default=64)
    args = parser.parse_args()
    print(json.dumps(build(args.core, args.h5, args.destination, args.threads, args.batch)))
