"""Public anonymous API, explicitly enabled by the operator. No auth gateway."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .delivery import DeliveryWorkers
from .domain import DirectFailure, Settings
from .service import AnalysisService
from .store import JobStore
from .batches import BatchService
from .batch_api import install_batch_routes


def create_app(environment=None):
    environment = dict(os.environ if environment is None else environment)
    settings = Settings(root=Path(environment["AI_DIRECT_SPOOL_ROOT"]),
        enabled=environment.get("AI_DIRECT_ENABLED", "false") == "true",
        origins=tuple(x.strip() for x in environment.get("AI_DIRECT_ALLOWED_ORIGINS", "").split(",") if x.strip()))
    @asynccontextmanager
    async def lifespan(app):
        store = JobStore(settings)
        service = AnalysisService(store, environment)
        delivery = DeliveryWorkers(store, environment)
        app.state.store, app.state.service = store, service
        app.state.batches = BatchService(store, service)
        app.state.uploads = 0
        await service.start()
        delivery.start()
        try:
            yield
        finally:
            await service.close()
            await delivery.close()
            store.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.origins),
        allow_credentials=False, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Analysis-Metadata", "Last-Event-ID"], max_age=3600)

    @app.exception_handler(DirectFailure)
    async def failure(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status,
                            headers={"Cache-Control": "no-store", "Retry-After": "3"})

    @app.exception_handler(TimeoutError)
    async def upload_timeout(request, exc):
        return JSONResponse({"code": "UPLOAD_TIMEOUT"}, status_code=408, headers={"Cache-Control": "no-store"})

    @app.get("/readyz")
    async def ready():
        active = app.state.service.ready()
        return JSONResponse({"ready": active, "reasonCode": app.state.service.ready_error or (None if active else "CORE_WARMING"),
                            "authentication": "NONE", "maximumAudioBytes": settings.maximum_bytes},
                            status_code=200 if active else 503)

    @app.post("/api/v1/analyses")
    async def submit(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "audio/wav":
            raise DirectFailure("PCM_WAV_REQUIRED", 415)
        raw_metadata = request.headers.get("x-analysis-metadata", "")
        if len(raw_metadata) > 24000:
            raise DirectFailure("METADATA_TOO_LARGE", 413)
        try:
            metadata = json.loads(base64.b64decode(raw_metadata, validate=True))
            if not isinstance(metadata, dict) or set(metadata) - {"clientAttemptId", "scriptText", "contentId", "historyClaimSha256"}:
                raise ValueError()
        except (ValueError, UnicodeError) as exc:
            raise DirectFailure("INVALID_METADATA", 422) from exc
        if not isinstance(metadata.get("clientAttemptId"), str):
            raise DirectFailure("INVALID_ATTEMPT_ID", 422)
        if not app.state.service.ready() and not app.state.service.known_attempt(metadata["clientAttemptId"]):
            raise DirectFailure(app.state.service.ready_error or "CORE_NOT_READY", 503)
        if app.state.uploads >= settings.maximum_pending:
            raise DirectFailure("UPLOAD_CAPACITY_EXCEEDED", 429)
        app.state.uploads += 1
        try:
            body = bytearray()
            async with asyncio.timeout(60):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > settings.maximum_bytes:
                        raise DirectFailure("AUDIO_TOO_LARGE", 413)
                    body.extend(chunk)
            result = await app.state.service.submit(metadata, bytes(body))
            return JSONResponse(result, status_code=202, headers={"Cache-Control": "no-store"})
        finally:
            app.state.uploads -= 1

    @app.get("/api/v1/analyses/{job_id}")
    async def result(job_id: str):
        return JSONResponse(await asyncio.to_thread(app.state.service.view, job_id), headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/analyses")
    async def find(clientAttemptId: str):
        return JSONResponse(app.state.service.find(clientAttemptId), headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/analyses/{job_id}/cancellations")
    async def cancel(job_id: str):
        return JSONResponse(await asyncio.to_thread(app.state.service.cancel, job_id), headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/analyses/{job_id}/events")
    async def events(job_id: str, request: Request):
        app.state.service.view(job_id)
        async def stream():
            previous = None
            while not await request.is_disconnected():
                view = await asyncio.to_thread(app.state.service.view, job_id)
                if view["sequence"] != previous:
                    previous = view["sequence"]
                    yield f"id: {previous}\nevent: analysis\ndata: {json.dumps(view, ensure_ascii=False)}\n\n"
                if view["status"] in ("RESULT_READY", "FAILED", "CANCELLED"):
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(.25)
        return StreamingResponse(stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    install_batch_routes(app, settings)
    return app
