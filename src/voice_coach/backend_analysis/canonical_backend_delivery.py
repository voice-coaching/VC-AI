"""Deliver immutable callback to the Backend-owned durable inbox, not SQLite.

The first /result POST is the durable handoff; a 503 alone is never an ACK.
Backend independently verifies and applies its stored bytes within live fences.
After lease loss/restart, recovery is GET ACK only: no AI, PUT, claim, or heartbeat.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, UTC
from email.utils import parsedate_to_datetime
import math
import time
from typing import Callable
import httpx

from .canonical_backend_journal import BackendCanonicalJournal, BackendJournalFailure
from .canonical_bridge import _require
from .canonical_codec import read_document, validate_document
from .canonical_result import ResultIdentity, MAX_RESULT_BYTES
from .http_settings import HttpSettings


def _retry_after(value):
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 1
    _require(math.isfinite(delay) and delay <= 60, 'CALLBACK_BACKOFF_EXCEEDS_BUDGET')
    return max(1, delay)


class BackendCanonicalDelivery:
    def __init__(self, settings: HttpSettings, journal: BackendCanonicalJournal):
        _require(type(journal) is BackendCanonicalJournal, 'BACKEND_JOURNAL_REQUIRED')
        self._journal = journal
        self._client = httpx.AsyncClient(base_url=settings.callback_base, follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(10, connect=3), headers={'Authorization': 'Bearer ' + settings.callback_token,
                'X-Worker-Instance-Id': str(journal.worker_id), 'Content-Type': 'application/json', 'Accept-Encoding': 'identity'})

    async def close(self):
        await self._client.aclose()

    def _ack(self, doc, identity: ResultIdentity, *, confirmation=False):
        _require(identity.worker_instance_id == self._journal.worker_id, 'CALLBACK_WORKER_CONFLICT')
        validate_document(doc, 'journalAck', schema_name='journal')
        request = self._journal.request
        _require(all(doc[key] == value for key, value in {
            'analysisId': request.analysis_id, 'requestId': str(request.event_id),
            'executionId': str(request.execution_id), 'eventId': str(identity.event_id)}.items())
            and (not confirmation or doc['status'] == 'DUPLICATE'), 'CALLBACK_ACK_IDENTITY')
        return doc

    async def recover_ack(self, identity: ResultIdentity):
        # Server checks current visibility, same original worker, APPLIED inbox and
        # immutable result. No worker assertion of an APPLIED/VERIFIED state.
        doc = await asyncio.to_thread(self._journal._call, 'GET', '/callback/ack', late=True)
        return self._ack(doc, identity, confirmation=True)

    async def deliver(self, raw: bytes, *, identity: ResultIdentity,
                      execution_is_active: Callable[[], bool], remaining_seconds: Callable[[], float]):
        doc = read_document(raw, MAX_RESULT_BYTES)
        validate_document(doc)
        _require(identity.worker_instance_id == self._journal.worker_id, 'CALLBACK_WORKER_CONFLICT')
        request = self._journal.request
        _require(all(doc[key] == value for key, value in {
            'eventId': str(identity.event_id), 'workerInstanceId': str(self._journal.worker_id),
            'requestId': str(request.event_id), 'executionId': str(request.execution_id),
            'analysisId': request.analysis_id, 'recordingId': request.recording_id, 'contentId': request.content_id,
            'workerRevision': identity.worker_revision, 'pipelineRevision': identity.pipeline_revision}.items()),
            'CALLBACK_IDENTITY_CONFLICT')
        path = f'/api/internal/ai/analyses/{request.analysis_id}/result'
        while execution_is_active():
            budget = remaining_seconds()
            if not math.isfinite(budget) or budget <= 0:
                break
            delay = 1.0
            try:
                async def send():
                    nonlocal delay
                    async with self._client.stream('POST', path, content=raw) as response:
                        _require(response.headers.get('Content-Encoding', 'identity') == 'identity', 'CALLBACK_ENCODING')
                        body = bytearray()
                        async for chunk in response.aiter_raw():
                            body.extend(chunk)
                            _require(len(body) <= 65536, 'CALLBACK_RESPONSE_SIZE')
                        if response.status_code == 200:
                            _require(response.headers.get('Content-Type', '').split(';')[0].strip() == 'application/json',
                                     'CALLBACK_CONTENT_TYPE')
                            return self._ack(read_document(bytes(body), 65536), identity)
                        if response.status_code not in (429, 500, 502, 503, 504):
                            raise BackendJournalFailure(response.status_code)
                        delay = _retry_after(response.headers.get('Retry-After', '1'))
                        return None
                ack = await asyncio.wait_for(send(), min(10, budget))
                if ack is not None:
                    return ack
            except (httpx.HTTPError, TimeoutError):
                pass  # Unknown outcome; retry only the exact body under a still-live lease.
            except BackendJournalFailure as error:
                if error.status not in (404, 409):
                    raise
                break  # Terminal/unknown ownership allows confirmation only.
            until = time.monotonic() + delay
            while time.monotonic() < until and execution_is_active() and remaining_seconds() > 0:
                await asyncio.sleep(min(0.25, max(0, until - time.monotonic())))
        # One bounded read-only reconciliation. A runtime may schedule further
        # GET-only recovery; it must never restart generation from missing scratch.
        return await self.recover_ack(identity)
