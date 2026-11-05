"""Root operator entrypoint: execute a clean candidate as service UID, bind receipt to Git.

Requires the deployed controller, local fixture media and immutable candidate H5s.
No production requests are replayed; original fixtures and CORE packages are read only.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()


def identity(source):
    if git(source, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('SMOKE_CANDIDATE_MUST_BE_CLEAN')
    return dict(revision=git(source, 'rev-parse', 'HEAD'), tree=git(source, 'rev-parse', 'HEAD^{tree}'))


def main(args):
    if os.geteuid() != 0:
        raise ValueError('SMOKE_OPERATOR_ROOT_REQUIRED')
    source = args.source.resolve(strict=True)
    before = identity(source)
    fixtures_raw = (source / 'tests/voice/cases.json').read_bytes()
    fixtures = json.loads(fixtures_raw)['cases']
    cases, aliases, seen = [], [], {}
    for case in fixtures:
        media = args.fixtures_root / case['file']
        if sha(media.read_bytes()) != case['sha256']:
            raise ValueError('SMOKE_FIXTURE_DIGEST_CHANGED')
        if 'aliasOf' in case:
            parent = seen[case['aliasOf']]
            if (parent['sha256'], parent['scriptText']) != (case['sha256'], case['scriptText']):
                raise ValueError('SMOKE_ALIAS_MISMATCH')
            aliases.append(dict(id=case['id'], aliasOf=case['aliasOf'], sha256=case['sha256']))
        else:
            cases.append(case)
        seen[case['id']] = case
    output = args.output.resolve()
    output.mkdir(mode=0o755, parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('deployed_controller', '/opt/intelligentai/cicd/deploy_source.py')
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    values = controller.values()  # Secrets stay in this process and approved child environment.
    env = {k: values[k] for k in ('OPENAI_API_KEY', 'AI_ANALYSIS_NLTK_DATA_ROOT', 'AI_ANALYSIS_RESOURCE_PROFILE')}
    env.update(PATH='/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8',
        PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
        PYTHONPATH=str(source/'src'), VOICE_COACH_REPOSITORY_ROOT=str(source),
        AI_CANONICAL_PYTHON='/opt/intelligentai/envs/canonical/bin/python',
        AI_CANONICAL_STANDBY_RESERVE_BYTES='0', AI_CANONICAL_STANDBY_MIN_FREE_BYTES='0',
        AI_DIRECT_STANDBY_RESERVE_BYTES='0', AI_DIRECT_STANDBY_MIN_FREE_BYTES='0')
    receipt = dict(schemaVersion='voice-analysis-smoke-receipt.v1', **before,
        baseRevision=args.base_revision or git(source, 'merge-base', 'HEAD', 'origin/main'),
        fixtureManifestSha256=sha(fixtures_raw), aliases=aliases, startedAt=time.time(),
        lanes={}, passed=False)
    for lane in ('direct', 'internal'):
        core = getattr(args, lane+'_core').resolve(strict=True)
        h5 = getattr(args, lane+'_h5').resolve(strict=True)
        manifest_raw = (h5/'contracts/llm/llm_contract_manifest.json').read_bytes()
        manifest = json.loads(manifest_raw)
        lock = json.loads((h5/'contracts/llm/dependency_lock.json').read_bytes())['files']
        for module, pin in lock.items():
            if sha((h5/module).read_bytes()) != pin:
                raise ValueError('SMOKE_H5_FILE_CHANGED')
        # Prevent testing an old frozen adapter with new source changes.
        frozen = (source/'deploy/runpod/harnesses/six_dimension_bridge.py').read_bytes().replace(
            b'__CORE_MANIFEST__', sha((core/'release_manifest.json').read_bytes()).encode())
        if frozen != (h5/'bridge/adapter.py').read_bytes():
            raise ValueError('SMOKE_H5_ADAPTER_NOT_CANDIDATE')
        for name in ('policy', 'assessment', 'composite', 'native_rubric', 'pipeline'):
            if (source/f'src/voice_coach/six_dimension/{name}.py').read_bytes() != (h5/f'bridge/{name}.py').read_bytes():
                raise ValueError('SMOKE_H5_MODULE_NOT_CANDIDATE')
        root = output/lane
        root.mkdir(mode=0o700)
        os.chown(root, 10001, 10001)
        configured = []
        for case in cases:
            pcm = root/(case['id']+'.wav')
            subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-nostdin', '-i',
                str(args.fixtures_root/case['file']), '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(pcm)], check=True)
            os.chown(pcm, 10001, 10001)
            configured.append(dict(case, pcm=pcm.name))
        config = root/'config.json'
        config.write_text(json.dumps(dict(lane=lane, cases=configured), ensure_ascii=False))
        os.chown(config, 10001, 10001)
        lane_env = dict(env, AI_DIRECT_CORE_ROOT=str(core), AI_DIRECT_LLM_ROOT=str(h5),
            AI_CANONICAL_CORE_ROOT=str(core), AI_CANONICAL_LLM_ROOT=str(h5),
            AI_DIRECT_WORKSPACE_ROOT=str(root/'work'))
        with (root/'worker.private.log').open('wb') as log:
            os.chmod(root/'worker.private.log', 0o600)
            child = subprocess.run([env['AI_CANONICAL_PYTHON'], '-B', str(source/'scripts/voice_smoke_worker.py'), str(root)],
                env=lane_env, cwd=source, user=10001, group=10001, extra_groups=[10001],
                stdout=log, stderr=subprocess.STDOUT, timeout=1500, start_new_session=True)
        report_path = root/'report.json'
        report = json.loads(report_path.read_bytes()) if report_path.exists() else dict(passed=False, error='SMOKE_WORKER_NO_REPORT')
        receipt['lanes'][lane] = dict(report, coreManifestSha256=sha((core/'release_manifest.json').read_bytes()),
            harnessManifestSha256=sha(manifest_raw), lockSha256=manifest['dependency_lock_sha256'], promptSha256=manifest['prompt_sha256'])
        print(json.dumps(dict(lane=lane, passed=report['passed'], cases=len(report.get('cases', [])), error=report.get('error'))), flush=True)
    # This is an actual internal request, not DirectInput with an internal H5.
    root = output/'internal-v6'
    root.mkdir(mode=0o700)
    os.chown(root,10001,10001)
    config = dict(recordingsPrefix=values['AI_ANALYSIS_OBJECT_STORAGE_RECORDINGS_PREFIX'],
        revision=before['revision'], pipelineRevision=values['AI_CANONICAL_PIPELINE_REVISION'])
    for name, raw in [('request.private.json',args.internal_request.read_bytes()),
                      ('input.wav',args.internal_audio.read_bytes()),('config.json',json.dumps(config).encode())]:
        path=root/name
        path.write_bytes(raw)
        path.chmod(0o600)
        os.chown(path,10001,10001)
    lane_env=dict(env, AI_CANONICAL_CORE_ROOT=str(args.internal_core.resolve(strict=True)),
                  AI_CANONICAL_LLM_ROOT=str(args.internal_h5.resolve(strict=True)))
    with (root/'worker.private.log').open('wb') as log:
        os.chmod(root/'worker.private.log',0o600)
        subprocess.run([env['AI_CANONICAL_PYTHON'],'-B',str(source/'scripts/internal_voice_smoke_worker.py'),str(root)],
            env=lane_env,cwd=source,user=10001,group=10001,extra_groups=[10001],
            stdout=log,stderr=subprocess.STDOUT,timeout=600,start_new_session=True)
    report_path=root/'report.json'
    receipt['internalV6']=json.loads(report_path.read_bytes()) if report_path.exists() else dict(passed=False,error='INTERNAL_SMOKE_NO_REPORT')
    print(json.dumps(dict(internalV6Passed=receipt['internalV6']['passed'],error=receipt['internalV6'].get('error'))),flush=True)
    # Parse the actual generated bytes with the existing Backend Java classes.
    backend = identity(args.backend_root.resolve(strict=True))
    schema_raw=(args.backend_root/'docs/contracts/runpod_result_v6.schema.json').read_bytes()
    classpath=args.backend_classpath_file.read_text().strip()
    classes=root/'backend-probe'
    classes.mkdir(mode=0o700)
    os.chown(classes,10001,10001)
    java_env=dict(PATH='/usr/bin:/bin',LANG='C.UTF-8')
    passed=False
    with (root/'backend.private.log').open('wb') as log:
        os.chmod(root/'backend.private.log',0o600)
        compiled=subprocess.run([str(args.java_home/'bin/javac'),'-cp',classpath,'-d',str(classes),
            str(source/'scripts/BackendVoiceSmoke.java')],env=java_env,user=10001,group=10001,
            extra_groups=[10001],stdout=log,stderr=subprocess.STDOUT,timeout=90)
        if compiled.returncode==0 and receipt['internalV6']['passed']:
            parsed=subprocess.run([str(args.java_home/'bin/java'),'-cp',str(classes)+os.pathsep+classpath,
                'BackendVoiceSmoke',str(root/'RESULT_V6.private.json'),str(root/'HANDOFF_V2.private.json')],
                env=java_env,user=10001,group=10001,extra_groups=[10001],stdout=log,stderr=subprocess.STDOUT,timeout=90)
            passed=parsed.returncode==0
    receipt['backendVerification']=dict(passed=passed,**backend,schemaSha256=sha(schema_raw),
        scope='BACKEND_JAVA_PARSE_HANDOFF_PUBLIC_PROJECTION',databaseWritten=False)
    print(json.dumps(dict(backendContractPassed=passed)),flush=True)
    if identity(source) != before:
        raise ValueError('SMOKE_CANDIDATE_CHANGED_DURING_TEST')
    receipt.update(passed=all(row['passed'] for row in receipt['lanes'].values())
                   and receipt['internalV6']['passed'] and passed, finishedAt=time.time())
    path = output/'receipt.json'
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    os.chmod(path, 0o644)
    print(json.dumps(dict(receipt=str(path), passed=receipt['passed'], revision=before['revision'])), flush=True)
    return receipt['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--base-revision', help='Exact target main SHA checked by the operator before the run')
    parser.add_argument('--fixtures-root', type=Path, required=True)
    for lane in ('direct','internal'):
        parser.add_argument('--'+lane+'-core',type=Path,required=True)
        parser.add_argument('--'+lane+'-h5',type=Path,required=True)
    parser.add_argument('--internal-request',type=Path,required=True)
    parser.add_argument('--internal-audio',type=Path,required=True)
    parser.add_argument('--backend-root',type=Path,required=True)
    parser.add_argument('--backend-classpath-file',type=Path,required=True)
    parser.add_argument('--java-home',type=Path,required=True)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(0 if main(parser.parse_args()) else 1)
