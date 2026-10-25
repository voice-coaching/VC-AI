"""Derive a timing-only core and matching Luna none H5; no analysis or activation."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runpod'))
from release_packaging import encode, load_files, replace_once, sha, write_release

CORE = 'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391'
LLM = 'a0be7ab1bd5738c14798f15e6c72f82558106f717798ad7e5da90f52e2dc5691'
LOCK = 'ed9bab85e9fbbda03196c77ebc7d191f382dfb108670a6fb296f15f83f061869'
NAME = 'canonical_dlpc_mfa_trace_20261025_v1'


def wrap(source, first, last, label):
    if source.count(first) != 1 or source.count(last) != 1:
        raise ValueError('MFA_TRACE_SOURCE_CONTEXT')
    start = source.index(first)
    end = source.index(last, start) + len(last)
    block = source[start:end]
    indent = first[:len(first)-len(first.lstrip())]
    replacement = indent + 'with mfa_trace.span(' + repr(label) + '):\n'
    replacement += ''.join('    ' + line if line.strip() else line for line in block.splitlines(keepends=True))
    return source[:start] + replacement + source[end:]


def worker(raw):
    text = replace_once(raw.decode(), 'import time\n', 'import time\nimport mfa_trace\n')
    text = replace_once(text, 'def execute(req):', '@mfa_trace.request_trace\ndef execute(req):')
    text = wrap(text,
        '    rid,root,argv=validate(req);SEEN.add(rid);lifecycle.CURRENT_REQUEST=rid\n',
        '    rid,root,argv=validate(req);SEEN.add(rid);lifecycle.CURRENT_REQUEST=rid\n', 'request_validation')
    text = wrap(text, '    for p,identity in IDENTITY.items():\n',
        "        if (st.st_ino,st.st_size,st.st_mtime_ns)!=identity:raise RuntimeError('MFA identity changed')\n", 'identity_check')
    line = '    before=snapshot();started=time.perf_counter();wall_start=time.time_ns();aligner=None;ctx=None\n'
    text = wrap(text, line, line, 'initial_snapshot')
    record = "    record={'request_id':rid,'worker_pid':os.getpid(),'argv':argv,'root':str(root),'before':before,'started_ns':wall_start,'workflow_executed':False}\n"
    text = replace_once(text, record, record + "    mfa_trace.bind(record, O/'reports')\n")
    text = wrap(text, "        state=root/'state';state.mkdir()\n",
        "        config.MEMORY=type(BASE_MEMORY)(location=str(state/'joblib_cache'),verbose=0)\n", 'request_state_setup')
    text = replace_once(text, "        with command.make_context('align_hf',argv[1:]) as ctx:",
        "        with mfa_trace.cli_context(command, 'align_hf', argv[1:]) as ctx:")
    for line, label in (
        ('            config.load_configuration();config.update_configuration(ctx.params)\n', 'configuration_load'),
        ("            check_third_party();warnings.simplefilter('ignore')\n", 'third_party_check'),
        ("            g2p_model=model.get_g2p_model(kw['dialect']) if kw['use_g2p'] else None\n", 'g2p_resolve'),
        ("            aligner=PretrainedAligner(corpus_directory=kw['corpus_directory'].absolute(),acoustic_model=model.acoustic_model,dictionary=dictionary,g2p_model=g2p_model,**extra)\n", 'aligner_construct'),
        ("            aligner.align();record['alignment_workflow_s']=time.perf_counter()-t\n", 'alignment_workflow'),
    ):
        text = wrap(text, line, line, label)
    text = wrap(text, "            extra=PretrainedAligner.parse_parameters(kw.get('config_path'),ctx.params,ctx.args)\n",
        "            if kw['no_tokenization']:extra['language']='unknown'\n", 'parameter_parse')
    text = replace_once(text, "            model=kw['model_id'];dictionary=model.get_dictionary_model(kw['dialect'])\n",
        "            model=kw['model_id']\n            with mfa_trace.span('dictionary_resolve'):\n                dictionary=model.get_dictionary_model(kw['dialect'])\n")
    text = replace_once(text, "            record['initial_state']=", "            mfa_trace.attach(aligner)\n            record['initial_state']=")
    text = wrap(text, '            try:\n                import sqlite3\n',
        "                record['db_close_probe']='FAILED';record['db_close_error']=traceback.format_exc()\n", 'db_close_probe')
    text = wrap(text, '        if ctx is not None:ctx.params.clear()\n',
        '            else:os.environ[k]=v\n', 'request_state_reset')
    text = wrap(text, '        gc.collect()\n', '        gc.collect()\n', 'gc_collect')
    text = wrap(text, "        record['after_close']=snapshot()\n", "        record['after_close']=snapshot()\n", 'post_cleanup_snapshot')
    text = wrap(text, "        record['lifecycle_gate']=lifecycle.gate(before,record['after_close'])\n",
        '            shutil.rmtree(root)\n', 'lifecycle_and_temp_removal')
    text = wrap(text, "        record['after']=snapshot()\n", "        record['after']=snapshot()\n", 'final_snapshot')
    line = "        (O/'reports'/(rid+'_worker.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2,default=str))\n"
    text = wrap(text, line, line, 'worker_report_write')
    # Include the original cleanup order, DB/config reset, gate and report I/O.
    text = wrap(text, '        t=time.perf_counter()\n        if aligner is not None:\n',
        '    ' + line, 'cleanup_total')
    compile(text, 'resident_worker.py', 'exec')
    return text.encode()


def build(core, llm, destination):
    manifest, files = load_files(core, 'release_manifest.json', CORE, 'file_sha256')
    raw = (llm / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(raw) != LLM:
        raise ValueError('MFA_TRACE_LLM_PARENT')
    lm = json.loads(raw)
    if lm['parent_core_manifest_sha256'] != CORE or lm['dependency_lock_sha256'] != LOCK:
        raise ValueError('MFA_TRACE_PARENT_PAIR')
    _, h5 = load_files(llm, 'contracts/llm/dependency_lock.json', LOCK, 'files')
    config = json.loads(h5['contracts/llm/request_config.json'])
    if (config['model'], config['reasoning_effort'], config.get('service_tier')) != ('gpt-6-luna', 'none', 'default'):
        raise ValueError('MFA_TRACE_GPT_SETTINGS_CHANGED')
    files['runtime_bundle/resident_worker.py'] = worker(files['runtime_bundle/resident_worker.py'])
    helper = (Path(__file__).parent / 'performance/mfa_trace.py').read_bytes()
    files['runtime_bundle/mfa_trace.py'] = helper
    identity = json.loads(files['config/historical_runtime_identity.json'])
    record = identity['source_files']['resident_worker.py']
    record.update(sha256=sha(files['runtime_bundle/resident_worker.py']),
                  copied_sha256=sha(files['runtime_bundle/resident_worker.py']))
    if 'bytes' in record:record['bytes'] = len(files['runtime_bundle/resident_worker.py'])
    identity['source_files']['mfa_trace.py'] = dict(source='deploy/dlpc/performance/mfa_trace.py',
        sha256=sha(helper), copied_sha256=sha(helper))
    identity['dlpc_derivation'] = 'MFA request timing only; algorithm, resources, cleanup and three-request budget preserved'
    files['config/historical_runtime_identity.json'] = encode(identity)
    manifest.update(release_version=NAME, release_root=str(destination / NAME), parent_manifest_sha256=CORE,
        deployed=False, production_modified=False,
        verification_scope='immutable source hashes and syntax; no inference or quality validation',
        mfa_observation_revision='mfa-request-spans-v1')
    manifest['file_sha256'] = {n: sha(content) for n, content in sorted(files.items())}
    core_raw = encode(manifest)
    files['release_manifest.json'] = core_raw
    h5['bridge/adapter.py'] = replace_once(h5['bridge/adapter.py'].decode(),
        'PARENT = "' + CORE + '"', 'PARENT = "' + sha(core_raw) + '"').encode()
    lock = encode(dict(files={n: sha(content) for n, content in sorted(h5.items())}))
    lm.update(contract_version=NAME + '_h5', parent_llm_manifest_sha256=LLM,
              parent_core_manifest_sha256=sha(core_raw), dependency_lock_sha256=sha(lock))
    llm_raw = encode(lm)
    h5['contracts/llm/dependency_lock.json'] = lock
    h5['contracts/llm/llm_contract_manifest.json'] = llm_raw
    for package in (files, h5):
        for filename, content in package.items():
            if filename.endswith('.py'):compile(content, filename, 'exec')
    if (destination / NAME).exists() or (destination / (NAME + '_h5')).exists():
        raise ValueError('MFA_TRACE_RELEASE_EXISTS')
    write_release(destination / NAME, files)
    write_release(destination / (NAME + '_h5'), h5)
    return dict(directory=NAME, coreManifestSha256=sha(core_raw), llmManifestSha256=sha(llm_raw),
                llmLockSha256=sha(lock), promptSha256=lm['prompt_sha256'], activated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--h5', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.core, args.h5, args.destination), sort_keys=True))
