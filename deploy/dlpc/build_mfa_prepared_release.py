"""Build pinned prepared-MFA core/H5 pairs; no inference or release activation."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runpod'))
from release_packaging import encode, load_files, replace_once, sha, write_release

CORE = 'c9c7d893ebbf6e5a2fa7762c3fa2e72b33b4ada391c945ce564c59d4d4a5bf82'
H5 = '1d227a6654060357c04fb1a592826bba2bfc464cc536837e2f142cc612573211'
LOCK = '06907765cad8d14300e41fa6cf8709e32e11edf89c9bf021b945e534348e9579'


def worker(raw, threads):
    text = replace_once(raw.decode(), 'import mfa_trace\n',
                        'import mfa_trace\nfrom mfa_prepared import PreparedDependencies\n')
    text = replace_once(text, 'BASE_MEMORY,recovery,IDENTITY\n', 'BASE_MEMORY,recovery,IDENTITY,PREPARED\n')
    text = replace_once(text, '    model_path=str(model.directory);del model\n',
        '    model_path=str(model.directory);del model\n'
        '    from montreal_forced_aligner.utils import check_third_party,canary_kaldi_bins\n'
        '    PREPARED=PreparedDependencies(check_third_party,canary_kaldi_bins,config.USE_POSTGRES)\n')
    text = replace_once(text, "            'startup_s':time.perf_counter()-PROCESS_ENTRY,",
                        "            'prepared_dependencies':PREPARED.receipt,'startup_s':time.perf_counter()-PROCESS_ENTRY,")
    text = replace_once(text, "    mfa_trace.bind(record, O/'reports')\n",
        "    mfa_trace.bind(record, O/'reports')\n"
        "    record['prepared_dependencies']=dict(PREPARED.receipt,reused=True)\n")
    text = replace_once(text, "        with mfa_trace.cli_context(command, 'align_hf', argv[1:]) as ctx:\n",
        "        with mfa_trace.cli_context(command, 'align_hf', argv[1:]) as ctx:\n"
        f"            ctx.params['blas_num_threads']={threads}\n")
    text = replace_once(text,
        "            with mfa_trace.span('third_party_check'):\n"
        "                check_third_party();warnings.simplefilter('ignore')\n",
        "            with mfa_trace.span('prepared_dependencies_verify'):\n"
        "                PREPARED.verify(config.USE_POSTGRES);warnings.simplefilter('ignore')\n")
    # The request still constructs a fresh aligner and database and performs all
    # original workflow/export/cleanup calls. No dictionary or audio result cache.
    return text.encode()


def bounded_observation(files):
    name = 'runtime_bundle/memory_watchdog.py'
    text = files[name].decode()
    marker = 'class Watchdog:\n'
    if text.count(marker) != 1:raise ValueError('MFA_WATCHDOG_SOURCE_CONTEXT')
    text = text[:text.index(marker)] + (
        'from mfa_memory_watchdog import BoundedWatchdog\n\n'
        'class Watchdog(BoundedWatchdog):\n'
        '    def __init__(self,pgid,cfg,path):\n'
        '        super().__init__(pgid,cfg,path,snapshot,violation)\n')
    files[name] = text.encode()
    name = 'runtime_bundle/worker_transport.py'
    text = replace_once(files[name].decode(), "        self.watch.phase='READY'\n        return result\n",
        "        self.watch.phase='READY'\n"
        "        result['sampled_peak']=self.watch.peak(payload['request_id'])\n        return result\n")
    text = replace_once(text, "'watchdog_error':self.watch.error,",
                        "'watchdog_error':self.watch.error,'watchdog_errno':self.watch.error_errno,")
    files[name] = text.encode()
    name = 'runtime_bundle/bounded_supervisor.py'
    files[name] = replace_once(files[name].decode(), "'worker':result,'shutdown_s':0}",
        "'worker':result,'sampled_peak':result.pop('sampled_peak'),'shutdown_s':0}").encode()
    name = 'runtime_bundle/mfa_sidecar.py'
    text = replace_once(files[name].decode(), "max(x['tree_rss_bytes'] for x in mon.samples)",
                        'mon.peak_tree_rss_bytes')
    text = replace_once(text,
        '  # Read recorded samples, not a result cache. Worker state/result freshly generated.\n'
        "  samples=[json.loads(z) for z in (O/'logs/g1_memory.jsonl').read_text().splitlines() if z]\n"
        "  active=[z for z in samples if z['phase']==rid]\n"
        "  rec['sampled_peak']={k:max((sum_memory(z)[k] for z in active if sum_memory(z)[k] is not None),default=None) for k in ['rss_bytes','pss_bytes','uss_bytes']}\n",
        '  # Transport captured this request\'s sampled peak before any generation drain.\n')
    files[name] = text.encode()


def build(core, h5, destination, threads):
    if threads not in (1, 2, 4):raise ValueError('MFA_THREAD_CANDIDATE_INVALID')
    manifest, files = load_files(core, 'release_manifest.json', CORE, 'file_sha256')
    lm_raw = (h5 / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(lm_raw) != H5:raise ValueError('MFA_PREPARED_H5_PARENT')
    lm = json.loads(lm_raw)
    if lm['parent_core_manifest_sha256'] != CORE or lm['dependency_lock_sha256'] != LOCK:
        raise ValueError('MFA_PREPARED_PARENT_PAIR')
    _, llm = load_files(h5, 'contracts/llm/dependency_lock.json', LOCK, 'files')
    config = json.loads(llm['contracts/llm/request_config.json'])
    if (config['model'], config['reasoning_effort'], config.get('service_tier')) != ('gpt-6-luna','none','default'):
        raise ValueError('MFA_PREPARED_GPT_POLICY')
    files['runtime_bundle/resident_worker.py'] = worker(files['runtime_bundle/resident_worker.py'], threads)
    bounded_observation(files)
    helpers = Path(__file__).parent / 'performance'
    for name in ('mfa_prepared.py','mfa_memory_watchdog.py','mfa_trace.py'):
        files['runtime_bundle/' + name] = (helpers / name).read_bytes()
    if threads != 1:
        name = 'runtime_bundle/demo_common.py'
        files[name] = replace_once(files[name].decode(), ' return e\n',
            " if kind=='mfa':\n"
            f"  e.update(OMP_NUM_THREADS='{threads}',MKL_NUM_THREADS='{threads}',"
            f"OPENBLAS_NUM_THREADS='{threads}',NUMEXPR_NUM_THREADS='{threads}')\n return e\n").encode()
    identity = json.loads(files['config/historical_runtime_identity.json'])
    changed = [name for name, content in files.items() if manifest['file_sha256'].get(name) != sha(content)]
    for name in changed:
        if not name.startswith('runtime_bundle/'):continue
        relative = name.removeprefix('runtime_bundle/')
        record = identity['source_files'].setdefault(relative, dict(source='prepared-MFA derivative'))
        record.update(sha256=sha(files[name]),copied_sha256=sha(files[name]))
        if 'bytes' in record:record['bytes'] = len(files[name])
    identity['dlpc_derivation'] = 'Prepared dependencies; bounded diagnostics; fresh per-request alignment state'
    files['config/historical_runtime_identity.json'] = encode(identity)
    name = f'canonical_dlpc_mfa_prepared_20261025_t{threads}_v2'
    manifest.update(release_version=name, release_root=str(destination / name), parent_manifest_sha256=CORE,
        deployed=False, production_modified=False,
        verification_scope='source hashes and syntax only; no inference or quality validation',
        mfa_preparation_revision='mfa-prepared-v1',mfa_blas_threads=threads,
        candidate_requires_performance_observation=threads != 1)
    manifest['file_sha256'] = {n:sha(content) for n,content in sorted(files.items())}
    core_raw = encode(manifest)
    files['release_manifest.json'] = core_raw
    llm['bridge/adapter.py'] = replace_once(llm['bridge/adapter.py'].decode(),
        'PARENT = "' + CORE + '"','PARENT = "' + sha(core_raw) + '"').encode()
    lock = encode(dict(files={n:sha(content) for n,content in sorted(llm.items())}))
    lm.update(contract_version=name+'_h5', parent_llm_manifest_sha256=H5,
              parent_core_manifest_sha256=sha(core_raw),dependency_lock_sha256=sha(lock))
    h5_raw = encode(lm)
    llm['contracts/llm/dependency_lock.json'] = lock
    llm['contracts/llm/llm_contract_manifest.json'] = h5_raw
    if (destination/name).exists() or (destination/(name+'_h5')).exists():raise ValueError('MFA_PREPARED_TARGET_EXISTS')
    for package in (files,llm):
        for path,content in package.items():
            if path.endswith('.py'):compile(content,path,'exec')
    write_release(destination/name,files)
    write_release(destination/(name+'_h5'),llm)
    return dict(directory=name,coreManifestSha256=sha(core_raw),llmManifestSha256=sha(h5_raw),
                llmLockSha256=sha(lock),promptSha256=lm['prompt_sha256'],mfaThreads=threads,activated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core',type=Path,required=True)
    parser.add_argument('--h5',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--mfa-threads',type=int,choices=(1,2,4),default=1)
    args = parser.parse_args()
    print(json.dumps(build(args.core,args.h5,args.destination,args.mfa_threads),sort_keys=True))
