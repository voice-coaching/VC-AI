"""Derive pinned PCM/provenance/FST-cache core and compact Luna H5 assets."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'runpod'))
from release_packaging import encode, load_files, replace_once, sha, write_release

CORE = 'fb597319347d106e4a8e59d7bb6a254f031a7e85a21b87d8c349763829b0152d'
H5 = 'c41b820326cd6a95a9ef6ff502b400be0a078ee9d51c5cce99fdabca5c60ee1d'
LOCK = 'ecc76bcc2ca505f4b717d7a49add8b556fef427c9a1bc61f84204f11ce572639'
NAME = 'canonical_dlpc_pipeline_latency_20261025_v4'


def build(core, h5, destination):
    manifest, files = load_files(core, 'release_manifest.json', CORE, 'file_sha256')
    lm_raw = (h5 / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(lm_raw) != H5:
        raise ValueError('PIPELINE_LATENCY_H5_PARENT')
    lm = json.loads(lm_raw)
    if lm['parent_core_manifest_sha256'] != CORE or lm['dependency_lock_sha256'] != LOCK:
        raise ValueError('PIPELINE_LATENCY_PARENT_PAIR')
    _, llm = load_files(h5, 'contracts/llm/dependency_lock.json', LOCK, 'files')
    cfg = json.loads(llm['contracts/llm/request_config.json'])
    if (cfg['model'], cfg['reasoning_effort'], cfg['service_tier']) != ('gpt-6-luna', 'none', 'default'):
        raise ValueError('PIPELINE_LATENCY_GPT_POLICY')
    files['source/scripts/pipeline_prepared.py'] = (HERE / 'performance/pipeline_prepared.py').read_bytes()
    runner = files['source/scripts/run_canonical_batch.py'].decode()
    preparation = '''def prepare_static_pipeline():
    from pipeline_prepared import install
    config = json.loads((RECOVERY/'configs/candidate_alignment_config.json').read_text())
    config['final_pronunciation'] = {'enabled': False}
    config['pronunciation'] = {'enabled': False}
    config['paths'].update(project_root=str(SOURCE), work_dir=str(SOURCE/'metadata_only_unused_work'),
                           output_dir=str(SOURCE/'metadata_only_unused_output'),
                           mfa_root_dir=str(SOURCE/'metadata_only_unused_mfa'))
    return install(pipeline_mod, PocConfig.model_validate(config))


'''
    runner = replace_once(runner, 'def main():\n', preparation + 'def main():\n')
    runner = replace_once(runner, '            def preparer(*a):\n',
        "            def preparer(*a):\n                marks['audio_preparation_start'] = time.perf_counter()\n")
    runner = replace_once(runner, '            def provenance(runtime_cfg, *args, **kw):\n',
        "            def provenance(runtime_cfg, *args, **kw):\n                marks['provenance_start'] = time.perf_counter()\n")
    runner = replace_once(runner,
        '                return base_deps.collect_provenance(PocConfig.model_validate(z), *args, **kw)\n',
        "                try:\n                    return base_deps.collect_provenance(PocConfig.model_validate(z), *args, **kw)\n"
        "                finally:\n                    marks['provenance_done'] = time.perf_counter()\n")
    files['source/scripts/run_canonical_batch.py'] = runner.encode()
    server = replace_once(files['scripts/run_resident.py'].decode(),
        '        batch.load_g2p()  # The same engine is reused; request observation state is separate.\n',
        '        batch.load_g2p()  # The same engine is reused; request observation state is separate.\n'
        "        static_pipeline = batch.prepare_static_pipeline()\n"
        "        print(json.dumps(dict(event='static_pipeline_ready', **static_pipeline)), flush=True)\n")
    files['scripts/run_resident.py'] = server.encode()
    worker = replace_once(files['runtime_bundle/resident_worker.py'].decode(),
        'BASE_MEMORY,recovery,IDENTITY,PREPARED,DISAMBIGUATION\n',
        'BASE_MEMORY,recovery,IDENTITY,PREPARED,DISAMBIGUATION,LEXICON_CACHE\n')
    worker = replace_once(worker, '    DISAMBIGUATION=install_disambiguation(PretrainedAligner)\n',
        '    DISAMBIGUATION=install_disambiguation(PretrainedAligner)\n'
        '    from mfa_lexicon_cache import install as install_lexicon_cache\n'
        '    LEXICON_CACHE=install_lexicon_cache(PretrainedAligner)\n')
    worker = replace_once(worker, "            'disambiguation_optimization':DISAMBIGUATION,",
        "            'lexicon_cache':LEXICON_CACHE,'disambiguation_optimization':DISAMBIGUATION,")
    files['runtime_bundle/resident_worker.py'] = worker.encode()
    files['runtime_bundle/mfa_lexicon_cache.py'] = (HERE / 'performance/mfa_lexicon_cache.py').read_bytes()
    identity = json.loads(files['config/historical_runtime_identity.json'])
    for name in ('resident_worker.py', 'mfa_lexicon_cache.py'):
        raw = files['runtime_bundle/' + name]
        identity['source_files'].setdefault(name, dict(source='deploy/dlpc/performance/' + name)).update(
            sha256=sha(raw), copied_sha256=sha(raw), bytes=len(raw))
    identity['dlpc_derivation'] = 'Strict PCM copy; startup tool versions; full-input-keyed request-owned FST copies; numerical alignment workflow retained'
    files['config/historical_runtime_identity.json'] = encode(identity)
    preflight = json.loads(files['config/release_preflight_identity.json'])
    preflight.update(release_blackbox_runner_sha256=sha(files['source/scripts/run_canonical_batch.py']),
                     status='PIPELINE_LATENCY_STATIC_PREFLIGHT_ONLY')
    files['config/release_preflight_identity.json'] = encode(preflight)
    files['config/dlpc_execution_profile.json'] = encode(dict(
        profile='j406-pipeline-latency-v2', nativeDevice='cuda:0', nativeDtype='float32',
        nativeThreads=1, mfaJobs=1, mfaBlasThreads=4, gopDevice='cpu', gopDtype='float64',
        maxActive=1, pcmPreparationRevision='pcm-copy-and-tool-versions-v1',
        lexiconFstCache=dict(maxEntries=1, estimatedMaxBytes=64*1024**2, requestOwnedCopies=True),
        mfaRequestState='fresh DB, text normalization and features; no audio/feature/alignment result cache'))
    manifest.update(release_version=NAME, release_root=str(destination / NAME), parent_manifest_sha256=CORE,
        deployed=False, production_modified=False,
        verification_scope='syntax and hashes; no inference or performance measurement')
    manifest['file_sha256'] = {name: sha(raw) for name, raw in sorted(files.items())}
    core_raw = encode(manifest)
    files['release_manifest.json'] = core_raw

    prompt = llm['contracts/llm/llm_system_prompt.txt'].decode()
    prompt = prompt[:prompt.index('JSON 객체 하나만 출력한다.')]
    prompt = prompt.replace('scoring.status=UNSCORABLE', 'levels=null, score=null')
    prompt = prompt.replace('최대 4문장', '최대 2문장').replace('최대 4개 선택', '최대 2개 선택').replace('4문장 한도', '2문장 한도')
    prompt = prompt.replace('아래 scoring/feedback JSON 계약', '아래 compact JSON 계약')
    prompt += '''JSON 객체 하나만 출력한다. 최상위 키는 levels, score, feedback뿐이다.
levels는 RUBRIC의 9개 항목 순서에 정확히 대응하는 0~4 정수 또는 null의 배열이다.
score는 위 총점 공식으로 직접 판단·계산한 0~100 수다. 채점 불가능하면 levels와 score를 모두 null로 둔다.
feedback은 근거가 있는 단어의 구체적인 연습을 한국어로 최대 두 줄, 두 문장으로 간결하게 작성한다.
문장 수를 채우려고 오류·교정을 만들지 않는다. 항목별 긴 JSON key나 설명·Markdown·JSON 밖 텍스트는 출력하지 않는다.

RUBRIC:
'''
    prompt += llm['contracts/llm/scoring_rubric.json'].decode()
    llm['contracts/llm/llm_system_prompt.txt'] = prompt.encode()
    llm['contracts/llm/compact_output.schema.json'] = (HERE.parent / 'runpod/harnesses/compact_gop_output.schema.json').read_bytes()
    adapter = llm['bridge/adapter.py'].decode()
    adapter = replace_once(adapter, 'PARENT = "' + CORE + '"', 'PARENT = "' + sha(core_raw) + '"')
    adapter = replace_once(adapter,
        '            RUBRIC=self.read("scoring_rubric.json"))', '            )')
    adapter = replace_once(adapter, '            candidate = strict_json(choice["message"]["content"])\n',
        '            candidate = strict_json(choice["message"]["content"])\n'
        '            jsonschema.Draft202012Validator(self.read("compact_output.schema.json")).validate(candidate)\n'
        '            candidate = expand_compact_output(candidate, self.read("scoring_rubric.json"))\n')
    adapter += '\n' + (HERE.parent / 'runpod/harnesses/compact_gop_output.py').read_text()
    llm['bridge/adapter.py'] = adapter.encode()
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(llm.items())}))
    lm.update(contract_version=NAME + '_h5', parent_llm_manifest_sha256=H5,
              parent_core_manifest_sha256=sha(core_raw), dependency_lock_sha256=sha(lock),
              prompt_sha256=sha(prompt.encode()), latency_stage='prepared_compact',
              feedback_format='at most two evidence-bound practice lines; same rubric')
    llm_raw = encode(lm)
    llm['contracts/llm/dependency_lock.json'] = lock
    llm['contracts/llm/llm_contract_manifest.json'] = llm_raw
    for package in (files, llm):
        for name, raw in package.items():
            if name.endswith('.py'):
                compile(raw, name, 'exec')
            elif name.endswith('.json'):
                json.loads(raw)
    if (destination / NAME).exists() or (destination / (NAME + '_h5')).exists():
        raise ValueError('PIPELINE_LATENCY_TARGET_EXISTS')
    write_release(destination / NAME, files)
    write_release(destination / (NAME + '_h5'), llm)
    return dict(directory=NAME, coreManifestSha256=sha(core_raw), llmManifestSha256=sha(llm_raw),
                llmLockSha256=sha(lock), promptSha256=sha(prompt.encode()), activated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('core', 'h5', 'destination'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.core, args.h5, args.destination), sort_keys=True))
