"""Offline score/prompt replay of retained evidence; never decodes or calls GPT."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from voice_coach.backend_analysis.canonical_bridge import FrozenCanonicalArtifacts, FrozenCanonicalCoaching
from voice_coach.visual_feedback.canonical_service import assess_aligned, SPARSE_ASSESSMENT
from voice_coach.visual_feedback.canonical_verification import verify_visual_evidence
from voice_coach.visual_feedback.coaching_input import prepare_visual_coaching
from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
from voice_coach.visual_feedback.feature_protocol import PROTOCOL, MAPPING


def encode(v):
    return (json.dumps(v,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))+'\n').encode()


def replay(job: Path, core_root: Path, h5_root: Path, output: Path):
    # Never write into the source job or reuse its provider response as a new one.
    job=job.resolve(strict=True);output=output.resolve()
    if output.is_relative_to(job) or job.is_relative_to(output):
        raise ValueError('REPLAY_REQUIRES_SEPARATE_DIRECTORY')
    originals={p:p.read_bytes() for p in (job/'artifacts').iterdir() if p.is_file()}
    originals.update({p:p.read_bytes() for p in (job/'job.json',job/'result.json')})
    before={p:hashlib.sha256(v).hexdigest() for p,v in originals.items()}
    raw=originals[job/'artifacts/CORE.json'];core=json.loads(raw)
    old_raw=originals[job/'artifacts/VISUAL_EVIDENCE.json'];old=json.loads(old_raw)
    receipt=json.loads(originals[job/'artifacts/MEDIA_RECEIPT.json'])
    assets=FrozenCanonicalArtifacts(core_root,h5_root)
    lock=assets.verify()
    catalog=ReferenceCatalog.load(h5_root/'contracts/visual/reference-catalog.json',
                                 lock['files']['contracts/visual/reference-catalog.json'])
    request=SimpleNamespace(event_id=old['requestId'],execution_id=old['executionId'],
        audio_sha256=old['audioSha256'],visual_input=SimpleNamespace(sha256=old['videoSha256']),
        media_preparation={'receipt':receipt})
    prepared=SimpleNamespace(sha256=old['preparedWavSha256'],frame_count=old['preparedPcm']['frames'],
                             sample_rate=old['preparedPcm']['sampleRate'])
    verify_args=dict(core=core,request=request,catalog=catalog,prepared_audio=prepared,
        core_raw=raw,expected_model_sha256=old['decoder']['modelSha256'])
    verify_visual_evidence(old_raw,**verify_args)
    features=dict(old['decoder'],features=dict(protocol=PROTOCOL,mappingRevision=MAPPING.revision,frames=old['frames']))
    new=assess_aligned(core=core,request=request,feature_value=features,
        source_pcm=old['sourcePcm'],prepared_pcm=old['preparedPcm'],catalog=catalog,
        alignment_evidence=old.get('alignmentSidecar'),core_raw=raw,assessment_policy=SPARSE_ASSESSMENT)
    new_raw=encode(new)
    verify_visual_evidence(new_raw,**verify_args)
    visual=prepare_visual_coaching(new_raw,core_bytes=raw)
    doc=json.loads((job/'job.json').read_bytes())
    binding=json.loads(originals[job/'artifacts/BINDING.json'])
    bridge=FrozenCanonicalCoaching(assets)._bridge()
    _,_,payload,reason=bridge.prepare(raw,doc['metadata']['scriptText'],binding,visual=visual)
    if payload is None or reason is not None:
        raise ValueError('REPLAY_PROMPT_UNAVAILABLE')
    request_value=json.loads(payload)
    if request_value['messages'][-1]['content'].encode()!=raw:
        raise ValueError('REPLAY_CORE_CHANGED')
    supplemental=json.loads(request_value['messages'][1]['content'])
    targets=supplemental['FEEDBACK_TARGETS']
    report=dict(policy=SPARSE_ASSESSMENT,coreUnchanged=True,historicalReplayPassed=True,
        newEvidenceReplayPassed=True,frozenPromptPrepared=True,
        providerPosts=0,model=request_value['model'],reasoningEffort=request_value['reasoning_effort'],
        serviceTier=request_value['service_tier'],
        before={k:old['visual'][k] for k in ('score','coverage')},
        after={k:new['visual'][k] for k in ('score','coverage')},
        feedbackTargetCount=len(targets),lipFeedbackTargetCount=sum(t['criterionId'].startswith('lip_') for t in targets),
        feedbackPracticeScopes=[t['practiceScope'] for t in targets if 'practiceScope' in t],
        audioVisualGrade=supplemental['AUDIOVISUAL_RESULT'],
        coreSha256=hashlib.sha256(raw).hexdigest(),visualEvidenceSha256=hashlib.sha256(new_raw).hexdigest(),
        h5ManifestSha256=assets.identity.manifest_sha256)
    source_files=sorted((ROOT/'src/voice_coach/visual_feedback').rglob('*.py')) + [
        ROOT/'docs/contracts/runpod_visual_coaching_input_v1.schema.json',
        ROOT/'src/voice_coach/six_dimension/visual.py',Path(__file__).resolve()]
    report['candidateSourceSha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in source_files}
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=pin for p,pin in before.items()):
        raise ValueError('REPLAY_SOURCE_CHANGED')
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    for name,content in [('VISUAL_EVIDENCE.private.json',new_raw),('verification.json',encode(report))]:
        with os.fdopen(os.open(output/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as stream:
            stream.write(content)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job',type=Path,required=True)
    p.add_argument('--core-root',type=Path,required=True)
    p.add_argument('--h5-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(replay(a.job,a.core_root,a.h5_root,a.output),ensure_ascii=False))
