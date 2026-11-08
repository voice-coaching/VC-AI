"""Offline contracts: independent video ownership, uploads, restart and fences."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import wave

import httpx

from voice_coach.backend_analysis.http_api import create_app
from voice_coach.backend_analysis.http_models import digest, HttpFailure
from voice_coach.backend_analysis.independent_av_runtime import IndependentAudiovisualRuntime, engine_environment
from voice_coach.backend_analysis.independent_av_store import AudiovisualStore, CONTRACT
from voice_coach.direct_analysis.domain import Settings
from voice_coach.direct_analysis.store import atomic, encoded


class IndependentVideoContract(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/home/2020067/.cache')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.env=dict(AI_ANALYSIS_API_TOKEN='a'*40,AI_INDEPENDENT_AV_ENABLED='true',
            AI_INDEPENDENT_AV_WORKSPACE_ROOT=str(self.root/'work'),
            AI_ANALYSIS_CALLBACK_TOKEN='unused',AI_ANALYSIS_CALLBACK_BASE_URL='https://backend.invalid',
            AWS_SECRET_ACCESS_KEY='unused')
        self.runtime=IndependentAudiovisualRuntime(self.env)
        self.runtime.store=AudiovisualStore(Settings(root=self.root/'spool'))
        self.addCleanup(self.runtime.store.close)
        self.runtime.assets_ready=True
        self.runtime.resident=type('LocalResident',(),{'ready':lambda _:True,'blocked':False})()
        self.runtime.budget=type('Budget',(),{'can_admit':lambda _:True})()
        with patch('voice_coach.backend_analysis.independent_av_runtime.IndependentAudiovisualRuntime',return_value=self.runtime):
            self.app=create_app(self.env)
        out=io.BytesIO()
        with wave.open(out,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\1\0'*16000)
        self.audio=out.getvalue();self.video=b'video-contract-fixture'
        a,v=hashlib.sha256(self.audio).hexdigest(),hashlib.sha256(self.video).hexdigest()
        receipt=dict(schemaVersion='voice-coaching.media-preparation.v1',sourceSha256='c'*64,
            canonicalPcmSha256=a,canonicalVideoSha256=v,normalizationRevision='backend-mp4-pcm-20261007-v1',
            canonicalAudioStartSeconds=0,canonicalVideoStartSeconds=0,canonicalAudioTimeBase='1/16000',
            canonicalVideoTimeBase='1/30',syncStatus='UNVERIFIED')
        self.metadata=dict(clientRequestId=str(uuid4()),scriptText='시험 원고',contentId=None,
            audio=dict(sha256=a,fileSizeBytes=len(self.audio),mimeType='audio/wav'),
            video=dict(sha256=v,fileSizeBytes=len(self.video),mimeType='video/mp4'),
            mediaPreparation=dict(receipt=receipt,receiptSha256=digest(receipt)))

    def client(self,auth=True):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://ai',
            headers={'Authorization':'Bearer '+'a'*40} if auth else {})

    async def accepted(self,client):
        r=await client.post('/v2/audiovisual-analysis-jobs',json=self.metadata)
        self.assertEqual(r.status_code,202,r.text)
        return '/v2/audiovisual-analysis-jobs/'+r.json()['jobId']

    async def test_backend_unavailable_video_health_and_upload_remain_independent(self):
        with patch('voice_coach.backend_analysis.adapters.backend_http.BackendHttp.call',side_effect=AssertionError('Backend call')):
            async with self.client() as c:
                r=await c.get('/health/audiovisual')
                self.assertEqual(r.status_code,200)
                self.assertFalse(r.json()['backendRequired'])
                self.assertEqual(r.json()['contractVersion'],CONTRACT)
                path=await self.accepted(c)
                self.assertEqual((await c.post(path+'/start')).status_code,409)
                self.assertEqual((await c.put(path+'/media/audio',content=self.audio,headers={'Content-Type':'audio/wav'})).status_code,200)
                self.assertEqual((await c.put(path+'/media/video',content=self.video,headers={'Content-Type':'video/mp4'})).status_code,200)
                self.assertEqual((await c.post(path+'/start')).json()['status'],'QUEUED')
                self.assertEqual((await c.post(path+'/start')).json()['status'],'QUEUED')
                self.assertEqual((await c.get(path+'/result')).status_code,409)

    async def test_authentication_and_video_route_separation(self):
        async with self.client(False) as c:
            self.assertEqual((await c.get('/health/audiovisual')).status_code,401)
            self.assertEqual((await c.post('/v2/audiovisual-analysis-jobs',json=self.metadata)).status_code,401)
        async with self.client() as c:
            self.assertEqual((await c.post('/v2/analysis-jobs',content=self.video,headers={'Content-Type':'video/mp4'})).status_code,404)

    async def test_idempotency_and_cross_attempt_media_binding(self):
        async with self.client() as c:
            path=await self.accepted(c)
            self.assertEqual(path,await self.accepted(c))
            modified=dict(self.metadata,scriptText='다른 원고')
            self.assertEqual((await c.post('/v2/audiovisual-analysis-jobs',json=modified)).status_code,409)
            self.assertEqual((await c.put(path+'/media/video',content=b'wrong',headers={'Content-Type':'video/mp4'})).status_code,422)
            bad=json.loads(json.dumps(self.metadata));bad['clientRequestId']=str(uuid4())
            bad['mediaPreparation']['receipt']['canonicalVideoSha256']='a'*64
            self.assertEqual((await c.post('/v2/audiovisual-analysis-jobs',json=bad)).status_code,422)
            extra=dict(self.metadata,callbackUrl='https://backend.invalid')
            self.assertEqual((await c.post('/v2/audiovisual-analysis-jobs',json=extra)).status_code,422)

    async def test_cancel_and_ack_preserve_originals_and_result_bytes(self):
        async with self.client() as c:
            path=await self.accepted(c);job=path.rsplit('/',1)[1];folder=self.runtime.store.directory(job)
            await c.put(path+'/media/audio',content=self.audio,headers={'Content-Type':'audio/wav'})
            self.assertEqual((await c.post(path+'/cancel')).json()['status'],'CANCELLED')
            self.assertEqual((folder/'audio.wav').read_bytes(),self.audio)
            raw=b'{"fixture":"unchanged-result"}'
            atomic(folder/'result.json',raw);self.runtime.store.update(job,status='COMPLETED')
            r=await c.get(path+'/result');self.assertEqual(r.content,raw)
            self.assertEqual((await c.post(path+'/ack',json={'resultSha256':'a'*64})).status_code,409)
            pin=hashlib.sha256(raw).hexdigest()
            self.assertEqual((await c.post(path+'/ack',json={'resultSha256':pin})).status_code,200)
            self.assertEqual((folder/'result.json').read_bytes(),raw)
            self.assertEqual((folder/'audio.wav').read_bytes(),self.audio)

    async def test_interrupted_running_job_is_never_requeued(self):
        d=self.runtime.store.create(self.metadata);job=d['jobId']
        self.runtime.store.update(job,status='RUNNING')
        self.runtime.store.recover()
        self.assertEqual(self.runtime.store.read(job)['status'],'FAILED')
        self.assertEqual(self.runtime.store.read(job)['failure'],'EXECUTION_INTERRUPTED')

    async def test_worker_receives_no_backend_or_storage_credentials(self):
        env=engine_environment(self.env)
        self.assertFalse(any(k.startswith('AWS_') or any(s in k for s in ('BACKEND','CALLBACK','JOURNAL')) for k in env))
        self.assertNotIn('AI_ANALYSIS_API_TOKEN',env)
