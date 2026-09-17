"""Bounded HTTPS calls to the configured Backend, without redirects or logging."""
import asyncio
import httpx
import rfc8785
from email.utils import parsedate_to_datetime
from datetime import datetime, UTC

from ..http_models import HttpFailure, parse_json, validate


class BackendHttp:
    def __init__(self, settings):
        self.client = httpx.AsyncClient(base_url=settings.callback_base,
            headers={"Authorization": "Bearer " + settings.callback_token},
            timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False)

    async def close(self):
        await self.client.aclose()

    async def call(self, path, body, response_kind, *, budget=10):
        async def receive():
            async with self.client.stream("GET" if body is None else "POST", path,
                    content=rfc8785.dumps(body) if body is not None else None,
                    headers={"Content-Type": "application/json"}) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise HttpFailure(503, "DEPENDENCY_UNAVAILABLE")
                if response.status_code != 200:
                    try:
                        error = validate(parse_json(bytes(raw)), "error")
                        reason = error["reasonCode"]
                    except HttpFailure:
                        reason = "DEPENDENCY_UNAVAILABLE"
                    retry = 1.0
                    value = response.headers.get("Retry-After", "1")
                    try:
                        retry = max(0, float(value))
                    except ValueError:
                        try:
                            retry = max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
                        except (ValueError, TypeError):
                            pass
                    raise HttpFailure(response.status_code, reason, retry_after=retry)
                if response.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise HttpFailure(503, "DEPENDENCY_UNAVAILABLE")
                return validate(parse_json(bytes(raw)), response_kind)
        try:
            return await asyncio.wait_for(receive(), timeout=max(0.01, min(10, budget)))
        except (httpx.HTTPError, TimeoutError):
            raise HttpFailure(503, "DEPENDENCY_UNAVAILABLE") from None
