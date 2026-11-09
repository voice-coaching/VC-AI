"""Private, content-addressed AI-Hub 538 source/evidence compiler. No provider IO.

JSON pointers are supplied after inspecting the downloaded label version. We
never guess a speaker identity, expert label, sentence clock or transcript.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path, PurePosixPath

from .corpus_reference import (MEASUREMENTS, CorpusPolicy, build_population,
    digest, encode, measure_phone, neighbour_context, require, sha)
from .feature_protocol import MAPPING, strict_json

SOURCE_SCHEMA = 'voice-coaching.aihub538-source-manifest.v1'
PLAN_SCHEMA = 'voice-coaching.aihub538-source-plan.v1'
POINTER_FIELDS = {'speakerId','speakerType','view','noise','text','startSeconds','endSeconds'}


def ordered_tar_parts(parts):
    """Official suffixes may be part numbers OR original byte offsets."""
    require(parts and all(p.isfile() and p.size>0 and re.search(r'\.tar\.part[0-9]+$',p.name) for p in parts),
            'CORPUS_NESTED_TAR_LAYOUT')
    require(len({re.sub(r'\.part[0-9]+$','',p.name) for p in parts})==1,'CORPUS_ONE_BUNDLE_REQUIRED')
    parts=sorted(parts,key=lambda p:int(p.name.rsplit('.part',1)[1]))
    suffixes=[int(p.name.rsplit('.part',1)[1]) for p in parts]
    offsets=[];position=0
    for p in parts:offsets.append(position);position+=p.size
    require(suffixes==list(range(len(parts))) or suffixes==offsets,'CORPUS_ARCHIVE_PART_MISSING')
    return parts


def is_training_path(relative):
    return bool({'Training','1.Training'} & set(PurePosixPath(relative).parts))


def plan_from_labels(root):
    """Adapter for the actual 2022 AI-Hub 538 list-of-one JSON label layout."""
    root=Path(root).resolve(strict=True);videos={};entries=[];missing=skipped=invalid=0
    for path in sorted(root.rglob('*.mp4')):
        if not is_training_path(str(path.relative_to(root))):continue
        require(path.name not in videos,'CORPUS_AMBIGUOUS_VIDEO_FILENAME')
        videos[path.name]=path
    for label in sorted(root.rglob('*.json')):
        relative=str(label.relative_to(root))
        if (not is_training_path(relative) or not label.name.startswith('lip_')
                or label.name.count('.')!=1):continue
        raw=strict_json(label.read_bytes())
        require(type(raw) is list and len(raw)==1 and type(raw[0]) is dict,'CORPUS_LABEL_VERSION_UNKNOWN')
        value=raw[0]
        if (value['speaker_info']['Specificity']!='E' or value['Audio_env']['Noise']!=1
                or value['Video_env']['Angle']!='A'):
            skipped+=1;continue
        video=videos.get(value['Video_info']['video_Name'])
        if video is None:missing+=1;continue
        for i,sentence in enumerate(value['Sentence_info']):
            from .feature_protocol import finite
            start,end=sentence['start_time'],sentence['end_time']
            text=sentence['sentence_text']
            if (not finite(start) or not finite(end) or not 0<=start<end or not .5<=end-start<=120
                    or type(text) is not str or not text.strip()):
                invalid+=1;continue
            pointers=dict(speakerId='/0/speaker_info/speaker_ID',speakerType='/0/speaker_info/Specificity',
                noise='/0/Audio_env/Noise',view='/0/Video_env/Angle',
                text=f'/0/Sentence_info/{i}/sentence_text',startSeconds=f'/0/Sentence_info/{i}/start_time',
                endSeconds=f'/0/Sentence_info/{i}/end_time')
            entries.append(dict(video=str(video.relative_to(root)),label=relative,pointers=pointers,
                cohortValues={'speakerType':'E','view':'A','noise':1}))
    return dict(schemaVersion=PLAN_SCHEMA,datasetId='538',entries=entries),dict(
        matchedSentences=len(entries),missingVideoLabels=missing,excludedCohortLabels=skipped,
        excludedSentenceIntervals=invalid)


def private_write(path, raw):
    from uuid import uuid4
    path = Path(path)
    path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+uuid4().hex+'.tmp')
    try:
        with os.fdopen(os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.link(temporary,path,follow_symlinks=False)  # Publish complete bytes without clobber.
    finally:
        temporary.unlink(missing_ok=True)


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def contained(root, relative):
    require(type(relative) is str and '\\' not in relative,'CORPUS_PATH_INVALID')
    p=PurePosixPath(relative)
    require(not p.is_absolute() and '..' not in p.parts and bool(p.parts),'CORPUS_PATH_INVALID')
    root=Path(root).resolve(strict=True)
    path=(root/relative).resolve(strict=True)
    require(path.is_relative_to(root) and path.is_file(),'CORPUS_PATH_INVALID')
    return path


def pointer(value, path):
    require(type(path) is str and path.startswith('/'),'CORPUS_LABEL_POINTER_INVALID')
    try:
        for item in path[1:].split('/'):
            key=item.replace('~1','/').replace('~0','~')
            value=value[int(key)] if type(value) is list else value[key]
    except (KeyError,IndexError,ValueError,TypeError):
        raise ValueError('CORPUS_LABEL_POINTER_INVALID') from None
    return value


def index_sources(plan, root):
    """Only original official TRAIN assets, full frontal expert/quiet sessions.

The plan declares label value mappings and pointers; actual cohort membership,
identity, transcript and times come from the hash-bound label, not the plan.
"""
    require(type(plan) is dict and plan.get('schemaVersion')==PLAN_SCHEMA
            and plan.get('datasetId')=='538','CORPUS_SOURCE_PLAN_INVALID')
    require(type(plan.get('entries')) is list and bool(plan['entries']))
    entries=[];seen=set();hashes={};labels={}
    for spec in plan['entries']:
        require(set(spec)=={'video','label','pointers','cohortValues'} and
                set(spec['pointers'])==POINTER_FIELDS and
                set(spec['cohortValues'])=={'speakerType','view','noise'})
        video=contained(root,spec['video']);label=contained(root,spec['label'])
        # Official evaluation/validation archives never become fitted TRAIN data.
        require(is_training_path(spec['video']) and is_training_path(spec['label']),
                'CORPUS_OFFICIAL_TRAIN_REQUIRED')
        require(video.suffix.lower()=='.mp4' and label.suffix.lower()=='.json')
        if label not in labels:labels[label]=strict_json(label.read_bytes())
        metadata=labels[label]
        values={k:pointer(metadata,p) for k,p in spec['pointers'].items()}
        require(all(values[k]==spec['cohortValues'][k] for k in ('speakerType','view','noise')),
                'CORPUS_SOURCE_COHORT')
        # The exact external codes are kept with their pointers for audit;
        # operator maps the documented expert/frontal/quiet codes once.
        require(spec['cohortValues'] in ({'speakerType':'E','view':'A','noise':1},
                                        {'speakerType':'E','view':'A','noise':'1'}),
                'CORPUS_SOURCE_COHORT')
        require(type(values['speakerId']) is str and 0<len(values['speakerId'])<=200)
        require(type(values['text']) is str and bool(values['text'].strip()) and len(values['text'])<=4000)
        from .feature_protocol import finite
        start,end=values['startSeconds'],values['endSeconds']
        require(finite(start) and finite(end) and 0<=start<end and .5<=end-start<=120,
                'CORPUS_SENTENCE_CLOCK_INVALID')
        for path in (video,label):
            if path not in hashes:hashes[path]=file_sha(path)
        source_sha=hashes[video];label_sha=hashes[label]
        entry_id=sha(encode([source_sha,label_sha,start,end]))
        require(entry_id not in seen,'CORPUS_DUPLICATE_SOURCE');seen.add(entry_id)
        entries.append(dict(entryId=entry_id,video=spec['video'],label=spec['label'],
            sourceVideoSha256=source_sha,labelSha256=label_sha,
            speakerSha256=sha(('aihub538:speaker:'+values['speakerId']).encode()),
            sourcePartition='TRAIN',speakerType='EXPERT',view='A',noise='1',
            startSeconds=start,endSeconds=end,scriptText=values['text'],
            pointers=spec['pointers'],cohortValues=spec['cohortValues']))
    return dict(schemaVersion=SOURCE_SCHEMA,datasetId='538',sourcePlanSha256=sha(encode(plan)),
                sourceUrl='https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=538',entries=entries)


def verify_sources(manifest,root):
    require(manifest.get('schemaVersion')==SOURCE_SCHEMA and manifest.get('datasetId')=='538')
    # Re-derive every field from retained labels, catching later manifest edits.
    plan=dict(schemaVersion=PLAN_SCHEMA,datasetId='538',entries=[
        {k:r[k] for k in ('video','label','pointers','cohortValues')} for r in manifest['entries']])
    require(index_sources(plan,root)==manifest,'CORPUS_SOURCE_MANIFEST_MISMATCH')


def measurement_records(entry, *, manifest_sha256, core_raw, evidence_raw, catalog,
                        quality_sha256, policy=None):
    """Replay retained CORE, MFA sidecar, PCM clock and frames before deriving rows."""
    from types import SimpleNamespace
    from .canonical_alignment import anchors_from_core
    from .canonical_verification import verify_visual_evidence
    from .audiovisual_evidence import LipFrame
    policy=policy or CorpusPolicy();policy.validate()
    evidence=strict_json(evidence_raw);core=strict_json(core_raw)
    require(evidence.get('alignmentSidecar') is not None,'CORPUS_MFA_REQUIRED')
    request=SimpleNamespace(event_id=evidence['requestId'],execution_id=evidence['executionId'],
        audio_sha256=evidence['audioSha256'],visual_input=SimpleNamespace(sha256=evidence['videoSha256']),
        media_preparation={'receipt':{'sourceSha256':entry['clipSha256']}})
    prepared=SimpleNamespace(sha256=evidence['preparedWavSha256'],
        frame_count=evidence['preparedPcm']['frames'],sample_rate=evidence['preparedPcm']['sampleRate'])
    verify_visual_evidence(evidence_raw,core=core,request=request,catalog=catalog,
        prepared_audio=prepared,core_raw=core_raw,expected_model_sha256=evidence['decoder']['modelSha256'])
    require(evidence['timeMap'] is not None,'CORPUS_AV_CLOCK_UNVERIFIED')
    require(evidence['assessmentPolicy']=='PHONE_ROLE_SPARSE_30FPS_V1' and not catalog.scoring_enabled)
    sidecar=evidence['alignmentSidecar']
    anchors=anchors_from_core(core,request_id=evidence['requestId'],audio_sha256=evidence['audioSha256'],
        prepared_sha256=evidence['preparedWavSha256'],alignment_evidence=sidecar,
        core_sha256=sha(core_raw),core_raw=core_raw,recover_words=True).anchors
    anchors={a.expected_index:a for a in anchors}
    frames=tuple(LipFrame(r['pts'],tuple(map(tuple,r['landmarks'])),tuple(r['features']),r['reason'] is None)
                 for r in evidence['frames'])
    records=[]
    for row in evidence['visual']['phoneAssessments']:
        index=row['expectedIndex'];anchor=anchors.get(index)
        if (anchor is None or row['videoStartSeconds'] is None or row['status']!='OBSERVED'
                or 'MULTIPLE_FACES' in row['reasonCodes']):continue
        context=neighbour_context(sidecar['alignment']['rows'],index)
        metrics,_=measure_phone(anchor,frames,start=row['videoStartSeconds'],end=row['videoEndSeconds'],
                                context=context,policy=policy)
        if not metrics:continue
        records.append(dict(schemaVersion=MEASUREMENTS,datasetId='538',sourcePartition='TRAIN',
            speakerType='EXPERT',view='A',noise='1',datasetManifestSha256=manifest_sha256,
            speakerSha256=entry['speakerSha256'],sourceVideoSha256=entry['sourceVideoSha256'],
            normalizedVideoSha256=evidence['videoSha256'],
            entryId=entry['entryId'],occurrenceId=sha(encode([entry['entryId'],index])),coreSha256=sha(core_raw),
            alignmentSha256=sha(encode(sidecar)),mappingRevision=MAPPING.revision,
            modelSha256=evidence['decoder']['modelSha256'],qualitySha256=quality_sha256,
            phone=anchor.expected_phone,role=anchor.role,
            metrics=[dict(criterionId=k[0],phaseBin=k[1],contextId=k[2],value=v) for k,v in sorted(metrics.items())]))
    return records


def load_measurements(manifest_raw, root, measurements_root, configuration, empty_catalog_raw,
                      *, policy=None,allow_pending=False):
    """All statistics rebuilt from verified source/retained evidence, never CSV claims."""
    from .reference_catalog import ReferenceCatalog
    manifest=strict_json(manifest_raw);verify_sources(manifest,root)
    empty=ReferenceCatalog.from_bytes(empty_catalog_raw,sha(empty_catalog_raw))
    require(not empty.scoring_enabled and not empty.references and not empty.calibrations)
    quality_sha=sha(encode(configuration['quality']));records=[];receipts=[];failures=pending=0
    for entry in manifest['entries']:
        folder=Path(measurements_root)/entry['entryId']
        if (allow_pending and not (folder/'corpus-receipt.json').exists()
                and not (folder/'failure.json').exists()):pending+=1;continue
        if not (folder/'corpus-receipt.json').exists() and (folder/'failure.json').is_file():
            failure=strict_json((folder/'failure.json').read_bytes())
            require(failure.get('providerPosts')==0)
            failures+=1;continue
        receipt_raw=(folder/'corpus-receipt.json').read_bytes();receipt=strict_json(receipt_raw)
        require(receipt['entryId']==entry['entryId'] and receipt['datasetManifestSha256']==sha(manifest_raw)
                and receipt['providerPosts']==0 and receipt['sourceVideoSha256']==entry['sourceVideoSha256'])
        require(receipt['modelSha256']==configuration['modelSha256'] and receipt['qualitySha256']==quality_sha)
        require(set(receipt['artifacts'])=={'CORE.json','VISUAL_EVIDENCE.json','MFA.json','MEDIA_RECEIPT.json'})
        require(all(file_sha(contained(folder,relative))==pin for relative,pin in receipt['artifacts'].items()))
        evidence=strict_json((folder/'VISUAL_EVIDENCE.json').read_bytes())
        media=strict_json((folder/'MEDIA_RECEIPT.json').read_bytes())
        require(evidence['decoder']['modelSha256']==configuration['modelSha256']
                and media['sourceSha256']==receipt['clipSha256']
                and media['canonicalVideoSha256']==evidence['videoSha256']==receipt['normalizedVideoSha256']
                and media['canonicalPcmSha256']==evidence['audioSha256'],'CORPUS_MEDIA_PROVENANCE_MISMATCH')
        from uuid import UUID
        execution=str(UUID(evidence['executionId']));require(execution==evidence['executionId'])
        case_path=contained(configuration['residentWorkspaceRoot'],f'{execution}/core/{execution}/admitted-case.json')
        case=strict_json(case_path.read_bytes())
        require(len(case)==1 and case[0]['executionId']==execution and case[0]['id']==evidence['requestId']
                and case[0]['script']==entry['scriptText'],'CORPUS_TRANSCRIPT_PROVENANCE_MISMATCH')
        entry=dict(entry,clipSha256=receipt['clipSha256'])
        records.extend(measurement_records(entry,manifest_sha256=sha(manifest_raw),
            core_raw=(folder/'CORE.json').read_bytes(),evidence_raw=(folder/'VISUAL_EVIDENCE.json').read_bytes(),
            catalog=empty,quality_sha256=quality_sha,policy=policy))
        receipts.append(sha(receipt_raw))
    return records,dict(receiptSha256s=receipts,sourceFailureCount=failures,pendingEntryCount=pending)


def compile_catalog(manifest_raw, root, measurements_root, configuration, empty_catalog_raw,
                    *, revision, policy=None):
    """Only a full measured manifest can produce a production candidate catalog."""
    from .reference_catalog import ReferenceCatalog
    records,report=load_measurements(manifest_raw,root,measurements_root,configuration,empty_catalog_raw,policy=policy)
    quality_sha=sha(encode(configuration['quality']))
    population=build_population(records,dataset_manifest_sha256=sha(manifest_raw),
        model_sha256=configuration['modelSha256'],quality_sha256=quality_sha,revision=revision,policy=policy)
    base=strict_json(empty_catalog_raw)
    base.update(schemaVersion='voice-coaching.lip-reference-catalog.v5',revision=revision,
        contextPolicy='CORPUS_PHASE_AND_NEIGHBOUR_V1',minimumCoverage=.6,population=population)
    raw=encode(base)
    # A failed fit yields a report only, never an installable active catalog.
    if population['validationStatus']=='PASSED':ReferenceCatalog.from_bytes(raw,sha(raw))
    return raw,dict(datasetManifestSha256=sha(manifest_raw),**report,
        providerPosts=0,validationStatus=population['validationStatus'],
        scoredGroupCount=len(population['statistics']),
        missingGroupCount=len(population['unavailableGroups']))
