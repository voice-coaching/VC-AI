"""Single-supervisor filesystem journal on a verified persistent POSIX volume."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
from threading import RLock
from time import time
from uuid import UUID, uuid4

from .domain import DirectFailure, Settings


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def atomic(path: Path, value: bytes) -> None:
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


class JobStore:
    def __init__(self, settings: Settings):
        self.settings, self.lock = settings, RLock()
        self.root = settings.root
        if not self.root.is_absolute() or str(self.root).startswith(("/run/", "/tmp/")):
            raise DirectFailure("PERSISTENT_SPOOL_REQUIRED", 503)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.guard = (self.root / ".supervisor.lock").open("a+b")
        fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.attempts: dict[str, str] = {}
        self.cache: dict[str, dict] = {}
        for path in self.root.glob("*/job.json"):
            doc = json.loads(path.read_bytes())
            self.attempts[doc["clientAttemptId"]] = doc["jobId"]
            self.cache[doc["jobId"]] = doc

    def close(self):
        self.guard.close()

    def directory(self, job_id: str) -> Path:
        try:
            return self.root / str(UUID(job_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise DirectFailure("NOT_FOUND", 404) from exc

    def read(self, job_id: str) -> dict:
        key = self.directory(job_id).name
        with self.lock:
            if key not in self.cache:
                raise DirectFailure("NOT_FOUND", 404)
            return dict(self.cache[key])

    def write(self, doc):
        atomic(self.directory(doc["jobId"]) / "job.json", encoded(doc))
        self.cache[doc["jobId"]] = dict(doc)

    def update(self, job_id, **changes):
        with self.lock:
            doc = self.read(job_id)
            doc.update(changes, sequence=doc["sequence"] + 1, updatedAt=time())
            self.write(doc)
            return doc

    def documents(self):
        with self.lock:
            return [self.read(job) for job in self.attempts.values()]

    def capacity(self, size):
        if size > self.settings.maximum_bytes:
            raise DirectFailure("AUDIO_TOO_LARGE", 413)
        docs = self.documents()
        if sum(d["status"] in ("QUEUED", "RUNNING") for d in docs) >= self.settings.maximum_pending:
            raise DirectFailure("CAPACITY_EXCEEDED", 429)
        if len(docs) >= self.settings.maximum_jobs:
            raise DirectFailure("SPOOL_FULL", 503)
        used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        # Reserve room for private artifacts as well as the input.
        if used + size + 32 * 1024 * 1024 > self.settings.spool_bytes:
            raise DirectFailure("SPOOL_FULL", 503)

    def accept(self, attempt: str, script: str, audio: bytes, content_id: int | None, claim_sha: str):
        attempt = str(UUID(attempt))
        sha = hashlib.sha256(audio).hexdigest()
        request_sha = hashlib.sha256(encoded([script, sha, content_id, claim_sha])).hexdigest()
        with self.lock:
            if attempt in self.attempts:
                doc = self.read(self.attempts[attempt])
                if doc["requestSha256"] != request_sha:
                    raise DirectFailure("IDEMPOTENCY_CONFLICT")
                return doc
            self.capacity(len(audio))
            job_id, execution_id, event_id, asset_id = [str(uuid4()) for _ in range(4)]
            folder = self.directory(job_id)
            folder.mkdir(mode=0o700)
            root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(root_fd)
            finally:
                os.close(root_fd)
            # The browser retains the random history-link value before upload.
            # Its digest is not an authorization gate for the public analysis.
            doc = dict(jobId=job_id, executionId=execution_id, requestId=event_id,
                       audioAssetId=asset_id, clientAttemptId=attempt, requestSha256=request_sha,
                       audioSha256=sha, scriptSha256=hashlib.sha256(script.encode()).hexdigest(),
                       scriptText=script, contentId=content_id, fileSizeBytes=len(audio),
                       historyClaimSha256=claim_sha,
                       status="QUEUED", sequence=1, createdAt=time(), updatedAt=time(),
                       archiveStatus="PENDING", historySyncStatus="PENDING", failure=None)
            atomic(folder / "audio.wav", audio)
            self.write(doc)
            self.attempts[attempt] = job_id
            return doc

    def view(self, job_id):
        doc = self.read(job_id)
        result = None
        if doc["status"] == "RESULT_READY":
            result = json.loads((self.directory(job_id) / "result.json").read_bytes())
        return {key: doc[key] for key in ("jobId", "executionId", "clientAttemptId", "status",
                "sequence", "createdAt", "updatedAt", "archiveStatus", "historySyncStatus", "failure")} | {
                    "schemaVersion": "voice-coaching.direct-analysis.v1", "result": result}

    def cancel(self, job_id):
        with self.lock:
            doc = self.read(job_id)
            if doc["status"] in ("RESULT_READY", "FAILED", "CANCELLED"):
                return self.view(job_id)
            self.update(job_id, status="CANCELLED")
            return self.view(job_id)
