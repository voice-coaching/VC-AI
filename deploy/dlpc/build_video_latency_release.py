"""Port pinned static MFA preparation to internal lane; never activate it."""
import argparse
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE.parent/'runpod'))
from release_packaging import encode, load_files, replace_once, sha, write_release

CORE='46410550bcab94739b6a62a8a7366f9ad97ec1ded47a0076c34570ce41654806'
H5='4a73c128f2b81027e7e59ce9f56015ca76abfe4dd0d1764a875b4d34c97ede20'
NAME='canonical_video_latency_internal_20261111_v1'


def build(core,h5,destination):
    manifest,files=load_files(core,'release_manifest.json',CORE,'file_sha256')
    raw=(h5/'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(raw)!=H5:raise ValueError('VIDEO_LATENCY_PARENT_H5')
    lm=json.loads(raw)
    if lm['parent_core_manifest_sha256']!=CORE:raise ValueError('VIDEO_LATENCY_PARENT_PAIR')
    _,llm=load_files(h5,'contracts/llm/dependency_lock.json',lm['dependency_lock_sha256'],'files')
    worker=files['runtime_bundle/resident_worker.py'].decode()
    worker=replace_once(worker,'import mfa_timing\n','import mfa_timing\nfrom mfa_prepared import PreparedDependencies\n')
    worker=replace_once(worker,'BASE_MEMORY,recovery,IDENTITY\n','BASE_MEMORY,recovery,IDENTITY,PREPARED,STATIC_MFA\n')
    worker=replace_once(worker,'    model_path=str(model.directory);del model\n',
        '    model_path=str(model.directory);del model\n'
        '    from montreal_forced_aligner.utils import check_third_party,canary_kaldi_bins\n'
        '    PREPARED=PreparedDependencies(check_third_party,canary_kaldi_bins,config.USE_POSTGRES)\n'
        "    STATIC_MFA={'installed':False}\n"
        "    if (PretrainedAligner.__module__,PretrainedAligner.__name__)==('direct_adapter','DirectAligner'):\n"
        '        from mfa_scalar_disambiguation import install\n'
        '        STATIC_MFA=install(PretrainedAligner)\n')
    worker=replace_once(worker,"            check_third_party();warnings.simplefilter('ignore')\n",
        "            PREPARED.verify(config.USE_POSTGRES);warnings.simplefilter('ignore')\n"
        "            record['prepared_dependencies']=dict(PREPARED.receipt,reused=True)\n"
        "            record['static_mfa']=dict(STATIC_MFA)\n")
    files['runtime_bundle/resident_worker.py']=worker.encode()
    names=('mfa_timing.py','mfa_prepared.py','mfa_disambiguation.py','mfa_scalar_disambiguation.py')
    for name in names:files['runtime_bundle/'+name]=(HERE/'performance'/name).read_bytes()
    identity=json.loads(files['config/historical_runtime_identity.json'])
    for name in ('resident_worker.py',*names):
        content=files['runtime_bundle/'+name]
        identity['source_files'].setdefault(name,dict(source='deploy/dlpc/performance/'+name)).update(
            sha256=sha(content),copied_sha256=sha(content),bytes=len(content))
    identity['dlpc_derivation']='Prepared tool validation and pinned Core-row disambiguation; fresh DB/features; unchanged search/threads'
    files['config/historical_runtime_identity.json']=encode(identity)
    manifest.update(release_version=NAME,release_root=str(destination/NAME),parent_manifest_sha256=CORE,
        deployed=False,production_modified=False,verification_scope='Fresh audio/visual parity and performance pending')
    manifest['file_sha256']={n:sha(content) for n,content in sorted(files.items())}
    core_raw=encode(manifest);files['release_manifest.json']=core_raw;core_pin=sha(core_raw)
    adapter=(ROOT/'deploy/runpod/harnesses/six_dimension_feedback_only_bridge.py').read_bytes()
    if adapter.count(b'__CORE_MANIFEST__')!=1:raise ValueError('VIDEO_LATENCY_ADAPTER_BOUNDARY')
    llm['bridge/adapter.py']=adapter.replace(b'__CORE_MANIFEST__',core_pin.encode())
    lock=encode(dict(files={n:sha(content) for n,content in sorted(llm.items())}))
    lm.update(contract_version=NAME+'_h5',parent_llm_manifest_sha256=H5,
        parent_core_manifest_sha256=core_pin,dependency_lock_sha256=sha(lock),productionActivated=False)
    llm['contracts/llm/dependency_lock.json']=lock;llm['contracts/llm/llm_contract_manifest.json']=encode(lm)
    for target,package in ((destination/NAME,files),(destination/(NAME+'_h5'),llm)):
        if target.exists():raise ValueError('VIDEO_LATENCY_REQUIRES_NEW_DIRECTORY')
        for name,content in package.items():
            if name.endswith('.py'):compile(content,name,'exec')
    write_release(destination/NAME,files);write_release(destination/(NAME+'_h5'),llm)
    return dict(coreRoot=str(destination/NAME),h5Root=str(destination/(NAME+'_h5')),coreSha256=core_pin,
        manifestSha256=sha(llm['contracts/llm/llm_contract_manifest.json']),lockSha256=sha(lock),
        promptSha256=lm['prompt_sha256'],productionActivated=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('core','h5','destination'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();print(json.dumps(build(args.core,args.h5,args.destination)))
