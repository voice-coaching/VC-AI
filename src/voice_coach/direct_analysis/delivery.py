"""Bounded, replayable storage lanes. Never called by the result response path."""
import asyncio
import hashlib
import json
import os
import random
import time
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError

from .store import atomic, encoded


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class DeliveryWorkers:
    def __init__(self, store, environment=None):
        self.store = store
        self.tasks = []
        self.stop = False
        self.environment = dict(os.environ if environment is None else environment)
        private = self.environment.get("AI_DIRECT_DELIVERY_ENV_FILE")
        if private:
            path = Path(private)
            info = path.stat()
            if not path.is_absolute() or path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o027:
                raise ValueError("DIRECT_DELIVERY_ENV_PERMISSIONS")
            for line in path.read_text().splitlines():
                if line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    self.environment[key] = value

    def start(self):
        self.tasks = [asyncio.create_task(self.lane(kind)) for kind in ("archive", "history")]

    async def close(self):
        # Drain bounded I/O; cancelling to_thread does not stop its underlying call.
        self.stop = True
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def lane(self, kind):
        state = "archiveStatus" if kind == "archive" else "historySyncStatus"
        while not self.stop:
            for doc in self.store.documents():
                if self.stop:
                    break
                if doc["status"] != "RESULT_READY" or doc[state] in ("COMPLETE", "CONFLICT") or doc.get(kind + "Next", 0) > time.time():
                    continue
                await asyncio.to_thread(self.store.update, doc["jobId"], **{state: "RUNNING"})
                try:
                    archived_sent = await asyncio.to_thread(self.archive if kind == "archive" else self.history, doc)
                    # Send the later archive state even if an earlier summary was acknowledged.
                    complete = kind == "archive" or archived_sent
                    await asyncio.to_thread(self.store.update, doc["jobId"], **{state: "COMPLETE" if complete else "PENDING", kind + "Next": time.time() + 3})
                except HTTPError as exc:
                    if kind == "history" and exc.code in (409, 422):
                        await asyncio.to_thread(self.store.update, doc["jobId"], **{state: "CONFLICT"})
                    else:
                        await asyncio.to_thread(self.retry, doc, kind, state)
                except Exception:
                    await asyncio.to_thread(self.retry, doc, kind, state)
            await asyncio.sleep(1)

    def retry(self, doc, kind, state):
        attempts = doc.get(kind + "Attempts", 0) + 1
        self.store.update(doc["jobId"], **{state: "RETRYING", kind + "Attempts": attempts,
            kind + "Next": time.time() + min(300, 2 ** min(attempts, 8)) + random.random()})

    def archive(self, doc):
        from voice_coach.backend_analysis.canonical_evidence_store import evidence_store_from_environment
        folder = self.store.directory(doc["jobId"])
        evidence = evidence_store_from_environment(self.environment)
        # Reuse configured endpoint/client. UUID namespace deliberately separate from v5 integer IDs.
        client, settings = evidence._client, evidence._settings
        references = []
        try:
            paths = [(path, path.stem, "application/json", ".json")
                     for path in sorted((folder / "artifacts").glob("*.json"))]
            paths.append((folder / "audio.wav", "AUDIO", "audio/wav", ".wav"))
            for path, kind, content_type, extension in paths:
                raw = path.read_bytes()
                sha = hashlib.sha256(raw).hexdigest()
                if kind == "AUDIO" and sha != doc["audioSha256"]:
                    raise ValueError("RECORDING_ARCHIVE_MISMATCH")
                key = settings.prefix.rstrip("/") + "/direct/" + doc["jobId"] + "/" + kind + "/" + sha + extension
                marker = folder / ("archived-" + kind + ".json")
                if marker.exists():
                    references.append(json.loads(marker.read_bytes()))
                    continue
                response = client.put_object(Bucket=settings.bucket, Key=key, Body=raw,
                    ContentType=content_type, Metadata={"sha256": sha})
                version = response.get("VersionId")
                if not version or version == "null":
                    raise ValueError("VERSIONED_ARCHIVE_REQUIRED")
                downloaded = client.get_object(Bucket=settings.bucket, Key=key, VersionId=version)
                with downloaded["Body"] as body:
                    if hashlib.sha256(body.read(len(raw) + 1)).hexdigest() != sha:
                        raise ValueError("ARCHIVE_DIGEST_MISMATCH")
                ref = dict(kind=kind, objectKey=key, versionId=version, sha256=sha, byteSize=len(raw))
                atomic(marker, encoded(ref))
                references.append(ref)
        finally:
            client.close()
        # Direct audio has no AWS upload intent. Preserve it with the scoped B2
        # writer, not the read-only recordings client used for legacy downloads.
        recording = next(ref for ref in references if ref["kind"] == "AUDIO")
        atomic(folder / "archive.json", encoded(dict(schemaVersion="voice-coaching.direct-archive.v2",
            storage="B2", artifacts=[ref for ref in references if ref["kind"] != "AUDIO"],
            recording=recording)))

    def history(self, doc):
        folder = self.store.directory(doc["jobId"])
        archived = (folder / "archive.json").exists()
        raw = (folder / "result.json").read_bytes()
        payload = dict(schemaVersion="voice-coaching.analysis-history-event.v1", jobId=doc["jobId"],
            executionId=doc["executionId"], clientAttemptId=doc["clientAttemptId"],
            historyClaimSha256=doc["historyClaimSha256"], audioSha256=doc["audioSha256"],
            scriptSha256=doc["scriptSha256"], contentId=doc["contentId"], scriptText=doc["scriptText"],
            resultSha256=hashlib.sha256(raw).hexdigest(), result=json.loads(raw),
            archive=json.loads((folder / "archive.json").read_bytes()) if archived else None)
        base = self.environment["AI_DIRECT_HISTORY_BASE_URL"].rstrip("/")
        if not base.startswith("https://"):
            raise ValueError("HISTORY_HTTPS_REQUIRED")
        req = Request(base + "/api/internal/ai/direct-analysis-history", data=encoded(payload), method="POST",
            headers={"Authorization": "Bearer " + self.environment["AI_ANALYSIS_CALLBACK_TOKEN"], "Content-Type": "application/json"})
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=10) as response:
            if response.status not in (200, 202):
                raise ValueError("HISTORY_ACK_INVALID")
        return archived
