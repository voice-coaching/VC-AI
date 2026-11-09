"""Reconcile ONLY this corpus's interrupted private extraction. No CORE replay.

Completed CORE+MFA outputs may be joined to fresh frame measurements. An
ambiguous native execution without complete output is marked, never retried.
Pre-CORE scratch is moved intact; a later explicit extract may start it once.
"""
import fcntl
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4,UUID

from .corpus_dataset import file_sha,private_write,verify_sources,contained
from .corpus_reference import encode,sha,require
from .feature_protocol import strict_json


async def recover_one(entry,manifest_sha,folder,config,empty_raw):
    from ..backend_analysis.canonical_bridge import FrozenCanonicalArtifacts
    from ..backend_analysis.canonical_codec import validate_document
    from .adapters.canonical_video_features import CanonicalVideoFeatures,VideoFeatureConfiguration
    from .canonical_alignment import pcm_identity
    from .canonical_service import assess_aligned,SPARSE_ASSESSMENT
    from .canonical_verification import verify_visual_evidence
    from .reference_catalog import ReferenceCatalog
    started=time.monotonic();journal=strict_json((folder/'producer-started.json').read_bytes())
    execution=str(UUID(journal['executionId']));request_id=str(UUID(journal['requestId']))
    root=Path(config['residentWorkspaceRoot']);work=root/execution
    case=strict_json(contained(root,f'{execution}/core/{execution}/admitted-case.json').read_bytes())
    require(len(case)==1 and case[0]['id']==request_id and case[0]['script']==entry['scriptText'])
    metadata=strict_json((folder/'normalized-input.json').read_bytes())
    require(metadata['scriptText']==entry['scriptText'])
    base=f'{execution}/core/{execution}/release-output/candidate'
    native=contained(root,base+'/results/'+request_id+'.json')
    side_path=contained(root,base+'/results/'+request_id+'.alignment.json')
    core_raw=native.read_bytes();side_raw=side_path.read_bytes();core=strict_json(core_raw)
    validate_document(core,schema_name='parent')
    require(core['request_id']==request_id and core['source_audio_sha256']==metadata['audio']['sha256'])
    assets=FrozenCanonicalArtifacts(Path(config['coreRoot']),Path(config['h5Root']));assets.verify()
    prepared=contained(root,base+'/prepared_cache/'+request_id+'.wav')
    source_pcm=pcm_identity(work/'media/audio.wav',metadata['audio']['sha256'])
    prepared_pcm=pcm_identity(prepared,core['prepared_wav_sha256'])
    require(file_sha(work/'media/video.mp4')==metadata['video']['sha256'])
    request=SimpleNamespace(event_id=request_id,execution_id=execution,audio_sha256=metadata['audio']['sha256'],
        visual_input=SimpleNamespace(sha256=metadata['video']['sha256']),media_preparation=metadata['mediaPreparation'])
    empty=ReferenceCatalog.from_bytes(empty_raw,sha(empty_raw))
    identity=SimpleNamespace(sha256=core['prepared_wav_sha256'],frame_count=prepared_pcm['frames'],sample_rate=16000)
    if (folder/'VISUAL_EVIDENCE.json').exists():
        evidence_raw=(folder/'VISUAL_EVIDENCE.json').read_bytes()
    else:
        port=CanonicalVideoFeatures(VideoFeatureConfiguration(Path(config['visualPython']),Path(config['landmarkModel']),
            config['modelSha256'],config['quality']))
        features=await port.extract(video_path=work/'media/video.mp4',video_sha256=metadata['video']['sha256'],
            byte_count=metadata['video']['fileSizeBytes'])
        evidence_raw=encode(assess_aligned(core=core,core_raw=core_raw,request=request,feature_value=features,
            source_pcm=source_pcm,prepared_pcm=prepared_pcm,catalog=empty,
            alignment_evidence=strict_json(side_raw),assessment_policy=SPARSE_ASSESSMENT))
    evidence=verify_visual_evidence(evidence_raw,core=core,core_raw=core_raw,request=request,catalog=empty,
        prepared_audio=identity,expected_model_sha256=config['modelSha256'])
    require(evidence['timeMap'] is not None,'CORPUS_AV_CLOCK_UNVERIFIED')
    raw_parts={'CORE.json':core_raw,'MFA.json':side_raw,'VISUAL_EVIDENCE.json':evidence_raw,
        'MEDIA_RECEIPT.json':encode(metadata['mediaPreparation']['receipt'])}
    for name,raw in raw_parts.items():
        p=folder/name
        if p.exists():require(p.read_bytes()==raw,'CORPUS_RECOVERY_ORIGINAL_MISMATCH')
        else:private_write(p,raw)
    receipt=dict(schemaVersion='voice-coaching.aihub538-extraction-receipt.v1',entryId=entry['entryId'],
        datasetManifestSha256=manifest_sha,sourceVideoSha256=entry['sourceVideoSha256'],
        clipSha256=metadata['mediaPreparation']['receipt']['sourceSha256'],
        normalizedVideoSha256=metadata['video']['sha256'],coreManifestSha256=assets.identity.core_sha256,
        modelSha256=config['modelSha256'],qualitySha256=sha(encode(config['quality'])),providerPosts=0,
        extractorRevision='aihub538-shared-clock-v1',elapsedSeconds=time.monotonic()-started,
        durationMeaning='RECOVERY_ONLY',recoveredWithoutCoreReplay=True,
        artifacts={k:sha(v) for k,v in raw_parts.items()})
    private_write(folder/'corpus-receipt.json',encode(receipt))


async def recover(manifest_raw,source_root,measurements,config,empty_raw):
    manifest=strict_json(manifest_raw);verify_sources(manifest,source_root)
    workspace=Path(config['residentWorkspaceRoot'])
    fd=os.open(workspace/'.corpus-resident.lock',os.O_RDWR|os.O_NOFOLLOW)
    recovered=precore=failed=0
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for entry in manifest['entries']:
            folder=Path(measurements)/entry['entryId']
            if not folder.exists() or (folder/'corpus-receipt.json').exists() or (folder/'failure.json').exists():continue
            if not (folder/'producer-started.json').exists():
                folder.rename(folder.with_name(folder.name+'.precore-'+uuid4().hex));precore+=1;continue
            try:await recover_one(entry,sha(manifest_raw),folder,config,empty_raw);recovered+=1
            except Exception:
                private_write(folder/'failure.json',encode(dict(code='CORPUS_INTERRUPTED_NO_COMPLETE_CORE',
                    providerPosts=0,coreReplayForbidden=True)));failed+=1
    finally:os.close(fd)
    return dict(recoveredWithoutCoreReplay=recovered,precoreScratchPreserved=precore,nonReplayableFailures=failed,providerPosts=0)
