"""Package pinned public YuNet and the existing mesh as an opt-in candidate."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from voice_coach.visual_feedback.adapters.yunet_landmarks import FILES, PINS, SCHEMA, REVISION, read_bundle


def build(source, destination):
    files = {name: (source/name).read_bytes() for name in FILES}
    if any(hashlib.sha256(files[name]).hexdigest() != pin for name,pin in PINS.items()):
        raise ValueError('YUNET_SOURCE_DIGEST_MISMATCH')
    manifest = dict(schemaVersion=SCHEMA, revision=REVISION, files=PINS,
        detection='YUNET', landmarkInference='DIRECT_LITERT_NO_BLAZEFACE', roiScale=1.5,
        maximumDetectionDimension=640, headPosePolicy='CANONICAL_FACE_PNP_APPROXIMATE_V1',
        referenceRecalibrationRequired=True)
    files['yunet-manifest.json'] = json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()+b'\n'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as archive:
        for name, raw in sorted(files.items()):
            info=zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
            archive.writestr(info,raw)
    raw=stream.getvalue()
    read_bundle(raw)
    with destination.open('xb') as output:
        output.write(raw)
    destination.chmod(0o600)
    return dict(revision=REVISION,modelSha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),
                referenceRecalibrationRequired=True,productionActivated=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.source,a.destination)))
