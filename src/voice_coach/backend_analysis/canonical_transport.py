"""Explicit, no-retry transport for the frozen STEP7 serialized request.

Kept separate from ChatCoachingGenerator: rebuilding its messages/schema would
silently change the frozen contract. Import/construction performs no API call.
"""
from __future__ import annotations

import time
import http.client
import ssl

from .canonical_bridge import CanonicalIntegrationError, _json, _require
from .canonical_provider_observation import response_observation


ENDPOINT = "https://api.openai.com/v1/chat/completions"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 131072


def transport_for_artifacts(api_key: str, artifacts):
    """Keep each verified release's model/settings and the shared transport lifecycle."""
    return FrozenOpenAITransport.for_harness(api_key, artifacts.identity)


class FrozenOpenAITransport:
    def __init__(self, api_key: str, *, combined_grading: bool = False,
                 model: str = "gpt-6-astra"):
        _require(isinstance(api_key, str) and bool(api_key.strip())
                 and "\r" not in api_key and "\n" not in api_key,
                 "CANONICAL_PROVIDER_CONFIGURATION")
        self._api_key = api_key
        self._completion_tokens = 3000 if combined_grading else 1200
        _require(model in {"gpt-6-astra", "gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra"}, "CANONICAL_PROVIDER_CONFIGURATION")
        self._model = model
        self._connection: http.client.HTTPSConnection | None = None
        self._expected = dict(model=model, max_completion_tokens=self._completion_tokens,
            reasoning_effort="low", response_format={"type": "json_object"}, store=False)
        self.diagnostics = self._observation()

    @classmethod
    def for_harness(cls, api_key, identity):
        from .canonical_harness import harness_identity
        registered = harness_identity(identity.manifest_sha256)
        _require(registered == identity and identity.reasoning_effort in {'low', 'none'}
                 and identity.service_tier in {None, 'default'}, "FROZEN_PROVIDER_IDENTITY")
        transport = cls(api_key, combined_grading=identity.gpt_grading, model=identity.model)
        transport._expected['reasoning_effort'] = identity.reasoning_effort
        if identity.service_tier is not None:
            transport._expected['service_tier'] = identity.service_tier
        if identity.explicit_prompt_cache:
            transport._expected['prompt_cache_options'] = dict(mode='explicit', ttl='30m')
        transport.diagnostics = transport._observation()
        return transport

    def _observation(self):
        return dict(requestedModel=self._model, reasoningEffort=self._expected['reasoning_effort'],
            requestedServiceTier=self._expected.get('service_tier'),
            promptCacheMode='explicit' if 'prompt_cache_options' in self._expected else 'implicit',
            status='NOT_DISPATCHED', errorCode=None, httpStatus=None,
            requestBytes=None, responseBytes=None, requestValidationSeconds=None,
            httpHeadersSeconds=None, responseBodySeconds=None, totalSeconds=None,
            connectionReused=None, connectSeconds=None, **response_observation(b'{}'))

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _post(self, payload, timeout, started):
        # One POST only. A stale keep-alive failure is never resent: the server
        # may already have generated a response even if its receipt was lost.
        reused = self._connection is not None and self._connection.sock is not None
        connection = self._connection
        if connection is None:
            connection = http.client.HTTPSConnection('api.openai.com',
                timeout=timeout, context=ssl.create_default_context())
            self._connection = connection
        self.diagnostics['connectionReused'] = reused
        connected = time.perf_counter()
        if not reused:
            connection.connect()
        self.diagnostics['connectSeconds'] = time.perf_counter()-connected

        def remaining():
            value = timeout-(time.perf_counter()-started)
            if value <= 0:
                raise TimeoutError()
            if connection.sock is not None:
                connection.sock.settimeout(value)

        remaining()
        self.diagnostics['postAttempts'] = self.diagnostics.get('postAttempts', 0) + 1
        connection.request('POST', '/v1/chat/completions', body=payload,
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+self._api_key})
        remaining()
        response = connection.getresponse()
        return response, remaining

    def __call__(self, payload: bytes, *, timeout: float) -> bytes:
        started = time.perf_counter()
        self.diagnostics = self._observation()
        _require(type(payload) is bytes and 0 < len(payload) <= MAX_REQUEST_BYTES,
                 "CANONICAL_REQUEST_SIZE")
        _require(timeout == 120, "FROZEN_TIMEOUT_MISMATCH")
        body = _json(payload)
        import hashlib
        messages = body.get('messages', []) if isinstance(body, dict) else []
        self.diagnostics.update(postAttempts=0,
            rawCoreMessageSha256=hashlib.sha256(messages[-1]['content'].encode('utf-8')).hexdigest()
                if messages and isinstance(messages[-1].get('content'), str) else None)
        expected = self._expected
        _require(isinstance(body, dict) and set(body) == set(expected) | {"messages"}
                 and all(body.get(key) == value for key, value in expected.items())
                 and body.get("store") is False, "FROZEN_REQUEST_CONFIG_MISMATCH")
        # No body reconstruction, arbitrary endpoint, env proxy or redirect.
        configured = time.perf_counter()
        self.diagnostics.update(status='DISPATCHING', requestBytes=len(payload),
                                requestValidationSeconds=configured - started)
        try:
            response, remaining = self._post(payload, timeout, started)
            with response:
                headers_received = time.perf_counter()
                self.diagnostics.update(httpStatus=response.status,
                                        httpHeadersSeconds=headers_received - configured)
                if 300 <= response.status < 400:
                    raise CanonicalIntegrationError('CANONICAL_PROVIDER_REDIRECT')
                if response.status >= 400:
                    raise CanonicalIntegrationError('CANONICAL_PROVIDER_UNAVAILABLE')
                _require(response.status == 200, "CANONICAL_PROVIDER_STATUS")
                chunks, size = [], 0
                while size <= MAX_RESPONSE_BYTES:
                    remaining()
                    chunk = response.read1(min(8192, MAX_RESPONSE_BYTES+1-size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                raw = b''.join(chunks)
                body_received = time.perf_counter()
                self.diagnostics.update(responseBytes=len(raw),
                                        responseBodySeconds=body_received - headers_received)
        except (TimeoutError, OSError, http.client.HTTPException):
            self.close()
            # Do not expose response bodies, request text, keys or URL diagnostics.
            self.diagnostics.update(status='FAILED', errorCode='CANONICAL_PROVIDER_UNAVAILABLE')
            raise CanonicalIntegrationError("CANONICAL_PROVIDER_UNAVAILABLE") from None
        except CanonicalIntegrationError as error:
            self.close()
            self.diagnostics.update(status='FAILED', errorCode=str(error))
            raise
        finally:
            self.diagnostics['totalSeconds'] = time.perf_counter() - started
        if not 0 < len(raw) <= MAX_RESPONSE_BYTES:
            self.close()
            self.diagnostics.update(status='FAILED', errorCode='CANONICAL_RESPONSE_SIZE')
        _require(0 < len(raw) <= MAX_RESPONSE_BYTES, "CANONICAL_RESPONSE_SIZE")
        # Private timing only: never retain prompt, generated text, keys or headers.
        self.diagnostics.update(response_observation(raw), status='RESPONSE_RECEIVED')
        if expected.get('service_tier') == 'default' and self.diagnostics['actualServiceTier'] in {'fast', 'priority'}:
            self.diagnostics.update(status='FAILED', errorCode='CANONICAL_PROVIDER_TIER_MISMATCH')
            raise CanonicalIntegrationError('CANONICAL_PROVIDER_TIER_MISMATCH')
        return raw
