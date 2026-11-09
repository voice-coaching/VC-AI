"""Offline corpus extraction, one CORE per sentence and ZERO GPT calls.

Requires an explicitly provisioned isolated resident socket. Never reads the
operating API environment, takes a production job or modifies a previous spool.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

from .corpus_dataset import contained, file_sha, private_write, verify_sources
from .corpus_reference import encode, require, sha
from .reference_catalog import ReferenceCatalog


async def extract_entry(entry, manifest_sha256, root, output, configuration, empty_catalog_raw):
    from ..backend_analysis.browser_video_normalize import capture, normalize
    from ..backend_analysis.canonical_bridge import FrozenCanonicalArtifacts
    from ..backend_analysis.canonical_producer import FrozenCanonicalProducer
    from ..backend_analysis.http_models import HttpVisualInput
    from ..backend_analysis.independent_av_domain import IndependentAudiovisualInput
    from ..backend_analysis.media_preparation import MediaPreparation
    from ..backend_analysis.ports import MaterializedAudio, MaterializedVideo
    from ..direct_analysis.worker import LocalProducerJournal
    from .adapters.canonical_video_features import CanonicalVideoFeatures, VideoFeatureConfiguration
    from .canonical_service import CanonicalAudiovisualService

    require(configuration.get('schemaVersion')=='voice-coaching.aihub538-extractor.v1')
    require(configuration.get('isolatedResident') is True,'CORPUS_ISOLATED_RESIDENT_REQUIRED')
    source=contained(root,entry['video'])
    require(file_sha(source)==entry['sourceVideoSha256'],'CORPUS_SOURCE_CHANGED')
    folder=Path(output)/entry['entryId'];folder.mkdir(parents=True,mode=0o700,exist_ok=False)
    deadline=time.monotonic()+configuration.get('timeoutSeconds',600)
    # One shared timeline and real embedded audio. Silent/mouth-crop-only assets
    # are rejected; they cannot establish the audio/video clock for these metrics.
    clip=folder/'original-video'
    command=['/usr/bin/ffmpeg','-nostdin','-v','error','-threads','1',
        '-protocol_whitelist','file,pipe','-f','mov','-enable_drefs','0','-use_absolute_path','0',
        '-ss',str(entry['startSeconds']),'-accurate_seek','-i',str(source),
        '-t',str(entry['endSeconds']-entry['startSeconds']),'-map','0:v:0','-map','0:a:0',
        '-map_metadata','-1','-map_chapters','-1','-c:v','libx264','-preset','veryfast',
        '-pix_fmt','yuv420p','-threads','1','-c:a','aac','-ac','1','-ar','16000',
        '-movflags','+faststart','-f','mp4','-n',str(clip)]
    started=time.monotonic();await capture(command,deadline)
    clip.chmod(0o600);clip_sha=file_sha(clip)
    metadata=dict(clientRequestId=str(uuid4()),scriptText=entry['scriptText'],contentId=1,
        source=dict(sha256=clip_sha,fileSizeBytes=clip.stat().st_size,mimeType='video/mp4'))
    metadata=await normalize(folder,metadata,deadline=deadline)
    execution_id=uuid4()
    # The preserved resident requires this exact per-execution layout; the
    # dataset's entry-id receipt directory is separate from execution scratch.
    work=Path(configuration['residentWorkspaceRoot'])/str(execution_id)
    work.mkdir(mode=0o700);(work/'media').mkdir(mode=0o700);(work/'core').mkdir(mode=0o700)
    for name in ('audio.wav','video.mp4'):private_write(work/'media'/name,(folder/name).read_bytes())
    audio=MaterializedAudio(work/'media/audio.wav',metadata['audio']['fileSizeBytes'],metadata['audio']['sha256'])
    video=MaterializedVideo(work/'media/video.mp4',metadata['video']['fileSizeBytes'],metadata['video']['sha256'])
    request=IndependentAudiovisualInput(uuid4(),uuid4(),execution_id,uuid4(),entry['scriptText'],
        sha(entry['scriptText'].encode()),audio.sha256,audio.byte_count,1,
        visual_input=HttpVisualInput('local/video.mp4',video.sha256,'video/mp4',video.byte_count,None),
        media_preparation=metadata['mediaPreparation'])
    assets=FrozenCanonicalArtifacts(Path(configuration['coreRoot']),Path(configuration['h5Root']))
    producer=FrozenCanonicalProducer(assets,work/'core',LocalProducerJournal(folder),
        nltk_data_root=Path(configuration['nltkRoot']),resident_socket=Path(configuration['residentSocket']))
    catalog=ReferenceCatalog.from_bytes(empty_catalog_raw,sha(empty_catalog_raw))
    require(not catalog.scoring_enabled and not catalog.references and not catalog.calibrations)
    port=CanonicalVideoFeatures(VideoFeatureConfiguration(Path(configuration['visualPython']),
        Path(configuration['landmarkModel']),configuration['modelSha256'],configuration['quality']))
    result=await CanonicalAudiovisualService(video_port=port,catalog=catalog).produce(
        request=request,audio=audio,video=video,producer=producer,
        media_preparation=MediaPreparation.from_wire(metadata['mediaPreparation'],
            audio_sha256=audio.sha256,video_sha256=video.sha256,allow_ai=True),
        deadline=deadline,visual_timeout=min(120,deadline-time.monotonic()),
        execution_is_active=lambda:time.monotonic()<deadline)
    require(result.visual_evidence['timeMap'] is not None,'CORPUS_AV_CLOCK_UNVERIFIED')
    require(result.audio.alignment_evidence is not None,'CORPUS_MFA_REQUIRED')
    artifacts={'CORE.json':result.audio.raw,'VISUAL_EVIDENCE.json':encode(result.visual_evidence),
        'MFA.json':result.audio.alignment_evidence,'MEDIA_RECEIPT.json':encode(metadata['mediaPreparation']['receipt'])}
    for name,raw in artifacts.items():private_write(folder/name,raw)
    receipt=dict(schemaVersion='voice-coaching.aihub538-extraction-receipt.v1',entryId=entry['entryId'],
        datasetManifestSha256=manifest_sha256,sourceVideoSha256=entry['sourceVideoSha256'],
        clipSha256=clip_sha,normalizedVideoSha256=video.sha256,
        coreManifestSha256=assets.identity.core_sha256,modelSha256=configuration['modelSha256'],
        qualitySha256=sha(encode(configuration['quality'])),providerPosts=0,
        extractorRevision='aihub538-shared-clock-fastseek-v2',elapsedSeconds=time.monotonic()-started,
        artifacts={name:sha(raw) for name,raw in artifacts.items()})
    private_write(folder/'corpus-receipt.json',encode(receipt))
    return receipt


async def extract(manifest_raw, root, output, configuration, empty_catalog_raw, *, limit=None):
    from .feature_protocol import strict_json
    manifest=strict_json(manifest_raw);verify_sources(manifest,root)
    resident=None;lock_fd=None
    if configuration.get('manageResident') is True:
        from ..backend_analysis.canonical_resident import CanonicalResidentCore
        workspace=Path(configuration['residentWorkspaceRoot'])
        require(Path(output).resolve().is_relative_to(workspace.resolve()),'CORPUS_RESIDENT_WORKSPACE_SCOPE')
        workspace.mkdir(parents=True,mode=0o700,exist_ok=True)
        from ..backend_analysis.canonical_process import private_directory
        private_directory(workspace)
        lock_fd=os.open(workspace/'.corpus-resident.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:os.close(lock_fd);raise
        env=dict(AI_CANONICAL_CORE_ROOT=configuration['coreRoot'],AI_CANONICAL_LLM_ROOT=configuration['h5Root'],
            AI_CANONICAL_WORKSPACE_ROOT=str(workspace),AI_ANALYSIS_NLTK_DATA_ROOT=configuration['nltkRoot'],
            AI_ANALYSIS_RESOURCE_PROFILE=configuration['resourceProfile'])
        resident=CanonicalResidentCore(env,Path(sys.executable))
    try:
        return await _extract_entries(manifest,manifest_raw,root,output,configuration,empty_catalog_raw,limit,resident)
    finally:
        if resident is not None:await resident.close()
        if lock_fd is not None:os.close(lock_fd)


async def _extract_entries(manifest,manifest_raw,root,output,configuration,empty_catalog_raw,limit,resident):
    completed=failed=0
    for entry in manifest['entries'][:limit]:
        target=Path(output)/entry['entryId']
        # Existing successful or interrupted work is immutable. A resume never
        # spends a second inference on an ambiguous/failed occurrence.
        if target.exists():
            if (target/'corpus-receipt.json').is_file():completed+=1;continue
            failed+=1;continue
        try:
            config=dict(configuration)
            if resident is not None:
                deadline=time.monotonic()+360
                while not resident.ready():
                    require(time.monotonic()<deadline and not resident.blocked,'CORPUS_RESIDENT_NOT_READY')
                    task=resident.current.task
                    if task is not None and task.done() and resident.requests==0:
                        log=resident.current.generation/'resident.log'
                        if log.is_file():private_write(Path(output)/'resident-startup-failure.private.log',log.read_bytes()[:1024**2])
                        raise ValueError('CORPUS_RESIDENT_STARTUP_FAILED')
                    await resident.refresh();await asyncio.sleep(.1)
                config['residentSocket']=str(resident.socket)
            await extract_entry(entry,sha(manifest_raw),root,output,config,empty_catalog_raw)
            completed+=1
        except Exception as error:
            # No traceback/transcript/label paths in terminal logs.
            target.mkdir(mode=0o700,parents=True,exist_ok=True)
            code=str(error) if re.fullmatch('[A-Z][A-Z0-9_]{1,159}',str(error)) else 'CORPUS_EXTRACTION_FAILED'
            private_write(target/'failure.json',encode(dict(exceptionType=type(error).__name__,code=code,providerPosts=0)))
            if resident is not None:await resident.stop()
            failed+=1
        else:
            if resident is not None and (target/'producer-started.json').is_file():resident.requests+=1
        print(json.dumps(dict(event='corpus_extraction_progress',completed=completed,failed=failed,providerPosts=0)),flush=True)
    return dict(completed=completed,failed=failed,providerPosts=0)
