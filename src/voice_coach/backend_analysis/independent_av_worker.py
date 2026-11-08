"""One local audiovisual execution. No Backend, AWS, leases or callbacks."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import UUID

from voice_coach.direct_analysis.store import atomic, encoded
from voice_coach.direct_analysis.worker import LocalProducerJournal
from voice_coach.visual_feedback.canonical_service import CanonicalAudiovisualService
from voice_coach.visual_feedback.coaching_input import prepare_visual_coaching
from voice_coach.visual_feedback.adapters.canonical_video_features import CanonicalVideoFeatures, VideoFeatureConfiguration
from .canonical_bridge import FrozenCanonicalArtifacts, FrozenCanonicalCoaching
from .canonical_audiovisual_result import visual_assets, validated_visual, public_visual
from .canonical_codec import encode_document
from .canonical_producer import FrozenCanonicalProducer, write_private_new
from .canonical_result import preflight_canonical_result, _public_score, _public_coaching
from .canonical_transport import transport_for_artifacts
from .http_models import HttpVisualInput
from .independent_av_domain import IndependentAudiovisualInput
from .independent_av_store import CONTRACT
from .media_preparation import MediaPreparation
from .ports import MaterializedAudio, MaterializedVideo


async def execute(folder, env):
    d = json.loads((folder/'job.json').read_bytes())
    m = d['metadata']
    request = IndependentAudiovisualInput(UUID(d['jobId']), UUID(d['requestId']), UUID(d['executionId']),
        UUID(d['audioAssetId']), m['scriptText'], hashlib.sha256(m['scriptText'].encode()).hexdigest(),
        m['audio']['sha256'], m['audio']['fileSizeBytes'], m['contentId'],
        visual_input=HttpVisualInput('local/video.mp4', m['video']['sha256'], 'video/mp4',
                                    m['video']['fileSizeBytes'], None), media_preparation=m['mediaPreparation'])
    work = Path(env['AI_CANONICAL_WORKSPACE_ROOT'])/d['executionId']
    work.mkdir(mode=0o700, exist_ok=False)
    (work/'media').mkdir(mode=0o700)
    (work/'core').mkdir(mode=0o700)
    assets = FrozenCanonicalArtifacts(Path(env['AI_CANONICAL_CORE_ROOT']), Path(env['AI_CANONICAL_LLM_ROOT']),
                                      Path(env['AI_CANONICAL_VERIFIED_GENERATION']))
    frozen = FrozenCanonicalCoaching(assets)
    catalog, configuration = visual_assets(frozen)
    for name in ('audio.wav','video.mp4'):
        write_private_new(work/'media'/name, (folder/name).read_bytes())
    audio = MaterializedAudio(work/'media/audio.wav', m['audio']['fileSizeBytes'], m['audio']['sha256'])
    video = MaterializedVideo(work/'media/video.mp4', m['video']['fileSizeBytes'], m['video']['sha256'])
    deadline = time.monotonic()+max(0, d['deadlineAt']-time.time())
    active = lambda: time.monotonic() < deadline
    producer = FrozenCanonicalProducer(assets, work/'core', LocalProducerJournal(folder),
        nltk_data_root=Path(env['AI_ANALYSIS_NLTK_DATA_ROOT']),
        resident_socket=Path(env['AI_CANONICAL_RESIDENT_SOCKET']))
    port = CanonicalVideoFeatures(VideoFeatureConfiguration(Path(env['AI_CANONICAL_VISUAL_PYTHON']),
        Path(env['AI_ANALYSIS_VISUAL_LANDMARK_MODEL_PATH']), configuration['modelSha256'], configuration['quality']))
    started = time.monotonic()
    combined = await CanonicalAudiovisualService(video_port=port, catalog=catalog).produce(
        request=request, audio=audio, video=video,
        media_preparation=MediaPreparation.from_wire(m['mediaPreparation'],
            audio_sha256=audio.sha256, video_sha256=video.sha256), producer=producer,
        deadline=deadline-configuration['gptAndHandoffReserveSeconds'],
        visual_timeout=configuration['visualTimeoutSeconds'], execution_is_active=active)
    produced = combined.audio
    visual_raw = encode_document(combined.visual_evidence)
    saved = folder/'artifacts'
    saved.mkdir(mode=0o700)
    atomic(saved/'CORE.json', produced.raw)
    atomic(saved/'VISUAL_EVIDENCE.json', visual_raw)
    core_finished = time.monotonic()
    transport = transport_for_artifacts(env['OPENAI_API_KEY'], assets)
    try:
        expression = await asyncio.to_thread(frozen.express_admitted, request, audio=audio,
            core_bytes=produced.raw, prepared_wav=produced.prepared_wav,
            producer_workspace=produced.workspace, transport=transport, visual_evidence_bytes=visual_raw)
        for kind, raw in [('BRIDGE_RESULT',expression.expression_bytes),('BINDING',expression.binding_bytes),
                          ('ASSOCIATION',expression.association_bytes)]:
            atomic(saved/(kind+'.json'),raw)
    finally:
        atomic(folder/'provider-observation.json', encoded(transport.diagnostics))
        transport.close()
    expression_finished = time.monotonic()
    # Validate actual visual evidence and replay the frozen expression checks;
    # no second provider invocation or fabricated Backend retention receipt.
    visual = validated_visual(visual_raw, request=request, frozen=frozen,
        core=json.loads(produced.raw), prepared_audio=produced.prepared_audio, core_raw=produced.raw)['visual']
    preflight = await asyncio.to_thread(preflight_canonical_result, expression, request=request,
        frozen=frozen, prepared_audio=produced.prepared_audio,
        _visual_payload=prepare_visual_coaching(visual_raw, core_bytes=produced.raw))
    coaching, core = json.loads(expression.expression_bytes), json.loads(produced.raw)
    failed = coaching['adapterStatus'] == 'FAIL_CLOSED' or core['input_validation']['status'] == 'SYSTEM_FAILURE'
    result = dict(schemaVersion='voice-coaching.independent-audiovisual-result.v1',
        jobId=d['jobId'], executionId=d['executionId'], clientRequestId=d['clientAttemptId'],
        status='FAILED' if failed else 'COMPLETED',
        score=_public_score(coaching, None, assets.identity), coaching=_public_coaching(coaching),
        visual=public_visual(visual),
        sourceIdentity=dict(requestId=d['requestId'], audioSha256=audio.sha256, videoSha256=video.sha256,
            scriptSha256=request.script_sha256, coreSha256=hashlib.sha256(produced.raw).hexdigest(),
            mediaReceiptSha256=m['mediaPreparation']['receiptSha256'],
            visualEvidenceSha256=hashlib.sha256(visual_raw).hexdigest()),
        failure={'code':'CANONICAL_ANALYSIS_FAILED'} if failed else None)
    originals = {a.kind:a.raw for a in preflight.artifacts}
    originals.update(MEDIA_RECEIPT=encode_document(m['mediaPreparation']['receipt']), VISUAL_EVIDENCE=visual_raw)
    for kind, raw in originals.items(): atomic(saved/(kind+'.json'), raw)
    # A separate manifest binds every immutable audio/visual original to this job.
    atomic(folder/'artifacts-manifest.json', encoded(dict(schemaVersion=CONTRACT,
        jobId=d['jobId'], executionId=d['executionId'], sourceIdentity=result['sourceIdentity'],
        artifacts={k:hashlib.sha256(v).hexdigest() for k,v in originals.items()})))
    raw = encoded(result)
    atomic(folder/'result.json', raw)
    atomic(folder/'worker-complete.json', encoded(dict(executionId=d['executionId'], requestId=d['requestId'],
        status=result['status'], failure='CANONICAL_ANALYSIS_FAILED' if failed else None,
        resultSha256=hashlib.sha256(raw).hexdigest(), timing=dict(
            coreSeconds=core_finished-started, expressionSeconds=expression_finished-core_finished,
            publicationSeconds=time.monotonic()-expression_finished, parallel=combined.timings,
            producer=produced.timings, provider=transport.diagnostics))))


if __name__ == '__main__':
    try:
        asyncio.run(execute(Path(sys.argv[1]).resolve(strict=True), os.environ))
    except Exception as error:
        print(json.dumps({'event':'independent_av_execution_failed','exceptionType':type(error).__name__}), flush=True)
        raise SystemExit(1)
