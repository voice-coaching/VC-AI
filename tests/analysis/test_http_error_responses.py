"""Offline protocol checks: all 4xx/5xx statuses, no media, inference or GPT."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from voice_coach.backend_analysis.http_api import create_app as internal_app
from voice_coach.backend_analysis.http_models import HttpFailure, validate
from voice_coach.backend_analysis.canonical_handoff import CanonicalHandoffClient, HandoffHttpFailure
from voice_coach.backend_analysis.canonical_callback_client import CanonicalCallbackClient, CallbackTransportError
from voice_coach.direct_analysis.api import create_app as direct_app
from voice_coach.direct_analysis.domain import DirectFailure


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, raw):
        self.raw = raw

    async def __aiter__(self):
        yield self.raw


def backend_reply(status, *, content=b'', payload=None):
    raw = json.dumps(payload).encode() if payload is not None else content
    return httpx.Response(status, headers={'Content-Type': 'application/json'}, stream=ResponseStream(raw))


class HttpErrorResponses(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.environment = {'AI_ANALYSIS_API_TOKEN': 'test-token', 'AI_DIRECT_ENABLED': 'true',
            'AI_DIRECT_SPOOL_ROOT': self.root.name,
            'AI_DIRECT_ALLOWED_ORIGINS': 'https://frontend.invalid'}
        self.addCleanup(self.root.cleanup)

    def applications(self):
        internal = internal_app({'AI_ANALYSIS_API_TOKEN': 'test-token'})
        direct = direct_app(self.environment)
        mounted = internal_app(self.environment)
        child = next(route.app for route in mounted.routes if getattr(route, 'path', '') == '/direct')
        return [(internal, internal, '', False), (direct, direct, '', True),
                (mounted, child, '/direct', True)]

    async def test_every_framework_4xx_and_5xx_reaches_the_caller_as_json(self):
        for app, target, prefix, direct in self.applications():
            @target.get('/http-error/{status}')
            async def fail(status: int):
                raise HTTPException(status, detail='test-only-secret',
                    headers={'X-Secret': 'test-only-secret'})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='http://test', headers={'Authorization': 'Bearer test-token'}) as client:
                for status in range(400, 600):
                    with self.subTest(direct=direct, mounted=bool(prefix), status=status):
                        reply = await client.get(f'{prefix}/http-error/{status}')
                        self.assertEqual(reply.status_code, status)
                        self.assertEqual(reply.headers['cache-control'], 'no-store')
                        self.assertIn('application/json', reply.headers['content-type'])
                        self.assertNotIn('test-only-secret', reply.text)
                        self.assertNotIn('x-secret', reply.headers)
                        if not direct:
                            validate(reply.json(), 'error')
                        else:
                            self.assertEqual(set(reply.json()), {'code'})

    async def test_domain_failures_retain_all_4xx_and_5xx_statuses(self):
        for app, target, prefix, direct in self.applications():
            @target.get('/domain-error/{status}')
            async def fail(status: int):
                raise DirectFailure('INTERNAL_ERROR', status) if direct else HttpFailure(status, 'INTERNAL_ERROR')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='http://test', headers={'Authorization': 'Bearer test-token'}) as client:
                for status in range(400, 600):
                    reply = await client.get(f'{prefix}/domain-error/{status}')
                    self.assertEqual(reply.status_code, status)
                    self.assertEqual(reply.json()['code' if direct else 'reasonCode'], 'INTERNAL_ERROR')

    async def test_unknown_routes_methods_validation_and_unexpected_exceptions(self):
        for app, target, prefix, direct in self.applications():
            @target.get('/typed/{count}')
            async def typed(count: int):
                return {'count': count}
            @target.get('/crash')
            async def crash():
                raise RuntimeError('test-only-secret')
            @target.get('/headers')
            async def headers():
                raise HTTPException(405, headers={'Allow': 'GET'})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='http://test', headers={'Authorization': 'Bearer test-token',
                    'Origin': 'https://frontend.invalid'}) as client:
                for path, status in [('/absent', 404), ('/typed/test-only-secret', 422), ('/crash', 500)]:
                    reply = await client.get(prefix + path)
                    self.assertEqual(reply.status_code, status)
                    self.assertNotIn('test-only-secret', reply.text)
                    self.assertEqual(reply.headers['cache-control'], 'no-store')
                    if direct:
                        self.assertEqual(reply.headers['access-control-allow-origin'], 'https://frontend.invalid')
                reply = await client.post(prefix + '/crash')
                self.assertEqual(reply.status_code, 405)
                self.assertIn('GET', reply.headers['allow'])
                reply = await client.get(prefix + '/headers')
                self.assertEqual(reply.headers['allow'], 'GET')

    async def test_authentication_and_readiness_keep_their_statuses(self):
        app = internal_app({'AI_ANALYSIS_API_TOKEN': 'test-token'})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            reply = await client.get('/v1/analysis-jobs')
            self.assertEqual(reply.status_code, 401)
            self.assertEqual(reply.headers['www-authenticate'], 'Bearer')
            self.assertEqual((await client.get('/readyz')).status_code, 503)

    async def test_direct_upload_timeout_is_408(self):
        app = direct_app(self.environment)
        @app.get('/timeout')
        async def timeout():
            raise TimeoutError('test-only-secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            reply = await client.get('/timeout')
            self.assertEqual(reply.status_code, 408)
            self.assertEqual(reply.json(), {'code': 'UPLOAD_TIMEOUT'})


class HandoffStatuses(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        client = CanonicalHandoffClient(SimpleNamespace(callback_base='https://backend.invalid',
            callback_token='test-token'), uuid4())
        self.addAsyncCleanup(client.client.aclose)
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.close)
        return client

    async def test_all_backend_4xx_and_5xx_keep_the_status_even_for_html_errors(self):
        for status in range(400, 600):
            client = self.client(lambda request, code=status: backend_reply(code, content=b'<html>secret</html>'))
            with self.subTest(status=status):
                with self.assertRaises(HandoffHttpFailure) as caught:
                    await client._request('POST', '/api/internal/ai/analyses/168/handoffs', b'{}')
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn('secret', str(caught.exception))
                self.assertEqual(caught.exception.retryable, status in (429, 500, 502, 503, 504))

    async def test_terminal_rejection_keeps_safe_backend_reason_and_is_not_retried(self):
        calls = []
        def handler(request):
            calls.append(request.method)
            return backend_reply(422, payload=HttpFailure(422, 'VALIDATION_FAILED').payload())
        client = self.client(handler)
        with self.assertRaises(HandoffHttpFailure) as caught:
            await client.deliver({'metadata': {'analysisId': 168, 'handoffId': 'test'},
                'handoffSha256': 'a' * 64}, (), active=lambda: True, remaining=lambda: 10)
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.reason_code, 'VALIDATION_FAILED')
        self.assertEqual(calls, ['POST'])

    async def test_transient_rejection_probes_receipt_before_repeating_the_post(self):
        calls = []
        def handler(request):
            calls.append(request.method)
            if request.method == 'POST':
                return backend_reply(503, payload=HttpFailure(503, 'DEPENDENCY_UNAVAILABLE').payload())
            return backend_reply(200, payload={'state': 'COMMITTED', 'handoffId': 'test', 'handoffSha256': 'a' * 64})
        client = self.client(handler)
        with patch('voice_coach.backend_analysis.canonical_handoff.asyncio.sleep', new=AsyncMock()):
            result = await client.deliver({'metadata': {'analysisId': 168, 'handoffId': 'test'},
                'handoffSha256': 'a' * 64}, (), active=lambda: True, remaining=lambda: 10)
        self.assertEqual(result['state'], 'COMMITTED')
        self.assertEqual(calls, ['POST', 'GET'])

    async def test_absent_receipt_still_maps_get_404_to_unknown(self):
        client = self.client(lambda request: backend_reply(404))
        self.assertEqual(await client._request('GET', '/receipt'), {'state': 'UNKNOWN'})

    async def test_legacy_callback_preserves_each_http_error_status(self):
        delivery = SimpleNamespace(ack_only=False, raw=b'{}', request=SimpleNamespace(
            analysis_id=168, event_id=uuid4(), execution_id=uuid4()),
            identity=SimpleNamespace(worker_instance_id=uuid4()))
        for status in range(400, 600):
            client = CanonicalCallbackClient.__new__(CanonicalCallbackClient)
            client._client = httpx.AsyncClient(base_url='https://backend.invalid',
                transport=httpx.MockTransport(lambda request, code=status: backend_reply(
                    code, payload=HttpFailure(code, 'DEPENDENCY_UNAVAILABLE').payload())))
            try:
                with self.assertRaises(CallbackTransportError) as caught:
                    await client._call(delivery, 10)
                self.assertEqual(caught.exception.upstream_status, status)
            finally:
                await client.close()


if __name__ == '__main__':
    unittest.main()
