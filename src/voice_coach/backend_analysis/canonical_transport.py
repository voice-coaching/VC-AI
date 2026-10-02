"""Explicit, no-retry transport for the frozen STEP7 serialized request.

Kept separate from ChatCoachingGenerator: rebuilding its messages/schema would
silently change the frozen contract. Import/construction performs no API call.
"""
from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .canonical_bridge import CanonicalIntegrationError, _json, _require


ENDPOINT = "https://api.openai.com/v1/chat/completions"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 131072


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CanonicalIntegrationError("CANONICAL_PROVIDER_REDIRECT")


class FrozenOpenAITransport:
    def __init__(self, api_key: str):
        _require(isinstance(api_key, str) and bool(api_key.strip())
                 and "\r" not in api_key and "\n" not in api_key,
                 "CANONICAL_PROVIDER_CONFIGURATION")
        self._api_key = api_key

    def __call__(self, payload: bytes, *, timeout: float) -> bytes:
        _require(type(payload) is bytes and 0 < len(payload) <= MAX_REQUEST_BYTES,
                 "CANONICAL_REQUEST_SIZE")
        _require(timeout == 120, "FROZEN_TIMEOUT_MISMATCH")
        body = _json(payload)
        expected = dict(model="gpt-6-astra", max_completion_tokens=1200,
                        reasoning_effort="low", response_format={"type": "json_object"}, store=False)
        _require(isinstance(body, dict) and set(body) == set(expected) | {"messages"}
                 and all(body.get(key) == value for key, value in expected.items())
                 and body.get("store") is False, "FROZEN_REQUEST_CONFIG_MISMATCH")
        # No body reconstruction, arbitrary endpoint, env proxy or redirect.
        request = Request(ENDPOINT, data=payload, method="POST",
                          headers={"Content-Type": "application/json",
                                   "Authorization": "Bearer " + self._api_key})
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
                _require(response.status == 200, "CANONICAL_PROVIDER_STATUS")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError):
            # Do not expose response bodies, request text, keys or URL diagnostics.
            raise CanonicalIntegrationError("CANONICAL_PROVIDER_UNAVAILABLE") from None
        _require(0 < len(raw) <= MAX_RESPONSE_BYTES, "CANONICAL_RESPONSE_SIZE")
        return raw
