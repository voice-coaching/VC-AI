"""Install the closed pipeline/monitor pairs; rollback restores the previous installation."""
import argparse
import json
import os
from pathlib import Path
import sys

import host_identity
import restore_seungun_server as previous

base = previous.base
APP = base.APP
PAYLOAD = Path(__file__).resolve().parent / 'pipeline-latency-20261025'
SELECTOR = PAYLOAD / 'selected'
PATCH = 'pipeline-latency-20261025'
BACKUP = Path('/opt/intelligentai/ops/pipeline-latency-20261025')
CORE = Path('/workspace/intelligentai/validation/canonical_dlpc_pipeline_latency_20261025_v4')
LLM = CORE.with_name(CORE.name+'_h5')
CORE_SHA = '9e29fbc9a7fcc039a6d4fa36b323cdcca61431100f3786cc9a55e41b53c4a4ba'
LLM_SHA = 'c3d78433cdb0d9ca999ab7b407464084a95cbf8f6d931a80ffd9610656ea3b68'
LOCK_SHA = '2864be8b43b850136c7b41ffc9242265b5faa5890c48c59847f807051fc44114'
INTERNAL_CORE = Path('/workspace/intelligentai/validation/canonical_dlpc_internal_bounded_20261025_v1')
INTERNAL_LLM = INTERNAL_CORE.with_name(INTERNAL_CORE.name+'_h5')
INTERNAL_CORE_SHA = 'f562484d436e42626114e16afa2bcaa782c1abbfc3cc3cdbd5c3b3cf030b920a'
INTERNAL_LLM_SHA = '25531c9e52c1088c9cdceee82a5e2d57d52c46da4b4cd93cac75eea87d6ade60'
INTERNAL_LOCK_SHA = '570f92d23aa19f241e4ac73b35fccc406761bdfd2c097d1c83f3bb66c821d742'
OLD_INTERNAL_CORE = '/workspace/intelligentai/releases/canonical_dlpc_gop_cuda_20261007_v2'
OLD_INTERNAL_LLM = '/workspace/intelligentai/validation/canonical_dlpc_gop_announcer_20261007_v2_h5'
AFTER = {'docs/contracts/runpod_result_v4.schema.json': '8b32c55f5b64faa046f09db23db274c95799ce197e213bdff6b3df3ac1407b41',
 'src/voice_coach/backend_analysis/canonical_bridge.py': 'fe692362df6a7e39fd1d9d12dd75df678e6258601568eb511e152c7de56c7180',
 'src/voice_coach/backend_analysis/canonical_codec.py': 'fb26d8a2b3e3161324ff747f7d76e6a4fafe2cdf3b53d6316808c1be7d813752',
 'src/voice_coach/backend_analysis/canonical_harness.py': 'bc886f089a856aa2fb8a93f39193ec07d021fb64cd6e3c8d5bc946ae8a3f18c5',
 'src/voice_coach/backend_analysis/canonical_producer.py': 'ac728c32ea25c1421fe1232bd4d8524023a7c8563ed895a60a1722cc96a47a5f',
 'src/voice_coach/backend_analysis/canonical_provider_observation.py': '161cd31f74bc074ce854dc51ae28866dc71ecdd6044d0de320780e47458e1571',
 'src/voice_coach/backend_analysis/canonical_resident.py': '3c1398727bd81bdd2d23d9e51e7a984de8b3ba684bd3c81d2c2e6929c221f9df',
 'src/voice_coach/backend_analysis/canonical_transport.py': '611d0499e45d3eb5b1a647a117de296344b5738e7b1f17b6cef4af4c911710c4',
 'src/voice_coach/backend_analysis/cgroup_resources.py': '40984a4cb9ea3efb80caa08aa852293c0a78489f01e2986b082341e66d201060',
 'src/voice_coach/backend_analysis/native_execution.py': '008344b87a316e5805040979a18f4b4b1b5100a86baa8dd9f1e4744055fd7df9',
 'src/voice_coach/backend_analysis/resident_health.py': '37f57a765b3ebe560ca82f97f95564335b8330e1eb1ad31b053309ad4178986c',
 'src/voice_coach/backend_analysis/resource_budget.py': 'ec36b767f19f29a1203b2abda8683cacc8793a1d08961e9da0c82bae9f21ea29',
 'src/voice_coach/direct_analysis/executor.py': '5e62bae8af1a0a5ff4f3640c96fbc6cf53ab382a9432521a3a4ea1fab12f75f8',
 'src/voice_coach/direct_analysis/executor_pool.py': 'd498cb5ba24cda10d784607f45c5760d8a8ed3ace1ef166b75abb6ee25176626',
 'src/voice_coach/direct_analysis/release_selection.py': '2ed29bcb230dcbf5d3dced783659c9f1f1855e2c3f319c3abbc9c5f6f2d79bf7',
 'src/voice_coach/direct_analysis/service.py': '62ba96c6f7a63a3c80883819e4c47f9ae7e674dc632a64dda347f4c5233d771b',
 'src/voice_coach/direct_analysis/worker.py': '2edaa258b942279355d1f30d4b3abc7228ae36c046236652f98b6c1e05479ee7'}
EXTRA_BEFORE = {'src/voice_coach/backend_analysis/canonical_bridge.py': '06a3da5fe72c97b35f0d3e396aae9d5b0fd1d858452e15959d9e58c25f674e9b',
 'src/voice_coach/backend_analysis/canonical_codec.py': 'a1a3023b581997433946e6ec950dfe8dc9ff7dfd1e97133ba84b26bc0176c178',
 'src/voice_coach/backend_analysis/canonical_producer.py': '2d24fd595ceeedcb4515acb7ba934b3458328d705af8aaabb6ae901d13a35954',
 'src/voice_coach/backend_analysis/canonical_resident.py': '5eb5a13e765c6e5d8f9b72cf3cb23a2f7f7a55b773951c14898ad77eaaf736f7',
 'src/voice_coach/direct_analysis/executor.py': None,
 'src/voice_coach/direct_analysis/executor_pool.py': None}
FILES = {name:(tuple(before)+(old,), AFTER[name]) for name,(before,old) in previous.FILES.items()}
ROLLBACK = {name:old for name,(_,old) in previous.FILES.items()}
for name,before in EXTRA_BEFORE.items():
    FILES[name] = ((before,),AFTER[name])
    ROLLBACK[name] = before


def pair_assets(prefix,core,llm,core_sha,llm_sha,lock_sha):
    raw = base.pinned(PAYLOAD/prefix/'core/release_manifest.json',core_sha)
    doc = json.loads(raw)
    packages = [(core,{n:base.pinned(PAYLOAD/prefix/'core'/n,d) for n,d in doc['file_sha256'].items()} | {'release_manifest.json':raw})]
    raw = base.pinned(PAYLOAD/prefix/'h5/contracts/llm/llm_contract_manifest.json',llm_sha)
    lm = json.loads(raw)
    if lm['parent_core_manifest_sha256']!=core_sha or lm['dependency_lock_sha256']!=lock_sha:
        raise ValueError('PIPELINE_PAIR_IDENTITY')
    lock = base.pinned(PAYLOAD/prefix/'h5/contracts/llm/dependency_lock.json',lock_sha)
    package = {n:base.pinned(PAYLOAD/prefix/'h5'/n,d) for n,d in json.loads(lock)['files'].items()}
    package.update({'contracts/llm/dependency_lock.json':lock,'contracts/llm/llm_contract_manifest.json':raw})
    packages.append((llm,package))
    for target,files in packages:
        if target.exists():
            for n,raw in files.items():base.pinned(target/n,base.sha(raw))
            continue
        target.mkdir(mode=0o755);target.chmod(0o755)
        for n,raw in files.items():
            path=target/n;path.parent.mkdir(parents=True,mode=0o755,exist_ok=True)
            for parent in path.parents:
                if parent==target:break
                parent.chmod(0o755)
            base.replace(path,raw,0o440)


def assets():
    pair_assets('direct',CORE,LLM,CORE_SHA,LLM_SHA,LOCK_SHA)
    pair_assets('internal',INTERNAL_CORE,INTERNAL_LLM,INTERNAL_CORE_SHA,INTERNAL_LLM_SHA,INTERNAL_LOCK_SHA)


def settings(values,rollback=False):
    values['AI_DIRECT_CORE_ROOT'] = str(previous.CORE if rollback else CORE)
    values['AI_DIRECT_LLM_ROOT'] = str(previous.LLM if rollback else LLM)
    values['AI_CANONICAL_CORE_ROOT'] = OLD_INTERNAL_CORE if rollback else str(INTERNAL_CORE)
    values['AI_CANONICAL_LLM_ROOT'] = OLD_INTERNAL_LLM if rollback else str(INTERNAL_LLM)
    base.replace(base.ENV,''.join(k+'='+v+'\n' for k,v in sorted(values.items())).encode(),0o600,0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback',action='store_true');args=parser.parse_args()
    if host_identity.require_root().recovery!='dlpc-j406-20261016':raise ValueError('PIPELINE_HOST_IDENTITY')
    sys.path.insert(0,str(APP/'deploy/dlpc'))
    from launch import private_settings
    values=private_settings(base.ENV)
    if values.get('AI_DIRECT_CORE_ROOT') not in (None,str(previous.CORE),str(CORE)):
        raise ValueError('PIPELINE_CURRENT_DIRECT_SELECTION')
    if values.get('AI_CANONICAL_CORE_ROOT') not in (OLD_INTERNAL_CORE,str(INTERNAL_CORE)):
        raise ValueError('PIPELINE_CURRENT_INTERNAL_SELECTION')
    if values.get('AI_DIRECT_LLM_ROOT') not in (None,str(previous.LLM),str(LLM)):
        raise ValueError('PIPELINE_CURRENT_DIRECT_H5')
    if values.get('AI_CANONICAL_LLM_ROOT') not in (OLD_INTERNAL_LLM,str(INTERNAL_LLM)):
        raise ValueError('PIPELINE_CURRENT_INTERNAL_H5')
    if args.rollback:
        base.stopped();previous.assets()
        changes=[]
        for n,d in ROLLBACK.items():
            if base.sha(base.owned(APP/n,optional=True)) not in (d,AFTER[n]):raise ValueError('PIPELINE_ROLLBACK_REVISION')
            changes.append((APP/n,base.pinned(PAYLOAD/'rollback'/n,d) if d else None))
        for path,raw in changes:
            if raw is None:path.unlink(missing_ok=True)
            else:base.replace(path,raw)
        settings(values,True)
        base.replace(SELECTOR,b'disabled\n',0o600,0)
        base.replace(previous.SELECTOR,b'optimized\n',0o600,0)
        meta=Path('/opt/intelligentai/deployment.json');doc=json.loads(base.owned(meta));doc.get('operationalPatches',{}).pop(PATCH,None)
        base.replace(meta,(json.dumps(doc,sort_keys=True)+'\n').encode())
        print('Previous direct and internal pairs and runtime restored; analysis remains stopped')
        return
    if base.owned(SELECTOR)!=b'optimized\n':raise ValueError('PIPELINE_SELECTION_REQUIRED')
    changes=[]
    for n,(before,after) in FILES.items():
        digest=base.sha(base.owned(APP/n,optional=True))
        if digest==after:continue
        if digest not in before:raise ValueError('PIPELINE_RUNTIME_REVISION')
        raw=base.pinned(PAYLOAD/n,after)
        if n.endswith('.py'):compile(raw,n,'exec')
        changes.append((APP/n,raw))
    assets()
    if changes or values.get('AI_DIRECT_CORE_ROOT')!=str(CORE) or values.get('AI_CANONICAL_CORE_ROOT')!=str(INTERNAL_CORE):
        base.stopped()
        for n,d in ROLLBACK.items():
            if d:base.pinned(PAYLOAD/'rollback'/n,d)
        BACKUP.mkdir(mode=0o700,exist_ok=True)
        state=BACKUP/'state.json'
        if not state.exists():base.replace(state,(json.dumps(dict(rollbackSha256=ROLLBACK),sort_keys=True)+'\n').encode(),0o600,0)
        for path,raw in changes:base.replace(path,raw)
        settings(values)
    meta=Path('/opt/intelligentai/deployment.json');doc=json.loads(base.owned(meta))
    doc.setdefault('operationalPatches',{})[PATCH]=dict(stage='PREPARED_PIPELINE_COMPACT',
        coreManifestSha256=CORE_SHA,llmManifestSha256=LLM_SHA,internalCoreManifestSha256=INTERNAL_CORE_SHA,
        runtimeSha256=AFTER,verificationScope='static identity, source, dependencies and readiness; no new inference or latency measurement')
    base.replace(meta,(json.dumps(doc,sort_keys=True)+'\n').encode())
    print('Prepared pipeline, bounded internal diagnostics and worker generations installed')


if __name__=='__main__':main()
