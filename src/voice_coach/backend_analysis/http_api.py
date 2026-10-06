"""Authenticated asynchronous analysis API; health never claims inference success."""
from contextlib import AsyncExitStack, asynccontextmanager
import hmac
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .http_models import HttpFailure, MAX_CONTROL_BYTES, parse_json, validate, validate_analysis_request
from .http_runtime import HttpRuntime


def create_app(environment=None):
    runtime = HttpRuntime(os.environ if environment is None else environment)
    direct = None
    if runtime.environment.get("AI_DIRECT_ENABLED") == "true":
        from voice_coach.direct_analysis.api import create_app as create_direct_app
        direct = create_direct_app(runtime.environment)

    @asynccontextmanager
    async def lifespan(app):
        await runtime.start()
        try:
            async with AsyncExitStack() as stack:
                if direct is not None:
                    await stack.enter_async_context(direct.router.lifespan_context(direct))
                yield
        finally:
            await runtime.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime

    @app.exception_handler(HttpFailure)
    async def failure(request, error):
        logging.getLogger(__name__).info("analysis_http_rejected status=%s code=%s", error.status, error.code)
        headers = {"Cache-Control": "no-store"}
        if error.status in (429, 503):
            headers["Retry-After"] = str(max(1, int(error.retry_after)))
        if error.status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(error.payload(), status_code=error.status, headers=headers)

    @app.exception_handler(Exception)
    async def internal_failure(request, error):
        return await failure(request, HttpFailure(500, "INTERNAL_ERROR"))

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        try:
            public_direct = direct is not None and request.url.path.startswith("/direct/")
            if not public_direct and request.url.path not in ("/health", "/readyz"):
                values = request.headers.getlist("authorization")
                configured = runtime.environment.get("AI_ANALYSIS_API_TOKEN", "")
                if (not configured or len(values) != 1 or not values[0].startswith("Bearer ")
                        or not hmac.compare_digest(values[0][7:].encode(), configured.encode())):
                    raise HttpFailure(401, "UNAUTHENTICATED")
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        except HttpFailure as error:
            return await failure(request, error)

    async def body(request):
        if (request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
                or request.headers.get("content-encoding", "identity") != "identity"):
            raise HttpFailure(415, "UNSUPPORTED_MEDIA_TYPE")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_CONTROL_BYTES:
                raise HttpFailure(413, "PAYLOAD_TOO_LARGE")
        return parse_json(bytes(raw))

    @app.get("/health")
    async def health():
        return {"status": "alive"}

    @app.get("/readyz")
    async def ready():
        is_ready = runtime.ready()
        return JSONResponse({"status": "ready" if is_ready else "not_ready"}, status_code=200 if is_ready else 503)

    @app.get("/health/services")
    async def services():
        is_ready = runtime.ready()
        return JSONResponse(dict(status="ready" if is_ready else "not_ready", workerInstanceId=runtime.worker_id,
                                 reasonCode=runtime.reason or runtime.canonical_reason,
                                 executorReady=runtime.handoff_executor_ready(),
                                 handoffReady=runtime.handoff_admission_ready(),
                                 coachingProvider="OPENAI", coachingModel="gpt-6-astra",
                                 workerRevision=runtime.environment.get("VOICE_COACH_BUILD_REVISION")),
                            status_code=200 if is_ready else 503)

    @app.get("/health/deployment")
    async def deployment_health():
        return JSONResponse(runtime.deployment_snapshot(), headers={"Cache-Control": "no-store"})

    @app.post("/v1/analysis-jobs")
    async def accept(request: Request):
        payload = await body(request)
        validate_analysis_request(payload)
        code, result = await runtime.accept(payload)
        return JSONResponse(result, status_code=code)

    @app.get("/health/handoff")
    async def handoff_health():
        from .canonical_settings import handoff_schema_digests
        configured = runtime.handoff_executor_ready()
        return JSONResponse(dict(contractVersion="voice-coaching.canonical-handoff.v1",
            executorConfigured=configured, admissionEnabled=runtime.handoff_admission_ready(), schemaDigests=handoff_schema_digests()),
            status_code=200 if configured else 503, headers={"Cache-Control": "no-store"})

    @app.get("/v1/analysis-jobs/{request_id}")
    async def status(request_id: str, request: Request):
        values = request.query_params.getlist("executionId")
        if len(values) != 1 or set(request.query_params) != {"executionId"}:
            raise HttpFailure(422, "VALIDATION_FAILED")
        validate(request_id, "uuid")
        validate(values[0], "uuid")
        return runtime.status(runtime.lookup(request_id, values[0]), details=True)

    @app.post("/v1/analysis-jobs/{request_id}/cancel")
    async def cancel(request_id: str, request: Request):
        validate(request_id, "uuid")
        payload = validate(await body(request), "cancelRequest")
        code, result = runtime.cancel(request_id, payload["executionId"])
        return JSONResponse(result, status_code=code)

    if direct is not None:
        app.mount("/direct", direct)
    return app
