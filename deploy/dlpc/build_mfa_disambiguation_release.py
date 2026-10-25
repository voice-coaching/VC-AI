"""Derive a fixed disambiguation core/H5 pair; no alignment or activation."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runpod'))
from release_packaging import encode,load_files,replace_once,sha,write_release
from performance.mfa_disambiguation import optimized_source,MODULE_SHA,FUNCTION_SHA,REVISION

CORE = '755667629b5d603910ff2646daa807bf7757e2d3f3ca29845e265e2e4dc79984'
H5 = '11d920cd07d3ed583885d6ea79d1bc2ee0a65903a42c98448cca87a37b4a5e94'
LOCK = 'fb322f24a263d385accb6b48f9f841bbaa8dd866b68b7129c5189b854ede553b'
NAME = 'canonical_dlpc_mfa_disambiguation_20261025_v1'


def build(core,h5,mfa_source,destination):
    optimized = optimized_source(mfa_source.read_bytes())
    manifest,files = load_files(core,'release_manifest.json',CORE,'file_sha256')
    lm_raw = (h5/'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(lm_raw)!=H5:raise ValueError('MFA_DISAMBIGUATION_H5_PARENT')
    lm = json.loads(lm_raw)
    if lm['parent_core_manifest_sha256']!=CORE or lm['dependency_lock_sha256']!=LOCK:
        raise ValueError('MFA_DISAMBIGUATION_PARENT_PAIR')
    _,llm = load_files(h5,'contracts/llm/dependency_lock.json',LOCK,'files')
    config = json.loads(llm['contracts/llm/request_config.json'])
    if (config['model'],config['reasoning_effort'],config.get('service_tier'))!=('gpt-6-luna','none','default'):
        raise ValueError('MFA_DISAMBIGUATION_GPT_POLICY')
    text = files['runtime_bundle/resident_worker.py'].decode()
    text = replace_once(text,'BASE_MEMORY,recovery,IDENTITY,PREPARED\n',
                        'BASE_MEMORY,recovery,IDENTITY,PREPARED,DISAMBIGUATION\n')
    text = replace_once(text,'    model_path=str(model.directory);del model\n',
        '    model_path=str(model.directory);del model\n'
        '    from mfa_disambiguation import install as install_disambiguation\n'
        '    DISAMBIGUATION=install_disambiguation(PretrainedAligner)\n')
    text = replace_once(text,"'prepared_dependencies':PREPARED.receipt,",
                        "'disambiguation_optimization':DISAMBIGUATION,'prepared_dependencies':PREPARED.receipt,")
    text = replace_once(text,"    mfa_trace.bind(record, O/'reports')\n",
        "    mfa_trace.bind(record, O/'reports')\n"
        "    record['disambiguation_optimization']=dict(DISAMBIGUATION)\n")
    files['runtime_bundle/resident_worker.py'] = text.encode()
    helper = (Path(__file__).parent/'performance/mfa_disambiguation.py').read_bytes()
    files['runtime_bundle/mfa_disambiguation.py'] = helper
    identity = json.loads(files['config/historical_runtime_identity.json'])
    record = identity['source_files']['resident_worker.py']
    record.update(sha256=sha(text.encode()),copied_sha256=sha(text.encode()))
    if 'bytes' in record:record['bytes']=len(text.encode())
    identity['source_files']['mfa_disambiguation.py']=dict(source='deploy/dlpc/performance/mfa_disambiguation.py',
        sha256=sha(helper),copied_sha256=sha(helper))
    identity['dlpc_derivation']='Equivalent disambiguation prefix reduction; original query/order/writes and both calls retained'
    files['config/historical_runtime_identity.json']=encode(identity)
    manifest.update(release_version=NAME,release_root=str(destination/NAME),parent_manifest_sha256=CORE,
        deployed=False,production_modified=False,verification_scope='hashes, syntax and fixed-source equivalence analysis; quality unmeasured',
        disambiguation_revision=REVISION,disambiguation_module_sha256=MODULE_SHA,
        disambiguation_function_sha256=FUNCTION_SHA,optimized_disambiguation_sha256=sha(optimized.encode()))
    manifest['file_sha256']={name:sha(raw) for name,raw in sorted(files.items())}
    core_raw=encode(manifest);files['release_manifest.json']=core_raw
    llm['bridge/adapter.py']=replace_once(llm['bridge/adapter.py'].decode(),
        'PARENT = "'+CORE+'"','PARENT = "'+sha(core_raw)+'"').encode()
    lock=encode(dict(files={name:sha(raw) for name,raw in sorted(llm.items())}))
    lm.update(contract_version=NAME+'_h5',parent_llm_manifest_sha256=H5,
              parent_core_manifest_sha256=sha(core_raw),dependency_lock_sha256=sha(lock))
    llm_raw=encode(lm)
    llm['contracts/llm/dependency_lock.json']=lock
    llm['contracts/llm/llm_contract_manifest.json']=llm_raw
    if (destination/NAME).exists() or (destination/(NAME+'_h5')).exists():raise ValueError('MFA_DISAMBIGUATION_TARGET_EXISTS')
    for package in (files,llm):
        for name,raw in package.items():
            if name.endswith('.py'):compile(raw,name,'exec')
    write_release(destination/NAME,files);write_release(destination/(NAME+'_h5'),llm)
    return dict(directory=NAME,coreManifestSha256=sha(core_raw),llmManifestSha256=sha(llm_raw),
                llmLockSha256=sha(lock),optimizedFunctionSha256=sha(optimized.encode()),
                promptSha256=lm['prompt_sha256'],activated=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('core','h5','mfa-source','destination'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.core,args.h5,args.mfa_source,args.destination),sort_keys=True))
