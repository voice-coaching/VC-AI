"""Parent-owned, bounded resident core generation; never retries inference."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time

from .canonical_bridge import FrozenCanonicalArtifacts, _require
from .canonical_process import run_guarded, private_directory, CanonicalProcessCleanupUnconfirmed
from .canonical_producer import CORE_PYTHON, core_environment


class _ResidentGeneration:
    def __init__(self, environment, guard_python):
        self.env = environment
        self.guard_python = guard_python
        self.task = None
        self.generation = None
        self.socket = None
        self.stopping = False
        self.blocked = False
        self.prepared = False
        self.requests = 0
        self.keep_running = lambda: True
        self.next_start = 0.0
        self.prepare_deadline = 0.0
        self.artifacts = FrozenCanonicalArtifacts(Path(environment['AI_CANONICAL_CORE_ROOT']),
                                                  Path(environment['AI_CANONICAL_LLM_ROOT']))
        from .native_execution import require_native_execution
        require_native_execution(self.artifacts)

    def ready(self):
        return not self.stopping and not self.blocked and self.prepared and self.requests < 3 and self.task is not None and not self.task.done()

    async def refresh(self):
        if self.stopping or self.blocked:
            return
        if self.task is not None and self.task.done():
            error = None if self.task.cancelled() else self.task.exception()
            self.prepared = False
            self.task = None
            if isinstance(error, CanonicalProcessCleanupUnconfirmed):
                self.blocked = True
                return
            self.next_start = time.monotonic() + (30 if error else 0)
        if self.task is None and time.monotonic() >= self.next_start:
            from .canonical_generation import readonly_identity, write_verification
            before = (readonly_identity(self.artifacts.core_root), readonly_identity(self.artifacts.llm_root))
            verified_at = time.monotonic()
            await asyncio.to_thread(self.artifacts.verify)
            self.generation = private_directory(Path(tempfile.mkdtemp(prefix='vc-core-', dir='/tmp')))
            reused = write_verification(self.artifacts, self.generation, before)
            print(json.dumps(dict(event='resident_artifact_verification',
                elapsedSeconds=time.monotonic()-verified_at, reusable=reused)), flush=True)
            self.socket = self.generation / 'core.sock'
            self.requests = 0
            self.prepare_deadline = time.monotonic() + 300
            self.task = asyncio.create_task(run_guarded(guard_python=self.guard_python,
                command=[str(CORE_PYTHON), '-B', '-u', str(self.artifacts.core_root / 'scripts/run_resident.py'),
                         '--workspace', self.env['AI_CANONICAL_WORKSPACE_ROOT'],
                         '--generation', str(self.generation), '--socket', str(self.socket)],
                cwd=self.artifacts.core_root,
                environment=core_environment(Path(self.env['AI_ANALYSIS_NLTK_DATA_ROOT'])),
                log_path=self.generation / 'resident.log', deadline_monotonic=time.monotonic()+86400,
                execution_is_active=lambda: not self.stopping and self.keep_running()))
        if self.task is not None and not self.task.done():
            path = self.generation / 'ready.json'
            if path.exists():
                from .canonical_producer import read_private_result
                record = json.loads(read_private_result(path, self.generation, 1024))
                _require(record == dict(status='READY', coreManifestSha256=self.artifacts.identity.core_sha256),
                         'RESIDENT_READY_IDENTITY')
                self.prepared = True
            elif not self.prepared and time.monotonic() >= self.prepare_deadline:
                # A stalled model load must not leave readiness pending all day.
                # stop() drains the guarded subtree before another generation.
                await self.stop()
                self.next_start = time.monotonic() + 30

    async def stop(self):
        self.prepared = False
        if self.task is not None:
            self.task.cancel()
            result = await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
            if isinstance(result[0], CanonicalProcessCleanupUnconfirmed):
                self.blocked = True
                raise result[0]
        self.next_start = time.monotonic() + 1

    async def close(self):
        self.stopping = True
        await self.stop()


def available_cgroup_memory():
    """Unknown or unlimited container accounting never enables a standby."""
    for root, limit_name, used_name in (
        (Path('/sys/fs/cgroup'), 'memory.max', 'memory.current'),
        (Path('/sys/fs/cgroup/memory'), 'memory.limit_in_bytes', 'memory.usage_in_bytes')):
        try:
            limit = int((root / limit_name).read_text())
            used = int((root / used_name).read_text())
            if 0 < limit < 2**60 and 0 <= used <= limit:
                return limit-used
        except (OSError, ValueError):
            pass
    return None


class CanonicalResidentCore:
    """One dispatch target, optionally one prewarmed generation; never replay."""
    def __init__(self, environment, guard_python):
        self.environment, self.guard_python = environment, guard_python
        self.current = _ResidentGeneration(environment, guard_python)
        self.standby = None
        self.preparing = None
        self.retired = []
        self.blocked = False
        self.stopping = False
        self.next_standby = 0.0
        # Operator supplies measured extra peak RSS plus service burst reserve.
        # Default OFF, without changing the preserved three-request lifecycle.
        self.reserve = int(environment.get('AI_CANONICAL_STANDBY_RESERVE_BYTES', '0'))
        self.minimum_free = int(environment.get('AI_CANONICAL_STANDBY_MIN_FREE_BYTES', '0'))
        if self.reserve < 0 or self.minimum_free < 0 or (self.reserve and not self.minimum_free):
            raise ValueError('STANDBY_RESERVE_INVALID')

    @property
    def socket(self): return self.current.socket

    @property
    def generation(self): return self.current.generation

    @property
    def requests(self): return self.current.requests

    @requests.setter
    def requests(self, value): self.current.requests = value

    def ready(self):
        return not self.blocked and not self.stopping and self.current.ready()

    async def _prepare(self, generation):
        try:
            while not self.stopping and not generation.ready():
                if not generation.keep_running():
                    raise ValueError('STANDBY_MEMORY_HEADROOM')
                await generation.refresh()
                if generation.blocked:
                    raise CanonicalProcessCleanupUnconfirmed('STANDBY_CLEANUP_UNCONFIRMED')
                await asyncio.sleep(.1)
        except BaseException:
            await generation.close()
            raise

    async def _drop_standby(self):
        if self.preparing:
            self.preparing.cancel()
            outcomes = await asyncio.gather(self.preparing, return_exceptions=True)
            self.preparing = None
            if isinstance(outcomes[0], CanonicalProcessCleanupUnconfirmed):
                self.blocked = True
        if self.standby:
            await self.standby.close()
            self.standby = None
        self.next_standby = time.monotonic()+30

    async def refresh(self):
        if self.blocked or self.stopping:
            return
        for old in list(self.retired):
            if old.task is None or old.task.done():
                if old.task and not old.task.cancelled() and isinstance(old.task.exception(), CanonicalProcessCleanupUnconfirmed):
                    self.blocked = True
                    return
                self.retired.remove(old)
        if self.preparing and self.preparing.done():
            error = None if self.preparing.cancelled() else self.preparing.exception()
            self.preparing = None
            if error:
                if isinstance(error, CanonicalProcessCleanupUnconfirmed):
                    self.blocked = True
                    return
                await self._drop_standby()
        old_done = self.current.task is not None and self.current.task.done()
        if old_done and not self.current.task.cancelled() and isinstance(self.current.task.exception(), CanonicalProcessCleanupUnconfirmed):
            self.blocked = True
            return
        draining = self.current.requests >= 3 and self.current.generation is not None and (self.current.generation / 'draining.json').exists()
        if self.standby and self.standby.ready() and (draining or old_done):
            self.retired.append(self.current)
            self.current, self.standby = self.standby, None
            self.current.keep_running = lambda: True
            if self.preparing:
                await self.preparing
                self.preparing = None
            print(json.dumps(dict(event='resident_generation_promoted')), flush=True)
        elif not (self.standby and (draining or old_done)):
            await self.current.refresh()
        if self.current.blocked:
            self.blocked = True
            return
        # Recycle failed/ended standby instead of reserving a dead slot forever.
        if self.standby and self.standby.task and self.standby.task.done() and self.preparing is None:
            await self._drop_standby()
        if (self.reserve and self.current.ready() and self.current.requests >= 1
                and self.standby is None and not self.retired and time.monotonic() >= self.next_standby):
            free = available_cgroup_memory()
            if free is not None and free >= self.reserve + self.minimum_free:
                self.standby = _ResidentGeneration(self.environment, self.guard_python)
                self.standby.keep_running = lambda: (available_cgroup_memory() or 0) >= self.minimum_free
                self.preparing = asyncio.create_task(self._prepare(self.standby))
                print(json.dumps(dict(event='resident_standby_preparing', reserveBytes=self.reserve)), flush=True)

    async def stop(self):
        failure = None
        try:
            await self._drop_standby()
        except CanonicalProcessCleanupUnconfirmed as exc:
            self.blocked = True
            failure = exc
        for generation in [self.current, *self.retired]:
            try:
                await generation.stop()
            except CanonicalProcessCleanupUnconfirmed as exc:
                self.blocked = True
                failure = exc
        self.retired.clear()
        if failure:
            raise failure

    async def close(self):
        self.stopping = True
        await self.stop()


async def produce_resident(socket_path, *, request, case_path, deadline, active):
    import hashlib
    _require(socket_path.is_absolute() and len(str(socket_path).encode()) < 104, 'RESIDENT_SOCKET_INVALID')
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(socket_path, limit=4096), 3)
    receiving = None
    try:
        writer.write((json.dumps(dict(executionId=str(request.execution_id), requestId=str(request.event_id),
            deadline=deadline, caseSha256=hashlib.sha256(case_path.read_bytes()).hexdigest(),
            audioSha256=request.audio_sha256)) + '\n').encode())
        await asyncio.wait_for(writer.drain(), 3)
        receiving = asyncio.create_task(reader.readline())
        while not receiving.done():
            _require(active() and time.monotonic() < deadline, 'CANONICAL_EXECUTION_INACTIVE')
            await asyncio.wait({receiving}, timeout=.1)
        raw = receiving.result()
        _require(len(raw) <= 4096 and raw.endswith(b'\n'), 'RESIDENT_RESPONSE_INVALID')
        _require(json.loads(raw) == dict(executionId=str(request.execution_id), status='DONE'),
                 'RESIDENT_RESPONSE_INVALID')
    finally:
        if receiving is not None:
            receiving.cancel()
            await asyncio.gather(receiving, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
