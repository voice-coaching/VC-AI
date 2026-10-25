"""Install/restore the pinned prepared-MFA pair; rollback restores the M0 pair."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

import host_identity
import restore_mfa_trace as m0

base = m0.base
APP = base.APP
PAYLOAD = Path(__file__).resolve().parent / 'mfa-prepared-20261025-v2'
SELECTOR = PAYLOAD / 'selected'
BACKUP = Path('/opt/intelligentai/ops/mfa-prepared-20261025-v2')
CORE = Path('/workspace/intelligentai/validation/canonical_dlpc_mfa_prepared_20261025_t1_v2')
LLM = CORE.with_name(CORE.name + '_h5')
CORE_SHA = '755667629b5d603910ff2646daa807bf7757e2d3f3ca29845e265e2e4dc79984'
LLM_SHA = '11d920cd07d3ed583885d6ea79d1bc2ee0a65903a42c98448cca87a37b4a5e94'
LOCK_SHA = 'fb322f24a263d385accb6b48f9f841bbaa8dd866b68b7129c5189b854ede553b'
PATCH = 'mfa-prepared-20261025'
AFTER = {
    'src/voice_coach/backend_analysis/canonical_harness.py': '2297a6fbef3d6ea38e3dd682191ebc5fe4f069cec631d13faf5237abd4854fda',
    'src/voice_coach/backend_analysis/native_execution.py': 'b279a4ed61ea28ad6e7a6af300f74569bb24dda8639d40d37c78a44483826e5a',
    'src/voice_coach/backend_analysis/resident_health.py': '37f57a765b3ebe560ca82f97f95564335b8330e1eb1ad31b053309ad4178986c',
    'docs/contracts/runpod_result_v4.schema.json': 'ce268a4e41c9d7bed38177a9d61bf6d771d1df661f8dd13b5fc0bd26abc28e14',
}
FILES = {name:(tuple(before)+(after,),AFTER.get(name,after)) for name,(before,after) in m0.FILES.items()}
HEALTH = 'src/voice_coach/backend_analysis/resident_health.py'
HEALTH_BEFORE = '7a5a7e4f93bf351a8193c677e193f4fb872c9265e644a0a8467d88d3e62d1214'
FILES[HEALTH] = ((None,HEALTH_BEFORE),AFTER[HEALTH])
ROLLBACK = {name:after for name,(_,after) in m0.FILES.items()} | {HEALTH:HEALTH_BEFORE}


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
        raise ValueError('MFA_PREPARED_PAIR_DIGEST')
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
    if values.get('AI_DIRECT_CORE_ROOT') not in (str(CORE),str(m0.CORE)) or values.get('AI_DIRECT_LLM_ROOT') not in (str(LLM),str(m0.LLM)):
        raise ValueError('MFA_PREPARED_ROLLBACK_SELECTION')
    changes = []
    for name,digest in ROLLBACK.items():
        if base.sha(base.owned(APP/name,optional=True)) not in (digest,FILES[name][1]):
            raise ValueError('MFA_PREPARED_ROLLBACK_REVISION')
        changes.append((APP/name,base.pinned(PAYLOAD/'rollback'/name,digest)))
    m0.assets()
    for path,raw in changes:base.replace(path,raw)
    m0.settings(values,str(m0.CORE),str(m0.LLM))
    meta=Path('/opt/intelligentai/deployment.json');doc=json.loads(base.owned(meta))
    doc.get('operationalPatches',{}).pop(PATCH,None)
    base.replace(meta,(json.dumps(doc,sort_keys=True)+'\n').encode())
    base.replace(SELECTOR,b'disabled\n',0o600,0)
    base.replace(m0.SELECTOR,b'trace\n',0o600,0)
    print('M0 runtime, health, direct pair and recovery selection restored; analysis remains stopped')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback',action='store_true');args=parser.parse_args()
    if host_identity.require_root().recovery!='dlpc-j406-20261016':raise ValueError('MFA_PREPARED_J406_REQUIRED')
    os.umask(0o077)
    sys.path.insert(0,str(APP/'deploy/dlpc'))
    from launch import private_settings
    values=private_settings(base.ENV)
    if args.rollback:
        rollback(values);return
    if base.owned(SELECTOR)!=b'prepared\n':raise ValueError('MFA_PREPARED_SELECTION_REQUIRED')
    if values.get('AI_DIRECT_CORE_ROOT') not in (None,str(m0.CORE),str(CORE)):
        raise ValueError('MFA_PREPARED_EXISTING_CORE')
    if values.get('AI_DIRECT_LLM_ROOT') not in (None,base.LOW,str(base.LLM),str(m0.LLM),str(LLM)):
        raise ValueError('MFA_PREPARED_EXISTING_H5')
    changes=[]
    for name,(before,after) in FILES.items():
        old=base.owned(APP/name,optional=True)
        if base.sha(old)==after:continue
        if base.sha(old) not in before:raise ValueError('MFA_PREPARED_RUNTIME_REVISION')
        raw=base.pinned(PAYLOAD/name,after)
        if name.endswith('.py'):compile(raw,name,'exec')
        changes.append((APP/name,raw))
    assets()
    if changes or values.get('AI_DIRECT_CORE_ROOT')!=str(CORE) or values.get('AI_DIRECT_LLM_ROOT')!=str(LLM):
        base.stopped();BACKUP.mkdir(mode=0o700,exist_ok=True)
        state=BACKUP/'state.json'
        if not state.exists():
            # A deterministic M0 rollback is available even after a cold restore
            # whose B2 baseline predates both M0 and this operational patch.
            for name,digest in ROLLBACK.items():base.pinned(PAYLOAD/'rollback'/name,digest)
            base.replace(state,(json.dumps(dict(rollbackSha256=ROLLBACK,preparedAt=time.time()),sort_keys=True)+'\n').encode(),0o600,0)
        for path,raw in changes:base.replace(path,raw)
        m0.settings(values,str(CORE),str(LLM))
    meta=Path('/opt/intelligentai/deployment.json');doc=json.loads(base.owned(meta))
    doc.setdefault('operationalPatches',{})[PATCH]=dict(stage='P0/P1',mfaThreads=1,
        coreManifestSha256=CORE_SHA,llmManifestSha256=LLM_SHA,llmLockSha256=LOCK_SHA,
        files={name:dict(afterSha256=after) for name,(_,after) in FILES.items()},
        verificationScope='syntax, hashes and readiness; performance/quality unmeasured')
    base.replace(meta,(json.dumps(doc,sort_keys=True)+'\n').encode())
    print('Prepared MFA single-thread pair installed; numerical workflow, GPT and TTS policy preserved')


if __name__=='__main__':main()
