"""Three-job execution worker. Only its parent can dispatch accepted spool jobs."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import signal
import struct
import time
from uuid import UUID

from .worker import execute
from .store import atomic, encoded
from voice_coach.backend_analysis.canonical_bridge import FrozenCanonicalArtifacts, FrozenCanonicalCoaching
from voice_coach.backend_analysis.canonical_transport import FrozenOpenAITransport
from voice_coach.backend_analysis.native_execution import require_native_execution


async def serve(spool: Path, control: Path):
    parent = os.getppid()
    artifacts = FrozenCanonicalArtifacts(Path(os.environ['AI_CANONICAL_CORE_ROOT']),
                                         Path(os.environ['AI_CANONICAL_LLM_ROOT']))
    require_native_execution(artifacts)
    artifacts.verify()
    coaching = FrozenCanonicalCoaching(artifacts)
    coaching._bridge()  # Exact pinned import before READY; no request or provider call.
    transport = FrozenOpenAITransport.for_harness(os.environ['OPENAI_API_KEY'], artifacts.identity)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(control / 'executor.sock'))
    os.chmod(control / 'executor.sock', 0o600)
    listener.listen(1)
    listener.setblocking(False)
    atomic(control / 'ready.json', encoded(dict(pid=os.getpid(), parent=parent,
        status='READY', coreManifestSha256=artifacts.identity.core_sha256,
        llmManifestSha256=artifacts.identity.manifest_sha256, budget=3)))
    seen = set()
    loop = asyncio.get_running_loop()

    async def parent_health():
        while True:
            if os.getppid() != parent:
                # This worker owns its session. Do not leave a provider thread
                # running after the one-shot journal's supervisor disappears.
                os.killpg(os.getpid(), signal.SIGKILL)
            await asyncio.sleep(.5)

    watching = asyncio.create_task(parent_health())
    try:
        for ordinal in range(1, 4):
            connection, _ = await loop.sock_accept(listener)
            try:
                pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if pid != parent or uid != os.geteuid() or os.getppid() != parent:
                    raise ValueError('EXECUTOR_PEER_CHANGED')
                reader, writer = await asyncio.open_connection(sock=connection, limit=8192)
                raw = await asyncio.wait_for(reader.readline(), 10)
                if len(raw) > 4096 or not raw.endswith(b'\n'):
                    raise ValueError('EXECUTOR_REQUEST_SIZE')
                message = json.loads(raw)
                if set(message) != {'jobId', 'generation', 'deadline'}:
                    raise ValueError('EXECUTOR_REQUEST_SHAPE')
                job = str(UUID(message['jobId']))
                if job != message['jobId'] or job in seen or not time.monotonic() < message['deadline'] < time.monotonic()+3600:
                    raise ValueError('EXECUTOR_REQUEST_ID_OR_DEADLINE')
                seen.add(job)
                generation = Path(message['generation'])
                if not generation.is_absolute() or generation.resolve(strict=True) != generation:
                    raise ValueError('EXECUTOR_GENERATION_PATH')
                env = dict(os.environ, AI_CANONICAL_VERIFIED_GENERATION=str(generation),
                           AI_CANONICAL_RESIDENT_SOCKET=str(generation / 'core.sock'))
                await execute(spool / job, runtime_environment=env, shared_transport=transport, shared_coaching=coaching)
                writer.write(encoded(dict(jobId=job, status='DURABLE', ordinal=ordinal))+b'\n')
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            finally:
                connection.close()
    finally:
        watching.cancel()
        await asyncio.gather(watching, return_exceptions=True)
        transport.close()
        listener.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spool', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    asyncio.run(serve(args.spool.resolve(strict=True), args.control.resolve(strict=True)))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Request/provider exceptions may contain private data.
        print('EXECUTOR_GENERATION_FAILED', flush=True)
        raise SystemExit(1)
