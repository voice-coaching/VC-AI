from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import signal
import re
import sys
import time
import wave
from uuid import UUID

from voice_coach.backend_analysis.canonical_resident import CanonicalResidentCore
from .domain import DirectFailure
from .store import JobStore, atomic, encoded


class AnalysisService:
    def __init__(self, store: JobStore):
        self.store = store
        self.active = None
        self.process = None
        self.task = None
        self.resident = None
        self.ready_error = None
        self.stopping = False
        self.env = dict(os.environ)
        workspace = store.root / "executions"
        workspace.mkdir(mode=0o700, exist_ok=True)
        self.env["AI_CANONICAL_WORKSPACE_ROOT"] = str(workspace)

    async def start(self):
        # Do not replay a producer across a restart or uncertain process death.
        for doc in self.store.documents():
            if doc["status"] == "RUNNING":
                folder = self.store.directory(doc["jobId"])
                if (folder / "worker-complete.json").exists() and (folder / "result.json").exists():
                    timing = json.loads((folder / "worker-complete.json").read_bytes())["timing"]
                    self.store.update(doc["jobId"], status="RESULT_READY", resultReadyAt=time.time(), timing=timing)
                    continue
                atomic(self.store.root / "recovery-required.json", encoded({"jobId": doc["jobId"]}))
                self.store.update(doc["jobId"], status="FAILED", failure="EXECUTION_INTERRUPTED")
        if (self.store.root / "recovery-required.json").exists():
            self.ready_error = "RECOVERY_CONFIRMATION_REQUIRED"
        if self.store.settings.enabled and not self.ready_error:
            try:
                self.resident = CanonicalResidentCore(self.env, Path(sys.executable))
            except Exception:
                self.ready_error = "APPROVED_NATIVE_RELEASE_REQUIRED"
        self.task = asyncio.create_task(self.loop())

    def ready(self):
        return self.store.settings.enabled and not self.ready_error and self.resident is not None and self.resident.ready() and not self.stopping

    def known_attempt(self, attempt):
        try:
            return str(UUID(attempt)) in self.store.attempts
        except (ValueError, TypeError, AttributeError):
            return False

    def view(self, job_id):
        return self.store.view(job_id)

    def find(self, attempt):
        try:
            job_id = self.store.attempts.get(str(UUID(attempt)))
        except (ValueError, TypeError, AttributeError):
            job_id = None
        if not job_id:
            raise DirectFailure("NOT_FOUND", 404)
        return self.view(job_id)

    def cancel(self, job_id):
        return self.store.cancel(job_id)

    async def submit(self, metadata: dict, audio: bytes):
        if not self.ready() and not self.known_attempt(metadata.get("clientAttemptId")):
            raise DirectFailure(self.ready_error or "CORE_NOT_READY", 503)
        script = metadata.get("scriptText")
        if not isinstance(script, str) or not 1 <= len(script) <= 4000:
            raise DirectFailure("INVALID_SCRIPT", 422)
        content = metadata.get("contentId")
        claim_sha = metadata.get("historyClaimSha256")
        if not isinstance(claim_sha, str) or re.fullmatch("[a-f0-9]{64}", claim_sha) is None:
            raise DirectFailure("INVALID_HISTORY_LINK_DIGEST", 422)
        if content is not None and (type(content) is not int or content <= 0):
            raise DirectFailure("INVALID_CONTENT", 422)
        try:
            with wave.open(io.BytesIO(audio)) as wav:
                duration = wav.getnframes() / wav.getframerate()
                if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getsampwidth() != 2 or not .5 <= duration <= 180:
                    raise ValueError()
                # Truncated uploads must not be accepted as the declared frame count.
                if len(wav.readframes(wav.getnframes())) != wav.getnframes() * 2:
                    raise ValueError()
        except (ValueError, EOFError, wave.Error, ZeroDivisionError) as exc:
            raise DirectFailure("INVALID_PCM_WAV", 422) from exc
        try:
            doc = await asyncio.to_thread(self.store.accept, metadata["clientAttemptId"], script, audio, content, claim_sha)
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise DirectFailure("INVALID_ATTEMPT_ID", 422) from exc
        return self.store.view(doc["jobId"])

    async def loop(self):
        while not self.stopping:
            try:
                if self.resident and self.active is None:
                    await self.resident.refresh()
                if self.ready() and self.active is None:
                    job = next((d for d in self.store.documents() if d["status"] == "QUEUED"), None)
                    if job:
                        self.active = job["jobId"]
                        await self.execute(job)
                        self.active = None
            except asyncio.CancelledError:
                raise
            except Exception:
                if self.active:
                    self.store.update(self.active, status="FAILED", failure="EXECUTION_FAILED")
                self.active = None
                self.ready_error = "EXECUTION_RECOVERY_REQUIRED"
            await asyncio.sleep(.1)

    async def execute(self, doc):
        job_id = doc["jobId"]
        folder = self.store.directory(job_id)
        self.store.update(job_id, status="RUNNING", startedAt=time.time(),
                          deadlineAt=time.time() + self.store.settings.deadline_seconds)
        self.resident.requests += 1
        env = self.env | {"AI_CANONICAL_RESIDENT_SOCKET": str(self.resident.socket),
                          "AI_CANONICAL_VERIFIED_GENERATION": str(self.resident.generation)}
        with (folder / "executor.log").open("ab") as log:
            self.process = await asyncio.create_subprocess_exec(sys.executable, "-B", "-m",
                "voice_coach.direct_analysis.worker", str(folder), env=env,
                stdout=log, stderr=log, start_new_session=True)
            try:
                deadline = time.monotonic() + self.store.settings.deadline_seconds
                while self.process.returncode is None:
                    if self.store.read(job_id)["status"] == "CANCELLED" or time.monotonic() >= deadline:
                        await self.kill()
                        await self.resident.stop()
                        break
                    try:
                        await asyncio.wait_for(self.process.wait(), .2)
                    except asyncio.TimeoutError:
                        pass
                with self.store.lock:
                    if self.store.read(job_id)["status"] != "CANCELLED":
                        if self.process.returncode == 0 and (folder / "worker-complete.json").exists():
                            timing = json.loads((folder / "worker-complete.json").read_bytes())["timing"]
                            self.store.update(job_id, status="RESULT_READY", resultReadyAt=time.time(), timing=timing)
                        else:
                            self.store.update(job_id, status="FAILED", failure="EXECUTION_FAILED")
            finally:
                await self.kill()
                self.process = None

    async def kill(self):
        if self.process and self.process.returncode is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            await self.process.wait()

    async def close(self):
        self.stopping = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.kill()
        if self.resident:
            await self.resident.close()
