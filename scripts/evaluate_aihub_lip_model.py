"""Evaluate the learned research model on a designated test-video job. No GPT.

Replay original CORE/clock provenance first, freshly extract the test video's
landmarks by default, and save all new outputs in a separate directory.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from voice_coach.visual_feedback.corpus_dataset import file_sha,private_write
from voice_coach.visual_feedback.corpus_reference import encode,require,sha
from voice_coach.visual_feedback.feature_protocol import PROTOCOL,MAPPING,strict_json
from voice_coach.visual_feedback.corpus_research_model import evaluate


async def run(model_path,job,core_root,h5_root,output,visual_python,landmark_model,retained_only=False):
    from voice_coach.backend_analysis.canonical_bridge import FrozenCanonicalArtifacts,FrozenCanonicalCoaching
    from voice_coach.backend_analysis.canonical_audiovisual_result import visual_assets
    from voice_coach.visual_feedback.canonical_service import assess_aligned,SPARSE_ASSESSMENT
    from voice_coach.visual_feedback.canonical_verification import verify_visual_evidence
    from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
    from voice_coach.visual_feedback.adapters.canonical_video_features import CanonicalVideoFeatures,VideoFeatureConfiguration
    model_raw=model_path.read_bytes();model=strict_json(model_raw)
    core_path=job/'artifacts/CORE.json';old_path=job/'artifacts/VISUAL_EVIDENCE.json';video_path=job/'video.mp4'
    receipt_path=job/'artifacts/MEDIA_RECEIPT.json'
    before={p:file_sha(p) for p in (core_path,old_path,video_path,receipt_path)}
    raw=core_path.read_bytes();core=strict_json(raw);old_raw=old_path.read_bytes();old=strict_json(old_raw)
    receipt=strict_json(receipt_path.read_bytes())
    require(before[video_path]==old['videoSha256'],'TEST_VIDEO_IDENTITY_MISMATCH')
    frozen=FrozenCanonicalCoaching(FrozenCanonicalArtifacts(core_root,h5_root))
    catalog,config=visual_assets(frozen)
    require(config['modelSha256']==model['modelSha256'] and sha(encode(config['quality']))==model['qualitySha256'],
            'CORPUS_FEATURE_PROTOCOL_MISMATCH')
    request=SimpleNamespace(event_id=old['requestId'],execution_id=old['executionId'],
        audio_sha256=old['audioSha256'],visual_input=SimpleNamespace(sha256=old['videoSha256']),
        media_preparation={'receipt':receipt})
    prepared=SimpleNamespace(sha256=old['preparedWavSha256'],frame_count=old['preparedPcm']['frames'],
                            sample_rate=old['preparedPcm']['sampleRate'])
    verify_visual_evidence(old_raw,core=core,core_raw=raw,request=request,catalog=catalog,
        prepared_audio=prepared,expected_model_sha256=config['modelSha256'])
    if retained_only:
        features=dict(old['decoder'],features=dict(protocol=PROTOCOL,mappingRevision=MAPPING.revision,frames=old['frames']))
    else:
        port=CanonicalVideoFeatures(VideoFeatureConfiguration(visual_python,landmark_model,config['modelSha256'],config['quality']))
        features=await port.extract(video_path=video_path,video_sha256=old['videoSha256'],byte_count=video_path.stat().st_size)
    empty_raw=(ROOT/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes()
    empty=ReferenceCatalog.from_bytes(empty_raw,sha(empty_raw))
    measured=assess_aligned(core=core,core_raw=raw,request=request,feature_value=features,
        source_pcm=old['sourcePcm'],prepared_pcm=old['preparedPcm'],catalog=empty,
        alignment_evidence=old.get('alignmentSidecar'),assessment_policy=SPARSE_ASSESSMENT)
    require(measured['timeMap'] is not None,'TEST_AV_CLOCK_UNVERIFIED')
    verify_visual_evidence(encode(measured),core=core,core_raw=raw,request=request,catalog=empty,
        prepared_audio=prepared,expected_model_sha256=config['modelSha256'])
    report=evaluate(model,measured)
    report.update(freshLandmarkExtraction=not retained_only,coreUnchanged=file_sha(core_path)==before[core_path],
        originalEvidenceReplayPassed=True,newObservationReplayPassed=True,providerPosts=0)
    require(all(file_sha(p)==pin for p,pin in before.items()),'TEST_ORIGINAL_CHANGED')
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    private_write(output/'MEASURED_EVIDENCE.private.json',encode(measured))
    private_write(output/'evaluation.private.json',encode(report))
    # Terminal contains scores/counts, never original transcript or frame coordinates.
    return {k:report[k] for k in ('score','scoredPhoneCount','expectedPhoneCount','visuallyEligiblePhoneCount','scoredCoverage','subScores',
        'sourceSpeakerCount','freshLandmarkExtraction','coreUnchanged','providerPosts','validationStatus')}


async def run_bound_files(model_path,core_path,alignment_path,video_path,audio_path,prepared_path,
                          output,visual_python,landmark_model,quality):
    """Fresh geometry for a designated retained test CORE, with real PCM proof.

No synthetic Backend job/grant or asserted A/V offset is created. The original
request ID and audio SHA come from CORE, and the verified decoder sample clock
must match the materialized WAV files before any phone can be scored.
"""
    from uuid import uuid4
    from voice_coach.visual_feedback.canonical_alignment import pcm_identity
    from voice_coach.visual_feedback.canonical_service import assess_aligned,SPARSE_ASSESSMENT
    from voice_coach.visual_feedback.canonical_verification import verify_visual_evidence
    from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
    from voice_coach.visual_feedback.adapters.canonical_video_features import CanonicalVideoFeatures,VideoFeatureConfiguration
    paths=(model_path,core_path,alignment_path,video_path,audio_path,prepared_path)
    before={p:file_sha(p) for p in paths}
    model=strict_json(model_path.read_bytes());raw=core_path.read_bytes();core=strict_json(raw)
    require(sha(encode(quality))==model['qualitySha256'],'CORPUS_FEATURE_PROTOCOL_MISMATCH')
    sidecar=strict_json(alignment_path.read_bytes())
    source_pcm=pcm_identity(audio_path,core['source_audio_sha256'])
    prepared_pcm=pcm_identity(prepared_path,core['prepared_wav_sha256'])
    video_sha=before[video_path]
    request=SimpleNamespace(event_id=core['request_id'],execution_id=str(uuid4()),
        audio_sha256=core['source_audio_sha256'],visual_input=SimpleNamespace(sha256=video_sha),
        media_preparation={'receipt':{'sourceSha256':video_sha}})
    port=CanonicalVideoFeatures(VideoFeatureConfiguration(visual_python,landmark_model,model['modelSha256'],quality))
    features=await port.extract(video_path=video_path,video_sha256=video_sha,byte_count=video_path.stat().st_size)
    empty_raw=(ROOT/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes()
    empty=ReferenceCatalog.from_bytes(empty_raw,sha(empty_raw))
    measured=assess_aligned(core=core,core_raw=raw,request=request,feature_value=features,source_pcm=source_pcm,
        prepared_pcm=prepared_pcm,catalog=empty,alignment_evidence=sidecar,assessment_policy=SPARSE_ASSESSMENT)
    require(measured['timeMap'] is not None,'TEST_AV_CLOCK_UNVERIFIED')
    prepared=SimpleNamespace(sha256=core['prepared_wav_sha256'],frame_count=prepared_pcm['frames'],sample_rate=16000)
    verify_visual_evidence(encode(measured),core=core,core_raw=raw,request=request,catalog=empty,
        prepared_audio=prepared,expected_model_sha256=model['modelSha256'])
    report=evaluate(model,measured)
    require(all(file_sha(p)==pin for p,pin in before.items()),'TEST_ORIGINAL_CHANGED')
    report.update(freshLandmarkExtraction=True,coreUnchanged=True,originalAlignmentBindingPassed=True,
        newObservationReplayPassed=True,providerPosts=0)
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    private_write(output/'MEASURED_EVIDENCE.private.json',encode(measured))
    private_write(output/'evaluation.private.json',encode(report))
    return {k:report[k] for k in ('score','scoredPhoneCount','expectedPhoneCount','visuallyEligiblePhoneCount',
        'scoredCoverage','subScores','sourceSpeakerCount','freshLandmarkExtraction','coreUnchanged','providerPosts','validationStatus')}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('model','output','visual-python','landmark-model'):
        p.add_argument('--'+name,type=Path,required=True)
    for name in ('job','core-root','h5-root','test-core','test-alignment','test-video','test-audio','test-prepared','configuration'):
        p.add_argument('--'+name,type=Path)
    p.add_argument('--retained-frames-only',action='store_true');a=p.parse_args()
    try:
        if a.job:
            require(a.core_root is not None and a.h5_root is not None)
            result=asyncio.run(run(a.model,a.job,a.core_root,a.h5_root,a.output,a.visual_python,
                a.landmark_model,a.retained_frames_only))
        else:
            require(all(x is not None for x in (a.test_core,a.test_alignment,a.test_video,a.test_audio,a.test_prepared,a.configuration)))
            quality=strict_json(a.configuration.read_bytes())['quality']
            result=asyncio.run(run_bound_files(a.model,a.test_core,a.test_alignment,a.test_video,a.test_audio,
                a.test_prepared,a.output,a.visual_python,a.landmark_model,quality))
        print(json.dumps(result,ensure_ascii=False))
    except Exception as error:
        print(json.dumps(dict(status='FAILED',exceptionType=type(error).__name__,providerPosts=0)))
        raise SystemExit(2)
