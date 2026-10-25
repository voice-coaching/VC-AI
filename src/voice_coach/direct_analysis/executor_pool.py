"""Parent-owned execution generations; no replay on an uncertain dispatch."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
import time


class ExecutionGeneration:
    def __init__(self, environment, python, spool, workspace):
        self.environment, self.python = environment, python
        self.spool, self.workspace = spool, workspace
        self.process: asyncio.subprocess.Process | None = None
        self.control: Path | None = None
        self.log = None
        self.requests = 0
        self.started = 0.0
        from voice_coach.backend_analysis.canonical_bridge import FrozenCanonicalArtifacts
        self.identity = FrozenCanonicalArtifacts(Path(environment['AI_CANONICAL_CORE_ROOT']),
                                                  Path(environment['AI_CANONICAL_LLM_ROOT'])).identity

    async def start(self):
        self.control = Path(tempfile.mkdtemp(prefix='execution-', dir=self.workspace))
        self.log = (self.control / 'executor.log').open('ab')
        self.process = await asyncio.create_subprocess_exec(str(self.python), '-B', '-u', '-m',
            'voice_coach.direct_analysis.executor', '--spool', str(self.spool),
            '--control', str(self.control), env=self.environment,
            stdout=self.log, stderr=self.log, start_new_session=True)
        self.started = time.monotonic()

    def ready(self):
        if self.process is None or self.control is None or self.process.returncode is not None or self.requests >= 3:
            return False
        receipt = self.control / 'ready.json'
        if not receipt.is_file():
            return False
        if receipt.is_symlink():
            raise ValueError('EXECUTOR_READY_PATH')
        raw = receipt.read_bytes()
        if len(raw) > 4096:
            raise ValueError('EXECUTOR_READY_SIZE')
        record = json.loads(raw)
        return record == dict(pid=self.process.pid, parent=os.getpid(), status='READY',
            coreManifestSha256=self.identity.core_sha256,
            llmManifestSha256=self.identity.manifest_sha256, budget=3)

    async def dispatch(self, job, generation, deadline):
        if not self.ready() or self.control is None:
            raise ValueError('EXECUTOR_NOT_READY')
        self.requests += 1  # Uncertain dispatch still consumes a generation slot.
        reader, writer = await asyncio.open_unix_connection(str(self.control / 'executor.sock'), limit=8192)
        try:
            writer.write(json.dumps(dict(jobId=job, generation=str(generation), deadline=deadline)).encode()+b'\n')
            await writer.drain()
            raw = await reader.readline()
            if len(raw) > 4096 or not raw.endswith(b'\n'):
                raise ValueError('EXECUTOR_RECEIPT_SIZE')
            doc = json.loads(raw)
            if doc != dict(jobId=job, status='DURABLE', ordinal=self.requests):
                raise ValueError('EXECUTOR_RECEIPT_MISMATCH')
        finally:
            writer.close()
            await writer.wait_closed()

    async def close(self):
        if self.process is not None:
            if self.process.returncode is None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(self.process.wait(), 15)
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None
        if self.control is not None:
            shutil.rmtree(self.control)
            self.control = None


class ExecutionPool:
    def __init__(self, environment, python, spool, workspace):
        self.args = environment, python, spool, workspace
        self.current = ExecutionGeneration(*self.args)
        self.standby = None

    def ready(self):
        return self.current.ready()

    async def refresh(self, *, active):
        current = self.current
        if current.process is None:
            await current.start()
        if current.process is None:
            raise ValueError('EXECUTOR_PROCESS_UNAVAILABLE')
        if current.process.returncode is not None and current.requests == 0:
            raise ValueError('EXECUTOR_PREPARATION_FAILED')
        if not active and (current.process.returncode is not None or current.requests >= 3):
            await current.close()
            self.current = self.standby or ExecutionGeneration(*self.args)
            self.standby = None
            current = self.current
            if current.process is None:
                await current.start()
        if not current.ready() and time.monotonic()-current.started > 60 and not active:
            raise ValueError('EXECUTOR_PREPARATION_FAILED')
        if current.requests >= 2 and self.standby is None:
            from voice_coach.backend_analysis.cgroup_resources import memory_limits
            limits = memory_limits()
            if limits and min(item['available'] for item in limits) > 8*1024**3:
                self.standby = ExecutionGeneration(*self.args)
                await self.standby.start()

    async def retire(self):
        await self.current.close()
        self.current = self.standby or ExecutionGeneration(*self.args)
        self.standby = None

    async def close(self):
        await self.current.close()
        if self.standby is not None:
            await self.standby.close()
            self.standby = None
