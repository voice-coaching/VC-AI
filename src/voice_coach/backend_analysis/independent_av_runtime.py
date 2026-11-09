"""AI-local audiovisual scheduling, persistence and cancellation."""
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from uuid import UUID

from voice_coach.direct_analysis.domain import Settings
from voice_coach.direct_analysis.store import atomic, encoded
from .canonical_bridge import FrozenCanonicalArtifacts, FrozenCanonicalCoaching
from .canonical_audiovisual_result import visual_assets
from .canonical_process import check_guard_python, run_guarded, CanonicalProcessCleanupUnconfirmed
from .canonical_resident import CanonicalResidentCore
from .canonical_settings import child_environment
from .http_models import HttpFailure
from .independent_av_store import AudiovisualStore, CONTRACT, metadata
from .resource_budget import ResourceBudget


def engine_environment(environment):
    # The new worker never receives credentials capable of contacting Backend/S3.
    env = child_environment(environment)
    for k in tuple(env):
        if (k.startswith(('AWS_', 'AI_ANALYSIS_OBJECT_STORAGE_', 'AI_ANALYSIS_READINESS_'))
                or any(t in k for t in ('CALLBACK','BACKEND','JOURNAL')) or k == 'AI_ANALYSIS_API_TOKEN'):
            env.pop(k, None)
    env['AI_CANONICAL_WORKSPACE_ROOT'] = environment['AI_INDEPENDENT_AV_WORKSPACE_ROOT']
    return env


class IndependentAudiovisualRuntime:
    def __init__(self, environment):
        self.environment = dict(environment)
        self.store = self.resident = self.loop_task = self.execution_task = None
        self.active = None
        self.stopping = False
        self.reason = 'LOCAL_EXECUTOR_NOT_STARTED'
        self.assets_ready = False
        self.budget = ResourceBudget.from_environment(environment)
        self.hold = Path('/run/intelligentai/independent-av-admission.closed')

    async def start(self):
        try:
            env = self.environment
            root, work = Path(env['AI_INDEPENDENT_AV_SPOOL_ROOT']), Path(env['AI_INDEPENDENT_AV_WORKSPACE_ROOT'])
            paths = [root, work]
            paths.extend(Path(env[k]) for k in ('AI_DIRECT_SPOOL_ROOT','AI_DIRECT_WORKSPACE_ROOT',
                'AI_CANONICAL_WORKSPACE_ROOT') if env.get(k))
            if (any(not p.is_absolute() or p.resolve() != p for p in paths)
                    or any(root.is_relative_to(p) for p in (Path('/tmp'),Path('/run'),Path('/var/tmp')))
                    or any(a.is_relative_to(b) or b.is_relative_to(a)
                           for i,a in enumerate(paths) for b in paths[i+1:])):
                raise ValueError('DEDICATED_AV_PATHS_REQUIRED')
            self.env = engine_environment(env)
            self.python = Path(env['AI_CANONICAL_PYTHON'])
            self.store = AudiovisualStore(Settings(root=root, enabled=True, maximum_bytes=276*1024**2))
            self.store.recover()
            work.mkdir(mode=0o700, parents=True, exist_ok=True)
            await asyncio.to_thread(self.check_assets)
            self.assets_ready = True
            self.resident = CanonicalResidentCore(self.env, self.python)
            self.reason = 'LOCAL_EXECUTOR_WARMING'
            self.loop_task = asyncio.create_task(self.loop())
        except Exception as error:
            self.reason = 'LOCAL_VIDEO_CONFIGURATION_INVALID'
            logging.getLogger(__name__).warning('independent_av_configuration_failed exceptionType=%s', type(error).__name__)

    def check_assets(self):
        artifacts = FrozenCanonicalArtifacts(Path(self.env['AI_CANONICAL_CORE_ROOT']),Path(self.env['AI_CANONICAL_LLM_ROOT']))
        _, config = visual_assets(FrozenCanonicalCoaching(artifacts))
        from .canonical_transport import transport_for_artifacts
        transport_for_artifacts(self.env['OPENAI_API_KEY'], artifacts).close()
        check_guard_python(self.python)
        from voice_coach.visual_feedback.adapters.canonical_video_features import check_feature_runtime
        check_feature_runtime(Path(self.env['AI_CANONICAL_VISUAL_PYTHON']),
            Path(self.env['AI_ANALYSIS_VISUAL_LANDMARK_MODEL_PATH']), config['modelSha256'])
        if any(not os.access(p,os.X_OK) for p in ('/usr/bin/ffmpeg','/usr/bin/ffprobe')):
            raise ValueError('VISUAL_RUNTIME_UNAVAILABLE')

    def executor_ready(self):
        return (not self.stopping and self.assets_ready and self.store is not None
                and self.resident is not None and not self.resident.blocked and self.resident.ready())

    def ready(self):
        return (self.executor_ready() and not self.hold.exists()
                and not Path('/run/intelligentai/direct-admission.closed').exists())

    def snapshot(self):
        docs = self.store.documents() if self.store is not None else []
        return dict(contractVersion=CONTRACT, executionMode='INDEPENDENT_AUDIOVISUAL_PULL_V1',
            readinessScope='AI_LOCAL', executorConfigured=self.executor_ready(), admissionEnabled=self.ready(),
            resultStoreReady=self.store is not None, backendRequired=False,
            reasonCode=None if self.ready() else ('LOCAL_ADMISSION_HELD' if self.executor_ready() else self.reason),
            activeJobs=sum(d['status'] in ('QUEUED','RUNNING') for d in docs),
            supportedLearningFocuses=['PRONUNCIATION'],
            workerRevision=self.environment.get('VOICE_COACH_BUILD_REVISION'))

    def require_store(self):
        if self.store is None: raise HttpFailure(503,'NOT_READY')
        return self.store

    def create(self, value):
        store = self.require_store()
        value = metadata(value)
        known = value['clientRequestId'] in store.attempts
        if not known and not self.ready(): raise HttpFailure(503,'NOT_READY')
        if not known and not self.budget.can_admit(): raise HttpFailure(429,'CAPACITY_EXCEEDED')
        return store.view(store.create(value)['jobId'])

    def enqueue(self, job):
        store = self.require_store()
        if store.read(job)['status'] == 'AWAITING_MEDIA' and not self.ready():
            raise HttpFailure(503,'NOT_READY')
        return store.view(store.enqueue(job)['jobId'])

    def create_original(self, value, grant):
        from .browser_video_grants import binding, source_valid
        if (type(value) is not dict or set(value) != {'clientRequestId','scriptText','contentId','source'}
                or type(value.get('scriptText')) is not str or not value['scriptText'].strip()
                or not 1 <= len(value['scriptText']) <= 4000 or not source_valid(value['source'])):
            raise HttpFailure(422,'VALIDATION_FAILED')
        if (value['clientRequestId'] != grant['clientRequestId'] or value['contentId'] != grant['contentId']
                or type(value['contentId']) != type(grant['contentId']) or value['source'] != grant['source']
                or hashlib.sha256(value['scriptText'].encode()).hexdigest() != grant['scriptSha256']):
            raise HttpFailure(403,'UPLOAD_GRANT_BINDING_MISMATCH')
        store = self.require_store()
        known = value['clientRequestId'] in store.attempts
        if not known and not self.ready(): raise HttpFailure(503,'NOT_READY')
        if not known and not self.budget.can_admit(): raise HttpFailure(429,'CAPACITY_EXCEEDED')
        return store.view(store.create_original(value,binding(grant))['jobId'])

    async def loop(self):
        while not self.stopping:
            try:
                self.store.expire_original_uploads()
                if self.execution_task is not None and self.execution_task.done():
                    await self.execution_task
                    self.execution_task = self.active = None
                await self.resident.refresh(dispatch_active=self.active is not None)
                if self.resident.ready(): self.reason = None
                elif self.reason is None: self.reason = 'LOCAL_EXECUTOR_WARMING'
                if self.ready() and self.active is None:
                    job = next((d for d in self.store.documents() if d['status']=='QUEUED'),None)
                    if job:
                        self.active = job['jobId']
                        self.execution_task = asyncio.create_task(self.execute(job))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.reason = 'LOCAL_EXECUTOR_RECOVERY_REQUIRED'
                self.assets_ready = False
                logging.getLogger(__name__).warning('independent_av_scheduler_failed exceptionType=%s',type(error).__name__)
            await asyncio.sleep(.2)

    async def execute(self, doc):
        job = doc['jobId']
        cleanup = True
        try:
            with self.store.mutation_lock:
                if self.store.read(job)['status'] != 'QUEUED':return
                self.store.update(job,status='RUNNING',deadlineAt=time.time()+900)
            self.resident.requests += 1
            env = dict(self.env,AI_CANONICAL_RESIDENT_SOCKET=str(self.resident.socket),
                       AI_CANONICAL_VERIFIED_GENERATION=str(self.resident.generation))
            await run_guarded(guard_python=self.python,
                command=[str(self.python),'-B','-m','voice_coach.backend_analysis.independent_av_worker',
                         str(self.store.directory(job))], cwd=Path(__file__).resolve().parents[3],
                environment=env, log_path=self.store.directory(job)/'executor.log',
                deadline_monotonic=time.monotonic()+900,
                execution_is_active=lambda:not self.stopping and self.store.read(job)['status']=='RUNNING')
            c=json.loads((self.store.directory(job)/'worker-complete.json').read_bytes())
            raw=(self.store.directory(job)/'result.json').read_bytes()
            if (c.get('executionId')!=doc['executionId'] or c.get('requestId')!=doc['requestId']
                    or c.get('resultSha256')!=hashlib.sha256(raw).hexdigest()):
                raise ValueError('LOCAL_COMPLETION_IDENTITY')
            with self.store.mutation_lock:
                if self.store.read(job)['status']=='RUNNING':
                    self.store.update(job,status=c['status'],failure=c.get('failure'))
        except BaseException as error:
            cleanup = not isinstance(error,CanonicalProcessCleanupUnconfirmed)
            code = 'EXECUTION_INTERRUPTED' if isinstance(error,asyncio.CancelledError) else 'EXECUTION_FAILED'
            marker = self.store.directory(job)/'worker-failure.json'
            if code == 'EXECUTION_FAILED' and doc.get('inputMode') == 'ORIGINAL_VIDEO' and marker.is_file():
                try:
                    failure = json.loads(marker.read_bytes())
                    if (failure.get('executionId') == doc['executionId'] and failure.get('requestId') == doc['requestId']
                            and failure.get('code') == 'MEDIA_NORMALIZATION_FAILED'):
                        code = failure['code']
                except (ValueError,OSError): pass
            if self.store.read(job)['status']=='RUNNING':
                self.store.update(job,status='FAILED',failure=code)
            try: await self.resident.stop()
            except CanonicalProcessCleanupUnconfirmed:cleanup=False
            if not cleanup:
                self.assets_ready=False
                self.reason='LOCAL_EXECUTOR_RECOVERY_REQUIRED'
            if isinstance(error,asyncio.CancelledError):raise
        # Retain accepted originals, CORE, result and workspace for this version.
        # No cancellation/ACK/restart path deletes media or replays inference.

    def cancel(self, job):
        store=self.require_store()
        with store.mutation_lock:
            if store.read(job)['status'] not in ('COMPLETED','FAILED','CANCELLED'):
                store.update(job,status='CANCELLED')
        return store.view(job)

    def acknowledge(self, job, pin):
        store=self.require_store()
        with store.mutation_lock:
            if store.result(job)[1]!=pin:raise HttpFailure(409,'PAYLOAD_DIGEST_MISMATCH')
            if store.read(job).get('acknowledgedAt') is None:store.update(job,acknowledgedAt=time.time())
        return store.view(job)

    async def close(self):
        self.stopping=True
        if self.loop_task:
            self.loop_task.cancel()
            await asyncio.gather(self.loop_task,return_exceptions=True)
        if self.execution_task:
            self.execution_task.cancel()
            await asyncio.gather(self.execution_task,return_exceptions=True)
        if self.resident:await self.resident.close()
        if self.store:self.store.close()
