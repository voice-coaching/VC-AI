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
    canonical_workspace: Path | None = field(default=None, repr=False)


class HttpRuntime:
    def __init__(self, environment):
        self.environment = dict(environment)
        # Keep role secrets in the explicit lane configuration, not the ambient
        # environment inherited by multiprocessing's helper/legacy processes.
        for name in tuple(os.environ):
            if name.startswith("AI_CANONICAL_"):
                os.environ.pop(name, None)
        self.worker_id = str(uuid4())
        self.settings = None
        self.backend = None
        self.process = self.connection = None
        self.executor_ready = False
        self.openai_checked_until = 0.0
        self.dependencies_until = 0.0
        self.reason = "http_settings_incomplete_or_invalid"
        self.active: Job | None = None
        self.history = OrderedDict()
        self.stopping = False
        self.monitor = None
        self.legacy_requested = False
        self.legacy_bootstrap = None
        self.canonical_lane = None
        self.canonical_reason = "canonical_bootstrap_not_ready"
        self.canonical_dependencies_until = 0.0
        self.handoff_dependencies_until = 0.0
        self.canonical_monitor = None
        try:
            from .deployment_observation import read_identity
            self.deployment_identity = read_identity(self.environment)
        except (OSError, ValueError, KeyError, TypeError):
            self.deployment_identity = None

    def legacy_ready(self):
        return (not self.stopping and self.settings is not None and self.executor_ready
                and self.process is not None and self.process.is_alive()
                and time.monotonic() < self.dependencies_until)

    def ready(self):
        return self.handoff_admission_ready()

    async def _prepare_legacy(self):
        try:
            await self._stop_executor()
            await self._spawn()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.executor_ready = False
            try:
                await self._stop_executor()
            except Exception:
                # Unknown ownership is not permission to admit another model.
                self.stopping = True
                self.reason = "executor_cleanup_pending"

    async def _cancel_legacy_bootstrap(self):
        if self.legacy_bootstrap is not None:
            self.legacy_bootstrap.cancel()
            await asyncio.gather(self.legacy_bootstrap, return_exceptions=True)
            self.legacy_bootstrap = None

    async def start(self):
        try:
            self.settings = HttpSettings.from_environment(self.environment)
            self.backend = BackendHttp(self.settings)
        except ValueError:
            return
        if self.environment.get("AI_CANONICAL_BOOTSTRAP_READY") == "true":
            try:
                from .canonical_lane import CanonicalLane
                self.canonical_lane = CanonicalLane(self.environment)
                self.canonical_reason = None
            except Exception:
                self.canonical_reason = "canonical_configuration_invalid"
        self.monitor = asyncio.create_task(self._monitor())
        if self.canonical_lane is not None:
            self.canonical_monitor = asyncio.create_task(self._monitor_canonical())

    def canonical_admission_ready(self):
        return False  # v4 is retired, regardless of stale environment flags.

    def handoff_admission_ready(self):
        return (self.canonical_lane is not None and not self.stopping and self.canonical_lane.ready()
                and self.environment.get("AI_CANONICAL_HANDOFF_ENABLED") == "true"
                and time.monotonic() < self.handoff_dependencies_until
                and time.monotonic() < self.dependencies_until)

    async def _monitor_handoff(self, client):
        if self.environment.get("AI_CANONICAL_HANDOFF_ENABLED") != "true":
            return
        from .canonical_settings import handoff_schema_digests
        try:
            async with client.stream("GET", self.settings.callback_base + "/api/internal/ai/worker-readiness/handoff",
                    headers={"Authorization": "Bearer " + self.settings.callback_token, "Accept-Encoding": "identity"}) as reply:
                if reply.status_code != 200 or reply.headers.get("Content-Encoding", "identity") != "identity":
                    raise ValueError()
                raw = bytearray()
                async for chunk in reply.aiter_raw():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ValueError()
            from .http_models import parse_json
            state = parse_json(bytes(raw))
            if (state.get("contractVersion") != "voice-coaching.canonical-handoff.v1"
                    or not state.get("supported")
                    or state.get("schemaDigests") != handoff_schema_digests()):
                raise ValueError()
            self.handoff_dependencies_until = time.monotonic() + 45
        except asyncio.CancelledError:
            raise
        except Exception:
            self.handoff_dependencies_until = 0

    def deployment_snapshot(self):
        """Authenticated deployment diagnostics; counts only, never job content."""
        if self.deployment_identity is None:
            raise HttpFailure(503, 'NOT_READY') from None
        return dict(protocol='canonical-deployment-v1', workerInstanceId=self.worker_id,
                    activeJobs=int(self.active is not None), stopping=self.stopping,
                    **self.deployment_identity)

    async def _monitor_canonical(self):
        import httpx
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=8) as client:
            while not self.stopping:
                try:
                    await self.canonical_lane.refresh()
                    await self._monitor_handoff(client)
                    self.canonical_reason = None if time.monotonic() < self.handoff_dependencies_until else "handoff_backend_not_ready"
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.handoff_dependencies_until = 0
                    self.canonical_reason = "handoff_backend_not_ready"
                await asyncio.sleep(10)

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
                       if not k.startswith("AI_CANONICAL_") and k not in {"AI_ANALYSIS_API_TOKEN", "AI_ANALYSIS_CALLBACK_TOKEN",
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
            stage = "executor_not_ready"
            previous = self.reason
            try:
                stage = "backend_callback_not_ready"
                response = await self.backend.call("/api/internal/ai/worker-readiness", None, "workerReadiness")
                if response["status"] != "ready" or response["contractVersion"] != CONTRACT:
                    raise ValueError()
                stage = "openai_configuration_invalid"
                from .coaching_generator import openai_configuration
                api_key = openai_configuration(self.environment)
                stage = "openai_model_unavailable"
                if time.monotonic() >= self.openai_checked_until:
                    import httpx
                    async with httpx.AsyncClient(trust_env=False, timeout=5, follow_redirects=False) as client:
                        response = await client.get("https://api.openai.com/v1/models/gpt-6-astra",
                            headers={"Authorization": "Bearer " + api_key})
                        if response.status_code != 200 or response.json().get("id") != "gpt-6-astra":
                            raise ValueError()
                    # Authenticated model visibility, not an inference quality check.
                    self.openai_checked_until = time.monotonic() + 300
                stage = "storage_not_ready"
                from .http_storage import storage_ready
                if not await asyncio.to_thread(storage_ready, self.environment, self.settings):
                    raise ValueError()
                self.dependencies_until = time.monotonic() + 45
                self.reason = None
            except asyncio.CancelledError:
                raise
            except Exception:
                self.dependencies_until = 0
                self.reason = stage
            if previous != self.reason:
                logging.getLogger(__name__).warning("analysis_dependency_state provider=OPENAI model=gpt-6-astra reason=%s",
                    self.reason or "ready")
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
                    workerInstanceId=self.worker_id, status="DELIVERING_RESULT" if job.state == "HANDED_OFF" else job.state)
        if details:
            body["reasonCode"] = job.reason
        return body

    async def accept(self, body):
        if self.settings is None:
            raise HttpFailure(503, "NOT_READY")
        request = AnalysisInput.from_payload(body, self.settings.prefix)
        if not request.is_legacy and request.visual_input is not None:
            raise HttpFailure(503, "NOT_READY")
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
        if self.active is not None:
            raise HttpFailure(429, "CAPACITY_EXCEEDED")
        from .http_models import RESULT_V5
        canonical_ready = self.handoff_admission_ready() if request.result_schema_version == RESULT_V5 else self.canonical_admission_ready()
        if not request.is_legacy and not canonical_ready:
            raise HttpFailure(503, "NOT_READY")
        if request.is_legacy and not self.legacy_ready():
            if self.canonical_lane is not None and self.canonical_lane.resident is not None:
                raise HttpFailure(503, "NOT_READY")
            self.legacy_requested = True
            raise HttpFailure(503, "NOT_READY")
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
            if request.is_legacy:
                self.connection.send(request)
            job.claimed.set()
            job.task = asyncio.create_task(self._run(job) if request.is_legacy else self._run_canonical(job))
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
        if not job.request.is_legacy and self.canonical_lane is not None:
            self.canonical_lane.publish_lease(job)

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
            except Exception:
                job.reason = "DEPENDENCY_UNAVAILABLE"
                job.cancel.set()
                return

    async def _run_canonical(self, job):
        from .canonical_process import CanonicalProcessCleanupUnconfirmed
        heartbeat = asyncio.create_task(self._heartbeat(job))
        job.state = "RUNNING"
        cleanup_confirmed = True
        try:
            # The resident legacy model must release GPU ownership before R/MFA.
            # No legacy attempt is active: both lanes share the same reserved slot.
            self.legacy_requested = False
            await self._cancel_legacy_bootstrap()
            await self._stop_executor()
            job.state = await self.canonical_lane.run(job, self.worker_id)
        except CanonicalProcessCleanupUnconfirmed:
            cleanup_confirmed = False
            self.stopping = True
            self.canonical_reason = "canonical_cleanup_unconfirmed"
            job.reason = "INTERNAL_ERROR"
            job.state = "CANCELLING"
        except asyncio.CancelledError:
            job.cancel.set()
            job.state = "CANCELLED"
        except Exception as error:
            # No fabricated core/fallback or downgrade. Backend fences timeout/failure.
            from .canonical_process import CanonicalProcessFailed
            from .canonical_lease import LEASE_CODES
            from .canonical_bridge import CanonicalIntegrationError
            code = "INTERNAL_ERROR"
            if isinstance(error, CanonicalIntegrationError) and str(error) in LEASE_CODES | {
                    "CANONICAL_EXECUTION_INACTIVE", "CANONICAL_PROCESS_FAILED"}:
                code = str(error)
            logging.getLogger(__name__).warning(
                "canonical_execution_failed executionId=%s code=%s guardExitStatus=%s",
                job.request.execution_id, code,
                error.exit_status if isinstance(error, CanonicalProcessFailed) else None)
            job.reason = job.reason or "INTERNAL_ERROR"
            job.state = "CANCELLED" if job.cancel.is_set() else "FAILED"
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            if cleanup_confirmed:
                job.finished = time.monotonic()
                self.active = None

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
        if self.canonical_monitor:
            self.canonical_monitor.cancel()
            await asyncio.gather(self.canonical_monitor, return_exceptions=True)
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        if self.active:
            self.active.cancel.set()
            if self.active.task:
                self.active.task.cancel()
                await asyncio.gather(self.active.task, return_exceptions=True)
        await self._cancel_legacy_bootstrap()
        if self.canonical_lane is not None:
            await self.canonical_lane.close()
        await self._stop_executor()
        if self.backend:
            await self.backend.close()
