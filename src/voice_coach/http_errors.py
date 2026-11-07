"""HTTP error boundaries shared by the internal and anonymous analysis APIs."""
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException


def framework_reason(status: int) -> str:
    """Keep the HTTP status while using the existing safe control error codes."""
    return {
        401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "TARGET_NOT_FOUND",
        408: "DEADLINE_EXCEEDED", 413: "PAYLOAD_TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE", 429: "RATE_LIMITED",
        502: "DEPENDENCY_UNAVAILABLE", 503: "DEPENDENCY_UNAVAILABLE",
        504: "DEPENDENCY_UNAVAILABLE",
    }.get(status, "VALIDATION_FAILED" if status < 500 else "INTERNAL_ERROR")


class ErrorResponseBoundary:
    """Return a JSON 500 before headers start, including mounted app failures.

    ServerErrorMiddleware re-raises after sending a response. Handling the error
    here avoids propagating a mounted app's already-sent 500 into its parent.
    Once an SSE response has started, a second HTTP status cannot be sent.
    """
    def __init__(self, app, respond):
        self.app, self.respond = app, respond

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def track(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, track)
        except Exception:
            if started:
                raise
            response = await self.respond(None, 500, "INTERNAL_ERROR", {})
            await response(scope, receive, send)


def install_http_error_handlers(app, respond):
    async def http_failure(request, error):
        # Framework details/validation inputs may contain transcripts or secrets.
        # Retain protocol headers, never echo arbitrary exception headers/details.
        headers = {k: v for k, v in (error.headers or {}).items()
                   if k.lower() in {"allow", "www-authenticate", "retry-after"}}
        return await respond(request, error.status_code, framework_reason(error.status_code), headers)

    async def invalid_request(request, error):
        return await respond(request, 422, "VALIDATION_FAILED", {})

    async def internal_failure(request, error):
        return await respond(request, 500, "INTERNAL_ERROR", {})

    app.add_exception_handler(HTTPException, http_failure)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(Exception, internal_failure)
    app.add_middleware(ErrorResponseBoundary, respond=respond)
