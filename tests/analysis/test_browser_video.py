"""Offline browser grants, uploads and real decoding, without inference/GPT."""
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
import wave

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from voice_coach.backend_analysis.browser_video_api import PREFIX
from voice_coach.backend_analysis.browser_video_grants import TYPE, SCOPE
from voice_coach.backend_analysis.browser_video_normalize import normalize, probe, preserve_tracks
from voice_coach.backend_analysis.http_api import create_app
from voice_coach.backend_analysis.http_models import HttpFailure
from voice_coach.backend_analysis.independent_av_runtime import IndependentAudiovisualRuntime
from voice_coach.backend_analysis.independent_av_store import AudiovisualStore
from voice_coach.backend_analysis.media_preparation import MediaPreparation
from voice_coach.direct_analysis.domain import Settings
from voice_coach.direct_analysis.store import atomic
from voice_coach.visual_feedback.parallel_analysis import MediaIntegrityFailure
from voice_coach.visual_feedback.adapters.canonical_feature_worker import sealed_model_path


class BrowserVideo(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/home/2020067/.cache')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.key=Ed25519PrivateKey.generate()
        pub=self.key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        keys=self.root/'public-keys.json';keys.write_text(json.dumps({'test-key':pub}))
        self.origin='https://frontend.example'
        self.env=dict(AI_ANALYSIS_API_TOKEN='a'*40,AI_INDEPENDENT_AV_ENABLED='true',
            AI_INDEPENDENT_AV_WORKSPACE_ROOT=str(self.root/'work'),AI_BROWSER_VIDEO_ENABLED='true',
            AI_BROWSER_VIDEO_PUBLIC_KEYS_FILE=str(keys),AI_BROWSER_VIDEO_ISSUER='backend-test',
            AI_BROWSER_VIDEO_AUDIENCE='ai-test',AI_BROWSER_VIDEO_ALLOWED_ORIGINS=json.dumps([self.origin]))
        self.runtime=IndependentAudiovisualRuntime(self.env)
        self.runtime.store=AudiovisualStore(Settings(root=self.root/'spool'))
        self.addCleanup(self.runtime.store.close)
        self.runtime.assets_ready=True
        self.runtime.resident=type('Resident',(),{'ready':lambda _:True,'blocked':False})()
        self.runtime.budget=type('Budget',(),{'can_admit':lambda _:True})()
        with patch('voice_coach.backend_analysis.independent_av_runtime.IndependentAudiovisualRuntime',return_value=self.runtime):
            self.app=create_app(self.env)
        self.raw=b'synthetic original video bytes'
        self.metadata=dict(clientRequestId=str(uuid4()),scriptText='테스트 원고',contentId=11,
            source=dict(sha256=hashlib.sha256(self.raw).hexdigest(),fileSizeBytes=len(self.raw),mimeType='video/webm'))
        now=int(time.time())
        self.claims=dict(iss='backend-test',aud='ai-test',sub='7',jti=str(uuid4()),iat=now,nbf=now,exp=now+300,
            scope=SCOPE,origin=self.origin,sessionId=12,clientRequestId=self.metadata['clientRequestId'],
            scriptSha256=hashlib.sha256(self.metadata['scriptText'].encode()).hexdigest(),contentId=11,source=self.metadata['source'])

    def token(self,changes=None,header=None):
        enc=lambda b:base64.urlsafe_b64encode(b).rstrip(b'=')
        h=header or dict(alg='EdDSA',typ=TYPE,kid='test-key')
        claims=dict(self.claims,**(changes or {}))
        message=enc(json.dumps(h).encode())+b'.'+enc(json.dumps(claims).encode())
        return (message+b'.'+enc(self.key.sign(message))).decode()

    def client(self,token=None,origin=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://ai',
            headers={'Authorization':'Bearer '+(self.token() if token is None else token),
                     'Origin':origin or self.origin})

    async def create(self,c):
        r=await c.post(PREFIX,json=self.metadata)
        self.assertEqual(r.status_code,202,r.text)
        return PREFIX+'/'+r.json()['jobId']

    async def test_original_upload_and_enqueue_with_no_backend_dependency(self):
        with patch('voice_coach.backend_analysis.adapters.backend_http.BackendHttp.call',side_effect=AssertionError('Backend call')):
            async with self.client() as c:
                p=await self.create(c)
                self.assertEqual((await c.post(p+'/start')).status_code,409)
                r=await c.put(p+'/source',content=self.raw,headers={'Content-Type':'video/webm'})
                self.assertEqual(r.status_code,200,r.text)
                job=p.rsplit('/',1)[1]
                self.assertEqual((self.runtime.store.directory(job)/'original-video').read_bytes(),self.raw)
                self.assertEqual((await c.post(p+'/start')).json()['status'],'QUEUED')
                self.assertEqual((await c.post(p+'/start')).json()['status'],'QUEUED')
                self.assertEqual((await c.get(p+'/result')).status_code,409)
                self.assertEqual((await c.post(p+'/cancel')).json()['status'],'CANCELLED')
                self.assertEqual((self.runtime.store.directory(job)/'original-video').read_bytes(),self.raw)

    async def test_cors_preflight_and_readable_auth_failure(self):
        async with self.client('invalid') as c:
            r=await c.options(PREFIX,headers={'Access-Control-Request-Method':'PUT',
                'Access-Control-Request-Headers':'authorization,content-type'})
            self.assertEqual(r.status_code,204)
            self.assertEqual(r.headers['access-control-allow-origin'],self.origin)
            self.assertNotIn('access-control-allow-credentials',r.headers)
            r=await c.post(PREFIX,json=self.metadata)
            self.assertEqual(r.status_code,401)
            self.assertEqual(r.headers['access-control-allow-origin'],self.origin)
        async with self.client(origin='https://evil.example') as c:
            r=await c.post(PREFIX,json=self.metadata)
            self.assertEqual(r.status_code,403)
            self.assertNotIn('access-control-allow-origin',r.headers)

    async def test_browser_readiness_is_separate_and_bad_config_fails_closed(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://ai',
                headers={'Authorization':'Bearer '+'a'*40}) as c:
            self.assertEqual((await c.get('/health/browser-video')).status_code,200)
        broken=dict(self.env,AI_BROWSER_VIDEO_PUBLIC_KEYS_FILE=str(self.root/'missing.json'))
        with patch('voice_coach.backend_analysis.independent_av_runtime.IndependentAudiovisualRuntime',return_value=self.runtime):
            app=create_app(broken)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://ai',
                headers={'Authorization':'Bearer '+'a'*40}) as c:
            self.assertEqual((await c.get('/health/browser-video')).status_code,503)
            self.assertEqual((await c.get('/health/audiovisual')).status_code,200)
            r=await c.post(PREFIX,json=self.metadata,headers={'Origin':self.origin})
            self.assertEqual(r.status_code,503)

    async def test_expiry_issuer_audience_algorithm_and_key_are_pinned(self):
        bad=[self.token({'exp':int(time.time())-1}),self.token({'aud':'wrong'}),
            self.token({'iss':'wrong'}),self.token({'exp':int(time.time())+301}),
            self.token({'scope':'admin'}),self.token(header=dict(alg='none',typ=TYPE,kid='test-key')),
            self.token(header=dict(alg='EdDSA',typ='JWT',kid='test-key')),
            self.token(header=dict(alg='EdDSA',typ=TYPE,kid='unknown')),'a'*40]
        for token in bad:
            async with self.client(token) as c:
                self.assertEqual((await c.post(PREFIX,json=self.metadata)).status_code,401)

    async def test_refresh_is_idempotent_cross_owner_is_hidden_and_bound(self):
        async with self.client() as c:
            p=await self.create(c)
            self.assertEqual(p,await self.create(c))
            changed=dict(self.metadata,scriptText='변조 원고')
            self.assertEqual((await c.post(PREFIX,json=changed)).status_code,403)
        async with self.client(self.token({'jti':str(uuid4())})) as c:
            self.assertEqual(p,await self.create(c))
        async with self.client(self.token({'sub':'8'})) as c:
            self.assertEqual((await c.get(p)).status_code,404)
            self.assertEqual((await c.get(PREFIX,params={'clientRequestId':self.metadata['clientRequestId']})).status_code,404)
            self.assertEqual((await c.post(PREFIX,json=self.metadata)).status_code,409)

    async def test_wrong_digest_and_size_never_replace_an_original(self):
        async with self.client() as c:
            p=await self.create(c)
            self.assertEqual((await c.put(p+'/source',content=self.raw,headers={'Content-Type':'video/mp4'})).status_code,415)
            self.assertEqual((await c.put(p+'/source',content=b'wrong',headers={'Content-Type':'video/webm'})).status_code,422)
            self.assertEqual((await c.put(p+'/source',content=self.raw+b'x',headers={'Content-Type':'video/webm'})).status_code,413)
            for _ in range(2):
                self.assertEqual((await c.put(p+'/source',content=self.raw,headers={'Content-Type':'video/webm'})).status_code,200)
            self.assertEqual(list(self.runtime.store.root.rglob('upload-*.tmp')),[])

    async def test_quicktime_original_uses_the_same_signed_upload_contract(self):
        self.metadata['source']['mimeType']='video/quicktime'
        async with self.client() as c:
            p=await self.create(c)
            self.assertEqual((await c.put(p+'/source',content=self.raw,
                headers={'Content-Type':'video/quicktime'})).status_code,200)

    async def test_grant_cannot_access_server_routes_or_private_artifacts(self):
        async with self.client() as c:
            p=await self.create(c)
            self.assertEqual((await c.get('/health/audiovisual')).status_code,401)
            self.assertEqual((await c.post('/v2/audiovisual-analysis-jobs',json=self.metadata)).status_code,401)
            self.assertEqual((await c.get(p+'/artifacts/CORE')).status_code,404)
            self.assertEqual((await c.post(p+'/ack',json={'resultSha256':'a'*64})).status_code,404)

    async def test_binding_survives_restart_and_expired_upload_reservation_is_released(self):
        async with self.client() as c:
            p=await self.create(c);job=p.rsplit('/',1)[1]
            self.runtime.store.close()
            self.runtime.store=AudiovisualStore(Settings(root=self.root/'spool'))
            self.addCleanup(self.runtime.store.close)
            self.assertEqual((await c.get(p)).status_code,200)
            self.runtime.store.expire_original_uploads(time.time()+901)
            r=await c.get(p)
            self.assertEqual(r.json()['failure']['code'],'UPLOAD_EXPIRED')
            self.assertEqual((await c.post(p+'/start')).json()['status'],'FAILED')

    async def test_completed_result_is_returned_byte_for_byte_and_500_keeps_cors(self):
        async with self.client() as c:
            p=await self.create(c);job=p.rsplit('/',1)[1]
            raw=b'{"score":{"overallScore":77.1,"details":[]}}'
            atomic(self.runtime.store.directory(job)/'result.json',raw)
            self.runtime.store.update(job,status='COMPLETED')
            r=await c.get(p+'/result');self.assertEqual(r.content,raw)
            self.assertEqual(r.headers['x-result-sha256'],hashlib.sha256(raw).hexdigest())
            with patch.object(self.runtime.store,'result',side_effect=OSError('synthetic failure')):
                r=await c.get(p+'/result')
                self.assertEqual(r.status_code,500)
                self.assertEqual(r.json()['reasonCode'],'INTERNAL_ERROR')
                self.assertEqual(r.headers['access-control-allow-origin'],self.origin)


class VideoNormalization(unittest.IsolatedAsyncioTestCase):
    def test_native_model_snapshot_is_exact_immutable_and_closed(self):
        import os
        from pathlib import Path
        with sealed_model_path(b'verified-model-fixture') as path:
            self.assertEqual(Path(path).read_bytes(),b'verified-model-fixture')
            with self.assertRaises(PermissionError):
                with open(path,'wb') as stream:stream.write(b'changed')
        self.assertFalse(Path(path).exists())

    def test_supported_portrait_hevc_tracks_are_preserved(self):
        video=dict(codec_name='hevc',width=1080,height=1920,avg_frame_rate='30/1',nb_frames='145')
        self.assertTrue(preserve_tracks(video,dict(codec_name='aac')))
        self.assertFalse(preserve_tracks(dict(video,width=2160,height=3840),dict(codec_name='aac')))
        self.assertFalse(preserve_tracks(dict(video,tags={'rotate':'90'}),dict(codec_name='aac')))
        self.assertFalse(preserve_tracks(dict(video,avg_frame_rate='60/1'),dict(codec_name='aac')))

    async def test_real_webm_decode_preserves_relative_clock_and_exact_pcm(self):
        with tempfile.TemporaryDirectory(dir='/home/2020067/.cache') as tmp:
            root=Path(tmp)
            source=root/'original-video'
            # Synthetic audio begins 0.25 s after video. No inference, user media,
            # credentials, external storage or GPT is used by this regression.
            subprocess.run(['/usr/bin/ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
                'color=c=blue:s=320x240:r=30:d=2','-itsoffset','0.25','-f','lavfi','-i',
                'sine=frequency=440:sample_rate=16000:duration=1.5','-c:v','libvpx-vp9',
                '-threads','1','-c:a','libopus','-f','webm',str(source)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            raw=source.read_bytes()
            m=dict(clientRequestId=str(uuid4()),scriptText='시험',contentId=None,
                source=dict(sha256=hashlib.sha256(raw).hexdigest(),fileSizeBytes=len(raw),mimeType='video/webm'))
            before=await probe(source,time.monotonic()+20,'matroska')
            n=await normalize(root,m,deadline=time.monotonic()+120)
            r=n['mediaPreparation']['receipt']
            self.assertEqual(source.read_bytes(),raw)
            self.assertEqual(r['sourceSha256'],m['source']['sha256'])
            with self.assertRaises(MediaIntegrityFailure):
                MediaPreparation.from_wire(n['mediaPreparation'],audio_sha256=n['audio']['sha256'],video_sha256=n['video']['sha256'])
            MediaPreparation.from_wire(n['mediaPreparation'],audio_sha256=n['audio']['sha256'],video_sha256=n['video']['sha256'],allow_ai=True)
            pcm=subprocess.run(['/usr/bin/ffmpeg','-nostdin','-v','error','-threads','1','-i',str(root/'video.mp4'),
                '-map','0:a:0','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le','-f','s16le','pipe:1'],capture_output=True,check=True).stdout
            with wave.open(str(root/'audio.wav')) as w:
                self.assertEqual(pcm,w.readframes(w.getnframes()))
            old={s['codec_type']:float(s['start_time']) for s in before['streams']}
            # Codec delay/time-base rounding can shift a few milliseconds, never
            # erase the deliberately nonzero offset as independent resets would.
            self.assertAlmostEqual(r['canonicalAudioStartSeconds']-r['canonicalVideoStartSeconds'],
                old['audio']-old['video'],delta=.08)
