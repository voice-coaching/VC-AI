"""Durable sentence groups; child inference stays in the existing execution lane."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from time import time
from uuid import UUID, uuid5

from .domain import DirectFailure
from .store import atomic, encoded

TERMINAL = {"RESULT_READY", "FAILED", "CANCELLED"}
MAX_SENTENCES = 50


class BatchService:
    """Retain each result privately until every declared sentence is terminal.

    Child attempt IDs are derived before admission. A lost upload acknowledgement
    or a crash between child admission and the next group read cannot duplicate
    inference. Existing one-shot journals and archive bytes remain unchanged.
    """

    def __init__(self, store, analysis):
        self.store, self.analysis = store, analysis
        self.root = store.root / "batches"
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.submissions = asyncio.Lock()

    def path(self, batch_id):
        try:
            return self.root / (str(UUID(batch_id)) + ".json")
        except (ValueError, TypeError, AttributeError):
            raise DirectFailure("BATCH_NOT_FOUND", 404) from None

    def read(self, batch_id):
        path = self.path(batch_id)
        if not path.exists():
            raise DirectFailure("BATCH_NOT_FOUND", 404)
        return json.loads(path.read_bytes())

    def create(self, payload):
        if type(payload) is not dict or set(payload) != {
                "clientBatchId", "sentences", "contentId", "historyClaimSha256"}:
            raise DirectFailure("INVALID_BATCH", 422)
        self.path(payload["clientBatchId"])
        sentences = payload["sentences"]
        if (type(sentences) is not list or not 1 <= len(sentences) <= MAX_SENTENCES
                or any(type(text) is not str or not text.strip() for text in sentences)
                or sum(map(len, sentences)) > 4000):
            raise DirectFailure("INVALID_BATCH_SENTENCES", 422)
        content, claim = payload["contentId"], payload["historyClaimSha256"]
        if content is not None and (type(content) is not int or content <= 0):
            raise DirectFailure("INVALID_CONTENT", 422)
        if type(claim) is not str or not re.fullmatch("[a-f0-9]{64}", claim):
            raise DirectFailure("INVALID_HISTORY_LINK_DIGEST", 422)
        batch_id = str(UUID(payload["clientBatchId"]))
        identity = hashlib.sha256(encoded([batch_id, sentences, content, claim])).hexdigest()
        with self.store.mutation_lock:
            path = self.path(batch_id)
            if path.exists():
                if self.read(batch_id)["requestSha256"] != identity:
                    raise DirectFailure("IDEMPOTENCY_CONFLICT", 409)
            else:
                if len(list(self.root.glob("*.json"))) >= self.store.settings.maximum_jobs:
                    raise DirectFailure("SPOOL_FULL", 503)
                atomic(path, encoded(dict(batchId=batch_id, requestSha256=identity,
                    sentences=sentences, contentId=content, historyClaimSha256=claim,
                    createdAt=time(), published=False, cancelled=False)))
        return self.view(batch_id)

    def child(self, doc, index):
        attempt = str(uuid5(UUID(doc["batchId"]), f"sentence:{index}"))
        job = self.store.attempts.get(attempt)
        if job is not None:
            child = self.store.read(job)
            if child.get("batchId") != doc["batchId"]:
                raise DirectFailure("IDEMPOTENCY_CONFLICT", 409)
            return attempt, child
        return attempt, None

    async def submit(self, batch_id, index, audio):
        async with self.submissions:
            doc = self.read(batch_id)
            if not 0 <= index < len(doc["sentences"]):
                raise DirectFailure("INVALID_SENTENCE_INDEX", 422)
            if doc["cancelled"]:
                raise DirectFailure("BATCH_CANCELLED", 409)
            attempt, _ = self.child(doc, index)
            await self.analysis.submit(dict(clientAttemptId=attempt,
                scriptText=doc["sentences"][index], contentId=doc["contentId"],
                historyClaimSha256=doc["historyClaimSha256"]), audio, batch_id=doc["batchId"])
            return await asyncio.to_thread(self.view, batch_id)

    def view(self, batch_id):
        with self.store.mutation_lock:
            doc = self.read(batch_id)
            children = [self.child(doc, index)[1] for index in range(len(doc["sentences"]))]
            # An explicitly closed executor cannot drain the queue. Keep actual
            # failures visible rather than waiting forever or replaying inference.
            if self.analysis.ready_error:
                for index, child in enumerate(children):
                    if child is not None and child["status"] == "QUEUED":
                        children[index] = self.store.update(child["jobId"], status="FAILED",
                            failure="BATCH_EXECUTION_UNAVAILABLE")
            uploaded = sum(child is not None for child in children)
            completed = sum(child is not None and child["status"] in TERMINAL for child in children)
            ready = completed == len(children) and not doc["cancelled"]
            if ready and not doc["published"]:
                doc["published"] = True
                atomic(self.path(batch_id), encoded(doc))
            # The same barrier protects create/upload replies and aggregate GET.
            rows = [dict(sequenceNo=i+1, scriptText=text,
                         jobId=child["jobId"] if child else None,
                         status=child["status"] if child else "AWAITING_UPLOAD")
                    for i, (text, child) in enumerate(zip(doc["sentences"], children))]
            return dict(schemaVersion="voice-coaching.direct-practice-batch.v1",
                batchId=doc["batchId"], status="CANCELLED" if doc["cancelled"] else
                    "RESULT_READY" if ready else "RECORDING" if uploaded < len(children) else "PROCESSING",
                sentenceCount=len(children), uploadedCount=uploaded, completedCount=completed,
                sentences=rows, results=[dict(sequenceNo=i+1, scriptText=doc["sentences"][i],
                    analysis=self.store.view(child["jobId"])) for i, child in enumerate(children)] if ready else None)

    async def cancel(self, batch_id):
        async with self.submissions:
            with self.store.mutation_lock:
                doc = self.read(batch_id)
                if not doc["published"]:
                    doc["cancelled"] = True
                    atomic(self.path(batch_id), encoded(doc))
                    for index in range(len(doc["sentences"])):
                        _, child = self.child(doc, index)
                        if child:
                            self.store.cancel(child["jobId"])
            return await asyncio.to_thread(self.view, batch_id)
