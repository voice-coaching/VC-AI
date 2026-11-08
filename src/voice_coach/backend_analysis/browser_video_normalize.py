"""Bounded local MP4/WebM normalization; never contacts Backend or a provider."""
import asyncio
import hashlib
import json
from fractions import Fraction
import os
from pathlib import Path
import sys
import time

from voice_coach.direct_analysis.store import atomic, encoded
from voice_coach.pronunciation_attempts.adapters.network_isolated_exec import isolated_command
from .http_models import digest
from .independent_av_store import AUDIO_LIMIT, VIDEO_LIMIT, pcm_valid

REVISION = 'ai-original-video-pcm-20261108-v2'


async def capture(args, deadline, limit=1024**2):
    launcher = Path(__file__).parents[1]/'pronunciation_attempts/adapters/network_isolated_exec.py'
    command = isolated_command(launcher_python=sys.executable,launcher_path=str(launcher),
        target=[sys.executable,str(Path(__file__).with_name('browser_media_exec.py')),*map(str,args)],
        cpu_seconds=90,address_space_bytes=2*1024**3)
    # The parent worker is itself supervised by run_guarded. Cancellation also
    # kills/reaps this decoder's entire process group before returning.
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(*command,stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,stdin=asyncio.subprocess.DEVNULL,start_new_session=True,
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8',
            'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'}))
    try:
        p = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        from voice_coach.visual_feedback.adapters.canonical_video_features import _drain_cleanup
        while not spawn.done():
            try: await asyncio.shield(spawn)
            except asyncio.CancelledError: pass
        if not spawn.cancelled() and spawn.exception() is None:
            await _drain_cleanup([spawn.result()],[])
        raise
    out = bytearray()
    try:
        async with asyncio.timeout(max(.01,deadline-time.monotonic())):
            while chunk := await p.stdout.read(65536):
                out.extend(chunk)
                if len(out)>limit: raise ValueError('MEDIA_PROBE_LIMIT')
            if await p.wait(): raise ValueError('MEDIA_NORMALIZATION_FAILED')
        return bytes(out)
    finally:
        from voice_coach.visual_feedback.adapters.canonical_video_features import _drain_cleanup
        await _drain_cleanup([p],[])


async def probe(path, deadline, demuxer=None):
    args = ['/usr/bin/ffprobe','-v','error','-protocol_whitelist','file,pipe']
    if demuxer: args += ['-f',demuxer]
    if demuxer == 'mov': args += ['-enable_drefs','0','-use_absolute_path','0']
    args += ['-show_streams','-show_format','-of','json',str(path)]
    return json.loads(await capture(args,deadline))


def streams(doc):
    ss = doc.get('streams',[])
    v,a = [s for s in ss if s.get('codec_type')=='video'],[s for s in ss if s.get('codec_type')=='audio']
    if len(v)!=1 or len(a)!=1 or len(ss)!=2: raise ValueError('VIDEO_AUDIO_STREAM_REQUIRED')
    return v[0],a[0]


def preserve_tracks(video,audio):
    """Remux already supported tracks; do not resize valid portrait recordings."""
    try:
        rotation=[video.get('tags',{}).get('rotate',0),
                  *(s.get('rotation',0) for s in video.get('side_data_list',[]))]
        return (video['codec_name'] in ('h264','hevc') and audio['codec_name']=='aac'
                and all(Fraction(str(x))%360==0 for x in rotation)
                and 16<=int(video['width'])<=1920 and 16<=int(video['height'])<=1920
                and int(video['width'])*int(video['height'])<=1920*1080
                and 0<Fraction(video['avg_frame_rate'])<=30
                and 1<=int(video['nb_frames'])<=3600)
    except (KeyError,ValueError,TypeError,ZeroDivisionError):return False


async def normalize(folder, metadata, *, deadline):
    source = folder/'original-video'
    spec = metadata['source']
    with source.open('rb') as stream:
        if (os.fstat(stream.fileno()).st_size != spec['fileSizeBytes']
                or hashlib.file_digest(stream,'sha256').hexdigest() != spec['sha256']):
            raise ValueError('ORIGINAL_VIDEO_IDENTITY_MISMATCH')
    deadline = min(deadline,time.monotonic()+120)
    demuxer = 'matroska' if spec['mimeType']=='video/webm' else 'mov'
    initial = await probe(source,deadline,demuxer)
    v,a = streams(initial)
    if (not .5 <= float(initial['format']['duration']) <= 120
            or not 2 <= int(v['width']) <= 3840 or not 2 <= int(v['height']) <= 3840
            or int(v['width'])*int(v['height']) > 3840*2160
            or not 8000 <= int(a['sample_rate']) <= 192000 or not 1 <= int(a['channels']) <= 8):
        raise ValueError('ORIGINAL_VIDEO_LIMIT_EXCEEDED')
    video,audio = folder/'video.mp4',folder/'audio.wav'
    args = ['/usr/bin/ffmpeg','-nostdin','-v','error','-threads','1',
        '-protocol_whitelist','file,pipe','-f',demuxer]
    if demuxer=='mov': args += ['-enable_drefs','0','-use_absolute_path','0']
    # One common origin; no independent setpts/asetpts that would erase A/V offset.
    args += ['-copyts','-start_at_zero','-i',str(source),'-map','0:v:0','-map','0:a:0',
        '-map_metadata','-1','-map_chapters','-1']
    preserved = preserve_tracks(v,a)
    if preserved:
        args += ['-c','copy']
    else:
        args += ['-vf',"scale=w='if(gte(iw,ih),min(1920,iw),min(1080,iw))':h='if(gte(iw,ih),min(1080,ih),min(1920,ih))':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
            '-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-threads','1',
            '-c:a','aac','-ac','1','-ar','16000','-b:a','64k']
    args += ['-metadata:s:v:0','rotate=0','-movflags','+faststart','-f','mp4','-n',str(video)]
    await capture(args,deadline)
    if not 0 < video.stat().st_size <= VIDEO_LIMIT: raise ValueError('NORMALIZED_VIDEO_TOO_LARGE')
    # Decode the accepted canonical MP4, not the source: visual replay later
    # verifies sample-exact equality against this same AAC decode.
    await capture(['/usr/bin/ffmpeg','-nostdin','-v','error','-threads','1',
        '-protocol_whitelist','file,pipe','-i',str(video),'-map','0:a:0','-vn',
        '-ac','1','-ar','16000','-c:a','pcm_s16le','-f','wav','-n',str(audio)],deadline)
    if not 0 < audio.stat().st_size <= AUDIO_LIMIT: raise ValueError('NORMALIZED_AUDIO_TOO_LARGE')
    for path in (video,audio): path.chmod(0o600)
    pcm_valid(audio.read_bytes())
    final = await probe(video,deadline)
    v,a = streams(final)
    if (v['codec_name'] not in ('h264','hevc') or a['codec_name']!='aac'
            or not .5 <= float(final['format']['duration']) <= 120
            or any(float(s.get('rotation',0))%360 for s in v.get('side_data_list',[]))):
        raise ValueError('NORMALIZED_VIDEO_INVALID')
    def spec_for(path,mime):
        with path.open('rb') as f: pin=hashlib.file_digest(f,'sha256').hexdigest()
        return dict(sha256=pin,fileSizeBytes=path.stat().st_size,mimeType=mime)
    out = {k:metadata[k] for k in ('clientRequestId','scriptText','contentId')}
    out.update(audio=spec_for(audio,'audio/wav'),video=spec_for(video,'video/mp4'))
    receipt=dict(schemaVersion='voice-coaching.ai-media-preparation.v1',sourceSha256=spec['sha256'],
        canonicalPcmSha256=out['audio']['sha256'],canonicalVideoSha256=out['video']['sha256'],
        normalizationRevision=REVISION,canonicalAudioStartSeconds=float(a['start_time']),
        canonicalVideoStartSeconds=float(v['start_time']),canonicalAudioTimeBase=a['time_base'],
        canonicalVideoTimeBase=v['time_base'],syncStatus='UNVERIFIED')
    out['mediaPreparation']=dict(receipt=receipt,receiptSha256=digest(receipt))
    atomic(folder/'normalized-input.json',encoded(out))
    atomic(folder/'normalization-observation.json',encoded(dict(revision=REVISION,
        tracksPreserved=preserved,width=v['width'],height=v['height'])))
    return out
