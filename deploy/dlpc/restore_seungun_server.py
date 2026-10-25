"""Install the fixed J406 optimization; rollback restores the previous MFA pair."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

import host_identity
import restore_mfa_disambiguation as previous

base = previous.base
APP = base.APP
PAYLOAD = Path(__file__).resolve().parent / 'seungun-server-20261025'
SELECTOR = PAYLOAD / 'selected'
BACKUP = Path('/opt/intelligentai/ops/seungun-server-20261025')
CORE = Path('/workspace/intelligentai/validation/canonical_dlpc_seungun_server_20261025_v3')
LLM = CORE.with_name(CORE.name + '_h5')
CORE_SHA = 'fb597319347d106e4a8e59d7bb6a254f031a7e85a21b87d8c349763829b0152d'
LLM_SHA = 'c41b820326cd6a95a9ef6ff502b400be0a078ee9d51c5cce99fdabca5c60ee1d'
LOCK_SHA = 'ecc76bcc2ca505f4b717d7a49add8b556fef427c9a1bc61f84204f11ce572639'
PATCH = 'seungun-server-20261025'
AFTER = {'src/voice_coach/backend_analysis/canonical_harness.py': '599cded35def4e6abd394b5892df69938a2c9b5c1d7cf6496b35dffd15035a82', 'src/voice_coach/backend_analysis/native_execution.py': '4d77d8a6d23be4d33367eae3bd57995d16cc3cf7f5de6b9e82a611cf9f10d571', 'docs/contracts/runpod_result_v4.schema.json': 'a892bdaecec5075011bb30bb0ee2a7a8b4093700825fa9d99df01c7ba9da234d'}
FILES = {name:(tuple(before)+(after,),AFTER.get(name,after)) for name,(before,after) in previous.FILES.items()}
ROLLBACK = {name:after for name,(_,after) in previous.FILES.items()}
FILES['src/voice_coach/backend_analysis/resource_budget.py'] = (('777714da6fe1570362e4d491d7f3faa9060a1dab82db1601db3e4bec18e26905',), 'ec36b767f19f29a1203b2abda8683cacc8793a1d08961e9da0c82bae9f21ea29')
FILES['src/voice_coach/backend_analysis/cgroup_resources.py'] = ((None,), '40984a4cb9ea3efb80caa08aa852293c0a78489f01e2986b082341e66d201060')
ROLLBACK['src/voice_coach/backend_analysis/resource_budget.py'] = '777714da6fe1570362e4d491d7f3faa9060a1dab82db1601db3e4bec18e26905'
ROLLBACK['src/voice_coach/backend_analysis/cgroup_resources.py'] = None


def assets():
    packages = []
    raw = base.pinned(PAYLOAD/'core/release_manifest.json',CORE_SHA)
    core = json.loads(raw)
    files = {name:base.pinned(PAYLOAD/'core'/name,digest) for name,digest in core['file_sha256'].items()}
    files['release_manifest.json'] = raw
    packages.append((CORE,files))
    raw = base.pinned(PAYLOAD/'h5/contracts/llm/llm_contract_manifest.json',LLM_SHA)
    lm = json.loads(raw)
    if lm['parent_core_manifest_sha256'] != CORE_SHA or lm['dependency_lock_sha256'] != LOCK_SHA:
        raise ValueError('SEUNGUN_SERVER_PAIR_DIGEST')
    lock = base.pinned(PAYLOAD/'h5/contracts/llm/dependency_lock.json',LOCK_SHA)
    files = {name:base.pinned(PAYLOAD/'h5'/name,digest) for name,digest in json.loads(lock)['files'].items()}
    files.update({'contracts/llm/llm_contract_manifest.json':raw,'contracts/llm/dependency_lock.json':lock})
    packages.append((LLM,files))
    for target,files in packages:
        if target.exists():
            for name,content in files.items():base.pinned(target/name,base.sha(content))
            continue
        target.mkdir(mode=0o755);target.chmod(0o755)
        for name,content in files.items():
            dest = target/name;dest.parent.mkdir(parents=True,mode=0o755,exist_ok=True)
            for parent in dest.parents:
                if parent==target:break
                parent.chmod(0o755)
            base.replace(dest,content,0o440)


def rollback(values):
    base.stopped()
    if values.get('AI_DIRECT_CORE_ROOT') not in (str(CORE),str(previous.CORE)) or values.get('AI_DIRECT_LLM_ROOT') not in (str(LLM),str(previous.LLM)):
        raise ValueError('SEUNGUN_SERVER_ROLLBACK_SELECTION')
    changes = []
    for name,digest in ROLLBACK.items():
        if base.sha(base.owned(APP/name,optional=True)) not in (digest,FILES[name][1]):
            raise ValueError('SEUNGUN_SERVER_ROLLBACK_REVISION')
        changes.append((APP/name,base.pinned(PAYLOAD/'rollback'/name,digest) if digest else None))
    previous.assets()
    for path,raw in changes:
        if raw is None:path.unlink(missing_ok=True)
        else:base.replace(path,raw)
    previous.prepared.m0.settings(values,str(previous.CORE),str(previous.LLM))
    meta=Path('/opt/intelligentai/deployment.json');doc=json.loads(base.owned(meta))
    doc.get('operationalPatches',{}).pop(PATCH,None)
    base.replace(meta,(json.dumps(doc,sort_keys=True)+'\n').encode())
    base.replace(SELECTOR,b'disabled\n',0o600,0)
    base.replace(previous.SELECTOR,b'optimized\n',0o600,0)
    print('Previous disambiguation runtime and recovery selection restored; analysis remains stopped')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback',action='store_true');args=parser.parse_args()
    if host_identity.require_root().recovery!='dlpc-j406-20261016':raise ValueError('SEUNGUN_SERVER_J406_REQUIRED')
    os.umask(0o077)
    sys.path.insert(0,str(APP/'deploy/dlpc'))
    from launch import private_settings
    values=private_settings(base.ENV)
    if args.rollback:
        rollback(values);return
    if base.owned(SELECTOR)!=b'optimized\n':raise ValueError('SEUNGUN_SERVER_SELECTION_REQUIRED')
    if values.get('AI_DIRECT_CORE_ROOT') not in (None,str(previous.prepared.m0.CORE),str(previous.prepared.CORE),str(previous.CORE),str(CORE)):
        raise ValueError('SEUNGUN_SERVER_EXISTING_CORE')
    if values.get('AI_DIRECT_LLM_ROOT') not in (None,base.LOW,str(base.LLM),str(previous.prepared.m0.LLM),str(previous.prepared.LLM),str(previous.LLM),str(LLM)):
        raise ValueError('SEUNGUN_SERVER_EXISTING_H5')
    changes=[]
    for name,(before,after) in FILES.items():
        old=base.owned(APP/name,optional=True)
        if base.sha(old)==after:continue
        if base.sha(old) not in before:raise ValueError('SEUNGUN_SERVER_RUNTIME_REVISION')
        raw=base.pinned(PAYLOAD/name,after)
        if name.endswith('.py'):compile(raw,name,'exec')
        changes.append((APP/name,raw))
    assets()
    if changes or values.get('AI_DIRECT_CORE_ROOT')!=str(CORE) or values.get('AI_DIRECT_LLM_ROOT')!=str(LLM):
        base.stopped();BACKUP.mkdir(mode=0o700,exist_ok=True)
        state=BACKUP/'state.json'
        if not state.exists():
            # A deterministic previous-pair rollback is available even after a cold restore
            # whose B2 baseline predates these optimizations.
            for name,digest in ROLLBACK.items():
                if digest:base.pinned(PAYLOAD/'rollback'/name,digest)
            base.replace(state,(json.dumps(dict(rollbackSha256=ROLLBACK,preparedAt=time.time()),sort_keys=True)+'\n').encode(),0o600,0)
        for path,raw in changes:base.replace(path,raw)
        previous.prepared.m0.settings(values,str(CORE),str(LLM))
    meta=Path('/opt/intelligentai/deployment.json');doc=json.loads(base.owned(meta))
    doc.setdefault('operationalPatches',{})[PATCH]=dict(stage='SEUNGUN_SERVER_CORE_ROWS_GOP_OVERLAP',mfaThreads=4,
        coreManifestSha256=CORE_SHA,llmManifestSha256=LLM_SHA,llmLockSha256=LOCK_SHA,
        files={name:dict(afterSha256=after) for name,(_,after) in FILES.items()},
        verificationScope='syntax, hashes and readiness; performance/quality unmeasured')
    base.replace(meta,(json.dumps(doc,sort_keys=True)+'\n').encode())
    print('J406 Core rows, four-thread MFA and GOP overlap installed; request state and GPT policy preserved')


if __name__=='__main__':main()
