"""Authenticated loopback API for a single user's research workspace."""
from __future__ import annotations

import hmac
import hashlib
import io
import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .service import JobService, ServiceError


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaperBody(StrictBody):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=200000)


class FeaturesBody(StrictBody):
    paperVersion: int = Field(ge=1)
    features: list[str] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_features(self):
        if any(not x.strip() or len(x) > 1500 for x in self.features):
            raise ValueError("features must be nonempty and at most 1500 characters")
        return self


class ConfirmBody(StrictBody):
    paperVersion: int = Field(ge=1)


class SearchBody(StrictBody):
    paperId: str = Field(pattern=r"^[0-9a-f-]{36}$")
    paperVersion: int = Field(ge=1)
    countries: list[Literal["US", "KR"]] | None = Field(default=None, max_length=2)
    queryText: str | None = Field(default=None, min_length=1, max_length=8000)
    dateFrom: date | None = None
    dateTo: date | None = None
    mode: Literal["hybrid_semantic", "dense_only", "lexical_only"] = "hybrid_semantic"
    rerank: bool = False

    @model_validator(mode="after")
    def dates(self):
        if self.dateFrom and self.dateTo and self.dateFrom > self.dateTo:
            raise ValueError("dateFrom must not exceed dateTo")
        return self


def create_app(service: JobService, token: str) -> FastAPI:
    if len(token) < 32:
        raise ValueError("PATENT_API_TOKEN must have at least 32 characters")

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    app = FastAPI(title="Paper-to-patent local research API", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.url.path not in {"/health", "/readyz"}:
            auth = request.headers.get("authorization", "")
            if not hmac.compare_digest(auth.encode(), ("Bearer "+token).encode()):
                return JSONResponse({"reasonCode": "UNAUTHENTICATED"}, status_code=401)
        if request.method in {"POST", "PATCH"}:
            limit = 20*1024*1024 if request.url.path == "/api/papers/pdf" else 1024*1024
            # Bound the actual streamed bytes, including chunked requests.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > limit:
                    return JSONResponse({"reasonCode": "PAYLOAD_TOO_LARGE"}, status_code=413)
            request._body = bytes(body)
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse({"reasonCode": error.code}, status_code=error.status)

    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        # Do not echo user manuscript content in validation errors.
        return JSONResponse({"reasonCode": "VALIDATION_FAILED"}, status_code=422)

    @app.get("/health")
    def health():
        return {"status": "alive"}

    @app.get("/readyz")
    def ready():
        return {"status": "ready", "scope": "single_user_research_pilot"}

    @app.get("/api/patent-search-capabilities")
    def capabilities():
        manifest = service.engine.manifest
        return {"scope": manifest["corpusManifest"], "indexRevision": manifest["indexRevision"],
                "engine": "exact_cosine+BM25", "evidenceScope": manifest["evidenceScope"],
                "semanticReady": True, "productionReady": False,
                "explanationAvailable": False, "claimDependencyParsing": False,
                "modes": ["hybrid_semantic", "dense_only", "lexical_only"]}

    @app.post("/api/papers", status_code=201)
    def add_paper(body: PaperBody):
        return service.add_paper(body.title, body.text)

    @app.post("/api/papers/pdf", status_code=201)
    async def add_pdf(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/pdf":
            raise ServiceError("UNSUPPORTED_MEDIA_TYPE", 415)
        content = await request.body()
        if not content.startswith(b"%PDF-"):
            raise ServiceError("INVALID_PDF", 422)
        from .pdf_input import extract_pdf
        from starlette.concurrency import run_in_threadpool
        extracted = await run_in_threadpool(extract_pdf, content)
        if "error" in extracted:
            raise ServiceError(extracted["error"], 422)
        segments, spans, offset = [], [], 0
        for i, page in enumerate(extracted["pages"]):
            marker = f"[Page {i+1}]\n"
            segments.append(marker+page)
            spans.append({"page": i+1, "start": offset+len(marker), "end": offset+len(marker)+len(page)})
            offset += len(marker)+len(page)+2
        return service.add_paper("Uploaded PDF", "\n\n".join(segments), {
            "kind": "pdf_text", "fileSha256": hashlib.sha256(content).hexdigest(),
            "pageSpans": spans, "originalRetained": False})

    @app.get("/api/papers/{pid}")
    def paper(pid: str):
        return service.paper(pid)

    @app.patch("/api/papers/{pid}/features")
    def features(pid: str, body: FeaturesBody):
        return service.features(pid, body.paperVersion, body.features)

    @app.post("/api/papers/{pid}/features/confirm")
    def confirm(pid: str, body: ConfirmBody):
        return service.features(pid, body.paperVersion, None, confirm=True)

    @app.get("/api/papers/{pid}/patent-search-jobs")
    def jobs(pid: str):
        return service.list_jobs(pid)

    @app.post("/api/patent-search-jobs", status_code=202)
    def submit(body: SearchBody, idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
        if body.countries and any(x not in service.engine.manifest["corpusManifest"]["countries"] for x in body.countries):
            raise ServiceError("COUNTRY_NOT_INDEXED", 422)
        return service.submit(body.model_dump(mode="json"), idempotency_key)

    @app.get("/api/patent-search-jobs/{jid}")
    def job(jid: str):
        return service.job(jid)

    @app.get("/api/patent-search-jobs/{jid}/results")
    def results(jid: str):
        result = service.job(jid)["result"]
        if result is None:
            raise ServiceError("RESULT_NOT_READY")
        return result  # immutable bounded top 20; no pagination needed in pilot

    @app.get("/api/patent-search-jobs/{jid}/results/{publication_id}")
    def result(jid: str, publication_id: str):
        for item in results(jid)["results"]:
            if item["publicationId"] == publication_id:
                return item
        raise ServiceError("RESULT_NOT_FOUND", 404)

    @app.post("/api/patent-search-jobs/{jid}/cancel")
    def cancel(jid: str):
        return service.cancel(jid)

    @app.delete("/api/papers/{pid}", status_code=204)
    def delete(pid: str):
        service.delete(pid)

    return app
