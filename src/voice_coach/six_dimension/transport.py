"""Single bounded provider request, preserving explicit model configuration."""
from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .policy import require

ENDPOINT = "https://api.openai.com/v1/chat/completions"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("PROVIDER_REDIRECT")


class OpenAITransport:
    def __init__(self, api_key: str):
        require(isinstance(api_key, str) and bool(api_key.strip())
                and not any(c in api_key for c in "\r\n\x00"), "PROVIDER_CONFIGURATION")
        self._key = api_key

    def __call__(self, payload: bytes, *, timeout: int) -> bytes:
        require(type(payload) is bytes and 0 < len(payload) <= 1024*1024, "PROVIDER_REQUEST_SIZE")
        require(type(timeout) is int and 1 <= timeout <= 120, "PROVIDER_TIMEOUT")
        request = Request(ENDPOINT, data=payload, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self._key})
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
                require(response.status == 200, "PROVIDER_STATUS")
                raw = response.read(131073)
        except (HTTPError, URLError, TimeoutError, OSError):
            raise ValueError("PROVIDER_UNAVAILABLE") from None
        require(0 < len(raw) <= 131072, "PROVIDER_RESPONSE_SIZE")
        return raw
