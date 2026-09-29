"""Single-slot control plane. Backend leases fence every analysis execution."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, UTC
import multiprocessing as mp
import logging
import os
import random
import signal
import shutil
import time
from pathlib import Path
from uuid import uuid4

from .adapters.backend_http import BackendHttp
from .http_models import AnalysisInput, CONTRACT, HttpFailure, digest, result_payload, timestamp
from .http_settings import HttpSettings


def _executor(connection, environment):
    """Spawned process owns the inference core and all of its child processes."""
    os.setsid()
    os.environ.clear()
    os.environ.update(environment)
    core = None
    try:
        from .production import create_http_core
        core = create_http_core(environment)
        connection.send(("ready", None))
        while True:
            request = connection.recv()
            if request is None:
                break
            connection.send(("result", core.process_admitted(request)))
    except Exception:
        connection.send(("blocked", "analysis_executor_unavailable"))
    finally:
        if core is not None:
            core.close()
        connection.close()


@dataclass
class Job:
    body: dict = field(repr=False)
    request: AnalysisInput = field(repr=False)
    sha: str
    state: str = "ACCEPTED"
    reason: str | None = None
    lease_until: float = 0
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    claimed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    finished: float = 0


class HttpRuntime:
    def __init__(self, environment):
        self.environment = dict(environment)
        self.worker_id = str(uuid4())
        self.settings = None
        self.backend = None
        self.process = self.connection = None
        self.executor_ready = False
        self.dependencies_until = 0.0
        self.reason = "http_settings_incomplete_or_invalid"
        self.active: Job | None = None
        self.history = OrderedDict()
        self.stopping = False
        self.monitor = None

    def ready(self):
        return (not self.stopping and self.settings is not None and self.executor_ready
                and self.process is not None and self.process.is_alive()
                and time.monotonic() < self.dependencies_until)

    async def start(self):
        try:
            self.settings = HttpSettings.from_environment(self.environment)
            self.backend = BackendHttp(self.settings)
        except ValueError:
            return
        self.monitor = asyncio.create_task(self._monitor())

    async def _receive(self):
        while not self.stopping:
            if self.connection.poll():
                return self.connection.recv()
            if not self.process.is_alive():
                raise RuntimeError("executor_exited")
            await asyncio.sleep(0.1)
        raise asyncio.CancelledError()

    async def _spawn(self):
        self.executor_ready = False
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        self.connection = parent
        environment = {k: v for k, v in self.environment.items()
                       if k not in {"AI_ANALYSIS_API_TOKEN", "AI_ANALYSIS_CALLBACK_TOKEN",
                                    "AI_ANALYSIS_CALLBACK_BASE_URL"}}
        work = Path(environment["AI_ANALYSIS_RESTRICTED_WORKSPACE_ROOT"]) / ("http-" + self.worker_id)
        work.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = work / "tmp"
        temporary.mkdir(mode=0o700, exist_ok=True)
        environment["AI_ANALYSIS_RESTRICTED_WORKSPACE_ROOT"] = str(work)
        environment["TMPDIR"] = str(temporary)
        self.process = context.Process(target=_executor, args=(child, environment), daemon=False)
        self.process.start()
        child.close()
        kind, _ = await asyncio.wait_for(self._receive(), 600)
        if kind != "ready":
            raise RuntimeError("executor_not_ready")
        self.executor_ready = True

    async def _stop_executor(self):
        self.executor_ready = False
        process = self.process
        if process is not None and process.pid:
            # setsid is the first executor action. Never signal the supervisor group.
            try:
                if os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
            await asyncio.to_thread(process.join, 5)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                raise RuntimeError("executor_cleanup_pending")
        if self.connection:
            self.connection.close()
        self.process = self.connection = None
        root_text = self.environment.get("AI_ANALYSIS_RESTRICTED_WORKSPACE_ROOT")
        if root_text:
            root = Path(root_text).resolve()
            target = root / ("http-" + self.worker_id)
            if target.exists():
                if target.is_symlink() or not target.resolve().is_relative_to(root):
                    raise RuntimeError("executor_workspace_invalid")
                await asyncio.to_thread(shutil.rmtree, target)

    async def _monitor(self):
        while not self.stopping:
            try:
                if self.active is None and (not self.executor_ready or self.process is None or not self.process.is_alive()):
                    await self._stop_executor()
                    await self._spawn()
                response = await self.backend.call("/api/internal/ai/worker-readiness", None, "workerReadiness")
                if response["status"] != "ready" or response["contractVersion"] != CONTRACT:
                    raise ValueError()
                import httpx
                async with httpx.AsyncClient(trust_env=False, timeout=3, follow_redirects=False) as client:
                    clova = await client.get("http://127.0.0.1:8000/health")
                    if clova.status_code != 200 or clova.json().get("status") != "ready":
                        raise ValueError()
                from .http_storage import storage_ready
                if not await asyncio.to_thread(storage_ready, self.environment, self.settings):
                    raise ValueError()
                self.dependencies_until = time.monotonic() + 45
                self.reason = None
            except asyncio.CancelledError:
                raise
            except Exception:
                self.dependencies_until = 0
                self.reason = "analysis_dependencies_not_ready"
            await asyncio.sleep(15)

    def _prune(self):
        now = time.monotonic()
        for key, job in list(self.history.items()):
            if job.finished and (now - job.finished > 900 or len(self.history) > 1024):
                del self.history[key]

    def lookup(self, request_id, execution_id):
        self._prune()
        job = self.history.get((request_id, execution_id))
        if job is not None:
            return job
        if any(key[0] == request_id for key in self.history):
            raise HttpFailure(409, "STALE_EXECUTION")
        raise HttpFailure(404, "UNKNOWN_EXECUTION")

    def status(self, job, *, details=False):
        body = dict(requestId=str(job.request.event_id), executionId=str(job.request.execution_id),
                    workerInstanceId=self.worker_id, status=job.state)
        if details:
            body["reasonCode"] = job.reason
        return body

    async def accept(self, body):
        if self.settings is None:
            raise HttpFailure(503, "NOT_READY")
        request = AnalysisInput.from_payload(body, self.settings.prefix)
        key, sha = (body["requestId"], body["executionId"]), digest(body)
        self._prune()
        prior = self.history.get(key)
        if prior:
            if prior.sha != sha:
                raise HttpFailure(409, "PAYLOAD_DIGEST_MISMATCH")
            if not prior.claimed.is_set():
                raise HttpFailure(503, "DEPENDENCY_UNAVAILABLE")
            return (200 if prior.finished else 202), self.status(prior)
        if any(k[0] == key[0] and not j.finished for k, j in self.history.items()):
            raise HttpFailure(409, "STALE_EXECUTION")
        if not self.ready():
            raise HttpFailure(503, "NOT_READY")
        if self.active is not None:
            raise HttpFailure(429, "CAPACITY_EXCEEDED")
        if timestamp(body["deadlineAt"]) <= datetime.now(UTC):
            raise HttpFailure(409, "DEADLINE_EXCEEDED")
        job = Job(body, request, sha)
        # No await between checking and reserving the slot on this event loop.
        self.active, self.history[key] = job, job
        try:
            sent = time.monotonic()
            claim = await self.backend.call(self._path(job, "claim"), self._identity(job) |
                                           {"requestPayloadSha256": sha}, "leaseResponse")
            self._lease(job, claim, sent)
            if job.cancel.is_set():
                raise HttpFailure(409, "ANALYSIS_CANCELLED")
            self.connection.send(request)
            job.claimed.set()
            job.task = asyncio.create_task(self._run(job))
            return 202, self.status(job)
        except BaseException:
            self.history.pop(key, None)
            self.active = None
            raise

    def _identity(self, job):
        return dict(requestId=str(job.request.event_id), executionId=str(job.request.execution_id),
                    workerInstanceId=self.worker_id)

    def _path(self, job, action):
        return f"/api/internal/ai/analyses/{job.request.analysis_id}/{action}"

    def _lease(self, job, response, sent):
        if (any(response.get(k) != v for k, v in self._identity(job).items())
                or response["analysisId"] != job.request.analysis_id):
            raise HttpFailure(503, "DEPENDENCY_UNAVAILABLE")
        duration = (timestamp(response["leaseExpiresAt"]) - timestamp(response["serverTime"])).total_seconds()
        if not 0 < duration <= 90 or timestamp(response["leaseExpiresAt"]) > timestamp(job.body["deadlineAt"]):
            raise HttpFailure(503, "DEPENDENCY_UNAVAILABLE")
        job.lease_until = sent + duration - 2
        if time.monotonic() >= job.lease_until:
            raise HttpFailure(409, "LEASE_EXPIRED")

    async def _heartbeat(self, job):
        delay = 15
        while not job.cancel.is_set():
            await asyncio.sleep(min(delay, max(0, job.lease_until - time.monotonic())))
            remaining = job.lease_until - time.monotonic()
            if remaining <= 0:
                job.reason = "LEASE_EXPIRED"
                job.cancel.set()
                return
            try:
                sent = time.monotonic()
                response = await self.backend.call(self._path(job, "heartbeat"), self._identity(job),
                                                   "leaseResponse", budget=remaining)
                self._lease(job, response, sent)
                delay = 15
            except HttpFailure as error:
                if error.status not in (429, 500, 502, 503, 504):
                    job.reason = error.code
                    job.cancel.set()
                    return
                delay = max(1, error.retry_after)

    async def _run(self, job):
        heartbeat = asyncio.create_task(self._heartbeat(job))
        receiving = asyncio.create_task(self._receive())
        cancelled = asyncio.create_task(job.cancel.wait())
        job.state = "RUNNING"
        phase = "receive_result"
        try:
            done, _ = await asyncio.wait((receiving, cancelled), return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done:
                raise asyncio.CancelledError()
            kind, result = receiving.result()
            if kind != "result":
                raise RuntimeError("executor_unavailable")
            phase = "validate_result"
            body = result_payload(result, job.request, self.worker_id)
            job.state = "DELIVERING_RESULT"
            phase = "deliver_result"
            attempt = 0
            while not job.cancel.is_set() and time.monotonic() < job.lease_until:
                try:
                    ack = await self.backend.call(self._path(job, "result"), body, "resultAck",
                                                  budget=job.lease_until - time.monotonic())
                    if any(ack[k] != body[k] for k in ("eventId", "requestId", "executionId", "analysisId")):
                        raise HttpFailure(503, "DEPENDENCY_UNAVAILABLE")
                    job.state = body["status"]
                    return
                except HttpFailure as error:
                    if error.status not in (429, 500, 502, 503, 504):
                        job.reason = error.code
                        break
                    delay = max(min(2 ** min(attempt, 4), 15) + random.random(), error.retry_after)
                    attempt += 1
                    try:
                        await asyncio.wait_for(job.cancel.wait(), min(delay, max(.01, job.lease_until-time.monotonic())))
                    except TimeoutError:
                        pass
            job.cancel.set()
        except asyncio.CancelledError:
            job.cancel.set()
        except Exception as error:
            logging.getLogger(__name__).warning(
                "analysis_result_delivery_failed phase=%s error=%s", phase, type(error).__name__)
            job.reason = error.code if isinstance(error, HttpFailure) else "INTERNAL_ERROR"
            job.cancel.set()
        finally:
            for task in (heartbeat, receiving, cancelled):
                task.cancel()
            await asyncio.gather(heartbeat, receiving, cancelled, return_exceptions=True)
            if job.cancel.is_set():
                job.state = "CANCELLING"
                await self._stop_executor()
                # Clova has a separate process and a bounded 180s request. Drain it
                # before reusing the slot; do not kill shared Clova on cancellation.
                await asyncio.sleep(180 if not self.stopping else 0)
                job.state = "CANCELLED"
            job.finished = time.monotonic()
            self.active = None

    def cancel(self, request_id, execution_id):
        job = self.lookup(request_id, execution_id)
        if job.finished:
            return 200, self.status(job)
        job.reason = "ANALYSIS_CANCELLED"
        job.state = "CANCELLING"
        job.cancel.set()
        return 202, self.status(job)

    async def close(self):
        self.stopping = True
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        if self.active:
            self.active.cancel.set()
            if self.active.task:
                self.active.task.cancel()
                await asyncio.gather(self.active.task, return_exceptions=True)
        await self._stop_executor()
        if self.backend:
            await self.backend.close()
