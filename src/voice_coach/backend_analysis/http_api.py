"""Authenticated asynchronous analysis API; health never claims inference success."""
from contextlib import asynccontextmanager
import hmac
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .http_models import HttpFailure, MAX_CONTROL_BYTES, parse_json, validate, validate_analysis_request
from .http_runtime import HttpRuntime


def create_app(environment=None):
    runtime = HttpRuntime(os.environ if environment is None else environment)

    @asynccontextmanager
    async def lifespan(app):
        await runtime.start()
        yield
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
            if request.url.path not in ("/health", "/readyz"):
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
                                 reasonCode=runtime.reason, executorReady=runtime.executor_ready,
                                 legacyReady=runtime.legacy_ready(), canonicalReady=runtime.canonical_admission_ready(),
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
        configured = (runtime.canonical_lane is not None and not runtime.stopping
                      and runtime.environment.get("AI_CANONICAL_HANDOFF_ENABLED") == "true")
        return JSONResponse(dict(contractVersion="voice-coaching.canonical-handoff.v1",
            executorConfigured=configured, schemaDigests=handoff_schema_digests()),
            status_code=200 if configured else 503, headers={"Cache-Control": "no-store"})

    @app.get("/health/canonical")
    async def canonical_health(request: Request):
        # The shared middleware authenticates every non-public health route.
        from .canonical_settings import schema_digests
        ready = runtime.canonical_lane is not None and not runtime.stopping
        return JSONResponse({
            "executorConfigured": ready,
            "admissionEnabled": runtime.canonical_admission_ready(),
            "reasonCode": runtime.canonical_reason,
            "executionQaPerformed": False,
            "schemaDigests": schema_digests(),
        }, status_code=200 if ready else 503)

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

    return app
