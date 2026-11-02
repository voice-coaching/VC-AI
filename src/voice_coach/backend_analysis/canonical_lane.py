"""Parent-side isolated canonical executions with exclusive legacy admission."""
from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from pathlib import Path
import time

from .canonical_codec import encode_document
from .canonical_lease import publish_lease
from .canonical_process import check_guard_python, private_directory, run_guarded, CanonicalProcessCleanupUnconfirmed
from .canonical_producer import read_private_result, write_private_new
from .http_models import HttpFailure, parse_json, timestamp
from .resource_budget import MAX_CANONICAL_WORKERS
from .canonical_bridge import CanonicalIntegrationError


from .canonical_settings import child_environment

class CanonicalLane:
    def __init__(self, environment, *, capacity=1):
        self.environment = child_environment(environment)
        self.python = Path(environment["AI_CANONICAL_PYTHON"])
        check_guard_python(self.python)
        self.root = private_directory(Path(environment["AI_CANONICAL_WORKSPACE_ROOT"]))
        self.app = Path(__file__).resolve().parents[3]
        if environment.get('AI_CANONICAL_RESIDENT_ENABLED') != 'true':
            raise ValueError('NATIVE_RESIDENT_REQUIRED')
        from .canonical_resident import CanonicalResidentCore
        if type(capacity) is not int or not 1 <= capacity <= MAX_CANONICAL_WORKERS:
            raise ValueError("CANONICAL_WORKER_CAPACITY_INVALID")
        self.residents = [CanonicalResidentCore(self.environment, self.python) for _ in range(capacity)]
        # Retain the reviewed identity accessor used by audiovisual health.
        self.resident = self.residents[0]
        self.capacity = capacity
        self.reservations = {}
        self.activated = {self.resident}
        self.refresh_locks = {resident: asyncio.Lock() for resident in self.residents}

    def ready(self):
        return (not any(resident.blocked for resident in self.residents)
                and any(resident.ready() for resident in self.residents))

    def reserve(self, job):
        if not self.ready():
            raise HttpFailure(503, "NOT_READY")
        key = str(job.request.execution_id)
        if key in self.reservations:
            raise ValueError("CANONICAL_WORKER_ALREADY_RESERVED")
        busy = set(self.reservations.values())
        available = [resident for resident in self.residents
                     if resident not in busy and not resident.blocked]
        # Prefer an already warm worker. Cold workers own their own socket and
        # are prepared only after this request has obtained its Backend lease.
        available.sort(key=lambda resident: not resident.ready())
        if available:
            resident = available[0]
            self.reservations[key] = resident
            self.activated.add(resident)
            return
        raise HttpFailure(429, "CAPACITY_EXCEEDED")

    def release(self, job):
        self.reservations.pop(str(job.request.execution_id), None)

    async def refresh_worker(self, resident, *, dispatch_active):
        async with self.refresh_locks[resident]:
            await resident.refresh(dispatch_active=dispatch_active)

    async def refresh(self, *, dispatch_active=False):
        busy = set(self.reservations.values())
        outcomes = await asyncio.gather(
            *(self.refresh_worker(resident, dispatch_active=resident in busy)
              for resident in tuple(self.activated)), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome

    async def close(self):
        outcomes = await asyncio.gather(*(resident.close() for resident in self.residents),
                                        return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome

    @staticmethod
    def publish_lease(job):
        if job.canonical_workspace is None:
            return
        publish_lease(job.canonical_workspace, job.request.execution_id, job.lease_until)

    async def run(self, job, worker_id):
        resident = self.reservations[str(job.request.execution_id)]
        prepared = False
        try:
            while not resident.ready():
                if resident.blocked:
                    raise CanonicalProcessCleanupUnconfirmed("RESIDENT_CLEANUP_UNCONFIRMED")
                if job.cancel.is_set():
                    raise asyncio.CancelledError()
                if (time.monotonic() >= job.lease_until
                        or timestamp(job.body["deadlineAt"]) <= datetime.now(UTC)):
                    raise CanonicalIntegrationError("CANONICAL_EXECUTION_INACTIVE")
                # The monitor and this waiter serialize preparation of this
                # worker, while other workers prepare and execute independently.
                await self.refresh_worker(resident, dispatch_active=False)
                await asyncio.sleep(.1)
            prepared = True
            return await self._run(job, worker_id, resident)
        except BaseException:
            if not prepared:
                await resident.stop()
            raise
        finally:
            self.release(job)

    async def _run(self, job, worker_id, resident):
        work = self.root / str(job.request.execution_id)
        # An existing attempt is never restarted, even if memory was lost.
        work.mkdir(mode=0o700, exist_ok=False)
        job.canonical_workspace = work
        deadline = time.monotonic() + (timestamp(job.body["deadlineAt"]) - datetime.now(UTC)).total_seconds()
        write_private_new(work / "request.json", encode_document({
            "request": job.body, "workerId": worker_id, "deadline": deadline}))
        self.publish_lease(job)
        environment = dict(self.environment)
        if resident is not None:
            if not resident.ready():
                raise ValueError('RESIDENT_NOT_READY')
            resident.requests += 1
            environment['AI_CANONICAL_RESIDENT_SOCKET'] = str(resident.socket)
            environment['AI_CANONICAL_VERIFIED_GENERATION'] = str(resident.generation)
        cleanup_confirmed = True
        try:
            await run_guarded(guard_python=self.python,
                command=[str(self.python), "-B", "-m", "voice_coach.backend_analysis.canonical_executor",
                         str(work / "request.json")],
                cwd=self.app, environment=environment, log_path=work / "executor.log",
                deadline_monotonic=deadline,
                execution_is_active=lambda: not job.cancel.is_set() and time.monotonic() < job.lease_until)
        except BaseException as error:
            cleanup_confirmed = not isinstance(error, CanonicalProcessCleanupUnconfirmed)
            if resident is not None:
                try:
                    await resident.stop()
                except CanonicalProcessCleanupUnconfirmed:
                    cleanup_confirmed = False
                    raise
            raise
        finally:
            if cleanup_confirmed:
                from .scratch_cleanup import remove_audio
                await asyncio.to_thread(remove_audio, work)
        result = parse_json(read_private_result(work / "completed.json", work, 1024))
        if set(result) != {"status"} or result["status"] not in ("COMPLETED", "FAILED", "HANDED_OFF"):
            raise ValueError("canonical_completion_invalid")
        return result["status"]
