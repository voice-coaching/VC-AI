"""Re-measure the pinned 60-sentence corpus; reuse original CORE/MFA bytes, zero GPT."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from voice_coach.visual_feedback.corpus_dataset import measurement_records, verify_sources, private_write
from voice_coach.visual_feedback.corpus_reference import encode,sha,require
from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
from voice_coach.visual_feedback.canonical_service import assess_aligned,SPARSE_ASSESSMENT
from voice_coach.visual_feedback.corpus_research_model import fit,cross_speaker_diagnostics
from voice_coach.visual_feedback.adapters.canonical_video_features import CanonicalVideoFeatures,VideoFeatureConfiguration

SOURCE_BUNDLE_SHA='f0f661976de70e83892aa064720d59ece77f6c03110544dbddcbf84ae55ff4f1'


async def run(a):
    original=a.bundle.read_bytes();require(sha(original)==SOURCE_BUNDLE_SHA,'REFERENCE_SOURCE_BUNDLE_CHANGED')
    bundle=json.loads(original);require(bundle['encoding']=='UTF8_ORIGINAL_BYTES')
    files={}
    for name,item in bundle['files'].items():
        raw=item['rawUtf8'].encode('utf-8');require(sha(raw)==item['sha256'],'REFERENCE_SOURCE_ARTIFACT_CHANGED')
        files[name]=raw
    manifests={name.split('/')[0]:json.loads(raw) for name,raw in files.items() if name.endswith('/manifest.private.json')}
    for manifest in manifests.values():verify_sources(manifest,a.source_root)
    index={}
    for directory in a.clip_root:
        for path in directory.rglob('video.mp4'):
            index[sha(path.read_bytes())]=path
    cases=[]
    for name,raw in sorted(files.items()):
        if not name.endswith('/corpus-receipt.json'):continue
        group,entry_id,_=name.split('/')
        entry=next(e for e in manifests[group]['entries'] if e['entryId']==entry_id)
        prefix=group+'/'+entry_id+'/'
        receipt=json.loads(raw)
        require(receipt['providerPosts']==0 and receipt['datasetManifestSha256']==sha(files[group+'/manifest.private.json']))
        require(all(sha(files[prefix+n])==pin for n,pin in receipt['artifacts'].items()))
        old=json.loads(files[prefix+'VISUAL_EVIDENCE.json'])
        require(old['videoSha256'] in index,'REFERENCE_NORMALIZED_VIDEO_MISSING')
        cases.append((group,entry,receipt,old,prefix,index[old['videoSha256']]))
    require(len(cases)==60 and len({e['entryId'] for _,e,*_ in cases})==60,'REFERENCE_COHORT_CHANGED')
    a.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    empty_raw=(ROOT/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes()
    empty=ReferenceCatalog.from_bytes(empty_raw,sha(empty_raw))
    quality=dict(minimumFaceScalePixels=24,maximumYawDegrees=30,maximumPitchDegrees=20,
                 detectionConfidence=.7,presenceConfidence=.7,trackingConfidence=.7)
    configuration=VideoFeatureConfiguration(a.visual_python,a.model,sha(a.model.read_bytes()),quality)
    records=[];rows=[]
    for number,(group,entry,receipt,old,prefix,video) in enumerate(cases,1):
        core_raw=files[prefix+'CORE.json'];core=json.loads(core_raw)
        manifest_sha=receipt['datasetManifestSha256'];bound_entry=dict(entry,clipSha256=receipt['clipSha256'])
        # Replay original provenance first, before inferring any new geometry.
        measurement_records(bound_entry,manifest_sha256=manifest_sha,core_raw=core_raw,
            evidence_raw=files[prefix+'VISUAL_EVIDENCE.json'],catalog=empty,quality_sha256=receipt['qualitySha256'])
        folder=a.output/group/entry['entryId'];folder.mkdir(mode=0o700,parents=True)
        feature=await CanonicalVideoFeatures(configuration).extract(video_path=video,
            video_sha256=old['videoSha256'],byte_count=video.stat().st_size)
        request=SimpleNamespace(event_id=old['requestId'],execution_id=old['executionId'],
            audio_sha256=old['audioSha256'],visual_input=SimpleNamespace(sha256=old['videoSha256']),
            media_preparation={'receipt':{'sourceSha256':receipt['clipSha256']}})
        evidence=assess_aligned(core=core,core_raw=core_raw,request=request,feature_value=feature,
            source_pcm=old['sourcePcm'],prepared_pcm=old['preparedPcm'],catalog=empty,
            alignment_evidence=json.loads(files[prefix+'MFA.json']),assessment_policy=SPARSE_ASSESSMENT)
        measured=measurement_records(bound_entry,manifest_sha256=manifest_sha,core_raw=core_raw,
            evidence_raw=encode(evidence),catalog=empty,quality_sha256=sha(encode(quality)))
        require(bool(measured),'REFERENCE_ENTRY_NO_MEASUREMENTS')
        records.extend(measured)
        artifacts={'CORE.json':core_raw,'MFA.json':files[prefix+'MFA.json'],
            'MEDIA_RECEIPT.json':files[prefix+'MEDIA_RECEIPT.json'],'VISUAL_EVIDENCE.json':encode(evidence)}
        for name,body in artifacts.items():private_write(folder/name,body)
        row=dict(entryId=entry['entryId'],sourceGroup=group,sourceCorpusReceiptSha256=sha(files[prefix+'corpus-receipt.json']),
            normalizedVideoSha256=old['videoSha256'],sourceCoreSha256=sha(core_raw),coreBytesPreserved=True,
            modelSha256=configuration.model_sha256,artifacts={k:sha(v) for k,v in artifacts.items()},
            measurementRecordCount=len(measured),providerPosts=0)
        private_write(folder/'reextraction-receipt.json',encode(row));rows.append(row)
        require(sha(video.read_bytes())==old['videoSha256'],'REFERENCE_VIDEO_CHANGED')
        print(json.dumps(dict(completed=number,total=60,measurementRecords=len(records),providerPosts=0)),flush=True)
    model=fit(records,minimum_observations=5)
    model['crossSpeakerEvaluation']=cross_speaker_diagnostics(records,minimum_observations=5)
    model['crossSpeakerEvaluationPerformed']=model['crossSpeakerEvaluation']['status']=='EVALUATED'
    require(model['sourceSentenceCount']==60 and model['trainSentenceCount']==48
            and model['heldOutSentenceCount']==12 and model['sourceSpeakerCount']==3,'REFERENCE_SPLIT_CHANGED')
    require(sha(a.bundle.read_bytes())==SOURCE_BUNDLE_SHA,'REFERENCE_SOURCE_BUNDLE_CHANGED')
    private_write(a.output/'model.json',encode(model))
    private_write(a.output/'measurements.private.json',encode(records))
    private_write(a.output/'reference-receipt.json',encode(dict(passed=True,sourceBundleSha256=SOURCE_BUNDLE_SHA,
        modelSha256=sha(encode(model)),extractorModelSha256=configuration.model_sha256,
        sourceSentenceCount=60,trainSentenceCount=48,heldOutSentenceCount=12,sourceSpeakerCount=3,
        learnedGroupCount=len(model['statistics']),providerPosts=0,coreReplays=0,entries=rows)))
    print(json.dumps(dict(passed=True,sourceSentenceCount=60,learnedGroupCount=len(model['statistics']),
                         modelSha256=sha(encode(model)),providerPosts=0,coreReplays=0)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('bundle','source-root','model','visual-python','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--clip-root',type=Path,action='append',required=True)
    try:asyncio.run(run(p.parse_args()))
    except Exception as e:
        print(json.dumps(dict(status='FAILED_NO_RETRY',exceptionType=type(e).__name__,
            code=str(e) if str(e).isupper() and len(str(e))<100 else None,providerPosts=0)),flush=True)
        raise SystemExit(2)
