"""Operator-controlled coordinated audio H5 transition; no inference/QA probes.

Stdin supplies a pinned, secret-free B2 runtime plan and one explicit phase.
Backend hold/activate/resume and private recovery settings remain AWS admin work.
Errors retain the direct hold; rollback never guesses Backend compatibility.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

WORK=Path('/opt/intelligentai/ops/concise-20261008')
APP=Path('/opt/intelligentai/app')
ENV=Path('/etc/intelligentai/analysis.env')
META=Path('/opt/intelligentai/deployment.json')
CONFIG=APP/'deploy/dlpc/supervisord.conf'
HOLD=Path('/run/intelligentai/direct-admission.closed')
TOOLS=Path('/home/2020067/.local/share/intelligentai/recovery/startup-20261006')
MARKER='concise-20261008\n'
CORE='f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391'
LLM='3e430635a4d714ead5d8e08352e41ff9a2b06c893d73af7f018fb43b4c37f61c'
LOCK='58c26086546abb8b94eb6998671101d67633af426adf373cceb42862ccaf0ec4'
DIRECTORY='canonical_dlpc_gop_announcer_20261007_v2_h5'
CTL=['/usr/gatoai/python/extra/system-py3.12/bin/supervisorctl','-c',str(CONFIG)]


def sha(raw):return hashlib.sha256(raw).hexdigest()


def record(stage,**fields):
    row=dict(stage=stage,at=time.time(),**fields)
    (WORK/'state.json').write_text(json.dumps(row)+'\n')
    return row


def env():
    if ENV.is_symlink() or ENV.stat().st_uid!=0 or ENV.stat().st_mode&0o077:
        raise ValueError('PRIVATE_SETTINGS_REQUIRED')
    return dict(x.split('=',1) for x in ENV.read_text().splitlines() if x and not x.startswith('#'))


def fetch(path):
    values=env()
    if path=='backend':
        url=values['AI_ANALYSIS_CALLBACK_BASE_URL'].rstrip('/')+'/api/internal/ai/worker-readiness/handoff'
        if url!='https://api.voice-coaching.site/api/internal/ai/worker-readiness/handoff':raise ValueError('BACKEND_ORIGIN_CHANGED')
        token=values['AI_ANALYSIS_CALLBACK_TOKEN']
    else:url='http://127.0.0.1:8080'+path;token=values['AI_ANALYSIS_API_TOKEN']
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    request=urllib.request.Request(url,headers={'Authorization':'Bearer '+token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=10) as reply:
        return json.loads(reply.read(131073))


def service(action):
    r=subprocess.run(CTL+[action,'analysis'],capture_output=True,timeout=180)
    if r.returncode:raise ValueError('ANALYSIS_SERVICE_CONTROL_FAILED')


def expect(plan):
    state=fetch('/health/deployment')
    wanted=dict(sourceGitRevision=plan['revision'],bundleSha256=plan['sha256'],
                coreManifestSha256=CORE,llmManifestSha256=LLM,llmLockSha256=LOCK)
    if any(state.get(k)!=v for k,v in wanted.items()):raise ValueError('RUNTIME_IDENTITY_MISMATCH')
    return state


def warm(plan):
    until=time.monotonic()+600
    while time.monotonic()<until:
        try:fetch('/readyz');expect(plan);return
        except (OSError,ValueError):time.sleep(5)
    raise ValueError('CONCISE_READINESS_TIMEOUT')


def backend(plan,admission=False):
    d=fetch('backend');rows=d.get('schemaDigests',{})
    schemas=rows if isinstance(rows,dict) else {r['file']:r['sha256'] for r in rows}
    if d.get('supported') is not True or schemas!=plan['schemas'] or (admission and d.get('admissionEnabled') is not True):
        raise ValueError('BACKEND_CONTRACT_NOT_READY')


def main():
    if os.geteuid()!=0 or socket.gethostname()!='J3990.2020067.notebook.dlpc':raise ValueError('EXPECTED_DLPC_ROOT')
    os.umask(0o077)
    body=json.loads(sys.stdin.buffer.read(65537));phase=body['phase'];plan=body['plan']
    if phase=='prepare':
        before=fetch('/health/deployment')
        if before.get('bundleSha256')!='b7170dda1a4ec5a520f30dfa88a0f57f649f9c118060975957e7f68b27b94e78':
            raise ValueError('CURRENT_RUNTIME_CHANGED')
        if not re.fullmatch('[0-9a-f]{40}',plan['revision']) or not re.fullmatch('[0-9a-f]{64}',plan['sha256']) or not 0<plan['bytes']<32*1024**2:
            raise ValueError('INVALID_RELEASE_PLAN')
        if shutil.disk_usage('/opt').free<14*1024**3:raise ValueError('DISK_HEADROOM_REQUIRED')
        WORK.mkdir(mode=0o700,parents=True,exist_ok=False)
        sys.path.insert(0,str(TOOLS))
        from b2_backup import B2
        ssh=['/usr/sbin/runuser','-u','2020067','--','/usr/bin/ssh','-F','/dev/null','-T','-p','22132',
             '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes',
             '-o','HostKeyAlias=13.209.184.226','-o','UserKnownHostsFile=/home/2020067/.ssh/intelligentai_aws_known_hosts',
             '-i','/home/2020067/.ssh/intelligentai_aws_20261005','ec2-user@13.209.184.226','dlpc-recovery']
        result=subprocess.run(ssh,input=b'{"operation":"b2"}',capture_output=True,timeout=60)
        if result.returncode:raise ValueError('B2_RECOVERY_AUTH_UNAVAILABLE')
        with B2(json.loads(result.stdout)).read(plan['fileId']) as reply:raw=reply.read(plan['bytes']+1)
        if len(raw)!=plan['bytes'] or sha(raw)!=plan['sha256']:raise ValueError('RUNTIME_BUNDLE_DIGEST_MISMATCH')
        bundle=json.loads(raw)
        if bundle['sourceGitRevision']!=plan['revision'] or bundle['llmDirectory']!=DIRECTORY:raise ValueError('RUNTIME_PLAN_MISMATCH')
        for name,row in bundle['files'].items():
            data=base64.b64decode(row['base64'],validate=True)
            if sha(data)!=row['sha256']:raise ValueError('RUNTIME_FILE_DIGEST_MISMATCH')
            if name.endswith('.py'):compile(data,name,'exec')
        for name,digest in [('llm/contracts/llm/llm_contract_manifest.json',LLM),('llm/contracts/llm/dependency_lock.json',LOCK),('core/release_manifest.json',CORE)]:
            if bundle['files'][name]['sha256']!=digest:raise ValueError('FROZEN_RELEASE_PIN_MISMATCH')
        (WORK/'plan.json').write_text(json.dumps(plan)+'\n');(WORK/'runtime.json').write_bytes(raw)
        (WORK/'deployment.before.json').write_bytes(META.read_bytes());(WORK/'analysis.env.before').write_bytes(ENV.read_bytes())
        (WORK/'supervisord.conf.before').write_bytes(CONFIG.read_bytes());(WORK/'identity.before.json').write_text(json.dumps(before))
        shutil.copytree(APP,WORK/'app.before',ignore=shutil.ignore_patterns('__pycache__','.git'))
        return record('prepared',revision=plan['revision'])
    if json.loads((WORK/'plan.json').read_bytes())!=plan:raise ValueError('RELEASE_PLAN_CHANGED')
    stage=json.loads((WORK/'state.json').read_bytes())['stage']
    if phase=='hold':
        if stage!='prepared':raise ValueError('PREPARED_STATE_REQUIRED')
        with HOLD.open('x') as stream:stream.write(MARKER)
        record('draining');until=time.monotonic()+1200;zero=0
        while time.monotonic()<until:
            d=fetch('/health/deployment');zero=zero+1 if d.get('activeJobs')==0 and d.get('directJobs')==0 else 0
            if zero==2:return record('drained')
            time.sleep(5)
        raise ValueError('DRAIN_TIMEOUT_KEEP_HOLD')
    if HOLD.read_text()!=MARKER:raise ValueError('DIRECT_HOLD_OWNER_CHANGED')
    if phase=='install':
        if stage!='drained':raise ValueError('DRAINED_STATE_REQUIRED')
        backend(plan)
        d=fetch('/health/deployment')
        if d.get('activeJobs')!=0 or d.get('directJobs')!=0:raise ValueError('DRAIN_CHANGED')
        service('stop');record('installing')
        raw=(WORK/'runtime.json').read_bytes();bundle=json.loads(raw)
        helper=base64.b64decode(bundle['files']['app/deploy/dlpc/install_runtime_overlay.py']['base64'],validate=True)
        path=WORK/'install_runtime_overlay.py';path.write_bytes(helper)
        spec=importlib.util.spec_from_file_location('coordinated_overlay',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.install(raw,plan['sha256'])
        CONFIG.write_bytes((WORK/'supervisord.conf.before').read_bytes());os.chown(CONFIG,0,10001);CONFIG.chmod(0o640)
        values=env();values.update(AI_CANONICAL_LLM_ROOT='/workspace/intelligentai/validation/'+DIRECTORY,
            AI_CANONICAL_PIPELINE_REVISION='canonical-dlpc-gop-announcer-20261007-v2',VOICE_COACH_BUILD_REVISION=plan['revision'])
        pending=ENV.with_suffix('.concise-pending')
        with pending.open('x') as stream:stream.write(''.join(k+'='+v+'\n' for k,v in sorted(values.items())));stream.flush();os.fsync(stream.fileno())
        pending.chmod(0o600);pending.replace(ENV)
        service('start');record('warming');warm(plan)
        return record('ready_waiting_for_backend_resume',revision=plan['revision'])
    if phase=='open':
        if stage!='ready_waiting_for_backend_resume':raise ValueError('WARMED_STATE_REQUIRED')
        warm(plan);backend(plan,True)
        # Persistent home recovery downloads the same secret-free runtime.
        old=(TOOLS/'restore_runtime_overlay.py').read_text();updated=old
        for key,value in {'FILE_ID':plan['fileId'],'SIZE':plan['bytes'],'SHA256':plan['sha256']}.items():
            pattern=r'^'+key+r' = .+$'
            updated,count=re.subn(pattern,key+' = '+repr(value),updated,flags=re.MULTILINE)
            if count!=1:raise ValueError('RECOVERY_PIN_SOURCE_CHANGED')
        compile(updated,'restore_runtime_overlay.py','exec')
        for name,data in [('restore_runtime_overlay.py',updated.encode()),('install_runtime_overlay.py',(WORK/'install_runtime_overlay.py').read_bytes())]:
            target=TOOLS/name
            (WORK/(name+'.before')).write_bytes(target.read_bytes())
            target.write_bytes(data);os.chown(target,120067,target.stat().st_gid);target.chmod(0o600)
        HOLD.unlink()
        try:fetch('/direct/readyz')
        except Exception:
            HOLD.write_text(MARKER);raise ValueError('DIRECT_NOT_READY_KEEP_HOLD') from None
        return record('complete',revision=plan['revision'],bundleSha256=plan['sha256'])
    raise ValueError('UNKNOWN_RELEASE_PHASE')


if __name__=='__main__':
    try:print(json.dumps(main()))
    except Exception as error:
        reason=str(error) if re.fullmatch('[A-Z_]+',str(error)) else 'INSPECT_PRIVATE_CONCISE_RELEASE_STATE'
        print(json.dumps(dict(stage='stopped',reason=reason,admissionMayRemainClosed=True)))
        raise SystemExit(1)
