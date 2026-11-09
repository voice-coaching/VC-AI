"""Build AI-Hub 538 lip references privately. No OpenAI key or calls required."""
import argparse
import asyncio
import json
import io
from pathlib import Path
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from voice_coach.visual_feedback.corpus_dataset import (compile_catalog,index_sources,plan_from_labels,load_measurements,
    file_sha,private_write,ordered_tar_parts)
from voice_coach.visual_feedback.corpus_reference import encode,require,sha
from voice_coach.visual_feedback.feature_protocol import strict_json


def unpack(archive,receipt_path,output,maximum_bytes,nested_tar=False):
    """Verify completed official download, admit regular files without traversal."""
    receipt=strict_json(receipt_path.read_bytes())
    # Receipt names are those of download_aihub_secure.py, not user-supplied URLs.
    require(receipt.get('schema_version')=='voice_coach.aihub_secure_download_receipt.v1'
            and str(receipt.get('dataset_key'))=='538','CORPUS_DOWNLOAD_RECEIPT_REQUIRED')
    require(receipt.get('archive_sha256')==file_sha(archive)
            and receipt.get('archive_size_bytes')==archive.stat().st_size,'CORPUS_DOWNLOAD_SHA_MISMATCH')
    require(not output.exists() and maximum_bytes>0)
    if nested_tar:
        import re
        with tarfile.open(archive,'r:*') as outer:
            parts=ordered_tar_parts([m for m in outer.getmembers() if m.isfile()])
            class Joined(io.RawIOBase):
                def __init__(self):self.index=0;self.stream=outer.extractfile(parts[0])
                def readable(self):return True
                def readinto(self,buffer):
                    while self.index<len(parts):
                        raw=self.stream.read(len(buffer))
                        if raw:buffer[:len(raw)]=raw;return len(raw)
                        self.stream.close();self.index+=1
                        if self.index<len(parts):self.stream=outer.extractfile(parts[self.index])
                    return 0
            output.mkdir(parents=True,mode=0o700)
            total=count=0;names=set()
            with io.BufferedReader(Joined()) as joined,tarfile.open(fileobj=joined,mode='r|*') as inner:
                for m in inner:
                    p=Path(m.name)
                    require(not p.is_absolute() and '..' not in p.parts and '\\' not in m.name
                        and (m.isfile() or m.isdir()) and m.name not in names,'CORPUS_ARCHIVE_PATH_INVALID')
                    names.add(m.name);total+=m.size
                    require(total<=maximum_bytes,'CORPUS_ARCHIVE_BUDGET')
                    target=output/p
                    if m.isdir():target.mkdir(parents=True,mode=0o700,exist_ok=True);continue
                    import os,shutil
                    target.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
                    with inner.extractfile(m) as src,os.fdopen(os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as dst:
                        shutil.copyfileobj(src,dst,1024**2)
                    count+=1
            return dict(status='UNPACKED',fileCount=count,bytes=total,providerPosts=0)
    with tarfile.open(archive,'r:*') as tf:
        members=tf.getmembers();total=0;names=set()
        for m in members:
            p=Path(m.name)
            require(not p.is_absolute() and '..' not in p.parts and '\\' not in m.name
                    and (m.isfile() or m.isdir()),'CORPUS_ARCHIVE_PATH_INVALID')
            require(m.name not in names,'CORPUS_ARCHIVE_DUPLICATE');names.add(m.name)
            total+=m.size;require(total<=maximum_bytes,'CORPUS_ARCHIVE_BUDGET')
        output.mkdir(mode=0o700,parents=True)
        for m in members:
            p=output/m.name
            if m.isdir():p.mkdir(mode=0o700,parents=True,exist_ok=True);continue
            p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            import os,shutil
            with tf.extractfile(m) as source,os.fdopen(os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as dest:
                shutil.copyfileobj(source,dest,1024**2)
    return dict(status='UNPACKED',fileCount=sum(m.isfile() for m in members),bytes=total,providerPosts=0)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    u=sub.add_parser('unpack');u.add_argument('--archive',type=Path,required=True)
    u.add_argument('--receipt',type=Path,required=True);u.add_argument('--output',type=Path,required=True)
    u.add_argument('--maximum-bytes',type=int,required=True)
    u.add_argument('--nested-tar',action='store_true')
    i=sub.add_parser('index');i.add_argument('--plan',type=Path,required=True)
    i.add_argument('--source-root',type=Path,required=True);i.add_argument('--output',type=Path,required=True)
    s=sub.add_parser('plan');s.add_argument('--source-root',type=Path,required=True)
    s.add_argument('--output',type=Path,required=True)
    for action in ('extract','recover','compile','train-research-model'):
        s=sub.add_parser(action);s.add_argument('--manifest',type=Path,required=True)
        s.add_argument('--source-root',type=Path,required=True);s.add_argument('--measurements-root',type=Path,required=True)
        s.add_argument('--configuration',type=Path,required=True);s.add_argument('--empty-catalog',type=Path,
            default=ROOT/'deploy/dlpc/audiovisual/reference-catalog.json')
        if action=='extract':s.add_argument('--limit',type=int)
        elif action=='recover':pass
        elif action=='compile':
            s.add_argument('--revision',required=True);s.add_argument('--output',type=Path,required=True)
        else:
            s.add_argument('--output',type=Path,required=True);s.add_argument('--minimum-observations',type=int,default=5)
            s.add_argument('--additional-datasets',type=Path)
    a=p.parse_args(argv)
    if a.action=='unpack':result=unpack(a.archive,a.receipt,a.output,a.maximum_bytes,a.nested_tar)
    elif a.action=='plan':
        value,result=plan_from_labels(a.source_root);private_write(a.output,encode(value))
        result.update(status='PLANNED' if value['entries'] else 'NO_MATCHED_MEDIA',providerPosts=0)
    elif a.action=='index':
        value=index_sources(strict_json(a.plan.read_bytes()),a.source_root)
        private_write(a.output,encode(value))
        result=dict(status='INDEXED',entries=len(value['entries']),sha256=sha(encode(value)),providerPosts=0)
    else:
        config=strict_json(a.configuration.read_bytes());manifest_raw=a.manifest.read_bytes()
        if a.action=='extract':
            from voice_coach.visual_feedback.corpus_extract import extract
            require(a.limit is None or a.limit>0)
            result=asyncio.run(extract(manifest_raw,a.source_root,a.measurements_root,config,
                                      a.empty_catalog.read_bytes(),limit=a.limit))
        elif a.action=='recover':
            from voice_coach.visual_feedback.corpus_recovery import recover
            result=asyncio.run(recover(manifest_raw,a.source_root,a.measurements_root,config,a.empty_catalog.read_bytes()))
        elif a.action=='compile':
            raw,result=compile_catalog(manifest_raw,a.source_root,a.measurements_root,config,
                a.empty_catalog.read_bytes(),revision=a.revision)
            if result['validationStatus']=='PASSED':private_write(a.output,raw)
            private_write(a.output.with_suffix('.report.json'),encode(result))
        else:
            from voice_coach.visual_feedback.corpus_research_model import fit,cross_speaker_diagnostics
            records,result=load_measurements(manifest_raw,a.source_root,a.measurements_root,config,
                a.empty_catalog.read_bytes(),allow_pending=True)
            additional_reports=[]
            if a.additional_datasets:
                specs=strict_json(a.additional_datasets.read_bytes())
                require(type(specs) is list and len(specs)<=32)
                for spec in specs:
                    require(set(spec)=={'manifest','sourceRoot','measurementsRoot','configuration'})
                    rows,report=load_measurements(Path(spec['manifest']).read_bytes(),Path(spec['sourceRoot']),
                        Path(spec['measurementsRoot']),strict_json(Path(spec['configuration']).read_bytes()),
                        a.empty_catalog.read_bytes(),allow_pending=True)
                    records.extend(rows);additional_reports.append(report)
            model=fit(records,minimum_observations=a.minimum_observations)
            model['crossSpeakerEvaluation']=cross_speaker_diagnostics(records,minimum_observations=a.minimum_observations)
            model['crossSpeakerEvaluationPerformed']=model['crossSpeakerEvaluation']['status']=='EVALUATED'
            private_write(a.output,encode(model))
            result.update(status='RESEARCH_MODEL_TRAINED',modelSha256=sha(encode(model)),
                sourceSpeakerCount=model['sourceSpeakerCount'],sourceSentenceCount=model['sourceSentenceCount'],
                trainSentenceCount=model['trainSentenceCount'],heldOutSentenceCount=model['heldOutSentenceCount'],
                learnedGroupCount=len(model['statistics']),additionalDatasets=additional_reports,
                productionValidated=False,providerPosts=0)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result.get('validationStatus','PASSED')=='PASSED' and not result.get('failed') and not result.get('nonReplayableFailures') else 2


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError,KeyError,TypeError) as error:
        # Label text, raw paths, upstream response bodies and credentials stay private.
        print(json.dumps(dict(status='FAILED',exceptionType=type(error).__name__,providerPosts=0)))
        raise SystemExit(2)
