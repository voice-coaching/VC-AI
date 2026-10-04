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


class CanonicalResidentCore:
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
        self.next_start = 0.0
        self.artifacts = FrozenCanonicalArtifacts(Path(environment['AI_CANONICAL_CORE_ROOT']),
                                                  Path(environment['AI_CANONICAL_LLM_ROOT']))
        from .canonical_harness import RESIDENT
        _require(self.artifacts.identity == RESIDENT, 'RESIDENT_RELEASE_REQUIRED')

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
            await asyncio.to_thread(self.artifacts.verify)
            self.generation = private_directory(Path(tempfile.mkdtemp(prefix='vc-core-', dir='/tmp')))
            self.socket = self.generation / 'core.sock'
            self.requests = 0
            self.task = asyncio.create_task(run_guarded(guard_python=self.guard_python,
                command=[str(CORE_PYTHON), '-B', '-u', str(self.artifacts.core_root / 'scripts/run_resident.py'),
                         '--workspace', self.env['AI_CANONICAL_WORKSPACE_ROOT'],
                         '--generation', str(self.generation), '--socket', str(self.socket)],
                cwd=self.artifacts.core_root,
                environment=core_environment(Path(self.env['AI_ANALYSIS_NLTK_DATA_ROOT'])),
                log_path=self.generation / 'resident.log', deadline_monotonic=time.monotonic()+86400,
                execution_is_active=lambda: not self.stopping))
        if self.task is not None and not self.task.done():
            path = self.generation / 'ready.json'
            if path.exists():
                from .canonical_producer import read_private_result
                record = json.loads(read_private_result(path, self.generation, 1024))
                _require(record == dict(status='READY', coreManifestSha256=self.artifacts.identity.core_sha256),
                         'RESIDENT_READY_IDENTITY')
                self.prepared = True

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
