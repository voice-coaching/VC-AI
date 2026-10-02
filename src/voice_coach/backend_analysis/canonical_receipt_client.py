"""Authenticated evidence registration/status, distinct from legacy 200-only controls.

Retries resend the exact retained manifest, never objects or AI/GPT inference.
Only a live authenticated VERIFIED response can produce TrustedBackendVerifiedReceipt.
No client constructor opens admission or makes an HTTP request.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Callable
from uuid import UUID

import httpx

from .canonical_bridge import CanonicalIntegrationError, _require
from .canonical_journal_port import CanonicalJournalPort
from .canonical_codec import read_document as _read_json, validate_document as _validate
from .canonical_result import MAX_MANIFEST_BYTES, TrustedBackendVerifiedReceipt
from .canonical_retention import RetainedCanonicalManifest
from .http_settings import HttpSettings


class ReceiptTransportError(CanonicalIntegrationError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.retryable = retryable


class CanonicalReceiptClient:
    def __init__(self, settings: HttpSettings, worker_instance_id: UUID):
        _require(type(settings) is HttpSettings and type(worker_instance_id) is UUID,
                 'EVIDENCE_RECEIPT_CONFIGURATION')
        self._worker = worker_instance_id
        self._client = httpx.AsyncClient(
            base_url=settings.callback_base,
            headers={'Authorization': 'Bearer ' + settings.callback_token,
                     'X-Worker-Instance-Id': str(worker_instance_id),
                     'Accept-Encoding': 'identity', 'Content-Type': 'application/json'},
            timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, path: str, raw: bytes | None, budget: float) -> dict:
        async def receive():
            async with self._client.stream('POST' if raw is not None else 'GET', path, content=raw) as response:
                _require(response.headers.get('Content-Encoding', 'identity') == 'identity',
                         'EVIDENCE_RECEIPT_CONTENT_ENCODING')
                _require(response.headers.get('Content-Type', '').split(';')[0].strip() == 'application/json',
                         'EVIDENCE_RECEIPT_CONTENT_TYPE')
                body = bytearray()
                async for chunk in response.aiter_raw():
                    body.extend(chunk)
                    _require(len(body) <= MAX_MANIFEST_BYTES, 'EVIDENCE_RECEIPT_RESPONSE_SIZE')
                document = _read_json(bytes(body), MAX_MANIFEST_BYTES)
                if response.status_code not in ((200, 202) if raw is not None else (200,)):
                    _validate(document, 'evidenceError', schema_name='control')
                    retryable = response.status_code in (429, 502, 503, 504) and document['retryable'] is True
                    raise ReceiptTransportError(document['reasonCode'], retryable=retryable)
                _validate(document, 'evidenceReceipt', schema_name='control')
                return document
        try:
            return await asyncio.wait_for(receive(), timeout=min(10, budget))
        except (httpx.HTTPError, TimeoutError):
            raise ReceiptTransportError('EVIDENCE_RECEIPT_UNAVAILABLE', retryable=True) from None

    async def await_verified(self, retained: RetainedCanonicalManifest, *, journal: CanonicalJournalPort,
                             deadline_monotonic: float, execution_is_active: Callable[[], bool]) -> TrustedBackendVerifiedReceipt:
        _require(type(retained) is RetainedCanonicalManifest
                 and type(deadline_monotonic) in (float, int)
                 and time.monotonic() < deadline_monotonic < float('inf'), 'EVIDENCE_RECEIPT_INPUT')
        _require(hashlib.sha256(retained.raw).hexdigest() == retained.sha256, 'MANIFEST_BYTES_MISMATCH')
        manifest = _read_json(retained.raw, MAX_MANIFEST_BYTES)
        _validate(manifest, 'evidenceManifest', schema_name='control')
        _require(manifest['association'] == retained.association.to_document()
                 and manifest['artifacts'] == [ref.to_document() for ref in retained.artifacts],
                 'RECEIPT_ASSOCIATION_MISMATCH')

        def fence():
            _require(time.monotonic() < deadline_monotonic, 'EVIDENCE_DEADLINE_EXCEEDED')
            _require(execution_is_active() is True, 'EVIDENCE_EXECUTION_INACTIVE')

        fence()
        receipt = await asyncio.to_thread(journal.receipt_id, retained.association, self._worker, retained.raw)
        base = f'/api/internal/ai/analyses/{retained.association.analysis_id}/evidence'
        failures = 0
        while True:
            fence()
            try:
                document = await self._call(base if receipt is None else base + '/' + str(receipt),
                                            retained.raw if receipt is None else None,
                                            deadline_monotonic - time.monotonic())
            except ReceiptTransportError as error:
                if not error.retryable:
                    raise
                failures += 1
                # Network recovery has a finite per-execution deadline, no recursive retries.
                delay = min(5.0, float(2 ** min(failures - 1, 3)))
            else:
                _require(document['association'] == retained.association.to_document()
                         and document['workerInstanceId'] == str(self._worker)
                         and document['manifestSha256'] == retained.sha256
                         and (receipt is None or document['receiptId'] == str(receipt)),
                         'RECEIPT_ASSOCIATION_MISMATCH')
                # Persist a returned registration identity even when cancellation arrived during HTTP.
                await asyncio.to_thread(journal.received_receipt, retained.association, self._worker, retained.raw, document)
                receipt = UUID(document['receiptId'])
                fence()
                if document['status'] == 'VERIFIED':
                    return TrustedBackendVerifiedReceipt(receipt, retained.sha256, retained.artifacts,
                                                         retained.association, retained.raw)
                _require(document['status'] in ('PENDING', 'VERIFYING'),
                         'EVIDENCE_RECEIPT_' + document['status'])
                failures = 0
                delay = 1.0
            # Wake for cancellation/lease fencing at least once each second of backoff.
            while delay > 0:
                fence()
                step = min(1.0, delay, max(0.0, deadline_monotonic - time.monotonic()))
                await asyncio.sleep(step)
                delay -= step

