"""Durable v5 handoff transport. Retries transfer identical bytes, never inference."""
from __future__ import annotations

import asyncio
import base64
import hashlib

import httpx

from .canonical_bridge import _require
from .canonical_codec import encode_document, read_document, validate_document
from .http_models import digest, parse_json


def handoff_document(request_body, projection_raw, preflight, identity):
    projection = read_document(projection_raw)
    artifacts = preflight.artifacts if preflight is not None else ()
    _require(request_body.get("resultSchemaVersion") == projection.get("schemaVersion") ==
             "voice-coaching.runpod-analysis-result.v6", "V6_HANDOFF_REQUIRED")
    metadata = dict(schemaVersion="voice-coaching.canonical-handoff.v2",
        handoffId=str(identity.event_id), eventId=str(identity.event_id),
        workerInstanceId=str(identity.worker_instance_id),
        **{key: request_body[key] for key in
           ("requestId", "executionId", "analysisId", "recordingId", "contentId")},
        requestSha256=digest(request_body), projectionSha256=digest(parse_json(projection_raw)),
        artifacts=[dict(kind=a.kind, schemaVersion=a.schema_version,
                        sha256=hashlib.sha256(a.raw).hexdigest(), byteSize=len(a.raw))
                   for a in sorted(artifacts, key=lambda a: a.kind)])
    doc = dict(metadata=metadata, handoffSha256=digest(metadata), projection=projection,
               inlineArtifacts={a.kind: base64.b64encode(a.raw).decode("ascii") for a in artifacts})
    if len(encode_document(doc)) > 2 * 1024 * 1024:
        doc["inlineArtifacts"] = {}
    validate_document(doc, "handoff", schema_name="handoff2")
    return doc


class CanonicalHandoffClient:
    def __init__(self, settings, worker):
        self.base = settings.callback_base
        self.client = httpx.AsyncClient(trust_env=False, follow_redirects=False,
            timeout=10, limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={"Authorization": "Bearer " + settings.callback_token,
                     "X-Worker-Instance-Id": str(worker), "Accept-Encoding": "identity"})

    async def _request(self, method, path, raw=None, binary=False, timeout=10):
        # Bound the complete response, not only each read on a trickling socket.
        try:
            async with asyncio.timeout(timeout):
                return await self._exchange(method, path, raw, binary, timeout)
        except TimeoutError as error:
            raise httpx.TransportError("HANDOFF_RETRY") from error

    async def _exchange(self, method, path, raw=None, binary=False, timeout=10):
        headers = {"Content-Type": "application/octet-stream" if binary else "application/json"}
        async with self.client.stream(method, self.base + path, content=raw, headers=headers, timeout=timeout) as reply:
            _require(reply.headers.get("Content-Encoding", "identity") == "identity", "HANDOFF_RESPONSE_INVALID")
            body = bytearray()
            async for chunk in reply.aiter_raw():
                body.extend(chunk)
                _require(len(body) <= 65536, "HANDOFF_RESPONSE_INVALID")
            if reply.status_code in (200, 202):
                return parse_json(bytes(body))
            if reply.status_code == 204:
                return None
            if reply.status_code == 404:
                return {"state": "UNKNOWN"}
            if reply.status_code in (429, 500, 502, 503, 504):
                raise httpx.TransportError("HANDOFF_RETRY")
            _require(False, "HANDOFF_REJECTED")

    async def publish(self, projection_raw):
        """v6 becomes visible only after Backend verifies the durable handoff."""
        projection = parse_json(projection_raw)
        _require(projection.get("schemaVersion") == "voice-coaching.runpod-analysis-result.v6",
                 "V6_HANDOFF_REQUIRED")
        return False

    async def status(self, doc):
        meta = doc["metadata"]
        state = await self._request("GET", f'/api/internal/ai/analyses/{meta["analysisId"]}/handoffs/{meta["handoffId"]}')
        if state.get("state") != "UNKNOWN":
            _require(state.get("handoffId") == meta["handoffId"]
                     and state.get("handoffSha256") == doc["handoffSha256"], "HANDOFF_DIGEST_CONFLICT")
        return state

    async def deliver(self, doc, artifacts, *, active, remaining, probe_first=False):
        meta = doc["metadata"]
        root = f'/api/internal/ai/analyses/{meta["analysisId"]}/handoffs'
        path = root + "/" + meta["handoffId"]
        raw = encode_document(doc)
        # First send is already idempotent. Probe only after an uncertain transport outcome.
        first = not probe_first
        while active() and remaining() > 0:
            try:
                if first:
                    first = False
                    state = await self._request("POST", root, raw)
                else:
                    state = await self._request("GET", path)
                if state["state"] == "UNKNOWN":
                    state = await self._request("POST", root, raw)
                _require(state.get("handoffId") == meta["handoffId"]
                         and state.get("handoffSha256") == doc["handoffSha256"], "HANDOFF_DIGEST_CONFLICT")
                if state["state"] in ("RECEIVED", "VERIFYING", "COMMITTED"):
                    return state
                _require(state["state"] == "STAGING", "HANDOFF_REJECTED")
                semaphore = asyncio.Semaphore(2)
                async def stage(artifact):
                    async with semaphore:
                        _require(active(), "CANONICAL_EXECUTION_INACTIVE")
                        await self._request("PUT", path + "/artifacts/" + artifact.kind, artifact.raw, True)
                # Drain both requests before closing the client on a sibling failure.
                replies = await asyncio.gather(*(stage(a) for a in artifacts), return_exceptions=True)
                for reply in replies:
                    if isinstance(reply, BaseException):
                        raise reply
                state = await self._request("POST", path + "/seal")
                _require(state.get("handoffId") == meta["handoffId"]
                         and state.get("handoffSha256") == doc["handoffSha256"]
                         and state["state"] in ("RECEIVED", "VERIFYING", "COMMITTED"), "HANDOFF_RESPONSE_INVALID")
                return state
            except (httpx.TransportError, httpx.TimeoutException):
                await asyncio.sleep(min(1, max(0, remaining())))
        _require(False, "CANONICAL_EXECUTION_INACTIVE")

    async def close(self):
        await self.client.aclose()
