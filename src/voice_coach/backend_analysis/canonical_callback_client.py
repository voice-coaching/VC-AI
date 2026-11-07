"""Exact-byte v4 callback delivery and non-applying ACK-loss reconciliation.

No constructor starts a task. The production owner schedules bounded attempts,
maintains heartbeat/cancellation, and supplies the live remaining lease budget.
Calls without live ownership can ONLY use result/ack, never result ingestion.
Restart recovery may revalidate the original live owner by Backend heartbeat;
it cannot create a claim or reuse a persisted/expired local lease.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from email.utils import parsedate_to_datetime
import math
import time
from typing import Callable
from uuid import UUID

import httpx

from .canonical_bridge import CanonicalIntegrationError, _require
from .canonical_codec import read_document, validate_document, encode_document
from .canonical_delivery import CallbackDelivery, CanonicalResultOutbox
from .http_models import HttpFailure, MAX_CONTROL_BYTES, parse_json, validate, timestamp
from .http_settings import HttpSettings


_RECONCILE_CODES = frozenset({'ANALYSIS_TERMINAL', 'LEASE_EXPIRED', 'DEADLINE_EXCEEDED',
                            'STALE_EXECUTION', 'ANALYSIS_CANCELLED', 'CLAIM_REQUIRED',
                            'WORKER_CONFLICT', 'RESULT_ALREADY_FINALIZED'})


class CallbackTransportError(CanonicalIntegrationError):
    def __init__(self, code: str, *, retryable: bool, retry_after: float = 1,
                 upstream_status: int | None = None):
        super().__init__(code)
        self.code, self.retryable, self.retry_after = code, retryable, retry_after
        self.upstream_status = upstream_status


def _retry_after(value: str, *, upstream_status: int | None = None) -> float:
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 1
    if not math.isfinite(delay) or delay > 60:
        raise CallbackTransportError('CALLBACK_BACKOFF_EXCEEDS_BUDGET', retryable=False,
                                     upstream_status=upstream_status)
    return max(1, delay)


class CanonicalCallbackClient:
    def __init__(self, settings: HttpSettings, outbox: CanonicalResultOutbox):
        _require(type(settings) is HttpSettings and type(outbox) is CanonicalResultOutbox,
                 'CALLBACK_CONFIGURATION_INVALID')
        self._outbox = outbox
        self._client = httpx.AsyncClient(
            base_url=settings.callback_base,
            headers={'Authorization': 'Bearer ' + settings.callback_token,
                     'Content-Type': 'application/json', 'Accept-Encoding': 'identity'},
            timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, delivery: CallbackDelivery, budget: float, *, heartbeat: bool = False) -> bytes:
        path = f'/api/internal/ai/analyses/{delivery.request.analysis_id}/result'
        if delivery.ack_only:
            path += '/ack'
        content = delivery.raw
        if heartbeat:
            _require(not delivery.ack_only, 'CALLBACK_CONFIRMATION_ONLY')
            path = f'/api/internal/ai/analyses/{delivery.request.analysis_id}/heartbeat'
            content = encode_document(dict(requestId=str(delivery.request.event_id),
                executionId=str(delivery.request.execution_id), workerInstanceId=str(delivery.identity.worker_instance_id)))

        async def receive():
            async with self._client.stream('POST', path, content=content,
                    headers={'X-Worker-Instance-Id': str(delivery.identity.worker_instance_id)}) as response:
                raw = bytearray()
                _require(response.headers.get('Content-Encoding', 'identity') == 'identity', 'CALLBACK_RESPONSE_ENCODING')
                async for chunk in response.aiter_raw():
                    raw.extend(chunk)
                    _require(len(raw) <= MAX_CONTROL_BYTES, 'CALLBACK_RESPONSE_SIZE')
                if response.status_code != 200:
                    retryable = response.status_code in (429, 500, 502, 503, 504)
                    reason = 'DEPENDENCY_UNAVAILABLE'
                    try:
                        error = validate(parse_json(bytes(raw)), 'error')
                    except HttpFailure:
                        pass  # A bounded proxy error is not an ACK or a new authority.
                    else:
                        _require(error['requestId'] in (None, str(delivery.request.event_id))
                                 and error['executionId'] in (None, str(delivery.request.execution_id)),
                                 'CALLBACK_ERROR_IDENTITY')
                        reason, retryable = error['reasonCode'], retryable and error['retryable']
                    raise CallbackTransportError(reason, retryable=retryable,
                        upstream_status=response.status_code,
                        retry_after=_retry_after(response.headers.get('Retry-After', '1'),
                                                 upstream_status=response.status_code) if retryable else 1)
                _require(response.headers.get('Content-Type', '').split(';')[0].strip() == 'application/json',
                         'CALLBACK_RESPONSE_TYPE')
                ack = read_document(bytes(raw), MAX_CONTROL_BYTES)
                if heartbeat:
                    validate_document(ack, 'leaseResponse', schema_name='control')
                    _require(all(ack[k] == v for k, v in {
                        'analysisId': delivery.request.analysis_id, 'requestId': str(delivery.request.event_id),
                        'executionId': str(delivery.request.execution_id),
                        'workerInstanceId': str(delivery.identity.worker_instance_id),
                    }.items()), 'CALLBACK_LEASE_IDENTITY')
                    return bytes(raw)
                validate_document(ack, 'resultAck', schema_name='control')
                _require(all(ack[k] == v for k, v in {
                    'eventId': str(delivery.identity.event_id), 'analysisId': delivery.request.analysis_id,
                    'requestId': str(delivery.request.event_id), 'executionId': str(delivery.request.execution_id),
                }.items()) and (not delivery.ack_only or ack['status'] == 'DUPLICATE'), 'CALLBACK_ACK_IDENTITY')
                return bytes(raw)
        try:
            return await asyncio.wait_for(receive(), timeout=min(10, budget))
        except (httpx.HTTPError, TimeoutError):
            raise CallbackTransportError('CALLBACK_TRANSPORT_UNAVAILABLE', retryable=True) from None

    async def deliver_once(self, execution_id: UUID, *, live_worker_id: UUID | None = None,
                           execution_is_active: Callable[[], bool] | None = None,
                           remaining_seconds: Callable[[], float] | None = None) -> str:
        """One bounded attempt; no AI/GPT, uploads, lease renewal or hidden task loop.

        Omit ALL live arguments for confirmation-only recovery. To resume an
        unfinished delivery after restart, use recover_once for live Backend lease
        revalidation first. Stored worker/body/event stay fixed. The caller reschedules
        pending work by persisted next_attempt_at; local ACK is not public visibility.
        """
        live = live_worker_id is not None
        _require((not live and execution_is_active is None and remaining_seconds is None)
                 or (type(live_worker_id) is UUID and callable(execution_is_active) and callable(remaining_seconds)),
                 'CALLBACK_FENCE_REQUIRED')
        delivery = await asyncio.to_thread(self._outbox.claim, execution_id, ack_only=not live)
        if delivery is None:
            state, _ = await asyncio.to_thread(self._outbox.status, execution_id)
            return state

        def budget():
            if delivery.ack_only:
                return 10.0
            if live_worker_id != delivery.identity.worker_instance_id or execution_is_active() is not True:
                return 0.0
            seconds = remaining_seconds()
            _require(type(seconds) in (int, float) and math.isfinite(seconds), 'CALLBACK_FENCE_INVALID')
            return min(10.0, seconds, (delivery.deadline - datetime.now(UTC)).total_seconds())

        return await self._send_claimed(delivery, budget)

    async def recover_once(self, execution_id: UUID) -> str:
        """Revalidate the original worker's still-live lease; never claim a new one.

        No AI/GPT or upload can resume through this method. A heartbeat response
        is the only source of a fresh monotonic delivery budget after restart.
        Expired/terminal ownership falls back to non-applying ACK confirmation.
        """
        delivery = await asyncio.to_thread(self._outbox.claim, execution_id)
        if delivery is None:
            state, _ = await asyncio.to_thread(self._outbox.status, execution_id)
            return state
        if delivery.ack_only:
            return await self._send_claimed(delivery, lambda: 10.0)
        try:
            deadline_budget = (delivery.deadline - datetime.now(UTC)).total_seconds()
            if deadline_budget <= 0:
                await asyncio.to_thread(self._outbox.release, delivery, reason='DEADLINE_EXCEEDED',
                                        retry_after=0, confirmation_only=True)
                return 'PENDING'
            sent = time.monotonic()
            raw = await self._call(delivery, min(10, deadline_budget), heartbeat=True)
            lease = read_document(raw, MAX_CONTROL_BYTES)
            duration = (timestamp(lease['leaseExpiresAt']) - timestamp(lease['serverTime'])).total_seconds()
            _require(0 < duration <= 90 and timestamp(lease['leaseExpiresAt']) <= delivery.deadline,
                     'CALLBACK_LEASE_INVALID')
            lease_until = sent + duration - 2
        except CallbackTransportError as error:
            reconcile = error.code in _RECONCILE_CODES
            delay = max(error.retry_after, min(15, 2 ** min(delivery.attempts - 1, 4)))
            await asyncio.to_thread(self._outbox.release, delivery, reason=error.code,
                                    retry_after=delay if error.retryable or reconcile else None,
                                    confirmation_only=reconcile)
            return 'PENDING' if error.retryable or reconcile else 'STOPPED'
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self._outbox.release, delivery,
                                 reason='CALLBACK_INTERRUPTED', retry_after=0, confirmation_only=True))
            raise
        except CanonicalIntegrationError:
            await asyncio.to_thread(self._outbox.release, delivery,
                                    reason='CALLBACK_CONTRACT_INVALID', retry_after=None)
            raise
        return await self._send_claimed(delivery, lambda: min(lease_until - time.monotonic(),
            (delivery.deadline - datetime.now(UTC)).total_seconds()))

    async def _send_claimed(self, delivery: CallbackDelivery, budget: Callable[[], float]) -> str:
        try:
            available = budget()
            if available <= 0:
                await asyncio.to_thread(self._outbox.release, delivery, reason='CALLBACK_EXECUTION_INACTIVE',
                                        retry_after=0, confirmation_only=True)
                return 'PENDING'
            ack = await self._call(delivery, available)
            # A returned authenticated ACK must survive cancellation that happened during HTTP.
            # Recording it does not apply a new result or renew a lost execution lease.
            await asyncio.to_thread(self._outbox.acknowledge, delivery, ack)
            return 'ACKNOWLEDGED'
        except CallbackTransportError as error:
            expired = not delivery.ack_only and (error.code in _RECONCILE_CODES
                                                or (error.retryable and budget() <= 0))
            delay = max(error.retry_after, min(15, 2 ** min(delivery.attempts - 1, 4)))
            await asyncio.to_thread(self._outbox.release, delivery, reason=error.code,
                                    retry_after=delay if error.retryable or expired else None,
                                    confirmation_only=expired)
            return 'PENDING' if error.retryable or expired else 'STOPPED'
        except asyncio.CancelledError:
            # In-flight outcome is unknown. Only a non-applying confirmation can follow.
            await asyncio.shield(asyncio.to_thread(self._outbox.release, delivery,
                                 reason='CALLBACK_INTERRUPTED', retry_after=0, confirmation_only=True))
            raise
        except CanonicalIntegrationError:
            await asyncio.to_thread(self._outbox.release, delivery,
                                    reason='CALLBACK_CONTRACT_INVALID', retry_after=None)
            raise
