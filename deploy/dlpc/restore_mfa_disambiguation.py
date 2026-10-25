"""Install the fixed prefix reduction; rollback restores prepared MFA v2."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

import host_identity
import restore_mfa_prepared as prepared

base = prepared.base
APP = base.APP
PAYLOAD = Path(__file__).resolve().parent / 'mfa-disambiguation-20261025'
SELECTOR = PAYLOAD / 'selected'
BACKUP = Path('/opt/intelligentai/ops/mfa-disambiguation-20261025')
CORE = Path('/workspace/intelligentai/validation/canonical_dlpc_mfa_disambiguation_20261025_v1')
LLM = CORE.with_name(CORE.name + '_h5')
CORE_SHA = '55cdf30b40dd463080c2aeab8832821a2f6874a9994cc30657d8c5ef3eb6b714'
LLM_SHA = '0bc2d14c5229a932f56d0b984f357ccfcc3833775860a9857e986a36a55d2806'
LOCK_SHA = 'eb73ac8878e3154b6fc4c5b0609adb30bb0361e49048ad9b4bfbf5bde92e5e00'
PATCH = 'mfa-disambiguation-20261025'
AFTER = {
    'src/voice_coach/backend_analysis/canonical_harness.py': '6c4f79f916c480dcfe205f66ddbdd77782ef62fd29789782e0b196e7df248638',
    'src/voice_coach/backend_analysis/native_execution.py': '02c03bc3b5b22ded1791d043927e01f01f536521a64c44ede0c0c167eb1dfbab',
    'src/voice_coach/backend_analysis/resident_health.py': '37f57a765b3ebe560ca82f97f95564335b8330e1eb1ad31b053309ad4178986c',
    'docs/contracts/runpod_result_v4.schema.json': '706a4aed0e20cdea323e76c5029578a576920e7c5c8f6d7aa463a2b959bcae1f',
}
FILES = {name:(tuple(before)+(after,),AFTER.get(name,after)) for name,(before,after) in prepared.FILES.items()}
ROLLBACK = {name:after for name,(_,after) in prepared.FILES.items()}


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
        raise ValueError('MFA_DISAMBIGUATION_PAIR_DIGEST')
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
    if values.get('AI_DIRECT_CORE_ROOT') not in (str(CORE),str(prepared.CORE)) or values.get('AI_DIRECT_LLM_ROOT') not in (str(LLM),str(prepared.LLM)):
        raise ValueError('MFA_DISAMBIGUATION_ROLLBACK_SELECTION')
    changes = []
    for name,digest in ROLLBACK.items():
        if base.sha(base.owned(APP/name,optional=True)) not in (digest,FILES[name][1]):
            raise ValueError('MFA_DISAMBIGUATION_ROLLBACK_REVISION')
        changes.append((APP/name,base.pinned(PAYLOAD/'rollback'/name,digest)))
    prepared.assets()
    for path,raw in changes:base.replace(path,raw)
    prepared.m0.settings(values,str(prepared.CORE),str(prepared.LLM))
    meta=Path('/opt/intelligentai/deployment.json');doc=json.loads(base.owned(meta))
    doc.get('operationalPatches',{}).pop(PATCH,None)
    base.replace(meta,(json.dumps(doc,sort_keys=True)+'\n').encode())
    base.replace(SELECTOR,b'disabled\n',0o600,0)
    base.replace(prepared.SELECTOR,b'prepared\n',0o600,0)
    print('Prepared v2 runtime, health, direct pair and recovery selection restored; analysis remains stopped')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback',action='store_true');args=parser.parse_args()
    if host_identity.require_root().recovery!='dlpc-j406-20261016':raise ValueError('MFA_DISAMBIGUATION_J406_REQUIRED')
    os.umask(0o077)
    sys.path.insert(0,str(APP/'deploy/dlpc'))
    from launch import private_settings
    values=private_settings(base.ENV)
    if args.rollback:
        rollback(values);return
    if base.owned(SELECTOR)!=b'optimized\n':raise ValueError('MFA_DISAMBIGUATION_SELECTION_REQUIRED')
    if values.get('AI_DIRECT_CORE_ROOT') not in (None,str(prepared.m0.CORE),str(prepared.CORE),str(CORE)):
        raise ValueError('MFA_DISAMBIGUATION_EXISTING_CORE')
    if values.get('AI_DIRECT_LLM_ROOT') not in (None,base.LOW,str(base.LLM),str(prepared.m0.LLM),str(prepared.LLM),str(LLM)):
        raise ValueError('MFA_DISAMBIGUATION_EXISTING_H5')
    changes=[]
    for name,(before,after) in FILES.items():
        old=base.owned(APP/name,optional=True)
        if base.sha(old)==after:continue
        if base.sha(old) not in before:raise ValueError('MFA_DISAMBIGUATION_RUNTIME_REVISION')
        raw=base.pinned(PAYLOAD/name,after)
        if name.endswith('.py'):compile(raw,name,'exec')
        changes.append((APP/name,raw))
    assets()
    if changes or values.get('AI_DIRECT_CORE_ROOT')!=str(CORE) or values.get('AI_DIRECT_LLM_ROOT')!=str(LLM):
        base.stopped();BACKUP.mkdir(mode=0o700,exist_ok=True)
        state=BACKUP/'state.json'
        if not state.exists():
            # A deterministic prepared v2 rollback is available even after a cold restore
            # whose B2 baseline predates the prepared and disambiguation patches.
            for name,digest in ROLLBACK.items():base.pinned(PAYLOAD/'rollback'/name,digest)
            base.replace(state,(json.dumps(dict(rollbackSha256=ROLLBACK,preparedAt=time.time()),sort_keys=True)+'\n').encode(),0o600,0)
        for path,raw in changes:base.replace(path,raw)
        prepared.m0.settings(values,str(CORE),str(LLM))
    meta=Path('/opt/intelligentai/deployment.json');doc=json.loads(base.owned(meta))
    doc.setdefault('operationalPatches',{})[PATCH]=dict(stage='DISAMBIGUATION_PREFIX_REDUCTION',mfaThreads=1,
        coreManifestSha256=CORE_SHA,llmManifestSha256=LLM_SHA,llmLockSha256=LOCK_SHA,
        files={name:dict(afterSha256=after) for name,(_,after) in FILES.items()},
        verificationScope='syntax, hashes and readiness; performance/quality unmeasured')
    base.replace(meta,(json.dumps(doc,sort_keys=True)+'\n').encode())
    print('Equivalent prefix reduction installed; query/order/writes, prepared runtime and GPT policy preserved')


if __name__=='__main__':main()
