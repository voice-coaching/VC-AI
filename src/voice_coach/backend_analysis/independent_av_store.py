"""Dedicated audiovisual upload journal. Never opens legacy/direct spools."""
import hashlib
import io
import json
import os
from pathlib import Path
from time import time
from uuid import UUID, uuid4
import wave

from voice_coach.direct_analysis.store import JobStore, atomic, encoded
from .http_models import HttpFailure
from .media_preparation import MediaPreparation
from .browser_video_grants import source_valid
from voice_coach.visual_feedback.parallel_analysis import MediaIntegrityFailure

AUDIO_LIMIT = 20 * 1024**2
VIDEO_LIMIT = 256 * 1024**2
CONTRACT = 'voice-coaching.independent-audiovisual.v1'
TERMINAL = ('COMPLETED', 'FAILED', 'CANCELLED')
ARTIFACTS = ('CORE', 'BRIDGE_RESULT', 'BINDING', 'ASSOCIATION', 'MEDIA_RECEIPT', 'VISUAL_EVIDENCE')


def metadata(value):
    try:
        if (type(value) is not dict or set(value) != {
                'clientRequestId', 'scriptText', 'contentId', 'audio', 'video', 'mediaPreparation'}
                or type(value['clientRequestId']) is not str
                or str(UUID(value['clientRequestId'])) != value['clientRequestId']
                or type(value['scriptText']) is not str or not value['scriptText'].strip()
                or not 1 <= len(value['scriptText']) <= 4000
                or (value['contentId'] is not None and
                    (type(value['contentId']) is not int or value['contentId'] <= 0))):
            raise ValueError()
        import re
        for name, limit in [('audio', AUDIO_LIMIT), ('video', VIDEO_LIMIT)]:
            row = value[name]
            if (type(row) is not dict or set(row) != {'sha256', 'fileSizeBytes', 'mimeType'}
                    or type(row['sha256']) is not str or re.fullmatch('[a-f0-9]{64}', row['sha256']) is None
                    or type(row['fileSizeBytes']) is not int or not 0 < row['fileSizeBytes'] <= limit
                    or row['mimeType'] != ('audio/wav' if name == 'audio' else 'video/mp4')):
                raise ValueError()
        MediaPreparation.from_wire(value['mediaPreparation'],
            audio_sha256=value['audio']['sha256'], video_sha256=value['video']['sha256'])
    except (KeyError, ValueError, TypeError, AttributeError, MediaIntegrityFailure):
        raise HttpFailure(422, 'VALIDATION_FAILED') from None
    return value


def pcm_valid(raw):
    try:
        with wave.open(io.BytesIO(raw)) as w:
            if (w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != 16000
                    or not .5 <= w.getnframes()/16000 <= 120
                    or len(w.readframes(w.getnframes())) != w.getnframes()*2):
                raise ValueError()
    except (ValueError, wave.Error, EOFError):
        raise HttpFailure(422, 'INVALID_PCM_WAV') from None


class AudiovisualStore(JobStore):
    def create(self, value):
        request = metadata(value)
        return self._create(request)

    def create_original(self, request, binding):
        if (type(request) is not dict or set(request) != {
                'clientRequestId','scriptText','contentId','source'}
                or not source_valid(request.get('source'))):
            raise HttpFailure(422, 'VALIDATION_FAILED')
        return self._create(request, browser_binding=binding)

    @staticmethod
    def reserved_bytes(request):
        if 'source' in request:
            # Original + bounded canonical video/WAV, before any decoder runs.
            return request['source']['fileSizeBytes'] + VIDEO_LIMIT + AUDIO_LIMIT
        return sum(request[k]['fileSizeBytes'] for k in ('audio', 'video'))

    def _create(self, request, browser_binding=None):
        pin = hashlib.sha256(encoded(request if browser_binding is None else [request,browser_binding])).hexdigest()
        with self.mutation_lock:
            known = self.attempts.get(request['clientRequestId'])
            if known:
                doc = self.read(known)
                if doc['requestSha256'] != pin:
                    raise HttpFailure(409, 'IDEMPOTENCY_CONFLICT')
                return doc
            if sum(d['status'] not in TERMINAL for d in self.documents()) >= self.settings.maximum_pending:
                raise HttpFailure(429, 'CAPACITY_EXCEEDED')
            total = self.reserved_bytes(request)
            # Reserve declared upload bytes for every unfinished job.
            reserved = sum(self.reserved_bytes(d['metadata'])
                           for d in self.documents() if d['status'] not in TERMINAL)
            used = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
            if (len(self.cache) >= self.settings.maximum_jobs
                    or used+reserved+total+32*1024**2 > self.settings.spool_bytes):
                raise HttpFailure(503, 'SPOOL_FULL')
            job, execution, event, asset = [str(uuid4()) for _ in range(4)]
            folder = self.directory(job)
            folder.mkdir(mode=0o700)
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
            doc = dict(jobId=job, executionId=execution, requestId=event, audioAssetId=asset,
                clientAttemptId=request['clientRequestId'], requestSha256=pin, metadata=request,
                status='AWAITING_MEDIA', uploaded=[], sequence=1, createdAt=time(), updatedAt=time(),
                failure=None, acknowledgedAt=None)
            if browser_binding is not None:
                doc.update(inputMode='ORIGINAL_VIDEO', browserBinding=browser_binding)
            self.write(doc)
            self.attempts[request['clientRequestId']] = job
            return doc

    def upload_original_file(self, job, temporary, size, pin):
        with self.mutation_lock:
            doc = self.read(job)
            spec = doc['metadata']['source']
            if size != spec['fileSizeBytes'] or pin != spec['sha256']:
                raise HttpFailure(422, 'PAYLOAD_DIGEST_MISMATCH')
            path = self.directory(job)/'original-video'
            if 'source' in doc['uploaded']:
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream,'sha256').hexdigest() != pin:
                        raise HttpFailure(409,'STORED_MEDIA_CONFLICT')
                return doc
            if doc['status'] != 'AWAITING_MEDIA': raise HttpFailure(409,'JOB_STATE_CONFLICT')
            os.replace(temporary,path)
            fd = os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
            return self.update(job,uploaded=['source'])

    def upload(self, job, kind, raw):
        if kind not in ('audio','video'):
            raise HttpFailure(404, 'NOT_FOUND')
        with self.mutation_lock:
            doc = self.read(job)
            if doc.get('inputMode') == 'ORIGINAL_VIDEO': raise HttpFailure(409,'ORIGINAL_VIDEO_UPLOAD_REQUIRED')
            declared = doc['metadata'][kind]
            if len(raw) != declared['fileSizeBytes'] or hashlib.sha256(raw).hexdigest() != declared['sha256']:
                raise HttpFailure(422, 'PAYLOAD_DIGEST_MISMATCH')
            path = self.directory(job)/('audio.wav' if kind == 'audio' else 'video.mp4')
            if kind in doc['uploaded']:
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != declared['sha256']:
                    raise HttpFailure(409, 'STORED_MEDIA_CONFLICT')
                return doc
            if doc['status'] != 'AWAITING_MEDIA':
                raise HttpFailure(409, 'JOB_STATE_CONFLICT')
            if kind == 'audio': pcm_valid(raw)
            atomic(path, raw)
            return self.update(job, uploaded=sorted(set(doc['uploaded']) | {kind}))

    def enqueue(self, job):
        with self.mutation_lock:
            doc = self.read(job)
            if doc['status'] == 'AWAITING_MEDIA':
                expected = ['source'] if doc.get('inputMode') == 'ORIGINAL_VIDEO' else ['audio','video']
                if doc['uploaded'] != expected:
                    raise HttpFailure(409, 'MEDIA_NOT_READY')
                return self.update(job, status='QUEUED', enqueuedAt=time())
            if doc['status'] in ('QUEUED','RUNNING',*TERMINAL): return doc
            raise HttpFailure(409, 'JOB_STATE_CONFLICT')

    def expire_original_uploads(self, now=None):
        now = time() if now is None else now
        with self.mutation_lock:
            for doc in self.documents():
                if (doc.get('inputMode') == 'ORIGINAL_VIDEO' and doc['status'] == 'AWAITING_MEDIA'
                        and now-doc['createdAt'] > 900):
                    self.update(doc['jobId'],status='FAILED',failure='UPLOAD_EXPIRED')

    def recover(self):
        # An interrupted producer is never replayed. Keep all accepted bytes.
        for doc in self.documents():
            if doc['status'] != 'RUNNING': continue
            folder = self.directory(doc['jobId'])
            complete, result = folder/'worker-complete.json', folder/'result.json'
            valid = False
            if complete.is_file() and result.is_file():
                c = json.loads(complete.read_bytes())
                valid = (c.get('executionId') == doc['executionId'] and c.get('requestId') == doc['requestId']
                         and c.get('resultSha256') == hashlib.sha256(result.read_bytes()).hexdigest())
            self.update(doc['jobId'], status=c['status'] if valid else 'FAILED',
                        failure=c.get('failure') if valid else 'EXECUTION_INTERRUPTED')

    def result(self, job):
        doc = self.read(job)
        if doc['status'] not in ('COMPLETED','FAILED'):
            raise HttpFailure(409, 'RESULT_NOT_AVAILABLE')
        path = self.directory(job)/'result.json'
        if not path.is_file(): raise HttpFailure(409, 'RESULT_NOT_AVAILABLE')
        raw = path.read_bytes()
        if len(raw) > 2*1024**2: raise HttpFailure(500, 'INTERNAL_ERROR')
        return raw, hashlib.sha256(raw).hexdigest()

    def view(self, job):
        d = self.read(job)
        available = (self.directory(job)/'result.json').is_file() and d['status'] in ('COMPLETED','FAILED')
        return dict(schemaVersion=CONTRACT, jobId=d['jobId'], executionId=d['executionId'],
            clientRequestId=d['clientAttemptId'], status=d['status'], sequence=d['sequence'],
            createdAt=d['createdAt'], updatedAt=d['updatedAt'], uploaded=d['uploaded'],
            resultAvailable=available, resultSha256=self.result(job)[1] if available else None,
            failure=None if d['failure'] is None else {'code': d['failure']},
            acknowledgedAt=d.get('acknowledgedAt'))
