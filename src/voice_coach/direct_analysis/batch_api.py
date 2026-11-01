"""Bounded public HTTP transport for sentence practice batches."""
import asyncio
import json
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from .domain import DirectFailure


def install_batch_routes(app, settings):
    def reply(value, status=200):
        return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store"})

    async def body(request, maximum):
        raw = bytearray()
        async with asyncio.timeout(60):
            async for part in request.stream():
                if len(raw) + len(part) > maximum:
                    raise DirectFailure("PAYLOAD_TOO_LARGE", 413)
                raw.extend(part)
        return bytes(raw)

    @app.post("/api/v1/practice-batches")
    async def create(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise DirectFailure("JSON_REQUIRED", 415)
        try:
            payload = json.loads(await body(request, 32768))
        except (ValueError, UnicodeError):
            raise DirectFailure("INVALID_BATCH", 422) from None
        return reply(await asyncio.to_thread(app.state.batches.create, payload), 202)

    @app.post("/api/v1/practice-batches/{batch_id}/sentences/{sequence_no}")
    async def upload(batch_id: str, sequence_no: int, request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "audio/wav":
            raise DirectFailure("PCM_WAV_REQUIRED", 415)
        if app.state.uploads >= settings.maximum_pending:
            raise DirectFailure("UPLOAD_CAPACITY_EXCEEDED", 429)
        app.state.uploads += 1
        try:
            audio = await body(request, settings.maximum_bytes)
            return reply(await app.state.batches.submit(batch_id, sequence_no-1, audio), 202)
        finally:
            app.state.uploads -= 1

    @app.get("/api/v1/practice-batches/{batch_id}")
    async def view(batch_id: str, waitSeconds: int = 0):
        if not 0 <= waitSeconds <= 20:
            raise DirectFailure("INVALID_WAIT", 422)
        deadline = time.monotonic() + waitSeconds
        while True:
            value = await asyncio.to_thread(app.state.batches.view, batch_id)
            if value["status"] in {"RESULT_READY", "CANCELLED"} or time.monotonic() >= deadline:
                return reply(value)
            await asyncio.sleep(.5)

    @app.post("/api/v1/practice-batches/{batch_id}/cancellations")
    async def cancel(batch_id: str):
        return reply(await app.state.batches.cancel(batch_id))
