"""Read designated local videos with two isolated feature workers; no audio analysis or GPT."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from voice_coach.visual_feedback.adapters.canonical_video_features import CanonicalVideoFeatures, VideoFeatureConfiguration

sha=lambda raw:hashlib.sha256(raw).hexdigest()


async def compare(args):
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    quality=dict(minimumFaceScalePixels=24,maximumYawDegrees=30,maximumPitchDegrees=20,
                 detectionConfidence=.7,presenceConfidence=.7,trackingConfidence=.7)
    report=dict(schemaVersion='voice-coaching.face-extractor-comparison.v1',providerPosts=0,
        productionActivated=False,quality=quality,videos=[],sourceFileDigests={
            name:sha((ROOT/name).read_bytes()) for name in (
                'src/voice_coach/visual_feedback/adapters/canonical_feature_worker.py',
                'src/voice_coach/visual_feedback/adapters/yunet_landmarks.py',
                'scripts/compare_face_extractors.py')})
    for index,video in enumerate(args.video,1):
        raw=video.read_bytes();pin=sha(raw)
        row=dict(test=f'testvideo{index}',videoSha256=pin,bytes=len(raw),extractors={})
        frame_sets={}
        for lane in ('legacy','candidate'):
            model=getattr(args,lane+'_model');python=getattr(args,lane+'_python')
            configuration=VideoFeatureConfiguration(python,model,sha(model.read_bytes()),quality)
            start=time.monotonic()
            evidence=await asyncio.wait_for(CanonicalVideoFeatures(configuration).extract(
                video_path=video,video_sha256=pin,byte_count=len(raw)),timeout=180)
            seconds=time.monotonic()-start
            frames=evidence['features']['frames'];frame_sets[lane]=frames
            body=(json.dumps(evidence,allow_nan=False,separators=(',',':'))+'\n').encode()
            path=args.output/f'testvideo{index}-{lane}.private.json';path.write_bytes(body);path.chmod(0o600)
            counts=Counter(f['reason'] or 'OBSERVABLE' for f in frames)
            row['extractors'][lane]=dict(modelSha256=configuration.model_sha256,frameCount=len(frames),
                reasonCounts=dict(counts),observableCount=counts['OBSERVABLE'],
                observationRate=counts['OBSERVABLE']/len(frames),
                featureExtractionWallSeconds=round(seconds,4),
                amortizedWallMsPerDecodedFrame=round(1000*seconds/len(frames),4),
                evidenceSha256=sha(body))
        assert [f['pts'] for f in frame_sets['legacy']]==[f['pts'] for f in frame_sets['candidate']]
        common=[(a,b) for a,b in zip(frame_sets['legacy'],frame_sets['candidate'])
                if a['reason'] is None and b['reason'] is None]
        row['sharedObservableFrames']=len(common)
        row['meanAbsoluteFeatureDelta']=[sum(abs(a['features'][i]-b['features'][i]) for a,b in common)/len(common)
                                        for i in range(3)] if common else None
        assert sha(video.read_bytes())==pin
        report['videos'].append(row)
        print(json.dumps(row),flush=True)
    report['completed']=True
    path=args.output/'comparison.json';path.write_text(json.dumps(report,indent=2)+'\n');path.chmod(0o600)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    for lane in ('legacy','candidate'):
        for kind in ('model','python'):p.add_argument('--'+lane+'-'+kind,type=Path,required=True)
    asyncio.run(compare(p.parse_args()))
