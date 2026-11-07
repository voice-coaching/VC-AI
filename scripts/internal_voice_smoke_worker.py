"""Offline execution of a real internal v4 request through v6/handoff generation.

Uses the incident's actual recording/content IDs and media receipt; never claims
a Backend lease, publishes the result or overwrites the failed analysis.
"""
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import UUID, uuid4

from voice_coach.backend_analysis.canonical_bridge import FrozenCanonicalArtifacts, FrozenCanonicalCoaching
from voice_coach.backend_analysis.canonical_codec import encode_document, read_document
from voice_coach.backend_analysis.canonical_handoff import handoff_document
from voice_coach.backend_analysis.canonical_producer import FrozenCanonicalProducer, write_private_new
from voice_coach.backend_analysis.canonical_resident import CanonicalResidentCore
from voice_coach.backend_analysis.canonical_result import ResultIdentity, preflight_canonical_result, serialize_canonical_result, validate_canonical_result_document
from voice_coach.backend_analysis.canonical_transport import transport_for_artifacts
from voice_coach.backend_analysis.http_models import AnalysisInput
from voice_coach.backend_analysis.ports import MaterializedAudio
from voice_coach.direct_analysis.worker import LocalProducerJournal
from voice_coach.direct_analysis.store import atomic, encoded


def require(ok, code):
    if not ok:
        raise ValueError(code)


async def main(root):
    config = json.loads((root/'config.json').read_bytes())
    body = json.loads((root/'request.private.json').read_bytes())
    body.update(requestId=str(uuid4()), executionId=str(uuid4()))
    request = AnalysisInput.from_payload(body, config['recordingsPrefix'])
    env = dict(os.environ, AI_CANONICAL_WORKSPACE_ROOT=str(root/'work'),
               AI_CANONICAL_STANDBY_RESERVE_BYTES='0', AI_CANONICAL_STANDBY_MIN_FREE_BYTES='0')
    (root/'work').mkdir(mode=0o700)
    resident = CanonicalResidentCore(env, Path(env['AI_CANONICAL_PYTHON']))
    report = dict(passed=False, scope='REAL_INTERNAL_REQUEST_V4_TO_V6_HANDOFF_OFFLINE',
                  backendPublished=False, inputAudioSha256=request.audio_sha256,
                  inputScriptSha256=request.script_sha256, mediaPreparationPresent=request.media_preparation is not None)
    transport = None
    started = time.monotonic()
    try:
        until = time.monotonic()+300
        while not resident.ready() and time.monotonic()<until:
            await resident.refresh()
            await asyncio.sleep(.1)
        require(resident.ready(), 'INTERNAL_SMOKE_WARMUP_TIMEOUT')
        artifacts = FrozenCanonicalArtifacts(Path(env['AI_CANONICAL_CORE_ROOT']),
            Path(env['AI_CANONICAL_LLM_ROOT']), resident.generation)
        frozen = FrozenCanonicalCoaching(artifacts)
        transport = transport_for_artifacts(env['OPENAI_API_KEY'], artifacts)
        work = root/'work'/str(request.execution_id)
        (work/'media').mkdir(parents=True,mode=0o700)
        (work/'core').mkdir(mode=0o700)
        raw = (root/'input.wav').read_bytes()
        require(hashlib.sha256(raw).hexdigest()==request.audio_sha256, 'INTERNAL_SMOKE_INPUT_CHANGED')
        wav = work/'media/audio.wav'
        write_private_new(wav,raw)
        journal = root/'journal'
        journal.mkdir(mode=0o700)
        produced = await FrozenCanonicalProducer(artifacts,work/'core',LocalProducerJournal(journal),
            nltk_data_root=Path(env['AI_ANALYSIS_NLTK_DATA_ROOT']), resident_socket=resident.socket).produce(
                request, MaterializedAudio(wav,len(raw),request.audio_sha256),
                deadline_monotonic=time.monotonic()+240,execution_is_active=lambda: True)
        core = json.loads(produced.raw)
        report['decision'] = core['input_validation']['status']
        require(core['input_validation']['status']=='ACCEPT', 'INTERNAL_SMOKE_CORE_NOT_ACCEPTED')
        expression = await asyncio.to_thread(frozen.express_admitted,request,
            audio=MaterializedAudio(wav,len(raw),request.audio_sha256),core_bytes=produced.raw,
            prepared_wav=produced.prepared_wav,producer_workspace=produced.workspace,transport=transport)
        checked = await asyncio.to_thread(preflight_canonical_result,expression,request=request,
            frozen=frozen,prepared_audio=produced.prepared_audio)
        identity = ResultIdentity(uuid4(),uuid4(),config['revision'],config['pipelineRevision'])
        projection = await asyncio.to_thread(serialize_canonical_result,expression,request=request,
            identity=identity,frozen=frozen,prepared_audio=produced.prepared_audio)
        result = json.loads(projection)
        require(len(result['score'].get('details', {}).get('criteria', [])) == 6,
                'INTERNAL_SMOKE_SCORE_DETAILS_MISSING')
        historical = read_document(projection)
        historical['score'].pop('details')
        validate_canonical_result_document(historical, preflight=checked)
        report.update(scoreDetailsPresent=True, legacyWithoutDetailsVerified=True)
        doc = handoff_document(body,projection,checked,identity)
        require(result['status']=='COMPLETED' and result['failure'] is None, 'INTERNAL_SMOKE_RESULT_FAILED')
        require(type(result['score']['overallScore']) in (int,float)
                and 0 <= result['score']['overallScore'] <= 100
                and result['score']['validity']=='RUBRIC_COMPUTED', 'INTERNAL_SMOKE_NUMERIC_SCORE_REQUIRED')
        require(result['schemaVersion']=='voice-coaching.runpod-analysis-result.v6', 'INTERNAL_SMOKE_V6_MISSING')
        require(result['coaching']['generationStatus']=='SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID'
                and bool(result['coaching']['feedback']), 'INTERNAL_SMOKE_FEEDBACK_INVALID')
        require(expression.core_bytes==produced.raw, 'INTERNAL_SMOKE_CORE_CHANGED')
        core_sha=hashlib.sha256(produced.raw).hexdigest()
        require(transport.diagnostics['postAttempts']==1 and transport.diagnostics['rawCoreMessageSha256']==core_sha,
                'INTERNAL_SMOKE_PROVIDER_INVARIANT')
        for name,value in [('CORE',produced.raw),('BRIDGE_RESULT',expression.expression_bytes),
                           ('RESULT_V6',projection),('HANDOFF_V2',encode_document(doc))]:
            atomic(root/(name+'.private.json'),value)
        report.update(passed=True,status=result['status'],score=result['score'],
            postAttempts=transport.diagnostics['postAttempts'], coreSha256=core_sha,
            rawCoreMessageSha256=transport.diagnostics['rawCoreMessageSha256'],
            resultSchemaVersion=result['schemaVersion'],handoffSchemaVersion=doc['metadata']['schemaVersion'],
            coreManifestSha256=artifacts.identity.core_sha256,harnessManifestSha256=artifacts.identity.manifest_sha256)
    except Exception as error:
        import re
        text=str(error)
        report['error']=text if re.fullmatch('[A-Z][A-Z0-9_]{0,100}',text) else type(error).__name__
        if resident.generation:
            p=resident.generation/'mfa_runtime/host_failure.json'
            if p.exists():atomic(root/'mfa-host-failure.private.json',p.read_bytes())
    finally:
        report['seconds']=round(time.monotonic()-started,3)
        if transport:transport.close()
        atomic(root/'report.json',encoded(report))
        await resident.close()
    print(json.dumps(report),flush=True)
    return report['passed']


if __name__=='__main__':
    raise SystemExit(0 if asyncio.run(main(Path(sys.argv[1]).resolve(strict=True))) else 1)
